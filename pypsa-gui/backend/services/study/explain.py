"""
The sizing explanation of one asset, on an EXPLICIT network (guided
investment study MVP-1, phase S6 "explain").

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S6)

Lifted out of ``services/chat_tools.py`` (``_bus_price_signals``,
``_congestion_at``, ``_co2_signals``, ``_reading_notes`` and the body of
``explain_investment``) so the guided flow can explain an option's battery
and PV without the copilot. ONE implementation: ``chat_tools.
explain_investment`` now delegates here with the active network and its own
result and KPI readers, and its output is pinned byte-identical to a
recording made before the lift (``tests/test_study_explain.py``).

The sizing classification is ``services/results/sizing.py::classify_sizing``
(no second classifier). What the chat tool reads from the ACTIVE project —
the per-asset KPIs and the ``emissions`` / ``line_duals`` results — is
passed in as two callables, so a caller with a network read from disk (a
study fork) supplies readers over that network instead
(:func:`network_readers`).

Refusals are typed (:class:`ExplainError`, with the HTTP status and the
message the chat tool has always answered), never an ``HTTPException``:
this is a service.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from services.results.economics_caveats import ZERO_PROFIT_BY_CONSTRUCTION
from services.results.sizing import classify_sizing

__all__ = [
    "ExplainError", "INVESTMENT_BUS_COLS", "bus_price_signals", "co2_signals",
    "congestion_at", "explain_asset", "network_readers", "reading_notes",
]

# Which bus columns carry an asset's electrical location, per class.
INVESTMENT_BUS_COLS: dict[str, tuple[str, ...]] = {
    "Generator": ("bus",),
    "StorageUnit": ("bus",),
    "Store": ("bus",),
    "Link": ("bus0", "bus1"),
    "Line": ("bus0", "bus1"),
    "Transformer": ("bus0", "bus1"),
}

ResultsFn = Callable[[str], Any]
KpisFn = Callable[[str, str], dict]


class ExplainError(LookupError):
    """The asset cannot be explained. ``status`` is the HTTP answer."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def bus_price_signals(n: Any, buses: list[str]) -> dict:
    """Mean / min / max / load-weighted marginal price at each of the asset's buses."""
    from services.asset_results import service as svc

    wanted = ["bus_price_mean", "bus_price_min", "bus_price_max",
              "bus_load_weighted_price"]
    out: dict[str, Any] = {}
    for bus in buses:
        if bus not in n.buses.index:
            continue
        try:
            resp = svc.build_response(
                n, "Bus", bus, category="prices", metric_ids=wanted,
                source="lopf", from_iso=None, to_iso=None, period=None,
                mode="chronological",
            )
        except Exception:  # noqa: BLE001 — a missing signal is not a failure
            continue
        scalars = {k: v for k, v in resp.get("scalars", {}).items() if k in wanted}
        if scalars:
            out[bus] = scalars
    return out


def congestion_at(n: Any, buses: list[str], results: ResultsFn) -> dict:
    """
    The binding LINES touching the asset's buses, WITH why the list may be
    empty.

    An empty list has four very different causes — no lines there at all, no
    duals captured on this solve, lines that never bind, or an asset that
    connects through links and transformers, which `compute_line_duals` does
    not cover (it walks `n.lines`). Returning the bare list makes all four read
    as "uncongested", the one reading that can be flatly wrong, so the reason
    travels with the data instead of being inferred from its absence.
    """
    at_bus = {
        str(name) for name, line in n.lines.iterrows()
        if str(line.get("bus0")) in buses or str(line.get("bus1")) in buses
    }
    if not at_bus:
        # Checked FIRST: on a network with no lines, compute_line_duals says
        # "No LP duals captured — re-run the solve", which sends the agent
        # (and the user) after a solve that would change nothing.
        return {"lines": [], "note": (
            "no line connects to this asset's buses, so line congestion does "
            "not apply here — links and transformers are out of scope either "
            "way"
        )}

    payload = results("line_duals")
    if not isinstance(payload, dict):
        return {"lines": [], "note": "line duals unavailable"}
    if payload.get("status") == "no_data":
        return {"lines": [], "note": payload.get("message")}
    if payload.get("note"):
        # compute_line_duals' own sentence — "No LP duals captured…". Empty
        # here means UNKNOWN, not uncongested.
        return {"lines": [], "note": str(payload["note"])}

    lines = [
        {k: r.get(k) for k in ("name", "binding_hours",
                               "max_mu_eur_per_MWh", "congestion_rent_eur")}
        for r in payload.get("rows", [])
        if r.get("name") in at_bus and (r.get("binding_hours") or 0) > 0
    ]
    note = None if lines else (
        "no line at this asset's buses binds in any hour — but this covers "
        "n.lines only, so a link- or transformer-connected corridor is not "
        "evidence either way"
    )
    return {"lines": lines, "note": note}


