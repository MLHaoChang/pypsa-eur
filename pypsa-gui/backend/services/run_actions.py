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
REGISTRY = {
    CAMPUS: {"stage": STAGE, "reads_only": False},
    READINESS: {"stage": STAGE, "reads_only": True},
}
FROZEN = {
    "frozen": "dc_grid_code"
}  # an action's args until the harness freezes them; never a model's
JOB_KIND = "dc_campus"
EVENT = "dc_campus"  # the envelope ledger row of the campus run's reservation
# D5: the campus job's row carries no solve count. One campus job, and it runs no
# PyPSA optimisation (pinned by `test_run_profile_datacenter`), so this bound is stated, never measured.
SOLVES = 1
# The data-centre profile's campus study (GK B (36) F2 (a)): N-1 sizing with the
# investment pass, whatever the project's saved campus settings say.
CAMPUS_STUDY = {"n_minus_1": True, "invest": True}
_TERMINAL = ("completed", "failed", "aborted", "interrupted")
# The attempts this process is running now: a step left `running` by any other
# (a restart mid-dispatch) is settled uncertain by `settle`, never run again.
_LIVE: set = set()
UNCERTAIN = (
    "The native action was interrupted mid-dispatch; its outcome cannot be verified."
)


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
        raise ValueError(
            f"{tool} is not one of the runner's native actions ({', '.join(sorted(REGISTRY))})."
        )
    if action.get("args") != FROZEN:
        raise ValueError(f"{tool} takes only the harness's frozen marker as its args.")
    if REGISTRY[tool]["stage"] != stage:
        raise ValueError(f"{tool} runs in the {REGISTRY[tool]['stage']} stage only.")


def is_action(step) -> bool:
    return (
        isinstance(step, dict)
        and step.get("native") == KIND
        and step.get("tool") in REGISTRY
    )


def reads_only(step) -> bool:
    return is_action(step) and REGISTRY[step["tool"]]["reads_only"]


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def _project_dir(project) -> Path:
    from services import project_registry

    return Path(project_registry.project_dir(project))


def _saved_settings(project) -> dict:
    from services import campus_electrical_service as ce

    try:
        saved = json.loads(
            (_project_dir(project) / ce.SUBDIR / ce.SETTINGS_FILE).read_text()
        )
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
            raise FreezeError(
                "not_picked",
                "more than one grid-code profile is published: pick the run's "
                "profile on the campus card",
            )
    fingerprint = ce.profile_fingerprint(project, profile_id)
    if fingerprint is None:
        raise FreezeError(
            "unloadable",
            f"the published grid-code profile {profile_id} no longer loads",
        )
    return {
        "id": profile_id,
        "label": codes[profile_id] or profile_id,
        "digest": fingerprint,
    }


def _core(frozen) -> dict:
    return {k: frozen.get(k) for k in ("grid_code", "settings", "writes", "solves")}


def freeze(project, study=None) -> dict:
    """The actions' frozen record: the grid-code profile (D1), the campus
    run's settings (the saved campus settings, with the run profile's own
    `campus_study` block winning for the keys it names, held to that grid
    code; GK B (36) F2 (a)), what the run writes (D6) and the solves it
    reserves (D5), with their digest."""
    from fastapi import HTTPException

    from services import campus_electrical_service as ce

    study = CAMPUS_STUDY if study is None else study
    code = choose(project)
    saved = {k: v for k, v in _saved_settings(project).items() if k in ce.DEFAULTS}
    try:
        settings = ce._settings(
            {**saved, **study, "profile": code["id"]}, ce.profiles(project)
        )
    except HTTPException as exc:
        raise FreezeError(
            "settings", f"the campus settings cannot be used: {exc.detail}"
        ) from exc
    frozen = {
        "grid_code": code,
        "settings": settings,
        "writes": [
            f"{ce.SUBDIR}/run/ (the published campus study)",
            f"{ce.SUBDIR}/{ce.SETTINGS_FILE} (the campus settings, held to {code['id']})",
        ],
        "solves": SOLVES,
    }
    return {**frozen, "digest": digest(frozen)}


