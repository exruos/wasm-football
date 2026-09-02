import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")

with app.setup:
    import json
    import urllib.parse
    import urllib.request
    from datetime import datetime, timezone
    from pathlib import Path

    import altair as alt
    import marimo as mo
    import polars as pl

    from common_notebook import (
        export_all,
        target_color,
        target_rank,
    )


@app.cell
def md_intro():
    mo.md(r"""
    # Client-to-server round-trip time

    Load is generated from a workstation outside the benchmark network, so every
    response time carries a fixed network offset. Measured from the archived
    benchmark data rather than from a `ping` taken afterwards.

    - **Why `http_req_connecting`.** k6 records the TCP handshake separately, and
      a handshake is exactly one round trip. It is measured by the load generator
      itself, over the real path, at the time of the runs, so it survives the
      benchmark host being torn down and needs no assumption that ICMP and HTTP
      take the same route. Routers routinely deprioritise ICMP, and a `ping` also
      stops at the host and misses the ingress hop.
    - **Why the `coldstart` scenario only.** k6 reuses keep-alive connections, so
      in `baseline` and `scaling` this metric is 0 for almost every request - a
      pile of zeros with no usable centre. Every `coldstart` repetition is a
      separate k6 process issuing a single request, so each contributes exactly
      one fresh handshake: 30 repetitions x 6 targets = 180 clean samples.
    - The handshake terminates at Traefik / the KEDA interceptor, both already
      running when the request is issued, so pod start-up does not inflate it.
    """)
    return


@app.cell
def config():
    VM = "http://hetzner-vm:8428"
    RUNS_JSONL = Path("../k6/.output/benchmark_runs.jsonl")

    # The benchmark hosts are not always up. Raw samples are cached on the first
    # successful fetch so the notebook opens and re-runs offline afterwards; the
    # numbers it publishes must not depend on a machine being switched on.
    RAW_CACHE = Path("parquet") / "rtt_raw.parquet"

    # k6 flushes its final remote-write batch as the process exits, which can land
    # after the pipeline recorded EndTime. Samples are therefore attributed to the
    # run that was in flight when they were produced, with this much slack.
    ATTRIBUTION_SLACK_S = 120.0

    METRICS = (
        "k6_http_req_connecting_seconds_sum",
        "k6_http_req_connecting_seconds_count",
        "k6_http_req_tls_handshaking_seconds_sum",
    )
    return ATTRIBUTION_SLACK_S, METRICS, RAW_CACHE, RUNS_JSONL, VM


@app.cell
def helpers(VM):
    def ts(s: str) -> float:
        return (
            datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )

    def export_series(metric: str, start: float, end: float) -> list[tuple[float, float]]:
        """
        Raw samples via /api/v1/export.

        Deliberately not query_range: that interpolates and carries values
        forward across the gaps between runs, which would smear one run's
        handshake into the next run's window.
        """
        q = urllib.parse.urlencode({
            "match[]": f'{{__name__="{metric}",scenario="coldstart"}}',
            "start": f"{start:.0f}",
            "end": f"{end:.0f}",
        })
        out: list[tuple[float, float]] = []
        with urllib.request.urlopen(f"{VM}/api/v1/export?{q}", timeout=180) as r:
            for line in r.read().decode().splitlines():
                if line.strip():
                    d = json.loads(line)
                    out += list(zip([t / 1000 for t in d["timestamps"]], d["values"]))
        return sorted(out)

    return export_series, ts


