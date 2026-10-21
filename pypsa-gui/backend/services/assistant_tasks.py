"""Durable checkpoints for ordinary, independently authorized tool dispatches."""
from __future__ import annotations

import copy
import inspect
import json
import os
import re
import threading
import uuid
from pathlib import Path

from fastapi import HTTPException
from services.pypsa_service import PyPSAService

_LOCK = threading.RLock()
_WORKERS = {}
_CONTROLS = frozenset({"start_task", "get_task", "resume_task", "cancel_task", "resolve_task_step", "list_tasks"})
_REF = re.compile(r"^\$([A-Za-z][A-Za-z0-9_-]{0,31})\.([A-Za-z0-9_.-]+)$")
# LC-23 study brief (owner decisions AI-567b, COP-1): a plan labelled with an
# objective, cards in scope, cases and one phase per card.
CARDS = ("capacity", "grid", "control", "reliability", "commercial", "hand-off")
_LIMITS = {False: (12, 16000), True: (24, 32000)}  # (steps, step chars) without/with a brief
ARGS_CHARS = 1800
RESULT_CHARS = 4000  # the model-facing result cap, harness.results._RESULT_CONTENT_CAP
_WAIT_TOOLS = frozenset({"run_simulation", "run_ac_pf_stage"})
# Queued runs wait on their solve-queue job(s) (`harness/continuation.py`).
QUEUE_WAIT_TOOLS = frozenset({"run_sensitivity_sweep", "solve_queue_enqueue"})
_QUEUE_FOLLOWERS = set()  # (task_id, step_id, attempt_id) of steps this process follows


def _refuse(kind, message, status=409):
    raise HTTPException(status, {"error_kind": kind, "message": message})


def _authority():
    from services import chat_tools, project_registry
    ctx = PyPSAService.get_active_context()
    if not ctx.loaded_project or not ctx.project_uuid:
        _refuse("task_project_required", "Create, save and activate a project before using tasks.")
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, ctx.project_uuid)
        return project_registry.project_dir(project), str(project.id), str(user.id)


def _path(task_id):
    try:
        if str(uuid.UUID(task_id)) != task_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        _refuse("task_not_found", "Task not found in this project.", 404)
    root, project_id, owner = _authority()
    return root / "assistant_tasks" / (task_id + ".json"), project_id, owner


def _read(task_id):
    path, project, owner = _path(task_id)
    try:
        task = json.loads(path.read_text())
    except FileNotFoundError:
        _refuse("task_not_found", "Task not found in this project.", 404)
    except (OSError, ValueError):
        _refuse("task_unreadable", "The task checkpoint needs repair.")
    if task.get("project_id") != project or task.get("owner_id") != owner:
        _refuse("task_not_found", "Task not found in this project.", 404)
    return path, task


def _save(path, task):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("w") as stream:
            json.dump(task, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _resolve(value, steps):
    if isinstance(value, str) and value.startswith("$"):
        match = _REF.fullmatch(value)
        if not match:
            _refuse("invalid_task_reference", "Use a whole-value reference such as $solve.job_id.", 422)
        step = next((s for s in steps if s["id"] == match[1]), None)
        if step is None or step["status"] != "completed":
            _refuse("task_dependency_pending", "Referenced step has not completed.")
        result = step.get("result")
        try:
            for key in match[2].split("."):
                result = result[int(key)] if isinstance(result, list) else result[key]
        except (KeyError, IndexError, TypeError, ValueError):
            _refuse("task_reference_missing", "The saved result does not contain the requested reference.")
        return copy.deepcopy(result)
    if isinstance(value, dict):
        return {k: _resolve(v, steps) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, steps) for v in value]
    return value


def _next(task):
    return next((s for s in task["steps"] if s["status"] != "completed"), None)


def _check_brief(brief, ids):
    """The brief labels the steps; it grants nothing and adds no step."""
    def bad(message):
        _refuse("invalid_brief", message, 422)
    if not isinstance(brief, dict) or not {"objective", "cards", "phases"} <= set(brief) <= {"objective", "cards", "cases", "phases"}:
        bad("A brief has objective, cards, phases and optional cases.")
    objective, cards, cases, phases = brief["objective"], brief["cards"], brief.get("cases", []), brief["phases"]
    if not isinstance(objective, str) or not 1 <= len(objective.strip()) <= 240:
        bad("State the objective in 1–240 characters.")
    if not isinstance(cards, list) or not 1 <= len(cards) <= len(CARDS) or len(set(cards)) != len(cards) \
            or any(card not in CARDS for card in cards):
        bad("Cards are distinct names from: " + ", ".join(CARDS) + ".")
    if not isinstance(cases, list) or len(cases) > 8 or any(
            not isinstance(c, dict) or not {"name"} <= set(c) <= {"name", "experiment"}
            or not isinstance(c["name"], str) or not 1 <= len(c["name"].strip()) <= 60
            or not (c.get("experiment") is None or isinstance(c["experiment"], str) and len(c["experiment"]) <= 64)
            for c in cases):
        bad("Name at most 8 cases (1–60 characters each).")
    if not isinstance(phases, list) or not phases or any(
            not isinstance(p, dict) or set(p) != {"card", "steps"} or p["card"] not in cards
            or not isinstance(p["steps"], list) or not p["steps"] for p in phases):
        bad("Each phase names one declared card and its steps.")
    if [step for phase in phases for step in phase["steps"]] != ids:
        bad("Phases must cover every step once, in step order.")
    return {"objective": objective.strip(), "cards": list(cards),
            "cases": [{"name": c["name"].strip(), "experiment": c.get("experiment")} for c in cases],
            "phases": [{"card": p["card"], "steps": list(p["steps"])} for p in phases]}


