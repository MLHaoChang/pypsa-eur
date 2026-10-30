"""
AS-05 (`.scratch/asset-sizing-loop/issues/05-pcs-mva-statcom-coupling.md`): the PCS oversize S_x of a split
storage and a STATCOM split into a steady slice Q_ss and a dynamic slice Q_dyn. `_wrap_with_pcs_statcom` adds:

  * S_x <= r x P per tagged store (P the charger p_nom, AS-02), so a PCS with no battery behind it carries none;
  * a(t) >= p_tr(t) and a(t) >= -p_tr(t) on the grid-connection Link;
  * per hour, Q_ss + sum c_i S_x,i - (x p_cap / (pf^2 S_n)) a(t) + tan(acos pf_req) p_tr(t) >= Q_load(t),
    with c_i = sqrt(1 + 2 / r_i), the chord of the PQ circle on [0, r P];
  * the PCS and STATCOM costs in the objective. Q_dyn sits in no AS-05 row: AS-06 adds the Pst cut on it.

The hand values below use generic engineering parameters marked "assumed" (SAGC-1) and the illustrative
constants of the AS prototype (`.scratch/datacenter-grid-readiness/research/prototype-as-loop/`).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.solver import pcs_statcom as ps
from services.solver.pcs_statcom import (
    LINK_COLUMNS,
    STORE_COLUMNS,
    PcsStatcomRefusal,
    _wrap_with_pcs_statcom,
    credit,
    solution,
    tag_pcs,
    tag_pq_link,
)
from services.solver.split_storage import _wrap_with_split_storage, add_split_storage

# Prototype constants (illustrative, "assumed").
X_TR, PF_TR, S_N, P_CAP, PF_REQ, PF_LOAD = 0.12, 0.98, 125.0, 122.5, 0.98, 0.95
C_PCS, C_STATCOM = 6_000.0, 8_000.0
K_LOSS = X_TR * P_CAP / (PF_TR**2 * S_N)                    # 0.122449 Mvar per MW of |p_tr|
TAN_REQ = math.tan(math.acos(PF_REQ))                       # 0.203069 Mvar per MW of p_tr
TAN_LOAD = math.tan(math.acos(PF_LOAD))                     # 0.328684


def _campus(load_p, load_q, *, link_p_nom=S_N, link_p_max_pu=PF_TR, link_p_min_pu=0.0, campus_gen=None):
    """grid -> trafo Link -> campus. The load's Q is the native `q_set`; the Link carries the PQ tags."""
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(len(load_p)))
    n.snapshot_weightings.loc[:, "objective"] = 8760 / len(load_p)      # one modelled year: annual costs as typed
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "campus", carrier="AC")
    n.add("Generator", "import", bus="grid", p_nom=1e3, p_min_pu=-1.0, marginal_cost=10.0)
    n.add("Link", "trafo", bus0="grid", bus1="campus", p_nom=link_p_nom, p_max_pu=link_p_max_pu,
          p_min_pu=link_p_min_pu)
    n.add("Load", "dc", bus="campus", p_set=pd.Series(load_p, n.snapshots, dtype=float),
          q_set=pd.Series(load_q, n.snapshots, dtype=float))
    if campus_gen is not None:
        prof = pd.Series(campus_gen, n.snapshots, dtype=float) / max(max(campus_gen), 1e-9)
        n.add("Generator", "pv", bus="campus", p_nom=max(campus_gen), p_max_pu=prof, p_min_pu=prof)
    tag_pq_link(n, "trafo", x_pu=X_TR, pf=PF_TR, s_n_mva=S_N, p_cap_mw=P_CAP, pf_req=PF_REQ,
                statcom_capital_cost=C_STATCOM)
    return n


