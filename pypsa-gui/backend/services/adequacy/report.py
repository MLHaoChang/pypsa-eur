"""
Assemble the minimal AdequacyReport after a target-constrained solve.

Design: spec §§5.5, 7; plan Phase 1 Task 3. Everything here is arithmetic
over SOLVE-TIME truth handed in by the solver:

* ``targets`` — what `_wrap_with_ens_cap` actually enforced, stashed on the
  network at optimize time. Restore reverts the load-scaling transforms, so
  recomputing the demand denominator post-solve would drift from what the LP
  saw — the same two-readers-drift bug class the lost-load capture had.
* ``captured`` — the lost-load capture, whose weighted per-bus-per-period
  energies and electrical shed-hours are computed at capture time, before
  the slack generators are stripped.

Binding classification (spec §5.5): whichever standard actually shaped the
plan — a zone at its ceiling wins over the system cap, which wins over
"voll" (the cap was set but VoLL economics shed less than it allowed). The
user must be able to see which; two users with the same stated target
otherwise get different plans for unobservable reasons.
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd
from dataclasses import asdict, is_dataclass

import pandas as pd

from models.adequacy import (
    PeriodTarget,
    AdequacyReport,
    CostBlock,
    EnergyBlock,
    InputsBlock,
    MetricsBlock,
    ReserveMarginBlock,
    SystemTarget,
    TargetBlock,
    VollBlock,
    ZoneTarget,
)
from services.adequacy.metrics import (
    electrical_columns,
    horizon_years,
    resolve_time_basis,
)

# A cap counts as binding when achieved shed reaches this share of it —
# LP tolerances keep the achieved value epsilon under the cap.
BINDING_TOLERANCE = 1e-3


def _config_hash(cfg) -> str:
    try:
        payload = asdict(cfg) if is_dataclass(cfg) else dict(vars(cfg))
        blob = json.dumps(payload, sort_keys=True, default=str)
    except Exception:
        blob = repr(cfg)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _outage_basis_counts(n) -> dict[str, int]:
    """How many assets entered occurrence data on each basis (asset-level
    rows only — carrier defaults are library values, not user inputs)."""
    from services.adequacy.occurrence import resolve_outage_params

    counts: dict[str, int] = {}
    for component in ("generators", "storage_units", "stores", "links", "lines"):
        try:
            params = resolve_outage_params(n, component)
        except Exception:
            continue
        asset_rows = params[params["source"] == "asset"]
        for basis in asset_rows["basis"]:
            b = str(basis)
            counts[b] = counts.get(b, 0) + 1
    return counts


def reserve_margin_payload(n, targets: dict) -> dict:
    """
    The firm-capacity result: the solve-time stash (§2.6) joined to what the
    solve actually BUILT.

    ``targets`` is ``n._reserve_margin_targets`` — peaks, requirements and
    derating factors measured at LP-build time, when the load-scaling
    transforms were still applied. Only the CAPACITIES are read back off the
    network here (``p_nom_opt`` is the solve's answer and the restore step
    does not touch it); every demand-derived number comes from the stash,
    because recomputing a peak after restore reads different loads and drifts
    from the standard the LP enforced.

    ``max_achievable_mw`` is left EXACTLY as stashed, ``inf`` included — the
    honest value, and not JSON-serialisable. ``sanitize_reserve_margin_payload``
    is what makes it wire-safe, at the two surfaces that put it on the wire.
    """
    stash = targets or {}
    opt: dict[str, dict[str, float]] = {}
    for comp, kind in (("generators", "generator"), ("storage_units", "storage")):
        df = getattr(n, comp, None)
        if df is None or getattr(df, "empty", True):
            continue
        col = "p_nom_opt" if "p_nom_opt" in df.columns else "p_nom"
        opt[kind] = {str(k): float(v or 0.0) for k, v in df[col].items()}

    # Vintage rows are TRANSIENT. On a multi-period network with per-period
    # capacity bounds, `apply_vintage_bounds` expands `wind` into `wind@2030`,
    # `wind@2040` for the LP, the wrapper stashes those names, and the restore
    # drops the rows BEFORE this function runs — so the live lookup below
    # missed, credited zero, and a plan that built 35 MW of wind and met the
    # margin was reported `met=False` (found by the Phase 12b review while
    # checking a premise, not by looking). The built size is not lost: the
    # restore persists a per-vintage breakdown into `n.meta`, keyed by the
    # PyPSA class and the parent name, and that is what is read here.
    vintage_results = (getattr(n, "meta", None) or {}).get("vintage_results") or {}
    _class_of_kind = {"generator": "Generator", "storage": "StorageUnit"}

    def _built_vintage(kind: str, name: str) -> float | None:
        """`p_nom_opt` of the vintage `<parent>@<build_year>`, or None if the
        name is not a vintage this solve expanded. A user-named `foo@bar` with
        no breakdown entry falls through to None rather than being guessed."""
        parent, sep, suffix = name.rpartition("@")
        if not sep:
            return None
        try:
            build_year = int(suffix)
        except ValueError:
            return None
        by_class = vintage_results.get(_class_of_kind.get(kind, ""))
        entry = by_class.get(parent) if isinstance(by_class, dict) else None
        if not isinstance(entry, dict):
            return None
        for per in entry.get("periods") or []:
            try:
                if int(per.get("build_year")) == build_year:
                    return float(per.get("p_nom_opt") or 0.0)
            except (TypeError, ValueError):
                continue
        return None

    def _built(row) -> float | None:
        """The asset's capacity in the SOLVED plan. For a fixed asset that is
        the stash's constant; for an extendable it is `p_nom_opt`, which is
        the whole point of the standard — the capacity it forced into being.
        A vintage row is read from the persisted breakdown (above)."""
        if not row.get("extendable"):
            cap = row.get("capacity_mw")
            return None if cap is None else float(cap)
        kind, name = str(row.get("kind")), str(row.get("name"))
        val = opt.get(kind, {}).get(name)
        if val is None:
            val = _built_vintage(kind, name)
        return None if val is None else float(val)

    def _fin(v, default: float) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return default
        return f if math.isfinite(f) else default

    # ── first pass: built capacity and the gross credit, per row ──────────
    rows_by_period: dict[str, list[tuple[dict, float | None]]] = {}
    firm_built: dict[str, float] = {}
    bases: dict[str, set] = {}
    for row in (stash.get("assets") or []):
        cap = _built(row)
        derate = float(row.get("derate", 0.0) or 0.0)
        firm = derate * cap if cap is not None else 0.0
        period = str(row.get("period", "ALL"))
        if row.get("extendable"):
            firm_built[period] = firm_built.get(period, 0.0) + firm
        bases.setdefault(str(row.get("basis") or ""), set()).add(str(row.get("name")))
        rows_by_period.setdefault(period, []).append((row, cap))

    # ── the net-load window, per period (Phase 12b) ───────────────────────
    #
    # The margin credits every profile-bearing member on the hours when
    # GROSS demand peaks; a system with such members runs short on the hours
    # when NET demand peaks, and those are not the same hours. This selects
    # the second window by the SAME rule on the SAME stashed demand, nets
    # only rows that are `nettable` (a varying profile) AND built, and
    # reports what each member's derate would have been on it. A second
    # proxy, never a correction — and "netted capacity" is not "VRE": it is
    # every unit whose availability varies, thermal maintenance included.
    from services.adequacy.window import peak_window

    net_by_period: dict[str, dict] = {}
    derate_net_by_row: dict[int, float | None] = {}
    netted_by_row: dict[int, bool] = {}
    for P, per in (stash.get("periods") or {}).items():
        P = str(P)
        demand = per.get("demand_mw")
        rows = rows_by_period.get(P, [])
        netted_rows = []
        for row, cap in rows:
            netted = bool(row.get("nettable")) and cap is not None and cap > 0
            netted_by_row[id(row)] = netted
            if netted:
                netted_rows.append((row, cap))
        block = {
            "status": "ok",
            "netted_assets": [str(r.get("name")) for r, _c in netted_rows],
            "snapshots": [],
            "n_hours": 0,
            "net_peak_mw": None,
            "gross_at_net_peak_mw": None,
            "netted_mw": None,
            "overlap_hours": None,
            "firm_gross_mw": None,
            "firm_net_mw": None,
        }
        demand_ok = (demand is not None and len(demand) > 0
                     and bool(np.isfinite(demand.to_numpy(dtype=float)).any()))
        if not demand_ok:
            block["status"] = "no_finite_demand"
        elif not netted_rows:
            block["status"] = "nothing_netted"
        else:
            # Rule 1: an hour whose availability is UNKNOWN is netted as
            # unavailable (fillna 0) — pandas would otherwise skip it
            # silently and the hour would drop out of the window.
            net = demand.astype(float).copy()
            netted_series = pd.Series(0.0, index=demand.index)
            for row, cap in netted_rows:
                # The ONE line that implements rule 1. A plain `+` follows on
                # purpose: `Series.add(..., fill_value=0.0)` would re-apply
                # the rule by accident and hide a regression of this line —
                # the first bite of B10 did not bite for exactly that reason.
                contrib = row["profile"].reindex(demand.index).fillna(0.0) * float(cap)
                netted_series = netted_series + contrib
            net = net - netted_series
            net_idx = peak_window(net, n_override=per.get("peak_hours_override"))
            if len(net_idx) == 0:
                block["status"] = "no_finite_demand"
            else:
                gross_snaps = set(per.get("peak_snapshots") or [])
                net_snaps = [str(x) for x in net_idx]
                firm_gross = 0.0
                firm_net = 0.0
                for row, cap in rows:
                    kind = str(row.get("profile_kind") or "none")
                    if kind == "none":
                        continue
                    derate = float(row.get("derate", 0.0) or 0.0)
                    if kind == "varying":
                        # Rule 2: derate_net is `derate`'s own expression on
                        # the other window — a `_finite`-guarded skipna mean,
                        # so an all-NaN window reads 0.0, not NaN.
                        q = _fin(row.get("q"), 0.0)
                        avail = _fin(row["profile"].reindex(net_idx).mean(), 0.0)
                        dn = (1.0 - q) * avail
                        dn = max(0.0, min(1.0, dn if math.isfinite(dn) else 0.0))
                        derate_net_by_row[id(row)] = dn
                    else:
                        dn = derate            # constant: window-independent
                    if cap is not None:
                        firm_gross += derate * cap
                        firm_net += dn * cap
                block.update({
                    "snapshots": net_snaps,
                    "n_hours": int(len(net_idx)),
                    "net_peak_mw": _fin(net.max(), 0.0),
                    "gross_at_net_peak_mw": _fin(demand.reindex(net_idx).mean(), 0.0),
                    "netted_mw": _fin(netted_series.mean(), 0.0),
                    "overlap_hours": int(len(gross_snaps & set(net_snaps))),
                    "firm_gross_mw": firm_gross,
                    "firm_net_mw": firm_net,
                })
        net_by_period[P] = block

    # ── second pass: the asset rows, now with the net-window fields ───────
    assets: list[dict] = []
    for P in sorted(rows_by_period):
        for row, cap in rows_by_period[P]:
            derate = float(row.get("derate", 0.0) or 0.0)
            firm = derate * cap if cap is not None else 0.0
            assets.append({
                "name": str(row.get("name")),
                "period": P,
                "kind": str(row.get("kind")),
                "capacity_mw": cap,
                "derate": derate,
                "basis": str(row.get("basis") or ""),
                "source": str(row.get("source") or ""),
                "extendable": bool(row.get("extendable")),
                "firm_mw": firm,
                "energy_limited": bool(row.get("energy_limited")),
                "profile_kind": str(row.get("profile_kind") or "none"),
                "nettable": bool(row.get("nettable")),
                "netted": netted_by_row.get(id(row), False),
                "derate_net": derate_net_by_row.get(id(row)),
            })

    by_period: list[dict] = []
    for P, per in sorted((stash.get("periods") or {}).items()):
        peak = float(per.get("peak_mw", 0.0) or 0.0)
        required = float(per.get("required_mw", 0.0) or 0.0)
        firm = float(per.get("firm_fixed_mw", 0.0) or 0.0) + firm_built.get(str(P), 0.0)
        met = required > 0 and firm >= required * (1.0 - BINDING_TOLERANCE)
        by_period.append({
            "period": str(P),
            "peak_mw": peak,
            "required_mw": required,
            "firm_mw": firm,
            "margin_achieved": (firm / peak - 1.0) if peak > 0 else None,
            "met": bool(met),
            # Binding = the standard SHAPED the plan: firm capacity sitting on
            # the constraint's bound. A margin the fixed fleet already meets is
            # met and NOT binding, and saying otherwise would credit the margin
            # for capacity that was always there.
            "binding": bool(met and firm <= required * (1.0 + BINDING_TOLERANCE)),
            "n_peak_hours": int(per.get("n_peak_hours", 0) or 0),
            "peak_snapshots": [str(x) for x in (per.get("peak_snapshots") or [])],
            "max_achievable_mw": float(per.get("max_achievable_mw", 0.0) or 0.0),
            "net_window": net_by_period.get(str(P)),
        })

    return {
        "margin": float(stash.get("margin", 0.0) or 0.0),
        "horizon_wide": bool(stash.get("horizon_wide", False)),
        "by_period": by_period,
        "assets": assets,
        "derating_bases": {b: len(names) for b, names in sorted(bases.items())},
    }


def sanitize_reserve_margin_payload(payload: dict) -> dict:
    """
    Make a reserve-margin payload safe to put on the wire (amendment 6).

    ``max_achievable_mw`` is ``inf`` whenever an active extendable has an
    unbounded ``p_nom_max``. That is the mathematically right value — and
    Starlette serialises responses with ``allow_nan=False``, so an untouched
    ``inf`` raises inside the response and the panel gets a 500 instead of a
    report. It is NULLED rather than clamped: "unbounded" is not a number, and
    a clamp would invent a ceiling nobody entered and make §3's
    ``max_achievable < required`` test fire by accident. The companion flag is
    what tells a reader which case the null is.
    """
    out = dict(payload or {})
    rows: list[dict] = []
    for row in (out.get("by_period") or []):
        r = dict(row)
        val = r.get("max_achievable_mw")
        try:
            fval = float(val) if val is not None else None
        except (TypeError, ValueError):
            fval = None
        unbounded = fval is not None and math.isinf(fval)
        r["max_achievable_unbounded"] = bool(unbounded)
        r["max_achievable_mw"] = (
            None if fval is None or not math.isfinite(fval) else fval)
        for key in ("peak_mw", "required_mw", "firm_mw", "margin_achieved"):
            v = r.get(key)
            try:
                if v is not None and not math.isfinite(float(v)):
                    r[key] = None
            except (TypeError, ValueError):
                r[key] = None
        # Phase 12b: descend into the net-window block. Its numerics are
        # `_finite`-guarded upstream; this is belt and braces, because a NaN
        # in a nested dict is exactly as much of a 500 as one at the top.
        nw = r.get("net_window")
        if isinstance(nw, dict):
            nw = dict(nw)
            for key in ("net_peak_mw", "gross_at_net_peak_mw", "netted_mw",
                        "firm_gross_mw", "firm_net_mw"):
                v = nw.get(key)
                try:
                    if v is not None and not math.isfinite(float(v)):
                        nw[key] = None
                except (TypeError, ValueError):
                    nw[key] = None
            r["net_window"] = nw
        rows.append(r)
    out["by_period"] = rows
    # Asset rows were never sanitised: `derate` is clamped upstream so it was
    # safe by accident. `derate_net` is guarded upstream too, and is nulled
    # here as well so the guard is not the only thing between a NaN and a 500.
    arows: list[dict] = []
    for row in (out.get("assets") or []):
        a = dict(row)
        for key in ("derate_net", "firm_mw", "derate"):
            v = a.get(key)
            try:
                if v is not None and not math.isfinite(float(v)):
                    a[key] = None
            except (TypeError, ValueError):
                a[key] = None
        arows.append(a)
    out["assets"] = arows
    return out


def build_adequacy_report(n, cfg, targets: dict, captured: dict,
                          margin_payload: dict | None = None,
                          status: str = "ok") -> dict:
    """
    Returns the report as a plain dict (``model_dump``) — the solver state is
    pickled, and a dict keeps the pickle free of model-class coupling.

    ``targets``: ``{"permyriad", "zone_multiple", "zone_of_bus": {bus: zone},
    "periods": {P: {"cap_mwh", "demand_mwh", "zones": {z: cap_mwh}}}}``.
    ``captured`` may be empty (target on, nothing shed). Both may be EMPTY on
    a margin-only run: the report fires when EITHER standard was enforced, or
    the margin is invisible exactly when it is the only one (plan §3).

    ``margin_payload`` is ``reserve_margin_payload``'s output;
    ``status`` gates it. That guard is the one QA round 2 forced onto the ENS
    cap after an infeasible solve published a "target met" report: with no
    dispatch to measure, a republished margin block would describe the
    PREVIOUS solve's plan under this one's name.
    """
    periods: dict = targets.get("periods", {})
    zone_of_bus: dict = targets.get("zone_of_bus", {}) or {}

    # Achieved, from the capture's solve-time weighted energies.
    bp = captured.get("lost_load_bus_period_mwh")
    if bp is None or getattr(bp, "empty", True):
        bp = pd.DataFrame()
    elec_cols = electrical_columns(n, list(bp.columns)) if not bp.empty else []

    def _achieved(period, cols) -> float:
        if bp.empty or period not in bp.index:
            return 0.0
        cols = [c for c in cols if c in bp.columns]
        return float(bp.loc[period, cols].sum()) if cols else 0.0

    sys_cap_total = 0.0
    sys_achieved_total = 0.0
    binding_system = False
    zone_rows: dict[str, dict] = {}
    # Per-period rows are kept, not just summed. The cap binds PER PERIOD, so
    # the totals can read as comfortable while the period that actually bound
    # has zero headroom — "ENS 1800 / cap 3600" for two periods capped at 1800
    # each, one of them exactly on its limit.
    period_rows: list[dict] = []
    for P, tp in periods.items():
        cap_p = float(tp.get("cap_mwh", 0.0))
        ach_p = _achieved(P, elec_cols)
        sys_cap_total += cap_p
        sys_achieved_total += ach_p
        binding_p = bool(cap_p > 0 and ach_p >= cap_p * (1.0 - BINDING_TOLERANCE))
        period_rows.append({"period": str(P), "cap_mwh": cap_p,
                            "achieved_ens_mwh": ach_p, "binding": binding_p})
        if binding_p:
            binding_system = True
        for z, z_cap in (tp.get("zones") or {}).items():
            z_cols = [c for c in elec_cols if zone_of_bus.get(c, "") == z]
            z_ach = _achieved(P, z_cols)
            row = zone_rows.setdefault(z, {"cap": 0.0, "ach": 0.0, "binding": False})
            row["cap"] += float(z_cap)
            row["ach"] += z_ach
            if z_cap > 0 and z_ach >= float(z_cap) * (1.0 - BINDING_TOLERANCE):
                row["binding"] = True

    binding = (
        "zone_cap" if any(r["binding"] for r in zone_rows.values())
        else "system_cap" if binding_system
        else "voll"
    )

    sh = captured.get("shed_hours_electrical") or {"total": 0.0}
    involuntary_mwh = float(captured.get("lost_load_total_mwh", 0.0) or 0.0)
    shed_cost = float(captured.get("lost_load_cost_eur", 0.0) or 0.0)

    try:
        objective = float(n.objective)
    except (TypeError, ValueError, AttributeError):
        objective = float("nan")
    total_system_cost = objective - shed_cost if math.isfinite(objective) else 0.0

    # Zone field population — computed from the network, not from targets:
    # it must be reportable even when no zone ceiling was requested.
    zone_field_populated = False
    buses = getattr(n, "buses", None)
    loads = getattr(n, "loads", None)
    if buses is not None and "country" in getattr(buses, "columns", []) and loads is not None:
        load_buses = set(map(str, loads["bus"])) if "bus" in loads.columns else set()
        for b in buses.index:
            if str(b) in load_buses and str(buses.at[b, "country"] or "").strip():
                zone_field_populated = True
                break

    # Derive the time basis rather than asserting one — see
    # services/adequacy/metrics.resolve_time_basis for why the hardcoded
    # "hours_per_year" was a false claim on any horizon shorter than a year.
    _nyears = horizon_years(n)
    _basis = resolve_time_basis(_nyears)
    multi = bool(getattr(cfg, "multi_investment_periods", False))
    reserve_margin = None
    if margin_payload and str(status) in ("ok", "optimal"):
        reserve_margin = ReserveMarginBlock.model_validate(
            sanitize_reserve_margin_payload(margin_payload))

    report = AdequacyReport(
        engine="lp_proxy",
        fidelity="deterministic_scenario",
        reserve_margin=reserve_margin,
        target=TargetBlock(
            basis="energy",
            system=SystemTarget(
                cap_mwh=sys_cap_total,
                achieved_ens_mwh=sys_achieved_total,
                achieved_shed_hours=float(sh.get("total", 0.0)),
                by_period=[PeriodTarget(**r) for r in
                           sorted(period_rows, key=lambda r: r["period"])],
            ),
            zones=[
                ZoneTarget(zone=z, cap_mwh=r["cap"], achieved_ens_mwh=r["ach"],
                           binding=r["binding"])
                for z, r in sorted(zone_rows.items())
            ],
            binding=binding,
            zone_field_populated=zone_field_populated,
            energy_target_set=bool(periods),
        ),
        metrics=MetricsBlock(
            # `sys_achieved_total` accumulates INSIDE the loop over the ENS
            # cap's target periods, so on a margin-only run (no periods) it
            # stays 0.0 — and the report shipped `ens_mwh = 0.0` beside a
            # non-zero `shed_hours`: the system shed in every hour and shed no
            # energy. The capture's own total is the truth in that case; the
            # targeted path keeps its arithmetic untouched, because there the
            # two agree and its identity (objective − cost == ENS × VoLL) is
            # pinned live in S15.7.
            ens_mwh=(sys_achieved_total if periods else involuntary_mwh),
            shed_hours=float(sh.get("total", 0.0)),
            time_basis=_basis,
            horizon_years=_nyears,
        ),
        cost=CostBlock(
            total_system_cost_eur=total_system_cost,
            period_basis="npv_multi_period" if multi else "single_period",
        ),
        inputs=InputsBlock(
            weather_years=["modelled"],
            voll=VollBlock(default_eur_per_mwh=float(getattr(cfg, "voll", 0.0) or 0.0)),
            assumptions_hash=_config_hash(cfg),
            outage_rate_bases=_outage_basis_counts(n),
        ),
        energy=EnergyBlock(
            involuntary_mwh=involuntary_mwh,
            demand_response_mwh=float(captured.get("dsr_total_mwh", 0.0) or 0.0),
        ),
    )
    return report.model_dump()
