"""
The multi-objective front: reliability × CO2 × cost (DC review issue 01).

`frontier.py` sweeps ONE axis, the ENS cap, and on a network whose dispatch
LP has no outages every point binds at VoLL, reads ENS 0 and costs the same:
the "curve" is one point and nothing says so (review README §3). This study
sweeps TWO constraint axes and measures reliability where it can vary:

* a **reliability lever**, either the ENS cap (``ens_cap_permyriad``) or the
  planning reserve margin (``reserve_margin``, the firm-capacity constraint
  in ``services/solver/adequacy.py``), and
* a **CO2 cap**, one ``primary_energy`` GlobalConstraint on
  ``co2_emissions`` per investment period, named ``co2_cap`` (single period)
  or ``co2_cap_<period>``: the interface agreed with the DC 02 goal cap.

Each point is a full capacity re-optimisation of its OWN fresh copy of one
private base copy, so the foreground network is never touched (no restore
solve is needed) and the result does not depend on the order of the grid.
Each solved fleet is then frozen and sampled by the sequential MC with the
same seed and the same, pinned, number of draws on every point (common
random numbers), so LOLE/EUE differences come from the fleet, not the noise.

The study says plainly when an axis never binds, and when every point is the
same design, instead of drawing a flat line (``degenerate``).

The front never writes to the network it was given, nor to any fingerprint
or investment-case digest: it returns a plain dict.
"""

from __future__ import annotations

import dataclasses
import itertools
import logging
import math
import queue

logger = logging.getLogger(__name__)

# A point is one capacity-expansion solve. The budget counts every LP solve
# the study makes, the CO2 reference solve included (the MC runs are sampling,
# not solves). Above it the user wants a queued job, not this study.
MAX_FRONT_SOLVES = 16

# MC per point: pinned draws (max_draws == draws), one seed for every point.
# Full certification stays with the chosen point (the EH study's mc_certify).
DEFAULT_MC_DRAWS = 500
FRONT_SEED = 0

CO2_CAP_NAME = "co2_cap"
LEVERS = ("ens_cap", "reserve_margin")

# Equality tolerances for "the same design" (capacities, cost) and for the
# dominance comparison.
_REL_TOL = 1e-6
_ABS_TOL_MW = 1e-6
_ABS_TOL_EUR = 1e-3


class FrontBudgetError(ValueError):
    pass


class FrontConfigError(ValueError):
    pass


# --------------------------------------------------------------------------
# Lever choice
# --------------------------------------------------------------------------


def _outage_series(series, has_outage_data: bool) -> bool:
    """A two-state availability series: reaches 0 and every other value is
    the series' maximum. That is the signature a sampled outage leaves, and
    a weather profile does not."""
    if not has_outage_data:
        return False
    try:
        vals = [float(v) for v in series.to_numpy().ravel()]
    except (TypeError, ValueError, AttributeError):
        return False
    vals = [v for v in vals if math.isfinite(v)]
    if not vals or min(vals) > 0:
        return False
    top = max(vals)
    return top > 0 and all(v == 0 or math.isclose(v, top) for v in vals)


def lp_has_outage_model(n) -> bool:
    """Whether the dispatch LP itself sees outages, read from the network.

    True when some generator with outage data (``outage_rate_value`` > 0)
    carries a two-state ``p_max_pu`` series (0 in an outage hour, its
    availability otherwise). Without one, the LP can always build its way
    out of shortfall at less than VoLL, so the ENS cap cannot bind.
    """
    gens = getattr(n, "generators", None)
    if gens is None or gens.empty:
        return False
    pmpu = getattr(getattr(n, "generators_t", None), "p_max_pu", None)
    if pmpu is None or pmpu.empty:
        return False
    rates = gens["outage_rate_value"] if "outage_rate_value" in gens.columns else None
    for g in pmpu.columns:
        if g not in gens.index:
            continue
        rate = float(rates.get(g, 0.0) or 0.0) if rates is not None else 0.0
        if _outage_series(pmpu[g], rate > 0 and math.isfinite(rate)):
            return True
    return False


