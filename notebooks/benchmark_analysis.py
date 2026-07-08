import marimo

__generated_with = "0.23.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    import altair as alt
    import httpx
    import marimo as mo
    import numpy as np
    import polars as pl
    import polars.selectors as cs

    alt.data_transformers.disable_max_rows()
    return alt, cs, datetime, httpx, json, mo, pl, timezone


@app.cell
def _(mo):
    endpoint = mo.ui.text(
        value="http://hetzner-vm:8428",
        label="VictoriaMetrics endpoint",
        full_width=True,
    )

    jsonl_file = mo.ui.text(
        value="..\\k6\\.output\\benchmark_runs.jsonl",
        label="Benchmark JSONL",
        full_width=True,
    )

    step = mo.ui.text(
        value="5s",
        label="Prometheus query step",
    )

    mo.vstack(
        [
            endpoint,
            jsonl_file,
            step,
        ]
    )
    return endpoint, jsonl_file, step


@app.cell
def _(datetime, httpx, json, pl, timezone):

    def parse_metrics(text: str) -> dict[str, str]:
        return json.loads(text)


    def read_jsonl(path: str) -> pl.DataFrame:
        rows = []

        with open(path) as f:
            for line in f:
                rows.append(json.loads(line))

        df = pl.DataFrame(rows)

        df = (
            df.with_columns(
                pl.col("StartTime")
                .str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ")
                .alias("StartTime"),

                pl.col("EndTime")
                .str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ")
                .alias("EndTime"),
            )
            .with_columns(
                (
                    pl.col("EndTime") - pl.col("StartTime")
                ).dt.total_seconds().alias("DurationSeconds")
            )
            .sort(
                [
                    "Runtime",
                    "Framework",
                    "Scenario",
                    "Iteration",
                ]
            )
        )

        return df


    def query_range(
        endpoint: str,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "1s",
    ) -> pl.DataFrame:

        url = endpoint.rstrip("/") + "/api/v1/query_range"

        params = {
            "query": query,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "step": step,
        }

        response = httpx.get(
            url,
            params=params,
            timeout=120,
        )

        response.raise_for_status()

        payload = response.json()

        rows = []

        for series in payload["data"]["result"]:

            labels = series["metric"]

            for ts, value in series["values"]:

                row = {
                    "timestamp": datetime.fromtimestamp(
                        float(ts), timezone.utc
                    ),
                    "value": float(value),
                }

                row.update(labels)

                rows.append(row)

        if not rows:
            return pl.DataFrame(
                {
                    "timestamp": [],
                    "value": [],
                }
            )

        return (
            pl.DataFrame(rows)
            .with_columns(
                pl.col("timestamp").cast(
                    pl.Datetime()
                )
            )
            .sort("timestamp")
        )


    return query_range, read_jsonl


@app.cell
def _(jsonl_file, mo, pl, read_jsonl):

    try:
        benchmark_df: pl.DataFrame = read_jsonl(jsonl_file.value)

        mo.callout(
            f"Loaded {len(benchmark_df)} benchmark iterations.",
            kind="success",
        )

    except Exception as exc:

        mo.callout(
            str(exc),
            kind="danger",
        )
    return (benchmark_df,)


@app.cell
def _(benchmark_df: "pl.DataFrame", mo):

    if benchmark_df is None:
        mo.stop(True)

    benchmark_df
    return


@app.cell
def _(benchmark_df: "pl.DataFrame", pl):
    overview = (
        benchmark_df.group_by(
            [
                "Runtime",
                "Framework",
                "Scenario",
            ]
        )
        .agg(
            pl.len().alias("Iterations"),
            pl.mean("DurationSeconds").alias(
                "Mean Duration (s)"
            ),
            pl.std("DurationSeconds").alias(
                "Std Duration (s)"
            ),
            pl.median("DurationSeconds").alias(
                "Median Duration (s)"
            ),
            pl.quantile("DurationSeconds", 0.25).alias(
                "Q1 Duration (s)"
            ),
            pl.quantile("DurationSeconds", 0.75).alias(
                "Q3 Duration (s)"
            ),
        )
        .sort(
            [
                "Runtime",
                "Framework",
            ]
        )
    )

    overview
    return (overview,)


