"""
The frequency cuts of a hub project (campus island plan I7; owner Q6:
manual, with a preview).

The campus Run proposes the cuts: gridspine's ``cuts_campus`` turns each
I6 failure at an island hour into the 0⁺ quantity that passes
(``campus_freq_cuts.csv``, ``CUT_COLUMNS``): inertia M (MW·s), headroom
either way (MW) or the droop gain ΣK (MW/Hz). Here they are:

- previewed (``get_cuts``): each one the hub LP can take, a ``seamless``
  feasible cut, with its primal estimate (``solver.island.cut_estimates``);
  the others with the reason they have no row;
- applied (``apply_loop``, owner QA: one click runs the loop): the proposed
  cuts are written to the project's ``island_cuts.json`` (owner QB), which
  the solve reads with the sidecar; the hub is re-solved and the campus
  study rerun, which proposes the next cuts; until nothing fails, nothing
  more can be cut, or ``MAX_TRIALS``. Every trial is kept, as C11's loop;
- taken back (``remove_cut``, ``clear_cuts``).

The cuts are heuristic where gridspine says so, and the estimate is primal:
neither is a certificate. Bridge-energy failures (E4, ``ups_bridged``) get
no cut (``LIMIT``).
"""
from __future__ import annotations

import json
import math

import pandas as pd
from fastapi import HTTPException

from services import project_registry
from services.solver.island import CUTS_FILE, cut_estimates, load_applied_cuts

#: gridspine's frozen contract (``frequency.CUT_COLUMNS``,
#: ``campus_study.FREQ_CUTS_CSV``).
CUT_COLUMNS = ("period", "hour", "scenario", "event", "kind", "required", "current", "delta", "unit",
               "binding", "derivation", "heuristic", "feasible", "note")
FREQ_CUTS_CSV = "campus_freq_cuts.csv"
MAX_TRIALS = 3
LIMIT = ("Bridge-energy and pickup failures (E4, ups_bridged) get no cut: a cut bounds the "
         "inertia, headroom or droop gain at the instant of islanding, not the UPS energy or "
         "the gensets' pickup.")
#: What identifies an applied cut: one per hour, scenario and kind.
_KEY = ("period", "hour", "scenario", "kind")


def _key(cut) -> tuple:
    return (int(cut["period"]), int(cut["hour"]), cut["scenario"], cut["kind"])


def _cuts_path(project):
    return project_registry.project_dir(project) / CUTS_FILE


def _state(project) -> dict:
    path = _cuts_path(project)
    if not path.is_file():
        return {"cuts": [], "trials": []}
    data = json.loads(path.read_text())
    return {"cuts": load_applied_cuts(path.parent), "trials": list(data.get("trials") or [])}


def _write(project, state) -> None:
    from services.atomic_io import atomic_write_text

    atomic_write_text(_cuts_path(project), json.dumps(state, indent=2))


def _proposed_rows(project) -> list[dict]:
    """The last campus run's proposed cuts, each with whether the hub LP can
    take it (``applicable``) and, where not, why."""
    from services.campus_electrical_service import campus_dir

    path = campus_dir(project) / "run" / FREQ_CUTS_CSV
    if not path.is_file():
        return []
    df = pd.read_csv(path)
    if list(df.columns) != list(CUT_COLUMNS):
        raise HTTPException(status_code=422, detail=f"{FREQ_CUTS_CSV} does not have the cut "
                                                    f"contract's columns {', '.join(CUT_COLUMNS)}")
    rows = []
    for rec in df.astype(object).where(pd.notna(df), None).to_dict("records"):
        feasible = str(rec["feasible"]).lower() in ("true", "1")
        required = float(rec["required"]) if rec["required"] is not None else math.inf
        rec.update(period=int(rec["period"]), hour=int(rec["hour"]), required=required,
                   current=float(rec["current"]) if rec["current"] is not None else None,
                   feasible=feasible)
        if not feasible or not math.isfinite(required):
            rec.update(applicable=False, reason=rec.get("note") or "no addition fixes this event")
        elif rec["scenario"] != "seamless":
            rec.update(applicable=False, reason=f"{rec['scenario']}: only seamless cuts have rows "
                                                "(the 0⁺ rows I2b builds)")
        else:
            rec.update(applicable=True, reason="")
        rows.append(rec)
    return rows


