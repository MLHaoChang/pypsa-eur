"""
ELCC — effective load-carrying capability by bisection at constant LOLE
(Phase 6 Task 4).

Contract: docs/superpowers/specs/2026-08-28-sequential-mc-engine-spec.md §3 and
the T-elcc block of §6, INCLUDING both **[v1.1]** amendments (fixed-draw common
random numbers for every candidate evaluation; ``kind="vre"`` rejected for a
name that is already an occurrence-bearing unit); plan
docs/superpowers/plans/2026-08-28-fmea-phase6-sequential-mc.md Task 4.

**Fixture style (documented choice).** Every fixture here is a hand-built
``MCInputs`` rather than a solved ``pypsa.Network`` put through
``snapshot_inputs``. Reasons, in order of weight:

1. ELCC is a statement about a *predicate over LOLE*, and the only way to
   assert it sharply is to know the answer in closed form. Every fixture below
   has its ELCC computed by hand in the docstring from the shed-hour counts;
   that is impossible through a network whose residual is the output of
   ``fleet_and_residual``.
2. The snapshot path is already pinned test-for-test in
   ``test_adequacy_mc.py`` (units/residual/weights verbatim, storage capacity
   rule, and — the one this module consumes —
   ``test_snapshot_inputs_exports_requested_vre_profiles_only``, which asserts
   ``residual + vre_profiles[name]`` gives back the gross demand). Re-deriving
   it here would test that module twice and this one not at all.

**The horizon-length knob.** Several fixtures declare ``nyears=1.0`` on a
handful of modelled hours. That is not a claim about the calendar: the
resolution floor is ``1/(n_samples·nyears)`` (spec §2.5) and is compared
against a LOLE reported per HORIZON, so a 10-hour horizon at its true
``nyears`` would put the floor above every hand-placed shed hour and every
fixture would refuse as ``unidentifiable``. Declaring one year makes the floor
``1/n`` and lets the shed-hour arithmetic — which is what these tests are
about — be read directly. (That units mismatch inside ``mc_adequacy`` is
recorded as a finding by this task; it is not this module's to fix.)

**Stochastic assertions.** Only two tests here are stochastic (the perfect-unit
CRN test and the fixed-draw-discipline test); both are seeded, and the
perfect-unit assertion was spot-checked on three seeds (4, 5, 6) at
implementation time. Everything else is a q = 0 fleet, so every draw is
identical and the assertions are exact hand arithmetic.

Every ★ test names, in its docstring, the broken variant of ``elcc.py`` it is
required to FAIL against (spec §6). Those bite checks are run by hand at
implementation time; the docstring is the record of what was checked.
"""
from __future__ import annotations

import numpy as np
import pytest

from services.adequacy import copt as C
from services.adequacy import elcc as E
from services.adequacy import mc as M
from services.adequacy.metrics import HOURS_PER_YEAR

# ── fixtures / helpers ────────────────────────────────────────────────────


def _inputs(residual, *, units=(), storage=(), weight=1.0, periods=None,
            nyears=None, vre_profiles=None) -> "M.MCInputs":
    """An MCInputs built by hand (same helper as ``test_adequacy_mc.py`` — the
    two files are deliberately independent, per the one-file-per-worker rule)."""
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


