"""The investment case's baseline: the site as it stands today (owner decision
2026-10-15, after #294).

The counterfactual of a case whose owner keeps existing assets is a second
solve of the same network with the plan's new builds removed, so the case
measures what the investment adds, not what the site already has. The
baseline removes exactly what the case charges capex for, plus the LP's new
capacity:

- an extendable owned asset is fixed at its starting size: its increment
  (`p_nom_opt − p_nom`) goes, and a candidate of a later period (build_year
  after the first) is removed outright;
- a non-extendable owned row stays only when the case charges it no capex (a
  typed `overnight_cost` of 0, or untyped at no `capital_cost`); a priced row
  is a new investment and goes, and an untyped one with a `capital_cost` (its
  capex read from it) goes too, flagged `baseline_typing_mismatch:<a>` so a
  forgotten 0 is never silent;
- an extendable increment typed at 0 is flagged `baseline_free_capacity:<a>`;
- the owner's uncosted PoC meter Links are the connection, never an
  investment: kept, never flagged.

A case with no kept existing asset needs no baseline: removing every owned
asset is the analytic counterfactual's own definition, which then stays.

A load with a `reference_purchases` entry (grey H2, say) can be bought in the
baseline: a supply Generator on its bus (`reference_supply:<load>`), priced
at the reference price plus that period's CO2 (owner decision on #312, option
1), so the site as it stands buys whenever that is cheaper than running its
own conversion. The purchase is what it dispatches (`finance_case`).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import queue
import threading
from dataclasses import dataclass, field

from services.finance.baseline_text import flag_text  # noqa: F401  (the flags' wording)
from services.reliability_goal import goal_free_config

_EPS_MW = 1e-6
#: A removed row's upper bound in the baseline (MW): see `baseline_network`.
_REMOVED_MAX = 1e-9

#: The baseline's reference-purchase supply Generators: `<prefix><load>`.
SUPPLY_PREFIX = "reference_supply:"
SUPPLY_CARRIER = "reference_purchase"

_FRAMES = {"Generator": ("generators", "p_nom"), "StorageUnit": ("storage_units", "p_nom"),
           "Store": ("stores", "e_nom"), "Link": ("links", "p_nom")}


@dataclass(frozen=True)
class BaselinePlan:
    #: (component, name, the size the baseline keeps) for every removed build.
    removed: tuple[tuple[str, str, float], ...]
    #: the owned existing rows the baseline keeps (meter Links excepted).
    kept: tuple[tuple[str, str], ...]
    flags: tuple[str, ...]
    #: owned extendable rows, all fixed in the baseline (at their kept size).
    fixed: tuple[tuple[str, str, float], ...] = field(default=())

    @property
    def needed(self) -> bool:
        return bool(self.kept)


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _first_period(n) -> int | None:
    periods = getattr(n, "investment_periods", None)
    return int(periods[0]) if periods is not None and len(periods) else None


def plan_baseline(n, owned: list[tuple[str, str]], *,
                  meter_links: tuple[str, ...] | list[str] = ()) -> BaselinePlan:
    """Which owned rows the baseline removes or keeps (see the module docstring)."""
    first = _first_period(n)
    removed, kept, fixed, flags = [], [], [], []
    for comp, name in owned:
        spec = _FRAMES.get(comp)
        df = getattr(n, spec[0], None) if spec else None
        if df is None or name not in df.index:
            continue
        if comp == "Link" and name in meter_links:
            continue
        col = spec[1]
        start = _num(df.at[name, col]) or 0.0
        typed = _num(df.at[name, "overnight_cost"]) if "overnight_cost" in df.columns else None
        if bool(df.at[name, f"{col}_extendable"]):
            built = _num(df.at[name, f"{col}_opt"]) if f"{col}_opt" in df.columns else None
            built = start if built is None else built
            by = _num(df.at[name, "build_year"]) if "build_year" in df.columns else None
            later = first is not None and by is not None and by > first
            keep = 0.0 if later else start
            fixed.append((comp, name, keep))
            if built - keep > _EPS_MW:
                removed.append((comp, name, keep))
                if typed == 0.0:
                    flags.append(f"baseline_free_capacity:{name}")
        elif typed == 0.0 or (typed is None and not (_num(df.at[name, "capital_cost"]) or 0.0) > 0.0):
            # charged no capex (typed 0, or untyped at no capital cost): it stays
            kept.append((comp, name))
        else:
            removed.append((comp, name, 0.0))
            if typed is None:
                flags.append(f"baseline_typing_mismatch:{name}")
    return BaselinePlan(removed=tuple(removed), kept=tuple(kept), flags=tuple(flags),
                        fixed=tuple(fixed))


def reference_supplies(n, cfg) -> tuple[dict, ...]:
    """The baseline's reference-purchase supplies (owner decision on #312,
    option 1): one per `reference_purchases` load in `n`, with its bus, a
    capacity above the load's peak and its LP marginal cost per period
    (EUR/MWh of the load's energy).

    The LP cost is on the footing of the LP's other costs, which are NOT
    escalated across periods (grid supply, tariff energy charge, diesel):
    the reference price ×1000/LHV in the case's base-year money, unescalated,
    plus that period's CO2 (the listed year in force at the period's start,
    as `finance_case._price_co2` looks it up) × co2_t_per_kg ×1000/LHV,
    converted from the CO2 price's money year to the base year at the case's
    inflation (0 for a real case). Do not escalate it here: the cash is priced
    per operating year from the dispatched MWh in `finance_case` (fuel
    escalation, CO2 by year); this cost only steers the dispatch. The CO2
    path's real change between periods stays: it is a real price signal.
    Empty without reference purchases."""
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs

    com = getattr(cfg, "commercial", None)
    if not isinstance(com, dict) or not com.get("reference_purchases"):
        return ()
    parsed = CommercialConfig.model_validate(com)
    # The same finance inputs the case prices the cash with (the IC route
    # validates `cfg.finance`, as here): a model or its dict.
    fin_raw = getattr(cfg, "finance", None)
    fin = fin_raw if isinstance(fin_raw, FinanceInputs) else \
        FinanceInputs.model_validate(fin_raw) if isinstance(fin_raw, dict) else None
    real = fin is not None and fin.price_basis == "real"
    # A nominal case without an inflation cannot convert the CO2 price; its
    # CO2 cash lines read not established (`finance_case._co2_line`), and the
    # dispatch takes the CO2 price unconverted.
    infl = 0.0 if real or fin is None or fin.inflation is None else float(fin.inflation)
    snaps = n.snapshots
    periods = (list(snaps.get_level_values(0).unique()) if hasattr(snaps, "levels")
               else [int(snaps[0].year)])
    base_year = int(periods[0])
    co2 = parsed.co2_price
    listed = sorted(int(y) for y in co2.eur_per_t_by_year) if co2 is not None else []
    out = []
    for r in parsed.reference_purchases:
        if r.load not in n.loads.index:
            continue
        per_mwh = 1000.0 / r.lhv_kwh_per_kg
        mc = {}
        for p in periods:
            cost = r.price_eur_per_kg * per_mwh
            at = [y for y in listed if y <= int(p)]
            if co2 is not None and at:
                price = float(co2.eur_per_t_by_year[at[-1]]) * \
                    (1.0 + infl) ** (base_year - int(co2.money_year))
                cost += r.co2_t_per_kg * per_mwh * price
            mc[int(p)] = cost
        # Sized above the load as the LP sees it (`load_scalers` scale the
        # demand for the solve only), so the supply never runs short.
        from services.adequacy.demand import lp_demand_frame

        frame = lp_demand_frame(n, cfg)
        if frame is not None and r.load in frame.columns:
            peak = float(frame[r.load].max())
        elif r.load in n.loads_t.p_set.columns:
            peak = float(n.loads_t.p_set[r.load].max())
        else:
            peak = float(n.loads.at[r.load, "p_set"])
        out.append({"load": r.load, "bus": str(n.loads.at[r.load, "bus"]),
                    "p_nom": round(2.0 * max(peak, 0.0) + 1.0, 6), "mc": mc})
    return tuple(out)


def _add_supplies(base, supplies) -> None:
    """The supply Generators, in the baseline copy only (never the project)."""
    import pandas as pd

    if not supplies:
        return
    if SUPPLY_CARRIER not in base.carriers.index:
        base.add("Carrier", SUPPLY_CARRIER)
    multi = hasattr(base.snapshots, "levels")
    for sp in supplies:
        name = SUPPLY_PREFIX + sp["load"]
        base.add("Generator", name, bus=sp["bus"], carrier=SUPPLY_CARRIER, p_nom=sp["p_nom"],
                 marginal_cost=0.0)
        per = base.snapshots.get_level_values(0) if multi else \
            pd.Index([next(iter(sp["mc"]))] * len(base.snapshots))
        base.generators_t.marginal_cost[name] = pd.Series(
            [sp["mc"][int(p)] for p in per], index=base.snapshots, dtype=float)


def baseline_network(n, plan: BaselinePlan, supplies=()):
    """A copy of `n` with every owned extendable row fixed at its kept size and
    every removed row at 0 (rows stay, so value flows still name them; a size
    of 0 is an extendable row bounded to 0), plus the reference-purchase
    supplies (`reference_supplies`). The original is never touched."""
    from services.workflow_tools import _network_copy

    base = _network_copy(n)
    _add_supplies(base, supplies)
    sizes = {(c, a): k for c, a, k in plan.fixed}
    sizes.update({(c, a): k for c, a, k in plan.removed})
    for (comp, name), keep in sizes.items():
        frame, col = _FRAMES[comp]
        df = getattr(base, frame)
        if keep > _EPS_MW:
            df.loc[name, f"{col}_extendable"] = False
            df.loc[name, col] = float(keep)
        else:
            # A size of 0, as the run's validation allows it: an extendable row
            # bounded to `_REMOVED_MAX`, its capital cost kept (a non-extendable
            # size of 0, equal bounds, or an extendable row with no cost are
            # refused), so the LP builds nothing: below `_EPS_MW` in any case.
            df.loc[name, [f"{col}_extendable", col, f"{col}_min", f"{col}_max"]] = \
                [True, 0.0, 0.0, _REMOVED_MAX]
    return base


# ── the solve ────────────────────────────────────────────────────────────────


class BaselineRefused(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Baseline:
    network: object
    lost_load: dict | None
    plan: BaselinePlan
    digest: str


_CACHE: dict[str, Baseline] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 4


def _key(n, cfg, plan: BaselinePlan, island=None, supplies=()) -> str:
    from dataclasses import asdict

    from services.dispatch_status import dispatch_digest

    try:
        conf = asdict(cfg)
    except TypeError:
        conf = dict(vars(cfg))
    conf.pop("finance", None)
    conf = goal_free_config(conf)       # the LP never reads the goal (W3b-R)
    blob = {"dispatch": dispatch_digest(n), "config": conf,
            "removed": plan.removed, "fixed": plan.fixed, "island": island}
    if supplies:
        # Their LP costs read the finance inputs (popped above): a price
        # change re-solves, a case without purchases keeps its key.
        blob["supplies"] = supplies
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _island_sidecar(storage) -> dict | None:
    """What the solve's island rows read, for the key: the project's island
    sidecar as text and its campus's nominal frequency (a file the solve
    refuses keys on the refusal)."""
    if not storage:
        return None
    from pathlib import Path

    from services.adequacy.island_config import SIDECAR_NAME
    from services.solver.island import IslandRefusal, campus_f_hz

    path = Path(storage) / SIDECAR_NAME
    if not path.is_file():
        return None
    try:
        f_hz = campus_f_hz(storage)
    except IslandRefusal as exc:
        f_hz = str(exc)
    return {"sidecar": path.read_text(), "f_hz": f_hz}


def solve_baseline(n, cfg, plan: BaselinePlan, *, solve=None) -> Baseline:
    """The baseline solved: a copy of `n` (see `baseline_network`) through
    `run_simulation` with the same solver config, so energization, the
    connection agreement, the island rows and the modelling assumptions apply
    as in the actual solve. Isolated: a private lock, stop event, log queue and
    state sink, inside a private solving context that shares only the project's
    storage folder (its island sidecar). Cached by its inputs: reading a case
    again does not re-solve. Refused `baseline_not_established:<condition>`
    when the baseline does not solve. `solve` replaces `run_simulation` in tests."""
    from services.pypsa_service import PyPSAService

    live = PyPSAService.solve_target_context()
    storage = getattr(live, "storage_dir", None) if live is not None else None
    supplies = reference_supplies(n, cfg)
    key = _key(n, cfg, plan, _island_sidecar(storage), supplies)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit is not None:
        return hit
    if solve is None:
        from services.solver_service import run_simulation as solve

    def attempt(conf):
        base = baseline_network(n, plan, supplies)
        ctx = PyPSAService.build_context()
        ctx.network = base
        ctx.storage_dir = storage
        sink: dict = {}
        with PyPSAService.solving_context(ctx):
            status, condition = solve(conf, base, threading.Lock(), threading.Event(),
                                      queue.SimpleQueue(),
                                      state_update=lambda **kw: sink.update(kw))
        return base, sink, status, condition

    base, sink, status, condition = attempt(copy.copy(cfg))
    relaxed: tuple[str, ...] = ()
    if status not in ("ok", "optimal") and getattr(cfg, "island_constraints", False):
        # The island rows are a requirement the plan's new builds meet (they
        # name its batteries and gensets); the site as it stands may not meet
        # them. Its comparison is then solved without them, disclosed.
        conf = copy.copy(cfg)
        conf.island_constraints = False
        base, sink, status, condition = attempt(conf)
        relaxed = ("baseline_island_rows_relaxed",)
    if status not in ("ok", "optimal"):
        raise BaselineRefused(f"baseline_not_established:{condition or status}",
                              "the site without the plan's new builds does not solve")
    if relaxed:
        plan = BaselinePlan(removed=plan.removed, kept=plan.kept,
                            flags=plan.flags + relaxed, fixed=plan.fixed)
    got = Baseline(network=base, lost_load=sink.get("last_lost_load"), plan=plan, digest=key)
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = got
    return got


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
