"""Durable checkpoints for ordinary, independently authorized tool dispatches."""
from __future__ import annotations

import copy
import inspect
import json
import logging
import os
import re
import threading
import time
import uuid
from contextvars import ContextVar
from pathlib import Path

from fastapi import HTTPException
from services.provenance_graph import Pin, pin_stale
from services.pypsa_service import PyPSAService

_LOCK = threading.RLock()
logger = logging.getLogger(__name__)
_WORKERS = {}
TASK_STEP_RETRIES_MAX = 2

_CONTROLS = frozenset({"start_task", "get_task", "resume_task", "cancel_task", "resolve_task_step", "list_tasks"})
_REF = re.compile(r"^\$([A-Za-z][A-Za-z0-9_-]{0,31})\.([A-Za-z0-9_.-]+)$")
# SC-07 S3 (GK B N2-N4): a 3D site apply step commits only the preview a
# prepare step of the same plan made, and reconciles from the scene chain.
SCENE_APPLY = "site_scene_apply"
SCENE_APPLY_ARGS = ("site_id", "proposal_id", "input_hash", "expected_revision")
# A retried apply would replay a dead or moved preview: it needs a new prepare.
SCENE_APPLY_DEAD = frozenset({"proposal_expired", "proposal_cancelled", "revision_conflict"})
SCENE_PREPARE = "site_scene_prepare"
# A prepare that asks the user (services/site_scene/chat.prepare): no preview was made.
SCENE_PREPARE_ASKS = frozenset({"clarify", "unresolved"})
CHANGE_EDITS_MAX = 2000  # chars of receipt rows a task view carries (UX (14) ask 2)
# LC-23 study brief (owner decisions AI-567b, COP-1): a plan labelled with an
# objective, cards in scope, cases and one phase per card.
CARDS = ("capacity", "grid", "control", "reliability", "commercial", "hand-off")
_LIMITS = {False: (12, 16000), True: (24, 32000)}  # (steps, step chars) without/with a brief
ARGS_CHARS = 1800
RESULT_CHARS = 4000  # the model-facing result cap, harness.results._RESULT_CONTENT_CAP
_WAIT_TOOLS = frozenset({"run_simulation", "run_ac_pf_stage"})
# Queued runs wait on their solve-queue job(s) (`harness/continuation.py`).
QUEUE_WAIT_TOOLS = frozenset({"run_sensitivity_sweep", "solve_queue_enqueue",
                              "generate_renewable_profile", "run_decision_study"})  # LC-45: no polling
# LC-45: these two wait on their own job only in a plan; a harness run's step
# keeps its own judge (`run_explore`), so a run never takes the follower.
PLAN_ONLY_WAIT_TOOLS = frozenset({"generate_renewable_profile", "run_decision_study"})
_QUEUE_FOLLOWERS = set()  # (task_id, step_id, attempt_id) of steps this process follows
# Study harness 02 (GK B, B1 option b): the harness runner binds its run's own
# project context here; `_authority` and `_advance_run` prefer it to the
# request's active context. Only `harness/runner.py` sets it.
RUN_CONTEXT: ContextVar = ContextVar("assistant_task_run_context", default=None)
CLAIM_SECONDS = 360  # a run step's claim: the card TTL (300 s) and a margin
_WAKE_LISTENERS = []  # called with a task path when its run may go on (the runner's wake)


def _wake(path):
    for listener in list(_WAKE_LISTENERS):
        try:
            listener(Path(path))
        except Exception:  # noqa: BLE001 — a wake is a hint; the runner's timer still ticks
            pass


def _claim_live(step):
    claim = step.get("claim")
    return claim if claim and claim["until"] > time.time() else None


def _check_claim(task, step, holder):
    """GK B condition 7: a run step has one holder at a time, the chat or the
    runner; another holder's live claim refuses before any card."""
    claim = _claim_live(step) if task.get("run") else None
    if claim and claim["by"] != holder:
        _refuse("task_step_claimed", f"Step {step['id']} is being run by another session of this plan; "
                "wait for it, then read the plan again.")


def _take_claim(step, holder):
    step["claim"] = {"by": holder, "until": time.time() + CLAIM_SECONDS}


def claim_step(path, step_id, holder):
    """Compare-and-set the claim on a run's pending step, under the task lock.
    True when `holder` now holds it; the runner dispatches only then."""
    with _LOCK:
        task = json.loads(Path(path).read_text())
        step = _next(task)
        if (step is None or step["id"] != step_id or step["status"] != "pending"
                or task["status"] in ("cancelled", "completed", "paused_at_card")):
            return False
        claim = _claim_live(step)
        if claim and claim["by"] != holder:
            return False
        _take_claim(step, holder)
        _save(Path(path), task)
        return True


def release_claim(path, step_id, holder):
    """A dispatch that ended before its step began frees the step."""
    with _LOCK:
        task = json.loads(Path(path).read_text())
        step = next((s for s in task["steps"] if s["id"] == step_id), None)
        if step is not None and step["status"] == "pending" and (step.get("claim") or {}).get("by") == holder:
            step.pop("claim")
            _save(Path(path), task)


def _clear_waiting(task, ref):
    """The user acted on what the run waited for (a card it held back)."""
    run = task.get("run")
    if run and (run.get("waiting") or {}).get("ref") == ref:
        run["waiting"] = None
        run.pop("reason", None)
        if run["status"] == "needs_user":
            run["status"] = "running"


def track_queue_follower(ticket):
    """Before the step is saved waiting, so no reconcile sees it unfollowed."""
    _QUEUE_FOLLOWERS.add(tuple(ticket))


def forget_queue_follower(ticket):
    _QUEUE_FOLLOWERS.discard(tuple(ticket))


def _refuse(kind, message, status=409):
    raise HTTPException(status, {"error_kind": kind, "message": message})


def _authority():
    from services import chat_tools, project_registry
    ctx = RUN_CONTEXT.get() or PyPSAService.get_active_context()
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
    """The first step not done; a step a re-solve superseded (L2, issue 05), or
    an explore step its cases judged (issue 06), is never next."""
    return next((s for s in task["steps"]
                 if s["status"] != "completed" and not s.get("superseded") and not s.get("judged_by")), None)


def _check_brief(brief, ids, phased=True):
    """The brief labels the steps; it grants nothing and adds no step. A
    harness run's brief has no phases: its stages replace them."""
    def bad(message):
        _refuse("invalid_brief", message, 422)
    keys = {"objective", "cards"} | ({"phases"} if phased else set())
    if not isinstance(brief, dict) or not keys <= set(brief) <= keys | {"cases"}:
        bad("A brief has objective, cards, phases and optional cases." if phased else
            "A run's brief has objective, cards and optional cases; its stages replace phases.")
    objective, cards, cases, phases = brief["objective"], brief["cards"], brief.get("cases", []), brief.get("phases")
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
    out = {"objective": objective.strip(), "cards": list(cards),
           "cases": [{"name": c["name"].strip(), "experiment": c.get("experiment")} for c in cases]}
    if not phased:
        return out
    if not isinstance(phases, list) or not phases or any(
            not isinstance(p, dict) or set(p) != {"card", "steps"} or p["card"] not in cards
            or not isinstance(p["steps"], list) or not p["steps"] for p in phases):
        bad("Each phase names one declared card and its steps.")
    if [step for phase in phases for step in phase["steps"]] != ids:
        bad("Phases must cover every step once, in step order.")
    return {**out, "phases": [{"card": p["card"], "steps": list(p["steps"])} for p in phases]}


def _check_runnable(brief, steps):
    """A brief runs only what execution admits (issue 23, SC-10): its run
    steps wait by themselves, and its cases fit one 1–2 network-case
    experiment (`experiment_execution.runnable`, the advance rule)."""
    from harness.experiment_execution import RUNNABLE_RULE, runnable
    if any(step["tool"] == "wait_for_job" for step in steps):
        _refuse("invalid_task_tool", "Under a brief a run step waits by itself; drop the wait_for_job step.", 422)
    asked = [(step["args"].get("domain", "network"), len(step["args"].get("cases") or []))
             for step in steps if step["tool"] in ("start_experiment", "run_sensitivity_sweep")]
    if (brief["cases"] and not runnable("network", len(brief["cases"]))) or any(not runnable(*a) for a in asked):
        _refuse("brief_cases_not_runnable", RUNNABLE_RULE + " Propose the first two network cases as this brief "
                "and the rest as a follow-up brief.", 422)


