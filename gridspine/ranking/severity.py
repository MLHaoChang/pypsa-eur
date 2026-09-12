"""N-1 severity over the year, in DC — the measured proxy for the fifth criterion.

Since follow-ups F2 the ranking's ``max_n1_severity`` reads the AC screen's own
number (``static.contingency.n1_severity_ac``); this column stays in the metrics
table so the DC-vs-AC disagreement is measured over every hour of every run
(the driver's ``n1_severity_ac_pass`` manifest entry). On the v3 year the DC
proxy was anticorrelated with AC over the selected hours (rho -0.57) and
uncorrelated over 100 spread hours (rho -0.01).

For each hour: bus injections P from the dispatch and loads tables (units
placed by the registry, loads by bus), DC branch flows f = PTDF @ P, single-
outage flows f' = f + LODF[:, k] f_k for every non-islanding branch k, and

    n1_severity_dc[h] = max over k of  sum over l of  max(0, |f'_l| / rating_l - 1)

— the worst single outage's overload depth, the DC analogue of the branch
term in ``static.contingency.severity``. Dimensionless, zero when no outage
overloads anything.

Islanding outages are EXCLUDED. They are a topology fact identical in every
hour (task 4 found 11 of case39's 46) and would sit as a constant floor under
the metric, muting exactly the variation it exists to rank — the same reason
``inertia_excl_equiv_mws`` exists.

This is pass 1 of the locked decision in the increment-3 plan: DC ranks the
year, AC verifies the selection. The cost is stated in ``SEVERITY_LEDGER``
and MEASURED in ``tests/gridspine/test_severity.py`` against task 4's AC
severity: DC has no voltage term and underestimates loading on the critical
branch (task 5), so an hour dangerous only in AC can be missed here.

numpy, pandas and ``schema`` only — the LODF arrives as a validated artifact.
Its reference column is zero, so an hour whose injections do not sum to zero
puts the residual on the reference bus, exactly as the DC solve would.
"""
import numpy as np
import pandas as pd

from gridspine.schema.contracts import ContractError
from gridspine.schema.dc import DCSensitivities, validate_dc_sensitivities
from gridspine.schema.dispatch import validate_dispatch, validate_loads

SEVERITY_LEDGER = (
    "n1_severity_dc: per hour, bus injections from the dispatch and loads tables, "
    "DC branch flows PTDF @ P, single-outage flows by LODF, and the worst outage's "
    "overload depth sum max_k sum_l max(0, |f'_l|/rating_l - 1); ISLANDING outages "
    "excluded — a topology fact identical in every hour would be a constant floor",
    "DC sees no voltage and underestimates loading on the critical branch (task 5); "
    "measured against task 4's AC severity over 15 synthetic hours on case39_res "
    "(load factor 0.6-1.0 x RES capacity factor 0.1-0.6, slack balancing): "
    "Spearman rho = 1.00, worst rank gap 0 of 15 (2026-09-05) — the sample is "
    "monotone scalings dominated by the same three base-case overloads, so it "
    "does not exercise the reordering the blind spot could cause; re-measure on "
    "the UC-dispatched year's selected hours (task 13). This is the "
    "dc_severity_blind_spot the ledger README declares",
    "since follow-ups F2 the ranking's max_n1_severity reads the AC screen's "
    "n1_severity_ac, not this DC column; n1_severity_dc stays in metrics.csv as "
    "the proxy whose year-wide rank agreement with AC the manifest reports",
)


def _bus_positions(sens: DCSensitivities) -> dict:
    return {name: i for i, name in enumerate(sens.bus_names)}


def hourly_dc_flows(dispatch, loads, registry, sens: DCSensitivities) -> pd.DataFrame:
    """DC from-side MW per (hour, branch): index hour, columns branch_ids."""
    sens = validate_dc_sensitivities(sens)
    d = validate_dispatch(dispatch)
    ld = validate_loads(loads)
    d_hours, l_hours = set(d["hour"].tolist()), set(ld["hour"].tolist())
    if d_hours != l_hours:
        raise ContractError(
            "dispatch and loads cover different hours: "
            f"{len(d_hours - l_hours)} only in dispatch, {len(l_hours - d_hours)} only in loads"
        )
    pos = _bus_positions(sens)
    unknown_units = sorted(set(d["unit_id"]) - set(registry.index))
    if unknown_units:
        raise ContractError(f"dispatch names units absent from the registry: {unknown_units}")
    unit_bus = d["unit_id"].map(registry["bus"])
    bad_bus = sorted(set(unit_bus) - set(pos))
    if bad_bus:
        raise ContractError(f"registry places units on buses the DC artifact does not know: {bad_bus}")
    bad_load_bus = sorted(set(ld["bus"]) - set(pos))
    if bad_load_bus:
        raise ContractError(f"loads name buses the DC artifact does not know: {bad_load_bus}")

    hours = sorted(d_hours)
    h_pos = {h: i for i, h in enumerate(hours)}
    P = np.zeros((len(hours), len(sens.bus_names)))
    np.add.at(P, (d["hour"].map(h_pos).values, unit_bus.map(pos).values), d["p_mw"].values)
    np.add.at(P, (ld["hour"].map(h_pos).values, ld["bus"].map(pos).values), -ld["p_mw"].values)
    flows = P @ sens.ptdf.T
    return pd.DataFrame(flows, index=pd.Index(hours, dtype="int64", name="hour"), columns=list(sens.branch_ids))


def worst_n1_overload_depth(sens: DCSensitivities, flows_mw) -> float:
    """max over non-islanding outages of sum max(0, |f'|/rating - 1)."""
    f = np.asarray(flows_mw, dtype=float)
    live = ~sens.islanding
    if not live.any():
        return 0.0
    # column k of F is the post-outage flow vector for outage k (diag -1 zeroes f'_k)
    F = f[:, None] + sens.lodf[:, live] * f[live][None, :]
    depth = np.clip(np.abs(F) / sens.rating_mva[:, None] - 1.0, 0.0, None).sum(axis=0)
    return float(depth.max())


def n1_severity_dc(dispatch, loads, registry, sens: DCSensitivities) -> pd.Series:
    sens = validate_dc_sensitivities(sens)
    flows = hourly_dc_flows(dispatch, loads, registry, sens)
    values = [worst_n1_overload_depth(sens, row) for row in flows.values]
    return pd.Series(values, index=flows.index, name="n1_severity_dc", dtype="float64")
