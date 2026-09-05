"""Task 8: N-1 severity as a ranking criterion — DC over the year.

`ranking/` imports no engine, so the LODF crosses the stage boundary as a
plain artifact: `schema.dc.DCSensitivities` (PTDF, LODF, islanding mask,
ratings, bus and branch names), produced by `static.lodf.to_sensitivities` and
saved as .npz, exactly like every other arrow in the pipeline. The ranking
side rebuilds each hour's bus injections from the dispatch and loads tables
and multiplies — that is what makes 8760 hours affordable.

The proof that the ranking-side path is the static-side path: for one hour,
`PTDF @ P_h` must reproduce the DC branch flows pandapower solves for that
snapshot, and the per-hour severity must equal the same number computed with
`static.lodf.n1_dc_flows`.

Islanding outages are EXCLUDED from the severity: they are a topology fact
identical in every hour and would sit as a constant floor under the metric
(task 4's finding). The DC blind spot — DC has no voltage term and
underestimates loading on the critical branch (task 5's finding) — is
MEASURED here against task 4's AC severity over a set of synthetic hours and
pinned, so the number quoted in the ledger is the number the code produces.
"""
import copy

import numpy as np
import pandas as pd
import pytest

from gridspine.ingest.pandapower_source import RES_LEDGER, load_case39_res, registry_from_net
from gridspine.ranking.severity import (
    SEVERITY_LEDGER,
    n1_severity_dc,
    worst_n1_overload_depth,
)
from gridspine.schema.contracts import ContractError
from gridspine.schema.dc import (
    DCSensitivities,
    load_dc_sensitivities,
    save_dc_sensitivities,
    validate_dc_sensitivities,
)
from gridspine.static.contingency import screen_n1
from gridspine.static.contingency_set import branch_contingencies
from gridspine.static.lodf import dc_base, dc_loading_pct, n1_dc_flows, to_sensitivities
from gridspine.static.loadflow import apply_snapshot

CAP = {e["name"]: e["p_mw"] for e in RES_LEDGER}


def _synthetic_hours(net, registry, cases):
    """Hours as (load factor, RES capacity factor); the slack balances each."""
    d_rows, l_rows = [], []
    name_of = net.bus["name"]
    native_load = net.load.groupby("bus")[["p_mw", "q_mvar"]].sum()
    native_gen = {net.gen.at[i, "name"]: float(net.gen.at[i, "p_mw"]) for i in net.gen.index}
    for hour, (lf, cf) in enumerate(cases):
        gen_total = sum(native_gen.values()) * lf
        res_total = sum(cf * CAP[u] for u in CAP)
        load_total = float(native_load["p_mw"].sum()) * lf
        slack = load_total - gen_total - res_total
        for unit_id, rec in registry.iterrows():
            if rec["kind"] == "gen":
                p = native_gen[unit_id] * lf
            elif rec["kind"] == "res":
                p = cf * CAP[unit_id]
            else:
                p = slack
            d_rows.append({"unit_id": unit_id, "hour": hour, "p_mw": p, "q_mvar": 0.0, "status": 1})
        for bus_idx, rec in native_load.iterrows():
            l_rows.append({"bus": name_of.at[bus_idx], "hour": hour,
                           "p_mw": float(rec["p_mw"]) * lf, "q_mvar": float(rec["q_mvar"]) * lf})
    return pd.DataFrame(d_rows), pd.DataFrame(l_rows)


CASES = [(lf, cf) for lf in (0.6, 0.7, 0.8, 0.9, 1.0) for cf in (0.1, 0.3, 0.6)]


@pytest.fixture(scope="module")
def fixture():
    net = load_case39_res()
    reg = registry_from_net(net)
    dispatch, loads = _synthetic_hours(net, reg, CASES)
    state = dc_base(net)
    sens = to_sensitivities(state)
    return dict(net=net, reg=reg, dispatch=dispatch, loads=loads, state=state, sens=sens)


# --------------------------------------------------------------------------
# the artifact
# --------------------------------------------------------------------------

