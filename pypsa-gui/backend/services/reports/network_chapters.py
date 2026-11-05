"""Network-study chapters (LC-21 option A; study harness issue 07).

The chapters of a network study (model summary, headline results, capacity,
dispatch, prices and costs, emissions, load flow) built from the same native
reads the Results tabs and the run evidence keep: `retained_results` (LC-03)
captures each family as a bounded section, `project_summary` (LC-15) the
model. The read is `read_sources` (impure, the route's); the chapters are
`sections` (pure).

* A chapter whose read did not answer is `not_established` with the reason.
* Results that no longer describe the current inputs are reported as not
  established for every results chapter, never as the current results.
* Each payload carries at most `MAX_FACTS` numbers (AI-558: at most 12 facts
  per explanation), so the prose of one chapter can quote no more.
* The load flow chapter on an LOPF solve is an active-power proxy: its
  disclosure says so (`LOADFLOW_PROXY`).
"""
from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_FACTS = 12

MODEL_SUMMARY = "model_summary"
HEADLINE = "headline_results"
CAPACITY = "capacity"
DISPATCH = "dispatch"
PRICES = "prices_costs"
EMISSIONS = "emissions"
LOAD_FLOW = "load_flow"
SECTIONS = (MODEL_SUMMARY, HEADLINE, CAPACITY, DISPATCH, PRICES, EMISSIONS, LOAD_FLOW)

TITLES = {
    MODEL_SUMMARY: "Model summary",
    HEADLINE: "Headline results",
    CAPACITY: "Capacity",
    DISPATCH: "Dispatch",
    PRICES: "Prices and costs",
    EMISSIONS: "Emissions",
    LOAD_FLOW: "Load flow",
}

# The retained section each results chapter reads (`retained_results`).
_READS = {
    HEADLINE: ("results_summary", "results_emissions"),
    CAPACITY: ("results_summary",),
    DISPATCH: ("results_dispatch",),
    PRICES: ("results_prices", "results_summary"),
    EMISSIONS: ("results_emissions",),
    LOAD_FLOW: ("loadflow",),
}

NOT_CURRENT = ("the results do not describe the current inputs: the network changed since "
               "its last solve, so solve again before reporting them")
NO_READ = {
    MODEL_SUMMARY: "the model summary could not be read",
    HEADLINE: "no cost or emissions results were read from the current solve",
    CAPACITY: "no capacity results were read from the current solve",
    DISPATCH: "no dispatch results were read from the current solve",
    PRICES: "no price results were read from the current solve",
    EMISSIONS: "no emissions results were read from the current solve",
    LOAD_FLOW: "no load flow summary: the network has no lines or transformers, or none was ranked",
}
LOADFLOW_PROXY = ("Load flow: the branch loadings come from the linear (LOPF) active-power "
                  "solve, a proxy; AC apparent power, reactive flows and voltage were not assessed.")


# ── the read (impure) ────────────────────────────────────────────────────────


def read_sources(project_root: Any = None) -> dict | None:
    """The network chapters' sources on the bound project context, or None
    when the network was never solved (no chapters at all).

    `{"current": bool, "summary": dict | None, "reads": {section: dict | None}}`:
    `reads` holds `retained_results`' captures, empty when the results are
    not current (every results chapter is then stated not established)."""
    from services import project_summary, retained_results
    from services.pypsa_service import PyPSAService
    from services.workflow_tools import solve_freshness

    ctx = PyPSAService.get_active_context()
    meta = getattr(ctx.network, "meta", None) or {}
    if not meta.get("assistant_solve_input_fingerprint"):
        return None
    current = solve_freshness().get("results_current") is True
    summary = None
    try:
        summary = project_summary.summarise(
            ctx.network, solver_config=(ctx.solver_state or {}).get("solver_config"),
            project_id=str(getattr(ctx, "project_uuid", "") or ""), project_name="",
            root=project_root, live_ctx=ctx) if project_root is not None else None
    except Exception as exc:  # noqa: BLE001 - stated as not established
        logger.info("reports: model summary not read: %s", exc)
    reads: dict[str, dict | None] = {}
    if current:
        for a in retained_results.ADAPTERS:
            try:
                out = a.capture({})
            except Exception as exc:  # noqa: BLE001 - stated as not established
                logger.info("reports: %s not read: %s", a.section, exc)
                out = None
            reads[a.section] = out if isinstance(out, dict) and out.get("status") != "not_kept" else None
    return {"current": current, "summary": summary, "reads": reads}


