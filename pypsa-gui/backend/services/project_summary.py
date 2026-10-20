"""LC-15: one bounded summary of a project's assets (OJ-13, OJ-14).

Built from a network and its solver config, so the same code serves the
caller's live active project and another project's saved files. Every number
is read from the network; nothing is estimated. What the project lacks (no
profile for a variable generator, no run, stale results, validation issues)
is stated, not left out.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

DEFAULT_LIMIT = 5
MAX_LIMIT = 25
CLASSES = {
    "Bus": "buses", "Line": "lines", "Transformer": "transformers", "Link": "links",
    "Generator": "generators", "Load": "loads", "StorageUnit": "storage_units",
    "Store": "stores", "ShuntImpedance": "shunt_impedances",
}
# Per class: the fields shown per item, and the one items are ranked by.
_FIELDS = {
    "Bus": (("v_nom", "carrier"), "v_nom"),
    "Line": (("bus0", "bus1", "s_nom", "s_nom_extendable", "length"), "s_nom"),
    "Transformer": (("bus0", "bus1", "s_nom", "s_nom_extendable"), "s_nom"),
    "Link": (("bus0", "bus1", "carrier", "p_nom", "p_nom_extendable"), "p_nom"),
    "Generator": (("bus", "carrier", "p_nom", "p_nom_extendable"), "p_nom"),
    "Load": (("bus", "carrier", "p_set"), "p_set"),
    "StorageUnit": (("bus", "carrier", "p_nom", "max_hours", "p_nom_extendable"), "p_nom"),
    "Store": (("bus", "carrier", "e_nom", "e_nom_extendable"), "e_nom"),
    "ShuntImpedance": (("bus", "g", "b"), "g"),
}
_EXTENDABLE = {"Generator": "p_nom_extendable", "Link": "p_nom_extendable", "StorageUnit": "p_nom_extendable",
               "Store": "e_nom_extendable", "Line": "s_nom_extendable", "Transformer": "s_nom_extendable"}
_VARIABLE = ("solar", "wind", "pv", "ror", "run of river")


def _plain(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    return value


def _total(frame, column: str) -> float:
    return float(frame[column].sum()) if column in frame and len(frame) else 0.0


def _assets(n, klass: str, limit: int) -> dict:
    frame = getattr(n, CLASSES[klass])
    fields, rank = _FIELDS[klass]
    order = frame.sort_values(rank, ascending=False, kind="stable") if rank in frame else frame
    items = []
    for name, row in order.head(limit).iterrows():
        item = {"name": str(name)}
        item.update({f: _plain(row[f]) for f in fields if f in row.index})
        if klass == "StorageUnit":
            item["energy_mwh"] = _plain(float(row["p_nom"]) * float(row["max_hours"]))
        items.append(item)
    return {"total": len(frame), "items": items, "has_more": len(frame) > limit}


def _resolution(n) -> str | None:
    snapshots = n.snapshots.get_level_values(-1) if hasattr(n.snapshots, "levels") else n.snapshots
    if len(snapshots) < 2:
        return None
    try:
        step = (snapshots[1:] - snapshots[:-1]).median()
        hours = step.total_seconds() / 3600
    except (AttributeError, TypeError):
        return None
    return f"{hours:g}h" if hours >= 1 else f"{step.total_seconds() / 60:g}min"


def _time(n) -> dict:
    snapshots = n.snapshots.get_level_values(-1) if hasattr(n.snapshots, "levels") else n.snapshots
    first = snapshots[0] if len(snapshots) else None
    last = snapshots[-1] if len(snapshots) else None
    return {"snapshot_count": len(n.snapshots),
            "first": first.isoformat() if hasattr(first, "isoformat") else _plain(first),
            "last": last.isoformat() if hasattr(last, "isoformat") else _plain(last),
            "resolution": _resolution(n),
            "investment_periods": [_plain(p) for p in n.investment_periods]}


def _capacity(n) -> dict:
    gens = n.generators
    by_carrier = {str(c): float(v) for c, v in gens.groupby("carrier")["p_nom"].sum().items()} if len(gens) else {}
    storage_energy = sum(float(r.p_nom) * float(r.max_hours) for r in n.storage_units.itertuples()) \
        + _total(n.stores, "e_nom")
    extendable = {}
    for klass, column in _EXTENDABLE.items():
        frame = getattr(n, CLASSES[klass])
        if column in frame and len(frame):
            names = [str(x) for x in frame.index[frame[column].astype(bool)]]
            if names:
                extendable[klass] = names
    return {"generation_mw_by_carrier": by_carrier,
            "storage_power_mw": _total(n.storage_units, "p_nom"),
            "storage_energy_mwh": _plain(storage_energy),
            "link_mw": _total(n.links, "p_nom"),
            "transformer_mva": _total(n.transformers, "s_nom"),
            "extendable": extendable}


def _gaps(n) -> dict:
    profiled = set(n.generators_t.p_max_pu.columns)
    variable = [str(name) for name, carrier in n.generators.carrier.items()
                if any(word in str(carrier).lower() for word in _VARIABLE) and name not in profiled]
    return {"variable_generators_without_profile": variable,
            "loads_with_time_series": len(n.loads_t.p_set.columns)}


def _validation(n, solver_config) -> dict:
    from services.validation_service import validate_for_run

    try:
        issues = validate_for_run(n, solver_config)
    except Exception as error:  # noqa: BLE001 — a summary reports, never fails, on a check
        return {"checked": False, "reason": type(error).__name__}
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity != "error"]
    return {"checked": True, "errors": len(errors), "warnings": len(warnings),
            "first": [{"severity": i.severity, "code": i.code, "component_class": i.component_class, "name": i.name}
                      for i in (errors + warnings)[:3]]}


def _latest_run(root: Path, project_id: str, fingerprint: str | None) -> dict | None:
    from services import run_evidence

    records = run_evidence._all_records(root, project_id)
    if not records:
        return None
    record = records[0]
    current = None
    if record.get("status") == "completed" and fingerprint and record.get("fingerprint"):
        current = record["fingerprint"] == fingerprint
    return {"run_id": record["run_id"], "status": record["status"], "mode": record.get("mode"),
            "finished_at": record.get("finished_at"), "current": current,
            "note": None if current is not None else "Whether these results match the inputs is not known."}


def summarise(n, *, solver_config, project_id: str, project_name: str, root: Path,
              live_ctx: Any = None, limit: int = DEFAULT_LIMIT) -> dict:
    """`live_ctx` is the caller's active context when `n` is its live network.
    Only then is the input fingerprint known (it covers the context's user
    time series too), so a saved project's latest run says "not known"."""
    from services.run_evidence import _input_fingerprint

    live = live_ctx is not None
    fingerprint = _input_fingerprint(live_ctx) if live else None
    return {
        "version": 1,
        "project_id": project_id,
        "project_name": project_name,
        "source": "live" if live else "saved",
        "counts": {klass: len(getattr(n, attr)) for klass, attr in CLASSES.items()},
        "assets": {klass: _assets(n, klass, limit) for klass in CLASSES if len(getattr(n, CLASSES[klass]))},
        "capacity": _capacity(n),
        "lines": {"count": len(n.lines), "total_length_km": _total(n.lines, "length"),
                  "total_s_nom_mva": _total(n.lines, "s_nom")},
        "time": _time(n),
        "solver_mode": getattr(solver_config, "mode", None),
        "latest_run": _latest_run(root, project_id, fingerprint),
        "validation": _validation(n, solver_config),
        "gaps": _gaps(n),
        "units": {"p_nom": "MW", "s_nom": "MVA", "e_nom": "MWh", "energy_mwh": "MWh", "length": "km",
                  "v_nom": "kV", "p_set": "MW"},
    }