def _applied(rec) -> dict:
    return {"period": rec["period"], "hour": rec["hour"], "scenario": rec["scenario"],
            "kind": rec["kind"], "required": float(rec["required"])}


def _new_cuts(proposed, applied) -> list[dict]:
    """The applicable proposed cuts no applied cut already meets, the
    tightest per hour, scenario and kind."""
    have = {_key(c): c["required"] for c in applied}
    out: dict = {}
    for rec in proposed:
        if not rec["applicable"]:
            continue
        cut = _applied(rec)
        k = _key(cut)
        if have.get(k, -math.inf) >= cut["required"]:
            continue
        if k not in out or cut["required"] > out[k]["required"]:
            out[k] = cut
    return list(out.values())


def _merge(applied, new) -> list[dict]:
    by = {_key(c): c for c in applied}
    for cut in new:
        by[_key(cut)] = cut
    return [by[k] for k in sorted(by)]


def get_cuts(project) -> dict:
    """``{limit, max_trials, proposed, applied, trials}`` (see the module)."""
    from services import campus_electrical_service as ce
    from services.adequacy.island_config import SIDECAR_NAME, load_island_config

    proposed = _proposed_rows(project)
    applicable = [r for r in proposed if r["applicable"]]
    estimates = {}
    if applicable:
        island = load_island_config(project_registry.project_dir(project) / SIDECAR_NAME)
        if island is not None:
            with ce._netcdf_lock():
                n = ce.cs.load_solved_network(ce._network(project))
            for rec, est in zip(applicable, cut_estimates(n, island, applicable)):
                estimates[id(rec)] = est
    for rec in proposed:
        est = estimates.get(id(rec))
        rec.update(candidate=est["candidate"] if est else None,
                   added_mw=est["added_mw"] if est else None,
                   estimate=est["estimate"] if est else None,
                   label=est["label"] if est else "")
    state = _state(project)
    out = {"limit": LIMIT, "max_trials": MAX_TRIALS, "proposed": proposed,
           "applied": state["cuts"], "trials": state["trials"]}
    from services.campus_island_report import _finite

    return _finite(json.loads(json.dumps(out, default=ce._jsonable)))


def _verdict(proposed) -> str:
    hours = {(r["period"], r["hour"]) for r in proposed}
    if not hours:
        return "pass"
    return f"fails at {len(hours)} hour" + ("" if len(hours) == 1 else "s")


#: The ratings a trial refreshes from the re-solved hub (owner, 2026-10-14):
#: the size of each unit. Every other field, and every other part of the
#: campus file, is the user's and stays.
RATING_FIELDS = ("p_mw", "s_mva", "e_mwh")


def refresh_ratings(project) -> list[dict]:
    """Bring the campus file's unit sizes up to the re-solved hub: re-draft
    in memory, copy each unit's ``RATING_FIELDS`` by ``pypsa_name``, and add
    the units the solve newly built. Saved (validated) only when something
    changed. Returns what changed: ``{unit, field, from, to}`` per rating,
    ``{unit, added: True}`` per new unit, ``{unit, added: False, reason}``
    for one that cannot be added."""
    import yaml

    from services import campus_electrical_service as ce
    from services.adequacy.island_config import SIDECAR_NAME, load_island_config

    path = ce.campus_dir(project) / ce.CAMPUS_FILE
    spec = yaml.safe_load(path.read_text())
    campus = spec["campus"]
    island = load_island_config(project_registry.project_dir(project) / SIDECAR_NAME)
    with ce._netcdf_lock():
        draft = ce.cs.draft_from_project(ce._network(project), island=island).spec["campus"]
    mine = {u.get("pypsa_name"): uid for uid, u in campus["units"].items() if u.get("pypsa_name")}
    changed = []
    for uid, unit in draft["units"].items():
        name = unit.get("pypsa_name")
        if name is None:
            continue
        if name in mine:
            target = campus["units"][mine[name]]
            for field in RATING_FIELDS:
                if field not in unit:
                    continue
                old, new = (target.get(field) or {}).get("value"), unit[field]["value"]
                if old is None or not math.isclose(float(old), float(new), rel_tol=1e-9, abs_tol=1e-9):
                    target[field] = {**(target.get(field) or {}), "value": new}
                    changed.append({"unit": mine[name], "field": field, "from": old, "to": new})
        elif unit.get("bus") not in campus["buses"]:
            changed.append({"unit": uid, "added": False,
                            "reason": f"its bus {unit.get('bus')} is not in the campus file"})
        else:
            new_id = uid if uid not in campus["units"] else f"{uid}_{name}"
            campus["units"][new_id] = unit
            changed.append({"unit": new_id, "added": True})
    if changed:
        ce.save_campus(project, yaml.safe_dump(spec, sort_keys=False))
    return changed


