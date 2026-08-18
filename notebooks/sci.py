import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")

with app.setup:
    import altair as alt
    import marimo as mo
    import polars as pl

    from common_notebook import (
        TABLE_DIR,
        export_all,
        target_color,
        target_rank,
    )


@app.cell
def md_intro():
    mo.md(r"""
    # Software Carbon Intensity

    Energy is a proxy for the quantity an operator is accountable for. The
    Software Carbon Intensity specification (ISO/IEC 21031:2024) defines the step
    between them, scoring a system as a **rate** rather than a total:

    ```
    SCI = ((E * I) + M) / R        M = TE * TS * RS
    ```

    * `E` energy for one functional unit of work - measured here
    * `I` carbon intensity of the electricity - a CONSTANT for one site
    * `M` share of the hardware's embodied emissions charged to the software
    * `R` the functional unit - one baseline run of 100 000 requests

    **Why only one term can matter.** `I` is a property of the site, not of the
    software, so `E * I` is a positive constant times the measured energy: it
    rescales the baseline table without reordering it. For a single deployment
    location the joule ranking already *is* the operational-carbon ranking.

    `M` is different. It grows with how long a variant occupies the machine, and
    the cheapest variant by energy is also the slowest by a factor of four. That
    is the only place where costing carbon rather than joules can change an
    answer, which is what this notebook quantifies.

    **This is a sensitivity analysis, not a result.** Nothing in `M` was measured
    by this study. Two parameters are swept and one modelling choice is reported
    both ways, so the conclusion is a statement about which orderings survive the
    uncertainty - not a carbon figure for this workload.
    """)
    return


@app.cell
def md_parameters():
    mo.md(r"""
    ## Parameters, and where each comes from

    **TE - embodied emissions of the host.** From the published product carbon
    footprint of a Dell PowerEdge R360, the closest single-socket rack server
    with public data: 1 140 kgCO2e mean over a four-year life, 541 at the 5th
    percentile and 3 905 at the 95th. Only **37 %** of that is embodied
    (manufacturing 27 %, transport 9 %, end-of-life 1 %). The use phase - 63 % -
    is deliberately excluded: under SCI that is the `E * I` term, and folding it
    into TE would count the electricity twice. Getting this wrong inflates TE by
    ~2.7x and manufactures a ranking change that is not there.

    **I - carbon intensity.** German grid mix, 344 gCO2e/kWh (Umweltbundesamt,
    2025). The specification requires a *location-based* figure and explicitly
    forbids market-based instruments, so a hosting provider's renewable-supply
    contract cannot reduce the score.

    **RS - resource share.** Genuinely ambiguous on a dedicated single-tenant
    node, so both readings are carried through:

    * `attributed` - each variant is charged its measured CPU share of the 16
      logical CPUs. Conservative, and the reading a shared cluster would use.
    * `dedicated` - the whole machine is charged to whichever variant is running,
      which is what the benchmark host actually reserved for a run.
    """)
    return


@app.cell
def parameters():
    I_GRID_G_PER_KWH = 344.0        # Umweltbundesamt, German grid mix 2025
    SERVER_LIFETIME_Y = 4.0         # Dell PCF assumption for the R360
    EMBODIED_FRACTION = 0.37        # manufacturing 27 % + transport 9 % + EoL 1 %
    PCF_TOTAL_KG = {"p05": 541.0, "mean": 1140.0, "p95": 3905.0}

    R_REQUESTS = 100_000            # functional unit
    LIFETIME_S = SERVER_LIFETIME_Y * 365 * 24 * 3600
    TE_KG = {k: v * EMBODIED_FRACTION for k, v in PCF_TOTAL_KG.items()}
    return (
        EMBODIED_FRACTION,
        I_GRID_G_PER_KWH,
        LIFETIME_S,
        R_REQUESTS,
        TE_KG,
    )


@app.cell
def load_baseline():
    # Parquet, not CSV: the operational term is milligrams and the CSV is rounded
    # to three decimals.
    df_base = (
        pl.read_parquet(TABLE_DIR / "baseline_summary.parquet")
        .select(["target", "duration_s", "cpu_usage", "total_joules"])
        .sort(target_rank())
    )
    df_base
    return (df_base,)