@app.cell
def _(alt, overview):
    # Convert Polars DataFrame directly to a list of row dictionaries
    overview_dict = overview.to_dicts()

    chart = (
        alt.Chart(alt.Data(values=overview_dict))
        .mark_bar()
        .encode(
            # X-axis now tracks the duration (Length of horizontal bars)
            x=alt.X("Mean Duration (s):Q", title="Mean Duration (seconds)"),
        
            # Y-axis tracks the Frameworks, sorted descending based on the X-axis value (-x)
            y=alt.Y("Framework:N", sort="-x", title=None),
        
            # Color distinguishes the underlying Runtimes
            color=alt.Color("Runtime:N", scale=alt.Scale(scheme="tableau10"), title="Runtime"),
        
            # Row facets split the chart vertically for each Scenario
            row=alt.Row("Scenario:N", title="Scenario Benchmark")
        )
        .properties(
            width=400,
            height=120, # Shorter height per row since horizontal bars stack vertically
            title="Performance Comparison: Mean Duration by Scenario"
        )
        .configure_title(
            anchor="middle",
            fontSize=14,
            offset=20
        )
    )

    chart
    return


@app.cell
def _(mo):
    metrics = {
        "cpu_joules": 'kepler_pod_cpu_joules_total{pod_namespace="football",pod_name=~"football-app-.*"}',
        "cpu_frequency": "node_cpu_scaling_frequency_hertz",
        "pods": 'sum(kube_pod_container_status_running{pod=~"football-app-.*"})',
        "k6_requests": "sum(rate(k6_http_reqs_total[30s]))",
    }

    metric_selector = mo.ui.multiselect(
        options=list(metrics.keys()),
        value=list(metrics.keys()),  # Default to all selected
        label="Select metrics to include:",
    )

    metric_selector
    return metric_selector, metrics


@app.cell
def _(
    benchmark_df: "pl.DataFrame",
    datetime,
    endpoint,
    metric_selector,
    metrics,
    mo,
    pl,
    query_range,
    step,
    timezone,
):
    from collections import defaultdict
    combined_results = {}

    selected_metrics = {
        key: metrics[key] for key in metric_selector.value  # ty:ignore[invalid-argument-type]
    }

    try:
        for metric_name, promql in selected_metrics.items():
            per_group = defaultdict(list)

            for row in benchmark_df.to_dicts():
                start = row["StartTime"]
                end = row["EndTime"]

                # ensure datetimes are timezone-aware UTC for query_range
                if isinstance(start, datetime) and start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                if isinstance(end, datetime) and end.tzinfo is None:
                   end = end.replace(tzinfo=timezone.utc)
                df_run = query_range(
                    endpoint.value,
                    promql,
                    start,
                    end,
                    step=step.value,
                )
                # annotate run
                df_run = df_run.with_columns(
                    pl.lit(row["Runtime"]).alias("Runtime"),
                    pl.lit(row["Framework"]).alias("Framework"),
                    pl.lit(row["Scenario"]).alias("Scenario"),
                    pl.lit(row.get("Iteration")).alias("Iteration"),
                    pl.lit(metric_name).alias("Metric"),
                )

                key = (row["Runtime"], row["Framework"], row["Scenario"])
                per_group[key].append(df_run)

            # concat runs per group
            for key, dfs in per_group.items():
                if dfs:
                    per_group[key] = pl.concat(dfs, how="vertical").sort(
                        "timestamp"
                    )
                else:
                    per_group[key] = pl.DataFrame(
                        {"timestamp": [], "value": []}
                    )

            combined_results[metric_name] = per_group

        mo.callout(
            f"Queried VictoriaMetrics for {len(selected_metrics)} metrics across {len(benchmark_df)} iterations.",
            kind="success",
        )

    except Exception as exc:
        mo.callout(str(exc), kind="danger")
        combined_results = {}

    combined_results
    return (combined_results,)


