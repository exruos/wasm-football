import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")

with app.setup:
    import altair as alt
    import marimo as mo
    import polars as pl
    from pathlib import Path
    from common_notebook import (
        build_scenario_table,
        color_scale,
        counter_increments,
        export_all,
        export_table,
        load_scenario_metrics,
        pareto_frontier,
        pod_joules_increments,
        save_chart,
        target_color,
        target_rank,
        thesis_chart,
        FIGURE_DIR,
        TABLE_DIR,
        TARGET_ORDER,
    )


@app.cell
def md_intro():
    mo.md(r"""
    # Baseline benchmark: fixed capacity, fixed work

    Every target runs as a **single fixed pod** - no HPA, no KEDA, no scaling - and
    serves exactly the same amount of work, so the only free variables are how long
    it takes and what it costs.

    **k6 scenario** (`per-vu-iterations`): 32 VUs x 3 125 iterations =
    **100 000 requests per run**, `maxDuration` 10 m, repeated **10 times per target**.

    Every request hits one computationally heavy endpoint with several database
    queries behind it:

    ```
    GET /match/result-table?season={random}&leagueName={random}
    ```

    with `season` and `leagueName` drawn at random per iteration, so the workload
    exercises query execution rather than a response cache.

    **Why this scenario reads differently from the scaling one.** The request count
    is constant, so *total energy per run is directly comparable between targets* -
    no request-rate normalization needed. What varies is **completion time**, which
    means a slow target can still win on energy if its average power draw is low
    enough. The two questions this notebook answers are therefore:

    1. how long does each runtime need to grind through 100 000 heavy requests, and
    2. how many joules does that cost - both in total and per unit of work.

    An accompanying **idle** scenario (10 min, no traffic) gives each runtime's
    baseline power draw, which separates fixed overhead from the dynamic cost of
    actually serving requests.
    """)
    return


@app.cell
def load_baseline():
    # Label columns kept from the raw Prometheus/k6 parquets:
    #   status / url  - k6 request outcome and route (the baseline hits one endpoint)
    #   pod_name      - per-pod Kepler energy counters; the baseline runs a single pod,
    #                   but the key keeps the diff correct across a pod restart.
    select_baseline_columns = ["status", "url", "pod_name", "pod"]
    df_baseline_metrics = load_scenario_metrics("baseline", select_columns=select_baseline_columns)
    df_baseline_metrics
    return (df_baseline_metrics,)


