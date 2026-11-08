"""Study harness 10a: the runner's native actions (GK B (36) D1, D2, D5-D7).

A closed registry of steps only the runner runs (`harness/runner.py`'s
native dispatch). They are absent from the catalogue, from every tool list
and allowlist, and from the provider offer: a model turn that names one is
refused `unknown_tool` (`run_envelope._refusal`), and a chat call never
claims one (`assistant_tasks.begin_step` leaves native steps alone).

- `dc_campus_study` queues the project's campus run (a solve-queue job of
  kind `campus`, `campus_electrical_service.enqueue_run`) held to the run's
  frozen grid-code profile, then waits on that job. A runner tick settles it
  from the job's own row (`settle`). The job does no PyPSA optimisation: it
  runs pandapower AC flows and the greedy `invest_campus`. It writes only
  `campus_electrical/run/` (published by `_publish_run`, with
  `used_library` and the cost basis when investing) and
  `campus_electrical/settings.json`. It never writes the network or the
  solver config (D6), and the frozen record lists both writes for the
  solve stage's card. The job's row carries no solve count, so the step
  reserves the stated upper bound, `SOLVES`, and settles exactly that (D5).
- `dc_grid_readiness` reads the scorecard of the run's project with the
  frozen profile (`grid_readiness_for`) and stores that profile's digest.
  The result gate's scorecard rows read again with the same id, and give no
  evidence unless the digest still matches (D2).

The grid-code profile is frozen at solve-stage entry (`freeze`), from the
project's published profiles, with its id, label and content digest
(`campus_electrical_service.profile_fingerprint`). With more than one
published, the profile is the one the user picked on the campus card (the
campus settings), never one the runner picks (D1). The solve stage's card
is always the user's, in envelope mode too (`run_envelope.cross`), so it
names the profile. Each dispatch compares the live digest with the frozen
one. A republished or edited profile stops the run for the user, and the
run never reads the new one.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

KIND = "action"  # a step's `native` marker
CAMPUS = "dc_campus_study"
READINESS = "dc_grid_readiness"
STAGE = "solve"
# The registry: each action's stage and whether it only reads. Closed: a profile
# naming anything else is refused at load (D7).
REGISTRY = {CAMPUS: {"stage": STAGE, "reads_only": False}, READINESS: {"stage": STAGE, "reads_only": True}}
FROZEN = {"frozen": "dc_grid_code"}  # an action's args until the harness freezes them; never a model's
JOB_KIND = "dc_campus"
EVENT = "dc_campus"  # the envelope ledger row of the campus run's reservation
# D5: the campus job's row carries no solve count. One campus job, and it runs no
# PyPSA optimisation (pinned by `test_run_profile_datacenter`), so this bound is stated, never measured.
SOLVES = 1
_TERMINAL = ("completed", "failed", "aborted", "interrupted")


class FreezeError(ValueError):
    """The run's grid-code profile cannot be fixed now. `state` is the fact's
    no-evidence state and the message what the user does about it."""

    def __init__(self, state, message):
        super().__init__(message)
        self.state = state


def check(action: dict, stage: str) -> None:
    """A profile's native action (`run_profiles.load`): one of the registry's,
    in its own stage, with the frozen marker as its only args (D7)."""
    tool = action.get("tool")
    if tool not in REGISTRY or action.get("native") != KIND:
        raise ValueError(f"{tool!r} is not one of the runner's native actions ({', '.join(sorted(REGISTRY))}).")
    if action.get("args") != FROZEN:
        raise ValueError(f"{tool} takes only the harness's frozen marker as its args.")
    if REGISTRY[tool]["stage"] != stage:
        raise ValueError(f"{tool} runs in the {REGISTRY[tool]['stage']} stage only.")


def is_action(step) -> bool:
    return isinstance(step, dict) and step.get("native") == KIND and step.get("tool") in REGISTRY


def reads_only(step) -> bool:
    return is_action(step) and REGISTRY[step["tool"]]["reads_only"]


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _project_dir(project) -> Path:
    from services import project_registry
    return Path(project_registry.project_dir(project))


def _saved_settings(project) -> dict:
    from services import campus_electrical_service as ce
    try:
        saved = json.loads((_project_dir(project) / ce.SUBDIR / ce.SETTINGS_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def published(project) -> dict:
    """`{id: title}` of the project's published grid-code profiles."""
    from services import campus_grid_code_service as gc
    return {p["id"]: p.get("title") for p in gc.list_grid_codes(project)["published"]}


