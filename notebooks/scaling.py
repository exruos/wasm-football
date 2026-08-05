import marimo

__generated_with = "0.23.16"
app = marimo.App(width="full")

with app.setup:
    import altair as alt
    import marimo as mo
    import polars as pl
    from pathlib import Path
    from common_notebook import build_scenario_table, load_scenario_metrics, color_scale
    alt.data_transformers.enable("vegafusion")


@app.cell
def md_intro():
    mo.md(r"""
    # Auto-scaling benchmark: six target architectures

    A 13-minute k6 scenario replayed **10 times per target**, scraped from Prometheus
    (Kepler RAPL energy, cAdvisor cpu/memory, kube-state replica counts) and k6
    (latency, throughput, request outcomes). Six targets: `oci-axum`, `oci-native`,
    `oci-node`, `oci-spring`, `wasm-js`, `wasm-rust`.

    Scaling is driven by **KEDA on concurrency = 20**.

    | Window | Range | What it measures |
    | --- | --- | --- |
    | Scale-Up | 0-240 s | ramp to 40 then 80 VUs — cold starts, HPA reaction time |
    | Ramp-Up | 240-480 s | ramp to 100 VUs — scaling under contention |
    | Steady State | 480-600 s | hold at 100 VUs — peak capacity and efficiency |
    | Cooldown | 660-780 s | ramp to 0 VUs — retained footprint after load stops |
    | Overall | 0-780 s | macro summary of the full run |

    **Reading the numbers.** Kepler energy counters and cAdvisor cpu/memory series
    are *per pod*: each pod is differenced on its own series before the increments
    are summed, and resource gauges are reported both per replica and as a cluster
    total. Collapsing pods before differencing understates energy for the targets
    that run the most replicas.
    """)
    return


@app.cell
def load_data():
    # Label columns kept from the raw Prometheus/k6 parquets:
    #   url                 - k6 route label (request mix breakdown)
    #   pod_name / pod      - per-pod series: kepler energy counters (pod_name) and
    #                         cAdvisor cpu/memory (pod). Required — these counters are
    #                         PER POD, so they must be differenced/summed per pod.
    #   status / expected_response - k6 request outcome (200 vs timeout)
    select_scaling_columns = ["url", "pod_name", "pod", "status", "expected_response"]
    df_scaling = load_scenario_metrics("scaling", select_columns=select_scaling_columns)
    df_scaling
    return (df_scaling,)