@app.cell
def processing_helpers():
    # Baseline benchmark analysis - constants & Polars processing helpers
    # -------------------------------------------------------------------------
    # 1. Experiment constants
    # -------------------------------------------------------------------------
    BASELINE_VUS = 32
    BASELINE_ITERATIONS_PER_VU = 3125
    # Fixed work per run: this is what makes total energy directly comparable.
    BASELINE_REQUESTS = BASELINE_VUS * BASELINE_ITERATIONS_PER_VU  # 100_000
    BASELINE_ENDPOINT = "/match/result-table?season={random}&leagueName={random}"

    # k6 latency histograms are scraped in seconds; the thesis reports milliseconds.
    LATENCY_SCALE_MS = 1000.0

    # Single source of truth for target ordering: matches common_notebook.color_scale()

    # Runs differ in length by a factor of ~5, so phases are defined as a fraction
    # of each run's own progress rather than in absolute seconds. "warmup" captures
    # JIT/JVM/connection-pool warm-up, "steady" the settled remainder.
    PHASES = {
        "warmup": (0.0, 0.1),
        "steady": (0.1, 1.0),
        "overall": (0.0, 1.0),
    }
    PHASE_ORDER = ["warmup", "steady", "overall"]
    PHASE_LABELS = {
        "warmup": "Warm-up (first 10% of run)",
        "steady": "Steady (last 90% of run)",
        "overall": "Overall (whole run)",
    }

    METRIC_LABELS = {
        "rps": ("Throughput", "req/s"),
        "p95": ("P95 Latency", "ms"),
        "p99": ("P99 Latency", "ms"),
        "cpu_usage": ("CPU Usage", "cores"),
        "memory": ("Memory", "MB"),
        "iterations": ("Completed requests", "requests"),
        "pod_joules": ("Pod Energy (cumulative)", "J"),
    }

    # Metrics scraped in seconds that the report shows in milliseconds.
    LATENCY_METRICS = {"p95", "p99"}

    # Metrics whose targets span orders of magnitude: log axis by default.
    LOG_SCALE_METRICS = {"p95", "p99"}
    LOG_FLOOR = 1e-2


    def metric_title(metric: str) -> str:
        label, unit = METRIC_LABELS.get(metric, (metric.upper(), ""))
        return f"{label} ({unit})" if unit else label


    def metric_values(df: pl.DataFrame, metric: str) -> pl.DataFrame:
        """Metric frame with `value` converted to the reporting unit (ms for latency)."""
        if metric in LATENCY_METRICS:
            return df.with_columns(pl.col("value") * LATENCY_SCALE_MS)
        return df


    # -------------------------------------------------------------------------
    # 2. Run progress and phases
    # -------------------------------------------------------------------------
    def with_progress(df: pl.DataFrame) -> pl.DataFrame:
        """
        Add `progress` in [0, 1]: position within a run, normalized by that run's own
        length. Required because a run takes 61 s on one target and 304 s on another,
        so absolute timestamps are not comparable across targets.
        """
        return df.with_columns(
            (
                pl.col("normalized_time")
                / pl.col("normalized_time").max().over(["target", "iteration"])
            )
            .fill_nan(0.0)
            .alias("progress")
        )


    def phase_slice(df: pl.DataFrame) -> pl.DataFrame:
        """
        Assign rows to phases by run progress, duplicating them into "overall" so a
        sample contributes to both its phase and the whole-run summary.
        """
        dfp = with_progress(df)
        p = pl.col("progress")

        micro = (
            pl.when(p < PHASES["warmup"][1])
            .then(pl.lit("warmup"))
            .otherwise(pl.lit("steady"))
        )

        return pl.concat(
            [
                dfp.with_columns(micro.alias("phase")),
                dfp.with_columns(pl.lit("overall").alias("phase")),
            ],
            how="vertical",
        )


    # -------------------------------------------------------------------------
    # 3. Energy processing (RAPL package + dram)
    # -------------------------------------------------------------------------


    def process_baseline_energy(df_metrics: dict) -> pl.DataFrame:
        """Joules per run, split by RAPL zone (one row per target and iteration)."""
        return (
            pod_joules_increments(df_metrics["pod_joules"])
            .group_by(["target", "iteration"])
            .agg([
                pl.col("package").sum().alias("package_joules"),
                pl.col("dram").sum().alias("dram_joules"),
                pl.col("total").sum().alias("total_joules"),
            ])
            .sort(target_rank())
        )


    def process_energy_phases(df_metrics: dict) -> pl.DataFrame:
        """Joules per run phase, to show what warm-up costs relative to steady work."""
        per_iter = (
            phase_slice(pod_joules_increments(df_metrics["pod_joules"]))
            .group_by(["target", "iteration", "phase"])
            .agg([
                pl.col("package").sum().alias("package_joules"),
                pl.col("dram").sum().alias("dram_joules"),
                pl.col("total").sum().alias("total_joules"),
                pl.col("normalized_time").max().alias("phase_end_s"),
                pl.col("normalized_time").min().alias("phase_start_s"),
            ])
            .with_columns(
                (pl.col("phase_end_s") - pl.col("phase_start_s")).alias("phase_duration_s")
            )
        )
        return (
            per_iter.group_by(["target", "phase"])
            .agg([
                pl.col("package_joules").mean().alias("package_mean"),
                pl.col("dram_joules").mean().alias("dram_mean"),
                pl.col("total_joules").mean().alias("total_mean"),
                pl.col("total_joules").std().alias("total_std"),
                pl.col("phase_duration_s").mean().alias("duration_mean_s"),
            ])
            .with_columns([
                (pl.col("total_mean") / pl.col("duration_mean_s")).alias("mean_power_w"),
                pl.col("phase").replace_strict(PHASE_LABELS, default=pl.col("phase")).alias("phase_label"),
            ])
            .sort([target_rank(), pl.col("phase").replace_strict({p: i for i, p in enumerate(PHASE_ORDER)}, default=9)])
        )


    # Share of each idle capture discarded before averaging. The idle scenario
    # starts about 15 s after the deployment is applied, so the head of every
    # capture is the pod finishing start-up - class loading, pool initialisation,
    # framework boot - and not idle draw at all. Measured over the full capture,
    # five of six targets accumulate 90-99 % of their energy in the first fifth and
    # then flatline, which inflates the reported figure by one to two orders of
    # magnitude and scrambles the ordering between targets.
    #
    # 0.2 of a ten-minute capture drops two minutes and leaves eight. The exact
    # cutoff does not drive the result: 0.10, 0.20, 0.33 and 0.50 agree to within a
    # few per cent on every target, which is the check that justifies picking one.
    IDLE_WARMUP_FRACTION = 0.2


    def process_idle_power(df_idle: dict) -> pl.DataFrame:
        """
        Baseline power draw of an idle pod (no traffic), in watts. Used to separate
        fixed runtime overhead from the dynamic cost of serving requests.

        The start-up head of each capture is discarded; see IDLE_WARMUP_FRACTION.
        """
        _increments = pod_joules_increments(df_idle["pod_joules"])
        _spans = _increments.group_by(["target", "iteration"]).agg(
            pl.col("normalized_time").max().alias("_span_s")
        )
        per_iter = (
            _increments.join(_spans, on=["target", "iteration"], how="left")
            .filter(pl.col("normalized_time") > pl.col("_span_s") * IDLE_WARMUP_FRACTION)
            .group_by(["target", "iteration"])
            .agg([
                pl.col("total").sum().alias("idle_joules"),
                (pl.col("normalized_time").max() - pl.col("normalized_time").min()).alias("idle_duration_s"),
            ])
            .with_columns(
                (pl.col("idle_joules") / pl.col("idle_duration_s")).alias("idle_power_w")
            )
        )
        return (
            per_iter.group_by("target")
            .agg([
                pl.col("idle_power_w").mean().alias("idle_power_w"),
                pl.col("idle_power_w").std().alias("idle_power_w_std"),
                pl.col("idle_duration_s").mean().alias("idle_duration_s"),
            ])
            .sort(target_rank())
        )


    # -------------------------------------------------------------------------
    # 4. Per-run frame and summaries
    # -------------------------------------------------------------------------
    def build_baseline_runs(df_metrics: dict, df_idle: dict | None = None) -> pl.DataFrame:
        """
        One row per (target, iteration) with everything needed for the comparisons:
        completion time, effective throughput, latency, resources and energy.

          duration_s        - wall-clock time to complete BASELINE_REQUESTS
          effective_rps     - BASELINE_REQUESTS / duration_s
          joules_per_10k_requests - total energy normalized to 10k requests
          mean_power_w      - total joules / duration, i.e. how hard the box worked
          idle_share_pct    - share of the run's energy an idle pod would have drawn
        """
        duration = (
            df_metrics["p95"]
            .group_by(["target", "iteration"])
            .agg(pl.col("normalized_time").max().alias("duration_s"))
        )

        def _mean(metric: str, alias: str) -> pl.DataFrame:
            return (
                metric_values(df_metrics[metric], metric)
                .group_by(["target", "iteration"])
                .agg(pl.col("value").mean().alias(alias))
            )

        def _max(metric: str, alias: str) -> pl.DataFrame:
            return (
                metric_values(df_metrics[metric], metric)
                .group_by(["target", "iteration"])
                .agg(pl.col("value").max().alias(alias))
            )

        frame = (
            duration
            .join(_mean("p95", "p95_ms"), on=["target", "iteration"], how="left")
            .join(_mean("p99", "p99_ms"), on=["target", "iteration"], how="left")
            .join(_max("p95", "p95_max_ms"), on=["target", "iteration"], how="left")
            .join(_mean("rps", "rps"), on=["target", "iteration"], how="left")
            .join(_mean("cpu_usage", "cpu_usage"), on=["target", "iteration"], how="left")
            .join(_mean("memory", "memory_mb"), on=["target", "iteration"], how="left")
            .join(_max("memory", "memory_peak_mb"), on=["target", "iteration"], how="left")
            .join(_max("iterations", "completed_requests"), on=["target", "iteration"], how="left")
            .join(process_baseline_energy(df_metrics), on=["target", "iteration"], how="left")
            .with_columns([
                (BASELINE_REQUESTS / pl.col("duration_s")).alias("effective_rps"),
                (pl.col("total_joules") / BASELINE_REQUESTS * 10_000.0).alias("joules_per_10k_requests"),
                (BASELINE_REQUESTS / pl.col("total_joules")).alias("requests_per_joule"),
                (pl.col("total_joules") / pl.col("duration_s")).alias("mean_power_w"),
                (pl.col("dram_joules") / pl.col("total_joules") * 100).alias("dram_share_pct"),
            ])
        )

        if df_idle is not None:
            frame = frame.join(
                process_idle_power(df_idle).select(["target", "idle_power_w"]),
                on="target",
                how="left",
            ).with_columns([
                (pl.col("idle_power_w") * pl.col("duration_s")).alias("idle_energy_j"),
                (pl.col("total_joules") - pl.col("idle_power_w") * pl.col("duration_s")).alias("dynamic_energy_j"),
            ]).with_columns(
                (pl.col("idle_energy_j") / pl.col("total_joules") * 100).alias("idle_share_pct")
            )

        return frame.sort([target_rank(), pl.col("iteration")])


    BASELINE_SUMMARY_COLUMNS = [
        "duration_s",
        "effective_rps",
        "rps",
        "p95_ms",
        "p99_ms",
        "p95_max_ms",
        "cpu_usage",
        "memory_mb",
        "memory_peak_mb",
        "package_joules",
        "dram_joules",
        "total_joules",
        "joules_per_10k_requests",
        "requests_per_joule",
        "mean_power_w",
        "dram_share_pct",
    ]


    def summarize_baseline_runs(df_runs: pl.DataFrame) -> pl.DataFrame:
        """Mean and sd across the 10 runs for every per-run column, plus idle context."""
        cols = [c for c in BASELINE_SUMMARY_COLUMNS if c in df_runs.columns]
        extra = [c for c in ("idle_power_w", "idle_energy_j", "dynamic_energy_j", "idle_share_pct") if c in df_runs.columns]

        return (
            df_runs.group_by("target")
            .agg(
                [pl.col(c).mean().alias(c) for c in cols]
                + [pl.col(c).std().alias(f"{c}_std") for c in cols]
                + [pl.col(c).mean().alias(c) for c in extra]
                + [pl.len().alias("n_runs")]
            )
            .sort(target_rank())
        )


    def build_phase_summary(
        df_metrics: dict, metrics: list[str] | None = None
    ) -> pl.DataFrame:
        """
        mean/std/min/max/p95 per (metric, target, phase), where each of the 10 runs
        is first reduced to one sample inside the phase - so `std` is run-to-run
        variance rather than sample noise.
        """
        metrics = metrics or ["rps", "p95", "p99", "cpu_usage", "memory"]
        parts = []
        for metric in metrics:
            if metric not in df_metrics:
                continue
            per_iter = (
                phase_slice(metric_values(df_metrics[metric], metric))
                .group_by(["target", "phase", "iteration"])
                .agg(pl.col("value").mean().alias("iter_value"))
            )
            parts.append(
                per_iter.group_by(["target", "phase"])
                .agg([
                    pl.col("iter_value").mean().alias("mean"),
                    pl.col("iter_value").std().alias("std"),
                    pl.col("iter_value").min().alias("min"),
                    pl.col("iter_value").max().alias("max"),
                    pl.col("iter_value").quantile(0.95).alias("p95"),
                    pl.len().alias("n_runs"),
                ])
                .with_columns([
                    pl.lit(metric).alias("metric"),
                    pl.col("phase").replace_strict(PHASE_LABELS, default=pl.col("phase")).alias("phase_label"),
                ])
            )

        return (
            pl.concat(parts, how="vertical")
            .select(["metric", "target", "phase", "phase_label", "mean", "std", "min", "max", "p95", "n_runs"])
            .with_columns([
                target_rank().alias("_t"),
                pl.col("phase").replace_strict({p: i for i, p in enumerate(PHASE_ORDER)}, default=9).alias("_p"),
            ])
            .sort(["metric", "_p", "_t"])
            .drop("_t", "_p")
        )


    return (
        LOG_FLOOR,
        LOG_SCALE_METRICS,
        build_baseline_runs,
        build_phase_summary,
        metric_title,
        metric_values,
        process_energy_phases,
        process_idle_power,
        summarize_baseline_runs,
        with_progress,
    )


