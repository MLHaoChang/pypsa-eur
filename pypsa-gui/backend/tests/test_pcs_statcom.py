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
    # Computed with master's `_network_input_fingerprint` (f91d43c47, before AS-05) on this network. No Link,
    # so the extra-port filter leaves it untouched; the Link pin is below.
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
    n.add("Store", "bess", bus="campus", e_nom=40.0, e_cyclic=True, marginal_cost=0.5)
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
    assert _fingerprints(_link_network()) == ("b610fc3f01d827edf11e351450d8c9f48cc40feb0753b875ded5d27404866fa6",
                                              "a9ab52d602e68025d493832ed1838e56d04a0d0e4876cbe062b741d1d0945dc0")


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
    with_efficiency = _fingerprints(n)
    assert with_efficiency[0] != with_port[0] and with_efficiency[1] != with_port[1]


def test_a_time_varying_extra_port_efficiency_changes_the_fingerprint():
    n = _link_network()
    n.add("Bus", "heat")
    n.links.loc["trafo", "bus2"] = "heat"
    before = _fingerprints(n)
    n.links_t["efficiency2"] = pd.DataFrame({"trafo": [0.4, 0.5]}, index=n.snapshots)
    after = _fingerprints(n)
    assert after[0] != before[0] and after[1] != before[1]


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


# ── AS-12: the solved sizes outlive the model, and cost_breakdown carries their cost ──


def _run(n, cfg=None, expect="ok"):
    """The app's solve path, which persists the sizes once the solve succeeds. Returns the config it ran."""
    import queue
    import threading

    from services import solver_service
    from services.pypsa_service import PyPSAService

    cfg = cfg if cfg is not None else solver_service.SolverConfig()
    PyPSAService.set_network(n)
    status, condition = solver_service.run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(), queue.SimpleQueue(), state_update=lambda **kw: None)
    assert (status == "ok") == (expect == "ok"), condition
    return cfg


def _round_trip(n, tmp_path):
    from services.pypsa_service import PyPSAService

    path = tmp_path / "solved.nc"
    PyPSAService.export_network_to_netcdf(n, path)
    back = pypsa.Network()
    PyPSAService.import_network_from_netcdf(back, path)
    return back


def _persisted(n):
    return (n.stores["pcs_s_x_opt"].dropna().to_dict(), float(n.links.at["trafo", "statcom_q_ss_opt"]),
            float(n.links.at["trafo", "statcom_q_dyn_opt"]))


def test_result_columns_are_not_tags():
    assert not set(ps.RESULT_STORE_COLUMNS) & set(STORE_COLUMNS)
    assert not set(ps.RESULT_LINK_COLUMNS) & set(LINK_COLUMNS)


def test_run_simulation_persists_s_x_q_ss_and_q_dyn():
    n = _campus([105.0], [105.0 * TAN_LOAD])
    _bess(n, "small", 10.0)
    _run(n)
    s_x, q_ss, q_dyn = _persisted(n)
    assert s_x == {"small": pytest.approx(2.0, rel=1e-6)}
    assert q_ss == pytest.approx(HAND_Q_NEED - 2.0 * math.sqrt(11.0), rel=1e-6)
    assert q_dyn == pytest.approx(0.0, abs=1e-6)


def test_persist_solution_keeps_q_dyn_and_survives_a_netcdf_round_trip(tmp_path):
    n = _hand_case()

    def dyn(n, sns):
        n.model.add_constraints(n.model["Statcom-q_dyn"] >= 3.0, name="Test-q_dyn_floor")

    sol = _solve(n, dyn)
    ps.persist_solution(n)
    del n.model                                             # what the forward check and AS-06 see later
    assert _persisted(n) == (sol["s_x"].to_dict(), sol["q_ss"], sol["q_dyn"])
    assert _persisted(n)[2] == pytest.approx(3.0, abs=1e-6)
    back = _round_trip(n, tmp_path)
    assert _persisted(back) == _persisted(n)
    assert back.stores.loc["highC", list(STORE_COLUMNS)].tolist() == n.stores.loc["highC", list(STORE_COLUMNS)].tolist()


