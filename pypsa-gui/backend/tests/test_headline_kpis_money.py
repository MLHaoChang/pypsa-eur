# ruff: noqa: F811 -- imported shared fixtures.
"""guided-results 04: the `/results/headline_kpis` LCOE card carries its money year and
basis line, read with the finance report's own money-year helpers (GK B (72) ruling A).
Apart from `test_headline_kpis.py` so a mutant run over these lines stays short."""

from tests.test_headline_kpis import as_template, cards, checked_profiles_on  # noqa: F401
from tests.test_investment_assurance import lcoe_hand, project, store  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401


def _lcoe_card(project, client, monkeypatch, currency_year, base_year):
    def money(payload):
        lcoe_hand(payload)
        payload["currency_year"], payload["base_year"] = currency_year, base_year

    store(project, money)
    as_template(monkeypatch, "eh_datacenter")
    _, by_id, _ = cards(client)
    return by_id["lcoe"]


def _engine_words(currency_year, base_year):
    from types import SimpleNamespace

    from services.finance.report import basis_statement

    # the engine's own basis line, read as real: the card names the money it is in
    return basis_statement(
        SimpleNamespace(
            currency="USD", currency_year=currency_year, price_basis="real"
        ),
        base_year,
    )


def test_the_lcoe_card_is_in_base_year_money_when_the_price_data_states_another_year(
    project, client, monkeypatch
):
    lcoe = _lcoe_card(project, client, monkeypatch, 2020, 2027)
    assert lcoe["state"] == "ready" and lcoe["money_year"] == 2027
    assert lcoe["basis"] == "investment case · " + _engine_words(2020, 2027)
    assert (
        lcoe["basis"]
        == "investment case · real basis, costs in real 2027 USD (2020 price data)"
    )


def test_the_lcoe_card_names_the_currency_year_when_it_is_the_base_year(
    project, client, monkeypatch
):
    lcoe = _lcoe_card(project, client, monkeypatch, 2024, 2024)
    assert lcoe["money_year"] == 2024
    assert lcoe["basis"] == "investment case · " + _engine_words(2024, 2024)
    assert lcoe["basis"] == "investment case · real basis, 2024 USD"


def test_the_lcoe_card_guesses_no_year_when_none_is_stated(
    project, client, monkeypatch
):
    lcoe = _lcoe_card(project, client, monkeypatch, None, 2027)
    assert lcoe["money_year"] is None
    assert (
        lcoe["basis"] == "investment case · real basis, USD (currency year not stated)"
    )
    assert lcoe["basis"] == "investment case · " + _engine_words(None, 2027)


# ── `_lcoe_money` on its own: where the base year and the currency come from ──


def _ctx(stored):
    from types import SimpleNamespace

    return SimpleNamespace(solver_state={"investment_case_report": stored})


def _stored(base_year=2027, assumptions_hash="h"):
    return {
        "assumptions_hash": assumptions_hash,
        "sections": {"project": {"payload": {"base_year": base_year}}},
    }


RAW = {"assumptions_hash": "h", "currency_year": 2020}


def test_the_base_year_is_read_from_the_stored_report_raw_was_read_from():
    from services.results.headline_kpis import _lcoe_money

    assert _lcoe_money(_ctx(_stored()), RAW, "USD/MWh")["money_year"] == 2027

    class Model:  # a stored pydantic report
        def model_dump(self, mode):
            assert mode == "json"
            return _stored()

    assert _lcoe_money(_ctx(Model()), RAW, "USD/MWh")["money_year"] == 2027


def test_another_reports_base_year_is_never_borrowed():
    from services.results.headline_kpis import _lcoe_money

    # a report of other assumptions, no report, or one without a project payload: no base year
    for stored in (
        _stored(assumptions_hash="other"),
        None,
        {"assumptions_hash": "h"},
        {"assumptions_hash": "h", "sections": {"project": {}}},
        {"assumptions_hash": "h", "sections": {"project": None}},
        {"assumptions_hash": "h", "sections": None},
    ):
        out = _lcoe_money(_ctx(stored), RAW, "USD/MWh")
        assert out["money_year"] == 2020, stored
        assert out["basis"] == "investment case · real basis, 2020 USD", stored


def test_the_basis_names_the_currency_of_the_facts_unit():
    from services.results.headline_kpis import _lcoe_money

    for unit in ("currency/MWh", None, ""):
        out = _lcoe_money(_ctx(None), RAW, unit)
        assert out["basis"] == "investment case · real basis, 2020 report currency", (
            unit
        )
    assert _lcoe_money(_ctx(None), RAW, "GBP/MWh")["basis"].endswith("2020 GBP")
