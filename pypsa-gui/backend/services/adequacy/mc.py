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
    p_nom_mw: float          # p_nom_opt when finite & > 0, else p_nom
    e_nom_mwh: float         # max_hours × p_nom_mw
    eff_store: float         # efficiency_store, default 1.0
    eff_dispatch: float      # efficiency_dispatch, default 1.0


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
    """The CoptUnit capacity rule, applied to a store: the solved size when a
    fresh solve exists, else the nameplate. An extendable battery the LP built
    must be simulated at its BUILT size — stated for generators inside
    ``fleet_and_residual``, and the new storage extraction must say it too
    (plan [e2e] finding on the storage capacity basis)."""
    for col in ("p_nom_opt", "p_nom"):
        try:
            v = float(row[col])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(v) and v > 0:
            return v
    return 0.0


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


def _period_blocks(snapshots) -> tuple:
    """Contiguous blocks of the snapshot axis: ``((label, start, end), ...)``.

    On a MultiIndex horizon these are the investment periods in axis order —
    the boundaries at which chronology RESTARTS (spec §2.4 step 1). Contiguity
    is asserted, not assumed: a re-ordered axis would make "hour N of period P
    is followed by hour 0 of P+1" true again in the arrays while being false in
    the model, which is exactly the error the re-initialisation exists to
    prevent.
    """
    n_h = len(snapshots)
    if not isinstance(snapshots, pd.MultiIndex):
        return (("ALL", 0, n_h),)
    level = list(snapshots.get_level_values(0))
    blocks: list[tuple] = []
    start = 0
    for i in range(1, n_h + 1):
        if i == n_h or level[i] != level[start]:
            label = level[start]
            try:
                label = int(label)
            except (TypeError, ValueError):
                label = str(label)
            blocks.append((label, start, i))
            start = i
    labels = [b[0] for b in blocks]
    assert len(set(labels)) == len(labels), (
        f"snapshot periods are not contiguous: {labels}")
    return tuple(blocks)


def snapshot_inputs(n, *, vre_assets=()) -> MCInputs:
    """
    Freeze a network into MCInputs. Call this ONCE under the PyPSAService lock;
    everything downstream is lock-free (spec §1).

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

    units, residual, weights = fleet_and_residual(n)
    snapshots = n.snapshots

    # np.ascontiguousarray on .to_numpy(copy=True): no view onto a pandas block
    # can survive the lock release (spec §2.1).
    res = np.ascontiguousarray(
        np.asarray(residual.reindex(snapshots).to_numpy(), dtype=np.float64))
    w = np.ascontiguousarray(
        np.asarray(weights.reindex(snapshots).to_numpy(), dtype=np.float64))

    storage: list[StorageSpec] = []
    su = getattr(n, "storage_units", None)
    if su is not None and not su.empty:
        elec_buses = set(electrical_columns(n, list(n.buses.index)))
        for s in su.index:
            row = su.loc[s]
            if is_slack_name(str(s)) or is_slack_carrier(row.get("carrier")):
                continue
            if str(row.get("bus")) not in elec_buses:
                continue
            p_nom = _storage_capacity(row)
            if p_nom <= 0:
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
        from services.adequacy.copt import must_take_generators
        _must_take = set(must_take_generators(n))
    else:
        _must_take = set()
    for name in vre_assets:
        if gens is None or name not in gens.index:
            # Silently absent rather than raising: the route turns a missing
            # ELCC asset into a 404 (spec §3), and a snapshot must not fail
            # because a caller asked about a name that has since been renamed.
            continue
        if name not in _must_take:
            continue
        cap = _storage_capacity(gens.loc[name])
        if p_max_pu_t is not None and name in getattr(p_max_pu_t, "columns", []):
            avail = p_max_pu_t[name].reindex(snapshots).fillna(0.0).to_numpy()
        else:
            try:
                static = float(gens.at[name, "p_max_pu"])
            except (KeyError, TypeError, ValueError):
                static = 1.0
            avail = np.full(len(snapshots), static, dtype=np.float64)
        profiles[str(name)] = np.ascontiguousarray(
            np.asarray(avail, dtype=np.float64) * cap)

    return MCInputs(
        units=tuple(units),
        residual=res,
        weights=w,
        periods=_period_blocks(snapshots),
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
        cap = np.float32(u.capacity_mw)
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

    stores = _active_storage(inputs, storage_enabled, exclude_storage)
    n_store = len(stores)
    p_nom = np.array([s.p_nom_mw for s in stores], dtype=np.float64)
    e_nom = np.array([s.e_nom_mwh for s in stores], dtype=np.float64)
    eff_s = np.array([max(s.eff_store, 1e-12) for s in stores], dtype=np.float64)
    eff_d = np.array([max(s.eff_dispatch, 1e-12) for s in stores],
                     dtype=np.float64)
    single_order = np.zeros((1, draws), dtype=np.intp) if n_store == 1 else None
    firm = float(extra_firm_mw)

    out: dict = {}
    for label, start, end in inputs.periods:
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
    by_period = {
        lab: {"lole_hours": float(np.concatenate(parts[lab][0]).mean()),
              "eue_mwh": float(np.concatenate(parts[lab][1]).mean())}
        for lab in labels
    }

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
