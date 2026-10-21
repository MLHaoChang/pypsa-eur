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
#: A unit's response latency by its effective control, after its control
#: preset (DC 04) is expanded: how long before it answers a load step. All
#: assumed: a grid-forming converter is a voltage source and answers within a
#: cycle; a grid-following one runs a measured power loop; an FFR unit waits
#: its own ``fw_delay_s``; an online UPS sits in series with its IT load.
LATENCY_S = {"gfm_droop": 0.02, "gfm_vsm": 0.02, "gfl_pq": 0.2, "gfl_qu": 0.2, "gfl_pf": 0.2, "ups": 0.0}
CONTROL_WORDS = {"gfm_droop": "grid forming", "gfm_vsm": "grid forming", "gfl_pq": "grid following",
                 "gfl_qu": "grid following", "gfl_pf": "grid following", "gfl_fw": "fast frequency response",
                 "ups": "UPS"}

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


def response_latency(name: str, block: dict) -> dict:
    """``{unit, kind, latency_s, basis}``: the unit's assumed response
    latency from its effective control; ``latency_s`` None when the control
    is not known."""
    kind = block.get("kind")
    control = "ups" if kind == "ups" else _value(block.get("control"))
    if control == "gfl_fw":
        raw = (block.get("values") or {}).get("fw_delay_s", block.get("fw_delay_s"))
        latency = _value(raw)
    else:
        latency = LATENCY_S.get(control)
    return {"unit": name, "kind": kind, "latency_s": None if latency is None else float(latency),
            "basis": CONTROL_WORDS.get(control, "control not known")}


def latency_words(latency: list[dict]) -> str:
    """"'u' responds in 0 s (UPS, assumed)", or several in one line."""
    def one(x):
        t = "not known" if x["latency_s"] is None else f"{x['latency_s']:g} s"
        return t, x["basis"]
    if len(latency) == 1:
        t, b = one(latency[0])
        return f"'{latency[0]['unit']}' responds in {t} ({b}, assumed)"
    return ", ".join(f"'{x['unit']}' {one(x)[0]} ({one(x)[1]})" for x in latency) + "; all assumed"


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
    clamped at 0 on its own): ``{mw, mwh, backup_mwh, units, unsized, notes}``."""
    su = n.storage_units
    units = island.get("units") or {}
    req = island.get("requirements") or {}
    _, mask = _period(n, year)
    out = {"mw": 0.0, "mwh": 0.0, "backup_mwh": 0.0, "units": [], "unsized": [], "notes": []}
    effective = _effective_units(island)
    rt = ride_through_batteries(n, island, year)
    out["unsized"] += rt["unsized"]
    if rt["names"]:
        keep, words = outage_reserve_mwh(n, island, mask, delivered_mw(n), year)
        out["mw"] += rt["p_mw"]
        out["mwh"] += max(0.0, rt["e_mwh"] - keep)
        out["backup_mwh"] += keep
        out["units"] += rt["names"]
        out["notes"].append(f"ride-through {', '.join(rt['names'])} keeps the {words}")
    upss, unsized = committed_storage(n, [u for u, d in units.items() if d.get("kind") == "ups"], year)
    out["unsized"] += unsized
    bridge_s = float(_value(req.get("bridge_s")) or 0.0)
    for u in upss:
        p = float(su.at[u, "p_nom"])
        it = [x for x in units[u].get("it_load") or [] if x in n.loads.index]
        carried = min(p, float(named_load(n, it)[mask].max())) if it else p
        keep = carried * bridge_s / 3600.0
        out["mw"] += p
        out["mwh"] += max(0.0, deliverable_mwh(su, u) - keep)
        out["backup_mwh"] += keep
        out["units"].append(u)
        out["notes"].append(f"UPS {u}: {keep:.3g} MWh kept to bridge {carried:.3g} MW for {bridge_s:g} s"
                            + ("" if it else " (its IT load is not in the network: its rating)"))
    out["latency"] = [response_latency(u, effective.get(u) or units.get(u) or {}) for u in out["units"]]
    return out


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
    # The checkpoint dip charges and gives back at most S²/(2R) (≤ e_step): it
    # sits inside the step's band, so it adds to the repeat rate, not the band.
    need_mw, need_mwh = s + a_x, 2.0 * e_step + e_osc
    # The slowest unit's latency lets the step through at the envelope's rate
    # until the battery answers: ℓ = min(S, dP/dt × L).
    known = [x["latency_s"] for x in reserve.get("latency", []) if x["latency_s"] is not None]
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
    short = [w for w, ok in (("MW", reserve["mw"] >= need_mw - 1e-9),
                             ("MWh", reserve["mwh"] >= need_mwh - 1e-9)) if not ok]
    words = [f"a {s:.3g} MW step through the {ramp:g} MW/min limit takes {s / ramp:.3g} min and "
             f"{e_step:.3g} MWh each way",
             (f"oscillation {a_x:.3g} MW above the limit, {e_osc:.2g} MWh of swing" if a_x > 0
              else "the oscillation needs no smoothing" + ("" if in_band else " (outside the band of concern)")),
             f"need {need_mw:.3g} MW / {need_mwh:.3g} MWh against {reserve['mw']:.3g} MW / "
             f"{reserve['mwh']:.3g} MWh left after backup", *reserve["notes"],
             latency_words(reserve["latency"]) + (f", so {leak:.3g} MW reach the grid before it answers"
                                                   if leak > 0 else ""), CAVEATS]
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
    from one screened phase (``_phase``): ``{step_mw, covered_step_mw,
    residual_step_mw, amplitude_mw, residual_amplitude_mw, period_s}``, or
    None when the phase was not screened (no battery/UPS, no ramp limit).

    The band a covered step S_c takes scales with S_c², so the reserve's
    energy covers S_c = S·√min(1, E_r/band) and its power S_c ≤ P_r. What is
    left after the band and the step's MW goes to the oscillation, up to its
    whole amplitude: A_c = min(A, P_r − S_c, (E_r − band(S_c))·π/T). The
    response latency's leak ℓ reaches the grid however large the reserve:
    the residual step is max(S − S_c, ℓ)."""
    if ph.get("verdict") not in ("covered", "short"):
        return None
    s, p, e = ph["step_mw"], ph["reserve_mw"], ph["reserve_mwh"]
    band = 2.0 * ph["step_mwh"]
    s_c = min(p, s * math.sqrt(min(1.0, e / band)) if band > 0 else s)
    left = max(0.0, e - band * (s_c / s) ** 2) if s > 0 else e
    amp, period = ph.get("amplitude_mw", 0.0), ph.get("period_s", 0.0)
    a_c = min(amp, max(0.0, p - s_c), left * math.pi * 3600.0 / period if period > 0 else 0.0)
    residual = max(0.0, s - s_c, ph.get("leak_mw", 0.0))
    return {"step_mw": s, "covered_step_mw": s - residual, "residual_step_mw": residual,
            "amplitude_mw": amp, "residual_amplitude_mw": max(0.0, amp - a_c), "period_s": period}
