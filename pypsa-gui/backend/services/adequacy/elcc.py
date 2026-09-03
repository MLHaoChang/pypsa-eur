"""
ELCC — effective load-carrying capability, by bisection at constant LOLE.

Design: docs/superpowers/specs/2026-08-28-sequential-mc-engine-spec.md §3
(including both **[v1.1]** amendments) and the T-elcc block of §6; plan
docs/superpowers/plans/2026-08-28-fmea-phase6-sequential-mc.md Task 4.

**The question.** "What is this battery worth in firm MW?" is the question the
COPT structurally cannot answer (no memory) and the LP proxy answers
optimistically (perfect foresight). ELCC is the honest form of it: the
LAST-IN CREDIT of a named existing asset — remove the asset, then find the
block of perfectly firm capacity that restores the system's baseline LOLE.
"Last-in" is not a detail: the credit is conditional on everything else that is
already built, which is why these numbers do not add up (see
``elcc_of_removal``'s note on non-additivity).

**Why the answer is a PREDICATE, not an equation.** On finitely many draws
``LOLE_reduced(Δ)`` is a monotone non-increasing STEP function of Δ: it moves
only when Δ crosses a level at which some (hour, draw)'s deficit changes sign.
Exact equality with the baseline is therefore ill-posed — generically no Δ
attains it. The spec's predicate is the smallest Δ with
``LOLE_reduced(Δ) ≤ LOLE_baseline``, and bisection on it converges to the step
edge, which is the answer a reliability engineer means by "restores the
baseline".

**Common random numbers are the load-bearing part, and they are fragile in
two different places.**

1. *At the sampler.* ``mc.sample_capacity`` keys every unit's RNG substream to
   its POSITION IN THE FULL FLEET and generates-and-discards an excluded
   unit's path, so removing an asset does not move any other asset's draws by
   a bit. That is what makes ``LOLE_reduced(Δ)`` monotone rather than
   monotone-plus-noise, and it is pinned by T-CRN in ``test_adequacy_mc.py``.
2. *At the aggregator.* **[v1.1]** ``mc.mc_adequacy``'s adaptive batching is
   CRN-HOSTILE: two evaluations that stop at different ``n_samples`` are
   averages over different sample sets, and the difference between them is
   noise of order ``1.96·sem`` — on the fixtures in the test module, several
   MW of Δ. So the baseline may adapt, and then EVERY candidate evaluation is
   pinned to the baseline's final ``n_samples`` at the same seed.

**Deviation from the letter of §3 [v1.1], recorded (spec > plan, and a
deviation needs a reason, not a silent fix).** The spec writes the pinned
evaluation as ``mc_adequacy(draws=N, max_draws=N, seed=seed, ...)`` — a SINGLE
batch of N. That couples the candidates to each other but NOT to the baseline
whenever the baseline needed more than one batch, because ``mc_adequacy``
derives batch k's seed by spawning the k-th child of ``SeedSequence(seed)``: a
single batch of 750 draws consumes child 0 only, while an adaptive baseline of
500 + 250 consumed children 0 and 1. The baseline LOLE is the RIGHT-HAND SIDE
of the predicate, so a baseline drawn from a different sample set reintroduces
exactly the noise the amendment exists to remove — and would, for instance,
make ``LOLE_reduced(nameplate) > LOLE_baseline`` (a spurious
``not_bracketed``) a coin flip. This module therefore REPLAYS the baseline's
batch sequence instead: same ``draws``, same ``batch``, ``max_draws = N``, and
a never-satisfiable ``cov_target`` so the run cannot stop early. That
reproduces the baseline's batch sizes and hence its children exactly, so the
candidates are bit-identical to the baseline AND to each other. When the
baseline converges in its first batch (the common case) the two formulations
are the same call. The spec's requirement — "every candidate evaluation runs
at the baseline's final n_samples with the same seed" — is met either way.

**What ``not_bracketed`` is.** With the above discipline it is a TRIPWIRE, not
an expected outcome: at Δ = nameplate the reduced system's post-firm capacity
dominates the baseline's in every hour of every draw (a two-state unit
contributes ``c·state ≤ c``; a store delivers at most ``p_nom`` and only ever
charges out of surplus, so its removal can never help; a must-take profile
contributes at most ``max(profile)``), so on identical draws the top probe
cannot fail. If it does fail, the draws diverged. The status is kept, and
tested, because the alternative — extrapolating past the nameplate — invents
credit the bracket never priced (spec §3: exceedance rejected in v1).

**For the endpoint (spec §§3–4).** Exceptions are the route's status codes:
``KeyError`` → 404 (unknown asset), ``ValueError`` → 422 (unknown kind, a
must-take request for an occurrence-bearing name, a bad tolerance).
``MAX_ELCC_ASSETS`` is exported for the route to enforce on the request body;
it is not enforced here because this function prices ONE asset. Every returned
row carries the same nine keys whatever the outcome — a payload whose shape
depends on the answer is a payload the panel has to branch on.
"""
from __future__ import annotations

