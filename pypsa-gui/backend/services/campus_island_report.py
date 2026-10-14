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
selected hours (I3b), and the island-hours results of the campus run
(I9b): the island steady state and short circuit (I4, I5, I5b,
``island_static_campus``) and the island frequency check (I6,
``island_frequency_campus``), each table read against its gridspine column
contract. Each table names its ``group`` (``GROUPS``, the plan's order).
What is missing is listed in ``missing``, never invented; a file whose
columns are not the contract's is listed there too, never shown.
"""
from __future__ import annotations

import json
import math

import pandas as pd

CAVEAT = ("Screening, not a certificate: a steady-state hub LP, converter-aware fault "
          "current and aggregate-frequency screening of the site's island capability.")

_INPUTS_MODEL = "island sidecar (island_config.json): the study's inputs"
_RANKING_MODEL = "campus ranking (I3b) on the hub dispatch at the campus run's hours"
#: The report's groups, in the plan's order (I9).
GROUPS = ("inputs", "study", "hours", "static", "frequency")
STATIC_MISSING = "island steady state and short circuit at the island hours (I4, I5, I5b)"
FREQ_MISSING = "island frequency check at the island hours (I6)"
_STATIC_TITLES = {
    "flow": "Island load flow at the island hours (I4)",
    "flow_units": "Units in the island load flow (I4)",
    "flow_bus": "Bus voltages in the island (I4)",
    "flow_branch": "Branch loading in the island (I4)",
    "sc": "Fault levels as the island forms and once steady (I5)",
    "relays": "Relay sensitivity in the island (I5)",
    "relays_imax": "Relay current at each grid-forming current limit (I5, Q8)",
    "unbalanced": "Unbalanced fault levels (I5b)",
    "earth": "Earth reference, island against grid-connected (I5b)",
    "earth_relays": "Earth-fault relay sensitivity (I5b)",
}
_FREQ_TITLES = {
    "e1_hourly": "E1 at every hour of the seamless scenario: failing rows",
    "events": "Frequency events at the island hours (E1–E6)",
    "losses": "Network losses against the lossless islanding step",
    "bridge": "Bridge energy of the UPS and grid-forming units",
    "planned": "Planned islanding",
    "ups": "UPS ride-through",
}


def _table(tid, title, model, columns, rows, assumed_rows=(), group="inputs"):
    return {"id": tid, "group": group, "title": title, "model": model, "caveat": CAVEAT,
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
                  ("scenario", "status", "objective", "cost of island capability"), rows, group="study")


def _binding(study) -> dict:
    rows = []
    for sc, v in study["variants"].items():
        for b in (v.get("binding") or []):
            hours = b["hours"]
            rows.append([sc, b["row"], len(hours) if hours is not None else "static",
                         float(sum(b["duals"]))])
    return _table("binding", "Binding island rows and their duals", study.get("model", "hub LP"),
                  ("scenario", "row", "hours binding", "sum of duals"), rows, group="study")


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
                  rows, group="study")


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
                  ("period", "hour", "island reasons"), rows, group="hours")


def _hours_text(entries) -> str:
    """``[[period, hour, *rest]]`` as ``p<period> h<hour> rest…``, ``; ``-joined."""
    if not entries:
        return "none"
    return "; ".join(" ".join([f"p{e[0]}", f"h{e[1]}", *(str(x) for x in e[2:])]) for e in entries)


def _summary_table(tid, title, summary, keys, group) -> dict:
    """A driver summary as ``(item, value)`` rows: its hour lists as text, its
    other values as they are, then one ``ledger`` row per ledger line."""
    rows = []
    for key in keys:
        if key in ("label", "ledger", "models") or key not in summary:
            continue
        value = summary[key]
        if isinstance(value, list) and all(isinstance(e, list) for e in value):
            value = _hours_text(value)
        elif isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        rows.append([key, value])
    rows += [["ledger", line] for line in summary.get("ledger", [])]
    return _table(tid, title, summary.get("label", ""), ("item", "value"), rows, group=group)


#: Columns a later contract added (``trip_s``: the frequency check's trip
#: floor). A run written before them reads them as empty, not as off the contract.
_LATER_COLUMNS = frozenset({"trip_s"})


def _read(path, columns, missing):
    """The CSV's rows, or None (listed in ``missing``) when its header is not
    ``columns``, or ``columns`` without the later ones (``_LATER_COLUMNS``).
    Those read as empty, and ``missing`` says the run predates them, so an
    empty ``trip_s`` is not read as "no trip". Empty cells read as None."""
    df = pd.read_csv(path)
    have, want = list(df.columns), list(columns)
    if have != want:
        if have != [c for c in want if c not in _LATER_COLUMNS]:
            missing.append(f"{path.name} (columns differ from the contract)")
            return None
        # empty is not "no trip": the run predates the floor
        missing.append(f"{path.name} written before the trip floor: re-run for trip times")
    df = df.reindex(columns=want)
    return df.astype(object).where(pd.notna(df), None)


def _static_tables(run_dir, missing) -> list:
    try:
        from gridspine.drivers.campus_study import ISLAND_STATIC_CSV, ISLAND_STATIC_SUMMARY_JSON
        from gridspine.static.island_hours import MODELS, SUMMARY_KEYS, TABLES
    except ImportError:                          # a build without gridspine has no island hours
        missing.append(STATIC_MISSING)
        return []
    summary_path = run_dir / ISLAND_STATIC_SUMMARY_JSON
    if not summary_path.is_file():
        missing.append(STATIC_MISSING)
        return []
    summary = json.loads(summary_path.read_text())
    models = summary.get("models") or {}
    tables = [_summary_table("static_summary", "Island steady state and short circuit: what fails",
                             summary, SUMMARY_KEYS, "static")]
    for name, columns in TABLES:
        fname = ISLAND_STATIC_CSV[name]
        if not (run_dir / fname).is_file():
            missing.append(fname)
            continue
        df = _read(run_dir / fname, columns, missing)
        if df is None:
            continue
        # an empty cell (no assumed input) reads as None
        assumed = [i for i, v in enumerate(df["assumed"]) if v] if "assumed" in columns else []
        tables.append(_table(f"static_{name}", _STATIC_TITLES[name], models.get(fname, MODELS[name]),
                             columns, df.values.tolist(), assumed, group="static"))
    return tables


def _freq_tables(run_dir, missing) -> list:
    try:
        from gridspine.drivers import campus_study as cs
        from gridspine.static import frequency as F
    except ImportError:
        missing.append(FREQ_MISSING)
        return []
    summary_path = run_dir / cs.FREQ_SUMMARY_JSON
    if not summary_path.is_file():
        missing.append(FREQ_MISSING)
        return []
    summary = json.loads(summary_path.read_text())
    model = summary.get("label") or F.LABEL
    tables = [_summary_table("freq_summary", "Island frequency: what fails", summary, cs.FREQ_SUMMARY_KEYS,
                             "frequency")]
    for fname, columns in ((cs.FREQ_E1_HOURLY_CSV, F.E1_HOURLY_COLUMNS), (cs.FREQ_EVENTS_CSV, F.EVENT_COLUMNS),
                           (cs.FREQ_LOSSES_CSV, F.LOSSES_COLUMNS), (cs.FREQ_BRIDGE_CSV, F.BRIDGE_COLUMNS),
                           (cs.FREQ_PLANNED_CSV, F.PLANNED_COLUMNS), (cs.FREQ_UPS_CSV, F.UPS_COLUMNS)):
        if not (run_dir / fname).is_file():
            missing.append(fname)
            continue
        df = _read(run_dir / fname, columns, missing)
        if df is None:
            continue
        key = fname.removeprefix("campus_freq_").removesuffix(".csv")
        title = _FREQ_TITLES[key]
        if fname == cs.FREQ_E1_HOURLY_CSV:
            # Every hour is too long to show; its failing rows are the verdict.
            total = len(df)
            df = df[df["ok"].map(lambda v: str(v).lower() in ("false", "0"))]
            title = f"{title} ({len(df)} of {total} rows)"
        tables.append(_table(f"freq_{key}", title, model, columns, df.values.tolist(), group="frequency"))
    return tables


def _finite(value):
    """The payload with each infinite float as ``"inf"``/``"-inf"`` text (an
    unbounded autonomy, say): the route's JSON allows no Infinity."""
    if isinstance(value, float) and math.isinf(value):
        return "inf" if value > 0 else "-inf"
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value]
    return value


def island_report(project) -> dict:
    """``{caveat, tables, missing}`` for a hub project (see the module)."""
    from services import campus_electrical_service as ce
    from services.adequacy.island_config import validate

    state = ce.get_island(project)
    tables, missing = [], []
    if state["config"] is None:
        # The island-hours results below come from the campus's own island
        # data (I1), so they are shown without the sidecar too.
        missing.append("island_config.json")
    else:
        island = validate(state["config"])
        tables += [_requirements(island), _units(island)]
        study = state["study"]
        if study is None:
            missing.append("cost of islanding study")       # I2c, not the I4-I6 checks
        else:
            tables += [_costs(study), _binding(study), _facts(study)]
    hours = _island_hours(ce.campus_dir(project) / "run" / "campus_selected.csv")
    if hours is None:
        missing.append("campus run with island tables")
    else:
        tables.append(hours)
    run_dir = ce.campus_dir(project) / "run"
    tables += _static_tables(run_dir, missing)
    tables += _freq_tables(run_dir, missing)
    return _finite(json.loads(json.dumps({"caveat": CAVEAT, "tables": tables, "missing": missing},
                                         default=ce._jsonable)))
