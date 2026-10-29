"""Study harness issue 08: the run monitor's backend (`services/run_monitor.py`,
the `/api/chat/runs/{task_id}` routes) and the runner stop it needs.

Real project, real runner, the ordinary dispatch path; the routes are called
as the companion calls them."""
# ruff: noqa: F811 -- imported pytest fixtures
import json
import re
import uuid
from pathlib import Path

import pytest

from harness import confirm, runner as harness_runner
from services import chat_tools
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline, dispatch, edit, helpers_state, session, wait_until,
)
from tests.test_harness_run_skeleton import site, start  # noqa: F401
from tests.test_harness_runner import Clicks, detached, frames_of, settle, task_path  # noqa: F401


def preview_step(n=2):
    return {"id": f"inputs.{n}", "tool": "preview_project_changes", "args": {"changes": [
        {"component_class": "Generator", "names": ["G1"], "attribute": "p_nom", "value": 22.0}]}}


def with_steps(path, steps):
    record = json.loads(path.read_text())
    record["steps"] += [dict(s, status="pending") for s in steps]
    path.write_text(json.dumps(record))


# ── SH 03 follow-up: an uncarded step on a stale brief stops the run ────────


def test_an_uncarded_step_on_a_stale_brief_stops_the_run_instead_of_redispatching(detached, site):
    """Without a live envelope, an uncarded step the plan refuses as
    `task_brief_stale` used to be dispatched again on every tick; the plan
    card would say "running" for ever. It stops `needs_user`, `brief_stale`."""
    project_id, root = site
    task = detached()
    path = task_path(root, task)
    with_steps(path, [preview_step()])
    assert "preview_project_changes" in confirm.GUIDED_UNCARDED
    chat_tools.set_acting_user(None)
    PyPSAService.get_active_context().network.loads.at["L1", "p_set"] = 6.0  # a user edit: the brief is stale
    runner = harness_runner.Runner("a", decisions=Clicks(refs=["inputs"]))
    record = settle(runner, path, task["task_id"], lambda r: r["run"]["status"] != "running", limit=12)
    assert record["run"]["status"] == "needs_user" and record["run"]["reason"] == "brief_stale", record["run"]
    for _ in range(3):
        runner.tick(path)
    calls = [f for f in frames_of(task["task_id"], record["owner_id"])
             if f["frame"] == "tool_error" and f.get("error_kind") == "task_brief_stale"]
    assert len(calls) == 1


# ── The plan card's routes ───────────────────────────────────────────────────


def edit_step(n, value):
    return {"id": f"inputs.{n}", "tool": "update_component",
            "args": {"component_class": "Generator", "name": "G1", "attrs": {"p_nom": value}}}


def test_the_view_carries_stages_envelope_and_the_monitor(client, session, site):
    task = start()
    view = client.get(f"/api/chat/runs/{task["task_id"]}/view")
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["run"]["profile"] == "eh_site@1" and body["envelope"]["state"] == "proposed"
    assert [s["stage"] for s in body["run"]["stages"]][:3] == ["intake", "setup", "inputs"]
    monitor = body["monitor"]
    assert monitor["budgets"]["cost_eur"] == {"used": 0.0, "limit": 5.0, "unmetered": False}
    assert "solves" not in monitor["budgets"]  # nothing granted
    # Under pytest the runner is off: unattended cannot work, and says why.
    assert monitor["unattended"] == {"available": False, "reason": "runner_off"}
    from services import email_service
    assert monitor["email"] == {"available": email_service.get_status()["configured"]} and "card" not in monitor


def test_unattended_is_available_only_with_the_runner_a_provider_and_the_edit_right(client, session, site, monkeypatch):
    from services import run_monitor
    task = start()
    monkeypatch.setenv("PYPSA_GUI_HARNESS_RUNNER", "1")
    assert run_monitor.view(task["task_id"], provider_ready=False)["monitor"]["unattended"]["reason"] == "no_provider"
    assert run_monitor.view(task["task_id"], provider_ready=True)["monitor"]["unattended"] == {"available": True}
    monkeypatch.setattr(tasks_module(), "_may_write", lambda task: False)
    assert run_monitor.view(task["task_id"], provider_ready=True)["monitor"]["unattended"]["reason"] == "locked"


def tasks_module():
    from services import assistant_tasks
    return assistant_tasks


def test_a_granted_envelope_shows_solves_used_and_expiry(client, session, site):
    from services import run_envelope
    task = start()
    run_envelope.decide(session, task["task_id"], "unattended")
    budgets = client.get(f"/api/chat/runs/{task["task_id"]}/view").json()["monitor"]["budgets"]
    assert budgets["solves"] == {"used": 0, "of": 6} and budgets["expires_at"]


