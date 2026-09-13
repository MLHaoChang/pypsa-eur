"""COPT + FMEA-modes payload builders — lifted from ``routers/results.py``.

Route handlers keep FastAPI wiring and HTTP mapping (422 / 204 / 200).
This module owns the lock-scoped reads, screening call, and JSON assembly.
``cfg``, ``sweep_record``, and ``get_lock`` are injected so this module never
imports ``routers.*``. Pure engines stay in ``services/adequacy/copt.py``.
"""
from __future__ import annotations

from typing import Any, Callable


def build_copt_payload(
    n,
    cfg,
    *,
    get_lock: Callable[[], Any],
) -> dict | None:
    """Return the ``/copt`` JSON body, or ``None`` for HTTP 204.

    Raises ``ValueError`` when the fleet walk refuses an outage rate — the
    router maps that to HTTP 422, matching ``/mc`` and both planning loops.
    """
    from services.adequacy.activity import activity_summary as _activity_summary
    from services.adequacy.activity import period_blocks as _period_blocks
    from services.adequacy.copt import (
        K_EXACT,
        fleet_and_residual,
        is_flag_deterministic as _is_flag_deterministic,
        must_take_generators,
        rate_is_zero as _rate_is_zero,
        screening_analysis,
    )

    # Phase 12c-0: under the mutation lock, like /mc — a solve scales the
    # load frame IN PLACE for its duration, and a bare read mid-solve saw a
    # half-transformed network (v3 review, finding 8); and on the LP's
    # demand basis.
    with get_lock():
        # Whole-branch review S1: an outage rate outside [0, 1) is
        # refused by the walk as ValueError — the router maps that to 422,
        # matching /mc and both planning loops.
        units, residual, w = fleet_and_residual(n, cfg=cfg)
        # …and the membership read for `must_take`, under the same hold
        # (12c-0 shipped-code review, finding 4).
        n_must_take = len(must_take_generators(n))
        # Phase 12d: the activity disclosure reads the NETWORK (must-take
        # farms and rows dropped at zero capacity are not in the fleet —
        # shipped-code review, finding 1), so it is taken under the same
        # hold as the fleet it describes.
        activity = _activity_summary(n, _period_blocks(residual.index))
    if not units:
        return None
    voll = float(getattr(cfg, "voll", 0.0) or 0.0)
    # Phase 12c-pre: split, net the remainder, table, mixture, attribution —
    # one call so this route and the engine tests see the same arithmetic.
    analysis = screening_analysis(units, residual, weights=w, voll=voll,
                                  delta_mw=1.0)
    metrics = analysis["metrics"]
    rows = analysis["rows"]
    split = analysis["split"]
    # The must-take count comes from the SAME walk that decided membership.
    # The previous `electrical non-slack gens − len(units)` subtraction
    # miscounted zero-capacity generators, which the walk skips and the
    # subtraction did not (plan 12c-pre v2 review, finding 8).
    from services.adequacy.metrics import horizon_years, resolve_time_basis
    _copt_nyears = horizon_years(n)
    _copt_basis = resolve_time_basis(_copt_nyears)
    return {
        "engine": "copt",
        "fidelity": "analytic_convolution",
        "metrics": {
            "lole_hours": metrics["lole_hours"],
            "eue_mwh": metrics["eue_mwh"],
            "lolp_max": metrics["lolp_max"],
            "by_period": metrics["by_period"],
            # Derived, not asserted. The COPT sums over whatever horizon the
            # model spans, weighted; calling that "hours_per_year" on a
            # 168 h week reported 80.86 for a system whose annual LOLE is
            # ~4216 — and understating LOLE is the direction that gets a
            # number compared to a 3 h/yr standard it has no relation to.
            "time_basis": _copt_basis,
            "horizon_years": _copt_nyears,
        },
        "per_mode": [
            {**r["failure_mode"],
             "delta_eue_mwh": r["delta_eue_mwh"],
             **({"note": r["note"]} if "note" in r else {})}
            for r in rows
        ],
        "fleet": {
            "units": len(units),
            "must_take": n_must_take,
            "delta_mw": 1.0,
            # Phase 12c-pre disclosure: which units carry a profile INTO the
            # sampled fleet, which of those were netted beyond the exact
            # cap, and the sentence that says so. `fidelity` above stays the
            # engine enum the comparison table keys on.
            "profile_units": [u.name for u in split.mixed] + [u.name for u in split.netted],
            "netted_beyond_cap": [u.name for u in split.netted],
            "k_exact": K_EXACT,
            # Phase 12h, as M5 and F8 left it. Three kinds of unit the
            # profile lists above cannot describe:
            #  * a unit whose STATIC p_max_pu was folded into its capacity
            #    has no profile at all, so it is in no profile list — the
            #    `source` field is here so a later phase can add another
            #    fold without changing the shape;
            #  * a unit the 12h FLAG zeroed — profiled OR folded (M5: the
            #    disclosure is symmetric across the two shapes, where it
            #    once named the column unit twice and the static one never);
            #  * a unit whose rate the user TYPED as 0 (F8), which is the
            #    same q by a different route and belongs in its own list
            #    rather than under the flag's name.
            # A unit in either of the last two is netted exactly, at full
            # availability, and no outages are sampled for it.
            "folded_units": [
                {"name": u.name, "folded_constant": float(u.folded_constant),
                 "source": "static"}
                for u in units
                if getattr(u, "folded_constant", None) is not None],
            "deterministic_units": [u.name for u in units
                                    if _is_flag_deterministic(u)],
            # IEEE 39-bus review, F8. The OTHER way a unit reaches q = 0:
            # the user typed the rate as 0. M4 stopped calling that "the
            # flag", which was the defect — but on `/mc`, which has no rows
            # to carry a note, it then left such a unit named nowhere at
            # all. Same fleet, same q, different reason: two lists, both
            # disjoint from `profile_units` and from each other.
            "rate_zero_units": [u.name for u in units
                                if _rate_is_zero(u)
                                and not _is_flag_deterministic(u)],
        },
        "fidelity_note": analysis["fidelity_note"],
        # Phase 12d: which units the engines masked in which period, by
        # build year / lifetime (and which are below nameplate, a later
        # vintage not yet built), with the sentence that says so.
        "activity": activity,
        "voll_eur_per_mwh": voll,
    }


