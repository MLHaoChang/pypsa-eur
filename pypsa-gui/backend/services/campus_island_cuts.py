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


def apply_loop(project, *, solve, rerun, stop_event) -> dict:
    """Apply, re-solve, rerun, until nothing fails, nothing more can be cut,
    ``MAX_TRIALS``, or ``stop_event`` (owner QA). ``solve(project)`` re-solves
    and saves the hub and returns its objective; ``rerun(project)`` reruns
    the campus study, which proposes the next cuts."""
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
        rerun(project)
        proposed = _proposed_rows(project)
        trial = {"trial": len(state["trials"]) + 1, "added": new, "objective": objective,
                 "verdict": _verdict(proposed)}
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