def _bess(n, name, p_mw, r=0.2, technology="high_c_bess"):
    """A split storage with P fixed at `p_mw` that never dispatches, so the transformer flow is the load."""
    add_split_storage(n, name, "campus", technology, p_nom=p_mw, power_capital_cost=0.0,
                      energy_capital_cost=1.0, e_nom_max=1e3)
    for end in ("charger", "discharger"):
        n.links.at[f"{name} {end}", "p_max_pu"] = 0.0
    tag_pcs(n, name, r=r, capital_cost=C_PCS)


def _then(n, after):
    """The AS rows, then `after` (as AS-06 chains its Pst cut on Q_dyn after AS-05)."""
    fn = _wrap_with_pcs_statcom(n, _wrap_with_split_storage(n, None))

    def chained(n, sns):
        fn(n, sns)
        if after is not None:
            after(n, sns)

    return chained


def _solve(n, after=None, **solver_options):
    status, _ = n.optimize(solver_name="highs", extra_functionality=_then(n, after), **solver_options)
    assert status == "ok"
    return solution(n)


# ── the credit ───────────────────────────────────────────────────────────────


def test_r_0_2_gives_a_credit_of_sqrt_11():
    assert credit(0.2) == pytest.approx(math.sqrt(11.0), abs=1e-12)
    assert credit(0.2) == pytest.approx(3.3166, abs=1e-4)


@pytest.mark.parametrize("r", [0.05, 0.1, 0.2, 0.5, 1.0])
@pytest.mark.parametrize("p", [1.0, 50.0, 119.9])
def test_the_inner_bound_never_exceeds_the_exact_circle(r, p):
    # sqrt((P + S_x)^2 - P^2) is concave in S_x, so its chord on [0, r P] lies below it.
    for s_x in np.linspace(0.0, r * p, 11):
        exact = math.sqrt((p + s_x) ** 2 - p**2)
        assert credit(r) * s_x <= exact + 1e-9


# ── hand case (GK A condition 6): P_highC = 119.9 MW, transformer flow fixed at the load ──


def _hand_case():
    n = _campus([105.0], [105.0 * TAN_LOAD])
    _bess(n, "highC", 119.9)
    return n


# Q need at p_tr = 105 MW: Q_load + K_LOSS |p| - TAN_REQ p = 34.5118 + 12.8571 - 21.3222 = 26.0467 Mvar.
HAND_Q_NEED = 105.0 * TAN_LOAD + K_LOSS * 105.0 - TAN_REQ * 105.0
HAND_S_X = HAND_Q_NEED / math.sqrt(11.0)                    # 7.8535 MVA


def check_hand_case():
    sol = _solve(_hand_case())
    assert HAND_Q_NEED == pytest.approx(26.047, abs=1e-3)
    assert sol["s_x"]["highC"] == pytest.approx(HAND_S_X, rel=1e-6)
    assert sol["s_x"]["highC"] == pytest.approx(7.854, abs=1e-3)   # the prototype's final S_x
    assert sol["q_ss"] == pytest.approx(0.0, abs=1e-6)               # PCS Mvar is cheaper than STATCOM Mvar
    assert sol["q_dyn"] == pytest.approx(0.0, abs=1e-6)              # no Pst cut in AS-05


def test_hand_case_sizes_the_pcs_oversize_not_a_statcom():
    check_hand_case()


def test_the_exact_circle_covers_what_the_inner_bound_credited():
    # Forward check: the exact circle at rated P gives more Q than the LP's inner bound.
    p, s_x = 119.9, HAND_S_X
    exact = math.sqrt((p + s_x) ** 2 - p**2)
    assert exact == pytest.approx(44.1, abs=0.05)
    assert math.sqrt(11.0) * s_x == pytest.approx(26.05, abs=0.01)
    assert exact >= math.sqrt(11.0) * s_x


