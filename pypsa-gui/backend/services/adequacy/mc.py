"""
Sequential Monte Carlo adequacy — the chronological engine.

Design: docs/superpowers/specs/2026-08-28-sequential-mc-engine-spec.md §§1–2;
plan docs/superpowers/plans/2026-08-28-fmea-phase6-sequential-mc.md Tasks 1–3.
This is the engine that can answer the question the other two structurally
cannot — *what is a battery worth in firm MW?* — because it is the only one
with **memory**:

* The **COPT** (``copt.py``) is a distribution over available capacity *in one
  hour*. A battery is nothing but memory; forcing one into a convolution
  asserts it can deliver its power in every hour of an event, so a 4 h battery
  would "cover" a 12 h Dunkelflaute — wrong in the dangerous (optimistic)
  direction.
* The **LP proxy** is storage-aware but has **perfect foresight**: it saves
  energy on Monday for Thursday's Dunkelflaute because it has seen Thursday.
  Real operators haven't. Optimistic again, by a different mechanism.

So: persistent two-state outage chains, a **non-anticipative** greedy storage
dispatch, hourly shortfall counting, per-draw metrics with a confidence
interval that is part of the number.

WHAT THIS ENGINE IS NOT (the standing warning, ``MC_WARNING_V1``, ships with
every payload): single-area copper plate, electrical-only, ONE weather
realisation (the modelled horizon's profiles), INDEPENDENT unit outages (no
common-mode / cold-snap-correlated derating — exactly the class-C motivation
that independent two-state chains cannot produce), and DSR not modelled as a
resource. That last clause matters for reading the MC-vs-proxy gap: DSR slacks
are rightly excluded as slacks, but in the LP they SERVE demand, so without the
warning a user attributes to foresight what is actually a missing resource.

**The MC never mutates the network** (spec §1). Every input is snapshotted
under the PyPSAService lock exactly once by ``snapshot_inputs`` into plain
numpy arrays; everything after that is lock-free arithmetic on copies. Unlike
the contingency sweep there is no undo machinery to get wrong — that property
is what makes this study safe to run beside an editing user.

**Membership is not re-derived here.** ``fleet_and_residual`` (the COPT's) is
consumed VERBATIM: same units, same must-take-netted residual, same weights.
Scope/slack/VRE-netting therefore cannot disagree between the two engines —
the provable-membership invariant the cross-check (T2) rests on. Only storage
extraction is new.

**Weights scale ACCOUNTING, not dynamics** (spec §2.4): shortfall hours and
energy are weighted in the sums exactly as the COPT weights them, while MTTR,
sojourns and SoC evolve in MODELLED hours. A 52×-weighted representative week
is one week of chronology standing for 52, not a stretched year.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Numerical dust must not count as a loss-of-load hour (spec §2.4 step 5); the
# same reasoning as metrics.DEFAULT_SHED_THRESHOLD_MW, one order tighter
# because nothing here comes out of an LP.
SHORTFALL_TOL = 1e-6

# Product cap on the adaptive batching. The BENCHMARK draw budget is a
# separate harness parameter (plan Task 7) precisely so this cap can stay a
# product decision without capping validation.
MAX_DRAWS = 2000

# One string, three clauses (spec §2.5). Every one of them is a way the number
# is optimistic or incomplete, stated where the number is read.
MC_WARNING_V1 = (
    "Sequential MC results rest on ONE weather realisation (the modelled "
    "horizon's profiles — no inter-annual variability); unit outages are drawn "
    "INDEPENDENT of one another (no common-mode or cold-snap-correlated "
    "derating); and demand response is EXCLUDED as a resource (DSR slacks are "
    "excluded as slacks here, but in the LP they serve demand — so part of any "
    "MC-vs-LP-proxy gap is a missing resource, not foresight)."
)


# ── §2.1 input snapshot ───────────────────────────────────────────────────

@dataclass(frozen=True)
class StorageSpec:
    """One dispatchable store, flattened out of ``n.storage_units``.

    ``Store``s are deliberately absent in v1: they carry no power rating, so
    there is nothing to bound the hourly discharge with and a greedy dispatch
    would let them behave as unlimited-power energy. Revisit with H2.
    """
    name: str
    p_nom_mw: float          # copt.solved_capacity's rule (spec §1.1)
    e_nom_mwh: float         # max_hours × p_nom_mw
    eff_store: float         # efficiency_store, default 1.0
    eff_dispatch: float      # efficiency_dispatch, default 1.0
    # Phase 12d: power rating in MW per modelled hour, ``(H,)``, when it is
    # not constant at ``p_nom_mw`` (inactive in a period by build year /
    # lifetime, or a vintage built later). The energy bound follows the
    # power fraction block by block. None is the scalar path.
    capacity_series: np.ndarray | None = field(default=None, compare=False,
                                               hash=False, repr=False)


@dataclass(frozen=True)
class MCInputs:
    """Everything the simulation needs, as plain arrays — no live network
    reference survives the snapshot (spec §§1, 2.1)."""
    units: tuple                    # CoptUnit list from fleet_and_residual
    residual: np.ndarray            # float64 (H,) — load minus must-take
    weights: np.ndarray             # float64 (H,) — same source
    periods: tuple                  # ((label, start, end_exclusive), ...)
    storage: tuple = ()             # (StorageSpec, ...)
    nyears: float = 0.0             # horizon_years(n) — the shared helper
    # ONLY the assets named in snapshot_inputs(..., vre_assets=[...]): the
    # must-take contribution (profile × capacity) that was netted OUT of the
    # residual, so ELCC can un-net it. Empty by default.
    vre_profiles: dict = field(default_factory=dict)


def _storage_capacity(row) -> float:
    """The CoptUnit capacity rule, applied to a store — literally the same
    function (``copt.solved_capacity``), not a parallel copy of it.

    An extendable battery the LP BUILT must be simulated at its built size,
    and one the LP DECLINED must not be simulated at all: the rule's zero
    clause matters most here, because a phantom store carried full POWER and
    full ENERGY (``max_hours × p_nom``) into the dispatch (spec §1.1, plan
    finding [B2]). Sharing the function is what makes the generator side and
    the storage side unable to drift.
    """
    from services.adequacy.copt import solved_capacity

    return solved_capacity(row)


def _efficiency(row, col: str) -> float:
    """PyPSA's default is 1.0; a missing/NaN/non-positive value is data noise,
    not a physical claim, so it degrades to lossless rather than silently
    zeroing a store's output."""
    try:
        v = float(row[col])
    except (KeyError, TypeError, ValueError):
        return 1.0
    if not math.isfinite(v) or v <= 0.0:
        return 1.0
    return v


