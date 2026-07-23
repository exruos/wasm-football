import marimo

__generated_with = "0.23.14"
app = marimo.App(width="medium")


@app.cell
def _():
    from datetime import datetime, timezone
    from pathlib import Path

    import altair as alt
    import marimo as mo
    import numpy as np
    import polars as pl
    import polars.selectors as cs
    import glob

    return alt, glob, mo, pl


@app.cell
def _(glob, pl):
    def load_metric_data(metric: str, scenario: str, time_col: str = "timestamp") -> pl.DataFrame:
        path_pattern = f"./parquet/*/{scenario}/{metric}_*.parquet"
        dir_regex = f"parquet/([^/]+)/{scenario}"
        iter_regex = r"_(\d+)\.parquet$"

        all_files = glob.glob(path_pattern)
        # Filter out empty parquet files that cause schema unification failures
        empty_files = [f for f in all_files if pl.scan_parquet(f).select(pl.len()).collect()['len'][0] == 0]
        files = [f for f in all_files if f not in empty_files]

        if empty_files:
            print(f"Skipping {len(empty_files)} empty file(s) for {metric}/{scenario}:")
            for f in empty_files:
                print(f"  - {f}")

        if not files:
            return pl.DataFrame()

        return (
            pl.scan_parquet(files, include_file_paths="full_path", missing_columns="insert", extra_columns="ignore")
            .with_columns([
                pl.col("full_path").str.extract(dir_regex).alias("dir_name"),
                pl.col("full_path").str.extract(iter_regex).cast(pl.Int32).alias("iteration"),
            ])
            .drop("full_path")
            # Sort beforehand so the min() and ordering are perfectly predictable
            .sort(["dir_name", "iteration", time_col])
            # Calculate normalized time starting at 0 per unique iteration
            .with_columns(
                (pl.col(time_col) - pl.col(time_col).min().over(["dir_name", "iteration"]))
                .dt.total_seconds()
                .alias("normalized_time")
            )
            .collect()
        )

    return (load_metric_data,)


@app.cell
def _(load_metric_data, pl):
    metrics = [
        "pod_joules",
        "pods",
        "requests",
        "iterations",
        "vus",
        "p95",
        "p99",
        "rps",
        "memory",
        "cpu_usage",
    ]

    scenarios = ["baseline", "coldstart", "scaling"]

    # Load all metric/scenario combinations into a dictionary
    df_all_metrics = {}
    for metric in metrics:
        for scenario in scenarios:
            try:
                df_all_metrics[(metric, scenario)] = load_metric_data(
                    metric=metric,
                    scenario=scenario,
                    time_col="timestamp",
                )
            except Exception as e:
                print(f"Failed to load {metric} for {scenario}: {e}")

    # Display summary of what was loaded
    loaded_summary = pl.DataFrame([
        {"metric": k[0], "scenario": k[1], "rows": len(v), "columns": len(v.columns)}
        for k, v in df_all_metrics.items()
    ])
    loaded_summary
    return (df_all_metrics,)


@app.cell
def _(pl):
    def build_scenario_table(df_all_metrics: dict, scenario: str) -> pl.DataFrame:
                """Build a wide DataFrame for a scenario by joining all metric DataFrames on dir_name + iteration.

                Each metric may have multiple timestamped rows per (dir_name, iteration).
                We aggregate to one row per (dir_name, iteration) using max for cumulative
                counters (requests, iterations, vus, pods) and mean for sampled metrics
                (p95, p99, rps, memory, cpu_usage, pod_joules).
                """
                cumulative_counters = {"requests", "iterations", "vus", "pods", "pod_joules"}
                scenario_keys = [k for k in df_all_metrics if k[1] == scenario and len(df_all_metrics[k]) > 0]
                if not scenario_keys:
                    return pl.DataFrame()

                # Start with the first metric's dir_name and iteration
                base = df_all_metrics[scenario_keys[0]].select(["dir_name", "iteration"]).unique()

                # Aggregate each metric to one row per (dir_name, iteration), then join
                tables = []
                for metric, _ in scenario_keys:
                    df = df_all_metrics[(metric, scenario)]
                    if "value" in df.columns:
                        agg_fn = pl.col("value").max() if metric in cumulative_counters else pl.col("value").mean()
                        tables.append(
                            df.group_by(["dir_name", "iteration"])
                              .agg(agg_fn.alias(metric))
                              .unique(subset=["dir_name", "iteration"])
                        )
                    else:
                        tables.append(
                            df.select(["dir_name", "iteration"])
                              .unique(subset=["dir_name", "iteration"])
                              .with_columns(pl.lit(0.0).alias(metric))
                        )

                result = base
                for t in tables:
                    result = result.join(t, on=["dir_name", "iteration"], how="left")
                return result

    return (build_scenario_table,)


