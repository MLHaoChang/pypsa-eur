"""
AS-06 (`.scratch/asset-sizing-loop/issues/06-sizing-loop-runner.md`): the sizing loop's cuts as LP rows.
`_wrap_with_sizing_cuts` reads the cut tags on the grid-connection Link and adds:

  * import cap  p_tr(t) <= cap in every snapshot: the import flow at the PoC, never the transformer's p_nom, so
                it cannot contradict a transformer the loop froze (GK A condition 3);
  * GFM floor   sum over GFM-capable stores of P_k + S_x,k >= gfm_mva_min (the ESCR cut);
  * Pst row     P_res >= S + A - sum P_sm, Q_res >= tan phi_swing (S + A) - Q_dyn, P_res, Q_res >= 0 and
                cos psi P_res + sin psi Q_res <= rhs, the AS-04 quantities (`services.adequacy.flicker_inputs`).

The PQ margin is not a row here: `pq_q_margin_mvar` on the PQ Link adds to Q_load in AS-05's pq row.

Hand values use the AS prototype's illustrative constants, generic engineering, "assumed" (SAGC-1).
"""
from __future__ import annotations

import math
import queue
import threading

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.solver import sizing_cuts as sc
from services.solver.envelope_floors import _wrap_with_envelope_floors, tag_store
from services.solver.pcs_statcom import _wrap_with_pcs_statcom, credit, solution, tag_pcs, tag_pq_link
from services.solver.sizing_cuts import (
    LINK_COLUMNS,
    STORE_COLUMNS,
    SizingCutsRefusal,
    _wrap_with_sizing_cuts,
    pst_rhs_mva,
    tag_cuts,
    tag_gfm,
    tightened_pst_rhs,
)
from services.solver.split_storage import _wrap_with_split_storage, add_split_storage

X_TR, PF_TR, S_N, P_CAP, PF_REQ, PF_LOAD = 0.12, 0.98, 125.0, 122.5, 0.98, 0.95
C_PCS, C_STATCOM = 6_000.0, 8_000.0
TAN_LOAD = math.tan(math.acos(PF_LOAD))
K_LOSS = X_TR * P_CAP / (PF_TR**2 * S_N)
TAN_REQ = math.tan(math.acos(PF_REQ))
XR = 4.0
COS, SIN = math.cos(math.atan(XR)), math.sin(math.atan(XR))   # 0.242536, 0.970143


def _campus(load_p, *, pq=True, link_p_nom=S_N, extendable=False):
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(len(load_p)))
    n.snapshot_weightings.loc[:, "objective"] = 8760 / len(load_p)
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "campus", carrier="AC")
    n.add("Generator", "import", bus="grid", p_nom=1e3, p_min_pu=-1.0, marginal_cost=10.0)
    n.add("Generator", "local", bus="campus", p_nom=1e3, marginal_cost=500.0)
    n.add("Link", "trafo", bus0="grid", bus1="campus", p_nom=link_p_nom, p_max_pu=PF_TR, p_min_pu=0.0,
          p_nom_extendable=extendable, p_nom_max=link_p_nom, capital_cost=10_000.0)
    load_p = np.asarray(load_p, dtype=float)
    n.add("Load", "dc", bus="campus", p_set=pd.Series(load_p, n.snapshots),
          q_set=pd.Series(load_p * TAN_LOAD, n.snapshots))
    if pq:
        tag_pq_link(n, "trafo", x_pu=X_TR, pf=PF_TR, s_n_mva=S_N, p_cap_mw=P_CAP, pf_req=PF_REQ,
                    statcom_capital_cost=C_STATCOM)
    return n


def _bess(n, name, p_mw=None, *, technology="high_c_bess", pcs=True, idle=True):
    add_split_storage(n, name, "campus", technology, p_nom=p_mw, power_capital_cost=50_000.0,
                      energy_capital_cost=1.0, e_nom_max=1e3)
    if idle:
        for end in ("charger", "discharger"):
            n.links.at[f"{name} {end}", "p_max_pu"] = 0.0
    if pcs:
        tag_pcs(n, name, r=0.2, capital_cost=C_PCS)