def _check_runnable(brief, steps):
    """A brief runs only what execution admits (issue 23, SC-10): its run
    steps wait by themselves, and its cases fit one 1–2 network-case
    experiment (`experiment_execution.runnable`, the advance rule)."""
    from harness.experiment_execution import RUNNABLE_RULE, runnable
    if any(step["tool"] == "wait_for_job" for step in steps):
        _refuse("invalid_task_tool", "Under a brief a run step waits by itself; drop the wait_for_job step.", 422)
    asked = [(step["args"].get("domain", "network"), len(step["args"].get("cases") or []))
             for step in steps if step["tool"] == "start_experiment"]
    if (brief["cases"] and not runnable("network", len(brief["cases"]))) or any(not runnable(*a) for a in asked):
        _refuse("brief_cases_not_runnable", RUNNABLE_RULE + " Propose the first two network cases as this brief "
                "and the rest as a follow-up brief.", 422)


def _limit(tool):
    """Each step's packet limit, from the server, never from the model."""
    return {"args_chars": ARGS_CHARS, "result_chars": RESULT_CHARS, "wait": "job" if tool in _WAIT_TOOLS | QUEUE_WAIT_TOOLS else None}


def _fingerprint():
    """The project's input fingerprint (solver outputs excluded), or None."""
    from services import workflow_tools
    try:
        return workflow_tools._network_input_fingerprint(PyPSAService.get_active_context())
    except Exception:  # noqa: BLE001 — no network: freshness is unknown, never claimed stale
        return None


def _stale(task, current=None):
    """SC-11: the brief is current only while the inputs are the last plan
    step's own. Inputs that cannot be read count as changed (fail closed)."""
    if not task.get("brief") or task["status"] in ("completed", "cancelled"):
        return False
    current = current or _fingerprint()
    return current is None or current != task.get("fingerprint")


def _card_of(task, step_id):
    return next((p["card"] for p in task["brief"]["phases"] if step_id in p["steps"]), None)


def _pause_at_boundary(task, step):
    """COP-1: a plan going on to another card waits for the user's Continue."""
    following = _next(task)
    if task.get("brief") and task["status"] == "ready" and following is not None \
            and step["status"] == "completed" and _card_of(task, step["id"]) != _card_of(task, following["id"]):
        task["status"] = "paused_at_card"
        task["pause"] = {"card": _card_of(task, following["id"]), "after": step["id"]}


def _effectful(tool):
    from harness.catalogue import safety_tier_for
    return safety_tier_for(tool) != "read"


# LC-19 triage reads a failed run step's fix is drafted from. They are offered,
# never run: the fix is an ordinary edit card and the plan goes on only
# through resolve_task_step(retry) with its confirmation.
_TRIAGE = ("get_simulation_log_history", "diagnose_network", "validate_network")


def _correction(step):
    """`next_step.correction` under a brief: proposes, runs nothing."""
    if step.get("job") or step["tool"] in _WAIT_TOOLS | QUEUE_WAIT_TOOLS:
        return {"triage": list(_TRIAGE), "then": "resolve_task_step(retry) after the user approves the fix"}
    if step.get("receipt"):
        return {"reread": step["tool"], "then": "resolve_task_step(completed) re-reads it; else retry"}
    return {"then": "inspect, then resolve_task_step(retry) or cancel_task"}


