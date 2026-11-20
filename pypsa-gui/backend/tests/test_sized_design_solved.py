"""
Issue template-e2e 20, a real solve (GK A 33 S7): the data-centre and the
staged ammonia templates solved with HiGHS give the sizing payload's carrier
totals, connection limit, peak import and its check, and the ammonia
shortfall stays out of the total. Apart from ``test_sized_design_parity`` so
its mutant runs stay fast.
"""

from __future__ import annotations

import pytest

from services.adequacy import eh_report as R
from tests.test_sized_design_parity import _build


def _solved(tid: str):
    n = _build(tid)
    n.optimize(
        solver_name="highs",
        multi_investment_periods=len(n.investment_periods) > 0,
        solver_options={"output_flag": False},
        progress=False,
    )
    return n, R.sizing_summary_from_network(n)


def test_solved_datacenter():
    _n, p = _solved("eh_datacenter")
    assert p["by_carrier"] == pytest.approx({"gas": 40.0, "solar": 4.0}, abs=0.05)
    assert p["total_p_nom_mw"] == pytest.approx(44.0, abs=0.05)
    assert p["grid_connection_mw"] == 40.0 and p["grid_supply_mw"] == 500.0
    assert p["grid_import_peak_mw"] == pytest.approx(40.0, abs=1e-3)
    assert p["connection_check"] == "within"


def test_solved_ammonia_staged():
    _n, p = _solved("eh_ammonia_staged")
    assert p["by_carrier"] == pytest.approx({"solar": 140.0, "wind": 210.0}, abs=0.05)
    assert p["total_p_nom_mw"] == pytest.approx(350.0, abs=0.05)
    assert p["grid_connection_mw"] == 200.0 and p["grid_supply_mw"] == 1000.0
    assert set(p["grid_import_peak_by_period"]) == {"2027", "2030", "2035"}
    assert p["grid_import_peak_mw"] == pytest.approx(95.47, abs=0.05)
    assert p["connection_check"] == "within"
    lost = next(s for s in p["product_slacks"] if s["process_role"] == "shortfall")
    assert lost["carrier"] == "NH3 shortfall" and lost["p_nom"] == pytest.approx(
        32.29, abs=0.01
    )