@app.cell
def _(df_all_metrics):
    df_all_metrics
    return


@app.cell
def _(build_scenario_table, df_all_metrics, pl):
    # --- Compute all metrics for baseline scenario ---
    df_baseline = build_scenario_table(df_all_metrics, "baseline")

    if len(df_baseline) > 0:
        # Latency & Responsiveness metrics
        df_latency_baseline = df_baseline.group_by("dir_name").agg([
            pl.col("p95").mean().alias("latency_p95_mean"),
            pl.col("p99").mean().alias("latency_p99_mean"),
            pl.col("p95").median().alias("latency_p95_median"),
            pl.col("p95").std().alias("latency_stddev"),
            (pl.col("p95").std() / pl.col("p95").mean()).alias("latency_cv"),
            pl.col("p95").max().alias("latency_p95_max"),
            pl.col("p95").min().alias("latency_p95_min"),
            pl.col("p95").count().alias("sample_count"),
        ]).with_columns([
            # 2. Margin of Error for 95% Confidence Interval (Z = 1.96)
            (
                1.96 * (pl.col("latency_stddev") / pl.col("sample_count").sqrt())
            ).alias("latency_p95_ci_margin")
        ]).with_columns([
            # 3. Lower and Upper bounds for the p95 mean
            (pl.col("latency_p95_mean") - pl.col("latency_p95_ci_margin")).alias(
                "latency_p95_ci_lower"
            ),
            (pl.col("latency_p95_mean") + pl.col("latency_p95_ci_margin")).alias(
                "latency_p95_ci_upper"
            ),
        ]).with_columns(
            (pl.col("^latency_(p95|p99|stddev).*$").exclude("latency_cv") * 1000)
        )

        # Throughput metrics
        # requests is a global cumulative counter (shared across dir_names).
        # total_requests = the final cumulative value (max across all iterations).
        df_throughput_baseline = df_baseline.group_by("dir_name").agg([
            pl.col("rps").mean().alias("mean_rps"),
            pl.col("rps").max().alias("max_rps"),
            pl.col("rps").min().alias("min_rps"),
            # requests is a global cumulative counter (shared across dir_names).
                # total_requests = the final cumulative value (max across all iterations).
                pl.col("requests").max().alias("total_requests"),
        ])

        # Resource metrics (CPU/Memory)
        df_resource_baseline = df_baseline.group_by("dir_name").agg([
            pl.col("cpu_usage").mean().alias("avg_cpu_pct"),
            pl.col("cpu_usage").max().alias("peak_cpu_pct"),
            pl.col("cpu_usage").min().alias("min_cpu_pct"),
            pl.col("memory").mean().alias("avg_memory_mb"),
            pl.col("memory").max().alias("peak_memory_mb"),
            pl.col("memory").min().alias("min_memory_mb"),
            (pl.col("cpu_usage").mean() / pl.col("rps").mean() * 1000).alias("cpu_per_1k_rps"),
            (pl.col("memory").mean() / pl.col("rps").mean() * 1000).alias("memory_mb_per_1k_rps"),
        ])

        # Energy metrics: use raw pod_joules from df_all_metrics, not df_baseline
        # (df_baseline already has pod_joules aggregated as max per iteration,
        #  so max - min would always be 0)
        df_raw_joules_baseline = df_all_metrics[("pod_joules", "baseline")]

        # 1. Compute net energy per (dir_name, iteration, zone)
        df_iter_zone_baseline = df_raw_joules_baseline.group_by(["dir_name", "iteration", "zone"]).agg(
            (pl.col("value").max() - pl.col("value").min()).alias("iter_joules")
        )

        # 2. Average across iterations per zone, then pivot zones into separate columns
        df_energy_pivoted_baseline = (
            df_iter_zone_baseline.group_by(["dir_name", "zone"])
            .agg(pl.col("iter_joules").mean())
            .pivot(on="zone", index="dir_name", values="iter_joules")
        )

        # 3. Join back and calculate package, dram, total, and per-request metrics
        df_energy_baseline = (
            df_throughput_baseline.join(df_energy_pivoted_baseline, on="dir_name", how="left")
            .with_columns(
                (pl.col("package") + pl.col("dram")).alias("total_joules")
            )
            .select([
                "dir_name",
                pl.col("package").alias("cpu_joules"),
                pl.col("dram").alias("dram_joules"),
                "total_joules",
                (pl.col("total_joules") / pl.col("total_requests")).alias("joules_per_request"),
                (pl.col("total_requests") / pl.col("total_joules")).alias("requests_per_joule"),
            ])
        )

        # Combine all metrics into a single summary table
        df_metrics_baseline = df_latency_baseline.join(
            df_throughput_baseline, on=["dir_name"], how="left"
        ).join(
            df_resource_baseline, on=["dir_name"], how="left"
        ).join(
            df_energy_baseline, on=["dir_name"], how="left"
        ).sort(["dir_name"]).unique()
    else:
        df_metrics_baseline = pl.DataFrame()

    df_metrics_baseline
    return (df_metrics_baseline,)


