"""
Refinery electrification gap 2 (`.scratch/grid-to-process/research/se-electrifying-refining-desw024.md` §6a.3): the
defaults pack version 2026-10-24 = 2026-10-23 unchanged plus the electrified heat technologies, each one `investment`
part per MW of heat output (per MWh of storage for the store):

  * `electric-boiler-steam`       ← technology-data v0.14.0 "electric boiler steam" (DEA 310.1), 2025 EUR
  * `industrial-heat-pump-ht`     ← technology-data v0.14.0 "industrial heat pump high temperature" (DEA 302.b), 2025 EUR
  * `electric-process-heater`     ← DEA Technology Data for Industrial Process Heat, 312.c Direct firing, Electricity, 2019 EUR
  * `thermal-storage-molten-salt` ← DEA Technology Data for Energy Storage, 143b Molten salt (industrial steam), 2020 EUR

technology-data v0.14.0 is built with `eur_year: 2025` (its config.yaml), so its costs are 2025 EUR. A range the DEA
sheet states for 2030 is the row's range (`source`); every other range is the pack's assumed ±30 %. The operating
range is a row where the sheet gives it: `min_load`, and the heat pump's sink and the store's steam temperature.
"""
from __future__ import annotations

import csv
import json
import pathlib

import pytest

NEW, PREV = "2026-10-24", "2026-10-23"
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "defaults_pack"
TAG = "https://raw.githubusercontent.com/PyPSA/technology-data/refs/tags/v0.14.0"
TD, HEAT, STORE = (f"{TAG}/outputs/costs_2030.csv",
                   f"{TAG}/inputs/technology_data_for_industrial_process_heat.xlsx",
                   f"{TAG}/inputs/technology_data_catalogue_for_energy_storage.xlsx")

#: key -> (original value, original unit, pack value, money year, range in pack units or "assumed", source url),
#: each value verbatim from its source cell (2030 column).
ROWS = {
    "electric-boiler-steam.investment.overnight": (88.6295, "EUR/kW", 88629.5, 2025, "assumed", TD),
    "electric-boiler-steam.investment.fom_share": (1.4571, "%/year", 0.014571, None, "assumed", TD),
    "electric-boiler-steam.investment.lifetime": (25.0, "years", 25.0, None, (20.0, 25.0), TD),
    "electric-boiler-steam.investment.efficiency": (0.99, "per unit", 0.99, None, (0.98, 0.99), TD),
    "electric-boiler-steam.investment.min_load": (2.0, "%", 0.02, None, "assumed", HEAT),
    "industrial-heat-pump-ht.investment.overnight": (1183.28, "EUR/kW", 1183280.0, 2025, "assumed", TD),
    "industrial-heat-pump-ht.investment.fom_share": (0.0931, "%/year", 0.000931, None, "assumed", TD),
    "industrial-heat-pump-ht.investment.lifetime": (20.0, "years", 20.0, None, (15.0, 30.0), TD),
    "industrial-heat-pump-ht.investment.efficiency": (3.05, "per unit", 3.05, None, (3.05, 3.36), TD),
    "industrial-heat-pump-ht.investment.min_load": (25.0, "%", 0.25, None, "assumed", HEAT),
    "industrial-heat-pump-ht.investment.max_sink_temperature": (150.0, "degC", 150.0, None, None, HEAT),
    "electric-process-heater.investment.overnight": (0.06, "MEUR/MW", 60000.0, 2019, "assumed", HEAT),
    "electric-process-heater.investment.fom_share": (0.0, "%/year", 0.0, None, (0.0, 0.0), HEAT),
    "electric-process-heater.investment.lifetime": (12.5, "years", 12.5, None, (10.0, 15.0), HEAT),
    "electric-process-heater.investment.efficiency": (100.0, "%", 1.0, None, (1.0, 1.0), HEAT),
    "electric-process-heater.investment.min_load": (15.0, "%", 0.15, None, "assumed", HEAT),
    "thermal-storage-molten-salt.investment.overnight": (62.7096, "kEUR/MWh", 62709.6, 2020, "assumed", STORE),
    "thermal-storage-molten-salt.investment.fom_share": (1.5, "%/year", 0.015, None, "assumed", STORE),
    "thermal-storage-molten-salt.investment.lifetime": (25.0, "years", 25.0, None, "assumed", STORE),
    "thermal-storage-molten-salt.investment.efficiency": (92.0, "%", 0.92, None, "assumed", STORE),
    "thermal-storage-molten-salt.investment.steam_temperature": (250.0, "degC", 250.0, None, None, STORE),
}
#: technology -> (basis, capacity_basis, includes_grid_connection)
TYPED = {
    "electric-boiler-steam": ("per_MW", "output_product", "unknown"),
    "industrial-heat-pump-ht": ("per_MW", "output_product", "unknown"),
    "electric-process-heater": ("per_MW", "output_product", "unknown"),
    "thermal-storage-molten-salt": ("per_MWh", "storage_energy", False),     # DEA 143b note K: excluded
}


@pytest.fixture(scope="module")
def new():
    from services.library.defaults_pack.loader import load_defaults_pack

    return load_defaults_pack(NEW)


