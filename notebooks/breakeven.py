import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")

with app.setup:
    import altair as alt
    import marimo as mo
    import polars as pl

    from common_notebook import (
        TARGET_ORDER,
        TABLE_DIR,
        export_all,
        target_color,
        target_rank,
        thesis_chart,
    )


@app.cell
def md_intro():
    mo.md(r"""
    # Break-even duty cycle

    The baseline and idle scenarios disagree about which runtime is cheapest:
    under load `oci-node` wins and `wasm-js` is 25x worse, but at rest the
    ordering inverts - `wasm-js` draws 0.004 W against `oci-spring`'s 0.351 W.
    Neither ranking is wrong; they answer different questions. This notebook
    computes the operating point at which the answer changes.

    **Model.** A variant serving `N` requests within a period `T` is busy for
    `t = N / r` seconds, where `r` is its single-pod throughput, and idle for the
    rest:

    ```
    E(T) = E_active + (T - t) * P_idle
    ```

    `E_active` is the energy of one baseline run (a fixed 100 000 requests) and
    `P_idle` the resting draw of a deployed pod doing nothing. Equating two
    variants and solving for `T` gives the period at which they cost the same;
    dividing `N` by it turns that period into an **average request rate**, which
    is the form an operator can actually act on.

    **What the model assumes.** Work is served at the target's own throughput and
    the remainder of the period is spent idle at the measured resting draw - no
    partial load, no scale-to-zero, no cold-start cost per activation. Cold start
    is deliberately excluded: including it would favour the WebAssembly variants
    further, so leaving it out keeps the comparison conservative.
    """)
    return


@app.cell
def constants():
    # One baseline run. Both E_active and the throughput below refer to exactly
    # this many requests, so N cancels out of nothing and must stay consistent.
    N_REQUESTS = 100_000
    SECONDS_PER_DAY = 86_400.0

    WASM = ["wasm-js", "wasm-rust"]
    CONTAINERS = ["oci-spring", "oci-native", "oci-axum", "oci-node"]
    return CONTAINERS, N_REQUESTS, SECONDS_PER_DAY, WASM


@app.cell
def load_baseline():
    # The baseline export already carries everything the model needs: energy and
    # duration of a run, effective throughput, and the resting draw measured in
    # the accompanying idle scenario.
    #
    # Read the PARQUET, not the CSV. export_table() rounds the CSV to three
    # decimals, and the model divides by the DIFFERENCE between two resting
    # draws that are themselves only thousandths of a watt. Rounding
    # 0.004276 and 0.004798 to 0.004 and 0.005 turns a gap of 0.52 mW into
    # 1.0 mW and very nearly doubles the resulting break-even period.
    df_base = (
        pl.read_parquet(TABLE_DIR / "baseline_summary.parquet")
        .select([
            "target",
            "duration_s",
            "effective_rps",
            "total_joules",
            "mean_power_w",
            "idle_power_w",
            "memory_mb",
        ])
        .sort(target_rank())
    )
    df_base
    return (df_base,)


@app.cell
def model(N_REQUESTS):
    def energy_over_period(row: dict, period_s: float) -> float:
        """Energy to serve N_REQUESTS within `period_s`, idling the remainder."""
        busy_s = N_REQUESTS / row["effective_rps"]
        return row["total_joules"] + max(period_s - busy_s, 0.0) * row["idle_power_w"]

    def breakeven_period(a: dict, b: dict) -> float | None:
        """
        Period at which both variants cost the same.

        Solving E_a(T) == E_b(T) for T. The denominator is the difference in
        resting draw: when the variant that is cheaper under load *also* rests
        more cheaply it wins at every period, and there is no crossing.
        """
        denom = b["idle_power_w"] - a["idle_power_w"]
        if denom == 0:
            return None
        busy_a = N_REQUESTS / a["effective_rps"]
        busy_b = N_REQUESTS / b["effective_rps"]
        numer = (a["total_joules"] - busy_a * a["idle_power_w"]) - (
            b["total_joules"] - busy_b * b["idle_power_w"]
        )
        period = numer / denom
        return period if period > 0 else None

    return breakeven_period, energy_over_period


@app.cell
def compute_breakeven(CONTAINERS, N_REQUESTS, WASM, breakeven_period, df_base):
    _rows = {r["target"]: r for r in df_base.iter_rows(named=True)}

    _out = []
    for _w in WASM:
        for _c in CONTAINERS:
            _T = breakeven_period(_rows[_w], _rows[_c])
            _out.append({
                "wasm_target": _w,
                "container_target": _c,
                "breakeven_period_s": _T,
                "breakeven_period_h": None if _T is None else _T / 3600.0,
                "breakeven_rate_rps": None if _T is None else N_REQUESTS / _T,
                "breakeven_req_per_day": None if _T is None else N_REQUESTS / _T * 86_400.0,
                # A crossing exists only if the Wasm variant rests more cheaply;
                # otherwise it loses on both terms and is dominated outright.
                "wasm_dominated": _T is None,
            })

    df_breakeven = pl.DataFrame(_out).sort(["wasm_target", "breakeven_rate_rps"])
    df_breakeven
    return (df_breakeven,)


