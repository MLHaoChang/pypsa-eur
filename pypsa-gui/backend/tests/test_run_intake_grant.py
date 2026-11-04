"""Study harness issue 21, stage 2: the plan-review grant creates the
project and hands the run over (`run_envelope.decide` with a `setup` entry),
the new project's envelope is the host card's grant (A2), L1's exit asks for
data once (§3, GK B (20) Q2), and card writes answer that request (B1-B4).
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
from fastapi import HTTPException

from db import session as db_session
from db.models import Project
from harness.session import TURN_AUTHOR_KEY
from services import assistant_tasks as tasks
from harness import run_loops
from services import project_registry, run_answers, run_envelope, run_handover, run_intake, upload_service
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline, dispatch, edit, helpers_state, session, wait_until,
)
from tests.test_harness_envelope import granted, on_run, profile  # noqa: F401
from tests.test_harness_run_skeleton import RUN, site, start  # noqa: F401
from tests.test_harness_runner import detached, task_path  # noqa: F401


@pytest.fixture
def host(site, session, profile, monkeypatch):
    """The open (host) project with a stages table upload and one turn the
    owner typed; intake reads them through the owner's ACL."""
    owner = tasks._authority()[2]
    real = run_intake.host_for
    acl = real(PyPSAService.get_active_context(), owner, site[0])
    uid = upload_service.add_upload(acl.name, b"year,peak (kW)\n2027,3000\n2030,6000\n", "site.csv", "text/csv",
                                    project_dir=acl.dir).file_id
    turn = {"ts": time.time(), "session_id": "s-1", "user": "It is a hospital. Grid connection is 12 MW.",
            "assistant": [], TURN_AUTHOR_KEY: owner, run_intake.ORIGIN_KEY: "user"}
    tid = run_intake.turn_id(turn)

    def host_for(ctx, owner_id, project_id):
        found = real(ctx, owner_id, project_id)
        return run_intake.Host(found.name, found.dir, found.owner_id, (turn,))
    monkeypatch.setattr(run_intake, "host_for", host_for)
    citations = [{"param": "stages", "upload_id": uid, "columns": {"year": "year", "peak_mw": "peak (kW)"}},
                 {"param": "site_type", "turn_id": tid, "quote": "It is a hospital", "id": "hospital"},
                 {"param": "connection_mw", "turn_id": tid, "quote": "Grid connection is 12 MW", "unit": "MW"}]
    return {"upload": uid, "dir": acl.dir, "citations": citations, "owner": owner}


def _start(host, name, citations=None, request_id="run-intake"):
    return start(request_id=request_id, run={**RUN, "intake": {"project_name": name,
                                                              "citations": citations or host["citations"]}})


def _count(name):
    with db_session.SessionLocal() as db:
        return db.query(Project).filter(Project.name == name).count()


def _new_task(intent):
    with db_session.SessionLocal() as db:
        new_dir = project_registry.project_dir(db.get(Project, uuid.UUID(intent["project_id"])))
    path = new_dir / "assistant_tasks" / (intent["task_id"] + ".json")
    return path, json.loads(path.read_text())


def test_intake_puts_the_rows_and_the_setup_entry_on_the_plan_card_and_writes_nothing(host, session):
    name = f"Intake {uuid.uuid4().hex[:6]}"
    view = _start(host, name)
    intake = view["run"]["intake"]
    params = {r["param"]: r["value"] for r in intake["card"]["rows"]}
    assert params["connection_mw"] == 12.0 and params["site_type"] == "hospital"
    assert intake["missing"] == [] and "request" not in intake
    assert view["envelope"]["setup"] == {"tool": "create_project_from_template", "template": "eh_site",
                                         "name": name, "params_digest": intake["digest"]}
    assert view["envelope"]["state"] == "proposed"
    assert _count(name) == 0  # no project, no write, no task there before the click


def test_grant_creates_one_project_hands_over_and_grants_the_run_there(host, session, site):
    name = f"Intake {uuid.uuid4().hex[:6]}"
    view = _start(host, name)
    digest = view["run"]["intake"]["digest"]
    before = PyPSAService.get_active_context()
    uuid_before, network_before = before.project_uuid, before.network
    message = run_envelope.decide(session, view["task_id"], "unattended", intake_digest=digest)
    assert f"project `{name}` was created" in message
    after = PyPSAService.get_active_context()  # R2: the foreground never moved
    assert after is before and after.project_uuid == uuid_before and after.network is network_before
    assert _count(name) == 1
    host_path = task_path(site[1], view)
    host_task = json.loads(host_path.read_text())
    assert (host_task["status"], host_task["run"]["reason"]) == ("completed", "handed_over")
    intent = run_handover.read_intent(host_path)
    assert intent["state"] == "started" and intent["digest"] == digest
    new_path, new_task = _new_task(intent)
    origin = new_task["run"]["origin"]
    assert origin == {"project_id": site[0], "task_id": view["task_id"], "decision_id": intent["decision_id"],
                      "digest": digest}
    envelope = json.loads(run_envelope.path_for(new_path).read_text())
    assert envelope["state"] == "granted" and envelope["project_id"] == intent["project_id"]
    assert envelope["handover"] == {"decision_id": intent["decision_id"], "digest": digest}
    assert envelope["chain"][0]["fingerprint"] == new_task["fingerprint"] == envelope["fingerprint"]
    assert not run_envelope._handover_unbound(envelope, new_task)
    with db_session.SessionLocal() as db:
        new_dir = project_registry.project_dir(db.get(Project, uuid.UUID(intent["project_id"])))
    meta = upload_service.get_upload_meta(name, host["upload"], project_dir=new_dir)
    assert meta.file_id == host["upload"]  # same id, same bytes (content-addressed)
    with pytest.raises(HTTPException):
        run_envelope.decide(session, view["task_id"], "unattended", intake_digest=digest)
    assert _count(name) == 1  # a repeated grant creates no second project


