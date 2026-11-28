"""
Assemble ``ReferenceDesignReport`` — the only report builder for EH studies.

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md §4–§5
decision 16: EHStudyRunner emits reports only through this function.

Phase 5 adds sizing summary, TEA/LCOE wrap, stable export, and store/GET helpers.
"""
from __future__ import annotations

import math
from typing import Any

from models.energy_hub import (
    REPORT_SECTIONS,
    EHStudyPipeline,
    GatesBlock,
    PipelineStageRecord,
    ReferenceDesignReport,
    SectionState,
    SectionStatus,
    TeaBlock,
)
from services.adequacy import slack

EH_REPORT_STORE_KEY = "eh_reference_design_report"

EXPORT_KEYS: tuple[str, ...] = (
    "archetype",
    "pack_hash",
    "assumptions_hash",
    "ens_cap_permyriad",
    "achieved_ens_permyriad",
    "achieved_shed_hours",
    "mc_lole_h",
    "certified",
    "cost_at_target_eur",
    "period_basis",
    "valued_to_year",
    "excludes_shed_cost",
    "completeness",
    "sections",
    "pipeline",
    "tea",
    "gates",
    "notes",
)


#: Slack carriers this report always dropped that slack.py does not hold
#: (older networks). slack.SLACK_CARRIERS stays the canonical set.
_LEGACY_ALIASES = frozenset({"voll", "dsr"})


def _is_slack(name: Any, carrier: str) -> bool:
    """A slack generator the solve adds, never a unit anyone sized or installed:
    by slack.py's carriers or name prefixes, a legacy alias, the bare ``__voll``
    prefix, or the lost-production slack (``__lost_``, outside slack.py's sets)."""
    return (slack.is_slack_carrier(carrier) or carrier in _LEGACY_ALIASES
            or slack.is_slack_name(name)
            or str(name).startswith(("__voll", slack.LOST_SLACK_PREFIX)))


#: ``process_role`` of a product bus's slack generators: bought in or lost,
#: never installed (``services.asset_schema.entries``). Read by role, never by
#: carrier name (GK A 33 S3).
_PRODUCT_SLACK_ROLES = frozenset({"buy_in", "shortfall"})

#: How far the peak import may sit above the connection limit and still read
#: "within": float summing of the import Links' flows, never a real overload.
CONNECTION_TOL_MW = 1e-6

#: The request ``import_capacity`` reads when the caller gives none. Its pack
#: only matters to the carrier rule (3) of the import-Link selection; the
#: ``eh_role`` and ``eh_poc`` rules every template uses read the network alone.
_DEFAULT_REQUEST = {"archetype": "strong_grid"}

NO_REQUEST_REFUSAL = ("No link is marked as the grid import, so the connection limit "
                      "can't be read: set the grid link's role to Grid import.")


def _picked_by_carrier(n) -> bool:
    """True when the import Links are picked by the carrier rule (3) alone:
    neither ``eh_role`` nor ``eh_poc`` marks them."""
    from services.adequacy import archetypes as arch
    from services.flow_service import _pack_for

    _links, rule = arch.select_import_links_with_rule(n, _pack_for(_DEFAULT_REQUEST).import_overlay)
    return rule not in ("eh_role", "eh_poc")


def _p_nom(row) -> float:
    """A generator's installed size: its ``p_nom_opt`` when that is finite and
    above zero (a solve's), else ``p_nom``."""
    opt = _finite(row.get("p_nom_opt"))
    if opt is not None and opt > 0:
        return opt
    return _finite(row.get("p_nom")) or 0.0


def _weighted_energy(n, name: str) -> float | None:
    """A solved generator's output over the horizon, snapshot-weighted."""
    p = getattr(n.generators_t, "p", None)
    if not getattr(n, "is_solved", False) or p is None or name not in p.columns:
        return None
    return float((p[name] * n.snapshot_weightings.generators).sum())


# One rule for "the grid-side bus holds nothing else of the site's", shared
# with section 5's site assets (GK A 40, follow-up b); the old names stay.
from services.flow_service import _export_only, site_equipment_on as _site_side  # noqa: E402