# Phase 12d: the block construction lives beside the activity rule; the
# name is kept here for its callers and tests.
from services.adequacy.activity import period_blocks as _period_blocks  # noqa: E402


def snapshot_inputs(n, *, vre_assets=(), keep_zero_capacity=False, cfg=None,
                    demand_scaled_in_place: bool = False) -> MCInputs:
    """
    Freeze a network into MCInputs. Call this ONCE under the PyPSAService lock;
    everything downstream is lock-free (spec §1).

    ``keep_zero_capacity`` is threaded straight through to
    ``fleet_and_residual`` and applied to the storage extraction too
    (coupling-loop spec §1.2): with ``True``, generators and storage rows that
    clear every scope test except a positive capacity stay in the snapshot at
    0.0 — the units draw their outage chains and contribute 0 MW, the stores
    are dispatch-inert (``p_nom_mw = e_nom_mwh = 0.0``, so ``_dispatch``
    gives and takes exactly nothing). What that buys is a fleet whose
    MEMBERSHIP is invariant across solves that differ only in what was built,
    which is what keeps ``sample_capacity``'s positional substreams — and
    therefore common random numbers — alive across a coupling loop's iterates.
    Default ``False`` changes nothing: the MC study endpoint and ELCC take it.

    Units/residual/weights come from ``fleet_and_residual`` unchanged — see the
    module docstring on the membership invariant. Storage rows are the new
    part: electrical buses only, with ``slack.py``'s carrier and name tests
    applied to the STORAGE frame. There is no storage slack mask today because
    every slack the solver creates is a Generator (VOLL and DSR alike, already
    excluded upstream), but testing here anyway is the cheap half of defence in
    depth: the day a slack store appears it must not be counted as a resource.
    """
    from services.adequacy.copt import fleet_and_residual
    from services.adequacy.metrics import electrical_columns, horizon_years
    from services.adequacy.slack import is_slack_carrier, is_slack_name

    # Phase 12c-0: the LP's demand basis, threaded to the one place demand
    # is built (the fifteenth finding).
    units, residual, weights = fleet_and_residual(
        n, keep_zero_capacity=keep_zero_capacity, cfg=cfg,
        demand_scaled_in_place=demand_scaled_in_place)
    snapshots = n.snapshots

    # np.ascontiguousarray on .to_numpy(copy=True): no view onto a pandas block
    # can survive the lock release (spec §2.1).
    res = np.ascontiguousarray(
        np.asarray(residual.reindex(snapshots).to_numpy(), dtype=np.float64))
    w = np.ascontiguousarray(
        np.asarray(weights.reindex(snapshots).to_numpy(), dtype=np.float64))

    storage: list[StorageSpec] = []
    su = getattr(n, "storage_units", None)
    blocks = _period_blocks(snapshots)
    if su is not None and not su.empty:
        from services.adequacy.activity import ActivityContext
        elec_buses = set(electrical_columns(n, list(n.buses.index)))
        su_ctx = ActivityContext(n, "storage_units", blocks)
        for s in su.index:
            row = su.loc[s]
            if is_slack_name(str(s)) or is_slack_carrier(row.get("carrier")):
                continue
            if str(row.get("bus")) not in elec_buses:
                continue
            # Phase 12d: `solved_capacity`'s rule per period, through the
            # same activity context the generator walk uses; `p_nom_mw` is
            # the maximum over the blocks and the series carries the rest.
            p_nom, su_series = su_ctx.capacity_series(s, row)
            if p_nom <= 0 and not keep_zero_capacity:
                continue
            try:
                max_hours = float(row["max_hours"])
            except (KeyError, TypeError, ValueError):
                max_hours = 0.0
            if not math.isfinite(max_hours) or max_hours < 0:
                max_hours = 0.0
            storage.append(StorageSpec(
                name=str(s),
                p_nom_mw=p_nom,
                e_nom_mwh=max_hours * p_nom,
                eff_store=_efficiency(row, "efficiency_store"),
                eff_dispatch=_efficiency(row, "efficiency_dispatch"),
                capacity_series=su_series,
            ))

    profiles: dict[str, np.ndarray] = {}
    gens = getattr(n, "generators", None)
    p_max_pu_t = getattr(getattr(n, "generators_t", None), "p_max_pu", None)
    # Only genuinely MUST-TAKE generators may have a vre profile preserved.
    # Without this test the loop built a profile for whatever names the
    # request asked — a slack, a non-electrical generator — and kind="vre"
    # then priced it: a 9999 MW VoLL slack "un-netted" into the residual as
    # if it were wind. Membership is the same walk fleet_and_residual uses,
    # so the run and the candidates endpoint agree in BOTH directions; a name
    # that fails it is simply absent here, which _resolve turns into the
    # KeyError the route maps to 404.
    if vre_assets:
        from services.adequacy.activity import ActivityContext
        from services.adequacy.copt import must_take_generators
        _must_take = set(must_take_generators(n))
        gen_ctx = ActivityContext(n, "generators", blocks)
    else:
        _must_take = set()
        gen_ctx = None
    for name in vre_assets:
        if gens is None or name not in gens.index:
            # Silently absent rather than raising: the route turns a missing
            # ELCC asset into a 404 (spec §3), and a snapshot must not fail
            # because a caller asked about a name that has since been renamed.
            continue
        if name not in _must_take:
            continue
        # Phase 12d: the SAME per-period capacity the walk netted into the
        # residual (one function, `ActivityContext.capacity_series`), so
        # un-netting the preserved profile is exact.
        cap, cap_series = gen_ctx.capacity_series(name, gens.loc[name])
        cap_h = cap if cap_series is None else cap_series
        if p_max_pu_t is not None and name in getattr(p_max_pu_t, "columns", []):
            avail = p_max_pu_t[name].reindex(snapshots).fillna(0.0).to_numpy()
        else:
            try:
                static = float(gens.at[name, "p_max_pu"])
            except (KeyError, TypeError, ValueError):
                static = 1.0
            avail = np.full(len(snapshots), static, dtype=np.float64)
        profiles[str(name)] = np.ascontiguousarray(
            np.asarray(avail, dtype=np.float64) * cap_h)

    return MCInputs(
        units=tuple(units),
        residual=res,
        weights=w,
        periods=blocks,
        storage=tuple(storage),
        nyears=float(horizon_years(n)),
        vre_profiles=profiles,
    )