def test_persisted_results_leave_both_fingerprints_unchanged():
    n = _hand_case()
    _solve(n)
    before = _fingerprints(n)
    ps.persist_solution(n)
    assert n.stores.at["highC", "pcs_s_x_opt"] > 0
    assert _fingerprints(n) == before


def test_an_untagged_solve_clears_stale_results():
    n = _hand_case()
    _solve(n)
    ps.persist_solution(n)
    n.links.loc["trafo", list(LINK_COLUMNS)] = np.nan
    n.stores.loc["highC", list(STORE_COLUMNS)] = np.nan
    assert n.optimize(solver_name="highs", extra_functionality=_wrap_with_split_storage(n, None))[0] == "ok"
    ps.persist_solution(n)
    assert n.stores["pcs_s_x_opt"].isna().all()
    assert n.links[list(ps.RESULT_LINK_COLUMNS)].isna().all().all()
    assert ps.cost_terms(n) == {}


def test_an_untagged_network_gains_no_result_columns():
    n = _campus([105.0], [105.0 * TAN_LOAD])
    n.links.loc["trafo", list(LINK_COLUMNS)] = np.nan
    assert n.optimize(solver_name="highs")[0] == "ok"
    ps.persist_solution(n)
    assert not set(ps.RESULT_LINK_COLUMNS) & set(n.links.columns)
    assert not set(ps.RESULT_STORE_COLUMNS) & set(n.stores.columns)


def test_a_failed_re_solve_leaves_no_stale_sizes():
    n = _campus([105.0], [105.0 * TAN_LOAD])
    _bess(n, "small", 10.0)
    _run(n)
    assert ps.cost_terms(n)
    n.loads_t.p_set.loc[:, "dc"] = 1_000.0                 # beyond the transformer: infeasible
    _run(n, expect="fail")
    assert n.stores["pcs_s_x_opt"].isna().all()
    assert n.links[list(ps.RESULT_LINK_COLUMNS)].isna().all().all()
    assert ps.cost_terms(n) == {}


@pytest.mark.parametrize("weight, years, scale", [(8760.0, 1.0, 1.0), (1.0, 1 / 8760, 1.0), (8760.0, 1.0, 1e-3)])
def test_cost_breakdown_carries_pcs_and_statcom_cost_and_the_objective_reconciles(tmp_path, weight, years, scale):
    """One snapshot weighted as a year, the same snapshot unweighted (one hour: the typed annual costs are
    charged x 1/8760, as the LP charged them), and a user objective scale, which the solve reverts."""
    from services.solver_service import SolverConfig

    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    n = _campus([105.0], [105.0 * TAN_LOAD])
    n.snapshot_weightings.loc[:, "objective"] = weight
    _bess(n, "small", 10.0)
    cfg = _run(n, SolverConfig(user_objective_scale=scale))
    s_x, q_ss, _ = _persisted(n)
    assert q_ss > 1.0
    pcs = years * C_PCS * s_x["small"]
    statcom = years * C_STATCOM * q_ss
    for net in (n, _round_trip(n, tmp_path)):
        cb = compute_cost_breakdown(net, cfg)
        rows = {(r["component"], r["carrier"]): r for r in cb["by_carrier"]}
        assert rows[("PCS/STATCOM", "PCS oversize")]["capex"] == pytest.approx(pcs, rel=1e-6)
        assert rows[("PCS/STATCOM", "STATCOM")]["capex"] == pytest.approx(statcom, rel=1e-6)
        comp = {r["component"]: r for r in cb["by_component"]}
        assert comp["PCS/STATCOM"]["total"] == pytest.approx(pcs + statcom, rel=1e-6)
        dec = compute_objective_decomposition(net, cb, cfg)
        assert abs(dec["gap_eur"]) <= 1e-6 * abs(dec["lp_total"]), dec
        assert abs(dec["residual_gap_eur"]) <= 1e-6 * abs(dec["lp_total"]), dec