@app.cell
def chart_helpers(
    LOG_FLOOR,
    LOG_SCALE_METRICS,
    metric_title,
    metric_values,
    with_progress,
):
    # Altair visualization helpers - every target encoding uses color_scale()
    # -------------------------------------------------------------------------
    CHART_WIDTH = 620
    CHART_HEIGHT = 170

    # Samples arrive every 100 ms; binning to whole seconds keeps the curves
    # identical to the eye and keeps exported SVGs small enough to embed in Typst.
    TS_BIN_SECONDS = 1.0
    PROGRESS_BINS = 100


    def _err_bounds(df: pl.DataFrame, column: str, floor: float | None = 0.0) -> pl.DataFrame:
        """Add `<column>_lo` / `<column>_hi` from `<column>` +/- its `_std` sibling."""
        std = f"{column}_std"
        if std not in df.columns:
            return df
        lo = pl.col(column) - pl.col(std)
        return df.with_columns([
            (lo.clip(lower_bound=floor) if floor is not None else lo).alias(f"{column}_lo"),
            (pl.col(column) + pl.col(std)).alias(f"{column}_hi"),
        ])


    def make_ranked_bar_chart(
        df_summary: pl.DataFrame,
        column: str,
        title: str,
        subtitle: str,
        x_title: str,
        x_log: bool = False,
        ascending: bool = True,
    ) -> alt.Chart:
        """
        Horizontal bar per target with +/-1 sd whiskers, ordered best-first.
        `ascending=True` means lower values rank first (time, energy, latency).
        """
        df_plot = _err_bounds(df_summary, column).sort(column, descending=not ascending)
        order = df_plot["target"].to_list()

        base = alt.Chart(df_plot).encode(y=alt.Y("target:N", title="Target", sort=order))
        x_enc = alt.X(
            f"{column}:Q",
            title=x_title,
            scale=alt.Scale(type="log", nice=False) if x_log else alt.Scale(),
        )

        bars = base.mark_bar(opacity=0.9).encode(
            x=x_enc,
            color=target_color(legend=False),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip(f"{column}:Q", title=x_title, format=",.2f"),
            ],
        )

        layers = [bars]
        if f"{column}_lo" in df_plot.columns:
            layers.append(
                base.mark_errorbar(color="#333", ticks=True).encode(
                    x=alt.X(f"{column}_lo:Q", title=x_title), x2=f"{column}_hi:Q"
                )
            )

        return alt.layer(*layers).properties(
            width=380, height=210, title={"text": title, "subtitle": subtitle}
        )


    def make_duration_chart(df_summary: pl.DataFrame) -> alt.Chart:
        """Wall-clock time to grind through the fixed 100k requests."""
        return make_ranked_bar_chart(
            df_summary,
            "duration_s",
            title="Time to complete 100 000 requests",
            subtitle="Mean of 10 runs, +/-1 sd. Lower is better",
            x_title="Duration (s)",
        )


    def make_latency_chart(df_summary: pl.DataFrame, log_x: bool = False) -> alt.Chart:
        """
        P95 and P99 latency per target, one row per percentile.

        Linear by default and deliberately so: a bar encodes magnitude by its length
        from zero, which a log axis destroys - it measures from the axis minimum, so
        the fastest target renders as a zero-length (invisible) bar. The spread here
        is only ~7.5x, well within what a linear axis shows clearly.
        """
        df_plot = (
            df_summary.select(["target", "p95_ms", "p99_ms"])
            .unpivot(index="target", variable_name="percentile", value_name="latency_ms")
            .with_columns(
                pl.col("percentile").replace_strict({"p95_ms": "P95", "p99_ms": "P99"}, default="?")
            )
        )
        order = df_summary.sort("p95_ms")["target"].to_list()

        return (
            alt.Chart(df_plot)
            .mark_bar(opacity=0.9)
            .encode(
                y=alt.Y("target:N", title=None, sort=order),
                x=alt.X(
                    "latency_ms:Q",
                    title="Latency (ms)" + (" - log scale" if log_x else ""),
                    # stack=False is REQUIRED on a log axis: Vega-Lite cannot stack
                    # a log scale and silently renders nothing if asked to.
                    stack=False,
                    scale=alt.Scale(type="log", nice=False) if log_x else alt.Scale(zero=True),
                    axis=alt.Axis(values=[10, 20, 50, 100, 200], format=",.0f")
                    if log_x
                    else alt.Axis(format=",.0f"),
                ),
                color=target_color(legend=False),
                row=alt.Row("percentile:N", title=None, sort=["P95", "P99"]),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("percentile:N", title="Percentile"),
                    alt.Tooltip("latency_ms:Q", title="ms", format=".2f"),
                ],
            )
            .properties(
                width=360,
                height=140,
                title={"text": "Latency under fixed load", "subtitle": "32 VUs on a single pod, mean of 10 runs. Lower is better"},
            )
        )


    ZONE_COLORS = alt.Scale(domain=["package", "dram"], range=["#00758F", "#FC7C00"])


    def make_energy_domain_chart(
        df_summary: pl.DataFrame, x_log: bool = True, grouped: bool = True
    ) -> alt.Chart:
        """
        Package and DRAM joules per run, per target.

        grouped=True draws the two zones as separate bars (yOffset), which is the
        only honest option on a log axis: log scales cannot stack, and drawing both
        from the origin would hide the smaller DRAM bar behind the package bar.
        grouped=False stacks them on a linear axis, so bar length is total energy.
        """
        df_plot = (
            df_summary.select([
                pl.col("target"),
                pl.col("package_joules").alias("package"),
                pl.col("dram_joules").alias("dram"),
            ])
            .unpivot(index="target", variable_name="domain", value_name="joules")
        )
        order = df_summary.sort("total_joules")["target"].to_list()

        encoding = dict(
            y=alt.Y("target:N", title="Target", sort=order),
            x=alt.X(
                "joules:Q",
                title="Energy per run (J)" + (" - log scale" if x_log else ""),
                stack=False if grouped else "zero",
                # domainMin must sit BELOW the smallest value (oci-axum DRAM, 85 J).
                # On a log axis a bar is drawn from the domain minimum, so the
                # smallest value would otherwise have zero length and vanish.
                scale=alt.Scale(type="log", nice=False, domainMin=50) if x_log else alt.Scale(),
                # Explicit decade ticks: the default log ticks collide at this width.
                axis=alt.Axis(values=[50, 100, 300, 1000, 3000, 10000, 30000], format=",")
                if x_log
                else alt.Axis(format=","),
            ),
            color=alt.Color("domain:N", title="RAPL Zone", scale=ZONE_COLORS),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("domain:N", title="Zone"),
                alt.Tooltip("joules:Q", title="Joules", format=",.1f"),
            ],
        )
        if grouped:
            encoding["yOffset"] = alt.YOffset("domain:N", sort=["package", "dram"])

        subtitle = (
            "Identical work per target, so totals compare directly. Lower is better"
            if not grouped
            else "Package and DRAM shown separately; identical work per target. Lower is better"
        )

        return (
            alt.Chart(df_plot)
            .mark_bar()
            .encode(**encoding)
            .properties(
                width=380,
                height=230,
                title={"text": "Energy to serve 100 000 requests", "subtitle": subtitle},
            )
        )


    def make_energy_share_chart(df_summary: pl.DataFrame) -> alt.Chart:
        """
        The same split normalized to 100 %, so the DRAM share is comparable between
        targets whose absolute energy differs by 25x. Labels give the DRAM percent.
        """
        df_plot = (
            df_summary.select([
                pl.col("target"),
                pl.col("package_joules").alias("package"),
                pl.col("dram_joules").alias("dram"),
                pl.col("dram_share_pct"),
            ])
            .unpivot(
                index=["target", "dram_share_pct"],
                on=["package", "dram"],
                variable_name="domain",
                value_name="joules",
            )
        )
        order = df_summary.sort("dram_share_pct", descending=True)["target"].to_list()

        bars = (
            alt.Chart(df_plot)
            .mark_bar()
            .encode(
                y=alt.Y("target:N", title="Target", sort=order),
                x=alt.X("joules:Q", title="Share of run energy (%)", stack="normalize", axis=alt.Axis(format="%")),
                color=alt.Color("domain:N", title="RAPL Zone", scale=ZONE_COLORS),
                order=alt.Order("domain:N", sort="descending"),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("domain:N", title="Zone"),
                    alt.Tooltip("joules:Q", title="Joules", format=",.1f"),
                    alt.Tooltip("dram_share_pct:Q", title="DRAM share (%)", format=".1f"),
                ],
            )
        )
        # DRAM percent written inside the orange segment rather than in a side strip,
        # so the figure carries no second copy of the target axis.
        labels = (
            alt.Chart(df_summary)
            .mark_text(align="right", dx=-6, fontSize=11, color="white", fontWeight="bold")
            .encode(
                y=alt.Y("target:N", sort=order, title=None),
                x=alt.value(340),
                text=alt.Text("dram_share_pct:Q", format=".1f"),
            )
        )

        return (bars + labels).properties(
            width=340,
            height=230,
            title={
                "text": "RAPL domain split",
                "subtitle": "Package vs DRAM as a share of each run's energy (label = DRAM %)",
            },
        )


    def make_energy_domain_panel(df_summary: pl.DataFrame) -> alt.HConcatChart:
        """Absolute zone energy next to the normalized split - one figure for the thesis."""
        return alt.hconcat(
            make_energy_domain_chart(df_summary),
            make_energy_share_chart(df_summary),
            spacing=30,
        ).resolve_scale(color="shared")


    def make_power_chart(df_summary: pl.DataFrame) -> alt.Chart:
        """
        Mean power draw while working, with the idle draw of the same runtime as a
        reference tick: the gap is the dynamic cost of serving requests.
        """
        df_plot = _err_bounds(df_summary, "mean_power_w").sort("mean_power_w")
        order = df_plot["target"].to_list()
        base = alt.Chart(df_plot).encode(y=alt.Y("target:N", title="Target", sort=order))

        bars = base.mark_bar(opacity=0.9).encode(
            x=alt.X("mean_power_w:Q", title="Mean power (W)"),
            color=target_color(legend=False),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("mean_power_w:Q", title="Under load (W)", format=".2f"),
                alt.Tooltip("idle_power_w:Q", title="Idle (W)", format=".3f"),
                alt.Tooltip("idle_share_pct:Q", title="Idle share of run (%)", format=".2f"),
            ],
        )
        layers = [bars]
        if "mean_power_w_lo" in df_plot.columns:
            layers.append(
                base.mark_errorbar(color="#333", ticks=True).encode(
                    x=alt.X("mean_power_w_lo:Q", title="Mean power (W)"), x2="mean_power_w_hi:Q"
                )
            )
        if "idle_power_w" in df_plot.columns:
            layers.append(
                base.mark_tick(color="#111", thickness=2, size=18).encode(
                    x=alt.X("idle_power_w:Q", title="Mean power (W)")
                )
            )

        return alt.layer(*layers).properties(
            width=380,
            height=210,
            title={
                "text": "Power draw under load",
                "subtitle": "Bar = mean while serving, black tick = idle draw of the same runtime",
            },
        )


    def make_idle_power_chart(df_idle_power: pl.DataFrame) -> alt.Chart:
        """Idle scenario: what a pod costs while doing nothing at all."""
        return make_ranked_bar_chart(
            df_idle_power.rename({"idle_power_w_std": "idle_power_w_std"}),
            "idle_power_w",
            title="Idle power draw (no traffic)",
            subtitle="10-minute idle scenario, mean of all runs. Lower is better",
            x_title="Idle power (W)",
        )


    def make_resource_chart(df_summary: pl.DataFrame) -> alt.Chart:
        """CPU and memory footprint of the single pod, side by side."""
        df_plot = (
            df_summary.select([
                pl.col("target"),
                pl.col("cpu_usage").alias("CPU (mean)"),
                pl.col("memory_mb").alias("Memory (MB, mean)"),
            ])
            .unpivot(index="target", variable_name="resource", value_name="value")
        )
        return (
            alt.Chart(df_plot)
            .mark_bar(opacity=0.9)
            .encode(
                y=alt.Y("target:N", title=None, sort=TARGET_ORDER),
                x=alt.X("value:Q", title=None),
                color=target_color(legend=False),
                column=alt.Column("resource:N", title=None, header=alt.Header(labelFontSize=12)),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("resource:N", title="Resource"),
                    alt.Tooltip("value:Q", title="Value", format=",.2f"),
                ],
            )
            .properties(width=220, height=200, title="Single-pod resource footprint")
            .resolve_scale(x="independent")
        )


    # -------------------------------------------------------------------------
    # Time series
    # -------------------------------------------------------------------------
    def _timeseries_aggregates(
        df_metrics: dict,
        metric: str,
        x: str = "time",
        band: str = "std",
        bin_seconds: float = TS_BIN_SECONDS,
    ) -> pl.DataFrame:
        """
        Mean across runs per time bin, plus a variance band.
        x="time"     - elapsed seconds (runs end at different times)
        x="progress" - percent of the run completed, so targets are comparable
        """
        df = metric_values(df_metrics[metric], metric)

        if x == "progress":
            df = with_progress(df).with_columns(
                ((pl.col("progress") * PROGRESS_BINS).floor() / PROGRESS_BINS * 100).alias("x")
            )
        else:
            df = df.with_columns(
                ((pl.col("normalized_time") / bin_seconds).floor() * bin_seconds).alias("x")
            )

        agg = (
            df.group_by(["target", "x"])
            .agg([
                pl.col("value").mean().alias("mean"),
                pl.col("value").std().fill_null(0.0).alias("std"),
                pl.col("value").min().alias("min"),
                pl.col("value").max().alias("max"),
            ])
        )
        if band == "minmax":
            lower, upper = pl.col("min"), pl.col("max")
        else:
            lower, upper = pl.col("mean") - pl.col("std"), pl.col("mean") + pl.col("std")

        return agg.with_columns([
            lower.clip(lower_bound=0).alias("band_lower"),
            upper.alias("band_upper"),
        ]).sort(["target", "x"])


    def make_timeseries_chart(
        df_metrics: dict,
        metric: str,
        x: str = "time",
        band: str = "std",
        y_scale: str = "auto",
    ) -> alt.Chart:
        """
        One metric over the course of a run: mean line per target + variance band.
        y_scale: "auto" (log for LOG_SCALE_METRICS), "log" or "linear".
        """
        df_plot = _timeseries_aggregates(df_metrics, metric, x=x, band=band)

        log_y = y_scale == "log" or (y_scale == "auto" and metric in LOG_SCALE_METRICS)
        if log_y:
            # log(0) is undefined: lift the band floor, drop zero-valued means.
            df_plot = df_plot.filter(pl.col("mean") > 0).with_columns([
                pl.col("band_lower").clip(lower_bound=LOG_FLOOR),
                pl.col("band_upper").clip(lower_bound=LOG_FLOOR),
            ])

        y_def = alt.Scale(type="log", nice=False) if log_y else alt.Scale(zero=False)
        y_title = metric_title(metric) + (" - log scale" if log_y else "")
        x_title = "Run progress (% of requests completed)" if x == "progress" else "Elapsed time (s)"

        base = alt.Chart(df_plot)
        x_enc = alt.X("x:Q", title=x_title, scale=alt.Scale(nice=False))

        area = base.mark_area(opacity=0.18).encode(
            x=x_enc,
            y=alt.Y("band_lower:Q", title=y_title, scale=y_def),
            y2="band_upper:Q",
            color=target_color(legend=False),
        )
        line = base.mark_line(strokeWidth=1.6).encode(
            x=x_enc,
            y=alt.Y("mean:Q", title=y_title, scale=y_def),
            color=target_color(),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("x:Q", title=x_title, format=".1f"),
                alt.Tooltip("mean:Q", title="Mean", format=".3f"),
            ],
        )

        band_note = "min-max across runs" if band == "minmax" else "mean +/-1 sd across runs"
        return (area + line).properties(
            width=CHART_WIDTH,
            height=CHART_HEIGHT,
            title={"text": metric_title(metric), "subtitle": f"{band_note}, 10 runs"},
        )


    def make_timeseries_charts(
        df_metrics: dict,
        metrics: list = ["rps", "p95", "cpu_usage", "memory"],
        x: str = "time",
        band: str = "std",
    ) -> dict:
        """Standalone time-series chart per metric: metric name -> alt.Chart."""
        return {m: make_timeseries_chart(df_metrics, m, x=x, band=band) for m in metrics}


    def make_timeseries_facet(
        df_metrics: dict,
        metrics: list = ["rps", "p95", "cpu_usage", "memory"],
        x: str = "time",
    ) -> alt.VConcatChart:
        """Stacked panel of the time-series charts - one figure for the thesis."""
        charts = make_timeseries_charts(df_metrics, metrics, x=x)
        subtitle = "aligned by run progress" if x == "progress" else "aligned by elapsed time"
        return (
            alt.vconcat(*[charts[m] for m in metrics], spacing=8)
            # y stays independent: panels mix linear and log axes.
            .resolve_scale(color="shared", x="shared", y="independent")
            .properties(title=f"Baseline run behaviour ({subtitle})")
        )


    # -------------------------------------------------------------------------
    # Distributions and trade-offs
    # -------------------------------------------------------------------------
    def make_run_boxplots(
        df_runs: pl.DataFrame,
        columns: list[str] | None = None,
        labels: dict | None = None,
    ) -> alt.Chart:
        """
        Run-to-run distribution per target: each box is 10 points, one per run, so
        the spread is reproducibility rather than within-run noise.
        """
        columns = columns or ["duration_s", "p95_ms", "total_joules", "memory_mb"]
        labels = labels or {
            "duration_s": "Duration (s)",
            "p95_ms": "P95 latency (ms)",
            "total_joules": "Energy per run (J)",
            "memory_mb": "Memory (MB)",
        }

        df_plot = (
            df_runs.select(["target", "iteration", *columns])
            .unpivot(index=["target", "iteration"], variable_name="metric", value_name="value")
            .with_columns(pl.col("metric").replace_strict(labels, default=pl.col("metric")).alias("metric_label"))
        )

        return (
            alt.Chart(df_plot)
            .mark_boxplot(size=12)
            .encode(
                x=alt.X("value:Q", title="Per-run value", scale=alt.Scale(zero=False)),
                y=alt.Y("target:N", title=None, sort=TARGET_ORDER),
                color=target_color(),
                column=alt.Column("metric_label:N", title=None, sort=[labels[c] for c in columns]),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("iteration:Q", title="Run"),
                    alt.Tooltip("value:Q", title="Value", format=",.2f"),
                ],
            )
            .properties(width=200, height=150, title="Run-to-run distributions (10 runs per target)")
            .resolve_scale(x="independent")
        )


    def _labeled_scatter(
        df_plot: pl.DataFrame,
        x: str,
        y: str,
        x_title: str,
        y_title: str,
        title: str,
        subtitle: str,
        x_log: bool = False,
        y_log: bool = False,
        frontier: pl.DataFrame | None = None,
        x_err: bool = False,
        y_err: bool = False,
        width: int = 420,
        height: int = 320,
        legend: bool = True,
    ) -> alt.Chart:
        """Shared scaffold: labeled point per target, median quadrant guides,
        optional +/-1 sd whiskers and a Pareto frontier line."""
        x_enc = alt.X(
            f"{x}:Q",
            title=x_title,
            scale=alt.Scale(type="log", nice=False) if x_log else alt.Scale(zero=False, padding=30),
        )
        y_enc = alt.Y(
            f"{y}:Q",
            title=y_title,
            scale=alt.Scale(type="log", nice=False) if y_log else alt.Scale(zero=False, padding=30),
        )

        base = alt.Chart(df_plot)
        layers = [
            alt.Chart(pl.DataFrame({"v": [df_plot[x].median()]}))
            .mark_rule(strokeDash=[4, 4], opacity=0.35)
            .encode(x=alt.X("v:Q", title=x_title)),
            alt.Chart(pl.DataFrame({"v": [df_plot[y].median()]}))
            .mark_rule(strokeDash=[4, 4], opacity=0.35)
            .encode(y=alt.Y("v:Q", title=y_title)),
        ]

        err_layers: list = []

        if frontier is not None and len(frontier) > 1:
            layers.append(
                alt.Chart(frontier)
                .mark_line(strokeDash=[6, 3], color="#888", strokeWidth=1.2)
                .encode(x=x_enc, y=y_enc)
            )
        if x_err and f"{x}_lo" in df_plot.columns:
            err_layers.append(
                base.mark_rule(opacity=1.0, strokeWidth=2.2).encode(
                    y=y_enc, x=alt.X(f"{x}_lo:Q", title=x_title), x2=f"{x}_hi:Q", color=target_color(legend=False)
                )
            )
        if y_err and f"{y}_lo" in df_plot.columns:
            err_layers.append(
                base.mark_rule(opacity=1.0, strokeWidth=2.2).encode(
                    x=x_enc, y=alt.Y(f"{y}_lo:Q", title=y_title), y2=f"{y}_hi:Q", color=target_color(legend=False)
                )
            )

        points = base.mark_point(size=55, filled=True, opacity=0.95).encode(
            x=x_enc,
            y=y_enc,
            color=target_color(legend=legend),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip(f"{x}:Q", title=x_title, format=",.2f"),
                alt.Tooltip(f"{y}:Q", title=y_title, format=",.2f"),
            ],
        )
        labels = base.mark_text(align="left", dx=9, dy=-7, fontSize=11).encode(
            x=x_enc, y=y_enc, text="target:N", color=target_color(legend=False)
        )

        return (
            alt.layer(*layers, points, *err_layers, labels)
            .properties(width=width, height=height, title={"text": title, "subtitle": subtitle})
            .resolve_scale(color="shared")
        )


    def make_efficiency_scatter(df_summary: pl.DataFrame) -> alt.Chart:
        """
        Energy cost vs. delivered throughput. Bottom-right wins: finish faster while
        spending fewer joules on the identical 100k-request workload.
        """
        df_plot = _err_bounds(_err_bounds(df_summary, "effective_rps"), "joules_per_10k_requests")
        frontier = pareto_frontier(df_plot, "effective_rps", "joules_per_10k_requests", True, True)
        return _labeled_scatter(
            df_plot,
            x="effective_rps",
            y="joules_per_10k_requests",
            x_title="Effective throughput (req/s)",
            y_title="Energy cost (J per 10k requests) - log scale",
            title="Energy cost vs. throughput",
            subtitle="Bottom-right is better. Dashed line = Pareto frontier. Run-to-run spread (+/-1 sd) is drawn but smaller than the marker on every target",
            y_log=True,
            frontier=frontier,
            x_err=True,
            y_err=True,
        )


    def make_power_duration_scatter(df_summary: pl.DataFrame) -> alt.Chart:
        """
        The trade-off unique to fixed-work benchmarks: a slow target can still be
        cheap if it draws little power. Energy is the product of the two axes, so
        points nearer the origin spent less on the same work.
        """
        df_plot = _err_bounds(_err_bounds(df_summary, "duration_s"), "mean_power_w")
        frontier = pareto_frontier(df_plot, "duration_s", "mean_power_w", False, True)
        return _labeled_scatter(
            df_plot,
            x="duration_s",
            y="mean_power_w",
            x_title="Time to complete 100k requests (s)",
            y_title="Mean power draw (W)",
            title="Speed vs. power draw",
            subtitle="Energy = time x power, so bottom-left is best. Dashed line = Pareto frontier",
            frontier=frontier,
            x_err=True,
            y_err=True,
        )


    def make_energy_latency_scatter(df_summary: pl.DataFrame) -> alt.Chart:
        """Does paying more energy buy lower latency? Bottom-left is best."""
        frontier = pareto_frontier(df_summary, "total_joules", "p95_ms", False, True)
        return _labeled_scatter(
            df_summary,
            x="total_joules",
            y="p95_ms",
            x_title="Energy per run (J) - log scale",
            y_title="P95 latency (ms) - log scale",
            title="Energy cost vs. responsiveness",
            subtitle="Bottom-left is better: cheap and fast. Dashed line = Pareto frontier",
            x_log=True,
            y_log=True,
            frontier=frontier,
            # Every point already carries its target as a text label, and with the
            # two Wasm points sitting at the right edge the legend overlapped them.
            legend=False,
        )


    def make_run_scatter(df_runs: pl.DataFrame, df_summary: pl.DataFrame) -> alt.Chart:
        """Every run as its own point, with a cross at the target mean."""
        runs = (
            alt.Chart(df_runs)
            .mark_point(size=45, opacity=0.5, filled=True)
            .encode(
                x=alt.X("effective_rps:Q", title="Effective throughput (req/s)", scale=alt.Scale(zero=False, padding=25)),
                y=alt.Y("total_joules:Q", title="Energy per run (J) - log scale", scale=alt.Scale(type="log", nice=False)),
                color=target_color(),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("iteration:Q", title="Run"),
                    alt.Tooltip("effective_rps:Q", title="req/s", format=".1f"),
                    alt.Tooltip("total_joules:Q", title="Joules", format=",.1f"),
                ],
            )
        )
        means = (
            alt.Chart(df_summary)
            .mark_point(size=200, filled=False, strokeWidth=2.5, shape="cross")
            .encode(x="effective_rps:Q", y="total_joules:Q", color=target_color(legend=False))
        )
        return (runs + means).properties(
            width=420,
            height=320,
            title={
                "text": "Per-run energy/throughput positions",
                "subtitle": "One point per run, cross = target mean. Tight clusters mean reproducible results",
            },
        )


    return (
        make_duration_chart,
        make_efficiency_scatter,
        make_energy_domain_chart,
        make_energy_domain_panel,
        make_energy_latency_scatter,
        make_energy_share_chart,
        make_idle_power_chart,
        make_latency_chart,
        make_power_chart,
        make_power_duration_scatter,
        make_resource_chart,
        make_run_boxplots,
        make_run_scatter,
        make_timeseries_charts,
        make_timeseries_facet,
    )


