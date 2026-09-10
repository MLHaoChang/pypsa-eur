"""
Compare's Capacity tab and Results' Economics tab must report the SAME CAPEX,
including non-extendable lines and transformers.

Two existing parity suites (`test_compare_link_capex_parity.py`,
`test_compare_store_capex_parity.py`) assert exactly this property — and both
pass today while the two views disagree by 7500 MEUR on the golden fixture.
Neither fixture contains a line that carries a capital_cost, so neither
reaches the one thing the two mechanisms actually disagree about.

MEASURED, golden fixture, periods [2030, 2035]:

    Compare Capacity (_compute_total_annuitised_capex)     25.320785 MEUR
    Results Economics (get_cost_breakdown, n.statistics)  7525.320785 MEUR
    delta                                                -7500.000000 MEUR

Every other carrier agrees to six decimals — diesel 0.028212, gas 0.376324,
h2 0.166250, solar 24.750000. The whole gap is Lines, carrier AC, at
500 MEUR/a over 15 horizon-years.

RULED by the human, 2026-08-13: include it everywhere. A passive branch's
capital cost is part of what the system costs, so both views count it and
Results' published figure does not move. The prior exclusion is recorded in
`services/economics.py`'s walk comment; its stated reason — that a line-CAPEX
carrier "nobody could reconcile with the Results panel" appeared in Compare —
dissolves once both sides count it, which is the point of this change.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

import routers.simulation as sim_router
from routers.compare import _compute_total_annuitised_capex, _periodized_lookup
from routers.results import get_cost_breakdown
from services import period_utils
from services.solver_service import SolverConfig

# 300 MW x 40 000 EUR/MW/a = 12 MEUR/a of line CAPEX, on a NON-extendable
# line — the exact shape both existing parity fixtures lack.
LINE_S_NOM = 300.0
LINE_CAPITAL_COST = 40_000.0
LINE_CAPEX_MEUR = LINE_S_NOM * LINE_CAPITAL_COST / 1e6

GEN_P_NOM = 100.0
GEN_CAPITAL_COST = 20_000.0
GEN_CAPEX_MEUR = GEN_P_NOM * GEN_CAPITAL_COST / 1e6


def _network_with_costly_fixed_line() -> pypsa.Network:
    """Two buses joined by a NON-extendable AC line that carries capex."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "A", carrier="AC", v_nom=380.0)
    n.add("Bus", "B", carrier="AC", v_nom=380.0)
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Load", "L", bus="B", p_set=50.0)
    n.add(
        "Generator", "g",
        bus="A", carrier="gas",
        p_nom=GEN_P_NOM, marginal_cost=10.0, capital_cost=GEN_CAPITAL_COST,
    )
    n.add(
        "Line", "AC_line",
        bus0="A", bus1="B", carrier="AC",
        s_nom=LINE_S_NOM, s_nom_extendable=False,
        x=0.1, r=0.01,
        capital_cost=LINE_CAPITAL_COST,
    )
    return n


def _capacity_tab_capex(n) -> float:
    """What Compare's Capacity tab reports, in MEUR."""
    is_multi = isinstance(n.snapshots, pd.MultiIndex)
    periods = sorted(int(p) for p in n.investment_periods) if is_multi else []
    years_map = period_utils.period_years_map(n) if is_multi else {}
    pcc = _periodized_lookup(n)
    per_carrier = _compute_total_annuitised_capex(n, periods, is_multi, years_map, pcc)
    return sum(v["total"] for v in per_carrier.values())


def test_capacity_tab_counts_a_non_extendable_line_with_capital_cost():
    """
    The direct statement of the ruling. Before it, the walk skipped Lines
    outright and this returned the generator's 2.0 MEUR alone.
    """
    n = _network_with_costly_fixed_line()
    n.optimize(solver_name="highs")

    total = _capacity_tab_capex(n)

    assert total == pytest.approx(GEN_CAPEX_MEUR + LINE_CAPEX_MEUR, rel=1e-6), (
        f"expected generator {GEN_CAPEX_MEUR} + line {LINE_CAPEX_MEUR} MEUR, "
        f"got {total} — a non-extendable line's CAPEX is part of what the "
        f"system costs and must be counted"
    )


def test_capacity_tab_reports_the_line_under_its_own_carrier():
    """
    Pins WHERE the money lands, not just the total. A walk that folded line
    CAPEX into an existing carrier would satisfy the total-only assertion
    above while making the per-carrier table wrong.
    """
    n = _network_with_costly_fixed_line()
    n.optimize(solver_name="highs")

    is_multi = isinstance(n.snapshots, pd.MultiIndex)
    pcc = _periodized_lookup(n)
    per_carrier = _compute_total_annuitised_capex(n, [], is_multi, {}, pcc)

    assert "ac" in per_carrier, (
        f"line CAPEX must appear under its own carrier; got {sorted(per_carrier)}"
    )
    assert per_carrier["ac"]["total"] == pytest.approx(LINE_CAPEX_MEUR, rel=1e-6)
    assert per_carrier["gas"]["total"] == pytest.approx(GEN_CAPEX_MEUR, rel=1e-6)


def test_capacity_and_economics_agree_when_a_costly_fixed_line_exists(install_network):
    """
    The property both existing parity suites claim to guard, on the fixture
    shape that actually exercises the disagreement. This is the acceptance
    criterion for the whole change: one number, two views.
    """
    n = _network_with_costly_fixed_line()
    n.optimize(solver_name="highs")
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig()

    payload = get_cost_breakdown()
    assert isinstance(payload, dict), "cost_breakdown did not return a payload"
    economics_capex = float(payload["capex"]) / 1e6  # EUR -> MEUR

    capacity_capex = _capacity_tab_capex(n)

    assert capacity_capex == pytest.approx(economics_capex, rel=1e-6), (
        f"Capacity tab says {capacity_capex} MEUR, Economics says "
        f"{economics_capex} MEUR — the same network cannot cost two "
        f"different amounts depending on which tab is open"
    )
