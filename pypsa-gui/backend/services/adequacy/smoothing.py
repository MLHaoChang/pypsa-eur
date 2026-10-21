"""
The load-smoothing screen (DC 03, owner decision RLR-527): can the site's
battery/UPS hold the AI-training load's steps and oscillation inside the
connection's ramp and oscillation limits, from the reserve its backup duty
leaves?

One battery/UPS, two duties, no double credit: the backup duty is reserved
first and smoothing gets what is left.

* **Backup duty, energy only.** The ride-through batteries keep the island
  study's outage reserve R (``site_peak.outage_reserve_mwh``, the one the
  peak check keeps): (critical load − firm gensets)⁺ × ``sustained_h``, plus
  the seamless bridge. A UPS keeps the energy to bridge its IT load for
  ``bridge_s``. Their power is free for smoothing while the grid is there:
  they carry the load only once it is lost, and then smoothing stops.
* **Smoothing need**, closed form per envelope phase (IT side at the PoC, as
  the scorecard's ramp and oscillation rows read it), R the ramp limit:

  - a step S (training starts or stops) through a ramp limit R needs ≈ S MW
    and S²/(2R) MWh, discharging on the way up and charging on the way down,
    so the battery holds a band of S²/R MWh above its backup floor;
  - a checkpoint dip of depth S for d s needs min(S·d, S²/(2R)) MWh (hold the
    grid flat, or ramp it), inside the step's band, every checkpoint interval;
  - an oscillation of amplitude A and period T inside the band of concern
    needs the part above the limit, A_x = max(0, A − A_lim), in MW and
    A_x·T/π MWh of swing; outside the band it needs nothing.

  Need = S + A_x MW and S²/R + A_x·T/π MWh. Up and down share one
  state-of-charge band above the backup floor: the battery sits where it can
  both give E_up and take E_down, so E_up + E_down ≤ e − R (UPS: e − bridge).

Who shapes the step (``coverage``): a converter battery under plant control
holds it to the ramp limit; a UPS's rectifier walks its share onto the grid
over its own ``walk_in_s`` (gridspine's UPS block, 10-15 s in the presets),
so the grid sees 60·S_u/walk_in_s MW/min, within the limit only when the
walk-in is at least 60·S_u/R. Batteries take the step first, UPSs the rest.

A unit counts in a phase when it is in service at the phase's COD year (the
asset must exist when that load does); the reserve R reads the hours of the
investment period the phase falls in, as the peak check does. Each
StorageUnit counts once: a UPS also named for ride-through keeps both its
bridge and its share of R.

Only committed (non-extendable) StorageUnits count, as in the peak check: an
extendable unit is sized by the run, so the screen cannot know its rating
(one sizing basis with the peak check). Nothing is time-stepped, solved or
stored; no input is new. The screen reads the envelope's phases only: it is
blind to in-hour ramps beyond them (the hourly-mean profile carries none).
Energies are deliverable MWh; the charging side takes 1/η_store more from the
grid than it stores, which the screen does not add (a few per cent).
"""
from __future__ import annotations

import math

from services.adequacy.site_peak import (_periods, _value, committed_storage, deliverable_mwh, delivered_mw,
                                         named_load, outage_reserve_mwh, period_year,
                                         ride_through_batteries)

NO_BATTERY = "unknown, no battery/UPS"
#: A converter battery's response latency by its effective control, after
#: its control preset (DC 04) is expanded: how long before it answers a load
#: step. All assumed: a grid-forming converter is a voltage source and answers
#: within a cycle; a grid-following one runs a measured power loop; an FFR
#: unit waits its own ``fw_delay_s``. A UPS has no latency here: its share
#: reaches the grid over its rectifier's walk-in (``coverage``).
LATENCY_S = {"gfm_droop": 0.02, "gfm_vsm": 0.02, "gfl_pq": 0.2, "gfl_qu": 0.2, "gfl_pf": 0.2}
CONTROL_WORDS = {"gfm_droop": "grid forming", "gfm_vsm": "grid forming", "gfl_pq": "grid following",
                 "gfl_qu": "grid following", "gfl_pf": "grid following", "gfl_fw": "fast frequency response"}

#: The screen's own limits, on every result.
CAVEATS = ("closed form on the envelope's phases, blind to in-hour ramps beyond them; energies as "
           "delivered, charging losses not added")


