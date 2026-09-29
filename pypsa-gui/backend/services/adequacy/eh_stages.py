"""
The EH study's fixed-plan fleet: which units the hub's MC certification and
the class-A COPT screening sample, and how the import enters them (plans
docs/superpowers/plans/2026-09-26-eh-wire-skipped-stages.md WP1/WP3 and
2026-09-27 / 2026-09-28 eh-zonal-mc-*).

MERGE NOTE (2026-09-28, docs/superpowers/qa/2026-09-28-merge-master-decisions.md).
The stage BODIES (frontier / mc_certify / fmea_top) live in ``eh_study`` —
the P11/P12 stage table with its budget rules, private copies and verdict
contract. This module supplies what master's PR #53/#55 added on top: the
hub-side fleet (``hub_fleet_scope``), the sampled import Link units with
their hourly cap, the zonal grid areas behind the Link (``mc_zonal``) with
grid storage and common-mode events, and the COPT screening of that fleet
(``freeze_fixed_plan``). ``eh_study`` passes its gated hub boundary
(``archetypes.hub_boundary_copy``) as ``boundary=`` so the sides and the
decision-6 rule (a Link without its own outage data is not counted) are the
ones the P11 spec amendment pins.

Nothing here is a new engine and nothing here builds a report or solves an
LP: the MC and the COPT charge ZERO solves.
"""
from __future__ import annotations

import dataclasses
import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from models.energy_hub import (
    DEFAULT_EH_FMEA_TOP_N,
    DEFAULT_EH_FRONTIER_LADDER,
)

logger = logging.getLogger("pypsa_gui.eh_stages")

# Defaults for the pack fields ``frontier_ladder`` / ``fmea_top_n`` (owned by
# ``models.energy_hub``; the names are kept for callers and tests).
EH_FRONTIER_LADDER: tuple[float, ...] = DEFAULT_EH_FRONTIER_LADDER
FMEA_TOP_N = DEFAULT_EH_FMEA_TOP_N
#: Fewer points than this is not a curve (P12 rule, ``eh_study``: the frontier
#: stage needs two solved points; master PR #53 asked for three — merge
#: decision 2026-09-28 keeps the gated P12 budget rule).
MIN_EH_FRONTIER_POINTS = 2

# Spec decision 14 / P2: FMEA top-N from the EH study is Link-primary Class-B
# residual risk. AC Line/Transformer N-1 stays on SCLOPF and is omitted from
# the FMEA ranking unless a future product decision merges them.
FMEA_TOP_LINK_PRIMARY_NOTE = (
    "Link-primary residual risk (Class-B Link sweep); "
    "AC Line/Transformer N-1 remains on SCLOPF and is omitted from FMEA ranking"
)


# ── the fixed plan ────────────────────────────────────────────────────────

@dataclass
class FixedPlanSnapshot:
    """
    What the certification and screening stages read: the MC inputs and
    the COPT screening of the plan as ``ens_solve`` left it. Either half may
    be absent with its reason — an empty sampled fleet is a fact about the
    input data, recorded here and surfaced as ``not_established``.
    """

    mc_inputs: Any = None
    mc_error: str | None = None
    copt_rows: list[dict] = field(default_factory=list)
    copt_metrics: dict | None = None
    copt_error: str | None = None
    voll: float = 0.0
    # Which fleet the MC / COPT saw (see ``hub_fleet_scope``) — disclosed on
    # the certification and fmea_top payloads.
    scope: dict | None = None
    # The two-area inputs when the zonal path applies (``mc_zonal``); the
    # MC certifies on these and the COPT screens ``mc_inputs`` (the v1 hub
    # fleet incl. the sampled Link units — the grid surplus is MC-only).
    zonal_inputs: Any = None
    # The fleet the COPT screening actually ranked (``_screening_fleet``) and
    # the screening's fidelity note (profiled units beyond K_EXACT netted).
    screening_units: list = field(default_factory=list)
    copt_fidelity_note: str | None = None


# ── the hub's fleet, not the copper plate ─────────────────────────────────

#: How the two FMEA views relate for a sampled import Link — carried on the
#: fmea_top payload so a reader never wonders why the Link appears once.
IMPORT_LINK_RANKING_NOTE = (
    "A sampled import Link is part of the COPT / MC fleet (it shapes every "
    "class-A row and the certified LOLE) but is ranked ONCE: by its Class-B "
    "row (LP re-solve on the full network) when the sweep ran, otherwise by "
    "its class-A COPT row relabelled as a Link")


