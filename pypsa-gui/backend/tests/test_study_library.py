"""
Assumptions library and ledger (guided investment study MVP-1, phase S2).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S2)
Spec: docs/superpowers/specs/2026-09-28-guided-investment-study-design.md §4.3

The library is vendored (technology-data v0.14.0 plus two illustrative
tariffs and the finance defaults); the runtime never downloads. The ledger is
seeded from it with every row `provenance=library, status=default`, a user
edit flips a row to `user / customised`, a re-seed refreshes library rows and
keeps user rows, and the maturity badge reads the key-driver rows and the
load's provenance.
"""
from __future__ import annotations

import csv
import dataclasses
import io
import math
import pathlib
from datetime import datetime, UTC

import pytest

from models.study import AssumptionsLedger, BaselineDefinition, DecisionQuestion
from services.study import ledger as L
from services.study import library as lib

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
REPO = pathlib.Path(__file__).resolve().parents[3]
SEED_NOTE = (REPO / "docs/superpowers/notes/"
             "2026-09-28-mvp1-s2-seed-rows-technology-data-v0.14.0.csv")
TD_URL = ("https://raw.githubusercontent.com/PyPSA/technology-data/refs/tags/"
          "v0.14.0/outputs/costs_2030.csv")
KEY_DRIVERS = ("battery_storage_eur_per_kwh", "battery_inverter_eur_per_kw",
               "demand_charge_price", "energy_price_level", "discount_rate")


@pytest.fixture(scope="module")
def library():
    return lib.load_library()


@pytest.fixture
def seeded(library):
    return lib.seed_ledger(KEY_DRIVERS, {}, library)


def _row(ledger: AssumptionsLedger, key: str):
    [row] = [r for r in ledger.rows if r.key == key]
    return row


def _customise_key_drivers(ledger, *, provenance="user"):
    for key in KEY_DRIVERS:
        row = _row(ledger, key)
        value = row.value if row.value is not None else 1.0
        ledger = L.apply_user_row(ledger, key, value * 1.1, unit=row.unit,
                                  changed_by="u1", changed_at=NOW,
                                  provenance=provenance)
    return ledger


# ── the library ──────────────────────────────────────────────────────────

def test_library_version_is_pinned(library):
    assert library.version == "technology-data v0.14.0"
    assert lib.LIBRARY_VERSION == library.version


def test_an_unknown_library_version_is_refused():
    with pytest.raises(lib.UnknownLibraryVersion, match="v0.13.4"):
        lib.load_library("technology-data v0.13.4")


def test_every_key_driver_row_carries_source_year_currency_year_and_range(seeded):
    for key in KEY_DRIVERS:
        row = _row(seeded, key)
        assert row.source, key
        assert isinstance(row.source_year, int), key
        assert isinstance(row.currency_year, int), key
        assert row.range is not None, key
        assert row.range.source == "assumed", key
        assert row.range.low <= row.value <= row.range.high, key


def test_the_technology_rows_are_the_v0_14_0_transcription(library):
    """
    Every technology-data row in the library equals the extracted seed row
    kept beside the plan (value, unit, currency year, source string), and
    names the pinned release.
    """
    note = {(r["technology"], r["parameter"]): r
            for r in csv.DictReader(SEED_NOTE.open(encoding="utf-8"))}
    sourced = [r for r in library.technology if r.source != "derived"
               and r.note != "not_used_in_mvp1"]
    assert {(r.technology, r.parameter) for r in sourced} == {
        ("battery inverter", "investment"), ("battery inverter", "FOM"),
        ("battery inverter", "efficiency"), ("battery inverter", "lifetime"),
        ("battery storage", "investment"), ("battery storage", "lifetime"),
        ("solar-utility", "investment"), ("solar-utility", "FOM"),
        ("solar-utility", "lifetime"), ("solar-rooftop", "investment"),
        ("solar-rooftop", "FOM"), ("solar-rooftop", "lifetime"),
    }
    for r in sourced:
        src = note[(r.technology, r.parameter)]
        assert r.value == float(src["value"]), (r.technology, r.parameter)
        assert r.unit == src["unit"]
        assert r.currency_year == int(float(src["currency_year"])) == 2020
        assert r.source == src["source"]
        assert r.source_year == 2026
        assert r.source_url == TD_URL
    by = {(r.technology, r.parameter): r.value for r in sourced}
    assert by[("battery inverter", "investment")] == 213.9279
    assert by[("battery storage", "investment")] == 189.861
    assert by[("battery inverter", "lifetime")] == 10.0
    assert by[("battery storage", "lifetime")] == 25.0


