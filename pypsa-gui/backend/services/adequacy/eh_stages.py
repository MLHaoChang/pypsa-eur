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

import dataclasses
import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from models.energy_hub import (
    DEFAULT_EH_FMEA_TOP_N,
    DEFAULT_EH_FRONTIER_LADDER,
    CertificationVerdict,
)

logger = logging.getLogger("pypsa_gui.eh_stages")

# Defaults for the pack fields ``frontier_ladder`` / ``fmea_top_n`` (owned by
# ``models.energy_hub``; the names are kept for callers and tests).
EH_FRONTIER_LADDER: tuple[float, ...] = DEFAULT_EH_FRONTIER_LADDER
FMEA_TOP_N = DEFAULT_EH_FMEA_TOP_N
#: Fewer points than this is not a curve; the stage skips rather than run two.
MIN_EH_FRONTIER_POINTS = 3

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
    # Which fleet the MC / COPT saw (see ``hub_fleet_scope``) — disclosed on
    # the certification and fmea_top payloads.
    scope: dict | None = None


# ── the hub's fleet, not the copper plate ─────────────────────────────────

@dataclass
class FleetScope:
    """
    The part of the network the hub's adequacy is certified on.

    The sequential MC and the COPT are single-area: every electrical bus is
    one copper plate. Left alone they count generators BEHIND the import
    Link — on an ``off_grid`` hub whose PoC Link the pack islanded, a
    grid-side unit the LP cannot reach still covered the hub's deficits and
    the verdict came back ``certified`` for a hub that sheds. ``hub_side``
    restricts the fleet and the demand to the hub's side of the identified
    import Link(s) and counts the import as a firm block up to the Link's
    planning cap (0 MW when islanded) — ``import_firmness=planning_limit_only``,
    never certified interconnector adequacy (spec §6). ``whole_network`` is
    the fallback when the sides cannot be told apart, with the reason.
    """

    mode: str
    note: str
    import_links: list[str] = field(default_factory=list)
    excluded_buses: list[str] = field(default_factory=list)
    excluded_units: list[str] = field(default_factory=list)
    import_firm_mw: Any = None  # np.ndarray (H,) or None

    def as_payload(self) -> dict:
        firm = self.import_firm_mw
        return {
            "mode": self.mode,
            "import_links": list(self.import_links),
            "excluded_buses": list(self.excluded_buses),
            "excluded_units": list(self.excluded_units),
            "import_firm_mw_max": (float(np.max(firm)) if firm is not None
                                   and len(firm) else None),
            "import_firmness": "planning_limit_only",
            "note": self.note,
        }


def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("true", "1", "yes")


def _branch_edges(n, skip_links: set[str]) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for comp in ("lines", "transformers", "links"):
        df = getattr(n, comp, None)
        if df is None or df.empty:
            continue
        bus_cols = [c for c in df.columns
                    if c.startswith("bus") and c[3:].isdigit()]
        for name in df.index:
            if comp == "links" and str(name) in skip_links:
                continue
            ends = [str(df.at[name, c]) for c in bus_cols
                    if str(df.at[name, c] or "").strip() not in ("", "nan")]
            for a, b in zip(ends, ends[1:]):
                edges.append((a, b))
    return edges


def _components(buses: list[str], edges) -> dict[str, int]:
    parent = {b: b for b in buses}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
    roots: dict[str, int] = {}
    return {b: roots.setdefault(find(b), len(roots)) for b in buses}


def _series(n, name: str, attr: str, default: float) -> np.ndarray:
    frame = getattr(getattr(n, "links_t", None), attr, None)
    if frame is not None and name in getattr(frame, "columns", []):
        return frame[name].reindex(n.snapshots).fillna(default).to_numpy(float)
    try:
        v = float(n.links.at[name, attr])
    except (KeyError, TypeError, ValueError):
        v = default
    if not math.isfinite(v):
        v = default
    return np.full(len(n.snapshots), v, dtype=float)


