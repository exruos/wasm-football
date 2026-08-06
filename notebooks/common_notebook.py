import marimo

__generated_with = "0.23.15"
app = marimo.App(width="medium")

with app.setup:
    import glob
    import re
    from pathlib import Path

    import altair as alt
    import polars as pl

    # Canonical target ordering and palette. Every notebook derives its ordering
    # and colors from these, so a target keeps one identity across all figures.
    TARGET_ORDER = [
        "oci-axum",
        "oci-spring",
        "oci-native",
        "oci-node",
        "wasm-js",
        "wasm-rust",
    ]
    TARGET_PALETTE = [
        "#D34516",
        "#6DB33F",
        "#00758F",
        "#1B661B",
        "#FC7C00",
        "#654FF0",
    ]

    # Deployment variants: a re-packaged build of a target that already appears in
    # TARGET_ORDER. A variant is NOT a seventh stack - every other target ships one
    # binary serving all routes, so a variant is compared A/B against its own
    # baseline and kept out of the cross-target comparison, which would otherwise
    # give one runtime two attempts at the Pareto frontier.
    VARIANT_OF = {"wasm-rust-components": "wasm-rust"}

    # Variants render as a lighter tint of the baseline's color.
    VARIANT_PALETTE = {"wasm-rust-components": "#A99AF7"}

    # Where exported thesis assets land, relative to the notebook directory.
    FIGURE_DIR = Path("figures")
    TABLE_DIR = Path("tables")

    # Every label that can split a metric into separate time series. Counters must
    # be differenced per series - see series_key().
    SERIES_LABEL_COLUMNS = (
        "url",
        "name",
        "scenario",
        "stage",
        "status",
        "expected_response",
        "pod_name",
        "pod",
        "zone",
    )

    # Labels identifying one k6 route series, used to de-duplicate per-route gauges.
    ROUTE_LABEL_COLUMNS = ("url",)

    # Metrics k6 reports per route: the cluster figure is the SUM over routes.
    PER_ROUTE_METRICS = {"rps"}


@app.function
def load_metric_data(metric: str, scenario: str, time_col: str = "timestamp", select_columns: list[str] | None = None) -> pl.DataFrame:
    path_pattern = f"./parquet/*/{scenario}/{metric}_*.parquet"
    dir_regex = f"parquet/([^/]+)/{scenario}"
    iter_regex = r"_(\d+)\.parquet$"

    # The glob is a prefix match, so "p95_*" also catches "p95_by_route_1.parquet"
    # and silently merges two metrics into one frame. Only accept files whose name
    # is exactly <metric>_<iteration>.parquet.
    file_regex = re.compile(rf"(?:^|[\\/]){re.escape(metric)}_\d+\.parquet$")
    all_files = [f for f in glob.glob(path_pattern) if file_regex.search(f)]
    # Filter out empty parquet files that cause schema unification failures
    empty_files = [f for f in all_files if pl.scan_parquet(f).select(pl.len()).collect()['len'][0] == 0]
    files = [f for f in all_files if f not in empty_files]

    if empty_files:
        print(f"Skipping {len(empty_files)} empty file(s) for {metric}/{scenario}:")
        for f in empty_files:
            print(f"  - {f}")

    if not files:
        return pl.DataFrame()

    query = pl.scan_parquet(
        files, 
        include_file_paths="full_path", 
        missing_columns="insert", 
        extra_columns="ignore"
    )

    # 1. Add derived metadata columns first
    query = query.with_columns([
        pl.col("full_path").str.extract(dir_regex).alias("target"),
        pl.col("full_path").str.extract(iter_regex).cast(pl.Int32).alias("iteration"),
    ]).drop("full_path")

    # 2. If selective columns requested, retain metadata columns + requested columns
    if select_columns:
        required_cols = list(set(select_columns + [time_col, "value", "zone", "target", "iteration"]))
        # Keep only columns that exist in schema to avoid projection errors
        available = [c for c in required_cols if c in query.collect_schema().names()]
        query = query.select(available)

    # 3. Sort and calculate normalized time lazily before calling collect()
    return (
        query
        .sort(["target", "iteration", time_col])
        .with_columns(
            (pl.col(time_col) - pl.col(time_col).min().over(["target", "iteration"]))
            .dt.total_seconds(fractional=True)
            .alias("normalized_time")
        )
        .collect()
    )