# ── the perfect-unit fixture (the CRN fixture) ────────────────────────────
#
# A q = 0 unit is worth EXACTLY its capacity in firm MW: removing it and
# adding back its nameplate leaves the post-firm capacity identical in every
# hour of every draw. Under common random numbers that identity is exact, so
# the ELCC must come back at the nameplate to within the bisection tolerance —
# which is what makes this the sharpest possible CRN probe (spec §6 T-elcc).
#
# The fixture is built so the answer is not merely "close to 100" by accident:
#
#   * fleet = one perfect 100 MW unit + two stochastic units (60 MW q=0.2,
#     40 MW q=0.1), so the available-capacity levels WITH the perfect unit are
#     {100, 140, 160, 200} MW and the baseline sheds in ~24 % of hours;
#   * the residual is a fine ramp from 155 to 165 MW over 168 hours (step
#     0.06 MW) that CROSSES the 160 MW level, so the smallest strictly
#     positive supply margin anywhere in the sample is ≈ 0.03 MW.
#
# That last number is the whole design: ELCC(Δ) = smallest Δ restoring the
# baseline = nameplate − (smallest positive margin) = 100 − 0.03. With an
# integer load and integer capacities the smallest margin would be 1 MW and the
# true answer would be 99 — outside the 0.5 MW tolerance, and the test would be
# asserting the fixture's arithmetic rather than the engine's CRN.
PERFECT = C.CoptUnit(name="perfect", capacity_mw=100.0, q=0.0,
                     basis="FOR", mttr_hours=10.0)
STOCH = (
    C.CoptUnit(name="a", capacity_mw=60.0, q=0.20, basis="FOR", mttr_hours=20.0),
    C.CoptUnit(name="b", capacity_mw=40.0, q=0.10, basis="FOR", mttr_hours=10.0),
)
CRN_UNITS = (PERFECT,) + STOCH
CRN_RESIDUAL = np.linspace(155.0, 165.0, 168)


def _crn_inputs():
    return _inputs(CRN_RESIDUAL, units=CRN_UNITS)


# ── ★ CRN: a perfect unit is worth exactly its nameplate ──────────────────

def test_a_perfect_units_elcc_is_its_capacity_under_common_random_numbers():
    """★ T-elcc (clause 1), spec §3 + §6. A q = 0 unit's ELCC equals its
    capacity to within the bisection tolerance — the load-bearing consequence
    of every evaluation sharing one set of draws.

    Why this bites: ``LOLE_reduced(Δ) = LOLE_baseline`` holds EXACTLY at
    Δ = nameplate only because the same sampled outage paths are behind both
    numbers. Re-seed per evaluation and LOLE_reduced(Δ) becomes
    baseline ± 1.96·sem instead of a monotone step function; the bisection then
    solves a noisy equation whose slope here is ≈ 1.3 h/MW against a standard
    error of ≈ 3 h, i.e. a Δ error of several MW.

    BROKEN VARIANT (bite): derive a fresh seed per candidate evaluation
    (``seed=int(seed) + int(round(delta_mw * 1000))`` in the evaluation
    closure) — the answer becomes noise (and half the time the nameplate probe
    itself lands above the baseline and the row degrades to ``not_bracketed``).
    """
    row = E.elcc_for_asset(_crn_inputs(), "generator", "perfect",
                           seed=4, draws=200)
    assert row["status"] == "ok", row
    assert row["nameplate_mw"] == pytest.approx(100.0)
    tol = E.default_tol_mw(100.0)
    assert tol == pytest.approx(0.5)
    # Bracketed above by the nameplate; below by the smallest positive margin.
    assert row["elcc_mw"] <= 100.0
    assert abs(row["elcc_mw"] - 100.0) <= tol, row["elcc_mw"]
    assert row["elcc_share"] == pytest.approx(row["elcc_mw"] / 100.0)
    # The baseline this was solved against is a real, resolvable LOLE.
    assert row["baseline_lole_h"] > 0.0
    lo, hi = row["baseline_lole_ci"]
    assert lo <= row["baseline_lole_h"] <= hi