def test_prototype_pst_row_on_q_dyn_gives_3_4_mvar_and_leaves_s_x():
    """The revised Pst cut of the prototype (AS-06's row, added here by the test) on the weak grid:
    S_sc = 250 MVA, X/R = 4, P_res = S + A - P_sm = 0.1 MW, Q_res >= tan phi_swing (S + A) - Q_dyn = 4.0 - Q_dyn,
    cos psi 2 P_res + sin psi Q_res <= d_lim S_sc with d_lim = 0.35 / ((23 x 60 / 600)^(1/3.2)) / 100."""
    psi = math.atan(4.0)
    d_lim = 0.35 / (23.0 * 60.0 / 600.0) ** (1 / 3.2) / 100.0
    bound = d_lim * 250.0
    n = _hand_case()

    def pst(n, sns):
        m = n.model
        q_res = m.add_variables(lower=0, name="Pst-q_res")
        m.add_constraints(q_res + m["Statcom-q_dyn"] >= 0.1 * 40.0, name="Pst-q_res_floor")
        m.add_constraints(math.cos(psi) * 2 * 0.1 + math.sin(psi) * q_res <= bound, name="Pst-cut")

    sol = _solve(n, pst)
    q_dyn = 4.0 - (bound - math.cos(psi) * 0.2) / math.sin(psi)              # 3.3547 Mvar by hand
    assert sol["q_dyn"] == pytest.approx(q_dyn, rel=1e-6)
    assert sol["q_dyn"] == pytest.approx(3.4, abs=0.05)
    assert sol["s_x"]["highC"] == pytest.approx(HAND_S_X, rel=1e-6)          # not 28 Mvar of STATCOM
    assert sol["q_ss"] == pytest.approx(0.0, abs=1e-6)


def test_a_dynamic_floor_is_not_met_by_pcs_oversize():
    # The swap of the note's cuts: if one Mvar counted in both cuts, raising the dynamic need would trade
    # S_x for STATCOM. With separate slices Q_dyn rises one for one and S_x stays put.
    out = []
    for floor in (0.0, 3.0, 6.0):
        n = _hand_case()

        def dyn(n, sns, floor=floor):
            n.model.add_constraints(n.model["Statcom-q_dyn"] >= floor, name="Test-q_dyn_floor")

        out.append(_solve(n, dyn))
    for floor, sol in zip((0.0, 3.0, 6.0), out):
        assert sol["q_dyn"] == pytest.approx(floor, abs=1e-6)
        assert sol["q_ss"] == pytest.approx(0.0, abs=1e-6)
        assert sol["s_x"]["highC"] == pytest.approx(HAND_S_X, rel=1e-6)


# ── per hour, on the flow variable ──────────────────────────────────────────


def check_per_hour_cut_reads_the_flow():
    # Same Q_load both hours; flows of 50 and 100 MW. Needs differ by (K_LOSS - TAN_REQ) x 50 MW.
    q = 40.0
    n = _campus([50.0, 100.0], [q, q])
    _bess(n, "highC", 119.9)
    sol = _solve(n)
    need = {p: q + K_LOSS * p - TAN_REQ * p for p in (50.0, 100.0)}
    assert need[50.0] - need[100.0] == pytest.approx((TAN_REQ - K_LOSS) * 50.0)
    assert sol["s_x"]["highC"] == pytest.approx(max(need.values()) / math.sqrt(11.0), rel=1e-6)
    # The row's coefficient on Link-p is the PF allowance, on the flow variable, not a constant.
    con = n.model.constraints["PQ-pq"]
    assert "Link-p" in str(con.lhs)
    assert len(con.data.coords["snapshot"]) == 2


def test_per_hour_cut_reads_the_flow():
    check_per_hour_cut_reads_the_flow()


def check_export_hour_counts_the_loss_and_no_allowance():
    # Hour 0: 50 MW import, no Q load. Hour 1: 80 MW of campus generation against a 30 MW load, 50 MW export.
    # Export need: 0 + K_LOSS x 50 - TAN_REQ x (-50) = 16.276 Mvar (loss positive, allowance not credited).
    n = _campus([50.0, 30.0], [0.0, 0.0], link_p_min_pu=-PF_TR, campus_gen=[0.0, 80.0])
    _bess(n, "highC", 119.9)
    sol = _solve(n)
    need = K_LOSS * 50.0 + TAN_REQ * 50.0
    assert need == pytest.approx(16.276, abs=1e-3)
    assert sol["s_x"]["highC"] == pytest.approx(need / math.sqrt(11.0), rel=1e-6)