@app.function
def load_scenario_metrics(
    scenario: str,
    time_col: str = "timestamp",
    select_columns: list[str] | None = None,
) -> dict[str, pl.DataFrame]:
    """
    Loads all existing metric DataFrames for a single scenario, dropping
    missing or empty ones.
    """

    DEFAULT_METRICS = [
        "pod_joules",
        "node_joules",
        "node_avg_cpu_watts",
        "pods",
        "requests",
        "iterations",
        "vus",
        "p95",
        "p95_by_route",
        "p99",
        "rps",
        "memory",
        "checks_rate",
        "cpu_usage",
    ]

    scenario_data = {}

    for metric in DEFAULT_METRICS:
        try:
            df = load_metric_data(
                metric=metric,
                scenario=scenario,
                time_col=time_col,
                select_columns=select_columns,
            )

            if df is None:
                continue

            if "scenario" in df.columns:
                df = df.filter(pl.col("scenario") == scenario)

            if len(df) > 0:
                scenario_data[metric] = df

        except FileNotFoundError as e:
            print(f"[{scenario}] Skipped missing metric '{metric}': {e}")

    return scenario_data


@app.function
def build_scenario_table(df_scenario_metrics: dict) -> pl.DataFrame:
            """Build a wide DataFrame for a scenario by joining all metric DataFrames on target + iteration.

            Each metric may have multiple timestamped rows per (target, iteration).
            We aggregate to one row per (target, iteration) using max for cumulative
            counters (requests, iterations, vus, pods) and mean for sampled metrics
            (p95, p99, rps, memory, cpu_usage, pod_joules).
            """
            cumulative_counters = {"requests", "iterations", "vus", "pods", "pod_joules"}
            scenario_keys = [k for k in df_scenario_metrics if len(df_scenario_metrics[k]) > 0]
            if not scenario_keys:
                return pl.DataFrame()

            # Start with the first metric's target and iteration
            base = df_scenario_metrics[scenario_keys[0]].select(["target", "iteration"]).unique()

            # Aggregate each metric to one row per (target, iteration), then join
            tables = []
            for metric in scenario_keys:
                df = df_scenario_metrics[metric]
                if "value" in df.columns:
                    agg_fn = pl.col("value").max() if metric in cumulative_counters else pl.col("value").mean()
                    tables.append(
                        df.group_by(["target", "iteration"])
                          .agg(agg_fn.alias(metric))
                          .unique(subset=["target", "iteration"])
                    )
                else:
                    tables.append(
                        df.select(["target", "iteration"])
                          .unique(subset=["target", "iteration"])
                          .with_columns(pl.lit(0.0).alias(metric))
                    )

            result = base
            for t in tables:
                result = result.join(t, on=["target", "iteration"], how="left")
            return result


@app.function
def color_scale() -> alt.Scale:
    """Altair color scale mapping each target to its canonical color."""
    return alt.Scale(domain=TARGET_ORDER, range=TARGET_PALETTE)


@app.function
def split_variants(scenario_metrics: dict) -> tuple[dict, dict]:
    """
    Separate the cross-target comparison from deployment variants.

    Returns (main, variants): `main` keeps only targets in TARGET_ORDER, so every
    existing figure and table stays a like-for-like six-way comparison; `variants`
    keeps each variant together with the baseline it is measured against, ready
    for a paired A/B.
    """
    baselines = set(VARIANT_OF.values())
    variant_names = set(VARIANT_OF)

    main = {
        k: v.filter(pl.col("target").is_in(TARGET_ORDER))
        for k, v in scenario_metrics.items()
    }
    variants = {
        k: v.filter(pl.col("target").is_in(variant_names | baselines))
        for k, v in scenario_metrics.items()
        if v.filter(pl.col("target").is_in(variant_names)).height > 0
    }
    return main, variants