def apply_loop(project, *, solve, rerun, stop_event) -> dict:
    """Apply, re-solve, rerun, until nothing fails, nothing more can be cut,
    ``MAX_TRIALS``, or ``stop_event`` (owner QA). ``solve(project)`` re-solves
    and saves the hub and returns its objective; ``rerun(project)`` brings
    the campus file's ratings up to it and reruns the campus study, which
    proposes the next cuts, and returns what it refreshed."""
    state = _state(project)
    new = _new_cuts(_proposed_rows(project), state["cuts"])
    if not new:
        raise HTTPException(status_code=422, detail="nothing to apply: the last campus run proposes "
                                                    "no cut the hub can take")
    trials, stopped = [], "N reached"
    for i in range(MAX_TRIALS):
        state["cuts"] = _merge(state["cuts"], new)
        _write(project, state)
        objective = solve(project)
        refreshed = rerun(project) or []
        proposed = _proposed_rows(project)
        trial = {"trial": len(state["trials"]) + 1, "added": new, "objective": objective,
                 "refreshed": refreshed, "verdict": _verdict(proposed)}
        state["trials"].append(trial)
        trials.append(trial)
        _write(project, state)
        new = _new_cuts(proposed, state["cuts"])
        if not proposed:
            stopped = "all pass"
            break
        if not new:
            stopped = "no cut can fix what still fails"
            break
        if stop_event.is_set():
            stopped = "stopped by the user"
            break
    return {"trials": trials, "stopped": stopped, "applied": state["cuts"]}


def remove_cut(project, *, period, hour, kind, scenario="seamless") -> dict:
    """Take one applied cut back (the next solve no longer has it)."""
    state = _state(project)
    key = (int(period), int(hour), scenario, kind)
    kept = [c for c in state["cuts"] if _key(c) != key]
    if len(kept) == len(state["cuts"]):
        raise HTTPException(status_code=404, detail=f"no applied {kind} cut at period {period} hour {hour}")
    state["cuts"] = kept
    _write(project, state)
    return get_cuts(project)


def clear_cuts(project) -> dict:
    """Take every cut back, and the trials with them."""
    _cuts_path(project).unlink(missing_ok=True)
    return get_cuts(project)


# ── the apply loop as a background job ──────────────────────────────────────
#
# As the campus MILP job: a ``record`` per project, served without ``thread``
# and ``stop_event``; in memory, so after a restart there is no status and the
# trials on disk still read back. One job per project: the solves themselves
# queue behind every other solve (``services.solve_queue``).

import contextvars  # noqa: E402
import logging  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

logger = logging.getLogger(__name__)
THREAD_NAME = "campus-island-cuts"
_LOCK = threading.Lock()
_JOBS: dict = {}


def _job_key(project) -> str:
    return str(project_registry.project_dir(project))


def _public(record):
    if not record:
        return None
    with _LOCK:
        return {k: v for k, v in record.items() if k not in ("thread", "stop_event")}


def _running(record) -> bool:
    if not record or record.get("state") != "running":
        return False
    t = record.get("thread")
    return t is None or t.ident is None or t.is_alive()


