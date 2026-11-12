"""Minimum renewable fraction (homer-parity 01a, owner decision HOMER-GAPS-1).

HOMER Pro's renewable fraction, on the electrical side:

    f_ren = 1 − E_nonren / E_served

* ``E_served`` — the Loads on electrical buses (the p_set the LP solves, so
  after the solve-time load scalers) less every slack's supply on those buses
  (VOLL shed, demand response, data-centre curtailment): shed or curtailed
  energy is not served.
* ``E_nonren`` — the electricity that non-renewable sources put into the
  served network:

  - a Generator on an electrical bus whose carrier is not renewable
    (`carrier_kpis._is_renewable_carrier`; grid import, diesel, gas, …),
    counted gross (its positive output only);
  - a Link out of a *supply bus* into an electrical bus that is not one,
    counted as η(t)·p0 (positive part). A supply bus has a non-renewable,
    non-slack Generator, no Load, no Line or Transformer, and a Link into an
    electrical bus: a fuel bus feeding a genset Link, or a grid bus feeding an
    import Link (electrical or not). Its own Generator is then not counted,
    so nothing is counted twice. A bus fed by both a renewable and a
    non-renewable Generator counts whole (conservative).

  Storage (StorageUnit, Store and the Links around them), electrolyser and
  fuel-cell loops, and elec→elec converters are not sources: what they put
  out was generated upstream. Heat and hydrogen loads are out of scope.

Energy is MW × ``snapshot_weightings.generators`` per year of each period;
the horizon totals weight each period by its ``years``.

`renewable_fraction(n)` is the KPI: a pure function of the solved network
(plus the lost-load capture, which a solve removes from the network), shown
whether or not the constraint is on. homer-parity 03a ranks architectures
with it; there is one implementation, here.

`_wrap_with_min_renewable_fraction` adds, per investment period P, the row
``min_renewable_fraction_<P>`` (``min_renewable_fraction`` on a single-period
network):

    Σ_t∈P w_t·nonren_t + (1−f)·Σ_t∈P w_t·slack_t ≤ (1−f)·Σ_t∈P w_t·load_t

A period with no load gets no row (0 ≤ 0 is harmless but says nothing).
With a slack present the LP may meet the target by shedding or curtailing at
the slack's price: a target above what renewables can reach is then met by
shedding, not reported infeasible. Without a slack it is infeasible, and
`infeasibility_lines` names the row and the most renewable energy the plan
could produce.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from services.adequacy.slack import slack_generator_mask
from services.profile_shapes import _ELEC_CARRIERS
from services.results.carrier_kpis import _is_renewable_carrier

ROW = "min_renewable_fraction"
LABEL = "Minimum renewable fraction"
_AUX = "RenewableFraction-gross"


def row_name(period: Any) -> str:
    """The LP row of one investment period (None: the single-period row)."""
    return ROW if period is None else f"{ROW}_{period}"


def target_of(cfg: Any) -> float | None:
    """The configured target as a fraction in (0, 1], or None when off."""
    try:
        f = getattr(cfg, "min_renewable_fraction", None)
        f = None if f is None else float(f)
    except (TypeError, ValueError):
        return None
    if f is None or not math.isfinite(f) or f <= 0.0:
        return None
    # Not clamped: the schema refuses > 1 at entry; a path around it gets the
    # LP it asked for (a negative right-hand side, reported infeasible).
    return f


# ── classification ────────────────────────────────────────────────────────


def _is_electrical(carrier: object) -> bool:
    c = str(carrier or "").lower().strip()
    return c == "" or c in _ELEC_CARRIERS


def _electrical_buses(n) -> set[str]:
    return (
        {str(b) for b, c in n.buses["carrier"].items() if _is_electrical(c)}
        if "carrier" in n.buses.columns
        else {str(b) for b in n.buses.index}
    )


def classify(n) -> dict[str, list[str]]:
    """Which assets the fraction reads: non-renewable Generators counted at
    the generator, source Links (from a supply bus), slack Generators and
    Loads, all on electrical buses. Pure; see the module docstring."""
    elec = _electrical_buses(n)
    gens = n.generators
    slack = slack_generator_mask(gens) if len(gens) else pd.Series(dtype=bool)
    slack = slack.reindex(gens.index, fill_value=False)
    gbus = gens["bus"].astype(str) if len(gens) else pd.Series(dtype=str)
    nonren = [
        g
        for g in gens.index
        if not slack[g]
        and not _is_renewable_carrier(str(gens.at[g, "carrier"]))
        and not str(g).startswith("__lost_")
    ]
    load_buses = set(n.loads["bus"].astype(str)) if len(n.loads) else set()
    branch_buses: set[str] = set()
    for comp in (n.lines, n.transformers):
        if len(comp):
            branch_buses |= set(comp["bus0"].astype(str)) | set(
                comp["bus1"].astype(str)
            )
    links = n.links
    candidates = {gbus[g] for g in nonren} - load_buses - branch_buses
    supply = {
        b
        for b in candidates
        if len(links)
        and (
            (links["bus0"].astype(str) == b)
            & links["bus1"].astype(str).isin(elec - {b})
        ).any()
    }
    src_links = (
        [
            l
            for l in links.index
            if str(links.at[l, "bus0"]) in supply
            and str(links.at[l, "bus1"]) in elec
            and str(links.at[l, "bus1"]) not in supply
        ]
        if len(links)
        else []
    )
    return {
        "nonren_generators": [
            g for g in nonren if gbus[g] in elec and gbus[g] not in supply
        ],
        "source_links": src_links,
        "slack_generators": [g for g in gens.index if slack[g] and gbus[g] in elec],
        "loads": [l for l in n.loads.index if str(n.loads.at[l, "bus"]) in elec],
        "renewable_generators": [
            g
            for g in gens.index
            if not slack[g] and gbus[g] in elec and g not in nonren
        ],
    }


# ── weights and periods ───────────────────────────────────────────────────


def _weights(n, snapshots) -> pd.Series:
    sw = n.snapshot_weightings
    col = "generators" if "generators" in sw.columns else sw.columns[0]
    return sw.loc[snapshots, col].astype(float)


def _periods(snapshots) -> tuple[pd.Series, list]:
    """Each snapshot's period, and the periods; (None,) on a single period."""
    if isinstance(snapshots, pd.MultiIndex):
        per = pd.Series(snapshots.get_level_values(0), index=snapshots)
        return per, sorted(set(per))
    return pd.Series([None] * len(snapshots), index=snapshots, dtype=object), [None]