@app.function
def variant_color_scale(variant: str) -> alt.Scale:
    """Color scale pairing a variant with its baseline: baseline hue, lighter tint."""
    baseline = VARIANT_OF[variant]
    baseline_color = TARGET_PALETTE[TARGET_ORDER.index(baseline)]
    return alt.Scale(
        domain=[baseline, variant], range=[baseline_color, VARIANT_PALETTE[variant]]
    )


@app.function
def target_rank(column: str = "target") -> pl.Expr:
    """
    Polars sort key keeping targets in TARGET_ORDER.

    Preferred over casting to pl.Enum: vegafusion cannot pack Enum/categorical
    columns into Arrow dictionaries and fails when such a frame reaches a chart.
    """
    return pl.col(column).replace_strict(
        {t: i for i, t in enumerate(TARGET_ORDER)}, default=len(TARGET_ORDER)
    )


@app.function
def target_color(legend: bool = True, title: str = "Target") -> alt.Color:
    """Consistent target color/legend encoding for every chart."""
    return alt.Color(
        "target:N",
        scale=color_scale(),
        sort=TARGET_ORDER,
        title=title,
        legend=alt.Legend(orient="right") if legend else None,
    )


@app.function
def series_key(df: pl.DataFrame, extra: tuple[str, ...] = ()) -> list[str]:
    """
    The columns identifying one Prometheus/k6 time series in df: target, iteration
    and every label column that actually varies.

    A counter must be differenced WITHIN one series. Under a partial key several
    counters interleave, .diff() jumps between them and clip(lower_bound=0) turns
    each jump into a fake increment. k6 restarts http_reqs_total at every stage
    boundary, so omitting `stage` overcounts requests by roughly 100x.
    """
    labels = [c for c in SERIES_LABEL_COLUMNS if c in df.columns and df[c].n_unique() > 1]
    return list(dict.fromkeys(["target", "iteration", *extra, *labels]))


@app.function
def total_gauge(df: pl.DataFrame, dedup_on: tuple[str, ...] = ROUTE_LABEL_COLUMNS) -> pl.DataFrame:
    """
    Collapse a per-route gauge (k6 rps) into a per-timestamp cluster total.

    Overlapping k6 stage labels repeat the same route reading at one timestamp,
    so rows are de-duplicated per route before summing; a naive group_by().mean()
    instead reports the average route, understating the total by the route count.
    """
    dedup_cols = [c for c in dedup_on if c in df.columns]
    if not dedup_cols:
        return df

    return (
        df.unique(subset=["target", "iteration", "normalized_time", *dedup_cols])
        .group_by(["target", "iteration", "normalized_time"])
        .agg(pl.col("value").sum().alias("value"))
        .sort(["target", "iteration", "normalized_time"])
    )


@app.function
def metric_frame(scenario_metrics: dict, metric: str) -> pl.DataFrame:
    """
    The frame to analyse for a metric, with per-route metrics already summed into
    a cluster total. Use this instead of indexing the metric dict directly.
    """
    df = scenario_metrics[metric]
    return total_gauge(df) if metric in PER_ROUTE_METRICS else df


@app.function
def counter_increments(
    df: pl.DataFrame,
    group_keys: list[str],
    value_col: str = "value",
    alias: str = "increment",
    time_col: str = "normalized_time",
) -> pl.DataFrame:
    """
    First difference of a cumulative Prometheus counter within each series,
    clipped at zero so a counter reset (pod restart, RAPL wraparound) never
    subtracts.

    Diffs MUST be taken per series over the full series before any window or
    phase filtering - that ordering is what makes windowed sums correct.
    """
    return df.sort(group_keys + [time_col]).with_columns(
        pl.col(value_col)
        .diff()
        .over(group_keys)
        .fill_null(0.0)
        .clip(lower_bound=0.0)
        .alias(alias)
    )


