"""
The adequacy STANDARDS the solver enforces and reports — the energy cap
(``ens_cap_permyriad`` / zone ceiling) and the firm-capacity planning reserve
margin — as linopy extra-functionality wrappers, plus the margin's own facts
payload.

Carved out of ``services/solver_service.py`` in the same spirit as the
backend decomposition (spec `2026-09-04-backend-god-file-decomposition-design`):
this is solver-layer code, it is imported back into ``solver_service`` as a
re-export so every existing caller and test keeps its import path, and it
never imports back from there. What it does import from the solver layer is
``_canonical_load_carrier_key`` — the load-carrier canonicalisation the
scalers and this module must agree on — which lives in
``services/solver/assumptions.py``.

These four are the FMEA/adequacy phases' contribution to the solver layer:

* ``_prm_margin`` — the config's margin as a number, with "no standard"
  spelled one way;
* ``_wrap_with_reserve_margin`` — the firm-capacity constraint, per active
  investment period;
* ``_wrap_with_ens_cap`` — the energy-not-served ceiling, system-wide and
  per zone;
* ``reserve_margin_facts`` — what the standard required and what met it,
  read from the config and the network alone, because the LP cannot answer
  it.
"""
from __future__ import annotations

import logging
import math

import pandas as pd
import pypsa  # noqa: F401  (string annotations below)

from services import period_utils as _period_utils
from services.adequacy.window import snapshot_label as _snapshot_label
from services.solver.assumptions import _canonical_load_carrier_key
from services.solver.runtime import _safe_log

logger = logging.getLogger(__name__)


def _prm_margin(cfg) -> float | None:
    """The configured reserve margin, or None when no standard is asked for.

    ``<= 0``/non-finite reads as "no margin" in exactly one place so the
    wrapper, the preflight (§3) and the report (§4) can never disagree about
    whether a standard is in force.
    """
    try:
        margin = getattr(cfg, "reserve_margin", None)
        margin = float(margin) if margin is not None else None
    except (TypeError, ValueError):
        return None
    if margin is None or not math.isfinite(margin) or margin <= 0:
        return None
    return margin