def test_export_hour_counts_the_loss_and_no_allowance():
    check_export_hour_counts_the_loss_and_no_allowance()


# ── the S_x cap ─────────────────────────────────────────────────────────────


def check_sx_cap_and_statcom_tops_up():
    # P = 10 MW: S_x <= 2 MVA gives 6.633 Mvar; the rest of the hand need comes from Q_ss.
    n = _campus([105.0], [105.0 * TAN_LOAD])
    _bess(n, "small", 10.0)
    sol = _solve(n)
    assert sol["s_x"]["small"] == pytest.approx(2.0, rel=1e-6)
    assert sol["q_ss"] == pytest.approx(HAND_Q_NEED - 2.0 * math.sqrt(11.0), rel=1e-6)


def test_sx_cap_and_statcom_tops_up():
    check_sx_cap_and_statcom_tops_up()


def test_a_pcs_with_no_battery_is_priced_as_a_statcom():
    n = _campus([105.0], [105.0 * TAN_LOAD])
    _bess(n, "empty", 0.0)
    sol = _solve(n)
    assert sol["s_x"]["empty"] == pytest.approx(0.0, abs=1e-9)
    assert sol["q_ss"] == pytest.approx(HAND_Q_NEED, rel=1e-6)


def test_an_extendable_battery_sizes_p_and_s_x_together():
    # P is free (priced): S_x <= 0.2 P reads the P variable, so the LP buys enough P to carry the oversize.
    n = _campus([105.0], [105.0 * TAN_LOAD])
    add_split_storage(n, "bess", "campus", "high_c_bess", power_capital_cost=100.0, energy_capital_cost=1.0)
    for end in ("charger", "discharger"):
        n.links.at[f"bess {end}", "p_max_pu"] = 0.0
    tag_pcs(n, "bess", r=0.2, capital_cost=C_PCS)
    sol = _solve(n)
    p = float(n.links.at["bess charger", "p_nom_opt"])
    assert sol["s_x"]["bess"] == pytest.approx(HAND_S_X, rel=1e-6)
    assert p == pytest.approx(HAND_S_X / 0.2, rel=1e-6)


def check_mixed_r_gives_each_store_its_own_credit():
    # A: P = 10, r = 0.2 -> S_x <= 2, c = sqrt(11) = 3.3166. B: P = 10, r = 0.5 -> S_x <= 5, c = sqrt(5) = 2.2361.
    # Both are cheaper per Mvar than the STATCOM (1809 and 2683 against 8000 EUR), so both fill their caps.
    n = _campus([105.0], [105.0 * TAN_LOAD])
    _bess(n, "a", 10.0, r=0.2)
    _bess(n, "b", 10.0, r=0.5)
    sol = _solve(n)
    assert sol["s_x"]["a"] == pytest.approx(2.0, rel=1e-6)
    assert sol["s_x"]["b"] == pytest.approx(5.0, rel=1e-6)
    assert sol["q_ss"] == pytest.approx(HAND_Q_NEED - 2.0 * math.sqrt(11.0) - 5.0 * math.sqrt(5.0), rel=1e-6)


def test_mixed_r_gives_each_store_its_own_credit():
    check_mixed_r_gives_each_store_its_own_credit()


