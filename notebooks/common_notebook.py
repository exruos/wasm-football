import marimo

__generated_with = "0.23.15"
app = marimo.App(width="medium")

with app.setup:
    import glob

    import altair as alt
    import polars as pl


@app.function
def load_metric_data(metric: str, scenario: str, time_col: str = "timestamp", select_columns: list[str] | None = None) -> pl.DataFrame:
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
    targets = [
        "oci-axum",
        "wasm-js",
        "oci-spring",
        "oci-native",
        "oci-node",
        "wasm-rust",
    ]
    palette = [
        "#D34516",
        "#FC7C00",
        "#6DB33F",
        "#00758F",
        "#1B661B",
        "#654FF0",
    ]

    # Create an Altair Scale object to reuse across charts
    return alt.Scale(domain=targets, range=palette)


if __name__ == "__main__":
    app.run()
