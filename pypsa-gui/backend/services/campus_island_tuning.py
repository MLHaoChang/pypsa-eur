"""
Recommended grid-forming settings of a hub project (E2E demo plan
2026-10-14 §7.2, slice 5, GFM-b; owner Q7(b)).

gridspine's ``gfm_settings_campus`` searches one droop and one equivalent
inertia per GFM converter that make the last campus run's island-hour events
pass with margin, or, when none within the bounds does, the capacity I7 would
buy. Here it is:

- run on demand, never inside the campus Run, as a ``campus`` job of the
  solve queue (``enqueue_settings``; the gatekeeper: on the solver's job and
  progress path): one job per project (and none while the I7 cuts are being
  applied, since their trials rerun the campus study), its stage on
  ``run_job_state``, its
  failure recorded. The search caps its own simulations (``max_sims``), and
  an abort stops it before its next simulation, writing nothing;
- read back (``get_settings``) with whether it is still the run's: a campus
  run after it, or a campus file, network or library the run is stale
  against (``campus_electrical_service._stale``), makes it ``stale`` (and a
  rerun of the island checks removes it,
  ``campus_electrical_service._island_hours_files``).

- applied (``apply_settings``): the recommended droop, τ_f or H_v (and
  i_max when the search could raise it) written into the island sidecar and
  the campus file, tagged ``assumed``. The island study goes with the old
  inputs; the campus results go stale by their hash of the campus file; the
  hub must be re-solved, since droop, τ_f and H_v change its island rows
  (I2b): the applied record (``APPLIED_FILE``, beside the campus file, so a
  campus run keeps it) says so until the re-solved hub is saved.

The result is heuristic screening, labelled so.
"""
from __future__ import annotations

import json
from pathlib import Path

import logging

from fastapi import HTTPException

from services import campus_electrical_service as ce

logger = logging.getLogger(__name__)

#: The job's one stage, as ``run_job_state`` names it.
STAGE = "gfm_settings"
#: The campus job's action (in the job's settings); without it the job is the run.
ACTION = "gfm_settings"
MAX_SIMS = 400


def recommend(run_dir: Path, *, allow_i_max: bool = False, max_sims: int = MAX_SIMS,
              should_stop=None) -> dict:
    """gridspine's recommender on a run directory (replaced in tests);
    ``should_stop`` is asked before every simulation."""
    return ce.cs.gfm_settings_campus(run_dir, allow_i_max=allow_i_max, max_sims=max_sims,
                                     should_stop=should_stop)


def _run_dir(project) -> Path:
    return ce.campus_dir(project) / "run"


def get_settings(project) -> dict:
    """``_state(project)`` plus ``applied``: the last apply's record while the
    hub has not been re-solved since (``applied_record``), whatever became of
    the recommendation; None otherwise."""
    out = _state(project)
    out["applied"] = applied_record(project)
    return out


def _state(project) -> dict:
    """``{state, reason, recommendation}``: ``state`` is ``not_run`` (no
    recommendation for this run), ``ready``, or ``stale`` (the run's frequency
    check is newer than it); ``recommendation`` is gridspine's
    ``campus_gfm_settings.json`` as written."""
    ce.require_capacity_expansion(project)
    run = _run_dir(project)
    path, events = run / ce.cs.GFM_SETTINGS_JSON, run / ce.cs.FREQ_EVENTS_CSV
    if not path.is_file():
        reason = ("Recommend GFM settings: it reads the last campus run's frequency check"
                  if events.is_file() else
                  "Run the campus study with island requirements first, then Recommend GFM settings")
        return {"state": "not_run", "reason": reason, "recommendation": None}
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"state": "stale", "recommendation": None,
                "reason": "The last recommendation could not be read; recommend GFM settings again"}
    again = "recommend them again"
    if not events.is_file() or events.stat().st_mtime > path.stat().st_mtime:
        return {"state": "stale", "recommendation": rec,
                "reason": f"The campus run changed since these settings were recommended; {again}"}
    if ce._stale(project, ce.campus_dir(project)):
        return {"state": "stale", "recommendation": rec,
                "reason": "The campus file, the network or the library changed since the campus run "
                          f"these settings come from; run the campus study, then {again}"}
    return {"state": "ready", "reason": "", "recommendation": rec}


