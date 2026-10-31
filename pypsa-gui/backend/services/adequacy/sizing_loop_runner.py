"""The asset-sizing loop runner (AS-06, `.scratch/asset-sizing-loop/issues/06-sizing-loop-runner.md`).

Wires `services.adequacy.sizing_loop.run_sizing_loop` to real solves and the forward checks:

* **solve**: one LP on a private copy of the network, through the project solve's own extra_functionality chain
  (`solver_service.build_lp_chain`: user code, curtailment cost, capex budget, ENS cap, import-energy cap, reserve
  margin, island, the AS rows, commercial bindings, objective scale), after the energization schedule and the
  modelling assumptions are applied to the copy, and `n.optimize`. Not `run_simulation`: that publishes a sizing
  record, a DSR commit, commercial terms and solve state for the open project, and the loop must leave the
  project as it found it. The runner writes nothing of the caller's network or config; applying a result is
  AS-10's job. Settings whose pre-solve step the copy does not reproduce are refused by name (`refusals`): island
  ride-through, commercial terms, Capacity > sizing off. What the loop does not follow is named in `basis_note`:
  a rolling, myopic or SCLOPF solve (the loop sizes one full-horizon LP), unit commitment and the project's
  solver options, and a missing tsam (the inner rounds then solve, and are labelled, full year). The transforms'
  restores run when the loop ends, after the forward checks and the sheet, so the open project's transient rows
  are left as found. A stop request lands between rounds and
  inside a solve: `runtime._AbortWatcher` interrupts a running LP, and the loop ends `aborted`. Inner rounds solve a tsam-reduced year (`services.time_aggregation_service`, the limited-foresight
  clustering, with `SolverConfig.lf_*`); the final solve is the full year, and its forward pass gives the verdict.
* **forward**: the checks on the solved, fixed capacities. Each is a scalar check with gridspine or the AS
  helpers:

  - transformer: peak flow / pf rounded up to the library; a frozen size that the flow outgrows, or a flow past
    the largest size, cuts the import flow;
  - pq: the exact PQ circle of each PCS (`gridspine.static.campus_reactive.pcs_s_mva`) plus the STATCOM,
    against the worst hour's need; a shortfall raises the PQ margin;
  - escr: (S_sc + credit x GFM MVA) / P_n; a shortfall raises the GFM floor, or is unresolved without a GFM store;
  - smoothing: sum P_sm and E_sm against the AS-03 floors of the envelope (`floors_from_envelope`);
  - pst: gridspine's analytic Pst on the AS-04 voltage-form step with the Q_dyn the steady need leaves
    (`campus_reactive.statcom_split`), S_sc,min = S_k'' / c; a breach tightens the Pst row
    (`sizing_cuts.tightened_pst_rhs`);
  - harmonics: an optional filter-cost callable, never a cut;
  - campus_flow, campus_reactive, grid_readiness, gfm_tuning: optional adapters that need a campus model;
    one not given is named as not run.

After a converged run the AS-07 control-settings sheet is built from the final solve, while its model is still
attached, and written next to `dyr_path` when one is given.

Assumed values, marked as such (SAGC-1): tan phi_swing 0.1 (the GPU load's reactive swing per MW needs vendor
data), X/R 4 (`flicker_inputs.DEFAULT_XR`), the GFM fault-current credit 1.2 pu, ESCR >= 3.
"""
from __future__ import annotations

import copy
import dataclasses
import logging
import math
import threading
from typing import Callable

import numpy as np
import pandas as pd

from services.adequacy import sizing_loop as sl
from services.adequacy.flicker_inputs import DEFAULT_XR
from services.solver import sizing_cuts as sc
from services.solver.envelope_floors import _flagged, floors_from_envelope
from services.solver.pcs_statcom import pcs_statcom_tags, solution
from services.solver.runtime import _AbortWatcher
from services.solver.vintage_store import _frozen_vintage_store

logger = logging.getLogger(__name__)

