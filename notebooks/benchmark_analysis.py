import marimo

__generated_with = "0.23.14"
app = marimo.App(width="medium")


@app.cell
def _():
    import altair as alt
    import marimo as mo
    import polars as pl
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
                if "scenario" in df_all_metrics[(metric, scenario)].columns:
                    df_all_metrics[(metric, scenario)] = df_all_metrics[
                        (metric, scenario)
                    ].filter(pl.col("scenario") == scenario)
            except Exception as e:
                print(f"Failed to load {metric} for {scenario}: {e}")

    # Display summary of what was loaded
    loaded_summary = pl.DataFrame(
        [
            {
                "metric": k[0],
                "scenario": k[1],
                "rows": len(v),
                "columns": len(v.columns),
            }
            for k, v in df_all_metrics.items()
        ]
    )
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
    return (df_metrics_coldstart,)


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
    return df_metrics_scaling, df_scaling


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
    bars_baseline_latency = (
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
    error_baseline_latency = (
        alt.Chart(df_metrics_baseline)
        .mark_errorbar(color="#DC2626", size=14, ticks=True)
        .encode(
            y=alt.Y("dir_name:N", sort='x'),
            x="latency_p95_ci_lower:Q",
            x2="latency_p95_ci_upper:Q",
        )
    )

    chart_latency_baseline = (
        bars_baseline_latency
    ).properties(
        title=alt.TitleParams(
            text="P95 Latency",
            subtitle="100k Requests, n=10, VUs=32, Lower is better"
        ),
        width=400, height=220,
    )


    # Chart B: Throughput (RPS Mean + Min/Max Range)
    rps_bars_baseline = (
        alt.Chart(df_metrics_baseline)
        .mark_bar(color="#4c78a8")
        .encode(
            y=alt.Y("dir_name:N", title="Target", sort="-x"),
            x=alt.X("mean_rps:Q", title="Requests / Sec"),
            color=alt.Color("dir_name:N", scale=targets_color_scale, legend=None)
        )
    )

    rps_range_baseline = (
        alt.Chart(df_metrics_baseline)
        .mark_rule(color="#111111", strokeWidth=2)
        .encode(x="dir_name:N", y="min_rps:Q", y2="max_rps:Q")
    )

    chart_rps_baseline = (rps_bars_baseline).properties(
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

    chart_energy_baseline = (
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
    df_resources_baseline = df_metrics_baseline.unpivot(
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
    chart_resource_footprint_baseline = (
        alt.Chart(df_resources_baseline)
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
    df_normalized_baseline = df_metrics_baseline.unpivot(
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
    chart_normalized_cost_baseline = (
        alt.Chart(df_normalized_baseline)
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
    scatter_baseline = (
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

    scatter_labels_baseline = scatter_baseline.mark_text(align="left", dx=10, dy=-2).encode(
        text="dir_name:N"
    )

    chart_tradeoff_baseline = (scatter_baseline + scatter_labels_baseline).properties(
        title=alt.TitleParams(
            text="Efficiency Trade-off",
            subtitle="100k Requests, n=10, VUs=32, Top-Right is Best"
        ),
        width=320,
        height=260,
    )
    return (
        bars_baseline_latency,
        chart_energy_baseline,
        chart_latency_baseline,
        chart_normalized_cost_baseline,
        chart_resource_footprint_baseline,
        chart_rps_baseline,
        chart_tradeoff_baseline,
    )


@app.cell
def _(
    chart_energy_baseline,
    chart_latency_baseline,
    chart_normalized_cost_baseline,
    chart_resource_footprint_baseline,
    chart_rps_baseline,
    chart_tradeoff_baseline,
    mo,
):
    # Compose into a 2x2 grid in Marimo with native interactive state
    dashboard_baseline= (
        (chart_latency_baseline | chart_rps_baseline)
        & (chart_resource_footprint_baseline | chart_normalized_cost_baseline)
        & (chart_energy_baseline | chart_tradeoff_baseline)
    )

    chart_widget_baseline = mo.ui.altair_chart(dashboard_baseline)
    chart_widget_baseline
    return


@app.cell
def _(bars_baseline_latency):
    bars_baseline_latency
    return


@app.cell
def _(chart_latency_baseline):
    chart_latency_baseline
    return


@app.cell
def _(chart_energy_baseline):
    chart_energy_baseline
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Coldstart
    """)
    return


@app.cell
def _(alt, df_metrics_coldstart, targets_color_scale):
    # -----------------------------------------------------------------------------
    # Chart 1: P95 Latency with Confidence Intervals
    # -----------------------------------------------------------------------------
    bars_coldstart_latency = (
        alt.Chart(df_metrics_coldstart)
        .mark_bar(opacity=0.85)
        .encode(
            y=alt.Y(
                "dir_name:N",
                title="Target",
                sort='x',
            ),
            x=alt.X("latency_p95_mean:Q", title="Response Time (ms)"),
            color=alt.Color("dir_name:N", scale=targets_color_scale, legend=None),
        )
    )

    # 2. Error Bars
    errors_coldstart_latency = (
        alt.Chart(df_metrics_coldstart)
        .mark_errorbar(color="#DC2626", size=14, ticks=True)
        .encode(
            y=alt.Y("dir_name:N", sort='x'),
            x="latency_p95_ci_lower:Q",
            x2="latency_p95_ci_upper:Q",
        )
    )

    chart_coldstart_latency = (
        (bars_coldstart_latency + errors_coldstart_latency)
        .properties(
        title=alt.TitleParams(
            text="Coldstart Time to First Response (HTTP 200)",
            subtitle="Endpoint: /players/1, Lower is better"
        ),
        width=400, height=220,
        )
    )


    # -----------------------------------------------------------------------------
    # Chart 2: Energy Consumption Breakdown (CPU vs DRAM Stacked)
    # -----------------------------------------------------------------------------
    df_energy_coldstart_unpivot = df_metrics_coldstart.unpivot(
        index="dir_name",
        on=["cpu_joules", "dram_joules"],
        variable_name="Component",
        value_name="Joules",
    )

    chart_coldstart_energy = (
        alt.Chart(df_energy_coldstart_unpivot)
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


    # -----------------------------------------------------------------------------
    # Chart 3: Latency Range & Tail Spread (Min to Max Range)
    # -----------------------------------------------------------------------------
    chart_coldstart_spread = (
        alt.Chart(df_metrics_coldstart)
        .mark_rule(size=3, color="#555")
        .encode(
            y=alt.Y("dir_name:N", title="Runtime", sort="x"),
            x=alt.X("latency_p95_min:Q", title="P95 Latency Range (Min to Max ms)"),
            x2=alt.X2("latency_p95_max:Q"),
        )
        + alt.Chart(df_metrics_coldstart)
        .mark_point(size=70, filled=True, color="red")
        .encode(
            y=alt.Y("dir_name:N", sort="x"),
            x="latency_p95_median:Q",
            tooltip=["dir_name", "latency_p95_median", "latency_cv"],
        )
    ).properties(
        title="Response Time Variance (Range + Red Median Dot)", width=350, height=200
    )


    # -----------------------------------------------------------------------------
    # Chart 4: Speed vs. Energy Efficiency Trade-off (Log-Log Scatter)
    # -----------------------------------------------------------------------------
    chart_coldstart_tradeoff = (
        alt.Chart(df_metrics_coldstart)
        .mark_circle(size=140, opacity=0.9)
        .encode(
            x=alt.X(
                "latency_p95_mean:Q",
                title="P95 Latency (ms) [Log Scale]",
                scale=alt.Scale(type="log"),
            ),
            y=alt.Y(
                "total_joules:Q",
                title="Total Energy (Joules) [Log Scale]",
                scale=alt.Scale(type="log"),
            ),
            color=alt.Color("dir_name:N", title="Target"),
            tooltip=[
                "dir_name",
                "latency_p95_mean",
                "total_joules",
                "latency_cv",
            ],
        )
        .properties(
            title="Speed vs. Energy Trade-off (Bottom-Left is Best)",
            width=350,
            height=200,
        )
    )


    # -----------------------------------------------------------------------------
    # Combined 2x2 Dashboard
    # -----------------------------------------------------------------------------
    coldstart_dashboard = (
        (chart_coldstart_latency | chart_coldstart_energy)
        & (chart_coldstart_spread | chart_coldstart_tradeoff)
    ).resolve_scale(color="independent")

    # Render dashboard
    coldstart_dashboard
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Scaling
    """)
    return


@app.cell
def _(df_scaling):
    df_scaling
    return


@app.cell
def _(alt, df_metrics_scaling, pl, targets_color_scale):
    # Chart B: Throughput (RPS Mean + Min/Max Range)
    rps_bars_scaling = (
        alt.Chart(df_metrics_scaling)
        .mark_bar(color="#4c78a8")
        .encode(
            y=alt.Y("dir_name:N", title="Target", sort="-x"),
            x=alt.X("mean_rps:Q", title="Requests / Sec"),
            color=alt.Color("dir_name:N", scale=targets_color_scale, legend=None)
        )
    )

    rps_range_scaling = (
        alt.Chart(df_metrics_scaling)
        .mark_rule(color="#111111", strokeWidth=2)
        .encode(x="dir_name:N", y="min_rps:Q", y2="max_rps:Q")
    )

    chart_rps_scaling = (rps_bars_scaling).properties(
        title=alt.TitleParams(
            text="Avg Throughput Capacity (RPS)",
            subtitle="13 min, n=10, VUs=0 to 100, Higher is better"
        )
        , width=320, height=260
    )

    ###
    # Chart E: Cost per Load: Normalized Resource Efficiency (Grouped Bar Chart)
    # 1. Unpivot normalized metrics into long format
    df_normalized_scaling = df_metrics_scaling.unpivot(
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
    chart_normalized_cost_scaling = (
        alt.Chart(df_normalized_scaling)
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

    # requests per joule
    rpj_bars_scaling = (
        alt.Chart(df_metrics_scaling)
        .mark_bar(opacity=0.85)
        .encode(
            y=alt.Y(
                "dir_name:N",
                title="Target",
                sort='x',
            ),
            x=alt.X("requests_per_joule:Q", title="Requests per Joule"),
            color=alt.Color("dir_name:N", scale=targets_color_scale, legend=None),
        )
    )

    chart_scaling_rpj = (
        (rpj_bars_scaling)
        .properties(
        title=alt.TitleParams(
            text="Workload Efficiency",
            subtitle="13 min, n=10, VUs=0 to 100, Higher is better"
        ),
        width=400, height=220,
        )
    )

    # 1. Base scatter plot (bubbles)
    bubbles_scaling = (
        alt.Chart(df_metrics_scaling)
        .mark_circle(opacity=0.85, stroke="black", strokeWidth=1)
        .encode(
            x=alt.X(
                "avg_memory_mb:Q",
                title="Avg Memory Footprint (MB)",
                scale=alt.Scale(zero=True),  # Force X-axis to start at 0
            ),
            y=alt.Y(
                "avg_cpu_pct:Q",
                title="Avg CPU Usage (%)",
                scale=alt.Scale(zero=True),  # Force Y-axis to start at 0
            ),
            size=alt.Size(
                "mean_pods:Q",
                title="Mean Active Pods",
                scale=alt.Scale(range=[600, 2200]),  # Scaled up slightly to fit text inside
                legend=None,
            ),
            color=alt.Color("dir_name:N", title="Target", scale=targets_color_scale, legend=None),
            tooltip=[
                alt.Tooltip("dir_name:N", title="Target"),
                alt.Tooltip("avg_memory_mb:Q", format=".1f", title="Avg Memory (MB)"),
                alt.Tooltip("avg_cpu_pct:Q", format=".2f", title="Avg CPU (%)"),
                alt.Tooltip("mean_pods:Q", format=".1f", title="Mean Pods"),
                alt.Tooltip("max_pods:Q", title="Peak Pods"),
            ],
        )
    )

    # 2. Inside text labels (Pod Count centered inside bubbles)
    pod_count_text = bubbles_scaling.mark_text(
        align="center",
        baseline="middle",
        fontWeight="bold",
        fontSize=11,
    ).encode(
        text=alt.Text("mean_pods:Q", format=".1f"),  # Formats pod count to 1 decimal
        size=alt.value(11),  # Prevents text size from scaling with bubble size
        color=alt.value("black"),
    )

    # 3. Outer labels (Target names placed slightly above the bubbles)
    runtime_text = bubbles_scaling.mark_text(
        align="center",
        baseline="bottom",
        dy=-25,  # Offsets text upwards above the circle edge
        fontWeight="bold",
        fontSize=12,
    ).encode(
        text="dir_name:N",
        size=alt.value(12),
        color=alt.value("black"),
    )

    # Layer together
    chart_footprint_scaling = (
        (bubbles_scaling + pod_count_text + runtime_text)
        .properties(
            title={
                "text": "Infrastructure Footprint Matrix",
                "subtitle": "Memory vs CPU Usage (Mean Pod Count labeled inside circles)",
            },
            width=500,
            height=380,
        )
    )
    return (
        chart_footprint_scaling,
        chart_normalized_cost_scaling,
        chart_rps_scaling,
        chart_scaling_rpj,
    )


@app.cell
def _(
    chart_footprint_scaling,
    chart_normalized_cost_scaling,
    chart_rps_scaling,
    chart_scaling_rpj,
):
    dashboard_scaling = (chart_rps_scaling | chart_normalized_cost_scaling) & (chart_scaling_rpj | chart_footprint_scaling)
    dashboard_scaling
    return


@app.cell
def _(df_all_metrics, pl):
    # Define the k6 benchmark phase boundaries in seconds
    # 0-240s   : Scale-Out Trigger (40 -> 80 VUs)
    # 240-600s : Peak Steady State (100 VUs)
    # 600s+    : Ramp-Down / Incomplete Cooldown
    # Load RPS data for correlation
    df_pods_scaling = df_all_metrics[("pods", "scaling")].rename({"value": "pods"})
    df_vus_scaling = df_all_metrics[("vus", "scaling")].rename({"value": "vus"})
    df_rps_scaling = df_all_metrics[("rps", "scaling")].rename({"value": "rps"})
    df_p95_scaling = df_all_metrics[("p95", "scaling")].rename({"value": "p95"})
    df_p99_scaling = df_all_metrics[("p99", "scaling")].rename({"value": "p99"})
    df_requests_scaling = df_all_metrics[("requests", "scaling")]
    df_joules_scaling = df_all_metrics[("requests", "scaling")]
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
        .join_asof(
            df_p95_scaling.drop("timestamp", "dir_name"),
            on="normalized_time",
            by="iteration",
            strategy="backward"
        )
        .drop_nulls()
    ).select(["normalized_time", "dir_name", "pods", "vus", "rps", "stage", "p95"])

    df_joined_avg = (
        df_joined
        .with_columns(
            # 1. Round/cast normalized_time to whole seconds to align iterations perfectly
            pl.col("normalized_time").round(0).cast(pl.Int64)
        )
        .group_by(["dir_name", "normalized_time"])
        .agg([
            # 2. Compute mean across iterations for each metric
            pl.col("vus").mean(),
            pl.col("pods").mean(),
            pl.col("rps").mean(),
            pl.col("p95").mean(),
            # Add any other numeric columns you want to average:
            # pl.col("cpu").mean(),
        ])
        .sort(["dir_name", "normalized_time"])
    )

    df_joined_avg
    return df_joined_avg, df_requests_scaling


@app.cell
def _(alt, df_joined_avg):
    # 1. Primary Line: VUs over Time
    vu_line = (
        alt.Chart(df_joined_avg)
        .mark_line(color="#E65100", strokeWidth=2)
        .encode(
            x=alt.X("normalized_time:Q", title="Time (s)", scale=alt.Scale(zero=True)),
            y=alt.Y("vus:Q", title="Virtual Users (VUs)", axis=alt.Axis(titleColor="#E65100")),
        )
    )

    # 2. Secondary Line: Active Pods over Time
    pod_line = (
        alt.Chart(df_joined_avg)
        .mark_line(color="#1A237E", strokeWidth=2, strokeDash=[4, 4])
        .encode(
            x="normalized_time:Q",
            y=alt.Y("pods:Q", title="Active Pods", axis=alt.Axis(titleColor="#1A237E")),
        )
    )

    # Layer 2: RPS (Actual Throughput)
    rps_line = (
        alt.Chart(df_joined_avg)
        .mark_line(color="#2E7D32", strokeDash=[3, 3])
        .encode(
            x="normalized_time:Q",
            y=alt.Y("rps:Q", title="Requests/sec", axis=alt.Axis(titleColor="#2E7D32"))
        )
    )

    # Layer 2: P95 Latency (Response Degredation)
    p95_line = (
        alt.Chart(df_joined_avg)
        .mark_line(color="#C62828", strokeWidth=2)
        .encode(
            x="normalized_time:Q",
            y=alt.Y("p95:Q", title="P95 Latency (ms)", axis=alt.Axis(titleColor="#C62828"))
        )
    )

    # 3. Layer and attach data FIRST
    layered = (
        alt.layer(vu_line, pod_line, rps_line, data=df_joined_avg)
        #.resolve_scale(y="independent")
        .properties(width=300, height=180)
    )

    # 4. Facet with title passed cleanly at top level
    scaling_responsiveness_chart = layered.facet(
        facet=alt.Facet("dir_name:N", title="Runtime Environment"),
        columns=2,
        title=alt.TitleParams(
            text="KEDA Autoscaling Responsiveness: VUs vs Active Pods",
            subtitle="Orange (Solid) = User Load Demand | Blue (Dashed) = Pod Replica Response",
        ),
    )

    scaling_responsiveness_chart
    return rps_line, vu_line


@app.cell
def _(alt, rps_line, vu_line):
    chart_p95_scaling = alt.layer(vu_line, rps_line).resolve_scale(y="independent")
    chart_p95_scaling
    return


@app.cell
def _(df_joined_avg, pl):
    df_penalty = (
        df_joined_avg
        # 1. Find the baseline P95 latency for each target under minimal load (VUs > 0)
        .with_columns(
            pl.col("p95")
            .filter(pl.col("vus") > 0)
            .min()
            .over("dir_name")
            .alias("p95_baseline")
        )
        # 2. Compute marginal penalty per VU, safely handling vus = 0
        .with_columns(
            pl.when(pl.col("vus") > 0)
            .then((pl.col("p95") - pl.col("p95_baseline")) / pl.col("vus"))
            .otherwise(0.0)
            .alias("penalty_ms_per_vu")
        )
    )

    df_penalty
    return (df_penalty,)


@app.cell
def _(alt, df_penalty):
    chart_penalty_vs_vu = (
        alt.Chart(df_penalty)
        .mark_line(strokeWidth=2.5, interpolate="monotone")
        .encode(
            x=alt.X(
                "vus:Q", 
                title="Virtual Users (Load)", 
                scale=alt.Scale(zero=True)
            ),
            y=alt.Y(
                "penalty_ms_per_vu:Q",
                title="Added Latency per VU (ms/VU)",
                scale=alt.Scale(zero=True)
            ),
            color=alt.Color("dir_name:N", title="Runtime"),
            tooltip=[
                alt.Tooltip("dir_name:N", title="Runtime"),
                alt.Tooltip("vus:Q", title="VUs"),
                alt.Tooltip("p95:Q", format=".2f", title="P95 Latency (ms)"),
                alt.Tooltip("penalty_ms_per_vu:Q", format=".3f", title="Penalty (ms/VU)"),
            ],
        )
        .properties(
            title={
                "text": "Concurrency Degradation Rate",
                "subtitle": "Added P95 Latency per Virtual User above baseline load",
            },
            width=500,
            height=300,
        )
    )

    chart_penalty_vs_vu
    return


@app.cell
def _(df_requests_scaling):
    df_requests_scaling
    return


@app.cell
def _(alt, df_requests_scaling, pl):
    category_map = {
        "/players/:id": "simple",
        "/teams/:id": "simple",
        "/match/:id": "simple",
        "/players/record/:id": "detailed",
        "/teams/record/:id": "detailed",
        "/match/team/:id": "lookup",
        "/match/result-table": "aggregate",
    }

    # Group by 'name' and compute total requests
    df_pie_data = (
        df_requests_scaling
       .with_columns(
            # 2. Safely map categories, falling back to 'other' for unmapped routes
            pl.col("name")
            .cast(pl.String)
            .replace_strict(category_map, default="other")
            .alias("category")
        )
        .group_by("category")
        .agg(pl.len().alias("total_requests"))  # pl.len() counts rows per group
        .with_columns(
            (pl.col("total_requests") / pl.col("total_requests").sum() * 100).alias("pct")
        )
        .sort("total_requests", descending=True)
    )



    base = alt.Chart(df_pie_data).encode(
        theta=alt.Theta("total_requests:Q", stack=True),
        color=alt.Color("category:N", title="Request Type / Category"),
        tooltip=[
            alt.Tooltip("category:N", title="Category"),
            alt.Tooltip("total_requests:Q", format=",", title="Total Requests"),
            alt.Tooltip("pct:Q", format=".1f", title="Percentage (%)"),
        ]
    )

    # 1. Arc slices
    arcs = base.mark_arc(
        innerRadius=50,  # Set to 0 if you want a solid pie chart instead of a donut
        outerRadius=120,
        stroke="white",
        strokeWidth=2
    )

    # 2. Percentage labels directly on the slices
    text = base.mark_text(
        radius=85,
        fontWeight="bold",
        fontSize=13,
        fill="white"
    ).encode(
        text=alt.Text("pct:Q", format=".1f")
    )

    pie_chart = (
        (arcs + text)
        .properties(
            title={
                "text": "Request Distribution by Category",
                "subtitle": "Total volume per request category over 13-minute run",
            },
            width=350,
            height=350,
        )
        .configure_view(strokeWidth=0)
    )

    pie_chart
    return (df_pie_data,)


@app.cell
def _(df_pie_data):
    df_pie_data
    return


if __name__ == "__main__":
    app.run()