def card(frozen) -> dict:
    """What the step's card names (D1, D6): the profile with its id, label and
    digest, what the campus run writes, and the solves it reserves."""
    return {
        "grid_code": dict(frozen["grid_code"]),
        "writes": list(frozen["writes"]),
        "solves": frozen["solves"],
    }


def needs_card(step) -> bool:
    """GK B (36) F1: an action that writes (the campus run) runs only on its
    own Confirmation card, approved by the user; a read runs without one."""
    return is_action(step) and not REGISTRY[step["tool"]]["reads_only"]


# GK B (37) F4: every frozen setting is on the card (its digest binds them all).
SETTING_WORDS = {
    "k": "Contingency depth (k)",
    "pf": "Power factor",
    "margin": "Sizing margin",
    "pcc_switchgear_by_operator": "PCC switchgear owned by the operator",
    "pcc_tap_control": "PCC transformer taps moved by",
}


def _setting(key, value) -> str:
    if (
        key == "margin"
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return f"{value * 100:g}%"  # a fraction of the load (campus_electrical_service._settings: 0 to 2)
    if value is None:
        return "not set"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("_", " ")


def _note(field, note) -> dict:
    return {"role": "note", "field": field, "unit": "", "note": note}


def approval_card(step) -> dict | None:
    """GK B (36) F1: the step's Confirmation card, as an edit card of note
    rows (the chat's card shows them as they are, no new UI). It names the
    grid-code profile with its id and short digest, the campus settings that
    run, each write and the solves reserved. Its digest is the frozen
    record's, so an approval binds to exactly what the card showed. None
    when the step has no frozen record."""
    frozen = step.get("frozen") if isinstance(step, dict) else None
    if not isinstance(frozen, dict) or not isinstance(frozen.get("digest"), str):
        return None
    from services import campus_electrical_service as ce

    code, settings, solves = frozen["grid_code"], frozen["settings"], frozen["solves"]
    label = code.get("label") or code["id"]
    on = {True: "on", False: "off"}

    def saves(write):
        # UX (14): what is saved, in words; the path goes in the Source row.
        if write.startswith(f"{ce.SUBDIR}/run/"):
            return "Saves the campus study results"
        if write.startswith(f"{ce.SUBDIR}/{ce.SETTINGS_FILE}"):
            return f"Saves the campus study settings, held to {label}"
        return f"Saves {write}"

    paths = ", ".join(w.split(" (")[0] for w in frozen["writes"])
    rows = [
        _note("grid_code", f"Grid-code profile: {label}"),
        _note(
            "settings",
            f"Campus study settings: N-1 {on.get(settings.get('n_minus_1'), 'not set')}, "
            f"investment {on.get(settings.get('invest'), 'not set')}, held to {label}",
        ),
        *(
            _note(f"setting.{k}", f"{SETTING_WORDS[k]}: {_setting(k, settings[k])}")
            for k in sorted(settings)
            if k not in ("n_minus_1", "invest", "profile")
        ),
        *(_note("writes", saves(w)) for w in frozen["writes"]),
        _note("solves", f"Reserves {solves} solve{'' if solves == 1 else 's'}"),
        _note(
            "source",
            f"Source: grid-code profile {code['id']} · digest {str(code.get('digest'))[:12]} · {paths}",
        ),
    ]
    return {
        "version": 1,
        "kind": "native",
        "tool": step["tool"],
        "rows": rows,
        "sets": [],
        "says": f"Run the campus study held to {label}",
        "complete": True,
        "digest": frozen["digest"],
    }


def fill(steps, frozen) -> None:
    """Put the frozen record, and the card it shows, on freshly materialised
    actions (in place). A record that could not be fixed (`unfrozen`) is kept
    with its reason, which `start` stops on for the user."""
    for step in steps:
        if not is_action(step) or step["args"] != FROZEN or frozen is None:
            continue
        if "unfrozen" in frozen:
            step["unfrozen"] = dict(frozen["unfrozen"])
        else:
            step["frozen"] = copy.deepcopy(frozen)
            step["card"] = card(frozen)


def stage_frozen(task) -> dict:
    """At solve-stage entry, as the run's owner: the frozen record, or
    `{"unfrozen": {state, message}}` when it cannot be fixed (the step then
    stops for the user with that reason). A stage
    entered again (L2's re-solve) keeps the run's first record, so a profile
    republished meanwhile is caught at dispatch, never frozen anew (D1)."""
    from fastapi import HTTPException

    from services import chat_tools, project_registry

    prior = next(
        (
            s["frozen"]
            for s in reversed(task.get("steps") or [])
            if is_action(s) and isinstance(s.get("frozen"), dict)
        ),
        None,
    )
    if prior is not None:
        return copy.deepcopy(prior)
    from services import run_profiles

    try:
        study = run_profiles.resolve(task["run"]["profile"]).get("campus_study")
    except (KeyError, TypeError, run_profiles.ProfileError):
        study = None
    try:
        with chat_tools._acting() as (db, user):
            return freeze(
                project_registry.resolve_project(db, user, task["project_id"]), study
            )
    except FreezeError as exc:
        return {"unfrozen": {"state": exc.state, "message": str(exc)}}
    except HTTPException:
        logger.info(
            "run %s: the grid-code profile could not be frozen", task.get("task_id")
        )
        return {
            "unfrozen": {
                "state": "unreadable",
                "message": "the run's project cannot be read",
            }
        }


def latest(task, tool) -> dict | None:
    """The run's latest action `tool` (a stage entered again numbers on)."""
    return next(
        (
            s
            for s in reversed(task.get("steps") or [])
            if is_action(s) and s["tool"] == tool and not s.get("superseded")
        ),
        None,
    )


def frozen_code(task) -> dict | None:
    """The grid-code profile the run's latest readiness step was frozen to."""
    step = latest(task, READINESS)
    code = ((step or {}).get("frozen") or {}).get("grid_code")
    return code if isinstance(code, dict) else None


def unchanged(project, code) -> bool:
    """D1: the profile is still published with the frozen content."""
    from services import campus_electrical_service as ce

    try:
        return (
            code["id"] in published(project)
            and ce.profile_fingerprint(project, code["id"]) == code["digest"]
        )
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
        run_envelope._audit(
            envelope, EVENT, call_id=call_id, tool=CAMPUS, reserved=SOLVES
        )
        run_envelope._save(path, envelope)
        run_envelope._count_solves(path, envelope)
    return True


def release(path, call_id, solves=SOLVES):
    """Settle the reservation (once): `solves`, the stated bound once the job
    started, 0 when it never did. No-op without a row."""
    from services import run_envelope

    return run_envelope.settle_engine_solves(path, call_id, solves, event=EVENT)


def start(path, ctx, step, *, instance, approved=None) -> str:
    """Run one native action. Returns the action's name when it ran or
    started, else the refusal reason (the run then needs the user).
    `approved` is the digest of the card the user approved (F1): an action
    that needs a card runs only when it is the frozen record's digest."""
    from harness import runner

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
    _LIVE.add(attempt)
    try:
        return _dispatch(path, ctx, step["id"], attempt, approved)
    finally:
        _LIVE.discard(attempt)


def _dispatch(path, ctx, step_id, attempt, approved=None):
    from fastapi import HTTPException

    from services import chat_tools, project_registry

    task = json.loads(path.read_text())
    step = next(s for s in task["steps"] if s["id"] == step_id)
    if isinstance(
        step.get("unfrozen"), dict
    ):  # the freeze's own reason, e.g. "publish the grid-code profile"
        return _stop(
            path,
            step["id"],
            "dc_" + str(step["unfrozen"].get("state")),
            step["unfrozen"].get("message"),
        )
    frozen = step.get("frozen")
    if (
        not is_action(step)
        or step.get("args") != FROZEN
        or not isinstance(frozen, dict)
        or digest(_core(frozen)) != frozen.get("digest")
        or step.get("card") != card(frozen)
    ):
        return _stop(
            path,
            step["id"],
            "dc_args",
            "the action runs only with the record its card showed",
        )
    if needs_card(step) and approved != frozen["digest"]:
        return _stop(
            path,
            step["id"],
            "dc_card",
            "the campus study runs only on the card the user approved for this record",
        )
    if str(getattr(ctx, "project_uuid", "")) != task["project_id"]:
        return _stop(
            path, step["id"], "project_changed", "the bound project is not the run's"
        )
    try:
        with chat_tools._acting() as (db, user):
            project = project_registry.resolve_project(db, user, task["project_id"])
            code = frozen["grid_code"]
            if not unchanged(project, code):
                return _stop(
                    path,
                    step["id"],
                    "grid_code_changed",
                    f"the grid-code profile {code['id']} was republished, edited or withdrawn since "
                    "the card showed it; review the run again",
                )
            if step["tool"] == READINESS:
                return _readiness(path, step, project, code)
            return _campus(path, step, project, user, frozen, attempt, ctx)
    except HTTPException as exc:
        detail = (
            exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        )
        return _stop(
            path,
            step["id"],
            str(detail.get("error_kind") or "dc_refused"),
            str(detail.get("message") or "")[:240],
        )
    except Exception as exc:  # noqa: BLE001 — a launch that did not start fails the step, never strands it running
        logger.exception("native action %s failed for run %s", step["tool"], path.stem)
        return _stop(path, step["id"], "dc_start_failed", type(exc).__name__)


def rows_digest(card) -> str:
    """D2: the digest of a scorecard's rows and exclusions (id, verdict,
    assumed; id, reason), which the result gate compares with its own read."""
    return digest(
        {
            "rows": [
                [r["id"], r["verdict"], bool(r.get("assumed"))]
                for r in card.get("rows") or []
            ],
            "excluded": [
                [r["id"], r.get("reason")] for r in card.get("excluded") or []
            ],
        }
    )


def _readiness(path, step, project, code):
    """The scorecard with the frozen profile, from a campus run held to it."""
    from harness import runner
    from services import campus_electrical_service as ce
    from services.adequacy import grid_readiness

    since = ce.grid_code_since_run(project, code["id"])
    if (since or {}).get("status") != "same":
        return _stop(
            path,
            step["id"],
            "campus_run_stale",
            "the published campus run is not held to the run's grid-code profile",
        )
    card = grid_readiness.grid_readiness_for(project, code["id"])
    result = {
        "profile": code["id"],
        "digest": ce.profile_fingerprint(
            project, code["id"]
        ),  # read now, next to the rows
        "rows_digest": rows_digest(card),
        "summary": card.get("summary"),
        "rows": [
            {"id": r["id"], "verdict": r["verdict"], "assumed": bool(r.get("assumed"))}
            for r in card.get("rows") or []
        ],
        "excluded": [
            {"id": r["id"], "reason": r.get("reason")}
            for r in card.get("excluded") or []
        ],
    }

    def done(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if s["status"] != "running":
            return False
        s.update(status="completed", result=result)

    runner._update(path, done)
    return READINESS


def _campus(path, step, project, user, frozen, attempt, ctx=None):
    """Q2: no campus file is the user's to draft. Then the reservation, the
    job, and the step waiting on it. SH-10b CT-3: the network revision the
    job reads is recorded before it queues (the campus service keeps none),
    so the result gate can tell an edit since."""
    from harness import runner
    from services import campus_electrical_service as ce
    from services import dirty_state

    if not (_project_dir(project) / ce.SUBDIR / ce.CAMPUS_FILE).is_file():
        return _stop(path, step["id"], "campus_missing", "draft the campus")
    if not _reserve(path, attempt):
        return _stop(
            path,
            step["id"],
            "budget",
            f"the campus run needs {SOLVES} solve and the run has none left",
        )
    # None (no context that counts edits) reads as edited at the gate: never a pass.
    revision = dirty_state.revision(ctx) if hasattr(ctx, "network_revision") else None
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
        s["job"] = {
            "kind": JOB_KIND,
            "job_id": str(job["id"]),
            "call_id": attempt,
            "started_at": begun,
            "network_revision": revision,
        }
        if t["status"] != "cancelled":
            t["status"] = "waiting_job"

    try:
        runner._update(path, wait)
    except BaseException:
        _abort(
            job["id"]
        )  # a queued job the step cannot follow is stopped; its reservation stays charged
        raise
    return CAMPUS


def _abort(job_id):
    from services import solve_queue as sq

    try:
        sq.solve_queue.abort(uuid.UUID(str(job_id)))
    except Exception:  # noqa: BLE001 — best effort; the step fails for the user either way
        logger.exception("campus job %s could not be aborted", job_id)


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
        return str(
            status
        ), f"the campus run ended {status}: {str(job.get('error') or '')[:200]}"
    if used != {"profile": code["id"], "sha256": code["digest"]}:
        return (
            "stale",
            "the published campus run is not held to the run's grid-code profile",
        )
    return "done", None


def _interrupted(path, task) -> bool:
    """A step left `running` by a dispatch this process is not running (a
    restart mid-dispatch) is uncertain: the run waits for the user, any
    reservation stays charged in full, and nothing is queued again."""
    from harness import runner

    stuck = [
        s["id"]
        for s in task.get("steps") or []
        if is_action(s)
        and s["status"] == "running"
        and s.get("attempt_id") not in _LIVE
    ]
    if not stuck:
        return False
    for step in task["steps"]:
        if step["id"] in stuck:
            release(
                path, step.get("attempt_id")
            )  # unverifiable: the whole stated bound

    def change(t):
        hit = False
        for s in t["steps"]:
            if (
                s["id"] in stuck
                and s["status"] == "running"
                and s.get("attempt_id") not in _LIVE
            ):
                s.update(status="uncertain", error=UNCERTAIN)
                hit = True
        if not hit:
            return False
        t["run"]["stages"][STAGE].update(status="failed", reason="uncertain")
        if t["run"]["status"] in ("running", "waiting_job"):
            t["run"]["status"], t["status"] = "needs_user", "needs_review"

    runner._update(path, change)
    return True


def settle(path, ctx=None) -> str | None:
    """Each tick: settle the run's waiting campus step once its job ended,
    from the job's own row (matched by id) and the published run's
    `profile_used`. Anything but `done` fails the step and the run waits
    for the user; the reservation settles to the stated bound either way."""
    from harness import runner
    from services import campus_electrical_service as ce

    path = Path(path)
    task = json.loads(path.read_text())
    if _interrupted(path, task):
        return "uncertain"
    step = next(
        (
            s
            for s in task.get("steps") or []
            if is_action(s)
            and s["status"] == "waiting"
            and (s.get("job") or {}).get("kind") == JOB_KIND
        ),
        None,
    )
    if step is None:
        return None
    job = _job(step["job"]["job_id"])
    if isinstance(job, dict) and job.get("status") not in _TERMINAL:
        return None
    used = ce._profile_used(
        path.parent.parent / ce.SUBDIR / "run"
    )  # the task file's project
    result, error = outcome(job, used, step["frozen"]["grid_code"])
    # CT-3: the published run this job made, by its compliance tables' digests,
    # so a later campus run (a user's) is never read as this one.
    files = (
        run_files(path.parent.parent / ce.SUBDIR / "run") if result == "done" else None
    )
    release(path, step["job"]["call_id"])

    def done(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if (
            s["status"] != "waiting"
            or (s.get("job") or {}).get("job_id") != step["job"]["job_id"]
        ):
            return False
        s["job"].update(outcome=result, finished_at=time.time())
        if t["status"] == "waiting_job":
            t["status"] = "running"
        if result == "done":
            s.update(
                status="completed",
                result={
                    "job_id": s["job"]["job_id"],
                    "solves": SOLVES,
                    "grid_code": s["frozen"]["grid_code"],
                    "run_files": files,
                    "network_revision": s["job"].get("network_revision"),
                },
            )
            if t["run"]["status"] == "waiting_job":
                t["run"]["status"] = "running"
            return
        s.update(status="failed", error=error[:480])
        t["run"]["stages"][STAGE].update(status="failed", reason="dc_campus_" + result)
        if t["run"]["status"] in ("running", "waiting_job"):
            t["run"]["status"], t["status"] = "needs_user", "needs_review"

    runner._update(path, done)
    return result


# ── SH-10b: the planning-contingency read (GK A CT-1..CT-6, DC13-SCOPE) ──────
# The campus run's checks that the intact case and transformer N-1 decide:
# the worst transformer loading of both, and the PCC and campus voltages in
# every case. Read from the invested table (`invest` is frozen on): the
# re-solved AC result with the on-load taps and the compensation the study
# bought, which is DC13-SCOPE's stage 2 (taps and shunts only; the study has
# no redispatch). The as-is table is never read: a completed job with
# `invest` on always writes the invested one (`campus_electrical_service.run`).
CONTINGENCY_CHECKS = ("transformer_loading", "pcc_voltage", "campus_voltage")
# Each check's limit kind (CT-5, D3): a voltage band is the grid code's (or an
# assumed design band) and counts only with its own source. The loading limit
# is 100 % of each unit's own rating in the campus file, which the engine tags
# `assumed` with the clause "equipment rating (not a grid-code clause)"
# (`gridspine/static/campus_compliance.py` `_transformer_row`): it counts only
# with that clause (CT-5a), so a changed clause or tag fails closed.
EQUIPMENT_LIMIT = frozenset({"transformer_loading"})
EQUIPMENT_CLAUSE = "equipment rating"
EQUIPMENT_SOURCE = "assumed"  # the engine's only tag on a unit-rating limit
# The checks in words for the gate's reasons and note (UX: no internal keys in user-facing copy).
CHECK_WORDS = {
    "transformer_loading": "Transformer loading",
    "pcc_voltage": "PCC voltage",
    "campus_voltage": "Campus voltage",
}
_STATUS_WORDS = {"not_rechecked": "not re-checked", "not_rated": "not rated"}
_BAND = {
    "pcc_voltage": "the PCC voltage band",
    "campus_voltage": "the campus voltage band",
}
_NO_TRANSFORMER = "no transformer between the PCC and the site buses"


def run_files(run: Path) -> dict | None:
    """The digests of the published run's compliance tables (CT-3), None
    when the invested one is missing."""
    from services import campus_electrical_service as ce

    out = {}
    for name in (ce.cs.COMPLIANCE_INVESTED_CSV, ce.cs.COMPLIANCE_CSV, ce.cs.LF_PCC_CSV):
        try:
            out[name] = hashlib.sha256((run / name).read_bytes()).hexdigest()
        except OSError:
            out[name] = None
    return out if out[ce.cs.COMPLIANCE_INVESTED_CSV] else None


def _cases(run: Path, transformer: bool) -> str | None:
    """Why the study's cases do not cover the planning set (GK B E3), or
    None: every case converged, the intact one and, for a campus with a
    transformer, at least one transformer N-1 (the engine solves N-1 only for
    a group with a parallel unit, `campus_flow.solve_cases`)."""
    import pandas as pd

    from services import campus_electrical_service as ce

    try:
        pcc = pd.read_csv(run / ce.cs.LF_PCC_CSV)
        cases = pcc["case"].astype(str)
        converged = pcc["converged"].astype(str).str.lower() == "true"
    except (OSError, ValueError, KeyError):
        return "the campus run's case table cannot be read"
    if not len(pcc) or not converged.all():
        return "a case of the campus study did not converge"
    if not (cases == "intact").any():
        return "the campus study solved no intact case"
    if transformer and not cases.str.startswith("N-1:").any():
        return (
            "the campus study solved no transformer N-1 case: no transformer group has a parallel "
            "unit to lose"
        )
    return None


def contingency(project, task, revision: int) -> dict:
    """The run's own campus study against the planning contingencies:
    `{"failing": [...], "measures": [...]}` (each failing check with its case,
    period, value and limit; each measure the pass rests on, CT-5d), else
    `{"state", "reason"}` for no evidence. Bound to the run (CT-3):
    the step completed, the published run is held to the frozen profile id
    and content, its tables are the ones the step settled on, and the network
    is at the revision the job read."""
    import pandas as pd

    from services import campus_electrical_service as ce

    step = latest(task, CAMPUS)
    if step is None or step.get("status") != "completed":
        return {
            "state": "not_run",
            "reason": "the run's own campus study has not finished",
        }
    result, code = (
        step.get("result") or {},
        (step.get("frozen") or {}).get("grid_code") or {},
    )
    run = _project_dir(project) / ce.SUBDIR / "run"
    if ce._profile_used(run) != {
        "profile": code.get("id"),
        "sha256": code.get("digest"),
    }:
        return {
            "state": "grid_code_changed",
            "reason": "the published campus run is not held to the run's grid-code profile",
        }
    if not (run / ce.cs.COMPLIANCE_INVESTED_CSV).is_file():
        # CT-2, E4: `invest` is frozen on, so no fallback to the as-is table.
        return {
            "state": "no_invested_table",
            "reason": "the campus run wrote no compliance table after the measures",
        }
    files = result.get("run_files")
    if not files or run_files(run) != files:
        return {
            "state": "campus_rerun",
            "reason": "the published campus run is not the run's own campus study",
        }
    recorded = result.get("network_revision")
    if (
        not isinstance(recorded, int)
        or isinstance(recorded, bool)
        or recorded != revision
    ):
        return {
            "state": "edited",
            "reason": "the network was edited since the run's campus study read it",
        }
    try:
        table = pd.read_csv(run / ce.cs.COMPLIANCE_INVESTED_CSV)
    except (OSError, ValueError):
        return {
            "state": "unreadable",
            "reason": "the campus run's compliance table cannot be read",
        }
    rows = {str(r["check"]): r for r in table.to_dict("records")}
    failing, waiting, measures = [], [], []
    for check in CONTINGENCY_CHECKS:
        row = rows.get(check)
        if row is None:
            # The engine notes "no transformer" on the campus row, or on the PCC row without other buses.
            noted = any(
                _NO_TRANSFORMER in str((rows.get(v) or {}).get("detail"))
                for v in ("campus_voltage", "pcc_voltage")
            )
            if check == "transformer_loading" and noted:
                continue  # not applicable: the voltage row says the campus has no transformer
            return {
                "state": "no_row",
                "reason": f"the campus run has no row for {CHECK_WORDS[check]}",
            }
        status = row.get("status_with_measures")
        status = status if isinstance(status, str) else None
        if status not in (
            "pass",
            "fail",
        ):  # not_rechecked, unknown, not_rated, none: never read as a pass
            return {
                "state": "not_evaluated",
                "reason": f"{CHECK_WORDS[check]}: {_STATUS_WORDS.get(status) or (status or 'no status').replace('_', ' ')} after the measures",
            }
        if status == "fail":
            failing.append(
                {
                    "check": check,
                    "worst_case": _text(row.get("worst_case")),
                    "worst_period": _int(row.get("worst_period")),
                    "value": _num(row.get("value")),
                    "limit": _num(row.get("limit")),
                    "unit": _text(row.get("unit")),
                }
            )
            continue
        source = row.get("source")
        if check in EQUIPMENT_LIMIT:
            if (
                not str(row.get("clause") or "").startswith(EQUIPMENT_CLAUSE)
                or source != EQUIPMENT_SOURCE
            ):
                waiting.append(
                    f"{CHECK_WORDS[check]} is not read against the unit's own rating"
                )
        elif source == "assumed":
            # D3 (#1079): a pass on an assumed design band never passes a block row (CT-5b).
            waiting.append(
                f"{CHECK_WORDS[check]} passes only on an assumed band: state {_BAND[check]} in the grid-code profile"
            )
        elif source == "extracted":
            waiting.append(
                f"{CHECK_WORDS[check]} passes only on an unconfirmed band: confirm {_BAND[check]} in the grid-code profile"
            )
        elif source != "code":
            # A missing or unknown tag fails closed: only a band the code states passes (CT-5b).
            waiting.append(
                f"{CHECK_WORDS[check]} names no source for its band: state {_BAND[check]} in the grid-code profile"
            )
        detail = _text(row.get("detail")) or ""
        if row.get("status_as_is") != "pass" or "recommended" in detail:
            measures.append({"check": check, "detail": detail})
    if failing:
        return {"failing": failing, "measures": []}
    if waiting:
        return {"state": "not_evaluated", "reason": "; ".join(waiting)}
    why = _cases(run, "transformer_loading" in rows)
    if why is not None:
        return {"state": "cases", "reason": why}
    return {"failing": [], "measures": measures}


def _text(v):
    return v if isinstance(v, str) else None


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _int(v):
    f = _num(v)
    return int(f) if f is not None else None