#: The checks that need a campus model; each runs only through an adapter the caller supplies.
CAMPUS_CHECKS = ("campus_flow", "campus_reactive", "grid_readiness", "gfm_tuning")
#: The load's reactive swing per MW of active swing when the envelope gives none. Assumed: needs vendor data.
TAN_PHI_SWING_ASSUMED = 0.1
#: Fault-current credit per MVA of grid-forming converter, pu (assumed).
GFM_CREDIT = 1.2
ESCR_MIN = 3.0
#: What every result's sizes rest on, before the per-setting lines of `basis_lines`.
BASIS_NOTE = ("the loop's LP matches the project solve's: the same extra_functionality chain "
              "(solver_service.build_lp_chain), energization and modelling assumptions, on a private copy")


def refusals(config) -> list[str]:
    """One line per project setting whose pre-solve step the loop does not reproduce on its copy."""
    out = []
    if getattr(config, "island_constraints", False):
        out.append("sizing loop: island constraints are on for this project; their island_config.json apply is "
                   "not reproduced in the loop")
    if getattr(config, "commercial", None):
        out.append("sizing loop: commercial bindings are set on this project and are not applied to the loop "
                   "(the PoC price writer and connection agreement run only in a project solve)")
    if getattr(config, "fixed_sizes", "off") != "off":
        out.append("sizing loop: Capacity > sizing is off for this project; the loop sizes, so turn sizing on")
    return out


def basis_lines(config, network=None) -> list[str]:
    """One line per project solve option the loop's single full-horizon LP does not follow."""
    out = []
    mode = getattr(config, "solve_strategy", "full")
    if mode in ("rolling", "myopic"):
        out.append(f"the project solves {mode}; the loop sizes one full-horizon LP")
    if getattr(config, "sclopf", False):
        out.append("the project solves security-constrained (SCLOPF); the loop sizes the plain LOPF")
    if (getattr(config, "extra_functionality_code", "") or "").strip():
        out.append("the project's extra_functionality code is applied in the loop")
    if network is not None:
        from services.solver import process_uc

        gens, links = process_uc.committable_counts(network)
        if gens or links:
            out.append(f"{gens + links} committable unit(s): solved as a MIP with n.optimize's defaults, not the "
                       "project's unit-commitment keywords (process and island UC), MIP gap or time limit; on the "
                       "reduced year, start-ups and min up/down times across representative-period joins are "
                       "approximate")
    if getattr(config, "solver_options", None) or not getattr(config, "presolve_enabled", True):
        out.append("the project's solver and presolve options are not merged; the loop uses its own solver_options")
    return out


def _tsam_available() -> bool:
    try:
        import tsam  # noqa: F401
    except ImportError:
        return False
    return True


class SizingLoopRefusal(ValueError):
    """The network or inputs cannot carry the sizing loop."""


@dataclasses.dataclass
class SizingInputs:
    """What the loop needs beyond the network. `envelope` is a `dynamic_envelope` result (its `phases` and `max`)."""
    link: str
    envelope: dict
    ramp_mw_per_min: float
    sk_mva: float
    pst_limit: float
    transformer_library_mva: tuple
    xr: float = DEFAULT_XR
    tan_phi_swing: float | None = None
    escr_min: float = ESCR_MIN
    gfm_credit: float = GFM_CREDIT
    p_n_mw: float | None = None
    reduce: bool = True
    harmonics: Callable | None = None
    campus: dict = dataclasses.field(default_factory=dict)
    dyr_path: str | None = None


# ── inputs ──────────────────────────────────────────────────────────────────


def _envelope_max(env: dict) -> dict:
    mx = env.get("max") or {}
    for key in ("swing_mw", "oscillation_amplitude_mw", "oscillation_period_s"):
        if mx.get(key) is None:
            raise SizingLoopRefusal(f"the envelope's max has no {key}")
    return mx


def _check_inputs(n, inp: SizingInputs) -> None:
    if inp.link not in n.links.index:
        raise SizingLoopRefusal(f"link {inp.link} is not in the network")
    _, links = pcs_statcom_tags(n)
    if links is None or inp.link not in links.index:
        raise SizingLoopRefusal(f"link {inp.link} carries no AS-05 PQ tags; the PQ and Pst checks need them")
    if not inp.transformer_library_mva:
        raise SizingLoopRefusal("the transformer library is empty")
    for name, v in (("sk_mva", inp.sk_mva), ("pst_limit", inp.pst_limit), ("ramp_mw_per_min", inp.ramp_mw_per_min),
                    ("xr", inp.xr)):
        if not (math.isfinite(float(v)) and float(v) > 0):
            raise SizingLoopRefusal(f"{name} must be > 0, got {v}")
    if float(_envelope_max(inp.envelope)["oscillation_period_s"]) <= 0:
        raise SizingLoopRefusal("the envelope's oscillation period must be > 0")