def _period_mask(per_of: pd.Series, period) -> pd.Series:
    """The snapshots of one period (all of them on a single-period network)."""
    return (
        (per_of == period)
        if period is not None
        else pd.Series(True, index=per_of.index)
    )


def _efficiency(n, link: str, snapshots) -> pd.Series:
    eff_t = getattr(n.links_t, "efficiency", None)
    if eff_t is not None and link in getattr(eff_t, "columns", []):
        return eff_t[link].reindex(snapshots).astype(float)
    return pd.Series(float(n.links.at[link, "efficiency"]), index=snapshots)


def _load_mw(n, loads, snapshots) -> pd.Series:
    if not loads:
        return pd.Series(0.0, index=snapshots)
    p = n.get_switchable_as_dense("Load", "p_set", snapshots)[loads]
    return p.sum(axis=1).astype(float)


# ── the KPI ───────────────────────────────────────────────────────────────


def _unserved_mw(unserved_t, snapshots) -> pd.Series:
    if unserved_t is None:
        return pd.Series(0.0, index=snapshots)
    s = unserved_t.sum(axis=1) if isinstance(unserved_t, pd.DataFrame) else unserved_t
    return pd.Series(s, dtype=float).reindex(snapshots).fillna(0.0)


def renewable_fraction(
    n, *, unserved_t: pd.DataFrame | pd.Series | None = None, snapshots=None
) -> dict[str, Any]:
    """The renewable fraction of a solved network (HOMER's definition, see
    the module docstring). ``unserved_t`` is the shed MW per snapshot of the
    solve's lost-load capture (the VOLL slacks are removed after a solve);
    slack Generators still on the network count as well.

    Returns ``fraction`` (None, with ``reason``, when nothing was served or
    no dispatch is present; not clipped: like HOMER's, it is negative when
    non-renewable output exceeds what was served, e.g. a genset charging a
    battery), ``renewable_mwh``, ``nonrenewable_mwh``,
    ``served_mwh`` (horizon totals, period years applied) and ``per_period``.
    """
    sns = n.snapshots if snapshots is None else snapshots
    out: dict[str, Any] = {
        "fraction": None,
        "renewable_mwh": None,
        "nonrenewable_mwh": None,
        "served_mwh": None,
        "per_period": [],
        "reason": None,
    }
    gp = n.generators_t.p
    if len(n.generators) and (gp is None or gp.empty):
        out["reason"] = "no dispatch: the network has not been solved"
        return out
    lp = n.loads_t.p
    cls = classify(n)
    if cls["loads"] and (
        lp is None or lp.empty or not set(cls["loads"]) <= set(lp.columns)
    ):
        out["reason"] = "no dispatch: the network has not been solved"
        return out

    def _col(frame, name):
        if frame is None or name not in getattr(frame, "columns", []):
            return pd.Series(0.0, index=sns)
        return frame[name].reindex(sns).astype(float).fillna(0.0)

    nonren = pd.Series(0.0, index=sns)
    for g in cls["nonren_generators"]:
        nonren += _col(gp, g).clip(lower=0.0)
    for l in cls["source_links"]:
        nonren += _col(n.links_t.p0, l).clip(lower=0.0) * _efficiency(n, l, sns)
    slack = pd.Series(0.0, index=sns)
    for g in cls["slack_generators"]:
        slack += _col(gp, g).clip(lower=0.0)
    load = sum((_col(lp, l) for l in cls["loads"]), pd.Series(0.0, index=sns))
    served_t = load - slack - _unserved_mw(unserved_t, sns)

    w = _weights(n, sns)
    per_of, periods = _periods(sns)
    years = n.investment_period_weightings["years"] if periods != [None] else None
    tot_served = tot_nonren = 0.0
    for P in periods:
        mask = _period_mask(per_of, P)
        served = float((w * served_t)[mask].sum())
        nr = float((w * nonren)[mask].sum())
        out["per_period"].append(
            {
                "period": P,
                "served_mwh": served,
                "nonrenewable_mwh": nr,
                "renewable_mwh": served - nr,
                "fraction": (served - nr) / served if served > 0 else None,
            }
        )
        y = float(years.get(P, 1.0)) if years is not None else 1.0
        tot_served += y * served
        tot_nonren += y * nr
    out.update(
        served_mwh=tot_served,
        nonrenewable_mwh=tot_nonren,
        renewable_mwh=tot_served - tot_nonren,
    )
    if tot_served > 0:
        out["fraction"] = (tot_served - tot_nonren) / tot_served
    else:
        out["reason"] = "no electricity served: the fraction is undefined"
    return out


