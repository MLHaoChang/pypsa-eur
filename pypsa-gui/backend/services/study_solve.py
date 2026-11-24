# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""The solve a Guided study ran (template-e2e 30, owner decision TE2E-30-1).

A study solves a private copy of the network, so the live network carries no
dispatch after it. The study keeps that first (reference-design) solve here,
in the context's `solver_state`, and the results workbook, the dispatch status
and the run state read it when the live network has no dispatch of its own.
The live network's own solve (Run LOPF) always wins: `fallback` answers None
whenever the live network holds dispatch, fresh or stale.

`PREFERRED` is the one place the order sits (study first or live first).
"""
from __future__ import annotations

import math
from typing import Any

KEY = "eh_study_solve"
PREFERRED = "live"  # "live": the study's solve only when the live network has none


def keep(store: dict | None, network: Any) -> None:
    """Remember the study's solved network (a copy, so later stages cannot change it)."""
    if store is None:
        return
    from services.adequacy.redundancy import _detach_solver_model

    _detach_solver_model(network)
    store[KEY] = {"network": network.copy(), "objective": _objective(network)}


def _objective(network: Any) -> float | None:
    try:
        v = float(getattr(network, "objective", None))
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def fallback(ctx: Any = None) -> dict | None:
    """`{"network", "objective"}` of the finished, unedited study's solve when the
    live network has no dispatch of its own; None otherwise."""
    from services import study_state
    from services.dispatch_status import dispatch_status
    from services.pypsa_service import PyPSAService

    ctx = ctx or PyPSAService.get_active_context()
    state = getattr(ctx, "solver_state", None) or {}
    held = state.get(KEY)
    record = state.get("eh_study")
    if not isinstance(held, dict) or not isinstance(record, dict) or record.get("status") != "done":
        return None
    if study_state.edited_since(record) is not False:
        return None
    try:
        if PREFERRED == "live" and dispatch_status(ctx.network) != "none":
            return None
        if dispatch_status(held["network"], transient={}) != "fresh":
            return None
    except Exception:  # noqa: BLE001 — no readable dispatch: no fallback
        return None
    return held