@app.function
def pod_joules_increments(df_pj: pl.DataFrame) -> pl.DataFrame:
    """
    Per-timestamp incremental joules by RAPL zone, summed over the pods alive at
    that moment: columns package, dram, total = package + dram.

    kepler_pod_cpu_joules_total is a counter PER POD PER ZONE. Each pod must be
    differenced on its own series before the increments are summed; collapsing
    pods first (max/mean across pods) tracks only the hottest replica and drops
    energy whenever the leading pod changes.
    """
    pod_col = [c for c in ("pod_name", "pod") if c in df_pj.columns][:1]
    keys = ["target", "iteration", "zone", *pod_col]

    increments = counter_increments(df_pj, keys, alias="joules")

    return (
        increments.group_by(["target", "iteration", "normalized_time"])
        .agg([
            pl.col("joules").filter(pl.col("zone") == "package").sum().alias("package"),
            pl.col("joules").filter(pl.col("zone") == "dram").sum().alias("dram"),
        ])
        .with_columns((pl.col("package") + pl.col("dram")).alias("total"))
        .sort(["target", "iteration", "normalized_time"])
    )


@app.function
def pareto_frontier(
    df: pl.DataFrame,
    x: str,
    y: str,
    maximize_x: bool = True,
    minimize_y: bool = True,
) -> pl.DataFrame:
    """
    Non-dominated rows for a two-objective trade-off: a target is on the frontier
    when no other target beats it on one axis without losing on the other.
    """
    rows = df.sort(x, descending=maximize_x).to_dicts()
    frontier, best = [], None
    for row in rows:
        value = row[y]
        if value is None:
            continue
        if best is None or (value < best if minimize_y else value > best):
            frontier.append(row)
            best = value
    return pl.DataFrame(frontier).sort(x)


@app.function
def thesis_chart(chart, title: str | None = None):
    """Print-friendly styling for export: white background, readable type, light grid."""
    styled = (
        chart.configure_view(strokeWidth=0)
        .configure_axis(labelFontSize=11, titleFontSize=12, grid=True, gridOpacity=0.25)
        .configure_legend(labelFontSize=11, titleFontSize=12)
        .configure_title(fontSize=14, subtitleFontSize=11, anchor="start")
        .configure_header(labelFontSize=11, titleFontSize=12)
        .properties(background="white")
    )
    return styled.properties(title=title) if title else styled


@app.function
def save_chart(
    chart,
    name: str,
    formats: tuple = ("svg", "png"),
    directory: Path = FIGURE_DIR,
    scale_factor: float = 2.0,
    style: bool = True,
) -> list:
    """
    Render one Altair chart to disk for inclusion in the thesis and return the
    written paths. SVG is what Typst `image()` wants; PNG is the fallback.
    Requires vl-convert.
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


@app.function
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


@app.function
def export_all(
    charts: dict | None = None,
    tables: dict | None = None,
    formats: tuple = ("svg", "png"),
) -> pl.DataFrame:
    """
    Bulk-export named charts and tables; returns a manifest of what was written
    so a notebook shows exactly which files the thesis build will pick up.
    """
    rows = []
    for name, chart in (charts or {}).items():
        for path in save_chart(chart, name, formats=formats):
            rows.append({"kind": "figure", "name": name, "path": str(path)})
    for name, df in (tables or {}).items():
        for path in export_table(df, name):
            rows.append({"kind": "table", "name": name, "path": str(path)})
    return pl.DataFrame(
        rows, schema={"kind": pl.String, "name": pl.String, "path": pl.String}
    )


if __name__ == "__main__":
    app.run()
