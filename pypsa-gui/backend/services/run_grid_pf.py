"""Study harness 15 (design §9.3 "Grid-only"; GK A, ACPF-BUG): what a grid-only
run's result rules read about the AC power flow.

Every reading comes from the `ac_pf_results` snapshot the AC PF stage stored
(the engine's own PF label), never from the live `lines_t` frames: after a
second PF those hold PF values under an LOPF name. With no snapshot, a stale
one or an unknown convergence the reading is no evidence, so an LOPF flow
never satisfies a PF rule. No limit is assumed: a branch without a rating or a
bus without a voltage band is named, not defaulted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

NO_PF = (
    "no_ac_pf",
    "the run has no AC power-flow result: switch Load flow on and re-run "
    "(LOPF flows never stand in for it)",
)
# The solve job's condition when the LP was optimal and its AC power flow raised
# (`solver_service`); `run_gates.lp_condition` reads it as optimal.
AC_PF_FAILED = "optimal; ac_pf_failed: "
BRANCHES = (
    ("Line", "lines", "lines_t"),
    ("Transformer", "transformers", "transformers_t"),
)


@dataclass(frozen=True)
class Evidence:
    """A usable PF reading: the converged snapshot mask and the PF frames."""

    converged: np.ndarray
    unknown: int
    failed: int
    frames: dict


@dataclass(frozen=True)
class Missing:
    """No evidence: the code and the words the rule shows."""

    code: str
    reason: str


def _cfg(state, name, default=None):
    cfg = state.get("solver_config")
    return (
        cfg.get(name, default) if isinstance(cfg, dict) else getattr(cfg, name, default)
    )


def read(ctx) -> Evidence | Missing:
    """The PF snapshot of the run's own solve, or why there is none."""
    state, n = ctx.solver_state, ctx.network
    snap = state.get("ac_pf_results")
    if not isinstance(snap, dict) or not snap:
        condition = state.get("condition")
        if isinstance(condition, str) and condition.startswith(AC_PF_FAILED):
            # The flow ran and failed (GK B SH-15 (1)): convergence fails on it, and
            # every other PF reading is not evaluated with the failure's own words.
            return Missing(
                "ac_pf_failed",
                f"the AC power flow failed: {condition[len(AC_PF_FAILED) :]}",
            )
        return Missing(*NO_PF)
    if state.get("status") != "completed":
        return Missing(
            "solve_not_completed",
            "the solve did not complete, so its AC power flow is not a result",
        )
    if state.get("ac_pf_network_revision") != int(
        getattr(ctx, "network_revision", 0) or 0
    ):
        return Missing(
            "stale_ac_pf", "the inputs changed after the AC power flow: re-run it"
        )
    rows = state.get("ac_pf_convergence_list")
    if (
        not isinstance(rows, list)
        or len(rows) != len(n.snapshots)
        or not all(isinstance(r, dict) for r in rows)
    ):
        return Missing(
            "convergence_unaligned",
            "the AC power flow's convergence is not reported per snapshot",
        )
    ok = np.array([r.get("ok") is True for r in rows])
    unknown = sum(1 for r in rows if r.get("ok") is None)
    failed = sum(1 for r in rows if r.get("ok") is False)
    return Evidence(ok, unknown, failed, snap)


def converged(ev) -> Any:
    """True when every snapshot converged; False on a reported failure; no
    evidence while any snapshot's convergence is unknown (AA-12)."""
    if isinstance(ev, Missing):
        return False if ev.code == "ac_pf_failed" else ev
    if ev.failed:
        return False
    if ev.unknown:
        return Missing(
            "convergence_unknown",
            f"{ev.unknown} snapshot(s) did not report convergence",
        )
    return True


def _frame(ev, key, index):
    df = ev.frames.get(key)
    if df is None or getattr(df, "empty", True):
        return None
    try:
        return df.reindex(index=index).astype(float)
    except Exception:  # noqa: BLE001 — a malformed frame is no evidence
        return None