def enqueue_settings(project, user=None, *, allow_i_max: bool = False) -> dict:
    """Queue the recommendation for ``project`` and answer the queue job's
    public view. Refused in the request (422) when the last campus run has no
    frequency check to read; a 409 while the project has a queued or running
    job of any kind (the queue's one-active-job rule)."""
    from services import project_registry
    from services import solve_queue as sq

    from services import campus_island_cuts as cic

    ce.require_capacity_expansion(project)
    if cic._running(cic._JOBS.get(cic._job_key(project))):
        # its trials rerun the campus study outside the queue, replacing the
        # frequency check this would read
        raise HTTPException(status_code=409, detail="the frequency cuts are being applied to this "
                                                    "project; wait for them or cancel them")
    if not (_run_dir(project) / ce.cs.FREQ_EVENTS_CSV).is_file():
        raise HTTPException(status_code=422, detail="the last campus run has no frequency check; run "
                                                    "the campus study with island requirements first")
    job, created = sq.solve_queue.enqueue_unique(
        project.name, project_key=project_registry.registry_key(project),
        storage_dir=str(project_registry.ensure_project_dir(project)),
        solver_config_json=json.dumps({"action": ACTION, "allow_i_max": bool(allow_i_max)}),
        enqueued_by_user_id=getattr(user, "id", None), kind=sq.KIND_CAMPUS)
    if not created:
        raise HTTPException(status_code=409, detail=f"project '{project.name}' already has a {job.kind} job "
                                                    f"{job.status}; wait for it or abort it")
    return sq.solve_queue.get_job(job.id) or job.to_public(None)


def run_queued(project, settings: dict, *, progress, should_stop) -> None:
    """The ``campus`` job's body for ``ACTION``: the recommender on the run."""
    try:
        from gridspine.static.gfm_tuning import SearchStopped
    except ImportError:            # without gridspine the recommender refuses first (503)
        SearchStopped = ()

    if should_stop():
        raise ce.CampusRunAborted(f"aborted before {STAGE}")
    progress(STAGE, 0, 1)
    try:
        recommend(_run_dir(project), allow_i_max=bool(settings.get("allow_i_max")), max_sims=MAX_SIMS,
                  should_stop=should_stop)
    except SearchStopped as exc:
        raise ce.CampusRunAborted(f"aborted during {STAGE}") from exc
    progress(STAGE, 1, 1)


#: The fields a recommendation writes; ``H_equivalent_s`` is derived, never written.
WRITTEN = ("droop_pct", "tau_f_s", "H_v_s", "i_max_pu")
_REFUSE = {
    "not_achievable": "no setting within the bounds passes: capacity is needed (I7), so there is "
                      "nothing to apply",
    "unchanged": "the current settings already pass with margin: nothing to apply",
    "incomplete": "the recommendation is incomplete (the search hit its simulation cap); recommend "
                  "again",
}


def _same(a, b) -> bool:
    import math

    return a is not None and b is not None and math.isclose(float(a), float(b), rel_tol=1e-9,
                                                             abs_tol=1e-12)


#: The applied record (owner decision 2026-10-15: no hub marker, the record
#: says it). Beside the campus file, not in the run directory, so a campus run
#: (which removes the run's recommendation) keeps it; cleared when a hub solve
#: of the project, started after the apply, completes (``_hub_solved_since``).
APPLIED_FILE = "gfm_applied.json"


def _applied_path(project) -> Path:
    return ce.campus_dir(project) / APPLIED_FILE


def _epoch(value) -> float | None:
    if value is None:
        return None
    return value.timestamp() if hasattr(value, "timestamp") else float(value)


def _hub_solved_since(project, at: float) -> bool:
    """A ``solve`` job of the project (the Solve button and the solve queue
    are one path) that started after ``at`` and completed: the resident
    queue's records, then the persisted ones, so a restart keeps the answer.
    Neither a save (Ctrl+S, autosave, the pre-run save, a restore) nor an
    identical network counts: only the solve does."""
    from services import project_registry
    from services import solve_queue as sq

    key = project_registry.registry_key(project)
    rows = list(sq.solve_queue.list_jobs())
    try:
        from services import solve_job_store

        rows += solve_job_store.load_by_status(("completed",),
                                               project_key_prefix=key.partition(":")[0] + ":")
    except Exception:                       # noqa: BLE001 - no store (tests, a broken DB): resident only
        pass
    for job in rows:
        started = _epoch(job.get("started_at"))
        if (job.get("project_key") == key and (job.get("kind") or sq.KIND_SOLVE) == sq.KIND_SOLVE
                and job.get("status") == "completed" and started is not None and started > at):
            return True
    return False