import hashlib

import logging
import math
from dataclasses import replace

import numpy as np

from services.adequacy.mc import MAX_DRAWS, mc_adequacy

logger = logging.getLogger(__name__)

# Spec §3. A product cap on the study, enforced at the route: each asset costs
# a baseline plus ~10 full MC evaluations, so ten assets is already a hundred
# simulations of a job the user is watching a spinner for.
MAX_ELCC_ASSETS = 10

# A CoV target no run can satisfy: ``mc._cov`` returns 0.0 for a
# zero-mean/zero-spread batch and ``inf`` otherwise, and neither is ≤ −1. This
# is how a candidate evaluation is pinned to a draw count instead of a
# convergence criterion (see the module docstring on the [v1.1] amendment).
_NEVER_CONVERGE = -1.0

# Bisection on [0, nameplate] to a tolerance of at least 0.5 MW needs
# ceil(log2(nameplate/tol)) steps — 12 for a 2 GW asset at 0.5 MW. The guard is
# a loop-safety net, not a policy: it can only trip if tol is denormal.
_MAX_BISECTION_STEPS = 64

_KINDS = ("generator", "storage_unit", "vre")


def unit_nameplate_mw(u) -> float:
    """The capacity a firm block must bracket for one sampled unit: its
    nameplate, or for a profiled unit (Phase 12c-pre) its best hour
    ``max_h(profile_h) × cap`` — never a ``(1−q)``-derated figure."""
    prof = getattr(u, "profile", None)
    if prof is None:
        return float(u.capacity_mw)
    arr = np.asarray(prof, dtype=np.float64)
    finite = arr[np.isfinite(arr)]
    peak = float(finite.max()) if finite.size else 0.0
    nameplate = max(peak, 0.0) * float(u.capacity_mw)
    # The product, not only the peak, must be finite: a non-finite nameplate
    # is not JSON and brackets nothing (shipped-code review, finding 1).
    return nameplate if math.isfinite(nameplate) else 0.0