def _wrap_with_reserve_margin(network: "pypsa.Network", user_fn, cfg, log_queue=None):
    """
    Compose the extra_functionality callback with the FIRM-CAPACITY standard
    (planning reserve margin — Phase 8 spec §2).

    Per active investment period P, ONE constraint:

        Σ d_g·P_g (extendable ⇒ the LP variable)
      + Σ d_g·p_nom_g (fixed ⇒ a constant)
      + storage terms                    ≥   (1 + m) · peak_P

    where `m = cfg.reserve_margin` (a fraction; 0.15 == 15 %) and `d` is the
    derating factor built from the SAME occurrence chain the COPT and the MC
    read. Returns ``user_fn`` unchanged when no margin is set, so the LP stays
    untouched for runs that don't need it.

    Five details in here are load-bearing, each because getting it wrong is
    silent and changes the built plan:

    1. **Classification comes from the walk; capacity never does.**
       ``copt._membership_walk(..., keep_zero_capacity=True)`` decides scope,
       ``source`` and ``basis``. Its capacity number is ``solved_capacity``,
       which reads ``p_nom_opt`` — and this wrapper runs at LP *build* time,
       where every extendable's ``p_nom_opt`` is 0.0. Taking capacity from
       there would drop the unbuilt peaker: the exact asset the margin exists
       to force into being. Extendable ⇒ the ``*-p_nom`` variable; fixed ⇒
       ``p_nom``. ``keep_zero_capacity=True`` is what keeps the peaker in the
       walk at all.

    2. **``Generator-p_nom`` is ONE horizon-wide variable** with coords
       ``(name,)`` — there is no period dimension. The variable for a
       ``build_year=2040`` extendable EXISTS while the 2030 constraint is
       built, so both sides are masked with
       ``n.components.<comp>.get_active_assets(P)``; otherwise a 2040 vintage
       satisfies a 2030 standard. When the active EXTENDABLE set is identical
       in every period the per-period constraints share one variable and the
       system degenerates to a single horizon-wide standard at
       ``max_P peak_P`` — recorded as ``horizon_wide`` so the panel can say
       so instead of claiming a per-period standard the LP cannot express.

    3. **Coord membership before ``.sel``.** ``v.sel(name=X)`` raises
       ``KeyError`` when X is absent from the variable's coords, and PyPSA
       builds the nominal variable on ``extendables ∩ active_assets`` — so an
       ``active=False`` extendable is in the frame and not in the coords.
       ``_wrap_with_capex_budget`` guards only the variable's existence and
       has exactly that bug; this mirrors the curtailment wrapper instead.

    4. **Nothing in ``d`` may default to 1.0.** ``resolve_outage_params``
       returns ``source="missing"`` for any carrier outside the 10-entry
       defaults library. Such a unit splits by EVIDENCE, never by absence: it
       is must-take when it has a ``p_max_pu`` time series (credited at its
       peak-coincidence mean, §2.3) and EXCLUDED from the LHS otherwise —
       because a ``p_max_pu``-shaped fallback would credit a unit the tool
       knows nothing about at 1.0, above a gas unit on a class average
       (0.95). §3's preflight (a later wave) errors on the excluded ones.

    5. **The peak is an UNWEIGHTED MW maximum.** The ENS cap's denominator is
       a weighted ENERGY sum; copying that shape would report a 50× peak on a
       representative-week run with weight 50 and force 50× the capacity. A
       margin is a power standard.

    Slack exclusion uses ``slack.slack_generator_mask`` — BOTH tiers, never
    the ENS cap's ``involuntary_slack_mask``, which excludes only the VoLL
    tier by design: a ``__dsr_`` slack's ``p_nom`` is ``share × bus peak
    load`` and could satisfy any margin by itself.

    The margin is a CONSTRAINT, never a price (plan §1.6): a standard the LP
    can buy its way out of is not a standard.
    """
    if _prm_margin(cfg) is None:
        return user_fn

    def _emit(msg: str) -> None:
        _safe_log(log_queue, f"[PRM] {msg}")

    def reserve_margin_fn(n, snapshots):
        # Every number below comes from `reserve_margin_facts`, which knows
        # nothing about the LP — the same function §3's preflight calls, so
        # the standard it blocks on and the standard this installs cannot
        # drift apart. All this loop adds is the LP terms.
        # Phase 12c-0: inside the wrapper the load transforms are already
        # applied IN PLACE, so the facts read the frame as it stands; the
        # explicit switch is what keeps the LP-basis helper from scaling a
        # scaled frame twice (v3 review, finding 6).
        facts = reserve_margin_facts(n, cfg, snapshots, emit=_emit,
                                     demand_scaled_in_place=True)
        if facts is None:
            return
        # Option (y): an extendable behind a grid import is refused here as
        # in the preflight, never silently left out of the standard.
        if facts.get("import_group_extendable"):
            msg = import_group_extendable_message(facts["import_group_extendable"])
            _emit(msg)
            raise ValueError(msg)
        # GK A on #1998 (4): an extendable grid import, refused the same way.
        if facts.get("import_link_extendable"):
            msg = import_link_extendable_message(facts["import_link_extendable"])
            _emit(msg)
            raise ValueError(msg)
        stash = facts["stash"]
        margin = stash["margin"]
        # Solve-time truth for §4's report and §3's diagnoser: restore reverts
        # the load-scaling transforms, so a post-solve recomputation of these
        # peaks would drift. Cleaned up by run_simulation, like
        # `_ens_cap_targets`.
        n._reserve_margin_targets = stash

        for P, per in stash["periods"].items():
            peak = per["peak_mw"]
            required = per["required_mw"]
            firm_fixed = per["firm_fixed_mw"]
            max_achievable = per["max_achievable_mw"]

            terms: list = []
            for mem, d in facts["terms"].get(P, ()):
                vname = mem["var"]
                if vname not in n.model.variables:
                    continue
                var = n.model.variables[vname]
                # Coord membership BEFORE `.sel` — an inactive extendable
                # is in the frame and not in the variable's coords.
                if mem["name"] not in var.indexes["name"]:
                    continue
                terms.append(d * var.sel(name=mem["name"]))

            if not terms:
                # Linopy raises TypeError on a constant constraint, and the
                # nominal variable does not exist at all when nothing
                # extendable is active — a live path (myopic's dispatch-only
                # branch still passes extra_fn). So there is nothing to add:
                # say which way it fell and let §3's preflight own the error.
                if firm_fixed + 1e-9 >= required:
                    _emit(
                        f"Period {P}: reserve margin {margin:.1%} "
                        f"({required:,.1f} MW) already met by {firm_fixed:,.1f} MW "
                        "of derated fixed capacity — no LP constraint needed."
                    )
                else:
                    _emit(
                        f"Period {P}: reserve margin {margin:.1%} needs "
                        f"{required:,.1f} MW of derated capacity but the fleet "
                        f"tops out at {max_achievable:,.1f} MW and nothing "
                        "extendable is active — no constraint added (an "
                        "unreachable margin is a preflight error, not an "
                        "infeasible LP)."
                    )
                continue

            expr = sum(terms)
            cname = f"reserve_margin_{P}"
            try:
                n.model.add_constraints(expr >= required - firm_fixed, name=cname)
                _emit(
                    f"Period {P}: reserve margin {margin:.1%} — "
                    f"{required:,.1f} MW of derated firm capacity required "
                    f"against a {peak:,.1f} MW peak "
                    f"(over {per['n_peak_hours']} peak snapshot(s)); "
                    f"{firm_fixed:,.1f} MW fixed, {len(terms)} extendable "
                    f"term(s) must supply the remaining "
                    f"{max(0.0, required - firm_fixed):,.1f} MW."
                )
            except Exception as exc:
                _emit(
                    f"Period {P}: failed to add the reserve-margin constraint: "
                    f"{exc}. Skipping this period's margin."
                )

    def wrapper(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        reserve_margin_fn(n, snapshots)

    return wrapper


def _wrap_with_ens_cap(network: "pypsa.Network", user_fn, cfg, log_queue=None):
    """
    Compose the extra_functionality callback with the reliability target:
    a per-investment-period cap on unserved ELECTRICAL energy (adequacy
    spec §5.1), plus optional per-zone ceilings (zone = bus `country`).

    For each period P (and, with `ens_zone_cap_multiple` set, each zone z):

        Σ_{t∈P} w_gen[t] · Σ_{slack b ∈ scope} p_shed[b,t]
            ≤ (ens_cap_permyriad / 1e4) [· multiple] × D_{P[,z]}

    where D is the period's (zone's) weighted electrical demand, computed
    INSIDE the callback from n.loads / n.loads_t.p_set at optimize time —
    after the modelling-assumption transforms, so load scalers are already
    applied and the cap is a fraction of the demand the LP actually serves.
    Slack membership via services/adequacy/slack (never name literals);
    "electrical" via the same _canonical_load_carrier_key the load views
    use, so the target and the Results tab can never disagree on scope.

    The cap sums the INVOLUNTARY tier only (spec §4.4): demand response is a
    resource, not unserved energy — the DSR tests assert it stays
    unconstrained by the cap.

    Period restriction is done by ZERO-MASKING the weight vector rather than
    selecting snapshot subsets — sidesteps the MultiIndex .sel pitfalls the
    curtailment wrapper documents. Returns user_fn unchanged when no target
    is set. Rolling/myopic strategies are blocked at preflight (each LP
    window would need its own demand denominator).
    """
    try:
        permyriad = cfg.ens_cap_permyriad
        permyriad = float(permyriad) if permyriad is not None else None
    except (TypeError, ValueError):
        permyriad = None
    if permyriad is None or not math.isfinite(permyriad) or permyriad <= 0:
        return user_fn

    zone_multiple = None
    try:
        zm = cfg.ens_zone_cap_multiple
        if zm is not None and math.isfinite(float(zm)) and float(zm) > 0:
            zone_multiple = float(zm)
    except (TypeError, ValueError):
        zone_multiple = None

    def _emit(msg: str) -> None:
        _safe_log(log_queue, f"[ENS] {msg}")

    def ens_cap_fn(n, snapshots):
        import xarray as xr

        from services.adequacy.slack import involuntary_slack_mask

        gens = getattr(n, "generators", None)
        buses = getattr(n, "buses", None)
        loads = getattr(n, "loads", None)
        if gens is None or gens.empty or buses is None or loads is None:
            return

        # Electrical bus set — canonical classifier, blank counts electrical.
        elec_buses: set[str] = set()
        if "carrier" in buses.columns:
            for b in buses.index:
                if _canonical_load_carrier_key(buses.at[b, "carrier"]) == "electrical":
                    elec_buses.add(str(b))
        else:
            elec_buses = set(map(str, buses.index))

        mask = involuntary_slack_mask(gens)
        slack_names = [
            str(g) for g in gens.index[mask]
            if str(gens.at[g, "bus"]) in elec_buses
        ]
        if not slack_names:
            _emit(
                f"Target {permyriad:g}‱ set but no electrical slack "
                "generators exist (voll off?) — cap skipped."
            )
            return
        if "Generator-p" not in n.model.variables:
            _emit("Generator-p variable absent — cap skipped.")
            return
        p_var = n.model.variables["Generator-p"]

        # Energy weights over the LP's snapshots.
        w = _period_utils.snapshot_weights(n, "generators", sns=snapshots)

        # Electrical demand per snapshot: static p_set overridden per-column
        # by loads_t.p_set where present, restricted to electrical buses.
        elec_loads = [
            str(l) for l in loads.index
            if str(loads.at[l, "bus"]) in elec_buses
        ]
        demand = pd.Series(0.0, index=snapshots)
        p_set_t = getattr(getattr(n, "loads_t", None), "p_set", None)
        for l in elec_loads:
            if p_set_t is not None and l in getattr(p_set_t, "columns", []):
                demand = demand.add(
                    p_set_t[l].reindex(snapshots).fillna(0.0), fill_value=0.0)
            else:
                try:
                    demand = demand + float(loads.at[l, "p_set"] or 0.0)
                except (TypeError, ValueError):
                    continue

        # Period buckets: MultiIndex level 0, or one "ALL" bucket.
        if isinstance(snapshots, pd.MultiIndex):
            period_of = pd.Series(snapshots.get_level_values(0), index=snapshots)
            periods = sorted(set(period_of))
        else:
            period_of = pd.Series("ALL", index=snapshots)
            periods = ["ALL"]

        # Zone of each slack: its bus's country (Task 2). None ⇒ system-only.
        zone_of_slack: dict[str, str] = {}
        if zone_multiple is not None and "country" in buses.columns:
            for g in slack_names:
                b = str(gens.at[g, "bus"])
                zone_of_slack[g] = str(buses.at[b, "country"] or "") if b in buses.index else ""
            zones = sorted(set(zone_of_slack.values()))
            if zones == [""]:
                _emit(
                    "Per-zone ceiling requested but every electrical bus has "
                    "a blank `country` — the ceiling collapses into a second "
                    "system cap (one unnamed zone). Populate bus.country to "
                    "make zones real."
                )
        else:
            zones = []

        snap_coord = p_var.coords["snapshot"]

        # Solve-time truth for the post-solve report (services/adequacy/
        # report.py): restore reverts the load-scaling transforms, so a
        # post-solve recomputation of these denominators would drift.
        # Cleaned up by run_simulation after the report is built.
        stash = {
            "permyriad": permyriad,
            "zone_multiple": zone_multiple,
            "zone_of_bus": {
                str(gens.at[g, "bus"]): z for g, z in zone_of_slack.items()
            },
            "periods": {},
        }
        n._ens_cap_targets = stash

        def _add_cap(name: str, names: list[str], w_masked: pd.Series,
                     cap_mwh: float, label: str) -> None:
            w_xr = xr.DataArray(
                w_masked.values, dims=["snapshot"],
                coords={"snapshot": snap_coord},
            )
            expr = sum((p_var.sel(name=g) * w_xr).sum() for g in names)
            n.model.add_constraints(expr <= cap_mwh, name=name)
            _emit(label)

        for P in periods:
            in_p = (period_of == P)
            w_p = w.where(in_p, 0.0)
            demand_p = float((demand * w_p).sum())
            cap_p = permyriad / 1e4 * demand_p
            stash["periods"][P] = {
                "cap_mwh": cap_p, "demand_mwh": demand_p, "zones": {},
            }
            _add_cap(
                f"ens_cap_{P}", slack_names, w_p, cap_p,
                f"Period {P}: ENS cap {cap_p:,.1f} MWh "
                f"({permyriad:g}‱ of {demand_p:,.0f} MWh electrical demand) "
                f"on {len(slack_names)} slack(s).",
            )
            for z in zones:
                z_names = [g for g in slack_names if zone_of_slack.get(g, "") == z]
                if not z_names:
                    continue
                z_buses = {str(gens.at[g, "bus"]) for g in z_names}
                z_loads = [l for l in elec_loads if str(loads.at[l, "bus"]) in z_buses]
                z_demand = pd.Series(0.0, index=snapshots)
                for l in z_loads:
                    if p_set_t is not None and l in getattr(p_set_t, "columns", []):
                        z_demand = z_demand.add(
                            p_set_t[l].reindex(snapshots).fillna(0.0), fill_value=0.0)
                    else:
                        try:
                            z_demand = z_demand + float(loads.at[l, "p_set"] or 0.0)
                        except (TypeError, ValueError):
                            continue
                z_demand_p = float((z_demand * w_p).sum())
                z_cap = zone_multiple * permyriad / 1e4 * z_demand_p
                stash["periods"][P]["zones"][z] = z_cap
                zlabel = z or "<blank>"
                _add_cap(
                    f"ens_zone_{zlabel}_{P}", z_names, w_p, z_cap,
                    f"Period {P}, zone {zlabel}: ceiling {z_cap:,.1f} MWh "
                    f"({zone_multiple:g}× the target on its own "
                    f"{z_demand_p:,.0f} MWh) on {len(z_names)} slack(s).",
                )

    def wrapper(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        ens_cap_fn(n, snapshots)

    return wrapper


def _wrap_with_import_energy_cap(network: "pypsa.Network", user_fn, cfg,
                                 log_queue=None):
    """
    Compose extra_functionality with the Energy Hub energy import cap (EH
    spec §6, P17 amendment). For each investment period P:

        Σ_{t∈P} w[t] · Σ_{l ∈ metered} η_l[t] · p0[l,t]  ≤  E × Σ_{t∈P} w[t] / 8760

    ``w`` is the ``generators`` snapshot weighting WITHOUT the period's
    ``years`` multiplier — the cap is per YEAR of each period, not per
    period. ``metered`` = ``cfg.import_energy_links``, already oriented
    grid → hub by the pack patch (so η·p0 is the energy delivered to the
    hub); bidirectional Links and rolling/myopic are refused at preflight.
    Added through extra_functionality, not as a PyPSA GlobalConstraint row.
    """
    try:
        cap = cfg.import_energy_cap_mwh_per_year
        cap = float(cap) if cap is not None else None
    except (TypeError, ValueError, AttributeError):
        cap = None
    links = [str(x) for x in (getattr(cfg, "import_energy_links", None) or [])]
    if cap is None or not math.isfinite(cap) or cap < 0 or not links:
        return user_fn

    def _emit(msg: str) -> None:
        _safe_log(log_queue, f"[IMPORT] {msg}")

    def import_cap_fn(n, snapshots):
        import xarray as xr

        present = [l for l in links if l in n.links.index]
        missing = sorted(set(links) - set(present))
        if missing:
            _emit(f"Metered import Link(s) {missing} not on the network — "
                  "left out of the energy cap.")
        if not present:
            _emit("No metered import Link on the network — cap skipped.")
            return
        if "Link-p" not in n.model.variables:
            _emit("Link-p variable absent — cap skipped.")
            return
        p_var = n.model.variables["Link-p"]
        snap_coord = p_var.coords["snapshot"]
        sw = n.snapshot_weightings
        col = "generators" if "generators" in sw.columns else sw.columns[0]
        w = sw.loc[snapshots, col].astype(float)
        if isinstance(snapshots, pd.MultiIndex):
            period_of = pd.Series(snapshots.get_level_values(0), index=snapshots)
            periods = sorted(set(period_of))
        else:
            period_of = pd.Series("ALL", index=snapshots)
            periods = ["ALL"]
        eff_t = getattr(getattr(n, "links_t", None), "efficiency", None)

        def _eff(l) -> pd.Series:
            if eff_t is not None and l in getattr(eff_t, "columns", []):
                return eff_t[l].reindex(snapshots).astype(float)
            return pd.Series(float(n.links.at[l, "efficiency"]), index=snapshots)

        for P in periods:
            w_p = w.where(period_of == P, 0.0)
            budget = cap * float(w_p.sum()) / 8760.0
            expr = None
            for l in present:
                coef = xr.DataArray((w_p * _eff(l)).values, dims=["snapshot"],
                                    coords={"snapshot": snap_coord})
                term = (p_var.sel(name=l) * coef).sum()
                expr = term if expr is None else expr + term
            n.model.add_constraints(expr <= budget, name=f"import_energy_cap_{P}")
            _emit(f"Period {P}: hub import ≤ {budget:,.1f} MWh "
                  f"({cap:,.0f} MWh/yr × {float(w_p.sum()):g} h / 8760) on "
                  f"{', '.join(present)}.")

    def wrapper(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        import_cap_fn(n, snapshots)

    return wrapper


def _grid_imports(n, elec_buses: set[str]) -> list[str] | None:
    """The ``eh_role == "grid_import"`` Links with both ends electrical, or
    None when there is none (the margin is then untouched)."""
    links = getattr(n, "links", None)
    if links is None or links.empty or "eh_role" not in links.columns:
        return None
    imports = [
        str(lk) for lk in links.index
        if str(links.at[lk, "eh_role"]).strip() == "grid_import"
        and str(links.at[lk, "bus0"]) in elec_buses
        and str(links.at[lk, "bus1"]) in elec_buses
    ]
    return imports or None


def _link_value(n, lk: str, attr: str, snaps, worst, default: float) -> tuple[float, bool]:
    """A Link's ``attr`` (p_max_pu, p_min_pu, efficiency) over ``snaps``:
    the time series' ``worst`` (min or max) over those snapshots when it has
    one, else the static column. Returns ``(value, from_series)``. A value
    that is not a finite number reads ``default``."""
    ts = getattr(getattr(n, "links_t", None), attr, None)
    if ts is not None and lk in getattr(ts, "columns", []):
        vals = pd.to_numeric(ts[lk].reindex(snaps), errors="coerce").dropna()
        if len(vals):
            return float(worst(vals)), True
    try:
        v = float(n.links.at[lk, attr])
    except (KeyError, TypeError, ValueError):
        return default, False
    return (v if math.isfinite(v) else default), False


def _reach(start: set[str], adj: dict[str, set[str]]) -> set[str]:
    seen = set(start)
    stack = list(start)
    while stack:
        for w in adj.get(stack.pop(), ()):
            if w not in seen:
                seen.add(w)
                stack.append(w)
    return seen


def _sccs(nodes: set[str], fwd: dict[str, set[str]]) -> dict[str, int]:
    """Strongly connected components of ``fwd`` restricted to ``nodes``
    (Kosaraju, iterative): ``{bus: component id}``."""
    order: list[str] = []
    seen: set[str] = set()
    for s in sorted(nodes):
        if s in seen:
            continue
        seen.add(s)
        stack = [(s, iter(sorted(fwd[s] & nodes)))]
        while stack:
            v, it = stack[-1]
            for w in it:
                if w not in seen:
                    seen.add(w)
                    stack.append((w, iter(sorted(fwd[w] & nodes))))
                    break
            else:
                stack.pop()
                order.append(v)
    rev: dict[str, set[str]] = {b: set() for b in nodes}
    for a in nodes:
        for b in fwd[a] & nodes:
            rev[b].add(a)
    comp: dict[str, int] = {}
    cid = -1
    for s in reversed(order):
        if s in comp:
            continue
        cid += 1
        comp[s] = cid
        stack2 = [s]
        while stack2:
            for w in rev[stack2.pop()]:
                if w not in comp:
                    comp[w] = cid
                    stack2.append(w)
    return comp


def _import_topology(n, elec_buses: set[str], imports: list[str], snaps,
                     active) -> dict:
    """Which buses reach the site only through ``grid_import`` Links in ONE
    period, and by which hops (E3, G2; GK A on #1998 (1)-(3), (7)).

    **Paths are directed and per period.** A Line or Transformer active in
    the period carries power both ways. A plain Link active in the period
    carries it from bus0 toward its other ports when its p_max_pu (the
    lowest in the period, for a time series) is above 0, and back when its
    p_min_pu (the highest in the period) is below 0; a Link that cannot carry
    power toward the site is no path. ``active(comp)`` is the period's
    activity mask.

    **The site** is every bus holding an electrical Load (G2: the margin's
    demand is there), plus every bus flagged ``eh_critical``. With neither,
    it falls back to the ``bus1`` ends of the ``grid_import`` Links (their
    documented orientation: ``bus0`` upstream). A bus with a free path to a
    site bus is site-side and keeps plain credit.

    **The rest** is split into stages (strongly connected over the free
    paths). A stage's hop is where its power goes next: a free path to
    another stage, or its ``grid_import`` Links (bus0 -> bus1) to one stage
    or to the site. A stage that reaches the site only through hops is
    "behind" the import; one that cannot reach it at all is an island and
    keeps plain credit. A stage with hops to several places is credited only
    through its imports straight into the site (none: nothing), and says so.
    The imports are structural: one not active in the period is a hop rated
    0 MW, not a missing hop.

    Returns ``{"stage_of": {bus: stage}, "next": {stage: (target, links,
    fanout)}}`` for the behind stages, where ``target`` is a stage,
    ``"SITE"`` or None, and ``links`` is empty for a free hop."""
    from services.solver.process_uc import flag_column

    links = n.links
    imp = set(imports)
    fwd: dict[str, set[str]] = {b: set() for b in elec_buses}

    def _edge(a: str, b: str) -> None:
        if a in fwd and b in fwd and a != b:
            fwd[a].add(b)

    for comp in ("lines", "transformers"):
        df = getattr(n, comp, None)
        if df is None or df.empty:
            continue
        act = active(comp)
        for name in df.index:
            if not bool(act.get(name, False)):
                continue
            b0, b1 = str(df.at[name, "bus0"]), str(df.at[name, "bus1"])
            _edge(b0, b1)
            _edge(b1, b0)
    ports = sorted((c for c in links.columns
                    if c.startswith("bus") and c[3:].isdigit() and c != "bus0"),
                   key=lambda c: int(c[3:]))
    link_act = active("links")
    for name in links.index:
        lk = str(name)
        if lk in imp or not bool(link_act.get(name, False)):
            continue
        forward = _link_value(n, lk, "p_max_pu", snaps, min, 0.0)[0] > 0
        backward = _link_value(n, lk, "p_min_pu", snaps, max, 0.0)[0] < 0
        b0 = str(links.at[name, "bus0"])
        for c in ports:
            other = str(links.at[name, c])
            if other.strip() in ("", "nan", "None"):
                continue
            if forward:
                _edge(b0, other)
            if backward:
                _edge(other, b0)

    site: set[str] = set()
    loads = getattr(n, "loads", None)
    if loads is not None and not loads.empty and "bus" in loads.columns:
        site |= {str(b) for b in loads["bus"]} & elec_buses
    site |= {str(b) for b in n.buses.index[flag_column(n.buses, "eh_critical")]
             } & elec_buses
    if not site:
        site = {str(links.at[lk, "bus1"]) for lk in imports}

    rev: dict[str, set[str]] = {b: set() for b in fwd}
    for a, outs in fwd.items():
        for b in outs:
            rev[b].add(a)
    free = _reach(site & set(fwd), rev)
    rest = set(fwd) - free
    scc = _sccs(rest, fwd)

    # targets[stage][target] = {"free": bool, "links": set}
    targets: dict[int, dict] = {s: {} for s in set(scc.values())}
    for a in rest:
        for b in fwd[a]:
            if scc[b] != scc[a]:
                targets[scc[a]].setdefault(
                    scc[b], {"free": False, "links": set()})["free"] = True
    for lk in imports:
        t, h = str(links.at[lk, "bus0"]), str(links.at[lk, "bus1"])
        if t in free:
            continue
        hs = "SITE" if h in free else scc[h]
        if hs == scc[t]:
            continue
        targets[scc[t]].setdefault(hs, {"free": False, "links": set()})["links"].add(lk)

    behind = {s for s, tg in targets.items() if "SITE" in tg}
    grew = True
    while grew:
        grew = False
        for s, tg in targets.items():
            if s not in behind and any(t in behind for t in tg):
                behind.add(s)
                grew = True

    nxt: dict[int, tuple] = {}
    for s in behind:
        tg = {t: v for t, v in targets[s].items() if t == "SITE" or t in behind}
        if len(tg) == 1:
            (t, v), = tg.items()
            nxt[s] = (t, () if v["free"] else tuple(sorted(v["links"])), False)
        elif "SITE" in tg:
            nxt[s] = ("SITE", tuple(sorted(tg["SITE"]["links"])), True)
        else:
            nxt[s] = (None, (), True)
    return {"stage_of": {b: scc[b] for b in rest if scc[b] in behind},
            "next": nxt}


def _import_paths(topo: dict) -> dict[int, list[tuple]]:
    """Each behind stage's hops to the site (or to a dead end), in order."""
    paths: dict[int, list[tuple]] = {}
    for s in topo["next"]:
        path, cur, seen = [], s, set()
        while cur in topo["next"] and cur not in seen:
            seen.add(cur)
            path.append(topo["next"][cur])
            cur = topo["next"][cur][0]
        paths[s] = path
    return paths


def _import_trees(topo: dict) -> tuple[dict[int, int], dict[int, set[str]]]:
    """Each behind stage's root (the last stage before the site or a dead
    end) and each root's ``grid_import`` Links: one tree is one group."""
    nxt = topo["next"]
    root_of: dict[int, int] = {}
    for s, path in _import_paths(topo).items():
        cur = s
        for t, _l, _f in path:
            if t in nxt:
                cur = t
        root_of[s] = cur
    tree_links: dict[int, set[str]] = {}
    for s in nxt:
        tree_links.setdefault(root_of[s], set()).update(nxt[s][1])
    return root_of, tree_links


def _import_link_caps(n, P, topo: dict, snaps, links_active, link_q: dict,
                      energized: dict, notes: dict) -> dict[str, tuple]:
    """``{link: (C_l, a_l)}`` for every hop Link of the period: ``C_l`` the
    MW it DELIVERS (GK A on #1998 (3), (5)) = ``p_nom`` x its p_max_pu (the
    lowest in the period for a time series), at most its energization
    schedule's minimum in P (``energization.energized_mw_by_period``, a
    sending-side MW), x its efficiency (the lowest in the period); 0 when it
    is not active in P or its outage rate is unusable. ``a_l = 1 - q_l``.
    Records the Links read off a time series in ``notes``."""
    ekey = "all" if P == "ALL" else str(P)
    notes["pu_varies"], notes["eff_varies"] = [], []
    caps: dict[str, tuple] = {}
    for hop in topo["next"].values():
        for lk in hop[1]:
            if lk in caps:
                continue
            if lk not in link_q or not bool(links_active.get(lk, False)):
                caps[lk] = (0.0, 0.0)
                continue
            try:
                p_nom = float(n.links.at[lk, "p_nom"])
            except (TypeError, ValueError):
                p_nom = 0.0
            pu, pu_ts = _link_value(n, lk, "p_max_pu", snaps, min, 0.0)
            eff, eff_ts = _link_value(n, lk, "efficiency", snaps, min, 0.0)
            mw = max(0.0, p_nom) * max(0.0, pu) if math.isfinite(p_nom) else 0.0
            e = (energized.get(lk) or {}).get(ekey)
            if e is not None and math.isfinite(e):
                mw = min(mw, max(0.0, e))
            caps[lk] = (mw * max(0.0, eff), 1.0 - link_q[lk])
            if pu_ts:
                notes["pu_varies"].append(lk)
            if eff_ts:
                notes["eff_varies"].append(lk)
    return caps


def _import_group_credit(P, topo: dict, groups: dict, link_cap: dict,
                         notes: dict) -> float:
    """The period's firm credit through its ``grid_import`` Links (the series
    rule, E1/E2 and GK A (37)), written onto each member's ``assets`` row as
    ``cap`` (E5).

    Per hop of ``grid_import`` Links: ``C = Σ C_l`` with ``C_l`` the MW a
    Link DELIVERS in P (``link_cap``: p_nom × its lowest p_max_pu in P, at
    most its energization schedule, × efficiency) and ``A = Σ a_l C_l / C``.
    A stage passes on ``min(U, C)`` at availability ``(dU / U) × A``, where
    ``U`` and ``dU`` are its own fixed members (``Σ C_i``, ``Σ d_i C_i``)
    plus what its upstream stages pass to it; a free hop passes all of it.
    The hop into the site credits ``min(U, C) × (dU / U) × A``, so a chain
    is capped at its tightest Link: ``min(U, C_1, C_2) × A_up × A_1 × A_2``.
    A hop rated 0 MW passes nothing (G3).

    ``groups`` is ``{stage: {"U", "dU", "rows"}}`` from the member loop;
    ``notes`` holds the link lists ``nodata``, ``bad``, ``pu_varies`` and
    ``eff_varies`` (``_import_link_caps``)."""
    nxt = topo["next"]

    def _hop(links: tuple) -> tuple[float, float | None]:
        c = sum(link_cap[lk][0] for lk in links)
        if c <= 0:
            return 0.0, None
        return c, sum(link_cap[lk][1] * link_cap[lk][0] for lk in links) / c

    arrive: dict[int, tuple[float, float]] = {}

    def _arrive(s) -> tuple[float, float]:
        if s not in arrive:
            g = groups.get(s) or {"U": 0.0, "dU": 0.0}
            u, du = g["U"], g["dU"]
            for y, (t, _l, _f) in nxt.items():
                if t == s:
                    pu, pdu = _pass(y)
                    u, du = u + pu, du + pdu
            arrive[s] = (u, du)
        return arrive[s]

    def _pass(s) -> tuple[float, float]:
        t, links, _f = nxt[s]
        u, du = _arrive(s)
        if t is None or u <= 0:
            return 0.0, 0.0
        if not links:
            return u, du
        c, a = _hop(links)
        if c <= 0:
            return 0.0, 0.0
        out = min(u, c)
        return out, out * (du / u) * a

    paths = _import_paths(topo)
    root_of, _tree_links = _import_trees(topo)
    credit_of: dict[int, float] = {}
    for s in nxt:
        if root_of[s] == s:
            credit_of[s] = _pass(s)[1] if nxt[s][0] == "SITE" else 0.0
    total = sum(credit_of.values())

    tree_u: dict[int, float] = {}
    tree_du: dict[int, float] = {}
    for s in nxt:
        r = root_of[s]
        g = groups.get(s) or {"U": 0.0, "dU": 0.0}
        tree_u[r] = tree_u.get(r, 0.0) + g["U"]
        tree_du[r] = tree_du.get(r, 0.0) + g["dU"]

    for s, g in groups.items():
        if s not in nxt:
            continue
        r = root_of[s]
        hops = [h for h in paths[s] if h[1]]
        links = sorted({lk for h in hops for lk in h[1]})
        caps = [_hop(h[1]) for h in hops]
        c_l = min((c for c, _a in caps), default=0.0)
        a_l = None
        if c_l > 0:
            a_l = 1.0
            for _c, a in caps:
                a_l *= a
        dead_end = paths[s][-1][0] is None
        if dead_end:
            c_l, a_l = 0.0, None
        credit = credit_of.get(r, 0.0)
        names = ", ".join(links)
        if dead_end:
            reason = (f"no single path to the site in period {P}: the supply "
                      "behind it reaches the site only through grid_import "
                      "Links that lead several ways, none of them straight "
                      "into the site, so nothing behind it is credited")
        elif c_l > 0:
            reason = f"capped at grid_import {names} {c_l:g} MW × {a_l:g}"
        else:
            reason = (f"no import capacity in period {P}: grid_import {names} "
                      "is not active, open (p_max_pu 0) or rated 0 MW, so "
                      "nothing behind it is credited")
        if any(h[2] for h in paths[s]):
            reason += ("; it also leads elsewhere through other paths, which "
                       "are not counted")
        for key, what in (("pu_varies", "p_max_pu"), ("eff_varies", "efficiency")):
            varies = [lk for lk in links if lk in notes[key]]
            if varies:
                reason += (f"; the lowest {what} in the period is taken for "
                           f"{', '.join(varies)}")
        nodata = [lk for lk in links if lk in notes["nodata"]]
        if nodata:
            reason += ("; link availability taken as 1: no outage data "
                       f"({', '.join(nodata)})")
        bad = [lk for lk in links if lk in notes["bad"]]
        if bad:
            reason += f"; {', '.join(bad)} not counted: outage rate outside [0, 1)"
        share = credit / tree_du[r] if tree_du.get(r, 0.0) > 0 else 0.0
        for row, extendable, d_cap in g["rows"]:
            cap = {"links": links, "link_mw": c_l, "link_availability": a_l,
                   "group_mw": tree_u.get(r, 0.0), "credited_mw": credit,
                   "member_credit_mw": 0.0 if extendable else share * d_cap,
                   "reason": reason}
            if extendable:
                cap["reason"] = (reason + "; extendable behind the import: refused "
                                 "(reserve_margin_import_group_extendable), not credited")
            row["cap"] = cap
    return total


def import_group_extendable_message(groups: list[dict]) -> str:
    """The ONE sentence for option (y), shared by the preflight and the LP
    wrapper so the two refusals cannot drift."""
    parts = "; ".join(
        f"{', '.join(g['members'])} behind grid_import {', '.join(g['links'])}"
        for g in groups)
    return (
        "The reserve margin cannot credit extendable capacity that reaches "
        f"the site only through a grid import: {parts}. Its delivery is capped "
        "by the connection, which the margin's per-asset LP terms cannot "
        "express. Make the asset fixed, connect it on the site side, or unset "
        "the margin."
    )


def import_link_extendable_message(links: list[str]) -> str:
    """The ONE sentence for an extendable ``grid_import`` Link (GK A on
    #1998 (4)), shared by the preflight and the LP wrapper."""
    return (
        "The reserve margin cannot credit supply behind an extendable grid "
        f"import: {', '.join(links)}. The margin would read the Link's static "
        "p_nom, which the solve ignores for an extendable Link and may build "
        "smaller. Make the grid import fixed (set its p_nom) or unset the "
        "margin."
    )


def reserve_margin_facts(n, cfg, snapshots=None, emit=None, *,
                         demand_scaled_in_place: bool = False) -> dict | None:
    """
    Everything the firm-capacity standard knows BEFORE an LP exists (§2, §3).

    Returns ``None`` when no margin is set. Otherwise::

        {"stash":   <the §2.6 stash, exactly the four contract keys>,
         "terms":   {period: [(member, derate), …]},   # active extendables
         "unpriceable":     [names excluded for lack of evidence],
         "carrier_default": [names credited off a carrier class average],
         "import_group_extendable": [{"members": [...], "links": [...]}],
         "import_link_extendable":  [grid_import Links that are extendable]}

    **The point-of-connection cap.** Generation and storage that reach the
    site only through ``grid_import`` Links (``eh_role``) are credited as a
    GROUP, never above what the Links deliver (``_import_topology``, per
    period; ``_import_group_credit``): ``min(U, C_L) x A_up x A_L`` per
    period, a chain of imports at its tightest Link, replacing the members'
    own terms in ``firm_fixed_mw`` and ``max_achievable_mw``. Each capped
    member's ``assets`` row carries a ``cap`` dict saying so (with its own
    share, ``member_credit_mw``); ``capacity_mw`` is unchanged. An extendable
    member behind such a Link is not credited and is listed in
    ``import_group_extendable``; an extendable ``grid_import`` Link with
    something behind it is listed in ``import_link_extendable``. The
    preflight and the LP wrapper refuse both. A network with no
    ``grid_import`` Link is untouched.

    This is deliberately ONE function rather than two, because §3's preflight
    must answer "can any plan built from this candidate set reach the margin?"
    from the config and the network alone — the LP cannot answer it (linopy
    raises ``TypeError`` on a constant constraint, and ``Generator-p_nom``
    does not exist when nothing extendable is active). Two implementations of
    the derating chain would be two standards: the one the preflight blocks
    on and the one the LP enforces, drifting silently apart on the first
    change to either. The wrapper adds the LP terms; every NUMBER comes from
    here.

    Nothing in this function touches ``n.model``, so it is callable at
    preflight time. It does not write the stash either — the wrapper owns
    that, because the stash is solve-time truth and its lifetime is the
    solve's.
    """
    from services.adequacy.copt import _membership_walk
    from services.adequacy.occurrence import resolve_outage_params
    from services.adequacy.slack import slack_generator_mask

    margin = _prm_margin(cfg)
    if margin is None:
        return None

    # Reference duration for the storage haircut. Bounded on the schema; the
    # dataclass validates nothing, so a nonsense value falls back rather than
    # dividing by zero inside the LP build.
    try:
        duration_ref = float(getattr(cfg, "prm_storage_duration_h", 4.0))
    except (TypeError, ValueError):
        duration_ref = 4.0
    if not math.isfinite(duration_ref) or duration_ref <= 0:
        duration_ref = 4.0

    try:
        peak_hours_override = getattr(cfg, "prm_peak_hours", None)
        peak_hours_override = (
            int(peak_hours_override) if peak_hours_override is not None else None)
        if peak_hours_override is not None and peak_hours_override < 1:
            peak_hours_override = None
    except (TypeError, ValueError):
        peak_hours_override = None

    def _finite(v, default=float("nan")) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return default
        return f if math.isfinite(f) else default

    buses = getattr(n, "buses", None)
    loads = getattr(n, "loads", None)
    gens = getattr(n, "generators", None)
    if buses is None or loads is None:
        return None
    if snapshots is None:
        snapshots = n.snapshots

    # Electrical bus set — the SAME canonical classifier the ENS cap and
    # the load views use, so the standard and the Results tab can never
    # disagree on scope. Blank counts electrical.
    elec_buses: set[str] = set()
    if "carrier" in buses.columns:
        for b in buses.index:
            if _canonical_load_carrier_key(buses.at[b, "carrier"]) == "electrical":
                elec_buses.add(str(b))
    else:
        elec_buses = set(map(str, buses.index))

    # ── the demand series, built EXACTLY as _wrap_with_ens_cap builds it
    #    (electrical buses; loads_t.p_set overriding the static value per
    #    column) — the two standards must read the same demand.
    elec_loads = [
        str(l) for l in loads.index
        if str(loads.at[l, "bus"]) in elec_buses
    ]
    demand = pd.Series(0.0, index=snapshots)
    # Phase 12c-0: the LP's demand basis. From a route (preflight, the
    # margin loop, the payload comparison) the frame is scaled through the
    # shared helper; inside the solve wrapper it is already scaled in place
    # and the caller says so.
    from services.adequacy.demand import demand_frame_for
    p_set_t = demand_frame_for(
        n, cfg, demand_scaled_in_place=demand_scaled_in_place)
    for l in elec_loads:
        if p_set_t is not None and l in getattr(p_set_t, "columns", []):
            demand = demand.add(
                p_set_t[l].reindex(snapshots).fillna(0.0), fill_value=0.0)
        else:
            try:
                demand = demand + float(loads.at[l, "p_set"] or 0.0)
            except (TypeError, ValueError):
                continue

    # Period buckets: MultiIndex level 0, or one "ALL" bucket — the ENS
    # cap's convention, so the two stashes key alike.
    if isinstance(snapshots, pd.MultiIndex):
        period_of = pd.Series(snapshots.get_level_values(0), index=snapshots)
        periods = sorted(set(period_of))
    else:
        period_of = pd.Series("ALL", index=snapshots)
        periods = ["ALL"]

    # ── membership + classification ──────────────────────────────────
    members: list[dict] = []
    unpriceable: list[str] = []

    p_max_pu_t = getattr(getattr(n, "generators_t", None), "p_max_pu", None)
    profile_cols = set(map(str, list(getattr(p_max_pu_t, "columns", []))))

    if gens is not None and not gens.empty:
        slack = slack_generator_mask(gens)
        has_ext = "p_nom_extendable" in gens.columns
        has_pmp = "p_max_pu" in gens.columns
        has_pmax = "p_nom_max" in gens.columns
        for g, _cap_unused, _series_unused, occ in _membership_walk(
                n, elec_buses, keep_zero_capacity=True):
            # The walk already applies `slack_generator_mask`; re-stating
            # it here is deliberate. This is the one exclusion whose
            # WRONG version (`involuntary_slack_mask`, which keeps the
            # DSR tier) is a live, plausible mistake — the ENS cap next
            # door uses exactly that one — and a DSR slack sized at
            # `share × bus peak load` can satisfy a margin by itself.
            if bool(slack.get(g, False)):
                continue
            name = str(g)
            source = str(occ["source"])
            basis = str(occ["basis"] or "")
            profile = p_max_pu_t[name] if name in profile_cols else None
            if source == "missing":
                # Split by EVIDENCE, not by absence (§2.2).
                if profile is None:
                    unpriceable.append(name)
                    continue
                q = 0.0          # no outage data — the profile is all we know
            else:
                q = _finite(occ["rate"])
                # Whole-branch review S1: a rate outside [0, 1) is as
                # unpriceable as no rate — (1 − q) is not a derate then.
                if math.isnan(q) or not (0.0 <= q < 1.0):
                    unpriceable.append(name)
                    continue
            # Availability: the profile when the unit has one (PyPSA caps
            # dispatch at p_max_pu × p_nom, and a time series overrides
            # the static column), else the static column. NEVER an
            # unconditional 1.0.
            avail_static = _finite(
                gens.at[g, "p_max_pu"], 1.0) if has_pmp else 1.0
            members.append({
                "name": name,
                "kind": "generator",
                "comp": "generators",
                "bus": str(gens.at[g, "bus"]),
                "var": "Generator-p_nom",
                "extendable": bool(gens.at[g, "p_nom_extendable"]) if has_ext else False,
                "p_nom": _finite(gens.at[g, "p_nom"], 0.0),
                "p_nom_max": _finite(
                    gens.at[g, "p_nom_max"], float("inf")) if has_pmax else float("inf"),
                "q": q,
                "basis": basis,
                "source": source,
                "profile": profile,
                "avail_static": max(0.0, min(1.0, avail_static)),
                "energy_limited": False,
            })

    # ── storage: the duration haircut (§2.4). `Store` is excluded — no
    #    power rating, so no firm-power credit (the MC's rationale).
    sus = getattr(n, "storage_units", None)
    if sus is not None and not sus.empty:
        sparams = resolve_outage_params(n, "storage_units")
        inflow_t = getattr(getattr(n, "storage_units_t", None), "inflow", None)
        inflow_cols = set(map(str, list(getattr(inflow_t, "columns", []))))
        has_ext = "p_nom_extendable" in sus.columns
        has_pmax = "p_nom_max" in sus.columns
        for s in sus.index:
            name = str(s)
            if str(sus.at[s, "bus"]) not in elec_buses:
                continue
            row = sparams.loc[s]
            if str(row["source"]) == "missing":
                # Same rule as a generator with no evidence: a storage
                # unit has no availability profile to fall back on, so
                # crediting it would mean defaulting its derate to 1.0.
                unpriceable.append(name)
                continue
            q = _finite(row["rate"])
            if math.isnan(q) or not (0.0 <= q < 1.0):
                unpriceable.append(name)
                continue
            max_hours = _finite(sus.at[s, "max_hours"], 0.0) \
                if "max_hours" in sus.columns else 0.0
            haircut = min(1.0, max_hours / duration_ref) if max_hours > 0 else 0.0
            energy_limited = False
            if name in inflow_cols:
                try:
                    energy_limited = bool(
                        float(inflow_t[name].abs().max()) > 0.0)
                except (TypeError, ValueError):
                    energy_limited = False
            if not energy_limited and "inflow" in sus.columns:
                energy_limited = _finite(sus.at[s, "inflow"], 0.0) != 0.0
            members.append({
                "name": name,
                "kind": "storage",
                "comp": "storage_units",
                "bus": str(sus.at[s, "bus"]),
                "var": "StorageUnit-p_nom",
                "extendable": bool(sus.at[s, "p_nom_extendable"]) if has_ext else False,
                "p_nom": _finite(sus.at[s, "p_nom"], 0.0),
                "p_nom_max": _finite(
                    sus.at[s, "p_nom_max"], float("inf")) if has_pmax else float("inf"),
                "q": q,
                "basis": str(row["basis"] or ""),
                "source": str(row["source"]),
                "profile": None,
                "avail_static": haircut,
                "energy_limited": energy_limited,
            })

    def _active(comp: str, P) -> pd.Series:
        """Activity mask for one component frame in period P. Masks BOTH
        sides: a fixed asset not yet built is not a constant either.
        Phase 12d: the mask itself is ``services.adequacy.activity.
        active_mask`` — the SAME call the engines make — so the margin and
        the engines agree by construction; the all-True guard stays here
        (E12 pins the delegation; the import is deliberately lazy)."""
        df = getattr(n, comp, None)
        try:
            from services.adequacy import activity as _activity
            return _activity.active_mask(n, comp, P)
        except Exception:
            idx = df.index if df is not None else pd.Index([])
            return pd.Series(True, index=idx, dtype=bool)

    # ── the point-of-connection cap (E1-E7; GK A on #1998): members reaching
    #    the site only through `grid_import` Links are credited as a group,
    #    at most what the Links deliver. The topology is built per period
    #    from that period's active branches. No such Link: nothing below runs.
    topos: dict = {}                         # period -> _import_topology
    link_q: dict[str, float] = {}
    link_nodata: list[str] = []
    link_bad: list[str] = []
    energized: dict[str, dict | None] = {}
    import_group_extendable: list[dict] = []
    import_link_extendable: list[str] = []
    imports = _grid_imports(n, elec_buses)
    if imports is not None:
        used: set[str] = set()
        ext_groups: dict[tuple, set[str]] = {}
        ext_links: set[str] = set()
        for P in periods:
            snaps_p = snapshots[(period_of == P).to_numpy()]
            topo = _import_topology(n, elec_buses, imports, snaps_p,
                                    lambda comp, P=P: _active(comp, P))
            topos[P] = topo
            root_of, tree_links = _import_trees(topo)
            for s_links in tree_links.values():
                used |= s_links
            held: dict[int, list[dict]] = {}
            for mem in members:
                st = topo["stage_of"].get(mem["bus"])
                if st is not None:
                    held.setdefault(root_of[st], []).append(mem)
            for r, mems in held.items():
                ls = tuple(sorted(tree_links[r]))
                ext_links |= {lk for lk in ls if bool(n.links.at[lk, "p_nom_extendable"])}
                ext = {m["name"] for m in mems if m["extendable"]}
                if ext:
                    ext_groups.setdefault(ls, set()).update(ext)
        import_group_extendable = [
            {"members": sorted(ms), "links": list(ls)}
            for ls, ms in sorted(ext_groups.items())]
        import_link_extendable = sorted(ext_links)
        from services.adequacy.energization import energized_mw_by_period
        lparams = resolve_outage_params(n, "links")
        for lk in sorted(used):
            row = lparams.loc[lk]
            if str(row["source"]) == "missing":
                # G1: no evidence on the Link itself; its availability is
                # taken as 1 and the facts say so.
                link_q[lk] = 0.0
                link_nodata.append(lk)
            else:
                q = _finite(row["rate"])
                if math.isnan(q) or not (0.0 <= q < 1.0):
                    # Unusable rate: as unpriceable as a generator's, and
                    # the Link carries nothing until it is fixed.
                    unpriceable.append(lk)
                    link_bad.append(lk)
                    continue
                link_q[lk] = q
            energized[lk] = energized_mw_by_period(n, lk)

    if unpriceable and emit is not None:
        emit(
            f"Reserve margin cannot price {len(unpriceable)} asset(s) — no "
            f"outage data (or an outage rate outside [0, 1)) and no "
            f"availability profile: "
            f"{', '.join(sorted(unpriceable)[:20])}"
            f"{' …' if len(unpriceable) > 20 else ''}. They are EXCLUDED "
            "from the firm-capacity total (never credited at 1.0)."
        )

    stash: dict = {
        "margin": margin,
        "horizon_wide": True,
        "periods": {},
        "assets": [],
    }
    terms: dict[str, list] = {}
    carrier_default: list[str] = []

    ext_sets: list[frozenset] = []

    for P in periods:
        in_p = (period_of == P)
        demand_p = demand[in_p]
        peak = float(demand_p.max()) if len(demand_p) else 0.0
        required = (1.0 + margin) * peak

        # Peak-coincidence window (§2.3): ONE rule, shared with the net-load
        # window the payload selects post-solve (Phase 12b), so the two can
        # only differ by their series. Every snapshot tied with the
        # Nth-highest demand is included.
        from services.adequacy.window import peak_window
        peak_idx = peak_window(demand_p, n_override=peak_hours_override)

        active = {c: _active(c, P) for c in ("generators", "storage_units")}
        # Per-stage accumulators for the point-of-connection cap (E4: the
        # members, the Links and the paths are each masked for THIS period).
        topo_p = topos.get(P)
        groups_p: dict[int, dict] = {}
        # Phase 12b: the profile frame restricted to this period, ONCE, so the
        # per-member classification below is a column take rather than a
        # per-member reindex of the full column (the shipped-code review
        # measured the latter at +109 % on a 300-member, 3-period network).
        # Under copy-on-write a same-index reindex shares the buffer.
        pmp_period = (p_max_pu_t.reindex(demand_p.index)
                      if p_max_pu_t is not None and len(profile_cols) else None)

        firm_fixed = 0.0
        max_achievable = 0.0
        ext_here: list = []

        for mem in members:
            if not bool(active[mem["comp"]].get(mem["name"], False)):
                continue
            if mem["profile"] is not None and len(peak_idx):
                avail = _finite(
                    mem["profile"].reindex(peak_idx).mean(), 0.0)
            else:
                avail = mem["avail_static"]
            d = (1.0 - mem["q"]) * avail
            d = max(0.0, min(1.0, d if math.isfinite(d) else 0.0))

            capacity_mw: float | None
            stage = topo_p["stage_of"].get(mem["bus"]) if topo_p else None
            grp = (groups_p.setdefault(stage, {"U": 0.0, "dU": 0.0, "rows": []})
                   if stage is not None else None)
            if grp is not None:
                # Behind a grid_import Link: the group's credit replaces the
                # member's (added after this loop). An extendable here is
                # refused (option y), never credited.
                capacity_mw = None if mem["extendable"] else mem["p_nom"]
                if not mem["extendable"]:
                    grp["U"] += mem["p_nom"]
                    grp["dU"] += d * mem["p_nom"]
            elif mem["extendable"]:
                capacity_mw = None
                if d > 0:
                    ext_here.append((mem, d))
                max_achievable += d * mem["p_nom_max"]
            else:
                capacity_mw = mem["p_nom"]
                firm_fixed += d * mem["p_nom"]
                max_achievable += d * mem["p_nom"]

            # Phase 12h: a rate-zero unit's derate uses NO class average —
            # `(1 - 0) x avail` is the availability alone — so listing it
            # here would make preflight's "derates N assets using carrier
            # class averages" false of it. Its `source` still reads
            # `carrier_default`, which is where the (now unused) rate came
            # from; this list is about what the derate actually used.
            if mem["source"] == "carrier_default" \
                    and _finite(mem["q"], 0.0) > 0.0 \
                    and mem["name"] not in carrier_default:
                carrier_default.append(mem["name"])
            # Phase 12b: the profile leg is STASHED here, restricted to this
            # period, because it is the object the derate above was computed
            # from and it does not survive the restore — the vintage
            # expansion clones `p_max_pu` onto `wind@2030` and the restore
            # drops the column before the payload runs. Only a VARYING
            # profile is kept: a constant one shifts every hour equally and
            # cannot move a window, so stashing it would spend memory on a
            # row that is never netted (plan v4 §2.2, v5.1 §6).
            profile_kind = "none"
            profile_p = None
            if mem["profile"] is not None:
                import numpy as _np
                candidate = (pmp_period[mem["name"]] if pmp_period is not None
                             and mem["name"] in pmp_period.columns
                             else mem["profile"].reindex(demand_p.index))
                vals = candidate.to_numpy(dtype=float)
                fin = vals[_np.isfinite(vals)]
                if fin.size and (fin.max() - fin.min()) > 1e-9:
                    profile_kind = "varying"
                    profile_p = candidate
                else:
                    profile_kind = "constant"
            asset_row = {
                "name": mem["name"],
                "period": str(P),
                "kind": mem["kind"],
                "capacity_mw": capacity_mw,
                "derate": d,
                "q": float(mem["q"]),
                "basis": mem["basis"],
                "source": mem["source"],
                "extendable": bool(mem["extendable"]),
                "energy_limited": bool(mem["energy_limited"]),
                "profile_kind": profile_kind,
                "nettable": profile_kind == "varying",
                "profile": profile_p,
            }
            stash["assets"].append(asset_row)
            if grp is not None:
                grp["rows"].append((asset_row, bool(mem["extendable"]),
                                    0.0 if mem["extendable"] else d * mem["p_nom"]))

        if topo_p is not None and topo_p["next"]:
            notes = {"nodata": link_nodata, "bad": link_bad}
            link_cap = _import_link_caps(
                n, P, topo_p, snapshots[in_p.to_numpy()], _active("links", P),
                link_q, energized, notes)
            credit = _import_group_credit(P, topo_p, groups_p, link_cap, notes)
            firm_fixed += credit
            max_achievable += credit

        terms[str(P)] = ext_here
        ext_sets.append(frozenset(m["name"] for m, _d in ext_here))
        stash["periods"][str(P)] = {
            "peak_mw": peak,
            "peak_snapshots": [_snapshot_label(s) for s in peak_idx],
            "n_peak_hours": int(len(peak_idx)),
            "required_mw": required,
            "firm_fixed_mw": firm_fixed,
            "max_achievable_mw": max_achievable,
            # Phase 12b: the scaled demand series itself, so the payload can
            # select a net-load window on the SAME demand the constraint was
            # built on. Restore reverts the load scaling; a post-solve re-read
            # would be a different system. In memory only, never serialised.
            "demand_mw": demand_p,
            "peak_hours_override": peak_hours_override,
        }

    # One horizon-wide variable set ⇒ one horizon-wide standard at the
    # maximum peak, NOT a per-period one. Say which it is (§2.1).
    stash["horizon_wide"] = len(set(ext_sets)) <= 1

    return {
        "stash": stash,
        "terms": terms,
        "unpriceable": unpriceable,
        "carrier_default": carrier_default,
        "import_group_extendable": import_group_extendable,
        "import_link_extendable": import_link_extendable,
    }