def check_objective_carries_both_costs():
    n = _campus([105.0], [105.0 * TAN_LOAD])
    _bess(n, "small", 10.0)
    sol = _solve(n)
    with_rows = float(n.objective)
    base = _campus([105.0], [105.0 * TAN_LOAD])
    _bess(base, "small", 10.0)
    base.links.loc["trafo", list(LINK_COLUMNS)] = np.nan
    base.stores.loc["small", list(STORE_COLUMNS)] = np.nan
    assert base.optimize(solver_name="highs", extra_functionality=_wrap_with_split_storage(base, None))[0] == "ok"
    extra = C_PCS * sol["s_x"]["small"] + C_STATCOM * (sol["q_ss"] + sol["q_dyn"])
    assert sol["q_ss"] > 1.0 and sol["s_x"]["small"] > 1.0
    assert with_rows - float(base.objective) == pytest.approx(extra, rel=1e-6)


def test_objective_carries_both_costs():
    check_objective_carries_both_costs()


# ── mutation: drop each row, its own test fails (GK A condition 6) ──────────


@pytest.mark.parametrize("row, check", [
    ("sx_cap", check_sx_cap_and_statcom_tops_up),
    ("sx_cap", check_mixed_r_gives_each_store_its_own_credit),
    ("pq", check_hand_case),
    ("abs_pos", check_hand_case),
    ("abs_neg", check_export_hour_counts_the_loss_and_no_allowance),
    ("pcs_cost", check_objective_carries_both_costs),
    ("statcom_cost", check_hand_case),
])
def test_dropping_a_row_fails_its_own_test(monkeypatch, row, check):
    check()                                                      # passes with every row
    monkeypatch.setattr(ps, "ROWS", tuple(r for r in ps.ROWS if r != row))
    with pytest.raises(AssertionError):
        check()


# ── refusals before the solve (GK A conditions 2 and 3) ─────────────────────


def test_untagged_network_leaves_the_user_function_unchanged():
    n = _campus([105.0], [30.0])
    n.links.loc["trafo", list(LINK_COLUMNS)] = np.nan

    def user(n, sns):
        pass

    assert _wrap_with_pcs_statcom(n, user) is user
    plain = pypsa.Network()
    assert _wrap_with_pcs_statcom(plain, None) is None


def test_a_tag_on_a_store_that_is_not_a_split_storage_is_refused():
    n = _campus([105.0], [30.0])
    n.add("Bus", "h2")
    n.add("Store", "tank", bus="h2", e_nom=10.0)
    n.stores["pcs_r_max"] = np.nan
    n.stores.at["tank", "pcs_r_max"] = 0.2
    n.stores["pcs_capital_cost"] = np.nan
    n.stores.at["tank", "pcs_capital_cost"] = C_PCS
    with pytest.raises(PcsStatcomRefusal, match="tank"):
        _wrap_with_pcs_statcom(n, None)


def test_a_pcs_tag_without_a_pq_link_is_refused():
    n = _campus([105.0], [30.0])
    _bess(n, "highC", 119.9)
    n.links.loc["trafo", list(LINK_COLUMNS)] = np.nan
    with pytest.raises(PcsStatcomRefusal, match="highC"):
        _wrap_with_pcs_statcom(n, None)


def test_a_partly_tagged_link_is_refused():
    n = _campus([105.0], [30.0])
    n.links.at["trafo", "pq_s_n_mva"] = np.nan
    with pytest.raises(PcsStatcomRefusal, match="trafo.*pq_s_n_mva"):
        _wrap_with_pcs_statcom(n, None)


def test_a_link_with_no_campus_load_on_bus1_is_refused():
    n = _campus([105.0], [30.0])
    n.links.at["trafo", "bus0"], n.links.at["trafo", "bus1"] = "campus", "grid"
    with pytest.raises(PcsStatcomRefusal, match="trafo.*grid"):
        _wrap_with_pcs_statcom(n, None)


def test_two_pq_links_are_refused():
    n = _campus([105.0], [30.0])
    n.add("Link", "trafo2", bus0="grid", bus1="campus", p_nom=10.0)
    for col in LINK_COLUMNS:
        n.links.at["trafo2", col] = n.links.at["trafo", col]
    with pytest.raises(PcsStatcomRefusal, match="trafo2"):
        _wrap_with_pcs_statcom(n, None)


