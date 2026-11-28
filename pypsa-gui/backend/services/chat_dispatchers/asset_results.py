"""
Asset results: get_asset_results, ui_open_asset_detail and export_asset_results: dispatchers.

Moved out of `services/chat_tools.py` (RETRO-FRAG-1), function bodies
unchanged. Schemas and routes: `harness/catalogue_tools/asset_results.py`.

The chat_tools helpers stay in chat_tools and are reached through the
proxies below, at call time: chat_tools imports this module part-way through
its own import, and tests that patch `chat_tools.<helper>` still apply.
The helpers and tables only this family uses moved here with it; chat_tools
re-exports them, which is a binding, so a later monkeypatch of
`chat_tools.<moved name>` does not reach this module: patch it here.

"""

from __future__ import annotations

import math
from typing import Any

from fastapi import HTTPException

from services.pypsa_service import PyPSAService


def _ct():
    from services import chat_tools

    return chat_tools


def _save_agent_export(*args, **kwargs):
    return _ct()._save_agent_export(*args, **kwargs)


def _series_stats(index: list[str], values: list, *, points: int = 48) -> dict:
    """
    Compress a series to something worth putting in a context window.

    An hourly year is 8 760 numbers per metric; ten metrics is ~87 000. The
    agent can answer almost every real question — peak, mean, total, when it
    peaks, how often it sits at zero — from these ~12 fields plus a coarse
    sparkline, and it is told to reach for the export tool when it cannot.

    `zero_count` is an unweighted count of SNAPSHOTS, deliberately not named
    `zero_hours` — the response's own `scalars["zero_hours"]` (from the
    `zero_hours` metric in the registry) is the snapshot-WEIGHTED hour count.
    Two identically-named fields with different values in the same payload
    would be indistinguishable to the agent; different names make the
    difference legible instead of silent.
    """
    finite = [
        (i, float(v))
        for i, v in enumerate(values)
        if v is not None and math.isfinite(float(v))
    ]
    if not finite:
        return {
            "min": None,
            "max": None,
            "mean": None,
            "sum": None,
            "p50": None,
            "p95": None,
            "peak_at": None,
            "zero_count": 0,
            "sparkline": [],
        }
    vals = [v for _, v in finite]
    ordered = sorted(vals)
    peak_i = max(finite, key=lambda t: t[1])[0]

    def pct(q: float) -> float:
        k = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return ordered[k]

    step = max(1, len(vals) // points)
    return {
        "min": ordered[0],
        "max": ordered[-1],
        "mean": sum(vals) / len(vals),
        "sum": sum(vals),
        "p50": pct(0.5),
        "p95": pct(0.95),
        "peak_at": index[peak_i] if peak_i < len(index) else None,
        "zero_count": sum(1 for v in vals if abs(v) < 1e-9),
        "sparkline": [round(v, 4) for v in vals[::step]][:points],
    }


def get_asset_results(
    component_class: str,
    name: str,
    *,
    category: str = "summary",
    metrics: list | None = None,
    source: str = "lopf",
    from_iso: str | None = None,
    to_iso: str | None = None,
    period: str | None = None,
    resolution: str = "stats",
    max_rows: int = 2000,
) -> dict:
    """Per-asset results for the agent. Statistics by default; raw on request."""
    from services.asset_results import service as svc
    from services.asset_results.registry import metrics_for

    n = PyPSAService.get_network()
    df = getattr(n, svc.C.attr_for(component_class))
    if name not in df.index:
        raise ValueError(f"No {component_class} named '{name}'")

    # No explicit metric list means "everything in this category" — the agent
    # asks a question, it does not know the registry's metric ids up front.
    requested = [str(m) for m in (metrics or [])]
    if not requested:
        requested = [m.id for m in metrics_for(component_class, category)]

    resp = svc.build_response(
        n,
        component_class,
        name,
        category=category,
        metric_ids=requested,
        source=source,
        from_iso=from_iso,
        to_iso=to_iso,
        period=period,
        mode="chronological",
    )

    unavailable = [
        {
            "id": m["id"],
            "label": m["label"],
            "status": m["status"],
            "reason": m.get("reason", ""),
        }
        for m in resp["metrics"]
        if m["status"] != "ok"
    ]
    out: dict = {
        "asset": resp["asset"],
        "category": category,
        "categories": [
            {"id": c["id"], "status": c["status"], "reason": c.get("reason", "")}
            for c in resp["categories"]
        ],
        "scalars": resp["scalars"],
        "unavailable": unavailable,
        "n_snapshots": len(resp["index"]),
    }
    # The default category is `summary`, whose own metrics are just identity
    # and parameters. The headline KPIs are what makes "summarise Gas 1"
    # answerable in one call instead of seven — carry them through, each
    # tagged with the tab it came from so the agent can cite a source.
    if resp.get("headline"):
        out["headline"] = [
            {
                "id": h["id"],
                "label": h["label"],
                "unit": h.get("unit", ""),
                "category": h["category"],
                "status": h["status"],
                **({"value": h["value"]} if "value" in h else {}),
                **({"reason": h["reason"]} if h.get("reason") else {}),
            }
            for h in resp["headline"]
        ]
    if resolution == "raw":
        out["resolution"] = "raw"
        out["index"] = resp["index"][:max_rows]
        out["series"] = {k: v[:max_rows] for k, v in resp["series"].items()}
        out["truncated"] = len(resp["index"]) > max_rows
        out["n_total"] = len(resp["index"])
        out["note"] = (
            f"Truncated to the first {max_rows} rows. Call export_asset_results for the "
            "complete set as a workbook."
            if out["truncated"]
            else "Complete — no truncation."
        )
    else:
        out["resolution"] = "stats"
        out["series_stats"] = {
            k: _series_stats(resp["index"], v) for k, v in resp["series"].items()
        }
    return out


def ui_open_asset_detail(
    component_class: str,
    name: str,
    *,
    category: str | None = None,
    metrics: list | None = None,
    mode: str | None = None,
    chart: bool | None = None,
) -> dict:
    """Open the Asset Detail tab pre-configured. No backend mutation."""
    from services.asset_results import compute as asset_compute
    from services.asset_results.registry import metric_for

    try:
        df = getattr(
            PyPSAService.get_network(), asset_compute.attr_for(component_class)
        )
    except KeyError:
        df = None
    if df is None or name not in df.index:
        raise HTTPException(
            404,
            detail={
                "error_kind": "component_not_found",
                "component_class": component_class,
                "name": name,
                "message": f"{component_class} '{name}' is not in the network.",
            },
        )
    for m in metrics or []:
        if metric_for(component_class, str(m)) is None:
            raise HTTPException(
                400,
                detail={
                    "error_kind": "invalid_navigation",
                    "field": "metrics",
                    "message": f"{component_class} has no metric '{m}'.",
                },
            )
    from services.asset_results.registry import CATEGORY_IDS
    from services.asset_results.service import VIEW_MODES

    if category and category not in CATEGORY_IDS:
        raise HTTPException(
            400,
            detail={
                "error_kind": "invalid_navigation",
                "field": "category",
                "message": f"Unknown category '{category}'; use one of {', '.join(CATEGORY_IDS)}.",
            },
        )
    if mode and mode not in VIEW_MODES:
        raise HTTPException(
            400,
            detail={
                "error_kind": "invalid_navigation",
                "field": "mode",
                "message": f"Unknown mode '{mode}'; use one of {', '.join(VIEW_MODES)}.",
            },
        )
    event: dict[str, Any] = {
        "_ui_event": True,
        "kind": "open_asset_detail",
        "component_class": component_class,
        "name": name,
    }
    if category:
        event["category"] = category
    if metrics:
        event["metrics"] = [str(m) for m in metrics]
    if mode:
        event["mode"] = mode
    if chart is not None:
        event["chart"] = bool(chart)
    return event


def export_asset_results(
    component_class: str,
    name: str,
    *,
    scope: str = "view",
    category: str = "summary",
    metrics: list | None = None,
    filename: str | None = None,
    source: str = "lopf",
    mode: str = "chronological",
) -> dict:
    """Write one asset's results to an xlsx workbook in the project's uploads/."""
    from services.asset_results import export as xls
    from services.asset_results import service as svc

    n = PyPSAService.get_network()
    df = getattr(n, svc.C.attr_for(component_class))
    if name not in df.index:
        raise ValueError(f"No {component_class} named '{name}'")

    blob = xls.build_workbook(
        n,
        component_class,
        name,
        scope=scope,
        category=category,
        metric_ids=[str(m) for m in (metrics or [])],
        source=source,
        from_iso=None,
        to_iso=None,
        period=None,
        mode=mode,
        project=PyPSAService.get_loaded_project(),
    )
    # Same 25 MB pre-check export_to_excel applies before handing bytes to
    # the shared writer — see the note on `_save_agent_export` below for why
    # this isn't inside that helper itself.
    if len(blob) > 25 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail={
                "error_kind": "file_too_large",
                "message": (
                    f"serialised workbook exceeds the 25 MB upload cap "
                    f"({len(blob) // (1024 * 1024)} MB)"
                ),
            },
        )
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    fname = filename or f"{safe_name}_{category}.xlsx"
    # No `_write_agent_export` helper exists anywhere in this codebase — only
    # a stale docstring reference in filename_service.py. The real shared
    # writer (already used by export_to_excel and four sibling export tools)
    # is `_save_agent_export(data, filename, mime) -> dict`, which returns
    # `size`, not `bytes`. Reuse it as-is — restructuring a helper five
    # working call sites depend on is out of scope here — and alias the
    # field this tool's own schema promises.
    meta = _save_agent_export(
        blob,
        fname,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    meta["bytes"] = meta["size"]
    return meta


DISPATCHERS = {
    "get_asset_results": get_asset_results,
    "ui_open_asset_detail": ui_open_asset_detail,
    "export_asset_results": export_asset_results,
}