def test_a_full_nameplate_firm_block_dominates_the_asset_on_identical_draws():
    """The structural fact behind the test above — and behind this task's
    finding that ``not_bracketed`` is unreachable for the three v1 kinds.

    At Δ = nameplate the reduced system's post-firm capacity is ≥ the
    baseline's in EVERY hour of EVERY draw (a two-state unit contributes
    ``c·state ≤ c``; a store delivers ``≤ p_nom`` and only ever charges out of
    surplus; a must-take profile contributes ``≤ max(profile)``). On identical
    draws that makes ``LOLE_reduced(nameplate) ≤ LOLE_baseline`` a theorem, not
    a hope — so a nameplate probe that FAILS is evidence the draws diverged.
    """
    inp = _crn_inputs()
    base = M.mc_adequacy(inp, draws=200, seed=4)
    n = base["n_samples"]
    at_nameplate = M.mc_adequacy(inp, draws=200, seed=4, cov_target=-1.0,
                                 max_draws=n, exclude=frozenset({0}),
                                 extra_firm_mw=100.0)
    assert at_nameplate["n_samples"] == n
    # Identical in every hour of every draw ⇒ identical means, bit for bit.
    assert at_nameplate["lole_hours"] == base["lole_hours"]
    assert at_nameplate["eue_mwh"] == base["eue_mwh"]


def test_every_candidate_evaluation_runs_at_the_baselines_draw_count():
    """★ [v1.1] fixed-draw discipline (spec §3). The baseline may use the
    adaptive path; EVERY candidate evaluation must then run at the baseline's
    FINAL ``n_samples`` with the same seed. Two candidate evaluations that stop
    at different ``n_samples`` draw from different sets, and ``LOLE_reduced(Δ)``
    stops being the monotone step function the predicate assumes.

    The baseline here is forced to adapt (``cov_target=1e-9`` on a stochastic
    fleet, ``batch=100``, ``max_draws=400``) so ``n_samples`` (400) differs from
    ``draws`` (100) — otherwise the discipline would be satisfied by accident.

    BROKEN VARIANT (bite): let candidates keep the adaptive settings (drop the
    fixed ``max_draws=n_samples`` / never-converge pinning and pass
    ``cov_target``/``max_draws`` straight through) — candidate evaluations then
    stop at whatever draw count their own CoV reaches.
    """
    seen: list[dict] = []
    real = E.mc_adequacy

    def counting(inputs, **kw):
        out = real(inputs, **kw)
        seen.append({"n": out["n_samples"], "kw": kw})
        return out

    E.mc_adequacy = counting
    try:
        row = E.elcc_for_asset(_crn_inputs(), "generator", "perfect",
                               seed=4, draws=100, cov_target=1e-9,
                               max_draws=400, batch=100)
    finally:
        E.mc_adequacy = real

    assert row["status"] == "ok", row
    baseline, *candidates = seen
    assert baseline["n"] == 400, baseline          # the baseline DID adapt
    assert len(candidates) >= 8, len(candidates)   # bracket probes + bisection
    for call in candidates:
        assert call["n"] == baseline["n"], call
        assert call["kw"]["seed"] == 4, call       # same seed, every time
        assert call["kw"]["max_draws"] == baseline["n"], call


# ── ★ declining credit and non-additivity (one wind fixture) ──────────────
#
# Twenty modelled hours in two halves that share the same load ladder
# L = 160 + 4k MW (k = 0..9) but not the same wind:
#
#   * CALM hours 0–9  : each farm contributes  10 MW (profile 0.1 × 100 MW)
#   * WINDY hours 10–19: each farm contributes 100 MW (profile 1.0 × 100 MW)
#
# One perfectly reliable 150 MW firm unit. Two identical 100 MW farms, both
# netted out of the residual as must-take (so ``vre_profiles`` carries the
# contribution ``profile × capacity`` that ELCC un-nets — spec §2.1/§3).
#
# The ladder is the point: shed-hour COUNT responds to a firm block one rung at
# a time, so the credit is a real number rather than an all-or-nothing jump,
# and the marginal farm's credit is set by its output in the hours that are
# actually short (the calm ones), not by its nameplate.
_LADDER = np.array([160.0 + 4.0 * k for k in range(10)])
_CALM_MW, _WINDY_MW = 10.0, 100.0
WIND_PROFILE = np.concatenate([np.full(10, _CALM_MW), np.full(10, _WINDY_MW)])
GROSS_LOAD = np.concatenate([_LADDER, _LADDER])
FIRM150 = (C.CoptUnit(name="firm", capacity_mw=150.0, q=0.0, basis="FOR",
                      mttr_hours=10.0),)