@app.cell
def baseline_summary(
    build_baseline_runs,
    build_phase_summary,
    df_baseline_metrics,
    df_idle_metrics,
    process_energy_phases,
    process_idle_power,
    summarize_baseline_runs,
):
    # Per-run frame and summaries
    df_idle_power = process_idle_power(df_idle_metrics)
    df_baseline_runs = build_baseline_runs(df_baseline_metrics, df_idle_metrics)
    df_metrics_baseline = summarize_baseline_runs(df_baseline_runs)
    df_baseline_phases = build_phase_summary(df_baseline_metrics)
    df_energy_phases = process_energy_phases(df_baseline_metrics)

    mo.vstack([
        mo.md("## Baseline summary - one row per target"),
        df_metrics_baseline.select([
            "target", "duration_s", "effective_rps", "p95_ms", "p99_ms",
            "total_joules", "joules_per_10k_requests", "mean_power_w",
            "cpu_usage", "memory_mb", "idle_power_w", "idle_share_pct",
        ]),
    ])
    return (
        df_baseline_phases,
        df_baseline_runs,
        df_energy_phases,
        df_idle_power,
        df_metrics_baseline,
    )


@app.cell(hide_code=True)
def md_summary():
    mo.md(r"""
    ## Headline results

    Every target served the identical 100 000 heavy requests from a single pod, so these
    columns compare like for like.

    **Completion time spans a factor of five**, with the JVM and the compiled container
    targets at the fast end and `oci-node` slowest.

    **Energy spans an order of magnitude more than that, and it does not follow speed.**
    `oci-node` is the *slowest* target yet spends the least energy, because its average
    power draw while working is very low. `wasm-js` is the opposite - slow *and*
    power-hungry - which is how it accumulates the largest bill in the field for
    identical work.

    **Latency broadly tracks completion time**, as expected with a fixed VU count. The
    one inversion is at the slow end: `oci-node` takes the longest overall but has the
    *lower* p95 of the two slowest targets. Duration follows *mean* latency rather than
    the tail, and `wasm-rust` carries the heavier tail on top of a lower mean.

    **Idle draw is negligible here.** The idle scenario puts every runtime far below the
    loaded draw, so idle overhead accounts for at most a per cent or so of any run's
    energy. Essentially all of the measured energy is dynamic - the cost of executing the
    requests, not of existing.

    Compared with the scaling scenario, where mixed light routes across several replicas
    keep the container targets close together, this compute-heavy single-pod workload
    separates the runtimes far more sharply.
    """)
    return


