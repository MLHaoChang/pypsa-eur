"""
IC S3 seeds, step 1 (plan `2026-10-13-ic-engine-asks.md` §7; owner 2026-10-13, Q1 a): the defaults
pack version 2026-10-13 = 2026-10-07 unchanged plus the template technologies the pack lacked, from
the same catalogue as the battery and solar rows (technology-data v0.14.0, `outputs/costs_2030.csv`,
2020 EUR):

  * `gas-genset`     ← OCGT ("52 OCGT - Natural gas")
  * `diesel-genset`  ← oil ("50 Diesel engine farm")
  * `onwind`         ← onwind ("20 Onshore turbines")
  * `fuel-cell`      ← fuel cell ("12 LT-PEMFC CHP")
  * `electrolysis`   ← electrolysis ("86 AEC 100 MW"; investment from the IEA e-fuels report)

each as one `investment` part per MW: overnight, fom_share, lifetime and (except onwind) efficiency,
in the catalogue's own unit, with the pack's assumed ±30 % range (efficiency capped at 1). The
guided study pins 2026-10-05, so it reads none of this.

Written red first: the version did not exist.
"""
from __future__ import annotations

import pytest

NEW, PREV = "2026-10-13", "2026-10-07"
TD_URL = ("https://raw.githubusercontent.com/PyPSA/technology-data/refs/tags/v0.14.0/outputs/"
          "costs_2030.csv")
DEA_EL = "Danish Energy Agency, inputs/technology_data_for_el_and_dh.xlsx"
DEA_FUELS = "Danish Energy Agency, inputs/data_sheets_for_renewable_fuels.xlsx"
IEA = ("private communications; IEA https://iea.blob.core.windows.net/assets/"
       "9e0c82d4-06d2-496b-9542-f184ba803645/TheRoleofE-fuelsinDecarbonisingTransport.pdf")

#: (technology, catalogue name, parameter) → (original value, original unit, source), verbatim
#: from technology-data v0.14.0 `outputs/costs_2030.csv`.
TD: dict[tuple[str, str, str], tuple[float, str, str]] = {
    ("gas-genset", "OCGT", "overnight"): (581.3949, "EUR/kW", DEA_EL),
    ("gas-genset", "OCGT", "fom_share"): (1.7795, "%/year", DEA_EL),
    ("gas-genset", "OCGT", "lifetime"): (25.0, "years", DEA_EL),
    ("gas-genset", "OCGT", "efficiency"): (0.41, "per unit", DEA_EL),
    ("diesel-genset", "oil", "overnight"): (458.1805, "EUR/kW", DEA_EL),
    ("diesel-genset", "oil", "fom_share"): (2.463, "%/year", DEA_EL),
    ("diesel-genset", "oil", "lifetime"): (25.0, "years", DEA_EL),
    ("diesel-genset", "oil", "efficiency"): (0.35, "per unit", DEA_EL),
    ("onwind", "onwind", "overnight"): (1383.3059, "EUR/kW", DEA_EL),
    ("onwind", "onwind", "fom_share"): (1.2167, "%/year", DEA_EL),
    ("onwind", "onwind", "lifetime"): (30.0, "years", DEA_EL),
    ("fuel-cell", "fuel cell", "overnight"): (1469.3834, "EUR/kW_e", DEA_EL),
    ("fuel-cell", "fuel cell", "fom_share"): (5.0, "%/year", DEA_EL),
    ("fuel-cell", "fuel cell", "lifetime"): (10.0, "years", DEA_EL),
    ("fuel-cell", "fuel cell", "efficiency"): (0.5, "per unit", DEA_EL),
    ("electrolysis", "electrolysis", "overnight"): (1886.0019, "EUR/kW_e", IEA),
    ("electrolysis", "electrolysis", "fom_share"): (4.0, "%/year", DEA_FUELS),
    ("electrolysis", "electrolysis", "lifetime"): (25.0, "years", DEA_FUELS),
    ("electrolysis", "electrolysis", "efficiency"): (0.6217, "per unit", DEA_FUELS),
}
FACTOR = {"EUR/kW": 1000.0, "EUR/kW_e": 1000.0, "%/year": 0.01, "years": 1.0, "per unit": 1.0}


@pytest.fixture(scope="module")
def new():
    from services.library.defaults_pack.loader import load_defaults_pack

    return load_defaults_pack(NEW)


@pytest.fixture(scope="module")
def prev():
    from services.library.defaults_pack.loader import load_defaults_pack

    return load_defaults_pack(PREV)


def test_the_new_version_is_the_latest_and_pinned(new, prev):
    import json
    import pathlib

    from services.library.defaults_pack import loader

    pins = json.loads((pathlib.Path(__file__).parent / "fixtures" / "defaults_pack"
                       / "pack_hashes.json").read_text())["generic_defaults"]
    assert loader.available_versions()[-2:] == (PREV, NEW)
    assert loader.load_defaults_pack().version == NEW
    assert new.hash == pins[NEW] != prev.hash == pins[PREV]


def test_every_previous_row_is_kept_unchanged(new, prev):
    rows = {v.key: v for v in new.cost_values}
    for old in prev.cost_values:
        assert rows[old.key] == old, old.key
    assert new.finance == prev.finance and new.tariffs == prev.tariffs


def test_the_template_technologies_are_transcribed_from_the_catalogue(new):
    rows = {v.key: v for v in new.cost_values}
    added = {k for k in rows if k.split(".")[0] in {t for t, _c, _p in TD}}
    assert added == {f"{t}.investment.{p}" for t, _c, p in TD}
    for (tech, catalogue, param), (value, unit, source) in TD.items():
        r = rows[f"{tech}.investment.{param}"]
        assert (r.technology, r.part, r.basis, r.source_technology, r.parameter) == \
            (tech, "investment", "per_MW", catalogue, param), r.key
        assert (r.original_value, r.original_unit, r.source, r.source_url) == \
            (value, unit, source, TD_URL), r.key
        assert r.value == pytest.approx(value * FACTOR[unit], rel=1e-12), r.key
        assert r.price_basis == "real" and r.projection_year == 2030 and not r.illustrative
        if param == "overnight":
            assert (r.currency, r.currency_year) == ("EUR", 2020), r.key
        hi = min(1.3 * value, 1.0) if param == "efficiency" else 1.3 * value
        f = FACTOR[unit]                                # ranges are converted with the value
        assert r.range.low == pytest.approx(round(0.7 * value, 4) * f, rel=1e-9), r.key
        assert r.range.high == pytest.approx(round(hi, 4) * f, rel=1e-9), r.key
        assert r.range.source == "assumed"


def test_the_guided_study_still_reads_its_pinned_pack():
    from services.study import library as L

    assert L.PACK_VERSION == "2026-10-05"
