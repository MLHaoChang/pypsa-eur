"""
The BESS question template (MVP-1 S4; plan S4 "Files", gate S2 carries).

`services/study/questions.py::BESS_AT_SITE` is the one template MVP-1 ships.
Its key drivers are THE list the ledger seeds its sensitivity flags from
(gate S2: they replace the `library.BESS_KEY_DRIVERS` stand-in, with an
equality test), and its `none` option OMITS the battery and the PV rather
than fixing them at 0 (review v2 BC-1).
"""
from __future__ import annotations

from services.study import library as lib
from services.study import questions as Q


def test_the_template_names_its_options_inputs_streams_and_drivers():
    q = Q.BESS_AT_SITE
    assert q.question_id == "bess_at_site"
    assert q.mandatory_inputs == ["site", "tariff", "load", "connection_limit"]
    assert [o.option_id for o in q.options] == [
        "none", "bess_1h", "bess_2h", "bess_4h", "bess_pv_2h"]
    assert q.headline_metrics == ["npv", "payback", "sizes"]
    assert q.value_streams == [
        "demand_charge_reduction", "energy_shift", "export_credit"]
    assert q.key_drivers == [
        "battery_storage_eur_per_kwh", "battery_inverter_eur_per_kw",
        "demand_charge_price", "energy_price_level", "discount_rate"]


def test_none_omits_the_assets_instead_of_fixing_them_at_zero():
    none = Q.option(Q.BESS_AT_SITE, "none")
    assert set(none.omitted_assets) == {"battery", "pv"}
    assert none.free_assets == [] and none.fixed_assets == []


def test_durations_are_enumerated_per_option():
    hours = {o.option_id: Q.max_hours(o) for o in Q.BESS_AT_SITE.options}
    assert hours == {"none": None, "bess_1h": 1.0, "bess_2h": 2.0,
                     "bess_4h": 4.0, "bess_pv_2h": 2.0}


def test_pv_option_only_when_pv_is_enabled():
    off = [o.option_id for o in Q.options_for(Q.BESS_AT_SITE, {})]
    on = [o.option_id for o in Q.options_for(
        Q.BESS_AT_SITE, {"pv": {"enabled": True}})]
    assert "bess_pv_2h" not in off and off[0] == "none"
    assert on[-1] == "bess_pv_2h" and len(on) == 5


def test_template_key_drivers_equal_the_library_stand_in():
    """Gate S2: one list. The stand-in stays only as an alias of the template."""
    assert list(lib.BESS_KEY_DRIVERS) == Q.BESS_AT_SITE.key_drivers


def test_the_routes_seed_the_ledger_from_the_template():
    from routers import studies

    class _S:
        question_id = "bess_at_site"

    assert tuple(studies._key_drivers(_S())) == tuple(Q.BESS_AT_SITE.key_drivers)


def test_unknown_question_is_not_a_template():
    assert Q.get_question("bess_site") is None
    assert Q.get_question("bess_at_site") is Q.BESS_AT_SITE
