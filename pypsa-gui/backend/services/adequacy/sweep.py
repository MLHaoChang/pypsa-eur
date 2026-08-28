"""
The fixed-capacity contingency sweep driver (Phase 4).

Design: spec §§4.1, 5.4, 7.2; plan 2026-08-28-fmea-phase4-taxonomy.md. One
driver powers class B (Link outages) and class C (stress scenarios):

* **Fixed-capacity operational re-solves.** Every ``*_nom_extendable`` is
  transiently forced off with ``p_nom = p_nom_opt`` where a solved size
  exists, so severity is a DISPATCH question, not an investment one — an
  unfrozen LP would simply build its way around the contingency and report
  zero severity.
* **In-process, private sink.** Each re-solve runs ``run_simulation`` with a
  throwaway ``state_update`` sink: the foreground solver state (``_state``)
  is never touched, and nothing goes over HTTP (which would pay an
  undo-snapshot netCDF export and a results wipe per mutation — spec §7.2).
* **The sweep ends in the base state.** A closing UNFROZEN base re-solve
  (this one may write to a caller-supplied sink) leaves the network's
  dispatch tables exactly where the user's own solve left them, not at the
  last contingency.
* **The user's ENS target is stripped inside the sweep.** A binding cap
  would make every severity read as "the cap" (or go infeasible); the
  contingency question is what the OUTAGE costs, unconstrained.
* **Budget guard**: at most ``MAX_CONTINGENCIES`` per run — the sweep is
  tens of solves at most, never a Monte Carlo (spec §3.2).

Severity semantics (documented once, used by both classes): the mutation is
held for the WHOLE horizon, so ``delta_eue_mwh`` is the annual damage of
full unavailability. Expected annual criticality then first-orders as
``unavailability × delta_eue × VoLL`` — no outage-window placement problem,
because the horizon covers every hour (spec §5.4's timing-expectation
requirement, satisfied by integrating rather than sampling).
"""
from __future__ import annotations

import dataclasses
import math
import queue
import threading
from typing import Callable

MAX_CONTINGENCIES = 20


class SweepBudgetError(ValueError):
    pass


_CAPACITY_ATTRS = (
    ("generators", "p_nom"),
    ("storage_units", "p_nom"),
    ("stores", "e_nom"),
    ("links", "p_nom"),
    ("lines", "s_nom"),
    ("transformers", "s_nom"),
)


def freeze_capacities(n) -> Callable[[], None]:
    """Pin every extendable capacity to its solved size by clamping
    ``*_nom_min = *_nom_max = size`` (``*_nom_opt`` where finite, else the
    current ``*_nom``), KEEPING extendability on.

    Why bounds rather than flipping ``*_nom_extendable`` off: preflight
    rightly rejects a fixed asset with zero capacity
    (``generator_p_nom_invalid`` / ``link_p_nom_invalid``), and a
    never-built option freezes at exactly 0, and preflight equally rejects
    extendable bounds with min == max (generator_p_nom_bounds). So the pin
    is ``min = size, max = size + ε`` (ε = 1e-6 MW): the LP may "build" at
    most a micro-MW above the solved size — orders of magnitude below every
    tolerance in the sweep — and the (near-)constant capital term is the
    same in the base and in every contingency, cancelling out of every
    ΔEUE. Returns an undo closure restoring both bound columns exactly."""
    undo_ops: list[Callable[[], None]] = []
    for attr, nom in _CAPACITY_ATTRS:
        df = getattr(n, attr, None)
        ext_col = f"{nom}_extendable"
        min_col, max_col = f"{nom}_min", f"{nom}_max"
        if df is None or df.empty or ext_col not in df.columns:
            continue
        if min_col not in df.columns or max_col not in df.columns:
            continue
        mask = df[ext_col].astype(bool)
        if not mask.any():
            continue
        idx = df.index[mask]
        orig_min = df.loc[idx, min_col].copy()
        orig_max = df.loc[idx, max_col].copy()

        # Re-fetch the frame at undo time: run_simulation's transient
        # add/remove (VOLL slacks, vintages) can REPLACE the component
        # DataFrame, so a captured reference goes stale and the restore
        # would write into a dead object.
        def _undo(n=n, attr=attr, min_col=min_col, max_col=max_col,
                  orig_min=orig_min, orig_max=orig_max):
            live = getattr(n, attr)
            keep = [i for i in orig_min.index if i in live.index]
            live.loc[keep, min_col] = orig_min.loc[keep]
            live.loc[keep, max_col] = orig_max.loc[keep]

        undo_ops.append(_undo)
        size = df.loc[idx, nom].astype(float)
        opt_col = f"{nom}_opt"
        if opt_col in df.columns:
            opt = df.loc[idx, opt_col].astype(float)
            good = opt.notna() & opt.map(math.isfinite)
            size[good] = opt[good]
        size = size.fillna(0.0).clip(lower=0.0)
        df.loc[idx, min_col] = size
        df.loc[idx, max_col] = size + 1e-6

    def undo_all() -> None:
        for op in reversed(undo_ops):
            op()

    return undo_all