def choose(project) -> dict:
    """D1: `{id, label, digest}` of the profile the run is held to. Raises
    FreezeError when none is published, when more than one is and the
    campus card names none of them, or when it no longer loads."""
    from services import campus_electrical_service as ce
    codes = published(project)
    if not codes:
        raise FreezeError("none_published", "publish the grid-code profile")
    if len(codes) == 1:
        profile_id = next(iter(codes))
    else:
        profile_id = _saved_settings(project).get("profile")
        if profile_id not in codes:
            raise FreezeError("not_picked", "more than one grid-code profile is published: pick the run's "
                                            "profile on the campus card")
    fingerprint = ce.profile_fingerprint(project, profile_id)
    if fingerprint is None:
        raise FreezeError("unloadable", f"the published grid-code profile {profile_id!r} no longer loads")
    return {"id": profile_id, "label": codes[profile_id] or profile_id, "digest": fingerprint}


def _core(frozen) -> dict:
    return {k: frozen.get(k) for k in ("grid_code", "settings", "writes", "solves")}


def freeze(project) -> dict:
    """The actions' frozen record: the grid-code profile (D1), the campus
    run's settings (the saved campus settings held to that profile), what
    the run writes (D6) and the solves it reserves (D5), with their digest."""
    from fastapi import HTTPException

    from services import campus_electrical_service as ce
    code = choose(project)
    saved = {k: v for k, v in _saved_settings(project).items() if k in ce.DEFAULTS}
    try:
        settings = ce._settings({**saved, "profile": code["id"]}, ce.profiles(project))
    except HTTPException as exc:
        raise FreezeError("settings", f"the campus settings cannot be used: {exc.detail}") from exc
    frozen = {"grid_code": code, "settings": settings,
              "writes": [f"{ce.SUBDIR}/run/ (the published campus study)",
                         f"{ce.SUBDIR}/{ce.SETTINGS_FILE} (the campus settings, held to {code['id']})"],
              "solves": SOLVES}
    return {**frozen, "digest": digest(frozen)}


def fill(steps, frozen) -> None:
    """Put the frozen record on freshly materialised actions (in place). With
    none, a step keeps the marker alone, which `start` refuses."""
    for step in steps:
        if is_action(step) and step["args"] == FROZEN and frozen is not None:
            step["frozen"] = copy.deepcopy(frozen)


def stage_frozen(task) -> dict | None:
    """At solve-stage entry, as the run's owner: the frozen record, or None
    when it cannot be fixed (the step then stops for the user). A stage
    entered again (L2's re-solve) keeps the run's first record, so a profile
    republished meanwhile is caught at dispatch, never frozen anew (D1)."""
    from fastapi import HTTPException

    from services import chat_tools, project_registry
    prior = next((s["frozen"] for s in reversed(task.get("steps") or [])
                  if is_action(s) and isinstance(s.get("frozen"), dict)), None)
    if prior is not None:
        return copy.deepcopy(prior)
    try:
        with chat_tools._acting() as (db, user):
            return freeze(project_registry.resolve_project(db, user, task["project_id"]))
    except (FreezeError, HTTPException):
        logger.info("run %s: the grid-code profile could not be frozen", task.get("task_id"))
        return None


def latest(task, tool) -> dict | None:
    """The run's latest action `tool` (a stage entered again numbers on)."""
    return next((s for s in reversed(task.get("steps") or [])
                 if is_action(s) and s["tool"] == tool and not s.get("superseded")), None)


def frozen_code(task) -> dict | None:
    """The grid-code profile the run's latest readiness step was frozen to."""
    step = latest(task, READINESS)
    code = ((step or {}).get("frozen") or {}).get("grid_code")
    return code if isinstance(code, dict) else None


def unchanged(project, code) -> bool:
    """D1: the profile is still published with the frozen content."""
    from services import campus_electrical_service as ce
    try:
        return code["id"] in published(project) and ce.profile_fingerprint(project, code["id"]) == code["digest"]
    except Exception:  # noqa: BLE001 — an unreadable profile is not the frozen one
        return False