def _wind_inputs(n_farms: int):
    """``n_farms`` identical must-take farms netted out of the residual."""
    names = [f"w{i + 1}" for i in range(n_farms)]
    residual = GROSS_LOAD - n_farms * WIND_PROFILE
    return _inputs(residual, units=FIRM150, nyears=1.0,
                   vre_profiles={nm: WIND_PROFILE.copy() for nm in names})


def test_the_second_wind_tranche_earns_less_than_the_first():
    """★ T-elcc (clause 2): declining credit. The same 100 MW farm is worth
    30 MW as the first tranche on the system and 8 MW as the second.

    ONE-FARM SYSTEM (residual = L − W). Baseline sheds where L − W > 150:
    calm hours give 150 + 4k > 150 ⇒ k = 1..9 → 9 shed hours (windy hours sit
    at 60 + 4k, never short). Remove the farm (residual += W ⇒ gross L) and the
    shed count becomes 2·#{k : 160 + 4k > 150 + Δ}; ≤ 9 needs #{…} ≤ 4, i.e.
    the six lowest rungs cleared: Δ ≥ 30. **ELCC = 30 MW.**

    TWO-FARM SYSTEM (residual = L − 2W). Baseline sheds where L − 2W > 150:
    calm 140 + 4k > 150 ⇒ k = 3..9 → 7 shed hours. Remove ONE farm
    (residual += W) and the count is #{k : 150 + 4k > 150 + Δ}; ≤ 7 needs
    k = 1, 2 cleared: Δ ≥ 8. **ELCC = 8 MW.**

    30 > 8 is the declining-credit claim, and it is declining for the physical
    reason the panel copy states: the hours that remain short after the first
    farm is built are the CALM ones, where the second farm delivers 10 MW.

    BROKEN VARIANT (bite): un-net the profile with the wrong sign
    (``residual − profile`` instead of ``residual + profile``) — removal then
    ADDS supply, every candidate LOLE sits at or below the baseline, and both
    tranches come back at 0 MW (no ordering, no declining credit).
    """
    first = E.elcc_for_asset(_wind_inputs(1), "vre", "w1", seed=0, draws=8)
    second = E.elcc_for_asset(_wind_inputs(2), "vre", "w2", seed=0, draws=8)

    for row in (first, second):
        assert row["status"] == "ok", row
        assert row["nameplate_mw"] == pytest.approx(100.0)   # peak must-take
    assert first["baseline_lole_h"] == pytest.approx(9.0)
    assert second["baseline_lole_h"] == pytest.approx(7.0)

    tol = E.default_tol_mw(100.0)
    assert abs(first["elcc_mw"] - 30.0) <= tol, first
    assert abs(second["elcc_mw"] - 8.0) <= tol, second
    assert second["elcc_mw"] < first["elcc_mw"]
    assert second["elcc_share"] < 0.15 < 0.25 < first["elcc_share"]

    # The two farms are identical, so the marginal credit must not depend on
    # WHICH one is called the second tranche (a symmetry the bisection would
    # break if it leaked state between evaluations).
    other = E.elcc_for_asset(_wind_inputs(2), "vre", "w1", seed=0, draws=8)
    assert other["elcc_mw"] == pytest.approx(second["elcc_mw"])