@app.cell
def _(build_scenario_table, df_all_metrics, pl):
    # --- Compute all metrics for coldstart scenario ---
    df_coldstart = build_scenario_table(df_all_metrics, "coldstart")

    if len(df_coldstart) > 0:
        # Latency & Responsiveness metrics - this is the time to first response
        df_latency_coldstart = df_coldstart.group_by("dir_name").agg([
            pl.col("p95").mean().alias("latency_p95_mean"),
            pl.col("p99").mean().alias("latency_p99_mean"),
            pl.col("p95").median().alias("latency_p95_median"),
            pl.col("p95").std().alias("latency_stddev"),
            (pl.col("p95").std() / pl.col("p95").mean()).alias("latency_cv"),
            pl.col("p95").max().alias("latency_p95_max"),
            pl.col("p95").min().alias("latency_p95_min"),
            pl.col("p95").count().alias("sample_count"),
        ]).with_columns([
            # 2. Margin of Error for 95% Confidence Interval (Z = 1.96)
            (
                1.96 * (pl.col("latency_stddev") / pl.col("sample_count").sqrt())
            ).alias("latency_p95_ci_margin")
        ]).with_columns([
            # 3. Lower and Upper bounds for the p95 mean
            (pl.col("latency_p95_mean") - pl.col("latency_p95_ci_margin")).alias(
                "latency_p95_ci_lower"
            ),
            (pl.col("latency_p95_mean") + pl.col("latency_p95_ci_margin")).alias(
                "latency_p95_ci_upper"
            ),
        ]).with_columns(
            (pl.col("^latency_(p95|p99|stddev).*$").exclude("latency_cv") * 1000)
        )

        # Energy metrics: use raw pod_joules from df_all_metrics, not df_coldstart
        # (df_coldstart already has pod_joules aggregated as max per iteration,
        #  so max - min would always be 0)
        df_raw_joules_coldstart = df_all_metrics[("pod_joules", "coldstart")]

        # 1. Compute net energy per (dir_name, iteration, zone)
        df_iter_zone_coldstart = df_raw_joules_coldstart.group_by(["dir_name", "iteration", "zone"]).agg(
            (pl.col("value").max() - pl.col("value").min()).alias("iter_joules")
        )

        # 2. Average across iterations per zone, then pivot zones into separate columns
        df_energy_pivoted_coldstart = (
            df_iter_zone_coldstart.group_by(["dir_name", "zone"])
            .agg(pl.col("iter_joules").mean())
            .pivot(on="zone", index="dir_name", values="iter_joules")
        )

        # 3. Join back and calculate package, dram, and total joules
        df_energy_coldstart = (
            df_latency_coldstart.join(df_energy_pivoted_coldstart, on="dir_name", how="left")
            .with_columns(
                (pl.col("package") + pl.col("dram")).alias("total_joules")
            )
            .select([
                "dir_name",
                pl.col("package").alias("cpu_joules"),
                pl.col("dram").alias("dram_joules"),
                "total_joules",
            ])
        )

        # Combine all metrics into a single summary table
        df_metrics_coldstart = df_latency_coldstart.join(
            df_energy_coldstart, on=["dir_name"], how="left"
        ).sort(["dir_name"]).unique()
    else:
        df_metrics_coldstart = pl.DataFrame()

    df_metrics_coldstart
    return