@app.cell
def charts(
    df_baseline_metrics,
    df_baseline_runs,
    df_metrics_baseline,
    make_duration_chart,
    make_efficiency_scatter,
    make_energy_domain_chart,
    make_energy_domain_panel,
    make_energy_latency_scatter,
    make_energy_share_chart,
    make_latency_chart,
    make_power_chart,
    make_power_duration_scatter,
    make_resource_chart,
    make_run_boxplots,
    make_run_scatter,
    make_timeseries_charts,
    make_timeseries_facet,
):
    # Chart construction
    chart_duration_baseline = make_duration_chart(df_metrics_baseline)
    chart_latency_baseline = make_latency_chart(df_metrics_baseline)
    chart_energy_baseline = make_energy_domain_chart(df_metrics_baseline)
    chart_energy_share = make_energy_share_chart(df_metrics_baseline)
    chart_energy_panel = make_energy_domain_panel(df_metrics_baseline)
    chart_power_baseline = make_power_chart(df_metrics_baseline)
    chart_resource_baseline = make_resource_chart(df_metrics_baseline)

    chart_efficiency_scatter = make_efficiency_scatter(df_metrics_baseline)
    chart_power_duration = make_power_duration_scatter(df_metrics_baseline)
    chart_energy_latency = make_energy_latency_scatter(df_metrics_baseline)
    chart_run_scatter = make_run_scatter(df_baseline_runs, df_metrics_baseline)

    chart_boxplots_baseline = make_run_boxplots(df_baseline_runs)

    ts_charts_time = make_timeseries_charts(df_baseline_metrics, x="time")
    ts_charts_progress = make_timeseries_charts(df_baseline_metrics, x="progress")
    chart_ts_panel = make_timeseries_facet(df_baseline_metrics, x="progress")
    return (
        chart_boxplots_baseline,
        chart_duration_baseline,
        chart_efficiency_scatter,
        chart_energy_baseline,
        chart_energy_latency,
        chart_energy_panel,
        chart_energy_share,
        chart_latency_baseline,
        chart_power_baseline,
        chart_power_duration,
        chart_resource_baseline,
        chart_run_scatter,
        chart_ts_panel,
        ts_charts_progress,
    )