# ── dispatch (the runner, on the run's bound context) ─────────────────────────


def _stop(path, step_id, reason, detail=None):
    """A step that did not run: failed with `reason`, the run waits for the user."""
    from harness import runner

    def change(task):
        step = next(s for s in task["steps"] if s["id"] == step_id)
        step["status"] = "failed"
        step["error"] = (f"{reason}: {detail}" if detail else reason)[:480]
        run = task["run"]
        run["stages"][STAGE].update(status="failed", reason=reason)
        if run["status"] in ("running", "waiting_job"):
            run["status"], task["status"] = "needs_user", "needs_review"

    runner._update(path, change)
    return reason


def _reserve(path, call_id):
    """D5: reserve `SOLVES` on a granted envelope's ledger before the job
    starts. False when the solves left do not cover it."""
    from services import assistant_tasks, run_envelope
    with assistant_tasks._LOCK:
        envelope = run_envelope._load(path)
        if envelope is None or envelope.get("state") != "granted":
            return True
        if envelope["budget"]["solves"] - run_envelope.solves_used(envelope) < SOLVES:
            return False
        run_envelope._audit(envelope, EVENT, call_id=call_id, tool=CAMPUS, reserved=SOLVES)
        run_envelope._save(path, envelope)
        run_envelope._count_solves(path, envelope)
    return True


def release(path, call_id, solves=SOLVES):
    """Settle the reservation (once): `solves`, the stated bound once the job
    started, 0 when it never did. No-op without a row."""
    from services import run_envelope
    return run_envelope.settle_engine_solves(path, call_id, solves, event=EVENT)