@app.cell
def _(build_scenario_table, df_all_metrics, pl):
    # --- Compute all metrics for scaling scenario ---
    df_scaling = build_scenario_table(df_all_metrics, "scaling")

    if len(df_scaling) > 0:
        # Latency & Responsiveness metrics
        df_latency_scaling = df_scaling.group_by("dir_name").agg([
            pl.col("p95").mean().alias("latency_p95_mean"),
            pl.col("p99").mean().alias("latency_p99_mean"),
            pl.col("p95").median().alias("latency_p95_median"),
            pl.col("p95").std().alias("latency_stddev"),
            (pl.col("p95").std() / pl.col("p95").mean()).alias("latency_cv"),
            pl.col("p95").max().alias("latency_p95_max"),
            pl.col("p95").min().alias("latency_p95_min"),
            pl.col("p95").count().alias("sample_count"),
        ]).with_columns([
            # 2. Margin of Error for 95% Confidence Interval (Z = 1.96)
            (
                1.96 * (pl.col("latency_stddev") / pl.col("sample_count").sqrt())
            ).alias("latency_p95_ci_margin")
        ]).with_columns([
            # 3. Lower and Upper bounds for the p95 mean
            (pl.col("latency_p95_mean") - pl.col("latency_p95_ci_margin")).alias(
                "latency_p95_ci_lower"
            ),
            (pl.col("latency_p95_mean") + pl.col("latency_p95_ci_margin")).alias(
                "latency_p95_ci_upper"
            ),
        ]).with_columns(
            (pl.col("^latency_(p95|p99|stddev).*$").exclude("latency_cv") * 1000)
        )

        df_scaling_behavior = df_scaling.group_by("dir_name").agg([
            pl.col("pods").mean().alias("mean_pods"),
            pl.col("pods").max().alias("max_pods"),
            pl.col("pods").min().alias("min_pods"),
        ])

        # Throughput metrics
        # requests is a global cumulative counter (shared across dir_names).
        # total_requests = the final cumulative value (max across all iterations).
        df_throughput_scaling = df_scaling.group_by("dir_name").agg([
            pl.col("rps").mean().alias("mean_rps"),
            pl.col("rps").max().alias("max_rps"),
            pl.col("rps").min().alias("min_rps"),
            # requests is a global cumulative counter (shared across dir_names).
            # total_requests = the final cumulative value (max across all iterations).
            pl.col("requests").max().alias("total_requests"),
        ])

        # Resource metrics (CPU/Memory)
        df_resource_scaling = df_scaling.group_by("dir_name").agg([
            pl.col("cpu_usage").mean().alias("avg_cpu_pct"),
            pl.col("cpu_usage").max().alias("peak_cpu_pct"),
            pl.col("cpu_usage").min().alias("min_cpu_pct"),
            pl.col("memory").mean().alias("avg_memory_mb"),
            pl.col("memory").max().alias("peak_memory_mb"),
            pl.col("memory").min().alias("min_memory_mb"),
            (pl.col("cpu_usage").mean() / pl.col("rps").mean() * 1000).alias("cpu_per_1k_rps"),
            (pl.col("memory").mean() / pl.col("rps").mean() * 1000).alias("memory_mb_per_1k_rps"),
        ])

        # Energy metrics: use raw pod_joules from df_all_metrics, not df_scaling
        # (df_scaling already has pod_joules aggregated as max per iteration,
        #  so max - min would always be 0)
        df_raw_joules_scaling = df_all_metrics[("pod_joules", "scaling")]

        # 1. Compute net energy per (dir_name, iteration, zone)
        df_iter_zone_scaling = df_raw_joules_scaling.group_by(["dir_name", "iteration", "zone"]).agg(
            (pl.col("value").max() - pl.col("value").min()).alias("iter_joules")
        )

        # 2. Average across iterations per zone, then pivot zones into separate columns
        df_energy_pivoted_scaling = (
            df_iter_zone_scaling.group_by(["dir_name", "zone"])
            .agg(pl.col("iter_joules").mean())
            .pivot(on="zone", index="dir_name", values="iter_joules")
        )

        # 3. Join back and calculate package, dram, total, and per-request metrics
        df_energy_scaling = (
            df_throughput_scaling.join(df_energy_pivoted_scaling, on="dir_name", how="left")
            .with_columns(
                (pl.col("package") + pl.col("dram")).alias("total_joules")
            )
            .select([
                "dir_name",
                pl.col("package").alias("cpu_joules"),
                pl.col("dram").alias("dram_joules"),
                "total_joules",
                (pl.col("total_joules") / pl.col("total_requests")).alias("joules_per_request"),
                (pl.col("total_requests") / pl.col("total_joules")).alias("requests_per_joule"),
            ])
        )

        # Combine all metrics into a single summary table
        df_metrics_scaling = df_latency_scaling.join(
            df_throughput_scaling, on=["dir_name"], how="left"
        ).join(
            df_scaling_behavior, on=["dir_name"], how="left"
        ).join(
            df_resource_scaling, on=["dir_name"], how="left"
        ).join(
            df_energy_scaling, on=["dir_name"], how="left"
        ).sort(["dir_name"]).unique()
    else:
        df_metrics_scaling = pl.DataFrame()

    df_metrics_scaling
    return


