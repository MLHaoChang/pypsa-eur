"""
The copilot's read of the Energy flow tab (LC-04, hand-off from LC-03).

``get_energy_flows`` returns the CURRENT solve's Source → Via → Destination
MWh exactly as ``services/results/energy_flows.py`` computes them for the tab,
trimmed to a bounded payload: the largest rows, the conservation check, and
what was not delivered. No number is computed here.

Unserved energy (``unserved_product_mwh`` — a process shortfall — and
``unserved_mwh`` — load shedding) is reported under ``not_delivered`` as demand
that was NOT delivered. It is never a row and never supply. An attribution the
module cannot prove to conserve energy is ``unavailable``, with no rows.
"""
from __future__ import annotations

import json
from typing import Any

MAX_CHARS = 3800
DEFAULT_LIMIT = 12
MAX_LIMIT = 40

NOT_DELIVERED = ("Demand that was NOT delivered (a shortfall or shed load), per load in MWh. It is "
                 "missing energy, never supply, and is not in rows.")


def _inputs_changed() -> bool | None:
    from services import workflow_tools
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    solved = ctx.network.meta.get("assistant_solve_input_fingerprint") if hasattr(ctx.network, "meta") else None
    if not solved:
        return None
    return solved != workflow_tools._network_input_fingerprint(ctx)


def shape(payload: dict[str, Any], *, limit: int, inputs_changed: bool | None) -> dict[str, Any]:
    """The module's payload, bounded; every value is the module's own."""
    window = payload.get("window") or {}
    base = {"unit": payload.get("unit", "MWh"), "period": payload.get("period"), "periods": payload.get("periods"),
            "window": window, "inputs_changed_since_solve": inputs_changed}
    if payload.get("status") != "ok":
        return {"status": "unavailable", "unavailable_kind": "not_established", **base,
                "conservation": payload.get("conservation"), "flags": payload.get("flags", []),
                "message": "The energy flows could not be proved to conserve energy; no flows are claimed."}
    rows = list(payload.get("rows") or [])
    out = {"status": "ok", **base,
           "rows": rows[:limit], "rows_total": len(rows),
           "rows_mwh_total": sum(row["mwh"] for row in rows),
           "conservation": {k: v for k, v in (payload.get("conservation") or {}).items() if k != "checks"},
           "not_delivered": {"product_mwh": payload.get("unserved_product_mwh") or {},
                             "demand_mwh": payload.get("unserved_mwh"), "meaning": NOT_DELIVERED},
           "flags": payload.get("flags", []), "rule": payload.get("rule"),
           "ui_reference": {"results_tab": "flows", "period": payload.get("period"),
                            "window_from": window.get("from"), "window_to": window.get("to")}}
    while len(json.dumps(out, default=str)) > MAX_CHARS and out["rows"]:
        out["rows"].pop()
    out["rows_shown"] = len(out["rows"])
    return out


def get_energy_flows(period: int | None = None, window_from: int | None = None, window_to: int | None = None,
                     limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    from fastapi import Response

    from routers.results import _energy_flows_payload

    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        limit = DEFAULT_LIMIT
    payload = _energy_flows_payload(period, window_from, window_to)
    if isinstance(payload, Response):
        return {"status": "unavailable", "unavailable_kind": "not_solved",
                "message": "There are no solved results yet; run the simulation first."}
    return shape(payload, limit=limit, inputs_changed=_inputs_changed())