def test_a_stale_digest_is_refused_and_creates_nothing(host, session):
    name = f"Intake {uuid.uuid4().hex[:6]}"
    view = _start(host, name)
    with pytest.raises(HTTPException) as exc:
        run_envelope.decide(session, view["task_id"], "unattended", intake_digest="0" * 64)
    assert exc.value.detail["error_kind"] == "intake_stale"
    assert _count(name) == 0


def test_a_source_changed_after_the_card_is_refused(host, session, site):
    name = f"Intake {uuid.uuid4().hex[:6]}"
    view = _start(host, name)
    blob = upload_service.get_upload_bytes(host["dir"].name, host["upload"], project_dir=host["dir"])
    path = next(p for p in host["dir"].rglob("*") if p.is_file() and p.read_bytes() == blob)
    path.write_bytes(blob + b"2033,9000\n")  # R6: the bytes the card read are gone
    with pytest.raises(HTTPException) as exc:
        run_envelope.decide(session, view["task_id"], "unattended", intake_digest=view["run"]["intake"]["digest"])
    assert exc.value.detail["error_kind"] == "intake_stale"
    assert _count(name) == 0


def test_a_missing_required_input_is_listed_once_and_the_grant_is_refused(host, session):
    name = f"Intake {uuid.uuid4().hex[:6]}"
    view = _start(host, name, citations=host["citations"][:2])
    intake = view["run"]["intake"]
    assert [i["param"] for i in intake["missing"]] == ["connection_mw"]
    assert "connection_mw" in intake["request"] and "MW" in intake["request"]
    with pytest.raises(HTTPException) as exc:
        run_envelope.decide(session, view["task_id"], "unattended", intake_digest=intake["digest"])
    assert exc.value.detail["error_kind"] == "intake_missing"
    assert _count(name) == 0


def test_a_citation_carrying_a_value_is_refused(host, session):
    with pytest.raises(HTTPException) as exc:
        _start(host, "Valued", citations=[dict(host["citations"][2], value=12)])
    assert exc.value.detail["error_kind"] == "intake_value_supplied"


def test_inputs_moved_since_the_brief_refuse_the_grant_under_the_lock(host, session, monkeypatch):
    name = f"Intake {uuid.uuid4().hex[:6]}"
    view = _start(host, name)
    monkeypatch.setattr(tasks, "_stale", lambda task, current=None: True)
    with pytest.raises(HTTPException) as exc:
        run_envelope.decide(session, view["task_id"], "unattended", intake_digest=view["run"]["intake"]["digest"])
    assert exc.value.detail["error_kind"] == "task_brief_stale"
    assert _count(name) == 0


# ── A2: the new project's envelope is the host card's grant ────────────────


@pytest.mark.parametrize("bound, origin", [
    (None, {"project_id": "h", "task_id": "t", "decision_id": "envelope-1", "digest": "d"}),  # self-grant
    ({"decision_id": "envelope-2", "digest": "d"}, {"decision_id": "envelope-1", "digest": "d"}),
    ({"decision_id": "envelope-1", "digest": "e"}, {"decision_id": "envelope-1", "digest": "d"}),
    ({"decision_id": "envelope-1", "digest": "d"}, None),  # a hand-over grant on another run
    ({"decision_id": None, "digest": None}, {"decision_id": None, "digest": None}),
])
def test_a_handed_over_run_without_the_matching_decision_and_digest_is_refused(bound, origin):
    envelope = {"handover": bound} if bound is not None else {}
    task = {"run": {"origin": origin}} if origin is not None else {"run": {}}
    assert run_envelope._handover_unbound(envelope, task)
    assert "handover_unbound" in run_envelope.PAUSING


def test_an_unbound_hand_over_envelope_refuses_every_admission(granted, session):
    task, path, _ = granted()
    record = json.loads(path.read_text())
    record["run"]["origin"] = {"project_id": "h", "task_id": "t", "decision_id": "envelope-1", "digest": "d"}
    path.write_text(json.dumps(record))
    envelope = json.loads(run_envelope.path_for(path).read_text())
    with on_run(task, path) as run:
        reason = run_envelope._standing(run, envelope, record, tasks.RUN_CONTEXT.get())
    assert reason == "handover_unbound"