def _limit(tool):
    """Each step's packet limit, from the server, never from the model."""
    return {"args_chars": ARGS_CHARS, "result_chars": RESULT_CHARS, "wait": "job" if tool in _WAIT_TOOLS | QUEUE_WAIT_TOOLS else None}


def _fingerprint():
    """The project's input fingerprint (solver outputs excluded), or None (`provenance_graph.runs`)."""
    from services.provenance_graph.runs import active_input_fingerprint
    return active_input_fingerprint()


def _stale(task, current=None):
    """SC-11: the brief is current only while the inputs are the last plan
    step's own. Inputs that cannot be read count as changed (fail closed)."""
    if not task.get("brief") or task["status"] in ("completed", "cancelled"):
        return False
    current = current or _fingerprint()

    def read():
        if current is None:
            raise ValueError("the inputs cannot be read")
        return current

    # The first broken pin stops the check: the requirements thread is read (under the caller's lock) only when
    # the fingerprint still matches, as before.
    subject = f"assistant_task:{task.get('id')}"
    if pin_stale(subject, [Pin("inputs", task.get("fingerprint"), read)]):
        return True
    return pin_stale(subject, [Pin("requirements", False, lambda: bool(_requirements_moved(task)))])


def _requirements_moved(task):
    """Issue 23: a requirement activated or retired since the brief was
    approved makes it stale; an evaluation mark does not. A recorded thread
    that cannot be read counts as moved (fail closed)."""
    if not task.get("requirements"):
        return False
    from harness import requirements
    try:
        return requirements.moved(task["requirements"], _authority())
    except Exception:  # noqa: BLE001 — unreadable counts as moved
        return True


def _requirements_record(session):
    from harness import requirements
    return requirements.brief_record(session)


def _card_of(task, step_id):
    return next((p["card"] for p in task["brief"]["phases"] if step_id in p["steps"]), None)


def _pause_at_boundary(task, step):
    """COP-1: a plan going on to another card waits for the user's Continue."""
    following = _next(task)
    if task.get("brief") and not task.get("run") and task["status"] == "ready" and following is not None \
            and step["status"] == "completed" and _card_of(task, step["id"]) != _card_of(task, following["id"]):
        task["status"] = "paused_at_card"
        task["pause"] = {"card": _card_of(task, following["id"]), "after": step["id"]}


def _effectful(tool):
    from harness.catalogue import safety_tier_for
    return safety_tier_for(tool) != "read"


def _carded(step):
    """COP-1: a stage pauses at its card before a step that writes or runs.
    The hand-off's native read-back writes nothing to the project (issue 07);
    its revert preview is a stored proposal the user applies on a card."""
    from services import run_actions
    return step.get("native") != "handoff" and not run_actions.reads_only(step) and _effectful(step["tool"])


# The skip reason the generic profile gave the report before issue 07 (§9.2):
# a record stored before `by` existed was skipped by its profile when it carries it.
_PROFILE_SKIPS = frozenset({"The run report is not part of a run yet."})
# The stages an in-flight run picks up from its profile (C8): issue 07's own.
# Explore stays as the run started it (issue 06 E3: frozen at start).
_SYNCED_STAGES = ("report", "handoff")


def _skipped_by_profile(stored):
    return stored.get("status") == "skipped" and (
        stored.get("by") == "profile" or ("by" not in stored and stored.get("reason") in _PROFILE_SKIPS))


def _sync_stages(run, profile):
    """Issue 07 (GK B C8, B1-a): the profile is read by id, so a run started
    before its profile gained a report or hand-off stage goes on to it
    (explore stays as started, issue 06 E3). A later stage the stored
    record lacks, or skipped only because its profile did, becomes pending
    when the profile now runs it; a stage skipped for any other reason stays
    skipped, and a stage the run already reached is never reopened. True
    when the record changed."""
    from services.run_profiles import STAGES
    changed = False
    for name in STAGES[STAGES.index(run["stage"]) + 1:]:
        if name not in _SYNCED_STAGES:
            continue
        stage = profile["stages"][name]
        stored = run["stages"].get(name)
        if "skip" in stage:
            if stored is None:
                run["stages"][name], changed = {"status": "skipped", "reason": stage["skip"], "by": "profile"}, True
        elif stored is None or _skipped_by_profile(stored):
            run["stages"][name], changed = {"status": "pending", "attempts": 0}, True
    return changed


# LC-19 triage reads a failed run step's fix is drafted from. They are offered,
# never run: the fix is an ordinary edit card and the plan goes on only
# through resolve_task_step(retry) with its confirmation.
_TRIAGE = ("get_simulation_log_history", "diagnose_network", "validate_network")


def _correction(step):
    """`next_step.correction` under a brief: proposes, runs nothing."""
    if step.get("retries", 0) >= TASK_STEP_RETRIES_MAX:
        return {"stop": "retry_limit", "then": "explain what failed; the user decides: fix the inputs, or cancel_task"}
    if step.get("job") or step["tool"] in _WAIT_TOOLS | QUEUE_WAIT_TOOLS:
        return {"triage": list(_TRIAGE), "then": "resolve_task_step(retry) after the user approves the fix"}
    if step.get("receipt"):
        return {"reread": step["tool"], "then": "resolve_task_step(completed) re-reads it; else retry"}
    return {"then": "inspect, then resolve_task_step(retry) or cancel_task"}


def _view(task, fresh=False, current=None):
    step = _next(task)
    state = task["status"]
    out = {"task_id": task["task_id"], "title": task["title"], "project_id": task["project_id"],
           "status": state, "completed_steps": sum(s["status"] == "completed" and not s.get("superseded") for s in task["steps"]),
           "total_steps": len(task["steps"]),
           "steps": [{k: s[k] for k in ("id", "tool", "status")} for s in task["steps"]]}
    for entry, s in zip(out["steps"], task["steps"]):
        if isinstance(s.get("card"), dict):
            entry["card"] = s["card"]  # issue 07 B3a: what a step-by-step engine card shows
        if (s.get("job") or {}).get("cases"):
            entry["cases"] = s["job"]["cases"]  # LC-17: each case's outcome and a failed one's cause
    brief = task.get("brief")
    if task.get("run"):
        out["run"] = _run_view(task)
        out["brief"] = {"objective": brief["objective"], "cards": brief["cards"], "cases": [c["name"] for c in brief["cases"]]}
        out["steps"] = [dict(e, limit=_limit(e["tool"])) for e in out["steps"] if e["id"].startswith(task["run"]["stage"] + ".")]
        if state == "paused_at_card":
            out["paused_at_card"] = task["pause"]["card"]
        if fresh and _stale(task, current):
            out["brief_stale"] = "inputs_changed_outside_plan"
    elif brief:
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
        if step.get("native"):
            out["next_step"]["native"] = True  # issue 07: the runner runs it; a chat call never claims it
        if step.get("retries", 0) > 0:
            out["next_step"]["retries"] = step["retries"]
        if step["status"] == "waiting":
            out["next_step"]["job_id"] = step["job"].get("job_id") or step["job"].get("report_id")
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
        if isinstance(step.get("error_kind"), str):
            # SC-07 S5b (GK B 1a): the stored kind finish_step took from the tool's own typed
            # refusal (N3), read-only and never derived from the message; absent when none.
            out["next_step"]["error_kind"] = step["error_kind"]
        if brief and step["status"] in ("failed", "uncertain"):
            out["next_step"]["correction"] = _correction(step)
    receipts = [s["result"]["_edit_receipt"] for s in task["steps"] if s["status"] == "completed"
                and isinstance(s.get("result"), dict) and isinstance(s["result"].get("_edit_receipt"), dict)
                and isinstance(s["result"]["_edit_receipt"].get("says"), str)]
    changes = [r["says"] for r in receipts]
    if changes:
        out["changes"] = changes
    # SC-07 S5b (UX (14) ask 2): each edit's receipt rows, so the partial-mapping line words the
    # change with the panel's field labels as Guided does. Only a task stopped at a failed or
    # uncertain scene step can show that line, so only it carries them: on every other `_task` a
    # result rides with they would only grow it (past the result cap, into a spill). Past the cap
    # they are left out and `says` stands.
    stop = out.get("next_step") or {}
    if changes and stop.get("tool") in (SCENE_PREPARE, SCENE_APPLY) and stop.get("status") in ("failed", "uncertain"):
        edits = [{k: r[k] for k in ("rows", "sets") if k in r} for r in receipts]
        if len(json.dumps(edits, default=str)) <= CHANGE_EDITS_MAX:
            out["change_edits"] = edits
    run = task.get("run") or {}
    out["note"] = ("Wait for the job; the conversation continues when it ends." if state == "waiting_job" else
                   f"The {run['stage']} gate failed: tell the user which rules failed and their facts; "
                   "the run goes on only after the user fixes the inputs and resumes it." if run.get("status") == "needs_user" else
                   "The run reached hand-off; read back its results for the user." if run.get("status") == "completed" else
                   f"Paused before the {task['pause']['card']} card; the user continues from the plan card." if state == "paused_at_card" else
                   "The inputs changed outside this plan; draft a new brief for the user to review." if out.get("brief_stale") else
                   "Execute next_step as an ordinary offered tool call. Cancel stops future steps, not running jobs.")
    while len(json.dumps(out, default=str)) > 3800 and out["steps"]:
        out["steps"].pop()
        out["step_details_truncated"] = True
    return out


