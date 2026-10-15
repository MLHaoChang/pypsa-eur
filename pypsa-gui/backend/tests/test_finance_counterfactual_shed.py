"""The grid-only counterfactual above its connection (owner decision
2026-10-15, after #283): the site without the owner's assets can import at
most the PoC's rating, capped hour by hour by the energization schedule, so
the load above it is shed and priced at the case's VoLL. The bill and the
commodity are rated on what the connection carries. Without a VoLL the
excess cannot be priced: the old basis (the whole load billed) stays,
flagged `counterfactual_exceeds_connection` and
`counterfactual_shed_unpriced:no_voll`."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tests.test_finance_case_adapter import OWNED, _case, _edge7, _fin, _flag_num, _lines
from tests.test_value_flow_reconciliation import _commercial, _solve

VOLL = 5_000.0
POC_MW = 42.0          # the BESS shaves the 45 MW evening peak


def _site(*, energization: float | None = None):
    n = _edge7()
    n.links.loc["import", "p_nom_extendable"] = False
    n.links.loc["import", "p_nom"] = POC_MW
    if energization is not None:
        n.links_t["eh_energization_mw"] = pd.DataFrame(
            {"import": np.full(len(n.snapshots), energization)}, index=n.snapshots)
    com = _commercial(OWNED, contracts=[])
    com.pop("connection")
    return n, com


def _excess_mwh(n, cap_mw: float) -> float:
    w = n.snapshot_weightings.objective.to_numpy(float)
    load = n.loads_t.p_set["site_load"].to_numpy(float)
    return float((w * np.clip(load - cap_mw, 0.0, None)).sum())


def _mc(n) -> np.ndarray:
    g = "grid_supply"
    if g in n.generators_t.marginal_cost.columns:
        return n.generators_t.marginal_cost[g].to_numpy(float)
    return np.full(len(n.snapshots), float(n.generators.at[g, "marginal_cost"]))


@pytest.mark.live_solve
def test_the_excess_above_the_connection_is_shed_at_voll(reset_backend):
    n, com = _site()
    state: dict = {}
    n, cfg = _solve(n, com, voll=VOLL, state=state)
    case = _case(n, cfg, _fin(annualise=True), lost_load=state.get("last_lost_load"))
    excess = _excess_mwh(n, POC_MW)
    assert excess > 0
    assert _flag_num(case.flags, "counterfactual_shed_at_voll:") == pytest.approx(excess, rel=1e-6)
    assert "counterfactual_exceeds_connection" not in case.flags      # priced, not impossible
    (cf,) = case.counterfactual
    lines = _lines(cf)
    shed = lines["counterfactual:shed_at_voll"]
    # The 7-day template is annualised like every other line (the flag's MWh is the week's).
    assert "template_annualised:52.14" in case.flags           # the flag rounds the factor
    year = 8760.0 / float(n.snapshot_weightings.objective.sum())
    assert shed.amount == pytest.approx(-VOLL * excess * year, rel=1e-6)
    assert shed.source == "counterfactual" and shed.money_year == case.base_year
    # The commodity is the meter's on what the connection carries, never above it.
    w = n.snapshot_weightings.objective.to_numpy(float)
    served = np.minimum(n.loads_t.p_set["site_load"].to_numpy(float), POC_MW)
    comm = sum(ln.amount for ln in cf.lines if ln.source_id == "commodity")
    assert comm == pytest.approx(-float((w * served * _mc(n)).sum()) * year, rel=1e-6)


@pytest.mark.live_solve
def test_without_a_voll_the_excess_stays_billed_and_is_flagged_unpriced(reset_backend):
    n, com = _site()
    n, cfg = _solve(n, com)
    case = _case(n, cfg, _fin(annualise=True))
    assert {"counterfactual_exceeds_connection", "counterfactual_shed_unpriced:no_voll"} <= \
        set(case.flags)
    (cf,) = case.counterfactual
    assert "counterfactual:shed_at_voll" not in _lines(cf)
    assert all(ln.amount is not None for ln in cf.lines)


@pytest.mark.live_solve
def test_the_energization_schedule_caps_the_counterfactual_import(reset_backend):
    n, com = _site(energization=40.0)
    state: dict = {}
    n, cfg = _solve(n, com, voll=VOLL, state=state)
    case = _case(n, cfg, _fin(annualise=True), lost_load=state.get("last_lost_load"))
    assert _flag_num(case.flags, "counterfactual_shed_at_voll:") == \
        pytest.approx(_excess_mwh(n, 40.0), rel=1e-6)


def test_an_overshoot_within_the_tolerance_is_not_a_shed():
    """#294 review: floating-point noise in the served load (cap + 1e-9) is
    not a shed, so it adds no line and leaves the counterfactual's hash as it
    was; a real overshoot is kept whole."""
    from services.results.finance_case import _EPS_MW, _excess_above

    cap = np.full(4, 40.0)
    assert not _excess_above(cap + 1e-9, cap).any()
    assert not _excess_above(cap + 0.5 * _EPS_MW, cap).any()
    got = _excess_above(np.array([40.0, 41.5, 39.0, 40.0 + 2 * _EPS_MW]), cap)
    assert got.tolist() == pytest.approx([0.0, 1.5, 0.0, 2 * _EPS_MW])


# ── the connection agreement's cap (#294 review, item 4) ────────────────────
# `apply_connection_agreement` imposes a non-firm cap (and a closed connection
# before `available_from`) for the solve only and undoes it after; the
# counterfactual composes the same cap into what the connection carries.

def _agreement(kind="non_firm_static", cap=38.0, available_from="2020-01-01"):
    return {"kind": kind, "import_cap_mw": cap, "available_from": available_from}


@pytest.mark.live_solve
def test_a_non_firm_contract_caps_the_counterfactual_import(reset_backend):
    n, com = _site()
    com["connection"] = _agreement(cap=38.0)
    state: dict = {}
    n, cfg = _solve(n, com, voll=VOLL, state=state)
    case = _case(n, cfg, _fin(annualise=True), lost_load=state.get("last_lost_load"))
    # Shed against the contract's 38 MW, not the 42 MW link.
    assert _flag_num(case.flags, "counterfactual_shed_at_voll:") == \
        pytest.approx(_excess_mwh(n, 38.0), rel=1e-6)
    assert _excess_mwh(n, 38.0) > _excess_mwh(n, POC_MW)


@pytest.mark.live_solve
def test_a_firm_agreement_at_the_link_rating_leaves_the_shed_unchanged(reset_backend):
    """A firm agreement for what the link carries adds no cap (gatekeeper
    review of #302): the shed is the one against the link alone."""
    n, com = _site()
    com["connection"] = _agreement(kind="firm", cap=POC_MW)
    state: dict = {}
    n, cfg = _solve(n, com, voll=VOLL, state=state)
    case = _case(n, cfg, _fin(annualise=True), lost_load=state.get("last_lost_load"))
    assert _flag_num(case.flags, "counterfactual_shed_at_voll:") == \
        pytest.approx(_excess_mwh(n, POC_MW), rel=1e-6)


def test_the_agreement_cap_series_matches_what_the_solve_imposes():
    """`connection.agreement_import_cap_mw`: the contract cap (a study's
    planning limit clamps it), 0 before `available_from`, constant for firm
    and static non-firm agreements."""
    from datetime import date

    from models.commercial import ConnectionAgreement
    from services.commercial import connection as C

    n, _com = _site()
    t0 = pd.DatetimeIndex(n.snapshots)[0]
    a = ConnectionAgreement(kind="non_firm_static", import_cap_mw=38.0, available_from=date(2020, 1, 1))
    assert np.allclose(C.agreement_import_cap_mw(n, a, poc_link="import"), 38.0)
    firm = ConnectionAgreement(kind="firm", import_cap_mw=40.0, available_from=date(2020, 1, 1))
    assert np.allclose(C.agreement_import_cap_mw(n, firm, poc_link="import"), 40.0)
    setattr(n, C.LINK_LIMITS_ATTR, {"import": 30.0})          # a lever below the contract
    assert np.allclose(C.agreement_import_cap_mw(n, a, poc_link="import"), 30.0)
    delattr(n, C.LINK_LIMITS_ATTR)
    later = ConnectionAgreement(kind="non_firm_static", import_cap_mw=38.0,
                                available_from=(t0 + pd.Timedelta(days=2)).date())
    got = C.agreement_import_cap_mw(n, later, poc_link="import")
    closed = np.asarray(pd.DatetimeIndex(n.snapshots) < pd.Timestamp(later.available_from))
    assert closed.any() and np.all(got[closed] == 0.0) and np.allclose(got[~closed], 38.0)