# ── the reduced year ────────────────────────────────────────────────────────


class _PeriodView:
    """The single-period MultiIndex view `aggregate_period_snapshots` reads (snapshots, load and availability
    profiles, objective weights), over a network without investment periods. Snapshots that are not timestamps
    (a RangeIndex) are read as hourly, so tsam's step length is an hour, not a nanosecond."""

    def __init__(self, n):
        sns = n.snapshots
        self.timesteps = sns if isinstance(sns, pd.DatetimeIndex) else pd.date_range("2001-01-01", periods=len(sns),
                                                                                     freq="h")
        idx = pd.MultiIndex.from_product([[0], self.timesteps], names=["period", "timestep"])
        self.snapshots = idx
        self.snapshot_weightings = pd.DataFrame(n.snapshot_weightings.to_numpy(), index=idx,
                                                columns=n.snapshot_weightings.columns)
        self.loads_t = _Frames(p_set=_reindex(n.loads_t.p_set, idx))
        self.generators_t = _Frames(p_max_pu=_reindex(n.generators_t.p_max_pu, idx))


class _Frames:
    def __init__(self, **frames):
        self.__dict__.update(frames)


def _reindex(frame: pd.DataFrame, idx: pd.MultiIndex) -> pd.DataFrame:
    out = frame.copy()
    out.index = idx[: len(out)] if len(out) == len(idx) else idx[:0]
    return out


def _forget(view) -> None:
    """Drop the clustering cache's entries for `view`: they are keyed by id(view), which a later object may reuse
    once this one is freed."""
    from services import time_aggregation_service as tas

    with tas._cache_lock:
        for key in [k for k in tas._cache if k[0] == id(view)]:
            del tas._cache[key]


def reduce_year(n, config) -> tuple[object, dict]:
    """A copy of `n` on tsam's representative snapshots, weighted to the full year, and `{"snapshots",
    "of", "typical_periods"}`. Falls back to the full year where the limited-foresight clustering does."""
    from services.time_aggregation_service import aggregate_period_snapshots

    if isinstance(n.snapshots, pd.MultiIndex):
        raise SizingLoopRefusal("the sizing loop solves one investment period; run the periods myopically")
    view = _PeriodView(n)
    try:
        agg = aggregate_period_snapshots(view, 0, config)
    finally:
        _forget(view)
    keep = n.snapshots[view.timesteps.get_indexer(agg.snapshots.get_level_values(1))]
    out = n.copy()
    reduced = len(keep) < len(n.snapshots)
    if reduced:
        # tsam's weight is cluster size x the objective weight; each column takes the cluster size x its own.
        orig = n.snapshot_weightings.loc[keep]
        count = pd.Series(agg.weights.to_numpy(), index=keep) / orig["objective"]
        out.set_snapshots(keep)
        for col in out.snapshot_weightings.columns:
            out.snapshot_weightings[col] = orig[col] * count
    return out, {"snapshots": len(out.snapshots), "of": len(n.snapshots), "typical_periods": agg.typical_periods,
                 "reduced": reduced}


# ── one solve ───────────────────────────────────────────────────────────────


def _config_with_floors(config, cuts: dict):
    cfg = copy.deepcopy(config)
    floors = dict(getattr(cfg, "envelope_floors", None) or {})
    for key, cut in (("power_floor_mw", "p_floor_mw"), ("energy_floor_mwh", "e_floor_mwh")):
        if cut in cuts:
            floors[key] = max(float(floors.get(key) or 0.0), cuts[cut])
    cfg.envelope_floors = floors or None
    return cfg