def test_portfolio_credit_is_not_the_sum_of_marginal_credits():
    """★ T-elcc (clause 5): non-additivity, asserted so the UI copy's claim is
    itself tested (plan Task 4).

    Same two-farm system. Removing BOTH farms (residual += 2W ⇒ gross L) leaves
    2·#{k : 160 + 4k > 150 + Δ} shed hours; ≤ 7 needs #{…} ≤ 3, i.e. the seven
    lowest rungs cleared: Δ ≥ 34. **Portfolio ELCC = 34 MW**, against a sum of
    last-in credits of 8 + 8 = 16 MW.

    The direction matters and is the interesting half: a sum of MARGINAL
    (last-in) credits UNDERSTATES the portfolio, because each marginal
    evaluation charges the asset for standing behind the other one. Reporting
    ``Σ elcc_mw`` as "the portfolio's firm capacity" is therefore wrong by more
    than a factor of two on this fixture.

    BROKEN VARIANT (bite): make the reduced system share the baseline's
    residual (ignore the ``reduced`` argument in ``elcc_of_removal``) — the
    portfolio removal then removes nothing, its ELCC collapses to 0, and the
    inequality flips.
    """
    inp = _wind_inputs(2)
    marginal = [E.elcc_for_asset(inp, "vre", nm, seed=0, draws=8)["elcc_mw"]
                for nm in ("w1", "w2")]

    from dataclasses import replace
    portfolio = E.elcc_of_removal(
        inp,
        reduced=replace(inp, residual=inp.residual + 2.0 * WIND_PROFILE),
        nameplate_mw=200.0, seed=0, draws=8)

    assert portfolio["status"] == "ok", portfolio
    assert abs(portfolio["elcc_mw"] - 34.0) <= E.default_tol_mw(200.0)
    assert sum(marginal) == pytest.approx(16.0, abs=1.0)
    # Non-additive, and by a lot: the sum understates by > 2×.
    assert portfolio["elcc_mw"] > 2.0 * sum(marginal)


# ── ★ battery credit: long event vs short events ─────────────────────────

BATT = M.StorageSpec(name="batt", p_nom_mw=50.0, e_nom_mwh=200.0,
                     eff_store=1.0, eff_dispatch=1.0)


def test_a_battery_on_a_long_event_earns_less_than_its_power_rating():
    """★ T-elcc (clause 3a): a 50 MW / 200 MWh battery on a ten-hour event
    earns 20 MW — strictly between zero and its power rating. This is the
    engine's reason to exist stated as a number: the COPT would credit the
    battery with 50 MW in every hour of the event.

    Fixture: ten consecutive hours of 20 MW deficit, no fleet, initial SoC 50 %
    (= 100 MWh) so the answer is about the asset, not a free initial cycle
    (plan review finding 5).
      baseline  : the battery serves 20 MW/h until its 100 MWh runs out after
                  five hours ⇒ 5 shed hours.
      removed+Δ : deficit 20 − Δ in all ten hours ⇒ 10 shed hours while
                  Δ < 20, none at Δ ≥ 20.
    Smallest Δ with LOLE ≤ 5 is therefore **20 MW** = 0.4 × p_nom.

    BROKEN VARIANT (bite): remove the storage from the REDUCED run only by
    disabling storage globally (``storage_enabled=False`` on both baseline and
    candidates) — the baseline then also loses the battery, baseline LOLE
    becomes 10, and the credit collapses to 0 MW.
    """
    inp = _inputs([20.0] * 10, storage=(BATT,), nyears=1.0)
    row = E.elcc_for_asset(inp, "storage_unit", "batt", seed=0, draws=4,
                           initial_soc_frac=0.5)

    assert row["status"] == "ok", row
    assert row["nameplate_mw"] == pytest.approx(50.0)
    assert row["baseline_lole_h"] == pytest.approx(5.0)
    assert 0.0 < row["elcc_mw"] < 50.0, row
    assert abs(row["elcc_mw"] - 20.0) <= E.default_tol_mw(50.0), row
    assert row["elcc_share"] < 0.5