def _import_peaks(n, links: tuple[str, ...]) -> dict[Any, float] | None:
    """The peak import through ``links`` per period (one key, None, on a
    single-period network): the max over snapshots of their summed ``p0``,
    import positive, in the bus unit; a horizon (or period) with no import
    peaks at 0, never a negative "import" (GK A 33). None when unsolved."""
    flows = getattr(n.links_t, "p0", None)
    if not getattr(n, "is_solved", False) or flows is None or not links:
        return None
    cols = [c for c in links if c in flows.columns]
    if not cols:
        return None
    total = flows[cols].sum(axis=1).clip(lower=0.0)
    if len(getattr(n, "investment_periods", ())) > 0:
        return {int(k): float(v) for k, v in total.groupby(level=0).max().items()}
    return {None: float(total.max())}


def _connection(n, request: dict | None) -> tuple[dict[str, Any], set[str]]:
    """The grid connection as the study reads it, and the generators that are
    grid supply beyond it (never installed capacity).

    The limit is `flow_service.import_capacity`, the one reading readiness and
    the run take (GK A 33 S1): refused, it is None with the refusal's words,
    never a number or "within". A generator is grid supply when it sits on the
    grid side of an import Link and that bus holds nothing of the site's; one
    beside site equipment is counted, with the reason. A generator at the PoC
    with no Link to it is the grid itself, with no limit between: "unbounded"
    (SH 40's PCC rule, GK A 33 S2)."""
    from services.adequacy.archetypes import _poc_buses
    from services.flow_service import ImportCapacity, import_capacity, site_equipment_on

    cap = import_capacity(n, request or _DEFAULT_REQUEST)
    if request is None and cap.links and _picked_by_carrier(n):
        # Without the study's request the pack is a placeholder: a pick by the
        # carrier rule (3) is the placeholder's, never the network's (GK A 33).
        cap = ImportCapacity((), None, NO_REQUEST_REFUSAL)
    links = set(cap.links)
    grid_side = ({str(n.links.at[x, "bus0"]) for x in cap.links} if cap.links else set())
    supply: set[str] = set()
    counted: list[str] = []
    unbounded = False
    gens = n.generators
    for bus in sorted(grid_side | _poc_buses(n)):
        here = [str(g) for g in gens.index[gens["bus"].astype(str) == bus]] if not gens.empty else []
        here = [g for g in here if not _is_slack(g, str(gens.at[g, "carrier"] or ""))
                and str(gens.at[g, "process_role"] if "process_role" in gens.columns else "")
                not in _PRODUCT_SLACK_ROLES]
        if not here:
            continue
        ports = [c for c in n.links.columns if c.startswith("bus") and c[3:].isdigit()]
        touching = not n.links.empty and bool(n.links[ports].astype(str).eq(bus).any(axis=None))
        if not touching:
            supply.update(here)
            unbounded = True
            continue
        if bus not in grid_side:
            counted += [f"{g} is counted: bus {bus} is marked as the point of connection, "
                        "but no grid import link leaves it" for g in here]
            continue
        others = site_equipment_on(n, bus, links)
        if others:
            counted += [f"{g} is counted: bus {bus} also holds {', '.join(others)}" for g in here]
        else:
            supply.update(here)
    peaks = None if unbounded else _import_peaks(n, cap.links)
    peak = max(peaks.values()) if peaks else None
    if unbounded:
        check = "unbounded"
    elif cap.mw is None or peaks is None:
        check = None
    else:
        over = any(v > cap.mw + CONNECTION_TOL_MW for v in peaks.values())
        check = "exceeds" if over else "within"
    return {
        "grid_connection_mw": None if unbounded else cap.mw,
        "grid_connection_refusal": None if unbounded else cap.refusal,
        "grid_supply_mw": float(sum(_p_nom(gens.loc[g]) for g in sorted(supply))),
        "grid_supply_counted": counted,
        "grid_import_peak_mw": peak,
        "grid_import_peak_by_period": ({str(k): v for k, v in peaks.items()}
                                       if peaks and None not in peaks else None),
        "connection_check": check,
    }, supply


def _horizon_words(n) -> str:
    """"2027–2035" on a staged plan (its first and last period), "2030" with
    one period; "8,760 h" on a single-period network (its weighted hours; a
    network always has a snapshot)."""
    periods = [int(p) for p in getattr(n, "investment_periods", ())]
    if periods:
        return f"{periods[0]}–{periods[-1]}" if len(periods) > 1 else str(periods[0])
    return f"{float(n.snapshot_weightings.generators.sum()):,.0f} h"