def applied_record(project) -> dict | None:
    """The last apply's record until a hub solve started after it completes
    (``_hub_solved_since``); None then, and the record goes."""
    path = _applied_path(project)
    if not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if _hub_solved_since(project, float(rec.get("at") or 0.0)):
        path.unlink(missing_ok=True)
        return None
    return rec


def _island_rows_on(project) -> bool:
    """Whether the hub solve reads the island sidecar (``island_constraints``
    in the project's solver config)."""
    from services import project_registry

    cfg = project_registry.project_dir(project) / "solver_config.json"
    try:
        return bool(json.loads(cfg.read_text()).get("island_constraints")) if cfg.is_file() else False
    except (OSError, ValueError):
        return False


def _project_busy(project) -> str | None:
    """Why an apply cannot run now: the I7 cuts loop rewrites the campus file,
    and a queued or running job of the project (a campus run, a GFM search, a
    hub solve) reads or rewrites what an apply writes."""
    from services import campus_island_cuts as cic
    from services import project_registry
    from services import solve_queue as sq

    if cic._running(cic._JOBS.get(cic._job_key(project))):
        return "the frequency cuts are being applied to this project"
    key = project_registry.registry_key(project)
    for job in sq.solve_queue.list_jobs():
        if job.get("project_key") == key and job.get("status") in ("queued", "running"):
            return f"the project has a {job.get('kind')} job {job.get('status')}"
    return None


