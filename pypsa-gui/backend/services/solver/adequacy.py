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


def reserve_margin_facts(n, cfg, snapshots=None, emit=None, *,
                         demand_scaled_in_place: bool = False) -> dict | None:
    """
    Everything the firm-capacity standard knows BEFORE an LP exists (§2, §3).

    Returns ``None`` when no margin is set. Otherwise::

        {"stash":   <the §2.6 stash, exactly the four contract keys>,
         "terms":   {period: [(member, derate), …]},   # active extendables
         "unpriceable":     [names excluded for lack of evidence],
         "carrier_default": [names credited off a carrier class average]}

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
            if mem["extendable"]:
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
            stash["assets"].append({
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
            })

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
    }