def renewable_fraction_result(
    n, capture: dict | None, target: float | None
) -> dict[str, Any]:
    """The /results/renewable_fraction payload: the KPI with the solve's
    lost-load capture as the shed (a capture keyed to other snapshots is
    another solve's and is refused, with a reason, rather than dropped), and
    the target the solve enforced (None when off)."""
    shed = (capture or {}).get("lost_load_t")
    if shed is not None and (
        not isinstance(shed, (pd.DataFrame, pd.Series))
        or not shed.index.equals(n.snapshots)
    ):
        kpi = renewable_fraction(n)
        kpi.update(
            fraction=None, reason="the lost-load capture belongs to another solve"
        )
    else:
        kpi = renewable_fraction(n, unserved_t=shed)
    return {**kpi, "target": target}


# ── the LP row ────────────────────────────────────────────────────────────


def _safe_log(log_queue, msg: str) -> None:
    if log_queue is None:
        return
    try:
        log_queue.put(f"[RENEWABLE] {msg}")
    except Exception:  # noqa: BLE001
        pass


def _gross(m, var, names: list[str], lower) -> Any:
    """``var`` for ``names``, or a gross (≥ 0, ≥ var) auxiliary for those
    that may run negative (an export or a bidirectional import Link)."""
    v = var.sel(name=names)
    neg = [nm for nm, lo in zip(names, lower) if lo < 0]
    if not neg:
        return v
    aux_name = f"{_AUX}-{var.name}"
    z = m.add_variables(
        lower=0.0,
        coords=[v.coords["snapshot"], pd.Index(neg, name="name")],
        name=aux_name,
    )
    m.add_constraints(z - var.sel(name=neg) >= 0, name=f"{aux_name}-def")
    pos = [nm for nm in names if nm not in neg]
    return (z, var.sel(name=pos) if pos else None)


