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
* **The user's ENS target and reserve margin are stripped inside the sweep.**
  A binding standard would make every severity read as "the standard" (or go
  infeasible); the contingency question is what the OUTAGE costs,
  unconstrained.
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
import logging
import math
import queue
import threading
from typing import Callable

logger = logging.getLogger(__name__)

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


# The solver words that mean the foreground really is the user's plan again.
#
# It is the CONDITION that has to be read, never the status. `run_simulation`
# returns linopy's `(SolverStatus, TerminationCondition)`, and `SolverStatus.ok`
# covers `time_limit`, `iteration_limit`, `terminated_by_limit`, `suboptimal`
# and `imprecise` as well as `optimal` — a re-solve that hit a MIP time limit
# reports `ok` and leaves a dispatch that is NOT the user's plan. In the other
# direction a genuinely infeasible re-solve reports status `warning` with the
# word `infeasible` in the condition, so a caller reading the status alone
# cannot even name what went wrong. The frontier's own point loop has always
# read `_condition or _status` for exactly this reason.
#
# `ok` is here because a `mode="pf"` run legitimately reports `("ok", "ok")`,
# and `lopf+ac_pf_ok` because a successful stage-2 AC power flow rewrites the
# condition. A compound like `optimal; ac_pf_failed: …` starts with a clean
# word and counts as restored: the LP dispatch — which is what the foreground
# results are — did come back; the AC PF that failed afterwards is a separate
# condition already reported on its own surface.
#
# This lives in ONE place because the two studies and two panels each used to
# decide it for themselves, which is how the `ok`-means-optimal reading got
# into three of them at once (shipped-code review of 12e, finding S1).
_RESTORE_CLEAN_PREFIXES = ("optimal", "ok", "lopf+ac_pf_ok")


def restore_is_clean(word) -> bool:
    """Whether a closing re-solve's reported word means the plan is back."""
    if word is None:
        return False
    return str(word).startswith(_RESTORE_CLEAN_PREFIXES)


def _restore_base_guarded(network, lock, cfg, log_queue, final_state_update):
    """The closing base re-solve: ``(restored, word)``.

    ``restored`` means THE PLAN IS BACK — the re-solve ran AND its condition
    is one the fleet can be read against (`restore_is_clean`). It does not
    mean "it did not raise": a re-solve that comes back `infeasible`, or one
    that stopped at a MIP time limit, raises nothing and restores nothing, and
    the one thing the caller must not be told is that the foreground is the
    user's plan when it demonstrably is not.

    A restore that raises is REPORTED, not propagated — the sweep's rows are
    still a valid answer.
    """
    from services.solver_service import run_simulation

    final_sink: dict = {}
    try:
        status, condition = run_simulation(
            cfg, network, lock, threading.Event(),
            log_queue if log_queue is not None else queue.SimpleQueue(),
            state_update=final_state_update or (lambda **kw: final_sink.update(kw)),
        )
    except Exception as exc:                                  # noqa: BLE001
        logger.exception(
            "contingency sweep: the closing base re-solve FAILED — the network "
            "is left on the last contingency and the foreground results do not "
            "describe the user's own config")
        return False, f"raised: {exc}"
    word = str(condition or status)
    if not restore_is_clean(word):
        logger.warning(
            "contingency sweep: the closing base re-solve returned %r — it ran "
            "but did not restore the user's plan", word)
    return restore_is_clean(word), word