def _view(task, fresh=False, current=None):
    step = _next(task)
    state = task["status"]
    out = {"task_id": task["task_id"], "title": task["title"], "project_id": task["project_id"],
           "status": state, "completed_steps": sum(s["status"] == "completed" for s in task["steps"]),
           "total_steps": len(task["steps"]),
           "steps": [{k: s[k] for k in ("id", "tool", "status")} for s in task["steps"]]}
    brief = task.get("brief")
    if brief:
        for entry in out["steps"]:
            entry["limit"] = _limit(entry["tool"])
        # Condensed: the current phase's steps; the other phases as counts.
        phases = brief["phases"]
        index = next((i for i, p in enumerate(phases) if step and step["id"] in p["steps"]), len(phases) - 1)
        status = {s["id"]: s["status"] for s in task["steps"]}
        out["brief"] = {"objective": brief["objective"], "cards": brief["cards"],
                        "cases": [c["name"] for c in brief["cases"]],
                        "phase": {"card": phases[index]["card"], "index": index + 1, "of": len(phases)},
                        "phases": [{"card": p["card"], "done": sum(status[i] == "completed" for i in p["steps"]),
                                    "total": len(p["steps"])} for p in phases]}
        out["steps"] = [s for s in out["steps"] if s["id"] in phases[index]["steps"]]
        if state == "paused_at_card":
            out["paused_at_card"] = task["pause"]["card"]
        if fresh and _stale(task, current):
            out["brief_stale"] = "inputs_changed_outside_plan"
    if step and state != "cancelled":
        out["next_step"] = {"id": step["id"], "tool": step["tool"], "status": step["status"]}
        if step["status"] == "waiting":
            out["next_step"]["job_id"] = step["job"]["job_id"]
        elif step["status"] != "uncertain":
            try:
                resolved = _resolve(step["args"], task["steps"])
                if len(json.dumps(resolved, default=str)) > 1800:
                    _refuse("task_reference_too_large", "Resolved arguments exceed the task limit; use file IDs or smaller result references.")
                out["next_step"]["args"] = resolved
            except HTTPException as exc:
                out["next_step"]["blocked"] = exc.detail
        if step.get("error"):
            out["next_step"]["error"] = step["error"]
        if brief and step["status"] in ("failed", "uncertain"):
            out["next_step"]["correction"] = _correction(step)
    changes = [s["result"]["_edit_receipt"]["says"] for s in task["steps"] if s["status"] == "completed"
               and isinstance(s.get("result"), dict) and isinstance(s["result"].get("_edit_receipt"), dict)
               and isinstance(s["result"]["_edit_receipt"].get("says"), str)]
    if changes:
        out["changes"] = changes
    out["note"] = ("Wait for the job; the conversation continues when it ends." if state == "waiting_job" else
                   f"Paused before the {task['pause']['card']} card; the user continues from the plan card." if state == "paused_at_card" else
                   "The inputs changed outside this plan; draft a new brief for the user to review." if out.get("brief_stale") else
                   "Execute next_step as an ordinary offered tool call. Cancel stops future steps, not running jobs.")
    while len(json.dumps(out, default=str)) > 3800 and out["steps"]:
        out["steps"].pop()
        out["step_details_truncated"] = True
    return out