def _weighted_sum(var_or_pair, coef: pd.DataFrame, snap_coord):
    """Σ_t,name coef[t,name]·var[t,name]; ``var_or_pair`` may be (aux, rest)."""
    import xarray as xr

    def one(v):
        if v is None:
            return None
        names = list(v.indexes["name"])
        da = xr.DataArray(
            coef[names].values,
            dims=["snapshot", "name"],
            coords={"snapshot": snap_coord, "name": names},
        )
        return (v * da).sum()

    if isinstance(var_or_pair, tuple):
        a, b = (one(x) for x in var_or_pair)
        return a if b is None else a + b
    return one(var_or_pair)


def _wrap_with_min_renewable_fraction(network, user_fn, cfg, log_queue=None):
    """Compose extra_functionality with the minimum-renewable-fraction rows
    (one per investment period; see the module docstring). Off (None, 0,
    non-finite) returns ``user_fn`` unchanged, so the LP is untouched."""
    f = target_of(cfg)
    if f is None:
        return user_fn

    def rows(n, snapshots):
        m = n.model
        cls = classify(n)
        w = _weights(n, snapshots)
        per_of, periods = _periods(snapshots)
        load = _load_mw(n, cls["loads"], snapshots)
        gvar = m.variables["Generator-p"] if "Generator-p" in m.variables else None
        lvar = m.variables["Link-p"] if "Link-p" in m.variables else None
        coord = next(
            (v.coords["snapshot"] for v in (gvar, lvar) if v is not None), None
        )
        gens = [
            g
            for g in cls["nonren_generators"]
            if gvar is not None and g in gvar.indexes["name"]
        ]
        links = [
            l
            for l in cls["source_links"]
            if lvar is not None and l in lvar.indexes["name"]
        ]
        slacks = [
            g
            for g in cls["slack_generators"]
            if gvar is not None and g in gvar.indexes["name"]
        ]
        pmin_g = n.get_switchable_as_dense("Generator", "p_min_pu", snapshots)
        pmin_l = n.get_switchable_as_dense("Link", "p_min_pu", snapshots)
        g_src = (
            _gross(m, gvar, gens, [float(pmin_g[g].min()) for g in gens])
            if gens
            else None
        )
        l_src = (
            _gross(m, lvar, links, [float(pmin_l[l].min()) for l in links])
            if links
            else None
        )
        eff = pd.DataFrame(
            {l: _efficiency(n, l, snapshots) for l in links}, index=snapshots
        )
        for P in periods:
            in_p = _period_mask(per_of, P)
            w_p = w.where(in_p, 0.0)
            demand = float((w_p * load).sum())
            name = row_name(P)
            if demand <= 0.0:
                _safe_log(
                    log_queue,
                    f"{LABEL}: period {P if P is not None else 'all'} has no "
                    f"electrical load; no row ({name}) added.",
                )
                continue
            terms = []
            if gens:
                terms.append(
                    _weighted_sum(g_src, pd.DataFrame({g: w_p for g in gens}), coord)
                )
            if links:
                terms.append(_weighted_sum(l_src, eff.mul(w_p, axis=0), coord))
            if slacks:
                terms.append(
                    _weighted_sum(
                        gvar.sel(name=slacks),
                        pd.DataFrame({g: w_p * (1.0 - f) for g in slacks}),
                        coord,
                    )
                )
            if not terms:
                _safe_log(
                    log_queue,
                    f"{LABEL}: no non-renewable source or slack on the electrical "
                    f"buses; {name} is met trivially and not added.",
                )
                continue
            expr = terms[0]
            for t in terms[1:]:
                expr = expr + t
            rhs = (1.0 - f) * demand
            m.add_constraints(expr <= rhs, name=name)
            _safe_log(
                log_queue,
                f"{LABEL} {f:.1%} (row {name}): non-renewable supply ≤ "
                f"{rhs:,.1f} MWh of {demand:,.1f} MWh electrical load"
                + (
                    f"; {len(slacks)} slack(s) may shed at their price instead"
                    if slacks
                    else ""
                )
                + ".",
            )

    def wrapper(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        rows(n, snapshots)

    return wrapper


# ── infeasibility ─────────────────────────────────────────────────────────


def max_renewable_mwh(n, snapshots=None) -> dict[Any, float]:
    """Per period, the most renewable electricity the plan could produce:
    Σ w·p_max_pu·(p_nom_max if extendable else p_nom) over the renewable
    Generators on electrical buses (∞ when one is extendable without a cap)."""
    sns = n.snapshots if snapshots is None else snapshots
    cls = classify(n)
    w = _weights(n, sns)
    per_of, periods = _periods(sns)
    gens = cls["renewable_generators"]
    pmax = n.get_switchable_as_dense("Generator", "p_max_pu", sns)
    out: dict[Any, float] = {}
    for P in periods:
        in_p = _period_mask(per_of, P)
        tot = 0.0
        for g in gens:
            ext = bool(n.generators.at[g, "p_nom_extendable"])
            cap = float(
                n.generators.at[g, "p_nom_max"] if ext else n.generators.at[g, "p_nom"]
            )
            tot += float((w.where(in_p, 0.0) * pmax[g].clip(lower=0.0)).sum()) * cap
        out[P] = tot if np.isfinite(tot) else math.inf
    return out


def infeasibility_lines(n, cfg) -> list[str]:
    """The hints of an infeasible solve with the target on: the row per
    period, the renewable energy the target needs and the most the plan can
    produce. Empty when the target is off."""
    f = target_of(cfg)
    if f is None:
        return []
    cls = classify(n)
    per_of, periods = _periods(n.snapshots)
    w = _weights(n, n.snapshots)
    load = _load_mw(n, cls["loads"], n.snapshots)
    reach = max_renewable_mwh(n)
    lines = []
    for P in periods:
        in_p = _period_mask(per_of, P)
        demand = float((w.where(in_p, 0.0) * load).sum())
        if demand <= 0:
            continue
        need = f * demand
        can = reach.get(P, 0.0)
        where = "" if P is None else f" in {P}"
        lines.append(
            f"{LABEL}: the target of {f:.0%}{where} (row {row_name(P)}) needs "
            f"{need:,.1f} MWh of renewable electricity served; the renewable "
            f"generators can produce at most "
            + ("an unbounded amount" if math.isinf(can) else f"{can:,.1f} MWh")
            + ". Lower the target, raise a renewable p_nom_max or add a VOLL slack to "
            "allow shedding instead."
        )
    return lines