def _product_slacks(n) -> list[dict[str, Any]]:
    """A product's buy-in and shortfall generators (by ``process_role``):
    never installed, never hidden either (GK A 33 S3). Each with its size, its
    energy over the horizon when solved, and its bus's units."""
    from services.flow_service import _size_unit

    gens = n.generators
    if gens.empty or "process_role" not in gens.columns:
        return []
    out = []
    for name in gens.index[gens["process_role"].isin(_PRODUCT_SLACK_ROLES)]:
        row = gens.loc[name]
        out.append({"name": str(name), "process_role": str(row["process_role"]),
                    "carrier": str(row.get("carrier", "") or ""),
                    "p_nom": _p_nom(row), "energy": _weighted_energy(n, str(name)),
                    "unit": _size_unit(n, row.get("bus"), stock=False),
                    "energy_unit": _size_unit(n, row.get("bus"), stock=True)})
    return out


def sizing_summary_from_network(n, request: dict | None = None) -> dict[str, Any]:
    """Installed p_nom by generator carrier (MVP-A sizing section), the grid
    connection and the product slacks beside it, and every asset the study
    sized.

    Installed capacity is the site's own generation: the grid supply behind
    the connection (``_connection``), a product's buy-in or shortfall and the
    VOLL / DSR slacks are not counted. ``request`` is the study's request
    (its pack picks the import Links); without one, the network's own
    ``eh_role`` / ``eh_poc`` marks pick them (issue template-e2e 20)."""
    connection, supply = _connection(n, request)
    slacks = _product_slacks(n)
    left_out = supply | {s["name"] for s in slacks}
    by_carrier: dict[str, float] = {}
    total = 0.0
    if n.generators is not None and not n.generators.empty:
        for name, row in n.generators.iterrows():
            carrier = str(row.get("carrier", "") or "")
            if _is_slack(name, carrier) or str(name) in left_out:
                continue
            p_nom = _p_nom(row)
            key = carrier or "unknown"
            by_carrier[key] = by_carrier.get(key, 0.0) + p_nom
            total += p_nom
    return {
        "by_carrier": {k: float(v) for k, v in sorted(by_carrier.items())},
        "total_p_nom_mw": float(total),
        **connection,
        "product_slacks": slacks,
        # The span the slacks' energy is over, in words (UX 34).
        "horizon": _horizon_words(n),
        "assets": sized_assets(n),
        # False: the network was not solved, so `assets` carry no built size.
        "assets_solved": bool(getattr(n, "is_solved", False)),
    }


