"""
``suggest_eh_setup`` — the Energy Hub tags an untagged network needs
(guided-mode spec §6.3, P25).

A READ tool: it proposes the grid import Link (``eh_role = grid_import``),
the point-of-connection bus (``eh_poc``) and the critical buses
(``eh_critical``) with a reason each, as ready-to-run ``update_component`` /
``bulk_update_components`` actions — and lists the thermal-like units without
outage data as questions for the user (values only the user can give, so no
action). It NEVER writes: the assistant presents the suggestions and the write
tools ask the user to confirm the ones they pick.

Pure and deterministic: no solve, no mutation of ``n`` (pinned by
``tests/test_eh_setup_suggest.py``).
"""
from __future__ import annotations

import math
import re
from collections import defaultdict, deque
from typing import Any

from models.energy_hub import EH_CONVERSION_ROLES, EH_INTERNAL_ROLES

_IMPORT_KW = re.compile(r"(grid|import|utility|mainland|tie|poc)", re.I)
_CRITICAL_KW = re.compile(
    r"(crit|hospital|clinic|it[_-]|data|process|essential|emerg)", re.I)
# A supply-side bus must cover this share of the site's summed peak demand.
_SUPPLY_SHARE = 0.8
# Candidates scoring within this share of the best are all proposed.
_BEST_SHARE = 0.9

_ARCHETYPE_IMPORT_NOTE = {
    "strong_grid": " The strong_grid study imports through it freely.",
    "weak_flexible": " The weak_flexible study caps what it can import.",
    "off_grid": " The off_grid study islands it, so the site must run without it.",
}


def _f(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if math.isfinite(x) else 0.0


def _flag(df, name: str, col: str) -> bool:
    if col not in df.columns:
        return False
    v = df.at[name, col]
    try:
        return bool(v) and not (isinstance(v, float) and math.isnan(v))
    except (TypeError, ValueError):
        return False


def _role(n, link: str) -> str:
    if "eh_role" not in n.links.columns:
        return ""
    v = n.links.at[link, "eh_role"]
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)


def _peaks(n) -> dict[str, float]:
    """peak(load) = max of ``loads_t.p_set[name]`` if present, else ``p_set``."""
    ts = n.loads_t.p_set if hasattr(n.loads_t, "p_set") else None
    out: dict[str, float] = {}
    for name in n.loads.index:
        if ts is not None and name in ts.columns and len(ts):
            out[name] = _f(ts[name].max())
        else:
            out[name] = _f(n.loads.at[name, "p_set"]) if "p_set" in n.loads.columns else 0.0
    return out


def _supply(n) -> dict[str, float]:
    """Σ p_nom of the cheaper half (marginal_cost ≤ median) of the generators
    per bus — any generator when there are fewer than three."""
    from services.adequacy.eh_readiness import _NOT_EQUIPMENT_KW

    gens = n.generators
    out: dict[str, float] = defaultdict(float)
    # A VOLL slack / load-shedding sink / dump is a modelling device, not
    # supply (the same exclusion as the outage rule, P24-BE gate N4).
    if "carrier" in gens.columns:
        gens = gens[[not any(k in str(c).lower() for k in _NOT_EQUIPMENT_KW)
                     for c in gens["carrier"]]]
    if gens.empty:
        return out
    mc = gens["marginal_cost"].map(_f) if "marginal_cost" in gens.columns else None
    cheap = gens.index if (mc is None or len(gens) < 3) else gens.index[mc <= mc.median()]
    for g in cheap:
        out[str(gens.at[g, "bus"])] += _f(gens.at[g, "p_nom"])
    return out


def _edges(n) -> list[tuple[str, str, str]]:
    """Undirected branches: (bus0, bus1, "<Class>:<name>")."""
    out = []
    for cls, attr in (("Line", "lines"), ("Transformer", "transformers"),
                      ("Link", "links")):
        df = getattr(n, attr)
        for name in df.index:
            out.append((str(df.at[name, "bus0"]), str(df.at[name, "bus1"]),
                        f"{cls}:{name}"))
    return out


def _reaches_load(adj, start: str, banned: str, load_buses: set[str]) -> bool:
    seen = {start}
    todo = deque([start])
    while todo:
        b = todo.popleft()
        if b in load_buses:
            return True
        for nb, eid in adj.get(b, ()):
            if eid != banned and nb not in seen:
                seen.add(nb)
                todo.append(nb)
    return False


def _update(cls: str, name: str, proposed: dict, effect: str) -> dict:
    return {"tool": "update_component",
            "args": {"component_class": cls, "name": name, "attrs": dict(proposed)},
            "effect": effect}


def _effect(kind: str, names: list[str]) -> str:
    quoted = ", ".join(f"'{x}'" for x in names)
    if kind == "import_link":
        return f"tag Link {quoted} as the grid import (eh_role = grid_import)"
    if kind == "poc_bus":
        return f"mark Bus {quoted} as the point of connection (eh_poc = true)"
    plural = "Buses" if len(names) > 1 else "Bus"
    return f"mark {plural} {quoted} as critical load that must stay on (eh_critical = true)"