@app.cell
def md_table(df_breakeven):
    _crossing = df_breakeven.filter(~pl.col("wasm_dominated"))
    _dominated = df_breakeven.filter(pl.col("wasm_dominated"))

    mo.md(f"""
    ## Result

    {_crossing.height} of {df_breakeven.height} pairings have a crossing at all.
    Below the stated rate the WebAssembly variant is the cheaper choice, above it
    the container variant is.

    **Where the argument works.** Against the JVM the window is narrow but real:
    `wasm-js` undercuts `oci-spring` below
    **{_crossing.filter((pl.col('wasm_target') == 'wasm-js') & (pl.col('container_target') == 'oci-spring'))['breakeven_rate_rps'][0]:.2f} req/s**,
    roughly
    {_crossing.filter((pl.col('wasm_target') == 'wasm-js') & (pl.col('container_target') == 'oci-spring'))['breakeven_req_per_day'][0]:,.0f}
    requests a day.

    **Where it fails.** {_dominated.height} pairings have no crossing:
    {", ".join(f"`{r['wasm_target']}` vs `{r['container_target']}`" for r in _dominated.iter_rows(named=True))}.
    In each of these the WebAssembly variant draws *more* power at rest **and**
    more energy per request, so no duty cycle however low makes it the cheaper
    option. That is the substantive finding: the duty-cycle argument rescues
    WebAssembly against a heavyweight managed runtime, not against a lean
    container.
    """)
    return


@app.cell
def md_chart():
    mo.md(r"""
    ## Why the crossings sit where they do

    Plotting daily energy against sustained request rate makes the mechanism
    visible. Each line is flat on the left, where the day is almost entirely idle
    and the resting draw sets the bill, and rises on the right, where the cost per
    request dominates. A crossing is where a low-idle, expensive-per-request
    runtime gives way to a high-idle, cheap-per-request one. Lines stop at the
    target's own single-pod capacity, so a short line is itself a limitation.
    """)
    return


@app.cell
def chart_breakeven_fn(N_REQUESTS, SECONDS_PER_DAY, df_base, energy_over_period):
    def _curve(row: dict) -> list[dict]:
        # Log-spaced rates from one request a minute up to the target's own
        # single-pod ceiling; beyond that the model would describe a load the
        # target cannot serve.
        _pts = []
        _rate = 0.01
        while _rate <= row["effective_rps"]:
            _reqs = _rate * SECONDS_PER_DAY
            _scale = _reqs / N_REQUESTS
            _busy = _reqs / row["effective_rps"]
            _joules = _scale * row["total_joules"] + max(SECONDS_PER_DAY - _busy, 0.0) * row["idle_power_w"]
            _pts.append({
                "target": row["target"],
                "rate_rps": _rate,
                "energy_wh_per_day": _joules / 3600.0,
            })
            _rate *= 1.15
        return _pts

    df_curves = pl.DataFrame(
        [p for r in df_base.iter_rows(named=True) for p in _curve(r)]
    )

    chart_breakeven = thesis_chart(
        alt.Chart(df_curves)
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X(
                "rate_rps:Q",
                title="Sustained request rate (req/s)",
                scale=alt.Scale(type="log", nice=False),
            ),
            y=alt.Y(
                "energy_wh_per_day:Q",
                title="Energy per day (Wh)",
                scale=alt.Scale(type="log", nice=False),
            ),
            color=target_color(),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("rate_rps:Q", title="req/s", format=".2f"),
                alt.Tooltip("energy_wh_per_day:Q", title="Wh/day", format=".2f"),
            ],
        )
        .properties(width=620, height=300),
        title="Daily energy against sustained request rate",
    )
    chart_breakeven
    return (chart_breakeven,)


@app.cell
def md_caveats():
    mo.md(r"""
    ## Two limitations

    **The idle figures carry the result.** Because the crossing depends on the
    *difference* between two small resting draws, it is more sensitive to
    attribution error than any other number derived from this data. Those draws
    come from Kepler's per-pod attribution, whose accuracy is contested in the
    literature; 0.004 W for `wasm-js` is not credible as the true marginal power
    of keeping a pod resident. Treat the ordering as sound and the exact crossing
    rate as indicative.

    **Energy is not the only cost of residency.** `oci-spring` holds 705 MB while
    idle against 132 MB for `wasm-js`. An operator constrained by memory rather
    than by power reaches a different conclusion from the same measurements, and
    nothing in this model captures that.
    """)
    return


@app.cell
def export_figures(chart_breakeven, df_breakeven):
    export_manifest = export_all(
        charts={"breakeven_energy_vs_rate": chart_breakeven},
        tables={"breakeven_duty_cycle": df_breakeven},
    )
    export_manifest
    return (export_manifest,)


if __name__ == "__main__":
    app.run()