def _check_run(run, steps, brief):
    """A harness run (study harness issue 01): its profile's stages make the
    steps, so the caller passes none, and the brief has no phases."""
    from services import run_profiles
    if not isinstance(run, dict) or set(run) != {"profile", "mode"}:
        _refuse("invalid_task", "A run names its profile and mode.", 422)
    if run["mode"] != "stepwise":
        _refuse("invalid_task", "Runs are step-by-step for now: every write and run has its own card.", 422)
    try:
        profile = run_profiles.resolve(run["profile"])
    except run_profiles.ProfileError as exc:
        _refuse("invalid_task", str(exc)[:300], 422)
    if steps != []:
        _refuse("invalid_task", "A run's steps come from its profile's stages; pass steps as [].", 422)
    if brief is None:
        _refuse("invalid_brief", "A run starts from the brief the user reviewed.", 422)
    brief = _check_brief(brief, [], phased=False)
    _check_runnable(brief, [])
    return profile, brief


def _check_scene_apply(args, earlier):
    """N2: every apply arg is a whole-value ref to one earlier site_scene_prepare
    step's `apply_with.args`, so the plan never carries a literal preview."""
    sources = set()
    for key in SCENE_APPLY_ARGS:
        value = args.get(key)
        match = _REF.fullmatch(value) if isinstance(value, str) else None
        source = next((s for s in earlier if match and s["id"] == match[1]), None)
        if source is None or source["tool"] != "site_scene_prepare" or match[2] != f"apply_with.args.{key}":
            _refuse("invalid_task_reference", f"A site_scene_apply step takes {key} as ${{step}}.apply_with.args.{key} "
                    "from an earlier site_scene_prepare step of this plan, never a literal.", 422)
        sources.add(match[1])
    if len(sources) != 1:
        _refuse("invalid_task_reference", "A site_scene_apply step takes all its args from one site_scene_prepare step.", 422)


# LC48D-GOAL-1: the task statuses during which its steps execute.
_RUNNING_TASK = frozenset({"running", "waiting_job"})


def _refuse_switch(session, task_id):
    """409 `switch_in_progress` naming the session's task while it runs and
    a start names another plan; a repeated start of the same plan is not a switch."""
    active = getattr(session, "task_id", None)
    if not active or active == task_id:
        return
    try:
        _, task = _read(active)
    except HTTPException:
        return  # gone or unreadable: nothing runs that a switch would cut
    if task.get("status") not in _RUNNING_TASK:
        return
    raise HTTPException(409, {
        "error_kind": "switch_in_progress",
        "active": {"kind": "task", "id": active, "title": task.get("title"),
                   "status": task.get("status")},
        "message": f"the task \u201c{task.get('title')}\u201d is {task.get('status')}; wait for it, "
                   "or cancel it, before starting another",
    })


def start_task(title: str, steps: list[dict], request_id: str, brief: dict | None = None,
               run: dict | None = None) -> dict:
    from harness.catalogue import TOOLS
    from harness.loop import PROJECT_REBINDING_TOOLS
    from services import chat_tools
    session = chat_tools.chat_session()
    if session is None:
        _refuse("no_chat_session", "Starting a task requires a chat session.")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
        _refuse("invalid_task", "Provide a title and a stable request_id (maximum 64 characters).", 422)
    if run is not None:
        # Issue 21: the model's intake citations, read on the open project.
        intake_request = run.get("intake") if isinstance(run, dict) else None
        run = {k: v for k, v in run.items() if k != "intake"} if isinstance(run, dict) else run
        return _start_run(session, title, request_id, run, *_check_run(run, steps, brief),
                          intake_request=intake_request)
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
        if tool == SCENE_APPLY:
            _check_scene_apply(args, checked)
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
        _refuse_switch(session, task_id)
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
                record = _requirements_record(session)
                if record:
                    task["requirements"] = record  # issue 23: the revision approved with the brief
            _save(path, task)
        session.task_id = task_id
        return _view(task)


# ── Harness runs (study harness issue 01): version 3 adds the `run` block ────
# The run block is written only here, by harness code: the stage on entry,
# its steps from the profile, and the gate from native facts. No tool
# argument writes it, so a caller-supplied result never passes a gate (SC-12).


def _now():
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat(timespec="seconds")


def _done(task):
    """No step left, and for a run, the run completed (its gate passed)."""
    return not _next(task) and not task.get("run")


def _next_stage(run, name):
    from services.run_profiles import STAGES
    return next((s for s in STAGES[STAGES.index(name) + 1:] if run["stages"][s]["status"] != "skipped"), None)


def _start_run(session, title, request_id, run, profile, brief, *, origin=None, intake=None, grant=None,
               intake_request=None):
    root, project, owner = _authority()
    if intake_request is not None:
        from services import run_intake
        try:  # read before the lock: files and turns, nothing written (R3)
            intake = run_intake.take(intake_request, profile, PyPSAService.get_active_context(), owner, project)
        except run_intake.IntakeError as exc:
            if exc.kind == "not_found":
                _refuse("not_found", str(exc), 404)
            if exc.kind == "intake_value_supplied":
                _refuse("intake_value_supplied", str(exc), 422)
            _refuse("invalid_intake", str(exc), 422)
    task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{project}:{owner}:{request_id}"))
    path = root / "assistant_tasks" / (task_id + ".json")
    from services import run_envelope
    held = run_envelope.counted_live(project, profile)  # study harness 12c (G1): K, before the lock (#1345)
    with _LOCK:
        if path.exists():
            _, task = _read(task_id)
            if {k: (task.get("run") or {}).get(k) for k in ("profile", "mode")} != run \
                    or task["title"] != title.strip() or task.get("brief") != brief:
                _refuse("task_request_conflict", "This request_id already identifies a different plan.")
        else:
            fingerprint = _fingerprint()
            if fingerprint is None:
                _refuse("brief_inputs_unreadable", "The project's inputs cannot be read, so the brief cannot be kept current.")
            stages = {name: {"status": "skipped", "reason": stage["skip"], "by": "profile"} if "skip" in stage else
                      {"status": "pending", "attempts": 0} for name, stage in profile["stages"].items()}
            task = {"version": 3, "task_id": task_id, "owner_id": owner, "project_id": project,
                    "title": title.strip(), "status": "ready", "steps": [], "brief": brief, "fingerprint": fingerprint,
                    "run": {**run, "status": "running", "stage": "intake", "stages": stages,
                            # Issue 21: the host task a hand-over started this run from,
                            # and the confirmed intake rows it was created with.
                            **({"origin": dict(origin)} if origin else {}),
                            **({"intake": intake} if intake is not None else {})}}
            record = _requirements_record(session)
            if record:
                task["requirements"] = record  # issue 23: a run inherits the confirmed requirements
            _save(path, task)
            run_envelope.propose(path, task, profile, held)  # issue 03: the plan-review card's envelope
            if intake_request is not None:
                run_envelope.set_setup(path, profile["templates"][0], intake["project_name"], intake["digest"])
            if grant is not None:
                # Issue 21 A2: a hand-over's run starts inside the host card's grant,
                # before anything can wake it.
                grant(path, task, RUN_CONTEXT.get() or PyPSAService.get_active_context())
        session.task_id = task_id
    if grant is not None:
        _pin_planned(path)
    _advance_run(path)
    _wake(path)
    with _LOCK:
        return _with_envelope(path, _view(_read(task_id)[1]))


