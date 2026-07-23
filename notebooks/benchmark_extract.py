import marimo

__generated_with = "0.23.14"
app = marimo.App(width="medium")


@app.cell
def _():
    import json
    from datetime import datetime, timezone, timedelta
    import altair as alt
    import httpx
    import polars as pl
    from polars import DataFrame

    return DataFrame, alt, datetime, httpx, json, pl, timedelta, timezone


@app.cell
def _():
    endpoint = "http://hetzner-vm:8428"
    jsonl_file = "../k6/.output/benchmark_runs.jsonl"
    step = "100ms"
    return endpoint, jsonl_file, step


@app.cell
def _(DataFrame, datetime, httpx, json, pl, timezone):
    def read_jsonl(path: str) -> DataFrame:
        rows = []

        with open(path) as f:
            for line in f:
                rows.append(json.loads(line))

        df = DataFrame(rows)

        df = (
            df.with_columns(
                pl.col("StartTime")
                .str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ")
                .dt.convert_time_zone("UTC")
                .alias("StartTime"),

                pl.col("EndTime")
                .str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ")
                .dt.convert_time_zone("UTC")
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
    ) -> DataFrame:

        url = endpoint.rstrip("/") + "/api/v1/query_range"

        params = {
            "query": query,
            "start": int(start.timestamp()),
            "end": int(end.timestamp()),
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
            return DataFrame(
                {
                    "timestamp": [],
                    "value": [],
                }
            )

        return (
            DataFrame(rows)
            .with_columns(
                pl.col("timestamp").cast(
                    pl.Datetime()
                )
            )
            .sort("timestamp")
        )

    return query_range, read_jsonl


@app.cell
def _():
    metrics = {
        'pod_joules': 'kepler_pod_cpu_joules_total{pod_namespace="football",pod_name=~"football-app-.*"}',
        'pods': 'kube_deployment_status_replicas_available{deployment="football-app", namespace="football"}',
        'requests': 'k6_http_reqs_total',
        'iterations': 'k6_iterations_total',
        'vus': 'k6_vus',
        'p95': 'histogram_quantile(0.95, sum(k6_http_req_duration_seconds_bucket) by (vmrange))',
        'p99': 'histogram_quantile(0.99, sum(k6_http_req_duration_seconds_bucket) by (vmrange))',
        'rps': 'rate(k6_http_req_duration_seconds_count)',
        'memory': 'pod_memory_working_set_bytes{pod=~"football-app.*"} / 1024 / 1024',
        'checks_rate': 'k6_checks_rate',
        'cpu_usage': 'rate(pod_cpu_usage_seconds_total{pod=~"football-app.*"}) / count(node_cpu_seconds_total{mode="idle"}) * 100',
    }
    return (metrics,)


@app.cell
def _():
    scenario_ignored_metrics: dict[str, set[str]] = {
        "coldstart": {"pods", "requests", "iterations", "vus", "rps", "memory", "checks_rate", "cpu_usage"},
        "baseline":  {"pods"},
    }
    return (scenario_ignored_metrics,)


@app.cell
def _(jsonl_file, read_jsonl):
    # Read the benchmark runs from JSONL
    df_runs = read_jsonl(jsonl_file)
    df_runs
    return (df_runs,)


@app.cell
def _(
    datetime,
    df_runs,
    endpoint,
    metrics,
    query_range,
    scenario_ignored_metrics: dict[str, set[str]],
    step,
    timedelta,
):
    import os

    # Create output directory for parquet files
    output_dir = "./parquet"
    os.makedirs(output_dir, exist_ok=True)

    print(f"Found {len(df_runs)} runs to process...")
    print(f"Metrics to query: {list(metrics.keys())}")
    print()

    # Process each run
    for run in df_runs.iter_rows(named=True):
        runtime = run["Runtime"]
        framework = run["Framework"]
        scenario = run["Scenario"]
        iteration = run["Iteration"]
        if scenario != "coldstart":
            continue

        benchmark_delta = timedelta(seconds=5)
        start_time: datetime = run["StartTime"] - (timedelta(seconds=0) if scenario == "coldstart" else benchmark_delta)
        end_time: datetime = run["EndTime"] + (timedelta(seconds=10) if scenario == "coldstart" else benchmark_delta)

        print(f"Processing: {framework}/{runtime}/{scenario} Iteration: {iteration}")
        print(f" Time range: {start_time} to {end_time}")

        # Query metrics for this run, skipping scenario-irrelevant ones
        ignored = scenario_ignored_metrics.get(scenario, set())
        for metric_name, promql_query in metrics.items():
            if metric_name in ignored:
                print(f"  Skipping {metric_name} (irrelevant for '{scenario}')")
                continue
            try:
                df_metric = query_range(
                    endpoint=endpoint,
                    query=promql_query,
                    start=start_time,
                    end=end_time,
                    step=step
                )

                # Create nested directory structure: runtime-framework/scenario/
                sub_dir = f"{runtime}-{framework}"
                scenario_dir = os.path.join(output_dir, sub_dir, scenario)
                os.makedirs(scenario_dir, exist_ok=True)

                # Create filename for parquet
                filename = f"{metric_name}_{iteration}.parquet"
                filepath = os.path.join(scenario_dir, filename)

                # Export to parquet
                df_metric.write_parquet(filepath)
                print(f"Saved: {filepath} ({len(df_metric)} rows)")

            except Exception as e:
                print(f"  Error querying {metric_name}: {e}")

        print()

    print(f"Processing complete! Files saved to: {output_dir}")
    return


@app.cell
def _(pl):
    test_df = pl.read_parquet("parquet/oci-axum/baseline/pod_joules_1.parquet")
    test_df
    return (test_df,)


@app.cell
def _(alt, test_df):
    fig = (
        alt.Chart(test_df)
        .mark_point(size=80)
        .encode(
            x=alt.X("timestamp:T", title="Time", axis=alt.Axis(format="%H:%M:%S")),
            y=alt.Y("value:Q", title="Pod Joules", axis=alt.Axis(format=".2f")),
            color=alt.Color("zone:N", title="Zone"),
            tooltip=["timestamp:T", "value:Q", "zone:N"],
        )
        .properties(
            title="Pod CPU Joules by Zone (Scatter Plot)",
            width=800,
            height=400,
        )
        .interactive()
    )

    fig
    return


if __name__ == "__main__":
    app.run()