def _electrical_eue_mwh(capture: dict | None, n) -> float:
    """Weighted electrical unserved energy from a solve's capture — the
    class-scope quantity severity is measured in (spec §4.3)."""
    if not capture:
        return 0.0
    bp = capture.get("lost_load_bus_period_mwh")
    if bp is None or getattr(bp, "empty", True):
        return 0.0
    from services.adequacy.metrics import electrical_columns
    cols = electrical_columns(n, list(bp.columns))
    return float(bp[cols].to_numpy().sum()) if cols else 0.0


def _solve_once(cfg, n, lock, log_queue, sink: dict) -> None:
    from services.solver_service import run_simulation

    status, condition = run_simulation(
        cfg, n, lock, threading.Event(),
        log_queue if log_queue is not None else queue.SimpleQueue(),
        state_update=lambda **kw: sink.update(kw),
    )
    sink["_status"] = status
    sink["_condition"] = condition


def run_contingency_sweep(network, lock, cfg, contingencies: list[dict], *,
                          log_queue=None,
                          final_state_update=None) -> dict:
    """
    ``contingencies``: ``[{id, mutate(n) -> undo(), meta}, ...]``. Returns
    ``{"base": {eue_mwh, status}, "contingencies": {id: {delta_eue_mwh,
    eue_mwh, status, meta}}}``. Contingencies whose re-solve does not come
    back optimal are reported with ``status`` — an infeasible contingency is
    a DISTINCT outcome (a starved transit bus has no slack, spec §6.3),
    never silently a zero.
    """
    if len(contingencies) > MAX_CONTINGENCIES:
        raise SweepBudgetError(
            f"{len(contingencies)} contingencies exceed the sweep budget of "
            f"{MAX_CONTINGENCIES} — group assets or run in batches"
        )
    if float(getattr(cfg, "voll", 0.0) or 0.0) <= 0:
        raise ValueError(
            "the contingency sweep requires VOLL > 0 — without slack "
            "generators, severity is an infeasibility, not a number"
        )
    # Strip the reliability target inside the sweep; keep everything else.
    sweep_cfg = dataclasses.replace(
        cfg, ens_cap_permyriad=None, ens_zone_cap_multiple=None)

    unfreeze = freeze_capacities(network)
    results: dict = {"base": {}, "contingencies": {}}
    try:
        base_sink: dict = {}
        _solve_once(sweep_cfg, network, lock, log_queue, base_sink)
        if base_sink.get("_status") not in ("ok", "optimal"):
            raise RuntimeError(
                f"base operational solve failed: {base_sink.get('_condition')}")
        base_eue = _electrical_eue_mwh(base_sink.get("last_lost_load"), network)
        results["base"] = {"eue_mwh": base_eue,
                           "status": base_sink.get("_status")}
        for c in contingencies:
            undo = c["mutate"](network)
            sink: dict = {}
            try:
                _solve_once(sweep_cfg, network, lock, log_queue, sink)
            finally:
                undo()
            status = sink.get("_status")
            eue = _electrical_eue_mwh(sink.get("last_lost_load"), network)
            results["contingencies"][c["id"]] = {
                "status": status,
                "eue_mwh": eue if status in ("ok", "optimal") else None,
                "delta_eue_mwh": (max(eue - base_eue, 0.0)
                                  if status in ("ok", "optimal") else None),
                "meta": c.get("meta", {}),
            }
    finally:
        unfreeze()
    # Closing UNFROZEN base re-solve with the user's ORIGINAL config: leaves
    # dispatch (and, if the caller wires the real state sink, the foreground
    # results) exactly as the user's own solve would.
    final_sink: dict = {}
    from services.solver_service import run_simulation
    run_simulation(
        cfg, network, lock, threading.Event(),
        log_queue if log_queue is not None else queue.SimpleQueue(),
        state_update=final_state_update or (lambda **kw: final_sink.update(kw)),
    )
    return results