def handover_starter(host_task: dict, profile: dict, owner_id: str, *, grant=None, handover=None):
    """`start(row, ctx)` for `run_handover.hand_over` (issue 21 §2): the
    host's run started on the created project, through that project's own
    context (A3). The foreground session is not touched: no session's
    `task_id` moves to the new task. `grant(path, task, ctx)` grants the new
    run's envelope as the host card's decision; `handover` (its decision id
    and digest) goes on `run.origin`, which the envelope must match (A2)."""
    from types import SimpleNamespace

    from services import chat_tools

    def start(row, ctx):
        token = RUN_CONTEXT.set(ctx)
        chat_tools.set_acting_user(owner_id)
        try:
            run = {k: host_task["run"][k] for k in ("profile", "mode")}
            request_id = "handover:" + host_task["task_id"]
            # The new task's own runner session (`_start_run`'s task id), so no
            # chat session and no thread of the foreground is read or bound.
            task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{row.id}:{owner_id}:{request_id}"))
            from services import run_envelope
            view = _start_run(SimpleNamespace(task_id=None, session_id=run_envelope.SESSION_PREFIX + task_id),
                              host_task["title"], request_id, run, profile, host_task.get("brief"),
                              origin={"project_id": host_task["project_id"], "task_id": host_task["task_id"],
                                      **(handover or {})},
                              intake=(host_task["run"].get("intake") or {}).get("rows"), grant=grant)
        finally:
            RUN_CONTEXT.reset(token)
        return view["task_id"]
    return start


def end_handed_over(path, intent: dict) -> dict:
    """The host task ends `completed`, reason `handed_over`, linking the run
    on the created project (issue 21 §2)."""
    with _LOCK:
        task = json.loads(Path(path).read_text())
        run = task.get("run") or {}
        run.update(status="completed", reason="handed_over",
                   handed_over={k: intent.get(k) for k in ("project_id", "project_name", "task_id")})
        task["run"], task["status"] = run, "completed"
        _save(path, task)
    return task


def _with_envelope(path, view):
    """A run's view carries its approval envelope (issue 03): what the
    plan-review card proposes, or what was granted and how far it went."""
    from services import run_envelope
    envelope = run_envelope.read_for_card(path, view) if view.get("run") else None
    return {**view, "envelope": envelope} if envelope is not None else view


def _may_write(task):
    """Only a caller that may edit the run's project (ACL, and no other
    user's edit lock) writes its run block (GK B)."""
    from routers.projects import _check_project_lock
    from services import chat_tools, project_registry
    try:
        with chat_tools._acting() as (db, user):
            _check_project_lock(db, project_registry.resolve_project(db, user, task["project_id"]), user)
            return True
    except HTTPException:
        return False


def _gate_now(task, rules, ctx):
    """The stage's gate from native facts, on the run's own project's context."""
    from services import chat_tools, project_registry, run_gates
    try:
        with chat_tools._acting() as (db, user):
            project = project_registry.resolve_project(db, user, task["project_id"])
            return run_gates.gate(rules, run_gates.Env(task=task, ctx=ctx, project=project,
                                                       project_dir=project_registry.project_dir(project)))
    except HTTPException:  # the project cannot be resolved: no fact is evidence
        return run_gates.gate(rules, run_gates.Env(task=task, ctx=None, project=None, project_dir=None))


def _inputs_moved(task, current):
    """B1 (GK B): before a stage's write or run step, the input gate must still
    describe the inputs: its fingerprint against the current one."""
    run = task["run"]
    stage = run["stage"]
    gate = (run["stages"].get("input_gate") or {}).get("gate")
    pending = [s for s in task["steps"] if s["id"].startswith(stage + ".") and s["status"] == "pending"]
    return (stage != "input_gate" and run["stages"].get("input_gate", {}).get("status") == "passed"
            and gate is not None and any(_carded(s) for s in pending) and gate["fingerprint"] != current)


def _back_to_input_gate(task):
    """The input gate is checked again: later stages' unstarted steps go, and
    those stages are entered afresh once it passes."""
    from services.run_profiles import STAGES
    run = task["run"]
    later = STAGES[STAGES.index("input_gate") + 1:]
    task["steps"] = [s for s in task["steps"] if not (s["status"] == "pending" and s["id"].split(".")[0] in later)]
    for name in later:
        if run["stages"][name]["status"] == "active" and not any(
                s["id"].startswith(name + ".") for s in task["steps"]):
            run["stages"][name] = {"status": "pending", "attempts": run["stages"][name]["attempts"]}
    run["stage"] = "input_gate"
    run["stages"]["input_gate"]["status"] = "active"
    task.pop("pause", None)
    if task["status"] == "paused_at_card":
        task["status"] = "ready"


def _pin_planned(path):
    """Issue 21c: the created project exists; pin the run's planned writes on
    it (the run's own context) before the run can enter stage 2. With no run
    context nothing pins, never on the foreground (G6): the writes then card."""
    from services import run_envelope
    bound = RUN_CONTEXT.get()
    if bound is not None:
        run_envelope.pin_planned(path, bound)