# ── the chapters (pure) ──────────────────────────────────────────────────────


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value and abs(value) != float("inf") else None


def _count(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_count(v) for v in value.values())
    if isinstance(value, list):
        return sum(_count(v) for v in value)
    return 1 if _num(value) is not None else 0


def limit_facts(payload: dict, cap: int = MAX_FACTS) -> dict:
    """`payload` with at most `cap` numbers, in its own order: a scalar or a
    whole list row is kept while it fits, the rest dropped and counted in
    `omitted` (words, not a number, so it is no fact itself)."""
    left = cap
    dropped = 0

    def keep(value):
        nonlocal left, dropped
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                n = _count(v)
                if n == 0:
                    out[k] = copy.deepcopy(v)
                elif isinstance(v, (dict, list)):
                    kept = keep(v)
                    if kept not in ({}, []):
                        out[k] = kept
                elif n <= left:
                    out[k], left = v, left - n
                else:
                    dropped += 1
            return out
        if isinstance(value, list):
            out = []
            for v in value:
                n = _count(v)
                if n <= left:
                    out.append(copy.deepcopy(v))
                    left -= n
                else:
                    dropped += 1
            return out
        return value

    out = keep(payload)
    if dropped:
        out["omitted"] = f"{dropped} more value(s) or row(s) are in the Results tabs, not in this chapter"
    return out


def _model_summary(summary: dict) -> dict:
    counts = {k: v for k, v in (summary.get("counts") or {}).items() if v}
    time = summary.get("time") or {}
    cap = summary.get("capacity") or {}
    return {"counts": counts,
            "time": {k: v for k, v in time.items() if k in ("snapshots", "resolution", "start", "end",
                                                              "weighted_hours", "years")},
            "capacity": {k: cap.get(k) for k in ("storage_power_mw", "storage_energy_mwh", "link_mw")
                         if _num(cap.get(k))},
            "solver_mode": summary.get("solver_mode")}


def _headline(results: dict | None, emissions: dict | None) -> dict | None:
    cost = (results or {}).get("cost_breakdown") or {}
    out = {k: cost[k] for k in ("total", "capex", "opex", "fom") if _num(cost.get(k)) is not None}
    total = ((emissions or {}).get("emissions") or {}).get("total_tCO2")
    if _num(total) is not None:
        out["emissions_tCO2"] = total
    if not out:
        return None
    return {"cost_eur": {k: v for k, v in out.items() if k != "emissions_tCO2"},
            **({"emissions_tCO2": out["emissions_tCO2"]} if "emissions_tCO2" in out else {}),
            "basis": "the objective's cost basis over the modelled horizon (cost_breakdown)"}


def _capacity(results: dict | None) -> dict | None:
    rows = []
    for r in (results or {}).get("statistics") or []:
        if not isinstance(r, dict) or r.get("level_0") in ("Load", "Bus", None):
            continue
        opt, inst = _num(r.get("Optimal Capacity")), _num(r.get("Installed Capacity"))
        if opt is None and inst is None:
            continue
        rows.append({"component": r.get("level_0"), "carrier": r.get("level_1"),
                     "optimal_mw": opt, "installed_mw": inst})
    rows.sort(key=lambda r: -(r["optimal_mw"] or 0.0))
    return {"rows": rows, "unit": "MW (MWh for stores)"} if rows else None


