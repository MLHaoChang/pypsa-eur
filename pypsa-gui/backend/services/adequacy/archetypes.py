"""
Energy Hub archetype packs — apply / undo network overlays + SolverConfig patch.

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md §3, §6
Plan: Phase 1 (no SCR; import overlays + DSR preflight only).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from models.energy_hub import ArchetypePack, ImportOverlaySpec


class ArchetypePackError(ValueError):
    """Pack cannot be applied (missing import Links, invalid overlay, …)."""


@dataclass
class ApplyResult:
    undo: Callable[[], None]
    pack_hash: str
    archetype: str
    import_links: list[str]
    warnings: list[str]


def pack_hash(pack: ArchetypePack) -> str:
    payload = pack.model_dump(mode="json")
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _flag(v) -> bool:
    return v is True or str(v).strip().lower() in ("true", "1", "yes")


def select_import_links_with_rule(
        n, overlay: ImportOverlaySpec) -> tuple[list[str], str]:
    """Spec §6 selection, returning which rule matched.

    Rule names: ``"eh_role"`` (1), ``"eh_poc"`` (2), ``"carrier"`` (3), or
    ``"none"`` when nothing matched.
    """
    if n.links is None or n.links.empty:
        return [], "none"

    links = n.links
    # 1) eh_role == grid_import
    if "eh_role" in links.columns:
        role_hits = [
            str(i) for i in links.index
            if str(links.at[i, "eh_role"]) == "grid_import"
        ]
        if role_hits:
            return role_hits, "eh_role"

    # 2) either endpoint bus tagged eh_poc
    poc_buses = _poc_buses(n)
    if poc_buses:
        hits = []
        for i in links.index:
            b0 = str(links.at[i, "bus0"]) if "bus0" in links.columns else ""
            b1 = str(links.at[i, "bus1"]) if "bus1" in links.columns else ""
            if b0 in poc_buses or b1 in poc_buses:
                hits.append(str(i))
        if hits:
            return hits, "eh_poc"

    # 3) carrier ∈ import_carriers
    carriers = {str(c) for c in (overlay.import_carriers or [])}
    if carriers and "carrier" in links.columns:
        hits = [
            str(i) for i in links.index
            if str(links.at[i, "carrier"]) in carriers
        ]
        if hits:
            return hits, "carrier"

    return [], "none"


def select_import_links(n, overlay: ImportOverlaySpec) -> list[str]:
    """Spec §6 selection order: eh_role → eh_poc bus → carrier fallback."""
    return select_import_links_with_rule(n, overlay)[0]


def _poc_buses(n) -> set[str]:
    if n.buses is None or n.buses.empty or "eh_poc" not in n.buses.columns:
        return set()
    return {str(b) for b in n.buses.index if _flag(n.buses.at[b, "eh_poc"])}


def dsr_double_count_warnings(n, *, dsr_buses: list[str]) -> list[str]:
    """Mirror validation_service._check_dsr_coherence double-count messages."""
    out: list[str] = []
    su_buses: set[str] = set()
    if n.storage_units is not None and not n.storage_units.empty \
            and "bus" in n.storage_units.columns:
        su_buses = set(n.storage_units["bus"].astype(str))
    link_buses: set[str] = set()
    if n.links is not None and not n.links.empty:
        for col in ("bus0", "bus1"):
            if col in n.links.columns:
                link_buses |= set(n.links[col].astype(str))
    for bus in dsr_buses:
        bus = str(bus)
        if n.buses is None or bus not in n.buses.index:
            out.append(f"DSR opt-in bus '{bus}' does not exist on the network.")
            continue
        if bus in su_buses or bus in link_buses:
            out.append(
                f"Bus '{bus}' is opted into demand response but already "
                "hosts modelled flexibility (a storage unit or link). The "
                "DSR slack would count the same flexibility twice — either "
                "remove the opt-in or accept the deliberate double count."
            )
    return out


def solver_config_patch(pack: ArchetypePack) -> dict[str, Any]:
    """SolverConfig field patch derived from the pack (DSR stays off here)."""
    patch: dict[str, Any] = {}
    avail = pack.availability
    if avail.ens_cap_permyriad is not None:
        patch["ens_cap_permyriad"] = float(avail.ens_cap_permyriad)
    # DSR never silently global — callers must use solver_config_patch_with_preflight
    # with explicit buses to enable the tier.
    return patch


def solver_config_patch_with_preflight(
        pack: ArchetypePack, *, network,
        dsr_buses: list[str] | None = None,
        dsr_price_eur_per_mwh: float = 100.0,
        dsr_share_of_load: float = 0.1,
) -> tuple[dict[str, Any], list[str]]:
    """Patch + warnings. Enables DSR only when dsr_opt_in and buses provided."""
    patch = solver_config_patch(pack)
    warnings: list[str] = []
    if not pack.dsr_opt_in:
        return patch, warnings
    buses = [str(b) for b in (dsr_buses or [])]
    if not buses:
        warnings.append(
            "weak_flexible pack has dsr_opt_in=True but no dsr_buses were "
            "supplied — DSR stays OFF (never applied globally; double-count "
            "hazard on networks that already model flexibility)."
        )
        return patch, warnings
    warnings.extend(dsr_double_count_warnings(network, dsr_buses=buses))
    patch["dsr_price_eur_per_mwh"] = float(dsr_price_eur_per_mwh)
    patch["dsr_share_of_load"] = float(dsr_share_of_load)
    patch["dsr_buses"] = buses
    return patch, warnings


def _save_link_power(n, name: str) -> dict[str, float]:
    row: dict[str, object] = {
        "p_nom": float(n.links.at[name, "p_nom"]),
    }
    if "p_nom_max" in n.links.columns:
        try:
            row["p_nom_max"] = float(n.links.at[name, "p_nom_max"])
        except (TypeError, ValueError):
            pass
    if "p_max_pu" in n.links.columns:
        row["p_max_pu"] = float(n.links.at[name, "p_max_pu"])
    if "p_min_pu" in n.links.columns:
        row["p_min_pu"] = float(n.links.at[name, "p_min_pu"])
    return row


def _restore_link_power(n, name: str, saved: dict[str, float]) -> None:
    for col, val in saved.items():
        if col.startswith("t_"):
            attr = col[2:]
            ts = getattr(getattr(n, "links_t", None), attr, None)
            if ts is not None and name in getattr(ts, "columns", []):
                ts[name] = val
            continue
        if col in n.links.columns:
            n.links.at[name, col] = val


def apply_archetype_pack(n, pack: ArchetypePack) -> Callable[[], None]:
    return apply_archetype_pack_detailed(n, pack).undo


def apply_archetype_pack_detailed(n, pack: ArchetypePack) -> ApplyResult:
    """Apply import overlay (spec §6). Returns undo + metadata."""
    overlay = pack.import_overlay
    selected = select_import_links(n, overlay)
    warnings: list[str] = []

    if pack.archetype in ("weak_flexible", "off_grid") and not selected:
        raise ArchetypePackError(
            f"archetype {pack.archetype!r} requires identifiable import "
            "Links (eh_role=grid_import, eh_poc bus, or import_carriers); "
            "none matched"
        )

    saved: dict[str, dict[str, object]] = {
        name: _save_link_power(n, name) for name in selected
    }

    if pack.archetype == "strong_grid":
        # No-op mutation.
        pass
    elif pack.archetype == "off_grid":
        # Class-B discipline: zero flow via p_max_pu/p_min_pu, keep p_nom > 0
        # so validation's link_p_nom_invalid does not fire.
        for name in selected:
            if "p_max_pu" in n.links.columns:
                n.links.at[name, "p_max_pu"] = 0.0
            if "p_min_pu" in n.links.columns:
                n.links.at[name, "p_min_pu"] = 0.0
            # Time-series overlays if present.
            for attr in ("p_max_pu", "p_min_pu"):
                ts = getattr(getattr(n, "links_t", None), attr, None)
                if ts is not None and name in getattr(ts, "columns", []):
                    saved[name][f"t_{attr}"] = ts[name].copy()
                    ts[name] = 0.0
    elif pack.archetype == "weak_flexible":
        total = overlay.import_p_nom_mw
        if total is None:
            raise ArchetypePackError(
                "weak_flexible pack requires import_overlay.import_p_nom_mw")
        each = float(total) / float(len(selected))
        for name in selected:
            n.links.at[name, "p_nom"] = each
            if "p_nom_max" in n.links.columns:
                try:
                    cur = float(n.links.at[name, "p_nom_max"])
                    # Only tighten an existing finite max; leave NaN alone.
                    if cur == cur:  # not NaN
                        n.links.at[name, "p_nom_max"] = each
                except (TypeError, ValueError):
                    pass
        if overlay.import_energy_mwh_per_year is not None:
            warnings.append(
                "import_energy_mwh_per_year is reserved for a GlobalConstraint "
                "overlay; power-only apply used in Phase 1"
            )
    else:
        raise ArchetypePackError(f"unknown archetype {pack.archetype!r}")

    def undo() -> None:
        for name, vals in saved.items():
            if name in n.links.index:
                _restore_link_power(n, name, vals)

    return ApplyResult(
        undo=undo,
        pack_hash=pack_hash(pack),
        archetype=pack.archetype,
        import_links=list(selected),
        warnings=warnings,
    )


# ── P11: hub boundary for MC certification ──────────────────────────────────

class HubBoundaryError(ValueError):
    """The hub side of the import boundary cannot be established."""


# Synthetic MC unit standing in for an import Link that carries its own
# outage data (spec §4 amendment / Q7). Not a slack name or carrier, so the
# MC fleet builder treats it as an ordinary two-state electrical unit.
IMPORT_UNIT_PREFIX = "eh_import_unit_"
IMPORT_UNIT_CARRIER = "eh_grid_import"


def _branch_edges(n, *, exclude_links: set[str]) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for attr in ("lines", "transformers"):
        df = getattr(n, attr, None)
        if df is None or df.empty:
            continue
        for b0, b1 in zip(df["bus0"].astype(str), df["bus1"].astype(str)):
            edges.append((b0, b1))
    links = getattr(n, "links", None)
    if links is not None and not links.empty:
        bus_cols = [c for c in links.columns
                    if c.startswith("bus") and c[3:].isdigit()]
        for name in links.index:
            if str(name) in exclude_links:
                continue
            ends = [str(links.at[name, c]) for c in bus_cols
                    if str(links.at[name, c]).strip() not in ("", "nan", "None")]
            for other in ends[1:]:
                edges.append((ends[0], other))
    return edges


def _components(buses: list[str], edges) -> list[set[str]]:
    adj: dict[str, set[str]] = {b: set() for b in buses}
    for a, b in edges:
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
    seen: set[str] = set()
    comps: list[set[str]] = []
    for b in buses:
        if b in seen:
            continue
        stack, comp = [b], set()
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            stack.extend(adj[x] - comp)
        seen |= comp
        comps.append(comp)
    return comps


def _component_load(n, comp: set[str]) -> float:
    if n.loads is None or n.loads.empty or "bus" not in n.loads.columns:
        return 0.0
    names = [str(l) for l in n.loads.index if str(n.loads.at[l, "bus"]) in comp]
    if not names:
        return 0.0
    w = n.snapshot_weightings.generators
    total = 0.0
    p_set_t = getattr(n.loads_t, "p_set", None)
    for l in names:
        if p_set_t is not None and l in getattr(p_set_t, "columns", []):
            total += float((p_set_t[l].clip(lower=0) * w).sum())
        else:
            try:
                total += max(float(n.loads.at[l, "p_set"]), 0.0) * float(w.sum())
            except (TypeError, ValueError):
                pass
    return total


def _import_capacity_to_hub(n, link: str, far: set[str]) -> float:
    """MW an import Link can deliver INTO the hub (static + time-series pu)."""
    row = n.links.loc[link]
    p_nom = float(row.get("p_nom", 0.0) or 0.0)
    if bool(row.get("p_nom_extendable", False)):
        opt = row.get("p_nom_opt")
        try:
            if opt is not None and float(opt) == float(opt) and n.is_solved:
                p_nom = float(opt)
        except (TypeError, ValueError):
            pass

    def _pu(attr: str, default: float) -> float:
        ts = getattr(getattr(n, "links_t", None), attr, None)
        if ts is not None and link in getattr(ts, "columns", []):
            return float(ts[link].mean())
        try:
            return float(row.get(attr, default))
        except (TypeError, ValueError):
            return default

    # A two-state unit has ONE capacity: time-varying pu is averaged over the
    # horizon (neither its peak nor its trough stands for the whole study).
    if str(row.get("bus0")) in far:          # grid → hub: forward flow
        eff = float(row.get("efficiency", 1.0) or 1.0)
        return max(p_nom * _pu("p_max_pu", 1.0), 0.0) * eff
    # hub → grid: only a reverse flow (p_min_pu < 0) imports
    return max(-p_nom * _pu("p_min_pu", 0.0), 0.0)


def hub_boundary_copy(n, pack: ArchetypePack):
    """A copy of ``n`` holding only the hub side of the import boundary.

    The MC/COPT engine is copper-plate and network-free: without this, a
    generator on the grid side of an islanded import Link counts as local
    firm capacity (plan P11 / B1, R1). Returns ``(copy, info)``; raises
    ``HubBoundaryError`` when the hub side cannot be established.

    Hub side, by the §6 rule that selected the import Links:

    * ``eh_poc`` — components holding a PoC bus are the far side; the hub is
      every other component (none left → ambiguous).
    * ``eh_role`` — the component holding the ``eh_critical`` buses, else the
      one with the largest weighted load (tie or critical buses split across
      components → ambiguous).
    * ``carrier`` fallback — refused: it also matches internal hub Links.
    * no import Links — the whole network is the hub.

    Import Links carrying their OWN outage data enter as one two-state unit
    of their hub-side capacity (decision 6: import is firm only when its
    outages are modelled); the rest are excluded.
    """
    from services.adequacy.occurrence import resolve_outage_params

    links, rule = select_import_links_with_rule(n, pack.import_overlay)
    # A solved network with its solver model attached cannot be copied.
    model = getattr(n, "model", None)
    if model is not None and getattr(model, "solver_model", None) is not None:
        model.solver_model = None
    mc = n.copy()
    info: dict[str, Any] = {
        "rule": rule, "import_links": list(links), "hub_buses": [],
        "removed_buses": [], "removed_generators": [],
        "import_units": [], "excluded_import_links": [],
    }
    if rule == "none":
        info["hub_buses"] = sorted(str(b) for b in mc.buses.index)
        return mc, info
    if rule == "carrier":
        raise HubBoundaryError(
            "import Links were selected only by carrier (spec §6 rule 3), which "
            "also matches internal hub Links — the hub side cannot be "
            "established; tag the grid-side Link eh_role=grid_import or the "
            "grid-side bus eh_poc to certify")

    buses = [str(b) for b in mc.buses.index]
    comps = _components(buses, _branch_edges(mc, exclude_links=set(links)))
    comp_of = {b: i for i, c in enumerate(comps) for b in c}
    # Every selected import Link must SEPARATE its endpoints: if a parallel
    # Line/Link still connects both sides, removing the far side is
    # impossible and the copper-plate MC would count the grid as local.
    bus_cols = [c for c in mc.links.columns
                if c.startswith("bus") and c[3:].isdigit()]
    for link in links:
        ends = {str(mc.links.at[link, c]) for c in bus_cols
                if str(mc.links.at[link, c]).strip() not in ("", "nan", "None")}
        if len({comp_of.get(b) for b in ends}) <= 1:
            raise HubBoundaryError(
                f"import Link {link!r} does not separate the hub from the "
                "grid — another Line/Link still connects both sides; tag "
                "every grid-side connection eh_role=grid_import (or tag the "
                "grid-side bus eh_poc) so the grid can be excluded")
    crit = set()
    if "eh_critical" in mc.buses.columns:
        crit = {str(b) for b in mc.buses.index
                if _flag(mc.buses.at[b, "eh_critical"])}
    if rule == "eh_poc":
        poc = _poc_buses(mc)
        hub_comps = [c for c in comps if not (c & poc)]
        if not hub_comps:
            raise HubBoundaryError(
                "hub side ambiguous: every connected component holds an eh_poc "
                "bus — tag eh_poc on the grid-side bus only")
        hub = set().union(*hub_comps)
    else:  # eh_role
        crit_comps = [c for c in comps if c & crit]
        if len(crit_comps) == 1:
            hub = crit_comps[0]
        elif len(crit_comps) > 1:
            raise HubBoundaryError(
                "hub side ambiguous: eh_critical buses lie on both sides of the "
                "import Links — tag eh_poc on the grid-side bus")
        else:
            loads = sorted(((_component_load(mc, c), i)
                            for i, c in enumerate(comps)), reverse=True)
            if len(loads) > 1 and abs(loads[0][0] - loads[1][0]) <= 1e-9:
                raise HubBoundaryError(
                    "hub side ambiguous: no eh_critical bus and two components "
                    "carry the same load — tag eh_poc on the grid-side bus")
            hub = comps[loads[0][1]]

    far = set(buses) - hub
    # Sanity of the chosen side: a critical bus beyond the boundary, or a hub
    # that serves no load, means the tags point the wrong way — certifying
    # would sample the grid's fleet against nothing (gate probe P4).
    if crit & far:
        raise HubBoundaryError(
            "hub side inverted: eh_critical bus(es) "
            f"{sorted(crit & far)} lie beyond the import boundary — eh_poc "
            "must tag the GRID-side bus")
    if _component_load(mc, hub) <= 0:
        raise HubBoundaryError(
            "hub side has no load — the boundary tags probably point the "
            "wrong way (eh_poc must tag the GRID-side bus)")
    info["hub_buses"] = sorted(hub)
    info["removed_buses"] = sorted(far)

    # Import units (Q7) — computed on the pack-applied network, before removal.
    params = resolve_outage_params(mc, "links") if links else None
    units: list[dict[str, Any]] = []
    for link in links:
        src = params.at[link, "source"] if params is not None else "missing"
        if src != "asset":
            info["excluded_import_links"].append(
                {"link": link, "reason": "no outage data on the Link — import "
                 "is a planning limit, not firm capacity (decision 6)"})
            continue
        cap = _import_capacity_to_hub(mc, link, far)
        if cap <= 0:
            info["excluded_import_links"].append(
                {"link": link, "reason": "closed by the pack (0 MW into the hub)"})
            continue
        b0, b1 = str(mc.links.at[link, "bus0"]), str(mc.links.at[link, "bus1"])
        hub_bus = b1 if b0 in far else b0
        units.append({
            "link": link, "bus": hub_bus, "capacity_mw": cap,
            "rate": float(params.at[link, "rate"]),
            "basis": str(params.at[link, "basis"]),
            "mttr_hours": float(params.at[link, "mttr_hours"]),
        })

    def _drop(component: str, attr: str, mask_fn) -> list[str]:
        df = getattr(mc, attr, None)
        if df is None or df.empty:
            return []
        names = [str(i) for i in df.index if mask_fn(df, i)]
        if names:
            mc.remove(component, names)
        return names

    def _on_far(df, i) -> bool:
        return str(df.at[i, "bus"]) in far

    def _branch_far(df, i) -> bool:
        cols = [c for c in df.columns if c.startswith("bus") and c[3:].isdigit()]
        return any(str(df.at[i, c]) in far for c in cols)

    info["removed_generators"] = _drop("Generator", "generators", _on_far)
    _drop("Load", "loads", _on_far)
    _drop("StorageUnit", "storage_units", _on_far)
    _drop("Store", "stores", _on_far)
    _drop("Link", "links",
          lambda df, i: str(i) in set(links) or _branch_far(df, i))
    _drop("Line", "lines", _branch_far)
    _drop("Transformer", "transformers", _branch_far)
    if far:
        mc.remove("Bus", sorted(far))

    for u in units:
        name = f"{IMPORT_UNIT_PREFIX}{u['link']}"
        mc.add("Generator", name, bus=u["bus"], p_nom=u["capacity_mw"],
               p_nom_extendable=False, marginal_cost=0.0,
               carrier=IMPORT_UNIT_CARRIER)
        for col, key in (("outage_rate_value", "rate"),
                         ("outage_rate_basis", "basis"),
                         ("mttr_hours", "mttr_hours")):
            if col not in mc.generators.columns:
                mc.generators[col] = (float("nan") if col != "outage_rate_basis"
                                      else "")
            mc.generators.at[name, col] = u[key]
        info["import_units"].append({**u, "unit": name})
    return mc, info