def _freeze(n, link: str, size_mva: float) -> None:
    n.links.at[link, "p_nom"] = size_mva
    n.links.at[link, "p_nom_extendable"] = False
    pu = max(n.get_switchable_as_dense("Link", "p_max_pu")[link].abs().max(),
             n.get_switchable_as_dense("Link", "p_min_pu")[link].abs().max())
    n.links.at[link, "pq_s_n_mva"] = size_mva           # AS-05: resize the loss chord with the transformer
    n.links.at[link, "pq_p_cap_mw"] = size_mva * pu


def _prepare(n, cfg, undo: list) -> dict:
    """The project solve's pre-LP transforms on the private copy `n`, and the `n.optimize` keywords it would pass.
    Each transform's restore goes on `undo`: the modelling assumptions register their VOLL and DSR slacks in the
    open project's transient rows, and only their restore clears them."""
    from services import solver_service as ss
    from services.adequacy.energization import apply_energization

    undo.append(apply_energization(n))
    undo.append(ss._apply_modelling_assumptions(n, cfg, lambda _msg: None)[0])
    kw = {"extra_functionality": ss.build_lp_chain(n, cfg, None)}
    if getattr(cfg, "transmission_losses", False):
        kw["transmission_losses"] = ss._compute_loss_atol(n)
    return kw


def _nominal(frame, attr, name) -> float:
    return float(frame.at[name, f"{attr}_opt" if bool(frame.at[name, f"{attr}_extendable"]) else attr])


def _sum_var(m, var) -> float:
    return float(m.variables[var].solution.sum()) if var in m.variables else 0.0


def _sizes(n, link: str) -> dict:
    m = n.model
    pq = solution(n)
    sizes = {link: _nominal(n.links, "p_nom", link), "q_ss": pq["q_ss"], "q_dyn": pq["q_dyn"],
             "p_sm": _sum_var(m, "EnvelopeFloor-p_smooth"), "e_sm": _sum_var(m, "EnvelopeFloor-e_smooth")}
    for s in n.stores.index:
        sizes[f"store:{s}"] = _nominal(n.stores, "e_nom", s)
        ch = n.stores.at[s, "charger"] if "charger" in n.stores.columns else None
        if isinstance(ch, str) and ch in n.links.index:
            sizes[f"charger:{s}"] = _nominal(n.links, "p_nom", ch)
    for s, v in pq["s_x"].items():
        sizes[f"s_x:{s}"] = float(v)
    return sizes


def solve_once(base, config, inp: SizingInputs, state: dict, *, reduce: bool, solver_name: str = "highs",
               solver_options: dict | None = None, stop_event=None, undo: list | None = None) -> dict:
    """One LP under `state` (cuts, frozen) on a copy of `base`; the result `run_sizing_loop` reads, plus the
    solved `network` for the forward pass. A stop request during the solve interrupts it (status "aborted").
    The copy's transform restores go on `undo` for the caller to run once it has read the network; without
    `undo` they run before returning."""
    n, info = reduce_year(base, config) if reduce else (base.copy(), {"snapshots": len(base.snapshots),
                                                                      "of": len(base.snapshots)})
    info["basis"] = "reduced_year" if reduce and info.get("reduced") else "full_year"
    cuts, frozen = state["cuts"], state["frozen"]
    mx = _envelope_max(inp.envelope)
    lp = {k: v for k, v in cuts.items() if k in sc.CUT_COLUMNS}
    sc.tag_cuts(n, inp.link, lp, swing_mw=float(mx["swing_mw"]) + float(mx["oscillation_amplitude_mw"]),
                tan_phi_swing=_tan_phi(inp), xr=inp.xr)
    if inp.link in frozen:
        _freeze(n, inp.link, frozen[inp.link])
    cfg = _config_with_floors(config, cuts)
    watcher = _AbortWatcher(stop_event, threading.get_ident())
    watcher.start()
    own = undo is None
    undo = [] if own else undo
    vintages = dict(_frozen_vintage_store())
    try:
        watcher.arm()
        status, condition = n.optimize(solver_name=solver_name, **_prepare(n, cfg, undo), **(solver_options or {}))
        watcher.disarm()
        if status != "ok":
            return {"status": status, "condition": condition, "info": info}
        return {"status": status, "condition": condition, "cost_eur": float(n.objective),
                "sizes": _sizes(n, inp.link), "info": info, "network": n}
    except KeyboardInterrupt:                                 # the watcher's interrupt: a stop request
        return {"status": "aborted", "condition": "stopped during the solve", "info": info}
    except Exception as exc:                                  # noqa: BLE001 - the controller reads a status
        logger.exception("sizing loop: the solve raised")
        return {"status": "error", "condition": f"{type(exc).__name__}: {exc}", "info": info}
    finally:
        watcher.disarm()
        watcher.shutdown()
        if own:
            _restore(undo, vintages)


