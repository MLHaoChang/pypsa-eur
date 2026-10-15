"""Slice D4: a Guided staged case is not refused for its CODs.

`compile.finance_from_ledger` types the ledger's one COD year only on the
owned assets in service at the first period; a later-built asset is left to
the finance adapter's `build_year` resolution (P5c). `compile.option_finance`
takes the model year from the first investment period on a multi-period
network (a MultiIndex has no `.year`).
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tests.golden import site_fixture as sf

META = {"currency": "EUR", "currency_year": 2020, "tariff_id": "t"}


def _C():
    from services.study import compile as C

    return C


def _ledger(*, dated: bool = True):
    """The guided ledger; `dated=False` drops its COD and close rows, so both
    follow the model year (`finance_from_ledger`'s default)."""
    from services.study import library as L
    from services.study import questions as Q

    led = L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), L.load_defaults())
    if dated:
        return led
    rows = [r for r in led.rows if r.key not in ("cod_year", "financial_close_year")]
    return led.model_copy(update={"rows": rows})


def test_a_later_asset_is_left_to_its_build_year():
    fin = _C().finance_from_ledger(_ledger(dated=False), model_year=2030, owned_assets=("pv", "battery"),
                                   tariff_meta=META, later_assets=("battery",)).inputs
    assert fin.cod_by_asset == {"pv": date(2030, 1, 1)}
    # Without later assets every owned asset is typed, as before.
    fin = _C().finance_from_ledger(_ledger(dated=False), model_year=2030, owned_assets=("pv", "battery"),
                                   tariff_meta=META).inputs
    assert fin.cod_by_asset == {"pv": date(2030, 1, 1), "battery": date(2030, 1, 1)}


def _staged_network():
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.set_investment_periods([2030, 2040])
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "site", carrier="AC")
    n.add("Generator", "grid_supply", bus="grid", p_nom=100.0, marginal_cost=50.0)
    n.add("Link", "import", bus0="grid", bus1="site", p_nom=50.0)
    n.add("Load", "load", bus="site", p_set=10.0)
    n.add("Generator", "pv", bus="site", p_nom=5.0)                         # existing
    n.add("Generator", "pv_new", bus="site", p_nom_extendable=True, build_year=2030,
          lifetime=25.0)
    n.add("StorageUnit", "battery", bus="site", p_nom_extendable=True, build_year=2040,
          lifetime=15.0, max_hours=2.0)
    return n


def test_option_finance_on_a_multi_period_network_types_the_first_period_only():
    from models.commercial import CommercialConfig

    C = _C()
    n = _staged_network()
    assert isinstance(n.snapshots, pd.MultiIndex)
    compiled = C._finish(CommercialConfig(poc_link="import"), {}, META, [])
    fin = C.option_finance(_ledger(dated=False), compiled, n).inputs
    # The model year is the first period: close a year before, COD 2030 typed
    # on the assets in service then (no build year, or built by 2030) ...
    assert fin.financial_close == date(2029, 1, 1)
    assert fin.cod_by_asset["pv"] == date(2030, 1, 1)
    assert fin.cod_by_asset["pv_new"] == date(2030, 1, 1)
    # ... and the 2040 build left to the adapter (its build year).
    assert "battery" not in fin.cod_by_asset


def test_option_finance_on_a_flat_network_is_unchanged():
    from models.commercial import CommercialConfig

    import pypsa

    C = _C()

    m = pypsa.Network()
    m.set_snapshots(pd.date_range("2031-01-01", periods=4, freq="h"))
    m.add("Bus", "grid", carrier="AC")
    m.add("Bus", "site", carrier="AC")
    m.add("Generator", "grid_supply", bus="grid", p_nom=100.0)
    m.add("Link", "import", bus0="grid", bus1="site", p_nom=50.0)
    m.add("Load", "load", bus="site", p_set=10.0)
    m.add("StorageUnit", "battery", bus="site", p_nom_extendable=True, max_hours=2.0)
    compiled = C._finish(CommercialConfig(poc_link="import"), {}, META, [])
    fin = C.option_finance(_ledger(dated=False), compiled, m).inputs
    assert fin.cod_by_asset["battery"] == date(2031, 1, 1)
    assert fin.financial_close == date(2030, 1, 1)


@pytest.mark.parametrize("build_year,typed", [(0, True), (2030, True), (2040, False)])
def test_in_service_at_the_first_period(build_year, typed):
    n = _staged_network()
    n.storage_units.loc["battery", "build_year"] = build_year
    assert ("battery" in _C().later_built_assets(n, ["battery"], 2030)) is not typed
