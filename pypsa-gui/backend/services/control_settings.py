"""Control-settings sheet (AS-07): what an EMT or HiL study needs from the sized plant.

After the forward pass the sized fast tiers (AS-02 split storage, sized by the AS-03 envelope floors) and the
island data become one sheet of settings, each traced to the capacity it comes from. Pure functions; nothing is
solved here. `sheet_from_solved` reads a solved network, `build_sheet` takes the numbers directly, and
`write_sheet` exports the sheet next to the PSS/E dyr. All limits are generic engineering, "assumed" (SAGC-1).

Settings (`rows`, one per setting and unit):

  ramp_limiter                MW/min at the PoC: the ramp the floors were sized with.
  smoothing_filter_fc         first-order low-pass cut-off in Hz: min(f_ramp, f_osc), f_ramp = R/60 / (2π S) so a
                              full step S passes no faster than R, f_osc = 1/T the oscillation. Assumed first
                              order; EMT confirms the shape.
  tier_split.<store>          each smoothing store's share of the summed smoothing power and energy.
  soc_band.<store>            the store's SOC bounds in MWh: backup_floor (e_min x E plus the backup energy held
                              as stored energy), smoothing_lower = backup_floor + E_sm/2, smoothing_upper =
                              e_max x E - E_sm/2, the LP's own band (`envelope_floors`). `fits` is False when
                              lower exceeds upper. The backup MWh (AC, through the discharger) is shared over
                              the backup stores by energy rating.
  latency.<store>             the tier's response latency against the band limit 1/(2π f), f = the highest
                              oscillation frequency of the envelope. Latency must be strictly below the limit;
                              a tier at or above it is flagged. Latency is the control's assumed one
                              (`smoothing.response_latency`) unless the tier gives its own.
  gfm_droop.<u>, gfm_h.<u>    droop (%) and H (s) from `gfm_tuning.recommend_gfm_settings`; flagged when the
                              search was not achievable or incomplete, or was not run (`gfm_tuning`).
  ups_walk_in.<u>             s; flagged when the UPS rating is known and the walk-in is below 60 x P_ups / R
                              (the rectifier would ramp the grid faster than the limit).
  ups_transfer.<u>            the f window and whether it transfers on islanding.
  ups_disturbance_counting.<u>  "none": a UPS walk-in or transfer is not counted as a disturbance (ERCOT NOGRR 282).
  preset                      the project preset `select_preset` selected (None: none).
  latency (not checked)       the single row that stands in for the latency rows when the envelope has no
                              oscillation, so no band exists; status "not checked", not a failure.
  emt.*                       sub-cycle items no steady-state or RMS check can settle, status "needs EMT".

Row columns: id, group, setting, value, unit, source ("sized" from a capacity, "assumed"), status ("set", "ok",
"flagged", "not checked", "needs EMT"), traces_to (the asset or function the value comes from), note. A "needs EMT" row never
counts as a failure; a "flagged" row does, and `sheet["ok"]` is False with `sheet["flags"]` naming it.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from services.adequacy.site_peak import _value
from services.adequacy.smoothing import response_latency
from services.adequacy.spectrum import store_latency
from services.solver import envelope_floors as EF

FORMAT = (
    "control_settings v1. The JSON file holds {format, ramp_mw_per_min, latency_limit_s, ok, flags, rows}; the CSV "
    "holds the rows with columns id, group, setting, value, unit, source, status, traces_to, note (a structured "
    "value is JSON text). source is 'sized' (from a sized capacity) or 'assumed' (generic engineering, SAGC-1). "
    "status is 'set' (a setting to apply), 'ok' (a check passed), 'flagged' (a check failed or could not run: "
    "ok is then false), 'not checked' (no input to check against, e.g. no oscillation for the latency band; "
    "not a failure) or 'needs EMT' (sub-cycle, outside a steady-state or RMS check; never a failure). "
    "traces_to names the asset or function behind the value."
)
CSV_COLUMNS = ("id", "group", "setting", "value", "unit", "source", "status", "traces_to", "note")

#: Sub-cycle items: (id, setting, why it needs EMT). All assumed.
_EMT = (
    ("emt.current_loop", "converter current-loop bandwidth and limiter",
     "acts inside one cycle; RMS phasor models do not represent it"),
    ("emt.pll", "PLL bandwidth of grid-following tiers",
     "its dynamics are sub-cycle and depend on grid strength"),
    ("emt.gfm_current_limit", "GFM current-limit action in a fault or step",
     "the limiter engages within milliseconds"),
    ("emt.ups_transfer_switching", "UPS transfer and static-switch transient",
     "the switching transient lasts under a cycle"),
)


class ControlSettingsError(ValueError):
    """The solved network cannot give the sheet its sized slices; raised instead of reading them as zero."""


def band_limit_s(f_hz: float) -> float:
    """The latency a tier must beat to act inside a band at f: 1 / (2π f), in s."""
    return 1.0 / (2.0 * math.pi * f_hz)


def soc_band(e_mwh: float, e_sm_mwh: float, e_min_pu: float = 0.0, e_max_pu: float = 1.0,
             backup_stored_mwh: float = 0.0) -> dict:
    """The store's SOC bounds in MWh: the backup floor, and the smoothing band half E_sm in from each end."""
    floor = e_min_pu * e_mwh + backup_stored_mwh
    lower, upper = floor + e_sm_mwh / 2.0, e_max_pu * e_mwh - e_sm_mwh / 2.0
    return {"smoothing_lower_mwh": lower, "smoothing_upper_mwh": upper, "backup_floor_mwh": floor,
            "fits": lower <= upper + 1e-9}