def _advance_run(path, ctx=None):
    """Enter the run's stage, or evaluate its gate once its steps completed.

    Gates read the network, so they are evaluated outside the task lock and
    written only if (stage, attempts, fingerprint) did not move meanwhile; a
    caller that may not edit the project writes nothing (`gate_due`)."""
    from harness import run_eh, run_explore, run_loops
    from services import flow_service, run_gates, run_profiles
    ctx = ctx or RUN_CONTEXT.get() or PyPSAService.get_active_context()
    for _ in range(2 * len(run_profiles.STAGES)):
        with _LOCK:
            task = json.loads(Path(path).read_text())
            run = task.get("run")
            if not run or run["status"] != "running" or task["status"] in ("cancelled", "completed", "paused_at_card"):
                return
            if str(ctx.project_uuid) != task["project_id"] or not _may_write(task):
                return  # another project's context, or no edit right: the view shows the gate as due
            profile = run_profiles.resolve(run["profile"])
            if _sync_stages(run, profile):
                _save(path, task)
            name = run["stage"]
            stage = run["stages"][name]
            if stage["status"] == "pending":
                start = 1 + sum(1 for s in task["steps"] if s["id"].split(".")[0] == name)
                planned = []
                if name == "inputs" and start == 1:
                    # Issue 21c: stage 2 runs the grant's planned writes first, in
                    # intake order, with the envelope's args.
                    from services import run_envelope
                    planned = run_envelope.planned_steps(path, name)
                steps = planned + run_profiles.materialise(profile, name, task["project_id"],
                                                           start=start + len(planned))
                if any(run_eh.is_eh_step(s) for s in steps):
                    # Issue 07 B2 (C1, Q2 = A): the EH study's args, frozen at the grant or now.
                    run_eh.fill(steps, run_eh.stage_frozen(path, task, profile, ctx))
                from services import run_actions
                if any(run_actions.is_action(s) for s in steps):
                    # Study harness 10a (GK B (36) D1): the grid-code profile, frozen now, shown on the stage card.
                    # Study harness 12b (M2): one record per kind, never crossed.
                    for kind in run_actions.kinds(steps):
                        run_actions.fill(steps, run_actions.stage_frozen(task, kind), kind)
                short = run_eh.pass_short(path, steps)
                if short is not None:
                    # Study harness 12c (Q1-i): a pass the solves left cannot finish never
                    # starts; nothing of it is materialised and the stage stays pending.
                    loop = run.get("loop")
                    if loop and not loop.get("ended"):
                        loop.pop("due", None)
                        run_loops._end_cycle(loop, None, run_eh.BUDGET_KIND)
                        loop["ended"] = run_eh.BUDGET_KIND
                    stage["refused"] = {"kind": run_eh.BUDGET_KIND, "need": short[0], "left": short[1]}
                    run["status"], run["reason"], task["status"] = "needs_user", run_eh.BUDGET_KIND, "needs_review"
                    _save(path, task)
                    return
                stage.update(status="active", entered_at=_now())
                task["steps"].extend(steps)
                if any(_carded(s) for s in steps):
                    # COP-1: the plan pauses before each stage that writes or runs.
                    task["status"], task["pause"] = "paused_at_card", {"card": name, "after": None}
                _save(path, task)
                if steps:
                    return
            if stage["status"] == "active" and _inputs_moved(task, run_gates.fingerprint(ctx)):
                _back_to_input_gate(task)
                _save(path, task)
                name, stage = "input_gate", run["stages"]["input_gate"]
            if stage["status"] != "active" or any(
                    s["status"] != "completed" and not s.get("superseded") and not s.get("judged_by")
                    and not run_loops.judged(Path(path), profile, s)
                    for s in task["steps"] if s["id"].startswith(name + ".")):
                return
            if run_loops.due(task):
                return  # an input-loop round is due: the runner's agent turn first (issue 04)
            if name == "explore" and run_explore.holds(task):
                return  # issue 06: the proposal turn, its research, or a case still running first
            attempts = stage["attempts"]
        gate = _gate_now(task, run_loops.gate_rules(task, profile, name), ctx)
        # Issue 21 §3: what a failed input gate would ask for, read outside the lock.
        gaps = run_loops.data_gaps(path, task, profile, ctx) \
            if name == run_loops.STAGE and not gate["passed"] and run.get("loop") is not None else None
        with _LOCK:
            task = json.loads(Path(path).read_text())
            run = task["run"]
            stage = run["stages"][name]
            if run["status"] != "running" or run["stage"] != name or stage["status"] != "active" \
                    or stage["attempts"] != attempts or task["status"] in ("cancelled", "completed") \
                    or (gate["fingerprint"] is not None and run_gates.fingerprint(ctx) != gate["fingerprint"]):
                continue  # the run or its inputs moved while the gate was read: look again
            stage["attempts"], stage["gate"] = attempts + 1, gate
            following = _next_stage(run, name)
            if not gate["passed"]:
                if (name == run_loops.STAGE and run_loops.after_failed_gate(Path(path), task, gate, profile)) \
                        or (name in run_loops.RESULT_STAGES
                            and run_loops.after_failed_result(Path(path), task, gate, profile)):
                    _save(path, task)  # L1 (§6.1), L2 (§6.2): the stage stays active while a round is due
                    _wake(path)
                    return
                stage["status"], run["status"], task["status"] = "failed", "needs_user", "needs_review"
                if name == run_loops.STAGE and (run.get("loop") or {}).get("ended") and gaps:
                    run_loops.data_request(task, gaps)  # one notice listing every data gap
            else:
                if name == run_loops.RESULT_STAGE and run_loops.engine_due(task, profile):
                    run_loops.card_engine(path, task, profile, ctx)  # issue 07 B3a: step by step, its card
                    _save(path, task)  # issue 05 PR C: the next engine rule's loop, then the whole gate
                    _wake(path)
                    return
                if name in (run_loops.STAGE, run_loops.RESULT_STAGE):
                    run_loops.after_passed_gate(task, name)
                stage.update(status="passed", exited_at=_now())
                if name == "setup":
                    run["scope"] = run_profiles.scope_for(flow_service.read_template(path.parent.parent))
                if following is None:
                    run["status"], task["status"] = "completed", "completed"
                else:
                    run["stage"] = following
            _save(path, task)
            if run["status"] != "running":
                return


def _intake_view(run):
    """Issue 21: the plan-review card's "Inputs from the chat" section (issue
    08 renders it), the digest a grant sends back, and what is missing."""
    from project_templates import eh_site
    from services import run_intake, run_profiles
    intake = run["intake"]
    card = run_intake.card_view(intake["rows"], run_profiles.resolve(run["profile"]), eh_site.site_params,
                                eh_site.entered_fields)
    return {"card": card, "digest": intake["digest"], "missing": intake.get("missing") or [],
            **({"request": intake["request"]} if intake.get("request") else {})}


def _run_view(task):
    run = task["run"]
    stages = []
    for name, stage in run["stages"].items():
        entry = {"stage": name, "status": stage["status"]}
        if stage.get("reason"):
            entry["reason"] = stage["reason"]
        if stage.get("gate"):
            entry["gate"] = [{"rule": r["rule"], "verdict": r["verdict"], "fact": r["facts"][0]["fact"],
                              "value": r["facts"][0]["value"],
                              **({"unit": r["facts"][0]["unit"]} if r["facts"][0]["unit"] else {}),
                              **({"reason": r["reason"]} if r.get("reason") else {})} for r in stage["gate"]["rules"]]
        stages.append(entry)
    due = run["status"] == "running" and run["stages"][run["stage"]]["status"] == "active" and all(
        s["status"] == "completed" for s in task["steps"]
        if s["id"].startswith(run["stage"] + ".") and not s.get("superseded"))
    return {"profile": run["profile"], "mode": run["mode"], "status": run["status"], "stage": run["stage"],
            **({"gate_due": True} if due else {}),
            **({"waiting": {k: run["waiting"][k] for k in ("kind", "ref", "since", "items", "notice")
                            if k in run["waiting"]}} if run.get("waiting") else {}),
            **({"intake": _intake_view(run)} if isinstance(run.get("intake"), dict) else {}),
            **({"handed_over": run["handed_over"]} if run.get("handed_over") else {}),
            **({"reason": run["reason"]} if run.get("reason") else {}),
            "next_stage": _next_stage(run, run["stage"]), "scope": (run.get("scope") or {}).get("preset"),
            "stages": stages,
            **({"loop": {**{k: run["loop"][k] for k in ("kind", "stage", "rounds", "limit", "refusals", "ended")
                            if k in run["loop"]},
                         # the browser gets the outcome only; `token` and `claimed_at` are the claim's own
                         **({"notice": {k: run["loop"]["notice"][k] for k in ("sent", "error")
                                        if k in run["loop"]["notice"]}} if "notice" in run["loop"] else {})}}
               if run.get("loop") else {}),
            **({"ledger": copy.deepcopy(run["ledger"])} if run.get("ledger") else {})}


def get_task(task_id: str) -> dict:
    with _LOCK:
        path, task = _read(task_id)
    if task.get("run"):
        _advance_run(path)
        with _LOCK:
            _, task = _read(task_id)
        return _with_envelope(path, _view(task, fresh=True))
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
        run = task.get("run")
        if run and run["status"] == "needs_user" and run["stages"][run["stage"]]["status"] == "failed":
            # The user fixed the inputs: the harness checks the same gate again
            # from native facts (attempts + 1); nothing the caller says reaches it.
            run["status"], run["stages"][run["stage"]]["status"], task["status"] = "running", "active", "ready"
            _save(path, task)
        elif run and run["status"] in ("needs_user", "paused") and run.get("reason"):
            # The runner stopped it (a card, a budget, a lock, an unknown job,
            # a crash loop): the user's resume lets it look again, and the
            # runner's own stop checks decide anew (harness/runner.py).
            run["status"], run["waiting"] = "running", None
            run.pop("reason")
            if task["status"] == "needs_review" and not any(s["status"] == "uncertain" for s in task["steps"]):
                task["status"] = "ready"
            _save(path, task)
        session.task_id = task_id
    if task.get("run"):
        _advance_run(path)
        with _LOCK:
            _, task = _read(task_id)
        _wake(path)
    return _view(task, fresh=True)


