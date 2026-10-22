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


def _view(task):
    step = _next(task)
    state = task["status"]
    out = {"task_id": task["task_id"], "title": task["title"], "project_id": task["project_id"],
           "status": state, "completed_steps": sum(s["status"] == "completed" for s in task["steps"]),
           "total_steps": len(task["steps"]),
           "steps": [{k: s[k] for k in ("id", "tool", "status")} for s in task["steps"]]}
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
    changes = [s["result"]["_edit_receipt"]["says"] for s in task["steps"] if s["status"] == "completed"
               and isinstance(s.get("result"), dict) and isinstance(s["result"].get("_edit_receipt"), dict)
               and isinstance(s["result"]["_edit_receipt"].get("says"), str)]
    if changes:
        out["changes"] = changes
    out["note"] = ("Wait for the job; the conversation continues when it ends." if state == "waiting_job" else
                   "Execute next_step as an ordinary offered tool call. Cancel stops future steps, not running jobs.")
    while len(json.dumps(out, default=str)) > 3800 and out["steps"]:
        out["steps"].pop()
        out["step_details_truncated"] = True
    return out


def start_task(title: str, steps: list[dict], request_id: str) -> dict:
    from harness.catalogue import TOOLS
    from harness.loop import PROJECT_REBINDING_TOOLS
    from services import chat_tools
    session = chat_tools.chat_session()
    if session is None:
        _refuse("no_chat_session", "Starting a task requires a chat session.")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
        _refuse("invalid_task", "Provide a title and a stable request_id (maximum 64 characters).", 422)
    if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
        _refuse("invalid_task", "A task needs 1–12 ordered steps.", 422)
    if len(json.dumps(steps, allow_nan=False)) > 16000:
        _refuse("invalid_task", "Task arguments exceed 16000 characters; use file references.", 422)
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
        if not isinstance(args, dict) or len(json.dumps(args, allow_nan=False)) > 1800:
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
    root, project, owner = _authority()
    # Deterministic owner-scoped ID makes a repeated start request idempotent.
    task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{project}:{owner}:{request_id}"))
    path = root / "assistant_tasks" / (task_id + ".json")
    with _LOCK:
        if path.exists():
            _, task = _read(task_id)
            original = [{k: s[k] for k in ("id", "tool", "args")} for s in task["steps"]]
            if original != steps or task["title"] != title.strip():
                _refuse("task_request_conflict", "This request_id already identifies a different plan.")
        else:
            task = {"version": 1, "task_id": task_id, "owner_id": owner, "project_id": project,
                    "title": title.strip(), "status": "ready", "steps": checked}
            _save(path, task)
        session.task_id = task_id
        return _view(task)


def get_task(task_id: str) -> dict:
    with _LOCK:
        _, task = _read(task_id)
        return _view(task)


def resume_task(task_id: str) -> dict:
    from services import chat_tools
    session = chat_tools.chat_session()
    if session is None:
        _refuse("no_chat_session", "Resume requires a chat session.")
    with _LOCK:
        path, task = _read(task_id)
        if task["status"] == "cancelled":
            _refuse("task_cancelled", "Create a new plan to restart a cancelled task.")
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
        return _view(task)


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


def resolve_task_step(task_id: str, outcome: str, result: dict | None = None) -> dict:
    """Explicitly confirmed reconciliation; never an automatic write retry."""
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
        if (origin and origin._turn_in_flight) or (worker is not None and not worker.done()):
            _refuse("task_step_running", "Wait for the originating session to stop before reconciliation.")
        step["status"] = "completed" if outcome == "completed" else "pending"
        step["result"] = result
        step.pop("error", None)
        task["status"] = "ready" if _next(task) else "completed"
        _save(path, task)
        return _view(task)


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
            return


def await_job(ticket, job_id, session_id):
    """A run step whose solve is still running waits on its job, durably.

    The turn is released; `settle_job` records the outcome when the job ends,
    and the conversation continues once (`claim_continuation`)."""
    if ticket is None:
        return None
    with _LOCK:
        path, task = _read(ticket[0])
        step = next(s for s in task["steps"] if s["id"] == ticket[1])
        if step.get("attempt_id") != ticket[2] or step["status"] != "running":
            return None
        step["status"] = "waiting"
        step["job"] = {"job_id": job_id, "session_id": session_id, "continuation": "armed"}
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
        if step["status"] == "waiting" and not job_adapters.is_registered(step["job"]["job_id"]):
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
        step["status"], step["session_id"] = "running", session.session_id
        step["attempt_id"] = uuid.uuid4().hex
        task["status"] = "running"
        _save(path, task)
        return (task_id, step["id"], step["attempt_id"])


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
        receipt = result.get("_edit_receipt") if isinstance(result, dict) else None
        if error:
            step["status"] = "failed" if _retryable_read(step["tool"]) else "uncertain"
            step["error"] = str(error)[:300]
        elif isinstance(result, dict) and (result.get("final_status") in ("failed", "aborted", "idle")
                or result.get("status") in ("failed", "error", "aborted", "partial")
                or result.get("success") is False or result.get("ok") is False
                or (result.get("terminal") is True and result.get("completed") is False)
                or result.get("timed_out") or result.get("wait_cancelled")):
            step["status"] = "uncertain" if result.get("status") == "partial" and not _retryable_read(step["tool"]) else "failed"
            step["error"] = "Operation did not complete; inspect the existing job/solver before retrying."
        elif step["tool"] in ("run_simulation", "run_ac_pf_stage") and isinstance(result, dict) and (
            result.get("running") is True
            or result.get("status") == "started" and result.get("final_status") != "completed"
        ):
            step["status"] = "uncertain"
            step["error"] = "Solver start is not completion; inspect its outcome before advancing or retrying."
        elif receipt is not None and (not isinstance(receipt, dict) or receipt.get("outcome") != "verified"):
            # An edit counts only when its native read-back equals the card.
            step["status"] = "uncertain"
            step["error"] = "The edit's read-back did not match its card; inspect it before going on."
        else:
            step["status"] = "completed"
            # Retain references without replaying these bytes in every prompt.
            from harness.results import _coerce_jsonable
            encoded = json.dumps(_coerce_jsonable(result), default=str, allow_nan=False)
            step["result"] = json.loads(encoded) if len(encoded) <= 100000 else {"result_omitted": True, "note": "Use existing result tools for full evidence."}
            step.pop("error", None)
        if task["status"] != "cancelled":
            task["status"] = "completed" if not _next(task) else "needs_review" if step["status"] == "uncertain" else "ready"
        _save(path, task)
        return _view(task)


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
    with _LOCK:
        _WORKERS[ticket] = future
    def finished(_):
        with _LOCK:
            _WORKERS.pop(ticket, None)
    future.add_done_callback(finished)


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