@pytest.fixture(scope="module")
def prev():
    from services.library.defaults_pack.loader import load_defaults_pack

    return load_defaults_pack(PREV)


def _rows(version):
    from services.library.defaults_pack import loader

    with (loader.version_dir(version) / "values.csv").open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_the_new_version_follows_the_campus_one_and_is_pinned(new, prev):
    from services.library.defaults_pack import loader

    pins = json.loads((FIXTURES / "pack_hashes.json").read_text())["generic_defaults"]
    versions = loader.available_versions()
    assert versions[versions.index(PREV) + 1] == NEW
    assert new.hash == pins[NEW] != prev.hash == pins[PREV]
    assert new.typed and NEW not in loader.LEGACY_VERSIONS


def test_every_previous_row_is_kept_unchanged(new, prev):
    old_rows, new_rows = _rows(PREV), _rows(NEW)
    assert new_rows[:len(old_rows)] == old_rows
    assert {r["key"] for r in new_rows[len(old_rows):]} == set(ROWS)
    assert new.finance == prev.finance and new.tariffs == prev.tariffs


def test_the_heat_rows_are_transcribed_from_their_source_cells(new):
    for key, (original, unit, value, money_year, rng, url) in ROWS.items():
        v = new.value(key)
        tech = key.split(".")[0]
        basis, capacity, grid = TYPED[tech]
        assert (v.part, v.basis, v.capacity_basis, v.includes_grid_connection) == ("investment", basis, capacity,
                                                                                   grid), key
        assert v.original_value == pytest.approx(original, rel=1e-9) and v.original_unit == unit, key
        assert v.value == pytest.approx(value, rel=1e-9), key
        assert v.currency_year == money_year and (v.currency == "EUR") == (money_year is not None), key
        assert v.source_url == url and v.source and v.price_basis == "real" and not v.illustrative, key
        assert v.projection_year == 2030
        if rng is None:
            assert v.range is None, key
        elif rng == "assumed":
            assert v.range.source == "assumed", key
            assert v.range.low == pytest.approx(round(0.7 * original, 4) * value / original, rel=1e-9), key
            hi = round(1.3 * original, 4) * value / original
            assert v.range.high == pytest.approx(min(hi, 1.0) if v.unit == "per unit" and value <= 1 else hi,
                                                 rel=1e-9), key
        else:
            assert (v.range.low, v.range.high, v.range.source) == (pytest.approx(rng[0]), pytest.approx(rng[1]),
                                                                    "source"), key


def test_the_heat_pump_states_a_cop_and_the_others_an_efficiency_within_one(new):
    assert new.value("industrial-heat-pump-ht.investment.efficiency").domain == "[1, inf)"
    for tech in ("electric-boiler-steam", "electric-process-heater", "thermal-storage-molten-salt"):
        assert new.value(f"{tech}.investment.efficiency").domain == "(0, 1]"


def test_a_scaled_money_unit_keeps_its_currency():
    """The DEA sheets state MEUR per MW and kEUR per MWh: a scale prefix is not a currency."""
    from services.library.defaults_pack import loader as D

    assert [D._money_currency(u) for u in ("MEUR/MW", "kEUR/MWh", "EUR/MW", "GBP/kW", "%", "degC")] == \
        ["EUR", "EUR", "EUR", "GBP", None, None]
    assert D.UNIT_CONVERSIONS[("MEUR/MW", "EUR/MW")] == 1e6
    assert D.UNIT_CONVERSIONS[("kEUR/MWh", "EUR/MWh")] == 1000.0


@pytest.mark.parametrize("tech,eff", [("electric-boiler-steam", 0.99), ("electric-process-heater", 1.0),
                                      ("industrial-heat-pump-ht", 3.05)])
def test_a_heat_link_is_priced_per_mw_of_heat_out(new, tech, eff):
    """p_nom is the electrical input (bus0); the row is per MW of heat, so it scales by the heat bus's
    efficiency (the heat pump's COP, with its low-grade source drawn on bus2)."""
    import pypsa

    from services.library.defaults_pack.pricing import price_from_pack

    n = pypsa.Network()
    n.add("Bus", "ac", carrier="AC")
    n.add("Bus", "heat", carrier="heat")
    n.add("Bus", "low", carrier="low-grade heat")
    n.add("Link", "u", bus0="ac", bus1="heat", bus2="low", efficiency=eff,
          efficiency2=-(eff - 1) if eff > 1 else 0.0, p_nom_extendable=True)
    out = price_from_pack(n, "links", "u", tech, version=NEW)
    assert out["scale"] == pytest.approx(eff) and out["capacity_basis"] == "output_product"
    assert n.links.at["u", "overnight_cost"] == pytest.approx(eff * new.value(f"{tech}.investment.overnight").value)


def test_the_heat_store_is_priced_per_mwh():
    import pypsa

    from services.library.defaults_pack.pricing import price_from_pack

    n = pypsa.Network()
    n.add("Bus", "heat", carrier="heat")
    n.add("Store", "tes", bus="heat", carrier="heat", e_nom_extendable=True)
    out = price_from_pack(n, "stores", "tes", "thermal-storage-molten-salt", version=NEW)
    assert out["scale"] == 1.0 and out["includes_grid_connection"] is False
