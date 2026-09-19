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


def select_import_links(n, overlay: ImportOverlaySpec) -> list[str]:
    """Spec §6 selection order: eh_role → eh_poc bus → carrier fallback."""
    if n.links is None or n.links.empty:
        return []

    links = n.links
    # 1) eh_role == grid_import
    if "eh_role" in links.columns:
        role_hits = [
            str(i) for i in links.index
            if str(links.at[i, "eh_role"]) == "grid_import"
        ]
        if role_hits:
            return role_hits

    # 2) either endpoint bus tagged eh_poc
    poc_buses: set[str] = set()
    if n.buses is not None and not n.buses.empty and "eh_poc" in n.buses.columns:
        for b in n.buses.index:
            val = n.buses.at[b, "eh_poc"]
            if val is True or str(val).lower() in ("true", "1", "yes"):
                poc_buses.add(str(b))
    if poc_buses:
        hits = []
        for i in links.index:
            b0 = str(links.at[i, "bus0"]) if "bus0" in links.columns else ""
            b1 = str(links.at[i, "bus1"]) if "bus1" in links.columns else ""
            if b0 in poc_buses or b1 in poc_buses:
                hits.append(str(i))
        if hits:
            return hits

    # 3) carrier ∈ import_carriers
    carriers = {str(c) for c in (overlay.import_carriers or [])}
    if carriers and "carrier" in links.columns:
        hits = [
            str(i) for i in links.index
            if str(links.at[i, "carrier"]) in carriers
        ]
        if hits:
            return hits

    return []


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