@app.cell(disabled=True, hide_code=True)
def _(alt, df_joined):
    # alt.data_transformers.enable("vegafusion")
    _chart = (
        alt.Chart(df_joined)
        .mark_line()
        .encode(
            x=alt.X(field='normalized_time', type='quantitative'),
            y=alt.Y(field='value', type='quantitative', stack=False, aggregate='median'),
            color=alt.Color(field='dir_name', type='nominal'),
            tooltip=[
                alt.Tooltip(field='normalized_time', format=',.0f'),
                alt.Tooltip(field='value', aggregate='median', format=',.2f'),
                alt.Tooltip(field='dir_name')
            ]
        )
        .properties(
            height=290,
            width='container',
            config={
                'axis': {
                    'grid': False
                }
            }
        )
    )
    _chart
    return


@app.cell
def _(df_all_metrics):
    # Load RPS data for correlation
    df_pods_scaling = df_all_metrics[("pods", "scaling")].rename({"value": "pods"})
    df_vus_scaling = df_all_metrics[("vus", "scaling")].rename({"value": "vus"})
    df_rps_scaling = df_all_metrics[("rps", "scaling")].rename({"value": "rps"})
    # correlate with time when upscaling happened, list min/max etc.
    # how many vus per pod

    # Join pods with VUs and RPS, ensuring proper column names
    df_joined = (
        df_pods_scaling.drop("timestamp")
        .join_asof(
            df_vus_scaling.drop("timestamp", "dir_name"),
            on="normalized_time",
            by="iteration",
            strategy="backward"
        )
        .join_asof(
            df_rps_scaling.drop("timestamp", "dir_name"),
            on="normalized_time",
            by="iteration",
            strategy="backward"
        )
        .drop_nulls()
    )
    df_joined
    return (df_joined,)