def test_sensitivities_validate_and_carry_the_branch_identity(fixture):
    s = validate_dc_sensitivities(fixture["sens"])
    n_br, n_bus = 46, 39
    assert s.ptdf.shape == (n_br, n_bus) and s.lodf.shape == (n_br, n_br)
    assert list(s.bus_names) == list(fixture["net"].bus["name"])
    cset = branch_contingencies(fixture["net"])
    assert set(s.branch_ids) == set(cset["contingency_id"])
    assert s.islanding.sum() == 11


def test_sensitivities_round_trip_through_npz(fixture, tmp_path):
    p = save_dc_sensitivities(fixture["sens"], tmp_path / "dc.npz")
    back = load_dc_sensitivities(p)
    np.testing.assert_array_equal(back.ptdf, fixture["sens"].ptdf)
    np.testing.assert_array_equal(np.isnan(back.lodf), np.isnan(fixture["sens"].lodf))
    np.testing.assert_allclose(np.nan_to_num(back.lodf), np.nan_to_num(fixture["sens"].lodf))
    assert list(back.branch_ids) == list(fixture["sens"].branch_ids)
    assert list(back.bus_names) == list(fixture["sens"].bus_names)
    assert back.ref_bus == fixture["sens"].ref_bus


@pytest.mark.parametrize("break_it", ["ref_col", "inf", "rating", "island_col", "dup_bus"])
def test_sensitivities_validator_rejects_a_broken_artifact(fixture, break_it):
    s = fixture["sens"]
    ptdf, lodf, rating = s.ptdf.copy(), s.lodf.copy(), s.rating_mva.copy()
    bus_names = list(s.bus_names)
    if break_it == "ref_col":
        ptdf[:, s.ref_bus] = 1e-3
    elif break_it == "inf":
        live = int(np.where(~s.islanding)[0][0])
        lodf[0, live] = np.inf
    elif break_it == "rating":
        rating[3] = 0.0
    elif break_it == "island_col":
        isl = int(np.where(s.islanding)[0][0])
        lodf[:, isl] = 0.0
    elif break_it == "dup_bus":
        bus_names[1] = bus_names[0]
    bad = DCSensitivities(ptdf=ptdf, lodf=lodf, islanding=s.islanding, rating_mva=rating,
                          bus_names=bus_names, branch_ids=list(s.branch_ids), ref_bus=s.ref_bus)
    with pytest.raises(ContractError):
        validate_dc_sensitivities(bad)


# --------------------------------------------------------------------------
# the ranking-side path IS the static-side path
# --------------------------------------------------------------------------

def test_ranking_side_injections_reproduce_the_dc_solve_for_one_hour(fixture):
    """PTDF @ P_h == the DC flows pandapower solves on the applied snapshot."""
    net = copy.deepcopy(fixture["net"])
    d, l = fixture["dispatch"], fixture["loads"]
    apply_snapshot(net, d, l, hour=14, registry=fixture["reg"])   # lf 1.0, cf 0.6
    want = dc_base(net).flows_mw
    from gridspine.ranking.severity import hourly_dc_flows
    got = hourly_dc_flows(d, l, fixture["reg"], fixture["sens"]).loc[14].values
    np.testing.assert_allclose(got, want, atol=1e-6)


def test_per_hour_severity_equals_the_static_side_computation(fixture):
    net = copy.deepcopy(fixture["net"])
    d, l = fixture["dispatch"], fixture["loads"]
    apply_snapshot(net, d, l, hour=14, registry=fixture["reg"])
    state = dc_base(net)
    depths = []
    for k in np.where(~state.islanding)[0]:
        ld = dc_loading_pct(state, n1_dc_flows(state, int(k)))
        depths.append(np.clip(ld / 100.0 - 1.0, 0.0, None).sum())
    want = max(depths)
    sev = n1_severity_dc(d, l, fixture["reg"], fixture["sens"])
    assert sev.loc[14] == pytest.approx(want, rel=1e-9)
    assert worst_n1_overload_depth(fixture["sens"], state.flows_mw) == pytest.approx(want, rel=1e-9)


