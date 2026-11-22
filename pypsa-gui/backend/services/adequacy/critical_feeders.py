"""
Must-stay-on buses behind internal feeders (asset-investment walkthrough
2026-11-17, issue 02).

The sequential MC and the COPT are single-area: ``hub_fleet_scope`` merges
every bus on the hub side of the import Link into one copper plate, so the
outage of an internal Link that is the only path to an ``eh_critical`` bus is
never drawn. On the Island Microgrid template the study certified 0.0 h/yr
while the hospital's only feeder (unavailability 0.01) cuts it off
0.01 × 8,760 = 87.6 h/yr.

This module finds those buses and states what the certification does with
them:

* ``critical_feeder_rows(n, scope)`` — topology only. Drop every internal Link
  that carries usable outage data, and every one without it that decides
  whether a critical bus keeps a path to supply; a component that then holds
  an ``eh_critical`` bus is a *pocket*. One row per Link feeding a pocket,
  with ``model``:

  - ``composed``: the pocket has no supply of its own, every feeder has
    usable data and comes straight from a supplied component. Its loss of
    supply while all its feeders are out is added to the certified LOLE
    (``compose``).
  - ``unsampled``: the pocket has units of its own, is fed through another
    pocket, or has a feeder without usable data. Its outages cannot be added
    in closed form. Not chained, the composed figure (units ignored, an
    unread rate taken as 1) is an upper bound (``bounded_verdict``, GK A
    F6'): at or under the goal it does not block a ``pass``; over it the
    verdict is ``inconclusive``. A chained pocket has no such bound and
    keeps a ``pass`` from being given.
  - ``no_outage_data``: a feeder without usable outage data (GK R2). Its
    rate is not known, so it is taken as 1: its pocket is bounded as above
    (``q`` None, ``upper_bound`` set unless chained). Never a ``fail`` and
    never a ``pass`` on the unread Link alone.

  A parallel Line, or a Link without data whose outage cuts no critical bus
  off, is not a feeder: no row.

* ``pocket_series(n, rows, weights, cfg)`` — per snapshot, the probability the
  pocket is cut off (``Π q_k`` over its feeders, independent, the stationary
  unavailability the MC sampler draws for each Link) gated on the pocket's
  load, and the expected energy lost.

* ``compose(...)`` — the certified LOLE with the pockets added, per period:
  ``L + P − L·P/H`` (union of independent events; the overlap term assumes
  the hub's shortfall hours are spread evenly, error ≤ ``L·P/H``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

COMPOSED = "composed"
UNSAMPLED = "unsampled"
NO_OUTAGE_DATA = "no_outage_data"

CAVEAT = (
    "must-stay-on buses fed by one path inside the hub: their loss of supply "
    "while every feeder is out is added to the expected shortfall in closed form "
    "(independent outages at each feeder's unavailability); the confidence "
    "interval covers the simulation only, not the outage-rate inputs; the "
    "hub simulation still counts the cut-off load as demand, so its own "
    "shortfall is conservative in those hours"
)
LOOP_CAVEAT = (
    "this view does not sample the feeders of must-stay-on buses inside the "
    "hub ({names}); the reliability study's certification does"
)


@dataclass
class Pocket:
    """One pocket: per-snapshot cut-off probability and load. ``bound``: the
    pocket has units of its own or a feeder without outage data, so its
    figure is an upper bound only and is not composed into the certified
    LOLE. ``no_data``: its feeders whose rate is not known (taken as 1);
    ``own_units``: it has supply of its own (left out of the bound)."""

    buses: list[str]
    feeders: list[str]
    u: float  # Π q over the feeders, q = 1 for a feeder without data
    load: np.ndarray = field(default_factory=lambda: np.zeros(0))
    bound: bool = False
    no_data: list[str] = field(default_factory=list)
    own_units: bool = False


def _cap(df, name: str, col: str) -> float:
    try:
        v = float(df.at[name, col])
    except (KeyError, TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _supplied_buses(n) -> set[str]:
    """Buses holding a generator, storage unit or store with any capacity."""
    out: set[str] = set()
    for comp, attrs in (
        ("generators", ("p_nom", "p_nom_opt")),
        ("storage_units", ("p_nom", "p_nom_opt")),
        ("stores", ("e_nom", "e_nom_opt")),
    ):
        df = getattr(n, comp, None)
        if df is None or df.empty or "bus" not in df.columns:
            continue
        ext = f"{attrs[0]}_extendable"
        for u in df.index:
            if any(_cap(df, str(u), a) > 0 for a in attrs if a in df.columns) or (
                ext in df.columns and bool(df.at[u, ext])
            ):
                out.add(str(df.at[u, "bus"]))
    return out


def _delivers_into(n, lk: str, side_bus: str) -> bool:
    """Can Link ``lk`` deliver power into ``side_bus`` (one of its ends)?"""
    ldf = n.links
    if str(ldf.at[lk, "bus1"]) == side_bus:
        return _cap(ldf, lk, "p_nom") > 0 or bool(
            ldf.at[lk, "p_nom_extendable"]
            if "p_nom_extendable" in ldf.columns
            else False
        )
    p_min = _cap(ldf, lk, "p_min_pu") if "p_min_pu" in ldf.columns else 0.0
    return p_min < 0 and _cap(ldf, lk, "p_nom") > 0


def _on_supply_paths(
    n,
    buses: list[str],
    skip: set[str],
    no_data: list[str],
    supplied: set[str],
    crit: set[str],
) -> dict[str, set[str]]:
    """For each no-data Link, the critical buses it lies on a simple path to
    from a supplied bus (GK R2'). Exact: subdivide the Link with a node z,
    join a source s* to every supplied bus, the critical bus c to a sink c*,
    and both to t. The Link is on a simple supplied-to-c path iff z has two
    node-disjoint paths to t (Menger): t's only neighbours are s* and c*, so
    one path ends through each, and the paths share no bus."""
    import networkx as nx

    from services.adequacy.eh_stages import _branch_edges

    inside = set(buses)
    nd = set(no_data)
    g = nx.Graph()
    g.add_nodes_from(inside)
    for a, b in _branch_edges(n, skip | nd):
        if a in inside and b in inside and a != b:
            g.add_edge(a, b)
    ldf = n.links
    for lk in no_data:
        a, b = str(ldf.at[lk, "bus0"]), str(ldf.at[lk, "bus1"])
        g.add_edge(("z", lk), a)
        g.add_edge(("z", lk), b)
    src, sink, t = ("s*",), ("c*",), ("t",)
    g.add_edges_from((src, s) for s in supplied)
    g.add_edges_from([(src, t), (sink, t)])
    out: dict[str, set[str]] = {lk: set() for lk in no_data}
    for c in sorted(crit):
        g.add_edge(sink, c)
        for lk in no_data:
            z = ("z", lk)
            if (
                nx.algorithms.connectivity.local_node_connectivity(g, z, t, cutoff=2)
                >= 2
            ):
                out[lk].add(c)
        g.remove_edge(sink, c)
    return out


def critical_feeder_rows(
    n, *, hub_buses: list[str], import_links: list[str]
) -> list[dict]:
    """The rows described in the module docstring, sorted by Link name."""
    from services.adequacy.eh_stages import _branch_edges, _components, _truthy
    from services.adequacy.occurrence import rate_is_usable, resolve_outage_params

    from services.adequacy.metrics import electrical_columns

    # Electrical buses only: a conversion Link from a fuel or heat bus is a
    # supplying unit the fleet walk already samples, not a feeder.
    buses = [str(b) for b in electrical_columns(n, [str(b) for b in hub_buses])]
    if not buses or "eh_critical" not in n.buses.columns:
        return []
    crit = {b for b in buses if _truthy(n.buses.at[b, "eh_critical"])}
    ldf = getattr(n, "links", None)
    if not crit or ldf is None or ldf.empty:
        return []
    inside = set(buses)
    skip = {str(x) for x in import_links}
    internal = [
        str(lk)
        for lk in ldf.index
        if str(lk) not in skip
        and str(ldf.at[lk, "bus0"]) in inside
        and str(ldf.at[lk, "bus1"]) in inside
    ]
    params = resolve_outage_params(n, "links")
    usable: dict[str, tuple[float, float]] = {}
    for lk in internal:
        occ = params.loc[lk]
        mttr = float(occ["mttr_hours"]) if occ["source"] != "missing" else float("nan")
        if (
            occ["source"] != "missing"
            and rate_is_usable(occ["rate"])
            and math.isfinite(mttr)
            and mttr > 0
        ):
            usable[lk] = (float(occ["rate"]), mttr)
    supplied = _supplied_buses(n) | {
        str(ldf.at[lk, end])
        for lk in skip
        if lk in ldf.index
        for end in ("bus0", "bus1")
    }

    def comps(cut: set[str]):
        c = _components(buses, _branch_edges(n, skip | cut))
        groups: dict[int, set[str]] = {}
        for b, i in c.items():
            groups.setdefault(i, set()).add(b)
        return c, groups

    # GK R2/R2': a Link without usable data is taken as out (q = 1), so it is
    # cut wherever it lies on a simple path from a supplied bus to a critical
    # one, whatever else is out (``_on_supply_paths``). One on no such path
    # (a dead end) is not cut.
    no_data = [lk for lk in internal if lk not in usable]
    on_path = _on_supply_paths(n, buses, skip, no_data, supplied & set(buses), crit)
    relevant = {lk for lk, hit in on_path.items() if hit}
    cut = set(usable) | relevant
    rows: list[dict] = []
    comp, groups = comps(cut)
    fed = {i for i, g in groups.items() if g & supplied}
    for i, g in sorted(groups.items(), key=lambda kv: sorted(kv[1])):
        if not (g & crit):
            continue
        feeders = [
            lk
            for lk in sorted(cut)
            if (str(ldf.at[lk, "bus0"]) in g) != (str(ldf.at[lk, "bus1"]) in g)
            and _delivers_into(
                n,
                lk,
                next(
                    b
                    for b in (str(ldf.at[lk, "bus0"]), str(ldf.at[lk, "bus1"]))
                    if b in g
                ),
            )
        ]
        if not feeders:
            continue
        # A feeder whose far side is never supplied (q = 1 upstream) is out
        # for good: it adds a factor 1, not a chain (GK R2'). "Supplied" is
        # judged with every relevant no-data Link out and without the pocket
        # itself: supply that reaches the far side only back through the
        # pocket cannot feed it (GK R2'').
        live = _components(
            [b for b in buses if b not in g], _branch_edges(n, skip | relevant)
        )
        live_fed = {live[b] for b in live if b in supplied}
        dead = {
            lk
            for lk in feeders
            if live[
                next(
                    b
                    for b in (str(ldf.at[lk, "bus0"]), str(ldf.at[lk, "bus1"]))
                    if b not in g
                )
            ]
            not in live_fed
        }
        far = {
            comp[
                next(
                    b
                    for b in (str(ldf.at[lk, "bus0"]), str(ldf.at[lk, "bus1"]))
                    if b not in g
                )
            ]
            for lk in feeders
            if lk not in dead
        }
        chained = not far <= fed
        own_units = i in fed
        # Every unread Link on a supply path to this pocket's critical buses,
        # its own feeders or further up (GK R2').
        unread = sorted(lk for lk in relevant if on_path[lk] & g)
        if chained:
            model, reason = (
                UNSAMPLED,
                (
                    "fed through another bus that has no supply of its own: "
                    "chained feeders are not composed"
                ),
            )
        elif own_units:
            model, reason = (
                UNSAMPLED,
                (
                    "the must-stay-on bus has units of its own: what it can carry "
                    "while its feeders are out is not simulated"
                ),
            )
        elif unread:
            model, reason = (
                UNSAMPLED,
                (
                    f"{', '.join(unread)} beside it has no usable outage data: "
                    "its outage rate is not known and is taken as certain, so "
                    "the pocket's figure is an upper bound"
                ),
            )
        else:
            model, reason = COMPOSED, None
        u = float(
            np.prod(
                [
                    usable[lk][0] if lk in usable and lk not in dead else 1.0
                    for lk in feeders
                ]
            )
        )
        for lk in sorted(set(feeders) | set(unread)):
            read = lk in usable
            rows.append(
                {
                    "name": lk,
                    "model": model if read else NO_OUTAGE_DATA,
                    "buses": sorted(g & crit),
                    "pocket_buses": sorted(g),
                    "feeders": list(feeders),
                    "no_data_feeders": list(unread),
                    "own_units": own_units,
                    "q": usable[lk][0] if read else None,
                    "mttr_hours": usable[lk][1] if read else None,
                    "pocket_unavailability": u,
                    "reason": reason
                    if read
                    else (
                        "no usable outage data: its outage rate is not known "
                        "and is taken as certain for the upper bound"
                        + ("; " + reason if chained else "")
                    ),
                    "chained": chained,
                    "upper_bound": model == UNSAMPLED and not chained,
                    "lole_h": None,
                    "eue_mwh": None,
                }
            )
    return sorted(rows, key=lambda r: r["name"])


def pocket_series(
    n, rows: list[dict], *, cfg=None, demand_scaled_in_place: bool = False
) -> list[Pocket]:
    """One ``Pocket`` per composed pocket and per bounded one (``bound``),
    with its load per snapshot read on the MC's own demand basis
    (``demand.demand_frame_for``)."""
    from services.adequacy.demand import demand_frame_for

    pockets: dict[tuple, Pocket] = {}
    for r in rows:
        if r["model"] != COMPOSED and not r.get("upper_bound"):
            continue
        key = tuple(r["pocket_buses"])
        if key not in pockets:
            pockets[key] = Pocket(
                buses=list(r["buses"]),
                feeders=list(r["feeders"]),
                u=float(r["pocket_unavailability"]),
                bound=r["model"] != COMPOSED,
                no_data=list(r.get("no_data_feeders") or []),
                own_units=bool(r.get("own_units")),
            )
    if not pockets:
        return []
    snapshots = n.snapshots
    p_set_t = demand_frame_for(n, cfg, demand_scaled_in_place=demand_scaled_in_place)
    loads = n.loads
    for key, p in pockets.items():
        load = np.zeros(len(snapshots))
        for l in loads.index:
            if str(loads.at[l, "bus"]) not in key:
                continue
            if p_set_t is not None and l in getattr(p_set_t, "columns", []):
                load += p_set_t[l].reindex(snapshots).fillna(0.0).to_numpy(float)
            else:
                load += _cap(loads, str(l), "p_set")
        p.load = load
    return list(pockets.values())


def pocket_terms(
    pockets: list[Pocket], w: np.ndarray, sl=slice(None)
) -> tuple[float, float]:
    """(P, E): expected weighted hours with any pocket cut off, and the
    expected energy the pockets lose, over the snapshots in ``sl``."""
    none_cut = np.ones_like(w[sl], dtype=float)
    energy = 0.0
    for p in pockets:
        on = (p.load[sl] > 0).astype(float)
        none_cut *= 1.0 - p.u * on
        energy += float(np.sum(p.u * w[sl] * np.clip(p.load[sl], 0.0, None)))
    return float(np.sum((1.0 - none_cut) * w[sl])), energy


def _union(lole: float, p: float, h: float) -> float:
    return lole + p - (lole * p / h if h > 0 else 0.0)


def compose(res: dict, pockets: list[Pocket], *, weights: np.ndarray, periods) -> dict:
    """``res`` (an MC result) with the pockets added; a new dict. Per period
    when the MC reports one (``by_period`` keyed by the period labels), the
    totals then being the sums; on the whole horizon otherwise. Bounded
    pockets are left out (``bounded_verdict``)."""
    pockets = [p for p in pockets if not p.bound]
    if not pockets:
        return res
    out = dict(res)
    w = np.asarray(weights, dtype=float)
    by_period = res.get("by_period") or {}
    blocks = [(str(lab), slice(int(a), int(b))) for lab, a, b in (periods or ())]
    # Every period the MC reports, a single one too, so it matches the total.
    use_periods = bool(blocks) and all(lab in by_period for lab, _ in blocks)
    if not use_periods:
        blocks = [("_all", slice(None))]
        by_period_in = {
            "_all": {
                "lole_hours": res["lole_hours"],
                "lole_ci": list(res["lole_ci"]),
                "eue_mwh": res.get("eue_mwh"),
            }
        }
    else:
        by_period_in = by_period
    tot_l = tot_lo = tot_hi = tot_e = tot_p = tot_pe = overlap = 0.0
    new_by: dict = {}
    for lab, sl in blocks:
        cur = by_period_in[lab]
        p, e = pocket_terms(pockets, w, sl)
        h = float(np.sum(w[sl]))
        lole = _union(float(cur["lole_hours"]), p, h)
        lo, hi = (_union(float(x), p, h) for x in cur["lole_ci"])
        eue = float(cur.get("eue_mwh") or 0.0) + e
        overlap += float(cur.get("eue_mwh") or 0.0) * (p / h if h > 0 else 0.0)
        tot_l += lole
        tot_lo += lo
        tot_hi += hi
        tot_e += eue
        tot_p += p
        tot_pe += e
        if lab != "_all":
            new_by[lab] = {
                **by_period[lab],
                "lole_hours": lole,
                "lole_ci": [lo, hi],
                "eue_mwh": eue,
            }
            # The interval moves with the value, as the total's does (GK R4).
            if cur.get("eue_ci"):
                new_by[lab]["eue_ci"] = [float(x) + e for x in cur["eue_ci"]]
    out["lole_hours"] = tot_l
    out["lole_ci"] = [tot_lo, tot_hi]
    out["eue_mwh"] = tot_e
    if res.get("eue_ci"):
        out["eue_ci"] = [float(x) + tot_pe for x in res["eue_ci"]]
    if new_by:
        out["by_period"] = {**by_period, **new_by}
    out["critical_pocket_lole_hours"] = tot_p
    out["critical_pocket_eue_mwh"] = tot_pe
    # F3: hub shortfall in hours a pocket is cut off can count the pocket's
    # load twice; this is the expected amount, stated not subtracted.
    out["critical_pocket_eue_overlap_bound_mwh"] = overlap
    return out


def bound_hours(pockets: list[Pocket], weights: np.ndarray) -> float | None:
    """Expected weighted hours with any bounded pocket cut off over the
    horizon (their union), the upper bound a certified LOLE must carry in
    every reader (GK R1); None with no bounded pocket."""
    bounded = [p for p in pockets if p.bound]
    if not bounded:
        return None
    return pocket_terms(bounded, np.asarray(weights, dtype=float))[0]


def _why_bound(p: Pocket) -> str:
    """Why a bounded pocket's figure is an upper bound, in words."""
    bus = ", ".join(p.buses)
    why = []
    if p.no_data:
        why.append(
            f"{', '.join(p.no_data)}'s outage rate is not known and is taken as certain"
        )
    if p.own_units or not p.no_data:
        why.append(f"{bus}'s own units not counted")
    return "upper bound: " + "; ".join(why)


def bounded_verdict(
    verdict: str | None,
    note: str | None,
    *,
    hi: float,
    pockets: list[Pocket],
    rows: list[dict],
    weights: np.ndarray,
    target_h: float | None,
    years: float,
) -> tuple[str | None, str | None, dict | None]:
    """GK A F6': a ``pass`` stands only if the pockets this certification
    cannot simulate cannot break it. A bounded pocket's figure (its units
    ignored, an unread rate taken as 1) added to the CI upper bound must
    stay at or under the target; a chained pocket has no bound. Otherwise
    ``inconclusive`` — never ``fail`` — naming the pocket, its bound and what
    clears it. The third item, set only when it withholds the pass, says why
    in fields the review and the Guided headline read (``feeder_bound``)."""
    if verdict != "pass" or target_h is None:
        return verdict, note, None
    chained = sorted({str(r["name"]) for r in rows if r.get("chained")})
    bounded = [p for p in pockets if p.bound]
    per_year = years if years > 0 else 1.0
    parts = []
    held: dict = {"pockets": [], "chained": chained}
    p_h = bound_hours(bounded, weights)
    if p_h is not None and hi + p_h > target_h:
        for p in bounded:
            b_h, _ = pocket_terms([p], np.asarray(weights, dtype=float))
            bus = ", ".join(p.buses)
            held["pockets"].append(
                {
                    "buses": list(p.buses),
                    "feeders": list(p.feeders),
                    "bound_h_per_year": b_h / per_year,
                    "no_data": list(p.no_data),
                    "own_units": bool(p.own_units),
                }
            )
            parts.append(
                f"{bus} behind {', '.join(p.feeders)} could be short up to "
                f"{b_h / per_year:.3g} h/yr ({_why_bound(p)})"
            )
        unread = sorted({f for p in bounded for f in p.no_data})
        buses = ", ".join(b for p in bounded for b in p.buses)
        clears = ([f"outage data for {', '.join(unread)}"] if unread else []) + [
            "a second feeder",
            "a lower outage rate on the feeder",
            f"simulating {buses} as its own area (the two-area treatment, issue 09)",
        ]
        settle = ", ".join(clears[:-1]) + f" or {clears[-1]}"
        parts[-1] += (
            f", which with the rest of the hub exceeds the goal of "
            f"{target_h / per_year:g} h/yr. "
            + settle[0].upper()
            + settle[1:]
            + " would settle it"
        )
    if chained:
        parts.append(
            f"{', '.join(chained)} feeds a must-stay-on bus through another "
            "bus with no supply of its own, which this certification cannot "
            "bound. A feeder straight from a bus with supply would settle it"
        )
    if not parts:
        return verdict, note, None
    held["target_h_per_year"] = target_h / per_year
    return "inconclusive", "; ".join(parts) + ".", held


def certification_note(rows: list[dict]) -> str | None:
    """One sentence per kind of row, naming the Links; None with no rows."""
    if not rows:
        return None
    by: dict[str, list[str]] = {}
    bounds: dict[str, list[str]] = {}
    for r in rows:
        by.setdefault(r["model"], []).append(str(r["name"]))
        if r.get("upper_bound") and r.get("lole_h") is not None:
            bus = ", ".join(r["buses"])
            why = (
                "upper bound: its outage rate taken as certain"
                if r["model"] == NO_OUTAGE_DATA
                else f"upper bound: {bus}'s own units not counted"
            )
            bounds.setdefault(r["model"], []).append(
                f"{r['name']} at most {float(r['lole_h']):.3g} h/yr ({why})"
            )
    parts = []
    if by.get(COMPOSED):
        parts.append(f"added in closed form for {', '.join(by[COMPOSED])}: " + CAVEAT)
    if by.get(UNSAMPLED):
        b = bounds.get(UNSAMPLED)
        parts.append(
            "not simulated for "
            + ", ".join(by[UNSAMPLED])
            + ": the must-stay-on bus it feeds has units of its own, "
            "is fed through another bus or beside a feeder without outage "
            "data, so what it can carry while the feeder is out is not "
            "modelled" + (", bounded: " + ", ".join(b) if b else "")
        )
    if by.get(NO_OUTAGE_DATA):
        b = bounds.get(NO_OUTAGE_DATA)
        parts.append(
            ", ".join(by[NO_OUTAGE_DATA]) + " feeds a must-stay-on bus but "
            "has no usable outage data: its outage rate is not known, so it "
            "is taken as certain for the upper bound"
            + (", bounded: " + ", ".join(b) if b else "")
        )
    return "Must-stay-on feeders: " + "; ".join(parts) + "."


def loop_note(rows: list[dict]) -> str | None:
    """The caveat for the views that keep the copper plate (the coupling and
    margin loops, Expert's Monte Carlo), naming the Links; None with none."""
    names = [
        str(r["name"])
        for r in rows
        if r["model"] in (COMPOSED, UNSAMPLED)
        or r.get("upper_bound")
        or r.get("chained")
    ]
    return LOOP_CAVEAT.format(names=", ".join(names)) if names else None