def test_a_battery_on_short_events_earns_its_full_power_rating():
    """★ T-elcc (clause 3b): the same battery on events it CAN ride through
    earns its full 50 MW — run at initial SoC 50 %, per spec §6.

    Fixture (no fleet; the residual is the whole story):
      h0,h1   : +50 MW — served from the 100 MWh the battery starts with
      h2..h5  : −50 MW — surplus; the battery recharges to full (200 MWh)
      h6,h7   : +50 MW — served again
      h8,h9   : +300 MW — beyond anything on the table; the battery shaves
                50 MW and the hour is short either way
    ⇒ baseline LOLE = 2 h (h8, h9 — present in every candidate too, which is
    what stops the credit from being an artefact of an all-clear baseline).
      removed+Δ : h0,h1,h6,h7 short whenever Δ < 50, plus h8,h9 always
                  ⇒ LOLE = 4·[Δ < 50] + 2.
    Smallest Δ with LOLE ≤ 2 is exactly **50 MW = p_nom**: full credit, because
    every event is inside the battery's energy and it gets to recharge between
    them.

    BROKEN VARIANT (bite): use the ASSET's own rating as the firm block bound
    but bisect on ``[0, e_nom]`` instead of ``[0, nameplate]`` (spec §3's
    bracket) — the answer is unchanged here but ``elcc_share`` becomes 0.25 and
    the "full credit" claim silently disappears from the payload.
    """
    residual = [50.0, 50.0, -50.0, -50.0, -50.0, -50.0, 50.0, 50.0, 300.0,
                300.0]
    inp = _inputs(residual, storage=(BATT,), nyears=1.0)
    row = E.elcc_for_asset(inp, "storage_unit", "batt", seed=0, draws=4,
                           initial_soc_frac=0.5)

    assert row["status"] == "ok", row
    assert row["baseline_lole_h"] == pytest.approx(2.0)
    assert row["elcc_mw"] == pytest.approx(50.0)
    assert row["elcc_share"] == pytest.approx(1.0)


# ── ★ honest refusals ────────────────────────────────────────────────────

def test_a_shortfall_free_baseline_refuses_to_state_a_credit():
    """★ T-elcc (clause 4): baseline LOLE at or below the resolution floor →
    ``status="unidentifiable"`` with a reason, and NO number.

    Two systems, one refusal, because the refusal is about RESOLUTION and not
    about zero:

    a. **All clear.** Nothing ever sheds. There is nothing to hold constant:
       every Δ, including Δ = 0, satisfies "LOLE ≤ baseline", so a bisection
       would dutifully return 0 MW and a user would read "this unit is
       worthless" where the truth is "this system never failed in 50 draws".
    b. **At the floor.** A 100 MW unit at q = 1.2e-4 sheds exactly ONE hour
       across all 50 draws at this seed — LOLE = 0.02 h, equal to the
       per-horizon floor ``min positive weight / n`` (spec v1.2; the fixture
       was originally calibrated against the buggy per-year floor, which
       inflated 52× here and would have called a resolvable 0.3 h
       unidentifiable). One observed hour is the definition of
       indistinguishable-from-zero; pricing an asset against it prices noise.

    The floor is quoted in the reason so the refusal is actionable (raise the
    draw count).

    BROKEN VARIANT (bite): compare against a bare zero
    (``if lole_base <= 0.0``) instead of against ``resolution_floor_h`` — case
    (a) still refuses (its LOLE is exactly zero) and case (b) sails through
    with a confident credit built out of noise.
    """
    all_clear = _inputs(
        [70.0] * 168,
        units=(C.CoptUnit(name="huge", capacity_mw=500.0, q=0.0, basis="FOR",
                          mttr_hours=10.0),))
    row = E.elcc_for_asset(all_clear, "generator", "huge", seed=0, draws=50)
    assert row["status"] == "unidentifiable"
    assert row["elcc_mw"] is None and row["elcc_share"] is None
    assert row["baseline_lole_h"] == 0.0
    assert row["reason"] and "floor" in row["reason"].lower()
    assert row["kind"] == "generator" and row["name"] == "huge"
    assert row["nameplate_mw"] == pytest.approx(500.0)

    # (b) positive, but under the floor at this draw count.
    rare = _inputs(
        [100.0] * 168,
        units=(C.CoptUnit(name="rare", capacity_mw=100.0, q=1.2e-4, basis="FOR",
                          mttr_hours=1.0),))
    floor = 1.0 / 50                      # min positive weight (1.0) / n draws
    # seed=1 pinned: exactly one shortfall hour in 50 draws (seed-hunted, so
    # the assertion is deterministic, not statistical).
    thin = E.elcc_for_asset(rare, "generator", "rare", seed=1, draws=50,
                            max_draws=50)
    assert 0.0 < thin["baseline_lole_h"] <= floor, (thin, floor)
    assert thin["status"] == "unidentifiable", thin
    assert thin["elcc_mw"] is None and thin["elcc_share"] is None


