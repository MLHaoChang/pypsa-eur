"""
AS-10 — the preference comparison table: one sizing loop per design intent, one row each.

Pure orchestration over `services.adequacy.sizing_loop_runner.run_sizing` (injected, so tests need no LP).
Rows carry the DC 05 columns: cost, MW/MWh/Mvar per asset, transformer and STATCOM sizes, a verdict per
check and the loop status. Only a converged loop with no failing or unresolved check is a pass.
"""
from __future__ import annotations

from typing import Callable, Sequence

from models.design_intent import DesignIntentSpec
from services.design_intent import backup_energy_mwh, grid_code_warnings

#: Loop statuses that are never a pass, whatever the checks of their last round say.
_NOT_PASS = ("not_converged", "unresolved", "failed", "aborted")


def _unit(key: str) -> str:
    if key in ("q_ss", "q_dyn") or key.startswith("s_x:"):
        return "Mvar"
    if key == "e_sm" or key.startswith("store:"):
        return "MWh"
    return "MW"


def _check_rows(final: dict) -> list[dict]:
    rows = []
    for c in final.get("checks") or []:
        if c.get("skipped"):
            state = "not_run"
        elif c.get("ok"):
            state = "ok"
        else:
            state = "unresolved" if c.get("lever") == "none" else "fail"
        rows.append({"name": c["name"], "state": state, "value": c.get("value"), "limit": c.get("limit"),
                     "note": c.get("note", "")})
    return rows


def _outcome(result: dict) -> str:
    status = result.get("status")
    if status in _NOT_PASS:
        return status
    if result.get("unresolved"):
        return "unresolved"
    if result.get("failing"):
        return "fail"
    return "pass" if status == "converged" else "failed"


def build_row(spec: DesignIntentSpec, result: dict, critical_mw: float, profile: dict) -> dict:
    final = result.get("final") or {}
    sizes = final.get("sizes") or {}
    assets = {k: {"value": float(v), "unit": _unit(k)} for k, v in sizes.items()}
    statcom = None
    if "q_ss" in sizes or "q_dyn" in sizes:
        statcom = float(sizes.get("q_ss", 0.0)) + float(sizes.get("q_dyn", 0.0))
    cost = final.get("total_cost_eur", final.get("cost_eur"))
    return {
        "intent": spec.intent,
        "status": result.get("status"),
        "outcome": _outcome(result),
        "verdict": result.get("verdict", ""),
        "cost_eur": None if cost is None else float(cost),
        "assets": assets,
        "transformer_mva": (result.get("library") or {}).get("transformer"),
        "statcom_mvar": statcom,
        "checks": _check_rows(final),
        "failing": list(result.get("failing") or []),
        "unresolved": list(result.get("unresolved") or []),
        "not_run": list(result.get("not_run") or []),
        "basis_note": result.get("basis_note", ""),
        "warnings": grid_code_warnings(spec, profile) + list(result.get("warnings") or []),
        "backup_autonomy_h": spec.backup_autonomy_h if spec.intent == "A" else None,
        "backup_energy_mwh": backup_energy_mwh(spec, critical_mw),
    }


def compare_intents(prepare: Callable, specs: Sequence[DesignIntentSpec], *, critical_mw: float, profile: dict,
                    runner: Callable) -> dict:
    """Run one sizing loop per spec. `prepare(spec)` returns `(network, config, inputs)` for that intent's
    scenario; `runner(network, config, inputs)` is `run_sizing`. A loop that raises is a failed row."""
    intents = [s.intent for s in specs]
    if len(specs) < 2 or len(set(intents)) != len(intents):
        raise ValueError("choose at least two different design intents")
    rows = []
    for spec in specs:
        network, config, inputs = prepare(spec)
        try:
            result = runner(network, config, inputs)
        except Exception as exc:                       # one intent's refusal must not hide the others
            result = {"status": "failed", "verdict": f"failed: {exc}", "final": None}
        rows.append(build_row(spec, result, critical_mw, profile))
    return {"rows": rows}
