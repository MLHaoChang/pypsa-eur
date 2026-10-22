"""The old → new card for applying a generated (or refined) series (ticket 04).

`harness.edit_card` registers this as the adapter of `apply_generated_profile`.
``preview`` reads the pending artifact the call names, runs
`apply.prepare_profile` (exactly what the apply would decide, nothing
written) and compares the series the target holds NOW with the one it would
hold: for a power target the peak (MW), the energy the model counts (Σ value
× snapshot weighting) and the load factor; for any other target the
maximum, mean and minimum. Notes carry the hours that differ, the apply's
warnings and the refinement ops. ``readback`` computes the same numbers from
the stored series, so a verified receipt means the network now holds what
the card showed.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

_CLASS = {"generators": "Generator", "loads": "Load", "links": "Link",
          "storage_units": "StorageUnit"}


def _refuse(exc: Exception):
    from fastapi import HTTPException

    from routers.profiles import _apply_refusal_kind
    from services.profile_gen.refusals import public_message

    raise HTTPException(422, {**_apply_refusal_kind(exc),
                              "message": public_message(exc, route="series_edit_card")}) from None


def _artifact(args: dict) -> tuple[pd.Series, dict]:
    import json

    import xarray as xr

    from services import chat_tools
    from services.profile_gen.job import _artifact_paths, pending_io_lock

    proj = chat_tools._authorized_project(str(args.get("project_id") or ""))
    nc, meta_path = _artifact_paths(proj.directory, str(args.get("job_id") or ""))
    if not nc.exists() or not meta_path.exists():
        from fastapi import HTTPException

        raise HTTPException(404, f"no pending profile for job {args.get('job_id')!r}")
    meta = json.loads(meta_path.read_text())
    point = args.get("point_id") or args.get("component")
    with pending_io_lock(), xr.open_dataset(nc) as ds:
        if point not in ds.data_vars:
            from fastapi import HTTPException

            raise HTTPException(422, {"error_kind": "point_not_in_profile",
                                      "message": f"the pending profile has no column {point!r}"})
        s = ds[point].to_series()
    return pd.Series(s.to_numpy(), index=pd.DatetimeIndex(s.index), name=point,
                     dtype=float), meta


# The target field in plain words (the card is user-facing copy).
_OP = {"scale_peak_to": "peak target", "scale_window": "hour window",
       "set_floor": "floor", "set_cap": "cap", "shift": "shift", "smooth": "smoothing",
       "rescale_energy": "energy target"}
_FIELD = {"p_set": "Load", "p_max_pu": "Availability", "p_min_pu": "Must-run floor",
          "marginal_cost": "Price", "inflow": "Inflow", "efficiency": "COP"}


def _stats(n, values: np.ndarray, power: bool, weighting: str) -> dict[str, float]:
    if power:
        w = n.snapshot_weightings[weighting].to_numpy(dtype=float)
        energy = float((values * w).sum())
        peak = float(values.max())
        hours = float(w.sum())
        return {"peak": peak, "energy_mwh": energy,
                "load_factor": energy / (peak * hours) if peak > 0 and hours > 0 else 0.0}
    return {"max": float(values.max()), "mean": float(values.mean()),
            "min": float(values.min())}


def _current(n, component_type: str, attribute: str, component: str) -> np.ndarray:
    return n.get_switchable_as_dense(_CLASS[component_type], attribute,
                                     inds=[component]).to_numpy(dtype=float)[:, 0]


def preview(args: dict) -> list[dict]:
    from services.profile_gen.apply import ProfileApplyError, prepare_profile
    from services.profile_gen.families import default_target
    from services.pypsa_service import PyPSAService

    series, meta = _artifact(args)
    component = str(args.get("component") or "")
    ctx = PyPSAService.get_active_context()
    try:
        ct, attr = default_target(meta, args.get("component_type"), args.get("attribute"))
        p = prepare_profile(ctx, component, series, meta, component_type=ct,
                            attribute=attr, time_basis=args.get("time_basis"))
    except ProfileApplyError as exc:
        _refuse(exc)
    n = ctx.network
    power = p.target.kind == "power"
    old_v = _current(n, ct, attr, component)
    new_v = p.aligned.reindex(n.snapshots).to_numpy(dtype=float)
    old, new = _stats(n, old_v, power, p.target.weighting), _stats(n, new_v, power, p.target.weighting)
    unit = "MW" if power else ("" if p.target.kind != "price" else str(p.full_meta.get("unit", "")))
    units = {"peak": unit, "energy_mwh": "MWh", "load_factor": "", "max": unit, "mean": unit,
             "min": unit}
    cls = _CLASS[ct]
    rows = [{"component_class": cls, "name": component, "field": f"{attr}.{k}",
             "label": ("Load factor (weighted, model snapshots)" if k == "load_factor"
                       else f"{_FIELD.get(attr, 'Series')} {k.replace('_mwh', '')}"),
             "old": round(old[k], 6), "new": round(new[k], 6), "unit": units[k], "role": "typed"}
            for k in new]
    differ = int((~np.isclose(old_v, new_v)).sum())
    rows.append(_note(cls, component, "hours", f"{differ} of {len(new_v)} snapshots change"))
    for w in p.warnings:
        rows.append(_note(cls, component, "warning", w))
    ops = meta.get("refinements") or []
    if ops:
        rows.append(_note(cls, component, "refinements",
                          "refined by " + ", ".join(_OP.get(str(o.get("op")), "an edit") for o in ops)))
    return rows


def readback(args: dict, rows: list[dict]) -> list[Any]:
    from services.profile_gen.families import default_target
    from services.pypsa_service import PyPSAService

    _series, meta = _artifact(args)
    ct, attr = default_target(meta, args.get("component_type"), args.get("attribute"))
    n = PyPSAService.get_active_context().network
    from services.profile_gen.apply import TARGETS

    spec = TARGETS[(ct, attr)]
    values = _current(n, ct, attr, str(args.get("component") or ""))
    stats = _stats(n, values, spec.kind == "power", spec.weighting)
    out = []
    for row in rows:
        key = row["field"].split(".", 1)[-1]
        out.append(round(stats[key], 6) if row["role"] == "typed" and key in stats else None)
    return out


def _note(cls: str, name: str, field: str, text: str) -> dict:
    return {"component_class": cls, "name": name, "field": field, "label": field, "old": None,
            "new": None, "unit": "", "role": "note", "note": text}


# ── "Generate defaults for all": one card for the batch (plan D) ────────────


def _job_meta(project_id: str, job_id: str) -> dict:
    import json

    from services import chat_tools
    from services.profile_gen.job import _artifact_paths

    proj = chat_tools._authorized_project(project_id)
    _nc, meta_path = _artifact_paths(proj.directory, job_id)
    if not meta_path.exists():
        from fastapi import HTTPException

        raise HTTPException(404, f"no pending profile for job {job_id!r}")
    return json.loads(meta_path.read_text())


def _batch(args: dict) -> list[tuple[str, dict]]:
    project = str(args.get("project_id") or "")
    out = []
    for job in args.get("job_ids") or []:
        meta = _job_meta(project, str(job))
        out.append((str(job), {"project_id": project, "job_id": str(job),
                               "component": (meta.get("points") or [""])[0], "_meta": meta}))
    return out


def preview_batch(args: dict) -> list[dict]:
    """Each default's old → new rows, with why it was picked and how sized."""
    rows: list[dict] = []
    for _job, one in _batch(args):
        meta = one.pop("_meta")
        sub = preview(one)
        default = meta.get("default") or {}
        if default:
            ct = (meta.get("target") or ["loads"])[0]
            sub.append(_note(_CLASS.get(ct, ""), one["component"], "default",
                             f"assumed: {default.get('reason')}; sized by its "
                             f"{default.get('sizing')} (fixed load "
                             f"{default.get('static_p_set_mw')} MW)"))
        rows += sub
    return rows


