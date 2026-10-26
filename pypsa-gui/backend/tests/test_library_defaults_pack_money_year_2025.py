"""
Money-year issue 01 (owner decisions MONEY-YEAR-1, MONEY-YR-SEED-1): defaults pack version 2026-10-26 = 2026-10-25
with the two illustrative seed tariffs converted from 2020 to 2025 EUR by the Eurostat HICP (EEA) factor, and the
finance defaults' currency year 2025; the regional overlays 2026-10-26+<region> carry the 2026-10-22 rows on that
base. The templates, redundancy and the guided study read it; every older version and what was pinned to it stays.
"""
from __future__ import annotations

import csv
import json
import math
import pathlib
from decimal import Decimal

import pytest

NEW, PREV = "2026-10-26", "2026-10-25"
REGIONS = ("ca_federal", "uk", "us_federal")
SEEDS = ("de_industrial_illustrative", "tou_reference_illustrative")


def _dir(version: str):
    from services.library.defaults_pack.loader import VERSIONS_DIR

    return VERSIONS_DIR / version


def _json(version: str, name: str) -> dict:
    return json.loads((_dir(version) / name).read_text(encoding="utf-8"))


def _factor() -> Decimal:
    return Decimal(_json(NEW, "manifest.json")["money_year_conversion"]["factor"])


def _fixed_pins() -> dict:
    from tests.test_library_defaults_pack import FIXTURES

    return json.loads((FIXTURES / "pack_hashes.json").read_text())["generic_defaults"]


def test_the_stored_factor_is_the_product_of_the_five_stored_hicp_rates():
    conv = _json(NEW, "manifest.json")["money_year_conversion"]
    assert sorted(conv["rates_percent"]) == ["2021", "2022", "2023", "2024", "2025"]
    product = Decimal(1)
    for pct in conv["rates_percent"].values():
        assert isinstance(pct, str)
        product *= 1 + Decimal(pct) / 100
    assert isinstance(conv["factor"], str) and product == Decimal(conv["factor"])
    assert conv["factor"] == "1.2573346011408"
    assert "prc_hicp_aind" in conv["index"] and "EEA" in conv["index"]
    assert conv["from"] == PREV and sorted(conv["converted"]) == list(SEEDS)


def test_every_tariff_money_figure_is_the_previous_one_times_the_factor_unrounded():
    f = _factor()
    old, new = _json(PREV, "tariffs.json")["tariffs"], _json(NEW, "tariffs.json")["tariffs"]
    assert [t["tariff"]["id"] for t in new] == [t["tariff"]["id"] for t in old] == list(SEEDS)

    def conv(x):
        return float(Decimal(repr(x)) * f)

    seen = 0
    for o, n in zip(old, new):
        ot, nt, om, nm = o["tariff"], n["tariff"], o["meta"], n["meta"]
        assert (om["currency_year"], ot["valid_from"]) == (2020, "2020-01-01")
        assert (nm["currency_year"], nt["valid_from"]) == (2025, "2025-01-01")
        for oi, ni in zip(ot["items"], nt["items"], strict=True):
            for op, np_ in zip(oi["periods"], ni["periods"], strict=True):
                assert np_["rate"] == conv(op["rate"]), (nt["id"], ni["id"], np_["name"])
                assert {**np_, "rate": 0} == {**op, "rate": 0}
                seen += 1
        for key in om["items"]:
            for orow, nrow in zip(om["items"][key], nm["items"][key], strict=True):
                assert nrow["original_price"] == conv(orow["original_price"])
        assert nm["export"]["price_per_mwh"] == conv(om["export"]["price_per_mwh"])
        assert "1.2573346011408" in nm["source"] and "prc_hicp_aind" in nm["source"]
    assert seen == 9
    # Not rounded: the 2020 energy rate 0.11 EUR/kWh is 0.138306806125488 in 2025 EUR.
    assert new[0]["tariff"]["items"][0]["periods"][0]["rate"] == 0.138306806125488


