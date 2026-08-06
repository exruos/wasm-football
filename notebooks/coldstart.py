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
    is the interval from that request to the **first HTTP 200** - it therefore
    includes image/pod scheduling, container start, runtime boot, connection setup
    and the query itself. **30 repetitions per target.**

    ### What is measured, and how

    * **Cold-start time** comes from k6:
      `histogram_quantile(0.95, sum(k6_http_req_duration_seconds_bucket) by (vmrange))`.
      With exactly one request in flight the quantile is that request's duration; the
      **first** sample of the series is the cold start, and later samples only decay
      as the histogram ages.
    * **Energy is measured at the node**, not the pod. Kepler's per-pod counters do
      not exist before the pod does, so a pod-scoped measurement cannot see the part
      of a cold start that matters. `kepler_node_cpu_joules_total` is a cumulative
      counter per RAPL zone, so the energy over the cold-start window is the counter
      delta across it - no numerical integration of a power series required.
    * **The idle baseline** comes from a separate `idle-scaled` capture with **zero
      application pods deployed**, giving the node's resting draw. Cold-start energy
      is the excess over that baseline:

    $$E_{\text{coldstart}} \approx \big(J_{\text{node}}(t_1) - J_{\text{node}}(t_0)\big) - P_{\text{idle}} \cdot \Delta t$$

    ### What this number contains

    The node is shared. Two PostgreSQL instances, three pgbouncer pods, KEDA, the HPA
    and the kube scheduler all run on it and all do work during a cold start. The
    excess is therefore the **whole-system cost of bringing this service up**, not
    the container's own consumption - an upper bound on the runtime's share, and the
    figure a capacity planner actually pays.

    Because the excess is a small difference between two large numbers, the notebook
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
          single_run_*  - 95 % interval for one measurement
          mean_2se      - the smallest mean excess resolvable by averaging n_runs
        """
        excess = df_noise["excess_j"]
        sd = excess.std()
        return {
            "noise_mean_j": excess.mean(),
            "noise_sd_j": sd,
            "single_run_lo_j": excess.quantile(0.025),
            "single_run_hi_j": excess.quantile(0.975),
            "mean_2se_j": 2 * sd / (n_runs ** 0.5),
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
            ])
            .with_columns([
                (pl.col("excess_mean_j") - 1.96 * pl.col("excess_sem_j")).alias("excess_ci_lo_j"),
                (pl.col("excess_mean_j") + 1.96 * pl.col("excess_sem_j")).alias("excess_ci_hi_j"),
                (pl.col("duration_mean_s") - 1.96 * pl.col("duration_sem_s")).alias("duration_ci_lo_s"),
                (pl.col("duration_mean_s") + 1.96 * pl.col("duration_sem_s")).alias("duration_ci_hi_s"),
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
                alt.Tooltip("duration_std_s:Q", title="sd (s)", format=".2f"),
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
            threshold = pl.DataFrame({"v": [noise["mean_2se_j"]]})
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
                "subtitle": "Bars = mean of 30 runs with 95 % CI. Grey band = 95 % noise range of a SINGLE measurement, dashed line = resolution limit of the 30-run mean",
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

        band = base.mark_area(opacity=0.15).encode(
            x=x, y=alt.Y("band_lower:Q", title="Cumulative energy above idle (J)"), y2="band_upper:Q",
            color=target_color(legend=False),
        )
        line = base.mark_line(strokeWidth=1.8).encode(
            x=x, y=y, color=target_color(),
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
                "subtitle": "Cumulative node energy above the idle baseline, mean of 30 runs +/-1 sd. Triangle = mean time of first HTTP 200",
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
        the idle baseline, averaged across runs with a +/-1 sd band.
        """
        df_plot = df_timeline.filter((pl.col("zone") == zone) & (pl.col("t_bin") <= horizon_s))
        idle_total = df_baseline["idle_power_w"].sum() if zone == "total" else (
            df_baseline.filter(pl.col("zone") == zone)["idle_power_w"].sum()
        )

        base = alt.Chart(df_plot)
        x = alt.X("t_bin:Q", title="Time since request (s)", scale=alt.Scale(nice=False))

        band = base.mark_area(opacity=0.15).encode(
            x=x,
            y=alt.Y("band_lower:Q", title="Node power (W)", scale=alt.Scale(zero=False)),
            y2="band_upper:Q",
            color=target_color(legend=False),
        )
        line = base.mark_line(strokeWidth=1.7).encode(
            x=x,
            y=alt.Y("power_mean_w:Q", title="Node power (W)", scale=alt.Scale(zero=False)),
            color=target_color(),
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
            width=620,
            height=240,
            title={
                "text": "Node power during a cold start",
                "subtitle": "Mean of 30 runs +/-1 sd; dashed line = idle node with zero application pods",
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
        layers.append(
            means.mark_text(align="left", dx=10, dy=-8, fontSize=11).encode(
                text="target:N", color=target_color(legend=False)
            )
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

        return alt.layer(*layers).properties(
            width=440,
            height=330,
            title={
                "text": "Cold-start time vs. energy",
                "subtitle": "Large points = 30-run mean with 95 % CI, small points = individual runs. Bottom-left is best",
            },
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
                "subtitle": f"{noise['n_windows']} idle-only windows, sd {noise['noise_sd_j']:.1f} J. Vertical lines = measured per-target means",
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
              f"resolution of the 30-run mean **{noise['mean_2se_j']:.1f} J**."),
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

    **`wasm-rust` is the fastest to serve its first request at 2.02 s**, followed by
    `wasm-js` (2.81 s), `oci-native` (3.16 s), `oci-node` (3.53 s) and `oci-axum`
    (3.58 s). **`oci-spring` needs 9.53 s** - 4.7x the fastest target and 2.7x the
    next slowest, which is the JVM paying for class loading and context
    initialization before it can answer at all.

    The distributions matter as much as the means:

    * `oci-spring` is slow but *predictable* (sd 0.37 s, the tightest in the field).
    * `wasm-js` is the least predictable (sd 1.36 s, runs from 1.5 s to 9.5 s) - its
      ECDF has a long tail, so a user's experience of it varies wildly.
    * `wasm-rust` combines the fastest mean with a tight spread (sd 0.39 s), making
      it the best target on this axis by both measures.

    Two repetitions were **excluded as not cold** (`oci-node` run 30 at 17 ms,
    `wasm-js` run 4 at 68 ms): a cold start cannot complete in tens of milliseconds,
    so KEDA had evidently not finished scaling to zero and the request hit a
    surviving replica. They are flagged by `is_cold` rather than deleted, so the
    exclusion is visible and reversible.
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

    Energy is measured at the **node** and the idle baseline is subtracted, because
    Kepler cannot report per-pod counters for a pod that does not exist yet. The
    baseline comes from the `idle-scaled` capture with zero application pods:
    **26.57 W** (21.91 W package + 4.66 W dram).

    Two things follow from that, and the first is uncomfortable:

    **Most of the energy in the window is not the cold start.** Over a 3.6 s
    `oci-axum` cold start the node draws 105 J, of which **95 J is idle baseline** -
    the service start accounts for 9.8 J, under 10 % of what was measured. The stacked
    chart shows this directly; only `oci-spring`, whose window is long and whose
    startup is CPU-heavy, breaks out of the baseline (441 J excess against 253 J of
    baseline).

    **The excess ranks the targets the same way as time, with one exception.**
    Mean excess energy per cold start: `wasm-rust` **6.3 J**, `oci-axum` **9.8 J**,
    `wasm-js` **13.3 J**, `oci-node` **19.4 J**, `oci-native` **22.9 J**,
    `oci-spring` **441 J**. `oci-native` costs more than `oci-node` despite starting
    faster, because it draws more power while it starts (7.5 W vs 5.4 W above idle).

    Expressed as excess power, the picture is flatter: 2.8-7.5 W above idle for every
    target except `oci-spring`, which sustains **46 W above idle** for 9.5 s. A
    single JVM cold start therefore costs roughly **as much energy as 70 `wasm-rust`
    cold starts**.
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

    The excess is a small difference between two large numbers, so it needs a stated
    detection limit rather than a bare mean. Applying the **identical estimator to
    1 210 windows of the idle capture**, where no pod starts and the true answer is
    zero by construction, gives the noise floor:

    * mean **0.03 J** - the estimator is unbiased, which validates the method;
    * sd **15.2 J**, with a 95 % range of **-35 J to +34 J** for a *single* window.

    **A single cold-start measurement is therefore worthless for every target except
    `oci-spring`.** The 6-23 J excesses are far inside the noise band, and individual
    runs come out negative regularly. What rescues the analysis is repetition: with
    30 runs the standard error falls to about 2.8 J, so the smallest resolvable mean
    is **5.6 J** (2 SE, the dashed line in the energy chart).

    Against that limit every target's 95 % CI excludes zero, but by very different
    margins: `oci-spring` (CI 419-464 J) is unambiguous, `oci-native` (12.9-33.0 J)
    and `oci-node` (10.0-28.8 J) are solid, while `wasm-rust` (0.5-12.1 J) and
    `wasm-js` (0.3-26.2 J) sit close enough to the limit that their ordering should
    not be over-interpreted - the honest statement is that both are small, and
    smaller than the container targets.
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

    The measurement is node-scoped, so the excess covers **everything the machine did
    during the window**, not just the application container:

    * the two PostgreSQL instances and three pgbouncer pods answering the query that
      the first request triggers;
    * KEDA, the HPA and the kube scheduler reacting to the scale-from-zero event;
    * the kubelet pulling from cache, creating the sandbox and starting the container.

    That makes the figure a **whole-system cost of bringing the service up** - an
    upper bound on the runtime's own share, and arguably the more useful number for
    capacity planning, but not a clean per-runtime attribution. A target whose
    startup triggers more database work will look worse here even if its own
    container is frugal.

    Two smaller caveats worth stating in the thesis:

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
):
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
    To evaluate the statement, we can analyze the mathematical derivation step-by-step from the initial continuous integral down to the discrete metric approximation.

    ---

    ### Step 1: Continuous Integral Expansion

    Let $\Delta t = t_1 - t_0$ represent the duration of the cold start interval. Starting from the total energy integral:

    $$E_{\text{coldstart}} = \int_{t_0}^{t_1} \Big( P_{\text{node}}(t) - P_{\text{baseline}} \Big) \, dt$$

    By the linearity property of integration, we split the integral into two parts:

    $$\int_{t_0}^{t_1} \Big( P_{\text{node}}(t) - P_{\text{baseline}} \Big) \, dt = \int_{t_0}^{t_1} P_{\text{node}}(t) \, dt - \int_{t_0}^{t_1} P_{\text{baseline}} \, dt$$

    Since $P_{\text{baseline}}$ is constant over $[t_0, t_1]$, its integral simplifies to:

    $$\int_{t_0}^{t_1} P_{\text{baseline}} \, dt = P_{\text{baseline}} \cdot (t_1 - t_0) = P_{\text{baseline}} \Delta t$$

    ---

    ### Step 2: Factoring $\Delta t$

    Substituting $P_{\text{baseline}} \Delta t$ back into the split integral:

    $$E_{\text{coldstart}} = \int_{t_0}^{t_1} P_{\text{node}}(t) \, dt - P_{\text{baseline}} \Delta t$$

    Factoring out $\Delta t$:

    $$E_{\text{coldstart}} = \left( \frac{1}{\Delta t} \int_{t_0}^{t_1} P_{\text{node}}(t) \, dt - P_{\text{baseline}} \right) \Delta t$$

    This algebraic transformation is exact and confirms the equality of the second formula.

    ---

    ### Step 3: Mean Power Approximation

    By definition, the mean value of a continuous power function $P_{\text{node}}(t)$ over time interval $\Delta t$ is:

    $$\bar{P}_{\text{node, exact}} = \frac{1}{\Delta t} \int_{t_0}^{t_1} P_{\text{node}}(t) \, dt$$

    In monitoring systems like Prometheus and Kepler, power is measured via discrete samples. The function $\text{avg\_over\_time}(\text{kepler\_node\_cpu\_watts}[\Delta t])$ computes the arithmetic mean of these discrete samples, which serves as a Riemann sum approximation of the continuous time average:

    $$\bar{P}_{\text{node}} = \text{avg\_over\_time}(\text{kepler\_node\_cpu\_watts}[\Delta t]) \approx \frac{1}{\Delta t} \int_{t_0}^{t_1} P_{\text{node}}(t) \, dt$$

    ---

    ### Step 4: Final Substitution

    Replacing the continuous mean integral with the discrete sample average $\bar{P}_{\text{node}}$ yields the energy estimate:

    $$E_{\text{coldstart}} \approx (\bar{P}_{\text{node}} - P_{\text{baseline}}) \times \Delta t$$

    ---

    Since every algebraic step is exact and the transition to PromQL discrete sampling is mathematically valid, the overall statement is **True**.
    """)
    return


if __name__ == "__main__":
    app.run()
