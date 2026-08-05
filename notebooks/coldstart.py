import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")

with app.setup:
    import altair as alt
    import marimo as mo
    import polars as pl
    from common_notebook import build_scenario_table, load_scenario_metrics, load_metric_data


@app.cell
def _():
    select_columns = ["status"]
    df_metrics = load_scenario_metrics("coldstart", select_columns=select_columns)
    df_metrics
    return (df_metrics,)


@app.cell
def _():
    df_idle_metrics = load_scenario_metrics("idle-scaled", select_columns=["all"])
    df_idle_metrics
    return (df_idle_metrics,)


@app.cell
def _(df_idle_metrics):
    df_idle_metrics["node_avg_cpu_watts"]
    return


@app.cell
def _(df_idle_metrics):
    df_avg_idle_watts = (df_idle_metrics["node_avg_cpu_watts"].filter(
        pl.col("normalized_time") > (pl.col("normalized_time").min())
    )).group_by(["zone"]).agg(pl.col("value").mean())
    df_avg_idle_watts
    return (df_avg_idle_watts,)


@app.cell
def _(df_avg_idle_watts):
    chart_idle_watts = (
        alt.Chart(df_avg_idle_watts)
        .mark_line()
        .encode(
            x=alt.X(field='normalized_time', type='quantitative'),
            y=alt.Y(field='value', type='quantitative', aggregate='mean'),
            color=alt.Color(field='zone', type='nominal'),
            tooltip=[
                alt.Tooltip(field='normalized_time', format=',.2f'),
                alt.Tooltip(field='value', aggregate='mean', format=',.2f'),
                alt.Tooltip(field='zone')
            ]
        )
        .properties(
            height=290,
            width='container',
            config={
                'axis': {
                    'grid': False
                }
            }
        )
    )
    chart_idle_watts
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    $$E_{\text{coldstart}} = \int_{t_0}^{t_1} \Big( P_{\text{node}}(t) - P_{\text{baseline}} \Big) \, dt$$



    $$
    E_{\text{coldstart}}
    = \left(\frac{1}{\Delta t}\int_{t_0}^{t_1}P_{\text{node}}(t),dt - P_{\text{baseline}}\right)\Delta t,
    $$
    since $(P_{\text{baseline}})$ is constant.

    $$E_{\text{coldstart}} \approx (\bar{P}_{\text{node}} - P_{\text{baseline}}) \times \Delta t$$

    $$\bar{P}_{\text{node}} = \text{avg\_over\_time}(\text{kepler\_node\_cpu\_watts}[\Delta t])$$

    $$\bar P_{\text{node}}
    \approx
    \frac{1}{\Delta t}
    \int_{t_0}^{t_1}P_{\text{node}}(t),dt.$$
    """)
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    To evaluate the statement, we can analyze the mathematical derivation step-by-step from the initial continuous integral down to the discrete metric approximation.

    ---

    ### Step 1: Continuous Integral Expansion

    Let $\Delta t = t_1 - t_0$ represent the duration of the cold start interval. Starting from the total energy integral:

    $$E_{\text{coldstart}} = \int_{t_0}^{t_1} \Big( P_{\text{node}}(t) - P_{\text{baseline}} \Big) \, dt$$

    By the linearity property of integration, we split the integral into two parts:

    $$\int_{t_0}^{t_1} \Big( P_{\text{node}}(t) - P_{\text{baseline}} \Big) \, dt = \int_{t_0}^{t_1} P_{\text{node}}(t) \, dt - \int_{t_0}^{t_1} P_{\text{baseline}} \, dt$$

    Since $P_{\text{baseline}}$ is constant over $[t_0, t_1]$, its integral simplifies to:

    $$\int_{t_0}^{t_1} P_{\text{baseline}} \, dt = P_{\text{baseline}} \cdot (t_1 - t_0) = P_{\text{baseline}} \Delta t$$

    ---

    ### Step 2: Factoring $\Delta t$

    Substituting $P_{\text{baseline}} \Delta t$ back into the split integral:

    $$E_{\text{coldstart}} = \int_{t_0}^{t_1} P_{\text{node}}(t) \, dt - P_{\text{baseline}} \Delta t$$

    Factoring out $\Delta t$:

    $$E_{\text{coldstart}} = \left( \frac{1}{\Delta t} \int_{t_0}^{t_1} P_{\text{node}}(t) \, dt - P_{\text{baseline}} \right) \Delta t$$

    This algebraic transformation is exact and confirms the equality of the second formula.

    ---

    ### Step 3: Mean Power Approximation

    By definition, the mean value of a continuous power function $P_{\text{node}}(t)$ over time interval $\Delta t$ is:

    $$\bar{P}_{\text{node, exact}} = \frac{1}{\Delta t} \int_{t_0}^{t_1} P_{\text{node}}(t) \, dt$$

    In monitoring systems like Prometheus and Kepler, power is measured via discrete samples. The function $\text{avg\_over\_time}(\text{kepler\_node\_cpu\_watts}[\Delta t])$ computes the arithmetic mean of these discrete samples, which serves as a Riemann sum approximation of the continuous time average:

    $$\bar{P}_{\text{node}} = \text{avg\_over\_time}(\text{kepler\_node\_cpu\_watts}[\Delta t]) \approx \frac{1}{\Delta t} \int_{t_0}^{t_1} P_{\text{node}}(t) \, dt$$

    ---

    ### Step 4: Final Substitution

    Replacing the continuous mean integral with the discrete sample average $\bar{P}_{\text{node}}$ yields the energy estimate:

    $$E_{\text{coldstart}} \approx (\bar{P}_{\text{node}} - P_{\text{baseline}}) \times \Delta t$$

    ---

    Since every algebraic step is exact and the transition to PromQL discrete sampling is mathematically valid, the overall statement is **True**.
    """)
    return