@pytest.mark.parametrize("link", [
    dict(link_p_nom=130.0, link_p_max_pu=1.0),                # 130 MW > p_cap 122.5
    dict(link_p_nom=125.0, link_p_max_pu=0.5, link_p_min_pu=-1.0),   # export reach 125 MW
])
def test_a_link_that_can_exceed_the_chord_range_is_refused(link):
    n = _campus([105.0], [30.0], **link)
    with pytest.raises(PcsStatcomRefusal, match="trafo.*122.5"):
        _wrap_with_pcs_statcom(n, None)


def test_an_extendable_link_is_checked_on_its_p_nom_max():
    n = _campus([105.0], [30.0])
    n.links.at["trafo", "p_nom_extendable"] = True
    n.links.at["trafo", "p_nom_max"] = math.inf
    with pytest.raises(PcsStatcomRefusal, match="trafo"):
        _wrap_with_pcs_statcom(n, None)
    n.links.at["trafo", "p_nom_max"] = 125.0
    assert _wrap_with_pcs_statcom(n, None) is not None


@pytest.mark.parametrize("col, value", [("pq_pf", 1.2), ("pq_s_n_mva", 0.0), ("pq_p_cap_mw", -1.0),
                                        ("pq_pf_req", 0.0), ("pq_x_pu", -0.1), ("statcom_capital_cost", -1.0)])
def test_bad_link_values_are_refused(col, value):
    n = _campus([105.0], [30.0])
    n.links.at["trafo", col] = value
    with pytest.raises(PcsStatcomRefusal, match=col):
        _wrap_with_pcs_statcom(n, None)


@pytest.mark.parametrize("col, value", [("pcs_r_max", 0.0), ("pcs_capital_cost", -1.0)])
def test_bad_store_values_are_refused(col, value):
    n = _campus([105.0], [30.0])
    _bess(n, "highC", 119.9)
    n.stores.at["highC", col] = value
    with pytest.raises(PcsStatcomRefusal, match=col):
        _wrap_with_pcs_statcom(n, None)


def test_a_multi_period_solve_is_refused():
    periods = [2030, 2035]
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product([periods, pd.RangeIndex(2)], names=["period", "timestep"]))
    n.investment_periods = periods
    n.add("Bus", "grid")
    n.add("Bus", "campus")
    n.add("Generator", "import", bus="grid", p_nom=1e3, marginal_cost=10.0)
    n.add("Link", "trafo", bus0="grid", bus1="campus", p_nom=S_N, p_max_pu=PF_TR)
    n.add("Load", "dc", bus="campus", p_set=50.0, q_set=10.0)
    tag_pq_link(n, "trafo", x_pu=X_TR, pf=PF_TR, s_n_mva=S_N, p_cap_mw=P_CAP, pf_req=PF_REQ,
                statcom_capital_cost=C_STATCOM)
    fn = _wrap_with_pcs_statcom(n, None)
    with pytest.raises(PcsStatcomRefusal, match="one investment period"):
        n.optimize(solver_name="highs", extra_functionality=fn, multi_investment_periods=True)


# ── persistence and fingerprint (GK A conditions 4 and 5) ───────────────────


def test_the_tags_survive_a_netcdf_round_trip(tmp_path):
    from services.pypsa_service import PyPSAService

    n = _hand_case()
    path = tmp_path / "n.nc"
    PyPSAService.export_network_to_netcdf(n, path)
    back = pypsa.Network()
    PyPSAService.import_network_from_netcdf(back, path)
    assert back.stores.loc["highC", list(STORE_COLUMNS)].tolist() == n.stores.loc["highC", list(STORE_COLUMNS)].tolist()
    assert back.links.loc["trafo", list(LINK_COLUMNS)].tolist() == n.links.loc["trafo", list(LINK_COLUMNS)].tolist()
    assert _solve(back)["s_x"]["highC"] == pytest.approx(HAND_S_X, rel=1e-6)


