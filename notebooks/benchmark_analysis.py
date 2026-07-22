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
    import glob

    return glob, pl


@app.cell
def _(glob, pl):
    def load_metric_data(metric: str, scenario: str, time_col: str = "timestamp") -> pl.DataFrame:
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

        return (
            pl.scan_parquet(files, include_file_paths="full_path", missing_columns="insert", extra_columns="ignore")
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

    return (load_metric_data,)


@app.cell
def _(load_metric_data, pl):
    metrics = [
        "pod_joules",
        "pods",
        "requests",
        "iterations",
        "vus",
        "p95",
        "p99",
        "rps",
        "memory",
        "cpu_usage",
    ]

    scenarios = ["baseline", "coldstart", "scaling"]

    # Load all metric/scenario combinations into a dictionary
    df_all_metrics = {}
    for metric in metrics:
        for scenario in scenarios:
            try:
                df_all_metrics[(metric, scenario)] = load_metric_data(
                    metric=metric,
                    scenario=scenario,
                    time_col="timestamp",
                )
            except Exception as e:
                print(f"Failed to load {metric} for {scenario}: {e}")

    # Display summary of what was loaded
    loaded_summary = pl.DataFrame([
        {"metric": k[0], "scenario": k[1], "rows": len(v), "columns": len(v.columns)}
        for k, v in df_all_metrics.items()
    ])
    loaded_summary
    return (df_all_metrics,)


@app.cell
def _(pl):
    def build_scenario_table(df_all_metrics: dict, scenario: str) -> pl.DataFrame:
                """Build a wide DataFrame for a scenario by joining all metric DataFrames on dir_name + iteration.

                Each metric may have multiple timestamped rows per (dir_name, iteration).
                We aggregate to one row per (dir_name, iteration) using max for cumulative
                counters (requests, iterations, vus, pods) and mean for sampled metrics
                (p95, p99, rps, memory, cpu_usage, pod_joules).
                """
                cumulative_counters = {"requests", "iterations", "vus", "pods", "pod_joules"}
                scenario_keys = [k for k in df_all_metrics if k[1] == scenario and len(df_all_metrics[k]) > 0]
                if not scenario_keys:
                    return pl.DataFrame()

                # Start with the first metric's dir_name and iteration
                base = df_all_metrics[scenario_keys[0]].select(["dir_name", "iteration"]).unique()

                # Aggregate each metric to one row per (dir_name, iteration), then join
                tables = []
                for metric, _ in scenario_keys:
                    df = df_all_metrics[(metric, scenario)]
                    if "value" in df.columns:
                        agg_fn = pl.col("value").max() if metric in cumulative_counters else pl.col("value").mean()
                        tables.append(
                            df.group_by(["dir_name", "iteration"])
                              .agg(agg_fn.alias(metric))
                              .unique(subset=["dir_name", "iteration"])
                        )
                    else:
                        tables.append(
                            df.select(["dir_name", "iteration"])
                              .unique(subset=["dir_name", "iteration"])
                              .with_columns(pl.lit(0.0).alias(metric))
                        )

                result = base
                for t in tables:
                    result = result.join(t, on=["dir_name", "iteration"], how="left")
                return result

    return (build_scenario_table,)


@app.cell
def _(df_all_metrics):
    df_all_metrics
    return


@app.cell
def _(build_scenario_table, df_all_metrics, pl):
    # --- Compute all metrics for baseline scenario ---
    df_baseline = build_scenario_table(df_all_metrics, "baseline")

    if len(df_baseline) > 0:
        # Latency & Responsiveness metrics
        df_latency_baseline = df_baseline.group_by("dir_name").agg([
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
        ])


        # Throughput metrics
        # requests is a global cumulative counter (shared across dir_names).
        # total_requests = the final cumulative value (max across all iterations).
        df_throughput_baseline = df_baseline.group_by("dir_name").agg([
            pl.col("rps").mean().alias("mean_rps"),
            pl.col("rps").max().alias("max_rps"),
            pl.col("rps").min().alias("min_rps"),
            # requests is a global cumulative counter (shared across dir_names).
                # total_requests = the final cumulative value (max across all iterations).
                pl.col("requests").max().alias("total_requests"),
        ])

        # Resource metrics (CPU/Memory)
        df_resource_baseline = df_baseline.group_by("dir_name").agg([
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
        df_raw_joules_baseline = df_all_metrics[("pod_joules", "baseline")]

        # 1. Compute net energy per (dir_name, iteration, zone)
        df_iter_zone_baseline = df_raw_joules_baseline.group_by(["dir_name", "iteration", "zone"]).agg(
            (pl.col("value").max() - pl.col("value").min()).alias("iter_joules")
        )

        # 2. Sum across iterations per zone, then pivot zones into separate columns
        df_energy_pivoted_baseline = (
            df_iter_zone_baseline.group_by(["dir_name", "zone"])
            .agg(pl.col("iter_joules").sum())
            .pivot(on="zone", index="dir_name", values="iter_joules")
        )

        # 3. Join back and calculate package, dram, total, and per-request metrics
        df_energy_baseline = (
            df_throughput_baseline.join(df_energy_pivoted_baseline, on="dir_name", how="left")
            .with_columns(
                (pl.col("package") + pl.col("dram")).alias("total_joules")
            )
            .select([
                "dir_name",
                pl.col("package").alias("cpu_joules"),
                pl.col("dram").alias("dram_joules"),
                "total_joules",
                (pl.col("total_joules") / pl.col("total_requests")).alias("joules_per_request"),
                (pl.col("total_requests") / pl.col("total_joules")).alias("requests_per_joule"),
            ])
        )

        # Combine all metrics into a single summary table
        df_metrics_baseline = df_latency_baseline.join(
            df_throughput_baseline, on=["dir_name"], how="left"
        ).join(
            df_resource_baseline, on=["dir_name"], how="left"
        ).join(
            df_energy_baseline, on=["dir_name"], how="left"
        ).sort(["dir_name"]).unique()
    else:
        df_metrics_baseline = pl.DataFrame()

    df_metrics_baseline
    return


@app.cell
def _(build_scenario_table, df_all_metrics, pl):
    # --- Compute all metrics for coldstart scenario ---
    df_coldstart = build_scenario_table(df_all_metrics, "coldstart")

    if len(df_coldstart) > 0:
        # Latency & Responsiveness metrics - this is the time to first response
        df_latency_coldstart = df_coldstart.group_by("dir_name").agg([
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
        ])


        # Energy metrics: use raw pod_joules from df_all_metrics, not df_coldstart
        # (df_coldstart already has pod_joules aggregated as max per iteration,
        #  so max - min would always be 0)
        df_raw_joules_coldstart = df_all_metrics[("pod_joules", "coldstart")]

        # 1. Compute net energy per (dir_name, iteration, zone)
        df_iter_zone_coldstart = df_raw_joules_coldstart.group_by(["dir_name", "iteration", "zone"]).agg(
            (pl.col("value").max() - pl.col("value").min()).alias("iter_joules")
        )

        # 2. Sum across iterations per zone, then pivot zones into separate columns
        df_energy_pivoted_coldstart = (
            df_iter_zone_coldstart.group_by(["dir_name", "zone"])
            .agg(pl.col("iter_joules").sum())
            .pivot(on="zone", index="dir_name", values="iter_joules")
        )

        # 3. Join back and calculate package, dram, and total joules
        df_energy_coldstart = (
            df_latency_coldstart.join(df_energy_pivoted_coldstart, on="dir_name", how="left")
            .with_columns(
                (pl.col("package") + pl.col("dram")).alias("total_joules")
            )
            .select([
                "dir_name",
                pl.col("package").alias("cpu_joules"),
                pl.col("dram").alias("dram_joules"),
                "total_joules",
            ])
        )

        # Combine all metrics into a single summary table
        df_metrics_coldstart = df_latency_coldstart.join(
            df_energy_coldstart, on=["dir_name"], how="left"
        ).sort(["dir_name"]).unique()
    else:
        df_metrics_coldstart = pl.DataFrame()

    df_metrics_coldstart
    return


if __name__ == "__main__":
    app.run()
