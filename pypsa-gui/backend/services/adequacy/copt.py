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
* **A unit with BOTH an availability series and outage data** (Phase
  12c-pre) carries the series as ``CoptUnit.profile``: UP is the series'
  value that hour, DOWN is zero. The table is built over the units without
  a profile and the profiled units are MIXED exactly per hour over their
  ``2^k`` outage states (``hourly_adequacy``), up to ``K_EXACT`` of them;
  any beyond are netted at expected output and disclosed. Netting at the
  expectation was measured to understate LOLE 3× and a unit's criticality
  14× (LOLP is convex in the shortfall), which is why the mixture is the
  rule and netting the disclosed exception. The series is attached
  whenever it is INFORMATIVE — not identically 1 — so a constant series
  at 0.8 is honoured at 0.8·cap, exactly as the reserve margin credits it.
  A static ``p_max_pu < 1`` is still NOT applied (see
  ``validation_service.static_p_max_pu_not_applied``).
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

import itertools
import math
from dataclasses import dataclass, field

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
    # Availability fraction per modelled hour, ``(H,)``, or None for a plain
    # two-state unit. Excluded from equality and hashing: an ndarray has no
    # scalar truth value, and the identity of a unit is its name and numbers.
    profile: np.ndarray | None = field(default=None, compare=False,
                                       hash=False, repr=False)


#: Exact per-hour mixture for up to this many profiled units (``2^K`` states
#: per hour, vectorised over H); beyond it the smallest-mean units are netted
#: at expected output and the payload says which (plan 12c-pre v2 §1.2).
K_EXACT = 8


def _availability_mw(u: CoptUnit, H: int) -> np.ndarray:  # noqa: N803
    """``a_{i,h} = profile_i(h) × cap_i`` as a float64 ``(H,)`` array. A unit
    without a profile is available at ``cap`` every hour."""
    if u.profile is None:
        return np.full(int(H), float(u.capacity_mw), dtype=np.float64)
    prof = np.asarray(u.profile, dtype=np.float64)
    if prof.shape != (int(H),):
        raise ValueError(
            f"unit {u.name!r}: profile has shape {prof.shape}, expected ({H},)")
    return prof * float(u.capacity_mw)


def series_is_informative(values) -> bool:
    """True when a ``p_max_pu`` column says something — it is not
    identically 1 over its finite values (``|v − 1| > 1e-9`` anywhere).
    A column of ones is the default on every generator and carries no
    information; a column at a constant 0.8 does, and so does a varying one.
    All-NaN or empty: nothing to attach."""
    try:
        arr = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError):
        return False
    fin = arr[np.isfinite(arr)]
    if fin.size == 0:
        return False
    return bool((np.abs(fin - 1.0) > 1e-9).any())


class CapacityDistribution:
    """P[available capacity = k·Δ] as a dense array over k = 0..K."""

    def __init__(self, probs: np.ndarray, delta_mw: float):
        self.probs = probs
        self.delta_mw = float(delta_mw)
        # Survival S[k] = P[available ≥ k·Δ]; S[0] = 1.
        self._surv = np.concatenate(
            [np.cumsum(probs[::-1])[::-1], [0.0]])
        # Prefix tables for the vectorised expected shortfall:
        #   ES(x) = Σ_{k<j} p_k·(x − kΔ) = x·F[j] − Δ·G[j],  j = #states below x
        # with F[j] = Σ_{k<j} p_k and G[j] = Σ_{k<j} k·p_k (F[0] = G[0] = 0).
        p = np.asarray(probs, dtype=np.float64)
        self._F = np.concatenate([[0.0], np.cumsum(p)])
        self._G = np.concatenate([[0.0], np.cumsum(p * np.arange(len(p)))])

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

    # ── vectorised twins (Phase 12c-pre, plan v2.1 C4) ─────────────────
    # The same grid rule as the scalar methods — ``ceil(x/Δ − 1e-12)``,
    # ``x ≤ 0 → S = 1 / ES = 0``, beyond the table → ``S = 0`` — over an
    # ``(H,)`` array. Pinned equal to the scalar pair to 1e-12 on grid
    # points, negatives and beyond-table loads (test A12).

    def _grid_index(self, x: np.ndarray) -> np.ndarray:
        k = np.ceil(x / self.delta_mw - 1e-12)
        return np.clip(k, 0, len(self.probs)).astype(np.int64)

    def survival_vec(self, x_mw) -> np.ndarray:
        """``P[available ≥ x]`` per element; ``x ≤ 0 → 1``."""
        x = np.asarray(x_mw, dtype=np.float64)
        s = self._surv[self._grid_index(x)]
        return np.where(x <= 0.0, 1.0, s)

    def expected_shortfall_vec(self, load_mw) -> np.ndarray:
        """``E[max(load − available, 0)]`` per element; ``load ≤ 0 → 0``."""
        x = np.asarray(load_mw, dtype=np.float64)
        j = self._grid_index(x)
        es = x * self._F[j] - self.delta_mw * self._G[j]
        return np.where(x <= 0.0, 0.0, es)


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
    """Convolve two-state units into the table. A unit carrying a profile is
    REFUSED rather than convolved at its nameplate: that silent flattening
    was the Phase 12a defect, and ``split_fleet`` is the one way in."""
    profiled = [u.name for u in units if u.profile is not None]
    if profiled:
        raise ValueError(
            "build_copt: units carry an availability profile and cannot be "
            f"convolved as two-state at nameplate: {profiled[:5]} — split the "
            "fleet with split_fleet() and mix them in hourly_adequacy()")
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