def elcc_candidates(n, cfg=None) -> list[dict]:
    """
    Every asset in ``n`` whose capacity credit this module can price, as
    ``[{kind, name, nameplate_mw}, …]`` sorted by nameplate DESCENDING with
    ties broken by name.

    **Agreement by construction is the entire contract.** This list is what a
    picker offers; the names come back as ``elcc_assets`` on
    ``POST /results/mc`` and every one of them must resolve. So membership is
    not re-derived here — it is READ OFF the very structures the run itself
    resolves against:

    * ``kind="generator"`` — the occurrence-bearing units of
      ``fleet_and_residual``, i.e. ``inputs.units``, which is exactly the list
      ``_resolve`` scans for a generator name. Nameplate = ``capacity_mw``.
    * ``kind="storage_unit"`` — the storage rows ``snapshot_inputs`` built,
      i.e. ``inputs.storage``, the list ``_resolve`` scans by name.
      Nameplate = ``p_nom_mw``.
    * ``kind="vre"`` — the must-take generators (``copt.must_take_generators``,
      the complement of the unit branch in the SAME membership walk), passed
      straight back into ``snapshot_inputs`` as ``vre_assets`` so the profile
      whose peak we report is bit-for-bit the profile ``_resolve`` un-nets.
      Nameplate = the PEAK must-take contribution over the horizon — the
      bracket top the bisection actually prices, not the installed capacity
      (spec §3 [v1.2]).

    A must-take generator whose peak contribution is ZERO is EXCLUDED: an
    all-zero profile contributes nothing to net out, so there is no credit to
    measure, and ``_resolve`` would hand the bisection a degenerate ``[0, 0]``
    bracket whose only possible answer is 0 MW. Offering it would spend a
    baseline plus a bracket probe to print a number that is a property of the
    missing profile, not of the asset.

    Cheap and read-only: one snapshot, no sampling. The caller holds the
    PyPSAService lock for the duration (same discipline as ``post_mc``).
    """
    from services.adequacy.copt import must_take_generators
    from services.adequacy.mc import snapshot_inputs

    must_take = must_take_generators(n)
    inputs = snapshot_inputs(n, vre_assets=must_take, cfg=cfg)

    # Phase 12c-pre: a profiled unit's nameplate for the bracket is its
    # best hour, max_h(a_{i,h}) — the firm block then dominates the unit
    # hour by hour and the dominance tripwire holds; a (1−q)-derated peak
    # would make `not_bracketed` reachable on the unit's best hour. A
    # zero-peak profile has nothing to price and is excluded, as the vre
    # branch below excludes it.
    rows: list[dict] = []
    for u in inputs.units:
        nameplate = unit_nameplate_mw(u)
        if nameplate <= 0.0 and getattr(u, "profile", None) is not None:
            continue
        rows.append({"kind": "generator", "name": str(u.name),
                     "nameplate_mw": nameplate})
    rows += [
        {"kind": "storage_unit", "name": str(s.name),
         "nameplate_mw": float(s.p_nom_mw)}
        for s in inputs.storage
    ]
    for name in must_take:
        profile = inputs.vre_profiles.get(name)
        if profile is None:               # renamed between the two reads
            continue
        peak = float(np.asarray(profile, dtype=np.float64).max(initial=0.0))
        if peak <= 0.0:
            continue                      # see the docstring: nothing to price
        rows.append({"kind": "vre", "name": str(name), "nameplate_mw": peak})

    # Descending nameplate puts the assets whose credit moves the answer at the
    # top of a list the user may only tick MAX_ELCC_ASSETS of; the name
    # tie-break makes the order stable across requests rather than an artefact
    # of the component frames' insertion order.
    rows.sort(key=lambda r: (-r["nameplate_mw"], r["name"]))
    return rows


def default_tol_mw(nameplate_mw) -> float:
    """``max(0.5, 0.001·nameplate)`` (spec §3).

    RELATIVE, so a 5 GW nuclear unit is not bisected to half a megawatt (each
    halving is a full MC evaluation; 0.1 % is ~10 steps, 0.5 MW would be ~13).
    With an ABSOLUTE FLOOR, so a 10 MW asset is not "resolved" to 10 kW — a
    precision the sampler's own resolution floor cannot support and which would
    read as false confidence in the payload.
    """
    nameplate = float(nameplate_mw)
    if not math.isfinite(nameplate):
        raise ValueError(f"nameplate {nameplate_mw!r} is not a capacity")
    return max(0.5, 0.001 * abs(nameplate))


def _row(*, nameplate, baseline, elcc_mw=None, status="ok", reason=None,
         period=None):
    """The row shape, in one place so every exit builds the same nine keys
    (spec §3's eight, plus ``reason`` — spec §5 renders it for status rows).
    With ``period`` the baseline LOLE is that period's; the interval stays
    the horizon's (the per-period split carries no interval of its own)."""
    share = None
    if elcc_mw is not None and nameplate > 0:
        share = float(elcc_mw) / float(nameplate)
    return {
        "nameplate_mw": float(nameplate),
        "elcc_mw": None if elcc_mw is None else float(elcc_mw),
        "elcc_share": share,
        "status": status,
        "reason": reason,
        "baseline_lole_h": _lole_of(baseline, period),
        "baseline_lole_ci": tuple(float(v) for v in baseline["lole_ci"]),
    }