def step_mwh(step_mw: float, ramp_mw_per_min: float) -> float:
    """The energy a battery moves while the grid ramps through a step S at R:
    the triangle S·(S/R)/2, in MWh."""
    return step_mw * step_mw / (2.0 * ramp_mw_per_min * 60.0)


def checkpoint_mwh(depth_mw: float, duration_s: float, ramp_mw_per_min: float) -> float:
    """A checkpoint dip's energy: hold the grid flat for the dip (S·d) or ramp
    it down and back (S²/(2R)), whichever is less."""
    return min(depth_mw * duration_s / 3600.0, step_mwh(depth_mw, ramp_mw_per_min))


def oscillation_mwh(amplitude_mw: float, period_s: float) -> float:
    """The swing of a sinusoid of amplitude A and period T: A·T/π, in MWh."""
    return amplitude_mw * period_s / (math.pi * 3600.0)


def _effective_units(island: dict) -> dict:
    """The sidecar's units with their control presets expanded (gridspine's
    validation), or the raw units when it cannot validate them."""
    from services.adequacy.island_config import ContractError, validate

    try:
        return validate(island)["units"]
    except (ContractError, KeyError, TypeError, ValueError):
        return island.get("units") or {}


def _walk_in_s(block: dict) -> float | None:
    """A UPS's walk-in time from its (effective or raw) island block."""
    raw = (block.get("values") or {}).get("walk_in_s", block.get("walk_in_s"))
    v = _value(raw)
    return None if v is None else float(v)


def response_latency(name: str, block: dict) -> dict:
    """``{unit, kind, latency_s, walk_in_s, basis}``: a converter battery's
    assumed response latency from its effective control (``latency_s`` None
    when the control is not known); a UPS's walk-in instead."""
    kind = block.get("kind")
    if kind == "ups":
        w = _walk_in_s(block)
        return {"unit": name, "kind": kind, "latency_s": None, "walk_in_s": w,
                "basis": "UPS, walks in over " + ("an unknown time" if w is None else f"{w:g} s")}
    control = _value(block.get("control"))
    if control == "gfl_fw":
        latency = _value((block.get("values") or {}).get("fw_delay_s", block.get("fw_delay_s")))
    else:
        latency = LATENCY_S.get(control)
    return {"unit": name, "kind": kind, "latency_s": None if latency is None else float(latency),
            "walk_in_s": None, "basis": CONTROL_WORDS.get(control, "control not known")}


def latency_words(latency: list[dict]) -> str:
    """"'u' responds in 0.02 s (grid forming, assumed)", "'u' walks in over
    15 s (UPS, assumed)", or several in one line."""
    def one(x, lead):
        if x["kind"] == "ups":
            return (f"walks in over {x['walk_in_s']:g} s" if x["walk_in_s"] is not None
                    else "walks in over an unknown time"), "UPS"
        t = "not known" if x["latency_s"] is None else f"{x['latency_s']:g} s"
        return (f"responds in {t}" if lead else t), x["basis"]
    if len(latency) == 1:
        t, b = one(latency[0], True)
        return f"'{latency[0]['unit']}' {t} ({b}, assumed)"
    return ", ".join(f"'{x['unit']}' {one(x, False)[0]} ({one(x, False)[1]})" for x in latency) + "; all assumed"


def _year(cod: str) -> int | None:
    try:
        return int(str(cod)[:4])
    except ValueError:
        return None


def _period(n, year: int | None):
    """(year, mask) of the investment period a phase falls in, as the peak
    check labels them: the last period starting at or before ``year`` (the
    first when none does); the one period of a single-period network."""
    periods = _periods(n)
    if year is None or len(periods) == 1:
        label, mask = periods[0]
        return period_year(label), mask
    dated = [(period_year(lab), m) for lab, m in periods if period_year(lab) is not None]
    before = [(y, m) for y, m in dated if y <= year]
    return (before[-1] if before else dated[0]) if dated else (None, periods[0][1])


