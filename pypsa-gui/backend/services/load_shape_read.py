"""
The copilot's read of the Load shape tab (site commercial UX A2, LC-04 area).

``get_load_shape`` returns the CURRENT solve's load shape exactly as
``services/results/load_shape.py`` computes it for the tab, one view at a time
and trimmed to a bounded payload, so the model can ask for a week or a period
instead of reading a whole-horizon payload. It mirrors ``get_energy_flows``
(``services/energy_flows_read.py``): the same read tier, the same refusals
(not solved, 409 while a solve runs, not established), the same ``period`` and
``window_from`` / ``window_to`` arguments, plus ``week``, ``view`` and
``series``. No number is computed here: every value is the module's own,
selected and rounded.

A group the network does not model, or the run cannot establish, keeps its
``not_modelled`` / ``unavailable`` state and reason all the way to the model;
it is never a missing key that reads as zero.
"""
from __future__ import annotations

import json
from typing import Any

MAX_CHARS = 3800
VIEWS = ("summary", "heatmap", "week", "monthly", "annual")
SERIES = ("grid", "export", "solar", "wind", "genset", "ev", "flexible", "battery_soc", "unserved")

CLAIM_LIMITS = ("MW per snapshot; MWh = MW × generators weighting, per modelled year. A group that is "
                "not_modelled or unavailable is NOT zero: say what its reason says. Monthly needs a full "
                "calendar year of snapshots; representative-week runs have none.")


def _inputs_changed() -> bool | None:
    from services import workflow_tools
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    solved = ctx.network.meta.get("assistant_solve_input_fingerprint") if hasattr(ctx.network, "meta") else None
    if not solved:
        return None
    return solved != workflow_tools._network_input_fingerprint(ctx)


def _r(v: Any, nd: int = 2) -> Any:
    return round(v, nd) if isinstance(v, float) else v


def _peak(heat: dict[str, Any]) -> dict[str, Any]:
    """The state of a heatmap and, if drawn, its largest cell (a selection)."""
    if heat.get("status") != "ok":
        return {k: heat[k] for k in ("status", "reason") if k in heat}
    best = None
    for d, row in enumerate(heat["mean_mw"]):
        for h, v in enumerate(row):
            if v is not None and (best is None or v > best["mean_mw"]):
                best = {"weekday": d, "hour": h, "mean_mw": v}
    return {"status": "ok", "peak_cell": best and {**best, "mean_mw": _r(best["mean_mw"])}}


def _states(series_status: dict[str, Any]) -> dict[str, Any]:
    return {k: ({"status": v["status"]} if v["status"] == "ok" else {"status": v["status"], "reason": v.get("reason")})
            for k, v in (series_status or {}).items()}


def _annual(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _r(v, 1) for k, v in row.items()} for row in rows]


