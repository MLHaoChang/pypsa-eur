"""
COPT: a negative availability is never treated as a silent unit, and never
reaches the mixture in the first place.

Two defects, one root. `mixture_hourly` freezes a "silent" unit in the UP
state and enumerates one state instead of two. That is exact only when the
unit's availability is identically zero — both states then contribute the
same capacity, so the pair collapses by the law of total probability. The
shipped test was `avail.max() <= 0.0`, which also matches an ALL-NEGATIVE
availability: that unit is not capacity-neutral, and freezing it applies its
negative contribution with probability 1 instead of `1 − q`.

The negative availability gets there because `_occurrence_profile` does not
clip, while the must-take branch does — the asymmetry the IEEE 39-bus review
(F6) closed on one branch only.

The reference here is a brute-force enumeration of the full `2^k` state
space with no freezing at all: the definition `mixture_hourly` optimises,
computed the slow way.
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import pytest

from services.adequacy import copt as C


def _brute_force(dist, residual, mixed):
    """`(LOLP_h, EUE_h)` by enumerating every state of every unit.

    No `fixed_up`, no silent-unit collapse — the plain definition:
        LOLP_h = Σ_s P[s] · (1 − S(r_h − Σ_i s_i·a_{i,h}))
    """
    r = np.asarray(residual, dtype=np.float64)
    H = r.shape[0]
    avail = np.stack([C._availability_mw(u, H) for u in mixed])
    qs = np.array([float(u.q) for u in mixed], dtype=np.float64)
    lolp = np.zeros(H)
    eue = np.zeros(H)
    for bits in itertools.product((0, 1), repeat=len(mixed)):
        prob = 1.0
        for i, b in enumerate(bits):
            prob *= (1.0 - qs[i]) if b else qs[i]
        if prob <= 0.0:
            continue
        s = np.array(bits, dtype=np.float64)
        x = r - (s[:, None] * avail).sum(axis=0)
        lolp += prob * (1.0 - dist.survival_vec(x))
        eue += prob * dist.expected_shortfall_vec(x)
    return lolp, eue


def _base_dist():
    return C.build_copt([C.CoptUnit(name="base", capacity_mw=50.0, q=0.1)],
                        delta_mw=1.0)


RESIDUAL = [40.0, 60.0, 25.0]


def test_all_negative_availability_is_still_enumerated_over_both_states():
    # avail = -0.5 × 40 = -20 MW every hour. Not capacity-neutral, so the
    # unit must keep both of its states.
    unit = C.CoptUnit(name="neg", capacity_mw=40.0, q=0.2,
                      profile=np.array([-0.5, -0.5, -0.5]))
    dist = _base_dist()
    got_lolp, got_eue = C.mixture_hourly(dist, RESIDUAL, [unit])
    want_lolp, want_eue = _brute_force(dist, RESIDUAL, [unit])
    assert got_lolp == pytest.approx(want_lolp, abs=1e-14)
    assert got_eue == pytest.approx(want_eue, abs=1e-14)


def test_mixed_sign_availability_is_still_enumerated_over_both_states():
    # `max() <= 0` also catches a profile that is negative in some hours and
    # zero in the rest — the shape a clipped-at-zero column would have had.
    unit = C.CoptUnit(name="mixed", capacity_mw=40.0, q=0.2,
                      profile=np.array([-0.5, 0.0, -0.25]))
    dist = _base_dist()
    got_lolp, got_eue = C.mixture_hourly(dist, RESIDUAL, [unit])
    want_lolp, want_eue = _brute_force(dist, RESIDUAL, [unit])
    assert got_lolp == pytest.approx(want_lolp, abs=1e-14)
    assert got_eue == pytest.approx(want_eue, abs=1e-14)


def test_truly_silent_unit_is_still_collapsed_and_still_exact():
    # The control: an identically-zero availability MUST keep collapsing —
    # that is the optimisation the fix has to preserve, not remove.
    unit = C.CoptUnit(name="silent", capacity_mw=40.0, q=0.2,
                      profile=np.zeros(3))
    dist = _base_dist()
    got_lolp, got_eue = C.mixture_hourly(dist, RESIDUAL, [unit])
    want_lolp, want_eue = _brute_force(dist, RESIDUAL, [unit])
    assert got_lolp == pytest.approx(want_lolp, abs=1e-14)
    assert got_eue == pytest.approx(want_eue, abs=1e-14)


def test_negative_p_max_pu_never_becomes_a_negative_profile():
    # F6 on the occurrence-bearing branch: the must-take branch clips at 0,
    # so this one must too, or the mixture is handed an availability that
    # means "this farm consumes".
    snapshots = pd.date_range("2030-01-01", periods=3, freq="h")
    p_max_pu_t = pd.DataFrame({"wind": [-0.5, 0.8, 0.2]}, index=snapshots)
    prof = C._occurrence_profile(p_max_pu_t, "wind", snapshots)
    assert prof is not None
    assert prof.min() >= 0.0, f"negative availability survived: {prof}"
    # The finite, non-negative hours are untouched.
    assert prof[1] == pytest.approx(0.8)
    assert prof[2] == pytest.approx(0.2)