def baseline_key(inputs, *, draws, seed, cov_target, max_draws, batch,
                 sim_kwargs=None) -> str:
    """
    sha256 over EVERYTHING the baseline evaluation depends on (Phase 12c,
    plan v3.1 A7): every unit (name, capacity, q, MTTR, profile bytes), every
    store, the residual and weight bytes, the period blocks, and the sampling
    parameters plus the simulation kwargs. A baseline injected into
    ``elcc_of_removal`` must carry the key its callee recomputes, or the
    replay that CRN rests on could silently run against a different sample
    set — the v2 review's MINOR 10, closed by construction rather than by
    trust. Content, never ``id()``.
    """
    h = hashlib.sha256()
    for u in inputs.units:
        prof = getattr(u, "profile", None)
        h.update(f"{u.name}\x1f{float(u.capacity_mw)!r}\x1f{float(u.q)!r}\x1f"
                 f"{float(u.mttr_hours)!r}\x1e".encode())
        h.update(b"" if prof is None else np.asarray(prof, dtype=np.float64).tobytes())
        h.update(b"\x1e")
    h.update(b"\x1d")
    for st in inputs.storage:
        h.update(f"{st.name}\x1f{float(st.p_nom_mw)!r}\x1f{float(st.e_nom_mwh)!r}\x1f"
                 f"{float(st.eff_store)!r}\x1f{float(st.eff_dispatch)!r}\x1e".encode())
    h.update(b"\x1d")
    h.update(np.ascontiguousarray(inputs.residual, dtype=np.float64).tobytes())
    h.update(np.ascontiguousarray(inputs.weights, dtype=np.float64).tobytes())
    h.update(repr(tuple(inputs.periods)).encode())
    h.update(f"\x1d{int(draws)}\x1f{seed!r}\x1f{float(cov_target)!r}\x1f"
             f"{int(max_draws)}\x1f{int(batch)}".encode())
    for k in sorted(sim_kwargs or {}):
        v = (sim_kwargs or {})[k]
        v = sorted(v) if isinstance(v, (set, frozenset)) else v
        h.update(f"\x1f{k}={v!r}".encode())
    return h.hexdigest()


# `elcc_of_removal` takes a kwarg named `baseline_key`, which would shadow
# the function inside its body; the body uses this alias.
baseline_key_for = baseline_key


def _period_slice(inputs, period):
    """``(start, end)`` of the period block labelled ``period`` in
    ``inputs.periods``; ``KeyError`` when there is no such block."""
    for label, start, end in inputs.periods:
        if label == period or str(label) == str(period):
            return int(start), int(end)
    raise KeyError(f"no period {period!r} in the snapshot "
                   f"(periods: {[b[0] for b in inputs.periods]})")


def _lole_of(metrics: dict, period) -> float:
    """The LOLE the predicate compares: the horizon's, or one period's
    (Phase 12c §3.2 — the margin is enforced per period, and a horizon-wide
    credit beside a per-period proxy is the Phase-4 mistake)."""
    if period is None:
        return float(metrics["lole_hours"])
    by = metrics.get("by_period") or {}
    if period in by:
        return float(by[period]["lole_hours"])
    for k, v in by.items():
        if str(k) == str(period):
            return float(v["lole_hours"])
    raise KeyError(f"no period {period!r} in the MC payload ({list(by)})")