def _chain(n, cfg=None):
    fn = _wrap_with_split_storage(n, None)
    fn = _wrap_with_envelope_floors(n, fn, cfg)
    fn = _wrap_with_pcs_statcom(n, fn)
    return _wrap_with_sizing_cuts(n, fn)


def _solve(n, cfg=None):
    status, cond = n.optimize(solver_name="highs", extra_functionality=_chain(n, cfg))
    assert (status, cond) == ("ok", "optimal")
    return n


# ── no tags, no rows ────────────────────────────────────────────────────────


def test_an_untagged_network_gets_no_rows():
    n = _campus([100.0])
    sentinel = object()
    assert _wrap_with_sizing_cuts(n, sentinel) is sentinel


# ── import cap (GK A condition 3) ───────────────────────────────────────────


def test_the_import_cap_bounds_the_import_flow():
    n = _campus([100.0, 80.0])
    tag_cuts(n, "trafo", {"import_cap_mw": 90.0})
    _solve(n)
    p = n.links_t.p0["trafo"]
    assert p.max() == pytest.approx(90.0, abs=1e-6)
    assert n.generators_t.p["local"].iloc[0] == pytest.approx(10.0, abs=1e-6)


def test_the_import_cap_cannot_contradict_a_frozen_transformer():
    # The loop froze the transformer at 100 MVA (p_nom fixed); a cap below it leaves the LP feasible and the
    # p_nom untouched.
    n = _campus([100.0], link_p_nom=100.0)
    tag_cuts(n, "trafo", {"import_cap_mw": 90.0})
    _solve(n)
    assert n.links.at["trafo", "p_nom_opt"] == pytest.approx(100.0)
    assert n.links_t.p0["trafo"].iloc[0] == pytest.approx(90.0, abs=1e-6)


# ── GFM floor (the ESCR cut) ────────────────────────────────────────────────


def test_the_gfm_floor_sizes_the_gfm_store_power_and_pcs():
    n = _campus([100.0])
    _bess(n, "gfm", None)
    tag_gfm(n, "gfm")
    tag_cuts(n, "trafo", {"gfm_mva_min": 30.0})
    _solve(n)
    p = n.links.at["gfm charger", "p_nom_opt"]
    s_x = solution(n)["s_x"].get("gfm", 0.0)
    assert p + s_x == pytest.approx(30.0, abs=1e-5)


def test_a_store_not_flagged_gfm_does_not_count():
    n = _campus([100.0])
    _bess(n, "gfl", 40.0)
    _bess(n, "gfm", None)
    tag_gfm(n, "gfm")
    tag_cuts(n, "trafo", {"gfm_mva_min": 30.0})
    _solve(n)
    assert n.links.at["gfm charger", "p_nom_opt"] + solution(n)["s_x"]["gfm"] == pytest.approx(30.0, abs=1e-5)


def test_a_gfm_floor_with_no_gfm_store_is_refused():
    n = _campus([100.0])
    tag_cuts(n, "trafo", {"gfm_mva_min": 30.0})
    with pytest.raises(SizingCutsRefusal, match="gfm"):
        _chain(n)


# ── Pst row (AS-04 quantities) ──────────────────────────────────────────────


def test_the_pst_row_sizes_q_dyn_by_hand():
    # No smoothing tier: P_res = S + A = 40 MW, so cos psi x 40 = 9.7014. rhs = 10 leaves sin psi Q_res <= 0.2986,
    # Q_res <= 0.30779, and Q_dyn >= tan phi (S + A) - Q_res = 4 - 0.30779 = 3.69221 Mvar.
    n = _campus([100.0])
    _bess(n, "highC", 119.9)
    tag_cuts(n, "trafo", {"pst_rhs_mva": 10.0}, swing_mw=40.0, tan_phi_swing=0.1, xr=XR)
    _solve(n)
    q_dyn = solution(n)["q_dyn"]
    assert q_dyn == pytest.approx(4.0 - (10.0 - COS * 40.0) / SIN, abs=1e-6)
    assert q_dyn == pytest.approx(3.6922, abs=1e-4)