# ── §2.2 transition math ──────────────────────────────────────────────────

@dataclass(frozen=True)
class Transition:
    p_fail: float           # per-hour hazard of an up→down transition
    p_repair: float         # per-hour hazard of a down→up transition
    mttr_hours: float       # AS USED (i.e. after the one-timestep floor)
    mttf_hours: float


def transition_probs(q, mttr_hours, *, name: str = "") -> Transition:
    """
    The two-state chain implied by an (unavailability, MTTR) pair:

        MTTF = MTTR·(1−q)/q,   p_fail = 1/MTTF,   p_repair = 1/MTTR

    NO SILENT COERCION (spec §2.2, plan review finding 3). The v1 design
    clamped both probabilities into (0, 1], which quietly broke the stationary
    identity ``q = MTTR/(MTTR+MTTF)`` for sub-hour parameters — the model then
    simulated a different unavailability than the one the user entered, in the
    optimistic direction, with nothing said. Instead:

    * MTTR is FLOORED at one timestep (1 h) with a logged warning naming the
      unit. The floor preserves stationarity exactly: q depends only on the
      RATIO MTTF/MTTR, and flooring MTTR rescales MTTF with it.
    * A pair whose implied MTTF is under one hour is REJECTED — that is an
      inconsistent pair (the occurrence validator flags the same class via
      events/yr), and it surfaces as a 422 at the route rather than as a
      plausible-looking number.

    Sojourns are geometric (PRAS-standard): ``P(run = 1) = 1/MTTR`` per outage.
    Persistence is a statement about the MEAN run, not a floor on run length.
    A minimum-duration/semi-Markov repair model would preserve stationary
    availability by renewal-reward but break the memoryless stationary start
    that hour 0 depends on (it would need equilibrium residual-life
    initialisation) — a recorded non-goal, not an oversight.
    """
    q = float(q)
    if not math.isfinite(q) or q < 0.0:
        raise ValueError(f"unit {name!r}: unavailability {q!r} is not a "
                         "probability")
    # Upstream (occurrence.py) rejects q ≥ 1; assert anyway — a certainly-dead
    # unit has no MTTF and must never reach the sampler as one.
    assert q < 1.0, (f"unit {name!r}: unavailability {q} ≥ 1 must be rejected "
                     "by the occurrence validator before sampling")

    mttr = float(mttr_hours)
    if not math.isfinite(mttr) or mttr < 1.0:
        logger.warning(
            "adequacy MC: MTTR %r h for unit %r is below one timestep — "
            "floored to 1 h (stationary unavailability preserved)",
            mttr_hours, name)
        mttr = 1.0

    if q <= 0.0:
        # Deterministically up. No chain, no RNG (see sample_capacity).
        return Transition(p_fail=0.0, p_repair=1.0 / mttr,
                          mttr_hours=mttr, mttf_hours=float("inf"))

    mttf = mttr * (1.0 - q) / q
    if mttf < 1.0:
        raise ValueError(
            f"unit {name!r}: implied MTTF {mttf:.4g} h < 1 h — inconsistent "
            f"(unavailability {q:g}, MTTR {mttr:g} h) pair")
    # Both are now guaranteed ≤ 1 by construction, so nothing is clamped.
    return Transition(p_fail=1.0 / mttf, p_repair=1.0 / mttr,
                      mttr_hours=mttr, mttf_hours=mttf)