def elcc_of_removal(inputs, *, nameplate_mw, seed, draws, reduced=None,
                    exclude=frozenset(), exclude_storage=frozenset(),
                    tol_mw=None, cov_target: float = 0.05,
                    max_draws: int = MAX_DRAWS, batch: int = 250,
                    period=None, baseline=None, baseline_key=None,
                    _zero_probe=None, **sim_kwargs) -> dict:
    """
    The credit of an arbitrary REMOVAL, expressed as firm MW (spec §3).

    ``inputs`` is the system as built (the baseline); ``reduced`` is the
    residual the system faces once the asset is gone (defaults to ``inputs``;
    only must-take VRE needs a different one, because un-netting its profile
    changes the residual rather than the fleet). ``exclude`` /
    ``exclude_storage`` are ``mc.simulate``'s removal semantics, forwarded
    unchanged.

    This is the entry point for anything that is not a single named asset —
    notably a PORTFOLIO removal, which is what makes ELCC's non-additivity
    testable. The SIGN of that non-additivity is not a law. On fixtures whose
    members share peak hours the sum of last-in credits UNDERSTATES the
    portfolio, because each marginal evaluation charges the asset for
    standing behind the others. It OVERSTATES when members do not overlap —
    by up to k× for k such members, since each marginal is bracketed by its
    own peak (``max_h(profile_i)``) while the group is capped at the most it
    can deliver at once (``max_h(Σ profile_i)``) — and the effect GROWS as
    the fleet loosens. Measured: two 100 MW farms, A available hours 0–9 and
    B hours 10–19, flat 250 MW load, five 30 MW units at q = 0.15 (seed 0,
    draws 64): marginals 60.16 + 60.16 = 120.31 against a portfolio of
    100.00; with six units instead of five, 100 + 100 = 200 against 100.
    ``elcc_for_asset`` is this function plus name resolution.

    ``sim_kwargs`` (e.g. ``initial_soc_frac``) reaches ``mc.simulate`` through
    ``mc_adequacy`` and is applied IDENTICALLY to the baseline and to every
    candidate — an ELCC run at a different initial SoC than its own baseline
    would price the free initial cycle rather than the asset (plan review
    finding 5).

    **Phase 12c.** ``period``: compare the predicate on ONE period's LOLE
    (``by_period[period]``) with that period's resolution floor — the
    margin is enforced per period; a firm block in every hour cannot raise
    any period's LOLE, the periods are chronologically independent (states
    and SoC restart at the boundary), and CRN holds per period, so
    ``LOLE_P(Δ)`` is the same monotone step function the horizon predicate
    rests on. ``baseline`` / ``baseline_key``: a caller that already ran the
    identical ``mc_adequacy`` (the ``/mc`` worker, whose headline metrics
    ARE this baseline) passes it in with ``baseline_key(...)``; the key is
    recomputed here and a mismatch raises, so the CRN replay can never run
    against a baseline from another sample set. ``_zero_probe``: the
    Δ = 0 evaluation, shareable across periods since one call returns every
    period's LOLE.
    """
    nameplate = float(nameplate_mw)
    if not math.isfinite(nameplate) or nameplate < 0.0:
        raise ValueError(f"nameplate {nameplate_mw!r} is not a capacity")
    tol = default_tol_mw(nameplate) if tol_mw is None else float(tol_mw)
    if not math.isfinite(tol) or tol <= 0.0:
        raise ValueError(f"tol_mw {tol_mw!r} must be a positive MW tolerance")

    # ── the baseline: the ONLY evaluation allowed to adapt (spec §3 [v1.1]) ──
    if baseline is not None:
        expected = baseline_key_for(inputs, draws=draws, seed=seed,
                                    cov_target=cov_target, max_draws=max_draws,
                                    batch=batch, sim_kwargs=sim_kwargs)
        if baseline_key != expected:
            raise ValueError(
                "elcc_of_removal: the injected baseline's key does not match "
                "this evaluation's inputs and sampling parameters — a "
                "baseline from another sample set would break the CRN replay")
    else:
        baseline = mc_adequacy(inputs, draws=draws, seed=seed,
                               cov_target=cov_target, max_draws=max_draws,
                               batch=batch, **sim_kwargs)
    lole_base = _lole_of(baseline, period)
    n_fixed = int(baseline["n_samples"])
    floor = baseline["resolution_floor_h"]
    if period is not None:
        # The period's own floor: its smallest positive weight over the
        # sample count — `resolution_floor_h`'s definition, restricted.
        start, end = _period_slice(inputs, period)
        w = np.asarray(inputs.weights, dtype=np.float64)[start:end]
        w_pos = w[w > 0]
        floor = (float(w_pos.min()) / n_fixed) if (w_pos.size and n_fixed) else None

    # ── honest refusal 1: nothing to hold constant (spec §3) ────────────────
    # Compared against the RESOLUTION FLOOR, not against zero. A baseline of
    # one shed hour in 500 draws is not a small LOLE, it is a LOLE this many
    # draws cannot distinguish from zero; bisecting against it would price an
    # asset off a single sampled hour. A horizon of unknown length (floor None,
    # nyears ≤ 0) states no floor, so only an exact zero refuses there.
    refuse_at = float(floor) if floor is not None else 0.0
    if lole_base <= refuse_at:
        shown = "unknown" if floor is None else f"{refuse_at:.4g} h"
        return _row(
            nameplate=nameplate, baseline=baseline, status="unidentifiable",
            period=period,
            reason=(f"baseline LOLE {lole_base:.4g} h is at or below the "
                    f"resolution floor ({shown}) at {n_fixed} draws — no "
                    "shortfall to hold constant, so no credit is "
                    "identifiable; raise the draw count or study a tighter "
                    "system"))

    reduced_inputs = inputs if reduced is None else reduced

    def metrics_at(delta_mw: float) -> dict:
        """One candidate evaluation, PINNED to the baseline's sample set.

        ``cov_target=_NEVER_CONVERGE`` + ``max_draws=n_fixed`` replays the
        baseline's batch sizes, so this run spawns the same RNG children in the
        same order and sees bit-identical outage paths (module docstring).
        ``seed`` is the caller's, unmodified, on every single call — that is
        the CRN contract in one line.
        """
        return mc_adequacy(reduced_inputs, draws=draws, seed=seed,
                           cov_target=_NEVER_CONVERGE, max_draws=n_fixed,
                           batch=batch, exclude=exclude,
                           exclude_storage=exclude_storage,
                           extra_firm_mw=float(delta_mw), **sim_kwargs)

    def lole_at(delta_mw: float) -> float:
        return _lole_of(metrics_at(delta_mw), period)

    # ── Δ = 0: does the asset carry any LOLE credit at all? ─────────────────
    # Probed explicitly so a worthless asset reads exactly 0.0 rather than the
    # half-tolerance crumb a bare bisection would return. (Under CRN
    # LOLE_reduced(0) ≥ LOLE_baseline always — removing capacity cannot help —
    # so this is an equality test in all but name.) Shareable across periods
    # through `_zero_probe`: one Δ = 0 call carries every period's LOLE.
    zero = _zero_probe if _zero_probe is not None else metrics_at(0.0)
    if _lole_of(zero, period) <= lole_base:
        return _row(nameplate=nameplate, baseline=baseline, elcc_mw=0.0,
                    period=period)

    # ── the bracket [0, nameplate]; NEVER extrapolate past it (spec §3) ─────
    if lole_at(nameplate) > lole_base:
        return _row(
            nameplate=nameplate, baseline=baseline, status="not_bracketed",
            period=period,
            reason=(f"a firm block of {nameplate:.4g} MW — the asset's full "
                    "nameplate — does not restore the baseline LOLE of "
                    f"{lole_base:.4g} h; v1 rejects exceedance rather than "
                    "extrapolating a credit the bracket never priced"))

    # ── bisection on the predicate: smallest Δ with LOLE_reduced ≤ baseline ─
    # INVARIANT: lo fails the predicate, hi satisfies it. Both ends are probed
    # above, so the invariant holds on entry and the answer is always `hi` —
    # the smallest Δ KNOWN to restore the baseline. Returning the midpoint (or
    # `lo`) would report a credit that has not been demonstrated.
    lo, hi = 0.0, nameplate
    steps = 0
    while hi - lo > tol and steps < _MAX_BISECTION_STEPS:
        mid = 0.5 * (lo + hi)
        if lole_at(mid) <= lole_base:
            hi = mid
        else:
            lo = mid
        steps += 1
    if steps >= _MAX_BISECTION_STEPS:                       # pragma: no cover
        logger.warning("adequacy ELCC: bisection hit its step guard at "
                       "tol %.4g MW on a %.4g MW bracket", tol, nameplate)
    return _row(nameplate=nameplate, baseline=baseline, elcc_mw=hi, period=period)