def test_the_pst_row_reads_the_smoothing_slice():
    # tan phi = 0: only P_res; rhs = cos psi x 10 MW asks for P_sm >= 30 MW.
    n = _campus([100.0])
    _bess(n, "fast", None, idle=False)
    tag_store(n, "fast", smoothing_tier=True)
    tag_cuts(n, "trafo", {"pst_rhs_mva": COS * 10.0}, swing_mw=40.0, tan_phi_swing=0.0, xr=XR)
    _solve(n)
    p_sm = float(n.model.variables["EnvelopeFloor-p_smooth"].solution.sum())
    assert p_sm == pytest.approx(30.0, abs=1e-5)


def test_the_pst_row_needs_its_swing_and_xr():
    n = _campus([100.0])
    tag_cuts(n, "trafo", {"pst_rhs_mva": 10.0})
    with pytest.raises(SizingCutsRefusal, match="pst_swing_mw"):
        _chain(n)


def test_the_pst_rhs_is_d_lim_times_the_minimum_short_circuit_power():
    from gridspine.static import flicker as fl
    from gridspine.static.island_sc_seq import c_factor

    r = fl.changes_per_min(60.0 / 2.0)                      # T = 2 s: 60 voltage changes per minute
    rhs = pst_rhs_mva(0.35, r, sk_mva=250.0)
    d_lim_pct = rhs / (250.0 / c_factor(None, "max")) * 100.0
    assert fl.pst(d_lim_pct, r) == pytest.approx(0.35, rel=1e-12)


def test_the_pst_rhs_tightens_by_the_voltage_shortfall():
    # rhs' = min(rhs, step_lp - (d - d_lim) / 100 x S_sc,min): the LP's own step, less the MVA the forward pass
    # found the voltage change over by.
    assert tightened_pst_rhs(5.0, step_lp_mva=4.8, d_pct=0.9, d_lim_pct=0.5, sk_min_mva=200.0) == \
        pytest.approx(4.8 - 0.4 / 100 * 200.0)
    assert tightened_pst_rhs(3.0, step_lp_mva=4.8, d_pct=0.9, d_lim_pct=0.5, sk_min_mva=200.0) == 3.0
    assert tightened_pst_rhs(None, step_lp_mva=4.8, d_pct=0.9, d_lim_pct=0.5, sk_min_mva=200.0) == \
        pytest.approx(4.0)


# ── PQ margin (in AS-05's pq row) ───────────────────────────────────────────


def test_the_pq_margin_adds_to_q_load():
    n = _campus([105.0])
    _bess(n, "highC", 119.9)
    q_need = 105.0 * TAN_LOAD + K_LOSS * 105.0 - TAN_REQ * 105.0
    tag_cuts(n, "trafo", {"pq_margin_mvar": 2.0})
    _solve(n)
    assert solution(n)["s_x"]["highC"] == pytest.approx((q_need + 2.0) / credit(0.2), rel=1e-6)


def test_a_negative_pq_margin_is_refused():
    from services.solver.pcs_statcom import PcsStatcomRefusal

    n = _campus([105.0])
    n.links.at["trafo", "pq_q_margin_mvar"] = -1.0
    with pytest.raises(PcsStatcomRefusal, match="pq_q_margin_mvar"):
        _wrap_with_pcs_statcom(n, None)


# ── refusals ────────────────────────────────────────────────────────────────


def test_cut_tags_on_two_links_are_refused():
    n = _campus([100.0])
    n.add("Link", "trafo2", bus0="grid", bus1="campus", p_nom=10.0)
    tag_cuts(n, "trafo", {"import_cap_mw": 90.0})
    tag_cuts(n, "trafo2", {"import_cap_mw": 5.0})
    with pytest.raises(SizingCutsRefusal, match="one grid connection"):
        _chain(n)


def test_an_unknown_cut_key_is_refused():
    with pytest.raises(ValueError, match="unknown"):
        tag_cuts(_campus([100.0]), "trafo", {"p_floor_mw": 1.0})


@pytest.mark.parametrize("col, value", [("cut_import_cap_mw", -1.0), ("cut_gfm_mva_min", float("inf")),
                                        ("pst_xr", -2.0)])
def test_an_out_of_range_tag_is_refused(col, value):
    n = _campus([100.0])
    tag_cuts(n, "trafo", {"import_cap_mw": 90.0, "pst_rhs_mva": 5.0}, swing_mw=40.0, tan_phi_swing=0.1, xr=XR)
    n.links.at["trafo", col] = value
    with pytest.raises(SizingCutsRefusal, match=col):
        _chain(n)