def _fingerprints(n):
    from types import SimpleNamespace

    from services import workflow_tools as wt

    ctx = SimpleNamespace(network=n, solver_state={"solver_config": {}}, user_ts={})
    return wt._network_input_fingerprint(ctx), wt._network_input_fingerprint(ctx, exact=True)


def test_an_untagged_network_keeps_masters_fingerprint():
    # Computed with master's `_network_input_fingerprint` (f91d43c47, before AS-05) on this network. No Link:
    # PyPSA registers extra Link ports (bus2...) on the process-wide defaults once any network uses them, so a
    # hash of a Link network depends on which tests ran before it (see test_extras_passthrough).
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(2))
    n.add("Bus", "campus")
    n.add("Generator", "import", bus="campus", p_nom=200, marginal_cost=10.0)
    n.add("Load", "dc", bus="campus", p_set=[100.0, 105.0], q_set=[30.0, 34.5])
    assert _fingerprints(n) == ("ae6082a31962e2c70073341e1662b3259204edc0a0c09052bf2a5298d44fa922",
                                "a5561ccbaa135de5db22096dd74899f7a92be69b854b85cdf2c71176ce0196ed")


def _link_network():
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(2))
    n.add("Bus", "grid")
    n.add("Bus", "campus")
    n.add("Generator", "import", bus="grid", p_nom=200, marginal_cost=10.0)
    n.add("Link", "trafo", bus0="grid", bus1="campus", p_nom=150, efficiency=0.99)
    n.add("Load", "dc", bus="campus", p_set=[100.0, 105.0], q_set=[30.0, 34.5])
    return n


def _load_a_three_port_link():
    # Registers bus2 / efficiency2 on PyPSA's process-wide Link defaults, as a CHP project does in the API.
    m = pypsa.Network()
    m.add("Bus", ["x", "y", "z"])
    m.add("Link", "chp", bus0="x", bus1="y", bus2="z", efficiency2=0.5)


def test_an_untagged_link_network_keeps_masters_fingerprint():
    # Computed with master's `_network_input_fingerprint` (b421ad19) in a fresh process, before any network
    # registered extra Link ports. It must hold whatever ran before it in this process.
    _load_a_three_port_link()
    assert _fingerprints(_link_network()) == ("2e5174230c47db442708bd8bdd09c4535fbe737ed85a9b2cfbff67e0ab8eb090",
                                              "c6f3feb6a69782b4747c573278da75f93784787be0181dbf8c4d8fca28b05b0e")


def test_a_link_networks_fingerprint_ignores_ports_another_network_registered():
    before = _fingerprints(_link_network())
    _load_a_three_port_link()
    after = _link_network()
    assert {"bus2", "efficiency2"} <= set(after.links.columns)
    assert _fingerprints(after) == before
    # As built before any registration, whatever this process loaded earlier.
    after.links.drop(columns=[c for c in after.links.columns if c in ("bus2", "efficiency2")], inplace=True)
    after.links_t.pop("efficiency2", None)
    assert _fingerprints(after) == before


def test_a_populated_extra_port_still_changes_the_fingerprint():
    n = _link_network()
    n.add("Bus", "heat")
    before = _fingerprints(n)
    n.links.loc["trafo", "bus2"] = "heat"
    with_port = _fingerprints(n)
    assert with_port[0] != before[0] and with_port[1] != before[1]
    n.links.loc["trafo", "efficiency2"] = 0.4
    assert _fingerprints(n)[0] != with_port[0]


def test_an_untagged_link_network_appends_nothing(monkeypatch):
    # The same network with the AS-05 append switched off gives the same bytes in this process.
    n = _campus([100.0, 105.0], [30.0, 34.5])
    n.links.loc["trafo", list(LINK_COLUMNS)] = np.nan
    with_append = _fingerprints(n)
    monkeypatch.setattr(ps, "pcs_statcom_tags", lambda n: (None, None))
    assert _fingerprints(n) == with_append


