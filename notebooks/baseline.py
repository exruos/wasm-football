import marimo

__generated_with = "0.23.15"
app = marimo.App(width="medium")

with app.setup:
    import altair as alt
    import marimo as mo
    import polars as pl
    from common_notebook import build_scenario_table, load_scenario_metrics


@app.cell
def _():
    select_baseline_columns = ["status", "url"]
    df_baseline_metrics = load_scenario_metrics("baseline", select_columns=select_baseline_columns)
    df_baseline_metrics
    return (df_baseline_metrics,)


@app.cell
def _(df_baseline_metrics):
    df_baseline = build_scenario_table(df_baseline_metrics)

    if len(df_baseline) > 0:
        # Latency & Responsiveness metrics
        df_latency_baseline = df_baseline.group_by("target").agg([
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
            pl.col("^latency_(p95|p99|stddev).*$").exclude("latency_cv") * 1000
        )

        # Throughput metrics
        # requests is a global cumulative counter (shared across targets).
        # total_requests = the final cumulative value (max across all iterations).
        df_throughput_baseline = df_baseline.group_by("target").agg([
            pl.col("rps").mean().alias("mean_rps"),
            pl.col("rps").max().alias("max_rps"),
            pl.col("rps").min().alias("min_rps"),
            # requests is a global cumulative counter (shared across targets).
                # total_requests = the final cumulative value (max across all iterations).
                pl.col("iterations").max().alias("total_requests"),
        ])

        # Resource metrics (CPU/Memory)
        df_resource_baseline = df_baseline.group_by("target").agg([
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
        df_raw_joules_baseline = df_baseline_metrics[("pod_joules")]

        # 1. Compute net energy per (target, iteration, zone)
        df_iter_zone_baseline = df_raw_joules_baseline.group_by(["target", "iteration", "zone"]).agg(
            (pl.col("value").max() - pl.col("value").min()).alias("iter_joules")
        )

        # 2. Average across iterations per zone, then pivot zones into separate columns
        df_energy_pivoted_baseline = (
            df_iter_zone_baseline.group_by(["target", "zone"])
            .agg(pl.col("iter_joules").mean())
            .pivot(on="zone", index="target", values="iter_joules")
        )

        # 3. Join back and calculate package, dram, total, and per-request metrics
        df_energy_baseline = (
            df_throughput_baseline.join(df_energy_pivoted_baseline, on="target", how="left")
            .with_columns(
                (pl.col("package") + pl.col("dram")).alias("total_joules")
            )
            .select([
                "target",
                pl.col("package").alias("cpu_joules"),
                pl.col("dram").alias("dram_joules"),
                "total_joules",
                (pl.col("total_joules") / pl.col("total_requests")).alias("joules_per_request"),
                (pl.col("total_requests") / pl.col("total_joules")).alias("requests_per_joule"),
            ])
        )

        # Combine all metrics into a single summary table
        df_metrics_baseline = df_latency_baseline.join(
            df_throughput_baseline, on=["target"], how="left"
        ).join(
            df_resource_baseline, on=["target"], how="left"
        ).join(
            df_energy_baseline, on=["target"], how="left"
        ).sort(["target"]).unique()
    else:
        df_metrics_baseline = pl.DataFrame()

    df_metrics_baseline
    return (df_metrics_baseline,)


@app.cell
def _(df_metrics_baseline):
    from common_notebook import color_scale
    # Chart A: P95 Latency + 95% CI Error Bars
    # 1. Base Bars
    bars_baseline_latency = (
        alt.Chart(df_metrics_baseline)
        .mark_bar(opacity=0.85)
        .encode(
            y=alt.Y(
                "target:N",
                title="Target",
                sort='x',
            ),
            x=alt.X("latency_p95_mean:Q", title="P95 Latency (ms)"),
            color=alt.Color("target:N", scale=color_scale(), legend=None),
        )
    )

    # 2. Error Bars
    error_baseline_latency = (
        alt.Chart(df_metrics_baseline)
        .mark_errorbar(color="#DC2626", size=14, ticks=True)
        .encode(
            y=alt.Y("target:N", sort='x'),
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
            y=alt.Y("target:N", title="Target", sort="-x"),
            x=alt.X("mean_rps:Q", title="Requests / Sec"),
            color=alt.Color("target:N", scale=color_scale(), legend=None)
        )
    )

    rps_range_baseline = (
        alt.Chart(df_metrics_baseline)
        .mark_rule(color="#111111", strokeWidth=2)
        .encode(x="target:N", y="min_rps:Q", y2="max_rps:Q")
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
        index="target",
        on=["cpu_joules", "dram_joules"],
        variable_name="Component",
        value_name="Joules",
    )

    chart_energy_baseline = (
        alt.Chart(df_energy_unpivot)
        .mark_bar()
        .encode(
            y=alt.Y("target:N", title="Target", sort="x"),
            x=alt.X("Joules:Q", title="Total Joules"),
            color=alt.Color(
                "Component:N", scale=alt.Scale(
                    domain=["cpu_joules", "dram_joules"],
                    range=["CPU", "DRAM (Memory)"],
                    scheme="category10"
                )
            ),
            tooltip=["target", "Component", "Joules"],
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
        index="target",
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
            y=alt.Y("target:N", title="Environment", sort="-x"),
            x=alt.X("Value:Q", title=None),
            color=alt.Color("target:N", scale=color_scale(), legend=None),
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
        index="target",
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
            y=alt.Y("target:N", title="Target", sort="x"),
            x=alt.X("CostPer1kRPS:Q", title="Cost per 1k RPS (Lower is better)"),
            color=alt.Color("target:N", scale=color_scale(), legend=None),
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
            color=alt.Color("target:N", scale=color_scale()),
            tooltip=["target", "mean_rps", "requests_per_joule"],
        )
    )

    scatter_labels_baseline = scatter_baseline.mark_text(align="left", dx=10, dy=-2).encode(
        text="target:N"
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
        chart_energy_baseline,
        chart_latency_baseline,
        chart_normalized_cost_baseline,
        chart_resource_footprint_baseline,
        chart_rps_baseline,
        chart_tradeoff_baseline,
        color_scale,
    )


@app.cell
def _(chart_latency_baseline):
    chart_latency_baseline
    return


@app.cell
def _(
    chart_energy_baseline,
    chart_latency_baseline,
    chart_normalized_cost_baseline,
    chart_resource_footprint_baseline,
    chart_rps_baseline,
    chart_tradeoff_baseline,
):
    dashboard_baseline= (
        (chart_latency_baseline | chart_rps_baseline)
        & (chart_resource_footprint_baseline | chart_normalized_cost_baseline)
        & (chart_energy_baseline | chart_tradeoff_baseline)
    )

    chart_widget_baseline = mo.ui.altair_chart(dashboard_baseline)
    chart_widget_baseline
    return


@app.cell
def _():
    select_idle_metrics = ["all"]
    df_idle_metrics = load_scenario_metrics("idle", select_columns=select_idle_metrics)
    df_idle_metrics
    return (df_idle_metrics,)


@app.cell
def _(df_idle_metrics):
    df_idle = build_scenario_table(df_idle_metrics)
    df_idle
    return (df_idle,)


@app.cell
def _(color_scale, df_idle):
    chart_idle = (
        alt.Chart(df_idle)
        .mark_bar()
        .encode(
            x=alt.X(field='pod_joules', type='quantitative', aggregate='mean'),
            y=alt.Y(field='target', type='nominal', sort='x', stack=False),
            color=alt.Color(field='target', scale=color_scale(), legend=None, type='nominal'),
            tooltip=[
                alt.Tooltip(field='target'),
                alt.Tooltip(field='pod_joules', aggregate='mean', format=',.2f'),
                alt.Tooltip(field='target')
            ]
        )
        .properties(
            height=290,
            width=420,
            config={
                'axis': {
                    'grid': False
                }
            }
        )
    )
    chart_idle
    return


if __name__ == "__main__":
    app.run()