# ── §2.3 sampling: the CRN stream contract ────────────────────────────────

def sample_capacity(units, H, draws, seed, *, exclude=frozenset(),  # noqa: N803
                    periods=None) -> np.ndarray:
    """
    Available capacity, ``(draws, H)`` float32, summed over the fleet.

    THE CRN STREAM CONTRACT (spec §2.3 — load-bearing, not an optimisation).
    Every unit owns its own RNG substream keyed by its POSITION in the FULL
    fleet: ``children = rng.spawn(len(units))`` and ``children[i]`` is unit
    *i*'s stream whether or not unit *i* is excluded. An excluded unit's path
    is still GENERATED — and discarded — so every other unit's draws are
    bitwise identical across exclusion sets. If instead a ``(draws × units)``
    matrix were sampled jointly, changing the unit count would shift every
    other unit's draws and common random numbers would silently die exactly
    where ELCC leans on them (plan [e2e] finding on the stream-keying trap).

    A ``q = 0`` unit still occupies its slot and consumes NOTHING: identity is
    positional and consumption is per-stream, so the other units are unaffected
    either way.

    ``periods`` (default: one block over the whole horizon) restarts the chain
    from the stationary distribution at each block boundary — required by
    spec §2.4 step 1, and kept here rather than in ``simulate`` so the restart
    consumes the unit's OWN stream and the CRN guarantee survives it.

    Memory: the ``(draws, H, units)`` cube is never materialised (it is ~287 MB
    for RTS-79's 32 units at 256 draws) — each unit's path is reduced into the
    accumulator and dropped. Arrays are hour-major internally because the hour
    loop walks the time axis; strided column access over a draw-major array
    costs a cache line per draw per hour.
    """
    units = tuple(units)
    H = int(H)
    draws = int(draws)
    blocks = tuple(periods) if periods else (("ALL", 0, H),)

    acc = np.zeros((H, draws), dtype=np.float32)
    if not units or draws <= 0 or H <= 0:
        return np.ascontiguousarray(acc.T)

    rng = np.random.default_rng(seed)
    children = rng.spawn(len(units))
    state_path = np.empty((H, draws), dtype=bool)

    for i, u in enumerate(units):
        prof = getattr(u, "profile", None)
        cs = getattr(u, "capacity_series", None)
        if prof is None and cs is None:
            cap = np.float32(u.capacity_mw)
        elif cs is None:
            # Phase 12c-pre: UP is the series' value that hour, DOWN is zero.
            # An (H, 1) column broadcasts over draws in the very same
            # `np.add(..., where=state_path)` below — the chain, its stream
            # and its consumption are untouched, and a unit without a
            # profile takes the scalar path byte-for-byte as before (M2).
            prof = np.asarray(prof, dtype=np.float64)
            if prof.shape != (H,):
                raise ValueError(
                    f"unit {u.name!r}: profile has shape {prof.shape}, "
                    f"expected ({H},)")
            cap = (prof * float(u.capacity_mw)).astype(np.float32)[:, None]
        else:
            # Phase 12d: UP is the capacity the unit HAS that hour (its
            # series in MW — 0 in a period it is inactive in, a vintage's
            # partial size before the rest is built), times the profile.
            # Same column, same broadcast; an all-zero block is bit-identical
            # to excluding the unit there (E3).
            cs = np.asarray(cs, dtype=np.float64)
            if cs.shape != (H,):
                raise ValueError(
                    f"unit {u.name!r}: capacity series has shape {cs.shape}, "
                    f"expected ({H},)")
            if prof is not None:
                prof = np.asarray(prof, dtype=np.float64)
                if prof.shape != (H,):
                    raise ValueError(
                        f"unit {u.name!r}: profile has shape {prof.shape}, "
                        f"expected ({H},)")
                cs = prof * cs
            cap = cs.astype(np.float32)[:, None]
        q = float(u.q)
        included = i not in exclude
        if q <= 0.0:
            # Deterministically up; children[i] is never advanced (see above).
            if included:
                acc += cap
            continue

        t = transition_probs(q, u.mttr_hours, name=u.name)
        p_fail, p_repair = t.p_fail, t.p_repair
        # One block of uniforms per unit, consumed identically whether or not
        # the unit is included — that identity IS the bit-identity guarantee.
        u01 = children[i].random((H, draws))
        for _label, start, end in blocks:
            # Stationary start: up with probability 1−q. No burn-in, and hour 0
            # of every block is a usable hour (spec §2.3, T6).
            state = u01[start] >= q
            state_path[start] = state
            for h in range(start + 1, end):
                col = u01[h]
                # Geometric sojourns: survive up w.p. 1−p_fail, repair w.p.
                # p_repair. One uniform per (draw, hour) — the hazard applied
                # depends on the state, so the draw is reused, not re-drawn.
                state = (state & (col >= p_fail)) | (~state & (col < p_repair))
                state_path[h] = state
        if included:
            np.add(acc, cap, out=acc, where=state_path)

    return np.ascontiguousarray(acc.T)