def test_a_bracket_that_cannot_restore_the_baseline_is_reported_not_bracketed():
    """★ T-elcc: ``status="not_bracketed"`` when even the top of the bracket
    leaves LOLE above the baseline — never extrapolate past nameplate
    (spec §3: "exceedance rejected in v1").

    **FINDING, recorded here because the test has to reach the branch
    artificially.** Under the [v1.1] fixed-draw CRN discipline this status is
    UNREACHABLE for all three v1 kinds: at Δ = nameplate the reduced system
    dominates the baseline hour by hour on identical draws (see
    ``test_a_full_nameplate_firm_block_dominates_the_asset_on_identical_draws``),
    so the nameplate probe always succeeds. The guard is therefore a CRN
    tripwire, not an expected outcome — which is exactly why it must keep
    working. This test reaches it through ``elcc_of_removal`` with a
    deliberately truncated bracket (40 MW of headroom for a 100 MW unit), the
    same code path a future kind — or a broken CRN — would take.

    BROKEN VARIANT (bite): extrapolate instead of refusing (return the
    nameplate with ``status="ok"`` when the top probe fails) — the row then
    reports 40 MW of credit for an asset the bracket never priced.
    """
    row = E.elcc_of_removal(_crn_inputs(), nameplate_mw=40.0,
                            exclude=frozenset({0}), seed=4, draws=200)
    assert row["status"] == "not_bracketed"
    assert row["elcc_mw"] is None and row["elcc_share"] is None
    assert row["nameplate_mw"] == pytest.approx(40.0)
    assert row["reason"] and "40" in row["reason"]
    assert row["baseline_lole_h"] > 0.0


def test_vre_is_rejected_for_an_occurrence_bearing_name():
    """★ [v1.1] (spec §3): ``kind="vre"`` on a name that is present in
    ``inputs.units`` is a 422, not an answer.

    An occurrence-bearing generator was never netted into the residual — it is
    sampled as a two-state unit. ``residual += profile`` would put its output
    into the load AND leave its unit in the fleet, double-counting the asset
    and handing back a credit near twice its capacity. The message names the
    unit so the route's 422 is actionable ("ask for it as kind=generator").

    BROKEN VARIANT (bite): drop the membership check — the call succeeds and
    returns a credit for an asset that is in the model twice.
    """
    units = (C.CoptUnit(name="windfarm", capacity_mw=100.0, q=0.05,
                        basis="FOR", mttr_hours=10.0),) + STOCH
    inp = _inputs(np.linspace(155.0, 165.0, 168), units=units,
                  vre_profiles={"windfarm": np.full(168, 40.0)})
    with pytest.raises(ValueError) as e:
        E.elcc_for_asset(inp, "vre", "windfarm", seed=0, draws=50)
    msg = str(e.value)
    assert "windfarm" in msg
    assert "generator" in msg.lower()
    # The same name IS answerable as a generator — the rejection is about the
    # kind, not about the asset.
    row = E.elcc_for_asset(inp, "generator", "windfarm", seed=0, draws=50)
    assert row["status"] in {"ok", "unidentifiable"}


