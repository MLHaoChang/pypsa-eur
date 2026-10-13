"""
A hand-typed price on a grid Link the tariff also prices (IC engine ask 6,
owner decision 2026-10-13: warn, show both prices, the numbers unchanged).

`lp_bindings` ADDS the tariff's energy price to whatever the metered Link
already costs (its static or time-varying `marginal_cost`), so a price typed
on the PoC Link and an energy tariff on the same Link are both paid. That is
kept (it is how a hand-modelled network fee is carried); the preflight now
says so: `commercial.link_price_and_tariff` (warning) on each metered Link
with its own non-zero cost that the tariff also prices, naming the Link's
own price, the tariff's, and their sum. The export side counts the export
fees net of the export price.

Written red first: no such warning existed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from services.commercial import lp_bindings as L
from services.commercial import preflight as P
from tests.fixtures.investment_case.edge_15min import build_edge_15min

CODE = "commercial.link_price_and_tariff"
ENERGY = {"id": "e", "kind": "energy", "unit": "per_kwh",
          "periods": [{"name": "all", "rate": 0.10}]}
TOU = {"id": "tou", "kind": "energy", "unit": "per_kwh",
       "periods": [{"name": "night", "rate": 0.05, "start_hour": 0, "end_hour": 6},
                   {"name": "day", "rate": 0.20}]}
DEMAND = {"id": "d", "kind": "demand", "unit": "per_kw_month", "measured_on": "import",
          "periods": [{"name": "all", "rate": 15.0}]}
EXPORT_FEE = {"id": "xf", "kind": "energy", "unit": "per_kwh", "measured_on": "export",
              "periods": [{"name": "all", "rate": 0.01}]}
REF = {"id": "px", "version": 1, "hash": "a" * 64, "source": "t"}


def _tariff(*items):
    return {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2030-01-01",
            "items": list(items)}


def _hits(out):
    return [f for f in out if f[1] == CODE]


def test_a_typed_price_on_a_tariff_priced_import_link_warns_with_both_prices():
    n = build_edge_15min()
    n.links.loc["import", "marginal_cost"] = 12.0
    out = P.commercial_findings(n, {"poc_link": "import", "import_tariff": _tariff(ENERGY)})
    [(sev, _code, cls, name, msg)] = _hits(out)
    assert (sev, cls, name) == ("warning", "Link", "import")
    # Its own 12.00 €/MWh, the tariff's 100.00 (0.10 €/kWh), paid together 112.00.
    assert "12.00" in msg and "100.00" in msg and "112.00" in msg


def test_ranges_are_shown_for_time_varying_prices():
    n = build_edge_15min()
    h = np.asarray(pd.DatetimeIndex(n.snapshots).hour)
    n.links_t.marginal_cost["import"] = np.where(h < 6, 2.0, 4.0)
    out = P.commercial_findings(n, {"poc_link": "import", "import_tariff": _tariff(TOU)})
    [(_s, _c, _cls, _n, msg)] = _hits(out)
    # Own 2.00–4.00; tariff 50.00 (night) – 200.00 (day); sum 52.00–204.00.
    assert "2.00–4.00" in msg and "50.00–200.00" in msg and "52.00–204.00" in msg


def test_no_warning_without_a_typed_price_or_without_an_energy_price():
    n = build_edge_15min()
    assert _hits(P.commercial_findings(
        n, {"poc_link": "import", "import_tariff": _tariff(ENERGY)})) == []
    n.links.loc["import", "marginal_cost"] = 12.0
    # A demand charge alone prices no MWh through the Link.
    assert _hits(P.commercial_findings(
        n, {"poc_link": "import", "import_tariff": _tariff(DEMAND)})) == []
    assert _hits(P.commercial_findings(n, {"poc_link": "import"})) == []


def test_the_export_link_counts_its_fee_net_of_the_export_price():
    n = build_edge_15min()
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC",
          marginal_cost=3.0)
    n.links_t[L.EXPORT_PRICE_ATTR] = pd.DataFrame({"export": 40.0}, index=n.snapshots)
    out = P.commercial_findings(n, {"poc_link": "import", "export_link": "export",
                                    "export_price_ref": REF,
                                    "import_tariff": _tariff(EXPORT_FEE)})
    [(_s, _c, _cls, name, msg)] = _hits(out)
    # Own +3.00; tariff fee 10.00 − price 40.00 = −30.00; together −27.00.
    assert name == "export" and "3.00" in msg and "-30.00" in msg and "-27.00" in msg


def test_the_numbers_are_unchanged():
    """The warning changes nothing: the materialised price is still the sum."""
    n = build_edge_15min()
    n.links.loc["import", "marginal_cost"] = 12.0
    applied = L.materialise_poc_prices(n, {"poc_link": "import",
                                           "import_tariff": _tariff(ENERGY)})
    try:
        assert np.allclose(n.links_t.marginal_cost["import"].to_numpy(), 112.0)
    finally:
        applied.undo()
