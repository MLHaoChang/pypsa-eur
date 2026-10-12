"""Bounded load-flow evidence over the CURRENT results (live-voice issue 07).

``get_loadflow_summary`` ranks the AC branches (lines, transformers) of the
active project's current solve. It aggregates what the Results tab reads: the
same source selection (``routers.results._result_df``: LOPF snapshot or AC PF
snapshot), the same dispatch freshness gate (``services.dispatch_status``), the
same rating (``s_nom_opt`` for extendable branches, otherwise ``s_nom``) and the
same energy weighting basis (``period_utils.snapshot_weights``) as the Compare
loading summary. The model never supplies numbers, paths or code, only a source,
a window and a bound.

Two conventions are never mixed up in the payload:

* ``active_power_proxy`` (default, always the case for LOPF results): the larger
  of ``|p0|`` and ``|p1|`` against the rating. It is NOT an AC apparent-power
  assessment.
* ``ac_apparent_power`` (only ``source='ac_pf'`` branches whose reactive flows
  exist): the larger of ``hypot(p0, q0)`` and ``hypot(p1, q1)``.

Neither proves AC thermal compliance (no N-1, no voltage limits). The payload
says so in ``claims``. Missing or zero ratings, branches without flow data,
unconverged AC snapshots and uncomputed voltage evidence are reported, never
silently ranked as zero.

Historical runs are not computed here: ``run_id`` is resolved through the run
evidence store (issue 06) and answers a typed unavailable until a ``loadflow``
section artifact exists for that run.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import numpy as np
import pandas as pd
from fastapi import HTTPException

from services import run_evidence
from services.pypsa_service import PyPSAService

SOURCES = ("lopf", "ac_pf")
DEFAULT_LIMIT = 5
MAX_LIMIT = 25
MAX_LISTED_NAMES = 5
MAX_PAYLOAD_CHARS = 3800  # under the harness per-result cap (4000), so no tail is cut
AT_LIMIT_FRACTION = 0.99
OVER_LIMIT_TOLERANCE = 1e-6
HISTORICAL_SECTION = "loadflow"

_BRANCH_CLASSES = (
    ("Line", "lines", "lines_t"),
    ("Transformer", "transformers", "transformers_t"),
)
_FAILED_STATUSES = frozenset({"failed", "aborted"})
_INFEASIBLE_MARKERS = ("infeas", "unbound")

RANKING_CONVENTION = (
    "Descending peak utilisation = max over window of flow / (rating * s_max_pu); "
    "flow = larger terminal magnitude; ties by name. rating_pct ignores s_max_pu "
    "(the Load flow tab's number)."
)
LIMITS_CLAIM = "Not proof of AC thermal compliance: no N-1, no voltage limits, no dynamic limits."
UNITS = {"rating": "MVA", "flow": "MW (active_power_proxy) or MVA (ac_apparent_power)",
         "utilisation": "%", "hours": "weighted hours"}


def _refuse(kind: str, message: str, status: int = 422, **extra: Any) -> None:
    detail = {"error_kind": kind, "message": message}
    detail.update(extra)
    raise HTTPException(status, detail)


def _unavailable(kind: str, message: str, **extra: Any) -> dict:
    out = {"status": "unavailable", "unavailable_kind": kind, "message": message}
    out.update(extra)
    return out


def _r(value: Any, digits: int = 3) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(f, digits) if math.isfinite(f) else None


def _name(value: Any) -> str:
    return str(value)[:80]


def _check_args(source: Any, limit: Any, window_from: Any, window_to: Any,
                run_id: Any) -> None:
    if source not in SOURCES:
        _refuse("invalid_loadflow_argument", "source must be 'lopf' or 'ac_pf'.")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        _refuse("invalid_loadflow_argument", f"limit must be an integer from 1 to {MAX_LIMIT}.")
    for label, value in (("window_from", window_from), ("window_to", window_to)):
        if value is not None and (type(value) is not int or value < 0):
            _refuse("invalid_loadflow_argument", f"{label} must be a non-negative snapshot index.")
    if (window_from is not None and window_to is not None) and window_from > window_to:
        _refuse("invalid_loadflow_argument", "window_from must not exceed window_to.")
    if run_id is not None and (not isinstance(run_id, str) or not run_evidence._valid_run_id(run_id)):
        _refuse("invalid_loadflow_argument", "run_id must be a run id from list_project_runs.")


def _historical(run_id: str) -> dict:
    try:
        record = run_evidence.get_run_evidence(run_id, HISTORICAL_SECTION)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("error_kind") in ("run_not_found", "evidence_unavailable"):
            return _unavailable(
                "historical_loadflow_unavailable",
                "No stored load-flow evidence exists for this run. Historical load-flow "
                "summaries need run evidence artifacts (live-voice issue 06); only the "
                "current results can be summarised now.",
                run_id=run_id, reason=detail.get("reason") or detail.get("error_kind"),
                depends_on="issue-06 run evidence", scope="historical",
                current_results_hint="Call get_loadflow_summary without run_id for the current results.")
        raise
    return {"status": "ok", "scope": "historical", "run_id": run_id,
            "section": HISTORICAL_SECTION, "run_status": record.get("status"),
            "data": record.get("data")}


def _solve_state() -> dict:
    state = PyPSAService.get_solver_state()
    return {
        "status": state.get("status"),
        "condition": None if state.get("condition") is None else str(state.get("condition"))[:60],
        "objective": _r(state.get("objective"), 2),
    }


def _gate(n: Any, solve: dict, source: str) -> dict | None:
    from services.dispatch_status import dispatch_status

    status = solve["status"]
    condition = (solve["condition"] or "").lower()
    if status == "running":
        return _unavailable("solve_in_progress",
                            "A solve is running; load-flow results are not final yet.", solve=solve)
    if status in _FAILED_STATUSES or any(m in condition for m in _INFEASIBLE_MARKERS):
        kind = "solve_infeasible" if any(m in condition for m in _INFEASIBLE_MARKERS) else "solve_failed"
        return _unavailable(
            kind, "The last solve did not produce a usable solution; no load-flow values are "
            "reported from earlier results.", solve=solve)
    freshness = dispatch_status(n)
    if not getattr(n, "is_solved", False) or freshness == "none":
        return _unavailable("not_solved", "The network has not been solved; run a simulation first.",
                            solve=solve, freshness="none")
    if freshness == "stale":
        return _unavailable(
            "stale_results", "The topology changed after the last solve; re-run the simulation "
            "before reading load flow.", solve=solve, freshness="stale")
    if source == "ac_pf":
        state = PyPSAService.get_solver_state()
        if state.get("ac_pf_results") is None or state.get("ac_pf_convergence") is None:
            return _unavailable("ac_pf_not_available",
                                "No AC power-flow result exists; run the AC PF stage or use source='lopf'.",
                                solve=solve)
    return None


def _window(n: Any, window_from: int | None, window_to: int | None) -> tuple[pd.Index, int, int]:
    sns = n.snapshots
    total = len(sns)
    start = 0 if window_from is None else window_from
    end = total - 1 if window_to is None else window_to
    if total == 0 or start >= total or end >= total:
        _refuse("invalid_loadflow_window",
                f"The snapshot window must lie inside 0..{max(total - 1, 0)}.", 422,
                snapshots_total=total)
    return sns[start:end + 1], start, end


def _ac_convergence(n: Any, win_index: pd.Index, start: int, end: int) -> tuple[np.ndarray | None, dict]:
    state = PyPSAService.get_solver_state()
    entries = state.get("ac_pf_convergence_list") or []
    info = {"converged_count": state.get("ac_pf_converged_count"),
            "total_snapshots": state.get("ac_pf_total_snapshots")}
    if len(entries) != len(n.snapshots):
        info["per_snapshot"] = "unaligned"
        return None, info
    ok = np.array([bool(e.get("ok")) for e in entries[start:end + 1]])
    info["per_snapshot"] = "aligned"
    info["unconverged_in_window"] = int((~ok).sum())
    return ok, info


def _frame(n: Any, accessor: str, attr: str, source: str, names: pd.Index,
           win_index: pd.Index) -> pd.DataFrame | None:
    from routers.results import _result_df

    df = _result_df(n, accessor, attr, source)
    if df is None or getattr(df, "empty", True):
        return None
    try:
        out = df.reindex(index=win_index, columns=[c for c in names if c in df.columns])
        return out.astype(float)
    except Exception:  # noqa: BLE001 — a malformed frame is missing evidence, not a crash
        return None


def _smax_matrix(n: Any, comp_df: pd.DataFrame, t_accessor: str, names: pd.Index,
                 win_index: pd.Index) -> pd.DataFrame:
    static = pd.to_numeric(comp_df.get("s_max_pu", pd.Series(1.0, index=comp_df.index)),
                           errors="coerce").reindex(names).fillna(1.0)
    smax = pd.DataFrame(np.tile(static.to_numpy(float), (len(win_index), 1)),
                        index=win_index, columns=names)
    t_df = getattr(getattr(n, t_accessor, None), "s_max_pu", None)
    if t_df is not None and not t_df.empty:
        for col in t_df.columns:
            if col in smax.columns:
                smax[col] = t_df[col].reindex(win_index).fillna(static[col]).to_numpy(float)
    return smax


def _rating(row: pd.Series) -> tuple[float | None, str | None]:
    extendable = bool(row.get("s_nom_extendable", False))
    field = "s_nom_opt" if extendable else "s_nom"
    try:
        value = float(row.get(field))
    except (TypeError, ValueError):
        return None, field
    if not math.isfinite(value) or value <= 1e-9:
        return None, field
    return value, field


def _summarise_class(n: Any, cls: str, comp: str, t_accessor: str, source: str,
                     win_index: pd.Index, weights: pd.Series, valid_mask: np.ndarray | None,
                     missing: list[str]) -> tuple[list[dict], list[dict]]:
    comp_df = getattr(n, comp)
    if comp_df is None or comp_df.empty:
        return [], []
    names = comp_df.index
    p0 = _frame(n, t_accessor, "p0", source, names, win_index)
    if p0 is None:
        missing.append(f"{cls} active-power flows (p0) are not populated for source '{source}'.")
        return [], [{"name": _name(nm), "class": cls, "reason": "no_flow_data"} for nm in names]
    p1 = _frame(n, t_accessor, "p1", source, names, win_index)
    q0 = q1 = None
    if source == "ac_pf":
        q0 = _frame(n, t_accessor, "q0", source, names, win_index)
        q1 = _frame(n, t_accessor, "q1", source, names, win_index)
    smax = _smax_matrix(n, comp_df, t_accessor, names, win_index)
    mask = pd.Series(True if valid_mask is None else valid_mask, index=win_index)
    w = weights.reindex(win_index).fillna(0.0)

    rows: list[dict] = []
    skipped: list[dict] = []
    for name in names:
        label = _name(name)
        if name not in p0.columns:
            skipped.append({"name": label, "class": cls, "reason": "no_flow_data"})
            continue
        rating, rating_field = _rating(comp_df.loc[name])
        if rating is None:
            skipped.append({"name": label, "class": cls, "reason": "rating_missing_or_zero",
                            "rating_field": rating_field})
            continue
        a0 = p0[name].where(mask)
        two_terminals = p1 is not None and name in p1.columns
        flow = a0.abs()
        basis = "active_power_proxy"
        if two_terminals:
            flow = pd.Series(np.fmax(a0.abs().to_numpy(), p1[name].where(mask).abs().to_numpy()),
                             index=win_index)
        if q0 is not None and name in q0.columns and q0[name].notna().any():
            s0 = np.hypot(a0.to_numpy(), q0[name].to_numpy())
            s1 = (np.hypot(p1[name].to_numpy(), q1[name].to_numpy())
                  if two_terminals and q1 is not None and name in q1.columns else np.full(len(a0), np.nan))
            flow = pd.Series(np.where(mask.to_numpy(), np.fmax(s0, s1), np.nan), index=win_index)
            basis = "ac_apparent_power"
        present = flow.notna()
        if not present.any():
            skipped.append({"name": label, "class": cls, "reason": "no_flow_data"})
            continue
        limit_t = rating * smax[name]
        valid_limit = present & (limit_t > 1e-9)
        if not valid_limit.any():
            skipped.append({"name": label, "class": cls, "reason": "zero_effective_limit"})
            continue
        util = (flow / limit_t).where(valid_limit)
        peak_util = float(util.max())
        peak_at = util.idxmax()
        smax_col = smax[name]
        coverage = int(valid_limit.sum())
        wp = w.where(valid_limit, 0.0)
        wsum = float(wp.sum())
        mean_util = float((util.fillna(0.0) * wp).sum() / wsum) if wsum > 0 else None
        at_limit = float(((util >= AT_LIMIT_FRACTION).astype(float) * wp).sum())
        over = float(((util > 1.0 + OVER_LIMIT_TOLERANCE).astype(float) * wp).sum())
        smin, smax_v = float(smax_col.min()), float(smax_col.max())
        row = {
            "name": label,
            "class": cls,
            "basis": basis,
            "util_pct": _r(100 * peak_util, 2),
            "rating_pct": _r(100 * float(flow[valid_limit].max()) / rating, 2),
            "mean_util_pct": _r(None if mean_util is None else 100 * mean_util, 2),
            "peak_flow": _r(flow[valid_limit].max(), 2),
            "peak_at": str(peak_at),
            "h_at_limit": _r(at_limit, 2),
            "h_over_limit": _r(over, 2),
            "rating": {"value": _r(rating, 2), "basis": "optimized" if rating_field == "s_nom_opt" else "configured"},
            "s_max_pu": {"min": _r(smin, 3), "max": _r(smax_v, 3)},
            "_rank": peak_util,
        }
        if not two_terminals:
            row["terminals"] = "bus0_only"
        if coverage < len(win_index):
            row["snapshots_with_data"] = coverage
        rows.append(row)
    return rows, skipped


def _evidence_token(project_id: str, source: str, start: int, end: int, solve: dict,
                    rows: list[dict]) -> str:
    material = json.dumps([project_id, source, start, end, solve, [
        (r["name"], r["util_pct"], r["rating_pct"]) for r in rows]],
        sort_keys=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def get_loadflow_summary(source: str = "lopf", limit: int = DEFAULT_LIMIT,
                         window_from: int | None = None, window_to: int | None = None,
                         run_id: str | None = None) -> dict:
    _check_args(source, limit, window_from, window_to, run_id)
    _root, project_id, _owner = run_evidence._authority()
    if run_id is not None:
        return _historical(run_id)

    n = PyPSAService.get_network()
    solve = _solve_state()
    blocked = _gate(n, solve, source)
    if blocked is not None:
        return blocked

    win_index, start, end = _window(n, window_from, window_to)
    from services import period_utils

    weights = period_utils.snapshot_weights(n, "generators")
    missing: list[str] = []
    valid_mask = None
    ac_info = None
    excluded = 0
    if source == "ac_pf":
        valid_mask, ac_info = _ac_convergence(n, win_index, start, end)
        if valid_mask is None:
            missing.append("AC convergence is not aligned with the snapshots; unconverged "
                           "snapshots could not be excluded.")
        else:
            excluded = int((~valid_mask).sum())
            if not valid_mask.any():
                return _unavailable("ac_pf_not_converged",
                                    "No AC power flow converged inside the requested window.",
                                    solve=solve, ac_pf=ac_info)

    rows: list[dict] = []
    skipped: list[dict] = []
    for cls, comp, t_accessor in _BRANCH_CLASSES:
        r, s = _summarise_class(n, cls, comp, t_accessor, source, win_index, weights,
                                valid_mask, missing)
        rows += r
        skipped += s
    if not rows and not skipped:
        return _unavailable("no_ac_branches", "The network has no lines or transformers.", solve=solve)
    if not rows:
        return _unavailable("no_rankable_branches",
                            "No line or transformer has both a rating and flow data in this window.",
                            solve=solve, skipped_total=len(skipped),
                            skipped=skipped[:MAX_LISTED_NAMES])

    rows.sort(key=lambda r: (-r["_rank"], r["name"]))
    token = _evidence_token(project_id, source, start, end, solve, rows)
    for r in rows:
        r.pop("_rank")
    ranked_total = len(rows)
    bases = sorted({r["basis"] for r in rows})
    window_weights = weights.reindex(win_index).fillna(0.0)

    if source == "lopf":
        missing.append("AC apparent power, reactive flows and voltage not assessed (LOPF active-power results).")
    else:
        missing.append("Bus voltage limits not assessed.")
        if any(r["basis"] == "active_power_proxy" for r in rows):
            missing.append("Branches without reactive flows use the active-power proxy.")
    if any(r.get("terminals") == "bus0_only" for r in rows):
        missing.append("Some branches have no bus1 flow (p1); only bus0 was used.")
    if skipped:
        missing.append(f"{len(skipped)} branch(es) not ranked; see skipped.")

    out = {
        "status": "ok",
        "scope": "current",
        "project_id": project_id,
        "source": source,
        "window": {"from": start, "to": end, "snapshots": len(win_index),
                   "of": len(n.snapshots), "start": str(win_index[0]), "end": str(win_index[-1]),
                   "weighted_hours": _r(window_weights.sum(), 2)},
        "freshness": "fresh",
        "solve": solve,
        "claims": {"full_ac_thermal_compliance": False, "reason": LIMITS_CLAIM},
        "bases_present": bases,
        "ranking_convention": RANKING_CONVENTION,
        "units": UNITS,
        "counts": {"ranked": ranked_total,
                   "over_limit": sum(1 for r in rows if r["h_over_limit"]),
                   "at_limit": sum(1 for r in rows if r["h_at_limit"]),
                   "skipped": len(skipped)},
        "missing_evidence": [m[:160] for m in missing[:5]],
        "skipped": skipped[:MAX_LISTED_NAMES],
        "ui_reference": {"results_tab": "loadflow", "source": source,
                         "window_from": start, "window_to": end},
        "evidence_token": token,
        "scope_note": "Lines and transformers only. Drill down with get_asset_results.",
        "top": rows[:limit],
    }
    if source == "ac_pf":
        out["ac_pf"] = {**(ac_info or {}), "unconverged_snapshots_excluded": excluded}
    # Shed the weakest-ranked rows, then the skipped list, until the whole
    # payload (claims and missing evidence included) fits the per-result cap.
    while len(json.dumps(out)) > MAX_PAYLOAD_CHARS and len(out["top"]) > 1:
        out["top"].pop()
    while len(json.dumps(out)) > MAX_PAYLOAD_CHARS and out["skipped"]:
        out["skipped"].pop()
    out["top_shown"] = len(out["top"])
    out["top_truncated"] = ranked_total > len(out["top"])
    out["skipped_truncated"] = len(skipped) > len(out["skipped"])
    return out
