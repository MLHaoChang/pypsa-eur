"""
Owner decision MONEY-YEAR-1: defaults pack version 2026-10-25 = 2026-10-24 with the technology-data rows relabelled
to 2025 money. technology-data v0.14.0 is built with `eur_year: 2025` (its config.yaml), so every value in its
`outputs/costs_2030.csv` is 2025 EUR; the CSV's `currency_year` column is the year each source was inflated from.
Only the money year moves: every value, every other row (a row transcribed from a DEA workbook keeps its sheet's
year), the finance defaults' currency year, the tariffs and the load profiles are as in 2026-10-24.
"""
from __future__ import annotations

import csv
import json
import pathlib

import pytest

NEW, PREV = "2026-10-25", "2026-10-24"
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "defaults_pack"
TD = "https://raw.githubusercontent.com/PyPSA/technology-data/refs/tags/v0.14.0/outputs/costs_2030.csv"

#: the relabelled rows -> their (unchanged) costs_2030.csv value and unit
RELABELLED = {
    "battery.power.overnight": (213.9279, "EUR/kW"),
    "battery.energy.overnight": (189.861, "EUR/kWh"),
    "solar-utility.investment.overnight": (482.4785, "EUR/kW_e"),
    "solar-rooftop.investment.overnight": (883.8138, "EUR/kW_e"),
    "gas-genset.investment.overnight": (581.3949, "EUR/kW"),
    "diesel-genset.investment.overnight": (458.1805, "EUR/kW"),
    "onwind.investment.overnight": (1383.3059, "EUR/kW"),
    "fuel-cell.investment.overnight": (1469.3834, "EUR/kW_e"),
    "electrolysis.investment.overnight": (1886.0019, "EUR/kW_e"),
    "haber-bosch.investment.overnight": (1842.9888, "EUR/kW_NH3"),
    "nh3-tank.investment.overnight": (211.8256, "EUR/MWh_NH3"),
    "h2-tank.investment.overnight": (60.0469, "EUR/kWh"),
}


def _rows(version: str) -> dict[str, dict]:
    from services.library.defaults_pack.loader import VERSIONS_DIR

    with (VERSIONS_DIR / version / "values.csv").open(encoding="utf-8", newline="") as fh:
        return {r["key"]: r for r in csv.DictReader(fh)}


def test_the_new_version_directly_succeeds_the_previous_and_is_the_latest():
    from services.library.defaults_pack import loader

    pins = json.loads((FIXTURES / "pack_hashes.json").read_text())["generic_defaults"]
    versions = loader.available_versions()
    assert versions[versions.index(PREV) + 1] == NEW == versions[-1]
    new, prev = loader.load_defaults_pack(NEW), loader.load_defaults_pack(PREV)
    assert new.hash == pins[NEW] != prev.hash == pins[PREV]
    assert new.typed and NEW not in loader.LEGACY_VERSIONS


def test_only_the_technology_data_money_rows_change_and_only_their_money_year():
    old, new = _rows(PREV), _rows(NEW)
    assert list(old) == list(new)
    changed = {}
    for key, row in new.items():
        diff = {c for c in row if row[c] != old[key][c]}
        if diff:
            changed[key] = diff
    assert changed == {key: {"money_year"} for key in RELABELLED}
    for key, (value, unit) in RELABELLED.items():
        row, was = new[key], old[key]
        assert (was["money_year"], row["money_year"]) == ("2020", "2025")
        assert row["source_url"] == TD and row["currency"] == "EUR"
        assert (float(row["original_value"]), row["original_unit"]) == (value, unit)


def test_every_technology_data_money_row_is_2025_and_the_dea_workbook_rows_keep_their_year():
    for key, row in _rows(NEW).items():
        if not row["currency"]:
            continue
        if row["source_url"] == TD:
            assert row["money_year"] == "2025", key
    rows = _rows(NEW)
    assert rows["electric-process-heater.investment.overnight"]["money_year"] == "2019"      # DEA 312.c
    assert rows["thermal-storage-molten-salt.investment.overnight"]["money_year"] == "2020"  # DEA 143b


@pytest.mark.parametrize("key", sorted(RELABELLED))
def test_the_loaded_rows_carry_2025_and_the_same_value(key):
    from services.library.defaults_pack.loader import load_defaults_pack

    new, prev = load_defaults_pack(NEW).value(key), load_defaults_pack(PREV).value(key)
    assert (prev.money_year, new.money_year) == (2020, 2025)
    assert new.model_dump(exclude={"currency_year"}) == prev.model_dump(exclude={"currency_year"})


def test_finance_tariffs_and_profiles_are_unchanged():
    from services.library.defaults_pack.loader import load_defaults_pack

    new, prev = load_defaults_pack(NEW), load_defaults_pack(PREV)
    assert new.finance.currency_year == prev.finance.currency_year == 2020    # the seed tariffs' year
    assert "2025" in new.finance.currency_year_source
    assert new.tariff_ids() == prev.tariff_ids() and new.load_profile_ids() == prev.load_profile_ids()
    for tid in new.tariff_ids():
        assert new.tariff_meta(tid) == prev.tariff_meta(tid)
    manifest = json.loads((pathlib.Path(__file__).parents[1] / "services" / "library" / "defaults_pack"
                           / "versions" / NEW / "manifest.json").read_text())
    assert manifest["money_year_relabel"]["from"] == PREV
    assert sorted(manifest["money_year_relabel"]["relabelled"]) == sorted(RELABELLED)


@pytest.mark.parametrize("version,year", [(PREV, 2020), (NEW, 2025)])
def test_pricing_stamps_the_row_money_year_and_the_same_cost(version, year):
    import pypsa
    from services.library.defaults_pack.pricing import price_from_pack

    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "w", bus="b", carrier="onwind")
    out = price_from_pack(n, "generators", "w", "onwind", version=version)
    assert out["money_year"] == year
    assert out["overnight_cost"] == pytest.approx(1383305.9)