def test_an_unknown_foreign_or_unreachable_run_is_task_not_found_and_nothing_changes(client, session, site):
    """SC-16: a run the caller no longer owns reads as unknown, and no click on it has an effect."""
    project_id, root = site
    task = start()
    path = task_path(root, task)
    record = json.loads(path.read_text())
    record["owner_id"] = "00000000-0000-0000-0000-000000000001"  # the caller lost it
    path.write_text(json.dumps(record))
    before = path.read_text()
    for target in (task["task_id"], "00000000-0000-4000-8000-000000000000", "not-a-task"):
        assert client.get(f"/api/chat/runs/{target}/view").json()["detail"]["error_kind"] == "task_not_found"
        for action in ("present", "pause", "resume", "stop"):
            response = client.post(f"/api/chat/runs/{target}/{action}")
            assert response.status_code == 404 and response.json()["detail"]["error_kind"] == "task_not_found"
    assert path.read_text() == before


RUN_ACTIONS = ("present", "pause", "resume", "stop", "confirm")


def run_call(client, task_id, action):
    """One click on the plan card, as the companion sends it."""
    if action == "view":
        return client.get(f"/api/chat/runs/{task_id}/view")
    if action == "confirm":
        return client.post(f"/api/chat/run:{task_id}/confirm", json={"token": "t" * 16, "decision": "approve"})
    return client.post(f"/api/chat/runs/{task_id}/{action}")


def lose_owner(path):
    record = json.loads(path.read_text())
    record["owner_id"] = "00000000-0000-0000-0000-000000000001"
    path.write_text(json.dumps(record))


@pytest.fixture
def member_only(seeded_identity, second_identity, _auth_db):
    """The caller reaches the project only through a project membership row:
    an org member (not admin) who did not create it. Yields (add, remove)."""
    from datetime import datetime, timezone

    from sqlalchemy import delete, update

    from db.models import OrgMembership, Project, ProjectMembership
    _engine, session_local = _auth_db
    me = seeded_identity["user_id"]

    def add(project_id):
        with session_local() as db:
            db.execute(update(OrgMembership).where(OrgMembership.user_id == me).values(role="member"))
            db.execute(update(Project).where(Project.id == uuid.UUID(project_id))
                       .values(created_by=second_identity["user_id"]))
            db.add(ProjectMembership(project_id=uuid.UUID(project_id), user_id=me, assigned_by=me,
                                     assigned_at=datetime.now(tz=timezone.utc)))
            db.commit()

    def remove(project_id):
        with session_local() as db:  # the real membership row, deleted
            gone = db.execute(delete(ProjectMembership).where(ProjectMembership.user_id == me,
                                                              ProjectMembership.project_id == uuid.UUID(project_id)))
            assert gone.rowcount == 1
            db.commit()
    try:
        yield add, remove
    finally:
        with session_local() as db:
            db.execute(update(OrgMembership).where(OrgMembership.user_id == me).values(role="admin"))
            db.commit()


def delete_project(project_id):
    from sqlalchemy import delete

    from db.models import Project
    from db.session import SessionLocal
    with SessionLocal() as db:
        assert db.execute(delete(Project).where(Project.id == uuid.UUID(project_id))).rowcount == 1
        db.commit()


@pytest.mark.parametrize("case", ["unknown", "foreign", "removed", "deleted"])
def test_a_run_the_caller_cannot_reach_is_the_same_task_not_found_on_every_route(client, session, site,
                                                                                 member_only, case):
    """GK B (1), SC-16: an unknown run, another user's, one in a project the
    caller was removed from (a real membership-row delete) and one in a
    deleted project are told apart by nothing: the same 404 bytes on the view
    and on every click, and nothing changes."""
    project_id, root = site
    task = start()
    path = task_path(root, task)
    unknown = "00000000-0000-4000-8000-000000000000"
    expected = {a: run_call(client, unknown, a) for a in ("view", *RUN_ACTIONS)}
    assert all(r.status_code == 404 and r.json()["detail"]["error_kind"] == "task_not_found"
               for r in expected.values()), {a: r.text for a, r in expected.items()}
    target = unknown if case == "unknown" else task["task_id"]
    if case == "foreign":
        lose_owner(path)
    elif case == "removed":
        add, remove = member_only
        add(project_id)
        assert run_call(client, target, "view").status_code == 200  # reached through the membership alone
        remove(project_id)
    elif case == "deleted":
        delete_project(project_id)
    before = path.read_text()
    for action in ("view", *RUN_ACTIONS):
        response = run_call(client, target, action)
        assert (response.status_code, response.content) == (404, expected[action].content), (action, response.text)
    assert path.read_text() == before


def test_pause_stops_the_runner_and_resume_goes_on_with_the_envelope_still_granted(client, session, site, detached):
    from services import run_envelope
    project_id, root = site
    task = start()
    run_envelope.decide(session, task["task_id"], "unattended")
    path = task_path(root, task)
    paused = client.post(f"/api/chat/runs/{task['task_id']}/pause").json()
    assert paused["run"]["status"] == "paused" and paused["run"]["reason"] == "user"
    assert paused["envelope"]["state"] == "granted"
    chat_tools.set_acting_user(None)
    harness_runner.Runner("a").tick(path)  # the runner leaves a paused run alone
    assert json.loads(path.read_text())["run"]["status"] == "paused"
    again = client.post(f"/api/chat/runs/{task['task_id']}/pause")
    assert again.json()["detail"]["error_kind"] == "run_not_pausable"
    resumed = client.post(f"/api/chat/runs/{task['task_id']}/resume").json()
    assert resumed["run"]["status"] == "running" and "reason" not in resumed["run"]
    assert resumed["envelope"]["state"] == "granted"


