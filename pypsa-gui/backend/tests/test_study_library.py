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


# A tariff the user supplied (their own bill), as the intake carries it. The
# illustrative library tariffs never establish a study beyond screening
# (gate S2, BC-S2-1); this one can.
USER_TARIFF = {
    "tariff_id": "site_a_contract", "name": "Site A supply contract",
    "source": "Site A electricity bill, August 2026", "source_year": 2026,
    "currency": "EUR", "currency_year": 2020, "billing_period": "month",
    "energy_bands": [{"label": "all hours", "price_per_mwh": 125.0}],
    "demand_charge": {"price_per_mw_per_period": 7000.0},
    "export": {"price_per_mwh": 35.0},
}
USER_INTAKE = {"tariff": {"custom": USER_TARIFF}}


@pytest.fixture
def seeded(library):
    return lib.seed_ledger(KEY_DRIVERS, {}, library)


@pytest.fixture
def seeded_user(library):
    """Seeded on a tariff the user supplied."""
    return lib.seed_ledger(KEY_DRIVERS, USER_INTAKE, library)


def _row(ledger: AssumptionsLedger, key: str):
    [row] = [r for r in ledger.rows if r.key == key]
    return row


def _customise_key_drivers(ledger, *, provenance="user"):
    for key in KEY_DRIVERS:
        row = _row(ledger, key)
        if row.value is None:  # not applicable under this tariff: not editable
            continue
        value = row.value
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
        if row.value is None:
            # Gate S4 [S5]: energy_price_level on the single-band seed is not
            # applicable (null + flag), like a demand charge a tariff lacks.
            assert row.unavailable == {"value": "not_applicable"}, key
            continue
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
        if _row(seeded, key).value is None:  # not applicable: never asked for
            assert not any(r.startswith(f"{key}:") for r in m.reasons), key
            continue
        assert any(r.startswith(f"{key}:") for r in m.reasons), (key, m.reasons)
    assert any(r.startswith("load:") for r in m.reasons), m.reasons


def test_maturity_moves_to_feasibility_when_key_rows_are_customised_and_load_uploaded(
        seeded_user):
    led = _customise_key_drivers(seeded_user)
    m = L.maturity_from_ledger(led, L.load_provenance({"load": {"source": "upload"}}))
    assert m.class_ == "feasibility", m.reasons
    assert (m.accuracy_band.low_pct, m.accuracy_band.high_pct) == (-30.0, 50.0)
    assert (m.accuracy_band.low_pct_narrow, m.accuracy_band.high_pct_narrow) == (-15.0, 20.0)
    assert m.reasons == []


def test_a_synthetic_load_keeps_a_customised_ledger_at_screening(seeded_user):
    led = _customise_key_drivers(seeded_user)
    for intake in ({"load": {"source": "sector_profile"}}, {}):
        m = L.maturity_from_ledger(led, L.load_provenance(intake))
        assert m.class_ == "screening", intake
        assert len(m.reasons) == 1 and m.reasons[0].startswith("load:"), m.reasons


def test_one_default_key_row_keeps_it_at_screening_and_is_named(seeded_user):
    led = _customise_key_drivers(seeded_user)
    led = L.reseed_ledger(led, KEY_DRIVERS, USER_INTAKE, lib.load_library())  # no-op for user rows
    led = led.model_copy(update={"rows": [
        r.model_copy(update={"provenance": "library", "status": "default"})
        if r.key == "discount_rate" else r for r in led.rows]})
    m = L.maturity_from_ledger(led, "uploaded")
    assert m.class_ == "screening"
    assert m.reasons == ["discount_rate: default (library)"]


def test_measured_rows_count_as_established(seeded_user):
    """
    A measured row counts even at status `default` (gate S2 N8: the earlier
    version also set `customised`, so it passed without the measured clause).
    """
    led = _customise_key_drivers(seeded_user, provenance="measured")
    led = led.model_copy(update={"rows": [
        r.model_copy(update={"status": "default"}) if r.key in KEY_DRIVERS else r
        for r in led.rows]})
    applicable = [k for k in KEY_DRIVERS if _row(led, k).value is not None]
    assert {_row(led, k).provenance for k in applicable} == {"measured"}
    assert {_row(led, k).status for k in KEY_DRIVERS} == {"default"}
    assert L.maturity_from_ledger(led, "uploaded").class_ == "feasibility"