def _resolve(inputs, kind: str, name: str):
    """(reduced inputs, exclude, exclude_storage, nameplate) for one asset.

    Removal semantics are per kind (spec §3) and each one is a different
    statement about how the asset entered the model in the first place:

    * ``generator`` — an occurrence-bearing unit in the sampled fleet. Removal
      is EXCLUSION BY POSITION, so its substream is still consumed and every
      other unit's draws are untouched (mc §2.3).
    * ``storage_unit`` — removal from the dispatch, by name (``mc.simulate``
      accepts names or indices for exactly this reason).
    * ``vre`` — must-take, already NETTED OUT of the residual by
      ``fleet_and_residual``. Removal is un-netting: ``residual += profile``,
      where the profile is the contribution (profile × capacity) that
      ``snapshot_inputs(n, vre_assets=[...])`` preserved for this purpose.
    """
    if kind == "generator":
        for i, u in enumerate(inputs.units):
            if u.name == name:
                return inputs, frozenset({i}), frozenset(), unit_nameplate_mw(u)
        raise KeyError(f"no occurrence-bearing generator named {name!r} in the "
                       "sampled fleet")

    if kind == "storage_unit":
        for s in inputs.storage:
            if s.name == name:
                return inputs, frozenset(), frozenset({name}), float(s.p_nom_mw)
        raise KeyError(f"no storage unit named {name!r} in the snapshot")

    if kind == "vre":
        # **[v1.1]** (spec §3). A name that is in the sampled fleet was NEVER
        # netted into the residual — it is an occurrence-bearing unit with an
        # outage chain. Un-netting a profile for it would put its output into
        # the load while leaving the unit in the fleet: the asset counted
        # twice, and a credit near twice its capacity. 422, naming the unit.
        if any(u.name == name for u in inputs.units):
            raise ValueError(
                f"{name!r} is an occurrence-bearing generator in the sampled "
                "fleet, not must-take: its output was never netted into the "
                "residual, so crediting it as kind='vre' would double-count "
                "it — ask for it as kind='generator'")
        profile = inputs.vre_profiles[name]      # KeyError → 404 at the route
        profile = np.asarray(profile, dtype=np.float64)
        # Nameplate = the PEAK must-take contribution. FINDING (recorded, not
        # silently resolved): MCInputs carries profile × capacity only, so the
        # installed capacity is not recoverable here; this equals it exactly
        # when the profile attains 1.0 somewhere in the horizon (it does for
        # any real wind/solar trace) and is otherwise conservative — a
        # narrower bracket, never a wider one.
        return (replace(inputs, residual=inputs.residual + profile),
                frozenset(), frozenset(), float(profile.max(initial=0.0)))

    raise ValueError(f"unknown ELCC asset kind {kind!r} (expected one of "
                     f"{', '.join(_KINDS)})")


def elcc_for_asset(inputs, kind: str, name: str, *, seed, draws,
                   tol_mw=None, **kwargs) -> dict:
    """
    The last-in credit of ONE named asset, as the payload row (spec §3).

    Row: ``{kind, name, nameplate_mw, elcc_mw, elcc_share, status, reason,
    baseline_lole_h, baseline_lole_ci}`` — always all nine keys.
    ``status`` is ``"ok"`` (``elcc_mw``/``elcc_share`` are numbers, ``reason``
    is None), ``"unidentifiable"`` or ``"not_bracketed"`` (both carry None for
    the numbers and a sentence for the panel to render).

    Raises ``KeyError`` for an unknown asset (404) and ``ValueError`` for an
    unknown kind, a must-take request for an occurrence-bearing name, or a
    non-positive tolerance (422).
    """
    reduced, exclude, exclude_storage, nameplate = _resolve(inputs, kind, name)
    row = elcc_of_removal(inputs, reduced=reduced, exclude=exclude,
                          exclude_storage=exclude_storage,
                          nameplate_mw=nameplate, seed=seed, draws=draws,
                          tol_mw=tol_mw, **kwargs)
    return {"kind": kind, "name": name, **row}