# ── fingerprint (GK A condition 5) ──────────────────────────────────────────


def _fingerprints(n):
    from types import SimpleNamespace

    from services import workflow_tools as wt

    ctx = SimpleNamespace(network=n, solver_state={"solver_config": {}}, user_ts={})
    return wt._network_input_fingerprint(ctx), wt._network_input_fingerprint(ctx, exact=True)


def test_a_pcs_statcom_network_keeps_masters_fingerprint():
    # Computed with master's `_network_input_fingerprint` (8bbddae1, before AS-06) on test_pcs_statcom's hand case.
    from tests import test_pcs_statcom as ps_tests

    assert _fingerprints(ps_tests._hand_case()) == (
        "34ac0d022d69b9188efe40499b455f02c3310ec5fb49b52a262cde4be300c6e1",
        "147ad4f7ee1cfe357582252a68a802e499c4ec77edff7ed7b1ffb5ee491c8aa3")


def test_an_all_blank_margin_column_leaves_the_fingerprint_unchanged():
    n = _campus([100.0])
    before = _fingerprints(n)
    n.links["pq_q_margin_mvar"] = np.nan
    for col in LINK_COLUMNS:
        n.links[col] = np.nan
    n.stores["gfm_capable"] = np.nan
    assert _fingerprints(n) == before


@pytest.mark.parametrize("frame, name, col, value", [
    ("links", "trafo", "pq_q_margin_mvar", 1.0),
    *[("links", "trafo", col, 2.5) for col in LINK_COLUMNS],
    *[("stores", "highC", col, 1.0) for col in STORE_COLUMNS],
])
def test_each_cut_tag_changes_both_fingerprints(frame, name, col, value):
    n = _campus([100.0])
    _bess(n, "highC", 119.9)
    before = _fingerprints(n)
    getattr(n, frame).at[name, col] = value
    after = _fingerprints(n)
    assert after[0] != before[0] and after[1] != before[1]


# ── the app's solve path ────────────────────────────────────────────────────


def test_run_simulation_applies_the_sizing_cuts():
    from services import solver_service
    from services.pypsa_service import PyPSAService

    n = _campus([100.0, 80.0])
    tag_cuts(n, "trafo", {"import_cap_mw": 90.0})
    PyPSAService.set_network(n)
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = solver_service.run_simulation(
        solver_service.SolverConfig(), n, PyPSAService.get_lock(), threading.Event(), log_q,
        state_update=lambda **kw: None)
    lines = []
    while not log_q.empty():
        lines.append(str(log_q.get()))
    assert status == "ok", (condition, [ln for ln in lines if "ERROR" in ln or "TRACEBACK" in ln])
    assert any("[SIZING]" in ln for ln in lines)
    assert n.links_t.p0["trafo"].max() == pytest.approx(90.0, abs=1e-6)


# ── each row is needed ──────────────────────────────────────────────────────


def _each_cut_case():
    n = _campus([100.0, 80.0])
    _bess(n, "gfm", None)
    tag_gfm(n, "gfm")
    tag_cuts(n, "trafo", {"import_cap_mw": 90.0, "gfm_mva_min": 30.0, "pst_rhs_mva": 10.0},
             swing_mw=40.0, tan_phi_swing=0.1, xr=XR)
    return n


def _each_cut_measures(n):
    return {"import_cap": float(n.links_t.p0["trafo"].max()),
            "gfm_floor": float(n.links.at["gfm charger", "p_nom_opt"] + solution(n)["s_x"]["gfm"]),
            "pst": solution(n)["q_dyn"]}


@pytest.mark.parametrize("row, holds", [("import_cap", lambda v: v <= 90.0 + 1e-6),
                                        ("gfm_floor", lambda v: v >= 30.0 - 1e-6),
                                        ("pst", lambda v: v >= 3.69 - 1e-3)])
def test_each_row_is_needed(monkeypatch, row, holds):
    assert holds(_each_cut_measures(_solve(_each_cut_case()))[row])
    monkeypatch.setattr(sc, "ROWS", tuple(r for r in sc.ROWS if r != row))
    assert not holds(_each_cut_measures(_solve(_each_cut_case()))[row])