@app.cell
def processing_helpers():
    # Scaling benchmark analysis — constants & Polars processing helpers
    # -------------------------------------------------------------------------
    # 1. Experiment constants
    # -------------------------------------------------------------------------
    # The 13-minute k6 scenario splits into four evaluation windows plus a macro
    # "overall" window covering the full run.
    WINDOWS = {
        "scale_up": (0.0, 240.0),    # ramp to 40 then 80 VUs: cold starts, HPA reaction
        "ramp_up": (240.0, 480.0),   # ramp to 100 VUs: scaling under contention
        "steady": (480.0, 600.0),    # hold at 100 VUs: peak capacity & efficiency
        "cooldown": (660.0, 780.0),  # ramp to 0 VUs: scale-to-zero & trailing usage
        "overall": (0.0, 780.0),     # full benchmark
    }

    WINDOW_ORDER = ["scale_up", "ramp_up", "steady", "cooldown", "overall"]

    WINDOW_LABELS = {
        "scale_up": "Scale-Up (0-240 s)",
        "ramp_up": "Ramp-Up (240-480 s)",
        "steady": "Steady State (480-600 s)",
        "cooldown": "Cooldown (660-780 s)",
        "overall": "Overall (0-780 s)",
    }

    # Single source of truth for target ordering: matches common_notebook.color_scale()
    TARGET_ORDER = list(color_scale().domain)

    # Axis titles / units per metric, used by charts and exported tables.
    METRIC_LABELS = {
        "rps": ("Throughput", "req/s"),
        "pods": ("Pod Count", "pods"),
        "cpu_usage": ("CPU Usage", "cores"),
        "memory": ("Memory per Pod", "MB"),
        "p95": ("P95 Latency", "ms"),
        "p99": ("P99 Latency", "ms"),
        "vus": ("Virtual Users", "VUs"),
        "checks_rate": ("Check Success Rate", "ratio"),
        "requests": ("Requests (cumulative)", "requests"),
        "pod_joules": ("Pod Energy (cumulative)", "J"),
    }

    # Metrics stored as monotonically increasing counters: aggregate them as
    # deltas inside a window rather than as instantaneous means.
    CUMULATIVE_METRICS = {"requests", "iterations", "pod_joules"}


    def target_rank(column: str = "target") -> pl.Expr:
        """Sort key that keeps targets in the canonical color_scale() order.
        Preferred over pl.Enum: vegafusion cannot serialize Enum/categorical columns."""
        return pl.col(column).replace_strict(
            {t: i for i, t in enumerate(TARGET_ORDER)}, default=len(TARGET_ORDER)
        )


    def metric_title(metric: str) -> str:
        label, unit = METRIC_LABELS.get(metric, (metric.upper(), ""))
        return f"{label} ({unit})" if unit else label


    # -------------------------------------------------------------------------
    # 2. Window slicing
    # -------------------------------------------------------------------------
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


    def window_mask(window: str) -> pl.Expr:
        """Boolean expression selecting one window by name (upper bound inclusive
        only for the final windows, matching window_slice)."""
        lo, hi = WINDOWS[window]
        t = pl.col("normalized_time")
        upper = t <= hi if window in ("cooldown", "overall") else t < hi
        return (t >= lo) & upper


    def with_counter_deltas(
        df: pl.DataFrame, value_col: str = "value", alias: str = "delta"
    ) -> pl.DataFrame:
        """
        Per-(target, iteration) first difference of a cumulative counter, clipped at
        zero so counter resets (pod restarts, RAPL wraparound) never subtract.
        Diffs are computed on the full series BEFORE any window filtering.
        """
        return df.sort(["target", "iteration", "normalized_time"]).with_columns(
            pl.col(value_col).diff().over(["target", "iteration"]).clip(lower_bound=0.0).alias(alias)
        )


    # -------------------------------------------------------------------------
    # 3. Windowed aggregations
    # -------------------------------------------------------------------------
    def aggregate_metric_windows(
        df: pl.DataFrame, metric: str | None = None, per_iter: str = "auto"
    ) -> pl.DataFrame:
        """
        For a metric frame (target, iteration, normalized_time, value), reduce each
        (target, window, iteration) to one sample, then summarize across the 10
        iterations: mean, std, min, max, p95 — grouped by target and window.

        per_iter: how one iteration is reduced inside a window.
          "mean"  - time-average of the sampled signal (default for gauges)
          "max"   - peak within the window (spikes, high-water pod count)
          "sum"   - delta-sum for cumulative counters
          "auto"  - "sum" for CUMULATIVE_METRICS, else "mean"
        """
        if per_iter == "auto":
            per_iter = "sum" if metric in CUMULATIVE_METRICS else "mean"

        if per_iter == "sum":
            dfw = window_slice(with_counter_deltas(df))
            sample = pl.col("delta").sum()
        elif per_iter == "max":
            dfw = window_slice(df)
            sample = pl.col("value").max()
        else:
            dfw = window_slice(df)
            sample = pl.col("value").mean()

        per_iteration = dfw.group_by(["target", "window", "iteration"]).agg(
            sample.alias("iter_value")
        )

        summary = (
            per_iteration.group_by(["target", "window"])
            .agg([
                pl.col("iter_value").mean().alias("mean"),
                pl.col("iter_value").std().alias("std"),
                pl.col("iter_value").min().alias("min"),
                pl.col("iter_value").max().alias("max"),
                pl.col("iter_value").quantile(0.95).alias("p95"),
                pl.len().alias("n_iterations"),
            ])
            .with_columns([
                pl.lit(metric or "value").alias("metric"),
                pl.col("window").replace_strict(WINDOW_LABELS, default=pl.col("window")).alias("window_label"),
            ])
            .sort(["target", "window"])
        )
        return summary.select(
            ["metric", "target", "window", "window_label", "mean", "std", "min", "max", "p95", "n_iterations"]
        )


    def build_windowed_summary(
        df_scaling: dict,
        metrics: list[str] | None = None,
        per_iter: str = "auto",
    ) -> pl.DataFrame:
        """
        Long-form windowed statistics for every requested metric:
        one row per (metric, target, window) with mean/std/min/max/p95.
        """
        metrics = metrics or ["rps", "pods", "cpu_usage", "memory", "p95", "p99", "checks_rate"]
        parts = [
            aggregate_metric_windows(df_scaling[m], metric=m, per_iter=per_iter)
            for m in metrics
            if m in df_scaling
        ]
        return (
            pl.concat(parts, how="vertical")
            .with_columns([
                target_rank().alias("_target_rank"),
                pl.col("window").replace_strict(
                    {w: i for i, w in enumerate(WINDOW_ORDER)}, default=len(WINDOW_ORDER)
                ).alias("_window_rank"),
            ])
            .sort(["metric", "_window_rank", "_target_rank"])
            .drop("_target_rank", "_window_rank")
        )


    def windowed_metric_table(
        df_windowed: pl.DataFrame, metric: str, window: str = "steady"
    ) -> pl.DataFrame:
        """Display-ready slice of the long windowed summary: one row per target."""
        return (
            df_windowed.filter(
                (pl.col("metric") == metric) & (pl.col("window") == window)
            )
            .select(["target", "mean", "std", "min", "max", "p95"])
            .sort("target")
        )


    # -------------------------------------------------------------------------
    # 4. RAPL energy processing (pod_joules: zone = package | dram)
    # -------------------------------------------------------------------------
    # kepler_pod_cpu_joules_total is a counter PER POD PER ZONE. Each pod must be
    # differenced on its own series and the increments summed across the pods that
    # were alive at that moment. Taking a max/mean across pods first tracks only the
    # hottest replica and silently discards energy whenever the leading pod changes
    # (that is what made wasm-js, which runs the most replicas, look ~1000x cheaper
    # than everything else).
    POD_ID_COLUMN = "pod_name"


    def pod_joules_increments(df_pj: pl.DataFrame) -> pl.DataFrame:
        """
        Per-timestamp incremental joules by RAPL zone, summed over all pods of a
        target: columns package, dram, total = package + dram.
        """
        keys = ["target", "iteration", "zone"]
        if POD_ID_COLUMN in df_pj.columns:
            keys.append(POD_ID_COLUMN)

        increments = df_pj.sort(keys + ["normalized_time"]).with_columns(
            pl.col("value")
            .diff()
            .over(keys)
            .fill_null(0.0)          # a pod's first sample has no predecessor
            .clip(lower_bound=0.0)   # counter resets on pod restart
            .alias("joules")
        )

        return (
            increments.group_by(["target", "iteration", "normalized_time"])
            .agg([
                pl.col("joules").filter(pl.col("zone") == "package").sum().alias("package"),
                pl.col("joules").filter(pl.col("zone") == "dram").sum().alias("dram"),
            ])
            .with_columns((pl.col("package") + pl.col("dram")).alias("total"))
            .sort(["target", "iteration", "normalized_time"])
        )


    def process_pod_joules_windows(
        df_pj: pl.DataFrame, df_requests: pl.DataFrame
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """
        Energy consumed per evaluation window, per RAPL zone, plus request-normalized
        efficiency (joules per 10k requests).

        Returns (per_iteration, summary_across_iterations).
        """
        # Increments are already per-sample, so windows only need summing.
        energy = (
            window_slice(pod_joules_increments(df_pj))
            .group_by(["target", "iteration", "window"])
            .agg([
                pl.col("package").sum().alias("package_joules"),
                pl.col("dram").sum().alias("dram_joules"),
                pl.col("total").sum().alias("total_joules"),
            ])
        )

        requests = (
            window_slice(request_increments(df_requests))
            .group_by(["target", "iteration", "window"])
            .agg([
                pl.col("requests").sum().alias("requests_total"),
                pl.col("requests_ok").sum().alias("requests_ok"),
                pl.col("requests_failed").sum().alias("requests_failed"),
            ])
        )

        joined = energy.join(
            requests, on=["target", "iteration", "window"], how="left"
        ).with_columns([
            pl.when(pl.col("requests_total") > 0)
            .then(pl.col("total_joules") / pl.col("requests_total") * 10_000.0)
            .otherwise(None)
            .alias("joules_per_10k_requests"),
            # Failed requests still burn energy but deliver nothing: normalizing by
            # successful requests is the honest efficiency number.
            pl.when(pl.col("requests_ok") > 0)
            .then(pl.col("total_joules") / pl.col("requests_ok") * 10_000.0)
            .otherwise(None)
            .alias("joules_per_10k_successful_requests"),
            pl.when(pl.col("requests_total") > 0)
            .then(pl.col("package_joules") / pl.col("requests_total") * 10_000.0)
            .otherwise(None)
            .alias("package_joules_per_10k_requests"),
            pl.when(pl.col("requests_total") > 0)
            .then(pl.col("dram_joules") / pl.col("requests_total") * 10_000.0)
            .otherwise(None)
            .alias("dram_joules_per_10k_requests"),
        ])

        summary = (
            joined.group_by(["target", "window"])
            .agg([
                pl.col("package_joules").mean().alias("package_mean"),
                pl.col("package_joules").std().alias("package_std"),
                pl.col("dram_joules").mean().alias("dram_mean"),
                pl.col("dram_joules").std().alias("dram_std"),
                pl.col("total_joules").mean().alias("total_mean"),
                pl.col("total_joules").std().alias("total_std"),
                pl.col("requests_total").mean().alias("requests_mean"),
                pl.col("requests_ok").mean().alias("requests_ok_mean"),
                pl.col("requests_failed").mean().alias("requests_failed_mean"),
                pl.col("joules_per_10k_requests").mean().alias("efficiency_mean"),
                pl.col("joules_per_10k_requests").std().alias("efficiency_std"),
                pl.col("joules_per_10k_successful_requests").mean().alias("efficiency_ok_mean"),
                pl.col("joules_per_10k_successful_requests").std().alias("efficiency_ok_std"),
            ])
            .sort(["target", "window"])
        )
        return joined, summary


    def process_pod_joules(
        df_pj: pl.DataFrame, df_requests: pl.DataFrame, window: str = "steady"
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """
        Steady-state (default) view of the windowed energy tables:
          per-iteration joules by zone + joules per 10k requests, and the summary
          across the 10 iterations. Efficiency = (total_joules / requests) * 10_000.
        """
        joined, summary = process_pod_joules_windows(df_pj, df_requests)
        return (
            joined.filter(pl.col("window") == window).drop("window"),
            summary.filter(pl.col("window") == window).drop("window"),
        )


    # -------------------------------------------------------------------------
    # 5. Scenario-specific derived metrics
    # -------------------------------------------------------------------------
    def request_increments(df_requests: pl.DataFrame) -> pl.DataFrame:
        """
        Per-timestamp request increments from the k6_http_reqs_total counter.
        The counter is split across label series (url, status, expected_response),
        so each series is differenced separately and then summed.
          requests        - all requests
          requests_ok     - expected_response == "true" (HTTP 200)
          requests_failed - everything else (timeouts, 5xx)
        """
        label_cols = [c for c in ("url", "status", "expected_response") if c in df_requests.columns]
        keys = ["target", "iteration", *label_cols]

        increments = df_requests.sort(keys + ["normalized_time"]).with_columns(
            pl.col("value").diff().over(keys).fill_null(0.0).clip(lower_bound=0.0).alias("n")
        )

        ok_expr = (
            (pl.col("expected_response") == "true")
            if "expected_response" in df_requests.columns
            else pl.lit(True)
        )

        return (
            increments.group_by(["target", "iteration", "normalized_time"])
            .agg([
                pl.col("n").sum().alias("requests"),
                pl.col("n").filter(ok_expr).sum().alias("requests_ok"),
                pl.col("n").filter(~ok_expr).sum().alias("requests_failed"),
            ])
            .sort(["target", "iteration", "normalized_time"])
        )


    def process_request_outcomes(df_requests: pl.DataFrame) -> pl.DataFrame:
        """Delivered vs. failed request volume and failure rate per target and window."""
        per_iter = (
            window_slice(request_increments(df_requests))
            .group_by(["target", "iteration", "window"])
            .agg([
                pl.col("requests").sum().alias("requests"),
                pl.col("requests_ok").sum().alias("requests_ok"),
                pl.col("requests_failed").sum().alias("requests_failed"),
            ])
            .with_columns(
                pl.when(pl.col("requests") > 0)
                .then(pl.col("requests_failed") / pl.col("requests"))
                .otherwise(0.0)
                .alias("failure_rate")
            )
        )
        return (
            per_iter.group_by(["target", "window"])
            .agg([
                pl.col("requests").mean().alias("requests_mean"),
                pl.col("requests_ok").mean().alias("requests_ok_mean"),
                pl.col("requests_failed").mean().alias("requests_failed_mean"),
                pl.col("failure_rate").mean().alias("failure_rate_mean"),
                pl.col("failure_rate").max().alias("failure_rate_max"),
            ])
            .sort(target_rank())
        )


    def per_pod_and_cluster(df: pl.DataFrame, window: str = "steady") -> pl.DataFrame:
        """
        Resource gauges (cpu_usage, memory) carry one series per pod. Report both
        readings explicitly instead of silently averaging them together:
          *_per_pod  - mean of a single replica
          *_cluster  - sum over the replicas alive at each timestamp, time-averaged
        """
        pod_col = next((c for c in ("pod", "pod_name") if c in df.columns), None)
        dfw = df.filter(window_mask(window))

        per_pod = (
            dfw.group_by(["target", "iteration"])
            .agg(pl.col("value").mean().alias("per_pod"))
        )

        if pod_col is None:
            cluster = per_pod.rename({"per_pod": "cluster"})
        else:
            cluster = (
                dfw.group_by(["target", "iteration", "normalized_time"])
                .agg(pl.col("value").sum().alias("total"))
                .group_by(["target", "iteration"])
                .agg(pl.col("total").mean().alias("cluster"))
            )

        return per_pod.join(cluster, on=["target", "iteration"], how="left")


    def build_efficiency_frame(
        df_scaling: dict, window: str = "steady"
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """
        Per-iteration efficiency observations for the trade-off scatter plots:
        throughput, latency and replica count next to the energy actually spent.

          requests_per_joule    - work delivered per unit of energy (higher is better)
          joules_per_10k_requests - inverse view, comparable across scenarios (lower is better)
          joules_per_10k_successful_requests - same, counting only HTTP 200s

        Returns (per_iteration, summary_across_iterations).
        """
        energy, _ = process_pod_joules_windows(df_scaling["pod_joules"], df_scaling["requests"])
        energy = energy.filter(pl.col("window") == window).drop("window")

        mask = window_mask(window)

        def _per_iter(metric: str, expr: pl.Expr, alias: str) -> pl.DataFrame:
            return (
                df_scaling[metric]
                .filter(mask)
                .group_by(["target", "iteration"])
                .agg(expr.alias(alias))
            )

        frame = (
            energy.join(_per_iter("rps", pl.col("value").mean(), "rps"), on=["target", "iteration"], how="left")
            .join(_per_iter("p95", pl.col("value").mean(), "p95_ms"), on=["target", "iteration"], how="left")
            .join(_per_iter("pods", pl.col("value").mean(), "pods"), on=["target", "iteration"], how="left")
            .join(
                per_pod_and_cluster(df_scaling["memory"], window).rename({
                    "per_pod": "memory_per_pod_mb",
                    "cluster": "memory_cluster_mb",
                }),
                on=["target", "iteration"],
                how="left",
            )
            .with_columns([
                pl.when(pl.col("total_joules") > 0)
                .then(pl.col("requests_total") / pl.col("total_joules"))
                .otherwise(None)
                .alias("requests_per_joule"),
                pl.when(pl.col("total_joules") > 0)
                .then(pl.col("requests_ok") / pl.col("total_joules"))
                .otherwise(None)
                .alias("successful_requests_per_joule"),
            ])
            .sort(target_rank())
        )

        stat_cols = [
            "rps",
            "p95_ms",
            "pods",
            "memory_per_pod_mb",
            "memory_cluster_mb",
            "total_joules",
            "requests_total",
            "requests_per_joule",
            "successful_requests_per_joule",
            "joules_per_10k_requests",
            "joules_per_10k_successful_requests",
        ]
        summary = (
            frame.group_by("target")
            .agg(
                [pl.col(c).mean().alias(c) for c in stat_cols]
                + [pl.col(c).std().alias(f"{c}_std") for c in ("rps", "joules_per_10k_requests", "requests_per_joule", "p95_ms")]
            )
            .sort(target_rank())
        )
        return frame, summary


    def pareto_frontier(
        df: pl.DataFrame, x: str, y: str, maximize_x: bool = True, minimize_y: bool = True
    ) -> pl.DataFrame:
        """
        Non-dominated targets for a two-objective trade-off. A target is on the
        frontier when no other target beats it on one axis without losing on the other.
        """
        rows = df.sort(x, descending=maximize_x).to_dicts()
        frontier, best = [], None
        for row in rows:
            value = row[y]
            if best is None or (value < best if minimize_y else value > best):
                frontier.append(row)
                best = value
        return pl.DataFrame(frontier).sort(x)


    def process_scale_up_responsiveness(
        df_p95: pl.DataFrame, df_checks: pl.DataFrame, df_pods: pl.DataFrame
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """
        Evaluates cold starts and scaling contention during Scale-Up (0s-240s):
          - max_latency_spike_p95: Peak P95 latency spike while waiting for HPA/pods
          - min_checks_rate: Worst success check rate (detects dropped requests/5xx errors)
          - scale_up_max_pods: Pod count reached during initial scale-up
          - time_to_first_scale_s: first timestamp where the pod count exceeds its
            initial value (how quickly the platform reacts to load)
        """
        scale_up = window_mask("scale_up")

        p95_spikes = (
            df_p95.filter(scale_up)
            .group_by(["target", "iteration"])
            .agg(pl.col("value").max().alias("max_latency_spike_p95"))
        )

        checks_drops = (
            df_checks.filter(scale_up)
            .group_by(["target", "iteration"])
            .agg(pl.col("value").min().alias("min_checks_rate"))
        )

        pods_scale = (
            df_pods.filter(scale_up)
            .sort(["target", "iteration", "normalized_time"])
            .group_by(["target", "iteration"])
            .agg([
                pl.col("value").max().alias("scale_up_max_pods"),
                pl.col("value").first().alias("initial_pods"),
                pl.col("normalized_time")
                .filter(pl.col("value") > pl.col("value").first())
                .min()
                .alias("time_to_first_scale_s"),
            ])
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
                pl.col("time_to_first_scale_s").mean().alias("time_to_first_scale_s_mean"),
            ])
            .sort("target")
        )
        return joined, summary


    def process_scale_to_zero(df_pods: pl.DataFrame) -> pl.DataFrame:
        """
        Replica behaviour during Cooldown (660s-780s).

        NOTE: KEDA scales on concurrency (=20) and its scale-down only fires ~30 s
        after the run ends, so within the measurement window the replicas stay up by
        design — traffic is still load balanced across them while VUs ramp to 0.
        These columns therefore quantify the RETAINED footprint at end of run, not
        drain speed; expect drain_time_s == 0 and end == start pods. Scale-down
        latency itself would need a longer trailing capture.
        """
        cooldown = window_mask("cooldown")

        per_iter = (
            df_pods.filter(cooldown)
            .sort(["target", "iteration", "normalized_time"])
            .group_by(["target", "iteration"])
            .agg([
                pl.col("value").first().alias("cooldown_start_pods"),
                pl.col("value").last().alias("cooldown_end_pods"),
                pl.col("value").min().alias("cooldown_min_pods"),
                (
                    pl.col("normalized_time").filter(pl.col("value") == pl.col("value").min()).min()
                    - pl.lit(WINDOWS["cooldown"][0])
                ).alias("drain_time_s"),
            ])
        )

        return (
            per_iter.group_by("target")
            .agg([
                pl.col("cooldown_start_pods").mean().alias("cooldown_start_pods_mean"),
                pl.col("cooldown_end_pods").mean().alias("cooldown_end_pods_mean"),
                pl.col("cooldown_min_pods").mean().alias("cooldown_min_pods_mean"),
                pl.col("drain_time_s").mean().alias("drain_time_s_mean"),
            ])
            .sort("target")
        )


    def process_memory_efficiency(
        df_memory: pl.DataFrame, df_pods: pl.DataFrame
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """
        Computes memory footprint metrics during Steady State (480s-600s).
        Assumes df_memory values are ALREADY in Megabytes (MB) per pod.
        """
        steady = window_mask("steady")

        # Cluster total is the real sum over the live replicas at each timestamp,
        # not per-pod mean x replica count (which ignores staggered pod lifetimes).
        mem_steady = per_pod_and_cluster(df_memory, "steady").rename({
            "per_pod": "memory_per_pod_mb",
            "cluster": "total_cluster_memory_mb",
        })

        pods_steady = (
            df_pods.filter(steady)
            .group_by(["target", "iteration"])
            .agg(pl.col("value").mean().alias("avg_pod_count"))
        )

        joined = mem_steady.join(pods_steady, on=["target", "iteration"], how="left")

        summary = (
            joined.group_by("target")
            .agg([
                pl.col("memory_per_pod_mb").mean().alias("memory_per_pod_mb_mean"),
                pl.col("memory_per_pod_mb").std().alias("memory_per_pod_mb_std"),
                pl.col("total_cluster_memory_mb").mean().alias("total_cluster_memory_mb_mean"),
                pl.col("total_cluster_memory_mb").std().alias("total_cluster_memory_mb_std"),
            ])
            .sort("target")
        )

        return joined, summary


    def process_cooldown_idle_drain(
        df_cpu: pl.DataFrame, df_memory: pl.DataFrame
    ) -> pl.DataFrame:
        """
        Evaluates residual CPU and Memory utilization during Cooldown (660s-780s).
        """
        cooldown = window_mask("cooldown")

        cpu_idle = (
            df_cpu.filter(cooldown)
            .group_by(["target", "iteration"])
            .agg(pl.col("value").mean().alias("cooldown_cpu_mean"))
        )

        mem_idle = (
            df_memory.filter(cooldown)
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
        Master benchmark summary: one row per target combining steady-state
        performance, resource footprint, energy and scale-up responsiveness.
        """
        steady = window_mask("steady")

        def _steady_agg(metric: str, expr: pl.Expr, alias: str) -> pl.DataFrame:
            return (
                df_scaling[metric]
                .filter(steady)
                .group_by("target")
                .agg(expr.alias(alias))
            )

        p95_steady = _steady_agg("p95", pl.col("value").mean(), "p95_latency_ms")
        p99_steady = _steady_agg("p99", pl.col("value").mean(), "p99_latency_ms")
        rps_steady = _steady_agg("rps", pl.col("value").mean(), "steady_rps")
        checks_steady = _steady_agg("checks_rate", pl.col("value").min(), "min_checks_rate")
        cpu_split = (
            per_pod_and_cluster(df_scaling["cpu_usage"], "steady")
            .group_by("target")
            .agg([
                pl.col("per_pod").mean().alias("cpu_cores_per_pod"),
                pl.col("cluster").mean().alias("cpu_cores_cluster"),
            ])
        )
        pods_steady = _steady_agg("pods", pl.col("value").mean(), "steady_pods")

        _, energy_sum = process_pod_joules(df_scaling["pod_joules"], df_scaling["requests"])
        outcomes = process_request_outcomes(df_scaling["requests"]).filter(
            pl.col("window") == "steady"
        )
        _, scale_up_sum = process_scale_up_responsiveness(
            df_scaling["p95"], df_scaling["checks_rate"], df_scaling["pods"]
        )
        _, mem_sum = process_memory_efficiency(df_scaling["memory"], df_scaling["pods"])
        idle_sum = process_cooldown_idle_drain(df_scaling["cpu_usage"], df_scaling["memory"])
        zero_sum = process_scale_to_zero(df_scaling["pods"])

        master_table = (
            p95_steady.join(p99_steady, on="target", how="left")
            .join(rps_steady, on="target", how="left")
            .join(checks_steady, on="target", how="left")
            .join(cpu_split, on="target", how="left")
            .join(
                outcomes.select(["target", "requests_ok_mean", "failure_rate_mean"]),
                on="target",
                how="left",
            )
            .join(pods_steady, on="target", how="left")
            .join(
                mem_sum.select(["target", "memory_per_pod_mb_mean", "total_cluster_memory_mb_mean"]),
                on="target",
                how="left",
            )
            .join(
                energy_sum.select(["target", "total_mean", "efficiency_mean", "efficiency_ok_mean"]),
                on="target",
                how="left",
            )
            .join(
                scale_up_sum.select(["target", "p95_spike_mean", "time_to_first_scale_s_mean"]),
                on="target",
                how="left",
            )
            .join(zero_sum.select(["target", "cooldown_end_pods_mean", "drain_time_s_mean"]), on="target", how="left")
            .join(idle_sum, on="target", how="left")
            .rename({
                "total_mean": "steady_joules_total",
                "efficiency_mean": "joules_per_10k_req",
                "efficiency_ok_mean": "joules_per_10k_successful_req",
                "p95_spike_mean": "scale_up_p95_spike_ms",
            })
            .sort(target_rank())
        )

        return master_table


    return (
        TARGET_ORDER,
        WINDOWS,
        WINDOW_LABELS,
        build_efficiency_frame,
        build_windowed_summary,
        metric_title,
        pareto_frontier,
        process_all_metrics_summary,
        process_cooldown_idle_drain,
        process_pod_joules,
        process_pod_joules_windows,
        process_request_outcomes,
        process_scale_to_zero,
        process_scale_up_responsiveness,
        target_rank,
        window_slice,
    )


@app.cell
def chart_helpers(
    TARGET_ORDER,
    WINDOWS,
    WINDOW_LABELS,
    build_efficiency_frame,
    metric_title,
    pareto_frontier,
    process_pod_joules,
    target_rank,
    window_slice,
):
    # Altair visualization helpers — all target encodings share color_scale()
    # -------------------------------------------------------------------------
    CHART_WIDTH = 650
    CHART_HEIGHT = 160


    def target_color(legend: bool = True) -> alt.Color:
        """Consistent target color/legend encoding across every chart."""
        return alt.Color(
            "target:N",
            scale=color_scale(),
            sort=TARGET_ORDER,
            title="Target",
            legend=alt.Legend(orient="right") if legend else None,
        )


    def window_bands(
        windows: list[str] | None = None, opacity: float = 0.07
    ) -> alt.Chart:
        """Shaded background rectangles marking the k6 load stages."""
        windows = windows or ["scale_up", "ramp_up", "steady", "cooldown"]
        df_bands = pl.DataFrame({
            "window": windows,
            "start": [WINDOWS[w][0] for w in windows],
            "end": [WINDOWS[w][1] for w in windows],
            "label": [WINDOW_LABELS[w] for w in windows],
        })
        return (
            alt.Chart(df_bands)
            .mark_rect(opacity=opacity)
            .encode(
                x=alt.X("start:Q"),
                x2="end:Q",
                color=alt.Color(
                    "window:N",
                    scale=alt.Scale(scheme="greys"),
                    legend=None,
                ),
                tooltip=[alt.Tooltip("label:N", title="Window")],
            )
        )


    # Metrics whose targets span orders of magnitude: plotted on a log y-axis by
    # default so the slowest target does not flatten every other curve.
    LOG_SCALE_METRICS = {"p95", "p99"}

    # Smallest value a log axis can show; band lower bounds are clipped to it
    # because log(0) is undefined and would drop the whole band.
    LOG_FLOOR = 1e-2

    # Metrics are sampled every 100 ms (7 800 points per target per metric).
    # Binning to whole seconds keeps the curves identical to the eye while cutting
    # exported SVGs from megabytes to kilobytes — Typst has to embed them.
    TS_BIN_SECONDS = 1.0


    def _timeseries_aggregates_for_metrics(
        metrics: list, df_scaling: dict, band: str = "std", bin_seconds: float = TS_BIN_SECONDS
    ) -> pl.DataFrame:
        """
        Long-form frame with the across-iteration mean per time bin plus a variance
        band: "std" (mean ± 1 sd) or "minmax" (full envelope across the 10 runs).
        bin_seconds=0 disables resampling and keeps the raw sample rate.
        """
        parts = []
        for m in metrics:
            df = df_scaling[m]
            if bin_seconds:
                df = df.with_columns(
                    ((pl.col("normalized_time") / bin_seconds).floor() * bin_seconds).alias(
                        "normalized_time"
                    )
                )
            agg = (
                df
                .group_by(["target", "normalized_time"])
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
                lower = pl.col("mean") - pl.col("std")
                upper = pl.col("mean") + pl.col("std")
            agg = agg.with_columns([
                lower.clip(lower_bound=0).alias("band_lower"),
                upper.alias("band_upper"),
                pl.lit(m).alias("metric"),
            ])
            parts.append(agg)
        return pl.concat(parts, how="vertical").sort(["metric", "target", "normalized_time"])


    def make_timeseries_chart(
        df_scaling: dict,
        metric: str,
        band: str = "std",
        show_windows: bool = True,
        bin_seconds: float = TS_BIN_SECONDS,
        y_scale: str = "auto",
    ) -> alt.Chart:
        """
        Single metric over time: mean line per target + variance band.
        y_scale: "auto" (log for LOG_SCALE_METRICS, linear otherwise), "log" or "linear".
        """
        df_metric = _timeseries_aggregates_for_metrics(
            [metric], df_scaling, band=band, bin_seconds=bin_seconds
        ).drop("metric")

        log_y = y_scale == "log" or (y_scale == "auto" and metric in LOG_SCALE_METRICS)
        if log_y:
            # A log axis cannot render zero or negative values: lift the band floor
            # and drop samples whose mean is zero (idle gaps between load stages).
            df_metric = df_metric.filter(pl.col("mean") > 0).with_columns([
                pl.col("band_lower").clip(lower_bound=LOG_FLOOR),
                pl.col("band_upper").clip(lower_bound=LOG_FLOOR),
            ])

        y_def = alt.Scale(type="log", nice=False) if log_y else alt.Scale(zero=False)
        y_title = metric_title(metric) + (" - log scale" if log_y else "")

        base = alt.Chart(df_metric)
        x = alt.X("normalized_time:Q", title="Normalized Time (s)", scale=alt.Scale(domain=[0, 780], nice=False))

        area = base.mark_area(opacity=0.18).encode(
            x=x,
            y=alt.Y("band_lower:Q", title=y_title, scale=y_def),
            y2="band_upper:Q",
            color=target_color(legend=False),
        )

        line = base.mark_line(strokeWidth=1.6).encode(
            x=x,
            y=alt.Y("mean:Q", title=y_title, scale=y_def),
            color=target_color(),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("normalized_time:Q", title="t (s)", format=".0f"),
                alt.Tooltip("mean:Q", title="Mean", format=".2f"),
                alt.Tooltip("band_lower:Q", title="Lower", format=".2f"),
                alt.Tooltip("band_upper:Q", title="Upper", format=".2f"),
            ],
        )

        layers = [window_bands(), area, line] if show_windows else [area, line]
        band_note = "min-max across runs" if band == "minmax" else "mean ± 1 sd across runs"

        return (
            alt.layer(*layers)
            .properties(
                title={"text": metric_title(metric), "subtitle": f"{band_note}, 10 iterations"},
                width=CHART_WIDTH,
                height=CHART_HEIGHT,
            )
            .resolve_scale(color="independent")
        )


    def make_timeseries_charts(
        df_scaling: dict,
        metrics: list = ["rps", "pods", "cpu_usage", "p95"],
        band: str = "std",
        show_windows: bool = True,
        y_scale: str = "auto",
    ) -> dict:
        """Standalone time-series chart per metric: metric name -> alt.Chart."""
        return {
            m: make_timeseries_chart(
                df_scaling, m, band=band, show_windows=show_windows, y_scale=y_scale
            )
            for m in metrics
        }


    def make_timeseries_facet(
        df_scaling: dict,
        metrics: list = ["rps", "pods", "cpu_usage", "p95"],
        band: str = "std",
        y_scale: str = "auto",
    ) -> alt.VConcatChart:
        """Stacked panel of the time-series charts — one figure for the thesis."""
        charts = make_timeseries_charts(df_scaling, metrics, band=band, y_scale=y_scale)
        return (
            alt.vconcat(*[charts[m] for m in metrics], spacing=8)
            # y stays independent: panels mix linear and log axes.
            .resolve_scale(color="shared", x="shared", y="independent")
            .properties(title="Auto-scaling behaviour over the 13-minute load scenario")
        )


    def make_rapl_energy_chart(
        df_scaling: dict, window: str = "steady", stacked: bool = True
    ) -> alt.Chart:
        """
        RAPL energy breakdown per target for one window.
        stacked=True: package + dram stacked (bar height == total energy).
        stacked=False: grouped bars for package, dram and total side by side.
        """
        _, summary = process_pod_joules(
            df_scaling["pod_joules"], df_scaling["requests"], window=window
        )

        domains = ["package", "dram"] if stacked else ["package", "dram", "total"]
        df_plot = (
            summary.select([
                pl.col("target"),
                pl.col("package_mean").alias("package"),
                pl.col("dram_mean").alias("dram"),
                pl.col("total_mean").alias("total"),
            ])
            .unpivot(index="target", variable_name="domain", value_name="joules")
            .filter(pl.col("domain").is_in(domains))
        )

        title = {
            "text": f"RAPL Energy Breakdown — {WINDOW_LABELS[window]}",
            "subtitle": "Mean joules per run across 10 iterations",
        }

        if stacked:
            return (
                alt.Chart(df_plot)
                .mark_bar()
                .encode(
                    x=alt.X("joules:Q", title="Mean Energy (J)", stack="zero"),
                    y=alt.Y("target:N", title="Target", sort=TARGET_ORDER),
                    color=alt.Color(
                        "domain:N",
                        title="RAPL Zone",
                        scale=alt.Scale(domain=["package", "dram"], range=["#00758F", "#FC7C00"]),
                    ),
                    tooltip=[
                        alt.Tooltip("target:N", title="Target"),
                        alt.Tooltip("domain:N", title="Zone"),
                        alt.Tooltip("joules:Q", title="Joules", format=",.1f"),
                    ],
                )
                .properties(width=320, height=190, title=title)
            )

        return (
            alt.Chart(df_plot)
            .mark_bar()
            .encode(
                x=alt.X("target:N", title=None, sort=TARGET_ORDER, axis=alt.Axis(labels=False, ticks=False)),
                y=alt.Y("joules:Q", title="Mean Energy (J)"),
                color=target_color(),
                column=alt.Column("domain:N", title=None, sort=domains, header=alt.Header(labelOrient="bottom")),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("domain:N", title="Zone"),
                    alt.Tooltip("joules:Q", title="Joules", format=",.1f"),
                ],
            )
            .properties(width=140, height=200, title=title)
        )


    def make_energy_efficiency_chart(
        df_scaling: dict, window: str = "steady", successful_only: bool = False
    ) -> alt.Chart:
        """
        Joules per 10k requests per target, with ±1 sd error bars.
        successful_only=True normalizes by requests that actually returned HTTP 200.
        """
        _, summary = process_pod_joules(
            df_scaling["pod_joules"], df_scaling["requests"], window=window
        )
        mean_col, std_col = ("efficiency_ok_mean", "efficiency_ok_std") if successful_only else ("efficiency_mean", "efficiency_std")
        df_plot = summary.rename({mean_col: "efficiency_mean", std_col: "efficiency_std"}) if successful_only else summary
        df_plot = df_plot.with_columns([
            (pl.col("efficiency_mean") - pl.col("efficiency_std")).clip(lower_bound=0).alias("lo"),
            (pl.col("efficiency_mean") + pl.col("efficiency_std")).alias("hi"),
        ])

        base = alt.Chart(df_plot).encode(
            y=alt.Y("target:N", title="Target", sort=TARGET_ORDER)
        )
        bars = base.mark_bar().encode(
            x=alt.X("efficiency_mean:Q", title="Joules per 10 000 requests"),
            color=target_color(legend=False),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("efficiency_mean:Q", title="J / 10k req", format=".2f"),
                alt.Tooltip("efficiency_std:Q", title="sd", format=".2f"),
            ],
        )
        errors = base.mark_errorbar().encode(x=alt.X("lo:Q", title=""), x2="hi:Q")

        return (bars + errors).properties(
            width=320,
            height=190,
            title={
                "text": f"Energy Efficiency — {WINDOW_LABELS[window]}",
                "subtitle": (
                    "Total RAPL joules per 10k successful requests"
                    if successful_only
                    else "Total RAPL joules normalized by served requests"
                ),
            },
        )


    def make_peak_boxplots(
        df_scaling: dict,
        metrics: list = ["p95", "memory", "pods"],
        windows: list[str] = ["steady", "cooldown"],
    ) -> alt.Chart:
        """
        Per-iteration distributions across targets: one boxplot column per metric,
        one row per window (peak vs. scale-to-zero).
        """
        samples = []
        for m in metrics:
            per_iter = (
                window_slice(df_scaling[m])
                .filter(pl.col("window").is_in(windows))
                .group_by(["target", "window", "iteration"])
                .agg(pl.col("value").mean().alias("sample"))
                .with_columns([
                    pl.lit(m).alias("metric"),
                    pl.lit(metric_title(m)).alias("metric_label"),
                    pl.col("window").replace_strict(WINDOW_LABELS, default=pl.col("window")).alias("window_label"),
                ])
            )
            samples.append(
                per_iter.select(["target", "window", "window_label", "iteration", "sample", "metric", "metric_label"])
            )

        combined = pl.concat(samples, how="vertical").sort(target_rank())

        return (
            alt.Chart(combined)
            .mark_boxplot(size=12)
            .encode(
                x=alt.X("sample:Q", title="Per-iteration mean", scale=alt.Scale(zero=False)),
                y=alt.Y("target:N", title=None, sort=TARGET_ORDER),
                color=target_color(),
                column=alt.Column("metric_label:N", title=None, sort=[metric_title(m) for m in metrics]),
                row=alt.Row("window_label:N", title=None, sort=[WINDOW_LABELS[w] for w in windows]),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("sample:Q", title="Value", format=".2f"),
                    alt.Tooltip("iteration:Q", title="Iteration"),
                ],
            )
            .properties(width=220, height=150, title="Steady-State vs. Cooldown Distributions")
            .resolve_scale(x="independent")
        )


    def make_window_summary_chart(
        df_windowed: pl.DataFrame, metric: str, windows: list[str] | None = None
    ) -> alt.Chart:
        """Grouped bars of the windowed mean (±1 sd) per target across windows."""
        windows = windows or ["scale_up", "ramp_up", "steady", "cooldown"]
        labels = [WINDOW_LABELS[w] for w in windows]
        df_plot = df_windowed.filter(
            (pl.col("metric") == metric) & (pl.col("window").is_in(windows))
        ).with_columns([
            (pl.col("mean") - pl.col("std")).clip(lower_bound=0).alias("lo"),
            (pl.col("mean") + pl.col("std")).alias("hi"),
        ])

        base = alt.Chart(df_plot).encode(
            x=alt.X("target:N", title=None, sort=TARGET_ORDER, axis=alt.Axis(labels=False, ticks=False))
        )
        bars = base.mark_bar().encode(
            y=alt.Y("mean:Q", title=metric_title(metric)),
            color=target_color(),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("window_label:N", title="Window"),
                alt.Tooltip("mean:Q", title="Mean", format=".2f"),
                alt.Tooltip("std:Q", title="sd", format=".2f"),
            ],
        )
        errors = base.mark_errorbar().encode(y=alt.Y("lo:Q", title=""), y2="hi:Q")

        return (
            (bars + errors)
            .properties(width=130, height=180)
            .facet(column=alt.Column("window_label:N", title=None, sort=labels))
            .properties(title=f"{metric_title(metric)} by evaluation window")
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
        x_err: str | None = None,
        y_err: str | None = None,
        width: int = 420,
        height: int = 320,
    ) -> alt.Chart:
        """Shared scaffold: one labeled point per target, median quadrant guides,
        optional ±1 sd whiskers and a Pareto frontier line."""
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

        # Median guides split the plane into the four trade-off quadrants.
        guides = [
            alt.Chart(pl.DataFrame({"v": [df_plot[x].median()]}))
            .mark_rule(strokeDash=[4, 4], opacity=0.35)
            .encode(x=alt.X("v:Q", title=x_title)),
            alt.Chart(pl.DataFrame({"v": [df_plot[y].median()]}))
            .mark_rule(strokeDash=[4, 4], opacity=0.35)
            .encode(y=alt.Y("v:Q", title=y_title)),
        ]

        layers = list(guides)

        if frontier is not None and len(frontier) > 1:
            layers.append(
                alt.Chart(frontier)
                .mark_line(strokeDash=[6, 3], color="#888", strokeWidth=1.2)
                .encode(x=x_enc, y=y_enc)
            )

        if x_err:
            layers.append(
                base.mark_rule(opacity=0.5).encode(
                    y=y_enc,
                    x=alt.X(f"{x}_lo:Q", title=x_title),
                    x2=f"{x}_hi:Q",
                    color=target_color(legend=False),
                )
            )
        if y_err:
            layers.append(
                base.mark_rule(opacity=0.5).encode(
                    x=x_enc,
                    y=alt.Y(f"{y}_lo:Q", title=y_title),
                    y2=f"{y}_hi:Q",
                    color=target_color(legend=False),
                )
            )

        points = base.mark_point(size=160, filled=True, opacity=0.95).encode(
            x=x_enc,
            y=y_enc,
            color=target_color(),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip(f"{x}:Q", title=x_title, format=".2f"),
                alt.Tooltip(f"{y}:Q", title=y_title, format=".2f"),
            ],
        )
        labels = base.mark_text(align="left", dx=9, dy=-7, fontSize=11).encode(
            x=x_enc, y=y_enc, text="target:N", color=target_color(legend=False)
        )

        return (
            alt.layer(*layers, points, labels)
            .properties(width=width, height=height, title={"text": title, "subtitle": subtitle})
            .resolve_scale(color="shared")
        )


    def make_efficiency_scatter(
        df_scaling: dict, window: str = "steady", y_log: bool = False
    ) -> alt.Chart:
        """
        Energy cost vs. delivered throughput. Bottom-right is the winning quadrant:
        high throughput for few joules. Whiskers are ±1 sd across the 10 iterations.
        """
        _, summary = build_efficiency_frame(df_scaling, window)
        df_plot = summary.with_columns([
            (pl.col("rps") - pl.col("rps_std")).alias("rps_lo"),
            (pl.col("rps") + pl.col("rps_std")).alias("rps_hi"),
            (pl.col("joules_per_10k_requests") - pl.col("joules_per_10k_requests_std")).clip(lower_bound=0).alias("joules_per_10k_requests_lo"),
            (pl.col("joules_per_10k_requests") + pl.col("joules_per_10k_requests_std")).alias("joules_per_10k_requests_hi"),
        ])
        frontier = pareto_frontier(
            df_plot, "rps", "joules_per_10k_requests", maximize_x=True, minimize_y=True
        )
        return _labeled_scatter(
            df_plot,
            x="rps",
            y="joules_per_10k_requests",
            x_title="Throughput (req/s)",
            y_title="Energy cost (J per 10k requests)",
            title=f"Energy cost vs. throughput - {WINDOW_LABELS[window]}",
            subtitle="Bottom-right is better: more work per joule. Dashed line = Pareto frontier, whiskers = +/-1 sd",
            y_log=y_log,
            frontier=frontier,
            x_err="rps",
            y_err="joules_per_10k_requests",
        )


    def make_work_per_joule_scatter(df_scaling: dict, window: str = "steady") -> alt.Chart:
        """
        The inverted view: requests delivered per joule against throughput. Top-right
        is best - fast AND cheap. Reads more naturally as 'work per unit of energy'.
        """
        _, summary = build_efficiency_frame(df_scaling, window)
        df_plot = summary.with_columns([
            (pl.col("rps") - pl.col("rps_std")).alias("rps_lo"),
            (pl.col("rps") + pl.col("rps_std")).alias("rps_hi"),
            (pl.col("requests_per_joule") - pl.col("requests_per_joule_std")).clip(lower_bound=0).alias("requests_per_joule_lo"),
            (pl.col("requests_per_joule") + pl.col("requests_per_joule_std")).alias("requests_per_joule_hi"),
        ])
        frontier = pareto_frontier(
            df_plot, "rps", "requests_per_joule", maximize_x=True, minimize_y=False
        )
        return _labeled_scatter(
            df_plot,
            x="rps",
            y="requests_per_joule",
            x_title="Throughput (req/s)",
            y_title="Requests served per joule",
            title=f"Work per joule vs. throughput - {WINDOW_LABELS[window]}",
            subtitle="Top-right is better: fast and cheap. Dashed line = Pareto frontier, whiskers = +/-1 sd",
            frontier=frontier,
            x_err="rps",
            y_err="requests_per_joule",
        )


    def make_energy_latency_scatter(df_scaling: dict, window: str = "steady") -> alt.Chart:
        """
        The other trade-off a reader asks about: does paying more energy buy lower
        latency? Bottom-left is best - cheap AND responsive. Latency uses a log axis
        because wasm-js is an order of magnitude off the rest.
        """
        _, summary = build_efficiency_frame(df_scaling, window)
        frontier = pareto_frontier(
            summary, "joules_per_10k_requests", "p95_ms", maximize_x=False, minimize_y=True
        )
        return _labeled_scatter(
            summary,
            x="joules_per_10k_requests",
            y="p95_ms",
            x_title="Energy cost (J per 10k requests)",
            y_title="P95 latency (ms) - log scale",
            title=f"Energy cost vs. responsiveness - {WINDOW_LABELS[window]}",
            subtitle="Bottom-left is better: cheap and responsive. Dashed line = Pareto frontier",
            y_log=True,
            frontier=frontier,
        )


    def make_efficiency_iteration_scatter(df_scaling: dict, window: str = "steady") -> alt.Chart:
        """
        Every iteration as its own point (10 per target) so the clusters show how
        reproducible each target's energy/throughput position is.
        """
        frame, summary = build_efficiency_frame(df_scaling, window)

        runs = (
            alt.Chart(frame)
            .mark_point(size=45, opacity=0.5, filled=True)
            .encode(
                x=alt.X("rps:Q", title="Throughput (req/s)", scale=alt.Scale(zero=False, padding=25)),
                y=alt.Y("joules_per_10k_requests:Q", title="Energy cost (J per 10k requests)", scale=alt.Scale(zero=False, padding=25)),
                color=target_color(),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("iteration:Q", title="Iteration"),
                    alt.Tooltip("rps:Q", title="req/s", format=".2f"),
                    alt.Tooltip("joules_per_10k_requests:Q", title="J / 10k req", format=".2f"),
                ],
            )
        )
        means = (
            alt.Chart(summary)
            .mark_point(size=200, filled=False, strokeWidth=2.5, shape="cross")
            .encode(x="rps:Q", y="joules_per_10k_requests:Q", color=target_color(legend=False))
        )
        return (runs + means).properties(
            width=420,
            height=320,
            title={
                "text": f"Per-iteration energy/throughput positions - {WINDOW_LABELS[window]}",
                "subtitle": "One point per run, cross = target mean. Tight clusters mean reproducible results",
            },
        )


    return (
        make_efficiency_iteration_scatter,
        make_efficiency_scatter,
        make_energy_efficiency_chart,
        make_energy_latency_scatter,
        make_peak_boxplots,
        make_rapl_energy_chart,
        make_timeseries_charts,
        make_timeseries_facet,
        make_window_summary_chart,
        make_work_per_joule_scatter,
    )


@app.cell
def export_helpers():
    # Export helpers — figures and tables for the Typst thesis
    # -------------------------------------------------------------------------
    # Charts go to FIGURE_DIR as SVG (vector, what Typst `image()` wants) and PNG;
    # tables go to TABLE_DIR as CSV, readable from Typst via `#csv("...")`.
    FIGURE_DIR = Path("figures")
    TABLE_DIR = Path("tables")


    def thesis_chart(chart, title: str | None = None):
        """Apply print-friendly styling: white background, readable type, no grid clutter."""
        styled = chart.configure_view(strokeWidth=0).configure_axis(
            labelFontSize=11, titleFontSize=12, grid=True, gridOpacity=0.25
        ).configure_legend(
            labelFontSize=11, titleFontSize=12
        ).configure_title(
            fontSize=14, subtitleFontSize=11, anchor="start"
        ).configure_header(
            labelFontSize=11, titleFontSize=12
        ).properties(background="white")
        return styled.properties(title=title) if title else styled


    def save_chart(
        chart,
        name: str,
        formats: tuple = ("svg", "png"),
        directory: Path = FIGURE_DIR,
        scale_factor: float = 2.0,
        style: bool = True,
    ) -> list:
        """
        Render one Altair chart to disk for inclusion in the thesis.
        Returns the list of written paths. Requires vl-convert (already installed).
        """
        directory.mkdir(parents=True, exist_ok=True)
        to_save = thesis_chart(chart) if style else chart
        written = []
        for fmt in formats:
            path = directory / f"{name}.{fmt}"
            if fmt == "png":
                to_save.save(path, scale_factor=scale_factor, engine="vl-convert")
            else:
                to_save.save(path, engine="vl-convert")
            written.append(path)
        return written


    def export_table(
        df: pl.DataFrame,
        name: str,
        directory: Path = TABLE_DIR,
        float_precision: int = 3,
        formats: tuple = ("csv", "parquet"),
    ) -> list:
        """
        Write a summary table for the thesis. CSV is what Typst reads with
        `#csv("tables/<name>.csv")`; parquet keeps full precision for re-analysis.
        """
        directory.mkdir(parents=True, exist_ok=True)
        rounded = df.with_columns(pl.col(pl.Float32, pl.Float64).round(float_precision))
        written = []
        for fmt in formats:
            path = directory / f"{name}.{fmt}"
            if fmt == "csv":
                rounded.write_csv(path)
            else:
                df.write_parquet(path)
            written.append(path)
        return written


    def export_all(
        charts: dict | None = None,
        tables: dict | None = None,
        formats: tuple = ("svg", "png"),
    ) -> pl.DataFrame:
        """
        Bulk-export named charts and tables; returns a manifest of what was written
        so the notebook shows exactly which files the thesis build will pick up.
        """
        rows = []
        for name, chart in (charts or {}).items():
            for path in save_chart(chart, name, formats=formats):
                rows.append({"kind": "figure", "name": name, "path": str(path)})
        for name, df in (tables or {}).items():
            for path in export_table(df, name):
                rows.append({"kind": "table", "name": name, "path": str(path)})
        return pl.DataFrame(rows, schema={"kind": pl.String, "name": pl.String, "path": pl.String})


    return (export_all,)


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
def md_windowed():
    mo.md(r"""
    ## Windowed statistics

    `mean`, `std`, `min`, `max` and `p95` per (metric, target, window), computed by
    reducing each of the 10 iterations to one sample inside a window and then
    summarizing across iterations — so `std` is **run-to-run** variance, not
    sample noise inside a run.

    **Throughput is stable once scaled.** Steady-state RPS has a run-to-run sd of
    0.65-2.35 req/s on every target (1-3 % of the mean), so the throughput ranking
    is not an artefact of a lucky iteration. The Scale-Up window is where the runs
    disagree most (`oci-node` sd 3.56, `oci-native` 3.11) — that is HPA reaction
    timing, not steady capacity.

    **Cooldown throughput stays high.** With KEDA scaling on concurrency, traffic is
    still balanced across the replicas as VUs ramp down: 40.3 req/s for `oci-spring`
    and 24.4 for `oci-axum` are still being served in the 660-780 s window.
    """)
    return


@app.cell
def windowed_summary(
    build_windowed_summary,
    df_scaling,
    process_pod_joules_windows,
    process_request_outcomes,
    process_scale_to_zero,
):
    # Windowed statistics across all evaluation windows (mean/std/min/max/p95)
    df_windowed_summary = build_windowed_summary(
        df_scaling, metrics=["rps", "pods", "cpu_usage", "memory", "p95", "p99", "checks_rate"]
    )
    _, df_energy_windows = process_pod_joules_windows(
        df_scaling["pod_joules"], df_scaling["requests"]
    )
    df_request_outcomes = process_request_outcomes(df_scaling["requests"])
    df_scale_to_zero = process_scale_to_zero(df_scaling["pods"])

    mo.vstack([
        mo.md("## Windowed metric statistics (across 10 iterations)"),
        df_windowed_summary,
    ])
    return (
        df_energy_windows,
        df_request_outcomes,
        df_scale_to_zero,
        df_windowed_summary,
    )


@app.cell
def extra_charts(
    chart_energy,
    df_scaling,
    df_windowed_summary,
    make_energy_efficiency_chart,
    make_timeseries_facet,
    make_window_summary_chart,
):
    # Additional standalone charts
    chart_efficiency = make_energy_efficiency_chart(df_scaling)
    chart_ts_panel = make_timeseries_facet(df_scaling, metrics=["rps", "pods", "cpu_usage", "p95"])
    chart_pods_by_window = make_window_summary_chart(df_windowed_summary, "pods")

    mo.hstack([chart_energy, chart_efficiency], justify="start")
    return chart_efficiency, chart_pods_by_window, chart_ts_panel


@app.cell
def md_scale_up():
    mo.md(r"""
    ## Scale-Up responsiveness and cold starts (0-240 s)

    Per-iteration peak p95, worst check rate, replica high-water mark and the time
    until KEDA first adds a replica.

    **Cold-start latency cost is negligible for the container targets** — the peak
    p95 during the initial ramp is 0.75-2.01 ms, i.e. barely above their steady-state
    p95, so no target pays a visible first-request penalty. `wasm-js` again is the
    outlier at **51.2 ms peak p95**, 25x its nearest neighbour.

    **First scale-up fires after 29-48 s** on all targets: `oci-spring` reacts
    fastest (29.2 s) and `oci-native` slowest (48.1 s). Since KEDA scales on
    concurrency = 20, the targets that saturate a worker soonest trigger earliest —
    this measures request-holding time, not platform startup speed.
    """)
    return


@app.cell
def _(df_scaling, process_scale_up_responsiveness):
    df_responsiveness = process_scale_up_responsiveness(
        df_scaling["p95"], df_scaling["checks_rate"], df_scaling["pods"]
    )
    df_responsiveness
    return


@app.cell
def md_master():
    mo.md(r"""
    ## Master summary — headline results

    One row per target, all values from the Steady-State window (480-600 s) unless
    the column name says otherwise.

    **Throughput splits the field into two groups.** The four container targets
    deliver 43.6-75.3 req/s (`oci-spring` highest at 75.3, `oci-native` 59.8), while
    the WebAssembly targets deliver far less: `wasm-rust` 21.0 req/s and `wasm-js`
    6.9 req/s — a **10.9x gap** between best and worst.

    **Latency follows throughput.** Steady p95 runs 1.06-1.97 ms for the container
    targets and 3.50 ms for `wasm-rust`; `wasm-js` sits at **19.2 ms p95 / 32.6 ms
    p99**, an order of magnitude above everything else.

    **`wasm-js` is the only target that drops requests** — a 1.14 % steady failure
    rate (HTTP timeouts, `expected_response == false`). Every other target holds a
    perfect check rate across all 10 iterations.

    **Energy per request is where the ranking inverts.** `oci-axum` is not the
    throughput leader but is the most efficient at **10.3 J/10k requests**, followed
    by `oci-node` (14.5) and `oci-spring` (16.4). `oci-native` costs 23.8, and the
    Wasm targets are the most expensive per unit of work: `wasm-js` 35.5 and
    `wasm-rust` **43.5 J/10k requests**, 4.2x the `oci-axum` figure.

    **Footprint spans two orders of magnitude.** Per replica: `oci-axum` 16.5 MB,
    `wasm-rust` 41.9 MB, `oci-node` 103.7 MB, `wasm-js` 189.0 MB, `oci-native`
    206.9 MB, `oci-spring` 690.7 MB. As a cluster total the spread widens to
    82.7 MB (`oci-axum`) vs 3 079 MB (`oci-spring`) — a **37x** difference in RAM
    needed to serve the same scenario.
    """)
    return


@app.cell
def _(df_master_summary):
    df_master_summary
    return


@app.cell
def md_ts_rps():
    mo.md(r"""
    ### Throughput over time

    Mean across the 10 iterations with a ±1 sd band; shaded regions mark the load
    stages. The step at each VU increase is visible for the container targets and
    flat for `wasm-js`, which is already saturated during the first ramp — it gains
    only 11.1 → 13.7 → 7.0 req/s across Scale-Up, Ramp-Up and Steady State, i.e. it
    *loses* throughput when the load doubles.
    """)
    return


@app.cell
def _(ts_charts):
    ts_charts["rps"]
    return


@app.cell
def md_ts_pods():
    mo.md(r"""
    ### Replica count over time

    KEDA targets 20 concurrent requests per pod, so the replica count is a direct
    readout of how long each request occupies a worker. The slow targets need
    **more** pods to serve **less** traffic: `wasm-js` holds 6.75 and `wasm-rust`
    6.22 replicas in steady state against 5.0 for `oci-axum` / `oci-node` and 4.43
    for `oci-spring`. Replica counts stay flat through Cooldown by design — KEDA's
    scale-down fires roughly 30 s after the run ends, outside the capture window.
    """)
    return


@app.cell
def _(ts_charts):
    ts_charts["pods"]
    return


@app.cell
def md_ts_cpu():
    mo.md(r"""
    ### CPU usage over time

    Per-replica cores. `oci-native` (10.98) and `oci-spring` (9.65) are the heaviest
    under peak load, `oci-axum` the lightest at 2.75. Note the ordering differs from
    the energy ranking: `oci-spring` burns comparable CPU to `oci-native` but
    converts it into 26 % more throughput, which is why its joules-per-request is
    lower.
    """)
    return


@app.cell
def _(ts_charts):
    ts_charts["cpu_usage"]
    return


@app.cell
def md_ts_p95():
    mo.md(r"""
    ### P95 latency over time (log scale)

    Latency spans two orders of magnitude across targets, so this panel uses a
    **logarithmic y-axis** - on a linear axis `wasm-js` flattens the other five
    curves into a single line along the bottom.

    `wasm-js` sits an order of magnitude above everything else and its band is both
    high and wide (steady sd 5.81 ms across runs, iteration means from 12.9 to
    30.1 ms), so its latency is not just worse but *unpredictable* between runs.
    The remaining targets separate cleanly on the log axis: `oci-spring` (1.06 ms)
    and `oci-native` (1.33 ms) at the bottom, `oci-node` (1.69 ms) and `oci-axum`
    (1.97 ms) in the middle, and `wasm-rust` distinctly above them at 3.50 ms.
    """)

    return


@app.cell
def _(ts_charts):
    ts_charts["p95"]
    return


@app.cell
def md_energy():
    mo.md(r"""
    ## RAPL energy: domains and efficiency

    Package and DRAM joules accumulated during Steady State, summed over the pods
    alive at each sample. DRAM is a small and fairly constant share (5-7 % of total
    on every target), so the package domain drives all of the differences.

    **Absolute energy** ranks `oci-native` highest (10 083 J), then `oci-spring`
    (8 563 J), `wasm-rust` (7 788 J), `oci-node` (4 767 J), `wasm-js` (4 527 J) and
    `oci-axum` lowest (3 431 J).

    **Per-request efficiency reorders that list**, because absolute energy rewards
    targets that simply did less work. Normalized: `oci-axum` 10.3, `oci-node` 14.5,
    `oci-spring` 16.4, `oci-native` 23.8, `wasm-js` 35.5, `wasm-rust` 43.5 J per
    10k requests. `wasm-js` looks mid-pack in absolute joules only because it served
    1.28 M requests against `oci-spring`'s 5.23 M.

    Normalizing by *successful* requests instead barely moves the picture
    (`wasm-js` 35.5 → 35.9 J/10k): its 1.14 % failure rate is too small to explain
    the efficiency gap — the cost is in how it serves the requests that do succeed.
    """)
    return


@app.cell
def _(chart_energy):
    chart_energy
    return


@app.cell
def md_boxplots():
    mo.md(r"""
    ## Steady State vs. Cooldown distributions

    Each box is 10 points — one per iteration — so the spread is run-to-run
    reproducibility.

    **Latency:** every container target is tight in both windows (p95 sd ≤ 0.18 ms).
    `wasm-js` is the exception in both: 19.2 ms ± 5.8 in steady state, and still
    17.4 ms ± 15.1 during Cooldown with one run reaching 53.5 ms — it does not
    recover when the load drops, which points at a queue that never drains rather
    than at instantaneous capacity.

    **Memory:** `oci-native` is the least reproducible (steady sd 57.3 MB, runs from
    167.9 to 360.8 MB), consistent with GC heap growth varying by run. `oci-axum`
    (sd 0.70 MB) and `wasm-rust` (sd 2.01 MB) are essentially deterministic.

    **Pods:** `oci-axum`, `oci-node` and `wasm-js` hold a fixed replica count in
    every run; `oci-spring` (4.43 ± 0.48) and `oci-native` (4.89 ± 0.21) oscillate
    around the KEDA threshold, so they occasionally serve peak load with one replica
    fewer.
    """)
    return


@app.cell
def _(chart_boxplots):
    chart_boxplots
    return


@app.cell
def md_efficiency_scatter():
    mo.md(r"""
    ## Which target is actually the most efficient?

    Bar charts rank one axis at a time; these scatter plots put energy against the
    work it bought, one point per target (crosses/whiskers show run-to-run spread).
    The dashed line is the **Pareto frontier** - the targets no other target beats on
    both axes at once.

    **Only two targets are non-dominated: `oci-axum` and `oci-spring`.** `oci-axum`
    is the cheapest per unit of work (10.3 J/10k requests, 972 requests per joule) and
    `oci-spring` is the fastest (75.3 req/s) at a moderate 16.4 J/10k. Everything else
    is beaten outright by one of them:

    - `oci-node` (43.6 req/s, 14.5 J/10k) is dominated by `oci-axum`, which is both
      slightly faster **and** 29 % cheaper per request.
    - `oci-native` pays 23.8 J/10k for 59.8 req/s - `oci-spring` delivers 26 % more
      throughput for 31 % less energy per request.
    - Both Wasm targets sit in the worst quadrant: `wasm-rust` is the most expensive
      per request in the field (43.5 J/10k, 230 requests per joule - **4.2x**
      `oci-axum`'s cost) and `wasm-js` combines the second-worst cost (35.5 J/10k)
      with the lowest throughput (7.0 req/s).

    **Energy does not buy latency.** In the cost-vs-responsiveness plot the frontier
    runs `oci-axum` -> `oci-node` -> `oci-spring`: paying more per request does move
    p95 down slightly (1.97 -> 1.69 -> 1.06 ms), but `oci-native` and both Wasm
    targets pay *more* energy for *worse* latency, so their extra consumption is
    overhead rather than performance.

    **The per-iteration cloud shows the ranking is not noise.** Each target's 10 runs
    form a tight cluster well separated from its neighbours; the only visible spread
    is `wasm-js` along the throughput axis, and it never comes close to the
    container targets.

    A caveat worth stating in the thesis: efficiency here is measured at each
    target's *own* steady-state throughput, not at a matched request rate. A target
    serving less traffic still pays its fixed idle draw, so part of the Wasm penalty
    is a fixed cost spread over fewer requests rather than a higher marginal cost per
    request.
    """)
    return


@app.cell
def efficiency_scatters(
    build_efficiency_frame,
    df_scaling,
    make_efficiency_iteration_scatter,
    make_efficiency_scatter,
    make_energy_latency_scatter,
    make_work_per_joule_scatter,
):
    # Energy-efficiency trade-off scatter plots
    df_efficiency_runs, df_efficiency = build_efficiency_frame(df_scaling)

    chart_efficiency_scatter = make_efficiency_scatter(df_scaling)
    chart_work_per_joule = make_work_per_joule_scatter(df_scaling)
    chart_energy_latency = make_energy_latency_scatter(df_scaling)
    chart_efficiency_runs = make_efficiency_iteration_scatter(df_scaling)

    mo.vstack([
        mo.hstack([chart_efficiency_scatter, chart_work_per_joule], justify="start"),
        mo.hstack([chart_energy_latency, chart_efficiency_runs], justify="start"),
    ])

    return (
        chart_efficiency_runs,
        chart_efficiency_scatter,
        chart_energy_latency,
        chart_work_per_joule,
        df_efficiency,
        df_efficiency_runs,
    )


@app.cell
def md_cooldown():
    mo.md(r"""
    ## Cooldown: retained footprint, not scale-to-zero

    KEDA scales on concurrency = 20 and its scale-down only fires about 30 s **after**
    the run ends, so replicas intentionally stay up for the whole 660-780 s window
    while traffic is still balanced across them. `df_scale_to_zero` therefore reports
    `drain_time_s == 0` and identical start/end replica counts for every target: what
    it measures is the **resource footprint kept alive at end of run**, not scale-down
    latency. Measuring the latter needs a capture that extends past the scale-down
    delay.

    Idle footprint per replica ranges from **17.0 MB** (`oci-axum`) and 29.8 MB
    (`wasm-rust`) to 218.6 MB (`wasm-js`) and **691.0 MB** (`oci-spring`) — a 41x
    spread that translates directly into how many idle replicas fit on a node.
    Idle CPU orders differently: `wasm-js` is lowest at 1.23 cores (it has little
    left to do), `oci-native` highest at 7.58.
    """)
    return


@app.cell
def _(df_cooldown_idle):
    df_cooldown_idle
    return


@app.cell
def request_outcomes_view(df_request_outcomes, df_scale_to_zero):
    # Request outcomes and end-of-run replica footprint
    mo.vstack([
        mo.md("### Request outcomes per window"),
        df_request_outcomes,
        mo.md("### Replica footprint retained at end of run"),
        df_scale_to_zero,
    ])
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
def export_figures(
    chart_boxplots,
    chart_efficiency,
    chart_efficiency_runs,
    chart_efficiency_scatter,
    chart_energy,
    chart_energy_latency,
    chart_pods_by_window,
    chart_ts_panel,
    chart_work_per_joule,
    df_cooldown_idle,
    df_efficiency,
    df_efficiency_runs,
    df_energy_windows,
    df_master_summary,
    df_request_outcomes,
    df_scale_to_zero,
    df_windowed_summary,
    export_all,
    ts_charts,
):
    # Write every thesis figure/table to disk (figures/*.svg|png, tables/*.csv)
    export_manifest = export_all(
        charts={
            "scaling_timeseries_panel": chart_ts_panel,
            "scaling_rps": ts_charts["rps"],
            "scaling_pods": ts_charts["pods"],
            "scaling_cpu_usage": ts_charts["cpu_usage"],
            "scaling_p95": ts_charts["p95"],
            "scaling_energy_domains": chart_energy,
            "scaling_energy_efficiency": chart_efficiency,
            "scaling_boxplots_steady_cooldown": chart_boxplots,
            "scaling_pods_by_window": chart_pods_by_window,
            "scaling_efficiency_scatter": chart_efficiency_scatter,
            "scaling_work_per_joule_scatter": chart_work_per_joule,
            "scaling_energy_latency_scatter": chart_energy_latency,
            "scaling_efficiency_runs_scatter": chart_efficiency_runs,
        },
        tables={
            "scaling_master_summary": df_master_summary,
            "scaling_windowed_summary": df_windowed_summary,
            "scaling_energy_windows": df_energy_windows,
            "scaling_cooldown_idle": df_cooldown_idle,
            "scaling_scale_to_zero": df_scale_to_zero,
            "scaling_request_outcomes": df_request_outcomes,
            "scaling_efficiency": df_efficiency,
            "scaling_efficiency_runs": df_efficiency_runs,
        },
    )
    export_manifest
    return


if __name__ == "__main__":
    app.run()