def hub_fleet_scope(n, overlay) -> FleetScope:
    """
    Split the network at the identified import Link(s) → ``FleetScope``.

    * Import Links come from the pack's spec-§6 selection. Only a Link
      identified by ``eh_role == grid_import`` or an ``eh_poc`` endpoint is
      trusted to BE the boundary — the carrier fallback can match Links
      inside the hub, so it leaves the whole network in scope.
    * The grid side of each Link is its ``bus0`` (PyPSA's positive direction:
      import flows bus0 → bus1), flipped when only bus0's side carries an
      ``eh_critical`` bus. ``eh_poc`` is NOT used for orientation: fixtures
      and the SCR gate tag the PoC on either side.
    * The Links must actually separate the two sides once removed; a
      parallel Line / Link means there is no hub boundary to certify on.
    """
    from services.adequacy.archetypes import select_import_links
    from services.adequacy.copt import solved_capacity

    links = select_import_links(n, overlay)
    if not links:
        return FleetScope(mode="whole_network", note=(
            "no import Link identified — the MC / COPT fleet is the whole "
            "network (single-area copper plate)"))
    ldf = n.links
    buses = [str(b) for b in n.buses.index]
    poc = {str(b) for b in buses
           if "eh_poc" in n.buses.columns and _truthy(n.buses.at[b, "eh_poc"])}
    crit = {str(b) for b in buses
            if "eh_critical" in n.buses.columns
            and _truthy(n.buses.at[b, "eh_critical"])}
    identified = [
        lk for lk in links
        if ("eh_role" in ldf.columns and str(ldf.at[lk, "eh_role"]) == "grid_import")
        or str(ldf.at[lk, "bus0"]) in poc or str(ldf.at[lk, "bus1"]) in poc]
    if len(identified) != len(links):
        return FleetScope(mode="whole_network", import_links=list(links), note=(
            "import Link(s) matched only by the carrier fallback — hub and grid "
            "sides are not identifiable, so the MC / COPT fleet is the whole "
            "network (single-area copper plate); tag the PoC Link "
            "eh_role=grid_import to certify on the hub side"))
    comp = _components(buses, _branch_edges(n, set(links)))
    by_comp: dict[int, set[str]] = {}
    for b, c in comp.items():
        by_comp.setdefault(c, set()).add(b)

    grid_comps: set[int] = set()
    hub_comps: set[int] = set()
    orient: dict[str, tuple[str, str]] = {}
    for lk in links:
        b0, b1 = str(ldf.at[lk, "bus0"]), str(ldf.at[lk, "bus1"])
        g, h = b0, b1
        if (by_comp[comp[b0]] & crit) and not (by_comp[comp[b1]] & crit):
            g, h = b1, b0
        orient[lk] = (g, h)
        grid_comps.add(comp[g])
        hub_comps.add(comp[h])
    if grid_comps & hub_comps:
        return FleetScope(mode="whole_network", import_links=list(links), note=(
            f"import Link(s) {', '.join(links)} do not separate the hub from "
            "the grid (another branch connects them) — the MC / COPT fleet is "
            "the whole network (single-area copper plate)"))

    excluded = sorted(b for b, c in comp.items() if c in grid_comps)
    ex_set = set(excluded)
    units = []
    for cname in ("generators", "storage_units"):
        df = getattr(n, cname, None)
        if df is not None and not df.empty:
            units += [str(u) for u in df.index if str(df.at[u, "bus"]) in ex_set]

    firm = np.zeros(len(n.snapshots), dtype=float)
    for lk in links:
        g, h = orient[lk]
        cap = solved_capacity(ldf.loc[lk])
        if h == str(ldf.at[lk, "bus1"]):
            eff = _series(n, lk, "efficiency", 1.0)
            firm += cap * np.clip(_series(n, lk, "p_max_pu", 1.0), 0.0, None) * eff
        else:
            firm += cap * np.clip(-_series(n, lk, "p_min_pu", 0.0), 0.0, None)
    note = (
        f"MC / COPT fleet restricted to the hub side of import Link(s) "
        f"{', '.join(links)}: {len(excluded)} grid-side bus(es) and "
        f"{len(units)} unit(s) excluded; the import is counted as a firm block "
        f"up to its planning cap (peak {float(firm.max()) if len(firm) else 0.0:.4g} MW, "
        "import_firmness=planning_limit_only — Link outages are ranked in "
        "fmea_top, not sampled here)")
    return FleetScope(mode="hub_side", note=note, import_links=list(links),
                      excluded_buses=excluded, excluded_units=sorted(units),
                      import_firm_mw=firm)


