"""Recommended grid-forming settings for an island (E2E demo plan 2026-10-14
§7.2, with the gatekeeper's changes in §7.4).

I6 (``frequency.py``) screens the settings the user typed; I7 turns a failing
event into the capacity the hub must buy. This module asks the question in
between: **which firmware settings of the grid-forming converters would make
the island's frequency events pass, with margin, before anything is bought?**

The settings are one droop ``d`` (percent on P_n) and one equivalent inertia
``H`` (s on S_n) for every GFM converter, so the converters keep sharing in
proportion to their ratings. When the current settings pass, each unit keeps
its own (a mixed fleet is left as it is); a search starts from common settings,
every GFM at the first one's droop and H, and says so in the notes. A VSM takes ``H_v = H``; a droop GFM takes the
low-pass constant that gives the same synthetic inertia, ``tau_f = 2 H S_n R
/ P_n`` (``frequency._gfm_m``: M = P_n tau_f / (R f0) = 2 H S_n / f0). The
current-limit ``i_max`` is hardware: it is held unless ``allow_i_max``, when
it may rise to ``bounds.i_max_pu_max``.

**Cases.** Each case is one event at one island hour, rebuilt as the study ran
it, with ``run(units, dt, t_end)`` (``Case``). E4 (UPS-bridged start) has no
grid-forming converter in it and is never a case; the driver leaves it out.

**Margin.** A case passes with margin ``m`` when ``frequency.judge`` passes it
against limits shrunk by ``m`` (``shrunk``): the RoCoF limit times (1 − m),
each frequency limit moved toward f0 by m of what it allows, the settled band
times (1 − m). ``0 ≤ m < 1``. The margin covers the grid-code limits only:
equipment, UPS and protection limits are used as given (a note says so).

**Search.** Fixed horizons only (never a settle-lengthening loop): ``search``
``(dt, t_end)`` while searching, ``verify`` once at the end. In order:

1. the current settings pass every case at ``verify`` → kept, nothing changes;
2. the strongest corner (lowest droop, highest H) at ``search`` fails → with
   ``allow_i_max``, the corner again at the highest ``i_max``; failing that,
   not achievable (below);
3. the highest droop that passes with H at its maximum, by ``BISECT_STEPS``
   halvings between the current droop and the lowest (the setting closest to
   the current one);
4. the lowest H that passes with that droop, the same way;
5. that setting at ``verify``; if it fails there, the corner at ``verify``.

Steps 3 and 4 assume a stronger setting never makes a case worse (lower droop,
more inertia), which holds while no converter saturates; the final verify is
what the answer rests on. Every simulation is counted against ``max_sims``,
and ``len(cases)`` of them are reserved for the final after-check, so a
search that finished is always verified; the search stops, ``incomplete``
(never a capacity verdict), when the budget would be exceeded, and so does a
result whose after-check ran out of budget. One written exception: the
capacity fallback of a not-achievable island runs I7's existing path
(``frequency.cuts_for_event``, with its own bounded tries, ``_settle_run``
included) outside the cap; those simulations are counted in the reported
total and named in a note ("capacity fallback: N extra simulations").

**Not achievable.** The corner settings and, for each case that still fails,
``frequency.cuts_for_event``'s rows against the shrunk limits: the inertia,
headroom or droop gain the hub must buy (I7). **Comparison.** Each converter run as ``gfl_fw`` (when it
has a frequency-watt block), ``gfm_droop`` and ``gfm_vsm`` at the
recommended settings, its worst RoCoF at 0+ (where a grid-following unit adds
no inertia) and over the window (where a fast frequency-watt block can still
win), and its worst nadir over the cases.

A heuristic screening on the single-bus aggregate model, never a tuning
study: ``LABEL``.
"""
from __future__ import annotations

import copy
import dataclasses
import math
from collections.abc import Callable

from gridspine.static import frequency as F

LABEL = ("heuristic screening: one droop and one equivalent inertia for every grid-forming converter, "
         "on the single-bus aggregate frequency model; not a converter tuning study")
