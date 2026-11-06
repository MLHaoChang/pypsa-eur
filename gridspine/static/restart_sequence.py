"""The process restart sequence after a trip or an island transition (grid-to-process issue 12b, design §4.3
item 3).

A simple discrete-event model from 1 s to 4 h, not dynamics, and nothing here feeds the LP (design D5):

1. **Order.** The tripped units come back in criticality-tier order (``TIER_ORDER``: safety first); a unit with
   no tier goes last, with a note, never a guessed tier. Ties keep the input order.
2. **Pickup.** With ``pickup_blocks`` (MW, the island sidecar's), the units fill the blocks in that order, each
   into the earliest block with room for its rated MW (first fit). A unit bigger than every block gets none: its
   restart is ``fail`` ("no block fits"), never placed silently. Without blocks, one unit per step. Block k
   (0-based) is picked up at ``T_FIRST_S + k * pickup_interval_s``.
3. **Motor start.** Each pickup's motor start runs through ``motor_start.motor_start_table`` on the source of
   the transition: the PoC's after a grid trip, the island's (its gensets' S_k) after an island transition,
   never the grid PoC's there (it would be optimistic). With no source the start is ``unknown``.
4. **Timeline and tonnes.** A unit reaches full rate ``restart_time_h`` after its pickup. Its lost tonnes are

       lost_wait_t    = pickup offset * rate * mass pull
       lost_restart_t = restart_time_h * rate * mass pull
       lost_t         = lost_wait_t + lost_restart_t

   counted to full rate even past the 4 h window (the unit's note says "beyond the 4 h window").

A restart whose motor start is ``fail`` or ``unknown`` keeps its place on the timeline (a screen, not a physical
block). Any ``fail`` or ``unknown`` makes the total a ``lower_bound`` and is counted; a unit missing its
restart time, rate or mass pull adds no tonnes and is counted unknown.
"""
from __future__ import annotations

import math

import pandas as pd

from gridspine.schema.contracts import ContractError
from gridspine.static import motor_start as ms
from gridspine.static.flicker import _num
from gridspine.static.ride_through import _blank

TOLERANCE = 1e-9
#: Equal to the GUI's ``models.process_unit.CRITICALITY_TIERS``: the order units come back in.
TIER_ORDER: tuple[str, ...] = ("safety", "process_critical", "process_flexible", "deferrable")
TRANSITIONS = ("trip", "island")
#: The model's window: the first pickup 1 s after the event, the timeline shown to 4 h.
T_FIRST_S = 1.0
HORIZON_S = 4.0 * 3600.0
#: Demo time between two pickups: a motor start and the voltage's recovery before the next.
PICKUP_INTERVAL_S = 60.0

LABEL = "event model, not dynamics; the binding answer is an RMS study of the restart"
BEYOND = "beyond the 4 h window"
NO_BLOCK = "no block fits"
LEDGER = (
    f"pickups start {T_FIRST_S:g} s after the event, one block every {PICKUP_INTERVAL_S:g} s (demo, assumed)",
    "units are assigned to the pickup blocks in criticality-tier order, safety first, first fit: each into the "
    "earliest block with room for its rated MW; a unit with no tier last (assumed)",
    "a unit reaches full rate its restart time after its pickup, and production is lost from the event until "
    "full rate (assumed; conservative)",
    "after an island transition each motor start is screened on the island's short-circuit power, after a grid "
    "trip on the PoC's",
)

UNIT_COLUMNS = ("unit", "tier", "rated_mw", "block", "pickup_s", "full_rate_s", "restart_time_h", "rate_tph",
                "mass_pull", "du_pct", "start_verdict", "lost_wait_t", "lost_restart_t", "lost_t", "verdict",
                "note", "label")
EVENT_COLUMNS = ("t_s", "unit", "event", "block", "du_pct", "verdict", "note")