def _row(id_: str, group: str, setting: str, value, unit: str, source: str, status: str, traces_to: str,
         note: str = "", **extra) -> dict:
    return {"id": id_, "group": group, "setting": setting, "value": value, "unit": unit, "source": source,
            "status": status, "traces_to": traces_to, "note": note, **extra}


def _envelope_terms(envelope: dict) -> tuple[float, float | None]:
    """(largest swing S in MW, highest oscillation frequency in Hz or None) over the phases."""
    swing, f_osc = 0.0, None
    for ph in envelope["phases"]:
        swing = max(swing, float(ph["swing_mw"]))
        period = float((ph.get("oscillation") or {}).get("period_s", 0.0))
        if period > 0 and float((ph.get("oscillation") or {}).get("amplitude_mw", 0.0)) > 0:
            f_osc = max(f_osc or 0.0, 1.0 / period)
    return swing, f_osc


def _latency_frequency(envelope: dict) -> float | None:
    """The frequency the latency band is set at: the highest oscillation frequency, except that a trace-sourced
    oscillation (`spectrum.replace_oscillation`) contributes the higher of 1/T and the trace's f_c: f_c sits
    above 1/T on a broadband trace, but can fall below it (a dominant low tone), and the band must not loosen."""
    best = None
    for ph in envelope["phases"]:
        osc = ph.get("oscillation") or {}
        period = float(osc.get("period_s", 0.0))
        if not (period > 0 and float(osc.get("amplitude_mw", 0.0)) > 0):
            continue
        f = 1.0 / period
        if osc.get("source") == "trace" and float(osc.get("f_c_hz") or 0.0) > 0:
            f = max(f, float(osc["f_c_hz"]))
        best = max(best or 0.0, f)
    return best


def _filter_row(swing: float, f_osc: float | None, ramp: float) -> dict | None:
    f_ramp = (ramp / 60.0) / (2.0 * math.pi * swing) if swing > 0 else None
    cands = [f for f in (f_ramp, f_osc) if f is not None]
    if not cands:
        return None
    return _row("smoothing_filter_fc", "filter", "smoothing filter cut-off f_c", min(cands), "Hz", "assumed", "set",
                f"envelope: S {swing:g} MW, ramp {ramp:g} MW/min",
                "first-order low-pass, min(f_ramp, f_osc); EMT confirms the shape")


def _gfm_rows(gfm, tier_names: list[str]) -> list[dict]:
    if gfm is None:
        return [_row("gfm_tuning", "gfm", "GFM droop and H", None, "", "assumed", "flagged",
                     "gfm_tuning.recommend_gfm_settings", "not run: no island frequency study to tune from")]
    bad = gfm.status in ("not_achievable", "incomplete")
    status = "flagged" if bad else "set"
    why = {"not_achievable": "no setting within the bounds passes: capacity is needed",
           "incomplete": "the search stopped early: the best found so far"}.get(gfm.status, "")
    trace = f"gfm_tuning.recommend_gfm_settings (status {gfm.status})"
    out = []
    for unit, s in gfm.settings.items():
        vals = s.get("recommended") or s.get("current") or {}
        out.append(_row(f"gfm_droop.{unit}", "gfm", f"GFM droop of {unit}", vals.get("droop_pct"), "%", "assumed",
                        status, trace, why))
        out.append(_row(f"gfm_h.{unit}", "gfm", f"GFM inertia H of {unit}", vals.get("H_equivalent_s"), "s", "assumed",
                        status, trace, why))
    return out