@app.cell
def _(alt):
    targets = ["oci-axum", "wasm-js", "oci-spring", "oci-node", "wasm-rust"]

    palette = ["#DE4C36", "#D97706", "#6DB33F", "#007ACC", "#654FF0"]

    # Create an Altair Scale object to reuse across charts
    targets_color_scale = alt.Scale(domain=targets, range=palette)
    return (targets_color_scale,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Baseline
    """)
    return


@app.cell
def _(alt, df_metrics_baseline, pl, targets_color_scale):
    # Chart A: P95 Latency + 95% CI Error Bars
    # 1. Base Bars
    p95_bars = (
        alt.Chart(df_metrics_baseline)
        .mark_bar(opacity=0.85)
        .encode(
            y=alt.Y(
                "dir_name:N",
                title="Target",
                sort='x',
            ),
            x=alt.X("latency_p95_mean:Q", title="P95 Latency (ms)"),
            color=alt.Color("dir_name:N", scale=targets_color_scale, legend=None),
        )
    )

    # 2. Error Bars
    p95_error = (
        alt.Chart(df_metrics_baseline)
        .mark_errorbar(color="#DC2626", size=14, ticks=True)
        .encode(
            y=alt.Y("dir_name:N", sort='x'),
            x="latency_p95_ci_lower:Q",
            x2="latency_p95_ci_upper:Q",
        )
    )

    chart_latency = (
        p95_bars
    ).properties(
        title=alt.TitleParams(
            text="P95 Latency",
            subtitle="100k Requests, n=10, VUs=32, Lower is better"
        ),
        width=400, height=220,
    )


    # Chart B: Throughput (RPS Mean + Min/Max Range)
    rps_bars = (
        alt.Chart(df_metrics_baseline)
        .mark_bar(color="#4c78a8")
        .encode(
            y=alt.Y("dir_name:N", title="Target", sort="-x"),
            x=alt.X("mean_rps:Q", title="Requests / Sec"),
            color=alt.Color("dir_name:N", scale=targets_color_scale, legend=None)
        )
    )

    rps_range = (
        alt.Chart(df_metrics_baseline)
        .mark_rule(color="#111111", strokeWidth=2)
        .encode(x="dir_name:N", y="min_rps:Q", y2="max_rps:Q")
    )

    chart_rps = (rps_bars).properties(
        title=alt.TitleParams(
            text="Avg Throughput Capacity (RPS)",
            subtitle="100k Requests, n=10, VUs=32, Higher is better"
        )
        , width=320, height=260
    )

    # Chart C: Energy Consumption (CPU vs DRAM Stacked)
    # Efficient Polars unpivot to make data wide -> long for stacked bars
    df_energy_unpivot = df_metrics_baseline.unpivot(
        index="dir_name",
        on=["cpu_joules", "dram_joules"],
        variable_name="Component",
        value_name="Joules",
    )

    chart_energy = (
        alt.Chart(df_energy_unpivot)
        .mark_bar()
        .encode(
            y=alt.Y("dir_name:N", title="Runtime", sort="x"),
            x=alt.X("Joules:Q", title="Total Joules"),
            color=alt.Color(
                "Component:N", scale=alt.Scale(
                    domain=["cpu_joules", "dram_joules"],
                    range=["CPU", "DRAM (Memory)"],
                    scheme="category10"
                )
            ),
            tooltip=["dir_name", "Component", "Joules"],
        )
        .properties(
            title=alt.TitleParams(
            text="Avg Energy Consumption CPU/DRAM",
            subtitle="100k Requests, n=10, VUs=32, Lower is better"
        )
            , width=320, height=260
        )
    )

    # Chart D: Memory vs. CPU Footprint (Side-by-Side Faceted / Dual View)
    # 1. Unpivot Memory and CPU into long format for clean side-by-side plotting
    df_resources = df_metrics_baseline.unpivot(
        index="dir_name",
        on=["avg_memory_mb", "avg_cpu_pct"],
        variable_name="Metric",
        value_name="Value",
    ).with_columns(
        # Create user-friendly labels for the facets/legend
        pl.col("Metric").replace(
            {
                "avg_memory_mb": "Avg Memory (MiB)",
                "avg_cpu_pct": "Avg CPU (%)",
            }
        )
    )

    # 2. Side-by-Side Faceted Chart
    chart_resource_footprint = (
        alt.Chart(df_resources)
        .mark_bar(opacity=0.85)
        .encode(
            y=alt.Y("dir_name:N", title="Environment", sort="-x"),
            x=alt.X("Value:Q", title=None),
            color=alt.Color("dir_name:N", scale=targets_color_scale, legend=None),
            column=alt.Column(
                "Metric:N",
                title=None,
                header=alt.Header(labelFontSize=13, labelFontWeight="bold"),
            ),
        )
        .properties(width=220, height=200, title="CPU vs. Memory Footprint")
        .resolve_scale(x="independent")  # Allows MB and % to have their own scales
    )

    # Chart E: Cost per Load: Normalized Resource Efficiency (Grouped Bar Chart)
    # 1. Unpivot normalized metrics into long format
    df_normalized = df_metrics_baseline.unpivot(
        index="dir_name",
        on=["cpu_per_1k_rps", "memory_mb_per_1k_rps"],
        variable_name="Resource Metric",
        value_name="CostPer1kRPS",
    ).with_columns(
        pl.col("Resource Metric").replace(
            {
                "cpu_per_1k_rps": "CPU % per 1k RPS",
                "memory_mb_per_1k_rps": "Memory (MiB) per 1k RPS",
            }
        )
    )

    # 2. Grouped Bar Chart by Metric
    chart_normalized_cost = (
        alt.Chart(df_normalized)
        .mark_bar(opacity=0.85)
        .encode(
            y=alt.Y("dir_name:N", title="Environment", sort="x"),
            x=alt.X("CostPer1kRPS:Q", title="Cost per 1k RPS (Lower is better)"),
            color=alt.Color("dir_name:N", scale=targets_color_scale, legend=None),
            row=alt.Row(
                "Resource Metric:N",
                title=None,
                header=alt.Header(labelFontSize=12, labelFontWeight="bold"),
            ),
        )
        .properties(
            width=380,
            height=180,
            title="Normalized Cost per Load (Scaling Efficiency)",
        )
        .resolve_scale(x="independent")  # Keeps CPU % and Memory MB scales separate
    )


    # Chart D: Pareto Frontier (Throughput vs. Energy Efficiency)
    scatter = (
        alt.Chart(df_metrics_baseline)
        .mark_circle(size=140)
        .encode(
            x=alt.X("mean_rps:Q", title="Throughput (RPS)"),
            y=alt.Y(
                "requests_per_joule:Q", title="Efficiency (Requests / Joule)"
            ),
            color="dir_name:N",
            tooltip=["dir_name", "mean_rps", "requests_per_joule"],
        )
    )

    labels = scatter.mark_text(align="left", dx=10, dy=-2).encode(
        text="dir_name:N"
    )

    chart_tradeoff = (scatter + labels).properties(
        title=alt.TitleParams(
            text="Efficiency Trade-off",
            subtitle="100k Requests, n=10, VUs=32, Top-Right is Best"
        ),
        width=320,
        height=260,
    )
    return (
        chart_energy,
        chart_latency,
        chart_normalized_cost,
        chart_resource_footprint,
        chart_rps,
        chart_tradeoff,
        p95_bars,
    )


@app.cell
def _(
    chart_energy,
    chart_latency,
    chart_normalized_cost,
    chart_resource_footprint,
    chart_rps,
    chart_tradeoff,
    mo,
):
    # Compose into a 2x2 grid in Marimo with native interactive state
    dashboard = (chart_latency | chart_rps) & (chart_resource_footprint | chart_normalized_cost) & (chart_energy | chart_tradeoff)
    # dashboard = (p95_bars | rps_bars) & (chart_energy | chart_tradeoff)

    # Render interactive chart grid in marimo
    chart_widget = mo.ui.altair_chart(dashboard)
    chart_widget
    return


@app.cell
def _(p95_bars):
    p95_bars
    return


@app.cell
def _(chart_latency):
    chart_latency
    return


@app.cell
def _(chart_energy):
    chart_energy
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Coldstart
    """)
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