def _empty(status: str, n=None) -> dict[str, Any]:
    count = (lambda a: int(len(getattr(n, a)))) if n is not None else (lambda a: 0)
    return {"status": status,
            "network": {"buses": count("buses"), "links": count("links"),
                        "loads": count("loads"), "generators": count("generators")},
            "already": {"import_links": [], "poc_buses": [], "critical_buses": []},
            "suggestions": [], "actions": [], "notes": []}


def suggest_eh_setup(n, archetype: str | None = None) -> dict[str, Any]:
    """Suggested Energy Hub tags for ``n`` (never applied). ``archetype``
    only words the reasons."""
    if n is None or len(n.buses) == 0:
        out = _empty("no_network", n)
        out["notes"].append("No network is loaded — open or build one first.")
        return out
    out = _empty("ok", n)
    buses, links = n.buses, n.links

    already_import = [str(l) for l in links.index if _role(n, l) == "grid_import"]
    already_poc = [str(b) for b in buses.index if _flag(buses, b, "eh_poc")]
    already_crit = [str(b) for b in buses.index if _flag(buses, b, "eh_critical")]
    out["already"] = {"import_links": already_import, "poc_buses": already_poc,
                      "critical_buses": already_crit}

    peaks = _peaks(n)
    total_peak = sum(peaks.values())
    bus_peak: dict[str, float] = defaultdict(float)
    for name, p in peaks.items():
        bus_peak[str(n.loads.at[name, "bus"])] += p
    load_buses = set(bus_peak)
    supply = _supply(n)
    adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for b0, b1, eid in _edges(n):
        adj[b0].append((b1, eid))
        adj[b1].append((b0, eid))
    gens_at: dict[str, list[str]] = defaultdict(list)
    for g in n.generators.index:
        gc = n.generators.at[g, "carrier"] if "carrier" in n.generators.columns else ""
        gens_at[str(n.generators.at[g, "bus"])].append(f"{g} {gc}")

    suggestions: list[dict[str, Any]] = []

    # ── import link ────────────────────────────────────────────────────────
    # Only while none is tagged: a tagged network already has its grid
    # connection, and a second "import" (e.g. a site transformer behind it)
    # would change what every pack applies to. A Link the user gave a
    # conversion role is theirs and never proposed.
    supply_side: dict[str, str] = {}
    proposed_imports: list[str] = []
    if not already_import and total_peak > 0:
        scored: list[tuple[float, str, str, str, float, bool]] = []
        skip = set(EH_CONVERSION_ROLES) | set(EH_INTERNAL_ROLES)
        for l in links.index:
            if _role(n, l) in skip:
                continue
            b0, b1 = str(links.at[l, "bus0"]), str(links.at[l, "bus1"])
            carrier = str(links.at[l, "carrier"]) if "carrier" in links.columns else ""
            best = None
            for s, d in ((b0, b1), (b1, b0)):
                if supply.get(s, 0.0) < _SUPPLY_SHARE * total_peak:
                    continue
                if not _reaches_load(adj, d, f"Link:{l}", load_buses):
                    continue
                ratio = supply[s] / total_peak
                text = " ".join([s, str(l), carrier, *gens_at.get(s, [])])
                match = bool(_IMPORT_KW.search(text))
                score = ratio + (1.0 if match else 0.0)
                if best is None or score > best[0]:
                    best = (score, str(l), s, d, ratio, match)
            if best:
                scored.append(best)
        if scored:
            top = max(x[0] for x in scored)
            for score, l, s, d, ratio, match in scored:
                if score < _BEST_SHARE * top:
                    continue
                supply_side[l] = s
                proposed_imports.append(l)
                reason = (
                    f"Bus '{s}' holds {supply[s]:.0f} MW of supply — "
                    f"{ratio:.1f}× the site's peak demand of {total_peak:.0f} MW — "
                    f"and the other side, '{d}', serves the site's loads"
                    + ("; the names point to a grid connection" if match else "")
                    + "." + _ARCHETYPE_IMPORT_NOTE.get(archetype or "", ""))
                proposed = {"eh_role": "grid_import"}
                suggestions.append({
                    "kind": "import_link", "component_class": "Link", "name": l,
                    "current": {"eh_role": _role(n, l)}, "proposed": proposed,
                    "reason": reason, "confidence": "high" if match else "medium",
                    "action": _update("Link", l, proposed, _effect("import_link", [l]))})

    # ── point of connection ────────────────────────────────────────────────
    for l in already_import:
        b0, b1 = str(links.at[l, "bus0"]), str(links.at[l, "bus1"])
        supply_side.setdefault(l, b0 if supply.get(b0, 0.0) >= supply.get(b1, 0.0) else b1)
    poc: list[tuple[str, str, str]] = []
    if supply_side:
        for l, b in supply_side.items():
            if b not in already_poc and b not in [p[0] for p in poc]:
                poc.append((b, "high",
                            f"Bus '{b}' is the grid side of the import link '{l}'."))
    elif not already_poc:
        free = [(v, b) for b, v in supply.items() if v > 0 and b not in load_buses]
        if free:
            v, b = max(free)
            poc.append((b, "low",
                        f"No grid import link was found; '{b}' holds the most supply "
                        f"({v:.0f} MW) and no load, so it may be where the grid connects."))
    for b, conf, reason in poc:
        proposed = {"eh_poc": True}
        suggestions.append({
            "kind": "poc_bus", "component_class": "Bus", "name": b,
            "current": {"eh_poc": _flag(buses, b, "eh_poc")}, "proposed": proposed,
            "reason": reason, "confidence": conf,
            "action": _update("Bus", b, proposed, _effect("poc_bus", [b]))})
    poc_buses = set(already_poc) | {p[0] for p in poc}

    # ── critical buses ─────────────────────────────────────────────────────
    matched: dict[str, str] = {}
    for name in n.loads.index:
        b = str(n.loads.at[name, "bus"])
        lc = str(n.loads.at[name, "carrier"]) if "carrier" in n.loads.columns else ""
        for label, text in (("bus", b), ("load", str(name)), ("load carrier", lc)):
            if _CRITICAL_KW.search(text) and b not in matched:
                matched[b] = f"its {label} name '{text}'"
    crit: list[tuple[str, str, str]] = []
    for b in load_buses_in_order(n, bus_peak):
        if b in matched and b not in already_crit:
            crit.append((b, "high",
                         f"Bus '{b}' carries load that must stay on — {matched[b]} "
                         "points to critical demand."))
    if not matched and not already_crit:
        rest = [(bus_peak[b], b) for b in load_buses_in_order(n, bus_peak)
                if b not in poc_buses]
        if rest:
            v, b = max(rest)
            crit.append((b, "medium",
                         f"No load is named as critical; '{b}' has the largest peak "
                         f"demand ({v:.1f} MW). Confirm it is the load that must stay on."))
    for b, conf, reason in crit:
        proposed = {"eh_critical": True}
        suggestions.append({
            "kind": "critical_bus", "component_class": "Bus", "name": b,
            "current": {"eh_critical": _flag(buses, b, "eh_critical")},
            "proposed": proposed, "reason": reason, "confidence": conf,
            "action": _update("Bus", b, proposed, _effect("critical_bus", [b]))})

    # ── outage data (a question for the user — never an action) ────────────
    from services.adequacy.eh_readiness import outage_scan
    from services.adequacy.occurrence import resolve_outage_params

    _by_class, missing = outage_scan(n, already_import + proposed_imports)
    attr = {"Generator": "generators", "Link": "links", "StorageUnit": "storage_units"}
    params = {c: resolve_outage_params(n, a) for c, a in attr.items()
              if any(m["class"] == c for m in missing)}
    for m in missing:
        cls, name = m["class"], m["name"]
        p = params[cls]

        def num(col):
            v = _f(p.at[name, col]) if name in p.index else 0.0
            raw = p.at[name, col] if name in p.index else None
            try:
                return v if raw is not None and math.isfinite(float(raw)) else None
            except (TypeError, ValueError):
                return None
        reason = (f"{cls} '{name}' has no usable outage data: set outage_rate_value "
                  "(forced-outage rate) and mttr_hours (repair time) — values only "
                  "you can give.")
        suggestions.append({
            "kind": "outage_data", "component_class": cls, "name": name,
            "current": {"outage_rate_value": num("rate"), "mttr_hours": num("mttr_hours")},
            "proposed": None, "reason": reason, "confidence": "high", "action": None})
        out["notes"].append(
            f"{cls} '{name}': no outage data — ask the user for outage_rate_value "
            "and mttr_hours.")

    # ── the deduplicated, ready-to-run list ────────────────────────────────
    groups: dict[tuple, list[dict]] = {}
    for s in suggestions:
        if s["action"] is None:
            continue
        key = (s["component_class"], tuple(sorted(s["proposed"].items())))
        groups.setdefault(key, []).append(s)
    actions = []
    for (cls, items), group in groups.items():
        if len(group) >= 2:
            names = [s["name"] for s in group]
            actions.append({
                "tool": "bulk_update_components",
                "args": {"component_class": cls, "names": names, "updates": dict(items)},
                "effect": _effect(group[0]["kind"], names)})
        else:
            actions.append(group[0]["action"])
    out["suggestions"] = suggestions
    out["actions"] = actions
    if not suggestions:
        out["notes"].append("Nothing to suggest: the grid import, point of "
                            "connection and critical load are already tagged.")
    return out


def load_buses_in_order(n, bus_peak: dict[str, float]) -> list[str]:
    """Buses carrying a Load, in the network's bus order (deterministic)."""
    return [str(b) for b in n.buses.index if str(b) in bus_peak]