def test_only_the_tariffs_and_the_finance_currency_year_change():
    from services.library.defaults_pack.loader import load_defaults_pack

    assert (_dir(NEW) / "values.csv").read_bytes() == (_dir(PREV) / "values.csv").read_bytes()
    new, prev = load_defaults_pack(NEW), load_defaults_pack(PREV)
    assert (prev.finance.currency_year, new.finance.currency_year) == (2020, 2025)
    assert "1.2573346011408" in new.finance.currency_year_source
    skip = {"currency_year", "currency_year_source"}
    assert new.finance.model_dump(exclude=skip) == prev.finance.model_dump(exclude=skip)
    assert new.load_profile_ids() == prev.load_profile_ids()
    for pid in new.load_profile_ids():
        assert new.load_profile(pid) == prev.load_profile(pid)
    assert new.hash == _fixed_pins()[NEW] != prev.hash


@pytest.mark.parametrize("region", REGIONS)
def test_the_regional_overlay_keeps_its_rows_on_the_new_base(region):
    from services.library.defaults_pack.loader import load_defaults_pack

    new, old = _dir(f"{NEW}+{region}"), _dir(f"2026-10-22+{region}")
    for name in ("values.csv", "source_cells.csv"):
        assert (new / name).read_bytes() == (old / name).read_bytes()
    nm, om = _json(f"{NEW}+{region}", "manifest.json"), _json(f"2026-10-22+{region}", "manifest.json")
    moved = {"version", "base_version", "base_hash"}
    assert {k: v for k, v in nm.items() if k not in moved} == {
        k: v for k, v in om.items() if k not in moved}
    assert (nm["version"], nm["base_version"]) == (NEW, NEW)
    assert nm["base_hash"] == load_defaults_pack(NEW).hash
    pack = load_defaults_pack(NEW, region=region)
    assert pack.region_info.base_version == NEW and pack.currency != "EUR" \
        if hasattr(pack, "currency") else pack.region_info.base_version == NEW


def test_every_pin_moved_and_the_co2_path_keeps_2020():
    import sys

    from services.adequacy import redundancy
    from services.study import library
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "project_templates"))
    import eh_ammonia
    import eh_ipp
    import eh_site
    import eh_staged_hubs
    import eh_templates

    assert {eh_templates.TEMPLATE_PACK_VERSION, eh_ammonia.AMMONIA_PACK_VERSION, eh_ipp.PACK_VERSION,
            redundancy.REDUNDANCY_PACK_VERSION, library.PACK_VERSION} == {NEW}
    assert redundancy.REDUNDANCY_COST_BASIS == f"defaults_pack:{NEW}"
    assert eh_staged_hubs.CO2_PRICE["money_year"] == 2020      # its own source year; inflated by the case
    assert eh_ipp.YEAR == 2030                                 # the IPP's case year is E1d's, untouched
    del eh_site


# ── what was pinned to an older version keeps its values, hash and year ───

@pytest.mark.parametrize("version", ["2026-10-05", "2026-10-22", "2026-10-25"])
def test_an_older_version_keeps_its_hash_and_its_2020_year(version):
    from services.library.defaults_pack.loader import load_defaults_pack

    pack = load_defaults_pack(version)
    assert pack.hash == _fixed_pins()[version]
    assert pack.finance.currency_year == 2020
    assert {pack.tariff_meta(t).currency_year for t in pack.tariff_ids()} == {2020}


def test_a_project_priced_on_2026_10_22_reloads_with_its_cost_hash_and_year():
    import pypsa

    from models.finance import FinanceInputs
    from services.library.defaults_pack.loader import load_defaults_pack
    from services.library.defaults_pack.pricing import price_from_pack

    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "w", bus="b", carrier="onwind")
    old = price_from_pack(n, "generators", "w", "onwind", version="2026-10-22")
    assert old["money_year"] == 2020 and old["overnight_cost"] == pytest.approx(1383305.9)
    assert load_defaults_pack("2026-10-22").hash[:16] in json.dumps(old, default=str)
    new = price_from_pack(n, "generators", "w", "onwind", version=NEW)
    assert new["money_year"] == 2025 and new["overnight_cost"] == old["overnight_cost"]
    # A saved project's finance block keeps the case year it was saved with.
    saved = {"financial_close": "2026-01-01", "currency": "EUR", "currency_year": 2020,
             "price_basis": "nominal"}
    assert FinanceInputs.model_validate(saved).currency_year == 2020