def test_severity_is_a_series_over_every_hour_finite_and_non_negative(fixture):
    sev = n1_severity_dc(fixture["dispatch"], fixture["loads"], fixture["reg"], fixture["sens"])
    assert sev.name == "n1_severity_dc"
    assert sev.index.tolist() == list(range(len(CASES)))
    assert sev.index.name == "hour"
    assert np.isfinite(sev).all() and (sev >= 0).all()


def test_severity_rises_with_load_at_fixed_res(fixture):
    sev = n1_severity_dc(fixture["dispatch"], fixture["loads"], fixture["reg"], fixture["sens"])
    at_cf03 = [sev.loc[h] for h, (lf, cf) in enumerate(CASES) if cf == 0.3]
    assert all(b >= a for a, b in zip(at_cf03, at_cf03[1:]))
    assert at_cf03[-1] > at_cf03[0]


def test_islanding_outages_are_excluded_so_severity_stays_finite(fixture):
    assert fixture["sens"].islanding.sum() == 11
    sev = n1_severity_dc(fixture["dispatch"], fixture["loads"], fixture["reg"], fixture["sens"])
    assert np.isfinite(sev).all()


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------

def test_a_unit_on_a_bus_the_artifact_does_not_know_is_refused(fixture):
    reg = fixture["reg"].copy()
    reg.loc["G_BUS_30", "bus"] = "BUS_99"
    with pytest.raises(ContractError, match="BUS_99"):
        n1_severity_dc(fixture["dispatch"], fixture["loads"], reg, fixture["sens"])


def test_loads_and_dispatch_covering_different_hours_are_refused(fixture):
    loads = fixture["loads"]
    with pytest.raises(ContractError, match="hours"):
        n1_severity_dc(fixture["dispatch"], loads[loads["hour"] < 5], fixture["reg"], fixture["sens"])


# --------------------------------------------------------------------------
# the DC blind spot, measured against task 4's AC severity
# --------------------------------------------------------------------------

def test_dc_severity_tracks_ac_severity_with_a_measured_rank_correlation(fixture):
    """Fifteen synthetic hours. AC severity per hour is the worst converged,
    non-islanded N-1 row's `severity` (task 4: overload depth PLUS voltage
    excursion); DC is overload depth only, from PTDF/LODF. The Spearman rank
    correlation and the worst rank disagreement are the numbers the ledger
    quotes as `dc_severity_blind_spot`."""
    d, l, reg, sens = fixture["dispatch"], fixture["loads"], fixture["reg"], fixture["sens"]
    dc = n1_severity_dc(d, l, reg, sens)
    cset = branch_contingencies(fixture["net"])
    ac = {}
    for hour in range(len(CASES)):
        net = copy.deepcopy(fixture["net"])
        apply_snapshot(net, d, l, hour=hour, registry=reg)
        rows = screen_n1(net, cset, d, l, hour, reg)
        ok = rows[rows["converged"] & ~rows["islanded"]]
        ac[hour] = float(ok["severity"].max())
    ac = pd.Series(ac, name="ac").sort_index()
    assert dc.nunique() > 5 and ac.nunique() > 5, "a rank correlation over near-constants is meaningless"
    rho = dc.rank().corr(ac.rank(), method="pearson")   # Spearman
    worst = int((dc.rank() - ac.rank()).abs().max())
    assert rho > 0.5, (rho, worst)
    assert worst <= len(CASES) // 2, (rho, worst)
    # pinned after measurement — see SEVERITY_LEDGER
    text = " ".join(SEVERITY_LEDGER)
    assert f"rho = {rho:.2f}" in text, (rho, worst)
    assert f"worst rank gap {worst}" in text, (rho, worst)


def test_severity_ledger_names_the_exclusion_and_the_blind_spot():
    text = " ".join(SEVERITY_LEDGER).lower()
    for word in ("island", "voltage", "measured", "lodf"):
        assert word in text, word


def test_severity_module_imports_only_numpy_pandas_and_schema():
    import ast

    import gridspine.ranking.severity as mod

    tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    allowed = {"numpy", "pandas", "gridspine.schema.contracts", "gridspine.schema.dispatch", "gridspine.schema.dc"}
    assert imported <= allowed, sorted(imported - allowed)