def _reserve(n, island: dict, year: int | None) -> dict:
    """What the committed ride-through batteries and UPSs in service in
    ``year`` (the phase's) hold for smoothing after their backup duty, read
    over the hours of the period the phase falls in (energy only, each group
    clamped at 0 on its own): ``{mw, mwh, backup_mwh, units, unsized, notes,
    battery: {mw, mwh}, ups: {mw, mwh, walk_in_s}}``. Batteries shape a step
    to the ramp limit under plant control; a UPS's rectifier walks its load
    onto the grid over its own ``walk_in_s`` (the fastest of them is kept)."""
    su = n.storage_units
    units = island.get("units") or {}
    req = island.get("requirements") or {}
    _, mask = _period(n, year)
    out = {"mw": 0.0, "mwh": 0.0, "backup_mwh": 0.0, "units": [], "unsized": [], "notes": [],
           "battery": {"mw": 0.0, "mwh": 0.0}, "ups": {"mw": 0.0, "mwh": 0.0, "walk_in_s": None}}
    effective = _effective_units(island)
    rt = ride_through_batteries(n, island, year)
    out["unsized"] += rt["unsized"]
    upss, unsized = committed_storage(n, [u for u, d in units.items() if d.get("kind") == "ups"], year)
    out["unsized"] += unsized
    bridge_s = float(_value(req.get("bridge_s")) or 0.0)
    # Island row (b): each UPS keeps η·e ≥ P_it·bridge_s/3600, the whole IT
    # load it carries (its rating when that load is not in the network).
    bridge = {}
    for u in upss:
        it = [x for x in units[u].get("it_load") or [] if x in n.loads.index]
        carried = float(named_load(n, it)[mask].max()) if it else float(su.at[u, "p_nom"])
        bridge[u] = (carried * bridge_s / 3600.0, carried, bool(it))

    def bridge_note(u):
        keep, carried, known = bridge[u]
        return (f"UPS {u}: {keep:.3g} MWh kept to bridge {carried:.3g} MW for {bridge_s:g} s"
                + ("" if known else " (its IT load is not in the network: its rating)"))

    if rt["names"]:
        # A UPS also named for ride-through is one StorageUnit: counted once,
        # in this group, keeping its bridge on top of the outage reserve.
        keep, words = outage_reserve_mwh(n, island, mask, delivered_mw(n), year)
        shared = [u for u in upss if u in rt["names"]]
        keep += sum(bridge[u][0] for u in shared)
        out["mw"] += rt["p_mw"]
        out["mwh"] += max(0.0, rt["e_mwh"] - keep)
        out["battery"] = {"mw": rt["p_mw"], "mwh": max(0.0, rt["e_mwh"] - keep)}
        out["backup_mwh"] += keep
        out["units"] += rt["names"]
        out["notes"] += [f"ride-through {', '.join(rt['names'])} keeps the {words}",
                         *(bridge_note(u) for u in shared)]
    for u in upss:
        if u in rt["names"]:
            continue
        keep = bridge[u][0]
        out["mw"] += float(su.at[u, "p_nom"])
        out["mwh"] += max(0.0, deliverable_mwh(su, u) - keep)
        out["ups"]["mw"] += float(su.at[u, "p_nom"])
        out["ups"]["mwh"] += max(0.0, deliverable_mwh(su, u) - keep)
        w = _walk_in_s(effective.get(u) or units.get(u) or {})
        if w is not None:
            prev = out["ups"]["walk_in_s"]
            out["ups"]["walk_in_s"] = w if prev is None else min(prev, w)
        out["backup_mwh"] += keep
        out["units"].append(u)
        out["notes"].append(bridge_note(u))
    out["latency"] = [response_latency(u, effective.get(u) or units.get(u) or {}) for u in out["units"]]
    return out


def coverage(s: float, e_one: float, amp: float, period: float, ramp: float, reserve: dict) -> dict:
    """How much of a step S and an oscillation (A, T) the reserve holds, by
    who holds it. The batteries take the step first and shape it to the ramp
    limit: their band 2·e_one scales with S², so they cover
    S_b = min(P_b, S·√min(1, E_b/band)). A UPS takes what is left, up to its
    MW and the energy of its walk-in triangle up and down (S_u·w/3600 MWh),
    and the grid sees its share at 60·S_u/w MW/min. What power and energy
    are left go to the oscillation, up to its whole amplitude:
    A_c = min(A, P_left, E_left·π·3600/T)."""
    bat, ups = reserve.get("battery") or {}, reserve.get("ups") or {}
    p_b, e_b = float(bat.get("mw", 0.0)), float(bat.get("mwh", 0.0))
    p_u, e_u, w = float(ups.get("mw", 0.0)), float(ups.get("mwh", 0.0)), ups.get("walk_in_s")
    band = 2.0 * e_one
    s_b = min(p_b, s * math.sqrt(min(1.0, e_b / band)) if band > 0 else s, s)
    used_b = band * (s_b / s) ** 2 if s > 0 else 0.0
    s_u = 0.0
    if w and w > 0 and p_u > 0:
        s_u = max(0.0, min(p_u, s - s_b, e_u * 3600.0 / w))
    used_u = s_u * w / 3600.0 if s_u > 0 else 0.0
    left_p = max(0.0, p_b - s_b) + max(0.0, p_u - s_u)
    left_e = max(0.0, e_b - used_b) + max(0.0, e_u - used_u)
    a_c = min(amp, left_p, left_e * math.pi * 3600.0 / period if period > 0 else 0.0)
    return {"covered_battery_mw": s_b, "covered_ups_mw": s_u, "ups_walk_in_s": w,
            "ups_ramp_mw_per_min": 60.0 * s_u / w if s_u > 0 else 0.0, "oscillation_covered_mw": a_c}