def build_fmea_modes_payload(
    n,
    cfg,
    *,
    sweep_record: dict | None,
    get_lock: Callable[[], Any],
) -> dict | None:
    """Return the ``/fmea_modes`` JSON body, or ``None`` for HTTP 204.

    Raises ``ValueError`` from the nested COPT walk (router maps to 422).
    """
    per_mode: list = []
    copt = build_copt_payload(n, cfg, get_lock=get_lock)
    if isinstance(copt, dict):
        per_mode.extend(copt["per_mode"])
    sweep = sweep_record
    # Phase 12e: an ABORTED sweep measured real contingencies before it was
    # stopped, and the worksheet is where those rows are read. Dropping them
    # here would make the abort silently lose work the user paid solves for.
    if sweep and sweep.get("status") in ("done", "aborted"):
        for r in sweep.get("rows", []):
            if r.get("failure_mode"):
                per_mode.append({**r["failure_mode"],
                                 "delta_eue_mwh": r.get("delta_eue_mwh")})
    if not per_mode:
        return None
    # Phase 12e (shipped-code review, finding 11): `(-criticality, mode_id)`,
    # which is what the spec claimed and the code did not do. Sorting on
    # criticality alone left exactly-tied rows in SOURCE order — class A from
    # the COPT engine, then the sweep's B and C — and the tie is not a corner
    # case here: with no VoLL set every criticality is €0/yr (see below), so
    # the whole ranking ties and the order the worksheet renders depended on
    # which classes happened to have been computed. `reverse=True` cannot be
    # used with a tuple key: it would reverse the mode_id order too.
    per_mode.sort(key=lambda r: (-float(r.get("criticality_eur_per_year", 0.0)),
                                 str(r.get("mode_id", ""))))
    # VOLL travels with the rows so the worksheet can say WHY every
    # criticality is zero. Criticality is ΔEUE × VoLL × occurrence, so with
    # no VoLL set the whole ranking collapses to €0/yr — modes whose ΔEUE
    # differs by 4× tie at zero, and the table reads "these failure modes
    # cost nothing" when the truth is "these failure modes cannot be priced".
    # The sweep already refuses outright (422) without a VoLL; this surface
    # still has LOLE/EUE worth serving, so it reports the condition instead.
    _cfg = cfg
    try:
        _voll = float(getattr(_cfg, "voll", 0.0) or 0.0)
    except (TypeError, ValueError):
        _voll = 0.0
    return {"per_mode": per_mode,
            "voll_eur_per_mwh": _voll,
            "sweep_status": (sweep or {}).get("status"),
            # Phase 12e (shipped-code review, finding 14): the worksheet is
            # where the sweep's rows are read, so it is where a failed closing
            # re-solve has to be said. The record has carried these since the
            # review's finding 1; this surface used to drop them, leaving the
            # user reading contingency rows with no sign that the network is
            # still on the last contingency.
            "sweep_base_restored": (sweep or {}).get("base_restored"),
            "sweep_base_restore_status": (sweep or {}).get("base_restore_status"),
            "sweep_error": (sweep or {}).get("error")}