def _finite(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


#: (component, its table, the size attribute's stem): ``p`` sizes MW, ``e`` MWh.
_SIZED = (("Generator", "generators", "p"), ("StorageUnit", "storage_units", "p"),
          ("Link", "links", "p"), ("Store", "stores", "e"))


def sized_assets(n) -> list[dict[str, Any]]:
    """One row per asset the solve sized (``*_extendable``), in component
    order then the network's: its built size (``*_opt``) and allowed range
    (an infinite or non-finite figure reads None). A storage unit adds its
    energy (``built_mw × max_hours``); a store has no MW, only MWh; a link's
    MW is on its bus0 side (``basis``). One size per asset: on a
    multi-period network PyPSA keeps one ``*_opt`` per asset (a build in
    another period is another asset, with its own ``build_year``), so each
    row is that asset's one build. On a network
    that was not solved the built figures are None, never ``p_nom`` shown as
    built (``solved``) (walkthrough asset-investment 03, GK A 30 A5)."""
    from services.flow_service import _size_unit

    solved = bool(getattr(n, "is_solved", False))
    staged = len(getattr(n, "investment_periods", ())) > 0
    rows: list[dict[str, Any]] = []
    for component, table, stem in _SIZED:
        df = getattr(n, table, None)
        ext = f"{stem}_nom_extendable"
        if df is None or df.empty or ext not in df.columns:
            continue
        for name, row in df[df[ext].fillna(False).astype(bool)].iterrows():
            carrier = str(row.get("carrier", "") or "")
            # Only a GENERATOR is slack: DSR modelled as a StorageUnit or a
            # Store is a sized asset and stays a row (GK A 30 2).
            if component == "Generator" and _is_slack(name, carrier):
                continue
            unit = "mw" if stem == "p" else "mwh"
            out: dict[str, Any] = {
                "component": component, "name": str(name), "carrier": carrier,
                f"built_{unit}": _finite(row.get(f"{stem}_nom_opt")) if solved else None,
                f"min_{unit}": _finite(row.get(f"{stem}_nom_min")),
                f"max_{unit}": _finite(row.get(f"{stem}_nom_max")),
            }
            if component == "Link":
                out["basis"] = "bus0"
            # The size's own unit (MW or t/h, MWh or t), from its bus as
            # section 5 reads it, so the report and the UI print one unit.
            out["unit"] = _size_unit(n, row.get("bus0" if component == "Link" else "bus"),
                                     stock=component == "Store")
            if staged:
                # The period a staged build lands in (one candidate per period).
                year = _finite(row.get("build_year"))
                out["stage"] = int(year) if year is not None else None
            if component == "StorageUnit":
                hours = _finite(row.get("max_hours"))
                out["max_hours"] = hours
                built = out["built_mw"]
                out["built_mwh"] = (built * hours
                                    if built is not None and hours is not None else None)
            rows.append(out)
    return rows


def served_energy_mwh_from_network(n, *, ens_mwh: float | None = None) -> float | None:
    """Weighted electrical demand less ENS (served energy for LCOE).

    Prefer ``loads_t.p_set`` (demand) over ``loads_t.p`` (which can include
    shed-as-dispatch artefacts). Subtract ``ens_mwh`` when provided so LCOE
    is cost / *served* energy, not cost / gross demand.
    """
    if n.loads is None or n.loads.empty:
        return None
    weights = n.snapshot_weightings.generators
    demand: float | None = None
    if hasattr(n.loads_t, "p_set") and n.loads_t.p_set is not None \
            and not n.loads_t.p_set.empty:
        demand = float((n.loads_t.p_set.mul(weights, axis=0)).sum().sum())
    elif "p_set" in n.loads.columns:
        hours = float(weights.sum()) if weights is not None else float(len(n.snapshots))
        demand = float(n.loads["p_set"].fillna(0).sum()) * hours
    elif hasattr(n.loads_t, "p") and n.loads_t.p is not None and not n.loads_t.p.empty:
        # Last resort — may not distinguish shed; caller should pass ens_mwh.
        demand = float((n.loads_t.p.clip(lower=0).mul(weights, axis=0)).sum().sum())
    if demand is None or demand <= 0:
        return None
    if ens_mwh is not None:
        return max(0.0, float(demand) - float(ens_mwh))
    return float(demand)


# The electrolyser-Link carrier tokens ``services.results.lcoh.compute_lcoh``
# filters on (and the frontend's ``isElectrolyzerCarrier``). Read from the
# engine's own frame walk below rather than duplicated as a rule: this module
# only asks "are there any such Links" before spending the engine's time.
def _live_result_df(n, accessor_name: str, attr: str, source: str = "lopf"):
    """``result_df`` for a network with no router state: the live frames.

    ``compute_lcoh`` reads bus duals through ``corrected_marginal_prices``,
    which takes the router's ``_result_df`` (LP-stage snapshot first, live
    network as fallback). Inside the EH study the network IS the just-solved
    plan, so the live frame is the right answer and there is no router state
    to prefer.
    """
    frame = getattr(getattr(n, accessor_name, None), attr, None)
    if frame is None:
        raise KeyError(f"{accessor_name}.{attr} not available")
    return frame


def has_electrolyser_links(n) -> bool:
    """Any Link whose carrier the LCOH engine would price as an electrolyser."""
    links = getattr(n, "links", None)
    if links is None or links.empty or "carrier" not in links.columns:
        return False
    tokens = ("electrol", "p2g", "p2h2", "power-to-h2", "power-to-gas",
              "power2gas", "hydrogen", "h2")
    for c in links["carrier"].astype(str).str.strip().str.lower():
        if any(t in c for t in tokens):
            return True
    return False


def _fill_lcoa(block: TeaBlock, network, cfg) -> None:
    """The levelised cost of ammonia (EUR/t NH3) onto ``block``, flagged as LCOH is
    (ADR-0001): ``skipped`` with no ammonia-synthesis Link, ``not_established``
    when one made no NH3 or its prices are not prices. Never 0 (template-e2e 28a)."""
    from services.results.lcoa import ammonia_synthesis_links, compute_lcoa

    if not ammonia_synthesis_links(network):
        block.lcoa_status = "skipped"
        block.lcoa_note = "Cost of ammonia skipped: the network has no ammonia-synthesis Links"
        return
    try:
        from services.solver_service import SolverConfig

        from harness.solve_validity import mip_price_limit, price_limit_without_config

        # GK B2 A1: judged on the solve that produced `network`: the study's own config
        # (`st.cfg`) or the network's sealed record, never the live config; without a
        # config it fails closed.
        limit = mip_price_limit(network, cfg) if cfg is not None else price_limit_without_config(network)
        payload = compute_lcoa(network, cfg or SolverConfig(), result_df=_live_result_df,
                               price_limit=limit)
    except Exception as exc:  # noqa: BLE001 — flagged, never zeroed
        block.lcoa_status = "not_established"
        block.lcoa_note = f"Cost of ammonia not established: the engine failed ({exc})"
        return
    if payload.get("unavailable_reason"):
        block.lcoa_status = "not_established"
        block.lcoa_note = "Cost of ammonia not established: an integer (MIP) solve has no electricity price"
        return
    if payload.get("not_established"):
        block.lcoa_status = "not_established"
        block.lcoa_note = f"Cost of ammonia not established: {payload['not_established']}"
        return
    total = payload.get("total") or {}
    value = total.get("lcoa_eur_per_t")
    if value is None or not math.isfinite(float(value)):
        block.lcoa_status = "not_established"
        block.lcoa_note = (
            f"Cost of ammonia not established: {len(payload.get('rows') or [])} ammonia-synthesis "
            "Link(s) but no NH3 made in the solved dispatch")
        return
    block.lcoa_eur_per_t = float(value)
    block.currency = payload.get("currency") or block.currency
    block.lcoa_status = "ok"
    block.lcoa_note = (
        f"Cost of ammonia = (fixed + VOM + electricity + H2 at their bus prices) / NH3 made, over "
        f"{len(payload.get('rows') or [])} ammonia-synthesis Link(s), {total.get('nh3_t', 0.0):,.0f} t "
        f"({'; '.join(total.get('t_basis') or [])})")
    block.notes = f"{block.notes}; {block.lcoa_note}"


def compute_tea(*, cost_eur: float | None,
                served_energy_mwh: float | None,
                network=None, cfg=None) -> TeaBlock:
    """Post-process LCOE (+ optional LCOH) from existing cost + energy — no
    second cost engine (spec decision 9).

    LCOE = ``cost_at_target_eur / served_energy_mwh`` (ex-shed cost). LCOH is
    the fleet ``lcoh_eur_per_kg_h2`` of ``services.results.lcoh.compute_lcoh``
    over the solved network's electrolyser Links, when ``network`` is given
    and has any. ADR-0001: an LCOH that cannot be established is ``None`` +
    ``lcoh_status``/``lcoh_note`` — ``skipped`` when there is no electrolyser
    Link, ``not_established`` when there is one but it produced no H₂ (or the
    engine could not price it). Never 0. The cost of ammonia (EUR/t NH3) is
    flagged the same way over the ammonia-synthesis Links (``_fill_lcoa``).
    """
    if cost_eur is None or served_energy_mwh is None or served_energy_mwh <= 0:
        block = TeaBlock(
            notes="LCOE not established: need cost_at_target_eur and served energy > 0")
    else:
        block = TeaBlock(
            lcoe_eur_per_mwh=float(cost_eur) / float(served_energy_mwh),
            notes="LCOE = cost_at_target_eur / served_energy_mwh (ex-shed cost)",
        )
    if network is None:
        return block
    _fill_lcoh(block, network, cfg)
    # Ammonia after hydrogen, so the hydrogen path and its notes read as before (28a).
    try:
        _fill_lcoa(block, network, cfg)
    except Exception as exc:  # noqa: BLE001 — flagged, never zeroed
        block.lcoa_status = "not_established"
        block.lcoa_note = f"Cost of ammonia not established: the engine failed ({exc})"
    return block


def _fill_lcoh(block: TeaBlock, network, cfg) -> None:
    """The fleet LCOH onto ``block`` (the body ``compute_tea`` had before 28a)."""
    if not has_electrolyser_links(network):
        block.lcoh_status = "skipped"
        block.lcoh_note = "LCOH skipped: the network has no electrolyser Links"
        return
    try:
        from services.results.lcoh import compute_lcoh
        from services.solver_service import SolverConfig

        payload = compute_lcoh(network, cfg or SolverConfig(),
                               result_df=_live_result_df)
    except Exception as exc:  # noqa: BLE001 — flagged, never zeroed
        block.lcoh_status = "not_established"
        block.lcoh_note = f"LCOH not established: LCOH engine failed ({exc})"
        return
    if (payload or {}).get("unavailable_reason"):
        # assistant-assurance 17: an integer (MIP) solve has no electricity price.
        block.lcoh_status = "not_established"
        block.lcoh_note = "LCOH not established: an integer (MIP) solve has no electricity price"
        return
    total = (payload or {}).get("total") or {}
    value = total.get("lcoh_eur_per_kg_h2")
    if value is None or not math.isfinite(float(value)):
        n_rows = len((payload or {}).get("rows") or [])
        block.lcoh_status = "not_established"
        block.lcoh_note = (
            f"LCOH not established: {n_rows} electrolyser Link(s) but no H₂ "
            "produced in the solved dispatch (Links never consumed)")
        return
    block.lcoh_eur_per_kg = float(value)
    block.currency = payload.get("currency") or block.currency
    block.lcoh_status = "ok"
    block.lcoh_note = (
        f"LCOH = fleet (CAPEX + VOM + electricity) / H₂ produced over "
        f"{len(payload.get('rows') or [])} electrolyser Link(s), LHV basis")
    block.notes = f"{block.notes}; {block.lcoh_note}"


def assemble_reference_design_report(
    *,
    archetype: str,
    pack_hash: str,
    assumptions_hash: str,
    section_payloads: dict[str, tuple[SectionStatus, dict[str, Any] | None, str | None]],
    pipeline: EHStudyPipeline,
    ens_cap_permyriad: float | None = None,
    achieved_ens_permyriad: float | None = None,
    achieved_shed_hours: float | None = None,
    mc_lole_h: float | None = None,
    certified: bool | None = None,
    cost_at_target_eur: float | None = None,
    period_basis: str | None = None,
    valued_to_year: int | None = None,
    tea: TeaBlock | None = None,
    gates: GatesBlock | None = None,
    notes: list[str] | None = None,
) -> ReferenceDesignReport:
    """Build the one product artifact from stage fragments + completeness."""
    sections: dict[str, SectionState] = {}
    completeness: dict[str, SectionStatus] = {}
    for name in REPORT_SECTIONS:
        if name in section_payloads:
            status, payload, note = section_payloads[name]
        else:
            status, payload, note = "not_established", None, None
        sections[name] = SectionState(status=status, payload=payload, note=note)
        completeness[name] = status

    return ReferenceDesignReport(
        archetype=archetype,  # type: ignore[arg-type]
        pack_hash=pack_hash,
        assumptions_hash=assumptions_hash,
        ens_cap_permyriad=ens_cap_permyriad,
        achieved_ens_permyriad=achieved_ens_permyriad,
        achieved_shed_hours=achieved_shed_hours,
        mc_lole_h=mc_lole_h,
        certified=certified,
        cost_at_target_eur=cost_at_target_eur,
        period_basis=period_basis,  # type: ignore[arg-type]
        valued_to_year=valued_to_year,
        sections=sections,
        completeness=completeness,
        pipeline=pipeline,
        tea=tea,
        gates=gates,
        notes=list(notes or []),
    )


def pipeline_from_records(
        records: list[PipelineStageRecord], *,
        budget_solves: int,
        solves_consumed: int,
        aborted: bool = False) -> EHStudyPipeline:
    return EHStudyPipeline(
        stages=records,
        budget_solves=budget_solves,
        solves_consumed=solves_consumed,
        aborted=aborted,
    )


def export_reference_design(report: ReferenceDesignReport) -> dict[str, Any]:
    """Stable-key export for golden fixtures / CSV/JSON download."""
    raw = report.model_dump(mode="json")
    return {k: raw.get(k) for k in EXPORT_KEYS}


def store_eh_report(store: dict, report: ReferenceDesignReport) -> None:
    store[EH_REPORT_STORE_KEY] = report.model_dump(mode="json")


def load_eh_report(store: dict) -> ReferenceDesignReport | None:
    raw = store.get(EH_REPORT_STORE_KEY)
    if not raw:
        return None
    if isinstance(raw, ReferenceDesignReport):
        return raw
    return ReferenceDesignReport.model_validate(raw)


LEGACY_CRITICALITY_NOTE = (
    "Saved before FMEA criticality was put on a per-year basis: these "
    "criticality and severity figures are per modelled horizon, not per "
    "year. Re-run the study for per-year figures.")


def mark_legacy_fmea_top(payload: dict | None) -> dict | None:
    """
    LC-30: a saved ``fmea_top`` payload whose engine rows carry no
    ``criticality_basis`` predates the annualising — its criticality is the
    horizon's ΔEUE × VoLL. Never show it as per-year: return a copy with each
    such row marked ``criticality_basis="per_horizon_legacy"``, the payload's
    ``criticality_caveat`` set, and the caveat leading the class-A
    ``copt_fidelity_note`` (the note the panel shows beside the table).
    Current payloads come back unchanged.
    """
    if not isinstance(payload, dict):
        return payload
    class_a = payload.get("class_a") if isinstance(payload.get("class_a"), dict) else None
    # ``top``: the one merged list builds before the per-class ranks wrote.
    groups = [payload.get("rows"), payload.get("top"), (class_a or {}).get("rows")]
    def _legacy(r) -> bool:
        # Class C was always per event, so an old class-C row is not legacy.
        return (isinstance(r, dict) and "criticality_basis" not in r
                and r.get("criticality_eur_per_year") is not None
                and r.get("failure_class") != "C")

    if not any(_legacy(r) for g in groups if isinstance(g, list) for r in g):
        return payload

    def _mark(rows):
        if not isinstance(rows, list):
            return rows
        return [{**r, "criticality_basis": "per_horizon_legacy"} if _legacy(r) else r
                for r in rows]

    out = {**payload, "criticality_caveat": LEGACY_CRITICALITY_NOTE}
    for key in ("rows", "top"):
        if key in payload:
            out[key] = _mark(payload[key])
    if class_a is not None:
        note = class_a.get("copt_fidelity_note")
        out["class_a"] = {**class_a, "rows": _mark(class_a.get("rows")),
                          "copt_fidelity_note": "; ".join(
                              x for x in (LEGACY_CRITICALITY_NOTE, note) if x)}
    return out


def mark_legacy_report(body: dict) -> dict:
    """An exported report with its ``fmea_top`` payload through
    ``mark_legacy_fmea_top`` (a copy; the input is not mutated)."""
    sections = body.get("sections") if isinstance(body, dict) else None
    fmea = sections.get("fmea_top") if isinstance(sections, dict) else None
    if not (isinstance(fmea, dict) and isinstance(fmea.get("payload"), dict)):
        return body
    return {**body, "sections": {**sections, "fmea_top": {
        **fmea, "payload": mark_legacy_fmea_top(fmea["payload"])}}}


def record_report(record: dict | None) -> dict | None:
    """The report a study record keeps, or None: the ONE rule the export and
    ``eh_review.review_latest`` share. Any record that is not still running
    (done, failed, aborted, partial) may hold one; a running record's report
    describes a different run."""
    if not isinstance(record, dict) or record.get("status") == "running":
        return None
    rep = record.get("report")
    return rep if isinstance(rep, dict) else None


def eh_reference_design_http_payload(
        store: dict, record: dict | None = None) -> tuple[dict | None, int]:
    """Return (body, status) for GET /results/eh_reference_design.

    TE2E-16: a later foreground solve clears the stored report, but the study
    record keeps its own copy. With ``record`` given, that copy is served in
    the same export shape instead of 204. The body then carries ``stale``
    (true when it came from the record) and ``edited_since_study`` (the
    record's own value, as ``eh_review`` reports it), so a saved file says it
    is from an earlier run. Both keys are only added when ``record`` is given.
    """
    report, stale = load_eh_report(store), False
    if report is None and record_report(record) is not None:
        try:
            report = ReferenceDesignReport.model_validate(record_report(record))
            stale = True
        except ValueError:
            report = None
    if report is None:
        return None, 204
    body = mark_legacy_report(export_reference_design(report))
    if isinstance(record, dict):
        body["stale"] = stale
        body["edited_since_study"] = record.get("edited_since_study")
    return body, 200