def choose_lever(n, requested: str | None) -> tuple[str, str]:
    """``(lever, why)``. An explicit request wins; ``None``/``"auto"`` picks
    the ENS cap when the LP has an outage model, else the reserve margin."""
    if requested not in (None, "auto"):
        if requested not in LEVERS:
            raise FrontConfigError(
                f"unknown reliability lever {requested}; use one of {', '.join(LEVERS)}"
            )
        return requested, "chosen by the user"
    if lp_has_outage_model(n):
        return "ens_cap", (
            "the dispatch model has outage series, so an energy (ENS) cap can bind"
        )
    return "reserve_margin", (
        "the dispatch model has no outages, so an energy (ENS) cap cannot bind; "
        "the reserve margin changes the firm fleet, which the sampled-outage "
        "LOLE measures"
    )


# --------------------------------------------------------------------------
# Grid and budget
# --------------------------------------------------------------------------


def _clean_levels(lever: str, levels) -> list[float]:
    out: list[float] = []
    for v in levels or ():
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise FrontConfigError(f"reliability level {v} is not a number") from None
        if not math.isfinite(f) or f <= 0:
            raise FrontConfigError(
                f"reliability level {v} must be a positive number "
                f"({'‱ of demand' if lever == 'ens_cap' else 'margin fraction'})"
            )
        out.append(f)
    if not out:
        raise FrontConfigError("the front needs at least one reliability level")
    if len(set(out)) != len(out):
        raise FrontConfigError("reliability levels repeat")
    return out


def _clean_caps(caps) -> list[float | None]:
    out: list[float | None] = []
    for v in caps if caps else (None,):
        if v is None:
            out.append(None)
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise FrontConfigError(f"CO2 cap {v} is not a number") from None
        if not math.isfinite(f) or f < 0:
            raise FrontConfigError(f"CO2 cap {v} must be a finite t/yr ≥ 0")
        out.append(f)
    if len(set(out)) != len(out):
        raise FrontConfigError("CO2 caps repeat")
    return out


def solves_needed(levels: list, caps: list) -> int:
    """LP solves the study makes: every grid point, plus one uncapped
    reference solve when no column of the grid is uncapped (the CO2
    degenerate check needs the unconstrained emissions)."""
    return len(levels) * len(caps) + (0 if None in caps else 1)


def check_budget(levels: list, caps: list, budget: int) -> int:
    need = solves_needed(levels, caps)
    if need > budget:
        extra = "" if None in caps else " + 1 uncapped reference"
        raise FrontBudgetError(
            f"{len(levels)} × {len(caps)} points{extra} = {need} solves exceed "
            f"the {budget}-solve budget; drop levels or caps"
        )
    return need


# --------------------------------------------------------------------------
# One point
# --------------------------------------------------------------------------


def _periods(n):
    import pandas as pd

    if isinstance(n.snapshots, pd.MultiIndex):
        return [p for p in n.snapshots.unique("period")]
    return [None]


def apply_co2_cap(n, cap_t_per_yr: float | None) -> list[str]:
    """Replace every CO2 cap on ``n`` (a working copy) with the study's.

    ``None`` leaves no CO2 cap. Otherwise one ``primary_energy`` constraint
    per investment period, constant = cap × the period's modelled years (the
    same weighting PyPSA's constraint sums emissions with)."""
    from services.adequacy.metrics import horizon_years
    from services.fuel_curve import co2_caps

    for name in co2_caps(n):
        n.remove("GlobalConstraint", name)
    if cap_t_per_yr is None:
        return []
    names = []
    for p in _periods(n):
        if p is None:
            name = CO2_CAP_NAME
            years = horizon_years(n)
            n.add(
                "GlobalConstraint",
                name,
                type="primary_energy",
                carrier_attribute="co2_emissions",
                sense="<=",
                constant=cap_t_per_yr * years,
            )
        else:
            name = f"{CO2_CAP_NAME}_{p}"
            years = horizon_years(
                n, sns=n.snapshots[n.snapshots.get_level_values(0) == p]
            )
            n.add(
                "GlobalConstraint",
                name,
                type="primary_energy",
                carrier_attribute="co2_emissions",
                sense="<=",
                constant=cap_t_per_yr * years,
                investment_period=p,
            )
        names.append(name)
    return names