def co2_signals(results: ResultsFn) -> list[dict]:
    """Active CO2 caps with their shadow prices — the system-wide clean premium."""
    payload = results("emissions")
    if not isinstance(payload, dict) or payload.get("status") == "no_data":
        return []
    return [
        {k: cap.get(k) for k in ("name", "scope", "investment_period",
                                 "binding", "shadow_price_eur_per_tCO2",
                                 "slack_tCO2")}
        for cap in payload.get("caps", []) if cap.get("active")
    ]


def reading_notes(sizing: dict, co2: list[dict], buses: list[str]) -> list[str]:
    """The framing that keeps the narration honest. Order is deliberate."""
    notes = [
        "This payload is EVIDENCE, not a verdict. Narrate only numbers that "
        "appear in it, and name the field you used.",
    ]
    binding = sizing["binding_constraint"]
    if binding == "interior":
        notes.append(ZERO_PROFIT_BY_CONSTRUCTION)
    elif binding == "at_upper_bound":
        notes.append(
            "The size is a bound, not an optimum: do not narrate capture "
            "price or profitability as the reason it is this big."
        )
    elif binding == "not_built":
        notes.append(
            "Nothing was built, so revenue / capture-price KPIs below are "
            "zero or absent BY CONSTRUCTION. The question to answer is what "
            "it lost to: compare its capital_cost and marginal_cost against "
            "the bus price and against what the LP built instead."
        )
    elif binding == "not_extendable":
        notes.append(
            "Every capacity number below is an input the user typed. Nothing "
            "here explains a build decision, because none was made."
        )
    binding_caps = [c for c in co2 if c.get("binding")]
    if binding_caps:
        notes.append(
            "A CO2 cap binds. Its shadow price is part of this asset's "
            "competitiveness and vanishes if the cap is relaxed — say so "
            "rather than presenting the economics as cap-independent."
        )
    if len(buses) > 1:
        notes.append(
            "This asset spans more than one bus; the price signals are "
            "reported per bus and can disagree across a congested corridor."
        )
    notes.append(
        "Read system_signals.congestion.note before concluding anything from "
        "an empty `lines` list: it says whether nothing binds, the duals were "
        "never captured, or the corridor is simply out of scope."
    )
    return notes