def test_ranges_are_an_assumed_thirty_percent_and_stay_physical(library):
    ranged = [r for r in library.technology if r.value is not None]
    assert ranged
    for r in ranged:
        assert r.range_source == "assumed", r.key
        cap = 1.0 if r.unit == "per unit" else math.inf
        assert r.range_low == pytest.approx(round(r.value * 0.7, 4)), r.key
        assert r.range_high == pytest.approx(round(min(r.value * 1.3, cap), 4)), r.key


def test_round_trip_efficiency_is_derived_from_the_inverter(library, seeded):
    [rte] = [r for r in library.technology if r.key == "battery_round_trip_efficiency"]
    assert rte.source == "derived"
    assert rte.value == pytest.approx(0.96 ** 2)
    assert _row(seeded, "battery_round_trip_efficiency").value == pytest.approx(0.9216)


def test_degradation_rows_are_placeholders_not_used_in_mvp1(library, seeded):
    rows = [r for r in library.technology if r.note == "not_used_in_mvp1"]
    assert rows and all(r.value is None and r.range_low is None for r in rows)
    for r in rows:
        led = _row(seeded, r.key)
        assert led.value is None
        assert led.unavailable == {"value": "not_used_in_mvp1"}
        assert led.sensitivity_flag is False


def test_the_seed_tariffs_load_as_tariff_models(library):
    assert set(library.tariffs) == {"de_industrial_illustrative",
                                    "tou_reference_illustrative"}
    assert library.default_tariff_id == "de_industrial_illustrative"
    for t in library.tariffs.values():
        assert t.source == "illustrative"
        assert t.currency == "EUR" and t.currency_year == 2020
        assert t.export.price_per_mwh is not None and t.export.series_ref is None
        assert t.honesty_notes
    de = library.tariffs["de_industrial_illustrative"]
    assert de.billing_period == "month"
    assert de.demand_charge is not None
    assert de.demand_charge.basis == "billing_period_peak"
    assert de.demand_charge.price_per_mw_per_period > 0
    assert de.network_charges and de.energy_bands
    assert library.tariffs["tou_reference_illustrative"].demand_charge is None


@pytest.mark.parametrize("tariff_id", ["de_industrial_illustrative",
                                       "tou_reference_illustrative"])
def test_the_bands_partition_the_week_and_export_stays_below_import(
        library, tariff_id):
    """
    Every (month, weekday, hour) matches exactly one band, so first-match
    and partition readings agree; the export price is below the import price
    in every hour (S4 acceptance: no import-to-export cycling pays).
    """
    t = library.tariffs[tariff_id]
    energy_network = sum(c.price for c in t.network_charges
                         if c.basis == "per_mwh")

    def hits(rule, m, d, h):
        return ((not rule.months or m in rule.months)
                and (not rule.weekdays or d in rule.weekdays)
                and (not rule.hours or h in rule.hours))

    for m in range(1, 13):
        for d in range(7):
            for h in range(24):
                bands = [b for b in t.energy_bands if hits(b.applies, m, d, h)]
                assert len(bands) == 1, (tariff_id, m, d, h, bands)
                assert t.export.price_per_mwh < bands[0].price_per_mwh + energy_network