def _capacities(n) -> dict[str, float]:
    """Built capacity of every extendable asset, MW (MWh for Stores)."""
    out: dict[str, float] = {}
    for comp, nom in (
        ("generators", "p_nom"),
        ("storage_units", "p_nom"),
        ("links", "p_nom"),
        ("stores", "e_nom"),
        ("lines", "s_nom"),
    ):
        df = getattr(n, comp, None)
        if df is None or df.empty or f"{nom}_extendable" not in df.columns:
            continue
        ext = df.index[df[f"{nom}_extendable"].astype(bool)]
        col = f"{nom}_opt" if f"{nom}_opt" in df.columns else nom
        for k in ext:
            out[f"{comp}:{k}"] = float(df.at[k, col] or 0.0)
    return out


_CAPACITY_VARS = ("p_nom", "e_nom", "s_nom")
# PyPSA subtracts the capital cost of extendables' existing capacity through a
# fixed variable of this name: a capacity term, so it belongs to capex.
_CAPEX_CONSTANT_VAR = "objective_constant"


def _objective_years(n) -> float:
    """The years the LP objective counts: Σ snapshot objective weights / 8760,
    each period's share times its (discounted) objective weighting. Dividing
    an objective total by this gives an equivalent annual cost; on one
    period it is the modelled share of a year."""
    import pandas as pd

    sw = n.snapshot_weightings.objective
    if isinstance(n.snapshots, pd.MultiIndex):
        per = sw.groupby(level=0).sum() / 8760.0
        ipw = n.investment_period_weightings["objective"]
        return float(sum(per[p] * float(ipw.get(p, 1.0)) for p in per.index))
    return float(sw.sum()) / 8760.0


def _lp_capex_share(n) -> float | None:
    """The capacity terms of the solved LP objective as a share of the whole
    objective (shed included), read off the linopy model still attached to
    the working copy. The share is scale-free: the solver scales the LP's
    coefficients and rescales ``n.objective`` afterwards. It reads what the
    LP actually paid, which ``n.statistics`` cannot after the solve: the
    templates price candidates from the finance pack inside the LP and the
    import tariff is a commercial term, neither on the restored network."""
    m = getattr(n, "model", None)
    if m is None:
        return None
    try:
        sol: dict[int, float] = {}
        cap: set[int] = set()
        for name in m.variables:
            vf = m.variables[name].flat
            labels = vf["labels"].astype(int).tolist()
            sol.update(zip(labels, vf["solution"].astype(float).tolist()))
            if name.split("-", 1)[-1] in _CAPACITY_VARS or name == _CAPEX_CONSTANT_VAR:
                cap.update(labels)
        expr = m.objective.expression
        obj = expr.flat
        vals = obj["coeffs"].astype(float) * obj["vars"].map(sol).astype(float)
        # A bare constant on the expression is not a capacity term (the repo's
        # wrappers put penalty constants there), so it counts in the whole only.
        whole = float(vals.sum()) + float(getattr(expr, "const", 0.0) or 0.0)
        part = float(vals[obj["vars"].isin(cap)].sum())
    except Exception:  # noqa: BLE001
        logger.exception("front: reading the LP objective failed")
        return None
    if not (math.isfinite(whole) and math.isfinite(part)) or whole == 0:
        return None
    return part / whole


def _annual_costs(n, rep: dict | None) -> tuple[float, float, float]:
    """``(capex, opex, total)`` in EUR/yr, equivalent annual.

    ``total`` is the adequacy report's system cost, the LP objective less the
    shed cost (the frontier's CostBlock basis), divided by the objective's
    years. ``capex`` is the objective's capacity terms (annualised capital
    and fixed O&M as the LP counts them) on the same basis; ``opex`` is the
    rest: dispatch, fuel, import tariff, CO2 price. NaN when unreadable."""
    nan = float("nan")
    years = _objective_years(n)
    try:
        total_obj = float(((rep or {}).get("cost") or {})["total_system_cost_eur"])
        n_obj = float(n.objective)
    except (KeyError, TypeError, ValueError, AttributeError):
        return nan, nan, nan
    share = _lp_capex_share(n)
    if years <= 0 or share is None or not math.isfinite(n_obj):
        return nan, nan, (total_obj / years if years > 0 else nan)
    capex = share * n_obj / years
    total = total_obj / years
    return capex, total - capex, total