def start(path, ctx, step, *, instance) -> str:
    """Run one native action. Returns the action's name when it ran or
    started, else the refusal reason (the run then needs the user)."""
    from fastapi import HTTPException

    from harness import runner
    from services import chat_tools, project_registry

    path = Path(path)
    attempt = uuid.uuid4().hex
    claimed = {}

    def claim(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if s["status"] != "pending":
            return False
        s.update(status="running", attempt_id=attempt)
        claimed["ok"] = True

    runner._update(path, claim)
    if not claimed:
        return "claimed_elsewhere"
    task = json.loads(path.read_text())
    step = next(s for s in task["steps"] if s["id"] == step["id"])
    frozen = step.get("frozen")
    if not is_action(step) or step.get("args") != FROZEN or not isinstance(frozen, dict) \
            or digest(_core(frozen)) != frozen.get("digest"):
        return _stop(path, step["id"], "dc_args", "the action runs only with the record its card showed")
    if str(getattr(ctx, "project_uuid", "")) != task["project_id"]:
        return _stop(path, step["id"], "project_changed", "the bound project is not the run's")
    try:
        with chat_tools._acting() as (db, user):
            project = project_registry.resolve_project(db, user, task["project_id"])
            code = frozen["grid_code"]
            if not unchanged(project, code):
                return _stop(path, step["id"], "grid_code_changed",
                             f"the grid-code profile {code['id']!r} was republished, edited or withdrawn since "
                             "the card showed it; review the run again")
            if step["tool"] == READINESS:
                return _readiness(path, step, project, code)
            return _campus(path, step, project, user, frozen, attempt)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return _stop(path, step["id"], str(detail.get("error_kind") or "dc_refused"),
                     str(detail.get("message") or "")[:240])
    except Exception as exc:  # noqa: BLE001 — a launch that did not start fails the step, never strands it running
        logger.exception("native action %s failed for run %s", step["tool"], path.stem)
        return _stop(path, step["id"], "dc_start_failed", type(exc).__name__)


def _readiness(path, step, project, code):
    """The scorecard with the frozen profile, from a campus run held to it."""
    from harness import runner
    from services import campus_electrical_service as ce
    from services.adequacy import grid_readiness
    since = ce.grid_code_since_run(project, code["id"])
    if (since or {}).get("status") != "same":
        return _stop(path, step["id"], "campus_run_stale",
                     "the published campus run is not held to the run's grid-code profile")
    card = grid_readiness.grid_readiness_for(project, code["id"])
    result = {"profile": code["id"], "digest": code["digest"], "summary": card.get("summary"),
              "rows": [{"id": r["id"], "verdict": r["verdict"], "assumed": bool(r.get("assumed"))}
                       for r in card.get("rows") or []],
              "excluded": [{"id": r["id"], "reason": r.get("reason")} for r in card.get("excluded") or []]}

    def done(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if s["status"] != "running":
            return False
        s.update(status="completed", result=result)

    runner._update(path, done)
    return READINESS


def _campus(path, step, project, user, frozen, attempt):
    """Q2: no campus file is the user's to draft. Then the reservation, the
    job, and the step waiting on it."""
    from harness import runner
    from services import campus_electrical_service as ce
    if not (_project_dir(project) / ce.SUBDIR / ce.CAMPUS_FILE).is_file():
        return _stop(path, step["id"], "campus_missing", "draft the campus")
    if not _reserve(path, attempt):
        return _stop(path, step["id"], "budget", f"the campus run needs {SOLVES} solve and the run has none left")
    try:
        job = ce.enqueue_run(project, frozen["settings"], user)
    except BaseException:
        release(path, attempt, 0)  # it never started
        raise
    begun = time.time()

    def wait(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if s.get("attempt_id") != attempt or s["status"] != "running":
            return False
        s["status"] = "waiting"
        s["job"] = {"kind": JOB_KIND, "job_id": str(job["id"]), "call_id": attempt, "started_at": begun}
        if t["status"] != "cancelled":
            t["status"] = "waiting_job"

    runner._update(path, wait)
    return CAMPUS


def _job(job_id):
    from services import solve_queue as sq
    try:
        return sq.solve_queue.get_job(uuid.UUID(str(job_id)))
    except (ValueError, TypeError):
        return None


def outcome(job, used, code) -> tuple[str, str | None]:
    """(outcome, error) of the campus job: `done` only for a completed job
    whose published run was held to the frozen profile and content."""
    if not isinstance(job, dict):
        return "lost", "the campus job is gone (a restart); it is never queued again"
    status = job.get("status")
    if status != "completed":
        return str(status), f"the campus run ended {status}: {str(job.get('error') or '')[:200]}"
    if used != {"profile": code["id"], "sha256": code["digest"]}:
        return "stale", "the published campus run is not held to the run's grid-code profile"
    return "done", None


def settle(path, ctx=None) -> str | None:
    """Each tick: settle the run's waiting campus step once its job ended,
    from the job's own row (matched by id) and the published run's
    `profile_used`. Anything but `done` fails the step and the run waits
    for the user; the reservation settles to the stated bound either way."""
    from harness import runner
    from services import campus_electrical_service as ce

    path = Path(path)
    task = json.loads(path.read_text())
    step = next((s for s in task.get("steps") or []
                 if is_action(s) and s["status"] == "waiting" and (s.get("job") or {}).get("kind") == JOB_KIND), None)
    if step is None:
        return None
    job = _job(step["job"]["job_id"])
    if isinstance(job, dict) and job.get("status") not in _TERMINAL:
        return None
    used = ce._profile_used(path.parent.parent / ce.SUBDIR / "run")  # the task file's project
    result, error = outcome(job, used, step["frozen"]["grid_code"])
    release(path, step["job"]["call_id"])

    def done(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if s["status"] != "waiting" or (s.get("job") or {}).get("job_id") != step["job"]["job_id"]:
            return False
        s["job"].update(outcome=result, finished_at=time.time())
        if t["status"] == "waiting_job":
            t["status"] = "running"
        if result == "done":
            s.update(status="completed", result={"job_id": s["job"]["job_id"], "solves": SOLVES,
                                                 "grid_code": s["frozen"]["grid_code"]})
            if t["run"]["status"] == "waiting_job":
                t["run"]["status"] = "running"
            return
        s.update(status="failed", error=error[:480])
        t["run"]["stages"][STAGE].update(status="failed", reason="dc_campus_" + result)
        if t["run"]["status"] in ("running", "waiting_job"):
            t["run"]["status"], t["status"] = "needs_user", "needs_review"

    runner._update(path, done)
    return result
