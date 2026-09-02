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
        load_metric_data,
        load_scenario_metrics,
        save_chart,
        t_critical,
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
    # Cold start: time and energy from zero replicas

    KEDA scales the application to **zero replicas**; once the scale-down has settled,
    k6 fires a **single request** (1 VU, 1 iteration) at `/players/1`. The cold start
    is that request to the **first HTTP 200**, so it includes image/pod scheduling,
    container start, runtime boot, connection setup and the query itself.
    **30 repetitions per target.**

    ### What is measured, and how

    * **Time** from k6:
      `histogram_quantile(0.95, sum(k6_http_req_duration_seconds_bucket) by (vmrange))`.
      With one request in flight the quantile is that request's duration; the
      **first** sample of the series is the cold start, and later samples only decay
      as the histogram ages.
    * **Energy at the node**, not the pod - Kepler's per-pod counters do not exist
      before the pod does, so a pod-scoped measurement cannot see the part that
      matters. `kepler_node_cpu_joules_total` is a cumulative counter per RAPL zone,
      so window energy is the counter delta across it, with no numerical integration
      of a power series.
    * **Idle baseline** from a separate `idle-scaled` capture with **zero application
      pods deployed**, giving the node's resting draw. Cold-start energy is the excess
      over it:

    $$E_{\text{coldstart}} \approx \big(J_{\text{node}}(t_1) - J_{\text{node}}(t_0)\big) - P_{\text{idle}} \cdot \Delta t$$

    ### What this number contains

    The node is shared: two PostgreSQL instances, three pgbouncer pods, KEDA, the HPA
    and the kube scheduler all run on it and all do work during a cold start. The
    excess is therefore the **whole-system cost of bringing this service up**, not
    the container's own consumption - an upper bound on the runtime's share, and the
    figure a capacity planner actually pays.

    Because it is a small difference between two large numbers, the notebook
    quantifies its **noise floor** by applying the identical estimator to windows of
    the idle capture, where the true excess is zero by construction.
    """)
    return


@app.cell
def load_coldstart():
    select_columns = ["status"]
    df_metrics = load_scenario_metrics("coldstart", select_columns=select_columns)
    df_metrics
    return (df_metrics,)


@app.cell
def load_idle():
    # Idle reference: node with ZERO application pods deployed
    df_idle_metrics = load_scenario_metrics("idle-scaled", select_columns=["all"])
    df_idle_metrics
    return (df_idle_metrics,)


@app.cell
def processing_helpers():
    # Cold-start analysis - constants & Polars processing helpers
    # -------------------------------------------------------------------------
    # 1. Constants
    # -------------------------------------------------------------------------
    COLDSTART_REPETITIONS = 30

    # A "cold start" faster than this means the pod was still warm: KEDA had not
    # finished scaling to zero, or the request hit a surviving replica. Such runs are
    # flagged and excluded from the statistics rather than silently averaged in.
    MIN_VALID_COLDSTART_S = 0.5

    # Sliding-window step used to build the idle noise distribution.
    NOISE_WINDOW_STEP_S = 0.5

    RAPL_ZONES = ["package", "dram"]


    # -------------------------------------------------------------------------
    # 2. Cold-start duration (from k6)
    # -------------------------------------------------------------------------
    def coldstart_durations(
        df_metrics: dict, min_valid_s: float = MIN_VALID_COLDSTART_S
    ) -> pl.DataFrame:
        """
        One row per (target, iteration): the cold-start duration in seconds.

        With a single request in flight, histogram_quantile over the k6 duration
        buckets returns that request's latency; the FIRST sample of the run is the
        cold start. Later samples only decay as the histogram ages, so `first` -
        not mean or max - is the correct reduction.
        """
        return (
            df_metrics["p95"]
            .sort(["target", "iteration", "normalized_time"])
            .group_by(["target", "iteration"])
            .agg([
                pl.col("value").first().alias("duration_s"),
                pl.col("value").last().alias("duration_last_s"),
                pl.len().alias("n_samples"),
            ])
            .with_columns([
                pl.lit(0.0).alias("t_start"),
                pl.col("duration_s").alias("t_end"),
                (pl.col("duration_s") >= min_valid_s).alias("is_cold"),
            ])
            .sort([target_rank(), pl.col("iteration")])
        )


    # -------------------------------------------------------------------------
    # 3. Counter integration over arbitrary windows
    # -------------------------------------------------------------------------
    def window_energy(
        df_counter: pl.DataFrame,
        windows: pl.DataFrame,
        on: list[str] | None = None,
        group_extra: list[str] | None = None,
    ) -> pl.DataFrame:
        """
        Energy accumulated by a cumulative joules counter inside [t_start, t_end].

        Each scrape interval contributes its increment scaled by how much of it falls
        inside the window, which is exactly linear interpolation of the cumulative
        counter at both boundaries. This matters here: Kepler updates roughly once a
        second while the scrape runs at 100 ms, so a 2 s cold-start window covers only
        a couple of counter steps and naive filtering would quantize the result.

        `on` are the columns shared by counter and windows (empty/None = cross join,
        used for the sliding idle windows).
        """
        on = on or []
        group_extra = group_extra or []

        keys = ["target", "iteration", "zone"]
        intervals = (
            df_counter.sort(keys + ["normalized_time"])
            .with_columns([
                pl.col("normalized_time").shift(1).over(keys).alias("t_prev"),
                (pl.col("value") - pl.col("value").shift(1)).over(keys).clip(lower_bound=0.0).alias("dj"),
            ])
            .drop_nulls("t_prev")
        )

        joined = (
            intervals.join(windows, on=on, how="inner")
            if on
            else intervals.join(windows, how="cross")
        )

        return (
            joined.with_columns([
                (pl.col("normalized_time") - pl.col("t_prev")).alias("dt"),
                (
                    pl.min_horizontal(pl.col("normalized_time"), pl.col("t_end"))
                    - pl.max_horizontal(pl.col("t_prev"), pl.col("t_start"))
                ).clip(lower_bound=0.0).alias("overlap"),
            ])
            .with_columns(
                pl.when(pl.col("dt") > 0)
                .then(pl.col("dj") * pl.col("overlap") / pl.col("dt"))
                .otherwise(0.0)
                .alias("joules")
            )
            .group_by(["target", "iteration", "zone", *group_extra])
            .agg([
                pl.col("joules").sum().alias("energy_j"),
                pl.col("t_end").first().alias("t_end"),
                pl.col("t_start").first().alias("t_start"),
                (pl.col("normalized_time").max() >= pl.col("t_end").first()).alias("window_covered"),
            ])
            .with_columns((pl.col("t_end") - pl.col("t_start")).alias("window_s"))
        )


    # -------------------------------------------------------------------------
    # 4. Idle baseline and its noise floor
    # -------------------------------------------------------------------------
    def idle_baseline_power(df_idle: dict) -> pl.DataFrame:
        """
        Node power with zero application pods deployed, per RAPL zone (W).

        Derived from the joules counter over the whole idle capture rather than from
        the watts gauge, so it is the same estimator used on the cold-start windows.
        """
        per_capture = (
            df_idle["node_joules"]
            .sort(["target", "iteration", "zone", "normalized_time"])
            .group_by(["target", "iteration", "zone"])
            .agg([
                (pl.col("value").max() - pl.col("value").min()).alias("joules"),
                pl.col("normalized_time").max().alias("window_s"),
            ])
            .with_columns((pl.col("joules") / pl.col("window_s")).alias("power_w"))
        )
        return (
            per_capture.group_by("zone")
            .agg([
                pl.col("power_w").mean().alias("idle_power_w"),
                pl.col("power_w").std().alias("idle_power_w_std"),
                pl.col("window_s").sum().alias("capture_s"),
                pl.len().alias("n_captures"),
            ])
            .sort("zone")
        )


    def idle_noise_distribution(
        df_idle: dict,
        window_s: float,
        baseline: pl.DataFrame,
        step_s: float = NOISE_WINDOW_STEP_S,
    ) -> pl.DataFrame:
        """
        Null distribution of "excess energy" for a window of the given length.

        The identical estimator is slid across the idle capture, where no pod starts
        and the true excess is zero. The spread of the result is the measurement
        noise floor: any cold-start excess smaller than it cannot be distinguished
        from the node's own fluctuation.
        """
        counter = df_idle["node_joules"]
        span = counter["normalized_time"].max()
        starts = pl.DataFrame({
            "t_start": [
                s * step_s for s in range(int((span - window_s) / step_s)) if s * step_s + window_s <= span
            ]
        }).with_columns([
            (pl.col("t_start") + window_s).alias("t_end"),
            pl.int_range(pl.len()).alias("window_id"),
        ])

        per_zone = window_energy(counter, starts, on=[], group_extra=["window_id"]).join(
            baseline.select(["zone", "idle_power_w"]), on="zone", how="left"
        ).with_columns(
            (pl.col("energy_j") - pl.col("idle_power_w") * pl.col("window_s")).alias("excess_j")
        )

        return (
            per_zone.group_by(["target", "iteration", "window_id"])
            .agg([
                pl.col("excess_j").sum().alias("excess_j"),
                pl.col("energy_j").sum().alias("energy_j"),
                pl.col("window_s").first().alias("window_s"),
            ])
            .sort("window_id")
        )


    def noise_thresholds(df_noise: pl.DataFrame, n_runs: int = COLDSTART_REPETITIONS) -> dict:
        """
        Detection limits derived from the null distribution:
          single_run_*     - 95 % interval for one measurement, read off the
                             empirical quantiles rather than assumed normal
          mean_resolution  - the smallest mean excess resolvable by averaging
                             n_runs, at the same 95 % confidence
        """
        excess = df_noise["excess_j"]
        sd = excess.std()
        return {
            "noise_mean_j": excess.mean(),
            "noise_sd_j": sd,
            "single_run_lo_j": excess.quantile(0.025),
            "single_run_hi_j": excess.quantile(0.975),
            "mean_resolution_j": t_critical(n_runs) * sd / (n_runs ** 0.5),
            "n_windows": len(excess),
        }


    # -------------------------------------------------------------------------
    # 5. Cold-start energy
    # -------------------------------------------------------------------------
    def coldstart_energy(
        df_metrics: dict, df_durations: pl.DataFrame, df_baseline: pl.DataFrame
    ) -> pl.DataFrame:
        """
        Per (target, iteration, zone) energy over the cold-start window, split into
        what an idle node would have drawn anyway and the excess attributable to
        bringing the service up. A `total` zone row is appended per run.
        """
        per_zone = (
            window_energy(
                df_metrics["node_joules"],
                df_durations.select(["target", "iteration", "t_start", "t_end", "is_cold"]),
                on=["target", "iteration"],
                group_extra=["is_cold"],
            )
            .join(df_baseline.select(["zone", "idle_power_w"]), on="zone", how="left")
            .with_columns([
                (pl.col("idle_power_w") * pl.col("window_s")).alias("idle_energy_j"),
                (pl.col("energy_j") - pl.col("idle_power_w") * pl.col("window_s")).alias("excess_j"),
            ])
        )

        totals = (
            per_zone.group_by(["target", "iteration", "is_cold", "window_s", "window_covered"])
            .agg([
                pl.col("energy_j").sum(),
                pl.col("idle_energy_j").sum(),
                pl.col("excess_j").sum(),
                pl.lit("total").alias("zone"),
                pl.col("idle_power_w").sum(),
            ])
        )

        columns = ["target", "iteration", "zone", "is_cold", "window_covered", "window_s",
                   "energy_j", "idle_energy_j", "excess_j", "idle_power_w"]

        return (
            pl.concat([per_zone.select(columns), totals.select(columns)], how="vertical")
            .with_columns([
                (pl.col("energy_j") / pl.col("window_s")).alias("mean_power_w"),
                (pl.col("excess_j") / pl.col("window_s")).alias("excess_power_w"),
            ])
            .sort([target_rank(), pl.col("iteration")])
        )


    def summarize_coldstart(
        df_durations: pl.DataFrame,
        df_energy: pl.DataFrame,
        zone: str = "total",
        only_cold: bool = True,
    ) -> pl.DataFrame:
        """
        One row per target: cold-start time and energy with the standard error of the
        mean, which is the quantity that can actually be compared given the noise
        floor of a single measurement.
        """
        durations = df_durations.filter(pl.col("is_cold")) if only_cold else df_durations
        energy = df_energy.filter(pl.col("zone") == zone)
        if only_cold:
            energy = energy.filter(pl.col("is_cold"))

        dur_stats = durations.group_by("target").agg([
            pl.col("duration_s").mean().alias("duration_mean_s"),
            pl.col("duration_s").std().alias("duration_std_s"),
            pl.col("duration_s").median().alias("duration_median_s"),
            pl.col("duration_s").min().alias("duration_min_s"),
            pl.col("duration_s").max().alias("duration_max_s"),
            pl.col("duration_s").quantile(0.95).alias("duration_p95_s"),
            pl.len().alias("n_cold_runs"),
        ])

        energy_stats = energy.group_by("target").agg([
            pl.col("energy_j").mean().alias("node_energy_mean_j"),
            pl.col("idle_energy_j").mean().alias("idle_energy_mean_j"),
            pl.col("excess_j").mean().alias("excess_mean_j"),
            pl.col("excess_j").std().alias("excess_std_j"),
            pl.col("excess_j").median().alias("excess_median_j"),
            pl.col("mean_power_w").mean().alias("node_power_mean_w"),
            pl.col("excess_power_w").mean().alias("excess_power_mean_w"),
            pl.len().alias("n_energy_runs"),
        ])

        return (
            dur_stats.join(energy_stats, on="target", how="left")
            .with_columns([
                (pl.col("excess_std_j") / pl.col("n_energy_runs").sqrt()).alias("excess_sem_j"),
                (pl.col("duration_std_s") / pl.col("n_cold_runs").sqrt()).alias("duration_sem_s"),
                # The critical value is taken per target rather than once, because
                # excluded repetitions leave the targets with different counts.
                pl.col("n_energy_runs").map_elements(
                    t_critical, return_dtype=pl.Float64
                ).alias("excess_t_crit"),
                pl.col("n_cold_runs").map_elements(
                    t_critical, return_dtype=pl.Float64
                ).alias("duration_t_crit"),
            ])
            .with_columns([
                (pl.col("excess_mean_j") - pl.col("excess_t_crit") * pl.col("excess_sem_j")).alias("excess_ci_lo_j"),
                (pl.col("excess_mean_j") + pl.col("excess_t_crit") * pl.col("excess_sem_j")).alias("excess_ci_hi_j"),
                (pl.col("duration_mean_s") - pl.col("duration_t_crit") * pl.col("duration_sem_s")).alias("duration_ci_lo_s"),
                (pl.col("duration_mean_s") + pl.col("duration_t_crit") * pl.col("duration_sem_s")).alias("duration_ci_hi_s"),
            ])
            .sort([target_rank()])
        )


    def energy_by_zone(df_energy: pl.DataFrame, only_cold: bool = True) -> pl.DataFrame:
        """Mean measured / idle / excess energy per target and RAPL zone."""
        frame = df_energy.filter(pl.col("is_cold")) if only_cold else df_energy
        return (
            frame.filter(pl.col("zone") != "total")
            .group_by(["target", "zone"])
            .agg([
                pl.col("energy_j").mean().alias("energy_mean_j"),
                pl.col("idle_energy_j").mean().alias("idle_energy_mean_j"),
                pl.col("excess_j").mean().alias("excess_mean_j"),
                pl.col("excess_j").std().alias("excess_std_j"),
            ])
            .sort([target_rank(), pl.col("zone")])
        )


    def node_power_timeline(
        df_metrics: dict, bin_s: float = 0.5, horizon_s: float = 12.0
    ) -> pl.DataFrame:
        """
        Instantaneous node power around the cold start, derived from the joules
        counter (J per bin / bin length) and averaged across the 30 runs.
        Time 0 is the moment the request was issued.
        """
        increments = counter_increments(
            df_metrics["node_joules"], ["target", "iteration", "zone"], alias="dj"
        ).with_columns([
            ((pl.col("normalized_time") / bin_s).floor() * bin_s).alias("t_bin"),
            (pl.col("normalized_time") - pl.col("normalized_time").shift(1))
            .over(["target", "iteration", "zone"])
            .alias("dt"),
        ])

        per_bin = (
            increments.filter(pl.col("t_bin") <= horizon_s)
            .group_by(["target", "iteration", "zone", "t_bin"])
            .agg([pl.col("dj").sum().alias("joules"), pl.col("dt").sum().alias("elapsed_s")])
            .filter(pl.col("elapsed_s") > 0)
            .with_columns((pl.col("joules") / pl.col("elapsed_s")).alias("power_w"))
        )

        totals = (
            per_bin.group_by(["target", "iteration", "t_bin"])
            .agg(pl.col("power_w").sum().alias("power_w"))
            .with_columns(pl.lit("total").alias("zone"))
        )

        combined = pl.concat(
            [per_bin.select(["target", "iteration", "zone", "t_bin", "power_w"]), totals.select(["target", "iteration", "zone", "t_bin", "power_w"])],
            how="vertical",
        )

        return (
            combined.group_by(["target", "zone", "t_bin"])
            .agg([
                pl.col("power_w").mean().alias("power_mean_w"),
                pl.col("power_w").std().fill_null(0.0).alias("power_std_w"),
                pl.len().alias("n_runs"),
            ])
            .with_columns([
                (pl.col("power_mean_w") - pl.col("power_std_w")).alias("band_lower"),
                (pl.col("power_mean_w") + pl.col("power_std_w")).alias("band_upper"),
            ])
            .sort([target_rank(), pl.col("zone"), pl.col("t_bin")])
        )


    def node_energy_timeline(
        df_metrics: dict,
        df_baseline: pl.DataFrame,
        bin_s: float = 0.5,
        horizon_s: float = 12.0,
    ) -> pl.DataFrame:
        """
        Cumulative energy drawn since the request was issued, minus what an idle node
        would have drawn over the same interval - i.e. how the cold-start cost
        accumulates in time, averaged across runs.

        Preferred over an instantaneous power curve: Kepler updates the node counter
        only about once a second, so per-bin power is dominated by which bin happens
        to contain a counter step, while the cumulative series is monotone and
        unaffected by that quantization.
        """
        idle_total = df_baseline["idle_power_w"].sum()

        cumulative = (
            counter_increments(df_metrics["node_joules"], ["target", "iteration", "zone"], alias="dj")
            .filter(pl.col("normalized_time") <= horizon_s)
            .group_by(["target", "iteration", "normalized_time"])
            .agg(pl.col("dj").sum().alias("dj"))
            .sort(["target", "iteration", "normalized_time"])
            .with_columns(pl.col("dj").cum_sum().over(["target", "iteration"]).alias("energy_j"))
            .with_columns([
                ((pl.col("normalized_time") / bin_s).round() * bin_s).alias("t_bin"),
            ])
            .with_columns(
                (pl.col("energy_j") - idle_total * pl.col("normalized_time")).alias("excess_j")
            )
        )

        # one value per run and bin (last sample in the bin), then average across runs
        per_run_bin = (
            cumulative.group_by(["target", "iteration", "t_bin"])
            .agg([pl.col("excess_j").last(), pl.col("energy_j").last()])
        )

        return (
            per_run_bin.group_by(["target", "t_bin"])
            .agg([
                pl.col("excess_j").mean().alias("excess_mean_j"),
                pl.col("excess_j").std().fill_null(0.0).alias("excess_std_j"),
                pl.col("energy_j").mean().alias("energy_mean_j"),
                pl.len().alias("n_runs"),
            ])
            .with_columns([
                (pl.col("excess_mean_j") - pl.col("excess_std_j")).alias("band_lower"),
                (pl.col("excess_mean_j") + pl.col("excess_std_j")).alias("band_upper"),
            ])
            .sort([target_rank(), pl.col("t_bin")])
        )


    def duration_ecdf(df_durations: pl.DataFrame, only_cold: bool = True) -> pl.DataFrame:
        """Empirical CDF of cold-start times per target, for the distribution plot."""
        frame = df_durations.filter(pl.col("is_cold")) if only_cold else df_durations
        return (
            frame.sort(["target", "duration_s"])
            .with_columns([
                (pl.int_range(pl.len()).over("target") + 1).alias("rank"),
                pl.len().over("target").alias("n"),
            ])
            .with_columns((pl.col("rank") / pl.col("n")).alias("cdf"))
            .select(["target", "iteration", "duration_s", "cdf"])
        )


    return (
        coldstart_durations,
        coldstart_energy,
        duration_ecdf,
        energy_by_zone,
        idle_baseline_power,
        idle_noise_distribution,
        node_energy_timeline,
        node_power_timeline,
        noise_thresholds,
        summarize_coldstart,
    )


@app.cell
def chart_helpers():
    # Altair visualization helpers - all target encodings use color_scale()
    # -------------------------------------------------------------------------
    def make_duration_chart(df_summary: pl.DataFrame) -> alt.Chart:
        """Mean cold-start time per target with a 95 % confidence interval."""
        order = df_summary.sort("duration_mean_s")["target"].to_list()
        base = alt.Chart(df_summary).encode(y=alt.Y("target:N", title="Target", sort=order))

        bars = base.mark_bar(opacity=0.9).encode(
            x=alt.X("duration_mean_s:Q", title="Cold-start time (s)"),
            color=target_color(legend=False),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("duration_mean_s:Q", title="Mean (s)", format=".2f"),
                alt.Tooltip("duration_median_s:Q", title="Median (s)", format=".2f"),
                alt.Tooltip("duration_std_s:Q", title="SD (s)", format=".2f"),
                alt.Tooltip("n_cold_runs:Q", title="Runs"),
            ],
        )
        ci = base.mark_errorbar(color="#222", ticks=True).encode(
            x=alt.X("duration_ci_lo_s:Q", title="Cold-start time (s)"), x2="duration_ci_hi_s:Q"
        )
        return (bars + ci).properties(
            width=380,
            height=210,
            title={
                "text": "Cold-start time: request to first HTTP 200",
                "subtitle": "From zero replicas. Mean of 30 runs with 95 % CI. Lower is better",
            },
        )


    def make_duration_distribution(df_durations: pl.DataFrame, only_cold: bool = True) -> alt.Chart:
        """Every repetition as a point over the boxplot: shows spread and outliers."""
        frame = df_durations.filter(pl.col("is_cold")) if only_cold else df_durations
        base = alt.Chart(frame).encode(
            y=alt.Y("target:N", title=None, sort=TARGET_ORDER),
            x=alt.X("duration_s:Q", title="Cold-start time (s)", scale=alt.Scale(zero=False)),
        )
        box = base.mark_boxplot(size=14, opacity=0.55).encode(color=target_color(legend=False))
        points = base.mark_point(size=22, opacity=0.55, filled=True, xOffset=0).encode(
            color=target_color(),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("iteration:Q", title="Run"),
                alt.Tooltip("duration_s:Q", title="Seconds", format=".3f"),
            ],
        )
        return (box + points).properties(
            width=420,
            height=230,
            title={"text": "Cold-start time distribution", "subtitle": "One point per repetition (30 per target)"},
        )


    def make_duration_ecdf(df_ecdf: pl.DataFrame) -> alt.Chart:
        """Empirical CDF: the share of cold starts served within a given time."""
        return (
            alt.Chart(df_ecdf)
            .mark_line(interpolate="step-after", strokeWidth=1.8)
            .encode(
                x=alt.X("duration_s:Q", title="Cold-start time (s)", scale=alt.Scale(zero=False)),
                y=alt.Y("cdf:Q", title="Share of runs completed", axis=alt.Axis(format="%")),
                color=target_color(),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("duration_s:Q", title="Seconds", format=".2f"),
                    alt.Tooltip("cdf:Q", title="Cumulative share", format=".0%"),
                ],
            )
            .properties(
                width=420,
                height=230,
                title={"text": "Cold-start time ECDF", "subtitle": "Leftmost curve is fastest; steepness is consistency"},
            )
        )


    def make_energy_breakdown_chart(df_summary: pl.DataFrame) -> alt.Chart:
        """
        What the node actually drew during the cold-start window, split into the
        energy an idle node would have drawn anyway and the excess caused by
        starting the service.
        """
        df_plot = (
            df_summary.select([
                pl.col("target"),
                pl.col("idle_energy_mean_j").alias("idle baseline"),
                pl.col("excess_mean_j").alias("cold-start excess"),
            ])
            .unpivot(index="target", variable_name="component", value_name="joules")
        )
        order = df_summary.sort("node_energy_mean_j")["target"].to_list()

        return (
            alt.Chart(df_plot)
            .mark_bar()
            .encode(
                y=alt.Y("target:N", title="Target", sort=order),
                x=alt.X("joules:Q", title="Energy over the cold-start window (J)", stack="zero"),
                color=alt.Color(
                    "component:N",
                    title=None,
                    scale=alt.Scale(domain=["idle baseline", "cold-start excess"], range=["#B9C2C8", "#D34516"]),
                ),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("component:N", title="Component"),
                    alt.Tooltip("joules:Q", title="Joules", format=",.1f"),
                ],
            )
            .properties(
                width=400,
                height=210,
                title={
                    "text": "Node energy during the cold start",
                    "subtitle": "Grey is what an idle node draws anyway; colored is the cost of starting up",
                },
            )
        )


    def make_excess_energy_chart(df_summary: pl.DataFrame, noise: dict | None = None) -> alt.Chart:
        """
        Cold-start energy proper, with 95 % CI of the mean and the noise floor of a
        single measurement drawn behind it.
        """
        order = df_summary.sort("excess_mean_j")["target"].to_list()
        base = alt.Chart(df_summary).encode(y=alt.Y("target:N", title="Target", sort=order))

        layers = []
        if noise:
            band = pl.DataFrame({"lo": [noise["single_run_lo_j"]], "hi": [noise["single_run_hi_j"]]})
            layers.append(
                alt.Chart(band)
                .mark_rect(opacity=0.12, color="#666")
                .encode(x=alt.X("lo:Q", title="Cold-start energy (J above idle)"), x2="hi:Q")
            )
            threshold = pl.DataFrame({"v": [noise["mean_resolution_j"]]})
            layers.append(
                alt.Chart(threshold)
                .mark_rule(strokeDash=[5, 4], color="#444")
                .encode(x=alt.X("v:Q", title="Cold-start energy (J above idle)"))
            )

        layers.append(
            base.mark_bar(opacity=0.9).encode(
                x=alt.X("excess_mean_j:Q", title="Cold-start energy (J above idle)"),
                color=target_color(legend=False),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("excess_mean_j:Q", title="Mean excess (J)", format=".1f"),
                    alt.Tooltip("excess_ci_lo_j:Q", title="CI low", format=".1f"),
                    alt.Tooltip("excess_ci_hi_j:Q", title="CI high", format=".1f"),
                    alt.Tooltip("excess_power_mean_w:Q", title="Excess power (W)", format=".1f"),
                ],
            )
        )
        layers.append(
            base.mark_errorbar(color="#222", ticks=True).encode(
                x=alt.X("excess_ci_lo_j:Q", title="Cold-start energy (J above idle)"), x2="excess_ci_hi_j:Q"
            )
        )

        return alt.layer(*layers).properties(
            width=400,
            height=210,
            title={
                "text": "Energy cost of one cold start",
                # Two lines: as a single string the subtitle set the chart width and
                # left the plot itself stranded in the left half of the image.
                "subtitle": [
                    "Bars = mean of 30 runs with 95 % CI. Grey band = 95 % noise range",
                    "of a SINGLE measurement, dashed line = resolution limit of the 30-run mean",
                ],
            },
        )


    def make_zone_chart(df_zone: pl.DataFrame) -> alt.Chart:
        """Excess energy split into the package and DRAM RAPL domains."""
        return (
            alt.Chart(df_zone)
            .mark_bar()
            .encode(
                y=alt.Y("target:N", title="Target", sort=TARGET_ORDER),
                x=alt.X("excess_mean_j:Q", title="Excess energy (J)", stack="zero"),
                color=alt.Color(
                    "zone:N",
                    title="RAPL Zone",
                    scale=alt.Scale(domain=["package", "dram"], range=["#00758F", "#FC7C00"]),
                ),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("zone:N", title="Zone"),
                    alt.Tooltip("excess_mean_j:Q", title="Excess (J)", format=".1f"),
                ],
            )
            .properties(width=380, height=210, title="Cold-start energy by RAPL domain")
        )


    def make_energy_timeline_chart(
        df_timeline: pl.DataFrame,
        df_summary: pl.DataFrame,
        horizon_s: float = 12.0,
        y_log: bool = False,
    ) -> alt.Chart:
        """
        How the cold-start cost accumulates: cumulative node energy above idle from
        the moment the request is issued. The tick marks each target's mean
        cold-start time - the point where its first HTTP 200 arrived.
        """
        df_plot = df_timeline.filter(pl.col("t_bin") <= horizon_s)

        base = alt.Chart(df_plot)
        x = alt.X("t_bin:Q", title="Time since request (s)", scale=alt.Scale(nice=False))
        y = alt.Y(
            "excess_mean_j:Q",
            title="Cumulative energy above idle (J)",
            scale=alt.Scale(type="log", nice=False) if y_log else alt.Scale(zero=False),
        )

        # The band is the first layer to encode colour, so it is the one that has to
        # carry the legend; see target_color().
        band = base.mark_area(opacity=0.15).encode(
            x=x, y=alt.Y("band_lower:Q", title="Cumulative energy above idle (J)"), y2="band_upper:Q",
            color=target_color(symbol="stroke"),
        )
        line = base.mark_line(strokeWidth=1.8).encode(
            x=x, y=y, color=target_color(legend=False),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("t_bin:Q", title="t (s)", format=".1f"),
                alt.Tooltip("excess_mean_j:Q", title="Cumulative J above idle", format=".1f"),
                alt.Tooltip("n_runs:Q", title="Runs"),
            ],
        )
        ready = (
            alt.Chart(df_summary)
            .mark_point(size=90, shape="triangle-up", filled=True, yOffset=0)
            .encode(
                x=alt.X("duration_mean_s:Q", title="Time since request (s)"),
                y=alt.Y("excess_mean_j:Q", title="Cumulative energy above idle (J)"),
                color=target_color(legend=False),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("duration_mean_s:Q", title="First HTTP 200 at (s)", format=".2f"),
                    alt.Tooltip("excess_mean_j:Q", title="Cold-start energy (J)", format=".1f"),
                ],
            )
        )
        zero = (
            alt.Chart(pl.DataFrame({"v": [0.0]}))
            .mark_rule(strokeDash=[6, 4], color="#444")
            .encode(y=alt.Y("v:Q", title="Cumulative energy above idle (J)"))
        )

        return (band + line + zero + ready).properties(
            width=620,
            height=260,
            title={
                "text": "How cold-start energy accumulates",
                "subtitle": "Cumulative node energy above the idle baseline, mean of 30 runs +/-1 SD. Triangle = mean time of first HTTP 200",
            },
        )


    def make_power_timeline_chart(
        df_timeline: pl.DataFrame,
        df_baseline: pl.DataFrame,
        zone: str = "total",
        horizon_s: float = 12.0,
    ) -> alt.Chart:
        """
        Node power from the moment the request is issued: the cold-start spike over
        the idle baseline, averaged across runs with a +/-1 SD band.
        """
        df_plot = df_timeline.filter((pl.col("zone") == zone) & (pl.col("t_bin") <= horizon_s))
        idle_total = df_baseline["idle_power_w"].sum() if zone == "total" else (
            df_baseline.filter(pl.col("zone") == zone)["idle_power_w"].sum()
        )

        base = alt.Chart(df_plot)
        x = alt.X("t_bin:Q", title="Time since request (s)", scale=alt.Scale(nice=False))

        # The band is the first layer to encode colour, so it is the one that has to
        # carry the legend; see target_color().
        # The band is mean +/- 1 SD of a noisy difference, so its lower edge runs
        # below zero on the quiet targets. Node power cannot be negative, so the
        # axis is floored at 0 and the band is clipped there rather than the plot
        # carrying a physically meaningless negative region.
        power_scale = alt.Scale(zero=False, domainMin=0)
        band = base.mark_area(opacity=0.15).encode(
            x=x,
            y=alt.Y("band_lower:Q", title="Node power (W)", scale=power_scale),
            y2="band_upper:Q",
            color=target_color(symbol="stroke"),
        )
        line = base.mark_line(strokeWidth=1.7).encode(
            x=x,
            y=alt.Y("power_mean_w:Q", title="Node power (W)", scale=power_scale),
            color=target_color(legend=False),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("t_bin:Q", title="t (s)", format=".1f"),
                alt.Tooltip("power_mean_w:Q", title="Watts", format=".1f"),
            ],
        )
        baseline_rule = (
            alt.Chart(pl.DataFrame({"v": [idle_total]}))
            .mark_rule(strokeDash=[6, 4], color="#444")
            .encode(y=alt.Y("v:Q", title="Node power (W)"))
        )

        return (band + line + baseline_rule).properties(
            # 626 keeps the exported SVG at the same 767 px width it had while the
            # y-axis still carried a negative "-100" label.
            width=626,
            height=234,
            title={
                "text": "Node power during a cold start",
                "subtitle": "Mean of 30 runs +/-1 SD; dashed line = idle node with zero application pods",
            },
        )


    def make_time_energy_scatter(
        df_summary: pl.DataFrame, df_energy: pl.DataFrame | None = None
    ) -> alt.Chart:
        """
        Cold-start time against its energy cost. Bottom-left is best: fast to serve
        the first request and cheap to get there.
        """
        layers = []
        if df_energy is not None:
            runs = (
                df_energy.filter((pl.col("zone") == "total") & pl.col("is_cold"))
                .join(df_summary.select(["target"]), on="target", how="inner")
            )
            layers.append(
                alt.Chart(runs)
                .mark_point(size=35, opacity=0.35, filled=True)
                .encode(
                    x=alt.X("window_s:Q", title="Cold-start time (s)", scale=alt.Scale(zero=False, padding=25)),
                    y=alt.Y("excess_j:Q", title="Cold-start energy (J above idle)", scale=alt.Scale(zero=False, padding=25)),
                    color=target_color(legend=False),
                    tooltip=[
                        alt.Tooltip("target:N", title="Target"),
                        alt.Tooltip("iteration:Q", title="Run"),
                        alt.Tooltip("window_s:Q", title="Seconds", format=".2f"),
                        alt.Tooltip("excess_j:Q", title="Joules", format=".1f"),
                    ],
                )
            )

        means = alt.Chart(df_summary).encode(
            x=alt.X("duration_mean_s:Q", title="Cold-start time (s)", scale=alt.Scale(zero=False, padding=25)),
            y=alt.Y("excess_mean_j:Q", title="Cold-start energy (J above idle)", scale=alt.Scale(zero=False, padding=25)),
        )
        layers.append(means.mark_point(size=170, filled=True).encode(color=target_color()))

        # Five of the six targets fall inside a 1.6 s by 17 J cluster, so one
        # shared offset stacks their labels on top of each other. Each target is
        # placed on its own instead; the values are pixel offsets from the marker
        # and the anchor side, chosen so no two labels share a row.
        label_offsets = {
            "wasm-rust": ("right", -12, -14),
            "wasm-js": ("right", -12, 17),
            "oci-native": ("left", 12, -20),
            # Far enough right to clear the marker blob it sits inside.
            "oci-node": ("left", 28, 0),
            "oci-axum": ("left", 12, 20),
            # Anchored left of its marker: it is the right-most point, and on the
            # right of it the label ran into the legend.
            "oci-spring": ("right", -12, -8),
        }
        for label_target in df_summary["target"].to_list():
            align, dx, dy = label_offsets.get(label_target, ("left", 10, -8))
            layers.append(
                means.transform_filter(alt.datum.target == label_target)
                .mark_text(align=align, dx=dx, dy=dy, fontSize=11)
                .encode(text="target:N", color=target_color(legend=False))
            )
        layers.append(
            alt.Chart(df_summary)
            .mark_rule(opacity=0.5)
            .encode(
                x="duration_mean_s:Q",
                y=alt.Y("excess_ci_lo_j:Q", title="Cold-start energy (J above idle)"),
                y2="excess_ci_hi_j:Q",
                color=target_color(legend=False),
            )
        )

        return (
            alt.layer(*layers)
            .properties(
                # The legend costs the width the plot gives up here, so the
                # exported SVG keeps the box the thesis lays out around it.
                width=356,
                height=330,
                title={
                    "text": "Cold-start time vs. energy",
                    "subtitle": "Large points = 30-run mean with 95 % CI, small points = individual runs. Bottom-left is best",
                },
            )
            # The per-run points are drawn first and ask for no legend, which on a
            # shared color scale suppresses the one the mean points ask for.
            .resolve_scale(color="independent")
        )


    def make_noise_chart(df_noise: pl.DataFrame, df_summary: pl.DataFrame, noise: dict) -> alt.Chart:
        """
        Validation figure: the same estimator applied to idle-only windows, where the
        answer must be zero, next to the measured per-target means.
        """
        hist = (
            alt.Chart(df_noise)
            .mark_bar(opacity=0.65, color="#8A9BA8")
            .encode(
                x=alt.X("excess_j:Q", bin=alt.Bin(maxbins=60), title="Excess energy of an idle-only window (J)"),
                y=alt.Y("count():Q", title="Windows"),
                tooltip=[alt.Tooltip("count():Q", title="Windows")],
            )
        )
        zero = (
            alt.Chart(pl.DataFrame({"v": [0.0]}))
            .mark_rule(color="#111", strokeDash=[4, 3])
            .encode(x=alt.X("v:Q", title="Excess energy of an idle-only window (J)"))
        )
        observed = (
            alt.Chart(df_summary.filter(pl.col("excess_mean_j") <= noise["single_run_hi_j"] * 3))
            .mark_rule(strokeWidth=2)
            .encode(
                x=alt.X("excess_mean_j:Q", title="Excess energy of an idle-only window (J)"),
                color=target_color(),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("excess_mean_j:Q", title="Measured mean (J)", format=".1f"),
                ],
            )
        )
        return (hist + zero + observed).properties(
            width=520,
            height=230,
            title={
                "text": "Measurement noise floor",
                "subtitle": f"{noise['n_windows']} idle-only windows, SD {noise['noise_sd_j']:.1f} J. Vertical lines = measured per-target means",
            },
        )


    return (
        make_duration_chart,
        make_duration_distribution,
        make_duration_ecdf,
        make_energy_breakdown_chart,
        make_energy_timeline_chart,
        make_excess_energy_chart,
        make_noise_chart,
        make_power_timeline_chart,
        make_time_energy_scatter,
        make_zone_chart,
    )


@app.cell
def analysis(
    coldstart_durations,
    coldstart_energy,
    df_idle_metrics,
    df_metrics,
    duration_ecdf,
    energy_by_zone,
    idle_baseline_power,
    idle_noise_distribution,
    node_energy_timeline,
    node_power_timeline,
    noise_thresholds,
    summarize_coldstart,
):
    # Cold-start analysis
    df_idle_baseline = idle_baseline_power(df_idle_metrics)
    df_durations = coldstart_durations(df_metrics)
    df_coldstart_energy = coldstart_energy(df_metrics, df_durations, df_idle_baseline)
    df_coldstart_summary = summarize_coldstart(df_durations, df_coldstart_energy)
    df_coldstart_zones = energy_by_zone(df_coldstart_energy)
    df_power_timeline = node_power_timeline(df_metrics)
    df_energy_timeline = node_energy_timeline(df_metrics, df_idle_baseline)
    df_duration_ecdf = duration_ecdf(df_durations)

    # Noise floor: the same estimator on idle-only windows of the median cold-start length
    df_noise = idle_noise_distribution(
        df_idle_metrics, df_durations.filter(pl.col("is_cold"))["duration_s"].median(), df_idle_baseline
    )
    noise = noise_thresholds(df_noise)

    mo.vstack([
        mo.md("## Cold-start summary"),
        df_coldstart_summary.select([
            "target", "duration_mean_s", "duration_std_s", "duration_median_s", "duration_p95_s",
            "node_energy_mean_j", "idle_energy_mean_j", "excess_mean_j", "excess_ci_lo_j",
            "excess_ci_hi_j", "excess_power_mean_w", "n_cold_runs",
        ]),
        mo.md(f"Idle baseline: **{df_idle_baseline['idle_power_w'].sum():.2f} W** "
              f"(package {df_idle_baseline.filter(pl.col('zone') == 'package')['idle_power_w'][0]:.2f} W + "
              f"dram {df_idle_baseline.filter(pl.col('zone') == 'dram')['idle_power_w'][0]:.2f} W). "
              f"Noise floor: sd **{noise['noise_sd_j']:.1f} J** per single measurement, "
              f"resolution of the 30-run mean **{noise['mean_resolution_j']:.1f} J**."),
    ])
    return (
        df_coldstart_energy,
        df_coldstart_summary,
        df_coldstart_zones,
        df_duration_ecdf,
        df_durations,
        df_energy_timeline,
        df_idle_baseline,
        df_noise,
        df_power_timeline,
        noise,
    )


@app.cell
def charts(
    df_coldstart_energy,
    df_coldstart_summary,
    df_coldstart_zones,
    df_duration_ecdf,
    df_durations,
    df_energy_timeline,
    df_idle_baseline,
    df_noise,
    df_power_timeline,
    make_duration_chart,
    make_duration_distribution,
    make_duration_ecdf,
    make_energy_breakdown_chart,
    make_energy_timeline_chart,
    make_excess_energy_chart,
    make_noise_chart,
    make_power_timeline_chart,
    make_time_energy_scatter,
    make_zone_chart,
    noise,
):
    # Chart construction
    chart_duration = make_duration_chart(df_coldstart_summary)
    chart_duration_dist = make_duration_distribution(df_durations)
    chart_duration_ecdf = make_duration_ecdf(df_duration_ecdf)
    chart_energy_breakdown = make_energy_breakdown_chart(df_coldstart_summary)
    chart_excess_energy = make_excess_energy_chart(df_coldstart_summary, noise)
    chart_zones = make_zone_chart(df_coldstart_zones)
    chart_energy_timeline = make_energy_timeline_chart(df_energy_timeline, df_coldstart_summary)
    chart_power_timeline = make_power_timeline_chart(df_power_timeline, df_idle_baseline)
    chart_time_energy = make_time_energy_scatter(df_coldstart_summary, df_coldstart_energy)
    chart_noise = make_noise_chart(df_noise, df_coldstart_summary, noise)
    return (
        chart_duration,
        chart_duration_dist,
        chart_duration_ecdf,
        chart_energy_breakdown,
        chart_energy_timeline,
        chart_excess_energy,
        chart_noise,
        chart_power_timeline,
        chart_time_energy,
        chart_zones,
    )


@app.cell
def md_time():
    mo.md(r"""
    ## Cold-start time

    - **`wasm-rust` is the fastest to serve its first request**, with `wasm-js` next and
      the three other container targets close behind. **`oci-spring` is several times
      slower than any of them** - the JVM paying for class loading and context
      initialization before it can answer at all.
    - **The durations are quantized by k6's histogram buckets**, which limits how far
      the spreads can be read. `histogram_quantile` over `vmrange` returns a bucket
      edge, and the buckets are about 9 % wide in relative terms, so across 30 runs each
      target lands on only a handful of distinct values. Variation narrower than one
      bucket is invisible.

    With that caveat:

    * `oci-spring` has the smallest absolute spread, but relative to its mean that is
      *below* one bucket width - its run-to-run variation sits at or under the measurement
      resolution, so "predictable" is as much a statement about the instrument as about
      the JVM.
    * `wasm-js` is genuinely the least predictable - a long ECDF tail, many buckets wide
      and therefore real.
    * the other four targets sit at roughly two bucket widths: resolvable, but not finely.
    * `wasm-rust` combines the fastest mean with a tight spread, making it the best target
      on this axis by both measures.

    Two repetitions were **excluded as not cold** (`oci-node` run 30 at 17 ms, `wasm-js`
    run 4 at 68 ms): a cold start cannot complete in tens of milliseconds, so KEDA had
    evidently not finished scaling to zero and the request hit a surviving replica. They
    are flagged by `is_cold` rather than deleted, so the exclusion is visible and
    reversible.
    """)
    return


@app.cell
def view_time(chart_duration, chart_duration_dist, chart_duration_ecdf):
    mo.vstack([
        chart_duration,
        mo.hstack([chart_duration_dist, chart_duration_ecdf], justify="start"),
    ])
    return


@app.cell
def md_energy():
    mo.md(r"""
    ## Cold-start energy

    Measured at the **node** with the idle baseline subtracted, because Kepler cannot
    report per-pod counters for a pod that does not exist yet. The baseline comes from
    the `idle-scaled` capture with zero application pods deployed.

    - **Most of the energy in the window is not the cold start.** Over a typical
      few-second window the great majority of the node's draw is idle baseline, and the
      service start accounts for well under a tenth of what was measured. The stacked
      chart shows this directly; only `oci-spring`, whose window is long and whose
      startup is CPU-heavy, breaks out of the baseline.
    - **The excess ranks the targets much as time does, with one exception.**
      `oci-native` costs more than `oci-node` despite starting faster, because it draws
      more power while it starts.
    - Expressed as excess power rather than energy the picture is much flatter for every
      target except `oci-spring`, which sustains a large excess for the whole of its
      long start. A single JVM cold start therefore costs as much energy as a great many
      `wasm-rust` cold starts.
    """)
    return


@app.cell
def view_energy(
    chart_energy_breakdown,
    chart_energy_timeline,
    chart_excess_energy,
    chart_time_energy,
    chart_zones,
):
    mo.vstack([
        mo.hstack([chart_energy_breakdown, chart_excess_energy], justify="start"),
        chart_energy_timeline,
        mo.hstack([chart_zones, chart_time_energy], justify="start"),
    ])
    return


@app.cell
def md_noise():
    mo.md(r"""
    ## How much of this is measurable?

    A small difference between two large numbers needs a stated detection limit rather
    than a bare mean.

    - **The noise floor** comes from the identical estimator applied to **1 211 windows
      of the idle capture**, where no pod starts and the true answer is zero by
      construction. Its mean is close to zero against a much larger sd, so the estimator
      carries no meaningful bias - which is what validates the method.
    - **Almost independent of window length**: the length used here is the pooled median
      cold-start duration, and repeating the sweep at each target's own mean duration
      moves the sd by around 15 % over a 4.7x range of durations. The error is dominated
      by counter quantization at the two window boundaries rather than by the length of
      the integration, so one pooled figure is legitimate for all six targets.
    - **A single measurement is therefore worthless for every target except
      `oci-spring`.** The other five excesses lie far inside the noise band, and
      individual runs come out negative regularly - for `oci-spring` alone, never.
    - Repetition rescues the analysis: with 30 runs the standard error falls far enough
      that the smallest resolvable mean is a few joules (2 SE, the dashed line in the
      energy chart).
    - Against that limit every target's 95 % CI excludes zero, but by very different
      margins. `oci-spring` is unambiguous; `oci-native` and `oci-node` are solid;
      `wasm-rust`, `oci-axum` and `wasm-js` sit close enough to the limit that their
      ordering should not be over-interpreted - their point estimates fall below
      `oci-node` and `oci-native`, but the intervals overlap, so only the gap to
      `oci-spring` is beyond argument.
    """)
    return


@app.cell
def view_noise(chart_noise):
    chart_noise
    return


@app.cell
def md_caveats():
    mo.md(r"""
    ## What the number includes, and what it does not

    Node-scoped, so the excess covers **everything the machine did during the
    window**, not just the application container:

    * the two PostgreSQL instances and three pgbouncer pods answering the query that
      the first request triggers;
    * KEDA, the HPA and the kube scheduler reacting to the scale-from-zero event;
    * the kubelet pulling from cache, creating the sandbox and starting the container.

    That makes it a **whole-system cost of bringing the service up** - an upper bound
    on the runtime's own share, arguably the more useful number for capacity
    planning, but not a clean per-runtime attribution. A target whose startup
    triggers more database work looks worse here even if its own container is frugal.

    Two smaller caveats:

    1. **Counter granularity.** Kepler updates the node counter roughly once per
       second while the scrape runs at 100 ms. Windows of 2-3 s therefore span only a
       handful of counter steps, which is why `window_energy` attributes each scrape
       interval proportionally to its overlap with the window instead of filtering
       samples - filtering would quantize a 2 s measurement to whole counter ticks.
    2. **A single idle capture.** The baseline rests on one 608 s idle window. Its
       own variation is folded into the noise floor above, but a drift in machine
       conditions between the idle capture and the benchmark would shift every
       excess figure by roughly (drift in W) x (window length).
    """)
    return


@app.cell
def export_figures(
    chart_duration,
    chart_duration_dist,
    chart_duration_ecdf,
    chart_energy_breakdown,
    chart_energy_timeline,
    chart_excess_energy,
    chart_noise,
    chart_power_timeline,
    chart_time_energy,
    chart_zones,
    df_coldstart_energy,
    df_coldstart_summary,
    df_coldstart_zones,
    df_durations,
    df_energy_timeline,
    df_idle_baseline,
    df_power_timeline,
    noise,
):
    # The detection limits are quoted in the thesis (6.3 and 7.1.2) but were only
    # ever a dict in this notebook, so nothing could check them against the data.
    df_noise_thresholds = pl.DataFrame([noise])

    # Write every thesis figure/table to disk (figures/*.svg|png, tables/*.csv)
    export_manifest = export_all(
        charts={
            "coldstart_duration": chart_duration,
            "coldstart_duration_distribution": chart_duration_dist,
            "coldstart_duration_ecdf": chart_duration_ecdf,
            "coldstart_energy_breakdown": chart_energy_breakdown,
            "coldstart_excess_energy": chart_excess_energy,
            "coldstart_energy_zones": chart_zones,
            "coldstart_energy_timeline": chart_energy_timeline,
            "coldstart_power_timeline": chart_power_timeline,
            "coldstart_time_energy_scatter": chart_time_energy,
            "coldstart_noise_floor": chart_noise,
        },
        tables={
            "coldstart_summary": df_coldstart_summary,
            "coldstart_durations": df_durations,
            "coldstart_energy_runs": df_coldstart_energy,
            "coldstart_energy_zones": df_coldstart_zones,
            "coldstart_idle_baseline": df_idle_baseline,
            "coldstart_noise_thresholds": df_noise_thresholds,
            "coldstart_power_timeline": df_power_timeline,
            "coldstart_energy_timeline": df_energy_timeline,
        },
    )
    export_manifest
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    $$E_{\text{coldstart}} = \int_{t_0}^{t_1} \Big( P_{\text{node}}(t) - P_{\text{baseline}} \Big) \, dt$$



    $$
    E_{\text{coldstart}}
    = \left(\frac{1}{\Delta t}\int_{t_0}^{t_1}P_{\text{node}}(t),dt - P_{\text{baseline}}\right)\Delta t,
    $$
    since $(P_{\text{baseline}})$ is constant.

    $$E_{\text{coldstart}} \approx (\bar{P}_{\text{node}} - P_{\text{baseline}}) \times \Delta t$$

    $$\bar{P}_{\text{node}} = \text{avg\_over\_time}(\text{kepler\_node\_cpu\_watts}[\Delta t])$$

    $$\bar P_{\text{node}}
    \approx
    \frac{1}{\Delta t}
    \int_{t_0}^{t_1}P_{\text{node}}(t),dt.$$
    """)
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    ### Derivation of the discrete estimator

    Why the energy above can be computed from a mean-power metric and a duration, rather
    than by integrating a power series.

    **Step 1 - expand the continuous integral.** With $\Delta t = t_1 - t_0$, by
    linearity of integration:

    $$\int_{t_0}^{t_1} \Big( P_{\text{node}}(t) - P_{\text{baseline}} \Big) \, dt = \int_{t_0}^{t_1} P_{\text{node}}(t) \, dt - \int_{t_0}^{t_1} P_{\text{baseline}} \, dt$$

    $P_{\text{baseline}}$ is constant over $[t_0, t_1]$, so its integral is
    $P_{\text{baseline}} \Delta t$.

    **Step 2 - factor out $\Delta t$.** Exact algebra, not an approximation:

    $$E_{\text{coldstart}} = \left( \frac{1}{\Delta t} \int_{t_0}^{t_1} P_{\text{node}}(t) \, dt - P_{\text{baseline}} \right) \Delta t$$

    **Step 3 - approximate the mean power.** The leading term is by definition the mean
    of $P_{\text{node}}(t)$ over the interval. Kepler samples power discretely, and
    $\text{avg\_over\_time}(\text{kepler\_node\_cpu\_watts}[\Delta t])$ is the arithmetic
    mean of those samples - a Riemann sum approximating the continuous average:

    $$\bar{P}_{\text{node}} = \text{avg\_over\_time}(\text{kepler\_node\_cpu\_watts}[\Delta t]) \approx \frac{1}{\Delta t} \int_{t_0}^{t_1} P_{\text{node}}(t) \, dt$$

    **Step 4 - substitute.**

    $$E_{\text{coldstart}} \approx (\bar{P}_{\text{node}} - P_{\text{baseline}}) \times \Delta t$$

    Every algebraic step is exact; the only approximation is the discrete sampling in
    step 3, whose error is bounded by the noise-floor sweep above. The notebook does
    **not** use this route - it differences the cumulative joules counter across the
    window, avoiding the sampling approximation entirely. Kept because it shows the two
    formulations agree.
    """)
    return


if __name__ == "__main__":
    app.run()