def _own_previews(task):
    """SC-07 S5b (GK B 2a): the (step id, site_id, proposal_id) of the previews this task's own
    completed site_scene_prepare steps made, read from their stored results only."""
    out = []
    for step in task["steps"]:
        if step["tool"] != SCENE_PREPARE or step["status"] != "completed" or not isinstance(step.get("result"), dict):
            continue
        args = (step["result"].get("apply_with") or {}).get("args") or {}
        if isinstance(args, dict) and isinstance(args.get("site_id"), str) and isinstance(args.get("proposal_id"), str):
            out.append((step["id"], args["site_id"], args["proposal_id"]))
    return out


def _cancel_previews(previews, project_uuid):
    """Cancel each through the owner-scoped G4 path (`site_scene_cancel`) in the task's own project,
    not whichever is in the foreground: idempotent, never another owner's, never relabels an applied
    proposal. Its claim and this cancel share `proposals._lock`, so an apply that claimed first wins
    and one that comes after refuses proposal_cancelled. A failure never blocks the task cancel; the
    step ids whose preview is not now cancelled or applied (a raise, `applying`, `not_found`) are
    returned to say so."""
    from services.site_scene import chat as scene_chat
    failed = []
    for step_id, site_id, pid in previews:
        try:
            result = scene_chat.cancel(site_id, pid, project_uuid=project_uuid)
        except Exception:  # noqa: BLE001 — reported in the cancel's result, never silent
            result = None
        if not isinstance(result, dict) or result.get("status") not in ("cancelled", "applied"):
            failed.append(step_id)
    return failed


def cancel_task(task_id: str) -> dict:
    from services import chat_tools
    previews = []
    with _LOCK:
        path, task = _read(task_id)
        # _read's own project: the run's when bound, else the foreground one
        project_uuid = (RUN_CONTEXT.get() or PyPSAService.get_active_context()).project_uuid
        if task["status"] != "completed":
            previews = _own_previews(task)
            task["status"] = "cancelled"
            if task.get("run"):
                task["run"]["status"] = "cancelled"
            for step in task["steps"]:
                if step.get("job", {}).get("continuation") in ("armed", "due"):
                    step["job"]["continuation"] = "withdrawn"  # never after a cancel
            _save(path, task)
            if task.get("run"):
                from services import run_envelope
                run_envelope.end(path, "revoked", "cancel_task")  # issue 03: nothing outlives the run
                _abort_engines(task)
        session = chat_tools.chat_session()
        if session and getattr(session, "task_id", None) == task_id:
            session.task_id = None
        view = _view(task)
    _wake(path)  # the runner lets go of the project
    # Outside the task lock: the scene cancel takes its own project and proposal locks.
    failed = _cancel_previews(previews, project_uuid)
    if failed:
        view["previews_not_cancelled"] = failed
    return view


def _abort_engines(task):
    """Issue 05 PR C (condition 1, C10): a cancelled run stops its running
    engine loop through the loop's own abort route, and only its own: the
    slot's record must be the step's (`started_at`, under the solver-state
    lock). The step settles from the loop's record once it ends."""
    for step in task["steps"]:
        if step["status"] == "waiting" and (step.get("job") or {}).get("kind") == "engine":
            _abort_engine(task, step)


def _abort_engine(task, step):
    """Stop one engine step's loop through its abort route, on the active
    context of the run's own project, only when the slot's record is the
    step's (`started_at`, under the solver-state lock). Cancel, and a step
    settled `timed_out` (issue 05 H2), use it."""
    from routers import results
    ctx = PyPSAService.get_active_context()
    if str(getattr(ctx, "project_uuid", None)) != task["project_id"]:
        return
    job = step.get("job") or {}
    with ctx.solver_state_lock:
        record = ctx.solver_state.get(job["study"])
        mine = isinstance(record, dict) and record.get("started_at") == job.get("started_at")
    if mine:
        try:
            results._abort_study(job["study"], "no loop has been run")
        except HTTPException:
            logger.warning("the engine loop of %s was already gone", step["id"])


def settle_engine(path, ticket, loop, state):
    """Settle a waiting engine step once (issue 05 PR C, C7): only while it
    still waits on this attempt. `state` "ended" settles from the loop's own
    `loop` record (its solves charged: solves_used + probe_solves + the
    closing re-solve when it ran, GK A (12) Q1); "lost" and "timed_out"
    settle uncertain and keep the whole reservation (condition 9). Returns
    the task view, or None when another path settled it first."""
    from services import run_envelope
    with _LOCK:
        try:
            task = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            return None
        step = next((s for s in task["steps"] if s["id"] == ticket[1]), None)
        if (task.get("task_id") != ticket[0] or step is None or step.get("attempt_id") != ticket[2]
                or step["status"] != "waiting" or (step.get("job") or {}).get("kind") != "engine"):
            return None
        if state == "ended":
            spent = int(loop.get("solves_used") or 0) + int(loop.get("probe_solves") or 0) \
                + (1 if loop.get("base_restore_status") is not None else 0)
            run_envelope.settle_engine_solves(path, ticket[2], spent)
            task = json.loads(Path(path).read_text())  # the solve count was written to it
            step = next(s for s in task["steps"] if s["id"] == ticket[1])
            run_envelope.settle_carded(task, step, spent)  # issue 07 B3a: a step-by-step loop's own count
            step["status"], step["result"] = "completed", {"loop": loop}
            step["job"]["outcome"] = loop.get("status")
            step.pop("error", None)
        else:
            step["status"], step["job"]["outcome"] = "uncertain", state
            step["error"] = ("The loop's own record is gone (lost with a restart, or another loop took its "
                             "place); its solves stay spent." if state == "lost" else
                             "The loop did not end in time; its solves stay spent.")
        step["job"]["continuation"] = "withdrawn"  # the runner goes on; no chat continuation
        if task["status"] != "cancelled":
            task["status"] = "needs_review" if step["status"] == "uncertain" else "ready"
        _save(Path(path), task)
        view = _view(task)
    _wake(path)
    return view


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
        _clear_waiting(task, card)
        _save(path, task)
        session.task_id = task_id
    if task.get("run"):
        _advance_run(path)  # inputs edited during the pause: the input gate is checked again first
        _wake(path)
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
            if step["tool"] == SCENE_APPLY and step.get("error_kind") in SCENE_APPLY_DEAD:
                _refuse("task_retry_needs_prepare", f"This 3D site apply failed with {step['error_kind']}: its "
                        "preview cannot be applied again. Tell the user, then cancel_task and start a new plan "
                        "with a fresh site_scene_prepare.")
            if step.get("retries", 0) >= TASK_STEP_RETRIES_MAX:
                _refuse("task_step_retry_limit", "This step has failed after 2 retries. Tell the user what failed and ask how to go on: they can fix the inputs, or cancel_task and start a new plan.")
            step["retries"] = step.get("retries", 0) + 1
            step["status"] = "pending"
            step.pop("result", None)
        for key in ("error", "error_kind", "late_receipt", "receipt"):
            step.pop(key, None)
        task["status"] = "completed" if _done(task) else "ready"
        _pause_at_boundary(task, step)
        # The chain never advances on a reconcile: nothing tells the step's own
        # effect from other edits made since it went uncertain, so inputs that
        # differ now read stale and the user reviews a new brief (fail closed).
        _save(path, task)
    if task.get("run"):  # the gate reads native facts; `result` never reaches it
        _advance_run(path)
        with _LOCK:
            _, task = _read(task_id)
    return _view(task, fresh=True)


def _job_ref(job):
    """The id a waiting step's job is known by: a queue or solve job's
    `job_id`, a run's report job's `report_id` (issue 07)."""
    return job.get("job_id") or job.get("report_id") or "?"