def _restore(undo: list, vintages: dict) -> None:
    """Run the copies' transform restores, last first, as run_simulation does, and put the thread's frozen-vintage
    store back: the modelling assumptions mark VOLL and DSR slacks in the open project's transient rows, and only
    their restore clears them."""
    for fn in reversed(undo):
        try:
            fn()
        except Exception:                                     # noqa: BLE001 - one failed restore must not skip the rest
            logger.exception("sizing loop: a restore raised")
    store = _frozen_vintage_store()
    store.clear()
    store.update(vintages)


# ── the forward pass ────────────────────────────────────────────────────────


def _tan_phi(inp: SizingInputs) -> float:
    if inp.tan_phi_swing is not None:
        return float(inp.tan_phi_swing)
    v = (inp.envelope.get("max") or {}).get("tan_phi_swing")
    return TAN_PHI_SWING_ASSUMED if v is None else float(v)


def _check(name, ok, *, lever="cut", cut=None, value=None, limit=None, note="", **kw) -> dict:
    return {"name": name, "ok": bool(ok), "lever": lever, "cut": cut or {}, "value": value, "limit": limit,
            "note": note, **kw}


def _bus1_load(n, link, col) -> pd.Series:
    frame = n.get_switchable_as_dense("Load", col)
    return frame[n.loads.index[n.loads["bus"] == n.links.at[link, "bus1"]]].sum(axis=1)


def _transformer(n, inp, state, flow) -> tuple[dict, float]:
    pf = float(n.links.at[inp.link, "pq_pf"])
    s_need = float(np.abs(flow).max()) / pf
    lib = sorted(float(s) for s in inp.transformer_library_mva)
    up = next((s for s in lib if s >= s_need - 1e-6), None)
    frozen = state["frozen"].get(inp.link)
    s_n = frozen if frozen is not None else (up or lib[-1])
    if frozen is not None and s_need > frozen + 1e-6:
        return _check("transformer", False, cut={"import_cap_mw": frozen * pf}, value=s_need, limit=frozen,
                      note="the flow outgrows the frozen transformer"), s_n
    if up is None:
        return _check("transformer", False, cut={"import_cap_mw": lib[-1] * pf}, value=s_need, limit=lib[-1],
                      note="the flow needs more than the largest library transformer"), s_n
    return _check("transformer", True, value=s_need, limit=s_n, library={inp.link: up}), s_n


def _gridspine():
    """(campus_reactive, flicker, island_sc_seq, strength), or a refusal naming what the build lacks."""
    try:
        from gridspine.static import campus_reactive, flicker, island_sc_seq, strength
    except ImportError as exc:  # pragma: no cover - build-shape branch
        raise SizingLoopRefusal(f"the sizing loop's forward checks need gridspine ({exc})") from exc
    return campus_reactive, flicker, island_sc_seq, strength


def _pq(n, inp, state, flow, s_n) -> tuple[dict, float]:
    cr = _gridspine()[0]
    pcs_s_mva, statcom_split = cr.pcs_s_mva, cr.statcom_split
    t = n.links.loc[inp.link]
    q_load = _bus1_load(n, inp.link, "q_set").reindex(flow.index).to_numpy()
    p = flow.to_numpy()
    q_need = float((q_load + float(t["pq_x_pu"]) * (p / float(t["pq_pf"])) ** 2 / s_n
                    - math.tan(math.acos(float(t["pq_pf_req"]))) * p).max())   # signed, as AS-05's pq row
    pq = solution(n)
    q_pcs = 0.0
    for s, s_x in pq["s_x"].items():
        p_mw = _nominal(n.links, "p_nom", n.stores.at[s, "charger"])
        q_pcs += math.sqrt(max(pcs_s_mva(p_mw, float(s_x)) ** 2 - p_mw ** 2, 0.0))
    statcom = pq["q_ss"] + pq["q_dyn"]
    steady_need = max(0.0, q_need - q_pcs)
    _, q_dyn_free = statcom_split(statcom, steady_need)
    short = steady_need - statcom
    if short > 1e-3:
        margin = state["cuts"].get("pq_margin_mvar", 0.0) + short
        return _check("pq", False, cut={"pq_margin_mvar": margin}, value=q_pcs + statcom, limit=q_need), q_dyn_free
    return _check("pq", True, value=q_pcs + statcom, limit=q_need), q_dyn_free