GFM_MODELS = ("gfm_droop", "gfm_vsm")
CONVERTER_MODELS = GFM_MODELS + ("gfl_fw",)
BISECT_STEPS = 8


@dataclasses.dataclass(frozen=True)
class Bounds:
    """The settings' search ranges (ledgered as assumed, ``ledger``)."""
    droop_pct: tuple[float, float] = (1.0, 10.0)
    h_s: tuple[float, float] = (0.5, 10.0)
    tau_f_s: tuple[float, float] = (0.01, 1.0)
    i_max_pu_max: float = 1.5


GFM_BOUNDS = Bounds()
MARGIN_NOTE = ("the margin covers the grid-code limits only (frequency_limits); equipment, UPS and "
               "protection limits are used as given")


def ledger(bounds: Bounds = GFM_BOUNDS) -> list:
    """The search ranges as ledger lines, each assumed."""
    (d0, d1), (h0, h1), (t0, t1) = bounds.droop_pct, bounds.h_s, bounds.tau_f_s
    return [f"gfm settings: droop searched over {d0:g}–{d1:g} % on P_n (assumed)",
            f"gfm settings: equivalent inertia H searched over {h0:g}–{h1:g} s on S_n (assumed)",
            f"gfm settings: a droop GFM's tau_f held within {t0:g}–{t1:g} s (assumed)",
            f"gfm settings: i_max raised to at most {bounds.i_max_pu_max:g} pu of S_n, only with allow_i_max, "
            "never lowered (assumed)",
            "gfm settings: " + MARGIN_NOTE + " (assumed)"]


@dataclasses.dataclass
class Case:
    """One event at one island hour: ``run(units, dt, t_end)`` re-runs it."""
    key: str
    units: list
    run: Callable


#: ``Recommendation.status``: the current settings pass (``unchanged``); new
#: ones pass (``recommended``); none within the bounds does (``not_achievable``,
#: capacity is needed); or the simulation cap stopped the search
#: (``incomplete``: the best settings found so far, never a capacity verdict).
STATUSES = ("unchanged", "recommended", "not_achievable", "incomplete")


@dataclasses.dataclass
class Recommendation:
    achievable: bool | None     # None when the search was incomplete
    changed: bool
    settings: dict              # unit -> {"model", "current": {...}, "recommended": {...}}
    cases: list                 # {"key", "ok_before", "binding_before", "ok_after", "binding_after"}
    capacity: list              # cuts_for_event rows for the cases still failing (not achievable)
    comparison: list            # {"unit", "model", "worst_rocof0_hz_per_s" (at 0+), "worst_rocof_hz_per_s"
    #                             (over the window), "worst_nadir_hz", "ok", "note"}
    simulations: int
    margin: float
    bounds: Bounds
    notes: list = dataclasses.field(default_factory=list)
    label: str = LABEL
    status: str = "unchanged"


class _Budget(Exception):
    pass


def shrunk(limits: dict, margin: float, f0: float = F.F0) -> dict:
    """``limits`` with each one tightened by ``margin`` (0 ≤ m < 1) of what it allows."""
    if not (isinstance(margin, (int, float)) and 0.0 <= margin < 1.0):
        raise ValueError(f"margin must be in [0, 1), got {margin!r}")
    out = copy.deepcopy(limits)
    keep = 1.0 - margin
    for part in ("transition", "steady_island"):
        lim = out[part]
        lim["rocof_hz_per_s"] = lim["rocof_hz_per_s"] * keep
        lim["f_min_hz"] = f0 - (f0 - lim["f_min_hz"]) * keep
        lim["f_max_hz"] = f0 + (lim["f_max_hz"] - f0) * keep
        lim["qss_band_hz"] = lim["qss_band_hz"] * keep
    return out


def _gfms(units) -> list:
    return [u for u in units if u.model in GFM_MODELS]


def _h_of(u) -> float:
    """A GFM's equivalent inertia constant H, s on S_n."""
    if u.model == "gfm_vsm":
        return float(u.h)
    return float(u.tau_f * u.p_n / (2.0 * u.r * u.s_n))