def mixture_hourly(dist: CapacityDistribution, residual, mixed=(),
                   *, fixed_up=frozenset()) -> tuple[np.ndarray, np.ndarray]:
    """
    Per-hour ``(LOLP_h, EUE_h)`` of the table ``dist`` (built WITHOUT the
    ``mixed`` units) mixed exactly over the ``mixed`` units' independent
    outage states (plan 12c-pre v2 §1.2):

        LOLP_h = Σ_{s ∈ {0,1}^k} P[s] · (1 − S(r_h − Σ_i s_i·a_{i,h}))
        EUE_h  = Σ_{s ∈ {0,1}^k} P[s] · ES(r_h − Σ_i s_i·a_{i,h})

    with ``a_{i,h} = profile_i(h)·cap_i`` and ``P[s] = Π (1−q_i)^{s_i}
    q_i^{1−s_i}``. The law of total probability over the profiled units'
    states, so it is exact — verified equal to an independently built
    per-hour table to 1e-14. ``2^k`` vectorised evaluations over H.

    ``fixed_up``: positions in ``mixed`` whose state is forced UP — the
    attribution counterfactual (unit ``i`` perfectly available). With no
    ``mixed`` units this is one plain vectorised evaluation.
    """
    r = np.asarray(residual, dtype=np.float64)
    H = r.shape[0]
    mixed = tuple(mixed)
    lolp = np.zeros(H, dtype=np.float64)
    eue = np.zeros(H, dtype=np.float64)
    if not mixed:
        return 1.0 - dist.survival_vec(r), dist.expected_shortfall_vec(r)
    avail = np.stack([_availability_mw(u, H) for u in mixed])   # (k, H)
    qs = np.array([float(u.q) for u in mixed], dtype=np.float64)
    free = [i for i in range(len(mixed)) if i not in fixed_up]
    for bits in itertools.product((0, 1), repeat=len(free)):
        s = np.ones(len(mixed), dtype=np.float64)
        prob = 1.0
        for i, b in zip(free, bits):
            s[i] = float(b)
            prob *= (1.0 - qs[i]) if b else qs[i]
        if prob <= 0.0:
            continue
        x = r - (s[:, None] * avail).sum(axis=0)
        lolp += prob * (1.0 - dist.survival_vec(x))
        eue += prob * dist.expected_shortfall_vec(x)
    return lolp, eue


def hourly_adequacy(dist: CapacityDistribution, residual_load: pd.Series,
                    *, weights: pd.Series, mixed=()) -> dict:
    """
    Screening LOLP/LOLE/EUE over an EXOGENOUS residual-load series (MW),
    weighted by the snapshot weights. Hours with residual ≤ 0 contribute
    nothing. Per-period split mirrors the other adequacy surfaces
    (MultiIndex level 0, else "ALL"). ``mixed``: profiled units mixed per
    hour on top of the table (``mixture_hourly``); empty for a plain table.
    """
    w = weights.reindex(residual_load.index).fillna(0.0)
    lolp_arr, eue_arr = mixture_hourly(
        dist, residual_load.to_numpy(dtype=np.float64), mixed)
    lolp = pd.Series(lolp_arr, index=residual_load.index)
    eue_h = pd.Series(eue_arr, index=residual_load.index)
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