# ── §2.4 simulation: non-anticipative storage dispatch ────────────────────

def _active_storage(inputs: MCInputs, storage_enabled: bool, exclude_storage):
    """The stores that dispatch in this run. ``exclude_storage`` accepts
    positional indices (mirroring ``exclude`` for units) or names, because
    ELCC removes a store BY NAME while the sampler removes a unit by slot."""
    if not storage_enabled:
        return ()
    excl = set(exclude_storage or ())
    return tuple(s for i, s in enumerate(inputs.storage)
                 if i not in excl and s.name not in excl)


def _dispatch(deficit, soc, p_nom, e_nom, eff_store, eff_dispatch, order):
    """
    One hour of greedy, NON-ANTICIPATIVE dispatch, vectorised across draws.

    No lookahead of any kind: the store answers this hour's deficit with this
    hour's SoC. That is the whole point of the engine — the LP proxy's
    perfect-foresight storage is optimistic in a way no confidence interval
    reveals.

    Order is PINNED POLICY, not an optimum (spec §2.4): discharge in
    DESCENDING remaining energy, charge in ASCENDING. With one store it is
    irrelevant; with several it keeps the fleet's energy balanced, which is the
    behaviour an operator without foresight can actually implement.

    ``soc`` (S, draws) is mutated in place. Returns the post-storage deficit.
    """
    n_store, n_draw = soc.shape
    cols = np.arange(n_draw)
    # A draw is either in deficit or in surplus, never both, so the two passes
    # below are mutually exclusive per draw — the masks come out of the
    # arithmetic (need = 0 ⇒ give = 0) rather than out of an if.
    need = np.maximum(deficit, 0.0)
    surplus = np.maximum(-deficit, 0.0)

    for r in range(n_store - 1, -1, -1):                  # descending SoC
        si = order[r]
        cur = soc[si, cols]
        ed = eff_dispatch[si]
        give = np.minimum(np.minimum(p_nom[si], cur * ed), need)
        soc[si, cols] = cur - give / ed
        need -= give

    for r in range(n_store):                              # ascending SoC
        si = order[r]
        cur = soc[si, cols]
        es = eff_store[si]
        headroom = np.maximum(e_nom[si] - cur, 0.0) / es
        take = np.minimum(np.minimum(p_nom[si], headroom), surplus)
        soc[si, cols] = cur + take * es
        surplus -= take

    # Exactly one of the two terms is non-zero per draw.
    return need - surplus


