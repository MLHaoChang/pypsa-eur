"""Study harness issue 21 §3-4: data requests and answers (B1-B4)."""
from __future__ import annotations

from services import run_answers as A

WEATHER = {"target": {"component": "Settings", "name": "Weather", "attr": "year"}, "label": "Weather year"}
TARIFF = {"target": {"component": "Settings", "name": "Commercial", "attr": "import_tariff_ref"}, "label": "Tariff"}
W = ("Settings", "Weather", "year")
T = ("Settings", "Commercial", "import_tariff_ref")


def _task(req):
    return {"owner_id": "u1", "project_id": "p1",
            "run": {"waiting": {"kind": "data", "ref": req["id"], "items": req["items"]}}}


def _answer(task, req, **kw):
    args = dict(actor_id="u1", project_id="p1", decision={"by": "user", "id": "card-1"}, diff=[W],
                before="fp1", head="fp1")
    args.update(kw)
    return A.answer(task, req, **args)


def test_one_request_lists_every_gap_and_none_is_asked_twice():
    req = A.request([], [WEATHER, TARIFF])
    assert [i["label"] for i in req["items"]] == ["Weather year", "Tariff"] and req["id"].startswith("req-")
    assert A.request([req], [WEATHER, TARIFF]) is None
    later = A.request([req], [WEATHER, {"param": "connection_mw"}])
    assert [i.get("param") for i in later["items"]] == ["connection_mw"] and later["id"] != req["id"]


def test_two_carded_answers_complete_the_request():
    req = A.request([], [WEATHER, TARIFF])
    task = _task(req)
    row, why = _answer(task, req)
    assert why is None and row == {"answers": req["id"], "decision_id": "card-1", "items": [["target", *W]]}
    assert A.record(req, row) is False
    row, why = _answer(task, req, diff=[T], decision={"by": "user", "id": "card-2"}, before="fp2", head="fp2")
    assert why is None and A.record(req, row) is True


def test_a_write_answering_no_item_is_drift():
    req = A.request([], [WEATHER])
    assert _answer(_task(req), req, diff=[("Generator", "g1", "p_nom")]) == (None, "outside_the_request")


def test_b2_an_item_plus_one_extra_field_is_all_drift():
    req = A.request([], [WEATHER])
    assert _answer(_task(req), req, diff=[W, ("Settings", "Weather", "source")]) == (None, "outside_the_request")


def test_b3_a_write_between_the_chain_head_and_the_answer_is_drift():
    req = A.request([], [WEATHER])
    assert _answer(_task(req), req, before="fp1", head="fp0") == (None, "chain_moved")


def test_b1_only_the_owner_on_its_project_by_a_card_while_waiting():
    req = A.request([], [WEATHER])
    task = _task(req)
    assert _answer(task, req, actor_id="u2")[1] == "not_the_runs_owner_or_project"
    assert _answer(task, req, project_id="p2")[1] == "not_the_runs_owner_or_project"
    assert _answer(task, req, decision={"by": "envelope:x", "id": "e"})[1] == "not_a_card_decision"
    running = dict(task, run={"waiting": None})
    assert _answer(running, req)[1] == "not_waiting_on_data"
    other = A.request([req], [TARIFF])
    assert _answer(task, other)[1] == "not_waiting_on_data"


ENVELOPE = {"gaps": [{"tool": "set_weather_choice", "target": WEATHER["target"]},
                     {"tool": "attach_tariff", "target": TARIFF["target"]}]}


def test_a_gap_l1_filled_is_not_a_data_item():
    items = A.data_items(ENVELOPE, {}, unset=lambda t: t["attr"] == "import_tariff_ref", present=lambda p: True)
    assert [i["target"]["attr"] for i in items] == ["import_tariff_ref"]


def test_a_missing_required_input_is_listed_absent_not_falsy():
    profile = {"inputs": {"required": ["load_series", "zero_ok"]}}
    held = {"zero_ok": 0}
    items = A.data_items(None, profile, unset=lambda t: False, present=lambda p: p in held)
    assert [i["param"] for i in items] == ["load_series"]


def test_an_unrelated_failed_rule_is_never_a_data_item():
    """Only gap targets and required inputs are items; a failed validation
    rule (say `in.validate.errors`) stays in the needs_user notice."""
    items = A.data_items(ENVELOPE, {"inputs": {"required": []}}, unset=lambda t: False, present=lambda p: True)
    assert items == []
