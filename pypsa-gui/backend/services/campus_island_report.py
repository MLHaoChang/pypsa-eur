"""
The island report of a hub project (campus island plan, I9a).

``island_report(project)`` is the payload the panel shows and the bundle
carries: a list of tables, each ``{id, title, model, caveat, columns, rows,
assumed_rows}``. The plan's rules for it (I9):

* every table states that the study is a screening, not a certificate
  (``CAVEAT``);
* every table names the model behind it;
* every row that rests on an ``assumed`` value is marked (``assumed_rows``,
  indexes into ``rows``).

It reads what is on disk: the project's island sidecar, the last island
study (``campus_electrical_service.run_island_study``) and the campus run's
selected hours (I3b). What is missing is listed in ``missing``, never
invented. The load-flow, short-circuit and frequency verdicts at the island
hours (I4–I6) join it in I9b.
"""
from __future__ import annotations

import json

import pandas as pd

CAVEAT = ("Screening, not a certificate: a steady-state hub LP, converter-aware fault "
          "current and aggregate-frequency screening of the site's island capability.")

_INPUTS_MODEL = "island sidecar (island_config.json): the study's inputs"
_RANKING_MODEL = "campus ranking (I3b) on the hub dispatch at the campus run's hours"


def _table(tid, title, model, columns, rows, assumed_rows=()):
    return {"id": tid, "title": title, "model": model, "caveat": CAVEAT,
            "columns": list(columns), "rows": [list(r) for r in rows],
            "assumed_rows": sorted(assumed_rows)}


def _requirements(island) -> dict:
    req = island["requirements"]
    rows, assumed = [], []
    for field in sorted(req["values"]):
        source = req["sources"].get(field, "assumed")
        if source == "assumed":
            assumed.append(len(rows))
        rows.append([field, req["values"][field], source])
    for name in sorted(req["frequency_limits"]):
        lim = req["frequency_limits"][name]
        for field in sorted(lim["values"]):
            source = lim["sources"][field]
            if source == "assumed":
                assumed.append(len(rows))
            rows.append([f"{name}.{field}", lim["values"][field], source])
    for field in ("scenarios", "linearised_uc", "ride_through_storage"):
        rows.append([field, req[field], "definition"])
    return _table("requirements", "Island requirements", _INPUTS_MODEL,
                  ("field", "value", "source"), rows, assumed)


def _units(island) -> dict:
    rows, assumed = [], []
    for name in sorted(island["units"]):
        unit = island["units"][name]
        mode = unit.get("control") or unit.get("governor") or ""
        guessed = sorted(f for f, s in unit.get("sources", {}).items() if s == "assumed")
        if guessed:
            assumed.append(len(rows))
        rows.append([name, unit["kind"], mode, ", ".join(guessed)])
    return _table("units", "Island units and control modes", _INPUTS_MODEL,
                  ("unit", "kind", "control or governor", "assumed fields"), rows, assumed)


def _costs(study) -> dict:
    variants = study["variants"]
    capability = study.get("cost_of_island_capability") or {}
    rows = [["baseline (no island rows)", variants["baseline"]["status"],
             variants["baseline"].get("objective"), 0.0]]
    for sc in study.get("scenarios") or [k for k in variants if k != "baseline"]:
        v = variants.get(sc) or {}
        rows.append([sc, v.get("status"), v.get("objective"), capability.get(sc)])
    return _table("costs", "Cost of island capability", study.get("model", "hub LP"),
                  ("scenario", "status", "objective", "cost of island capability"), rows)


def _binding(study) -> dict:
    rows = []
    for sc, v in study["variants"].items():
        for b in (v.get("binding") or []):
            hours = b["hours"]
            rows.append([sc, b["row"], len(hours) if hours is not None else "static",
                         float(sum(b["duals"]))])
    return _table("binding", "Binding island rows and their duals", study.get("model", "hub LP"),
                  ("scenario", "row", "hours binding", "sum of duals"), rows)


def _facts(study) -> dict:
    rows = []
    for sc, v in study["variants"].items():
        facts = v.get("facts") or {}
        if not facts:
            continue
        uc = facts.get("uc") or {}
        rows.append([sc, facts.get("n_plus_k"), (facts.get("fuel") or {}).get("status"),
                     uc.get("status"), ", ".join(uc.get("committed") or []),
                     (facts.get("surplus") or {}).get("status") if facts.get("surplus") else None])
    return _table("facts", "Ride-through, fuel and unit commitment", study.get("model", "hub LP"),
                  ("scenario", "N+k", "fuel", "unit commitment", "committed gensets", "surplus"),
                  rows)


def _island_hours(selected_csv) -> dict | None:
    if not selected_csv.is_file():
        return None
    try:
        from gridspine.ranking.campus import ISLAND_CRITERIA
    except ImportError:                          # a build without gridspine has no ranking
        return None

    island = {r for r, _ in ISLAND_CRITERIA}
    sel = pd.read_csv(selected_csv)
    rows = []
    for period, hour, reasons in zip(sel["period"], sel["hour"], sel["reasons"]):
        mine = [r for r in str(reasons).split(";") if r in island]
        if mine:
            rows.append([int(period), int(hour), "; ".join(mine)])
    return _table("island_hours", "Island critical hours", _RANKING_MODEL,
                  ("period", "hour", "island reasons"), rows)


def island_report(project) -> dict:
    """``{caveat, tables, missing}`` for a hub project (see the module)."""
    from services import campus_electrical_service as ce
    from services.adequacy.island_config import validate

    state = ce.get_island(project)
    tables, missing = [], []
    if state["config"] is None:
        return {"caveat": CAVEAT, "tables": [], "missing": ["island_config.json"]}
    island = validate(state["config"])
    tables += [_requirements(island), _units(island)]
    study = state["study"]
    if study is None:
        missing.append("island study")
    else:
        tables += [_costs(study), _binding(study), _facts(study)]
    hours = _island_hours(ce.campus_dir(project) / "run" / "campus_selected.csv")
    if hours is None:
        missing.append("campus run with island tables")
    else:
        tables.append(hours)
    return json.loads(json.dumps({"caveat": CAVEAT, "tables": tables, "missing": missing},
                                 default=ce._jsonable))