@dataclass
class ImportLinkModel:
    """How ONE identified import Link enters the hub's MC / COPT fleet."""

    name: str
    model: str                 # sampled_unit | firm_block | islanded | excluded
    cap_mw_max: float
    q: float | None = None
    mttr_hours: float | None = None
    basis: str | None = None
    source: str = "missing"
    reason: str | None = None  # why firm_block when data exists but is unusable

    def as_payload(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class FleetScope:
    """
    The part of the network the hub's adequacy is certified on.

    The sequential MC and the COPT are single-area: every electrical bus is
    one copper plate. Left alone they count generators BEHIND the import
    Link — on an ``off_grid`` hub whose PoC Link the pack islanded, a
    grid-side unit the LP cannot reach still covered the hub's deficits and
    the verdict came back ``certified`` for a hub that sheds. ``hub_side``
    restricts the fleet and the demand to the hub's side of the identified
    import Link(s). Each Link then enters the hub fleet one of three ways
    (``ImportLinkModel``): a two-state ``sampled_unit`` at its planning cap
    when it carries resolvable occurrence data (plan 2026-09-27, WP1), a
    deterministic ``firm_block`` at that cap when it does not
    (``import_firmness=planning_limit_only``, spec §6), or ``islanded`` when
    the cap is zero every hour. ``freeze_fixed_plan`` may upgrade the whole
    import to ``zonal`` (WP2) when the grid side can be sampled too.
    ``whole_network`` is the fallback when the sides cannot be told apart,
    with the reason.
    """

    mode: str
    note: str
    import_links: list[str] = field(default_factory=list)
    excluded_buses: list[str] = field(default_factory=list)
    excluded_units: list[str] = field(default_factory=list)
    # The FIRM-BLOCK part only (Links without occurrence data, islanded
    # Links at 0): netted out of the hub residual. np.ndarray (H,) or None.
    import_firm_mw: Any = None
    # Every non-islanded Link's planning cap at the hub, per snapshot.
    import_cap_mw: Any = None
    link_models: list[ImportLinkModel] = field(default_factory=list)
    # CoptUnits appended to the hub fleet for the sampled Links.
    import_units: list = field(default_factory=list)
    # Per live Link: its grid component and its hub-side / grid-side series —
    # what the zonal builder groups into areas (plan 2026-09-28, WP2).
    link_grid: dict = field(default_factory=dict)
    # Grid component id → its buses (every component the Links reach).
    grid_components: dict = field(default_factory=dict)
    # Set by freeze_fixed_plan: one entry per grid area when the zonal path
    # applies (at least one area sampled); empty otherwise.
    grid_areas: list = field(default_factory=list)
    # WP3: area index → f_h, the expected share of the area's Link cap its
    # grid can back (``mc_zonal.expected_surplus_fraction``) — screening only.
    copt_fractions: dict = field(default_factory=dict)
    # WP4: common-mode events from opt-in Link data — one entry per import
    # Link that carries ``common_mode_rate`` (applied or not, with the reason).
    common_mode: list = field(default_factory=list)

    @property
    def zonal(self) -> bool:
        return any(a.get("sampled") for a in self.grid_areas)

    @property
    def common_mode_sampled(self) -> bool:
        """An applied common-mode event with a positive rate (WP4)."""
        return any(e.get("applied") and float(e.get("rate") or 0.0) > 0.0
                   for e in self.common_mode)

    def base_import_model(self) -> str | None:
        if self.mode != "hub_side":
            return None
        live = [m.model for m in self.link_models if m.model != "islanded"]
        if not live:
            return "islanded"
        # ``excluded`` (the EH study's decision-6 boundary, merge 2026-09-28):
        # a Link without its own outage data is not counted at all.
        counted = [m for m in live if m != "excluded"]
        if not counted:
            return "excluded"
        if all(m == "sampled_unit" for m in counted):
            return "sampled_unit"
        if all(m == "firm_block" for m in counted):
            return "firm_block"
        return "mixed"

    def import_model(self) -> str | None:
        return "zonal" if self.zonal else self.base_import_model()

    def import_firmness(self) -> str | None:
        base = self.base_import_model()
        if base is None:
            return None
        sampled = base in ("sampled_unit", "mixed")
        if self.zonal:
            return "outage_and_grid_sampled" if sampled else "grid_sampled"
        if base == "firm_block" and self.common_mode_sampled:
            # Review of WP4 (R2): a firm block under a sampled common-mode
            # event is not "planning_limit_only" any more.
            return "common_mode_sampled"
        if base == "sampled_unit" and self.common_mode_sampled:
            # Review of PR #55: say the event is sampled too.
            return "outage_and_common_mode_sampled"
        return {"sampled_unit": "outage_sampled",
                "mixed": "partially_outage_sampled",
                "excluded": "not_counted"}.get(
                    base, "planning_limit_only")

    def copt_import_model(self) -> str | None:
        """How the COPT screening (fmea_top class A) holds the import."""
        if self.mode != "hub_side":
            return None
        if self.zonal and self.copt_fractions:
            return "expected_surplus_profile"
        if self.import_units:
            return "two_state"
        if any(m.model == "firm_block" for m in self.link_models):
            return "firm_block"
        return None

    def copt_import_note(self) -> str | None:
        parts = []
        if self.zonal and self.copt_fractions:
            parts.append(
                "COPT screening (class-A ranking): each sampled Link stays a "
                "two-state unit, but its UP capacity per hour is scaled by "
                "the expected share of its AREA's total Link cap the grid can "
                "back, E[min(cap, surplus)]/cap from the area's own COPT; "
                "firm-block Links are derated the same way. The grid's "
                "randomness is netted at its expected value, so the screening "
                "LOLE may over- or under-state LOLE against the MC (review: "
                "±40 % measured) — it ranks modes, it does not certify. "
                "copt_metrics.import_exact carries the analytic LOLE / EUE "
                "with the import distribution mixed in exactly (no storage); "
                "the MC certifies. Grid storage is not in the COPT")
        if self.common_mode_sampled:
            parts.append(
                "COPT screening: common-mode events are MIXED exactly (one "
                "screening per event state, probability-weighted) and ranked "
                "as their own class-A modes")
        return "; ".join(parts) or None

    def as_payload(self) -> dict:
        firm = self.import_firm_mw
        cap = self.import_cap_mw
        has_firm = any(m.model in ("firm_block", "islanded")
                       for m in self.link_models)
        return {
            "mode": self.mode,
            "import_links": list(self.import_links),
            "excluded_buses": list(self.excluded_buses),
            "excluded_units": list(self.excluded_units),
            "import_model": self.import_model(),
            "import_firmness": self.import_firmness(),
            # null (not 0) when no Link is a firm block — ADR-0001.
            "import_firm_mw_max": (float(np.max(firm)) if has_firm and firm
                                   is not None and len(firm) else None),
            "import_cap_mw_max": (float(np.max(cap)) if cap is not None
                                  and len(cap) else None),
            "import_link_models": [m.as_payload() for m in self.link_models],
            "import_units": {u.name: u.name.split(":", 1)[1]
                             for u in self.import_units},
            "import_common_mode": [dict(e) for e in self.common_mode],
            "import_common_mode_sampled": self.common_mode_sampled,
            "copt_common_mode": ("event_mixture" if self.common_mode_sampled
                                 else None),
            "grid_areas": list(self.grid_areas),
            "copt_import_model": self.copt_import_model(),
            "copt_import_note": self.copt_import_note(),
            "note": self.note,
        }


def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("true", "1", "yes")


def _branch_edges(n, skip_links: set[str]) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for comp in ("lines", "transformers", "links"):
        df = getattr(n, comp, None)
        if df is None or df.empty:
            continue
        bus_cols = [c for c in df.columns
                    if c.startswith("bus") and c[3:].isdigit()]
        for name in df.index:
            if comp == "links" and str(name) in skip_links:
                continue
            ends = [str(df.at[name, c]) for c in bus_cols
                    if str(df.at[name, c] or "").strip() not in ("", "nan")]
            for a, b in zip(ends, ends[1:]):
                edges.append((a, b))
    return edges


def _components(buses: list[str], edges) -> dict[str, int]:
    parent = {b: b for b in buses}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
    roots: dict[str, int] = {}
    return {b: roots.setdefault(find(b), len(roots)) for b in buses}


def _series(n, name: str, attr: str, default: float) -> np.ndarray:
    frame = getattr(getattr(n, "links_t", None), attr, None)
    if frame is not None and name in getattr(frame, "columns", []):
        return frame[name].reindex(n.snapshots).fillna(default).to_numpy(float)
    try:
        v = float(n.links.at[name, attr])
    except (KeyError, TypeError, ValueError):
        v = default
    if not math.isfinite(v):
        v = default
    return np.full(len(n.snapshots), v, dtype=float)


def hub_fleet_scope(n, overlay, *, sample_links: bool = True,
                    boundary: dict | None = None) -> FleetScope:
    """
    Split the network at the identified import Link(s) → ``FleetScope``.

    * Import Links come from the pack's spec-§6 selection. Only a Link
      identified by ``eh_role == grid_import`` or an ``eh_poc`` endpoint is
      trusted to BE the boundary — the carrier fallback can match Links
      inside the hub, so it leaves the whole network in scope.
    * The grid side of each Link is its ``bus0`` (PyPSA's positive direction:
      import flows bus0 → bus1), flipped when only bus0's side carries an
      ``eh_critical`` bus. ``eh_poc`` is NOT used for orientation: fixtures
      and the SCR gate tag the PoC on either side.
    * The Links must actually separate the two sides once removed; a
      parallel Line / Link means there is no hub boundary to certify on.
    * Each Link's planning cap at the hub, per snapshot, is
      ``p_nom × p_max_pu × efficiency`` (forward) or ``p_nom × −p_min_pu``
      (reverse). With ``sample_links`` and resolvable occurrence data
      (``resolve_outage_params(n, "links")`` — the Class-B sweep's resolver)
      the Link becomes a two-state ``CoptUnit`` with that cap as its UP
      capacity; otherwise it is a firm block at the cap. A rate outside
      ``[0, 1)`` raises ``OutageRateError`` exactly as a generator's does.

    ``boundary`` (the EH study, merge 2026-09-28): the ``info`` of
    ``archetypes.hub_boundary_copy`` — the gated P11 hub boundary (spec §4
    amendment). Its sides are authoritative (``removed_buses`` is the grid
    side; carrier-only and ambiguous selections were already refused there),
    and decision 6 applies: a Link it lists in ``excluded_import_links`` (no
    outage data of its own, an energy-limited import) and a Link whose data
    cannot build a chain are ``excluded`` — not counted in the MC / COPT at
    all — instead of a firm block.
    """
    from services.adequacy.archetypes import select_import_links
    from services.adequacy.copt import solved_capacity

    links = select_import_links(n, overlay)
    if boundary is not None:
        links = [str(x) for x in boundary.get("import_links") or []]
    if not links:
        return FleetScope(mode="whole_network", common_mode=_ignored_common_mode(
            getattr(n, "links", None), set(), "whole-network scope (no identified hub boundary) — common-mode data not modelled"), note=(
            "no import Link identified — the MC / COPT fleet is the whole "
            "network (single-area copper plate)"))
    ldf = n.links
    buses = [str(b) for b in n.buses.index]
    poc = {str(b) for b in buses
           if "eh_poc" in n.buses.columns and _truthy(n.buses.at[b, "eh_poc"])}
    crit = {str(b) for b in buses
            if "eh_critical" in n.buses.columns
            and _truthy(n.buses.at[b, "eh_critical"])}
    identified = [
        lk for lk in links
        if ("eh_role" in ldf.columns and str(ldf.at[lk, "eh_role"]) == "grid_import")
        or str(ldf.at[lk, "bus0"]) in poc or str(ldf.at[lk, "bus1"]) in poc]
    if boundary is None and len(identified) != len(links):
        return FleetScope(mode="whole_network", import_links=list(links),
                          common_mode=_ignored_common_mode(ldf, set(), "whole-network scope (no identified hub boundary) — common-mode data not modelled"),
                          note=(
            "import Link(s) matched only by the carrier fallback — hub and grid "
            "sides are not identifiable, so the MC / COPT fleet is the whole "
            "network (single-area copper plate); tag the PoC Link "
            "eh_role=grid_import to certify on the hub side"))
    comp = _components(buses, _branch_edges(n, set(links)))
    by_comp: dict[int, set[str]] = {}
    for b, c in comp.items():
        by_comp.setdefault(c, set()).add(b)

    grid_comps: set[int] = set()
    hub_comps: set[int] = set()
    orient: dict[str, tuple[str, str]] = {}
    far = (set(str(b) for b in boundary.get("removed_buses") or [])
           if boundary is not None else None)
    for lk in links:
        b0, b1 = str(ldf.at[lk, "bus0"]), str(ldf.at[lk, "bus1"])
        g, h = b0, b1
        if far is not None:
            if b1 in far and b0 not in far:
                g, h = b1, b0
        elif (by_comp[comp[b0]] & crit) and not (by_comp[comp[b1]] & crit):
            g, h = b1, b0
        orient[lk] = (g, h)
        grid_comps.add(comp[g])
        hub_comps.add(comp[h])
    if far is None and grid_comps & hub_comps:
        return FleetScope(mode="whole_network", import_links=list(links),
                          common_mode=_ignored_common_mode(ldf, set(), "whole-network scope (no identified hub boundary) — common-mode data not modelled"),
                          note=(
            f"import Link(s) {', '.join(links)} do not separate the hub from "
            "the grid (another branch connects them) — the MC / COPT fleet is "
            "the whole network (single-area copper plate)"))

    excluded = sorted(b for b, c in comp.items() if c in grid_comps)
    if far is not None:
        excluded = sorted(far)
        grid_comps = {comp[b] for b in far}
    ex_set = set(excluded)
    # Decision 6 (EH boundary): Links the boundary refused to count, with why.
    boundary_excluded = {
        str(e.get("link")): str(e.get("reason") or "excluded by the hub boundary")
        for e in ((boundary or {}).get("excluded_import_links") or [])}
    units = []
    for cname in ("generators", "storage_units"):
        df = getattr(n, cname, None)
        if df is not None and not df.empty:
            units += [str(u) for u in df.index if str(df.at[u, "bus"]) in ex_set]

    from services.adequacy.copt import CoptUnit
    from services.adequacy.occurrence import (
        OutageRateError,
        rate_is_usable,
        resolve_outage_params,
    )

    H = len(n.snapshots)
    params = resolve_outage_params(n, "links")
    firm = np.zeros(H, dtype=float)
    cap_total = np.zeros(H, dtype=float)
    models: list[ImportLinkModel] = []
    import_units: list = []
    link_grid: dict = {}
    bad: list[str] = []
    bad_cm: list[str] = []
    common_mode: list[dict] = []
    for lk in links:
        g, h = orient[lk]
        cap = solved_capacity(ldf.loc[lk])
        if h == str(ldf.at[lk, "bus1"]):
            eff = _series(n, lk, "efficiency", 1.0)
            sending = cap * np.clip(_series(n, lk, "p_max_pu", 1.0), 0.0, None)
            delivered = sending * eff
        else:
            # Reverse flow bus1 → bus0: PyPSA withdraws eff × |p0| at bus1.
            eff = _series(n, lk, "efficiency", 1.0)
            delivered = cap * np.clip(-_series(n, lk, "p_min_pu", 0.0), 0.0, None)
            sending = delivered * eff
        cap_max = float(delivered.max()) if H else 0.0
        occ = params.loc[lk]
        src = str(occ["source"])
        cm = _common_mode_entry(ldf, str(lk), islanded=cap_max <= 0.0)
        if cm is not None:
            why = cm.pop("_bad", None)
            if why:
                bad_cm.append(f"{lk} ({why})")
            else:
                common_mode.append(cm)
        if cap_max <= 0.0:
            models.append(ImportLinkModel(name=str(lk), model="islanded",
                                          cap_mw_max=0.0, source=src))
            continue
        if str(lk) in boundary_excluded:
            models.append(ImportLinkModel(
                name=str(lk), model="excluded", cap_mw_max=cap_max,
                source=src, reason=boundary_excluded[str(lk)]))
            if cm is not None and cm.get("applied"):
                cm["applied"] = False
                cm["reason"] = ("import Link not counted in the MC fleet — "
                                + boundary_excluded[str(lk)])
            continue
        cap_total += delivered
        link_grid[str(lk)] = {"comp": comp[g], "delivered": delivered,
                              "sending": sending}
        mttr = float(occ["mttr_hours"]) if src != "missing" else float("nan")
        if sample_links and src != "missing":
            if not rate_is_usable(occ["rate"]):
                bad.append(f"{lk} (rate {float(occ['rate']):g})")
                continue
            if math.isfinite(mttr) and mttr > 0:
                constant = bool(np.all(delivered == delivered[0]))
                unit = CoptUnit(
                    name=f"link:{lk}", capacity_mw=cap_max, q=float(occ["rate"]),
                    basis=str(occ["basis"] or "FOR"), mttr_hours=mttr,
                    source=src,
                    capacity_series=None if constant else delivered.copy())
                import_units.append(unit)
                models.append(ImportLinkModel(
                    name=str(lk), model="sampled_unit", cap_mw_max=cap_max,
                    q=float(occ["rate"]), mttr_hours=mttr,
                    basis=str(occ["basis"] or "FOR"), source=src))
                continue
            reason = "outage rate resolved but no finite MTTR — cannot build a chain"
        elif src == "missing":
            reason = "no occurrence data on the Link"
        else:
            reason = "Link sampling disabled (firm-block comparison)"
        if boundary is not None:
            # Decision 6: firm only when its outages are modelled — a Link
            # that cannot be sampled is not counted (never a firm block).
            cap_total -= delivered
            link_grid.pop(str(lk), None)
            models.append(ImportLinkModel(
                name=str(lk), model="excluded", cap_mw_max=cap_max,
                source=src, reason=reason + " — not counted (decision 6)"))
            if cm is not None and cm.get("applied"):
                cm["applied"] = False
                cm["reason"] = ("import Link not counted in the MC fleet — "
                                + reason)
            continue
        firm += delivered
        models.append(ImportLinkModel(
            name=str(lk), model="firm_block", cap_mw_max=cap_max,
            q=float(occ["rate"]) if src != "missing" else None,
            mttr_hours=mttr if math.isfinite(mttr) else None,
            basis=str(occ["basis"]) if src != "missing" else None,
            source=src, reason=reason))
    if bad_cm:
        raise OutageRateError(
            f"common-mode data refused on import Link(s): "
            f"{'; '.join(bad_cm)}. A common-mode rate is a probability-like "
            "unavailability in [0, 1) and, with its MTTR, must imply an MTTF "
            "of at least one hour; fix the values (or clear them) before "
            "certifying the hub.")
    if bad:
        raise OutageRateError(
            f"outage rate outside [0, 1) on import Link(s): {', '.join(bad)}. "
            "An outage rate is a probability-like unavailability; fix the "
            "value (or clear it) before certifying the hub.")
    scope = FleetScope(mode="hub_side", note="", import_links=list(links),
                       excluded_buses=excluded, excluded_units=sorted(units),
                       import_firm_mw=firm, import_cap_mw=cap_total,
                       link_models=models, import_units=import_units,
                       link_grid=link_grid,
                       grid_components={c: sorted(by_comp[c])
                                        for c in sorted(grid_comps)},
                       common_mode=common_mode + _ignored_common_mode(
                           ldf, {str(x) for x in links},
                           "not an identified import Link — common-mode events "
                           "are modelled on identified import Links only"))
    scope.note = _scope_note(scope)
    return scope


def _link_value(ldf, lk: str, col: str):
    """A Link attribute as a finite float, or None when unset / blank / NaN."""
    if col not in ldf.columns:
        return None
    v = ldf.at[lk, col]
    if v is None or str(v).strip() in ("", "nan", "None"):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _common_mode_basis(ldf, lk: str) -> str:
    """The Link's ``common_mode_basis`` (default ``"FOR"`` when unset)."""
    if "common_mode_basis" in ldf.columns:
        b = str(ldf.at[lk, "common_mode_basis"] or "").strip()
        if b and b not in ("nan", "None"):
            return b
    return "FOR"


def _common_mode_entry(ldf, lk: str, *, islanded: bool) -> dict | None:
    """
    The Link's opt-in common-mode data (plan 2026-09-28, WP4) → payload
    entry, or None when the Link carries no ``common_mode_rate``. Never
    defaulted: a rate without a finite positive MTTR is reported and NOT
    modelled; a rate outside ``[0, 1)`` is flagged ``_bad`` for the caller
    to refuse, like any rate.
    """
    from services.adequacy.occurrence import rate_is_usable

    rate = _link_value(ldf, lk, "common_mode_rate")
    if rate is None:
        return None
    mttr = _link_value(ldf, lk, "common_mode_mttr_hours")
    entry = {"link": lk, "rate": rate, "mttr_hours": mttr,
             "basis": _common_mode_basis(ldf, lk),
             "area": None, "applied": False, "reason": None}
    if not rate_is_usable(rate):
        entry["_bad"] = f"common_mode_rate {rate:g} outside [0, 1)"
        return entry
    if islanded:
        entry["reason"] = "Link islanded — no live import for the event to take down"
    elif mttr is None or mttr <= 0.0:
        entry["reason"] = ("common_mode_rate given without a finite positive "
                           "common_mode_mttr_hours — not modelled (an MTTR is "
                           "never guessed)")
    else:
        # Review of WP4 (B2): an inconsistent pair (implied MTTF < 1 h) is
        # refused HERE, naming the Link, not at certify time.
        from services.adequacy.mc import transition_probs

        try:
            transition_probs(rate, mttr, name=f"cm:{lk}")
        except ValueError as exc:
            entry["_bad"] = str(exc)
            return entry
        entry["applied"] = True
    return entry


def _ignored_common_mode(ldf, keep: set, reason: str) -> list[dict]:
    """
    Review of WP4 (R3): common-mode data on a Link that is not an
    identified import Link is reported, never silently dropped.
    """
    out = []
    if ldf is None or ldf.empty or "common_mode_rate" not in ldf.columns:
        return out
    for lk in ldf.index:
        if str(lk) in keep:
            continue
        rate = _link_value(ldf, str(lk), "common_mode_rate")
        if rate is None:
            continue
        out.append({"link": str(lk), "rate": rate,
                    "mttr_hours": _link_value(ldf, str(lk),
                                              "common_mode_mttr_hours"),
                    "basis": _common_mode_basis(ldf, str(lk)), "area": None,
                    "applied": False,
                    "reason": reason})
    return out


def _scope_note(scope: FleetScope, extra: str | None = None) -> str:
    parts = []
    for m in scope.link_models:
        if m.model == "sampled_unit":
            parts.append(f"{m.name}: sampled two-state unit at its planning cap "
                         f"(q={m.q:.3g}, MTTR {m.mttr_hours:.3g} h)")
        elif m.model == "islanded":
            parts.append(f"{m.name}: islanded (0 MW)")
        elif m.model == "excluded":
            parts.append(f"{m.name}: not counted ({m.reason})")
        else:
            parts.append(f"{m.name}: firm block up to its planning cap "
                         f"({m.reason or 'no occurrence data'})")
    cap = scope.import_cap_mw
    peak = float(cap.max()) if cap is not None and len(cap) else 0.0
    note = (
        f"MC / COPT fleet restricted to the hub side of import Link(s) "
        f"{', '.join(scope.import_links)}: {len(scope.excluded_buses)} grid-side "
        f"bus(es) and {len(scope.excluded_units)} unit(s) excluded from the hub "
        f"area; import peak {peak:.4g} MW — " + "; ".join(parts)
        + f" (import_model={scope.import_model()}, "
        f"import_firmness={scope.import_firmness()})")
    if scope.zonal:
        sampled = [a for a in scope.grid_areas if a["sampled"]]
        note += (f"; {len(sampled)} of {len(scope.grid_areas)} grid area(s) "
                 f"sampled ({sum(a['n_units'] for a in sampled)} unit(s)): the "
                 "hub receives Σ min(Link availability, area surplus) each hour")
    if extra:
        note += f"; {extra}"
    return note


def _hub_side_copy(network, excluded_buses: list[str]):
    """
    A copy of the solved network with ``excluded_buses`` (and everything
    attached to them) removed — the grid side for the hub area, or the hub
    side for the zonal grid area.
    """
    from services.adequacy.redundancy import _detach_solver_model

    _detach_solver_model(network)
    nn = network.copy()
    ex = set(excluded_buses)
    for cls, attr in (("Generator", "generators"), ("Load", "loads"),
                      ("StorageUnit", "storage_units"), ("Store", "stores"),
                      ("ShuntImpedance", "shunt_impedances")):
        df = getattr(nn, attr, None)
        if df is not None and not df.empty and "bus" in df.columns:
            names = [i for i in df.index if str(df.at[i, "bus"]) in ex]
            if names:
                nn.remove(cls, names)
    for cls, attr in (("Line", "lines"), ("Transformer", "transformers"),
                      ("Link", "links")):
        df = getattr(nn, attr, None)
        if df is None or df.empty:
            continue
        cols = [c for c in df.columns if c.startswith("bus") and c[3:].isdigit()]
        names = [i for i in df.index
                 if any(str(df.at[i, c]) in ex for c in cols)]
        if names:
            nn.remove(cls, names)
    nn.remove("Bus", [b for b in nn.buses.index if str(b) in ex])
    return nn


def _grid_areas(network, cfg, scope: FleetScope, hub_inputs):
    """
    The zonal grid areas → ``(ZonalInputs | None, reason | None)``.

    One area per grid-side connected component that a LIVE import Link
    reaches (surplus in one grid cannot reach another except through the
    hub), in the order the Links are listed; area ``k`` samples from
    substream ``GRID_STREAM_KEY − k``. An area whose snapshot has no sampled
    unit (or refuses) keeps ``grid=None``: its Links see an unbounded
    surplus, i.e. v1 for those Links, and the payload says why. Zonal
    applies when at least one area is sampled; otherwise the certification
    stays on v1 and the reasons go into the note.
    """
    from services.adequacy.mc import snapshot_inputs
    from services.adequacy.occurrence import OutageRateError
    from services.adequacy.mc_zonal import (
        CommonMode,
        GridArea,
        ZonalInputs,
        expected_surplus_fraction,
    )

    live = [m for m in scope.link_models
            if m.model not in ("islanded", "excluded")]
    if not live:
        return None, None
    unit_pos = {u.name: i for i, u in enumerate(hub_inputs.units)}
    sampled_links = {m.name for m in scope.link_models
                     if m.model == "sampled_unit"}
    comps: list[int] = []
    for m in live:
        c = scope.link_grid[m.name]["comp"]
        if c not in comps:
            comps.append(c)
    H = len(hub_inputs.residual)
    all_buses = [str(b) for b in network.buses.index]
    areas: list = []
    payload: list[dict] = []
    reasons: list[str] = []
    cm_seen: list[str] = []
    for k, c in enumerate(comps):
        links = [m.name for m in live if scope.link_grid[m.name]["comp"] == c]
        firm = np.zeros(H, dtype=np.float64)
        deliv = np.zeros(H, dtype=np.float64)
        send = np.zeros(H, dtype=np.float64)
        for lk in links:
            info = scope.link_grid[lk]
            deliv += info["delivered"]
            send += info["sending"]
            if lk not in sampled_links:
                firm += info["delivered"]
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(send > 0, deliv / send, 1.0)
        keep = set(scope.grid_components[c])
        grid, why = None, None
        try:
            g = snapshot_inputs(_hub_side_copy(
                network, [b for b in all_buses if b not in keep]), cfg=cfg)
            if g.units:
                grid = g
            else:
                why = ("no occurrence data (no unit in this grid area carries "
                       "a resolvable outage rate) — its Links see an "
                       "unbounded surplus (v1)")
        except OutageRateError:
            # Review of PR #55: bad DATA on the grid side refuses the study,
            # exactly as it does on the hub side — falling back to an
            # unbounded grid would certify on optimistic inputs.
            raise
        except Exception as exc:  # noqa: BLE001 — the reason is the payload
            why = (f"grid-side snapshot refused ({exc}) — its Links see an "
                   "unbounded surplus (v1)")
        area_cm = []
        for e in scope.common_mode:
            if e["applied"] and e["link"] in links:
                e["area"] = k
                if float(e["rate"]) <= 0.0:
                    continue  # q = 0: no chain, no engine switch
                area_cm.append(CommonMode(link=e["link"], q=float(e["rate"]),
                                          mttr_hours=float(e["mttr_hours"]),
                                          stream=len(cm_seen)))
                cm_seen.append(e["link"])
        areas.append(GridArea(
            common_mode=tuple(area_cm),
            grid=grid,
            import_idx=tuple(unit_pos[f"link:{lk}"] for lk in links
                             if lk in sampled_links),
            firm_import_mw=np.ascontiguousarray(firm),
            delivery_ratio=np.ascontiguousarray(ratio),
            stream=k))
        entry = {"area": k, "links": links, "buses": sorted(keep),
                 "sampled": grid is not None, "reason": why,
                 "copt_surplus_fraction_min": None}
        if grid is not None:
            try:
                f = expected_surplus_fraction(
                    grid.units, grid.residual, cap=deliv, ratio=ratio,
                    periods=grid.periods)
                scope.copt_fractions[k] = f
                live_h = deliv > 0.0
                entry["copt_surplus_fraction_min"] = (
                    float(np.min(f[live_h])) if live_h.any() else None)
            except Exception as exc:  # noqa: BLE001 — screening keeps v1
                logger.exception("EH zonal COPT surplus fraction failed")
                entry["copt_note"] = (f"expected surplus not computed ({exc}); "
                                      "the screening holds this area's Links "
                                      "as in v1")
            stores = [s.name for s in grid.storage
                      if s.p_nom_mw > 0 and (s.capacity_series is None
                                             or float(np.max(s.capacity_series)) > 0)]
            entry.update({
                "units": [u.name for u in grid.units],
                "n_units": len(grid.units),
                "capacity_mw": float(sum(u.capacity_mw for u in grid.units)),
                "demand_peak_mw": (float(np.max(grid.residual))
                                   if grid.residual.size else None),
                "storage": stores,
                "storage_dispatched": bool(stores),
                "note": ("sampled as its own area: its fleet and demand "
                         "(residual after its must-take)"
                         + ("; storage dispatched grid-first, then as remote "
                            "support bounded by the Link headroom, charging "
                            "only from surplus not offered to the hub (power "
                            "offered but not used by the hub is not stored — "
                            "conservative)"
                            + ("; during a common-mode event the area exports "
                               "nothing and its stores neither support the hub "
                               "nor charge, but still serve the area's own "
                               "deficit"
                               if area_cm else "")
                            if stores else "; no storage in this area")),
            })
        else:
            entry.update({"units": [], "n_units": 0, "capacity_mw": None,
                          "demand_peak_mw": None, "storage": [],
                          "storage_dispatched": False, "note": why})
            reasons.append(f"area {k} ({', '.join(links)}): {why}")
        payload.append(entry)
    if not any(a.grid is not None for a in areas):
        why = "no grid area is sampled — " + "; ".join(reasons)
        if not cm_seen:
            return None, why + "; v1 applies"
        # WP4: a common-mode event still needs the two-area engine; every
        # area is unbounded (v1 for its Links) apart from the event.
        scope.grid_areas = payload
        return ZonalInputs(hub=hub_inputs, areas=tuple(areas)), (
            why + f"; common-mode event(s) on {', '.join(cm_seen)} sampled "
            "through the two-area engine (areas unbounded)")
    scope.grid_areas = payload
    return ZonalInputs(hub=hub_inputs, areas=tuple(areas)), None


def _screening_fleet(inputs, zonal, scope: FleetScope, *, down=frozenset()):
    """
    The COPT screening's fleet and residual (plan 2026-09-28, WP3).

    v1: the MC's own units and residual — the membership invariant. Zonal:
    the SAME units, but each area's sampled Link unit carries the area's
    ``f_h`` as its availability profile (UP = ``f_h × cap``), and the
    area's firm-block Links, netted out of the residual at full cap, are
    added back by ``(1 − f_h) × firm``. An area whose ``f`` is 1 every hour
    changes nothing, so an unbound grid screens exactly like v1. The MC keeps
    the unprofiled units: profiling them there would count the grid twice.

    ``down`` (WP4): the areas whose common-mode event is DOWN in this
    screening state — their Link units leave the fleet and their firm block
    returns to the residual in full (``_screen`` mixes the states).
    """
    units = list(inputs.units)
    res = np.array(inputs.residual, dtype=np.float64, copy=True)
    # A Link with an HOURLY cap (time-varying p_max_pu) carries an hourly
    # capacity series; the per-period COPT refuses a series that is not
    # constant within a block. Fold the hourly shape into the profile
    # (UP = cap_max × shape) — exact for a mixed unit (review of WP3).
    for i, u in enumerate(units):
        if not u.name.startswith("link:") or u.capacity_series is None:
            continue
        cs = np.asarray(u.capacity_series, dtype=np.float64)
        if all(float(np.ptp(cs[a:b])) <= 1e-9 * max(float(cs[a:b].max()), 1.0)
               for _l, a, b in inputs.periods if b > a):
            continue
        cap = float(u.capacity_mw)
        shape = cs / cap if cap > 0 else np.zeros_like(cs)
        base = (np.ones_like(cs) if u.profile is None
                else np.asarray(u.profile, dtype=np.float64))
        units[i] = dataclasses.replace(u, capacity_series=None,
                                       profile=base * shape)
    if zonal is None:
        return units, res
    drop: set[int] = set()
    for k, area in enumerate(zonal.areas):
        firm = np.asarray(area.firm_import_mw, dtype=np.float64)
        if k in down:
            # WP4 event state "down": the area's Links AND grid are out —
            # no Link unit, and the firm block netted out of the residual
            # comes back in full.
            drop.update(int(i) for i in area.import_idx)
            res = res + firm
            continue
        f = scope.copt_fractions.get(k)
        if f is None or bool(np.all(f >= 1.0)):
            continue
        for i in area.import_idx:
            prev = units[i].profile
            prof = np.asarray(f, dtype=np.float64).copy()
            if prev is not None:
                prof = prof * np.asarray(prev, dtype=np.float64)
            units[i] = dataclasses.replace(units[i], profile=prof)
        res = res + firm * (1.0 - f)
    return [u for i, u in enumerate(units) if i not in drop], res


#: The screening mixes the event states of at most this many areas that carry
#: a common-mode event (2^n screenings); areas beyond it are screened with
#: the event held up, and the payload says which.
MAX_SCREENED_CM_AREAS = 6


def _screen(inputs, zonal, scope: FleetScope, *, voll: float, index):
    """
    The class-A COPT screening → ``(analysis, screening_units, note)``.

    Without common-mode events: one ``screening_analysis`` on
    ``_screening_fleet``. With them (review of WP4, R1): a shared event is
    NOT an independent per-unit rate — folding ``q_cm`` into each Link unit
    gave P(both Links down) = q_cm² instead of q_cm (985 h vs 1190 h). COPT
    expectations are linear in the scenario probabilities, so the screening
    is run once per combination of the areas' event states and MIXED:
    LOLE / EUE / by-period and every row's ΔEUE and € are probability-
    weighted (a unit absent in a state contributes 0 there). Exact for the
    event; ``lolp_max`` becomes the maximum over states (an upper bound).
    Each event gets its own class-A row: ΔEUE = EUE − E[EUE | event up].
    """
    import itertools

    import pandas as pd

    from services.adequacy.copt import screening_analysis
    from services.adequacy.mc_zonal import _area_common_mode_q

    weights = pd.Series(inputs.weights, index=index)

    def run(down):
        units, res = _screening_fleet(inputs, zonal, scope, down=down)
        return screening_analysis(units, pd.Series(res, index=index),
                                  weights=weights, voll=voll,
                                  delta_mw=1.0), units

    cm_areas = ([k for k, a in enumerate(zonal.areas)
                 if _area_common_mode_q(a) > 0.0] if zonal is not None else [])
    if not cm_areas:
        analysis, units = run(frozenset())
        return analysis, units, None
    note = None
    if len(cm_areas) > MAX_SCREENED_CM_AREAS:
        held = cm_areas[MAX_SCREENED_CM_AREAS:]
        cm_areas = cm_areas[:MAX_SCREENED_CM_AREAS]
        note = (f"common-mode events of areas {held} held UP in the class-A "
                f"screening (more than {MAX_SCREENED_CM_AREAS} event areas); "
                "the MC and import_exact include them")
    qs = {k: _area_common_mode_q(zonal.areas[k]) for k in cm_areas}
    states = []
    for bits in itertools.product((0, 1), repeat=len(cm_areas)):
        down = frozenset(k for k, b in zip(cm_areas, bits) if b)
        p = 1.0
        for k, b in zip(cm_areas, bits):
            p *= qs[k] if b else 1.0 - qs[k]
        if p <= 0.0:
            continue
        analysis, units = run(down)
        states.append((p, down, analysis, units))
    base_analysis, base_units = next(
        (a, u) for _p, d, a, u in states if not d)
    eues = [float(a["metrics"]["eue_mwh"]) for _p, _d, a, _u in states]
    eue_mix = sum(st[0] * e for st, e in zip(states, eues))
    lole = sum(p * float(a["metrics"]["lole_hours"]) for p, _d, a, _u in states)
    by_period: dict = {}
    for p, _d, a, _u in states:
        for lab, v in (a["metrics"].get("by_period") or {}).items():
            acc = by_period.setdefault(lab, {"lole_hours": 0.0, "eue_mwh": 0.0})
            acc["lole_hours"] += p * float(v.get("lole_hours", 0.0))
            acc["eue_mwh"] += p * float(v.get("eue_mwh", 0.0))
    metrics = {
        "lole_hours": float(lole), "eue_mwh": float(eue_mix),
        "lolp_max": float(max(a["metrics"]["lolp_max"]
                              for _p, _d, a, _u in states)),
        "lolp_max_is_upper_bound": True,
        "by_period": by_period,
        "common_mode_states": len(states),
    }
    merged: dict[str, dict] = {}
    for p, _d, a, _u in states:
        for r in a["rows"]:
            m = merged.get(r["name"])
            if m is None:
                m = {**r, "failure_mode": dict(r["failure_mode"]),
                     "delta_eue_mwh": 0.0, "criticality_eur_per_year": 0.0}
                merged[r["name"]] = m
            m["delta_eue_mwh"] += p * float(r["delta_eue_mwh"])
            m["criticality_eur_per_year"] += p * float(
                r["criticality_eur_per_year"])
    for k in cm_areas:
        area = zonal.areas[k]
        q = qs[k]
        e_up = sum(st[0] * e for st, e in zip(states, eues) if k not in st[1])
        e_up = e_up / (1.0 - q) if q < 1.0 else 0.0
        delta = max(eue_mix - e_up, 0.0)
        crit = delta * max(float(voll), 0.0)
        occ = sum(8760.0 * float(c.q) / float(c.mttr_hours)
                  for c in area.common_mode if float(c.mttr_hours) > 0)
        name = "common_mode:" + ",".join(c.link for c in area.common_mode)
        merged[name] = {
            "name": name, "delta_eue_mwh": delta,
            "criticality_eur_per_year": crit,
            "failure_mode": {
                "mode_id": f"{name}:forced_outage",
                "component_class": "CommonMode", "name": name,
                "failure_class": "A", "occurrence_per_year": occ,
                "occurrence_basis": "FOR",
                "severity_eur": 0.0, "criticality_eur_per_year": crit,
                "in_metric_scope": True, "engine": "copt",
                "fidelity": "analytic_convolution",
                "rate_source": "common_mode",
            },
            "note": ("shared event: the area's import Link(s) and grid down "
                     "together; ΔEUE = EUE − E[EUE | event up]"),
        }
    for m in merged.values():
        fm = m["failure_mode"]
        crit = m["criticality_eur_per_year"]
        occ = float(fm.get("occurrence_per_year") or 0.0)
        fm["criticality_eur_per_year"] = crit
        fm["severity_eur"] = crit / occ if occ > 0 else 0.0
    rows = sorted(merged.values(), key=lambda r: (-r["delta_eue_mwh"], r["name"]))
    return {**base_analysis, "metrics": metrics, "rows": rows}, base_units, note


def freeze_fixed_plan(network, cfg, lock, *, overlay=None,
                      import_model: str = "auto",
                      boundary: dict | None = None) -> FixedPlanSnapshot:
    """
    Snapshot the MC inputs and screen the fleet, ONCE, under ``lock``.

    ``snapshot_inputs`` walks ``fleet_and_residual`` itself, so the two halves
    share membership by construction (the MC module's invariant); the COPT
    screening below reuses the very same units and residual rather than
    walking the network a second time.

    With ``overlay`` (the pack's import overlay) the fleet and the demand are
    those of the HUB side of the import Link(s) (``hub_fleet_scope``). Each
    sampled import Link is appended to the hub fleet AFTER the generators (so
    every generator keeps its positional CRN substream) and the COPT screens
    that same list; firm-block Links are netted out of the residual. Without
    an overlay, the whole network.

    ``import_model``: ``"auto"`` (default) samples Links with occurrence data
    and adds the zonal grid area when the grid side can be sampled;
    ``"sampled_unit"`` stops at v1; ``"firm_block"`` is the pre-2026-09-27
    behaviour (every Link a firm block) — kept for comparisons and tests.

    ``boundary``: the EH study's gated hub boundary (``hub_fleet_scope``).
    """
    from services.adequacy.mc import snapshot_inputs

    if import_model not in ("auto", "sampled_unit", "firm_block"):
        raise ValueError(f"unknown import_model {import_model!r}")
    snap = FixedPlanSnapshot(voll=float(getattr(cfg, "voll", 0.0) or 0.0))
    with lock:
        try:
            scope = (hub_fleet_scope(network, overlay,
                                     sample_links=import_model != "firm_block",
                                     boundary=boundary)
                     if overlay is not None
                     else FleetScope(mode="whole_network",
                                     note="no import overlay given"))
            if scope.mode == "hub_side":
                inputs = snapshot_inputs(
                    _hub_side_copy(network, scope.excluded_buses), cfg=cfg)
                firm = np.asarray(scope.import_firm_mw, dtype=np.float64)
                inputs = dataclasses.replace(
                    inputs,
                    units=tuple(inputs.units) + tuple(scope.import_units),
                    residual=np.ascontiguousarray(inputs.residual - firm))
                why = None
                if import_model == "auto":
                    snap.zonal_inputs, why = _grid_areas(network, cfg, scope,
                                                         inputs)
                # Review of WP4 (B1): only the "auto" path builds the chains.
                for e in scope.common_mode:
                    if e["applied"] and e["area"] is None:
                        e["applied"] = False
                        e["reason"] = (
                            f"import_model={import_model}: common-mode events "
                            "are modelled only on the default 'auto' path")
                # AFTER the loop: the note states import_firmness, which
                # reads the `applied` flags the loop may just have cleared.
                scope.note = _scope_note(scope, why)
            else:
                inputs = snapshot_inputs(network, cfg=cfg)
        except Exception as exc:  # noqa: BLE001 — the reason is the payload
            snap.mc_error = f"MC snapshot refused: {exc}"
            snap.copt_error = snap.mc_error
            return snap
    snap.scope = scope.as_payload()
    if not inputs.units and snap.zonal_inputs is not None:
        # Nothing sampled on the hub side, but the grid behind a firm Link
        # is: the two-area MC can still certify; the COPT has no unit to rank.
        snap.mc_inputs = inputs
        snap.copt_error = ("COPT screening skipped: no sampled unit on the hub "
                           "side (the grid-side area is MC-only)")
        # Review of WP5: no screening ran, so there is no screening model.
        snap.scope["copt_import_model"] = None
        snap.scope["copt_import_note"] = None
        return snap
    if not inputs.units:
        snap.mc_error = (
            "nothing to sample: no electrical generator carries resolvable "
            "occurrence data (unavailability + MTTR), so the sampled fleet is "
            "empty — a statement about missing input data, not about the system")
        snap.copt_error = snap.mc_error
        return snap
    snap.mc_inputs = inputs
    try:

        analysis, screen_units, cm_note = _screen(
            inputs, snap.zonal_inputs, scope, voll=snap.voll,
            index=network.snapshots)
        snap.screening_units = list(screen_units)
        snap.copt_rows = list(analysis.get("rows") or [])
        snap.copt_metrics = dict(analysis.get("metrics") or {})
        snap.copt_fidelity_note = analysis.get("fidelity_note")
        if cm_note:
            snap.copt_fidelity_note = "; ".join(
                x for x in (snap.copt_fidelity_note, cm_note) if x)
        if snap.zonal_inputs is not None:
            from services.adequacy.mc_zonal import exact_import_metrics
            try:
                snap.copt_metrics["import_exact"] = exact_import_metrics(
                    snap.zonal_inputs)
            except Exception as exc:  # noqa: BLE001 — disclosed, not fatal
                logger.exception("EH exact import metric failed")
                snap.copt_metrics["import_exact"] = {
                    "lole_hours": None, "eue_mwh": None,
                    "note": f"exact import metric not computed: {exc}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("EH fixed-plan COPT screening failed")
        snap.copt_error = f"COPT screening failed: {exc}"
    return snap


# ── WP3: fmea_top ─────────────────────────────────────────────────────────

def _flatten_mode(row: dict, *, rank: int) -> dict:
    fm = dict(row.get("failure_mode") or {})
    out = {"rank": rank, **fm}
    out["delta_eue_mwh"] = row.get("delta_eue_mwh")
    if "note" in row:
        out["note"] = row["note"]
    return out


def _rank_import_links_once(modes: list[dict], scope: dict | None
                            ) -> tuple[list[dict], dict[str, str]]:
    """
    A sampled import Link sits in the COPT fleet, so the screening produced
    a class-A row for it — and the Class-B sweep may have produced another.
    Rank it ONCE (``IMPORT_LINK_RANKING_NOTE``): drop the class-A row when a
    Class-B row names the same Link, otherwise relabel the class-A row as the
    Link's own (``component_class="Link"``, ``link:<name>:forced_outage``).
    Returns the filtered modes and ``{link: "class_b" | "class_a"}``.
    """
    unit_to_link = dict((scope or {}).get("import_units") or {})
    if not unit_to_link:
        return modes, {}
    b_links = {str((r.get("failure_mode") or {}).get("name"))
               for r in modes
               if (r.get("failure_mode") or {}).get("failure_class") == "B"}
    out: list[dict] = []
    ranking: dict[str, str] = {}
    for r in modes:
        fm = r.get("failure_mode") or {}
        uname = str(fm.get("name"))
        if fm.get("failure_class") != "A" or uname not in unit_to_link:
            out.append(r)
            continue
        link = unit_to_link[uname]
        if link in b_links:
            ranking[link] = "class_b"
            continue
        ranking[link] = "class_a"
        out.append({**r, "name": link, "failure_mode": {
            **fm, "name": link, "component_class": "Link",
            "mode_id": f"link:{link}:forced_outage"}})
    return out, ranking