def _order(units: pd.DataFrame) -> tuple[list[dict], dict]:
    """The units in pickup order, and the note of each unit placed last for having no tier."""
    recs = units.to_dict("records")
    notes = {}
    for r in recs:
        tier = "" if _blank(r.get("tier")) else str(r["tier"]).strip()
        if tier and tier not in TIER_ORDER:
            raise ContractError(f"{r['unit']}: tier {tier!r} is not one of {', '.join(TIER_ORDER)}.")
        r["tier"] = tier or None
        if not tier:
            notes[r["unit"]] = "no criticality tier: picked up last"
    rank = {t: i for i, t in enumerate(TIER_ORDER)}
    return sorted(recs, key=lambda r: rank.get(r["tier"], len(TIER_ORDER))), notes


def _blocks_of(ordered: list[dict], blocks) -> dict:
    """``{unit: block index or None}``: first fit by rated MW in pickup order, or one unit per step. A negative
    rated MW is refused, never read as "no rated MW"."""
    for r in ordered:
        mw = _num(r.get("rated_mw"))
        if mw is not None and mw < 0:
            raise ContractError(f"{r['unit']}: rated MW must be 0 or more, got {mw:g}.")
    if not blocks:
        return {r["unit"]: i for i, r in enumerate(ordered)}
    room = [float(b) for b in blocks]
    if any(not math.isfinite(b) or b <= 0 for b in room):
        raise ContractError(f"pickup_blocks must each be above 0 MW, got {list(blocks)}")
    out = {}
    for r in ordered:
        mw = _num(r.get("rated_mw"))
        if mw is None:
            out[r["unit"]] = None
            continue
        k = next((i for i, left in enumerate(room) if left >= mw - TOLERANCE), None)
        if k is not None:
            room[k] -= mw
        out[r["unit"]] = k
    return out


def _start(rec: dict, source: ms.Source | None, src_name: str, q_gfm, limit_pct) -> tuple[float | None, str, str]:
    """``(dU %, verdict, note)`` of the unit's motor start on ``source`` (``src_name``'s)."""
    if source is None:
        return None, "unknown", f"no short-circuit power for {src_name}: the motor start is unknown"
    row = pd.DataFrame([{"unit": rec["unit"], "poc": "source", "motor_kw": rec.get("motor_kw"),
                         "start_method": rec.get("start_method"), "i_start_pu": rec.get("i_start_pu")}])
    try:
        r = ms.motor_start_table(row, {"source": source}, {"source": q_gfm}, limit_pct).iloc[0]
    except ContractError as exc:
        return None, "unknown", str(exc)
    return _num(r["du_pct"]), str(r["verdict"]), str(r["note"])


