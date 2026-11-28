# ruff: noqa: F811 -- imported shared fixtures.
"""te-24 (LCOE-CAVEAT-1): the `/results/headline_kpis` LCOE card carries the caveat above
EUR 300/MWh and none below it. Apart from `test_lcoe_caveat.py` so a mutant run over
`services/results/lcoe_caveat.py` stays short."""

from tests.test_headline_kpis import cards, checked_profiles_on  # noqa: F401
from tests.test_investment_assurance import lcoe_hand, project, store  # noqa: F401
from tests.test_lcoe_caveat import CAUSE, HOSPITAL, NBSP
from tests.test_workflow_tools import baseline  # noqa: F401


def _lcoe_card(project, client, monkeypatch, currency, scale):
    from routers import results as results_router

    def money(payload):
        lcoe_hand(payload, value=(0.0, 100.0 * scale, 100.0 * scale))
        payload["currency"] = currency

    store(project, money)
    monkeypatch.setattr(results_router, "_workbook_template", lambda: HOSPITAL)
    _, by_id, _ = cards(client)
    return by_id["lcoe"]


def test_the_headline_card_carries_the_caveat_above_the_bound(
    project, client, monkeypatch
):
    lcoe = _lcoe_card(project, client, monkeypatch, "EUR", 100)
    assert lcoe["state"] == "ready" and lcoe["value"] > 300
    # the hospital's words; whether the cause shows depends on the network's gensets,
    # which the unit tests above pin
    assert lcoe["caveat"] in (
        f"Above €{NBSP}300/MWh, high for a hospital: {CAUSE}.",
        f"Above €{NBSP}300/MWh, high for a hospital.",
    )


def test_the_headline_card_has_no_caveat_below_the_bound(project, client, monkeypatch):
    lcoe = _lcoe_card(project, client, monkeypatch, "EUR", 1)
    assert lcoe["state"] == "ready" and lcoe["value"] < 300
    assert lcoe["caveat"] is None