@app.cell
def load_runs(RUNS_JSONL, ts):
    _rows = [
        json.loads(line)
        for line in RUNS_JSONL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    coldstart_runs = sorted(
        (r for r in _rows if r["Scenario"] == "coldstart"),
        key=lambda r: ts(r["StartTime"]),
    )
    mo.md(f"**{len(coldstart_runs)}** cold-start runs in the pipeline log.")
    return (coldstart_runs,)


@app.cell
def fetch(METRICS, RAW_CACHE, VM, coldstart_runs, export_series, ts):
    _lo = min(ts(r["StartTime"]) for r in coldstart_runs) - 120
    _hi = max(ts(r["EndTime"]) for r in coldstart_runs) + 120

    try:
        df_raw = pl.DataFrame(
            [
                {"metric": _m, "t": _t, "value": _v}
                for _m in METRICS
                for _t, _v in export_series(_m, _lo, _hi)
            ],
            schema={"metric": pl.Utf8, "t": pl.Float64, "value": pl.Float64},
        )
        RAW_CACHE.parent.mkdir(parents=True, exist_ok=True)
        df_raw.write_parquet(RAW_CACHE)
        raw_source = "VictoriaMetrics"
    except OSError as _err:
        if not RAW_CACHE.is_file():
            raise RuntimeError(
                f"{VM} is unreachable and no cache exists at {RAW_CACHE}. "
                "Run this notebook once while the metrics host is up."
            ) from _err
        df_raw = pl.read_parquet(RAW_CACHE)
        raw_source = f"cache at {RAW_CACHE} ({type(_err).__name__}: metrics host unreachable)"
    return df_raw, raw_source


@app.cell
def unpack(df_raw, raw_source):
    def _series(metric: str) -> list[tuple[float, float]]:
        d = df_raw.filter(pl.col("metric") == metric).sort("t")
        return list(zip(d["t"].to_list(), d["value"].to_list()))

    conn_sum = _series("k6_http_req_connecting_seconds_sum")
    conn_count = dict(_series("k6_http_req_connecting_seconds_count"))
    tls_sum = [v for _, v in _series("k6_http_req_tls_handshaking_seconds_sum")]

    mo.md(
        f"Source: **{raw_source}**. {len(conn_sum)} handshake samples. "
        f"Max TLS handshake time is **{max(tls_sum) if tls_sum else 0:g}** - "
        "zero means plain HTTP, so the figure is a full round trip with no TLS "
        "negotiation folded in."
    )
    return conn_count, conn_sum


@app.cell
def attribute(ATTRIBUTION_SLACK_S, coldstart_runs, conn_count, conn_sum, ts):
    def owner(t: float) -> dict | None:
        """The run that was in flight when a sample was produced."""
        prev = None
        for r in coldstart_runs:
            if ts(r["StartTime"]) > t:
                break
            prev = r
        return prev

    _rows = []
    for _t, _s in conn_sum:
        _run = owner(_t)
        _c = conn_count.get(_t)
        if _run is None or not _c or _t - ts(_run["EndTime"]) > ATTRIBUTION_SLACK_S:
            continue
        _rows.append({
            "target": f"{_run['Runtime']}-{_run['Framework']}",
            "iteration": _run["Iteration"],
            "requests": _c,
            "rtt_ms": _s / _c * 1000.0,
        })

    df_rtt = pl.DataFrame(_rows).sort(target_rank())
    df_rtt
    return (df_rtt,)


@app.cell
def md_check(coldstart_runs, df_rtt):
    _n = df_rtt.height
    _reqs = sorted(df_rtt["requests"].unique().to_list())
    mo.md(
        f"Matched **{_n} of {len(coldstart_runs)}** runs; requests per run "
        f"observed: **{_reqs}**. A value other than `[1.0]` would mean a run "
        "opened more than one connection and the sample is no longer a single "
        "clean round trip."
    )
    return


@app.cell
def summarise(df_rtt):
    df_rtt_by_target = (
        df_rtt.group_by("target")
        .agg([
            pl.len().alias("n"),
            pl.col("rtt_ms").median().alias("median_ms"),
            pl.col("rtt_ms").mean().alias("mean_ms"),
            pl.col("rtt_ms").min().alias("min_ms"),
            pl.col("rtt_ms").max().alias("max_ms"),
        ])
        .sort(target_rank())
    )

    # Report the median, not the mean: two targets carry a single ~18 ms outlier
    # that moves their mean but not their centre.
    df_rtt_summary = pl.DataFrame([{
        "n": df_rtt.height,
        "median_ms": df_rtt["rtt_ms"].median(),
        "mean_ms": df_rtt["rtt_ms"].mean(),
        "iqr_lo_ms": df_rtt["rtt_ms"].quantile(0.25),
        "iqr_hi_ms": df_rtt["rtt_ms"].quantile(0.75),
        "min_ms": df_rtt["rtt_ms"].min(),
        "p95_ms": df_rtt["rtt_ms"].quantile(0.95),
        "max_ms": df_rtt["rtt_ms"].max(),
        "sd_ms": df_rtt["rtt_ms"].std(),
        "target_median_spread_ms": (
            df_rtt_by_target["median_ms"].max() - df_rtt_by_target["median_ms"].min()
        ),
    }])
    df_rtt_by_target
    return df_rtt_by_target, df_rtt_summary


@app.cell
def show_summary(df_rtt_summary):
    df_rtt_summary
    return


@app.cell
def chart(df_rtt, df_rtt_by_target):
    # One explicit row order for both layers. Sorting each layer by its own
    # EncodingSortField cannot work here: the medians frame has no `rtt_ms`
    # column to sort on, and a mismatch would put a tick on the wrong row.
    _order = df_rtt_by_target.sort("median_ms")["target"].to_list()

    _points = (
        alt.Chart(df_rtt)
        .mark_point(size=28, opacity=0.45, filled=True)
        .encode(
            y=alt.Y("target:N", title="Target", sort=_order),
            x=alt.X("rtt_ms:Q", title="TCP handshake, one round trip (ms)",
                    scale=alt.Scale(zero=False, nice=False, padding=12)),
            color=target_color(legend=False),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("iteration:Q", title="Run"),
                alt.Tooltip("rtt_ms:Q", title="RTT (ms)", format=".2f"),
            ],
        )
    )
    # mark_tick, not a stroke-shaped point: a point with `filled=False` takes its
    # colour from the fill channel, so the tick was drawn with no stroke and came
    # out invisible against the cloud. A tick also reads as a summary rule rather
    # than as one more sample.
    _medians = (
        alt.Chart(df_rtt_by_target)
        .mark_tick(thickness=3, size=22, opacity=1, color="black")
        .encode(
            y=alt.Y("target:N", sort=_order),
            x="median_ms:Q",
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("median_ms:Q", title="Median (ms)", format=".2f"),
            ],
        )
    )

    chart_rtt = (_points + _medians).properties(
        width=520, height=200,
        title={
            "text": "Network round-trip time, measured during the cold-start runs",
            "subtitle": "One point per run, black tick = per-target median",
        },
    )
    chart_rtt
    return (chart_rtt,)


@app.cell
def md_reading():
    mo.md(r"""
    ## Reading the result

    - The per-target medians agree to within half a millisecond, which is the
      point that matters: the offset is a property of the network path and not of
      any variant, so it cannot change their ordering.
    - It is a larger share of a fast variant's response time than of a slow
      one's, so it **compresses** the measured differences - every reported gap
      is conservative.
    - Two targets carry a single ~18 ms outlier, which is why the summary reports
      the median rather than the mean.
    """)
    return


@app.cell
def export_figures(chart_rtt, df_rtt, df_rtt_by_target, df_rtt_summary):
    export_manifest = export_all(
        charts={"rtt_by_target": chart_rtt},
        tables={
            "rtt_samples": df_rtt,
            "rtt_by_target": df_rtt_by_target,
            "rtt_summary": df_rtt_summary,
        },
    )
    export_manifest
    return


if __name__ == "__main__":
    app.run()