def block_store_arrays(stores, start: int, end: int, *, any_series: bool):
    """The dispatch arrays ``(n, p_nom, e_nom, eff_store, eff_dispatch)`` for
    one period block. Without a store series this is the fleet as built (the
    pre-12d arrays, once); with one, each store enters at its capacity IN
    THIS BLOCK — dropped at 0, its energy bound following the POWER fraction
    — so a store built in 2035 is not dispatched in 2030, and one at a
    part-built vintage carries a proportionally smaller reservoir (Phase 12d;
    module-level so the arrays can be pinned directly — shipped-code review,
    finding 3)."""
    if not any_series:
        kept = list(stores)
        p = np.array([s.p_nom_mw for s in kept], dtype=np.float64)
        e = np.array([s.e_nom_mwh for s in kept], dtype=np.float64)
    else:
        from services.adequacy.activity import block_capacity
        kept, p_l, e_l = [], [], []
        for s in stores:
            c = block_capacity(s.p_nom_mw, s.capacity_series, start, end)
            if c <= 0.0:
                continue
            kept.append(s)
            p_l.append(c)
            e_l.append(s.e_nom_mwh * (c / s.p_nom_mw) if s.p_nom_mw > 0 else 0.0)
        p = np.array(p_l, dtype=np.float64)
        e = np.array(e_l, dtype=np.float64)
    es = np.array([max(s.eff_store, 1e-12) for s in kept], dtype=np.float64)
    ed = np.array([max(s.eff_dispatch, 1e-12) for s in kept], dtype=np.float64)
    return len(kept), p, e, es, ed


