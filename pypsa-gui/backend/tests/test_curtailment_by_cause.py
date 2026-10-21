"""
Curtailment by cause (IPP renewables + BESS ticket 05, spec §5 / G6): each plant's curtailed
energy split per snapshot by why it was curtailed: the export Link at its limit, a negative
export price, both, or neither ("other": a full battery, an import-side limit). The causes sum
to the existing curtailment total exactly, and nothing is split without an export price.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services.commercial import lp_bindings as LP
from services.results.curtailment import compute_curtailment, curtailment_by_cause

COMMERCIAL = {"poc_link": "grid_import", "export_link": "grid_export"}


def _network(price=(50.0, -5.0, -5.0, 20.0)) -> pypsa.Network:
    """Four hours of a 10 MW PV plant behind an 8 MW export limit, results set by hand:
    h0 at the limit (2 MWh curtailed), h1 negative price (7), h2 both (2), h3 neither (4)."""
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(4))
    n.add("Bus", "poi")
    n.add("Bus", "grid")
    n.add("Generator", "grid", bus="grid", carrier="grid", p_nom=100.0, p_min_pu=-1.0)
    n.add("Generator", "pv", bus="poi", carrier="solar", p_nom=10.0)
    n.add("Link", "grid_import", bus0="grid", bus1="poi", p_nom=5.0)
    n.add("Link", "grid_export", bus0="poi", bus1="grid", p_nom=8.0)
    n.generators.loc["pv", "p_nom_opt"] = 10.0
    n.generators_t.p_max_pu = pd.DataFrame({"pv": [1.0, 1.0, 1.0, 1.0]}, index=n.snapshots)
    n.generators_t.p = pd.DataFrame({"pv": [8.0, 3.0, 8.0, 6.0],
                                     "grid": [-8.0, -3.0, -8.0, -6.0]}, index=n.snapshots)
    n.links_t.p0 = pd.DataFrame({"grid_export": [8.0, 3.0, 8.0, 6.0],
                                 "grid_import": [0.0] * 4}, index=n.snapshots)
    if price is not None:
        n.links_t[LP.EXPORT_PRICE_ATTR] = pd.DataFrame({"grid_export": list(price)},
                                                       index=n.snapshots)
    return n


def test_each_cause_gets_its_hours_and_they_sum_to_the_total():
    out = curtailment_by_cause(_network(), COMMERCIAL)
    assert out["status"] == "ok"
    pv = out["plants"]["pv"]
    assert pv["by_cause_mwh"] == {"export_limit": 2.0, "negative_price": 7.0, "both": 2.0,
                                  "other": 4.0}
    assert pv["total_mwh"] == pytest.approx(15.0)
    assert pv["available_mwh"] == pytest.approx(40.0)
    assert out["export_link"] == "grid_export" and out["export_limit_mw"] == 8.0


def test_the_causes_sum_to_the_existing_curtailment_total():
    n = _network()
    hourly = compute_curtailment(n, None, None)
    existing = {col: sum(row[i] for row in hourly["data"])
                for i, col in enumerate(hourly["columns"])}
    out = curtailment_by_cause(n, COMMERCIAL)
    for plant, row in out["plants"].items():
        assert sum(row["by_cause_mwh"].values()) == pytest.approx(existing[plant])
        assert row["total_mwh"] == pytest.approx(existing[plant])


def test_snapshot_weights_count():
    n = _network()
    n.snapshot_weightings.loc[:, :] = 2.0
    pv = curtailment_by_cause(n, COMMERCIAL)["plants"]["pv"]
    assert pv["by_cause_mwh"]["negative_price"] == pytest.approx(14.0)
    assert pv["total_mwh"] == pytest.approx(30.0)


def test_without_an_export_price_nothing_is_split():
    out = curtailment_by_cause(_network(price=None), COMMERCIAL)
    assert out["status"] == "not_established"
    assert out["reason"] == "export_price_missing"
    assert out["plants"]["pv"]["total_mwh"] == pytest.approx(15.0)     # the total still shows
    assert "by_cause_mwh" not in out["plants"]["pv"]


def test_without_an_export_link_nothing_is_split():
    out = curtailment_by_cause(_network(), {"poc_link": "grid_import"})
    assert (out["status"], out["reason"]) == ("not_established", "export_link_missing")
    out = curtailment_by_cause(_network(), None)
    assert (out["status"], out["reason"]) == ("not_established", "export_link_missing")


def test_an_unsolved_network_answers_none():
    n = _network()
    n.generators_t.p = pd.DataFrame(index=n.snapshots)
    assert curtailment_by_cause(n, COMMERCIAL) is None