@app.cell
def model(I_GRID_G_PER_KWH, LIFETIME_S):
    def operational_g(joules: float, intensity: float = I_GRID_G_PER_KWH) -> float:
        """The E * I term, in grams of CO2e."""
        return joules / 3.6e6 * intensity

    def embodied_rate_g_per_s(te_kg: float) -> float:
        """Embodied grams charged per second of WHOLE-machine occupancy."""
        return te_kg * 1000.0 / LIFETIME_S

    def embodied_g(te_kg: float, duration_s: float, resource_share: float) -> float:
        """M = TE * TS * RS, with TS expressed as seconds over the lifetime."""
        return embodied_rate_g_per_s(te_kg) * duration_s * resource_share

    return embodied_g, embodied_rate_g_per_s, operational_g


@app.cell
def compute_scenarios(TE_KG, df_base, embodied_g, operational_g):
    _rows = []
    for _r in df_base.iter_rows(named=True):
        _o = operational_g(_r["total_joules"])
        _rs = _r["cpu_usage"] / 100.0
        for _case, _te in TE_KG.items():
            _m_attr = embodied_g(_te, _r["duration_s"], _rs)
            _m_ded = embodied_g(_te, _r["duration_s"], 1.0)
            _rows.append({
                "target": _r["target"],
                "te_case": _case,
                "te_embodied_kg": _te,
                "duration_s": _r["duration_s"],
                "cpu_share_pct": _r["cpu_usage"],
                "energy_j": _r["total_joules"],
                "operational_g": _o,
                "embodied_attributed_g": _m_attr,
                "embodied_dedicated_g": _m_ded,
                "sci_attributed_g": _o + _m_attr,
                "sci_dedicated_g": _o + _m_ded,
                "embodied_share_attributed_pct": 100 * _m_attr / (_o + _m_attr),
                "embodied_share_dedicated_pct": 100 * _m_ded / (_o + _m_ded),
            })

    df_sci = pl.DataFrame(_rows)
    df_sci
    return (df_sci,)


@app.cell
def md_ordering():
    mo.md(r"""
    ## Which ordering survives the uncertainty?

    The table below reports, for each embodied case and each reading of
    resource-share, the variant with the lowest score. The WebAssembly variants
    never appear: they are slow **and** power-hungry, so operational and embodied
    emissions penalise them together rather than trading off.
    """)
    return


@app.cell
def winners(df_sci):
    df_winners = (
        df_sci.group_by("te_case")
        .agg([
            pl.col("target").sort_by("sci_attributed_g").first().alias("lowest_attributed"),
            pl.col("target").sort_by("sci_dedicated_g").first().alias("lowest_dedicated"),
            pl.col("te_embodied_kg").first(),
        ])
        .sort("te_embodied_kg")
    )
    df_winners
    return (df_winners,)


@app.cell
def crossover(LIFETIME_S, df_base, operational_g):
    def crossover_te_kg(a: str, b: str, dedicated: bool = False) -> float | None:
        """
        The embodied figure at which two variants score the same.

        SCI is linear in TE, so equating the two scores and solving gives a
        single crossing. Returns None when they never cross for TE >= 0, i.e.
        when one variant is cheaper on both terms at once.
        """
        _rows = {r["target"]: r for r in df_base.iter_rows(named=True)}
        _a, _b = _rows[a], _rows[b]
        _oa, _ob = operational_g(_a["total_joules"]), operational_g(_b["total_joules"])
        _ca = _a["duration_s"] * (1.0 if dedicated else _a["cpu_usage"] / 100.0)
        _cb = _b["duration_s"] * (1.0 if dedicated else _b["cpu_usage"] / 100.0)
        if _ca == _cb:
            return None
        _k = (_ob - _oa) / (_ca - _cb)      # grams per second of occupancy
        return None if _k < 0 else _k * LIFETIME_S / 1000.0

    return (crossover_te_kg,)


@app.cell
def crossover_table(crossover_te_kg):
    df_crossover = pl.DataFrame([
        {
            "pair": "oci-node vs oci-axum",
            "resource_share": _label,
            "crossover_te_kg": crossover_te_kg("oci-node", "oci-axum", dedicated=_ded),
        }
        for _label, _ded in (("attributed", False), ("dedicated", True))
    ])
    df_crossover
    return (df_crossover,)