def _native_evidence(path, task, step):
    """(found, result) for a failed or uncertain step, from native records only."""
    if step["tool"] == SCENE_APPLY:
        return _scene_apply_evidence(path, task, step)
    if "late_receipt" in step:
        return True, step["late_receipt"]["result"]
    if (step.get("job") or {}).get("kind") == "queue":
        from harness import continuation
        try:
            status, _, result = continuation.queue_outcome(step["job"]["jobs"])
        except continuation.QueueBusy:
            return False, None  # no verdict now; the user can reconcile again
        return (True, {"job_id": step["job"]["job_id"], "final_status": "completed", **result}) \
            if status == "completed" else (False, None)
    if (step.get("job") or {}).get("kind") == "report":
        # Issue 07 (GK B): only the run's own report job that ended `done` and
        # whose document reads back; a failed, refused or lost one never.
        from harness import run_report
        job = step["job"]
        if job.get("outcome") != "done":
            return False, None
        try:
            from services.reports import store
            store.load_report(path.parent.parent, job["report_id"], job.get("version"))
        except Exception:  # noqa: BLE001 — an unreadable report is no evidence (C3)
            return False, None
        return True, {"kind": run_report.KIND, "report_id": job["report_id"], "version": job.get("version")}
    from services import run_actions
    if (step.get("job") or {}).get("kind") == run_actions.JOB_KIND:
        # Study harness 10a: only a runner tick settles the campus job from its own
        # row (`run_actions.settle`); a lost one is never reconciled or queued again.
        return False, None
    if (step.get("job") or {}).get("kind") == "eh_study":
        # Issue 07 B3a (GK B (32) L4): only the run's own Energy Hub study
        # record; never the solve-run branch below.
        from harness import run_eh
        return run_eh.evidence(path, task, step)
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


def _scene_apply_evidence(path, task, step):
    """N4: the scene chain's receipt for the plan's own preview, counted only
    when its input_hash is the one the prepare step returned. Nothing else
    (a late result, the edit read-back) stands in for it."""
    try:
        args = _resolve(step["args"], task["steps"])
        from services.site_scene import proposals
        receipt = proposals._committed(path.parent.parent, args["site_id"], args["proposal_id"])
    except Exception:  # noqa: BLE001 — a lock timeout or unreadable chain is no evidence
        return False, None
    if not isinstance(receipt, dict) or receipt.get("input_hash") != args["input_hash"]:
        return False, None
    return True, {"receipt": {k: receipt.get(k) for k in ("proposal_id", "input_hash", "revision", "changed")},
                  "read_back": {"receipt_found": True}}


def admit(session, tool, args):
    """Before any card: a bound plan's later steps never run out of order, and
    a step waiting on its job never runs twice. Other calls pass untouched."""
    task_id = getattr(session, "task_id", None)
    if not task_id or tool in _CONTROLS:
        return
    with _LOCK:
        try:
            path, task = _read(task_id)
        except HTTPException:
            return  # begin_step detaches a task this project cannot read
        if task["status"] in ("cancelled", "completed"):
            return
        step = _next(task)
        for later in task["steps"]:
            if later["status"] == "completed" or later.get("superseded") or later["tool"] != tool:
                continue
            try:
                expected = _resolve(later["args"], task["steps"])
            except HTTPException:
                continue  # its arguments still wait on an earlier result
            if not _same_args(tool, expected, args):
                continue
            if later is step and step["status"] == "waiting":
                raise HTTPException(409, {"error_kind": "task_step_waiting", "message":
                                          f"Step {step['id']} is waiting on job {_job_ref(step['job'])}; "
                                          "the conversation continues when it ends."})
            if later is not step:
                raise HTTPException(409, {"error_kind": "task_step_out_of_order", "message":
                                          f"Step {later['id']} ({tool}) cannot start before step {step['id']} "
                                          f"({step['tool']}) completes; it is {step['status']}."})
            _gate_brief(task, step)
            if len(json.dumps(expected, default=str)) > ARGS_CHARS:
                _refuse("task_reference_too_large", "Resolved arguments exceed the step's limit; use file IDs or smaller result references.")
            if task.get("run") and step["status"] == "running" and step.get("session_id") != session.session_id:
                # past pending -> running the step's status is the authority, not its (lapsing) claim
                _refuse("task_step_claimed", f"Step {step['id']} is being run by another session of this plan; "
                        "wait for it, then read the plan again.")
            if task.get("run") and step["status"] == "pending":
                _check_claim(task, step, session.session_id)
                _take_claim(step, session.session_id)
                _save(path, task)
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


def await_job(ticket, job_id, session_id, *, jobs=None):
    """A run step whose solve is still running waits on its job, durably.

    The turn is released; `settle_job` records the outcome when the job ends,
    and the conversation continues once (`claim_continuation`). A queued step
    passes `jobs` (`continuation.queue_jobs`) and `job_id=None`; the first job
    keys its continuation."""
    if ticket is None or jobs == []:
        return None
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
                or step["status"] != "waiting" or step["job"].get("job_id") != job_id):
            return None
        _settle(task, step, native_status, step_status, result)
        _save(Path(path), task)
        view = _view(task)
    _wake(path)  # the runner gates the settled stage (a follower thread holds no context)
    return view


def _settle(task, step, native_status, step_status, result):
    step["status"] = step_status
    if isinstance(result, dict) and result.get("cases"):
        step["job"]["cases"] = result["cases"]  # LC-17: each queued case's own outcome
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
        task["status"] = ("needs_review" if step_status == "uncertain" else
                          "completed" if _done(task) else "ready")
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
        if step["status"] != "waiting" or step["job"].get("kind") in ("engine", "report", "eh_study"):
            # An engine step: the runner's recover reads its loop's own record (issue 05 PR C);
            # a run's report job: the runner settles it from its job record (issue 07);
            # a run's EH study: `run_eh.recover` settles it and its reservation (issue 07 B3a).
            continue
        if step["job"].get("kind") == "queue":
            # Followed by a thread of this process, or lost with a restart (SC-09).
            # A cancelled plan's job runs on and is reported as it is (SC-13).
            lost = task["status"] != "cancelled" and \
                (task["task_id"], step["id"], step.get("attempt_id")) not in _QUEUE_FOLLOWERS
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
    from harness.run_lifecycle import continuation_note
    cases = "".join(f"; {c.get('name') or c.get('project_id')}: {c['status']}" for c in job.get("cases") or [])
    return f"job {job['job_id']} finished: {job['outcome']} (plan: {task['title']}){cases}{continuation_note(job['outcome'])}"


def continuation_armed(session) -> bool:
    """LC-24: a plan step of this conversation is waiting on a job and will
    continue it (armed or due), so a promise of a later report is true."""
    from harness import job_adapters
    with _LOCK:
        for path, task in _owned_tasks():
            for step in task["steps"]:
                job = step.get("job") or {}
                # Only a foreground-solve handle: a plan armed on a study or
                # queue job says nothing about the solve running now.
                if job.get("session_id") == session.session_id and job.get("continuation") in ("armed", "due") \
                        and str(job.get("job_id") or "").startswith(job_adapters.FOREGROUND_PREFIX):
                    return True
    return False


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


def is_next_step(session, tool, args) -> bool:
    """True for the bound plan's next step: it runs as that step, with its own
    card, so chat edit routing leaves it alone (harness/edit_routing.py)."""
    task_id = getattr(session, "task_id", None)
    if not task_id or tool in _CONTROLS:
        return False
    with _LOCK:
        try:
            _, task = _read(task_id)
            step = _next(task)
            if task["status"] in ("cancelled", "completed") or step is None or step["tool"] != tool:
                return False
            return _same_args(tool, _resolve(step["args"], task["steps"]), args)
        except HTTPException:
            return False