def _simulate_blocks(inputs: MCInputs, *, draws: int, seed,
                     exclude=frozenset(), extra_firm_mw: float = 0.0,
                     storage_enabled: bool = True,
                     exclude_storage=frozenset(),
                     initial_soc_frac: float = 1.0) -> dict:
    """Per-period per-draw (lole_h, eue_mwh). ``simulate`` sums the blocks;
    ``mc_adequacy`` also needs them split, and computing them twice is the one
    place the total and the split could drift."""
    residual = inputs.residual
    weights = inputs.weights
    H = residual.size
    draws = int(draws)

    capacity = sample_capacity(inputs.units, H, draws, seed, exclude=exclude,
                               periods=inputs.periods)
    # Back to hour-major for the dispatch loop (see sample_capacity's note).
    cap_t = np.ascontiguousarray(capacity.T)

    all_stores = _active_storage(inputs, storage_enabled, exclude_storage)
    firm = float(extra_firm_mw)
    any_series = any(getattr(s, "capacity_series", None) is not None
                     for s in all_stores)

    def _store_arrays(start: int, end: int):
        return block_store_arrays(all_stores, start, end, any_series=any_series)

    out: dict = {}
    for label, start, end in inputs.periods:
        n_store, p_nom, e_nom, eff_s, eff_d = _store_arrays(start, end)
        single_order = np.zeros((1, draws), dtype=np.intp) if n_store == 1 else None
        # float64 accumulators: EUE sums 8760 terms of very different
        # magnitudes and float32 would lose the small ones (global constraint).
        lole = np.zeros(draws, dtype=np.float64)
        eue = np.zeros(draws, dtype=np.float64)
        # Per-period re-initialisation (spec §2.4 step 1): hour N of period P
        # is NOT followed by hour 0 of period P+1 — a battery must not carry
        # charge across a ten-year investment-period gap, and the outage states
        # restart from stationary too (inside sample_capacity). NOTHING carries
        # across a boundary.
        soc = np.repeat((e_nom * float(initial_soc_frac))[:, None], draws,
                        axis=1) if n_store else np.zeros((0, draws))

        for h in range(start, end):
            deficit = residual[h] - cap_t[h].astype(np.float64) - firm
            if n_store:
                if n_store == 1:
                    order = single_order
                else:
                    # Ascending remaining energy; the discharge pass walks it
                    # backwards. Stable so ties resolve by fleet order.
                    order = np.argsort(soc, axis=0, kind="stable")
                deficit = _dispatch(deficit, soc, p_nom, e_nom, eff_s, eff_d,
                                    order)
            w_h = weights[h]
            # Weights scale ACCOUNTING only (module docstring): the chronology
            # above ran in modelled hours.
            lole += w_h * (deficit > SHORTFALL_TOL)
            eue += w_h * np.maximum(deficit, 0.0)
        out[label] = (lole, eue)
    return out


def simulate(inputs: MCInputs, *, draws: int, seed, exclude=frozenset(),
             extra_firm_mw: float = 0.0, storage_enabled: bool = True,
             exclude_storage=frozenset(),
             initial_soc_frac: float = 1.0) -> tuple:
    """
    Per-draw ``(lole_h, eue_mwh)``, both float64 ``(draws,)`` (spec §2.4).

    ``extra_firm_mw`` is the ELCC bisection's firm block; ``exclude`` /
    ``exclude_storage`` are its removal semantics. All three leave the sampled
    outage paths untouched, which is what keeps every ELCC evaluation on common
    random numbers.
    """
    blocks = _simulate_blocks(
        inputs, draws=draws, seed=seed, exclude=exclude,
        extra_firm_mw=extra_firm_mw, storage_enabled=storage_enabled,
        exclude_storage=exclude_storage, initial_soc_frac=initial_soc_frac)
    lole = np.zeros(int(draws), dtype=np.float64)
    eue = np.zeros(int(draws), dtype=np.float64)
    for b_lole, b_eue in blocks.values():
        lole += b_lole
        eue += b_eue
    return lole, eue


# ── §2.5 aggregation & convergence ────────────────────────────────────────

def _mean_ci(values: np.ndarray) -> tuple:
    """(mean, (lo, hi), sem) — the 95% normal interval, LOWER BOUND CLAMPED AT
    ZERO. A negative LOLE bound is not a small number, it is a nonsense one;
    the panel renders these as ranges rather than ``±`` for the same reason
    (an interval near zero is asymmetric)."""
    n = values.size
    m = float(values.mean()) if n else 0.0
    sd = float(values.std(ddof=1)) if n > 1 else 0.0
    sem = sd / math.sqrt(n) if n else 0.0
    return m, (max(0.0, m - 1.96 * sem), m + 1.96 * sem), sem


def _cov(mean: float, sem: float) -> float:
    """CoV of the MEAN. A mean of exactly zero with zero spread is not
    "unconverged": every draw agreed, and the honest statement about it is the
    resolution floor, not another thousand draws."""
    if mean > 0.0:
        return sem / mean
    return 0.0 if sem == 0.0 else float("inf")