@app.cell
def _(df_metrics):
    df_response_time = df_metrics["p95"].group_by(["iteration", "target"]).agg(
        pl.col("value").first()
    )
    df_response_time
    return (df_response_time,)


@app.cell
def _(df_metrics):
    df_response_time_avg = df_metrics["p95"].group_by("target").agg(
        pl.col("value").first().over("iteration", "target").mean().alias("mean_value")
    )
    df_response_time_avg
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    Because you scraped a wider window than the actual cold start duration, taking the average of **all** points in an iteration will dilute your power calculation with idle/post-execution samples.

    You need to **trim each iteration's power series to match the exact duration of the cold start** before integrating.

    ---

    ### The Trimming Formula

    If your cold start duration is $T_{\text{coldstart}}$ seconds (retrieved from your response time metric):

    1. For each iteration, slice the power samples where:

    $$\text{normalized\_time} \le T_{\text{coldstart}}$$


    2. Compute average power ($\bar{P}_{\text{node}}$) using **only those active samples**.
    3. Calculate energy using the actual response time as $\Delta t$:

    $$E_{\text{coldstart}} = (\bar{P}_{\text{node, active}} - P_{\text{baseline}}) \times T_{\text{coldstart}}$$



    ---

    ### How to Join and Calculate in Polars

    If you have a second dataframe containing response times per iteration:

    ```python
    import polars as pl

    # 1. Load Data
    df_power = pl.read_csv("power_data.csv")

    # Example response times lookup dataframe
    df_response = pl.DataFrame(
        {
            "iteration": [1, 2, 8, 17, 32],
            "response_time_sec": [8.5, 9.2, 7.8, 11.1, 5.4],  # Actual response times
        }
    )

    # Baseline power constant (in Watts)
    P_baseline = 15.0

    # 2. Join, Filter Active Window, and Calculate Energy
    results = (
        df_power.join(df_response, on="iteration", how="inner")
        # Filter out samples collected after the cold start finished
        .filter(pl.col("normalized_time") <= pl.col("response_time_sec"))
        # Aggregate per iteration
        .group_by("iteration")
        .agg(
            p_active_watts=pl.col("value").mean(),
            duration_sec=pl.col("response_time_sec").first(),
        )
        # Compute Coldstart Energy: E = (P_active - P_baseline) * T_coldstart
        .with_columns(
            e_coldstart_joules=(pl.col("p_active_watts") - P_baseline)
            * pl.col("duration_sec")
        )
        .sort("iteration")
    )

    print(results)

    # 3. Final Overall Metrics Across All Iterations
    overall_summary = results.select(
        mean_energy=pl.col("e_coldstart_joules").mean(),
        std_energy=pl.col("e_coldstart_joules").std(),
    )

    print("\nOverall Aggregate:")
    print(overall_summary)

    ```

    ---

    ### How to Handle Boundary Interpolation (Edge Case)

    What if $T_{\text{coldstart}} = 7.5\text{s}$, but your samples occur at `normalized_time` $= [0, 3, 6, 9]$?

    * **Simple approach:** Keep points up to $6\text{s}$ or include $9\text{s}$ depending on rounding. For small sample sizes, this can introduce a slight error.
    * **Accurate approach (Linear Interpolation / Trapezoidal Rule):** Interpolate the exact power value at $t = 7.5\text{s}$ and use trapezoidal integration (`numpy.trapz` or `scipy.integrate.trapezoid`) up to $7.5\text{s}$.

    If high precision is critical for your benchmark, trapezoidal integration over the interpolated line gives you the exact area under the power curve!
    """)
    return


@app.cell
def _(df_avg_idle_watts, df_metrics, df_response_time):
    df_duration = df_response_time.rename({"value": "duration"})
    df_baseline = df_avg_idle_watts.rename({"value": "p_baseline"})

    # 1. Run integration
    df_energy_per_run = (
        df_metrics["node_avg_cpu_watts"]
        .join(df_duration, on=["iteration", "target"], how="inner")
        .join(df_baseline, on="zone", how="left")
        .filter((pl.col("normalized_time") <= pl.col("duration")) | (pl.col("normalized_time") == 0))
        .sort(["iteration", "target", "zone", "normalized_time"])
        .with_columns((pl.col("value") - pl.col("p_baseline")).alias("excess_watts"))
        .with_columns(
            (pl.col("normalized_time") - pl.col("normalized_time").shift(1)).over(["iteration", "target", "zone"]).alias("dt"),
            ((pl.col("excess_watts") + pl.col("excess_watts").shift(1)) / 2.0).over(["iteration", "target", "zone"]).alias("avg_window_watts")
        )
        .with_columns((pl.col("avg_window_watts") * pl.col("dt")).alias("e_interval_joules"))
        .group_by(["iteration", "target", "zone", "duration"])
        .agg(pl.col("e_interval_joules").sum().fill_null(0.0).alias("e_joules"))
    )

    # 2. Add 'total' zone per iteration
    df_total_per_run = (
        df_energy_per_run
        .group_by(["iteration", "target", "duration"])
        .agg(
            pl.col("e_joules").sum().alias("e_joules"),
            pl.lit("total").alias("zone")
        )
    )

    df_all_runs = pl.concat([
        df_energy_per_run.select(["iteration", "target", "zone", "duration", "e_joules"]),
        df_total_per_run.select(["iteration", "target", "zone", "duration", "e_joules"])
    ])

    # 3. Aggregate statistics ONLY on captured spikes (e_joules > 0)
    df_summary = (
        df_all_runs
        .group_by(["target", "zone"])
        .agg(
            # Capture metadata
            pl.len().alias("total_runs"),
            (pl.col("e_joules") > 0.0).sum().alias("valid_runs"),
            ((pl.col("e_joules") > 0.0).sum() / pl.len() * 100).alias("capture_rate_pct"),

            # Energy metrics computed ONLY on non-zero runs
            pl.col("e_joules").filter(pl.col("e_joules") > 0.0).mean().alias("mean_e_joules"),
            pl.col("e_joules").filter(pl.col("e_joules") > 0.0).std().alias("std_e_joules"),
            pl.col("duration").mean().round(10).alias("mean_duration_sec")
        )
    )

    # 4. Pivot cleanly
    df_final = (
        df_summary
        .pivot(
            on="zone",
            index=["target", "mean_duration_sec"],
            values=["mean_e_joules", "std_e_joules", "capture_rate_pct"]
        )
    )
    return df_energy_per_run, df_final, df_summary


@app.cell
def _(df_energy_per_run):
    df_energy_per_run
    return


@app.cell
def _(df_summary):
    df_summary
    return


@app.cell
def _(df_final):
    df_final
    return


if __name__ == "__main__":
    app.run()