def readback_batch(args: dict, rows: list[dict]) -> list[Any]:
    """`readback` per default, matched to its rows by component."""
    out: list[Any] = [None] * len(rows)
    for _job, one in _batch(args):
        one.pop("_meta")
        idx = [i for i, r in enumerate(rows) if r.get("name") == one["component"]]
        for i, v in zip(idx, readback(one, [rows[i] for i in idx])):
            out[i] = v
    return out


# ── Several points of one job: apply_generated_profiles (RP-09) ─────────────


def _members(args: dict) -> list[dict]:
    """One `preview`/`readback` argument set per member, in the order the
    batch route writes them: both come from `batch.batch_members`."""
    from services import chat_tools
    from services.profile_gen.batch import batch_members
    from services.pypsa_service import PyPSAService

    base = {"project_id": str(args.get("project_id") or ""),
            "job_id": str(args.get("job_id") or ""),
            "time_basis": args.get("time_basis")}
    chat_tools._authorized_project(base["project_id"])
    meta = _job_meta(base["project_id"], base["job_id"])
    chosen, _skipped = batch_members(
        [str(c) for c in meta.get("points") or []],
        PyPSAService.get_active_context().network.generators,
        [(str(c), None) for c in args.get("components") or []],
        bool(args.get("all_from_job")))
    return [{**base, "component": c, **({"point_id": pid} if pid else {})}
            for c, pid in chosen]


def preview_members(args: dict) -> list[dict]:
    return [row for one in _members(args) for row in preview(one)]


def readback_members(args: dict, rows: list[dict]) -> list[Any]:
    out: list[Any] = [None] * len(rows)
    for one in _members(args):
        idx = [i for i, r in enumerate(rows) if r.get("name") == one["component"]]
        for i, v in zip(idx, readback(one, [rows[i] for i in idx])):
            out[i] = v
    return out
