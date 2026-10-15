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

The result is heuristic screening, labelled so; nothing here applies it.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException

from services import campus_electrical_service as ce

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