def _phase(ph: dict, reserve: dict, ramp: float | None, osc_limit_pct: float | None,
           connection_mw: float | None, band_hz: tuple[float, float]) -> dict:
    s = float(ph["swing_mw"])
    ck, osc = ph.get("checkpoint") or {}, ph.get("oscillation") or {}
    row = {"cod": ph.get("cod"), "it_mw": ph.get("it_mw"), "step_mw": s}
    if ramp is None or ramp <= 0:
        return {**row, "verdict": "unknown", "note": "no connection ramp limit to smooth to"}
    e_step = step_mwh(s, ramp)
    e_ck = checkpoint_mwh(float(ck.get("depth_mw", 0.0)), float(ck.get("duration_s", 0.0)), ramp)
    interval = float(ck.get("interval_s") or 0.0)
    amp, period = float(osc.get("amplitude_mw", 0.0)), float(osc.get("period_s", 0.0))
    hz = 1.0 / period if period > 0 else math.inf
    in_band = band_hz[0] <= hz <= band_hz[1]
    a_lim = (osc_limit_pct / 100.0 * connection_mw / 2.0
             if osc_limit_pct is not None and connection_mw else None)
    a_x = (max(0.0, amp - a_lim) if a_lim is not None else amp) if in_band else 0.0
    e_osc = oscillation_mwh(a_x, period) if a_x > 0 else 0.0
    # The band holds the larger of the step and the checkpoint dip (the dip is
    # inside it when its depth is at most the swing, as the envelope makes it).
    need_mw, need_mwh = s + a_x, 2.0 * max(e_step, e_ck) + e_osc
    # The slowest converter battery's latency lets the step through at the
    # envelope's rate until it answers: ℓ = min(S, dP/dt × L). For a true step
    # (dP/dt → ∞) ℓ is the whole step for any L > 0, which is what a rapid
    # voltage change sees in those first L seconds.
    known = [x["latency_s"] for x in reserve.get("latency", [])
             if x["kind"] != "ups" and x["latency_s"] is not None and x["unit"] in reserve["units"]]
    slope = float(ck.get("dp_dt_mw_per_s") or ph.get("max_dp_dt_mw_per_s") or 0.0)
    leak = min(s, slope * max(known)) if known else 0.0
    row.update({
        "ramp_limit_mw_per_min": ramp, "ramp_minutes": s / ramp,
        "step_mwh": e_step, "checkpoint_mwh": e_ck,
        "checkpoints_per_day": 86400.0 / interval if interval > 0 and e_ck > 0 else 0.0,
        "oscillation_excess_mw": a_x, "oscillation_mwh": e_osc, "oscillation_in_band": in_band,
        "amplitude_mw": amp, "period_s": period,
        "need_mw": need_mw, "need_mwh": need_mwh,
        "reserve_mw": reserve["mw"], "reserve_mwh": reserve["mwh"],
        "backup_mwh": reserve["backup_mwh"],
        "units": list(reserve["units"]), "latency": list(reserve.get("latency", [])), "leak_mw": leak,
    })
    if not reserve["units"]:
        why = (f"; {', '.join(reserve['unsized'])} sized by the run, not counted" if reserve["unsized"] else "")
        return {**row, "verdict": "unknown", "note": NO_BATTERY + why}
    cover = coverage(s, max(e_step, e_ck), amp, period, ramp, reserve)
    row.update(cover)
    short = [w for w, ok in (("MW", reserve["mw"] >= need_mw - 1e-9),
                             ("MWh", reserve["mwh"] >= need_mwh - 1e-9),
                             ("UPS walk-in", cover["ups_ramp_mw_per_min"] <= ramp + 1e-9),
                             ("UPS walk-in not known", not (reserve["ups"]["mw"] > 0
                                                            and reserve["ups"]["walk_in_s"] is None))) if not ok]
    words = [f"a {s:.3g} MW step through the {ramp:g} MW/min limit takes {s / ramp:.3g} min and "
             f"{e_step:.3g} MWh each way",
             (f"oscillation {a_x:.3g} MW above the limit, {e_osc:.2g} MWh of swing" if a_x > 0
              else "the oscillation needs no smoothing" + ("" if in_band else " (outside the band of concern)")),
             f"need {need_mw:.3g} MW / {need_mwh:.3g} MWh against {reserve['mw']:.3g} MW / "
             f"{reserve['mwh']:.3g} MWh left after backup", *reserve["notes"],
             latency_words(reserve["latency"]) + (f", so {leak:.3g} MW reach the grid before it answers"
                                                   if leak > 0 else ""), CAVEATS]
    if cover["covered_ups_mw"] > 0:
        words.append(f"the UPS carries {cover['covered_ups_mw']:.3g} MW of the step and walks it onto the "
                     f"grid over {cover['ups_walk_in_s']:g} s, {cover['ups_ramp_mw_per_min']:.3g} MW/min"
                     + ("" if cover["ups_ramp_mw_per_min"] <= ramp + 1e-9
                        else f", above the {ramp:g} MW/min limit"))
    if reserve["unsized"]:
        words.append(f"{', '.join(reserve['unsized'])} sized by the run, not counted")
    return {**row, "verdict": "short" if short else "covered", "short": short, "note": "; ".join(words)}