def _co2(n, names: list[str]) -> tuple[float | None, float | None, bool | None]:
    """``(t/yr, shadow EUR/t, binding)`` read off the solved working copy."""
    from services.adequacy.metrics import horizon_years
    from services.results.emissions import _cap_binding, compute_emissions

    def _reader(net, accessor, attr, _src="lopf"):
        return getattr(getattr(net, accessor, None), attr, None)

    payload = compute_emissions(n, "lopf", result_df=_reader)
    years = horizon_years(n)
    total = float(payload["total_tCO2"]) if payload and years > 0 else None
    t_per_yr = total / years if total is not None else None
    if not names:
        return t_per_yr, None, None
    gc = n.global_constraints
    shadows, binds = [], []
    for name in names:
        mu = float(gc.at[name, "mu"]) if "mu" in gc.columns else float("nan")
        # A `<=` cap in a minimisation has mu ≤ 0: one more tonne of headroom
        # is worth −mu in objective EUR. A period's constraint counts each
        # modelled tonne `years` times while its objective counts the cost
        # `objective` (discounted) times, so the undiscounted EUR/t is
        # −mu × years / objective.
        shadows.append(
            -mu * _period_shadow_factor(n, gc, name)
            if math.isfinite(mu)
            else float("nan")
        )
        binds.append(
            _cap_binding(
                mu if math.isfinite(mu) else None, None, float(gc.at[name, "constant"])
            )
        )
    if any(b is None for b in binds):
        return t_per_yr, None, None
    binding = any(binds)
    finite = [s for s in shadows if math.isfinite(s)]
    shadow = max(finite) if binding and finite else None
    return t_per_yr, shadow, binding


def _period_shadow_factor(n, gc, name: str) -> float:
    p = gc.at[name, "investment_period"] if "investment_period" in gc.columns else None
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return 1.0
    ipw = n.investment_period_weightings
    try:
        years, obj = float(ipw.at[p, "years"]), float(ipw.at[p, "objective"])
    except (KeyError, TypeError, ValueError):
        return 1.0
    return years / obj if obj > 0 and math.isfinite(years) else 1.0


def _reliability_binding(lever: str, rep: dict | None) -> bool | None:
    if not rep:
        return None
    if lever == "ens_cap":
        b = (rep.get("target") or {}).get("binding")
        return None if b is None else b != "voll"
    rows = (rep.get("reserve_margin") or {}).get("by_period") or []
    return any(bool(r.get("binding")) for r in rows) if rows else None


def _cfg_for(cfg, lever: str, level: float):
    if lever == "ens_cap":
        return dataclasses.replace(cfg, ens_cap_permyriad=level)
    return dataclasses.replace(cfg, reserve_margin=level)


def _solve_point(base, cfg, lock, log_queue, lever, level, cap):
    """One full re-optimisation on a fresh copy of ``base``. Returns
    ``(record, solved_copy_or_None)``."""
    from services.adequacy.sweep import _solve_once

    work = base.copy()
    names = apply_co2_cap(work, cap)
    sink: dict = {}
    _solve_once(_cfg_for(cfg, lever, level), work, lock, log_queue, sink)
    status = sink.get("_status")
    rec = {"lever": lever, "level": level, "co2_cap_t_per_yr": cap}
    if status not in ("ok", "optimal"):
        rec.update(status=str(sink.get("_condition") or status or "failed"), point=None)
        return rec, None
    rep = sink.get("adequacy_report")
    capex, opex, total = _annual_costs(work, rep)
    co2, shadow, co2_bind = _co2(work, names)
    metrics = (rep or {}).get("metrics") or {}
    rec.update(
        status="ok",
        point={
            "capex_eur_per_yr": capex,
            "opex_eur_per_yr": opex,
            "total_eur_per_yr": total,
            "lp_ens_mwh": (
                float(metrics["ens_mwh"])
                if metrics.get("ens_mwh") is not None
                else None
            ),
            "reliability_binding": _reliability_binding(lever, rep),
            "co2_t_per_yr": co2,
            "co2_shadow_eur_per_t": shadow,
            "co2_binding": co2_bind,
            "capacities": _capacities(work),
            "excludes_shed_cost": True,
        },
    )
    return rec, work