def restart_sequence(units: pd.DataFrame, transition: str, *, poc_source: ms.Source | None = None,
                     island_source: ms.Source | None = None, q_gfm_mvar: float | None = None,
                     limit_pct: float | None = None, pickup_blocks=None,
                     pickup_interval_s: float = PICKUP_INTERVAL_S) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """``(unit rows, timeline, total)`` of the restart of the tripped ``units`` after ``transition``.

    ``units``: ``unit``, ``tier`` (one of ``TIER_ORDER`` or blank), ``rated_mw``, ``restart_time_h``,
    ``rate_tph``, ``mass_pull``, ``motor_kw``, ``start_method`` and optionally ``i_start_pu``; NaN/None for not
    given. ``transition``: ``trip`` (screened on ``poc_source``) or ``island`` (on ``island_source``).
    ``limit_pct``: the rapid-voltage-change limit, None for not set."""
    if transition not in TRANSITIONS:
        raise ContractError(f"transition {transition!r} is not one of {', '.join(TRANSITIONS)}")
    for col in ("unit", "tier", "rated_mw", "restart_time_h", "rate_tph", "mass_pull", "motor_kw", "start_method"):
        if col not in units.columns:
            raise ContractError(f"units needs a {col!r} column")
    if units["unit"].duplicated().any():
        raise ContractError(f"duplicate unit(s): {sorted(units.loc[units['unit'].duplicated(), 'unit'])}")
    if not (math.isfinite(pickup_interval_s) and pickup_interval_s > 0):
        raise ContractError(f"pickup interval must be above 0 s, got {pickup_interval_s:g}")
    source = poc_source if transition == "trip" else island_source
    src_name = "the PoC" if transition == "trip" else "the island"

    ordered, tier_notes = _order(units)
    block_of = _blocks_of(ordered, pickup_blocks)
    rows, events = [], []
    for rec in ordered:
        name = rec["unit"]
        restart, rate, mp = (_num(rec.get(c)) for c in ("restart_time_h", "rate_tph", "mass_pull"))
        for label, v in (("restart time", restart), ("rate", rate), ("mass pull", mp)):
            if v is not None and v < 0:
                raise ContractError(f"{name}: {label} must be 0 or more, got {v:g}.")
        notes = [tier_notes[name]] if name in tier_notes else []
        events.append({"t_s": 0.0, "unit": name, "event": transition, "block": None, "du_pct": None,
                       "verdict": None, "note": f"{name} trips"})
        row = {"unit": name, "tier": rec["tier"], "rated_mw": _num(rec.get("rated_mw")), "restart_time_h": restart,
               "rate_tph": rate, "mass_pull": mp, "label": LABEL}
        k = block_of[name]
        if k is None:
            why = (f"{NO_BLOCK}: {row['rated_mw']:g} MW is above every pickup block"
                   if row["rated_mw"] is not None else "no rated MW to place in a pickup block")
            verdict = "fail" if row["rated_mw"] is not None else "unknown"
            rows.append({**row, "block": None, "pickup_s": None, "full_rate_s": None, "du_pct": None,
                         "start_verdict": verdict, "lost_wait_t": None, "lost_restart_t": None, "lost_t": None,
                         "verdict": verdict, "note": "; ".join([why, *notes])})
            continue
        t_pick = T_FIRST_S + k * pickup_interval_s
        du, sv, snote = _start(rec, source, src_name, q_gfm_mvar, limit_pct)
        events.append({"t_s": t_pick, "unit": name, "event": "pickup", "block": k + 1, "du_pct": du,
                       "verdict": sv, "note": f"motor start on {src_name}: {snote}"})
        notes.insert(0, f"motor start on {src_name} {sv}")
        missing = [lab for lab, v in (("restart time", restart), ("rate (t/h)", rate), ("mass pull", mp))
                   if v is None]
        if missing:
            rows.append({**row, "block": k + 1, "pickup_s": t_pick, "full_rate_s": None, "du_pct": du,
                         "start_verdict": sv, "lost_wait_t": None, "lost_restart_t": None, "lost_t": None,
                         "verdict": "unknown", "note": "; ".join([f"no input: {' and '.join(missing)} not set",
                                                                  *notes])})
            continue
        t_full = t_pick + restart * 3600.0
        events.append({"t_s": t_full, "unit": name, "event": "full_rate", "block": k + 1, "du_pct": None,
                       "verdict": None, "note": f"{name} back at full rate"})
        wait_t, restart_t = t_pick / 3600.0 * rate * mp, restart * rate * mp
        if t_full > HORIZON_S + TOLERANCE:
            notes.append(BEYOND)
        rows.append({**row, "block": k + 1, "pickup_s": t_pick, "full_rate_s": t_full, "du_pct": du,
                     "start_verdict": sv, "lost_wait_t": wait_t, "lost_restart_t": restart_t,
                     "lost_t": wait_t + restart_t, "verdict": sv, "note": "; ".join(notes)})

    table = pd.DataFrame(rows, columns=list(UNIT_COLUMNS))
    timeline = pd.DataFrame(events, columns=list(EVENT_COLUMNS)).sort_values("t_s", kind="stable")
    known = table[table["lost_t"].notna()]
    # A failed start counts as a fail even when the unit's tonnes are unknown for a missing input (both count).
    fails = int((table["start_verdict"] == "fail").sum())
    unknowns = int((table["verdict"] == "unknown").sum())
    total = {"transition": transition, "lost_t": float(known["lost_t"].sum()),
             "lost_restart_t": float(known["lost_restart_t"].sum()),
             "lost_wait_t": float(known["lost_wait_t"].sum()), "fail_count": fails, "unknown_count": unknowns,
             "lower_bound": bool(fails or unknowns or table["lost_t"].isna().any()),
             "end_s": float(timeline["t_s"].max()) if len(timeline) else 0.0}
    return table, timeline.reset_index(drop=True), total
