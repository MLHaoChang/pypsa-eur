"""
Capacity Outage Probability Table — the analytic screening adequacy engine.

Design: spec §§3.1, 3.3, 5.3; plan 2026-08-28-fmea-phase2-copt.md. This is
the classical Billinton–Allan construction with the review's corrections
baked in:

* **Thermal-only, storage-excluded, network-free.** StorageUnits, Stores,
  Links and imports never enter — a SCREENING number
  (fidelity="analytic_convolution"), never comparable to a statutory
  standard. Its divergence from the storage-aware LP proxy is the product:
  a large gap means storage/network carry the adequacy, which is exactly
  when this classical number misleads (spec §5.3).
* **The residual load is exogenous.** It nets ONLY must-take generation at
  its given hourly availability (profiles) — never LP dispatch decisions,
  which was v1's circularity error (spec §3.1).
* **Fleet membership is data-driven:** an electrical, non-slack generator
  with resolvable occurrence params is a two-state COPT unit at its firm
  capacity; one without is must-take, netted at ``p_max_pu × capacity``.
  VRE therefore nets via its hourly profiles — variance captured
  hour-by-hour; its mechanical FOR stays excluded, consistent with
  ``occurrence.py``'s no-VRE-defaults decision.
* **Rounding increment Δ** (``delta_mw``): the table is O(N·C/Δ), not 2^N.
  Capacity is apportioned PROBABILISTICALLY to the two adjacent rounded
  states so the distribution's mean is exact — plain rounding drifts the
  table (spec §5.3's rounding-bias note).

The leave-one-out attribution (Task 2) lives here too: deconvolve unit i
out of the table, convolve back a DETERMINISTIC capacity of the same size —
ΔEUE then prices the unit's OUTAGES over the full multi-outage state space
(N-2 and beyond), which is what a single-contingency LP sweep structurally
misses (spec §3.3). Zero LP solves anywhere in this module.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CoptUnit:
    name: str
    capacity_mw: float
    q: float                    # unavailability (FOR/EFORd as labelled)
    # Carried through for the FMECA rows; optional for the pure math.
    basis: str = ""
    mttr_hours: float = float("nan")
    source: str = ""


class CapacityDistribution:
    """P[available capacity = k·Δ] as a dense array over k = 0..K."""

    def __init__(self, probs: np.ndarray, delta_mw: float):
        self.probs = probs
        self.delta_mw = float(delta_mw)
        # Survival S[k] = P[available ≥ k·Δ]; S[0] = 1.
        self._surv = np.concatenate(
            [np.cumsum(probs[::-1])[::-1], [0.0]])

    @property
    def total_probability(self) -> float:
        return float(self.probs.sum())

    def probability_of(self, capacity_mw: float) -> float:
        k = int(round(capacity_mw / self.delta_mw))
        if k < 0 or k >= len(self.probs):
            return 0.0
        return float(self.probs[k])

    def mean(self) -> float:
        return float((self.probs * np.arange(len(self.probs))).sum() * self.delta_mw)

    def survival(self, x_mw: float) -> float:
        """P[available ≥ x]. x ≤ 0 → 1."""
        if x_mw <= 0:
            return 1.0
        k = int(math.ceil(x_mw / self.delta_mw - 1e-12))
        if k >= len(self._surv):
            return 0.0
        return float(self._surv[k])

    def expected_shortfall(self, load_mw: float) -> float:
        """E[max(load − available, 0)] in MWh per hour of exposure.
        = Σ_{k·Δ < load} p_k · (load − k·Δ)."""
        if load_mw <= 0:
            return 0.0
        k_max = int(math.ceil(load_mw / self.delta_mw - 1e-12))
        ks = np.arange(min(k_max, len(self.probs)))
        return float((self.probs[ks] * (load_mw - ks * self.delta_mw)).sum())


def _unit_states(capacity_mw: float, q: float, delta_mw: float) -> list[tuple[int, float]]:
    """Two-state unit → [(state_index, prob)], the up-state apportioned
    between the adjacent grid points so the mean is exact."""
    lo = int(math.floor(capacity_mw / delta_mw))
    frac = capacity_mw / delta_mw - lo
    p_up = 1.0 - q
    states = [(0, q)]
    if frac < 1e-12:
        states.append((lo, p_up))
    else:
        states.append((lo, p_up * (1.0 - frac)))
        states.append((lo + 1, p_up * frac))
    return states


def build_copt(units: list[CoptUnit], delta_mw: float = 1.0) -> CapacityDistribution:
    total_k = 1 + sum(int(math.ceil(u.capacity_mw / delta_mw)) + 1 for u in units)
    probs = np.zeros(max(total_k, 1))
    probs[0] = 1.0
    size = 1
    for u in units:
        nxt = np.zeros_like(probs)
        for k, p in _unit_states(u.capacity_mw, u.q, delta_mw):
            if p <= 0:
                continue
            nxt[k:k + size] += p * probs[:size]
        probs = nxt
        size = min(len(probs), size + int(math.ceil(u.capacity_mw / delta_mw)) + 1)
    return CapacityDistribution(probs[:size], delta_mw)


def hourly_adequacy(dist: CapacityDistribution, residual_load: pd.Series,
                    *, weights: pd.Series) -> dict:
    """
    Screening LOLP/LOLE/EUE over an EXOGENOUS residual-load series (MW),
    weighted by the snapshot weights. Hours with residual ≤ 0 contribute
    nothing. Per-period split mirrors the other adequacy surfaces
    (MultiIndex level 0, else "ALL").
    """
    w = weights.reindex(residual_load.index).fillna(0.0)
    lolp = residual_load.map(lambda x: 1.0 - dist.survival(float(x)))
    eue_h = residual_load.map(lambda x: dist.expected_shortfall(float(x)))
    lole_t = lolp * w
    eue_t = eue_h * w
    if isinstance(residual_load.index, pd.MultiIndex):
        lvl = residual_load.index.get_level_values(0)
        by_period = {
            int(p): {"lole_hours": float(lole_t[lvl == p].sum()),
                     "eue_mwh": float(eue_t[lvl == p].sum())}
            for p in sorted(set(lvl))
        }
    else:
        by_period = {"ALL": {"lole_hours": float(lole_t.sum()),
                             "eue_mwh": float(eue_t.sum())}}
    return {
        "lole_hours": float(lole_t.sum()),
        "eue_mwh": float(eue_t.sum()),
        "lolp_max": float(lolp.max()) if len(lolp) else 0.0,
        "by_period": by_period,
    }


def _firm_capacity(gens: pd.DataFrame, g) -> float:
    """Firm capacity: the solved size when a fresh solve exists, else p_nom."""
    for col in ("p_nom_opt", "p_nom"):
        if col in gens.columns:
            try:
                v = float(gens.at[g, col])
                if math.isfinite(v) and v > 0:
                    return v
            except (TypeError, ValueError):
                continue
    return 0.0


def _membership_walk(n, elec_buses):
    """
    THE generator-membership decision, in one place (spec §3.1).

    Yields ``(label, capacity_mw, occurrence_row)`` for every generator that
    clears the scope tests — non-slack, at an electrical bus, positive firm
    capacity — in ``n.generators`` order. The branch each caller then takes is
    the SAME one-line test on the resolved occurrence row:
    ``row["source"] != "missing"`` is an occurrence-bearing COPT/MC unit,
    ``== "missing"`` is must-take (netted into the residual at its hourly
    availability).

    Extracted rather than duplicated because two consumers now depend on the
    answer and they must agree BY CONSTRUCTION: ``fleet_and_residual`` decides
    who gets sampled, and the ELCC candidate enumeration decides who the UI may
    ask for a capacity credit. A generator the enumeration called a unit and
    this loop called must-take is not a cosmetic disagreement — it is a 404 on
    a name the picker itself offered.
    """
    from services.adequacy.occurrence import resolve_outage_params
    from services.adequacy.slack import slack_generator_mask

    gens = getattr(n, "generators", None)
    if gens is None or gens.empty:
        return
    slack = slack_generator_mask(gens)
    params = resolve_outage_params(n, "generators")
    for g in gens.index:
        if bool(slack.get(g, False)):
            continue
        if str(gens.at[g, "bus"]) not in elec_buses:
            continue
        cap = _firm_capacity(gens, g)
        if cap <= 0:
            continue
        yield g, cap, params.loc[g]


def must_take_generators(n) -> list[str]:
    """
    The names of the MUST-TAKE generators — the exact complement of the unit
    branch in ``fleet_and_residual``'s membership loop, taken from the same
    walk rather than re-derived (see ``_membership_walk``).

    These are the ``kind="vre"`` ELCC candidates: their output was netted into
    the residual, so pricing them means un-netting the profile, which is
    exactly what ``elcc._resolve`` does with the profiles
    ``mc.snapshot_inputs(n, vre_assets=…)`` preserves.
    """
    from services.adequacy.metrics import electrical_columns

    buses = getattr(n, "buses", None)
    if buses is None:
        return []
    elec_buses = set(electrical_columns(n, list(buses.index)))
    return [str(g) for g, _cap, row in _membership_walk(n, elec_buses)
            if row["source"] == "missing"]


def fleet_and_residual(n) -> tuple[list[CoptUnit], pd.Series, pd.Series]:
    """
    Apply the membership rule to a network: returns (COPT units, residual
    electrical load MW per snapshot, energy weights).

    Electrical scope and slack exclusion use the same classifiers as the
    ENS cap; occurrence resolution the same fallback chain as preflight —
    the engine and the target can never disagree on who counts.
    """
    from services import period_utils as _period_utils
    from services.adequacy.metrics import electrical_columns

    gens = n.generators
    buses = n.buses
    loads = n.loads
    snapshots = n.snapshots
    w = _period_utils.snapshot_weights(n, "generators", sns=snapshots)

    elec_buses = set(electrical_columns(n, list(buses.index)))

    p_max_pu_t = getattr(getattr(n, "generators_t", None), "p_max_pu", None)

    units: list[CoptUnit] = []
    must_take = pd.Series(0.0, index=snapshots)
    for g, cap, row in _membership_walk(n, elec_buses):
        if row["source"] != "missing":
            units.append(CoptUnit(
                name=str(g), capacity_mw=cap, q=float(row["rate"]),
                basis=str(row["basis"]), mttr_hours=float(row["mttr_hours"]),
                source=str(row["source"]),
            ))
        else:
            # Must-take: available output at its given hourly availability.
            if p_max_pu_t is not None and g in getattr(p_max_pu_t, "columns", []):
                avail = p_max_pu_t[g].reindex(snapshots).fillna(0.0) * cap
            else:
                try:
                    static = float(gens.at[g, "p_max_pu"]) if "p_max_pu" in gens.columns else 1.0
                except (TypeError, ValueError):
                    static = 1.0
                avail = pd.Series(static * cap, index=snapshots)
            must_take = must_take.add(avail, fill_value=0.0)

    demand = pd.Series(0.0, index=snapshots)
    if loads is not None and not loads.empty and "bus" in loads.columns:
        p_set_t = getattr(getattr(n, "loads_t", None), "p_set", None)
        for l in loads.index:
            if str(loads.at[l, "bus"]) not in elec_buses:
                continue
            if p_set_t is not None and l in getattr(p_set_t, "columns", []):
                demand = demand.add(p_set_t[l].reindex(snapshots).fillna(0.0),
                                    fill_value=0.0)
            else:
                try:
                    demand = demand + float(loads.at[l, "p_set"] or 0.0)
                except (TypeError, ValueError):
                    continue

    residual = demand - must_take
    return units, residual, w


def deconvolve(dist: CapacityDistribution, *, capacity_mw: float,
               q: float) -> CapacityDistribution:
    """
    Remove one two-state unit from the table: solve f = conv(g, unit) for g
    via the stable forward recursion over the unit's apportioned states
    ordered by index (state 0 = the outage, weight q):

        g(c) = ( f(c) − Σ_{k>0} p_k · g(c − k) ) / p_0-complement structure

    Concretely with states [(0, q), (k1, p1), (k2, p2)] the recursion is
        g(c) = ( f(c) − p1·g(c−k1) − p2·g(c−k2) ) / q        …when q > 0

    which is numerically stable for q < 0.5 (the usual regime — FORs are a
    few percent). When q is 0, ~1, or the recursion loses mass, the caller
    should rebuild without the unit instead (attribute_criticality does).
    Raises ValueError when the recursion is unusable.
    """
    states = _unit_states(capacity_mw, q, dist.delta_mw)
    # Recursion divides by the LOWEST state's probability — for a two-state
    # unit that is the outage state (index 0, prob q).
    base_k, base_p = states[0]
    assert base_k == 0
    if base_p < 1e-9 or base_p > 0.5:
        raise ValueError("deconvolution unstable for this q — rebuild instead")
    others = states[1:]
    f = dist.probs
    g = np.zeros_like(f)
    for c in range(len(f)):
        acc = f[c]
        for k, p in others:
            if c - k >= 0:
                acc -= p * g[c - k]
        g[c] = acc / base_p
    # Trim to the reduced fleet's support and guard against lost mass.
    total = float(g.sum())
    if not (0.999 <= total <= 1.001) or (g < -1e-6).any():
        raise ValueError("deconvolution lost probability mass — rebuild instead")
    g = np.clip(g, 0.0, None)
    return CapacityDistribution(g, dist.delta_mw)


def _shift_deterministic(dist: CapacityDistribution, capacity_mw: float) -> CapacityDistribution:
    """Convolve in a PERFECTLY AVAILABLE unit (q=0) of the given size —
    i.e. shift the distribution up, mean-preserving apportioning included."""
    states = _unit_states(capacity_mw, 0.0, dist.delta_mw)
    size = len(dist.probs) + max(k for k, _ in states) + 1
    out = np.zeros(size)
    for k, p in states:
        if p > 0:
            out[k:k + len(dist.probs)] += p * dist.probs
    return CapacityDistribution(out, dist.delta_mw)


def attribute_criticality(units: list[CoptUnit], dist: CapacityDistribution,
                          residual_load: pd.Series, *, weights: pd.Series,
                          voll: float) -> list[dict]:
    """
    Leave-one-out outage attribution (spec §3.3): for each unit i,

        ΔEUE_i = EUE(fleet as-is) − EUE(fleet with unit i PERFECTLY available)

    computed by deconvolving i out and convolving back a deterministic
    capacity of the same size. This prices the unit's OUTAGES over the full
    multi-outage state space — N-2 and beyond — which a single-contingency
    LP sweep structurally misses (its ΔEUE is zero whenever the rest of the
    fleet covers any single loss). Zero LP solves.

    Returns rows sorted by criticality, each carrying a contract-ready
    ``failure_mode`` dict (FailureModeResult shape: engine="copt",
    fidelity="analytic_convolution", class A). With voll ≤ 0 the € fields
    are 0 and ΔEUE remains the ranking.
    """
    base = hourly_adequacy(dist, residual_load, weights=weights)
    base_eue = base["eue_mwh"]
    rows: list[dict] = []
    for u in units:
        try:
            without = deconvolve(dist, capacity_mw=u.capacity_mw, q=u.q)
        except ValueError:
            without = build_copt([v for v in units if v.name != u.name],
                                 delta_mw=dist.delta_mw)
        perfect = _shift_deterministic(without, u.capacity_mw)
        eue_perfect = hourly_adequacy(perfect, residual_load, weights=weights)["eue_mwh"]
        delta_eue = max(base_eue - eue_perfect, 0.0)
        crit_eur = delta_eue * max(float(voll), 0.0)
        occ = (8760.0 * u.q / u.mttr_hours
               if math.isfinite(u.mttr_hours) and u.mttr_hours > 0 else 0.0)
        severity = crit_eur / occ if occ > 0 else 0.0
        rows.append({
            "name": u.name,
            "delta_eue_mwh": delta_eue,
            "criticality_eur_per_year": crit_eur,
            "failure_mode": {
                "mode_id": f"generator:{u.name}:forced_outage",
                "component_class": "Generator",
                "name": u.name,
                "failure_class": "A",
                "occurrence_per_year": occ,
                "occurrence_basis": u.basis or "FOR",
                "severity_eur": severity,
                "criticality_eur_per_year": crit_eur,
                "in_metric_scope": True,
                "engine": "copt",
                "fidelity": "analytic_convolution",
            },
        })
    rows.sort(key=lambda r: r["delta_eue_mwh"], reverse=True)
    return rows