def _hub_side_copy(network, excluded_buses: list[str]):
    """A copy of the solved network with the grid side removed."""
    from services.adequacy.redundancy import _detach_solver_model

    _detach_solver_model(network)
    nn = network.copy()
    ex = set(excluded_buses)
    for cls, attr in (("Generator", "generators"), ("Load", "loads"),
                      ("StorageUnit", "storage_units"), ("Store", "stores"),
                      ("ShuntImpedance", "shunt_impedances")):
        df = getattr(nn, attr, None)
        if df is not None and not df.empty and "bus" in df.columns:
            names = [i for i in df.index if str(df.at[i, "bus"]) in ex]
            if names:
                nn.remove(cls, names)
    for cls, attr in (("Line", "lines"), ("Transformer", "transformers"),
                      ("Link", "links")):
        df = getattr(nn, attr, None)
        if df is None or df.empty:
            continue
        cols = [c for c in df.columns if c.startswith("bus") and c[3:].isdigit()]
        names = [i for i in df.index
                 if any(str(df.at[i, c]) in ex for c in cols)]
        if names:
            nn.remove(cls, names)
    nn.remove("Bus", [b for b in nn.buses.index if str(b) in ex])
    return nn


def freeze_fixed_plan(network, cfg, lock, *, overlay=None) -> FixedPlanSnapshot:
    """
    Snapshot the MC inputs and screen the fleet, ONCE, under ``lock``.

    ``snapshot_inputs`` walks ``fleet_and_residual`` itself, so the two halves
    share membership by construction (the MC module's invariant); the COPT
    screening below reuses the very same units and residual rather than
    walking the network a second time.

    With ``overlay`` (the pack's import overlay) the fleet and the demand are
    those of the HUB side of the import Link(s) and the import is a firm
    block up to its cap (``hub_fleet_scope``); without it, the whole network.
    """
    from services.adequacy.copt import screening_analysis
    from services.adequacy.mc import snapshot_inputs

    snap = FixedPlanSnapshot(voll=float(getattr(cfg, "voll", 0.0) or 0.0))
    with lock:
        try:
            scope = (hub_fleet_scope(network, overlay) if overlay is not None
                     else FleetScope(mode="whole_network",
                                     note="no import overlay given"))
            if scope.mode == "hub_side":
                inputs = snapshot_inputs(
                    _hub_side_copy(network, scope.excluded_buses), cfg=cfg)
                firm = np.asarray(scope.import_firm_mw, dtype=np.float64)
                inputs = dataclasses.replace(
                    inputs, residual=np.ascontiguousarray(inputs.residual - firm))
            else:
                inputs = snapshot_inputs(network, cfg=cfg)
        except Exception as exc:  # noqa: BLE001 — the reason is the payload
            snap.mc_error = f"MC snapshot refused: {exc}"
            snap.copt_error = snap.mc_error
            return snap
    snap.scope = scope.as_payload()
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
            "fleet_scope": frozen.scope,
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
        "fleet_scope": frozen.scope,
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
                         remaining_solves: int, *,
                         ladder: tuple[float, ...] = EH_FRONTIER_LADDER,
                         ) -> tuple[list[float], str | None]:
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
    factors = sorted({float(f) for f in ladder}, reverse=True)
    if len(factors) < MIN_EH_FRONTIER_POINTS:
        return [], (
            f"frontier_ladder has {len(factors)} factor(s); a curve needs at "
            f"least {MIN_EH_FRONTIER_POINTS}")
    if affordable < len(factors):
        # Trim from the outside in: keep the factors nearest ×1 (the report's
        # own target) on a log scale, ties broken towards the looser side.
        nearest = sorted(factors, key=lambda f: (abs(math.log(f)), -f))
        factors = sorted(nearest[:affordable], reverse=True)
    return [float(ens_cap_permyriad) * f for f in factors], None


def run_frontier_stage(
    network, lock, cfg, *,
    ens_cap_permyriad: float | None,
    remaining_solves: int,
    stop_event,
    log_queue,
    final_state_update,
    ladder: tuple[float, ...] = EH_FRONTIER_LADDER,
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

    targets, why = frontier_targets_for(ens_cap_permyriad, remaining_solves,
                                        ladder=ladder)
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
        "fleet_scope": frozen.scope,
        "voll_eur_per_mwh": frozen.voll,
        "ranking": "criticality_eur_per_year desc, mode_id",
        "note": FMEA_TOP_LINK_PRIMARY_NOTE,
    }
    note = FMEA_TOP_LINK_PRIMARY_NOTE + (
        f"; top {len(top)} of {len(modes)} modes, classes {'+'.join(sorted(classes))}")
    if class_b["status"] != "run" and class_b.get("reason"):
        note += f"; Class-B {class_b['status']}: {class_b['reason']}"
    return "ok", payload, note, charged