def mc_adequacy(inputs: MCInputs, *, draws: int = 500, seed=0,
                cov_target: float = 0.05, max_draws: int = MAX_DRAWS,
                batch: int = 250, **sim_kwargs) -> dict:
    """
    Batch until ``CoV(mean LOLE) ≤ cov_target`` or ``max_draws`` (spec §2.5).

    Batch k's seed is derived from ``seed`` alone, so the k-th batch is
    reproducible independently of how many batches a given call ends up
    running — the property ELCC's common random numbers need across
    evaluations. ``**sim_kwargs`` forwards ELCC's removal/firm-block arguments
    to ``simulate`` unchanged.

    Every payload carries ``resolution_floor_h``: the smallest NONZERO LOLE this
    many draws can resolve, **in the same units as ``lole_hours``** — one
    shortfall hour in one draw contributes that hour's weight to the mean, so
    the floor is ``min(positive snapshot weight) / n``. The spec's original
    ``1/(n·nyears)`` was a per-YEAR quantity compared against a per-HORIZON
    metric; on a sub-year horizon it was inflated by 8760/H and made ELCC's
    "unidentifiable" refusal fire far too eagerly (found by the ELCC worker,
    spec v1.2). Reported ALWAYS, not only when the answer is zero — a converged
    0.0 h and "below the floor" are different claims, and only the second one
    is true. None when no positive weight exists (degenerate horizon).
    """
    seq = seed if isinstance(seed, np.random.SeedSequence) \
        else np.random.SeedSequence(seed)
    labels = [b[0] for b in inputs.periods]
    parts: dict = {lab: ([], []) for lab in labels}
    lole_parts: list = []
    eue_parts: list = []

    cap = int(max_draws) if max_draws and max_draws > 0 else int(draws)
    size = min(max(int(draws), 1), cap)
    n_total = 0
    converged = False
    lole_all = np.zeros(0)
    eue_all = np.zeros(0)

    while True:
        blocks = _simulate_blocks(inputs, draws=size, seed=seq.spawn(1)[0],
                                  **sim_kwargs)
        batch_lole = np.zeros(size, dtype=np.float64)
        batch_eue = np.zeros(size, dtype=np.float64)
        for lab in labels:
            b_lole, b_eue = blocks[lab]
            parts[lab][0].append(b_lole)
            parts[lab][1].append(b_eue)
            batch_lole += b_lole
            batch_eue += b_eue
        lole_parts.append(batch_lole)
        eue_parts.append(batch_eue)
        n_total += size

        lole_all = np.concatenate(lole_parts)
        eue_all = np.concatenate(eue_parts)
        mean_l, _ci_l, sem_l = _mean_ci(lole_all)
        if _cov(mean_l, sem_l) <= cov_target:
            converged = True
            break
        if n_total >= cap:
            break
        size = min(int(batch), cap - n_total)
        if size <= 0:
            break

    lole_mean, lole_ci, _ = _mean_ci(lole_all)
    eue_mean, eue_ci, _ = _mean_ci(eue_all)
    by_period = {}
    for lab in labels:
        p_lole = np.concatenate(parts[lab][0])
        p_eue = np.concatenate(parts[lab][1])
        # Phase 12c: the period's own interval, from the same per-draw
        # arrays — a per-period ELCC row must not carry the horizon's CI
        # beside a period LOLE that lies outside it (shipped-code review,
        # finding 4).
        by_period[lab] = {"lole_hours": float(p_lole.mean()),
                          "eue_mwh": float(p_eue.mean()),
                          "lole_ci": _mean_ci(p_lole)[1],
                          "eue_ci": _mean_ci(p_eue)[1]}

    from services.adequacy.metrics import resolve_time_basis

    nyears = float(inputs.nyears)
    # A horizon of unknown length cannot state a floor; None says so instead of
    # dividing by zero or printing an infinity into the payload.
    w_pos = inputs.weights[inputs.weights > 0]
    floor = (float(w_pos.min()) / n_total) if (w_pos.size and n_total) else None

    return {
        "lole_hours": lole_mean,
        "lole_ci": lole_ci,
        "eue_mwh": eue_mean,
        "eue_ci": eue_ci,
        "by_period": by_period,
        "n_samples": int(n_total),
        "converged": bool(converged),
        "time_basis": resolve_time_basis(nyears),
        "horizon_years": nyears,
        "resolution_floor_h": floor,
        "warning": MC_WARNING_V1,
    }
