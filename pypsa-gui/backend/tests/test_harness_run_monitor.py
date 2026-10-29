"""Study harness issue 08: the run monitor's backend (`services/run_monitor.py`,
the `/api/chat/runs/{task_id}` routes) and the runner stop it needs.

Real project, real runner, the ordinary dispatch path; the routes are called
as the companion calls them."""
# ruff: noqa: F811 -- imported pytest fixtures
import json

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
    view = client.get(f"/api/chat/runs/{task['task_id']}")
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
    budgets = client.get(f"/api/chat/runs/{task['task_id']}").json()["monitor"]["budgets"]
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
        assert client.get(f"/api/chat/runs/{target}").json()["detail"]["error_kind"] == "task_not_found"
        for action in ("present", "pause", "resume", "stop"):
            response = client.post(f"/api/chat/runs/{target}/{action}")
            assert response.status_code == 404 and response.json()["detail"]["error_kind"] == "task_not_found"
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
    view = client.get(f"/api/chat/runs/{task['task_id']}").json()
    assert view["monitor"]["waiting"] == {"kind": "card", "ref": "inputs.2"} and "card" not in view["monitor"]
    assert client.post(f"/api/chat/runs/{task['task_id']}/present").json() == {"ok": True}
    runner.tick(path)
    wait_until(lambda: "card" in client.get(f"/api/chat/runs/{task['task_id']}").json()["monitor"], 30)
    card = client.get(f"/api/chat/runs/{task['task_id']}").json()["monitor"]["card"]
    assert card["tool_name"] == "update_component" and card["session_id"] == "run:" + task["task_id"]
    assert card["edit_card"] and card["expires_at_epoch_ms"] > 0
    answer = client.post(f"/api/chat/run:{task['task_id']}/confirm",
                         json={"token": card["confirmation_token"], "decision": "approve"})
    assert answer.status_code == 200, answer.text
    runner._inflight[task["task_id"]][1].join(60)
    assert PyPSAService.get_active_context().network.generators.at["G1", "p_nom"] == 25.0
    assert "card" not in client.get(f"/api/chat/runs/{task['task_id']}").json()["monitor"]