def _escr(n, inp, sizes) -> dict:
    gfm = sc._gfm_stores(n)
    gfm_mva = sum(sizes.get(f"charger:{s}", 0.0) + sizes.get(f"s_x:{s}", 0.0) for s in gfm)
    p_n = inp.p_n_mw if inp.p_n_mw is not None else float(_bus1_load(n, inp.link, "p_set").max())
    if not p_n > 0:
        raise SizingLoopRefusal(f"P_n must be > 0 for the ESCR check, got {p_n}")
    escr = _gridspine()[3].escr(inp.sk_mva + inp.gfm_credit * gfm_mva, 0.0, p_n)   # no shunt Mvar here
    if escr >= inp.escr_min - 1e-9:
        return _check("escr", True, value=escr, limit=inp.escr_min)
    if not gfm:
        return _check("escr", False, lever="none", value=escr, limit=inp.escr_min,
                      note="no grid-forming store is offered; the grid alone sets the ESCR")
    return _check("escr", False, cut={"gfm_mva_min": sc.gfm_mva_min(inp.escr_min, p_n, inp.sk_mva, inp.gfm_credit)},
                  value=escr, limit=inp.escr_min)


def _smoothing(n, inp, sizes) -> dict:
    floors = floors_from_envelope(inp.envelope, inp.ramp_mw_per_min)
    p_floor, e_floor = floors["power_floor_mw"], floors["energy_floor_mwh"]
    ok = sizes["p_sm"] >= p_floor - 1e-6 and sizes["e_sm"] >= e_floor - 1e-6
    if ok:
        return _check("smoothing", True, value=sizes["p_sm"], limit=p_floor)
    if not _flagged(n.stores, "smoothing_tier"):
        return _check("smoothing", False, lever="none", value=sizes["p_sm"], limit=p_floor,
                      note="no store is flagged smoothing_tier")
    return _check("smoothing", False, cut={"p_floor_mw": p_floor, "e_floor_mwh": e_floor}, value=sizes["p_sm"],
                  limit=p_floor)


def _pst(n, inp, state, sizes, q_dyn_free) -> dict:
    _, fl, sc_seq, _ = _gridspine()
    c_factor = sc_seq.c_factor
    mx = _envelope_max(inp.envelope)
    swing = float(mx["swing_mw"]) + float(mx["oscillation_amplitude_mw"])
    tan_phi = _tan_phi(inp)
    r = fl.changes_per_min(60.0 / float(mx["oscillation_period_s"]))
    sk_min = inp.sk_mva / c_factor(None, "max")
    step = sc.pst_step_mva(swing, sizes["p_sm"], q_dyn_free, tan_phi, inp.xr)
    d_pct = step / sk_min * 100.0
    pst = fl.pst(d_pct, r)
    if pst <= inp.pst_limit * (1 + 1e-6):
        return _check("pst", True, value=pst, limit=inp.pst_limit)
    rhs0 = sc.pst_rhs_mva(inp.pst_limit, r, inp.sk_mva)
    d_lim_pct = rhs0 / sk_min * 100.0
    step_lp = sc.pst_step_mva(swing, sizes["p_sm"], sizes["q_dyn"], tan_phi, inp.xr)
    rhs = sc.tightened_pst_rhs(state["cuts"].get("pst_rhs_mva", rhs0), step_lp_mva=step_lp, d_pct=d_pct,
                               d_lim_pct=d_lim_pct, sk_min_mva=sk_min)
    return _check("pst", False, cut={"pst_rhs_mva": max(rhs, 0.0)}, value=pst, limit=inp.pst_limit)