def _with(u, d_pct: float, h: float, bounds: Bounds, i_max_pu: float | None):
    r = d_pct / 100.0
    kw = {"r": r}
    if u.model == "gfm_vsm":
        kw["h"] = h
    else:
        lo, hi = bounds.tau_f_s
        kw["tau_f"] = min(max(2.0 * h * u.s_n * r / u.p_n, lo), hi)
    if i_max_pu is not None:                     # a raise, never a cut
        kw["i_max_mw"] = max(u.i_max_mw, i_max_pu * u.s_n)
    return dataclasses.replace(u, **kw)


def apply(units, d_pct: float, h: float, bounds: Bounds = GFM_BOUNDS, i_max_pu: float | None = None) -> list:
    """``units`` with every GFM at droop ``d_pct`` and inertia ``h``."""
    return [_with(u, d_pct, h, bounds, i_max_pu) if u.model in GFM_MODELS else u for u in units]


def recommend_gfm_settings(cases, limits, *, f0=F.F0, equipment_=(), window_ms=500.0, bounds=GFM_BOUNDS,
                           margin=0.1, search=(5e-3, 10.0), verify=(1e-3, 20.0), allow_i_max=False,
                           max_sims=400) -> Recommendation:
    """The recommended settings over ``cases`` (see the module docstring)."""
    cases = list(cases)
    tight = shrunk(limits, margin, f0)
    sims = [0]
    cap = [max_sims - len(cases)]   # the after-check's len(cases) runs are reserved
    notes: list[str] = []

    def measured(case, units, horizon, lim):
        if sims[0] >= cap[0]:
            raise _Budget()
        sims[0] += 1
        dt, t_end = horizon
        m = F.metrics(case.run(units, dt, t_end), window_ms)
        return m, F.judge(m, lim, f0, equipment_)

    def judged(case, units, horizon, lim):
        return measured(case, units, horizon, lim)[1]

    best = {}            # the setting closest to the current one that passed so far

    def passes(d, h, horizon, i_max_pu=None):
        for c in cases:
            if not judged(c, apply(c.units, d, h, bounds, i_max_pu), horizon, tight).ok:
                return False
        # The latest passing setting is the closest to the current one (the
        # search walks toward it); a verified one is not replaced by a search one.
        if horizon == verify or not best.get("verified"):
            best.update(d=d, h=h, i_max_pu=i_max_pu, verified=horizon == verify)
        return True

    gfm0 = next((u for c in cases for u in _gfms(c.units)), None)
    settings = {}
    for c in cases:
        for u in _gfms(c.units):
            settings.setdefault(u.name, {"model": u.model, "current": _settings_of(u)})
    if gfm0 is None:
        notes.append("no grid-forming converter in any case: nothing to tune")
    else:
        notes.append(MARGIN_NOTE)
    d_cur = 100.0 * gfm0.r if gfm0 is not None else math.nan
    h_cur = _h_of(gfm0) if gfm0 is not None else math.nan
    (d_lo, d_hi), (h_lo, h_hi) = bounds.droop_pct, bounds.h_s
    # rec None: every unit keeps its own current settings.
    rec, before = None, []
    try:
        for c in cases:
            v = judged(c, c.units, verify, tight)
            before.append((v.ok, v.binding))
        if all(ok for ok, _ in before) or gfm0 is None:
            achievable, changed = all(ok for ok, _ in before), False
        else:
            own = {(round(s["current"]["droop_pct"], 9), round(s["current"]["H_equivalent_s"], 9))
                   for s in settings.values()}
            notes.append(f"the search starts from common settings: every grid-forming converter at one droop and "
                         f"one H, from {gfm0.name}'s current {d_cur:g} % and {h_cur:g} s"
                         + ("; the units' own settings differ and are replaced" if len(own) > 1 else ""))
            rec, achievable = _search(passes, d_cur, h_cur, d_lo, d_hi, h_lo, h_hi, search, verify, bounds,
                                      allow_i_max, notes)
            changed = True
    except _Budget:
        # Never a capacity verdict: the search did not finish. The best setting
        # it found (else the current one) is what is reported.
        notes.append(f"search incomplete (cap {max_sims} reached)"
                     + (": the best settings found so far are reported" if best else
                        ": no passing setting found yet; the current settings are reported"))
        rec = {k: best[k] for k in ("d", "h", "i_max_pu")} if best else None
        achievable, changed = None, bool(best)
        before += [(False, "not run (budget)")] * (len(cases) - len(before))

    cap[0] = max_sims
    after, finals = [], []
    for c, (ok0, b0) in zip(cases, before):
        units = c.units if rec is None else apply(c.units, rec["d"], rec["h"], bounds, rec["i_max_pu"])
        try:
            v = judged(c, units, verify, tight)
            ok1, b1 = v.ok, v.binding
        except _Budget:
            ok1, b1 = False, "not run (budget)"
        after.append({"key": c.key, "ok_before": ok0, "binding_before": b0, "ok_after": ok1, "binding_after": b1})
        finals.append(units)
    if achievable is not None and any(x["binding_after"] == "not run (budget)" for x in after):
        # Unreachable while the reservation holds; never report an unverified answer.
        notes[:] = [n for n in notes if "capacity is needed" not in n]
        notes.append(f"search incomplete (cap {max_sims} reached at the verification step)")
        achievable = None
    capacity = []
    if achievable is False:
        dt, t_end = verify
        extra = [0]
        for c, units, x in zip(cases, finals, after):
            if x["ok_after"]:
                continue

            def run(us, t, c=c):
                extra[0] += 1
                return c.run(us, dt, t)
            for kind, delta, binding, derivation, feasible, note in F.cuts_for_event(
                    units, run, tight, f0, equipment_, window_ms,
                    t_end=t_end, failing=x["binding_after"], where=f"dt {dt:g} s, t_end {t_end:g} s"):
                capacity.append({"case": c.key, "kind": kind, "delta": delta, "unit": F.CUT_KINDS[kind],
                                 "binding": binding, "derivation": derivation, "feasible": bool(feasible),
                                 "note": note})
        sims[0] += extra[0]
        notes.append(f"capacity fallback: {extra[0]} extra simulations via cuts_for_event "
                     "(I7's own bounded tries, outside the cap)")
    for name, s in settings.items():
        if rec is None:
            s["recommended"] = dict(s["current"])
        else:
            u = next(u for c in cases for u in _gfms(c.units) if u.name == name)
            s["recommended"] = _settings_of(_with(u, rec["d"], rec["h"], bounds, rec["i_max_pu"]))
    comparison = []
    if gfm0 is not None:
        try:
            comparison = _compare(cases, rec, (d_cur, h_cur), bounds, measured, search, tight)
        except _Budget:
            notes.append(f"the GFM/GFL comparison stopped at the budget of {max_sims} simulations")
    status = ("incomplete" if achievable is None else "not_achievable" if not achievable
              else "recommended" if changed else "unchanged")
    return Recommendation(achievable=achievable, changed=changed, settings=settings, cases=after,
                          capacity=capacity, comparison=comparison, simulations=sims[0], margin=margin,
                          bounds=bounds, notes=notes, status=status)