def smoothing_screen(n, island: dict | None, envelope: dict | None, *, ramp_limit_mw_per_min: float | None,
                     oscillation_limit_pct: float | None, connection_mw: float | None,
                     band_hz: tuple[float, float] = (0.1, 2.0)) -> dict | None:
    """The screen per envelope phase: ``{"verdict", "note", "phases": [{cod,
    step_mw, step_mwh, checkpoint_mwh, oscillation_mwh, need_mw, need_mwh,
    reserve_mw, reserve_mwh, backup_mwh, units, verdict, note}]}``.
    The overall verdict is the worst phase's (short over unknown over covered).
    ``None`` without an AI-training envelope: nothing to screen."""
    if not envelope or not envelope.get("phases"):
        return None
    if n is None or not island:
        return {"verdict": "unknown", "note": NO_BATTERY, "phases": [], "caveats": CAVEATS}
    phases = [_phase(ph, _reserve(n, island, _year(ph.get("cod"))), ramp_limit_mw_per_min,
                     oscillation_limit_pct, connection_mw, band_hz) for ph in envelope["phases"]]
    order = {"short": 0, "unknown": 1, "covered": 2}
    worst = min(phases, key=lambda p: order[p["verdict"]])
    return {"verdict": worst["verdict"], "note": worst["note"], "phases": phases, "caveats": CAVEATS}


def after_mitigation(ph: dict) -> dict | None:
    """What the point of connection sees once the battery/UPS has smoothed,
    from one screened phase (``_phase``, its ``coverage``): ``{step_mw,
    covered_battery_mw, covered_ups_mw, ups_ramp_mw_per_min, covered_step_mw,
    residual_step_mw, amplitude_mw, residual_amplitude_mw, period_s}``, or None
    when the phase was not screened (no battery/UPS, no ramp limit). The
    batteries' share reaches the grid at the ramp limit, the UPSs' at their
    walk-in rate, the rest unshaped."""
    if ph.get("verdict") not in ("covered", "short"):
        return None
    s = ph["step_mw"]
    # The latency's leak ℓ reaches the grid however large the reserve: the
    # residual step is max(S − covered, ℓ), taken from the batteries' share.
    leak = ph.get("leak_mw", 0.0)
    covered = min(s, ph["covered_battery_mw"] + ph["covered_ups_mw"], s - leak)
    amp = ph.get("amplitude_mw", 0.0)
    return {"step_mw": s, "covered_battery_mw": max(0.0, covered - ph["covered_ups_mw"]),
            "covered_ups_mw": ph["covered_ups_mw"], "ups_ramp_mw_per_min": ph["ups_ramp_mw_per_min"],
            "ups_walk_in_s": ph["ups_walk_in_s"], "covered_step_mw": covered,
            "residual_step_mw": max(0.0, s - covered), "amplitude_mw": amp,
            "residual_amplitude_mw": max(0.0, amp - ph["oscillation_covered_mw"]),
            "period_s": ph.get("period_s", 0.0)}
