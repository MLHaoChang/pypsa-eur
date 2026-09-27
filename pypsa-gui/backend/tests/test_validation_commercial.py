"""
Preflight validation of the commercial layer (Edge Investment Case P1 WP1.8).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.8
`validate_for_run` reports `commercial.*` issues before any solver time is
spent. A config that cannot bind is an error, the same refusal the solve would
make, stated earlier. Everything that binds but is likely to mislead is a
warning: circulation through the grid (`commercial.arbitrage_loop`), a demand
interval finer than the axis (`commercial.demand_resolution`), full monthly
demand charges on partly modelled months (`commercial.demand_partial_months`,
spec §5.2), and a tariff that is not valid for the modelled dates. The PPA and
DR-contract double-count checks need P2's contracts (plan deviation).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.solver_service import SolverConfig
from services.validation_service import validate_for_run
from tests.fixtures.investment_case.edge_15min import build_edge_15min

ENERGY = {"id": "e", "kind": "energy", "unit": "per_kwh",
          "periods": [{"name": "all", "rate": 0.10}]}


def _tariff(*items, valid_from="2030-01-01", valid_to=None):
    return {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": valid_from,
            "valid_to": valid_to, "items": list(items)}


def _codes(n, commercial):
    issues = validate_for_run(n, SolverConfig(commercial=commercial))
    return {i.code: i for i in issues if i.code.startswith("commercial.")}


def test_no_commercial_config_adds_nothing():
    assert _codes(build_edge_15min(), None) == {}


def test_a_config_that_cannot_bind_is_an_error():
    codes = _codes(build_edge_15min(), {"poc_link": "ghost", "import_tariff": _tariff(ENERGY)})
    assert codes["commercial.binding_invalid"].severity == "error"
    assert "ghost" in codes["commercial.binding_invalid"].message


def test_a_group_naming_a_non_link_is_an_error():
    codes = _codes(build_edge_15min(), {"poc_link": "import", "group_members": ["import", "pv"],
                                        "group_cap_mw": 10.0})
    assert codes["commercial.binding_invalid"].severity == "error"


def test_export_paying_more_than_import_costs_warns_arbitrage_loop():
    from services.commercial import lp_bindings as L

    n = build_edge_15min()
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC")
    n.links_t[L.EXPORT_PRICE_ATTR] = pd.DataFrame({"export": np.full(672, 500.0)},
                                                  index=n.snapshots)
    ref = {"id": "px", "version": 1, "hash": "a" * 64, "source": "t"}
    codes = _codes(n, {"poc_link": "import", "export_link": "export", "export_price_ref": ref,
                       "import_tariff": _tariff(ENERGY)})
    issue = codes["commercial.arbitrage_loop"]
    assert issue.severity == "warning" and "672" in issue.message


def test_a_demand_interval_finer_than_the_axis_warns():
    n = build_edge_15min()
    n.set_snapshots(n.snapshots[::4])  # hourly
    n.snapshot_weightings.loc[:, :] = 1.0
    demand = {"id": "d", "kind": "demand", "unit": "per_kw_month", "settlement": "15min",
              "periods": [{"name": "all", "rate": 10.0}]}
    codes = _codes(n, {"poc_link": "import", "import_tariff": _tariff(demand)})
    assert codes["commercial.demand_resolution"].severity == "warning"


def test_partial_demand_months_warn():
    demand = {"id": "d", "kind": "demand", "unit": "per_kw_month",
              "periods": [{"name": "all", "rate": 10.0}]}
    codes = _codes(build_edge_15min(), {"poc_link": "import", "import_tariff": _tariff(demand)})
    assert "2030-01" in codes["commercial.demand_partial_months"].message


@pytest.mark.parametrize("vf,vt", [("2031-01-01", None), ("2029-01-01", "2029-12-31")])
def test_a_tariff_not_valid_for_the_modelled_dates_warns(vf, vt):
    codes = _codes(build_edge_15min(), {"poc_link": "import",
                                        "import_tariff": _tariff(ENERGY, valid_from=vf,
                                                                 valid_to=vt)})
    assert codes["commercial.tariff_out_of_validity"].severity == "warning"


def test_a_clean_config_has_no_commercial_issues():
    assert _codes(build_edge_15min(), {"poc_link": "import", "import_tariff": _tariff(ENERGY)}) == {}
