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

    alt.data_transformers.disable_max_rows()
    return datetime, httpx, json, mo, pl, timezone


@app.cell
def _(mo):
    endpoint = mo.ui.text(
        value="http://localhost:8428",
        label="VictoriaMetrics endpoint",
        full_width=True,
    )

    jsonl_file = mo.ui.text(
        value="benchmarks.jsonl",
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
def _(mo):

    metrics = mo.ui.text_area(
        value="""{
      "cpu_joules": "kepler_pod_cpu_joules_total{pod_namespace=\\"football\\",pod_name=~\\"football-app-.*\\"}",
      "cpu_frequency": "node_cpu_scaling_frequency_hertz",
      "pods": "sum(kube_pod_container_status_running{pod=~\\"football-app-.*\\"})",
      "k6_requests": "sum(rate(k6_http_reqs_total[30s]))"
    }""",
        label="Metric dictionary (JSON)",
        rows=10,
        full_width=True,
    )

    metrics
    return (metrics,)


@app.cell
def _(datetime, httpx, json, pl, timezone):

    def parse_metrics(text: str):
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


    def dt_to_unix(dt):
        return dt.timestamp()


    def query_range(
        endpoint: str,
        query: str,
        start,
        end,
        step: str = "5s",
    ) -> pl.DataFrame:

        url = endpoint.rstrip("/") + "/api/v1/query_range"

        params = {
            "query": query,
            "start": start,
            "end": end,
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


    return parse_metrics, query_range, read_jsonl


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
            pl.min("StartTime").alias("First Run"),
            pl.max("EndTime").alias("Last Run"),
        )
        .sort(
            [
                "Runtime",
                "Framework",
            ]
        )
    )

    overview
    return


@app.cell
def _(benchmark_df: "pl.DataFrame"):

    benchmark_start = benchmark_df["StartTime"].min()
    benchmark_end = benchmark_df["EndTime"].max()

    benchmark_start, benchmark_end
    return benchmark_end, benchmark_start


@app.cell
def _(
    benchmark_end,
    benchmark_start,
    endpoint,
    metrics,
    mo,
    parse_metrics,
    query_range,
    step,
):

    mo.lazy
    metric_queries = parse_metrics(metrics.value)

    metric_frames = {}

    for name, promql in metric_queries.items():

        print(f"Downloading {name}...")

        metric_frames[name] = query_range(
            endpoint.value,
            promql,
            benchmark_start,
            benchmark_end,
            step.value,
        )

    metric_frames
    return


if __name__ == "__main__":
    app.run()