def explain_asset(n: Any, component_class: str, name: str, *,
                  results: ResultsFn, asset_kpis: KpisFn) -> dict:
    """
    Assemble the evidence behind one sizing decision on ``n``: what the LP
    built, WHICH CONSTRAINT stopped it there, what the asset earned, and the
    system signals it was priced against.

    ``results(kind)`` answers the ``emissions`` and ``line_duals`` results of
    ``n``; ``asset_kpis(component_class, name)`` its per-asset summary KPIs
    (``headline`` and ``unavailable``). Raises :class:`ExplainError` (400 for
    a class the optimiser does not size, 404 for an unknown asset).
    """
    from services.asset_results.compute import attr_for, nom_col_for
    from services.dispatch_status import dispatch_status_detail

    nom_col = nom_col_for(component_class)
    if nom_col is None:
        raise ExplainError(
            400,
            f"{component_class!r} carries no capacity the optimiser sizes. "
            f"Sizeable classes: "
            f"{', '.join(sorted(INVESTMENT_BUS_COLS))}",
        )

    # `attr_for`, not `_GENERIC_CRUD_ATTRS`: the same class → DataFrame map
    # `get_asset_results` uses, so the row this reads and the KPIs it fuses
    # below can never come from two different tables.
    df = getattr(n, attr_for(component_class))
    if name not in df.index:
        raise ExplainError(404, f"No {component_class} named {name!r}")
    row = df.loc[name].to_dict()

    dispatch = dispatch_status_detail(n)
    solved = dispatch.get("state") == "fresh"

    buses = [str(row.get(col)) for col in INVESTMENT_BUS_COLS[component_class]
             if row.get(col) is not None]
    sizing = classify_sizing(row, nom_col, solved=solved)
    co2 = co2_signals(results) if solved else []

    # The per-asset KPIs, taken from the registry rather than recomputed, so
    # this can never disagree with the Asset Detail tab the user is looking at.
    kpis = asset_kpis(component_class, name)

    return {
        "asset": {
            "component_class": component_class,
            "name": name,
            "carrier": row.get("carrier"),
            "buses": buses,
        },
        "dispatch_state": dispatch,
        "sizing": sizing,
        "asset_kpis": kpis.get("headline", []),
        "unavailable_kpis": kpis.get("unavailable", []),
        "system_signals": {
            "bus_prices": bus_price_signals(n, buses) if solved else {},
            "co2_caps": co2,
            "congestion": congestion_at(n, buses, results) if solved else {
                "lines": [], "note": "no fresh dispatch — nothing to assess"},
        },
        "reading_notes": reading_notes(sizing, co2, buses),
    }


def network_readers(n: Any) -> tuple[ResultsFn, KpisFn]:
    """
    ``(results, asset_kpis)`` over an explicit network — a study fork read
    from disk — computed from its live frames (the ``eh_report`` precedent),
    never from the active project's result state. A result the engine cannot
    give answers ``no_data``, as the chat tool's reader does.
    """
    from services.adequacy.eh_report import _live_result_df

    def results(kind: str) -> Any:
        try:
            if kind == "emissions":
                from services.results.emissions import compute_emissions

                out = compute_emissions(n, "lopf", result_df=_live_result_df)
            elif kind == "line_duals":
                from services.results.line_duals import compute_line_duals

                out = compute_line_duals(n, result_df=_live_result_df)
            else:
                out = None
        except Exception:  # noqa: BLE001 — a missing signal is not a failure
            out = None
        if not isinstance(out, dict):
            return {"status": "no_data", "kind": kind,
                    "message": "not computed for this network"}
        return out

    def asset_kpis(component_class: str, name: str) -> dict:
        from services.asset_results import service as svc
        from services.asset_results.registry import metrics_for

        requested = [m.id for m in metrics_for(component_class, "summary")]
        resp = svc.build_response(
            n, component_class, name, category="summary", metric_ids=requested,
            source="lopf", from_iso=None, to_iso=None, period=None,
            mode="chronological",
        )
        headline = [
            {"id": h["id"], "label": h["label"], "unit": h.get("unit", ""),
             "category": h["category"], "status": h["status"],
             **({"value": h["value"]} if "value" in h else {}),
             **({"reason": h["reason"]} if h.get("reason") else {})}
            for h in resp.get("headline") or []
        ]
        unavailable = [
            {"id": m["id"], "label": m["label"], "status": m["status"],
             "reason": m.get("reason", "")}
            for m in resp["metrics"] if m["status"] != "ok"
        ]
        return {"headline": headline, "unavailable": unavailable}

    return results, asset_kpis