def run_contingency_sweep(network, lock, cfg, contingencies: list[dict], *,
                          log_queue=None,
                          final_state_update=None, stop_event=None) -> dict:
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
    # Strip the reliability target AND the firm-capacity standard inside the
    # sweep; keep everything else. Same rationale for both: a binding standard
    # would make every severity read as the standard rather than as the
    # outage's own damage. The margin is the sharper case — `freeze_capacities`
    # pins bounds while KEEPING `p_nom_extendable=True`, so the nominal
    # variable still exists and stays pinned: any contingency that removes
    # derated capacity violates a surviving margin, the re-solve goes
    # infeasible, and the WHOLE sweep fails on the base solve.
    sweep_cfg = dataclasses.replace(
        cfg, ens_cap_permyriad=None, ens_zone_cap_multiple=None,
        reserve_margin=None)

    unfreeze = freeze_capacities(network)
    results: dict = {"base": {}, "contingencies": {}, "aborted": False,
                     "base_restored": False}
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
            # Phase 12e: checked BETWEEN contingencies and acted on with a
            # `break`, never an exception — the closing re-solve below sits
            # outside the `finally`, so an exception here would skip the
            # restore and leave the network on the last contingency.
            if stop_event is not None and stop_event.is_set():
                results["aborted"] = True
                break
            undo = c["mutate"](network)
            sink: dict = {}
            # Tell the solver not to re-broadcast `_user_ts` over this
            # mutation. Every contingency here works by rewriting the same
            # `_t` tables that reapply restores, and the solve runs on the
            # FOREGROUND network, so without this marker the uploaded
            # profiles were reinstated before the LP was built: the
            # contingency solved an unmutated network and reported a ΔEUE of
            # zero. Set per-contingency and cleared in the same `finally` as
            # the undo, so a mutation and its suppression can never outlive
            # each other — and so the base and closing solves, which must see
            # the real uploaded profiles, still get the reapply.
            network._adequacy_transient_profiles = True
            try:
                _solve_once(sweep_cfg, network, lock, log_queue, sink)
            finally:
                try:
                    del network._adequacy_transient_profiles
                except AttributeError:
                    pass
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
    #
    # Phase 12e: GUARDED, like the frontier's `_restore_base` — which since
    # the shipped-code review's finding 13 also returns `(ok, status)`, so the
    # two studies now report their closing re-solve in the same shape.
    # Unguarded, a
    # restore that raises destroyed `results` entirely — including the partial
    # rows an abort exists to keep — and surfaced as an opaque `failed`.
    # `base_restored` records whether the re-solve RAN, and carries its solver
    # status: `True` never meant "the plan is back", only "it did not raise".
    results["base_restored"], results["base_restore_status"] = _restore_base_guarded(
        network, lock, cfg, log_queue, final_state_update)
    return results


# ── class B: link forced outages ──────────────────────────────────────────

MAX_CLASS_B_LINKS = 20


def class_b_contingencies(n) -> list[dict]:
    """
    One contingency per Link with resolvable occurrence data (asset value or
    carrier default) and positive capacity. Lines/Transformers stay with the
    already-shipped SCLOPF machinery (spec §6.2) — this driver covers the
    gap SCLOPF leaves: HVDC / power-to-X links.

    Outage representation: p_max_pu AND p_min_pu forced to 0, static and
    time-varying alike, capacity intact — preflight rightly rejects a fixed
    link with p_nom = 0, and a bidirectional link needs its negative bound
    zeroed too. The mutation's undo re-fetches frames (see freeze note).
    """
    from services.adequacy.occurrence import resolve_outage_params

    links = getattr(n, "links", None)
    if links is None or links.empty:
        return []
    params = resolve_outage_params(n, "links")
    out: list[dict] = []
    for name in links.index:
        row = params.loc[name]
        if row["source"] == "missing":
            continue
        try:
            cap = float(links.at[name, "p_nom"])
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(cap) and cap > 0):
            continue

        def mutate(nn, name=name):
            lk = nn.links
            orig_max = float(lk.at[name, "p_max_pu"])
            orig_min = float(lk.at[name, "p_min_pu"])
            lk.at[name, "p_max_pu"] = 0.0
            lk.at[name, "p_min_pu"] = 0.0
            t = getattr(getattr(nn, "links_t", None), "p_max_pu", None)
            had_t = t is not None and name in getattr(t, "columns", [])
            orig_t = t[name].copy() if had_t else None

            if had_t:
                t[name] = 0.0

            def undo():
                live = nn.links
                if name in live.index:
                    live.at[name, "p_max_pu"] = orig_max
                    live.at[name, "p_min_pu"] = orig_min
                lt = getattr(getattr(nn, "links_t", None), "p_max_pu", None)
                if had_t and lt is not None and name in getattr(lt, "columns", []):
                    lt[name] = orig_t

            return undo

        out.append({
            "id": f"link:{name}:forced_outage",
            "mutate": mutate,
            "meta": {
                "name": str(name),
                "q": float(row["rate"]),
                "basis": str(row["basis"]),
                "mttr_hours": float(row["mttr_hours"]),
            },
        })
    if len(out) > MAX_CLASS_B_LINKS:
        raise SweepBudgetError(
            f"{len(out)} occurrence-bearing links exceed the class-B budget "
            f"of {MAX_CLASS_B_LINKS} — clear outage data on minor links or "
            "run per region"
        )
    return out