def test_stop_revokes_the_envelope_cancels_the_run_and_keeps_what_was_applied(client, session, site):
    from services import run_envelope
    project_id, root = site
    task = start()
    run_envelope.decide(session, task["task_id"], "unattended")
    PyPSAService.get_active_context().network.generators.at["G1", "p_nom"] = 25.0  # an edit applied so far
    stopped = client.post(f"/api/chat/runs/{task['task_id']}/stop").json()
    assert stopped["status"] == "cancelled" and stopped["run"]["status"] == "cancelled"
    assert stopped["envelope"]["state"] == "revoked"
    assert PyPSAService.get_active_context().network.generators.at["G1", "p_nom"] == 25.0
    assert client.post(f"/api/chat/runs/{task['task_id']}/pause").json()["detail"]["error_kind"] == "run_not_pausable"


def test_a_card_the_run_waits_on_is_issued_when_the_user_opens_it(client, session, site, detached, monkeypatch):
    """Issue 08 AC: the waiting card is issued when the user opens the
    companion, its TTL starting then; the plan card answers it through the
    ordinary /confirm on the run's own session."""
    project_id, root = site
    task = detached()
    path = task_path(root, task)
    with_steps(path, [edit_step(2, 25.0)])
    chat_tools.set_acting_user(None)
    runner = harness_runner.Runner("a", decisions=Clicks(refs=["inputs"]))
    monkeypatch.setattr(harness_runner, "_DEFAULT", runner)
    record = settle(runner, path, task["task_id"], lambda r: r["run"]["status"] == "needs_user", limit=12)
    assert record["run"]["waiting"]["ref"] == "inputs.2"
    view = client.get(f"/api/chat/runs/{task["task_id"]}/view").json()
    assert view["monitor"]["waiting"] == {"kind": "card", "ref": "inputs.2"} and "card" not in view["monitor"]
    assert client.post(f"/api/chat/runs/{task['task_id']}/present").json() == {"ok": True}
    runner.tick(path)
    wait_until(lambda: "card" in client.get(f"/api/chat/runs/{task["task_id"]}/view").json()["monitor"], 30)
    card = client.get(f"/api/chat/runs/{task["task_id"]}/view").json()["monitor"]["card"]
    assert card["tool_name"] == "update_component" and card["session_id"] == "run:" + task["task_id"]
    assert card["edit_card"] and card["expires_at_epoch_ms"] > 0
    answer = client.post(f"/api/chat/run:{task['task_id']}/confirm",
                         json={"token": card["confirmation_token"], "decision": "approve"})
    assert answer.status_code == 200, answer.text
    runner._inflight[task["task_id"]][1].join(60)
    assert PyPSAService.get_active_context().network.generators.at["G1", "p_nom"] == 25.0
    assert "card" not in client.get(f"/api/chat/runs/{task["task_id"]}/view").json()["monitor"]


# ── Word-map parity (UX condition 2): every key the card shows has words ─────

WORDS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "runMonitor" / "runWords.ts"


def words_keys(name):
    text = WORDS.read_text()
    start = text.index(f"export const {name}")
    body = text[text.index("= {", start) + 3:text.index("\n}", start)]
    body = re.sub(r"\{[^{}]*\}", "{}", body)
    return {a or b for a, b in re.findall(r"(?:'([^']+)'|([A-Za-z_]\w*))\s*:\s*['{]", body)}


def test_the_plan_card_names_every_stage_and_gate_fact_in_words():
    from services import run_gates, run_profiles
    assert words_keys("STAGE_WORDS") == set(run_profiles.STAGES)
    assert set(run_gates.FACTS) <= words_keys("FACT_WORDS")


def test_the_envelope_section_names_every_profile_edit_and_run_in_words():
    from services import run_envelope, run_profiles
    components, names, fields, runs = (words_keys(n) for n in ("COMPONENT_WORDS", "NAME_WORDS", "FIELD_WORDS", "RUN_WORDS"))
    assert run_envelope.RUN_TOOLS <= runs
    for ref in run_profiles.PROFILES:
        envelope = run_profiles.resolve(ref).get("envelope")
        for edit in (envelope or {}).get("edits", []):
            target = edit["target"]
            assert target["component"] in components and target["name"] in names and target["attr"] in fields, (ref, target)
        for run in (envelope or {}).get("runs", []):
            assert run["tool"] in runs, (ref, run)


def test_every_reason_unattended_cannot_work_has_words():
    from services import run_monitor
    source = Path(run_monitor.__file__).read_text()
    assert set(re.findall(r'return "([a-z_]+)"', source)) == words_keys("UNATTENDED_BLOCK_WORDS")