def test_a_study_pinned_to_2026_10_05_reloads_with_its_hash_and_2020():
    from services.library.defaults_pack.loader import load_defaults_pack
    from services.study import compile as C
    from services.study import library as L
    from services.study import questions as Q
    from tests.golden import site_fixture as sf

    lib = L.load_defaults("2026-10-05")
    assert load_defaults_pack("2026-10-05").hash[:12] in lib.version
    ledger = L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), lib)
    assert load_defaults_pack("2026-10-05").hash[:12] in ledger.ledger_version
    tariff = load_defaults_pack("2026-10-05").tariff_meta("de_industrial_illustrative")
    assert C.currency_year_of(ledger, {"currency": "EUR", "currency_year": tariff.currency_year}) == 2020
    assert sf.site_library().version == lib.version                # the frozen WP0 parity's pack


def test_currency_year_mixed_still_refuses_a_2020_tariff_against_2025_rows():
    from services.study import compile as C
    from services.study import library as L
    from services.study import questions as Q
    from tests.golden import site_fixture as sf

    lib = L.load_defaults()
    ledger = L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), lib)
    assert C.currency_year_of(ledger, {"currency": "EUR", "currency_year": 2025}) == 2025
    with pytest.raises(C.CompileError) as err:
        C.currency_year_of(ledger, {"currency": "EUR", "currency_year": 2020, "name": "old seed"})
    assert err.value.code == "currency_year_mixed" if hasattr(err.value, "code") else \
        "currency_year_mixed" in str(err.value)


# ── the guided study on the live pin ──────────────────────────────────────

def _bill(lib, tid: str, n):
    import pandas as pd

    from services.study import compile as C
    from services.study import engine_adapter as A
    from services.study import library as L
    from services.study import questions as Q
    from tests.golden import site_fixture as sf

    intake = {**sf.site_intake(), "tariff": {"tariff_id": tid}}
    ledger = L.seed_ledger(Q.BESS_AT_SITE, intake, lib)
    c = C.commercial_from_ledger(intake, ledger, lib, n.snapshots, export_series=sf.EXPORT_REF)
    price = float(c.tariff_meta["export_price_per_mwh"])
    series = pd.Series(price, index=pd.DatetimeIndex(n.snapshots), name="price")
    c = C.bind_on_network(n, c, resolve_ref=lambda _ref, s=series: s)
    comps = {k: getattr(A.bill(n, c).by_component, k) for k in A.BILL_COMPONENT_KEYS}
    return ledger, c, comps


@pytest.mark.live_solve
@pytest.mark.parametrize("tid", SEEDS)
def test_on_a_fixed_dispatch_every_seed_bill_component_rises_by_the_factor(tid):
    from services.study import library as L
    from tests.golden import site_fixture as sf
    from tests.u2_targets import golden_dispatch

    f = float(_factor())
    for option in sf.SITE_OPTIONS:
        _l0, _c0, old = _bill(sf.site_library(), tid, golden_dispatch(option))
        _l1, _c1, new = _bill(L.load_defaults(), tid, golden_dispatch(option))
        assert set(old) == set(new)
        for k in old:
            if old[k] is None:
                assert new[k] is None, (option, k)
            else:
                assert math.isclose(new[k], old[k] * f, rel_tol=1e-9, abs_tol=1e-9), (option, k)