def _finite_at(row, col) -> float | None:
    """``row[col]`` as a finite float, or None when it is absent, unparseable
    or NaN/inf. One accessor so the capacity rule below reads as the rule."""
    try:
        v = float(row[col])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _is_extendable(row) -> bool:
    """``p_nom_extendable`` on a component row (the column name is the same on
    ``generators`` and on ``storage_units``). Missing/NaN is NOT extendable —
    ``bool(nan)`` is True, which is exactly the wrong default here."""
    try:
        v = row["p_nom_extendable"]
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    if v is None:
        return False
    if isinstance(v, float) and math.isnan(v):
        return False
    return bool(v)


def solved_capacity(row) -> float:
    """
    THE capacity of one component row for the adequacy engines, in ONE place
    (coupling-loop spec §1.1; plan finding [B2]). Generators and storage share
    it because they shared the bug.

    For an **extendable** row whose frame carries a **finite** ``p_nom_opt``,
    that value is AUTHORITATIVE — **including 0.0**. An extendable asset the
    LP declined to build has zero capacity; the previous rule took the first
    finite value ``> 0`` from ``("p_nom_opt", "p_nom")``, so a declined asset
    fell through to its pre-solve NAMEPLATE and the COPT/MC scored a plan
    containing capacity that does not exist. The bias is optimistic and worst
    for an extendable battery, which was simulated at full power AND full
    energy.

    ``p_nom`` remains the fallback for NON-extendable rows, for frames with no
    ``p_nom_opt`` column, and for non-finite values. On that path the historic
    ``first finite value > 0`` chain is kept VERBATIM: PyPSA writes
    ``p_nom_opt = p_nom`` for a non-extendable row after a solve and leaves it
    at its 0.0 default before one, so the chain and ``p_nom`` already agree
    wherever the data is sane — and keeping it agreeing bit-for-bit is what
    leaves the pinned RTS-79/RBTS anchors untouched.

    Note the consequence, which is intended: on an UNSOLVED network PyPSA's
    ``p_nom_opt`` default is 0.0, so an extendable row reads as 0 MW until a
    solve writes a size. These engines score a solved PLAN; an extendable
    asset that no solve has sized is not capacity anyone can count on.
    """
    if _is_extendable(row):
        v = _finite_at(row, "p_nom_opt")
        if v is not None:
            # Clamp: a negative solved size is data noise, not a claim.
            return max(v, 0.0)
    for col in ("p_nom_opt", "p_nom"):
        v = _finite_at(row, col)
        if v is not None and v > 0:
            return v
    return 0.0


def _firm_capacity(gens: pd.DataFrame, g) -> float:
    """Firm capacity of generator ``g`` — ``solved_capacity``'s rule, applied
    to a row of ``n.generators``."""
    return solved_capacity(gens.loc[g])