def test_cost_terms_include_q_dyn():
    n = _hand_case()

    def dyn(n, sns):
        n.model.add_constraints(n.model["Statcom-q_dyn"] >= 3.0, name="Test-q_dyn_floor")

    sol = _solve(n, dyn)
    ps.persist_solution(n)
    assert ps.cost_terms(n) == {"PCS oversize": pytest.approx(C_PCS * sol["s_x"]["highC"], rel=1e-9),
                                "STATCOM": pytest.approx(C_STATCOM * (sol["q_ss"] + sol["q_dyn"]), rel=1e-9)}


# ── GK A conditions on AS-12: no silent zero, and the basis of a rolling solve ──


def test_a_tagged_network_solved_without_the_rows_stays_blank_and_says_so():
    n = _hand_case()
    assert n.optimize(solver_name="highs", extra_functionality=_wrap_with_split_storage(n, None))[0] == "ok"
    warnings = ps.persist_solution(n)
    assert any("highC" in w for w in warnings) and any("trafo" in w for w in warnings)
    assert n.stores.reindex(columns=["pcs_s_x_opt"]).isna().all().all()          # blank, never 0
    assert n.links.reindex(columns=list(ps.RESULT_LINK_COLUMNS)).isna().all().all()
    assert ps.cost_terms(n) == {}
    notes = ps.cost_notes(n)
    assert any("highC" in s for s in notes) and any("trafo" in s for s in notes)


def test_a_store_tagged_after_the_solve_is_left_blank_and_not_counted():
    n = _hand_case()
    sol = _solve(n)
    _bess(n, "late", 5.0)
    warnings = ps.persist_solution(n)
    assert warnings == [w for w in warnings if "late" in w] and warnings
    assert np.isnan(n.stores.at["late", "pcs_s_x_opt"])
    assert ps.cost_terms(n)["PCS oversize"] == pytest.approx(C_PCS * sol["s_x"]["highC"], rel=1e-9)
    assert [s for s in ps.cost_notes(n) if "late" in s]


# ── AS-14 (owner decision AS14-MAX-1): keep the max over the LPs, cost it once ──

Q_SS_105 = HAND_Q_NEED - 2.0 * math.sqrt(11.0)     # the hand case: 105 MW, S_x capped at r x P = 2 MVA
Q_SS_100 = 100.0 * (TAN_LOAD + K_LOSS - TAN_REQ) - 2.0 * math.sqrt(11.0)


def _periodic(loads, periods):
    """The campus with one snapshot per investment period, each weighted as a year."""
    n = _campus(loads, [p * TAN_LOAD for p in loads])
    n.set_snapshots(pd.MultiIndex.from_arrays([periods, [0] * len(periods)], names=["period", "timestep"]))
    n.investment_periods = periods
    n.snapshot_weightings.loc[:, "objective"] = 8760.0
    n.loads_t.p_set.loc[:, "dc"] = loads
    n.loads_t.q_set.loc[:, "dc"] = [p * TAN_LOAD for p in loads]
    _bess(n, "small", 10.0)
    return n


def _pq_rows(cb):
    rows = {(r["component"], r["carrier"]): r["capex"] for r in cb["by_carrier"] if r["component"] == "PCS/STATCOM"}
    by_period = {p["period"]: {c["component"]: c["capex"] for c in p["by_component"]} for p in cb["by_period"]}
    return rows, by_period


