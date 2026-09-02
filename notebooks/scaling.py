import marimo

__generated_with = "0.23.16"
app = marimo.App(width="full")

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
        metric_frame,
        split_variants,
        variant_color_scale,
        pareto_frontier,
        pod_joules_increments,
        welch_delta,
        save_chart,
        series_key,
        target_color,
        target_rank,
        thesis_chart,
        total_gauge,
        FIGURE_DIR,
        PER_ROUTE_METRICS,
        TABLE_DIR,
        TARGET_ORDER,
        VARIANT_OF,
    )
    alt.data_transformers.enable("vegafusion")


@app.cell
def md_intro():
    mo.md(r"""
    # Auto-scaling benchmark: six target architectures

    A 13-minute k6 scenario replayed **10 times per target**, scraped from Prometheus
    (Kepler RAPL energy, cAdvisor cpu/memory, kube-state replica counts) and k6
    (latency, throughput, request outcomes). Six targets: `oci-axum`, `oci-native`,
    `oci-node`, `oci-spring`, `wasm-js`, `wasm-rust`. Scaling is driven by **KEDA on
    concurrency = 20**.

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
    #   url                        - k6 route label (request mix breakdown)
    #   name / scenario / stage    - the remaining k6 label dimensions. k6 restarts
    #                                http_reqs_total at every stage boundary, so a
    #                                counter series is only identified once all of
    #                                these are present; differencing on a partial key
    #                                interleaves several counters and overcounts ~100x.
    #   pod_name / pod             - per-pod series: kepler energy counters (pod_name)
    #                                and cAdvisor cpu/memory (pod). These counters are
    #                                PER POD and must be differenced/summed per pod.
    #   status / expected_response - k6 request outcome (200 vs timeout)
    select_scaling_columns = [
        "url",
        "name",
        "scenario",
        "stage",
        "pod_name",
        "pod",
        "status",
        "expected_response",
    ]
    df_scaling_all = load_scenario_metrics("scaling", select_columns=select_scaling_columns)

    # The cross-target comparison covers TARGET_ORDER only. Deployment variants
    # (e.g. wasm-rust-components) travel separately so they cannot leak into the
    # six-way figures, where they would appear with fewer iterations and, for the
    # componentized build, no energy data at all.
    df_scaling, df_variants = split_variants(df_scaling_all)
    df_scaling_all
    return df_scaling, df_variants


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

    # Axis titles / units per metric, used by charts and exported tables.
    METRIC_LABELS = {
        "rps": ("Throughput", "req/s"),
        "pods": ("Pod Count", "pods"),
        # Query is rate(pod_cpu_usage_seconds_total) / count(node_cpu_seconds_total)
        # * 100, i.e. a share of total node capacity -- NOT a core count.
        "cpu_usage": ("CPU Usage", "% of node capacity"),
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
            dfw = window_slice(counter_increments(df, ["target", "iteration"], alias="delta"))
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
            aggregate_metric_windows(metric_frame(df_scaling, m), metric=m, per_iter=per_iter)
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
                pl.len().alias("n_iterations"),
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

        k6 labels this counter with url, name, scenario AND stage, and restarts it at
        every stage boundary, so the series key must include all of them (series_key
        derives it). Each series is differenced on its own; the increments are summed.
          requests        - all requests
          requests_ok     - expected_response == "true" (HTTP 200)
          requests_failed - everything else (timeouts, 5xx)
        """
        increments = counter_increments(
            df_requests, series_key(df_requests), alias="n"
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
            energy.join(
                metric_frame(df_scaling, "rps")
                .filter(mask)
                .group_by(["target", "iteration"])
                .agg(pl.col("value").mean().alias("rps")),
                on=["target", "iteration"],
                how="left",
            )
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


    # -------------------------------------------------------------------------
    # Request mix: what the k6 scenario offers to each target
    # -------------------------------------------------------------------------
    # The k6 script picks a category by weight, then picks uniformly among the
    # routes inside that category, so the designed share of a single route is
    # category_weight / routes_in_category.
    REQUEST_CATEGORIES = {
        "/players/:id": "simple",
        "/teams/:id": "simple",
        "/match/:id": "simple",
        "/players/record/:id": "detailed",
        "/teams/record/:id": "detailed",
        "/match/team/:id": "lookup",
        "/match/result-table": "aggregate",
    }

    # Weights from the k6 scenario (percent of all requests).
    REQUEST_CATEGORY_WEIGHTS = {
        "simple": 10.0,
        "detailed": 30.0,
        "lookup": 30.0,
        "aggregate": 30.0,
    }

    # Ordered light -> dark by query cost. The ramp is built along the warm axis the
    # target palette already uses (#FC7C00 -> #D34516) so these charts sit in the
    # same visual family as the rest of the figures; it stays a single-hue ramp
    # because the categories are ordinal, which keeps it readable next to the
    # categorical target colors without competing with them.
    CATEGORY_ORDER = ["simple", "detailed", "lookup", "aggregate"]
    CATEGORY_PALETTE = ["#FBDCB4", "#F5A855", "#E0701C", "#9C3111"]

    # Label ink per slice: the two light steps need dark text, the two dark ones white.
    CATEGORY_LABEL_COLORS = ["#5A2A0C", "#5A2A0C", "#FFFFFF", "#FFFFFF"]


    def category_scale() -> alt.Scale:
        """Altair color scale for request categories (ordinal, light -> dark by cost)."""
        return alt.Scale(domain=CATEGORY_ORDER, range=CATEGORY_PALETTE)


    def category_label_scale() -> alt.Scale:
        """Text color that stays legible on top of each category's slice."""
        return alt.Scale(domain=CATEGORY_ORDER, range=CATEGORY_LABEL_COLORS)


    def designed_route_share() -> dict:
        """Designed share per route: category weight split evenly over its routes."""
        routes_per_category = {}
        for category in REQUEST_CATEGORIES.values():
            routes_per_category[category] = routes_per_category.get(category, 0) + 1
        return {
            route: REQUEST_CATEGORY_WEIGHTS[category] / routes_per_category[category]
            for route, category in REQUEST_CATEGORIES.items()
        }


    def process_request_mix(
        df_requests: pl.DataFrame, by_target: bool = False
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """
        Observed request mix against the mix the k6 scenario was configured to offer.

        Counts come from the http_reqs counter (differenced per series), NOT from row
        counts: a scrape row is one sample of one label series, so counting rows
        measures how many routes a category owns rather than how much traffic it got.

        Returns (per_category, per_route), each with observed and designed percent.
        """
        route_col = "name" if "name" in df_requests.columns else "url"
        group_cols = (["target"] if by_target else []) + [route_col]

        per_route = (
            counter_increments(df_requests, series_key(df_requests), alias="n")
            .group_by(group_cols)
            .agg(pl.col("n").sum().alias("requests"))
            .rename({route_col: "route"})
            .with_columns(
                pl.col("route").replace_strict(REQUEST_CATEGORIES, default="other").alias("category")
            )
        )

        share_over = ["target"] if by_target else []
        designed = designed_route_share()

        per_route = (
            per_route.with_columns(
                (pl.col("requests") / pl.col("requests").sum().over(share_over) * 100).alias("observed_pct")
                if share_over
                else (pl.col("requests") / pl.col("requests").sum() * 100).alias("observed_pct")
            )
            .with_columns(
                pl.col("route").replace_strict(designed, default=0.0).alias("designed_pct")
            )
            .with_columns((pl.col("observed_pct") - pl.col("designed_pct")).alias("delta_pct"))
            .sort("observed_pct", descending=True)
        )

        per_category = (
            per_route.group_by((["target"] if by_target else []) + ["category"])
            .agg([
                pl.col("requests").sum(),
                pl.col("observed_pct").sum(),
                pl.col("designed_pct").sum(),
            ])
            .with_columns((pl.col("observed_pct") - pl.col("designed_pct")).alias("delta_pct"))
            .with_columns(
                pl.col("category")
                .replace_strict({c: i for i, c in enumerate(CATEGORY_ORDER)}, default=len(CATEGORY_ORDER))
                .alias("_rank")
            )
            .sort("_rank")
            .drop("_rank")
        )

        return per_category, per_route


    # -------------------------------------------------------------------------
    # Per-route latency (p95_by_route)
    # -------------------------------------------------------------------------
    def process_route_latency(
        df_p95_route: pl.DataFrame, window: str = "steady"
    ) -> pl.DataFrame:
        """
        Per-(target, route) p95 inside one window: each iteration is reduced to its
        time-average, then summarized across iterations.
        """
        per_iter = (
            df_p95_route.filter(window_mask(window))
            .group_by(["target", "url", "iteration"])
            .agg(pl.col("value").mean().alias("iter_p95"))
        )
        return (
            per_iter.group_by(["target", "url"])
            .agg([
                pl.col("iter_p95").mean().alias("p95_ms"),
                pl.col("iter_p95").std().alias("p95_sd"),
                pl.col("iter_p95").min().alias("p95_min"),
                pl.col("iter_p95").max().alias("p95_max"),
                pl.len().alias("n_iterations"),
            ])
            .rename({"url": "route"})
            .with_columns(
                pl.col("route").replace_strict(REQUEST_CATEGORIES, default="other").alias("category")
            )
            .sort(["target", "p95_ms"], descending=[False, True])
        )


    def process_route_service_time(
        df_scaling: dict, window: str = "steady"
    ) -> pl.DataFrame:
        """
        Where each target's service time actually goes.

        A route's share of total service time is (its request rate x its p95), which
        weights latency by how often the route is called - a slow route that is 3 %
        of traffic matters far less than a slow route that is 30 % of it.
        """
        latency = process_route_latency(df_scaling["p95_by_route"], window)

        throughput = (
            df_scaling["rps"]
            .filter(window_mask(window))
            .unique(subset=["target", "iteration", "normalized_time", "url"])
            .group_by(["target", "url"])
            .agg(pl.col("value").mean().alias("rps"))
            .rename({"url": "route"})
        )

        return (
            latency.join(throughput, on=["target", "route"], how="left")
            .with_columns((pl.col("p95_ms") * pl.col("rps")).alias("service_time"))
            .with_columns(
                (pl.col("service_time") / pl.col("service_time").sum().over("target") * 100)
                .alias("service_time_pct")
            )
            .with_columns(
                (pl.col("rps") / pl.col("rps").sum().over("target") * 100).alias("traffic_pct")
            )
            .sort(["target", "service_time_pct"], descending=[False, True])
        )


    def compare_variant(
        df_variants: dict, variant: str, window: str = "steady"
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """
        Paired A/B of a deployment variant against its baseline.

        Returns (per_route, summary). `significant` is Welch's two-sided test at
        alpha = 0.05 on the difference of the two run means, and `ci_low`/`ci_high`
        bound that difference. The summary carries sd and n for every cluster-level
        metric so the energy and memory deltas can be judged by the same test.
        """
        baseline = VARIANT_OF[variant]

        latency = process_route_latency(df_variants["p95_by_route"], window)
        base_l = latency.filter(pl.col("target") == baseline).select(
            ["route", "category", "p95_ms", "p95_sd", "n_iterations"]
        ).rename({"p95_ms": "baseline_ms", "p95_sd": "baseline_sd", "n_iterations": "baseline_n"})
        var_l = latency.filter(pl.col("target") == variant).select(
            ["route", "p95_ms", "p95_sd", "n_iterations"]
        ).rename({"p95_ms": "variant_ms", "p95_sd": "variant_sd", "n_iterations": "variant_n"})

        joined_routes = base_l.join(var_l, on="route", how="inner")
        stats = pl.DataFrame(
            [
                welch_delta(
                    r["baseline_ms"], r["baseline_sd"], r["baseline_n"],
                    r["variant_ms"], r["variant_sd"], r["variant_n"],
                )
                for r in joined_routes.to_dicts()
            ],
            schema={
                "delta": pl.Float64, "se": pl.Float64, "t": pl.Float64, "df": pl.Float64,
                "p": pl.Float64, "ci_low": pl.Float64, "ci_high": pl.Float64,
                "significant": pl.Boolean,
            },
        ).rename({"delta": "delta_ms"})
        per_route = (
            joined_routes.hstack(stats)
            .with_columns(
                (pl.col("delta_ms") / pl.col("baseline_ms") * 100).alias("delta_pct")
            )
            .sort("baseline_ms", descending=True)
        )

        def _metric(metric: str, expr: pl.Expr, alias: str) -> pl.DataFrame:
            if metric not in df_variants:
                return pl.DataFrame({"target": [], alias: []}, schema={"target": pl.String, alias: pl.Float64})
            frame = metric_frame(df_variants, metric).filter(window_mask(window))
            per_iter = frame.group_by(["target", "iteration"]).agg(expr.alias("v"))
            return per_iter.group_by("target").agg([
                pl.col("v").mean().alias(alias),
                pl.col("v").std().alias(f"{alias}_sd"),
                pl.len().alias(f"{alias}_n"),
            ])

        # Energy is only comparable if the variant run actually captured RAPL data.
        if df_variants.get("pod_joules") is not None and df_variants["pod_joules"].height:
            _, energy = process_pod_joules(df_variants["pod_joules"], df_variants["requests"])
            energy = energy.select([
                "target",
                pl.col("total_mean").alias("joules_total"),
                pl.col("total_std").alias("joules_total_sd"),
                pl.col("efficiency_mean").alias("joules_per_10k_req"),
                pl.col("efficiency_std").alias("joules_per_10k_req_sd"),
                pl.col("n_iterations").alias("joules_total_n"),
                pl.col("n_iterations").alias("joules_per_10k_req_n"),
            ])
        else:
            energy = pl.DataFrame(schema={"target": pl.String})

        memory = per_pod_and_cluster(df_variants["memory"], window).group_by("target").agg([
            pl.col("per_pod").mean().alias("memory_per_pod_mb"),
            pl.col("per_pod").std().alias("memory_per_pod_mb_sd"),
            pl.col("cluster").mean().alias("memory_cluster_mb"),
            pl.col("cluster").std().alias("memory_cluster_mb_sd"),
            pl.len().alias("memory_per_pod_mb_n"),
            pl.len().alias("memory_cluster_mb_n"),
        ])

        summary = (
            _metric("p95", pl.col("value").mean(), "p95_ms")
            .join(_metric("rps", pl.col("value").mean(), "rps"), on="target", how="left")
            .join(_metric("pods", pl.col("value").mean(), "pods"), on="target", how="left")
            .join(_metric("checks_rate", pl.col("value").min(), "min_checks_rate"), on="target", how="left")
            .join(memory, on="target", how="left")
            .join(energy, on="target", how="left")
            .join(
                per_pod_and_cluster(df_variants["cpu_usage"], window)
                .group_by("target")
                .agg(pl.col("cluster").mean().alias("cpu_pct_cluster")),
                on="target",
                how="left",
            )
            .sort("target")
        )
        return per_route, summary


    def compare_variant_deltas(df_variant_summary: pl.DataFrame, variant: str) -> pl.DataFrame:
        """
        Long-form baseline/variant/delta view of the cluster-level summary metrics,
        each judged by Welch's test on the difference of the two run means - the same
        rule the per-route table uses. A metric without an sd column (single
        aggregate, no per-iteration spread) is reported without a verdict.
        """
        baseline = VARIANT_OF[variant]
        metrics = [
            ("p95_ms", "Steady P95 (ms)"),
            ("rps", "Throughput (req/s)"),
            ("pods", "Replicas"),
            ("memory_per_pod_mb", "Memory per pod (MB)"),
            ("memory_cluster_mb", "Memory cluster (MB)"),
            ("joules_per_10k_req", "Energy (J / 10k req)"),
            ("cpu_pct_cluster", "CPU (% of node capacity, cluster)"),
        ]
        rows = []
        for col, label in metrics:
            if col not in df_variant_summary.columns:
                continue
            _get = lambda t, c: df_variant_summary.filter(pl.col("target") == t)[c].item()
            b, v = _get(baseline, col), _get(variant, col)
            sd_col, n_col = f"{col}_sd", f"{col}_n"
            has_spread = (
                sd_col in df_variant_summary.columns
                and n_col in df_variant_summary.columns
            )
            stat = (
                welch_delta(
                    b, _get(baseline, sd_col), _get(baseline, n_col),
                    v, _get(variant, sd_col), _get(variant, n_col),
                )
                if has_spread
                else {k: None for k in
                      ("se", "t", "df", "p", "ci_low", "ci_high", "significant")}
            )
            rows.append({
                "metric": label,
                "baseline": b,
                "variant": v,
                "delta": v - b,
                "delta_pct": (v - b) / b * 100 if b else None,
                "baseline_sd": _get(baseline, sd_col) if has_spread else None,
                "variant_sd": _get(variant, sd_col) if has_spread else None,
                "ci_low": stat["ci_low"],
                "ci_high": stat["ci_high"],
                "t": stat["t"],
                "df": stat["df"],
                "p": stat["p"],
                "significant": stat["significant"],
            })
        return pl.DataFrame(rows)


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
                pl.col("cooldown_cpu_mean").mean().alias("idle_cpu_pct_mean"),
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
        rps_steady = (
            metric_frame(df_scaling, "rps")
            .filter(steady)
            .group_by("target")
            .agg(pl.col("value").mean().alias("steady_rps"))
        )
        checks_steady = _steady_agg("checks_rate", pl.col("value").min(), "min_checks_rate")
        cpu_split = (
            per_pod_and_cluster(df_scaling["cpu_usage"], "steady")
            .group_by("target")
            .agg([
                pl.col("per_pod").mean().alias("cpu_pct_per_pod"),
                pl.col("cluster").mean().alias("cpu_pct_cluster"),
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
        CATEGORY_LABEL_COLORS,
        CATEGORY_ORDER,
        WINDOWS,
        WINDOW_LABELS,
        build_efficiency_frame,
        build_windowed_summary,
        category_scale,
        compare_variant,
        compare_variant_deltas,
        metric_title,
        process_all_metrics_summary,
        process_cooldown_idle_drain,
        process_pod_joules,
        process_pod_joules_windows,
        process_request_mix,
        process_request_outcomes,
        process_route_latency,
        process_route_service_time,
        process_scale_to_zero,
        process_scale_up_responsiveness,
        window_slice,
    )


@app.cell
def chart_helpers(
    CATEGORY_LABEL_COLORS,
    CATEGORY_ORDER,
    WINDOWS,
    WINDOW_LABELS,
    build_efficiency_frame,
    category_scale,
    metric_title,
    process_pod_joules,
    window_slice,
):
    # Altair visualization helpers — all target encodings share color_scale()
    # -------------------------------------------------------------------------
    CHART_WIDTH = 650
    CHART_HEIGHT = 160


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
        band: "std" (mean ± 1 SD) or "minmax" (full envelope across the 10 runs).
        bin_seconds=0 disables resampling and keeps the raw sample rate.
        """
        parts = []
        for m in metrics:
            df = metric_frame(df_scaling, m)
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
        band_note = "min-max across runs" if band == "minmax" else "mean ± 1 SD across runs"

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
            "text": f"RAPL Energy Breakdown - {WINDOW_LABELS[window]}",
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
                alt.Tooltip("efficiency_std:Q", title="SD", format=".2f"),
            ],
        )
        errors = base.mark_errorbar().encode(x=alt.X("lo:Q", title=""), x2="hi:Q")

        return (bars + errors).properties(
            width=320,
            height=190,
            title={
                "text": f"Energy Efficiency - {WINDOW_LABELS[window]}",
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

        Facet order is driven by the explicit integer columns `metric_order` and
        `window_order` rather than by a list passed to `sort=`. Under the
        VegaFusion data transformer the list form collapses to a constant sort
        index for every facet, which silently reorders the rows -- Cooldown was
        rendering above Steady State while the title said otherwise.
        """
        samples = []
        for _i, m in enumerate(metrics):
            per_iter = (
                window_slice(metric_frame(df_scaling, m))
                .filter(pl.col("window").is_in(windows))
                .group_by(["target", "window", "iteration"])
                .agg(pl.col("value").mean().alias("sample"))
                .with_columns([
                    pl.lit(m).alias("metric"),
                    pl.lit(metric_title(m)).alias("metric_label"),
                    pl.lit(_i).cast(pl.Int32).alias("metric_order"),
                    pl.col("window").replace_strict(WINDOW_LABELS, default=pl.col("window")).alias("window_label"),
                    pl.col("window")
                    .replace_strict({w: k for k, w in enumerate(windows)}, default=99)
                    .cast(pl.Int32)
                    .alias("window_order"),
                ])
            )
            samples.append(
                per_iter.select([
                    "target", "window", "window_label", "window_order",
                    "iteration", "sample", "metric", "metric_label", "metric_order",
                ])
            )

        combined = pl.concat(samples, how="vertical").sort(target_rank())

        return (
            alt.Chart(combined)
            .mark_boxplot(size=12)
            .encode(
                x=alt.X("sample:Q", title="Per-iteration mean", scale=alt.Scale(zero=False)),
                y=alt.Y("target:N", title=None, sort=TARGET_ORDER),
                color=target_color(),
                column=alt.Column(
                    "metric_label:N",
                    title=None,
                    sort=alt.EncodingSortField(field="metric_order", op="min", order="ascending"),
                ),
                row=alt.Row(
                    "window_label:N",
                    title=None,
                    sort=alt.EncodingSortField(field="window_order", op="min", order="ascending"),
                ),
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
                alt.Tooltip("std:Q", title="SD", format=".2f"),
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
        x_min: float | None = None,
    ) -> alt.Chart:
        """Shared scaffold: one labeled point per target, median quadrant guides,
        optional ±1 sd whiskers and a Pareto frontier line."""
        # `padding` widens the domain by the equivalent of 30 px on each side, which
        # runs a strictly positive quantity below zero when the smallest value sits
        # near the origin. It is applied after `domainMin`, so flooring the axis
        # means giving the domain outright; the headroom padding would have added
        # on the right is kept as a 15 % margin.
        if x_log:
            x_scale = alt.Scale(type="log", nice=False)
        elif x_min is not None:
            x_scale = alt.Scale(domain=[x_min, float(df_plot[x].max()) * 1.15], nice=True)
        else:
            x_scale = alt.Scale(zero=False, padding=30)
        x_enc = alt.X(f"{x}:Q", title=x_title, scale=x_scale)
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
        err_layers: list = []

        if frontier is not None and len(frontier) > 1:
            layers.append(
                alt.Chart(frontier)
                .mark_line(strokeDash=[6, 3], color="#888", strokeWidth=1.2)
                .encode(x=x_enc, y=y_enc)
            )

        if x_err:
            err_layers.append(
                base.mark_rule(opacity=1.0, strokeWidth=2.2).encode(
                    y=y_enc,
                    x=alt.X(f"{x}_lo:Q", title=x_title),
                    x2=f"{x}_hi:Q",
                    color=target_color(legend=False),
                )
            )
        if y_err:
            err_layers.append(
                base.mark_rule(opacity=1.0, strokeWidth=2.2).encode(
                    x=x_enc,
                    y=alt.Y(f"{y}_lo:Q", title=y_title),
                    y2=f"{y}_hi:Q",
                    color=target_color(legend=False),
                )
            )

        points = base.mark_point(size=55, filled=True, opacity=0.95).encode(
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
            alt.layer(*layers, points, *err_layers, labels)
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
            subtitle="Bottom-right is better: more work per joule. Dashed line = Pareto frontier, whiskers = +/-1 SD",
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
            subtitle="Top-right is better: fast and cheap. Dashed line = Pareto frontier, whiskers = +/-1 SD",
            frontier=frontier,
            x_err="rps",
            y_err="requests_per_joule",
        )


    def make_memory_energy_scatter(df_scaling: dict, window: str = "steady") -> alt.Chart:
        """
        Memory footprint against energy cost. The two are argued in the thesis to
        be independent axes rather than two views of "efficiency"; this is the
        figure that shows it. Over the six targets the rank correlation between
        memory per pod and joules per 10k requests is about +0.2, i.e. none.
        """
        _, summary = build_efficiency_frame(df_scaling, window)
        return _labeled_scatter(
            summary,
            x="memory_per_pod_mb",
            y="joules_per_10k_requests",
            x_title="Memory per replica (MB)",
            y_title="Energy per 10 000 requests (J)",
            title=f"Memory footprint vs. energy cost - {WINDOW_LABELS[window]}",
            subtitle="Bottom-left is better on both. The two axes do not order the targets alike",
            # Memory cannot be negative; without this the padding put the axis at -100 MB.
            x_min=0,
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


    def make_request_mix_donut(df_category_mix: pl.DataFrame) -> alt.Chart:
        """Share of total requests per category, as a labeled donut."""
        # Both layers must share one encoding basis. If the text layer omits the
        # color encoding, Vega-Lite stacks it independently of the arcs and the
        # percentages land on the wrong slices. So color/theta/order live on `base`,
        # and only the text's ink is overridden: `fill` takes precedence over `color`
        # for text marks, and scale=None keeps the value literal, so the arcs keep
        # their scale and legend while the labels stay legible on every step.
        df_plot = df_category_mix.with_columns([
            pl.col("category")
            .replace_strict(dict(zip(CATEGORY_ORDER, CATEGORY_LABEL_COLORS)), default="#000000")
            .alias("label_color"),
            pl.col("category")
            .replace_strict({c: i for i, c in enumerate(CATEGORY_ORDER)}, default=len(CATEGORY_ORDER))
            .alias("category_rank"),
        ])

        base = alt.Chart(df_plot).encode(
            theta=alt.Theta("requests:Q", stack=True),
            # Pin the slice order so arcs and labels agree regardless of row order.
            order=alt.Order("category_rank:Q", sort="ascending"),
            color=alt.Color(
                "category:N",
                scale=category_scale(),
                sort=CATEGORY_ORDER,
                title="Request category",
            ),
            tooltip=[
                alt.Tooltip("category:N", title="Category"),
                alt.Tooltip("requests:Q", format=",.0f", title="Requests"),
                alt.Tooltip("observed_pct:Q", format=".2f", title="Observed %"),
                alt.Tooltip("designed_pct:Q", format=".1f", title="Designed %"),
            ],
        )

        arcs = base.mark_arc(innerRadius=60, outerRadius=125, stroke="white", strokeWidth=2)

        labels = base.mark_text(radius=95, fontWeight="bold", fontSize=12).encode(
            text=alt.Text("observed_pct:Q", format=".1f"),
            fill=alt.Fill("label_color:N", scale=None),
        )

        return (arcs + labels).properties(
            width=320,
            height=320,
            title={
                "text": "Request mix by category",
                "subtitle": "Share of all requests served across the 13-minute run",
            },
        )


    def make_request_mix_validation(df_route_mix: pl.DataFrame) -> alt.Chart:
        """
        Observed share per route against the share the k6 weights prescribe.
        Ticks mark the designed value; bars are what the run actually produced.
        """
        base = alt.Chart(df_route_mix).encode(
            y=alt.Y("route:N", title=None, sort=alt.EncodingSortField("designed_pct", order="descending"))
        )

        bars = base.mark_bar().encode(
            x=alt.X("observed_pct:Q", title="Share of all requests (%)"),
            color=alt.Color(
                "category:N", scale=category_scale(), sort=CATEGORY_ORDER, title="Request category"
            ),
            tooltip=[
                alt.Tooltip("route:N", title="Route"),
                alt.Tooltip("category:N", title="Category"),
                alt.Tooltip("requests:Q", format=",.0f", title="Requests"),
                alt.Tooltip("observed_pct:Q", format=".2f", title="Observed %"),
                alt.Tooltip("designed_pct:Q", format=".2f", title="Designed %"),
                alt.Tooltip("delta_pct:Q", format="+.2f", title="Delta (pp)"),
            ],
        )

        designed = base.mark_tick(
            color="#3A2410", thickness=2, size=18, opacity=0.9
        ).encode(x=alt.X("designed_pct:Q", title="Share of all requests (%)"))

        return (bars + designed).properties(
            # The route labels are wider than the left padding Vega computes for
            # them, so the leading "/" was cut off at the SVG edge on export.
            padding={"left": 19, "top": 5, "right": 5, "bottom": 5},
            width=346,
            height=210,
            title={
                "text": "Offered load vs. k6 weighting",
                "subtitle": "Bars = observed share, tick = designed share (category weight / routes in category)",
            },
        )


    def make_route_latency_heatmap(
        df_route_latency: pl.DataFrame, targets: list | None = None
    ) -> alt.Chart:
        """
        Route x target p95 heatmap. Color is log-scaled because per-route latency
        spans four orders of magnitude (0.016 ms to 31 ms).
        """
        df_plot = df_route_latency
        if targets:
            df_plot = df_plot.filter(pl.col("target").is_in(targets))

        return (
            alt.Chart(df_plot)
            .mark_rect(stroke="white", strokeWidth=1)
            .encode(
                x=alt.X("target:N", title=None, sort=TARGET_ORDER, axis=alt.Axis(labelAngle=-30)),
                y=alt.Y("route:N", title=None, sort=alt.EncodingSortField("p95_ms", op="max", order="descending")),
                color=alt.Color(
                    "p95_ms:Q",
                    title="P95 (ms)",
                    scale=alt.Scale(type="log", scheme="orangered", nice=False),
                    # Left to itself the colour bar labels the raw domain ends,
                    # printing them at full float precision (0.0159456369525).
                    # `nice=False` keeps the scale honest, so the ticks are named
                    # here instead: one per decade of the 0.016-31 ms range.
                    legend=alt.Legend(values=[0.1, 1, 10], format="~g"),
                ),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("route:N", title="Route"),
                    alt.Tooltip("category:N", title="Category"),
                    alt.Tooltip("p95_ms:Q", format=".3f", title="P95 (ms)"),
                    alt.Tooltip("p95_sd:Q", format=".3f", title="SD"),
                ],
            )
            .properties(
                # See the padding note on make_request_mix_validation.
                padding={"left": 19, "top": 5, "right": 5, "bottom": 5},
                width=316,
                height=210,
                title={
                    "text": "Steady-state P95 per route",
                    "subtitle": "Log color scale; mean of per-iteration means",
                },
            )
        )


    def make_route_latency_bars(df_route_latency: pl.DataFrame) -> alt.Chart:
        """Per-route p95 per target on a shared log axis, faceted by route."""
        # A bar is drawn from the scale's zero baseline, which does not exist on a
        # log scale - encoding only `x` renders an empty panel. Both ends have to be
        # given explicitly, so bars start at a fixed floor below the smallest
        # observed p95 (0.016 ms) and grow to the measured value.
        df_plot = df_route_latency.with_columns(pl.lit(LOG_FLOOR).alias("floor_ms"))

        return (
            alt.Chart(df_plot)
            .mark_bar()
            .encode(
                x=alt.X(
                    "floor_ms:Q",
                    title="P95 (ms) - log scale",
                    scale=alt.Scale(type="log", nice=False),
                ),
                x2=alt.X2("p95_ms:Q"),
                y=alt.Y("target:N", title=None, sort=TARGET_ORDER),
                color=target_color(),
                row=alt.Row("route:N", title=None, header=alt.Header(labelAngle=0, labelAlign="left")),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("route:N", title="Route"),
                    alt.Tooltip("category:N", title="Category"),
                    alt.Tooltip("p95_ms:Q", format=".3f", title="P95 (ms)"),
                    alt.Tooltip("p95_sd:Q", format=".3f", title="SD"),
                ],
            )
            .properties(
                width=380,
                height=90,
                title={
                    "text": "P95 by route and target",
                    "subtitle": f"Bars start at the {LOG_FLOOR} ms floor; log axis",
                },
            )
        )


    def make_service_time_chart(df_service_time: pl.DataFrame) -> alt.Chart:
        """
        Share of each target's service time by route (rate x latency), stacked to
        100 %. Shows which endpoint a target's latency budget is actually spent on.
        """
        return (
            alt.Chart(df_service_time)
            .mark_bar()
            .encode(
                x=alt.X("service_time_pct:Q", title="Share of total service time (%)", stack="normalize"),
                y=alt.Y("target:N", title=None, sort=TARGET_ORDER),
                color=alt.Color(
                    "route:N",
                    title="Route",
                    scale=alt.Scale(scheme="tableau10"),
                    sort=alt.EncodingSortField("service_time_pct", op="max", order="descending"),
                ),
                tooltip=[
                    alt.Tooltip("target:N", title="Target"),
                    alt.Tooltip("route:N", title="Route"),
                    alt.Tooltip("traffic_pct:Q", format=".1f", title="Traffic share (%)"),
                    alt.Tooltip("p95_ms:Q", format=".3f", title="P95 (ms)"),
                    alt.Tooltip("service_time_pct:Q", format=".1f", title="Service-time share (%)"),
                ],
            )
            .properties(
                # The longest route name in the legend overran the right edge
                # of the exported SVG.
                padding={"left": 5, "top": 5, "right": 19, "bottom": 5},
                width=416,
                height=200,
                title={
                    "text": "Where each target spends its service time",
                    "subtitle": "Request rate x P95 per route, normalized per target",
                },
            )
            # The route names are long; without extra padding the legend is clipped
            # at the right edge when the chart is exported to SVG.
            .configure_view(continuousWidth=430)
            .configure_legend(labelLimit=0, padding=10, offset=16)
        )


    def make_variant_route_chart(df_variant_routes: pl.DataFrame, variant: str) -> alt.Chart:
        """
        Paired baseline-vs-variant p95 per route as a dumbbell: the connector shows
        the change, the dot color shows which build it belongs to. Log x-axis, since
        the routes span three orders of magnitude.
        """
        baseline = VARIANT_OF[variant]
        long = df_variant_routes.select([
            "route",
            pl.col("baseline_ms").alias(baseline),
            pl.col("variant_ms").alias(variant),
            "significant",
        ]).unpivot(
            index=["route", "significant"], variable_name="build", value_name="p95_ms"
        )

        # Explicit route order: the connector layer has no p95_ms column, so a sort
        # field would resolve differently in each layer.
        route_order = df_variant_routes.sort("baseline_ms", descending=True)["route"].to_list()
        y = alt.Y("route:N", title=None, sort=route_order)
        x = alt.X("p95_ms:Q", title="P95 (ms) - log scale", scale=alt.Scale(type="log", nice=False))

        connector = (
            alt.Chart(df_variant_routes)
            .mark_rule(strokeWidth=2, opacity=0.45)
            .encode(y=y, x=alt.X("baseline_ms:Q", title="P95 (ms) - log scale"), x2="variant_ms:Q")
        )
        points = (
            alt.Chart(long)
            .mark_point(size=110, filled=True, opacity=0.95)
            .encode(
                y=y,
                x=x,
                color=alt.Color("build:N", scale=variant_color_scale(variant), title="Build"),
                tooltip=[
                    alt.Tooltip("route:N", title="Route"),
                    alt.Tooltip("build:N", title="Build"),
                    alt.Tooltip("p95_ms:Q", format=".3f", title="P95 (ms)"),
                    alt.Tooltip("significant:N", title="Resolved (p < 0.05)"),
                ],
            )
        )

        return (connector + points).properties(
            # See the padding note on make_request_mix_validation.
            padding={"left": 19, "top": 5, "right": 5, "bottom": 5},
            width=386,
            height=210,
            title={
                "text": f"{baseline} vs {variant}: steady-state P95 per route",
                "subtitle": "Dot pairs joined per route; a shift left means the componentized build is faster. Welch's test per route, alpha = 0.05",
            },
        )


    return (
        make_efficiency_iteration_scatter,
        make_efficiency_scatter,
        make_energy_efficiency_chart,
        make_energy_latency_scatter,
        make_memory_energy_scatter,
        make_peak_boxplots,
        make_rapl_energy_chart,
        make_request_mix_donut,
        make_request_mix_validation,
        make_route_latency_bars,
        make_route_latency_heatmap,
        make_service_time_chart,
        make_timeseries_charts,
        make_timeseries_facet,
        make_variant_route_chart,
        make_window_summary_chart,
        make_work_per_joule_scatter,
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
        mo.md("## Steady-state performance and energy summary"),
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
    summarizing across iterations - so `std` is **run-to-run** variance, not sample
    noise inside a run.

    - **Throughput is stable once scaled**, so the ranking is not an artefact of a
      lucky iteration. `wasm-js` is the one target whose run-to-run spread is a large
      share of its own mean.
    - The Scale-Up window is where runs disagree most, and that is HPA reaction timing
      rather than steady capacity.
    - **Cooldown throughput stays high.** With KEDA scaling on concurrency, traffic is
      still balanced across the replicas as the VUs ramp down, so the window is not
      idle.
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

    - **Cold-start latency cost is negligible for the container targets**: peak p95
      during the initial ramp is barely above their steady-state p95, so no target pays
      a visible first-request penalty. `wasm-js` is the exception by a wide margin.
    - **The mean time to first scale-up does not rank the targets.** It averages only
      the iterations that started below their target replica count - 27 scale events
      across all 60 runs, as few as two for some targets - and every target has one slow
      outlier dragging its mean up. The medians are flat.
    - Read this as "KEDA reacts within roughly 15-20 s once it has to", not as a
      between-target ranking. Since KEDA scales on concurrency = 20, the targets that
      saturate a worker soonest trigger earliest: this measures request-holding time,
      not platform startup speed.
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
    ## Master summary - headline results

    One row per target, all values from the Steady-State window (480-600 s) unless the
    column name says otherwise. Throughput is the **sum over all seven routes**; energy
    is normalized by the request counter differenced per label series.

    - **Throughput splits the field into two groups**, the four container targets well
      ahead of the two WebAssembly ones.
    - **Latency follows throughput**, with `wasm-js` an order of magnitude above
      everything else on both p95 and p99.
    - **`wasm-js` is the only target that drops requests** (HTTP timeouts). Every other
      target holds a perfect check rate across all 10 iterations.
    - **Energy per request is where the ranking inverts.** The throughput leader is not
      the efficiency leader, so absolute joules have to be normalized by work done
      before the targets can be compared at all.
    - **Footprint spans two orders of magnitude** per replica, and the spread widens as
      a cluster total, because the slower targets also run more replicas.
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

    Mean across the 10 iterations with a +/-1 SD band, summed over all seven routes;
    shaded regions mark the load stages. The step at each VU increase is visible for the
    container targets, but not for `wasm-js`, which is already saturated during the
    first ramp and *loses* throughput once the load reaches 100 VUs.
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
    readout of how long each request occupies a worker.

    - The slow targets need **more** pods to serve **less** traffic.
    - Replica counts stay flat through Cooldown by design, because KEDA's scale-down
      fires roughly 30 s after the run ends, outside the capture window.
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

    Per-replica CPU as a share of total node capacity (percent), not cores: the query
    divides the pod CPU-time rate by the node's logical CPU count.

    - The ordering differs from the energy ranking - a target can burn comparable CPU to
      another and convert it into more throughput, which is what lowers its
      joules-per-request.
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
    **logarithmic y-axis** - on a linear axis `wasm-js` flattens the other five curves
    into a single line along the bottom.

    - `wasm-js` sits an order of magnitude above everything else and its band is both
      high and wide, so its latency is not just worse but *unpredictable* between runs.
    - The remaining targets separate cleanly on the log axis, with `wasm-rust`
      distinctly above the four container targets.
    """)
    return


@app.cell
def md_route_latency():
    mo.md(r"""
    ## Per-route latency: where the time actually goes

    `p95_by_route` breaks the aggregate p95 into the seven k6 routes. Two findings change
    how the earlier numbers should be read.

    - **One route dominates every container target.** `/match/team/:id`, the team join
      lookup, carries the overwhelming majority of total service time on every target
      except `wasm-js`, while every other route on those targets is an order of magnitude
      cheaper. Service-time share is request rate x p95, so this combines the route's
      30 % traffic weight with its cost; a slow route called rarely would not show up.
    - **The endpoint designed as "expensive" is not the expensive one.** The `aggregate`
      category (`/match/result-table`, a full result-table query) resolves quickly on all
      five of those targets and accounts for only a small share of service time. The
      cost sits in the `lookup` category instead: the weighting was designed on expected
      query cost, and the measurement disagrees.
    - **`wasm-js` behaves differently.** Its aggregate p95 is not uniform
      slowness but one route - `/teams/record/:id` alone accounts for most of its service
      time, while `/match/team/:id`, the route that dominates everyone else, is *faster*
      on `wasm-js` than on several container targets. Not a general latency problem, one
      pathological endpoint that the aggregate metric was hiding behind a single number.
    - **On trivial routes the runtimes converge.** For the three `simple` key lookups
      `wasm-rust` lands in the same band as the slower container targets. The Wasm
      penalty is not a fixed per-request overhead that shows up everywhere - it appears
      specifically on the database-heavy lookup.
    """)
    return


@app.cell
def route_latency(
    df_scaling,
    make_route_latency_bars,
    make_route_latency_heatmap,
    make_service_time_chart,
    process_route_latency,
    process_route_service_time,
):
    # Per-route latency and where service time is spent
    df_route_latency = process_route_latency(df_scaling["p95_by_route"])
    df_service_time = process_route_service_time(df_scaling)

    chart_route_heatmap = make_route_latency_heatmap(df_route_latency)
    chart_service_time = make_service_time_chart(df_service_time)
    chart_route_bars = make_route_latency_bars(df_route_latency)

    mo.vstack([
        mo.hstack([chart_route_heatmap, chart_service_time], justify="start", align="center"),
        df_route_latency,
        chart_route_bars
    ])
    return (
        chart_route_bars,
        chart_route_heatmap,
        chart_service_time,
        df_route_latency,
        df_service_time,
    )


@app.cell
def _(ts_charts):
    ts_charts["p95"]
    return


@app.cell
def md_energy():
    mo.md(r"""
    ## RAPL energy: domains and efficiency

    Package and DRAM joules accumulated during Steady State, summed over the pods alive
    at each sample.

    - DRAM is a small and fairly constant share on every target, so the package domain
      drives all of the differences.
    - **Per-request efficiency reorders the absolute ranking**, because absolute energy
      rewards targets that simply did less work: a target can look mid-pack in joules
      only because it served a fraction of the requests.
    - Normalizing by *successful* requests instead barely moves the picture - the
      `wasm-js` failure rate is too small to explain its efficiency gap. The cost is in
      how it serves the requests that do succeed.
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

    Each box is 10 points - one per iteration - so the spread is run-to-run
    reproducibility.

    - **Latency:** every container target is tight in both windows. `wasm-js` is the
      exception in both, and it does not recover when the load drops, which points at a
      queue that never drains rather than at instantaneous capacity.
    - **Memory:** `oci-native` is the least reproducible, consistent with GC heap growth
      varying by run; `oci-axum` and `wasm-rust` are essentially deterministic.
    - **Pods:** `oci-axum` and `oci-node` hold the same replica count in every run,
      while `oci-spring` and `oci-native` oscillate around the KEDA threshold and
      occasionally serve peak load with one replica fewer.
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

    Bar charts rank one axis at a time; these scatter plots put energy against the work
    it bought, one point per target (crosses/whiskers show run-to-run spread). The dashed
    line is the **Pareto frontier** - the targets no other target beats on both axes at
    once.

    - **Only two targets are non-dominated: `oci-axum` and `oci-spring`.** `oci-axum` is
      the cheapest per unit of work and `oci-spring` the fastest; everything else is
      beaten outright by one of them, and both Wasm targets sit in the worst quadrant.
    - **Energy does not buy latency.** Paying more per request does move p95 down
      slightly along the frontier, but `oci-native` and both Wasm targets pay *more*
      energy for *worse* latency, so their extra consumption is overhead rather than
      performance.
    - **The per-iteration cloud shows the ranking is not noise.** Each target's 10 runs
      form a tight cluster well separated from its neighbours; the only visible spread
      is `wasm-js` along the throughput axis.

    Two caveats:

    - Efficiency is measured at each target's *own* steady-state throughput, not at a
      matched request rate, so part of the Wasm penalty is a fixed idle draw spread over
      fewer requests rather than a higher marginal cost.
    - No target is anywhere near saturation, so these are efficiency figures at low
      utilisation, not at capacity.
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
    chart_memory_energy = make_memory_energy_scatter(df_scaling)
    chart_efficiency_runs = make_efficiency_iteration_scatter(df_scaling)

    mo.vstack([
        mo.hstack([chart_efficiency_scatter, chart_work_per_joule], justify="start"),
        mo.hstack([chart_energy_latency, chart_efficiency_runs], justify="start"),
        mo.hstack([chart_memory_energy], justify="start"),
    ])
    return (
        chart_efficiency_runs,
        chart_efficiency_scatter,
        chart_energy_latency,
        chart_memory_energy,
        chart_work_per_joule,
        df_efficiency,
        df_efficiency_runs,
    )


@app.cell(hide_code=True)
def md_cooldown():
    mo.md(r"""
    ## Cooldown: retained footprint, not scale-to-zero

    KEDA scales on concurrency = 20 and its scale-down only fires about 30 s **after** the
    run ends, so replicas intentionally stay up for the whole 660-780 s window while
    traffic is still balanced across them. `df_scale_to_zero` therefore reports
    `drain_time_s == 0` and identical start/end replica counts for every target: what it
    measures is the **resource footprint kept alive at end of run**, not scale-down
    latency. Measuring the latter needs a capture that extends past the scale-down delay.

    - Idle footprint per replica spans more than an order of magnitude, which translates
      directly into how many idle replicas fit on a node.
    - Idle CPU orders differently, because a target serving almost nothing has little
      left to do.
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
    `df_cooldown_idle` reports the CPU and RAM footprint each runtime **retains at the end
    of the run** (the 660-780 s window), per replica.

    The `idle_` prefix is a misnomer: the VUs ramp to zero across this window, but
    KEDA's scale-down has not fired yet and traffic is still being served on every target
    except `wasm-js`, the only one genuinely quiet. So the memory column is a fair read
    of retained footprint - within a few MB of the steady-state value on every target
    except `oci-native` - but the CPU column is *not* a baseline overhead measurement,
    since it still contains real request work. Isolating true idle draw needs a capture
    that extends past KEDA's scale-down delay.

    ### Metric definitions

    * **`idle_cpu_pct_mean`**: mean CPU over the window, as a percentage of total node
      capacity (the query divides the pod CPU-time rate by the node's logical CPU count).
    * **`idle_mem_mb_mean`**: mean resident memory in MB per replica over the window.

    ### What the numbers say

    * **Lowest retained memory:** `oci-axum` and `wasm-rust` - compiled, GC-free binaries
      with no runtime heap to hold on to.
    * **Highest retained memory:** `oci-spring`, essentially unchanged from its
      steady-state figure, because the JVM does not give the heap back.
    * **Memory that *does* fall back:** `oci-native` is the only target whose end-of-run
      footprint is materially below its steady-state value.
    * **CPU:** not comparable as an idle cost, because each target is still serving a
      different amount of traffic in this window.

    ### How to use this

    * **Node density.** The memory column supports the density argument directly: the
      compiled targets fit far more retained replicas on a node than `oci-spring`. That
      matters in an auto-scaling setup where replicas outlive the traffic that created
      them, which is exactly what this window shows.
    * **Not for cold-start or true-idle cost.** Neither is measured here; the scale-up
      section covers reaction time, and a genuine idle baseline would need a longer
      capture.
    """)
    return


@app.cell
def md_request_mix():
    mo.md(r"""
    ## Request mix: is the offered load the load we designed?

    Every target is driven by the same k6 scenario, which picks a **category** by weight
    and then a route uniformly inside that category:

    | Category | Weight | Routes | Designed share per route |
    | --- | --- | --- | --- |
    | `simple` | 10 % | `/players/:id`, `/teams/:id`, `/match/:id` | 3.33 % each |
    | `detailed` | 30 % | `/players/record/:id`, `/teams/record/:id` | 15.0 % each |
    | `lookup` | 30 % | `/match/team/:id` | 30.0 % |
    | `aggregate` | 30 % | `/match/result-table` | 30.0 % |

    - Cheap key lookups make up only a tenth of the traffic, while the three expensive
      shapes - multi-row record queries, a join by team, a full result-table aggregation
      - carry 90 % of it. Comparing runtimes on `/players/:id` alone would mostly measure
      HTTP framing overhead; this mix makes the database and serialisation work dominate.
    - **The run reproduces the design almost exactly.** Every observed category share
      lands within a tenth of a percentage point of its designed weight, and the
      intra-category split holds too. Random route selection had millions of requests to
      converge over, so this is expected - but it confirms that no target skewed the mix
      by failing a particular route, which would quietly invalidate the comparison.
    - Counts come from the `http_reqs` counter differenced per label series. Counting
      scrape rows instead - one row per label series per sample - measures how many
      routes a category owns rather than how much traffic it received, and overstates
      `simple` because it spans three routes.
    """)
    return


@app.cell
def request_mix(
    df_scaling,
    make_request_mix_donut,
    make_request_mix_validation,
    process_request_mix,
):
    # Request mix: what the k6 scenario actually offered
    df_request_mix, df_route_mix = process_request_mix(df_scaling["requests"])

    chart_request_mix = make_request_mix_donut(df_request_mix)
    chart_request_mix_validation = make_request_mix_validation(df_route_mix)

    mo.hstack([chart_request_mix, chart_request_mix_validation], justify="start", align="center")
    return (
        chart_request_mix,
        chart_request_mix_validation,
        df_request_mix,
        df_route_mix,
    )


@app.cell
def request_mix_table(df_route_mix):
    df_route_mix
    return


@app.cell
def md_variant_ab():
    mo.md(r"""
    ## Deployment variant: monolithic vs componentized `wasm-rust`

    The same Rust application built as three separate Wasm components
    (`players` / `teams` / `match`) routed inside one SpinApp, against the single monolithic
    component. Same pod topology, same KEDA configuration, same scenario - the artifact is
    the only thing that changed.

    Both builds have **10 iterations and full RAPL capture**, so this is a symmetric
    comparison, and every metric is judged by the same rule as the per-route table:
    Welch's two-sided test at alpha = 0.05 on the difference of the two run means. It is
    reported separately rather than as a seventh target, because every other target ships
    one binary serving all routes, and letting one runtime enter the comparison twice
    would give it two attempts at the Pareto frontier.

    - **Componentization helps the cheapest routes and hurts two of the mid-cost ones.**
      Six of the seven routes resolve, but they do not share a direction. The four
      cheapest gain 19-55 %, which is what the theory predicts: a smaller module resolves
      and dispatches faster, and where the work itself takes tens of microseconds that
      overhead share is large enough to see. `/match/:id` and `/match/result-table` move
      the other way by about 6 %; only the second is borderline under a Bonferroni
      correction across the seven routes. The most expensive route, `/match/team/:id`,
      does not resolve at all.
    - **The aggregate picture is small but not empty.** Because `/match/team/:id` owns
      almost all of the service time, savings of tens of microseconds on the cheap routes
      cannot surface in the total. Replica count is unchanged and both builds hold a
      perfect check rate, but steady p95, throughput and energy per request all move
      against the componentized build.
    - **Energy is not a wash, but the penalty is small.** Energy per 10k requests rises
      by about 2 %, and throughput falls by under 2 %; both resolve at ten iterations,
      and both survive a Bonferroni correction across the metrics in the table. Total
      energy over the window does not move, which is consistent: the componentized build
      spends slightly more energy per unit of work because it completes slightly less
      work for the same power.
    - **It costs memory, and that is by far the largest effect.** Resident memory per
      replica and cluster total both rise by about 36-38 %, because each pod instantiates
      three components instead of one.

    **Conclusion.** At this workload, component granularity buys tens of microseconds on
    routes that were already fast, costs about 2 % in energy per unit of work and 16 MiB
    of resident memory per replica, and leaves aggregate latency unresolved. The effect
    on the small routes is real and correctly signed, so the mechanism is doing what it
    should - it simply has no leverage on a steady mixed load whose cost is concentrated
    in one database-heavy route. Componentization should be argued for
    cold-start-dominated or independently-scaled workloads, not for this one.
    """)
    return


@app.cell
def variant_ab(
    compare_variant,
    compare_variant_deltas,
    df_variants,
    make_variant_route_chart,
):
    # Deployment variant A/B: monolithic vs componentized wasm-rust
    df_variant_routes, df_variant_summary = compare_variant(df_variants, "wasm-rust-components")
    df_variant_deltas = compare_variant_deltas(df_variant_summary, "wasm-rust-components")
    chart_variant_routes = make_variant_route_chart(df_variant_routes, "wasm-rust-components")

    mo.vstack([
        chart_variant_routes,
        df_variant_deltas,
        df_variant_routes.select([
            "route", "category", "baseline_ms", "variant_ms",
            "delta_ms", "delta_pct", "ci_low", "ci_high", "p", "significant"
        ]),
        df_variant_summary,
    ])
    return (
        chart_variant_routes,
        df_variant_deltas,
        df_variant_routes,
        df_variant_summary,
    )


@app.cell
def export_figures(
    chart_boxplots,
    chart_efficiency,
    chart_efficiency_runs,
    chart_efficiency_scatter,
    chart_energy,
    chart_energy_latency,
    chart_pods_by_window,
    chart_request_mix,
    chart_request_mix_validation,
    chart_route_bars,
    chart_route_heatmap,
    chart_service_time,
    chart_ts_panel,
    chart_variant_routes,
    chart_work_per_joule,
    df_cooldown_idle,
    df_efficiency,
    df_efficiency_runs,
    df_energy_windows,
    df_master_summary,
    df_request_mix,
    df_request_outcomes,
    df_route_latency,
    df_route_mix,
    df_scale_to_zero,
    df_service_time,
    df_variant_deltas,
    df_variant_routes,
    df_variant_summary,
    df_windowed_summary,
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
        "scaling_memory_energy_scatter": chart_memory_energy,
            "scaling_efficiency_runs_scatter": chart_efficiency_runs,
            "scaling_request_mix": chart_request_mix,
            "scaling_request_mix_validation": chart_request_mix_validation,
            "scaling_route_latency_heatmap": chart_route_heatmap,
            "scaling_route_latency_bars": chart_route_bars,
            "scaling_service_time_share": chart_service_time,
            "scaling_variant_route_p95": chart_variant_routes,
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
            "scaling_request_mix": df_request_mix,
            "scaling_route_mix": df_route_mix,
            "scaling_route_latency": df_route_latency,
            "scaling_service_time": df_service_time,
            "scaling_variant_routes": df_variant_routes,
            "scaling_variant_deltas": df_variant_deltas,
            "scaling_variant_summary": df_variant_summary,
        },
    )
    export_manifest
    return


if __name__ == "__main__":
    app.run()