def _ups_rows(ups: dict, ramp: float) -> list[dict]:
    out = []
    for name, block in ups.items():
        vals = block.get("values") or block
        walk_in = _value(vals.get("walk_in_s"))
        p_mw = block.get("p_mw")
        status, note, extra = "set", "", {}
        if walk_in is not None and p_mw:
            need = 60.0 * float(p_mw) / ramp
            extra["min_walk_in_s"] = need
            if float(walk_in) < need:
                status = "flagged"
                note = f"walk-in {float(walk_in):g} s is below {need:g} s: the grid would ramp faster than {ramp:g} MW/min"
        out.append(_row(f"ups_walk_in.{name}", "ups", f"UPS walk-in of {name}", walk_in, "s", "assumed", status,
                        f"island unit {name}", note, **extra))
        out.append(_row(f"ups_transfer.{name}", "ups", f"UPS transfer of {name}",
                        {"f_window_hz": _value(vals.get("f_window_hz")),
                         "transfers_on_islanding": _value(vals.get("transfers_on_islanding"))},
                        "", "assumed", "set", f"island unit {name}"))
        out.append(_row(f"ups_disturbance_counting.{name}", "ups", f"disturbance counting for {name}", "none", "",
                        "assumed", "set", f"island unit {name}",
                        "a UPS walk-in or transfer is not counted as a disturbance (ERCOT NOGRR 282)"))
    return out


def build_sheet(*, envelope: dict, ramp_mw_per_min: float, tiers: list[dict], backup_mwh: float | None = None,
                gfm=None, ups: dict | None = None, preset: str | None = None) -> dict:
    """The sheet for sized `tiers` (see `tiers_from_solved` for the keys) under `envelope` and the ramp limit.
    `backup_mwh` is the AC backup energy the backup stores hold; `ups` is `{unit: island block}` (an optional
    `p_mw` on a block enables the walk-in check); `gfm` is a `gfm_tuning.Recommendation` or None."""
    if not (math.isfinite(ramp_mw_per_min) and ramp_mw_per_min > 0):
        raise ValueError(f"ramp_mw_per_min must be > 0, got {ramp_mw_per_min}")
    swing, f_osc = _envelope_terms(envelope)
    f_lat = _latency_frequency(envelope)
    limit = band_limit_s(f_lat) if f_lat else None
    smoothing = [t for t in tiers if t.get("tier", True)]
    backups = [t for t in tiers if t.get("backup")]
    rows = [_row("ramp_limiter", "ramp", "ramp limiter at the PoC", ramp_mw_per_min, "MW/min", "assumed", "set",
                 "AS-03 envelope floors sized at this ramp")]
    if (fc := _filter_row(swing, f_osc, ramp_mw_per_min)) is not None:
        rows.append(fc)
    p_sum = sum(t["p_sm_mw"] for t in smoothing) or math.nan
    e_sum = sum(t["e_sm_mwh"] for t in smoothing) or math.nan
    for t in smoothing:
        rows.append(_row(f"tier_split.{t['name']}", "tiers", f"smoothing share of {t['name']}",
                         {"power_share": t["p_sm_mw"] / p_sum if p_sum == p_sum else 0.0,
                          "energy_share": t["e_sm_mwh"] / e_sum if e_sum == e_sum else 0.0}, "", "sized", "set",
                         f"Store {t['name']}: p_smooth {t['p_sm_mw']:.4g} MW, e_smooth {t['e_sm_mwh']:.4g} MWh"))
    e_backups = sum(t["e_mwh"] for t in backups)
    for t in tiers:
        stored = 0.0
        if t.get("backup") and backup_mwh and e_backups > 0:
            stored = backup_mwh / t.get("eta_dis", 1.0) * t["e_mwh"] / e_backups
        band = soc_band(t["e_mwh"], t["e_sm_mwh"], t.get("e_min_pu", 0.0), t.get("e_max_pu", 1.0), stored)
        rows.append(_row(f"soc_band.{t['name']}", "soc", f"SOC band of {t['name']}", band, "MWh", "sized",
                         "set" if band["fits"] else "flagged",
                         f"Store {t['name']}: e_nom_opt {t['e_mwh']:.4g} MWh, e_smooth {t['e_sm_mwh']:.4g} MWh",
                         "" if band["fits"] else "the backup slice and the smoothing band do not fit the store"))
    if limit is None and smoothing:
        rows.append(_row("latency", "latency", "response latency of the smoothing tiers", None, "s", "assumed",
                         "not checked", "envelope",
                         "the envelope has no oscillation, so there is no band to check a latency against"))
    for t in smoothing:
        if limit is None:
            break
        latency, basis = t.get("latency_s"), t.get("latency_basis") or "latency given for the tier"
        if latency is None:              # a hand-built tier; `tiers_from_solved` fills both from `store_latency`
            r = response_latency(t["name"], {"kind": "bess", "control": t.get("control")})
            latency, basis = r["latency_s"], r["basis"] + ", assumed"
        ok = latency < limit
        rows.append(_row(f"latency.{t['name']}", "latency", f"response latency of {t['name']}", latency, "s",
                         "assumed", "ok" if ok else "flagged", f"Store {t['name']}: control {t.get('control')}",
                         (f"limit {limit:.3g} s = 1/(2π f) at {f_lat:g} Hz" if ok else
                          f"latency {latency:g} s is not below {limit:.3g} s = 1/(2π f) at {f_lat:g} Hz")
                         + f"; {basis}"))
    rows += _gfm_rows(gfm, [t["name"] for t in tiers])
    rows += _ups_rows(ups or {}, ramp_mw_per_min)
    rows.append(_row("preset", "preset", "island preset (select_preset)", preset, "", "assumed", "set",
                     "select_preset"))
    rows += [_row(i, "emt", s, None, "", "assumed", "needs EMT", "", why) for i, s, why in _EMT]
    flags = [f"{r['id']}: {r['note'] or r['setting']}" for r in rows if r["status"] == "flagged"]
    return {"format": FORMAT, "ramp_mw_per_min": ramp_mw_per_min, "latency_limit_s": limit, "ok": not flags,
            "flags": flags, "rows": rows}