@app.cell
def md_timeseries():
    mo.md(r"""
    ## How a run unfolds

    The panel is aligned by **run progress** (percent of the 100 000 requests completed)
    rather than wall-clock time, because runs differ in length by a factor of five - on an
    elapsed-time axis the fast targets simply stop early and the comparison becomes visual
    guesswork. `ts_charts_time` holds the elapsed-time version of the same charts for when
    the differing run lengths are the point.

    **Throughput is flat from the first sample.** Comparing the warm-up phase (first 10 %
    of a run) with the steady remainder, throughput barely moves on any target. A single
    fixed pod at 32 VUs is saturated immediately - there is no ramp to wait out, which is
    what makes the whole-run averages trustworthy.

    **Warm-up is not what separates the targets.** Only the fast container targets show
    the classic warm-up penalty, and it is small. All three slow targets show the
    *opposite*, being faster during warm-up than in steady state - which is queueing, not
    warming: with all 32 VUs released at once, latency on a saturated runtime climbs as
    the backlog builds and then holds. Either way the ranking is set within the first few
    seconds and never changes, so it reflects the runtime rather than a transient.

    `df_baseline_phases` holds the warm-up/steady/overall breakdown for every metric with
    the exact numbers.
    """)
    return


@app.cell
def view_timeseries(chart_ts_panel):
    chart_ts_panel
    return


