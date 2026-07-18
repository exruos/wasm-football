import marimo

__generated_with = "0.23.13"
app = marimo.App(width="medium")


@app.cell
def _():
    import json
    from datetime import datetime, timezone
    import altair as alt
    import httpx
    import marimo as mo
    import polars as pl

    return datetime, httpx, json, pl, timezone


@app.cell
def _():
    endpoint = "http://hetzner-vm:8428"
    jsonl_file = "..\\k6\\.output\\benchmark_runs.jsonl"
    step = "1s"
    return


@app.cell
def _(datetime, httpx, json, pl, timezone):
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

    return


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
    return


if __name__ == "__main__":
    app.run()