def test_a_rolling_solve_persists_the_largest_size_over_its_windows_and_costs_it_once(tmp_path):
    """Two one-hour windows; the first needs the larger STATCOM. The last window alone would size it smaller."""
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.solver.periodized_costs import fom_horizon_factor
    from services.solver_service import SolverConfig

    n = _campus([105.0, 100.0], [105.0 * TAN_LOAD, 100.0 * TAN_LOAD])
    _bess(n, "small", 10.0)
    cfg = _run(n, SolverConfig(solve_strategy="rolling", rolling_horizon=1, rolling_overlap=0))
    s_x, q_ss, q_dyn = _persisted(n)
    assert q_ss == pytest.approx(Q_SS_105, rel=1e-6)
    assert s_x == {"small": pytest.approx(2.0, rel=1e-6)} and q_dyn == pytest.approx(0.0, abs=1e-6)
    years = fom_horizon_factor(n)
    for net in (n, _round_trip(n, tmp_path)):
        assert net.meta.get("pcs_statcom_basis") == "max_over_solves"
        assert _persisted(net)[1] == pytest.approx(Q_SS_105, rel=1e-6)
        cb = compute_cost_breakdown(net, cfg)
        rows, _ = _pq_rows(cb)
        assert rows[("PCS/STATCOM", "STATCOM")] == pytest.approx(years * C_STATCOM * Q_SS_105, rel=1e-6)
        assert rows[("PCS/STATCOM", "PCS oversize")] == pytest.approx(years * C_PCS * 2.0, rel=1e-6)
        notes = cb["pcs_statcom_notes"]
        assert any("largest over 2 LPs" in s and "counted once" in s and "differs from the sum of the window "
                   "objectives" in s for s in notes), notes


def test_a_myopic_solve_persists_the_largest_size_and_books_it_in_the_period_that_needs_it(tmp_path):
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.solver_service import SolverConfig

    n = _periodic([105.0, 100.0], [2030, 2035])
    cfg = _run(n, SolverConfig(solve_strategy="myopic", multi_investment_periods=True))
    assert _persisted(n)[1] == pytest.approx(Q_SS_105, rel=1e-6)
    for net in (n, _round_trip(n, tmp_path)):
        assert net.meta.get("pcs_statcom_basis") == "max_over_solves"
        cb = compute_cost_breakdown(net, cfg)
        rows, by_period = _pq_rows(cb)
        pq = rows[("PCS/STATCOM", "STATCOM")] + rows[("PCS/STATCOM", "PCS oversize")]
        assert rows[("PCS/STATCOM", "STATCOM")] == pytest.approx(C_STATCOM * Q_SS_105, rel=1e-6)
        assert by_period[2030]["PCS/STATCOM"] == pytest.approx(pq, rel=1e-9)
        assert "PCS/STATCOM" not in by_period[2035]          # the smaller later period adds nothing


def test_a_size_that_grows_in_a_later_period_books_only_the_growth_there():
    """GK A condition 3: period k pays max(0, size_k - running max before k); the parts sum to the max's cost."""
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.solver_service import SolverConfig

    n = _periodic([100.0, 105.0], [2030, 2035])
    cfg = _run(n, SolverConfig(solve_strategy="myopic", multi_investment_periods=True))
    assert _persisted(n)[1] == pytest.approx(Q_SS_105, rel=1e-6)
    cb = compute_cost_breakdown(n, cfg)
    _, by_period = _pq_rows(cb)
    assert by_period[2030]["PCS/STATCOM"] == pytest.approx(C_STATCOM * Q_SS_100 + C_PCS * 2.0, rel=1e-6)
    assert by_period[2035]["PCS/STATCOM"] == pytest.approx(C_STATCOM * (Q_SS_105 - Q_SS_100), rel=1e-6)
    assert by_period[2030]["PCS/STATCOM"] + by_period[2035]["PCS/STATCOM"] == pytest.approx(
        sum(ps.cost_terms(n).values()), rel=1e-12)


def test_q_ss_and_q_dyn_are_maxed_apart_and_split_by_period():
    """Q_dyn peaks in the period Q_ss does not: the persisted pair is the larger of each (GK A condition 5)."""
    readings = [(2030, {"q_ss": 5.0, "q_dyn": 0.0, "s_x": pd.Series({"a": 1.0})}),
                (2035, {"q_ss": 3.0, "q_dyn": 4.0, "s_x": pd.Series({"a": 2.0})})]
    sol, split = ps._max_over(readings)
    assert (sol["q_ss"], sol["q_dyn"], sol["s_x"]["a"]) == (5.0, 4.0, 2.0)
    assert split["statcom"] == [[2030, pytest.approx(5 / 9)], [2035, pytest.approx(4 / 9)]]
    assert split["stores"]["a"] == [[2030, 0.5], [2035, 0.5]]
    assert ps._increments([(None, 2.0), (None, 3.0)]) == [[None, 1.0]]