def _search(passes, d_cur, h_cur, d_lo, d_hi, h_lo, h_hi, search, verify, bounds, allow_i_max, notes):
    d_start = min(max(d_cur, d_lo), d_hi)
    h_start = min(max(h_cur, h_lo), h_hi)
    i_max = None
    if not passes(d_lo, h_hi, search):
        if allow_i_max and passes(d_lo, h_hi, search, bounds.i_max_pu_max):
            i_max = bounds.i_max_pu_max
            notes.append(f"i_max raised to {i_max:g} pu: a hardware change, not a setting")
        else:
            notes.append("no setting within the bounds passes every case: capacity is needed (I7)")
            return {"d": d_lo, "h": h_hi, "i_max_pu": None}, False
    # 3. the highest droop that passes with H at its maximum
    lo, hi = d_lo, d_start                       # lo passes; hi is the current (closest)
    if passes(hi, h_hi, search, i_max):
        d = hi
    else:
        for _ in range(BISECT_STEPS):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if passes(mid, h_hi, search, i_max) else (lo, mid)
        d = lo
    # 4. the lowest H that passes with that droop
    lo, hi = h_start, h_hi                       # hi passes
    if passes(d, lo, search, i_max):
        h = lo
    else:
        for _ in range(BISECT_STEPS):
            mid = 0.5 * (lo + hi)
            lo, hi = (lo, mid) if passes(d, mid, search, i_max) else (mid, hi)
        h = hi
    # 5. verify
    if passes(d, h, verify, i_max):
        return {"d": d, "h": h, "i_max_pu": i_max}, True
    if passes(d_lo, h_hi, verify, i_max):
        notes.append("the searched setting failed at the verification step; the strongest corner is kept")
        return {"d": d_lo, "h": h_hi, "i_max_pu": i_max}, True
    notes.append("no setting passes at the verification step: capacity is needed (I7)")
    return {"d": d_lo, "h": h_hi, "i_max_pu": i_max}, False