@app.cell
def md_energy():
    mo.md(r"""
    ## Energy, power and the speed trade-off

    The energy chart uses a **log x-axis** - the spread across targets is too wide for a
    linear scale to show the cheap ones at all - so package and DRAM are drawn as
    **separate bars rather than stacked**: a log axis cannot stack, and drawing both from
    the origin would bury the smaller DRAM bar behind the package one. The companion chart
    normalizes the same split to 100 %.

    **Package dominates everywhere**, but the DRAM share is not constant: `oci-node`
    spends materially more of its energy in DRAM than any other target. That follows from
    how it earns its low total - DRAM refresh cost accrues with *time*, and `oci-node`
    runs far longer than the fast targets, so a low-power run accumulates proportionally
    more of its bill in memory.

    The power chart separates *how hard* a runtime drives the CPU from *how long* it does
    so. The black tick marks the same runtime's idle draw - the distance from tick to bar
    is the dynamic cost of serving traffic, and it is essentially the whole bar for every
    target.

    The speed-vs-power scatter is the key figure for this scenario: **energy is the product
    of the two axes**, so any target above and to the right of another loses on both counts.
    `oci-node` sits bottom-right (slow but very cheap per second), `oci-spring` and
    `oci-axum` bottom-left (fast and moderate), and `wasm-js` top-right.
    """)
    return