def test_a_needs_attention_key_row_keeps_it_at_screening(seeded_user):
    led = _customise_key_drivers(seeded_user)
    led = led.model_copy(update={"rows": [
        r.model_copy(update={"status": "needs_attention"})
        if r.key == "discount_rate" else r for r in led.rows]})
    m = L.maturity_from_ledger(led, "uploaded")
    assert m.class_ == "screening"
    assert m.reasons == ["discount_rate: needs_attention (user)"]


def test_a_ledger_without_key_drivers_is_never_feasibility(library):
    led = lib.seed_ledger((), {}, library)
    m = L.maturity_from_ledger(led, "uploaded")
    assert m.class_ == "screening"
    assert m.reasons and "key driver" in m.reasons[0]


# ── gate S2 binding conditions ───────────────────────────────────────────

# BC-S2-1: an illustrative tariff holds the badge at screening.

@pytest.mark.parametrize("tariff_id", ["de_industrial_illustrative",
                                       "tou_reference_illustrative"])
def test_an_illustrative_tariff_holds_the_badge_at_screening(library, tariff_id):
    """
    Every key driver re-entered (so `customised`) and the load uploaded: the
    library's illustrative tariff still keeps the study at screening, and the
    tariff row is the one reason.
    """
    led = lib.seed_ledger(KEY_DRIVERS, {"tariff": {"tariff_id": tariff_id}}, library)
    led = _customise_key_drivers(led)
    m = L.maturity_from_ledger(led, "uploaded")
    assert m.class_ == "screening"
    assert m.reasons == [f"tariff: {tariff_id} (illustrative, library)"]


def test_the_tariff_row_records_where_the_tariff_came_from(library, seeded, seeded_user):
    lib_row, user_row = _row(seeded, "tariff"), _row(seeded_user, "tariff")
    assert (lib_row.provenance, lib_row.source) == ("library", "illustrative")
    assert lib_row.technical_name == "de_industrial_illustrative"
    assert user_row.provenance == "user"
    assert user_row.source == USER_TARIFF["source"]
    imported = lib.seed_ledger(
        KEY_DRIVERS, {"tariff": {"custom": USER_TARIFF, "provenance": "imported"}}, library)
    assert _row(imported, "tariff").provenance == "imported"
    # The prices a user tariff carries are the user's, not library defaults.
    dc = _row(seeded_user, "demand_charge_price")
    assert (dc.value, dc.provenance, dc.status) == (7000.0, "user", "customised")


def test_the_tariff_row_cannot_be_typed_over(seeded):
    """Re-entering an illustrative tariff by hand does not make it the user's."""
    with pytest.raises(L.LedgerEditError, match="tariff"):
        L.apply_user_row(seeded, "tariff", 1.0, unit="tariff",
                         changed_by="u1", changed_at=NOW)


def test_a_user_tariff_reaches_feasibility(seeded_user):
    m = L.maturity_from_ledger(_customise_key_drivers(seeded_user), "uploaded")
    assert m.class_ == "feasibility", m.reasons


def test_a_not_applicable_demand_charge_does_not_hold_the_badge(library):
    """
    Gate S2 [S7]: a user tariff with no demand charge leaves
    `demand_charge_price` null and flagged `not_applicable`; that row is not a
    default the user could customise, so it must not hold the badge.
    """
    no_dc = {**USER_TARIFF, "demand_charge": None}
    led = lib.seed_ledger(KEY_DRIVERS, {"tariff": {"custom": no_dc}}, library)
    assert _row(led, "demand_charge_price").unavailable == {"value": "not_applicable"}
    m = L.maturity_from_ledger(_customise_key_drivers(led), "uploaded")
    assert m.class_ == "feasibility", m.reasons


# BC-S2-2: edits to unused or inapplicable rows are refused; re-seed flags.