def test_finance_defaults_state_their_basis_and_rules(library):
    f = library.finance
    assert f["discount_rate"]["value"] == 0.07
    assert f["discount_rate"]["basis"] == "real"
    assert f["discount_rate"]["source"] and f["discount_rate"]["source_year"]
    assert f["basis"] == {"terms": "real", "tax": "pre", "subsidy": "excl"}
    assert f["perspective"] == "site_owner"
    assert f["currency_year"] == 2020
    assert f["horizon_rule"]["horizon_years"] == "battery_storage_lifetime_years"


def test_horizon_is_the_storage_lifetime_and_the_inverter_is_replaced_inside_it(
        library, seeded):
    assert lib.horizon_and_replacements(seeded, library) == (25, (10, 20))
    longer = L.apply_user_row(seeded, "battery_inverter_lifetime_years", 12.0,
                              unit="years", changed_by="u1", changed_at=NOW)
    assert lib.horizon_and_replacements(longer, library) == (25, (12, 24))


# ── seeding ──────────────────────────────────────────────────────────────

def test_a_seeded_ledger_is_all_library_defaults(seeded):
    assert seeded.ledger_version == lib.LIBRARY_VERSION
    assert seeded.rows
    for row in seeded.rows:
        assert row.provenance == "library", row.key
        assert row.status == "default", row.key
        assert row.changed_by is None and row.changed_at is None, row.key
        assert row.source, row.key


def test_sensitivity_flags_are_exactly_the_key_drivers(seeded):
    flagged = {r.key for r in seeded.rows if r.sensitivity_flag}
    assert flagged == set(KEY_DRIVERS)
    assert {r.key for r in L.key_driver_rows(seeded)} == set(KEY_DRIVERS)


def test_seed_accepts_a_question_and_reads_its_key_drivers(library):
    q = DecisionQuestion(question_id="bess_site", title="BESS",
                         baseline_definition=BaselineDefinition(text="grid"),
                         key_drivers=["discount_rate"])
    led = lib.seed_ledger(q, {}, library)
    assert {r.key for r in led.rows if r.sensitivity_flag} == {"discount_rate"}


def test_the_tariff_rows_follow_the_intake_tariff(library):
    tou = lib.seed_ledger(KEY_DRIVERS, {"tariff": {"tariff_id": "tou_reference_illustrative"}},
                          library)
    dc = _row(tou, "demand_charge_price")
    assert dc.value is None
    assert dc.unavailable == {"value": "not_applicable"}
    assert _row(tou, "energy_price_level").value == 1.0
    de = lib.seed_ledger(KEY_DRIVERS, {}, library)
    assert _row(de, "demand_charge_price").value == (
        library.tariffs["de_industrial_illustrative"]
        .demand_charge.price_per_mw_per_period)
    assert _row(de, "demand_charge_price").unit == "EUR/MW/month"


def test_an_unknown_intake_tariff_is_refused(library):
    with pytest.raises(lib.LibraryError, match="no_such_tariff"):
        lib.seed_ledger(KEY_DRIVERS, {"tariff": {"tariff_id": "no_such_tariff"}}, library)


# ── user edits and re-seed ───────────────────────────────────────────────

def test_a_user_edit_flips_provenance_and_status(seeded):
    edited = L.apply_user_row(seeded, "battery_storage_eur_per_kwh", 150.0,
                              unit="EUR/kWh", changed_by="u1", changed_at=NOW,
                              source="Vendor quote 2026-09")
    row = _row(edited, "battery_storage_eur_per_kwh")
    assert (row.value, row.provenance, row.status) == (150.0, "user", "customised")
    assert (row.changed_by, row.changed_at) == ("u1", NOW)
    assert row.source == "Vendor quote 2026-09"
    assert row.sensitivity_flag is True
    # Pure: the input ledger is untouched.
    assert _row(seeded, "battery_storage_eur_per_kwh").provenance == "library"