def test_unknown_names_raise_keyerror_for_the_routes_404():
    """Spec §3: an unknown asset is a KeyError here and a 404 at the route —
    for every kind, so the route needs exactly one except clause."""
    inp = _crn_inputs()
    for kind in ("generator", "storage_unit", "vre"):
        with pytest.raises(KeyError):
            E.elcc_for_asset(inp, kind, "nope", seed=0, draws=20)
    with pytest.raises(ValueError):
        E.elcc_for_asset(inp, "line", "perfect", seed=0, draws=20)


# ── the row contract the endpoint serialises ─────────────────────────────

def test_the_row_shape_is_the_same_whatever_the_status():
    """Spec §3's row, plus ``reason`` (the panel renders status rows' reasons,
    spec §5). Every key present in every outcome — a payload whose shape
    depends on the answer is a payload the frontend has to branch on."""
    keys = {"kind", "name", "nameplate_mw", "elcc_mw", "elcc_share", "status",
            "reason", "baseline_lole_h", "baseline_lole_ci"}
    ok = E.elcc_for_asset(_crn_inputs(), "generator", "perfect", seed=4,
                          draws=100)
    refused = E.elcc_for_asset(
        _inputs([70.0] * 168,
                units=(C.CoptUnit(name="huge", capacity_mw=500.0, q=0.0,
                                  basis="FOR", mttr_hours=10.0),)),
        "generator", "huge", seed=0, draws=50)
    for row in (ok, refused):
        assert set(row) == keys, sorted(set(row) ^ keys)
        assert isinstance(row["baseline_lole_ci"], tuple)
        assert len(row["baseline_lole_ci"]) == 2
    assert ok["reason"] is None                    # nothing to explain
    assert refused["reason"] is not None
    assert E.MAX_ELCC_ASSETS == 10


def test_the_tolerance_default_scales_with_the_nameplate_and_has_a_floor():
    """Spec §3: ``tol_mw = max(0.5, 0.001·nameplate)`` — a relative tolerance
    so a 5 GW nuclear unit is not bisected to half a megawatt (≈ 13 extra
    evaluations of a study that costs seconds each), with an absolute floor so
    a 10 MW asset is not "resolved" to 10 kW it cannot support."""
    assert E.default_tol_mw(10.0) == pytest.approx(0.5)
    assert E.default_tol_mw(500.0) == pytest.approx(0.5)
    assert E.default_tol_mw(5000.0) == pytest.approx(5.0)
    # An explicit tolerance overrides, and a tighter one may only move the
    # answer toward the true smallest Δ (which is 20 MW on this fixture).
    inp = _inputs([20.0] * 10, storage=(BATT,), nyears=1.0)
    tight = E.elcc_for_asset(inp, "storage_unit", "batt", seed=0, draws=4,
                             tol_mw=0.01, initial_soc_frac=0.5)
    assert abs(tight["elcc_mw"] - 20.0) <= 0.01
    with pytest.raises(ValueError):
        E.elcc_for_asset(inp, "storage_unit", "batt", seed=0, draws=4,
                         tol_mw=0.0)


def test_an_asset_that_carries_no_credit_is_reported_as_exactly_zero():
    """A worthless asset must read 0.0, not a tolerance-sized crumb: the
    Δ = 0 probe is evaluated before the bisection so "this asset changes
    nothing" is reported as such rather than as "≈ 0.25 MW" (half a tolerance,
    which is what a bare bisection on [0, nameplate] would return).

    Fixture: a 50 MW store on a system whose only shortfall is a 300 MW hour it
    cannot dent... at the hour count level. Removing it changes no shed HOUR,
    so its LOLE-credit is genuinely zero — and that is a true statement about
    LOLE, not about the battery's worth in EUE.
    """
    inp = _inputs([300.0], storage=(BATT,), nyears=1.0)
    row = E.elcc_for_asset(inp, "storage_unit", "batt", seed=0, draws=4,
                           initial_soc_frac=0.5)
    assert row["status"] == "ok", row
    assert row["elcc_mw"] == 0.0
    assert row["elcc_share"] == 0.0