@app.cell
def _(combined_results, cs, mo, pl):
    cpu_joules = combined_results.get("cpu_joules")

    if not cpu_joules:
        mo.stop(True)
        raise ValueError("CPU Joules data is not available.")

    def _add_zone_column(df: pl.DataFrame) -> pl.DataFrame:
        if "zone" in df.columns:
            return df.with_columns(pl.col("zone").alias("Zone"))
        return df.with_columns(pl.lit("unknown").alias("Zone"))


    per_iteration_joules = (
        pl.concat(
            [
                _add_zone_column(
                    df.with_columns(
                        pl.lit(runtime).alias("Runtime"),
                        pl.lit(framework).alias("Framework"),
                        pl.lit(scenario).alias("Scenario"),
                    )
                )
                for (runtime, framework, scenario), df in cpu_joules.items()
                if len(df) > 0
            ],
            how="vertical",
        )
        .group_by(["Runtime", "Framework", "Scenario", "Iteration", "Zone"])
        .agg(
            pl.min("value").alias("Start Joules"),
            pl.max("value").alias("End Joules"),
            (pl.max("value") - pl.min("value")).alias("Total Joules"),
        )
        .sort(["Runtime", "Framework", "Scenario", "Iteration", "Zone"])
    )

    joules_by_zone_overview = (
        per_iteration_joules
        .group_by(["Runtime", "Framework", "Scenario", "Zone"])
        .agg(
            pl.len().alias("Iterations"),
            pl.mean("Total Joules").alias("Average Joules"),
            pl.std("Total Joules").alias("Deviation Joules"),
            pl.median("Total Joules").alias("Median Joules"),
            pl.sum("Total Joules").alias("Total Joules"),
        )
        .sort(["Runtime", "Framework", "Scenario", "Zone"])
        .with_columns(cs.float().round(2))
    )

    joules_overall = (
        per_iteration_joules
        .group_by(["Runtime", "Framework", "Scenario", "Iteration"])
        .agg(pl.sum("Total Joules").alias("Iteration Joules"))

        .group_by(["Runtime", "Framework", "Scenario"])
        .agg(
            pl.len().alias("Iterations"),
            pl.mean("Iteration Joules").alias("Avg Joules"),
            pl.std("Iteration Joules").alias("Std Dev Joules"),
            pl.median("Iteration Joules").alias("Median Joules"),
        )
        .with_columns(
            pl.format(
                "{} ± {}", 
                pl.col("Avg Joules").round(2), 
                pl.col("Std Dev Joules").round(2)
            ).alias("Energy (Avg ± StdDev)")
        )
        .sort(["Runtime", "Framework", "Scenario"])
        .with_columns(cs.float().round(2))
    )

    (joules_by_zone_overview, joules_overall)
    return (per_iteration_joules,)


@app.cell
def _(alt, mo, per_iteration_joules, pl):
    if "per_iteration_joules" not in globals():
        mo.stop(True)

    group_cols = ["Runtime", "Framework", "Scenario"]

    # Per-iteration totals across zones
    total_by_iteration = (
        per_iteration_joules
        .group_by(group_cols + ["Iteration"])
        .agg(pl.sum("Total Joules").alias("Joules"))
        .with_columns(pl.lit("Total").alias("Metric"))
    )

    # Per-iteration zone totals
    zone_by_iteration = (
        per_iteration_joules
        .filter(pl.col("Zone").is_in(["dram", "package"]))
        .group_by(group_cols + ["Iteration", "Zone"])
        .agg(pl.sum("Total Joules").alias("Joules"))
        .rename({"Zone": "Metric"})
    )

    plot_df = (
        pl.concat([total_by_iteration, zone_by_iteration], how="diagonal")
        .group_by(group_cols + ["Metric"])
        .agg(
            pl.mean("Joules").alias("Mean Joules"),
            pl.std("Joules").alias("StdDev Joules"),
        )
        .with_columns(
            pl.concat_str(
                [
                    pl.col("Runtime"),
                    pl.col("Framework"),
                    pl.col("Scenario"),
                ],
                separator=" / ",
            ).alias("Group")
        )
        .sort(group_cols + ["Metric"])
    )

    chart_data = plot_df

    base = alt.Chart(chart_data).properties(width=500, height=400)

    # 1. Horizontal Bars
    bars = (
        base.mark_bar()
        .encode(
            x=alt.X("Mean Joules:Q", title="Joules"),
            # Group first by 'Group', then by 'Metric' inside the same Y axis
            y=alt.Y("Group:N", title="Scenario / Framework / Runtime"),
            # y=alt.Y(
            #     "Group:N", 
            #     title="Scenario / Framework / Runtime",
            #     sort=alt.EncodingSortField(field="Mean Joules", op="sum", order="descending")
            # ),
            yOffset=alt.YOffset("Metric:N", sort=["Total", "package", "dram"]), 
            color=alt.Color("Metric:N", title="Metric Metric", sort=["Total", "package", "dram"]),
            tooltip=[
                "Runtime:N",
                "Framework:N",
                "Scenario:N",
                "Metric:N",
                alt.Tooltip("Mean Joules:Q", format=".2f"),
                alt.Tooltip("StdDev Joules:Q", format=".2f"),
            ],
        )
    )

    # 2. Horizontal Error Bars
    error_bars = (
        base.mark_errorbar()
        .encode(
            x=alt.X("Mean Joules:Q"),
            xError=alt.XError("StdDev Joules:Q"),
            y=alt.Y("Group:N"),
            yOffset=alt.YOffset("Metric:N", sort=["Total", "package", "dram"]),
        )
    )

    # Layer them together into a single chart grid
    final_chart = alt.layer(bars, error_bars)

    final_chart
    return


if __name__ == "__main__":
    app.run()