def _queue_solve(project, user_id, stop_event) -> float | None:
    """Re-solve the hub and save it, through the solve queue: it hydrates the
    project, binds its directory (so the solve reads the sidecar and the
    applied cuts), solves and saves. Returns the objective."""
    from services.solve_queue import solve_queue
    from services.study.runner import _wait

    d = project_registry.project_dir(project)
    cfg = d / "solver_config.json"
    job, created = solve_queue.enqueue_unique(
        project.name, project_key=project_registry.registry_key(project), storage_dir=str(d),
        solver_config_json=cfg.read_text() if cfg.is_file() else None, enqueued_by_user_id=user_id)
    if not created:
        # another job of the project (a campus run, a GFM search) holds the
        # queue: never wait on it as if it were this solve
        raise RuntimeError(f"the project already has a {job.kind} job {job.status}; the hub solve "
                           "was not queued")
    _wait(job, stop_event)
    if job.status != "completed":
        raise RuntimeError(f"the hub solve ended {job.status}" + (f": {job.error}" if job.error else ""))
    return job.objective


def _rerun(project, settings) -> list[dict]:
    """The campus file's ratings brought up to the re-solved hub, then the
    campus study again with its last settings: I3a, I3b, I4–I6 and the cuts
    the new sizes still need. Returns what was refreshed."""
    from services import campus_electrical_service as ce

    refreshed = refresh_ratings(project)
    ce.run(project, settings)
    return refreshed


def _run_job(project, user_id, settings, record) -> None:
    stop = record["stop_event"]
    try:
        out = apply_loop(project, solve=lambda p: _queue_solve(p, user_id, stop),
                         rerun=lambda p: _rerun(p, settings), stop_event=stop)
        with _LOCK:
            record.update(state="cancelled" if out["stopped"] == "stopped by the user" else "done",
                          finished_at=time.time(), stopped=out["stopped"], trials=len(out["trials"]),
                          message=out["stopped"])
    except Exception as exc:                                    # noqa: BLE001 - recorded on the job
        logger.exception("campus island cuts job failed")
        detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
        with _LOCK:
            record.update(state="failed", finished_at=time.time(), error=str(detail), message="failed")


def start_apply_job(project, user_id) -> dict:
    """Start the apply loop (owner QA) as a background job and answer its
    record. Refused, with nothing started, when the project's solve would
    not read the cuts (island constraints off), when there is no campus run
    to rerun, or when the run proposes nothing the hub can take; a 409 while
    the project's job runs."""
    from services import campus_electrical_service as ce

    key = _job_key(project)
    busy = HTTPException(status_code=409, detail="the frequency cuts are already being applied to this "
                                                 "project; wait for it or cancel it")
    if _running(_JOBS.get(key)):                 # first: a running job has already written its cuts
        raise busy
    d = project_registry.project_dir(project)
    cfg_path = d / "solver_config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.is_file() else {}
    if not cfg.get("island_constraints"):
        raise HTTPException(status_code=422, detail="island constraints are off in the solver settings, "
                                                    "so the hub solve would not read the cuts; turn them on")
    settings_path = ce.campus_dir(project) / ce.SETTINGS_FILE
    if not settings_path.is_file():
        raise HTTPException(status_code=422, detail="run the campus study first: the cuts come from its "
                                                    "island hours, and each trial reruns it")
    settings = json.loads(settings_path.read_text())
    if not _new_cuts(_proposed_rows(project), _state(project)["cuts"]):
        raise HTTPException(status_code=422, detail="nothing to apply: the last campus run proposes no "
                                                    "cut the hub can take")
    record = {"state": "running", "project": project.name, "started_at": time.time(), "finished_at": None,
              "trials": 0, "max_trials": MAX_TRIALS, "stopped": None, "error": None,
              "message": "applying the cuts", "thread": None, "stop_event": threading.Event()}
    with _LOCK:
        if _running(_JOBS.get(key)):
            raise busy
        _JOBS[key] = record
    ctx = contextvars.copy_context()
    thread = threading.Thread(target=lambda: ctx.run(_run_job, project, user_id, settings, record),
                              daemon=True, name=THREAD_NAME)
    record["thread"] = thread
    thread.start()
    return _public(record)


def apply_status(project) -> dict | None:
    return _public(_JOBS.get(_job_key(project)))


def cancel_apply_job(project) -> dict | None:
    """Ask the job to stop after the trial in hand (the solve running now
    is aborted in the queue)."""
    record = _JOBS.get(_job_key(project))
    if _running(record):
        record["stop_event"].set()
    return _public(record)