@pytest.mark.live_solve
def test_the_guided_study_on_the_live_pin_solves_in_2025_money_end_to_end():
    import queue
    import threading

    import pandas as pd

    from services.library.defaults_pack.loader import load_defaults_pack
    from services.solver_service import run_simulation
    from services.study import compile as C
    from services.study import engine_adapter as A
    from services.study import library as L
    from services.study import packs
    from services.study import questions as Q
    from tests.golden import site_fixture as sf

    lib = L.load_defaults()
    assert L.PACK_VERSION == NEW and load_defaults_pack(NEW).hash[:12] in lib.version
    intake = sf.site_intake()
    ledger = L.seed_ledger(Q.BESS_AT_SITE, intake, lib)
    assert load_defaults_pack(NEW).hash[:12] in ledger.ledger_version
    n = packs.build_site_network(intake, ledger, "none", library=lib)
    c = C.commercial_from_ledger(intake, ledger, lib, n.snapshots, export_series=sf.EXPORT_REF)
    assert c.tariff_meta["currency_year"] == 2025
    assert C.currency_year_of(ledger, c.tariff_meta) == 2025
    price = float(c.tariff_meta["export_price_per_mwh"])
    series = pd.Series(price, index=pd.DatetimeIndex(n.snapshots), name="price")
    c = C.with_value_flows(C.bind_on_network(n, c, resolve_ref=lambda _ref: series), n)
    cfg = C.solver_config(ledger, c)
    status = run_simulation(cfg, n, threading.RLock(), threading.Event(), queue.SimpleQueue())
    assert status == ("ok", "optimal"), status
    bill = A.bill(n, c).by_component
    # Without a battery the dispatch is the load: the bill is the pinned one x the factor.
    _l, _c, old = _bill(sf.site_library(), "de_industrial_illustrative", n)
    for k in A.BILL_COMPONENT_KEYS:
        if old[k] is not None:
            assert math.isclose(getattr(bill, k), old[k] * float(_factor()), rel_tol=1e-9, abs_tol=1e-9), k


# ── the rows the guided ledger names as not seeded (GK B riders R2-R4) ───

def test_every_row_named_as_not_seeded_is_in_the_pinned_pack():
    from services.library.defaults_pack.loader import load_defaults_pack
    from services.study import library as L

    keys = {v.key for v in load_defaults_pack(L.PACK_VERSION).cost_values}
    assert sorted(set(L._PACK_ROWS_NOT_SEEDED) - keys) == []


def test_no_row_named_as_not_seeded_is_a_technology_the_ledger_reads():
    from services.study import library as L
    from services.study._pack_rows_not_seeded import PACK_ROWS_NOT_SEEDED_SINCE_2026_10_05 as LATER

    read = {k.split(".")[0] for k, _ in L._PACK_TECH_ROWS}
    assert read == {"battery", "solar-utility", "solar-rooftop"}
    assert [k for k in LATER if k.split(".")[0] in read] == []


def test_the_2026_10_05_study_keeps_its_ledger_hash():
    from services.study import library as L
    from services.study import packs as P
    from services.study import questions as Q
    from tests.golden import site_fixture as sf

    lib = L.load_defaults("2026-10-05")
    assert lib.version == "generic-defaults 2026-10-05 962a0184861c"
    # recorded on master 1d80850, before the not-seeded rows were named
    assert P.ledger_hash(L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), lib)) == "974275b95d58a7c2"


# ── a stored ledger reads the pack it was seeded from (GK B, condition 3) ─

def test_a_stored_ledger_reads_the_defaults_it_was_seeded_from():
    from services.study import library as L
    from services.study import packs as P
    from services.study import questions as Q
    from tests.golden import site_fixture as sf

    for version in ("2026-10-05", PREV, NEW):
        lib = L.load_defaults(version)
        ledger = L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), lib)
        assert L.library_for_ledger(ledger).version == lib.version
    assert L.library_for_ledger(None).version == L.load_defaults().version
    legacy = ledger.model_copy(update={"ledger_version": L.LIBRARY_VERSION})
    assert L.library_for_ledger(legacy).version == L.load_defaults(L.WP0_PACK_VERSION).version
    assert int(L.library_for_ledger(legacy).finance["currency_year"]) == 2020
    for bad in ("generic-defaults 2026-10-05 000000000000", "generic-defaults 2026-10-05", "nonsense"):
        with pytest.raises(L.LibraryError):
            L.library_for_ledger(ledger.model_copy(update={"ledger_version": bad}))
        with pytest.raises(P.PackError) as err:
            P.library_for(ledger.model_copy(update={"ledger_version": bad}))
        assert err.value.code == "ledger_defaults_unknown"