@pytest.mark.parametrize("key,value,unit,match", [
    ("no_such_key", 1.0, "EUR/kWh", "no_such_key"),
    ("battery_storage_eur_per_kwh", 150.0, "EUR/MWh", "battery_storage_eur_per_kwh"),
    ("battery_storage_eur_per_kwh", 150.0, "", "battery_storage_eur_per_kwh"),
    ("battery_storage_eur_per_kwh", float("nan"), "EUR/kWh", "finite"),
])
def test_an_edit_is_refused_with_the_row_named(seeded, key, value, unit, match):
    with pytest.raises(L.LedgerEditError, match=match):
        L.apply_user_row(seeded, key, value, unit=unit, changed_by="u1",
                         changed_at=NOW)


def test_a_derived_default_row_follows_its_input(seeded):
    edited = L.apply_user_row(seeded, "battery_inverter_efficiency", 0.95,
                              unit="per unit", changed_by="u1", changed_at=NOW)
    rte = _row(edited, "battery_round_trip_efficiency")
    assert rte.value == pytest.approx(0.9025)
    assert (rte.provenance, rte.status, rte.source) == ("library", "default", "derived")


def test_a_user_edit_survives_a_reseed(library, seeded):
    """
    Re-seed = seed afresh from the library and the current intake, then put
    every non-library row back. A library row takes the library's (possibly
    new) value; a user row keeps the user's.
    """
    edited = L.apply_user_row(seeded, "battery_storage_eur_per_kwh", 150.0,
                              unit="EUR/kWh", changed_by="u1", changed_at=NOW)
    newer = dataclasses.replace(library, technology=tuple(
        dataclasses.replace(r, value=r.value * 2)
        if r.key in ("battery_storage_eur_per_kwh", "battery_inverter_eur_per_kw")
        else r for r in library.technology))
    again = L.reseed_ledger(edited, KEY_DRIVERS, {}, newer)
    kept = _row(again, "battery_storage_eur_per_kwh")
    assert (kept.value, kept.provenance, kept.status) == (150.0, "user", "customised")
    assert kept.changed_at == NOW
    refreshed = _row(again, "battery_inverter_eur_per_kw")
    assert refreshed.value == pytest.approx(213.9279 * 2)
    assert refreshed.provenance == "library"
    assert [r.key for r in again.rows] == [r.key for r in seeded.rows]


def test_diff_against_defaults_names_only_the_edited_rows(library, seeded):
    edited = L.apply_user_row(seeded, "discount_rate", 0.05, unit="per unit",
                              changed_by="u1", changed_at=NOW)
    diff = L.diff_against_defaults(edited, lib.seed_ledger(KEY_DRIVERS, {}, library))
    assert [d["key"] for d in diff] == ["discount_rate"]
    assert diff[0]["default_value"] == 0.07 and diff[0]["value"] == 0.05
    assert diff[0]["provenance"] == "user"


# ── maturity ─────────────────────────────────────────────────────────────

def test_a_seeded_ledger_is_screening_and_names_every_row_holding_it(seeded):
    m = L.maturity_from_ledger(seeded, L.load_provenance({}))
    assert m.status == "ok" and m.class_ == "screening"
    assert (m.accuracy_band.low_pct, m.accuracy_band.high_pct) == (-50.0, 100.0)
    assert (m.accuracy_band.low_pct_narrow, m.accuracy_band.high_pct_narrow) == (-20.0, 30.0)
    assert "18R-97" in m.accuracy_band.reference
    for key in KEY_DRIVERS:
        assert any(r.startswith(f"{key}:") for r in m.reasons), (key, m.reasons)
    assert any(r.startswith("load:") for r in m.reasons), m.reasons


def test_maturity_moves_to_feasibility_when_key_rows_are_customised_and_load_uploaded(seeded):
    led = _customise_key_drivers(seeded)
    m = L.maturity_from_ledger(led, L.load_provenance({"load": {"source": "upload"}}))
    assert m.class_ == "feasibility", m.reasons
    assert (m.accuracy_band.low_pct, m.accuracy_band.high_pct) == (-30.0, 50.0)
    assert (m.accuracy_band.low_pct_narrow, m.accuracy_band.high_pct_narrow) == (-15.0, 20.0)
    assert m.reasons == []


