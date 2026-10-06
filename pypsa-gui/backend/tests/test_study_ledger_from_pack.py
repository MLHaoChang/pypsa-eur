"""
U2 WP3 — the assumptions ledger seeded from IC's generic defaults pack.

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §1
(rows 1-20 and 21-34), §6 (the pack as GS needs it), WP3.

`library.load_defaults()` reads the pack (U1 a) into the GS `Library` view,
so `seed_ledger` / `reseed_ledger` / `reset_rows` and the pack builder take it
unchanged. The seeded rows 1-20 equal the WP0 ledger (the vendored
`study_library`) row for row; rows 21-34 are the finance-engine inputs a
minimal single-owner case needs, each with its `engine_path` and the pack
rule as its provenance. Production keeps the legacy library until WP8.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.golden import site_fixture as SF

DE, TOU = "de_industrial_illustrative", "tou_reference_illustrative"
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
FIELDS = ("key", "value", "unit", "basis", "currency_year", "range", "domain", "source",
          "source_year", "sensitivity_flag")
ROWS_21_34 = (
    "financial_close_year", "cod_year", "contingency_share",
    "escalation_tariff", "escalation_export", "escalation_opex", "escalation_capex",
    "escalation_fuel", "escalation_ppa", "inflation", "cost_of_equity_rule",
    "analysis_years_rule", "value_flows_template", "salvage_rule",
    "pv_degradation_pct_per_year", "tax_pack", "incentives_rule", "export_series")
DESCRIPTORS = ("inflation", "cost_of_equity_rule", "analysis_years_rule",
               "value_flows_template", "salvage_rule", "tax_pack", "incentives_rule",
               "export_series")
NETWORK_ONLY = ("battery_inverter_efficiency",
                "battery_storage_degradation_calendar_pct_per_year",
                "battery_storage_degradation_cycling_pct_per_cycle")


def _L():
    from services.study import library as L

    return L


def _LG():
    from services.study import ledger as LG

    return LG


def _Q():
    from services.study import questions as Q

    return Q.BESS_AT_SITE


def _intake(tariff_id=DE, **over):
    return {**SF.site_intake(), "tariff": {"tariff_id": tariff_id}, **over}


USER_TARIFF = {
    "tariff_id": "site_a_contract", "name": "Site A supply contract", "source": "user bill",
    "source_year": 2026, "currency": "EUR", "currency_year": 2020,
    "energy_bands": [{"label": "flat", "price_per_mwh": 120.0, "applies": {}}],
    "demand_charge": {"price_per_mw_per_period": 8000.0}, "export": {"price_per_mwh": 35.0}}


@pytest.fixture(scope="module")
def defaults():
    return _L().load_defaults()


def _pair(intake, defaults):
    legacy = _L().seed_ledger(_Q(), intake, _L().load_library())
    pack = _L().seed_ledger(_Q(), intake, defaults)
    return legacy, pack


@pytest.mark.parametrize("intake", [_intake(DE), _intake(TOU),
                                    _intake(tariff={"custom": USER_TARIFF})],
                         ids=["de", "tou", "user"])
def test_rows_1_to_20_equal_the_wp0_ledger_row_for_row(defaults, intake):
    legacy, pack = _pair(intake, defaults)
    n = len(legacy.rows)
    assert [r.key for r in pack.rows[:n]] == [r.key for r in legacy.rows]
    for a, b in zip(legacy.rows, pack.rows[:n]):
        for f in FIELDS:
            assert getattr(b, f) == getattr(a, f), (a.key, f, getattr(a, f), getattr(b, f))
        assert (b.provenance, b.status, b.unavailable) == (a.provenance, a.status, a.unavailable)
        assert (b.label, b.technical_name) == (a.label, a.technical_name)
    assert pack.honesty_notes == legacy.honesty_notes


def test_the_pack_tariffs_are_the_wp0_seed_tariffs(defaults):
    legacy = _L().load_library()
    assert list(defaults.tariffs) == list(legacy.tariffs)
    for tid, t in legacy.tariffs.items():
        assert defaults.tariffs[tid].model_dump() == t.model_dump(), tid
    assert defaults.default_tariff_id == legacy.default_tariff_id


def test_rows_21_to_34_follow_with_engine_paths_and_the_pack_rule_as_provenance(defaults):
    led = _L().seed_ledger(_Q(), _intake(DE), defaults)
    keys = [r.key for r in led.rows]
    assert keys[-len(ROWS_21_34):] == list(ROWS_21_34)
    rows = {r.key: r for r in led.rows}
    for key in ROWS_21_34:
        r = rows[key]
        assert r.engine_path, key
        assert (r.provenance, r.status, r.sensitivity_flag) == ("library", "default", False)
        assert r.source and r.source_year
    assert rows["financial_close_year"].value == SF.SITE_YEAR - 1
    assert rows["financial_close_year"].engine_path == "finance.financial_close"
    assert rows["cod_year"].value == SF.SITE_YEAR
    assert rows["contingency_share"].value == 0.0
    assert all(rows[f"escalation_{c}"].value == 0.0 for c in
               ("tariff", "export", "opex", "capex", "fuel", "ppa"))
    assert rows["escalation_tariff"].engine_path == "finance.escalation.tariff"
    assert rows["pv_degradation_pct_per_year"].value == 0.0
    # Gate U2-S1 C4: the pack carries none of these rules (Q7), so they are the
    # guided study's own, never attributed to the pack; the rule id is kept.
    from services.library.defaults_pack.loader import load_defaults_pack

    stamp = load_defaults_pack().stamp
    for key in ROWS_21_34:
        if key == "export_series":
            continue                                   # the tariff's own source
        assert rows[key].source == "guided study (pending pack rule, Q7)", key
        assert stamp not in rows[key].source and "generic_defaults" not in rows[key].source
    assert "guided.salvage.annuity_pv_remaining_life" in rows["salvage_rule"].help
    assert rows["export_series"].source.startswith("Tariff ")
    assert rows["export_series"].engine_path == "commercial.export_price_ref"
    for key in DESCRIPTORS:
        assert rows[key].value is None and rows[key].unavailable == {"value": "rule_descriptor"}
    assert "export_cap_mw" not in rows            # row 34 only when the tariff states a cap


def test_the_export_cap_row_appears_when_the_tariff_states_one(defaults):
    tariff = {**USER_TARIFF, "export": {"price_per_mwh": 35.0, "cap_mw": 0.5}}
    rows = {r.key: r for r in _L().seed_ledger(
        _Q(), _intake(tariff={"custom": tariff}), defaults).rows}
    assert rows["export_cap_mw"].value == 0.5
    assert rows["export_cap_mw"].engine_path == "commercial.connection.export_cap_mw"


def test_every_row_names_the_engine_field_it_compiles_to(defaults):
    led = _L().seed_ledger(_Q(), _intake(DE), defaults)
    for r in led.rows:
        if r.key in NETWORK_ONLY:
            assert r.engine_path is None, r.key
        else:
            assert r.engine_path, r.key
    paths = {r.key: r.engine_path for r in led.rows}
    assert paths["demand_charge_price"] == "commercial.import_tariff.items[demand].periods[0].rate"
    assert paths["discount_rate"] == "solver_config.discount_rate"


@pytest.mark.parametrize("key", [*DESCRIPTORS, "financial_close_year", "cod_year"])
def test_rule_rows_cannot_be_typed_over(defaults, key):
    led = _L().seed_ledger(_Q(), _intake(DE), defaults)
    row = next(r for r in led.rows if r.key == key)
    with pytest.raises(_LG().LedgerEditError, match=key):
        _LG().apply_user_row(led, key, 1.0, unit=row.unit, changed_by="u1", changed_at=NOW)


def test_a_numeric_engine_row_is_editable_within_its_domain(defaults):
    led = _L().seed_ledger(_Q(), _intake(DE), defaults)
    led = _LG().apply_user_row(led, "escalation_tariff", 0.02, unit="per unit per year",
                               changed_by="u1", changed_at=NOW)
    assert next(r for r in led.rows if r.key == "escalation_tariff").value == 0.02
    with pytest.raises(_LG().LedgerEditError):
        _LG().apply_user_row(led, "contingency_share", -0.1, unit="share of capex",
                             changed_by="u1", changed_at=NOW)


def test_the_ledger_version_names_the_pack_version_and_hash(defaults):
    from services.library.defaults_pack.loader import load_defaults_pack

    pack = load_defaults_pack()
    led = _L().seed_ledger(_Q(), _intake(DE), defaults)
    assert led.ledger_version == f"generic-defaults {pack.version} {pack.hash[:12]}"
    assert defaults.version == led.ledger_version


def test_the_ledger_hash_keeps_its_shape_and_ignores_engine_paths(defaults):
    from services.study import packs

    led = _L().seed_ledger(_Q(), _intake(DE), defaults)
    stripped = led.model_copy(update={"rows": [r.model_copy(update={"engine_path": None})
                                               for r in led.rows]})
    assert packs.ledger_hash(led) == packs.ledger_hash(stripped)
    assert len(packs.ledger_hash(led)) == 16


def test_the_pack_tariff_row_is_illustrative_and_holds_the_badge(defaults):
    """
    BC-S2-1 on the flag (§6.2): the pack's illustrative tariff holds the
    study at screening even when its row is re-marked as the user's (an Expert
    re-import of the unchanged pack tariff, rule 5).
    """
    led = _L().seed_ledger(_Q(), _intake(DE), defaults)
    rows = []
    for r in led.rows:
        if r.sensitivity_flag and r.value is not None:
            r = r.model_copy(update={"status": "customised", "provenance": "user",
                                     "changed_at": NOW})
        if r.key == "tariff":
            r = r.model_copy(update={"provenance": "imported", "source": "pack tariff"})
        rows.append(r)
    m = _LG().maturity_from_ledger(led.model_copy(update={"rows": rows}), "uploaded")
    assert m.class_ == "screening"
    assert any(x.startswith("tariff: de_industrial_illustrative") for x in m.reasons), m.reasons


def test_the_rows_carry_the_packs_illustrative_flag(defaults):
    rows = {r.key: r for r in _L().seed_ledger(_Q(), _intake(DE), defaults).rows}
    assert rows["tariff"].illustrative is True and rows["demand_charge_price"].illustrative
    assert rows["battery_storage_eur_per_kwh"].illustrative is False
    assert rows["sizing_limit_connection_multiple"].illustrative is True


def test_horizon_and_replacements_read_the_pack_rules(defaults):
    led = _L().seed_ledger(_Q(), _intake(DE), defaults)
    assert _L().horizon_and_replacements(led, defaults) == (25, (10, 20))


def test_the_pack_builds_the_same_site_network(defaults):
    from services.study import packs

    legacy_led = _L().seed_ledger(_Q(), _intake(DE), _L().load_library())
    pack_led = _L().seed_ledger(_Q(), _intake(DE), defaults)
    a = packs.build_site_network(_intake(DE), legacy_led, "bess_pv_2h")
    b = packs.build_site_network(_intake(DE), pack_led, "bess_pv_2h", library=defaults)
    for comp in ("storage_units", "generators", "links"):
        cols = ["capital_cost", "fom_cost", "p_nom_max", "overnight_cost", "lifetime"]
        ga, gb = getattr(a, comp)[cols], getattr(b, comp)[cols]
        assert ga.equals(gb), comp
    assert a.links_t.marginal_cost.equals(b.links_t.marginal_cost)


# ── F1-B5's three ledger branches, on the pack-backed library ─────────────

def test_reset_clears_the_needs_attention_note(defaults):
    from services.study.packs import needs_attention_rows

    seeded = _L().seed_ledger(_Q(), _intake(DE), defaults)
    led = _LG().apply_user_row(seeded, "demand_charge_price", 8000.0, unit="EUR/MW/month",
                               changed_by="u1", changed_at=NOW)
    tou = _intake(TOU)
    again = _LG().reseed_ledger(led, _Q(), tou, defaults)
    note = "needs_attention:demand_charge_price:"
    assert any(n.startswith(note) for n in again.honesty_notes)
    back = _LG().reset_rows(again, ["demand_charge_price"], _Q(), tou, defaults)
    assert not any(n.startswith(note) for n in back.honesty_notes)
    assert needs_attention_rows(back) == []


def test_a_user_tariff_whose_source_says_illustrative_holds_the_badge(defaults):
    tariff = {**USER_TARIFF, "source": "Illustrative"}
    led = _L().seed_ledger(_Q(), _intake(tariff={"custom": tariff}), defaults)
    rows = [r.model_copy(update={"status": "customised", "provenance": "user",
                                 "changed_at": NOW})
            if r.sensitivity_flag and r.value is not None else r for r in led.rows]
    m = _LG().maturity_from_ledger(led.model_copy(update={"rows": rows}), "uploaded")
    assert m.class_ == "screening"
    assert m.reasons == ["tariff: site_a_contract (Illustrative, user)"]


def test_a_reseed_re_derives_a_supplied_tariffs_prices(defaults):
    seeded = _L().seed_ledger(_Q(), _intake(tariff={"custom": USER_TARIFF}), defaults)
    to_seed = _LG().reseed_ledger(seeded, _Q(), _intake(DE), defaults)
    row = next(r for r in to_seed.rows if r.key == "demand_charge_price")
    assert (row.value, row.provenance, row.status) == (9000.0, "library", "default")
    changed = {**USER_TARIFF, "demand_charge": {"price_per_mw_per_period": 9100.0}}
    again = _LG().reseed_ledger(seeded, _Q(), _intake(tariff={"custom": changed}), defaults)
    assert next(r for r in again.rows if r.key == "demand_charge_price").value == 9100.0
    assert not any(n.startswith("needs_attention:") for n in again.honesty_notes)
