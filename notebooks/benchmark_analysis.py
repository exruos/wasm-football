import marimo

__generated_with = "0.23.14"
app = marimo.App(width="medium")


@app.cell
def _():
    from datetime import datetime, timezone
    from pathlib import Path

    import altair as alt
    import marimo as mo
    import numpy as np
    import polars as pl
    import polars.selectors as cs

    return (pl,)


@app.cell
def _(pl):
    def load_metric_data(metric: str, scenario: str) -> pl.DataFrame:
        path_pattern = f"./parquet/*/{scenario}/{metric}_*.parquet"

        dir_regex = f"parquet/([^/]+)/{scenario}"
        iter_regex = r"_(\d+)\.parquet$"

        return (
            pl.scan_parquet(
                path_pattern,
                include_file_paths="full_path",
            )
            .with_columns(
                [
                    # Extract the directory name
                    pl.col("full_path").str.extract(dir_regex).alias("dir_name"),
                    # Extract the iteration number and cast it to an integer
                    pl.col("full_path")
                    .str.extract(iter_regex)
                    .cast(pl.Int32)
                    .alias("iteration"),
                ]
            )
            .drop("full_path")
            # Optional: Sort the final data cleanly by directory and iteration order
            .sort(["dir_name", "iteration"])
            .collect()
        )

    return (load_metric_data,)


@app.cell
def _(pl):
    def load_and_normalize_metric_data(metric: str, scenario: str, time_col: str = "timestamp") -> pl.DataFrame:
        path_pattern = f"./parquet/*/{scenario}/{metric}_*.parquet"
        dir_regex = f"parquet/([^/]+)/{scenario}"
        iter_regex = r"_(\d+)\.parquet$"

        return (
            pl.scan_parquet(path_pattern, include_file_paths="full_path")
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

    return (load_and_normalize_metric_data,)


@app.cell
def _(load_metric_data):
    # Load baseline p95 data
    df_p95_baseline = load_metric_data(metric="p95", scenario="baseline")

    # Load scaling p99 data
    #df_p99_scaling = load_metric_data(metric="p99", scenario="scaling")
    return


@app.cell
def _(load_and_normalize_metric_data):
    df_joules_baseline = load_and_normalize_metric_data(metric="pod_joules", scenario="baseline", time_col="timestamp")
    return (df_joules_baseline,)


@app.cell
def _(df_joules_baseline):
    df_joules_baseline
    return


if __name__ == "__main__":
    app.run()