def test_each_lp_is_read_once_and_a_build_with_no_solution_is_named():
    """GK A conditions 1 and 4: a model built and never solved is skipped, by number, never folded in as 0, and
    a call never reads its own model."""
    n = _hand_case()
    fn = _then(n, None)
    n.optimize.create_model()
    fn(n, n.snapshots)                                          # LP 1: built, never solved
    lps = n._pcs_statcom_lps
    lps.read_previous(n.model)                                  # its own model: not read
    assert lps.solved == [] and lps.skipped == []
    assert n.optimize(solver_name="highs", extra_functionality=fn)[0] == "ok"     # LP 2 reads LP 1 first
    assert lps.skipped == [1] and lps.solved == []
    ps.persist_solution(n, basis=ps.MAX_BASIS)
    assert lps.solved and n.meta[ps.LPS_KEY] == 1 and n.meta[ps.SKIPPED_KEY] == [1]
    assert any("LP 1" in s and "not in the max" in s for s in ps.cost_notes(n))
    assert _persisted(n)[1] == pytest.approx(solution(n)["q_ss"], rel=1e-12)


def test_a_rolling_solve_with_an_infeasible_window_fails_and_persists_nothing():
    """A failed window fails the whole solve, so no max is taken over a partial run (GK A condition 4)."""
    from services.solver_service import SolverConfig

    n = _campus([105.0, 400.0], [105.0 * TAN_LOAD, 400.0 * TAN_LOAD])
    _bess(n, "small", 10.0)
    _run(n, SolverConfig(solve_strategy="rolling", rolling_horizon=1, rolling_overlap=0), expect="fail")
    assert n.stores.reindex(columns=["pcs_s_x_opt"]).isna().all().all()
    assert "pcs_statcom_basis" not in n.meta


def test_a_network_persisted_before_as_14_still_reads_as_last_solve(tmp_path):
    """GK A condition 6: an old .nc carries basis "last_solve" and no period split."""
    n = _campus([105.0], [105.0 * TAN_LOAD])
    _bess(n, "small", 10.0)
    _run(n)
    n.meta["pcs_statcom_basis"] = "last_solve"
    for key in (ps.LPS_KEY, ps.SKIPPED_KEY, ps.PERIODS_KEY):
        n.meta.pop(key, None)
    back = _round_trip(n, tmp_path)
    assert ps.LAST_SOLVE_NOTE in ps.cost_notes(back)
    assert {(label, period) for label, period, _ in ps.cost_items(back)} == {("PCS oversize", None), ("STATCOM", None)}
    assert ps.cost_terms(back) == pytest.approx(ps.cost_terms(n), rel=1e-12)


def test_a_one_period_multi_index_solve_puts_the_cost_in_its_period():
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.solver_service import SolverConfig

    n = _periodic([105.0], [2030])
    cfg = _run(n, SolverConfig(multi_investment_periods=True))
    assert n.meta.get("pcs_statcom_basis") == "horizon"
    cb = compute_cost_breakdown(n, cfg)
    rows, by_period = _pq_rows(cb)
    assert by_period[2030]["PCS/STATCOM"] == pytest.approx(sum(rows.values()), rel=1e-9)
    assert sum(p["capex"] for p in cb["by_period"]) == pytest.approx(cb["capex"], rel=1e-9)


def test_a_full_horizon_solve_carries_no_basis_note():
    from services.results.cost_breakdown import compute_cost_breakdown

    n = _campus([105.0], [105.0 * TAN_LOAD])
    _bess(n, "small", 10.0)
    cfg = _run(n)
    assert n.meta.get("pcs_statcom_basis") == "horizon"
    assert compute_cost_breakdown(n, cfg)["pcs_statcom_notes"] == []
