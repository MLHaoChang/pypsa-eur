"""
`commercial_cost_terms` per tariff item (GS Q12a; IC U1 follow-up).

`block["by_item"]` = {"demand_charge": {item id: {period key: amount}},
"tariff_capacity": {item id: {period key: amount}}}, built from the same
committed records as the items (unweighted per period, keys "_" or "YYYY").
Additive: the item tuples and every existing block total are unchanged — the
per-item amounts sum to them — and each equals the bill's item.
"""
from __future__ import annotations

import pytest

from services.commercial import billing as B
from tests.test_lp_tariff_capacity import CAP, LEISTUNG, TOU, _commercial, _site, _solve

DEMAND = {"id": "demand", "kind": "demand", "unit": "per_kw_month", "measured_on": "import",
          "periods": [{"name": "all", "rate": 9.0}]}
EVENING = {"id": "evening", "kind": "demand", "unit": "per_kw_month", "measured_on": "import",
           "periods": [{"name": "evening", "rate": 4.0, "start_hour": 17, "end_hour": 21}]}


@pytest.mark.live_solve
def test_demand_and_capacity_amounts_by_item():
    from services.commercial.cost_rows import commercial_cost_terms

    n = _site()
    commercial = _commercial(TOU, DEMAND, EVENING, CAP, LEISTUNG)
    _solve(n, commercial)
    out = commercial_cost_terms(n, commercial)
    block, by_item = out["block"], out["block"]["by_item"]
    assert set(by_item["demand_charge"]) == {"demand", "evening"}
    assert set(by_item["tariff_capacity"]) == {"cap", "lp"}
    assert all(set(v) == {"_"} for v in by_item["demand_charge"].values())
    # The totals are unchanged and the items sum to them.
    demand_items = sum(cx + ox for lab, _p, cx, ox in out["items"] if lab == "demand_charge")
    assert block["demand_charge"] == pytest.approx(demand_items, rel=1e-12)
    assert sum(v["_"] for v in by_item["demand_charge"].values()) == \
        pytest.approx(block["demand_charge"], rel=1e-12)
    assert sum(v["_"] for v in by_item["tariff_capacity"].values()) == \
        pytest.approx(block["tariff_capacity"], rel=1e-12)
    # Each item is its bill line.
    bill = B.bill_site(n, commercial).per_period[None]
    for item in ("demand", "evening"):
        assert by_item["demand_charge"][item]["_"] == pytest.approx(
            bill.per_item_sampled[item], rel=1e-6), item
    for item in ("cap", "lp"):
        assert by_item["tariff_capacity"][item]["_"] == pytest.approx(
            bill.per_item[item], rel=1e-9), item


@pytest.mark.live_solve
def test_without_demand_or_capacity_items_there_is_no_by_item_block():
    from services.commercial.cost_rows import commercial_cost_terms

    n = _site(extendable=False)
    commercial = _commercial(TOU)
    _solve(n, commercial)
    assert "by_item" not in commercial_cost_terms(n, commercial)["block"]