def loading(ctx, ev) -> Any:
    """Peak apparent-power loading of every line and transformer on the
    converged snapshots: the larger apparent power at either end against the MVA
    rating x that snapshot's s_max_pu, with the branches over 100 %. A
    current-based rating at depressed voltage would read higher."""
    if isinstance(ev, Missing):
        return ev
    n = ctx.network
    if not ev.converged.any():
        return Missing(
            "no_converged_snapshot", "no snapshot converged, so no loading is certified"
        )
    index = n.snapshots[ev.converged]
    peak, over, unrated, branches = 0.0, [], [], 0
    for cls, comp, t in BRANCHES:
        static = getattr(n, comp)
        if static is None or static.empty:
            continue
        # The per-snapshot limit (GK A F2): a time-varying s_max_pu below the
        # static one would otherwise understate the loading.
        try:
            smax_t = n.get_switchable_as_dense(cls, "s_max_pu").reindex(index=index).astype(float)
        except Exception:  # noqa: BLE001 — an unreadable limit rates no branch
            smax_t = pd.DataFrame(index=index)
        p0, q0 = _frame(ev, f"{t}.p0", index), _frame(ev, f"{t}.q0", index)
        p1, q1 = _frame(ev, f"{t}.p1", index), _frame(ev, f"{t}.q1", index)
        if p0 is None or q0 is None:
            return Missing(
                "no_reactive_flows",
                f"the AC power flow left no {cls.lower()} P/Q flows to load against",
            )
        for name in static.index:
            row = static.loc[name]
            field = "s_nom_opt" if bool(row.get("s_nom_extendable", False)) else "s_nom"
            rating = pd.to_numeric(row.get(field), errors="coerce")
            smax = (
                smax_t[name].to_numpy()
                if name in smax_t.columns
                else np.array([np.nan])
            )
            if not (
                math.isfinite(rating)
                and rating > 1e-9
                and np.isfinite(smax).all()
                and (smax > 0).all()
            ):
                unrated.append(str(name))
                continue
            if name not in p0.columns or name not in q0.columns:
                return Missing("no_flow", f"{cls} {name} has no AC power-flow flow")
            s = np.hypot(p0[name].to_numpy(), q0[name].to_numpy())
            if (
                p1 is not None
                and q1 is not None
                and name in p1.columns
                and name in q1.columns
            ):
                s = np.fmax(s, np.hypot(p1[name].to_numpy(), q1[name].to_numpy()))
            if not np.isfinite(s).all():
                return Missing(
                    "flow_not_finite", f"{cls} {name}'s AC flow is not finite"
                )
            util = float((s / (rating * smax)).max()) * 100.0
            branches += 1
            peak = max(peak, util)
            if util > 100.0:
                over.append(f"{cls} {name} {util:.1f} %")
    if unrated:
        return Missing(
            "branch_unrated",
            f"no rating to judge {', '.join(unrated[:5])} against; none is assumed",
        )
    if not branches:
        return Missing(
            "no_ac_branches", "the network has no line or transformer to load"
        )
    return peak, over


def voltage(ctx, ev) -> Any:
    """Bus-snapshots outside the buses' own v_mag_pu_min/max on converged
    snapshots; no evidence when no bus states a band."""
    if isinstance(ev, Missing):
        return ev
    n = ctx.network
    buses = n.buses
    lo = pd.to_numeric(
        buses.get("v_mag_pu_min", pd.Series(0.0, index=buses.index)), errors="coerce"
    )
    hi = pd.to_numeric(
        buses.get("v_mag_pu_max", pd.Series(np.inf, index=buses.index)), errors="coerce"
    )
    banded = [b for b in buses.index if (lo[b] > 0) or math.isfinite(hi[b])]
    if not banded:
        return Missing(
            "no_voltage_band",
            "no bus states a voltage band (v_mag_pu_min/max); none is assumed",
        )
    if not ev.converged.any():
        return Missing(
            "no_converged_snapshot", "no snapshot converged, so no voltage is certified"
        )
    v = _frame(ev, "buses_t.v_mag_pu", n.snapshots[ev.converged])
    if v is None or any(b not in v.columns for b in banded):
        return Missing("no_voltage", "the AC power flow left no bus voltages")
    # A non-finite voltage on a banded bus compares False both ways: no evidence (GK A F1).
    for b in banded:
        if not np.isfinite(v[b].to_numpy()).all():
            return Missing("voltage_not_finite", f"bus {b}'s AC power-flow voltage is not finite")
    out, names = 0, []
    for b in banded:
        bad = int(((v[b] < lo[b] - 1e-9) | (v[b] > hi[b] + 1e-9)).sum())
        if bad:
            out += bad
            names.append(f"{b} ({bad} snapshot(s))")
    # The rule claims only the buses it read (GK A F4).
    unbanded = [str(b) for b in buses.index if b not in banded]
    if unbanded:
        more = f" and {len(unbanded) - 5} more" if len(unbanded) > 5 else ""
        names.append(f"not judged: no band on {', '.join(unbanded[:5])}{more}")
    return out, names


def n1(ctx) -> Any:
    """True when the solve enforced N-1 on every line and every transformer the
    network has (the solver config's security-constrained option on, each in the
    contingency set)."""
    state = ctx.solver_state
    if not _cfg(state, "sclopf", False):
        return Missing("n1_off", "the solver's N-1 security option is off")
    condition = state.get("condition")
    # N-1 is the LP's own property: a failed flow after it leaves it judged (GK B SH-15 (1)).
    if state.get("status") != "completed" or not (
        condition in ("optimal", "lopf+ac_pf_ok")
        or (isinstance(condition, str) and condition.startswith(AC_PF_FAILED))
    ):
        return Missing(
            "n1_no_solve", "the security-constrained solve did not finish optimal"
        )
    # The flags above are read from the editable config: they are the solve's own
    # only while the live inputs, that config included, are still the ones the
    # solve recorded (GK A F3). A config flipped after the solve is no evidence.
    from harness.calculations import solve_matches_inputs

    if not solve_matches_inputs(ctx, after_pf=True):
        return Missing(
            "n1_config_changed",
            "the inputs or the solver config changed since the solve, so its N-1 setting is not known",
        )
    n = ctx.network
    for flag, frame, name in (
        ("sclopf_include_all_lines", n.lines, "line"),
        ("sclopf_include_all_transformers", n.transformers, "transformer"),
    ):
        if len(frame) and not _cfg(state, flag, False):
            return Missing(
                "n1_partial",
                f"only the listed {name}s are in the contingency set, not every {name}",
            )
    return True