# --------------------------------------------------------------------------
# MC per point
# --------------------------------------------------------------------------


def _mc_point(
    work, cfg, lock, pack, draws: int, seed
) -> tuple[dict | None, str | None]:
    """Sampled-outage LOLE/EUE of the fleet ``work`` built, pinned draws."""
    from services.adequacy import eh_stages
    from services.adequacy import mc as mc_mod
    from services.adequacy import mc_zonal

    try:
        if pack is not None:
            from services.adequacy import archetypes as arch

            _mc_net, boundary = arch.hub_boundary_copy(work, pack)
            frozen = eh_stages.freeze_fixed_plan(
                work, cfg, lock, overlay=pack.import_overlay, boundary=boundary
            )
        else:
            frozen = eh_stages.freeze_fixed_plan(work, cfg, lock)
    except Exception as exc:  # noqa: BLE001
        logger.exception("front: freezing the fleet failed")
        return None, f"fleet could not be frozen: {exc}"
    if frozen.mc_inputs is None:
        return None, frozen.mc_error or "MC inputs unavailable"
    try:
        if frozen.zonal_inputs is not None:
            res = mc_zonal.zonal_mc_adequacy(
                frozen.zonal_inputs,
                draws=draws,
                max_draws=draws,
                batch=draws,
                seed=seed,
            )
        else:
            res = mc_mod.mc_adequacy(
                frozen.mc_inputs, draws=draws, max_draws=draws, batch=draws, seed=seed
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("front: MC failed")
        return None, f"MC failed: {exc}"
    floor = res.get("resolution_floor_h")
    lole = float(res["lole_hours"])
    return {
        "lole_hours": lole,
        "lole_ci": list(res["lole_ci"]) if res.get("lole_ci") is not None else None,
        "eue_mwh": float(res["eue_mwh"]),
        "eue_ci": list(res["eue_ci"]) if res.get("eue_ci") is not None else None,
        "n_samples": int(res["n_samples"]),
        "resolution_floor_h": floor,
        "below_floor": bool(floor is not None and lole < floor),
        "time_basis": res.get("time_basis"),
    }, None


# --------------------------------------------------------------------------
# Front marking and degenerate detection
# --------------------------------------------------------------------------


def _close(a: float, b: float, abs_tol: float) -> bool:
    return math.isclose(a, b, rel_tol=_REL_TOL, abs_tol=abs_tol)


def _dominates(a: tuple, b: tuple) -> bool:
    """``a`` dominates ``b`` (all objectives minimised): no worse on every
    one and strictly better on at least one, beyond tolerance."""
    better = False
    for x, y in zip(a, b):
        if _close(x, y, _ABS_TOL_EUR):
            continue
        if x > y:
            return False
        better = True
    return better


def mark_dominance(points: list[dict], basis: str) -> None:
    """Sets ``dominated_by`` (indices) and ``on_front`` on every point, in
    place. Failed points and points with a non-finite objective are neither
    dominating nor dominated (``on_front`` None)."""
    objs: dict[int, tuple] = {}
    for i, p in enumerate(points):
        obj = _objectives(p, basis)
        if obj is not None:
            objs[i] = obj
    for i, p in enumerate(points):
        if i not in objs:
            p["dominated_by"] = []
            p["on_front"] = None
            continue
        by = [j for j, o in objs.items() if j != i and _dominates(o, objs[i])]
        p["dominated_by"] = by
        p["on_front"] = not by


def _objectives(p: dict, basis: str) -> tuple | None:
    if p.get("status") != "ok" or not p.get("point"):
        return None
    pt = p["point"]
    rel = (
        (p.get("mc") or {}).get("eue_mwh")
        if basis == "mc_eue"
        else pt.get("lp_ens_mwh")
    )
    vals = (pt.get("total_eur_per_yr"), rel, pt.get("co2_t_per_yr"))
    if any(v is None or not math.isfinite(float(v)) for v in vals):
        return None
    return tuple(float(v) for v in vals)


def same_design(a: dict, b: dict) -> bool:
    """Same built capacities (every asset) and the same total cost."""
    ca, cb = a["capacities"], b["capacities"]
    if set(ca) != set(cb):
        return False
    if not all(_close(ca[k], cb[k], _ABS_TOL_MW) for k in ca):
        return False
    return _close(a["total_eur_per_yr"], b["total_eur_per_yr"], _ABS_TOL_EUR)


def _fmt(x: float) -> str:
    return f"{x:,.0f}" if abs(x) >= 10 else f"{x:.3g}"


def degenerate_findings(
    points: list[dict],
    lever: str,
    *,
    voll: float,
    outage_model: bool,
    reference_co2: float | None,
) -> dict:
    """Which axes never bound, in words, with the lever to pull."""
    ok = [p for p in points if p.get("status") == "ok" and p.get("point")]
    axes: dict[str, dict] = {}
    if not ok:
        if not points:
            return {"axes": axes, "all": False, "text": None}
        words = sorted({str(p.get("status")) for p in points})
        return {
            "axes": axes,
            "all": False,
            "text": (
                f"No point solved ({', '.join(words)}), so there is no front to read."
            ),
        }
    rel_b = [p["point"].get("reliability_binding") for p in ok]
    lever_words = "ENS target" if lever == "ens_cap" else "reserve margin"
    if not any(rel_b) and any(b is None for b in rel_b):
        # Unread is not "never bound": say what we do not know.
        axes[lever] = {
            "text": (
                f"Whether the {lever_words} bound could not be read at "
                f"{sum(b is None for b in rel_b)} of {len(ok)} points, and it "
                "did not bind at the rest."
            ),
            "levers": [],
            "unknown": True,
        }
    elif not any(rel_b):
        if lever == "ens_cap":
            why = "the dispatch model has no outages, so " if not outage_model else ""
            axes["ens_cap"] = {
                "text": (
                    f"The ENS target never bound at any of {len(ok)} points: "
                    f"{why}at VOLL {_fmt(voll)} EUR/MWh serving every MWh is "
                    "already the cheapest plan. Use the reserve-margin lever, "
                    "lower VOLL, or read the sampled-outage LOLE per point."
                ),
                "levers": ["reserve_margin", "lower_voll", "outage_sampling"],
            }
        else:
            tightest = max(p["level"] for p in ok)
            axes["reserve_margin"] = {
                "text": (
                    f"The reserve margin never bound at any of {len(ok)} points: "
                    f"the least-cost fleet already holds more firm capacity than "
                    f"the tightest margin swept ({tightest:.0%}). Sweep higher margins."
                ),
                "levers": ["higher_margin"],
            }
    capped = [p for p in ok if p.get("co2_cap_t_per_yr") is not None]
    co2_b = [p["point"].get("co2_binding") for p in capped]
    if capped and not any(co2_b) and any(b is None for b in co2_b):
        axes["co2"] = {
            "text": (
                f"Whether the CO2 limit bound could not be read at "
                f"{sum(b is None for b in co2_b)} of {len(capped)} capped "
                "points, and it did not bind at the rest."
            ),
            "levers": [],
            "unknown": True,
        }
    elif capped and not any(co2_b):
        lowest = min(p["co2_cap_t_per_yr"] for p in capped)
        ref = (
            f"with no limit, at the loosest reliability level swept, the "
            f"design emits {_fmt(reference_co2)} t/yr, "
            if reference_co2 is not None
            else ""
        )
        axes["co2"] = {
            "text": (
                f"The CO2 limit never bound: {ref}within every limit swept "
                f"(lowest {_fmt(lowest)} t/yr). Sweep limits below the "
                "unconstrained emissions."
            ),
            "levers": ["lower_co2_cap"],
        }
    all_same = len(ok) > 1 and all(
        same_design(ok[0]["point"], p["point"]) for p in ok[1:]
    )
    text = None
    if all_same:
        text = (
            f"Every one of the {len(ok)} points is the same design (same "
            "capacities, same cost): there is no trade-off in this range, so "
            "the front is one point."
        )
    return {"axes": axes, "all": all_same, "text": text}


# --------------------------------------------------------------------------
# The study
# --------------------------------------------------------------------------


def _margin_preflight(network, lock, cfg, margin: float, lever_why: str) -> None:
    """Refuse before any solve when the margin cannot be enforced on this
    network, with the validator's own sentence: otherwise every point fails
    the same blocking validation (the margin loop's guard, same codes)."""
    from services.validation_service import _check_reserve_margin

    with lock:
        issues = _check_reserve_margin(
            network, dataclasses.replace(cfg, reserve_margin=margin)
        )
    for iss in issues:
        if iss.code == "reserve_margin_unpriceable_assets" or iss.code.startswith(
            "nonfinite_"
        ):
            raise FrontConfigError(
                f"{iss.message} (The reserve-margin lever was used because {lever_why}.)"
            )


def _private_base(network, lock):
    from services.adequacy.redundancy import _detach_solver_model

    # Copying a network with a live solver model attached raises; detach it
    # under the lock first, as the hub-side copy does (the foreground keeps
    # its solution, only the solver handle is dropped).
    with lock:
        _detach_solver_model(network)
        base = network.copy()
    return base


def _guarded_point(base, cfg, lock, log_queue, lever, level, cap):
    """``_solve_point``, with an ordinary exception kept as a failed point so
    one broken point does not lose the others. ``BaseException`` (a stop,
    an interpreter exit) still propagates."""
    try:
        return _solve_point(base, cfg, lock, log_queue, lever, level, cap)
    except Exception as exc:  # noqa: BLE001
        logger.exception("front: point (%s, %s) raised", level, cap)
        return {
            "lever": lever,
            "level": level,
            "co2_cap_t_per_yr": cap,
            "status": "error",
            "error": str(exc) or type(exc).__name__,
            "point": None,
        }, None


def run_front_study(
    network,
    lock,
    cfg,
    *,
    levels,
    co2_caps_t=None,
    lever: str | None = None,
    budget_solves: int = MAX_FRONT_SOLVES,
    mc_draws: int = DEFAULT_MC_DRAWS,
    seed=FRONT_SEED,
    pack=None,
    log_queue=None,
    stop_event=None,
) -> dict:
    """The front over ``levels`` (reliability lever) × ``co2_caps_t`` (t/yr,
    ``None`` = uncapped).

    Validation and the budget refuse BEFORE any solve. ``network`` is copied
    once under ``lock`` and never written. A point whose solve is not optimal
    is kept with its status and no numbers; an aborted study keeps the
    points already swept (``aborted``).
    """
    from services.adequacy.frontier import non_convexity_warning
    from services.project_context import ProjectContext
    from services.pypsa_service import PyPSAService

    with lock:
        lever_used, lever_why = choose_lever(network, lever)
    lvls = _clean_levels(lever_used, levels)
    caps = _clean_caps(co2_caps_t)
    if lever_used == "ens_cap" and float(getattr(cfg, "voll", 0.0) or 0.0) <= 0:
        raise FrontConfigError(
            "the ENS lever requires VOLL > 0: with no slack generators the cap "
            "constrains nothing"
        )
    need = check_budget(lvls, caps, int(budget_solves))
    if int(mc_draws) < 1:
        raise FrontConfigError("mc_draws must be at least 1")

    if lever_used == "reserve_margin":
        _margin_preflight(network, lock, cfg, max(lvls), lever_why)
    lq = log_queue if log_queue is not None else queue.SimpleQueue()
    base = _private_base(network, lock)
    # read off the private copy: no lock, and the same state the points start from
    outage_model = lp_has_outage_model(base)
    points: list[dict] = []
    result = {
        "lever": lever_used,
        "lever_reason": lever_why,
        "levels": lvls,
        "co2_caps_t_per_yr": caps,
        "lp_outage_model": outage_model,
        "solves_planned": need,
        "solves": 0,
        "mc_budget": {
            "draws_per_point": int(mc_draws),
            "seed": seed,
            "pinned": True,
            "note": (
                "every point is sampled with the same seed and the "
                "same number of draws; certify the chosen point "
                "with the full MC"
            ),
            "unit_note": (
                "each asset row is sampled as one unit at its "
                "built size, so an extendable block fails as "
                "one unit"
            ),
        },
        "cost_basis": (
            "equivalent annual cost: the LP objective less shed cost, "
            "divided by the years the objective counts; capex is its "
            "capacity terms (annualised capital and fixed O&M), opex "
            "the rest (dispatch, fuel, import tariff, CO2 price)"
        ),
        "points": points,
        "reference_co2_t_per_yr": None,
        "warning": non_convexity_warning(base, cfg),
        "aborted": False,
    }
    with PyPSAService.solving_context(ProjectContext(network=base)):
        _sweep(
            result,
            points,
            base,
            cfg,
            lock,
            lq,
            lever_used,
            lvls,
            caps,
            mc_draws,
            seed,
            pack,
            stop_event,
        )
    return _finish(result, points, cfg, lever_used, outage_model)


def _sweep(
    result,
    points,
    base,
    cfg,
    lock,
    lq,
    lever_used,
    lvls,
    caps,
    mc_draws,
    seed,
    pack,
    stop_event,
) -> None:
    """Every grid point, then the uncapped reference when no column is
    uncapped. Runs inside the study's solving context, so the solver's
    transient marks land on the private base, never the foreground."""
    for idx, (lv, cap) in enumerate(itertools.product(lvls, caps)):
        if stop_event is not None and stop_event.is_set():
            result["aborted"] = True
            break
        rec, work = _guarded_point(base, cfg, lock, lq, lever_used, lv, cap)
        result["solves"] += 1
        rec["index"] = idx
        rec["mc"], rec["mc_error"] = (None, "point not solved")
        if work is not None:
            rec["mc"], rec["mc_error"] = _mc_point(
                work, cfg, lock, pack, int(mc_draws), seed
            )
        points.append(rec)

    ok = [p for p in points if p["status"] == "ok"]
    if None in caps:
        ref = [p["point"]["co2_t_per_yr"] for p in ok if p["co2_cap_t_per_yr"] is None]
        result["reference_co2_t_per_yr"] = min(ref) if ref else None
    elif stop_event is not None and stop_event.is_set():
        result["aborted"] = True  # the reference solve is skipped too
    else:
        # the loosest reliability level: the largest ENS cap, the smallest margin
        loosest = max(lvls) if lever_used == "ens_cap" else min(lvls)
        rec, _work = _guarded_point(base, cfg, lock, lq, lever_used, loosest, None)
        result["solves"] += 1
        if rec["status"] == "ok":
            result["reference_co2_t_per_yr"] = rec["point"]["co2_t_per_yr"]


def _finish(result, points, cfg, lever_used, outage_model) -> dict:
    ok = [p for p in points if p["status"] == "ok"]

    mc_all = bool(ok) and all(p.get("mc") is not None for p in ok)
    basis = "mc_eue" if mc_all else "lp_ens"
    result["reliability_basis"] = basis
    result["reliability_basis_note"] = (
        None
        if mc_all
        else "Sampled-outage reliability was not available for every point, so the "
        "whole front is ranked on the dispatch model's ENS instead."
    )
    mark_dominance(points, basis)
    _flag_indistinct(points, basis)
    result["degenerate"] = degenerate_findings(
        points,
        lever_used,
        voll=float(getattr(cfg, "voll", 0.0) or 0.0),
        outage_model=outage_model,
        reference_co2=result["reference_co2_t_per_yr"],
    )
    return result


def _flag_indistinct(points: list[dict], basis: str) -> None:
    """``reliability_indistinct``: this point's LOLE CI overlaps another
    point's, so their reliability order is not established by the draws."""
    cis = {}
    for i, p in enumerate(points):
        ci = (p.get("mc") or {}).get("lole_ci") if basis == "mc_eue" else None
        if ci and p.get("status") == "ok":
            cis[i] = ci
    for i, p in enumerate(points):
        p["reliability_indistinct"] = i in cis and any(
            j != i and cis[j][0] <= cis[i][1] and cis[i][0] <= cis[j][1] for j in cis
        )
