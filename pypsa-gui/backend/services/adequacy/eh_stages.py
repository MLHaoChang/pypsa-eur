"""
The EH study's certification / frontier / FMEA stages — thin wrappers that
wire EXISTING engines into ``run_eh_study`` (plan
docs/superpowers/plans/2026-09-26-eh-wire-skipped-stages.md, WP1–WP3).

Nothing here is a new engine and nothing here builds a report: each stage
returns a ``(status, payload, note, solves_charged)`` fragment (plus the MC
LOLE headline for ``mc_certify``) that ``eh_study`` hands to
``assemble_reference_design_report`` (spec decision 16).

THE FIXED PLAN. ``mc_certify`` and the class-A half of ``fmea_top`` are
statements about ONE plan — the plan whose cost, sizing and TEA the report
describes, i.e. the network as ``ens_solve`` left it. Both read that plan
through ``freeze_fixed_plan``, taken under the mutation lock at the end of
``ens_solve`` and BEFORE the frontier stage re-solves the network at other
targets (decision-18 order puts ``frontier`` first). The frontier's closing
restore re-solves the user's config and normally lands on the same optimum,
but "normally" is not a certification: freezing first means the number the
report certifies cannot depend on whether that restore came back clean.

THE BUDGET. LP solves are charged to the study budget the way ``campaign``
charges them: the frontier costs one solve per point plus the closing
restore; the class-B Link sweep costs its base solve, one per Link and the
restore; the MC and the COPT cost ZERO because they solve nothing. A stage
that cannot fit in the remaining budget is reported ``skipped`` with a note
naming the shortfall rather than run over it (spec decision 17).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from models.energy_hub import CertificationVerdict

logger = logging.getLogger("pypsa_gui.eh_stages")

# Frontier ladder around the pack's target: factors on ``ens_cap_permyriad``,
# loosest first (the engine sorts anyway). ×1 is the report's own point, so
# the curve passes through ``cost_at_target_eur``.
EH_FRONTIER_LADDER: tuple[float, ...] = (4.0, 2.0, 1.0, 0.5, 0.25)
#: Fewer points than this is not a curve; the stage skips rather than run two.
MIN_EH_FRONTIER_POINTS = 3
#: Ranked residual failure modes kept in the report.
FMEA_TOP_N = 10

# Spec decision 14 / P2: FMEA top-N from the EH study is Link-primary Class-B
# residual risk. AC Line/Transformer N-1 stays on SCLOPF and is omitted from
# the FMEA ranking unless a future product decision merges them.
FMEA_TOP_LINK_PRIMARY_NOTE = (
    "Link-primary residual risk (Class-B Link sweep); "
    "AC Line/Transformer N-1 remains on SCLOPF and is omitted from FMEA ranking"
)


# ── the fixed plan ────────────────────────────────────────────────────────

@dataclass
class FixedPlanSnapshot:
    """
    What the certification and screening stages read: the MC inputs and
    the COPT screening of the plan as ``ens_solve`` left it. Either half may
    be absent with its reason — an empty sampled fleet is a fact about the
    input data, recorded here and surfaced as ``not_established``.
    """

    mc_inputs: Any = None
    mc_error: str | None = None
    copt_rows: list[dict] = field(default_factory=list)
    copt_metrics: dict | None = None
    copt_error: str | None = None
    voll: float = 0.0


def freeze_fixed_plan(network, cfg, lock) -> FixedPlanSnapshot:
    """
    Snapshot the MC inputs and screen the fleet, ONCE, under ``lock``.

    ``snapshot_inputs`` walks ``fleet_and_residual`` itself, so the two halves
    share membership by construction (the MC module's invariant); the COPT
    screening below reuses the very same units and residual rather than
    walking the network a second time.
    """
    from services.adequacy.copt import screening_analysis
    from services.adequacy.mc import snapshot_inputs

    snap = FixedPlanSnapshot(voll=float(getattr(cfg, "voll", 0.0) or 0.0))
    with lock:
        try:
            inputs = snapshot_inputs(network, cfg=cfg)
        except Exception as exc:  # noqa: BLE001 — the reason is the payload
            snap.mc_error = f"MC snapshot refused: {exc}"
            snap.copt_error = snap.mc_error
            return snap
    if not inputs.units:
        snap.mc_error = (
            "nothing to sample: no electrical generator carries resolvable "
            "occurrence data (unavailability + MTTR), so the sampled fleet is "
            "empty — a statement about missing input data, not about the system")
        snap.copt_error = snap.mc_error
        return snap
    snap.mc_inputs = inputs
    try:
        import pandas as pd

        residual = pd.Series(inputs.residual, index=network.snapshots)
        weights = pd.Series(inputs.weights, index=network.snapshots)
        analysis = screening_analysis(
            list(inputs.units), residual, weights=weights, voll=snap.voll,
            delta_mw=1.0)
        snap.copt_rows = list(analysis.get("rows") or [])
        snap.copt_metrics = dict(analysis.get("metrics") or {})
    except Exception as exc:  # noqa: BLE001
        logger.exception("EH fixed-plan COPT screening failed")
        snap.copt_error = f"COPT screening failed: {exc}"
    return snap


# ── WP1: mc_certify ───────────────────────────────────────────────────────

def certification_verdict(*, mc_lole_h: float | None,
                          target_lole_h: float | None) -> CertificationVerdict:
    """
    Spec decision 2. LOLE failure fails certification even when ENS is met
    — ENS is the PLANNING metric and never enters this rule.
    """
    if mc_lole_h is None or not math.isfinite(float(mc_lole_h)):
        return "not_established"
    if target_lole_h is None:
        return "no_target"
    return "certified" if float(mc_lole_h) <= float(target_lole_h) + 1e-9 else "failed"


def run_mc_certify_stage(
    frozen: FixedPlanSnapshot,
    pack,
    *,
    stop_event,
    ens_met: bool | None,
) -> tuple[str, dict | None, str | None, float | None]:
    """
    Sequential MC on the frozen plan → ``(status, payload, note, mc_lole_h)``.

    This is the study's OWN baseline, not an ELCC replay, so it is the one
    kind of call site that may carry ``stop_event`` into ``mc_adequacy``
    (see that function's note on common random numbers). Charges no solves.
    """
    from services.adequacy.mc import MC_WARNING_V1, mc_adequacy

    target = pack.availability.target_lole_h
    if frozen.mc_inputs is None:
        note = frozen.mc_error or "MC inputs unavailable"
        return "not_established", {
            "metric": "mc_lole", "target_lole_h": target, "mc_lole_h": None,
            "verdict": "not_established", "ens_met": ens_met,
        }, note, None
    draws = int(getattr(pack, "mc_draws", 200))
    seed = int(getattr(pack, "mc_seed", 0))
    cov_target = float(getattr(pack, "mc_cov_target", 0.05))
    try:
        metrics = mc_adequacy(frozen.mc_inputs, draws=draws, seed=seed,
                              cov_target=cov_target, stop_event=stop_event)
    except Exception as exc:  # noqa: BLE001
        logger.exception("EH mc_certify failed")
        return "not_established", {
            "metric": "mc_lole", "target_lole_h": target, "mc_lole_h": None,
            "verdict": "not_established", "ens_met": ens_met,
        }, f"mc_certify failed: {exc}", None
    lole = metrics.get("lole_hours")
    lole = float(lole) if lole is not None and math.isfinite(float(lole)) else None
    if stop_event is not None and stop_event.is_set():
        note = (f"aborted during mc_certify after {metrics.get('n_samples')} "
                "samples — LOLE not certified")
        return "not_established", {
            "metric": "mc_lole", "target_lole_h": target, "mc_lole_h": None,
            "verdict": "not_established", "ens_met": ens_met,
            "n_samples": metrics.get("n_samples"), "draws_requested": draws,
        }, note, None
    verdict = certification_verdict(mc_lole_h=lole, target_lole_h=target)
    payload = {
        "metric": "mc_lole",
        "certification_metric": pack.availability.certification_metric,
        "target_lole_h": target,
        "mc_lole_h": lole,
        "lole_ci": list(metrics.get("lole_ci") or []),
        "eue_mwh": metrics.get("eue_mwh"),
        "eue_ci": list(metrics.get("eue_ci") or []),
        "by_period": metrics.get("by_period"),
        "n_samples": metrics.get("n_samples"),
        "draws_requested": draws,
        "seed": seed,
        "cov_target": cov_target,
        "converged": metrics.get("converged"),
        "resolution_floor_h": metrics.get("resolution_floor_h"),
        "time_basis": metrics.get("time_basis"),
        "horizon_years": metrics.get("horizon_years"),
        "ens_met": ens_met,
        "verdict": verdict,
        "engine": "mc",
        "fidelity": "sequential_mc",
        "warning": MC_WARNING_V1,
    }
    if verdict == "certified":
        note = f"MC LOLE {lole:.3g} h ≤ target {float(target):.3g} h — certified"
    elif verdict == "failed":
        note = (f"MC LOLE {lole:.3g} h > target {float(target):.3g} h — "
                "certification FAILED"
                + (" although the ENS target is met (spec decision 2)"
                   if ens_met else ""))
    elif verdict == "no_target":
        note = (f"MC LOLE {lole:.3g} h reported; the pack states no "
                "target_lole_h to certify against")
    else:
        note = "MC LOLE not established"
    return "ok" if verdict != "not_established" else "not_established", payload, note, lole


# ── WP2: frontier ─────────────────────────────────────────────────────────

def frontier_targets_for(ens_cap_permyriad: float | None,
                         remaining_solves: int) -> tuple[list[float], str | None]:
    """
    The ladder that fits: ``(targets, skip_reason)``. Empty targets + a
    reason when the stage should be skipped.
    """
    if ens_cap_permyriad is None or not (
            math.isfinite(float(ens_cap_permyriad)) and float(ens_cap_permyriad) > 0):
        return [], "frontier needs a positive ens_cap_permyriad to sweep around"
    affordable = int(remaining_solves) - 1  # the closing restore
    if affordable < MIN_EH_FRONTIER_POINTS:
        return [], (
            f"budget: {remaining_solves} solve(s) left, frontier needs at least "
            f"{MIN_EH_FRONTIER_POINTS} points + 1 closing restore")
    factors = list(EH_FRONTIER_LADDER)
    if affordable < len(factors):
        # Keep the report's own point (×1) and trim from the outside in.
        keep = [1.0] + [f for f in factors if f != 1.0]
        factors = sorted(keep[:affordable], reverse=True)
    return [float(ens_cap_permyriad) * f for f in factors], None


def run_frontier_stage(
    network, lock, cfg, *,
    ens_cap_permyriad: float | None,
    remaining_solves: int,
    stop_event,
    log_queue,
    final_state_update,
) -> tuple[str, dict | None, str | None, int]:
    """
    ε-constraint frontier around the target → ``(status, payload, note,
    solves_charged)``. Every cost field keeps ``excludes_shed_cost: true``
    and its ``period_basis`` (spec decision 3).
    """
    from services.adequacy.frontier import (
        FrontierBudgetError,
        FrontierConfigError,
        knee_index,
        run_frontier_sweep,
    )

    targets, why = frontier_targets_for(ens_cap_permyriad, remaining_solves)
    if not targets:
        return "skipped", None, why, 0
    voll = float(getattr(cfg, "voll", 0.0) or 0.0)
    try:
        res = run_frontier_sweep(
            network, lock, cfg, targets, stop_event=stop_event,
            log_queue=log_queue, final_state_update=final_state_update)
    except (FrontierBudgetError, FrontierConfigError) as exc:
        # Refused before any solve: nothing spent, nothing to restore.
        return "not_established", None, f"frontier refused: {exc}", 0
    except Exception as exc:  # noqa: BLE001
        partial = getattr(exc, "frontier_result", None) or {}
        pts = list(partial.get("points") or [])
        logger.exception("EH frontier stage failed")
        return "not_established", {
            "points": pts, "base_restored": partial.get("base_restored"),
            "base_restore_status": partial.get("base_restore_status"),
            "excludes_shed_cost": True,
        }, f"frontier failed: {exc}", len(pts) + 1
    points = []
    period_basis = None
    for p in res["points"]:
        row = dict(p)
        row["excludes_shed_cost"] = True
        if row.get("period_basis") and period_basis is None:
            period_basis = row["period_basis"]
        points.append(row)
    ok_points = [p for p in points if p.get("status") == "ok" and p.get("point")]
    charged = len(points) + 1  # + the closing restore
    payload = {
        "targets_permyriad": targets,
        "points": points,
        "n_ok": len(ok_points),
        "knee_index": knee_index(res["points"], voll),
        "voll_eur_per_mwh": voll,
        "warning": res.get("warning"),
        "base_restored": res.get("base_restored"),
        "base_restore_status": res.get("base_restore_status"),
        "aborted": bool(res.get("aborted")),
        "period_basis": period_basis,
        "excludes_shed_cost": True,
        "engine": "lp_proxy",
    }
    if res.get("aborted"):
        return "not_established", payload, (
            f"frontier aborted after {len(points)} of {len(targets)} points"), charged
    if len(ok_points) < MIN_EH_FRONTIER_POINTS:
        return "not_established", payload, (
            f"only {len(ok_points)} of {len(targets)} frontier points solved "
            f"(need {MIN_EH_FRONTIER_POINTS})"), charged
    note = f"{len(ok_points)} points around {float(ens_cap_permyriad):g}‱"
    if res.get("base_restored") is False:
        note += (f"; closing restore did NOT bring the plan back "
                 f"({res.get('base_restore_status')})")
    return "ok", payload, note, charged


# ── WP3: fmea_top ─────────────────────────────────────────────────────────

def _flatten_mode(row: dict, *, rank: int) -> dict:
    fm = dict(row.get("failure_mode") or {})
    out = {"rank": rank, **fm}
    out["delta_eue_mwh"] = row.get("delta_eue_mwh")
    if "note" in row:
        out["note"] = row["note"]
    return out


def run_fmea_top_stage(
    network, lock, cfg, frozen: FixedPlanSnapshot, *,
    remaining_solves: int,
    stop_event,
    log_queue,
    final_state_update,
    top_n: int = FMEA_TOP_N,
) -> tuple[str, dict | None, str | None, int]:
    """
    Top-N ranked failure modes on the fixed plan → ``(status, payload,
    note, solves_charged)``.

    Class A comes from the frozen COPT screening (zero solves). Class B is
    the Link outage sweep on frozen capacities when the network has Links
    with occurrence data AND the remaining budget affords ``n + 2`` solves
    (base, one per Link, closing restore); otherwise the payload says which
    of those it lacked. Ranking is the worksheet's ``(-criticality, mode_id)``.
    """
    from services.adequacy.sweep import class_b_contingencies, run_class_b_sweep

    if frozen.copt_error and not frozen.copt_rows:
        return "not_established", None, frozen.copt_error, 0
    modes: list[dict] = []
    for r in frozen.copt_rows:
        if r.get("failure_mode"):
            modes.append({**r})
    classes = {"A"} if modes else set()

    class_b: dict[str, Any] = {"status": "skipped", "reason": None, "rows": 0,
                               "solves_charged": 0, "base_restored": None,
                               "base_restore_status": None}
    charged = 0
    try:
        contingencies = class_b_contingencies(network)
    except Exception as exc:  # noqa: BLE001
        contingencies = []
        class_b["reason"] = f"class_b_contingencies refused: {exc}"
    need = len(contingencies) + 2
    if not contingencies:
        class_b["reason"] = class_b["reason"] or (
            "no Link carries resolvable occurrence data (no Class-B contingency)")
    elif frozen.voll <= 0:
        class_b["reason"] = "Class-B sweep requires VOLL > 0"
    elif need > int(remaining_solves):
        class_b["reason"] = (
            f"budget: Class-B sweep of {len(contingencies)} Link(s) needs "
            f"{need} solves (base + links + restore), {remaining_solves} left")
    elif stop_event is not None and stop_event.is_set():
        class_b["reason"] = "aborted before the Class-B sweep"
    else:
        try:
            rows, restore = run_class_b_sweep(
                network, lock, cfg, log_queue=log_queue,
                final_state_update=final_state_update, stop_event=stop_event)
            solved = [r for r in rows if r.get("failure_mode")]
            # Base solve + one per contingency actually attempted + restore.
            attempted = len(rows)
            charged = attempted + 2
            class_b.update(
                status="aborted" if restore.get("aborted") else "run",
                rows=len(solved), solves_charged=charged,
                base_restored=restore.get("base_restored"),
                base_restore_status=restore.get("base_restore_status"),
                unsolved=[{"id": r["id"], "status": r.get("status")}
                          for r in rows if not r.get("failure_mode")],
            )
            for r in solved:
                modes.append({**r})
            if solved:
                classes.add("B")
        except Exception as exc:  # noqa: BLE001
            logger.exception("EH fmea_top Class-B sweep failed")
            class_b.update(status="failed", reason=f"Class-B sweep failed: {exc}")
    if not modes:
        return "not_established", {
            "top": [], "n_total_modes": 0, "classes_included": [],
            "class_b": class_b, "voll_eur_per_mwh": frozen.voll,
            "note": FMEA_TOP_LINK_PRIMARY_NOTE,
        }, (frozen.copt_error or "no failure mode could be ranked"), charged
    modes.sort(key=lambda r: (
        -float((r.get("failure_mode") or {}).get("criticality_eur_per_year", 0.0) or 0.0),
        str((r.get("failure_mode") or {}).get("mode_id", ""))))
    top = [_flatten_mode(r, rank=i + 1) for i, r in enumerate(modes[:top_n])]
    payload = {
        "top": top,
        "top_n": int(top_n),
        "n_total_modes": len(modes),
        "classes_included": sorted(classes),
        "class_b": class_b,
        "copt_metrics": frozen.copt_metrics,
        "voll_eur_per_mwh": frozen.voll,
        "ranking": "criticality_eur_per_year desc, mode_id",
        "note": FMEA_TOP_LINK_PRIMARY_NOTE,
    }
    note = FMEA_TOP_LINK_PRIMARY_NOTE + (
        f"; top {len(top)} of {len(modes)} modes, classes {'+'.join(sorted(classes))}")
    if class_b["status"] != "run" and class_b.get("reason"):
        note += f"; Class-B {class_b['status']}: {class_b['reason']}"
    return "ok", payload, note, charged