@pytest.mark.parametrize("frame, name, col, value", [
    ("stores", "highC", "pcs_r_max", 0.3),
    ("stores", "highC", "pcs_capital_cost", 7_000.0),
    *[("links", "trafo", col, 0.5) for col in LINK_COLUMNS],
])
def test_editing_a_tag_or_cost_changes_both_fingerprints(frame, name, col, value):
    n = _hand_case()
    before = _fingerprints(n)
    getattr(n, frame).at[name, col] = value
    after = _fingerprints(n)
    assert after[0] != before[0] and after[1] != before[1]


# ── the app's solve path ────────────────────────────────────────────────────


def test_run_simulation_applies_the_pcs_statcom_rows():
    import queue
    import threading

    from services import solver_service
    from services.pypsa_service import PyPSAService

    n = _hand_case()
    PyPSAService.set_network(n)
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = solver_service.run_simulation(
        solver_service.SolverConfig(), n, PyPSAService.get_lock(), threading.Event(), log_q,
        state_update=lambda **kw: None)
    lines = []
    while not log_q.empty():
        lines.append(str(log_q.get()))
    assert status == "ok", (condition, [ln for ln in lines if "ERROR" in ln or "TRACEBACK" in ln])
    assert any("[PQ]" in ln and "PCS/STATCOM rows" in ln for ln in lines)


# ── regression: the prototype week (source: prototype-as-loop/as_loop_prototype.py, weak-flat, intent D) ──


def test_prototype_week_regression():
    """The prototype's 168 h load (seed 7), P_highC = 119.9 MW from its final round, flow = load. The per-hour
    cut picks the worst hour; the prototype ends at S_x = 7.854 MVA (RESULTS.md: about 7.9)."""
    rng = np.random.default_rng(7)
    hours = np.arange(168)
    train = (rng.random(168) > 0.2).astype(float)
    load = 70 + 30.0 * train + 5 * np.sin(2 * np.pi * (hours - 14) / 24)
    n = _campus(load, load * TAN_LOAD)
    _bess(n, "highC", 119.9)
    sol = _solve(n, time_limit=120.0)                       # HiGHS stops the run after 120 s
    assert sol["s_x"]["highC"] == pytest.approx(7.854, abs=0.05)
    assert sol["q_ss"] == pytest.approx(0.0, abs=1e-6)


def test_annual_costs_are_charged_per_modelled_horizon_on_a_168_h_week():
    """GK A: the typed annual costs scale by `fom_horizon_factor(n)` = 168 / 8760 on an unweighted week, as the
    app scales every typed annual capital_cost; the sizes do not move, since both costs scale alike."""
    from services.solver.periodized_costs import fom_horizon_factor

    rng = np.random.default_rng(7)
    hours = np.arange(168)
    load = 70 + 30.0 * (rng.random(168) > 0.2) + 5 * np.sin(2 * np.pi * (hours - 14) / 24)

    def week(tagged):
        n = _campus(load, load * TAN_LOAD)
        n.snapshot_weightings.loc[:, "objective"] = 1.0
        _bess(n, "small", 10.0)
        if not tagged:
            n.links.loc["trafo", list(LINK_COLUMNS)] = np.nan
            n.stores.loc["small", list(STORE_COLUMNS)] = np.nan
        return n

    n, base = week(True), week(False)
    assert fom_horizon_factor(n) == pytest.approx(168 / 8760)
    sol = _solve(n, time_limit=120.0)
    assert base.optimize(solver_name="highs", extra_functionality=_wrap_with_split_storage(base, None))[0] == "ok"
    assert sol["s_x"]["small"] == pytest.approx(2.0, rel=1e-6)
    assert sol["q_ss"] > 1.0
    extra = 168 / 8760 * (C_PCS * sol["s_x"]["small"] + C_STATCOM * (sol["q_ss"] + sol["q_dyn"]))
    assert float(n.objective) - float(base.objective) == pytest.approx(extra, rel=1e-6)