@app.cell
def chart_split(df_sci):
    # Operational against embodied, so the reader can see which term dominates.
    _long = (
        df_sci.filter(pl.col("te_case") == "mean")
        .select(["target", "operational_g", "embodied_attributed_g"])
        .unpivot(
            index="target",
            variable_name="term",
            value_name="gco2e",
        )
        .with_columns(
            pl.col("term").replace({
                "operational_g": "operational (E x I)",
                "embodied_attributed_g": "embodied (M)",
            })
        )
    )

    chart_sci_split = (
        alt.Chart(_long)
        .mark_bar()
        .encode(
            y=alt.Y("target:N", title="Target", sort=alt.EncodingSortField("gco2e", op="sum")),
            x=alt.X("gco2e:Q", title="gCO2e per 100,000 requests - log scale",
                    scale=alt.Scale(type="log", nice=False, domainMin=0.01)),
            color=alt.Color("term:N", title="Term",
                            scale=alt.Scale(range=["#00758F", "#FC7C00"])),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("term:N", title="Term"),
                alt.Tooltip("gco2e:Q", title="gCO2e", format=".4f"),
            ],
        )
        .properties(
            width=460, height=230,
            title={
                "text": "Where the carbon comes from",
                "subtitle": "Mean embodied case, resource-share attributed by measured CPU share",
            },
        )
    )
    chart_sci_split
    return (chart_sci_split,)


@app.cell
def chart_sweep(TE_KG, df_base, embodied_g, operational_g):
    # The two cheapest variants only. Sweeping TE shows the ordering turning over
    # inside the published uncertainty band, which is the whole point.
    _rows = {r["target"]: r for r in df_base.iter_rows(named=True)}
    _pts = []
    for _te in [50 * i for i in range(1, 31)]:
        for _t in ("oci-node", "oci-axum"):
            _r = _rows[_t]
            _pts.append({
                "te_embodied_kg": float(_te),
                "target": _t,
                "sci_g": operational_g(_r["total_joules"])
                + embodied_g(_te, _r["duration_s"], _r["cpu_usage"] / 100.0),
            })
    _df = pl.DataFrame(_pts)

    _bands = pl.DataFrame({
        "lo": [TE_KG["p05"]], "hi": [TE_KG["p95"]],
    })
    _band = (
        alt.Chart(_bands)
        .mark_rect(opacity=0.12, color="#666")
        .encode(x=alt.X("lo:Q", title="Embodied emissions of the host (kgCO2e)"), x2="hi:Q")
    )
    _mean = (
        alt.Chart(pl.DataFrame({"v": [TE_KG["mean"]]}))
        .mark_rule(strokeDash=[5, 4], color="#444")
        .encode(x=alt.X("v:Q", title="Embodied emissions of the host (kgCO2e)"))
    )
    _lines = (
        alt.Chart(_df)
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X("te_embodied_kg:Q", title="Embodied emissions of the host (kgCO2e)",
                    scale=alt.Scale(nice=False)),
            y=alt.Y("sci_g:Q", title="SCI (gCO2e per 100,000 requests)",
                    scale=alt.Scale(zero=False)),
            color=target_color(),
            tooltip=[
                alt.Tooltip("target:N", title="Target"),
                alt.Tooltip("te_embodied_kg:Q", title="TE (kg)", format=".0f"),
                alt.Tooltip("sci_g:Q", title="SCI (g)", format=".4f"),
            ],
        )
    )

    chart_sci_sweep = (_band + _mean + _lines).properties(
        width=520, height=280,
        title={
            "text": "The ordering of the two cheapest variants is not settled by the measurements",
            "subtitle": "Grey band = published 5th-95th percentile for the reference server, "
                        "dashed line = mean. Curves cross inside it",
        },
    )
    chart_sci_sweep
    return (chart_sci_sweep,)


@app.cell
def md_reading():
    mo.md(r"""
    ## Reading the result

    **The joule ranking is the operational-carbon ranking.** `E * I` cannot
    reorder anything within one site, so nothing in the baseline chapter needs
    restating in grams for the comparison to hold.

    **One pairing is undetermined.** On energy `oci-node` leads `oci-axum` by
    40 %. Charging each variant its measured CPU share, that collapses to under
    3 %, and the ordering turns over just above the central embodied estimate -
    inside the published range for a single server model. Charging whole-machine
    occupancy, `oci-axum` is ahead everywhere and embodied emissions are the
    large majority of `oci-node`'s score. `oci-node` is the least *energy*;
    `oci-axum` is the safer choice once the machine's manufacture is counted.

    **The WebAssembly conclusion is untouched.** Both variants remain the two
    most expensive under every parameterisation, at both extremes of the embodied
    range and under both readings of resource-share. Costing the hardware as well
    as the electricity widens the deficit rather than recovering it.
    """)
    return


@app.cell
def export_figures(chart_sci_split, chart_sci_sweep, df_sci):
    export_manifest = export_all(
        charts={
            "sci_carbon_split": chart_sci_split,
            "sci_embodied_sweep": chart_sci_sweep,
        },
        tables={"sci_scenarios": df_sci},
    )
    export_manifest
    return (export_manifest,)


if __name__ == "__main__":
    app.run()