def shape(payload: dict[str, Any], *, view: str, series: str, inputs_changed: bool | None) -> dict[str, Any]:
    """The module's payload, one view of it, bounded; every value is the module's own."""
    window = payload.get("window") or {}
    base = {"view": view, "unit": payload.get("unit", "MW"), "period": payload.get("period"),
            "periods": payload.get("periods"), "window": window, "inputs_changed_since_solve": inputs_changed}
    if payload.get("status") != "ok":
        return {"status": "unavailable", "unavailable_kind": "not_established", **base,
                "reason": payload.get("reason"),
                "message": "The load shape could not be established for this run; no values are claimed."}
    out: dict[str, Any] = {"status": "ok", **base, "series_status": _states(payload.get("series_status") or {}),
                           "claim_limits": CLAIM_LIMITS,
                           "ui_reference": {"results_tab": "loadshape", "period": payload.get("period")}}
    week = payload.get("week") or {}
    heat = payload.get("heatmaps") or {}
    monthly = payload.get("monthly") or {}
    if view == "summary":
        out["heatmaps"] = {k: _peak(v) for k, v in heat.items()}
        if week.get("status") == "ok":
            out["week"] = {"selected": week["selected"], "weeks": [w["key"] for w in week["weeks"]][:12],
                           "weeks_total": len(week["weeks"]), "flagged_intervals": len(week["flagged"]),
                           "flag_basis": week["flag_basis"]}
        else:
            out["week"] = {"status": "unavailable", "reason": week.get("reason")}
        out["monthly"] = ({"status": "ok", "months": len(monthly["months"]),
                           "average_daily_generation_mwh": _r(monthly["average_daily_generation_mwh"], 1)}
                          if monthly.get("status") == "ok"
                          else {"status": "unavailable", "reason": monthly.get("reason")})
        out["annual"] = _annual(payload.get("annual") or [])
    elif view == "heatmap":
        out["heatmaps"] = {k: ({"status": "ok", "mean_mw": [[_r(v, 1) for v in row] for row in h["mean_mw"]]}
                               if h.get("status") == "ok" else {kk: h[kk] for kk in ("status", "reason") if kk in h})
                           for k, h in heat.items()}
        out["meaning"] = "mean_mw[weekday 0=Mon][hour]; null = no snapshot in that cell, not zero."
    elif view == "week":
        if week.get("status") != "ok":
            out["week"] = {"status": "unavailable", "reason": week.get("reason")}
        else:
            chosen = week["series"].get(series, {"status": "unavailable", "reason": "unknown series"})
            out["week"] = {"selected": week["selected"], "weeks": [w["key"] for w in week["weeks"]][:12],
                           "start": week["index"][0] if week["index"] else None,
                           "end": week["index"][-1] if week["index"] else None,
                           "flagged": week["flagged"][:48], "flagged_total": len(week["flagged"]),
                           "flag_basis": week["flag_basis"], "series_name": series,
                           "series": ({"status": "ok", "mw": [_r(v) for v in chosen["mw"]]}
                                      if chosen["status"] == "ok"
                                      else {kk: chosen[kk] for kk in ("status", "reason") if kk in chosen})}
    elif view == "monthly":
        out["monthly"] = ({"status": "ok", "months": [{k: _r(v, 1) for k, v in m.items()} for m in monthly["months"]],
                           "average_daily_generation_mwh": _r(monthly["average_daily_generation_mwh"], 1)}
                          if monthly.get("status") == "ok"
                          else {"status": "unavailable", "reason": monthly.get("reason")})
    else:
        out["annual"] = _annual(payload.get("annual") or [])
    # Bounded: shorten the longest list until it fits (the week's series first).
    while len(json.dumps(out, default=str)) > MAX_CHARS:
        wk = out.get("week") or {}
        if isinstance(wk.get("series"), dict) and wk["series"].get("mw"):
            wk["series"]["mw"] = wk["series"]["mw"][: max(1, len(wk["series"]["mw"]) // 2)]
            wk["series"]["truncated"] = True
        elif isinstance(wk.get("flagged"), list) and wk["flagged"]:
            wk["flagged"] = wk["flagged"][: len(wk["flagged"]) // 2]
        elif isinstance(out.get("monthly"), dict) and out["monthly"].get("months"):
            out["monthly"]["months"] = out["monthly"]["months"][: len(out["monthly"]["months"]) // 2]
        else:
            break
    return out


def get_load_shape(view: str = "summary", period: int | None = None, window_from: int | None = None,
                   window_to: int | None = None, week: int | None = None, series: str = "grid") -> dict[str, Any]:
    from fastapi import Response

    from routers.results import _load_shape_payload

    if view not in VIEWS:
        view = "summary"
    if series not in SERIES:
        series = "grid"
    payload = _load_shape_payload(period, window_from, window_to, week)
    if isinstance(payload, Response):
        return {"status": "unavailable", "unavailable_kind": "not_solved",
                "message": "There are no solved results yet; run the simulation first."}
    return shape(payload, view=view, series=series, inputs_changed=_inputs_changed())