# ── §3: one data request when L1 ends ──────────────────────────────────────


def _gap(name, attr):
    return {"target": {"component": "Load", "name": name, "attr": attr}, "label": f"{name} {attr}",
            "tool": "apply_generated_profile"}


def test_l1_exhaustion_with_two_gaps_gives_one_data_request_and_never_asks_twice():
    task = {"run": {}}
    req = run_loops.data_request(task, [_gap("L1", "p_set"), _gap("L2", "p_set")])
    assert task["run"]["waiting"]["kind"] == "data" and task["run"]["waiting"]["ref"] == req["id"]
    assert [i["label"] for i in task["run"]["waiting"]["items"]] == ["L1 p_set", "L2 p_set"]
    assert req["notice"].count("\n- ") == 2  # one line per item, in one notice
    assert run_loops.data_request(task, [_gap("L1", "p_set")]) is None  # asked once per run
    again = run_loops.data_request(task, [_gap("L1", "p_set"), _gap("L3", "p_set")])
    assert [i["label"] for i in again["items"]] == ["L3 p_set"]


def test_a_gap_l1_filled_is_not_listed_and_a_missing_required_input_is(monkeypatch):
    envelope = {"gaps": [{"tool": "apply_generated_profile", "target": {"component": "Load", "name": "L*",
                                                                        "attr": "p_set"}}]}
    monkeypatch.setattr(run_envelope, "read", lambda path: envelope)
    monkeypatch.setattr(run_loops, "_unset", lambda path, ctx: (lambda target: False))
    profile = {"inputs": {"required": ["load_series", "tariff"]}}
    task = {"run": {"intake": {"rows": [{"param": "tariff", "status": "confirmed"}]}}}
    items = run_loops.data_gaps("p", task, profile, None)
    assert items == [{"param": "load_series", "label": "load_series"}]


# ── §4, B1-B4: card writes answering the request ───────────────────────────


@pytest.fixture
def waiting(granted, session):
    task, path, _ = granted()
    record = json.loads(path.read_text())
    req = run_answers.request([], [{"target": {"component": "Generator", "name": "G1", "attr": "p_nom"},
                                    "label": "G1 p_nom"},
                                   {"target": {"component": "Generator", "name": "G1", "attr": "marginal_cost"},
                                    "label": "G1 marginal_cost"}])
    record["run"].update(status="needs_user", requests=[req], waiting={"kind": "data", "ref": req["id"]})
    path.write_text(json.dumps(record))
    head = json.loads(run_envelope.path_for(path).read_text())["fingerprint"]
    return path, record, head


def _answer(path, record, before, diff, after="fp-after", decision="call-1"):
    return run_envelope.settle_answer(path, actor_id=record["owner_id"], project_id=record["project_id"],
                                      decision={"id": decision, "by": "user"}, diff=diff, before=before,
                                      after={"fingerprint": after, "exact": True, "network_revision": None})


def test_two_answers_go_on_the_chain_and_complete_the_request(waiting):
    path, record, head = waiting
    first = _answer(path, record, head, [("Generator", "G1", "p_nom")], after="fp-1")
    assert first["answers"] == record["run"]["requests"][0]["id"] and not first["complete"]
    assert first["decision_id"] == "call-1"
    second = _answer(path, record, "fp-1", [("Generator", "G1", "marginal_cost")], after="fp-2", decision="call-2")
    assert second["complete"]
    envelope = json.loads(run_envelope.path_for(path).read_text())
    assert [link["fingerprint"] for link in envelope["chain"]][-2:] == ["fp-1", "fp-2"]
    task = json.loads(path.read_text())
    assert len(task["run"]["answers"]) == 2 and task["run"]["waiting"] is None


def test_an_item_plus_another_field_is_drift(waiting):
    path, record, head = waiting
    assert _answer(path, record, head, [("Generator", "G1", "p_nom"), ("Generator", "G1", "p_min_pu")]) is None
    envelope = json.loads(run_envelope.path_for(path).read_text())
    assert envelope["chain"] == [] and envelope["audit"][-1]["reason"] == "outside_the_request"


def test_a_write_between_the_chain_head_and_the_answer_is_drift(waiting):
    path, record, _ = waiting
    assert _answer(path, record, "fp-someone-else", [("Generator", "G1", "p_nom")]) is None
    envelope = json.loads(run_envelope.path_for(path).read_text())
    assert envelope["chain"] == [] and envelope["audit"][-1]["reason"] == "chain_moved"


def test_another_users_write_is_not_an_answer(waiting):
    path, record, head = waiting
    assert run_envelope.settle_answer(path, actor_id=str(uuid.uuid4()), project_id=record["project_id"],
                                      decision={"id": "c", "by": "user"}, diff=[("Generator", "G1", "p_nom")],
                                      before=head, after={"fingerprint": "x"}) is None