def _dispatch(digest: dict | None) -> dict | None:
    if not isinstance(digest, dict) or digest.get("status") != "ok":
        return None
    gens = [{"carrier": r.get("name") or "(none)", "energy_mwh": r.get("energy_mwh"), "peak_mw": r.get("peak_mw")}
            for r in (digest.get("generators") or {}).get("by_carrier") or []]
    store = [{"carrier": r.get("name") or "(none)", "discharge_mwh": r.get("energy_mwh"),
              "charge_mwh": r.get("absorbed_mwh")}
             for r in (digest.get("storage_dispatch") or {}).get("by_carrier") or []]
    if not gens and not store:
        return None
    return {"generation_by_carrier": gens, "storage_by_carrier": store,
            "weighting": digest.get("weighting")}


def _prices(digest: dict | None, results: dict | None) -> dict | None:
    overall = (((digest or {}).get("prices") or {}).get("overall") or {}) if isinstance(digest, dict) else {}
    prices = {k: overall[k] for k in ("load_weighted_mean", "mean_price", "p90_price", "max_price")
              if _num(overall.get(k)) is not None}
    by_component = [{"component": r.get("component"), "total_eur": r.get("total")}
                    for r in ((results or {}).get("cost_breakdown") or {}).get("by_component") or []
                    if isinstance(r, dict) and _num(r.get("total"))]
    if not prices and not by_component:
        return None
    return {"price_eur_per_mwh": prices, "cost_by_component": by_component,
            "price_basis": ((digest or {}).get("prices") or {}).get("basis")}


def _emissions(read: dict | None) -> dict | None:
    em = (read or {}).get("emissions") or {}
    if _num(em.get("total_tCO2")) is None:
        return None
    return {"total_tCO2": em["total_tCO2"],
            "by_carrier": [{"carrier": r.get("carrier") or r.get("name"), "tCO2": r.get("tCO2")}
                           for r in em.get("by_carrier") or [] if isinstance(r, dict)],
            "cap_active": bool((em.get("cap") or {}).get("active"))}


def _load_flow(read: dict | None) -> dict | None:
    if not isinstance(read, dict) or read.get("status") != "ok":
        return None
    counts = read.get("counts") or {}
    return {"source": read.get("source"), "counts": {k: counts.get(k) for k in ("ranked", "over_limit", "at_limit")
                                                       if _num(counts.get(k)) is not None},
            "top": [{k: r.get(k) for k in ("name", "max_loading_pct", "h_over_limit") if k in r}
                    for r in read.get("top") or [] if isinstance(r, dict)],
            "missing_evidence": list(read.get("missing_evidence") or [])}


def payloads(sources: dict) -> dict[str, tuple[dict | None, str | None]]:
    """Each chapter's payload (≤ `MAX_FACTS` numbers) or None, with the
    reason it is not established."""
    reads = sources.get("reads") or {}
    current = sources.get("current") is True
    built = {
        HEADLINE: lambda: _headline(reads.get("results_summary"), reads.get("results_emissions")),
        CAPACITY: lambda: _capacity(reads.get("results_summary")),
        DISPATCH: lambda: _dispatch(reads.get("results_dispatch")),
        PRICES: lambda: _prices(reads.get("results_prices"), reads.get("results_summary")),
        EMISSIONS: lambda: _emissions(reads.get("results_emissions")),
        LOAD_FLOW: lambda: _load_flow(reads.get("loadflow")),
    }
    out: dict[str, tuple[dict | None, str | None]] = {}
    summary = sources.get("summary")
    out[MODEL_SUMMARY] = (limit_facts(_model_summary(summary)), None) if isinstance(summary, dict) \
        else (None, NO_READ[MODEL_SUMMARY])
    for sid, build in built.items():
        if not current:
            out[sid] = (None, NOT_CURRENT)
            continue
        payload = build()
        out[sid] = (limit_facts(payload), None) if payload else (None, NO_READ[sid])
    return out


def disclosures(payload_by_id: dict[str, dict | None]) -> list[str]:
    lf = payload_by_id.get(LOAD_FLOW)
    return [LOADFLOW_PROXY] if isinstance(lf, dict) and lf.get("source") != "ac_pf" else []