def run_class_b_sweep(network, lock, cfg, *, log_queue=None,
                      final_state_update=None,
                      stop_event=None) -> tuple[list[dict], dict]:
    """
    Class-B rows: sweep every eligible link outage and price it first-order
    (see the module docstring's severity semantics):

        criticality €/yr = q × ΔEUE_full-horizon × VoLL
        occurrence /yr   = 8760·q / MTTR   (cycle frequency, occurrence.py)
        severity €       = criticality / occurrence   (f×S by construction)

    A contingency whose re-solve is not optimal is returned with its status
    and NO failure_mode — a distinct outcome, not a zero.
    """
    contingencies = class_b_contingencies(network)
    if not contingencies:
        # The 2-tuple on EVERY path. This early return kept the bare list when
        # the rest of the function moved to `(rows, restore)`, and the caller
        # unpacks — so a network with no eligible link (a network with no
        # Links at all, the common case) raised "not enough values to unpack"
        # inside the worker, which reported `failed` with no rows and skipped
        # class C entirely. Nothing ran here, so nothing was restored: the
        # network is untouched and the flags say so rather than claiming a
        # re-solve that never happened.
        return [], {"base_restored": None, "base_restore_status": None,
                    "aborted": False}
    voll = float(getattr(cfg, "voll", 0.0) or 0.0)
    swept = run_contingency_sweep(
        network, lock, cfg, contingencies, stop_event=stop_event,
        log_queue=log_queue, final_state_update=final_state_update)
    rows: list[dict] = []
    for c in contingencies:
        # Phase 12e: an ABORTED sweep carries only the contingencies it got
        # to. Skipping the rest keeps the partial rows the abort exists to
        # preserve; indexing them raised `KeyError`, surfaced as an opaque
        # `failed`, and lost everything.
        res = swept["contingencies"].get(c["id"])
        if res is None:
            continue
        meta = c["meta"]
        if res["status"] not in ("ok", "optimal"):
            rows.append({"id": c["id"], "status": res["status"],
                         "delta_eue_mwh": None, "failure_mode": None,
                         "meta": meta})
            continue
        delta = float(res["delta_eue_mwh"] or 0.0)
        q = meta["q"]
        crit = q * delta * voll
        occ = (8760.0 * q / meta["mttr_hours"]
               if meta["mttr_hours"] and math.isfinite(meta["mttr_hours"])
               and meta["mttr_hours"] > 0 else 0.0)
        rows.append({
            "id": c["id"],
            "status": res["status"],
            "delta_eue_mwh": delta,
            "failure_mode": {
                "mode_id": c["id"],
                "component_class": "Link",
                "name": meta["name"],
                "failure_class": "B",
                "occurrence_per_year": occ,
                "occurrence_basis": meta["basis"],
                "severity_eur": (crit / occ) if occ > 0 else 0.0,
                "criticality_eur_per_year": crit,
                "in_metric_scope": True,
                "engine": "lp_proxy",
                "fidelity": "deterministic_scenario",
            },
            "meta": meta,
        })
    rows.sort(key=lambda r: (r["delta_eue_mwh"] or 0.0), reverse=True)
    # Phase 12e (shipped-code review, finding 1): the restore's outcome rides
    # OUT of the engine. It was computed and dropped here, so a sweep whose
    # closing re-solve failed reported `done` with rows and no error, while
    # the network sat on the last contingency — strictly less visible than
    # before the guard existed, when the exception at least reached the
    # record as `failed`. The caller puts it on the wire.
    return rows, {"base_restored": swept.get("base_restored"),
                  "base_restore_status": swept.get("base_restore_status"),
                  "aborted": bool(swept.get("aborted"))}
