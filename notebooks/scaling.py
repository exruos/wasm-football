import marimo

__generated_with = "0.23.16"
app = marimo.App(width="full")

with app.setup:
    import altair as alt
    import marimo as mo
    import polars as pl
    from common_notebook import build_scenario_table, load_scenario_metrics, color_scale
    alt.data_transformers.enable("vegafusion")


@app.cell
def _():
    select_scaling_columns = ["url"]
    df_scaling = load_scenario_metrics("scaling", select_columns=select_scaling_columns)
    df_scaling
    return (df_scaling,)


@app.cell
def _():
    # Processing & visualization helpers for scaling benchmarks (Polars + Altair)
    # -------------------------------------------------------------------------
    # 1. Window Definitions
    # -------------------------------------------------------------------------
    WINDOWS = {
        "scale_up": (0.0, 240.0),
        "ramp_up": (240.0, 480.0),
        "steady": (480.0, 600.0),
        "cooldown": (660.0, 780.0),
        "overall": (0.0, 780.0),
    }


    def window_slice(df: pl.DataFrame) -> pl.DataFrame:
        """
        Assign rows to evaluation windows.
        Duplicates rows so timestamps belong to both specific micro-stages AND 'overall'.
        """
        t = pl.col("normalized_time")

        # Explicit parenthesization is REQUIRED for Polars bitwise operators (&)
        micro_expr = (
            pl.when((t >= 0.0) & (t < 240.0))
            .then(pl.lit("scale_up"))
            .when((t >= 240.0) & (t < 480.0))
            .then(pl.lit("ramp_up"))
            .when((t >= 480.0) & (t < 600.0))
            .then(pl.lit("steady"))
            .when((t >= 660.0) & (t <= 780.0))
            .then(pl.lit("cooldown"))
            .otherwise(None)
        )

        df_micro = df.with_columns(micro_expr.alias("window")).filter(
            pl.col("window").is_not_null()
        )

        df_overall = df.filter((t >= 0.0) & (t <= 780.0)).with_columns(
            pl.lit("overall").alias("window")
        )

        return pl.concat([df_micro, df_overall], how="vertical")


    # -------------------------------------------------------------------------
    # 2. Windowed Aggregations & Metric Processing
    # -------------------------------------------------------------------------
    def aggregate_metric_windows(df: pl.DataFrame) -> pl.DataFrame:
        """
        For a metric dataframe with columns (target, iteration, normalized_time, value),
        compute per-iteration windowed aggregates, then summarize across iterations:
        mean, std, min, max, p95 (of per-iteration aggregated values) grouped by target and window.
        """
        dfw = window_slice(df)

        per_iter = dfw.group_by(["target", "window", "iteration"]).agg(
            pl.col("value").mean().alias("iter_mean")
        )

        summary = (
            per_iter.group_by(["target", "window"])
            .agg([
                pl.col("iter_mean").mean().alias("mean"),
                pl.col("iter_mean").std().alias("std"),
                pl.col("iter_mean").min().alias("min"),
                pl.col("iter_mean").max().alias("max"),
                pl.col("iter_mean").quantile(0.95).alias("p95"),
            ])
            .sort(["target", "window"])
        )
        return summary


    def process_pod_joules(df_pj: pl.DataFrame, df_requests: pl.DataFrame):
        """
        Pivot RAPL energy by zone (package/dram), compute totals, and produce:
          - per-target, per-iteration sums during steady window
          - summary across iterations
        Efficiency = (total_joules / total_requests) * 10_000 computed over steady window (480-600s)
        Returns: (joined_per_iteration, summary)
        """
        # Grouped conditional aggregation for RAPL zones
        # 1. Pivot the data securely
        pj_wide = (
            df_pj.group_by(["target", "iteration", "normalized_time"])
            .agg([
                pl.col("value").filter(pl.col("zone") == "package").max().fill_null(0.0).alias("package"),
                pl.col("value").filter(pl.col("zone") == "dram").max().fill_null(0.0).alias("dram"),
            ])
            .with_columns((pl.col("package") + pl.col("dram")).alias("total"))
            # 2. Sort is CRITICAL before diffing time series
            .sort(["target", "iteration", "normalized_time"]) 
        )

        steady_mask = (pl.col("normalized_time") >= WINDOWS["steady"][0]) & (
            pl.col("normalized_time") < WINDOWS["steady"][1]
        )

        # 3. Calculate diffs safely over partitions, then sum inside the window
        pj_steady = (
            pj_wide.with_columns([
                pl.col("package").diff().over(["target", "iteration"]).clip(lower_bound=0.0).alias("pkg_diff"),
                pl.col("dram").diff().over(["target", "iteration"]).clip(lower_bound=0.0).alias("dram_diff"),
                pl.col("total").diff().over(["target", "iteration"]).clip(lower_bound=0.0).alias("total_diff"),
            ])
            .filter(steady_mask)
            .group_by(["target", "iteration"])
            .agg([
                pl.col("pkg_diff").sum().alias("package_joules"),
                pl.col("dram_diff").sum().alias("dram_joules"),
                pl.col("total_diff").sum().alias("total_joules"),
            ])
        )

        req_steady = (
            df_requests
            .sort(["target", "iteration", "normalized_time"])
            .with_columns(
                pl.col("value").diff().over(["target", "iteration"]).clip(lower_bound=0.0).alias("req_diff")
            )
            .filter(steady_mask)
            .group_by(["target", "iteration"])
            .agg(pl.col("req_diff").sum().alias("requests_total"))
        )

        joined = pj_steady.join(
            req_steady, on=["target", "iteration"], how="left"
        ).with_columns(
            pl.when(pl.col("requests_total") > 0)
            .then(pl.col("total_joules") / pl.col("requests_total") * 10_000.0)
            .otherwise(None)
            .alias("joules_per_10k_requests")
        )

        summary = (
            joined.group_by("target")
            .agg([
                pl.col("package_joules").mean().alias("package_mean"),
                pl.col("package_joules").std().alias("package_std"),
                pl.col("dram_joules").mean().alias("dram_mean"),
                pl.col("dram_joules").std().alias("dram_std"),
                pl.col("total_joules").mean().alias("total_mean"),
                pl.col("total_joules").std().alias("total_std"),
                pl.col("joules_per_10k_requests").mean().alias("efficiency_mean"),
                pl.col("joules_per_10k_requests").std().alias("efficiency_std"),
            ])
            .sort("target")
        )
        return joined, summary


    def process_scale_up_responsiveness(
        df_p95: pl.DataFrame, df_checks: pl.DataFrame, df_pods: pl.DataFrame
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """
        Evaluates cold starts and scaling contention during Scale-Up (0s–240s):
          - max_latency_spike_p95: Peak P95 latency spike while waiting for HPA/pods
          - min_checks_rate: Worst success check rate (detects dropped requests/5xx errors)
          - max_pod_count: Pod count reached during initial scale-up
        """
        scale_up_mask = (pl.col("normalized_time") >= WINDOWS["scale_up"][0]) & (
            pl.col("normalized_time") < WINDOWS["scale_up"][1]
        )

        p95_spikes = (
            df_p95.filter(scale_up_mask)
            .group_by(["target", "iteration"])
            .agg(pl.col("value").max().alias("max_latency_spike_p95"))
        )

        checks_drops = (
            df_checks.filter(scale_up_mask)
            .group_by(["target", "iteration"])
            .agg(pl.col("value").min().alias("min_checks_rate"))
        )

        pods_scale = (
            df_pods.filter(scale_up_mask)
            .group_by(["target", "iteration"])
            .agg(pl.col("value").max().alias("scale_up_max_pods"))
        )

        joined = p95_spikes.join(
            checks_drops, on=["target", "iteration"], how="left"
        ).join(pods_scale, on=["target", "iteration"], how="left")

        summary = (
            joined.group_by("target")
            .agg([
                pl.col("max_latency_spike_p95").mean().alias("p95_spike_mean"),
                pl.col("max_latency_spike_p95").max().alias("p95_spike_max"),
                pl.col("min_checks_rate").mean().alias("checks_rate_min_mean"),
                pl.col("scale_up_max_pods").mean().alias("scale_up_pods_mean"),
            ])
            .sort("target")
        )
        return joined, summary


    def process_memory_efficiency(
        df_memory: pl.DataFrame, df_pods: pl.DataFrame
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """
        Computes memory footprint metrics during Steady State (480s–600s).
        Assumes df_memory values are ALREADY in Megabytes (MB) per pod.
        """
        steady_mask = (pl.col("normalized_time") >= WINDOWS["steady"][0]) & (
            pl.col("normalized_time") < WINDOWS["steady"][1]
        )

        mem_steady = (
            df_memory.filter(steady_mask)
            .group_by(["target", "iteration"])
            .agg(pl.col("value").mean().alias("memory_per_pod_mb"))
        )

        pods_steady = (
            df_pods.filter(steady_mask)
            .group_by(["target", "iteration"])
            .agg(pl.col("value").mean().alias("avg_pod_count"))
        )

        joined = mem_steady.join(
            pods_steady, on=["target", "iteration"], how="left"
        ).with_columns(
            (pl.col("memory_per_pod_mb") * pl.col("avg_pod_count")).alias(
                "total_cluster_memory_mb"
            )
        )

        summary = (
            joined.group_by("target")
            .agg([
                pl.col("memory_per_pod_mb").mean().alias("memory_per_pod_mb_mean"),
                pl.col("memory_per_pod_mb").std().alias("memory_per_pod_mb_std"),
                pl.col("total_cluster_memory_mb")
                .mean()
                .alias("total_cluster_memory_mb_mean"),
                pl.col("total_cluster_memory_mb")
                .std()
                .alias("total_cluster_memory_mb_std"),
            ])
            .sort("target")
        )

        return joined, summary


    def process_cooldown_idle_drain(
        df_cpu: pl.DataFrame, df_memory: pl.DataFrame
    ) -> pl.DataFrame:
        """
        Evaluates residual CPU and Memory utilization during Cooldown (660s–780s).
        """
        cooldown_mask = (pl.col("normalized_time") >= WINDOWS["cooldown"][0]) & (
            pl.col("normalized_time") <= WINDOWS["cooldown"][1]
        )

        cpu_idle = (
            df_cpu.filter(cooldown_mask)
            .group_by(["target", "iteration"])
            .agg(pl.col("value").mean().alias("cooldown_cpu_mean"))
        )

        mem_idle = (
            df_memory.filter(cooldown_mask)
            .group_by(["target", "iteration"])
            .agg(pl.col("value").mean().alias("cooldown_mem_per_pod_mb"))
        )

        summary = (
            cpu_idle.join(mem_idle, on=["target", "iteration"], how="left")
            .group_by("target")
            .agg([
                pl.col("cooldown_cpu_mean").mean().alias("idle_cpu_cores_mean"),
                pl.col("cooldown_mem_per_pod_mb").mean().alias("idle_mem_mb_mean"),
            ])
            .sort("target")
        )
        return summary


    def process_all_metrics_summary(df_scaling: dict) -> pl.DataFrame:
        """
        Generates a master benchmark summary table across targets.
        """
        steady_mask = (pl.col("normalized_time") >= WINDOWS["steady"][0]) & (
            pl.col("normalized_time") < WINDOWS["steady"][1]
        )

        p95_steady = (
            df_scaling["p95"]
            .filter(steady_mask)
            .group_by("target")
            .agg(pl.col("value").mean().alias("p95_latency_ms"))
        )
        p99_steady = (
            df_scaling["p99"]
            .filter(steady_mask)
            .group_by("target")
            .agg(pl.col("value").mean().alias("p99_latency_ms"))
        )
        rps_steady = (
            df_scaling["rps"]
            .filter(steady_mask)
            .group_by("target")
            .agg(pl.col("value").mean().alias("steady_rps"))
        )
        checks_steady = (
            df_scaling["checks_rate"]
            .filter(steady_mask)
            .group_by("target")
            .agg(pl.col("value").min().alias("min_checks_rate"))
        )
        cpu_steady = (
            df_scaling["cpu_usage"]
            .filter(steady_mask)
            .group_by("target")
            .agg(pl.col("value").mean().alias("cpu_usage_cores"))
        )

        _, energy_sum = process_pod_joules(
            df_scaling["pod_joules"], df_scaling["requests"]
        )
        _, scale_up_sum = process_scale_up_responsiveness(
            df_scaling["p95"], df_scaling["checks_rate"], df_scaling["pods"]
        )
        _, mem_sum = process_memory_efficiency(
            df_scaling["memory"], df_scaling["pods"]
        )

        master_table = (
            p95_steady.join(p99_steady, on="target", how="left")
            .join(rps_steady, on="target", how="left")
            .join(checks_steady, on="target", how="left")
            .join(cpu_steady, on="target", how="left")
            .join(
                mem_sum.select([
                    "target",
                    "memory_per_pod_mb_mean",
                    "total_cluster_memory_mb_mean",
                ]),
                on="target",
                how="left",
            )
            .join(
                energy_sum.select(["target", "total_mean", "efficiency_mean"]),
                on="target",
                how="left",
            )
            .join(
                scale_up_sum.select(["target", "p95_spike_mean"]),
                on="target",
                how="left",
            )
            .rename({
                "total_mean": "steady_joules_total",
                "efficiency_mean": "joules_per_10k_req",
                "p95_spike_mean": "scale_up_p95_spike_ms",
            })
            .sort("target")
        )

        return master_table


    # -------------------------------------------------------------------------
    # 3. Altair Visualization Helpers
    # -------------------------------------------------------------------------
    def _timeseries_aggregates_for_metrics(metrics: list, df_scaling: dict):
        """
        Build a long-form DataFrame with mean and stddev bounds across iterations.
        """
        parts = []
        for m in metrics:
            df = df_scaling[m]
            agg = (
                df.group_by(["target", "normalized_time"])
                .agg([
                    pl.col("value").mean().alias("mean"),
                    pl.col("value").std().fill_null(0.0).alias("std"),
                ])
                .with_columns([
                    (pl.col("mean") - pl.col("std"))
                    .clip(lower_bound=0)
                    .alias("band_lower"),
                    (pl.col("mean") + pl.col("std")).alias("band_upper"),
                    pl.lit(m).alias("metric"),
                ])
            )
            parts.append(agg)
        return pl.concat(parts, how="vertical").sort(
            ["metric", "target", "normalized_time"]
        )


    def make_timeseries_charts(
        df_scaling: dict, metrics: list = ["rps", "pods", "cpu_usage", "p95"]
    ) -> dict:
        """
        Builds individual standalone Altair time-series charts for each metric.
        Returns a dict mapping metric_name -> alt.Chart.
        """
        df_agg = _timeseries_aggregates_for_metrics(metrics, df_scaling)
        charts = {}

        for metric in metrics:
            # 1. Filter aggregated data per metric
            df_metric = df_agg.filter(pl.col("metric") == metric)

            base = alt.Chart(df_metric)

            # 2. Build area band (+/- 1 stddev)
            band = base.mark_area(opacity=0.2).encode(
                x=alt.X("normalized_time:Q", title="Normalized Time (s)"),
                y=alt.Y("band_lower:Q", title=f"{metric.upper()} Value"),
                y2="band_upper:Q",
                color=alt.Color("target:N", scale=color_scale(), title="Target"),
            )

            # 3. Build mean line
            line = base.mark_line().encode(
                x="normalized_time:Q",
                y="mean:Q",
                color="target:N",
            )

            # 4. Layer band + line AND set properties on the individual chart
            chart = (
                alt.layer(band, line)
                .properties(
                    title=f"{metric.upper()} over Time",
                    width=650,
                    height=150
                )
                .interactive()
            )

            charts[metric] = chart

        return charts


    def make_rapl_energy_chart(df_scaling: dict):
        """
        Stacked bar chart comparing package & dram energy during steady state.
        """
        _, summary = process_pod_joules(
            df_scaling["pod_joules"], df_scaling["requests"]
        )

        # Melt package and dram ONLY so stacked height equals actual total
        df_plot = (
            summary.select([
                pl.col("target"),
                pl.col("package_mean").alias("package"),
                pl.col("dram_mean").alias("dram"),
            ])
            .unpivot(
                index="target", variable_name="domain", value_name="joules"
            )
        )

        chart = (
            alt.Chart(df_plot)
            .mark_bar()
            .encode(
                x=alt.X("joules:Q", title="Mean Joules (Steady Window)"),
                y=alt.Y("target:N", title="Target"),
                color=alt.Color("domain:N", title="RAPL Zone"),
                tooltip=["target", "domain", "joules"],
            )
            .properties(
               width=250, height=180, title="Steady-State RAPL Energy Breakdown"
            )
        )
        return chart


    def make_peak_boxplots(df_scaling: dict, metrics=["p95", "memory", "pods"]):
        """
        Boxplots comparing metrics across targets during steady and cooldown windows.
        """
        samples = []
        for m in metrics:
            df = df_scaling[m]
            dfw = window_slice(df)
            per_iter = (
                dfw.group_by(["target", "window", "iteration"])
                .agg(pl.col("value").mean().alias("sample"))
                .filter(pl.col("window").is_in(["steady", "cooldown"]))
                .with_columns(pl.lit(m).alias("metric"))
            )
            samples.append(
                per_iter.select(
                    ["target", "window", "iteration", "sample", "metric"]
                )
            )

        combined = pl.concat(samples, how="vertical")

        chart = (
            alt.Chart(combined)
            .mark_boxplot()
            .encode(
                x=alt.X("sample:Q", title="Sample Value"),
                y=alt.Y("target:N", title="Target"),
                color=alt.Color("window:N", title="Window"),
                column=alt.Column(
                    "metric:N", header=alt.Header(labelOrient="bottom")
                ),
            )
           .properties(width=250, height=200)
        )
        return chart

    return (
        make_peak_boxplots,
        make_rapl_energy_chart,
        make_timeseries_charts,
        process_all_metrics_summary,
        process_cooldown_idle_drain,
        process_pod_joules,
        process_scale_up_responsiveness,
    )


@app.cell
def _(df_scaling, process_pod_joules):
    joined_df, summary_df = process_pod_joules(df_scaling["pod_joules"], df_scaling["requests"])

    summary_df
    return (summary_df,)


@app.cell
def _(df_scaling, make_rapl_energy_chart, summary_df):
    # Get data and chart

    energy_chart = make_rapl_energy_chart(df_scaling)

    # Create a clean horizontal layout
    mo.vstack([
        mo.md("## 📊 Steady-State Performance & Energy Summary"),
        mo.hstack([
            summary_df.select([
                "target", "total_mean", "efficiency_mean"
            ]),
            energy_chart
        ], align="center", justify="space-around")
    ])
    return


@app.cell
def _(
    df_scaling,
    make_peak_boxplots,
    make_rapl_energy_chart,
    make_timeseries_charts,
    process_all_metrics_summary,
    process_cooldown_idle_drain,
):
    df_master_summary = process_all_metrics_summary(df_scaling)
    df_cooldown_idle = process_cooldown_idle_drain(
        df_scaling["cpu_usage"], df_scaling["memory"]
    )

    # 3. Generate Charts
    ts_charts = make_timeseries_charts(
        df_scaling, metrics=["rps", "pods", "cpu_usage", "p95"]
    )
    chart_energy = make_rapl_energy_chart(df_scaling)
    chart_boxplots = make_peak_boxplots(
        df_scaling, metrics=["p95", "memory", "pods"]
    )
    return (
        chart_boxplots,
        chart_energy,
        df_cooldown_idle,
        df_master_summary,
        ts_charts,
    )


@app.cell
def _(df_scaling, process_scale_up_responsiveness):
    df_responsiveness = process_scale_up_responsiveness(
        df_scaling["p95"], df_scaling["checks_rate"], df_scaling["pods"]
    )
    df_responsiveness
    return


@app.cell
def _(df_master_summary):
    df_master_summary
    return


@app.cell
def _(ts_charts):
    ts_charts["rps"]
    return


@app.cell
def _(ts_charts):
    ts_charts["pods"]
    return


@app.cell
def _(ts_charts):
    ts_charts["cpu_usage"]
    return


@app.cell
def _(ts_charts):
    ts_charts["p95"]
    return


@app.cell
def _(chart_energy):
    chart_energy
    return


@app.cell
def _(chart_boxplots):
    chart_boxplots
    return


@app.cell
def _(df_cooldown_idle):
    df_cooldown_idle
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    Based on your benchmark data, `df_cooldown_idle` measures the **idle resource footprint** (CPU and RAM baseline consumption) of each application runtime during the **cooldown phase** (typically after load has dropped back down to zero).

    Because these applications are sitting idle with minimal to no active HTTP traffic, these metrics isolate the **fixed runtime overhead** required just to keep the service running in Kubernetes.

    ---

    ### 📊 Metric Definitions

    * **`idle_cpu_cores_mean`**: Average CPU capacity consumed while idle (expressed in milli-cores or percentage points depending on your Prometheus scraper scale).
    * **`idle_mem_mb_mean`**: Baseline Resident Set Size (RSS) memory consumption in Megabytes (MB) per runtime while idle.

    ---

    ### 🔎 Key Insights from Your Results

    | Runtime Baseline Comparison | Key Takeaway |
    | --- | --- |
    | **Lowest Memory Footprint** | **`oci-axum` (~17.0 MB)** and **`wasm-rust` (~29.8 MB)** maintain an exceptionally tiny baseline memory presence due to compiled, garbage-collector-free binaries. |
    | **Highest Memory Footprint** | **`oci-spring` (~691.0 MB)** requires significantly more baseline RAM due to the JVM process overhead, class-loading, and garbage collection framework heaps. |
    | **Lowest CPU Overhead** | **`wasm-js` (~1.22 cores)** and **`oci-axum` (~2.18 cores)** consume the least background CPU idle cycles. |
    | **Highest CPU Overhead** | **`oci-native` (~7.58 cores)** shows higher background CPU usage, which often points to active GC thread polling or runtime runtime maintenance loops while waiting for incoming connections. |

    ---

    ### 💡 How to use this in your Benchmark Report

    Use `df_cooldown_idle` to highlight **density and cost efficiency**:

    1. **Node Density:** Runtimes like `axum` or `wasm-rust` allow you to pack **10x to 40x more idle container replicas** on a single cloud server compared to heavy Java/JVM stacks (`oci-spring`).
    2. **Cold Start & Baseline Costs:** In auto-scaling microservices (like Knative or KEDA), high idle resource baselines drive up cloud infrastructure bills even when traffic is low.
    """)
    return


@app.cell(hide_code=True)
def _(df_scaling):
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
        df_scaling["requests"]
       .with_columns(
            # 2. Safely map categories, falling back to 'other' for unmapped routes
            pl.col("url")
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


@app.cell
def _(df_metrics_scaling, df_rps_sum_scaling_avg, targets_color_scale):
    # Chart B: Throughput (RPS Mean + Min/Max Range)
    rps_bars_scaling = (
        alt.Chart(df_rps_sum_scaling_avg)
        .mark_bar(color="#4c78a8")
        .encode(
            y=alt.Y("target:N", title="Target", sort="-x"),
            x=alt.X("mean_rps:Q", title="Requests / Sec"),
            color=alt.Color("target:N", scale=color_scale(), legend=None)
        )
    )

    rps_range_scaling = (
        alt.Chart(df_rps_sum_scaling_avg)
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
            y=alt.Y("dir_name:N", title="Target", sort="x"),
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
            color=alt.Color("target:N", title="Target", scale=color_scale(), legend=None),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
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
    target_text = bubbles_scaling.mark_text(
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
        (bubbles_scaling + pod_count_text + target_text)
        .properties(
            title={
                "text": "Infrastructure Footprint Matrix",
                "subtitle": "Memory vs CPU Usage (Mean Pod Count labeled inside circles)",
            },
            width=500,
            height=380,
        )
    )
    return


if __name__ == "__main__":
    app.run()
