"""The `ic.counterfactual` gate fact (study harness issue 19): read from the
stored investment case through `GET /results/investment_case`, never by
re-running it."""
from __future__ import annotations

import pytest

from services import ic_counterfactual as F

BUYS = ["counterfactual_basis:site_today", "counterfactual_buys:h2_offtake",
        "co2_price_period:2030:2030:120"]
UNSERVED = ["counterfactual_basis:site_today",
            "counterfactual_baseline_unserved:h2_offtake",
            "counterfactual_not_established:owner_conversion_load"]
INPUTS = {"finance": "f1", "dispatch": "d1"}


def _report(hash_="h1"):
    return {"assumptions_hash": hash_,
            "sections": {"project": {"payload": {"provenance": {"inputs": INPUTS}}}}}


@pytest.fixture
def stored(monkeypatch):
    """Seed the stored run and report; the current inputs match them unless
    a test says the network changed."""
    import routers.results as R

    def seed(record=None, report=None, current=("h1", INPUTS)):
        for key, value in (("investment_case", record), ("investment_case_report", report)):
            if value is None:
                monkeypatch.delitem(R._state, key, raising=False)
            else:
                monkeypatch.setitem(R._state, key, value)
        monkeypatch.setattr(R, "_ic_current_assumptions", lambda: (current, None))
        return F.read(lambda fn: fn())
    return seed


def test_a_done_case_on_the_site_today_basis_is_established(stored):
    fact = stored({"status": "done", "study": "investment_case", "flags": BUYS}, _report())
    assert fact == {"state": "established", "basis": "site_today", "reasons": []}


def test_a_done_case_without_the_basis_flag_is_established_on_the_analytic_basis(stored):
    fact = stored({"status": "done", "flags": ["degradation_bill_first_order"]}, _report())
    assert fact == {"state": "established", "basis": "analytic", "reasons": []}


def test_a_done_case_with_a_not_established_flag_says_why(stored):
    fact = stored({"status": "done", "flags": UNSERVED}, _report())
    assert fact == {"state": "not_established", "basis": "site_today",
                    "reasons": ["counterfactual_not_established:owner_conversion_load"]}


def test_a_refused_case_is_not_established_with_its_code(stored):
    fact = stored({"status": "refused", "error_code": "counterfactual_not_established:lossy_poc"},
                  _report())
    assert fact == {"state": "not_established", "basis": None,
                    "reasons": ["counterfactual_not_established:lossy_poc"]}


def test_no_case_is_not_run(stored):
    assert stored() == {"state": "not_run", "basis": None, "reasons": ["no_case"]}


def test_a_case_whose_inputs_changed_is_not_run(stored):
    fact = stored({"status": "done", "flags": BUYS}, _report(),
                  current=("h2", {**INPUTS, "dispatch": "d2"}))
    assert fact == {"state": "not_run", "basis": None,
                    "reasons": ["case_stale", "changed:dispatch"]}


@pytest.mark.parametrize("status, reason", [("running", "case_running"),
                                            ("failed", "case_failed")])
def test_a_running_or_failed_case_is_not_run(stored, status, reason):
    assert stored({"status": status}, _report())["state"] == "not_run"
    assert F.classify({"status": status, "report": {"present": True, "stale": False}}) \
        == {"state": "not_run", "basis": None, "reasons": [reason]}


def test_the_text_quotes_the_cases_own_sentences():
    from services.finance.baseline_text import flag_text
    from services.finance.export_xlsx import BASELINE_CAVEATS

    caveats = dict(BASELINE_CAVEATS)
    assert F.counterfactual_text(BUYS) == [caveats["counterfactual_buys"],
                                           caveats["co2_price_period"]]
    assert F.counterfactual_text(UNSERVED) \
        == [flag_text("counterfactual_baseline_unserved:h2_offtake")]
    # Quoted, never restated: no price in the words.
    assert not any("EUR" in t or "2.0" in t for t in F.counterfactual_text(BUYS))