def _membership_walk(n, elec_buses, *, keep_zero_capacity: bool = False):
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

    ``keep_zero_capacity`` (coupling-loop spec §1.2, plan finding [B1]) is the
    ONE test this walk relaxes: with it, a generator that clears every scope
    test EXCEPT ``cap > 0`` is still yielded, at 0.0 MW. ``sample_capacity``
    keys each unit's RNG substream to its POSITION in the fleet tuple, so a
    unit entering or leaving between two solves shifts every downstream unit's
    entire outage path — and building previously-unbuilt firm capacity is the
    canonical LP response to a tighter cap. Holding the zero-capacity row in
    the fleet keeps membership (the name set AND its order) invariant across
    those solves at no cost: the unit draws its chain, consumes its substream
    and contributes 0 MW, exactly the exclude-but-consume discipline
    ``elcc`` already uses. Default ``False`` — every existing surface (COPT,
    the MC study, ELCC, the pinned benchmark anchors) is unchanged.
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
        if cap <= 0 and not keep_zero_capacity:
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

    The walk is taken at the DEFAULT ``keep_zero_capacity=False`` on purpose,
    and the candidate list is therefore the same under either value of the
    flag: a zero-capacity row has no profile worth un-netting (``profile × 0``
    is the zero series), and offering it as a VRE candidate would price a
    capacity credit for an asset that does not exist. The superset fleet is a
    SAMPLING-stability device (§1.2); it is not a change to who the UI may ask
    about.
    """
    from services.adequacy.metrics import electrical_columns

    buses = getattr(n, "buses", None)
    if buses is None:
        return []
    elec_buses = set(electrical_columns(n, list(buses.index)))
    return [str(g) for g, _cap, row in _membership_walk(n, elec_buses)
            if row["source"] == "missing"]


def _occurrence_profile(p_max_pu_t, g, snapshots) -> np.ndarray | None:
    """The availability series an occurrence-bearing unit carries into the
    engines (Phase 12c-pre, plan v2.1 C1): its ``p_max_pu`` column when that
    column is INFORMATIVE (not identically 1), aligned to the snapshots. A
    NaN hour is availability 0 — one explicit line, the same rule the
    reserve margin nets a NaN hour by (Phase 12b rule 1). The static column
    is deliberately NOT read here (plan §1.3)."""
    if p_max_pu_t is None or g not in getattr(p_max_pu_t, "columns", []):
        return None
    col = p_max_pu_t[g].reindex(snapshots)
    if not series_is_informative(col.to_numpy(dtype=np.float64)):
        return None
    return np.nan_to_num(col.to_numpy(dtype=np.float64), nan=0.0)


def occurrence_units(n) -> list[tuple[str, float, object]]:
    """``(name, capacity_mw, occurrence_row)`` for every generator the
    membership walk admits to the sampled fleet — the population preflight
    must speak about when it discloses how a profile is modelled. The same
    walk ``fleet_and_residual`` takes, so the two cannot disagree."""
    from services.adequacy.metrics import electrical_columns

    buses = getattr(n, "buses", None)
    if buses is None:
        return []
    elec_buses = set(electrical_columns(n, list(buses.index)))
    return [(str(g), cap, row) for g, cap, row in _membership_walk(n, elec_buses)
            if row["source"] != "missing"]


@dataclass(frozen=True)
class FleetSplit:
    """How ``split_fleet`` partitions a fleet for the COPT surface."""
    table: tuple          # two-state units, convolved into the table
    mixed: tuple          # profiled units mixed exactly per hour (≤ K_EXACT)
    netted: tuple         # profiled units beyond the cap, netted at expectation


def split_fleet(units, *, k_exact: int = K_EXACT) -> FleetSplit:
    """
    Partition: units without a profile go into the table; profiled units are
    mixed exactly, largest ``mean(a_{i,h})`` first, up to ``k_exact`` of
    them; the remainder are netted at expected output. The order among the
    profiled units is stable (mean descending, then name) so which units
    are netted is a property of the data, not of frame order.
    """
    table = tuple(u for u in units if u.profile is None)
    profiled = [u for u in units if u.profile is not None]
    profiled.sort(key=lambda u: (
        -float(np.nanmean(np.asarray(u.profile, dtype=np.float64))) * float(u.capacity_mw),
        u.name))
    k = max(int(k_exact), 0)
    return FleetSplit(table=table, mixed=tuple(profiled[:k]),
                      netted=tuple(profiled[k:]))


def netted_expectation(netted, H: int) -> np.ndarray:  # noqa: N803
    """``Σ_j (1 − q_j)·a_{j,h}`` over the netted units — what is subtracted
    from the residual for the units beyond the exact cap. Zero when none."""
    out = np.zeros(int(H), dtype=np.float64)
    for u in netted:
        out += (1.0 - float(u.q)) * _availability_mw(u, H)
    return out


def fleet_and_residual(n, *, keep_zero_capacity: bool = False
                       ) -> tuple[list[CoptUnit], pd.Series, pd.Series]:
    """
    Apply the membership rule to a network: returns (COPT units, residual
    electrical load MW per snapshot, energy weights).

    Electrical scope and slack exclusion use the same classifiers as the
    ENS cap; occurrence resolution the same fallback chain as preflight —
    the engine and the target can never disagree on who counts.

    ``keep_zero_capacity`` is the superset-fleet flag (spec §1.2): see
    ``_membership_walk``. Default ``False`` changes nothing anywhere; ``True``
    admits occurrence-bearing generators at ``capacity_mw=0.0`` so that
    positional CRN substreams stay stable across networks that differ only in
    what the LP built. It is the coupling loop's flag — the COPT table, the MC
    study endpoint and ELCC all take the default.
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
    for g, cap, row in _membership_walk(n, elec_buses,
                                        keep_zero_capacity=keep_zero_capacity):
        if row["source"] != "missing":
            units.append(CoptUnit(
                name=str(g), capacity_mw=cap, q=float(row["rate"]),
                basis=str(row["basis"]), mttr_hours=float(row["mttr_hours"]),
                source=str(row["source"]),
                profile=_occurrence_profile(p_max_pu_t, g, snapshots),
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


NETTED_ROW_NOTE = (
    "beyond the exact-mixture cap: netted at expected output, so this row "
    "understates the unit's outages (netting was measured to understate a "
    "profiled unit's criticality up to 14x)")


def attribute_criticality(units: list[CoptUnit], dist: CapacityDistribution,
                          residual_load: pd.Series, *, weights: pd.Series,
                          voll: float, mixed=(), netted=()) -> list[dict]:
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

    Phase 12c-pre: ``units`` are the TABLE units (``dist`` was built over
    them); ``mixed`` are the profiled units mixed per hour and ``netted``
    those beyond the cap, exactly as ``split_fleet`` returned them and as
    ``hourly_adequacy`` saw them. A table unit's counterfactual is the
    deconvolve-and-shift table under the SAME mixture; a mixed unit's is the
    mixture with its state fixed UP (``ΔEUE_i = EUE(mixture) −
    EUE(s_i ≡ 1)``); a netted unit's is the residual with its full
    ``a_{j,h}`` netted instead of ``(1−q_j)·a_{j,h}`` — and its row carries
    ``note`` saying the netting understates it.
    """
    mixed = tuple(mixed)
    netted = tuple(netted)
    r = residual_load.to_numpy(dtype=np.float64)
    w = weights.reindex(residual_load.index).fillna(0.0).to_numpy(dtype=np.float64)

    def _eue(d, res, fixed_up=frozenset()) -> float:
        _lolp, eue_h = mixture_hourly(d, res, mixed, fixed_up=fixed_up)
        return float((eue_h * w).sum())

    base_eue = _eue(dist, r)
    rows: list[dict] = []
    todo: list[tuple[CoptUnit, float, str | None]] = []
    for u in units:
        try:
            without = deconvolve(dist, capacity_mw=u.capacity_mw, q=u.q)
        except ValueError:
            without = build_copt([v for v in units if v.name != u.name],
                                 delta_mw=dist.delta_mw)
        perfect = _shift_deterministic(without, u.capacity_mw)
        todo.append((u, _eue(perfect, r), None))
    for i, u in enumerate(mixed):
        todo.append((u, _eue(dist, r, fixed_up=frozenset({i})), None))
    for u in netted:
        # Base residual already nets (1−q)·a; perfect availability nets a.
        todo.append((u, _eue(dist, r - float(u.q) * _availability_mw(u, r.shape[0])),
                     NETTED_ROW_NOTE))
    for u, eue_perfect, note in todo:
        delta_eue = max(base_eue - eue_perfect, 0.0)
        crit_eur = delta_eue * max(float(voll), 0.0)
        occ = (8760.0 * u.q / u.mttr_hours
               if math.isfinite(u.mttr_hours) and u.mttr_hours > 0 else 0.0)
        severity = crit_eur / occ if occ > 0 else 0.0
        row = {
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
        }
        if note is not None:
            row["note"] = note
        rows.append(row)
    rows.sort(key=lambda r: r["delta_eue_mwh"], reverse=True)
    return rows


def fidelity_note(split: FleetSplit, *, k_exact: int = K_EXACT) -> str | None:
    """The one sentence the ``/copt`` payload says about profiled units;
    None when the fleet has none."""
    if not split.mixed and not split.netted:
        return None
    names = ", ".join(u.name for u in split.mixed)
    text = (f"{len(split.mixed)} unit(s) carry both an availability series "
            f"and outage data ({names}): outages are sampled on the series "
            "and the COPT mixes them exactly per hour over their outage "
            "states.")
    if split.netted:
        more = ", ".join(u.name for u in split.netted)
        text += (f" {len(split.netted)} more beyond the exact cap of "
                 f"{k_exact} ({more}) are netted at expected output; their "
                 "criticality rows understate their outages.")
    return text


def screening_analysis(units, residual_load: pd.Series, *, weights: pd.Series,
                       voll: float, delta_mw: float = 1.0,
                       k_exact: int = K_EXACT) -> dict:
    """
    The whole COPT surface for one fleet, in one call: split, net the
    remainder, build the table over the two-state units, mix the profiled
    ones per hour, attribute. Returns ``metrics``, ``rows``, ``split``,
    ``dist``, ``residual`` (as evaluated) and ``fidelity_note``.
    """
    split = split_fleet(units, k_exact=k_exact)
    H = len(residual_load)
    res = residual_load
    if split.netted:
        res = residual_load - pd.Series(netted_expectation(split.netted, H),
                                        index=residual_load.index)
    dist = build_copt(list(split.table), delta_mw=delta_mw)
    metrics = hourly_adequacy(dist, res, weights=weights, mixed=split.mixed)
    rows = attribute_criticality(list(split.table), dist, res, weights=weights,
                                 voll=voll, mixed=split.mixed,
                                 netted=split.netted)
    return {"metrics": metrics, "rows": rows, "split": split, "dist": dist,
            "residual": res,
            "fidelity_note": fidelity_note(split, k_exact=k_exact)}