@app.cell
def dashboard(
    chart_duration_baseline,
    chart_energy_panel,
    chart_latency_baseline,
    chart_power_baseline,
    chart_resource_baseline,
):
    dashboard_baseline = (
        (chart_duration_baseline | chart_energy_panel)
        & (chart_latency_baseline | chart_power_baseline)
        & (chart_resource_baseline)
    )

    chart_widget_baseline = mo.ui.altair_chart(dashboard_baseline)
    chart_widget_baseline
    return


@app.cell
def view_power_duration(chart_power_duration):
    chart_power_duration
    return


@app.cell
def md_scatter():
    mo.md(r"""
    ## Which target is actually the most efficient?

    With work held constant, "efficient" has two defensible readings, and they disagree:

    - **Least energy for the job:** `oci-node`, despite taking the longest.
    - **Best energy for a given speed:** `oci-axum`, several times faster than `oci-node`
      for a modest energy premium.

    Those two, plus `oci-spring` (fastest overall), are the only targets on the Pareto
    frontier of throughput vs. energy cost. Everything else is dominated: `oci-native` is
    beaten outright by `oci-axum`, which is both faster *and* substantially cheaper, and
    both Wasm targets are beaten on both axes at once.

    The per-run scatter shows the ranking is not noise: run-to-run spread in duration is a
    few seconds at most for every target, and the energy clusters do not overlap between
    targets.
    """)
    return


@app.cell
def view_scatters(
    chart_boxplots_baseline,
    chart_efficiency_scatter,
    chart_energy_latency,
    chart_run_scatter,
):
    mo.vstack([
        mo.hstack([chart_efficiency_scatter, chart_energy_latency], justify="start"),
        mo.hstack([chart_run_scatter, chart_boxplots_baseline], justify="start"),
    ])
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
    return


@app.cell
def md_idle():
    mo.md(r"""
    ## Idle scenario: what a pod costs doing nothing

    Ten minutes with no traffic, measuring only the runtime's resting draw. **The first
    two minutes of each capture are discarded**: the scenario begins about 15 s after the
    deployment is applied, so the head of every capture is the pod finishing start-up
    rather than idling. Over the full capture five of six targets accumulate almost all of
    their energy in the first fifth and then flatline, which inflates the figure by one to
    two orders of magnitude and scrambles the ordering. The cutoff itself does not drive
    the result - 10 %, 20 %, 33 % and 50 % agree to within a few per cent on every target.

    `df_baseline_idle` holds the resting draw measured over the remaining eight minutes.
    The ordering is what the runtimes' architectures predict: a compiled Rust binary with
    no managed runtime is quietest, the two Wasm components next, then the Node.js event
    loop, then GraalVM, and the JVM with its GC threads is dearest. All six are negligible
    against the loaded benchmark.

    Note what this does **not** show. The resting ranking does not invert the loaded one:
    `oci-axum` is both the cheapest at rest and on the loaded Pareto frontier. Idle draw is
    therefore not a lever that rescues WebAssembly at low duty cycle - see `breakeven.py`,
    where the crossings fall far below any realistic request rate and `oci-axum` is never
    overtaken at all.
    """)
    return


@app.cell
def view_idle_power(df_idle_power, make_idle_power_chart):
    chart_idle_power = make_idle_power_chart(df_idle_power)
    chart_idle_power
    return (chart_idle_power,)


@app.cell
def _(chart_latency_baseline):
    chart_latency_baseline
    return


@app.cell
def export_figures(
    chart_boxplots_baseline,
    chart_duration_baseline,
    chart_efficiency_scatter,
    chart_energy_baseline,
    chart_energy_latency,
    chart_energy_panel,
    chart_energy_share,
    chart_idle_power,
    chart_latency_baseline,
    chart_power_baseline,
    chart_power_duration,
    chart_resource_baseline,
    chart_run_scatter,
    chart_ts_panel,
    df_baseline_phases,
    df_baseline_runs,
    df_energy_phases,
    df_idle_power,
    df_metrics_baseline,
    ts_charts_progress,
):
    # Write every thesis figure/table to disk (figures/*.svg|png, tables/*.csv)
    export_manifest = export_all(
        charts={
            "baseline_duration": chart_duration_baseline,
            "baseline_latency": chart_latency_baseline,
            "baseline_energy_domains": chart_energy_baseline,
            "baseline_energy_share": chart_energy_share,
            "baseline_energy_domain_panel": chart_energy_panel,
            "baseline_power": chart_power_baseline,
            "baseline_resources": chart_resource_baseline,
            "baseline_idle_power": chart_idle_power,
            "baseline_efficiency_scatter": chart_efficiency_scatter,
            "baseline_power_duration_scatter": chart_power_duration,
            "baseline_energy_latency_scatter": chart_energy_latency,
            "baseline_run_scatter": chart_run_scatter,
            "baseline_boxplots": chart_boxplots_baseline,
            "baseline_timeseries_panel": chart_ts_panel,
            "baseline_rps": ts_charts_progress["rps"],
            "baseline_p95": ts_charts_progress["p95"],
            "baseline_cpu_usage": ts_charts_progress["cpu_usage"],
            "baseline_memory": ts_charts_progress["memory"],
        },
        tables={
            "baseline_summary": df_metrics_baseline,
            "baseline_runs": df_baseline_runs,
            "baseline_phases": df_baseline_phases,
            "baseline_energy_phases": df_energy_phases,
            "baseline_idle_power": df_idle_power,
        },
    )
    export_manifest
    return


if __name__ == "__main__":
    app.run()