def test_a_synthetic_load_keeps_a_customised_ledger_at_screening(seeded):
    led = _customise_key_drivers(seeded)
    for intake in ({"load": {"source": "sector_profile"}}, {}):
        m = L.maturity_from_ledger(led, L.load_provenance(intake))
        assert m.class_ == "screening", intake
        assert len(m.reasons) == 1 and m.reasons[0].startswith("load:"), m.reasons


def test_one_default_key_row_keeps_it_at_screening_and_is_named(seeded):
    led = _customise_key_drivers(seeded)
    led = L.reseed_ledger(led, KEY_DRIVERS, {}, lib.load_library())  # no-op for user rows
    led = led.model_copy(update={"rows": [
        r.model_copy(update={"provenance": "library", "status": "default"})
        if r.key == "discount_rate" else r for r in led.rows]})
    m = L.maturity_from_ledger(led, "uploaded")
    assert m.class_ == "screening"
    assert m.reasons == ["discount_rate: default (library)"]


def test_measured_rows_count_as_established(seeded):
    led = _customise_key_drivers(seeded, provenance="measured")
    assert {_row(led, k).provenance for k in KEY_DRIVERS} == {"measured"}
    assert L.maturity_from_ledger(led, "uploaded").class_ == "feasibility"


def test_a_needs_attention_key_row_keeps_it_at_screening(seeded):
    led = _customise_key_drivers(seeded)
    led = led.model_copy(update={"rows": [
        r.model_copy(update={"status": "needs_attention"})
        if r.key == "energy_price_level" else r for r in led.rows]})
    m = L.maturity_from_ledger(led, "uploaded")
    assert m.class_ == "screening"
    assert m.reasons == ["energy_price_level: needs_attention (user)"]


def test_a_ledger_without_key_drivers_is_never_feasibility(library):
    led = lib.seed_ledger((), {}, library)
    m = L.maturity_from_ledger(led, "uploaded")
    assert m.class_ == "screening"
    assert m.reasons and "key driver" in m.reasons[0]


# ── CSV export (import is MVP-2; the reader is internal) ─────────────────

def test_csv_export_round_trips(seeded):
    led = L.apply_user_row(seeded, "battery_storage_eur_per_kwh", 150.0,
                           unit="EUR/kWh", changed_by="u1", changed_at=NOW,
                           source="Vendor quote, 2026")
    text = L.ledger_to_csv(led)
    header = next(csv.reader(io.StringIO(text)))
    assert header == list(L.CSV_COLUMNS)
    assert len(list(csv.DictReader(io.StringIO(text)))) == len(led.rows)
    back = L.ledger_rows_from_csv(text)
    assert back == led.rows


def test_csv_export_neutralises_formula_cells(seeded):
    evil = '=HYPERLINK("http://x","click")'
    led = L.apply_user_row(seeded, "discount_rate", 0.05, unit="per unit",
                           changed_by="@admin", changed_at=NOW, source=evil)
    text = L.ledger_to_csv(led)
    rows = {r["key"]: r for r in csv.DictReader(io.StringIO(text))}
    assert rows["discount_rate"]["source"] == "'" + evil
    assert rows["discount_rate"]["changed_by"] == "'@admin"
    # Numbers are written as numbers (a negative is not a formula).
    assert rows["discount_rate"]["value"] == "0.05"
    back = {r.key: r for r in L.ledger_rows_from_csv(text)}
    assert back["discount_rate"].source == evil
    assert back["discount_rate"].changed_by == "@admin"


def test_an_import_row_without_a_unit_is_refused_with_the_row_named(seeded):
    text = L.ledger_to_csv(seeded)
    rows = list(csv.DictReader(io.StringIO(text)))
    rows[2]["unit"] = ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(L.CSV_COLUMNS), lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    with pytest.raises(L.LedgerImportError) as exc:
        L.ledger_rows_from_csv(buf.getvalue())
    assert rows[2]["key"] in str(exc.value)
    assert "line 4" in str(exc.value)
    assert "unit" in str(exc.value)