def tiers_from_solved(n) -> list[dict]:
    """The flagged smoothing_tier and backup_reserve stores of a solved network as `build_sheet` tiers:
    name, p_mw (charger p_nom), e_mwh, p_sm_mw, e_sm_mwh (the LP's smoothing slice), e_min_pu, e_max_pu (the
    store's tightest window), eta_dis, backup, tier, control (the `control` column or None), latency_s and
    latency_basis (`spectrum.store_latency`, the one reading AS-09's tier split uses)."""
    names = dict.fromkeys(EF._flagged(n.stores, "smoothing_tier") + EF._flagged(n.stores, "backup_reserve"))
    tier, backup = set(EF._flagged(n.stores, "smoothing_tier")), set(EF._flagged(n.stores, "backup_reserve"))
    model = getattr(n, "model", None)

    def slice_of(var: str, name: str) -> float:
        if name not in tier:
            return 0.0                       # a backup-only store has no smoothing slice
        if model is None:
            raise ControlSettingsError(f"Store '{name}' is a smoothing tier but the network has no solved model")
        if var not in model.variables or name not in model.variables[var].indexes["name"]:
            raise ControlSettingsError(f"Store '{name}' is a smoothing tier but the model has no {var} for it")
        return float(model.variables[var].solution.sel(name=name).sum())

    def nominal(frame, attr, name):
        col = f"{attr}_opt" if bool(frame.at[name, f"{attr}_extendable"]) else attr
        return float(frame.at[name, col])

    snaps = n.snapshots
    e_min = n.get_switchable_as_dense("Store", "e_min_pu", snaps)
    e_max = n.get_switchable_as_dense("Store", "e_max_pu", snaps)
    out = []
    for name in names:
        dis, ch = EF._link(n, name, "discharger"), EF._link(n, name, "charger")
        out.append({
            "name": name, "tier": name in tier, "backup": name in backup,
            "p_mw": nominal(n.links, "p_nom", ch) if ch else math.nan,
            "e_mwh": nominal(n.stores, "e_nom", name),
            "p_sm_mw": slice_of("EnvelopeFloor-p_smooth", name), "e_sm_mwh": slice_of("EnvelopeFloor-e_smooth", name),
            "e_min_pu": float(e_min[name].max()), "e_max_pu": float(e_max[name].min()),
            "eta_dis": float(n.links.at[dis, "efficiency"]) if dis else 1.0,
            "control": n.stores.at[name, "control"] if "control" in n.stores.columns else None})
        out[-1]["latency_s"], out[-1]["latency_basis"] = store_latency(n, name)
    return out


def sheet_from_solved(n, envelope: dict, ramp_mw_per_min: float, **kw) -> dict:
    """`build_sheet` over the tiers of a solved network (`kw`: backup_mwh, gfm, ups, preset)."""
    return build_sheet(envelope=envelope, ramp_mw_per_min=ramp_mw_per_min, tiers=tiers_from_solved(n), **kw)


def write_sheet(sheet: dict, dyr_path) -> list[Path]:
    """Write the sheet as `<stem>.control_settings.json` and `.csv` in the directory of the dyr file."""
    dyr = Path(dyr_path)
    base = dyr.parent / f"{dyr.stem}.control_settings"
    js, cs = base.with_name(base.name + ".json"), base.with_name(base.name + ".csv")
    js.write_text(json.dumps(sheet, indent=2) + "\n")
    with open(cs, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in sheet["rows"]:
            w.writerow({k: (json.dumps(v) if isinstance(v, (dict, list)) else "" if v is None else v)
                        for k, v in r.items()})
    return [js, cs]