def forward_checks(result: dict, state: dict, inp: SizingInputs) -> list[dict]:
    """The forward pass on the solved, fixed capacities of `result["network"]`."""
    n, sizes = result["network"], result["sizes"]
    flow = n.links_t.p0[inp.link]
    tr, s_n = _transformer(n, inp, state, flow)
    pq, q_dyn_free = _pq(n, inp, state, flow, s_n)
    checks = [tr, pq, _escr(n, inp, sizes), _smoothing(n, inp, sizes), _pst(n, inp, state, sizes, q_dyn_free)]
    if inp.harmonics is not None:
        cost = float(inp.harmonics(n, sizes) or 0.0)
        checks.append(_check("harmonics", cost <= 0, lever="filter_cost", filter_cost_eur=cost))
    for name in CAMPUS_CHECKS:
        fn = inp.campus.get(name)
        checks.append(_check(name, True, skipped=True, note="needs a campus model") if fn is None
                      else {**fn(n, result), "name": name})
    return checks


# ── the loop ────────────────────────────────────────────────────────────────


def run_sizing(network, config, inp: SizingInputs, *, max_rounds: int = sl.MAX_ROUNDS,
               freeze_after: int = sl.FREEZE_AFTER, stop_event=None, on_iteration=None, solver_name: str = "highs",
               solver_options: dict | None = None, solve=None) -> dict:
    """Run the sizing loop on a copy of `network`; `network` and `config` are left untouched. `solve` (tests)
    replaces `solve_once` with the same signature. The result is `run_sizing_loop`'s, plus `sheet` (the AS-07
    control-settings sheet of a converged run, or None) and `sheet_paths`."""
    refused = refusals(config)
    if refused:
        raise SizingLoopRefusal("; ".join(refused))
    base = network.copy()
    _check_inputs(base, inp)
    solve = solve or solve_once
    last: dict = {}
    undo: list = []
    vintages = dict(_frozen_vintage_store())
    try:
        return _run(base, config, inp, solve, last, undo, max_rounds=max_rounds, freeze_after=freeze_after,
                    stop_event=stop_event, on_iteration=on_iteration, solver_name=solver_name,
                    solver_options=solver_options)
    finally:
        _restore(undo, vintages)                              # after the forward checks and the sheet


def _run(base, config, inp, solve, last, undo, *, max_rounds, freeze_after, stop_event, on_iteration, solver_name,
         solver_options) -> dict:
    def run(state, reduce):
        try:
            res = solve(base, config, inp, state, reduce=reduce, solver_name=solver_name,
                        solver_options=solver_options, stop_event=stop_event, undo=undo)
        except KeyboardInterrupt:
            if stop_event is None or not stop_event.is_set():
                raise
            # The watcher's interrupt landed just outside solve_once's handler.
            return {"status": "aborted", "condition": "stopped during the solve"}
        if res.get("network") is not None:
            last["network"] = res["network"]
        return res

    out = sl.run_sizing_loop(lambda s: run(s, inp.reduce), lambda r, s: forward_checks(r, s, inp),
                             max_rounds=max_rounds, freeze_after=freeze_after,
                             final_solve=(lambda s: run(s, False)) if inp.reduce else None,
                             stop_event=stop_event, on_iteration=on_iteration)
    lines = basis_lines(config, base)
    if inp.reduce and any(r.get("info", {}).get("basis") == "full_year" for r in out["rounds"]
                          if r.get("basis") == "rounds"):
        lines.append("tsam not available: inner rounds solve the full year" if not _tsam_available()
                     else "the year reduction fell back to the full year for some inner rounds")
    out["basis_note"] = "; ".join([BASIS_NOTE, *lines])
    out["sheet"], out["sheet_paths"] = None, []
    if out["status"] == "converged" and last.get("network") is not None:
        from services import control_settings

        try:
            out["sheet"] = control_settings.sheet_from_solved(last["network"], inp.envelope, inp.ramp_mw_per_min)
        except control_settings.ControlSettingsError as exc:
            out["warnings"].append(f"control-settings sheet not built: {exc}")
        if out["sheet"] is not None and inp.dyr_path:
            out["sheet_paths"] = [str(p) for p in control_settings.write_sheet(out["sheet"], inp.dyr_path)]
    return out