def apply_settings(project) -> dict:
    """Write a ``recommended`` result that is still the run's into the island
    sidecar and the campus file (see the module docstring). Returns
    ``{changes, hub_resolve_needed}``, each change ``{unit, pypsa_name, field,
    from, to}``, ``from`` being the recommendation's ``current`` (what the
    search started from, which the ready state guarantees the files still
    hold). Refused, with nothing written:

    - 409 while a job of the project runs or is queued, or the cuts loop runs;
      when there is no recommendation for this run, it is stale, or it was
      already applied;
    - 422 for a result other than ``recommended``, a unit either file lacks, a
      unit whose control in either file is not the recommendation's model, a
      recommended field either file lacks, a value the schema refuses, or a
      file over its size cap.

    Both texts are built, size-checked and validated first; the sidecar is
    written atomically, then the campus file (temp and rename), and the old
    sidecar is put back if that fails; the island study made with the old
    inputs goes only after both are written. The campus file is re-dumped as
    YAML, so its comments go (as the cuts loop's refresh does).

    The busy check shares no lock with ``enqueue_settings`` or the queue: a
    job enqueued in the milliseconds between the check and the writes can
    still start. Both writes are atomic renames, so such a job reads either
    file whole, old or new; accepted.

    The record (``APPLIED_FILE``) says the hub must be re-solved only when its
    solve reads the island rows (``island_constraints``); an apply before an
    earlier one was re-solved merges its changes in."""
    import time

    import yaml

    from services.adequacy.island_config import validate
    from services.atomic_io import atomic_write_text

    busy = _project_busy(project)
    if busy:
        raise HTTPException(status_code=409, detail=f"{busy}; wait for it before applying GFM settings")
    state = get_settings(project)
    if state["state"] != "ready":
        raise HTTPException(status_code=409, detail="no recommendation for the last campus run; "
                                                    "recommend GFM settings again")
    rec = state["recommendation"]
    rec_path = _run_dir(project) / ce.cs.GFM_SETTINGS_JSON
    if state.get("applied") and state["applied"].get("recommendation_mtime") == rec_path.stat().st_mtime:
        raise HTTPException(status_code=409, detail="these settings are already applied; re-solve the hub, "
                                                    "run the campus study and recommend again")
    status = rec.get("status")
    if status != "recommended":
        raise HTTPException(status_code=422, detail=_REFUSE.get(status, f"status {status!r}: nothing to apply"))
    campus_path = ce.campus_dir(project) / ce.CAMPUS_FILE
    sidecar_path, study_path = ce._island_paths(project)
    old_sidecar = sidecar_path.read_text() if sidecar_path.is_file() else None
    spec = yaml.safe_load(campus_path.read_text())
    units = spec["campus"]["units"]
    config = json.loads(old_sidecar) if old_sidecar is not None else None
    if config is None:
        raise HTTPException(status_code=422, detail="the project has no island sidecar to write into")
    changes = []
    for uid, s in sorted((rec.get("settings") or {}).items()):
        unit = units.get(uid)
        name = (unit or {}).get("pypsa_name")
        block, side = (unit or {}).get("island"), config["units"].get(name)
        if block is None or side is None:
            raise HTTPException(status_code=422, detail=f"unit {uid!r} has no island block in the campus "
                                                        "file and the sidecar; draft the campus again")
        model = s.get("model")
        if block.get("control") != model or side.get("control") != model:
            raise HTTPException(status_code=422, detail=(
                f"unit {uid!r} is {side.get('control')!r} in the island data and {block.get('control')!r} "
                f"in the campus file, but the settings were recommended for {model!r}; recommend again"))
        for field in WRITTEN:
            if field == "i_max_pu" and not rec.get("allow_i_max"):
                continue                     # i_max is hardware: written only when the search could raise it
            new, old = s["recommended"].get(field), s["current"].get(field)
            if new is None or _same(new, old):
                continue
            if field not in side or field not in block:
                raise HTTPException(status_code=422, detail=(
                    f"unit {uid!r} has no {field} in the island data or the campus file; the droop and "
                    "inertia were verified together, so neither is written; recommend again"))
            tagged = {"value": float(new), "source": "assumed"}
            block[field], side[field] = dict(tagged), dict(tagged)
            changes.append({"unit": uid, "pypsa_name": name, "field": field, "from": old, "to": float(new)})
    if not changes:
        raise HTTPException(status_code=422, detail="the recommendation changes no setting the files carry")
    campus_text = yaml.safe_dump(spec, sort_keys=False)
    sidecar_text = json.dumps(config, indent=2)
    for text, cap, what in ((sidecar_text, ce.MAX_ISLAND_BYTES, "island config"),
                            (campus_text, ce.MAX_CAMPUS_BYTES, "campus file")):
        if len(text.encode()) > cap:
            raise HTTPException(status_code=422, detail=f"the {what} would be larger than {cap} bytes")
    try:
        validate(config)
        ce.cs.check_campus(spec)
    except ce.ContractError as exc:
        raise ce._unprocessable(exc)
    atomic_write_text(sidecar_path, sidecar_text)
    try:
        atomic_write_text(campus_path, campus_text)
    except BaseException:
        try:
            atomic_write_text(sidecar_path, old_sidecar)      # both or neither
        except Exception:                                     # noqa: BLE001 - the original error wins
            logger.exception("GFM apply: the campus file write failed and the island sidecar could "
                             "not be restored; the two files may disagree")
        raise
    study_path.unlink(missing_ok=True)                        # made with the old inputs
    island_rows = _island_rows_on(project)
    prior = applied_record(project)                           # an earlier apply not yet re-solved
    record = {"at": time.time(), "changes": _merge_changes((prior or {}).get("changes"), changes),
              # with island constraints off the hub solve does not read these settings
              "hub_resolve_needed": island_rows, "island_rows": island_rows,
              "from_basis": "the recommendation's current value",
              "recommendation_mtime": rec_path.stat().st_mtime}
    atomic_write_text(_applied_path(project), json.dumps(record, indent=2))
    return {"changes": changes, "hub_resolve_needed": island_rows}


def _merge_changes(prior: list | None, new: list) -> list:
    """One change per unit and field across applies not yet re-solved: the
    first ``from``, the latest ``to``."""
    by = {(c["unit"], c["field"]): dict(c) for c in prior or []}
    for c in new:
        k = (c["unit"], c["field"])
        by[k] = {**c, "from": by[k]["from"]} if k in by else dict(c)
    return [by[k] for k in sorted(by)]
