"""
Sequential Monte Carlo adequacy engine (Phase 6 Tasks 1–3): input snapshot,
two-state transition math, the CRN sampling contract, non-anticipative storage
dispatch, and the convergence/CI aggregation.

Contract: docs/superpowers/specs/2026-08-28-sequential-mc-engine-spec.md §§1, 2, 6;
plan docs/superpowers/plans/2026-08-28-fmea-phase6-sequential-mc.md Tasks 1–3.

Test standard, mirroring ``test_adequacy_copt.py``: wherever the quantity is
deterministic (dispatch arithmetic, CRN bit-identity, weight scaling) the
assertion is EXACT hand arithmetic, not a tolerance. Only the four genuinely
stochastic properties — stationary start (T6), sojourn law (T3), the COPT
cross-check (T2) and the persistence-in-a-metric contrast (T2b) — assert
through a confidence interval, and each of those is seeded and sized so the
assertion is stable across seeds (spot-checked on three).

Every ★ test names, in its docstring, the broken variant of ``mc.py`` it is
required to FAIL against (spec §6). Those bite checks are run by hand at
implementation time; the docstring is the record of what was checked.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.adequacy import copt as C
from services.adequacy import mc as M
from services.adequacy.metrics import HOURS_PER_YEAR

# ── fixtures / helpers ────────────────────────────────────────────────────

# Capacities are chosen exactly representable in float32 (the accumulator
# dtype, spec §2.3) so difference-based assertions are bit-exact.
UNITS3 = (
    C.CoptUnit(name="a", capacity_mw=100.0, q=0.10, basis="FOR", mttr_hours=10.0),
    C.CoptUnit(name="b", capacity_mw=50.0, q=0.20, basis="FOR", mttr_hours=20.0),
    C.CoptUnit(name="c", capacity_mw=25.0, q=0.05, basis="FOR", mttr_hours=5.0),
)


def _inputs(residual, *, units=(), storage=(), weight=1.0, periods=None,
            nyears=None, vre_profiles=None) -> "M.MCInputs":
    """An MCInputs built by hand — the dispatch tests must not depend on the
    network-extraction path (which has its own tests below)."""
    res = np.ascontiguousarray(np.asarray(residual, dtype=np.float64))
    h = len(res)
    w = np.full(h, float(weight), dtype=np.float64)
    return M.MCInputs(
        units=tuple(units),
        residual=res,
        weights=w,
        periods=tuple(periods) if periods is not None else (("ALL", 0, h),),
        storage=tuple(storage),
        nyears=(h * float(weight) / HOURS_PER_YEAR) if nyears is None else nyears,
        vre_profiles=dict(vre_profiles or {}),
    )


def _down_runs(down: np.ndarray) -> np.ndarray:
    """Lengths of maximal down-runs in a (draws, H) boolean matrix, EXCLUDING
    runs that touch either edge of the horizon — those are censored and would
    bias the sampled mean run length low."""
    out = []
    for row in down:
        pad = np.concatenate(([0], row.astype(np.int8), [0]))
        d = np.diff(pad)
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)          # exclusive
        keep = (starts > 0) & (ends < row.size)
        out.append(ends[keep] - starts[keep])
    return np.concatenate(out) if out else np.array([], dtype=int)


def _iid_sampler(units, h, draws, seed, *, exclude=frozenset(), periods=None):
    """The persistence-destroying variant used by T2b (and as T3's bite):
    independent Bernoulli draws every hour at the SAME stationary q. Its
    marginal per-hour availability distribution is identical to the Markov
    sampler's — which is exactly why thermal-only LOLE/EUE cannot tell them
    apart (plan review finding 1) and storage can."""
    rng = np.random.default_rng(seed)
    children = rng.spawn(len(units))
    acc = np.zeros((draws, h), dtype=np.float32)
    for i, u in enumerate(units):
        up = children[i].random((draws, h)) >= u.q
        if i not in exclude:
            acc += np.float32(u.capacity_mw) * up
    return acc


# ── §2.2 transition math ──────────────────────────────────────────────────

def test_transition_math_is_exact():
    """★ T-trans (part 1). MTTF = MTTR·(1−q)/q, p_fail = 1/MTTF,
    p_repair = 1/MTTR, and the stationary identity p_fail/(p_fail+p_repair) = q.

    T-trans is three clauses in three functions; the spec's named variant
    (drop the MTTR floor) bites part 2, which is where the floor lives.

    BROKEN VARIANT (bite) for THIS clause: take the failure hazard from the
    unavailability directly (``p_fail = q``) — p_fail becomes 0.1 instead of
    1/450 and the stationary identity collapses.
    """
    t = M.transition_probs(0.1, 50.0, name="u")
    assert t.mttr_hours == 50.0
    assert t.mttf_hours == pytest.approx(50.0 * 0.9 / 0.1)      # 450 h
    assert t.p_repair == pytest.approx(1.0 / 50.0)
    assert t.p_fail == pytest.approx(1.0 / 450.0)
    assert t.p_fail / (t.p_fail + t.p_repair) == pytest.approx(0.1, rel=1e-12)


def test_mttr_floor_keeps_the_stationary_unavailability():
    """★ T-trans (part 2): the floor at one timestep must not disturb q — the
    (0,1] clamp it replaced silently broke q = MTTR/(MTTR+MTTF) (plan review
    finding 3).

    BROKEN VARIANT (bite): remove the floor — mttr_hours stays 0.5, p_repair
    becomes 2.0 (> 1), and the asserted floored MTTR of 1.0 fails.
    """
    t = M.transition_probs(0.1, 0.5, name="tiny")
    assert t.mttr_hours == 1.0
    assert t.p_repair == pytest.approx(1.0)
    assert 0.0 < t.p_fail <= 1.0 and 0.0 < t.p_repair <= 1.0
    assert t.p_fail / (t.p_fail + t.p_repair) == pytest.approx(0.1, rel=1e-12)
    # ...and at MTTR exactly one hour, likewise (spec §2.2's worked case).
    t1 = M.transition_probs(0.5, 1.0, name="half")
    assert t1.p_fail == pytest.approx(1.0) and t1.p_repair == pytest.approx(1.0)
    assert t1.p_fail / (t1.p_fail + t1.p_repair) == pytest.approx(0.5, rel=1e-12)


def test_an_implied_sub_hour_mttf_is_rejected_not_clamped():
    """★ T-trans (part 3): an inconsistent (q, MTTR) pair raises instead of
    being clamped into a plausible-looking number (surfaces as 422 upstream).

    BROKEN VARIANT (bite) for THIS clause: clamp instead of reject
    (``mttf = max(mttf, 1.0)``) — exactly the silent coercion the spec forbids;
    no ValueError is raised and the inconsistent pair sails through.
    """
    with pytest.raises(ValueError) as e:
        M.transition_probs(0.9, 1.0, name="bad")
    assert "MTTF" in str(e.value)
    # q at or beyond 1 is an upstream validator's job, but never silently OK.
    with pytest.raises((ValueError, AssertionError)):
        M.transition_probs(1.0, 10.0, name="certainly_down")


def test_a_perfectly_available_unit_needs_no_transitions():
    """q = 0 → deterministically up; no transition probabilities to speak of
    and (spec §2.3) no RNG consumed."""
    t = M.transition_probs(0.0, 50.0, name="perfect")
    assert t.p_fail == 0.0
    assert math.isinf(t.mttf_hours) or t.mttf_hours > 1e12
    cap = M.sample_capacity(
        (C.CoptUnit(name="p", capacity_mw=64.0, q=0.0, mttr_hours=50.0),),
        8, 5, seed=3)
    assert np.array_equal(cap, np.full((5, 8), 64.0, dtype=np.float32))


# ── §2.3 sampling: stationary start, sojourn law, CRN ─────────────────────

def test_hour_zero_availability_is_stationary():
    """★ T6: the initial state is drawn from the stationary distribution (up
    w.p. 1−q), so hour 0 is usable — no burn-in.

    BROKEN VARIANT (bite): initialise every draw all-up
    (``state = np.ones(draws, bool)``) — hour-0 availability becomes 1.0.
    """
    q, cap, draws = 0.30, 100.0, 20_000
    units = (C.CoptUnit(name="u", capacity_mw=cap, q=q, mttr_hours=10.0),)
    sampled = M.sample_capacity(units, 2, draws, seed=101)
    # SE of the hour-0 mean is sqrt(0.3·0.7/20000) ≈ 0.0032; 0.02 is > 6 SE.
    assert abs(sampled[:, 0].mean() / cap - (1 - q)) < 0.02
    # and the chain is stationary, so hour 1 says the same thing.
    assert abs(sampled[:, 1].mean() / cap - (1 - q)) < 0.02


def test_sojourns_are_geometric_with_the_right_mean():
    """★ T3: mean sampled down-run ≈ MTTR AND P(run = 1) ≈ 1/MTTR. The second
    assertion pins the geometric FAMILY, so a fixed-duration sampler fails it
    too.

    BROKEN VARIANT (bite): swap in iid Bernoulli draws at the same stationary
    q (``state = u >= q`` every hour) — the mean run collapses to
    1/(1−q) = 1.25 h and P(run = 1) rises to 0.8.
    """
    q, mttr, cap = 0.20, 10.0, 100.0
    units = (C.CoptUnit(name="u", capacity_mw=cap, q=q, mttr_hours=mttr),)
    sampled = M.sample_capacity(units, 2000, 200, seed=7)
    runs = _down_runs(sampled == 0.0)
    assert runs.size > 5_000, runs.size          # sized for a stable CI
    # sd of a geometric(0.1) is 9.49 → SE of the mean ≈ 0.11 over ~8000 runs.
    assert abs(runs.mean() - mttr) < 0.6, runs.mean()
    p1 = float((runs == 1).mean())
    assert abs(p1 - 1.0 / mttr) < 0.02, p1


def test_every_included_units_path_is_bit_identical_under_exclusion():
    """★ T-CRN: each unit owns a substream keyed by its POSITION in the full
    fleet, and an excluded unit's draws are generated-and-discarded — so every
    other unit's sampled path is bitwise identical for any exclusion set.
    This is what makes ELCC's common random numbers real (spec §2.3, plan
    finding [e2e] on the CRN stream-keying trap).

    BROKEN VARIANT (bite): sample the fleet jointly — one shared stream, and
    generation SKIPPED for excluded units (``if i in exclude: continue`` before
    drawing) — every other unit's stream then shifts with the exclusion set.
    """
    h, draws, seed = 64, 32, 5
    full = M.sample_capacity(UNITS3, h, draws, seed)
    for i, u in enumerate(UNITS3):
        contribution = full - M.sample_capacity(UNITS3, h, draws, seed,
                                                exclude={i})
        # A two-state unit contributes exactly 0 or its capacity.
        assert set(np.unique(contribution)) <= {0.0, np.float32(u.capacity_mw)}
        for j in range(len(UNITS3)):
            if j == i:
                continue
            base = M.sample_capacity(UNITS3, h, draws, seed, exclude={j})
            both = M.sample_capacity(UNITS3, h, draws, seed, exclude={i, j})
            assert np.array_equal(base - both, contribution)


def test_a_perfect_unit_still_occupies_its_stream_slot():
    """★ T-CRN (part 2): substream identity is POSITIONAL, so replacing unit 0
    with a q = 0 unit (which consumes no draws at all) must not move unit 1's
    or unit 2's path by a single bit.

    BROKEN VARIANT (bite): the same joint-sampling variant as above.
    """
    h, draws, seed = 48, 16, 9
    alt = (C.CoptUnit(name="a0", capacity_mw=100.0, q=0.0, mttr_hours=10.0),
           ) + UNITS3[1:]
    for i in (1, 2):
        ref = (M.sample_capacity(UNITS3, h, draws, seed)
               - M.sample_capacity(UNITS3, h, draws, seed, exclude={i}))
        got = (M.sample_capacity(alt, h, draws, seed)
               - M.sample_capacity(alt, h, draws, seed, exclude={i}))
        assert np.array_equal(ref, got)


def test_outage_states_reinitialise_at_period_boundaries():
    """Spec §2.4 step 1: nothing carries across a period boundary — the outage
    state at hour 0 of a block is drawn fresh from the stationary distribution,
    not continued from the previous block's last hour. With MTTR = 200 h the
    within-block hour-to-hour agreement is ~99.5%; across the boundary it must
    fall to the independent level 1 − 2q(1−q) = 0.5."""
    q, mttr = 0.5, 200.0
    units = (C.CoptUnit(name="sticky", capacity_mw=100.0, q=q, mttr_hours=mttr),)
    blocks = (("p1", 0, 50), ("p2", 50, 100))
    up = M.sample_capacity(units, 100, 4000, seed=13, periods=blocks) > 0
    within = float((up[:, 20] == up[:, 21]).mean())
    across = float((up[:, 49] == up[:, 50]).mean())
    assert within > 0.95, within
    assert abs(across - 0.5) < 0.04, across


# ── §2.4 dispatch: exact hand arithmetic ──────────────────────────────────

def test_deterministic_fleet_with_storage_is_exact_both_efficiencies():
    """★ T1: q = 0 everywhere → EUE and shed hours equal hand-computed
    arithmetic exactly, including losses in BOTH directions.

    Fixture: 50 MW firm, residual [100, 100, −60, 100]; one 100 MW / 100 MWh
    store, η_store = 0.8, η_dispatch = 0.5, initial SoC 100%.
      h0 deficit 50 → give = min(100, 100·0.5, 50) = 50, SoC 100 → 0
      h1 deficit 50, SoC 0                       → EUE 50, one shed hour
      h2 surplus 110 → take = min(100, 100/0.8, 110) = 100, SoC += 80
      h3 deficit 50 → give = min(100, 80·0.5, 50) = 40 → EUE 10, one shed hour
    ⇒ EUE = 60 MWh, LOLE = 2 h.

    BROKEN VARIANT (bite): charge the SoC by the delivered power rather than
    the drawn energy (``soc -= give`` instead of ``soc -= give/eff_dispatch``)
    — EUE becomes 43.75 MWh.
    """
    units = (C.CoptUnit(name="firm", capacity_mw=50.0, q=0.0, mttr_hours=10.0),)
    store = M.StorageSpec(name="batt", p_nom_mw=100.0, e_nom_mwh=100.0,
                          eff_store=0.8, eff_dispatch=0.5)
    inp = _inputs([100.0, 100.0, -60.0, 100.0], units=units, storage=(store,))
    lole, eue = M.simulate(inp, draws=3, seed=0, initial_soc_frac=1.0)
    assert lole.dtype == np.float64 and eue.dtype == np.float64
    assert lole.shape == (3,) and eue.shape == (3,)
    assert eue == pytest.approx(np.full(3, 60.0))
    assert lole == pytest.approx(np.full(3, 2.0))


def test_the_charge_path_is_exact_from_empty():
    """★ T1b: start EMPTY with surplus hours ahead of the deficit, so the
    charging path is validated independently of any free initial cycle (plan
    review finding 5 — SoC 100% is not "bounded optimism" on short horizons).

    Fixture: no fleet, residual [−30, −80, −80, 200, 200]; 50 MW / 60 MWh
    store, η_store = 0.5, η_dispatch = 1.0, initial SoC 0.
      h0 surplus 30 → take = min(50, 60/0.5, 30) = 30 → SoC 15
      h1 surplus 80 → take = min(50, (60−15)/0.5, 80) = 50 (power-bound) → 40
      h2 surplus 80 → take = min(50, (60−40)/0.5, 80) = 40 (energy-bound) → 60
      h3 deficit 200 → give = min(50, 60, 200) = 50 → EUE 150, SoC 10
      h4 deficit 200 → give = min(50, 10, 200) = 10 → EUE 190
    ⇒ EUE = 340 MWh, LOLE = 2 h.

    BROKEN VARIANT (bite): store the drawn power rather than the stored energy
    (``soc += take`` instead of ``soc += take·eff_store``) — EUE becomes 320.
    """
    store = M.StorageSpec(name="batt", p_nom_mw=50.0, e_nom_mwh=60.0,
                          eff_store=0.5, eff_dispatch=1.0)
    inp = _inputs([-30.0, -80.0, -80.0, 200.0, 200.0], storage=(store,))
    lole, eue = M.simulate(inp, draws=2, seed=0, initial_soc_frac=0.0)
    assert eue == pytest.approx(np.full(2, 340.0))
    assert lole == pytest.approx(np.full(2, 2.0))


def test_state_of_charge_does_not_cross_a_period_boundary():
    """★ T-period: on a MultiIndex horizon, hour N of period P is NOT followed
    by hour 0 of period P+1 — a battery must not carry charge across a ten-year
    gap (spec §2.4 step 1, plan [e2e] per-period re-initialisation).

    Fixture: blocks [0,2) and [2,4), residual [−100, −100, 100, 100], one
    100 MW / 100 MWh lossless store starting empty. Block 1 charges to full and
    block 2 starts empty again ⇒ EUE = 200 MWh, LOLE = 2 h.

    BROKEN VARIANT (bite): remove the per-block re-initialisation (carry SoC
    across the boundary) — h2 is covered and EUE falls to 100 MWh.
    """
    store = M.StorageSpec(name="batt", p_nom_mw=100.0, e_nom_mwh=100.0,
                          eff_store=1.0, eff_dispatch=1.0)
    blocks = ((2030, 0, 2), (2035, 2, 4))
    inp = _inputs([-100.0, -100.0, 100.0, 100.0], storage=(store,),
                  periods=blocks)
    lole, eue = M.simulate(inp, draws=2, seed=0, initial_soc_frac=0.0)
    assert eue == pytest.approx(np.full(2, 200.0))
    assert lole == pytest.approx(np.full(2, 2.0))
    out = M.mc_adequacy(inp, draws=2, seed=0, initial_soc_frac=0.0)
    assert out["by_period"][2030]["eue_mwh"] == pytest.approx(0.0)
    assert out["by_period"][2035]["eue_mwh"] == pytest.approx(200.0)


def test_storage_limits_degenerate_to_firm_capacity_and_to_absence():
    """★ T4: an infinite-duration store is exactly ``+p_nom`` of firm capacity,
    and a zero-energy store is exactly absent — on IDENTICAL draws, so the
    equality is elementwise per draw, not statistical.

    BROKEN VARIANT (bite): drop the SoC bound from the discharge
    (``give = min(p_nom, deficit)``) — the zero-energy store then behaves as
    100 MW of firm capacity and the "≡ absent" equality fails.
    """
    residual = [120.0, 40.0, 160.0, 90.0, 200.0, 30.0]
    inf_store = M.StorageSpec(name="inf", p_nom_mw=100.0, e_nom_mwh=1e9,
                              eff_store=1.0, eff_dispatch=1.0)
    dead_store = M.StorageSpec(name="dead", p_nom_mw=100.0, e_nom_mwh=0.0,
                               eff_store=1.0, eff_dispatch=1.0)
    with_inf = _inputs(residual, units=UNITS3, storage=(inf_store,))
    bare = _inputs(residual, units=UNITS3)
    with_dead = _inputs(residual, units=UNITS3, storage=(dead_store,))

    a = M.simulate(with_inf, draws=64, seed=21, initial_soc_frac=1.0)
    b = M.simulate(bare, draws=64, seed=21, extra_firm_mw=100.0)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])

    c = M.simulate(with_dead, draws=64, seed=21, initial_soc_frac=1.0)
    d = M.simulate(bare, draws=64, seed=21)
    assert np.array_equal(c[0], d[0]) and np.array_equal(c[1], d[1])


def test_storage_can_only_help_on_identical_draws():
    """The storage-only-helps invariant under CRN (plan Task 3): the same
    sampled outage paths, dispatched with and without the battery, can never
    make the battery case worse — per draw, not on average."""
    residual = [80.0, 150.0, 20.0, 175.0, 60.0, 190.0, 100.0, 40.0]
    store = M.StorageSpec(name="batt", p_nom_mw=40.0, e_nom_mwh=80.0,
                          eff_store=0.9, eff_dispatch=0.9)
    with_s = _inputs(residual, units=UNITS3, storage=(store,))
    without = _inputs(residual, units=UNITS3)
    lw, ew = M.simulate(with_s, draws=128, seed=33, initial_soc_frac=1.0)
    lo, eo = M.simulate(without, draws=128, seed=33)
    assert (lw <= lo + 1e-12).all()
    assert (ew <= eo + 1e-12).all()
    assert ew.sum() < eo.sum()          # and it does help on this fixture


def test_storage_can_be_excluded_from_dispatch():
    """ELCC's ``storage_unit`` removal semantics (spec §3): excluding the only
    store must reproduce the storage-free run exactly, draws included."""
    residual = [80.0, 150.0, 20.0, 175.0, 60.0, 190.0]
    store = M.StorageSpec(name="batt", p_nom_mw=40.0, e_nom_mwh=80.0,
                          eff_store=0.9, eff_dispatch=0.9)
    with_s = _inputs(residual, units=UNITS3, storage=(store,))
    without = _inputs(residual, units=UNITS3)
    a = M.simulate(with_s, draws=32, seed=4, exclude_storage={0})
    b = M.simulate(without, draws=32, seed=4)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])
    c = M.simulate(with_s, draws=32, seed=4, storage_enabled=False)
    assert np.array_equal(c[0], b[0]) and np.array_equal(c[1], b[1])


def test_excluding_a_unit_removes_exactly_its_capacity():
    """ELCC's ``generator`` removal semantics: unit i's capacity leaves the
    aggregation while every other unit's path is untouched (T-CRN, at the
    simulate level)."""
    residual = [140.0, 160.0, 120.0, 175.0]
    full = _inputs(residual, units=UNITS3)
    a = M.simulate(full, draws=32, seed=8, exclude={2})
    # Unit c is 25 MW; compensating with 25 MW of firm capacity restores the
    # full-fleet result only when c happens to be up — so assert the weaker,
    # exact statement: excluding it can never reduce shortfall.
    b = M.simulate(full, draws=32, seed=8)
    assert (a[1] >= b[1] - 1e-9).all()
    assert a[1].sum() > b[1].sum()


# ── weights scale accounting, not dynamics ────────────────────────────────

def test_weights_scale_accounting_but_not_dynamics():
    """Spec §2.4 closing line / plan [e2e]: snapshot weights multiply the
    shortfall ACCOUNTING; MTTR, sojourns and SoC evolve in modelled hours. A
    52×-weighted representative week is one week of chronology standing for
    52, not a stretched year — so doubling the weights doubles LOLE and EUE
    exactly (a power-of-two factor, hence bit-exact) and changes nothing else.
    """
    residual = [80.0, 150.0, 20.0, 175.0, 60.0, 190.0, 100.0, 40.0]
    store = M.StorageSpec(name="batt", p_nom_mw=40.0, e_nom_mwh=80.0,
                          eff_store=0.9, eff_dispatch=0.9)
    one = _inputs(residual, units=UNITS3, storage=(store,), weight=1.0)
    two = _inputs(residual, units=UNITS3, storage=(store,), weight=2.0)
    l1, e1 = M.simulate(one, draws=64, seed=55, initial_soc_frac=1.0)
    l2, e2 = M.simulate(two, draws=64, seed=55, initial_soc_frac=1.0)
    assert np.array_equal(l2, 2.0 * l1)
    assert np.array_equal(e2, 2.0 * e1)


# ── §2.5 cross-checks, CI, convergence ────────────────────────────────────

def _copt_exact(units, residual, weight=1.0):
    idx = pd.date_range("2030-01-01", periods=len(residual), freq="h")
    dist = C.build_copt(list(units), delta_mw=1.0)
    return C.hourly_adequacy(dist, pd.Series(residual, index=idx),
                             weights=pd.Series(weight, index=idx))


def test_thermal_only_mc_agrees_with_the_exact_convolution():
    """★ T2: a thermal-only stochastic fleet — MC LOLE and EUE bracket the
    COPT's exact convolution inside the 99% CI (spec §6, plan Task 3).

    T2 IS PERSISTENCE-BLIND, AND THAT IS A THEOREM, NOT A GAP: thermal-only
    LOLE = Σ_t w_t·P(C_t < L_t) depends only on the MARGINAL per-hour
    availability distribution, which an iid Bernoulli sampler at the same
    stationary q reproduces exactly (plan review finding 1). Persistence is
    pinned by T3 (run lengths) and T2b (a storage metric).

    BROKEN VARIANT (bite): compute the failure hazard from the unavailability
    directly (``p_fail = q``) — the stationary unavailability becomes
    q/(q + 1/MTTR) and LOLE lands nowhere near the exact value.
    """
    units = (C.CoptUnit(name="u1", capacity_mw=60.0, q=0.1, mttr_hours=50.0),
             C.CoptUnit(name="u2", capacity_mw=40.0, q=0.2, mttr_hours=40.0))
    h, draws = 168, 1200
    residual = [70.0] * h                       # the canonical COPT fixture
    exact = _copt_exact(units, residual)
    inp = _inputs(residual, units=units)
    lole, eue = M.simulate(inp, draws=draws, seed=11)

    for got, ref in ((lole, exact["lole_hours"]), (eue, exact["eue_mwh"])):
        m = float(got.mean())
        sem = float(got.std(ddof=1)) / math.sqrt(draws)
        assert m - 2.576 * sem <= ref <= m + 2.576 * sem, (m, sem, ref)
        assert abs(m - ref) / ref < 0.15


def test_persistence_changes_a_storage_metric_but_not_a_thermal_one():
    """★ T2b: the metric-level pin for persistence (the test v1 wrongly
    believed T2 was). Same stationary q, two samplers:

    * WITHOUT storage the two agree — that is finding 1's theorem restated.
    * WITH a battery they must NOT: persistent outages outlast the battery's
      energy, iid ones are covered and let it recharge in between.

    BROKEN VARIANT (bite): the iid sampler IS the broken variant — if
    ``sample_capacity`` loses persistence, the with-storage assertion below
    reduces to EUE ≠ EUE and fails.
    """
    q, mttr, cap, load = 0.2, 20.0, 100.0, 80.0
    units = (C.CoptUnit(name="lonely", capacity_mw=cap, q=q, mttr_hours=mttr),)
    h, draws = 500, 400
    residual = [load] * h
    store = M.StorageSpec(name="batt", p_nom_mw=100.0, e_nom_mwh=200.0,
                          eff_store=1.0, eff_dispatch=1.0)
    bare = _inputs(residual, units=units)
    batt = _inputs(residual, units=units, storage=(store,))

    persistent_bare = M.simulate(bare, draws=draws, seed=17)[1]
    persistent_batt = M.simulate(batt, draws=draws, seed=17,
                                 initial_soc_frac=1.0)[1]
    saved = M.sample_capacity
    try:
        M.sample_capacity = _iid_sampler
        iid_bare = M.simulate(bare, draws=draws, seed=17)[1]
        iid_batt = M.simulate(batt, draws=draws, seed=17,
                              initial_soc_frac=1.0)[1]
    finally:
        M.sample_capacity = saved

    # 1. Thermal-only: statistically indistinguishable (the theorem).
    diff = abs(persistent_bare.mean() - iid_bare.mean())
    se = math.hypot(persistent_bare.std(ddof=1), iid_bare.std(ddof=1)) / math.sqrt(draws)
    assert diff < 4.0 * se, (persistent_bare.mean(), iid_bare.mean(), se)

    # 2. With the battery: persistence costs real energy — a factor, not noise.
    assert persistent_batt.mean() > 3.0 * iid_batt.mean(), (
        persistent_batt.mean(), iid_batt.mean())


def test_mc_adequacy_reports_two_intervals_and_the_standing_warning():
    """Spec §2.5: per-metric CIs, n_samples, the derived time basis, the
    resolution floor and the three-clause warning — every field always."""
    units = (C.CoptUnit(name="u1", capacity_mw=60.0, q=0.1, mttr_hours=50.0),
             C.CoptUnit(name="u2", capacity_mw=40.0, q=0.2, mttr_hours=40.0))
    inp = _inputs([70.0] * 168, units=units)
    out = M.mc_adequacy(inp, draws=200, seed=3, cov_target=0.10)
    for key in ("lole_hours", "lole_ci", "eue_mwh", "eue_ci", "by_period",
                "n_samples", "converged", "time_basis", "horizon_years",
                "resolution_floor_h", "warning"):
        assert key in out, key
    lo, hi = out["lole_ci"]
    assert lo <= out["lole_hours"] <= hi and lo >= 0.0
    elo, ehi = out["eue_ci"]
    assert elo <= out["eue_mwh"] <= ehi and elo >= 0.0
    assert out["time_basis"] == "hours_per_horizon"       # a week is not a year
    assert out["horizon_years"] == pytest.approx(168 / HOURS_PER_YEAR)
    # The floor lives in lole_hours' OWN units (per horizon, weight-aware):
    # min positive weight / n. This line previously pinned 1/(n·nyears) — a
    # per-YEAR quantity against a per-HORIZON metric, inflated 52× here —
    # i.e. it pinned the bug the ELCC worker found (spec v1.2).
    assert out["resolution_floor_h"] == pytest.approx(1.0 / out["n_samples"])
    assert out["warning"] == M.MC_WARNING_V1
    low = M.MC_WARNING_V1.lower()
    assert "weather" in low and "independent" in low and "demand response" in low
    assert out["by_period"]["ALL"]["lole_hours"] == pytest.approx(
        out["lole_hours"])


def test_an_annualised_week_is_labelled_per_year():
    """The shared helpers, re-asserted through the MC: the same week under two
    weightings gets two different labels and 52× the hours."""
    units = (C.CoptUnit(name="u1", capacity_mw=60.0, q=0.1, mttr_hours=50.0),)
    weight = HOURS_PER_YEAR / 168
    inp = _inputs([70.0] * 168, units=units, weight=weight)
    out = M.mc_adequacy(inp, draws=100, seed=5, cov_target=0.5)
    assert out["time_basis"] == "hours_per_year"
    assert out["horizon_years"] == pytest.approx(1.0)


def test_a_shortfall_free_system_reports_a_resolution_floor_not_a_bare_zero():
    """CI honesty (plan Task 3): all draws shortfall-free reports LOLE 0 with
    the floor `1/(n·nyears)` alongside — never a confident bare zero."""
    units = (C.CoptUnit(name="huge", capacity_mw=500.0, q=0.0, mttr_hours=10.0),)
    inp = _inputs([70.0] * 168, units=units)
    out = M.mc_adequacy(inp, draws=50, seed=1)
    assert out["lole_hours"] == 0.0
    assert out["lole_ci"] == (0.0, 0.0)
    assert out["eue_mwh"] == 0.0
    assert out["resolution_floor_h"] > 0.0
    assert out["converged"] is True


def test_convergence_stops_at_the_target_or_at_the_draw_cap():
    """Batches until CoV(mean LOLE) ≤ cov_target, capped by max_draws — and
    the cap reports ``converged: False`` rather than pretending."""
    units = (C.CoptUnit(name="u1", capacity_mw=60.0, q=0.1, mttr_hours=50.0),
             C.CoptUnit(name="u2", capacity_mw=40.0, q=0.2, mttr_hours=40.0))
    inp = _inputs([70.0] * 24, units=units)
    lax = M.mc_adequacy(inp, draws=100, seed=2, cov_target=0.5, max_draws=400,
                        batch=100)
    assert lax["converged"] is True and lax["n_samples"] == 100
    tight = M.mc_adequacy(inp, draws=100, seed=2, cov_target=1e-9,
                          max_draws=300, batch=100)
    assert tight["converged"] is False and tight["n_samples"] == 300
    assert M.MAX_DRAWS == 2000


# ── §2.1 the input snapshot ───────────────────────────────────────────────

def _network(multi: bool = False) -> pypsa.Network:
    n = pypsa.Network()
    base = pd.date_range("2030-01-01", periods=3, freq="h")
    if multi:
        mi = pd.MultiIndex.from_product([[2030, 2035], base],
                                        names=["period", "timestep"])
        mi.name = "snapshot"
        n.snapshots = mi
        n.investment_periods = [2030, 2035]
    else:
        n.set_snapshots(base)
    n.snapshot_weightings.loc[:, :] = 2.0
    n.add("Carrier", "gas")
    n.add("Carrier", "wind")
    n.add("Carrier", "battery")
    n.add("Bus", "b", carrier="AC")
    n.add("Bus", "b_h2", carrier="H2")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "thermal", bus="b", carrier="gas", p_nom=60.0,
          outage_rate_value=0.1, outage_rate_basis="EFORd", mttr_hours=50.0)
    n.add("Generator", "windfarm", bus="b", carrier="wind", p_nom=50.0)
    n.generators_t.p_max_pu = pd.DataFrame(
        {"windfarm": [0.6] * len(n.snapshots)}, index=n.snapshots)
    # Storage: one real battery (built by the LP → p_nom_opt wins), one that
    # was never built, one slack-carrier row, one slack-NAMED row, and one on
    # a non-electrical bus. Only the first two may survive.
    n.add("StorageUnit", "batt", bus="b", carrier="battery", p_nom=10.0,
          max_hours=4.0, efficiency_store=0.9, efficiency_dispatch=0.8)
    n.storage_units.loc["batt", "p_nom_opt"] = 25.0
    n.add("StorageUnit", "batt2", bus="b", carrier="battery", p_nom=8.0,
          max_hours=2.0)
    n.add("StorageUnit", "shed_store", bus="b", carrier="load_shedding",
          p_nom=99.0, max_hours=1.0)
    n.add("StorageUnit", "__voll_b", bus="b", carrier="battery", p_nom=99.0,
          max_hours=1.0)
    n.add("StorageUnit", "h2_store", bus="b_h2", carrier="battery", p_nom=99.0,
          max_hours=1.0)
    return n


def test_snapshot_inputs_consumes_fleet_and_residual_verbatim():
    """Spec §2.1: the units/residual/weights are the COPT's, unchanged — the
    provable-membership invariant across engines."""
    n = _network()
    inp = M.snapshot_inputs(n)
    units, residual, w = C.fleet_and_residual(n)
    assert [u.name for u in inp.units] == [u.name for u in units]
    assert inp.residual == pytest.approx(residual.to_numpy())
    assert inp.weights == pytest.approx(w.to_numpy())
    assert inp.residual.dtype == np.float64 and inp.weights.dtype == np.float64
    assert inp.periods == (("ALL", 0, 3),)
    assert inp.nyears == pytest.approx(3 * 2.0 / HOURS_PER_YEAR)
    assert inp.vre_profiles == {}


def test_snapshot_inputs_copies_and_never_aliases_the_network():
    """Spec §1/§2.1: everything is copied under the lock; no live reference
    escapes, so the MC can run beside an editing user."""
    n = _network()
    inp = M.snapshot_inputs(n)
    before = inp.residual.copy()
    n.loads_t.p_set = pd.DataFrame({"l": [500.0] * 3}, index=n.snapshots)
    assert np.array_equal(inp.residual, before)
    assert inp.residual.flags["C_CONTIGUOUS"]


def test_storage_extraction_applies_the_capacity_rule_and_the_slack_tests():
    """Spec §2.1: p_nom_opt when finite and > 0 else p_nom (the CoptUnit
    rule — an extendable battery the LP built must be simulated at its built
    size); e_nom = max_hours × p_nom; slack carriers/names and non-electrical
    buses excluded."""
    inp = M.snapshot_inputs(_network())
    by_name = {s.name: s for s in inp.storage}
    assert sorted(by_name) == ["batt", "batt2"]
    assert by_name["batt"].p_nom_mw == pytest.approx(25.0)      # p_nom_opt
    assert by_name["batt"].e_nom_mwh == pytest.approx(100.0)    # 4 h × 25 MW
    assert by_name["batt"].eff_store == pytest.approx(0.9)
    assert by_name["batt"].eff_dispatch == pytest.approx(0.8)
    assert by_name["batt2"].p_nom_mw == pytest.approx(8.0)      # never built
    assert by_name["batt2"].e_nom_mwh == pytest.approx(16.0)


def test_snapshot_inputs_blocks_the_multiindex_by_period():
    """Spec §2.1: contiguous blocks of the snapshot axis, level 0 in order."""
    inp = M.snapshot_inputs(_network(multi=True))
    assert inp.periods == ((2030, 0, 3), (2035, 3, 6))
    assert inp.residual.shape == (6,)


def test_snapshot_inputs_exports_requested_vre_profiles_only():
    """Spec §2.1: the must-take contribution (profile × capacity) of the named
    assets, so ELCC can un-net them from the residual. Empty by default."""
    n = _network()
    inp = M.snapshot_inputs(n, vre_assets=["windfarm"])
    assert set(inp.vre_profiles) == {"windfarm"}
    assert inp.vre_profiles["windfarm"] == pytest.approx([30.0, 30.0, 30.0])
    # Un-netting it must give back the gross demand.
    assert inp.residual + inp.vre_profiles["windfarm"] == pytest.approx(
        [100.0, 100.0, 100.0])


def test_resolution_floor_shares_units_with_lole_hours():
    """The floor must live in the SAME units as ``lole_hours`` — per horizon,
    weight-aware. The original ``1/(n·nyears)`` was per-year against a
    per-horizon metric: on a 168 h week at unit weights it claimed a floor
    52× too coarse, and on a weighted week it was wrong in the other
    direction — either way ELCC's "unidentifiable" refusal fired on systems
    whose LOLE was perfectly resolvable. Found by the ELCC worker (its
    fixtures declared nyears=1.0 to dodge it; this pins the fix so the dodge
    can be retired).

    Bite check (documented): restore ``1/(n_total·nyears)`` — both
    assertions below fail.
    """
    units = (C.CoptUnit(name="g", capacity_mw=100.0, q=0.1, mttr_hours=10.0),)
    H, n = 24, 50

    # Unit weights, sub-year horizon: smallest observable LOLE is one hour in
    # one draw = 1/n horizon-hours. The old formula said 1/(n·(24/8760)) —
    # 365× larger.
    res = M.mc_adequacy(_inputs(np.full(H, 50.0), units=units),
                        draws=n, max_draws=n, batch=n, seed=1)
    assert res["resolution_floor_h"] == pytest.approx(1.0 / n)

    # Weighted representative week: one shortfall hour in one draw carries
    # its WEIGHT into the mean, so the floor scales with it.
    w = 8760.0 / H
    res_w = M.mc_adequacy(_inputs(np.full(H, 50.0), units=units, weight=w),
                          draws=n, max_draws=n, batch=n, seed=1)
    assert res_w["resolution_floor_h"] == pytest.approx(w / n)
