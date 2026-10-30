"""
AS-02 (`.scratch/asset-sizing-loop/issues/02-separate-power-energy-storage.md`): the defaults pack version 2026-10-30 =
2026-10-26 unchanged plus two storage technologies, each priced in a power part (per MW) and an energy part (per
MWh), for the split Store + Link storage of `services/solver/split_storage.py`:

  * `supercapacitor` <- PNNL-28866 (Mongird et al., *Energy Storage Technology and Cost Characterization Report*,
    July 2019), section 4.3.11 and Table ES-1 / 4.x: the module at $32,365/kWh (E/P 0.0124 h); PCS $350/kW,
    balance of plant $100/kW and construction and commissioning $80/kW as the power part; fixed O&M $1/kW-yr;
    16 years; round-trip efficiency 0.92. 2018 USD, converted at the ECB 2018 annual average 1.18095 USD/EUR,
    no price index (MONEY-YEAR-1); the row is 2018 EUR.
  * `high-c-battery` <- the pack's DEA `battery` rows, with the energy part at 1.75 x (the midpoint of PNNL-28866
    section 4.2.1's "LTO systems had a 50-100 percent higher cost" than NMC) and range 1.5-2.0 x. The power
    part (inverter per MW), lifetimes and efficiencies are the battery's.

All ranges are "assumed" (SAGC-1: public sources and generic engineering only); no row is illustrative.
"""
from __future__ import annotations

import csv
import json
import pathlib

import pytest

NEW, PREV = "2026-10-30", "2026-10-26"
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "defaults_pack"
PNNL = ("https://www.energy.gov/sites/prod/files/2019/07/f65/"
        "Storage%20Cost%20and%20Performance%20Characterization%20Report_Final.pdf")
USD_PER_EUR_2018 = 1.18095
LTO = 1.75

#: key -> (original value, original unit, pack value, money year)
SUPERCAP = {
    "supercapacitor.power.overnight": (530 / USD_PER_EUR_2018, "EUR/kW", 1000 * 530 / USD_PER_EUR_2018, 2018),
    "supercapacitor.energy.overnight": (32365 / USD_PER_EUR_2018, "EUR/kWh", 1000 * 32365 / USD_PER_EUR_2018,
                                        2018),
    "supercapacitor.power.fom_share": (100 * 1 / 930, "%/year", 1 / 930, None),
    "supercapacitor.power.lifetime": (16.0, "years", 16.0, None),
    "supercapacitor.energy.lifetime": (16.0, "years", 16.0, None),
    "supercapacitor.round_trip_efficiency": (0.92, "per unit", 0.92, None),
}
HIGH_C_COPIED = ("power.overnight", "power.fom_share", "power.efficiency", "power.lifetime", "energy.lifetime",
                 "round_trip_efficiency")


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


def test_the_new_version_follows_and_is_pinned(new, prev):
    from services.library.defaults_pack import loader

    pins = json.loads((FIXTURES / "pack_hashes.json").read_text())["generic_defaults"]
    versions = loader.available_versions()
    assert versions[versions.index(PREV) + 1] == NEW
    assert new.hash == pins[NEW] != prev.hash == pins[PREV]
    assert new.typed and NEW not in loader.LEGACY_VERSIONS


def test_every_previous_row_is_kept_unchanged(new, prev):
    old_rows, new_rows = _rows(PREV), _rows(NEW)
    assert new_rows[:len(old_rows)] == old_rows
    added = {r["key"] for r in new_rows[len(old_rows):]}
    assert added == set(SUPERCAP) | {f"high-c-battery.{k}" for k in (*HIGH_C_COPIED, "energy.overnight")}
    assert new.finance == prev.finance and new.tariffs == prev.tariffs


def test_the_supercapacitor_rows_are_transcribed_from_pnnl(new):
    for key, (original, unit, value, money_year) in SUPERCAP.items():
        v = new.value(key)
        assert v.original_value == pytest.approx(original, rel=1e-4) and v.original_unit == unit, key
        assert v.value == pytest.approx(value, rel=1e-4), key
        assert v.currency_year == money_year and (v.currency == "EUR") == (money_year is not None), key
        assert v.source_url == PNNL and "PNNL-28866" in v.source and not v.illustrative, key
        assert v.range is not None and v.range.source == "assumed", key
    assert new.value("supercapacitor.power.overnight").capacity_basis == "output_electric_ac"
    assert new.value("supercapacitor.energy.overnight").capacity_basis == "storage_energy"


def test_the_high_c_battery_is_the_battery_with_the_lto_premium_on_energy(new):
    for k in HIGH_C_COPIED:
        hc, b = new.value(f"high-c-battery.{k}"), new.value(f"battery.{k}")
        assert (hc.value, hc.unit, hc.currency_year) == (b.value, b.unit, b.currency_year), k
        assert hc.range.source == "assumed", k
    hc, b = new.value("high-c-battery.energy.overnight"), new.value("battery.energy.overnight")
    assert hc.value == pytest.approx(LTO * b.value, rel=1e-6)
    assert (hc.range.low, hc.range.high) == (pytest.approx(1.5 * b.value, rel=1e-6),
                                             pytest.approx(2.0 * b.value, rel=1e-6))
    assert hc.range.source == "assumed" and hc.currency_year == b.currency_year == 2025
    assert "PNNL-28866" in hc.source and hc.source_url == PNNL


def test_every_split_storage_technology_is_priced_in_the_pack(new):
    from services.solver.split_storage import STORAGE_TECHNOLOGIES

    for tech in STORAGE_TECHNOLOGIES.values():
        assert new.value(f"{tech.pack_technology}.power.overnight").basis == "per_MW"
        assert new.value(f"{tech.pack_technology}.energy.overnight").basis == "per_MWh"