def governs(session, tool, args) -> bool:
    """Whether `begin_step` refuses this call itself: the task's next step is
    this tool with these arguments and is waiting, running or uncertain, or
    the call repeats a completed step (`task_step_already_completed`). Issue 04 S5 (GK B (30) N7): such a call
    gets only `begin_step`'s refusal, never a job hold's too. A task that
    cannot be read governs nothing, so the hold check still runs (#1079)."""
    task_id = getattr(session, "task_id", None)
    if not task_id or tool in _CONTROLS:
        return False
    with _LOCK:
        try:
            _, task = _read(task_id)
            if task["status"] in ("cancelled", "completed"):
                return False
            step = _next(task)
            if step is not None and step.get("native"):
                return False
            if step is not None and step["tool"] == tool and _same_args(
                tool, _resolve(step["args"], task["steps"]), args
            ):
                # Only a step begin_step refuses; one it admits still meets
                # the hold check (GK B (30) T1: no tasked replay of a held call).
                return step["status"] in ("waiting", "running", "uncertain")
            return any(
                done["status"] == "completed"
                and done["tool"] == tool
                and _same_args(tool, _resolve(done["args"], task["steps"]), args)
                for done in task["steps"]
            )
        except Exception:  # noqa: BLE001 - unreadable: the hold check runs (fail closed)
            return False


def admits_next_step(session, tool, args) -> bool:
    """Whether this call is the task's next step, same tool and arguments, that
    `begin_step` admits (not waiting, running or uncertain). Issue 04 S5 (GK B
    (30) U1): such a step skips only the N1 store read, since the handler and
    `begin_step` record it; session holds still apply (T1). A task that cannot
    be read admits nothing, so the store read runs (fail closed)."""
    task_id = getattr(session, "task_id", None)
    if not task_id or tool in _CONTROLS:
        return False
    with _LOCK:
        try:
            _, task = _read(task_id)
            if task["status"] in ("cancelled", "completed"):
                return False
            step = _next(task)
            return (
                step is not None
                and not step.get("native")
                and step["tool"] == tool
                and _same_args(tool, _resolve(step["args"], task["steps"]), args)
                and step["status"] not in ("waiting", "running", "uncertain")
            )
        except Exception:  # noqa: BLE001 - unreadable: the store read runs (fail closed)
            return False


def begin_step(session, tool, args):
    """Called only after the ordinary offered-tool and confirmation gates."""
    from harness import run_eh
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
        if step is not None and step.get("native"):
            return None  # issue 07: the runner's own step; a chat call is an unrelated one (GK B stage-7 ruling)
        expected = _resolve(step["args"], task["steps"]) if step and step["tool"] == tool else None
        if step is None or step["tool"] != tool or not _same_args(tool, expected, args):  # a run at a failed gate has none
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
        _check_claim(task, step, session.session_id)
        attempt = uuid.uuid4().hex
        if task.get("run") and run_eh.is_eh_step(step):
            # Issue 07 B2 (C1, C2, F1): the frozen args and goal, and the solves left,
            # checked and reserved at every execution of the run's EH study.
            refused = run_eh.admit(path, task, {**step, "attempt_id": attempt}, args,
                                   RUN_CONTEXT.get() or PyPSAService.get_active_context())
            if refused is not None:
                _save(path, task)  # the run, stopped for the user
                if refused[0] == run_eh.BUDGET_KIND:
                    _refuse("eh_budget", refused[1])
                if refused[0] == run_eh.CONTINGENCY_KIND:  # study harness 12c (G1)
                    _refuse("eh_contingencies", refused[1])
                _refuse("eh_args", refused[1])
        step.pop("claim", None)
        _clear_waiting(task, step["id"])
        step["status"], step["session_id"] = "running", session.session_id
        step["attempt_id"] = attempt
        task["status"] = "running"
        _save(path, task)
        return (task_id, step["id"], step["attempt_id"])


def is_run_ticket(ticket):
    """True when the ticket's task is a harness run (not a plan)."""
    try:
        return bool(_read(ticket[0])[1].get("run"))
    except HTTPException:
        return False


def _outcome(tool, result, error, run=False):
    """(status, error) of one finished call, from its own result only."""
    receipt = result.get("_edit_receipt") if isinstance(result, dict) else None
    if error:
        return ("failed" if _retryable_read(tool) else "uncertain"), str(error)[:300]
    if tool == SCENE_PREPARE and isinstance(result, dict) and result.get("status") in SCENE_PREPARE_ASKS:
        # SC-07 S4 (GK B H3): a clarify is the user's choice, never a completed
        # step, so no later apply ref resolves against it.
        return "failed", ("The 3D site preview needs the user's choice of model item. Ask them, then "
                          "cancel_task and start a new plan with the item they chose.")
    if tool == "refresh_experiment" and isinstance(result, dict) and result.get("status") != "completed":
        # Only a completed case with intact evidence completes the step (issue 23).
        if result.get("status") in ("ready", "submitting", "submitted", "running"):
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
    if tool in QUEUE_WAIT_TOOLS and not (run and tool in PLAN_ONLY_WAIT_TOOLS) and isinstance(result, dict) and (
        any(isinstance(c, dict) and c.get("status") != "completed" for c in result.get("cases") or [])
        if "cases" in result else True
    ):
        # Only a sweep whose every case was reused from a solved child completes
        # from its receipt; a started job settles from its own record.
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


def finish_step(ticket, result=None, error=None, error_kind=None):
    if ticket is None:
        return None
    with _LOCK:
        path, task = _read(ticket[0])
        step = next(s for s in task["steps"] if s["id"] == ticket[1])
        if step.get("attempt_id") != ticket[2]:
            return None
        if step.get("job"):
            return _view(task)  # waiting on its job, or already settled by it
        step["status"], message = _outcome(step["tool"], result, error, bool(task.get("run")))
        from harness import run_eh
        if task.get("run") and run_eh.is_eh_step(step):
            # Issue 07 B2: the run's EH study is settled from its own record only (`run_eh.wait`).
            if error is not None or not isinstance(result, dict) or result.get("status") != "running":
                # F5: refused before it started (409 busy, 422, a campaign cap): nothing
                # spent, the reservation released, and the step failed with no retry.
                run_eh.release(path, ticket[2], 0, task)
                step["status"] = "failed"
                message = f"The Energy Hub study did not start ({run_eh.REFUSED_KIND}): {str(error or result)[:200]}"
            else:
                run_eh.release(path, ticket[2], None, task)  # C4: unfollowed, so its whole reservation
                step["status"] = "uncertain"
                message = "The Energy Hub study started, but its record is not followed; inspect it before going on."
        if task.get("run") and step["tool"] == "run_investment_case":
            # Study harness 14: a run's investment case is settled from its own record only
            # (`run_ic.wait`); a receipt that is not that of a started case is a refusal, and a
            # started one that is not followed is not known to have finished.
            if error is not None or not isinstance(result, dict) or result.get("status") != "running":
                step["status"] = "failed"
                message = f"The investment case did not start: {str(error or result)[:200]}"
            else:
                step["status"] = "uncertain"
                message = "The investment case started, but its record is not followed; inspect it before going on."
        if task.get("run") and error is None and _retryable_read(step["tool"]):
            # A run's read step is done once it returned: what it found (an
            # `ok: false` validation) is the stage gate's to judge, from its
            # own native read (fail closed there, not here).
            step["status"], message = "completed", None
        receipt = result.get("_edit_receipt") if isinstance(result, dict) else None
        step.pop("error_kind", None)
        if message:
            step["error"] = message
            if error is not None and isinstance(error_kind, str):
                step["error_kind"] = error_kind[:64]  # what resolve_task_step's retry reads (N3)
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
            task["status"] = "needs_review" if step["status"] == "uncertain" else "completed" if _done(task) else "ready"
            _pause_at_boundary(task, step)
        _save(path, task)
    if task.get("run"):
        _advance_run(path)
        with _LOCK:
            _, task = _read(ticket[0])
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
            or step.get("job") or _outcome(step["tool"], result, None, bool(task.get("run")))[0] != "completed":
        return
    try:
        step["late_receipt"] = {"result": _stored(result)}
    except (TypeError, ValueError):
        return
    _save(Path(path), task)


def _same_args(tool, first, second):
    from harness.run_lifecycle import FLAG
    from services import chat_tools
    # LC-19: the re-run flag acknowledges current results; it is not an input.
    first, second = ({k: v for k, v in args.items() if k != FLAG} for args in (first, second))
    try:
        signature = inspect.signature(chat_tools.DISPATCHERS[tool])
        a, b = signature.bind(**first), signature.bind(**second)
        a.apply_defaults()
        b.apply_defaults()
        return a.arguments == b.arguments
    except TypeError:
        return False