def start_task(title: str, steps: list[dict], request_id: str, brief: dict | None = None) -> dict:
    from harness.catalogue import TOOLS
    from harness.loop import PROJECT_REBINDING_TOOLS
    from services import chat_tools
    session = chat_tools.chat_session()
    if session is None:
        _refuse("no_chat_session", "Starting a task requires a chat session.")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
        _refuse("invalid_task", "Provide a title and a stable request_id (maximum 64 characters).", 422)
    most, chars = _LIMITS[brief is not None]
    if not isinstance(steps, list) or not 1 <= len(steps) <= most:
        _refuse("invalid_task", f"A {'study brief' if brief is not None else 'task'} needs 1–{most} ordered steps; "
                "split a longer goal into a second plan the user reviews.", 422)
    if len(json.dumps(steps, allow_nan=False)) > chars:
        _refuse("invalid_task", f"Task arguments exceed {chars} characters; use file references.", 422)
    catalogue = {t["name"]: t for t in TOOLS}
    ids, checked = set(), []
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"id", "tool", "args"}:
            _refuse("invalid_task", "Each step has exactly id, tool and args.", 422)
        name, tool, args = step["id"], step["tool"], step["args"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", name) or name in ids:
            _refuse("invalid_task", "Use unique short step IDs beginning with a letter.", 422)
        if not isinstance(tool, str) or tool not in catalogue or tool in _CONTROLS or tool == "ask_user" or tool in PROJECT_REBINDING_TOOLS:
            _refuse("invalid_task_tool", "Task tools must be ordinary catalogue tools that do not change project binding.", 422)
        if not isinstance(args, dict) or len(json.dumps(args, allow_nan=False)) > ARGS_CHARS:
            _refuse("invalid_task", "Step args must be an object below 1800 characters; use file IDs.", 422)
        try:
            inspect.signature(chat_tools.DISPATCHERS[tool]).bind(**args)
        except TypeError:
            _refuse("invalid_task_args", f"Arguments do not match {tool}.", 422)
        def references(value):
            if isinstance(value, str) and value.startswith("$"):
                match = _REF.fullmatch(value)
                if not match or match[1] not in ids:
                    _refuse("invalid_task_reference", "References must address an earlier step.", 422)
            elif isinstance(value, (dict, list)):
                for child in (value.values() if isinstance(value, dict) else value):
                    references(child)
        references(args)
        checked.append({**copy.deepcopy(step), "status": "pending"})
        ids.add(name)
    if brief is not None:
        brief = _check_brief(brief, [s["id"] for s in steps])
        _check_runnable(brief, steps)
    root, project, owner = _authority()
    # Deterministic owner-scoped ID makes a repeated start request idempotent.
    task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{project}:{owner}:{request_id}"))
    path = root / "assistant_tasks" / (task_id + ".json")
    with _LOCK:
        if path.exists():
            _, task = _read(task_id)
            original = [{k: s[k] for k in ("id", "tool", "args")} for s in task["steps"]]
            if original != steps or task["title"] != title.strip() or task.get("brief") != brief:
                _refuse("task_request_conflict", "This request_id already identifies a different plan.")
        else:
            task = {"version": 1, "task_id": task_id, "owner_id": owner, "project_id": project,
                    "title": title.strip(), "status": "ready", "steps": checked}
            if brief is not None:
                # The chain starts at the inputs the user approved the brief on.
                fingerprint = _fingerprint()
                if fingerprint is None:
                    _refuse("brief_inputs_unreadable", "The project's inputs cannot be read, so the brief cannot be kept current.")
                task.update(version=2, brief=brief, fingerprint=fingerprint)
            _save(path, task)
        session.task_id = task_id
        return _view(task)


def get_task(task_id: str) -> dict:
    with _LOCK:
        _, task = _read(task_id)
        return _view(task, fresh=True)


def resume_task(task_id: str) -> dict:
    from services import chat_tools
    session = chat_tools.chat_session()
    if session is None:
        _refuse("no_chat_session", "Resume requires a chat session.")
    with _LOCK:
        path, task = _read(task_id)
        if task["status"] == "cancelled":
            _refuse("task_cancelled", "Create a new plan to restart a cancelled task.")
        if task["status"] == "paused_at_card":
            _refuse("task_pause_needs_user", f"The plan is paused before the {task['pause']['card']} card; "
                    "only the user's Continue on the plan card goes on.")
        _reconcile_lost(path, task)
        step = _next(task)
        if step and step["status"] == "running":
            from harness.session import get_session
            origin = get_session(step.get("session_id"))
            if (origin and origin._turn_in_flight) or (step.get("session_id") == session.session_id and session._turn_in_flight):
                _refuse("task_step_running", "The step is still running; wait for its result.")
            step["status"] = "failed" if _retryable_read(step["tool"]) else "uncertain"
            task["status"] = "ready" if step["status"] == "failed" else "needs_review"
            _save(path, task)
        session.task_id = task_id
        return _view(task, fresh=True)


def cancel_task(task_id: str) -> dict:
    from services import chat_tools
    with _LOCK:
        path, task = _read(task_id)
        if task["status"] != "completed":
            task["status"] = "cancelled"
            for step in task["steps"]:
                if step.get("job", {}).get("continuation") in ("armed", "due"):
                    step["job"]["continuation"] = "withdrawn"  # never after a cancel
            _save(path, task)
        session = chat_tools.chat_session()
        if session and getattr(session, "task_id", None) == task_id:
            session.task_id = None
        return _view(task)


def continue_at_card(session, task_id):
    """COP-1: the user's "Continue to <card>" on the plan card. Reached only
    from a `/stream` request the server marks (`continue_task_id`), never
    from a tool call, so the model cannot clear the pause. Grants nothing:
    every card after it still needs its own click."""
    with _LOCK:
        path, task = _read(task_id)
        if task["status"] != "paused_at_card":
            _refuse("task_not_paused", "This plan is not paused at a card.")
        card = task.pop("pause")["card"]
        task["status"] = "ready"
        _save(path, task)
        session.task_id = task_id
        return f"Continue to the {card} card (plan: {task['title']})"


def resolve_task_step(task_id: str, outcome: str, result: dict | None = None) -> dict:
    """Explicitly confirmed reconciliation; never an automatic write retry.

    `completed` never takes the caller's word (SC-12): the step resolves from
    native evidence only, its late worker receipt, its job's published run or
    its edit's fresh read-back. `result` is accepted for compatibility and
    ignored."""
    if outcome not in ("completed", "retry"):
        _refuse("invalid_task_resolution", "Choose completed or retry.", 422)
    result = result or {}
    if len(json.dumps(result, allow_nan=False)) > 4000:
        _refuse("invalid_task_resolution", "Use a compact verified result.", 422)
    with _LOCK:
        path, task = _read(task_id)
        step = _next(task)
        if not step or task["status"] in ("completed", "cancelled") or step["status"] not in ("uncertain", "failed"):
            _refuse("task_resolution_not_needed", "Only a failed or uncertain step can be reconciled.")
        from harness.session import get_session
        origin = get_session(step.get("session_id"))
        worker = _WORKERS.get((task_id, step["id"], step.get("attempt_id")))
        if (origin and origin._turn_in_flight) or worker is not None:  # its receipt is still landing
            _refuse("task_step_running", "Wait for the originating session to stop before reconciliation.")
        if outcome == "completed":
            found, evidence = _native_evidence(path, task, step)
            if not found:
                _refuse("task_resolution_unverifiable", "No native record shows this step's effect; "
                        "inspect it, then retry the step or cancel the plan.")
            step["result"] = evidence
            step["status"] = "completed"
        else:
            step["status"] = "pending"
            step.pop("result", None)
        for key in ("error", "late_receipt", "receipt"):
            step.pop(key, None)
        task["status"] = "ready" if _next(task) else "completed"
        _pause_at_boundary(task, step)
        # The chain never advances on a reconcile: nothing tells the step's own
        # effect from other edits made since it went uncertain, so inputs that
        # differ now read stale and the user reviews a new brief (fail closed).
        _save(path, task)
        return _view(task, fresh=True)


def _native_evidence(path, task, step):
    """(found, result) for a failed or uncertain step, from native records only."""
    if "late_receipt" in step:
        return True, step["late_receipt"]["result"]
    if (step.get("job") or {}).get("kind") == "queue":
        from harness import continuation
        status, _, result = continuation.queue_outcome(step["job"]["jobs"])
        return (True, {"job_id": step["job"]["job_id"], "final_status": "completed", **result}) \
            if status == "completed" else (False, None)
    if step.get("job"):
        from services import run_evidence
        job_id = step["job"]["job_id"]
        run_id = run_evidence.foreground_run_id(job_id)
        record = run_evidence._load_run(path.parent.parent, task["project_id"], run_id)
        objective = ((record or {}).get("metrics") or {}).get("objective", {}).get("value")
        if record and record.get("status") == "completed" and record.get("job_id") == job_id \
                and objective is not None and record.get("fingerprint") \
                and "inputs_changed_before_publish" not in (record.get("warnings") or []):
            return True, {"job_id": job_id, "final_status": "completed", "run_id": run_id,
                          "objective": objective, "fingerprint": record["fingerprint"]}
        return False, None
    if isinstance(step.get("receipt"), dict):
        from harness import edit_card
        try:
            receipt = edit_card.reread(step["tool"], _resolve(step["args"], task["steps"]), step["receipt"])
        except Exception:  # noqa: BLE001 — an unreadable target is no evidence
            return False, None
        if receipt is not None:
            return True, {"_edit_receipt": receipt}
    return False, None


def admit(session, tool, args):
    """Before any card: a bound plan's later steps never run out of order, and
    a step waiting on its job never runs twice. Other calls pass untouched."""
    task_id = getattr(session, "task_id", None)
    if not task_id or tool in _CONTROLS:
        return
    with _LOCK:
        try:
            _, task = _read(task_id)
        except HTTPException:
            return  # begin_step detaches a task this project cannot read
        if task["status"] in ("cancelled", "completed"):
            return
        step = _next(task)
        for later in task["steps"]:
            if later["status"] == "completed" or later["tool"] != tool:
                continue
            try:
                expected = _resolve(later["args"], task["steps"])
            except HTTPException:
                continue  # its arguments still wait on an earlier result
            if not _same_args(tool, expected, args):
                continue
            if later is step and step["status"] == "waiting":
                raise HTTPException(409, {"error_kind": "task_step_waiting", "message":
                                          f"Step {step['id']} is waiting on job {step['job']['job_id']}; "
                                          "the conversation continues when it ends."})
            if later is not step:
                raise HTTPException(409, {"error_kind": "task_step_out_of_order", "message":
                                          f"Step {later['id']} ({tool}) cannot start before step {step['id']} "
                                          f"({step['tool']}) completes; it is {step['status']}."})
            _gate_brief(task, step)
            if len(json.dumps(expected, default=str)) > ARGS_CHARS:
                _refuse("task_reference_too_large", "Resolved arguments exceed the step's limit; use file IDs or smaller result references.")
            return


def _gate_brief(task, step):
    """COP-1 pause and SC-11 freshness, before the plan's next step's card."""
    if task["status"] == "paused_at_card":
        _refuse("task_paused_at_card", f"The plan is paused before the {task['pause']['card']} card; "
                "the user continues from the plan card.")
    if task.get("brief") and step["status"] == "failed" and _effectful(step["tool"]):
        _refuse("task_step_needs_retry", "This step failed; draft the fix from next_step.correction, "
                "then resolve_task_step(retry) reopens it.")
    if _effectful(step["tool"]) and _stale(task):
        _refuse("task_brief_stale", "The inputs changed outside this plan, or could not be read; "
                "draft a new brief for the user to review.")


def await_job(ticket, job_id, session_id):
    """A run step whose solve is still running waits on its job, durably.

    The turn is released; `settle_job` records the outcome when the job ends,
    and the conversation continues once (`claim_continuation`). A queued step
    passes its jobs as a list (`continuation.queue_jobs`); the first one keys
    its continuation."""
    if ticket is None or job_id == []:
        return None
    jobs = job_id if isinstance(job_id, list) else None
    with _LOCK:
        path, task = _read(ticket[0])
        step = next(s for s in task["steps"] if s["id"] == ticket[1])
        if step.get("attempt_id") != ticket[2] or step["status"] != "running":
            return None
        step["status"] = "waiting"
        step["job"] = {"job_id": jobs[0]["job_id"] if jobs else job_id, "session_id": session_id, "continuation": "armed"}
        if jobs:
            step["job"].update(kind="queue", jobs=[dict(j) for j in jobs])
        if task["status"] != "cancelled":
            task["status"] = "waiting_job"
        _save(path, task)
        return path


def settle_job(path, ticket, job_id, native_status, step_status, result=None):
    """Record a waiting step's job outcome (`harness/continuation.py` decides it
    from the job's own record). Runs on the job's follower thread, so it reads
    the task file it was given, not the request's project. Takes only the
    task lock, never the network lock."""
    with _LOCK:
        try:
            task = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            return None
        step = next((s for s in task["steps"] if s["id"] == ticket[1]), None)
        if (task.get("task_id") != ticket[0] or step is None or step.get("attempt_id") != ticket[2]
                or step["status"] != "waiting" or step["job"]["job_id"] != job_id):
            return None
        _settle(task, step, native_status, step_status, result)
        _save(Path(path), task)
        return _view(task)


def _settle(task, step, native_status, step_status, result):
    step["status"] = step_status
    if step_status == "completed":
        step["result"] = {"job_id": step["job"]["job_id"], "final_status": "completed", **(result or {})}
        step.pop("error", None)
    elif step_status == "failed":
        step["error"] = f"The solve ended {native_status}; inspect its log before retrying."
    else:
        step["error"] = "The job's outcome cannot be verified; inspect the solver before retrying."
    step["job"]["outcome"] = native_status
    cancelled = task["status"] == "cancelled"
    stopped = cancelled or native_status == "aborted" or step["job"]["continuation"] == "withdrawn"
    step["job"]["continuation"] = "withdrawn" if stopped else "due"
    if not cancelled:
        task["status"] = ("completed" if not _next(task) else
                          "needs_review" if step_status == "uncertain" else "ready")
        _pause_at_boundary(task, step)


def continued_in_turn(path, ticket):
    """The job settled while its turn still held it: that turn goes on with
    the plan, so no separate continuation follows."""
    with _LOCK:
        task = json.loads(Path(path).read_text())
        step = next((s for s in task["steps"] if s["id"] == ticket[1]), None)
        if step is not None and step.get("attempt_id") == ticket[2] and \
                (step.get("job") or {}).get("continuation") == "due":
            step["job"]["continuation"] = "delivered"
            _save(Path(path), task)


def _owned_tasks():
    root, project, owner = _authority()
    for path in (root / "assistant_tasks").glob("*.json"):
        try:
            task = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if task.get("owner_id") == owner and task.get("project_id") == project:
            yield path, task


def _reconcile_lost(path, task):
    """A waiting job this process never registered was lost with a restart."""
    from harness import job_adapters
    changed = False
    for step in task["steps"]:
        if step["status"] != "waiting":
            continue
        if step["job"].get("kind") == "queue":
            # Followed by a thread of this process, or lost with a restart (SC-09).
            lost = (task["task_id"], step["id"], step.get("attempt_id")) not in _QUEUE_FOLLOWERS
        else:
            lost = not job_adapters.is_registered(step["job"]["job_id"])
        if lost:
            _settle(task, step, "unknown", "uncertain", None)  # lost with the process
            changed = True
    if changed:
        _save(path, task)


def due_continuations(session):
    """Finished jobs this conversation's plans started, not yet continued."""
    out = []
    with _LOCK:
        for path, task in _owned_tasks():
            _reconcile_lost(path, task)
            for step in task["steps"]:
                job = step.get("job") or {}
                if job.get("continuation") == "due" and job.get("session_id") == session.session_id:
                    out.append({"job_id": job["job_id"], "status": job["outcome"], "task_id": task["task_id"],
                                "message": _continuation_message(task, job)})
    return out


def _continuation_message(task, job):
    # The plan's title carries the request's words, so the turn is offered the
    # tools its remaining steps need (the 128-tool selection reads the message).
    return f"job {job['job_id']} finished: {job['outcome']} (plan: {task['title']})"


def dismiss_continuation(session, job_id):
    """"Stop waiting": this conversation will not continue for the job. The
    job itself runs on; nothing is cancelled."""
    with _LOCK:
        for path, task in _owned_tasks():
            for step in task["steps"]:
                job = step.get("job") or {}
                if job.get("job_id") == job_id and job.get("session_id") == session.session_id \
                        and job.get("continuation") in ("armed", "due"):
                    job["continuation"] = "withdrawn"
                    _save(path, task)
                    return True
    return False


def claim_continuation(session, job_id):
    """At most once per job: mark it delivered and rebind the plan. Grants nothing."""
    with _LOCK:
        for path, task in _owned_tasks():
            _reconcile_lost(path, task)
            for step in task["steps"]:
                job = step.get("job") or {}
                if job.get("job_id") != job_id:
                    continue
                if job.get("continuation") != "due" or job.get("session_id") != session.session_id \
                        or task["status"] == "cancelled":
                    break
                job["continuation"] = "delivered"
                _save(path, task)
                session.task_id = task["task_id"]
                return _continuation_message(task, job)
    _refuse("continuation_unavailable", "No finished job of this conversation is waiting to continue.")


def begin_step(session, tool, args):
    """Called only after the ordinary offered-tool and confirmation gates."""
    task_id = getattr(session, "task_id", None)
    if not task_id or tool in _CONTROLS:
        return None
    with _LOCK:
        try:
            path, task = _read(task_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                session.task_id = None  # A legitimate project switch detaches the task.
                return None
            raise
        if task["status"] in ("cancelled", "completed"):
            session.task_id = None
            return None
        step = _next(task)
        expected = _resolve(step["args"], task["steps"]) if step["tool"] == tool else None
        if step["tool"] != tool or not _same_args(tool, expected, args):
            # Discovery and unrelated reads remain useful while a task is paused.
            for done in task["steps"]:
                if done["status"] == "completed" and done["tool"] == tool and _same_args(tool, _resolve(done["args"], task["steps"]), args):
                    _refuse("task_step_already_completed", "This task step already completed; resume the next step.")
            return None
        if step["status"] == "waiting":
            _refuse("task_step_waiting", "This step is waiting on its job; it never runs twice.")
        if step["status"] in ("running", "uncertain"):
            _refuse("task_step_uncertain", "Verify the previous effect and explicitly reconcile this step before repeating it.")
        _gate_brief(task, step)
        if len(json.dumps(expected, default=str)) > ARGS_CHARS:
            _refuse("task_reference_too_large", "Resolved arguments exceed the step's limit; use file IDs or smaller result references.")
        step["status"], step["session_id"] = "running", session.session_id
        step["attempt_id"] = uuid.uuid4().hex
        task["status"] = "running"
        _save(path, task)
        return (task_id, step["id"], step["attempt_id"])


def _outcome(tool, result, error):
    """(status, error) of one finished call, from its own result only."""
    receipt = result.get("_edit_receipt") if isinstance(result, dict) else None
    if error:
        return ("failed" if _retryable_read(tool) else "uncertain"), str(error)[:300]
    if tool == "refresh_experiment" and isinstance(result, dict) and result.get("status") != "completed":
        # Only a completed case with intact evidence completes the step (issue 23).
        if result.get("status") in ("ready", "submitted", "running"):
            return "failed", "Case not finished; retry after it completes."
        return "failed", f"Case result unavailable: {str(result.get('last_error') or result.get('status'))[:120]}."
    if isinstance(result, dict) and (result.get("final_status") in ("failed", "aborted", "idle")
            or result.get("status") in ("failed", "error", "aborted", "partial")
            or result.get("success") is False or result.get("ok") is False
            or (result.get("terminal") is True and result.get("completed") is False)
            or result.get("timed_out") or result.get("wait_cancelled")):
        return ("uncertain" if result.get("status") == "partial" and not _retryable_read(tool) else "failed",
                "Operation did not complete; inspect the existing job/solver before retrying.")
    if tool in _WAIT_TOOLS and isinstance(result, dict) and (
        result.get("running") is True
        or result.get("status") == "started" and result.get("final_status") != "completed"
    ):
        return "uncertain", "Solver start is not completion; inspect its outcome before advancing or retrying."
    if tool in QUEUE_WAIT_TOOLS and isinstance(result, dict) and (
        result.get("status") in ("queued", "running", "submitted")
        or any(isinstance(c, dict) and c.get("status") != "completed" for c in result.get("cases") or [])
    ):
        return "uncertain", "A queued job is not completion; inspect its outcome before advancing or retrying."
    if receipt is not None and (not isinstance(receipt, dict) or receipt.get("outcome") != "verified"):
        # An edit counts only when its native read-back equals the card.
        return "uncertain", "The edit's read-back did not match its card; inspect it before going on."
    return "completed", None


def _stored(result):
    # Retain references without replaying these bytes in every prompt.
    from harness.results import _coerce_jsonable
    encoded = json.dumps(_coerce_jsonable(result), default=str, allow_nan=False)
    return json.loads(encoded) if len(encoded) <= 100000 else {"result_omitted": True, "note": "Use existing result tools for full evidence."}


def finish_step(ticket, result=None, error=None):
    if ticket is None:
        return None
    with _LOCK:
        path, task = _read(ticket[0])
        step = next(s for s in task["steps"] if s["id"] == ticket[1])
        if step.get("attempt_id") != ticket[2]:
            return None
        if step.get("job"):
            return _view(task)  # waiting on its job, or already settled by it
        step["status"], message = _outcome(step["tool"], result, error)
        receipt = result.get("_edit_receipt") if isinstance(result, dict) else None
        if message:
            step["error"] = message
            if step["status"] == "uncertain" and isinstance(receipt, dict) and isinstance(receipt.get("rows"), list) \
                    and len(json.dumps(receipt, default=str)) <= 20000:
                step["receipt"] = _stored(receipt)  # what resolve_task_step re-reads (SC-12)
        else:
            step["result"] = _stored(result)
            step.pop("error", None)
        current = None
        if task.get("brief") and step["status"] == "completed" and _effectful(step["tool"]):
            current = _fingerprint()
            if current:
                task["fingerprint"] = current  # the chain's next link (SC-11)
        if task["status"] != "cancelled":
            task["status"] = "completed" if not _next(task) else "needs_review" if step["status"] == "uncertain" else "ready"
            _pause_at_boundary(task, step)
        _save(path, task)
        return _view(task, current=current)


def list_tasks(limit: int = 10) -> dict:
    if type(limit) is not int or not 1 <= limit <= 20:
        _refuse("invalid_task_limit", "Choose a task limit of 1–20.", 422)
    root, project, owner = _authority()
    items = []
    with _LOCK:
        paths = sorted((root / "assistant_tasks").glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for path in paths:
            task = json.loads(path.read_text())
            if task.get("owner_id") != owner or task.get("project_id") != project:
                continue
            items.append({k: _view(task)[k] for k in ("task_id", "title", "status", "completed_steps", "total_steps")})
            if len(items) == limit:
                break
    out = {"items": items, "limit": limit, "note": "Most recently updated tasks in the active project, owned by you."}
    while len(json.dumps(out)) > 3800:
        items.pop()
        out["items_truncated"] = True
    return out


def _retryable_read(tool):
    from harness.catalogue import safety_tier_for
    # Native exports have read tier but create content-addressed upload files.
    return safety_tier_for(tool) == "read" and not tool.startswith("export")


def register_worker(ticket, future):
    if ticket is None:
        return
    try:
        path = _path(ticket[0])[0]  # the worker finishes outside this request's project binding
    except HTTPException:
        path = None
    with _LOCK:
        _WORKERS[ticket] = future
    def finished(done):
        with _LOCK:
            _WORKERS.pop(ticket, None)
            if path is not None and not done.cancelled() and done.exception() is None:
                try:
                    _late_receipt(path, ticket, done.result())
                except Exception:  # noqa: BLE001 — no receipt: the step stays to review
                    pass
    future.add_done_callback(finished)


def _late_receipt(path, ticket, result):
    """The harness's own record of a timed-out call that went on to succeed:
    the one execution receipt `resolve_task_step` may complete from."""
    try:
        task = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return
    step = next((s for s in task["steps"] if s["id"] == ticket[1]), None)
    if step is None or step.get("attempt_id") != ticket[2] or step["status"] not in ("failed", "uncertain") \
            or step.get("job") or _outcome(step["tool"], result, None)[0] != "completed":
        return
    try:
        step["late_receipt"] = {"result": _stored(result)}
    except (TypeError, ValueError):
        return
    _save(Path(path), task)


def _same_args(tool, first, second):
    from services import chat_tools
    try:
        signature = inspect.signature(chat_tools.DISPATCHERS[tool])
        a, b = signature.bind(**first), signature.bind(**second)
        a.apply_defaults()
        b.apply_defaults()
        return a.arguments == b.arguments
    except TypeError:
        return False
