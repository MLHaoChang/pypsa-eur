"""
U3: the study chat tools' views (`services/study/chat_view.py`) over the
recorded findings payloads the decision panel's tests use
(`frontend/src/pages/decision/__fixtures__`): the verdict reads in words with
its figures, every code carries its sentence, a missing figure says so, and
the result fits the harness's tool-result cap.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from services.study import chat_view
from services.study.report import HELP

FIX = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
       / "decision" / "__fixtures__")
# `harness/results.py` truncates a result above 4,000 characters.
CAP = 4000


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["findings.json", "findings_pre.json", "findings_pv.json",
                                  "findings_pv_pre.json"])
def test_both_views_fit_the_tool_result_cap(name):
    f = _load(name)
    for view in (chat_view.findings_digest(f), chat_view.verdict_explanation(f)):
        assert len(json.dumps(view, default=str)) < CAP, name


def test_the_verdict_sentence_is_rendered_with_its_figures():
    v = chat_view.findings_digest(_load("findings.json"))["verdict"]
    assert v["class"] == "recommended"
    assert "{{" not in v["sentence"]
    assert "a battery of 0.3 MW with 1 hours of storage" in v["sentence"]
    assert "3,531,384 EUR" in v["sentence"]
    assert [k["label"] for k in v["headline"]][0] == "Battery NPV"


def test_a_verdict_not_established_says_why_in_words():
    d = chat_view.findings_digest(_load("findings_pre.json"))
    v = d["verdict"]
    assert v["status"] == "not_established" and v["sentence"] is None
    assert v["not_established_because"] == [
        {"code": "tornado_not_run", "means": HELP["tornado_not_run"]}]
    assert "stage='tornado'" in d["next_step"]


def test_explain_names_drivers_disclosures_and_the_sizing_of_the_named_option():
    f = _load("findings.json")
    e = chat_view.verdict_explanation(f)
    assert [d["key"] for d in e["drivers"]] == f["verdict"]["drivers"]
    assert e["drivers"][0]["label"] == "Discount rate (real, pre-tax)"
    assert all(d["means"] != "No explanation is recorded for this code." for d in e["disclosures"])
    assert e["sizing"] and {s["option_id"] for s in e["sizing"]} == {f["verdict"]["option_id"]}


def test_a_missing_figure_reads_not_established_never_zero():
    facts = {"battery_npv": {"key": "battery_npv", "label": "Battery NPV", "value": None,
                             "unit": "EUR", "engine": "finance_engine",
                             "unavailable": "case_not_established"}}
    assert chat_view.render_sentence("NPV {{battery_npv}}.", facts) == "NPV not established."
    assert chat_view.render_sentence("NPV {{missing}}.", {}) == "NPV not established."


def test_the_ledger_view_returns_the_edited_row_and_the_maturity():
    ledger = _load("ledger.json")
    key = ledger["ledger"]["rows"][0]["key"]
    out = chat_view.ledger_row_digest(ledger, key)
    assert out["row"]["key"] == key and "maturity" in out
    assert len(json.dumps(out)) < CAP
