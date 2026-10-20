"""
Grid-to-process ticket 05: the defaults pack version 2026-10-20 = 2026-10-13 unchanged plus the ammonia technologies the
`eh_ammonia_staged` template prices from, from technology-data v0.14.0 (`outputs/costs_2030.csv`, 2020 EUR):

  * `haber-bosch` ← Haber-Bosch (per kW of NH3 output; investment, FOM, lifetime)
  * `nh3-tank`    ← NH3 (l) storage tank incl. liquefaction (per MWh_NH3)
  * `h2-tank`     ← hydrogen storage tank type 1 including compressor (per kWh_H2)

each as one `investment` part: overnight, fom_share, lifetime, with the pack's assumed ±30 % range. The Haber-Bosch
electricity input (0.2473 MWh_el/MWh_NH3) is a template number, ledgered there (D3), not a pack row.
"""
from __future__ import annotations

import csv
import json
import pathlib

import pytest

NEW, PREV = "2026-10-20", "2026-10-13"
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "defaults_pack"
TD_URL = ("https://raw.githubusercontent.com/PyPSA/technology-data/refs/tags/v0.14.0/outputs/"
          "costs_2030.csv")
DEA_FUELS = "Danish Energy Agency, inputs/data_sheets_for_renewable_fuels.xlsx"
DEA_STORAGE = "Danish Energy Agency, inputs/technology_data_catalogue_for_energy_storage.xlsx"
MORGAN = "Calculated based on Morgan E. 2013: doi:10.7275/11KT-3F59 , Fig. 55, Fig 58."

#: (technology, catalogue name, basis, parameter) → (value, unit, pack unit, factor, source), verbatim from the CSV.
TD = {
    ("haber-bosch", "Haber-Bosch", "per_MW", "overnight"): (1842.9888, "EUR/kW_NH3", "EUR/MW", 1000.0, DEA_FUELS),
    ("haber-bosch", "Haber-Bosch", "per_MW", "fom_share"): (3.0, "%/year", "share/year", 0.01, DEA_FUELS),
    ("haber-bosch", "Haber-Bosch", "per_MW", "lifetime"): (30.0, "years", "years", 1.0, DEA_FUELS),
    ("nh3-tank", "NH3 (l) storage tank incl. liquefaction", "per_MWh", "overnight"):
        (211.8256, "EUR/MWh_NH3", "EUR/MWh", 1.0, MORGAN),
    ("nh3-tank", "NH3 (l) storage tank incl. liquefaction", "per_MWh", "fom_share"):
        (2.0, "%/year", "share/year", 0.01, "Guesstimate, based on H2 (l) storage tank."),
    ("nh3-tank", "NH3 (l) storage tank incl. liquefaction", "per_MWh", "lifetime"):
        (20.0, "years", "years", 1.0, "Morgan E. 2013: doi:10.7275/11KT-3F59 , pg. 290"),
    ("h2-tank", "hydrogen storage tank type 1 including compressor", "per_MWh", "overnight"):
        (60.0469, "EUR/kWh", "EUR/MWh", 1000.0, DEA_STORAGE),
    ("h2-tank", "hydrogen storage tank type 1 including compressor", "per_MWh", "fom_share"):
        (1.1133, "%/year", "share/year", 0.01, DEA_STORAGE),
    ("h2-tank", "hydrogen storage tank type 1 including compressor", "per_MWh", "lifetime"):
        (30.0, "years", "years", 1.0, DEA_STORAGE),
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


def test_the_new_version_is_the_latest_and_pinned(new, prev):
    from services.library.defaults_pack import loader

    pins = json.loads((FIXTURES / "pack_hashes.json").read_text())["generic_defaults"]
    assert loader.available_versions()[-2:] == (PREV, NEW)
    assert loader.load_defaults_pack().version == NEW
    assert new.hash == pins[NEW] != prev.hash == pins[PREV]


def test_every_previous_row_is_kept_unchanged(new, prev):
    old_rows, new_rows = _rows(PREV), _rows(NEW)
    assert new_rows[:len(old_rows)] == old_rows
    assert [v.model_dump() for v in new.cost_values[:len(prev.cost_values)]] == \
        [v.model_dump() for v in prev.cost_values]
    assert new.finance == prev.finance and new.tariffs == prev.tariffs


def test_the_ammonia_technologies_are_transcribed_from_the_catalogue(new):
    rows = {v.key: v for v in new.cost_values}
    added = {k for k in rows if k.split(".")[0] in {t for t, _c, _b, _p in TD}}
    assert added == {f"{t}.investment.{p}" for t, _c, _b, p in TD}
    for (tech, catalogue, basis, param), (value, unit, pack_unit, factor, source) in TD.items():
        r = rows[f"{tech}.investment.{param}"]
        assert (r.technology, r.part, r.basis, r.source_technology, r.parameter) == \
            (tech, "investment", basis, catalogue, param), r.key
        assert (r.original_value, r.original_unit, r.unit, r.source, r.source_url) == \
            (value, unit, pack_unit, source, TD_URL), r.key
        assert r.value == pytest.approx(value * factor, rel=1e-12), r.key
        assert r.price_basis == "real" and r.projection_year == 2030 and not r.illustrative
        if param == "overnight":
            assert (r.currency, r.currency_year) == ("EUR", 2020), r.key
        assert r.range.low == pytest.approx(round(0.7 * value, 4) * factor, rel=1e-9), r.key
        assert r.range.high == pytest.approx(round(1.3 * value, 4) * factor, rel=1e-9), r.key
        assert r.range.source == "assumed"


def test_the_template_prices_its_tanks_from_this_version():
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "project_templates"))
    import eh_ammonia as A

    assert A.AMMONIA_PACK_VERSION == NEW


def test_moving_latest_changes_no_template_and_no_guided_seed():
    """The other templates price from the pinned 2026-10-13 rows (unchanged in this version), the guided study
    from 2026-10-05: only the ammonia template reads the new version."""
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "project_templates"))
    import eh_templates as T
    from services.study import library as L

    assert T.TEMPLATE_PACK_VERSION == PREV
    assert L.PACK_VERSION == "2026-10-05"