def test_an_edit_to_a_row_not_used_in_mvp1_is_refused(seeded):
    key = "battery_storage_degradation_calendar_pct_per_year"
    with pytest.raises(L.LedgerEditError, match=f"{key}.*not_used_in_mvp1"):
        L.apply_user_row(seeded, key, 2.0, unit="%/year", changed_by="u1",
                         changed_at=NOW)


def test_a_demand_charge_edit_on_a_tariff_without_one_is_refused(library):
    led = lib.seed_ledger(KEY_DRIVERS, {"tariff": {"tariff_id": "tou_reference_illustrative"}},
                          library)
    with pytest.raises(L.LedgerEditError,
                       match="demand_charge_price.*no demand charge"):
        L.apply_user_row(led, "demand_charge_price", 5000.0, unit="EUR/MW/month",
                         changed_by="u1", changed_at=NOW)


def test_reseed_flags_a_user_row_the_new_tariff_makes_inapplicable(library, seeded):
    led = L.apply_user_row(seeded, "demand_charge_price", 8000.0, unit="EUR/MW/month",
                           changed_by="u1", changed_at=NOW)
    again = L.reseed_ledger(led, KEY_DRIVERS,
                            {"tariff": {"tariff_id": "tou_reference_illustrative"}}, library)
    row = _row(again, "demand_charge_price")
    assert (row.value, row.provenance, row.status) == (8000.0, "user", "needs_attention")
    assert any("demand_charge_price" in n and "not_applicable" in n
               for n in again.honesty_notes), again.honesty_notes
    assert "demand_charge_price: needs_attention (user)" in \
        L.maturity_from_ledger(again, "uploaded").reasons
    # Not silently re-customisable: it must be reset first.
    with pytest.raises(L.LedgerEditError, match="demand_charge_price.*reset"):
        L.apply_user_row(again, "demand_charge_price", 9000.0, unit="EUR/MW/month",
                         changed_by="u1", changed_at=NOW)


def test_reseed_flags_a_user_row_whose_unit_changed(library, seeded):
    led = L.apply_user_row(seeded, "battery_storage_eur_per_kwh", 150.0, unit="EUR/kWh",
                           changed_by="u1", changed_at=NOW)
    newer = dataclasses.replace(library, technology=tuple(
        dataclasses.replace(r, unit="EUR/MWh", value=r.value * 1000,
                            range_low=r.range_low * 1000, range_high=r.range_high * 1000)
        if r.key == "battery_storage_eur_per_kwh" else r for r in library.technology))
    again = L.reseed_ledger(led, KEY_DRIVERS, {}, newer)
    row = _row(again, "battery_storage_eur_per_kwh")
    assert (row.value, row.unit, row.status) == (150.0, "EUR/kWh", "needs_attention")
    assert any("battery_storage_eur_per_kwh" in n and "unit" in n
               for n in again.honesty_notes), again.honesty_notes


def test_reset_puts_the_seed_row_back(library, seeded):
    led = L.apply_user_row(seeded, "demand_charge_price", 8000.0, unit="EUR/MW/month",
                           changed_by="u1", changed_at=NOW)
    tou = {"tariff": {"tariff_id": "tou_reference_illustrative"}}
    again = L.reseed_ledger(led, KEY_DRIVERS, tou, library)
    back = L.reset_rows(again, ["demand_charge_price"], KEY_DRIVERS, tou, library)
    row = _row(back, "demand_charge_price")
    assert (row.value, row.provenance, row.status) == (None, "library", "default")
    with pytest.raises(L.LedgerEditError, match="no_such_key"):
        L.reset_rows(again, ["no_such_key"], KEY_DRIVERS, tou, library)


# BC-S2-3: values outside a row's physical domain are refused.