def _settings_of(u) -> dict:
    out = {"droop_pct": 100.0 * u.r, "i_max_pu": u.i_max_mw / u.s_n if u.s_n == u.s_n and u.s_n else math.nan}
    if u.model == "gfm_vsm":
        out["H_v_s"] = float(u.h)
    else:
        out["tau_f_s"] = float(u.tau_f)
    out["H_equivalent_s"] = _h_of(u)
    return out


def _compare(cases, rec, ref, bounds, measured, horizon, tight) -> list:
    """Each converter as gfl_fw / gfm_droop / gfm_vsm at the recommended
    settings (the other GFMs at them too), at the ``search`` horizon. ``rec``
    None: every unit at its own current settings (a non-GFM run as a GFM at
    ``ref``, the first GFM's droop and H)."""

    def tuned(orig, u):
        if rec is not None:
            return _with(u, rec["d"], rec["h"], bounds, rec["i_max_pu"])
        if orig.model == u.model:
            return u
        d, h = (100.0 * orig.r, _h_of(orig)) if orig.model in GFM_MODELS else ref
        return _with(u, d, h, bounds, None)

    names = sorted({u.name for c in cases for u in c.units if u.model in CONVERTER_MODELS})
    out = []
    for name in names:
        for model in CONVERTER_MODELS:
            worst_r = worst_r0 = worst_n = 0.0
            ok_all, skipped = True, None
            for c in cases:
                units = []
                for u in c.units:
                    if u.name == name:
                        if model == "gfl_fw" and not u.fw:
                            skipped = "no frequency-watt block for this unit"
                            break
                        orig, u = u, dataclasses.replace(u, model=model)
                        if model in GFM_MODELS:
                            if math.isnan(u.r):
                                u = dataclasses.replace(u, r=(rec["d"] if rec else ref[0]) / 100.0)
                            u = tuned(orig, u)
                    elif u.model in GFM_MODELS:
                        u = tuned(u, u)
                    units.append(u)
                if skipped:
                    break
                m, v = measured(c, units, horizon, tight)
                worst_r0 = max(worst_r0, abs(m.rocof0_hz_per_s))
                worst_r = max(worst_r, math.inf if m.collapse is not None else abs(m.rocof_window_hz_per_s))
                worst_n = max(worst_n, math.inf if m.collapse is not None else abs(m.nadir_hz))
                ok_all = ok_all and v.ok
            if skipped is None and model == "gfl_fw":
                skipped_note = ("grid-following: adds no inertia at 0+; its RoCoF over the window comes from "
                                "its frequency-watt response, not inertia")
            else:
                skipped_note = skipped
            out.append({"unit": name, "model": model, "worst_rocof0_hz_per_s": None if skipped else worst_r0,
                        "worst_rocof_hz_per_s": None if skipped else worst_r,
                        "worst_nadir_hz": None if skipped else worst_n, "ok": None if skipped else ok_all,
                        "note": skipped_note})
    return out