@pytest.mark.parametrize("key,value,unit,domain", [
    ("battery_inverter_efficiency", 96.0, "per unit", "(0, 1]"),
    ("battery_inverter_efficiency", 0.0, "per unit", "(0, 1]"),
    ("battery_round_trip_efficiency", 1.2, "per unit", "(0, 1]"),
    ("battery_storage_lifetime_years", 0.0, "years", "[1, inf)"),
    ("battery_inverter_lifetime_years", 0.5, "years", "[1, inf)"),
    ("battery_storage_eur_per_kwh", -100.0, "EUR/kWh", "[0, inf)"),
    ("battery_inverter_fom_pct_per_year", -1.0, "%/year", "[0, 100]"),
    ("battery_inverter_fom_pct_per_year", 101.0, "%/year", "[0, 100]"),
    ("demand_charge_price", -1.0, "EUR/MW/month", "[0, inf)"),
    ("energy_price_level", 0.0, "multiplier", "(0, inf)"),
    ("discount_rate", -0.5, "per unit", "[0, 1)"),
    ("discount_rate", 1.0, "per unit", "[0, 1)"),
])
def test_a_value_outside_the_rows_domain_is_refused(seeded, library, key, value, unit, domain):
    if key == "energy_price_level":
        # Gate S4 [S5]: only a multi-band tariff makes the level editable.
        seeded = lib.seed_ledger(
            KEY_DRIVERS, {"tariff": {"tariff_id": "tou_reference_illustrative"}}, library)
    with pytest.raises(L.LedgerEditError) as exc:
        L.apply_user_row(seeded, key, value, unit=unit, changed_by="u1", changed_at=NOW)
    assert key in str(exc.value) and domain in str(exc.value), str(exc.value)


@pytest.mark.parametrize("key,value,unit", [
    ("battery_inverter_efficiency", 1.0, "per unit"),
    ("discount_rate", 0.0, "per unit"),
    ("battery_storage_lifetime_years", 1.0, "years"),
    ("battery_storage_eur_per_kwh", 0.0, "EUR/kWh"),
])
def test_a_value_on_a_closed_bound_is_accepted(seeded, key, value, unit):
    led = L.apply_user_row(seeded, key, value, unit=unit, changed_by="u1", changed_at=NOW)
    assert _row(led, key).value == value


def test_every_row_carries_its_domain_and_the_defaults_sit_inside(seeded, library):
    """The domain is data on the row (the library CSV's `domain` column)."""
    assert all(r.domain for r in library.technology), [
        r.key for r in library.technology if not r.domain]
    for row in seeded.rows:
        if row.key == "tariff":
            continue
        assert row.domain is not None, row.key
        if row.value is not None:
            assert row.domain.contains(row.value), (row.key, row.value, row.domain)


# BC-S2-4: one currency year, never mixed silently.

def test_a_value_in_another_currency_year_is_refused_naming_both(seeded):
    with pytest.raises(L.LedgerEditError) as exc:
        L.apply_user_row(seeded, "battery_storage_eur_per_kwh", 150.0, unit="EUR/kWh",
                         changed_by="u1", changed_at=NOW, currency_year=2026)
    text = str(exc.value)
    assert "battery_storage_eur_per_kwh" in text and "2026" in text and "2020" in text
    same = L.apply_user_row(seeded, "battery_storage_eur_per_kwh", 150.0, unit="EUR/kWh",
                            changed_by="u1", changed_at=NOW, currency_year=2020)
    assert _row(same, "battery_storage_eur_per_kwh").currency_year == 2020


def test_a_study_currency_year_other_than_the_ledgers_is_named(seeded):
    noted = L.with_study_currency_year(seeded, 2026)
    [note] = [n for n in noted.honesty_notes if "currency_year" in n and "2026" in n]
    assert "2020" in note
    assert L.with_study_currency_year(seeded, 2020).honesty_notes == seeded.honesty_notes
    assert L.with_study_currency_year(seeded, None).honesty_notes == seeded.honesty_notes
    # Idempotent: applying it twice names the mismatch once.
    assert L.with_study_currency_year(noted, 2026).honesty_notes == noted.honesty_notes


# BC-S2-5: the 2030 projection is labelled.

def test_the_cost_rows_are_labelled_2030_projections(library, seeded):
    sourced = [r for r in library.technology if r.value is not None]
    assert {r.projection_year for r in sourced} == {2030}
    assert "technology_costs_are_2030_projections_in_2020_eur" in seeded.honesty_notes
    for r in sourced:
        assert "(2030 projection)" in _row(seeded, r.key).label, r.key


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
