"""Refine a generated series by deterministic ops (ticket 04).

The user (or the copilot, which emits ops and never numbers for the series)
asks for a closed set of edits; the backend applies them to an artifact's
source-year hourly series, in the TARGET's own units: MW for a power target
(a load's p_set, a storage inflow — a per-unit shape is scaled to MW first),
per unit for a bound, currency/MWh for a price, a ratio for an efficiency.
The result is a NEW pending artifact; `apply.assign_profile` stays the one
writer, and the edit card on apply shows old → new.

Ops (``{"op": name, **params}``), applied in order:

- ``scale_peak_to``: proportional scale so the peak the MODEL will see (the
  series aligned to the snapshots as apply aligns it) hits the target —
  ``{"mw": x}`` (power), ``{"value": v}`` (any other target), or
  ``{"fraction": f, "of": "current_peak" | "connection"}`` (``of`` is
  required: "75 %" alone does not say of what). ``connection`` is resolved
  here, server-side, from the point-of-connection link's ``p_nom``; the MW,
  its source and a hash of the connection are recorded, and a caller that
  sends them itself is refused.
- ``scale_window``: ``{"hours": [start, end), "weekdays"?, "months"?,
  "factor"}`` multiplies the matching hours on the SITE's clock: the
  project's commercial timezone, else the profile's own ``params.timezone``,
  DST-aware. With neither the clock is unknown and the op is refused.
- ``set_floor`` / ``set_cap``: ``{"value"}``.
- ``shift``: ``{"hours": int}`` rolls the year, wrapping. Not a time-zone fix
  (apply's time basis does that).
- ``smooth``: ``{"window_h": int >= 2}`` centred moving average, wrapping,
  rescaled to keep the energy.
- ``rescale_energy``: ``{"mwh"}``, power targets only.

``keep_energy: true`` is refused in v1 (only proportional edits). After every
op the target's bounds clip the series (no negative load, a bound in its
range); the op's stats report what was actually kept — the energy after, the
hours clipped — never the request. After the whole list, an earlier target
that a later op moved (a peak target followed by a window over the peak) is
warned ``earlier_target_broken``, naming both ops and the final value: run
the window first.

`replay` re-runs a recorded op list (on regeneration): a scale to a
connection keeps its recorded MW and warns if the connection changed or its
link is gone; any
op that no longer fits, or a target that changed, is refused naming the op
and its index.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from services.profile_gen.apply import (
    TARGETS,
    WINDOW_MEAN_BASIS,
    ProfileApplyError,
    time_basis_for,
    _align_to_snapshots,
    commercial_timezone,
)

EARLIER_TARGET_WARNING = "earlier_target_broken"
CONNECTION_CHANGED_WARNING = "connection_changed_since_refinement"
OPS = ("scale_peak_to", "scale_window", "set_floor", "set_cap", "shift", "smooth",
       "rescale_energy")
# What the server records on an op; only a replay may carry them in.
_RESOLVED = ("connection", "resolved_mw")


class RefineError(ProfileApplyError):
    """Base of the typed refusals of a refinement."""


class InvalidRefineOpError(RefineError):
    """An op is unknown, malformed, or makes no sense for the target."""


class WindowClockUnknownError(RefineError):
    """A window op on a series whose site clock is unknown."""


class WindowSourceOpError(RefineError):
    """An op that needs hours outside the window a source was read from."""


class AnnualEnergyOnWindowSourceError(RefineError):
    """An annual energy target on a source that only knows its window."""


class RefineReplayError(RefineError):
    """A recorded op no longer fits the series it is replayed on."""


@dataclass
class Refined:
    series: pd.Series
    meta: dict[str, Any]
    stats: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _num(op: Mapping[str, Any], key: str) -> float:
    raw = op.get(key)
    if isinstance(raw, bool):
        raise InvalidRefineOpError(f"{op.get('op')}.{key} must be a number")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise InvalidRefineOpError(f"{op.get('op')}.{key} must be a number, got {raw!r}") from None
    if not np.isfinite(v):
        raise InvalidRefineOpError(f"{op.get('op')}.{key} must be finite")
    return v


class _Target:
    """The target's units and bounds, and how the model sees the series."""

    def __init__(self, ctx, meta: Mapping[str, Any], component_type: str, attribute: str):
        spec = TARGETS.get((component_type, attribute))
        if spec is None:
            raise InvalidRefineOpError(f"{component_type}/{attribute} is not a profile target")
        self.ctx, self.meta, self.spec = ctx, meta, spec
        self.key = [component_type, attribute]
        self.power = spec.kind == "power"
        basis, _rec, _warn = time_basis_for(ctx, meta, None)
        self.basis = basis
        # A source read from a snapshot window's current series: the year
        # outside the window is unknown (NaN), see `current.py`.
        self.window_source = meta.get("profile_annual_basis") == WINDOW_MEAN_BASIS
        self.resolution = str(meta.get("resolution") or "hourly")

    def clip(self, v: np.ndarray) -> tuple[np.ndarray, int]:
        if self.spec.kind == "per_unit":
            out = np.clip(v, self.spec.lo, self.spec.hi)
        elif self.spec.kind == "power":
            out = np.clip(v, 0.0, None)
        elif self.spec.kind == "ratio":
            out = np.clip(v, 1e-9, None)
        else:
            out = v
        return out, int(((out != v) & ~(np.isnan(out) & np.isnan(v))).sum())

    def aligned_peak(self, s: pd.Series) -> float:
        aligned, _ = _align_to_snapshots(s, self.ctx.network.snapshots,
                                         resolution=self.resolution, time_basis=self.basis)
        return float(np.nanmax(aligned.to_numpy(dtype=float)))


def _site_clock(ctx, meta: Mapping[str, Any]) -> str:
    zone = commercial_timezone(ctx)
    if not zone and meta.get("source_clock") == "snapshot":
        return "UTC"   # the snapshots are the site clock; the labels are its hours
    if not zone:
        params = meta.get("params")
        zone = params.get("timezone") if isinstance(params, Mapping) else None
    if not zone:
        raise WindowClockUnknownError(
            "the series is on UTC hours and neither the project's commercial "
            "timezone nor the profile's own timezone says what the site's "
            "clock is, so an hour window cannot be placed; set the project's "
            "timezone")
    return str(zone)


def _connection(ctx) -> dict[str, Any]:
    state = getattr(ctx, "solver_state", None) or {}
    cfg = state.get("solver_config") if isinstance(state, Mapping) else None
    commercial = getattr(cfg, "commercial", None) or {}
    poc = commercial.get("poc_link") if isinstance(commercial, Mapping) else getattr(
        commercial, "poc_link", None)
    links = ctx.network.links
    if not poc or poc not in links.index:
        raise InvalidRefineOpError(
            "fraction of the connection needs the project's point-of-connection "
            "link (set the site connection first)")
    p_nom = float(links.at[poc, "p_nom"])
    p_max = float(links.at[poc, "p_max_pu"])
    digest = hashlib.sha256(json.dumps([poc, p_nom, p_max]).encode()).hexdigest()[:16]
    return {"source": f"links.p_nom of {poc!r}", "link": poc, "value": p_nom,
            "hash": digest}


def _window_mask(index: pd.DatetimeIndex, op: Mapping[str, Any], zone: str) -> np.ndarray:
    hours = op.get("hours")
    if (not isinstance(hours, list) or len(hours) != 2 or not all(
            isinstance(h, int) and not isinstance(h, bool) for h in hours)
            or not 0 <= hours[0] < hours[1] <= 24):
        raise InvalidRefineOpError("scale_window.hours must be [start, end) with 0 <= start < end <= 24")
    local = index.tz_localize("UTC").tz_convert(zone)
    mask = (local.hour >= hours[0]) & (local.hour < hours[1])
    for key, values, lo, hi in (("weekdays", local.dayofweek, 0, 6),
                                ("months", local.month, 1, 12)):
        chosen = op.get(key)
        if chosen is None:
            continue
        if not isinstance(chosen, list) or not all(isinstance(x, int) and lo <= x <= hi
                                                   for x in chosen):
            raise InvalidRefineOpError(f"scale_window.{key} must list integers {lo}-{hi}")
        mask &= np.isin(values, chosen)
    return np.asarray(mask)


def _apply_op(t: _Target, s: pd.Series, op: Mapping[str, Any], i: int,
              record: dict[str, Any]) -> tuple[pd.Series, dict[str, Any]]:
    name = op.get("op")
    if name not in OPS:
        raise InvalidRefineOpError(f"op {i}: unknown op {name!r}; available: {', '.join(OPS)}")
    if op.get("keep_energy"):
        raise InvalidRefineOpError(
            f"op {i} ({name}): keep_energy is not supported yet; follow it with "
            "rescale_energy instead")
    v = s.to_numpy(dtype=float)
    extra: dict[str, Any] = {}
    if t.window_source and name in ("shift", "smooth"):
        raise WindowSourceOpError(
            f"op {i} ({name}): this series was read from a snapshot window and "
            "knows no hours outside it, which a shift or a smoothing would pull "
            "in; use scale_window, floor, cap or a peak or energy target")
    if name == "scale_peak_to":
        current = t.aligned_peak(s)
        if not current > 0:
            raise InvalidRefineOpError(f"op {i}: the series has no peak to scale")
        if "mw" in op:
            if not t.power:
                raise InvalidRefineOpError(f"op {i}: mw is for power targets; use value")
            target = _num(op, "mw")
        elif "value" in op:
            target = _num(op, "value")
        elif "fraction" in op:
            frac = _num(op, "fraction")
            of = op.get("of")
            if of == "current_peak":
                target = frac * current
            elif of == "connection":
                if not t.power:
                    raise InvalidRefineOpError(f"op {i}: a fraction of the connection is for power targets")
                conn = record.get("connection") or _connection(t.ctx)
                target = frac * float(conn["value"])
                record["connection"] = conn
                record["resolved_mw"] = target
            else:
                raise InvalidRefineOpError(f"op {i}: of must be current_peak or connection")
        else:
            raise InvalidRefineOpError(f"op {i}: scale_peak_to takes mw, value or fraction")
        if "resolved_mw" in op:  # a replay keeps the recorded MW
            target = _num(op, "resolved_mw")
        if target <= 0:
            raise InvalidRefineOpError(f"op {i}: the peak target must be positive")
        v = v * (target / current)
        extra["target_peak"] = target
    elif name == "scale_window":
        factor = _num(op, "factor")
        if factor <= 0:
            raise InvalidRefineOpError(f"op {i}: scale_window.factor must be positive")
        mask = _window_mask(s.index, op, _site_clock(t.ctx, t.meta))
        v = np.where(mask, v * factor, v)
    elif name in ("set_floor", "set_cap"):
        value = _num(op, "value")
        if t.power and value < 0:
            raise InvalidRefineOpError(f"op {i}: {name} on a power target must be >= 0")
        if t.spec.kind == "per_unit" and not t.spec.lo <= value <= t.spec.hi:
            raise InvalidRefineOpError(f"op {i}: {name} must be in [{t.spec.lo}, {t.spec.hi}]")
        v = np.maximum(v, value) if name == "set_floor" else np.minimum(v, value)
    elif name == "shift":
        hours = op.get("hours")
        if not isinstance(hours, int) or isinstance(hours, bool):
            raise InvalidRefineOpError(f"op {i}: shift.hours must be a whole number")
        v = np.roll(v, hours)
    elif name == "smooth":
        w = op.get("window_h")
        if not isinstance(w, int) or isinstance(w, bool) or not 2 <= w <= 24 * 14:
            raise InvalidRefineOpError(f"op {i}: smooth.window_h must be a whole number 2-336")
        before = float(v.sum())
        pad = np.concatenate([v[-(w // 2):], v, v[:w - 1 - w // 2]])
        v = np.convolve(pad, np.ones(w) / w, mode="valid")
        if before:
            v = v * (before / float(v.sum()))
    elif name == "rescale_energy":
        if not t.power:
            raise InvalidRefineOpError(
                f"op {i}: rescale_energy is for power targets, not "
                f"{t.key[0]}/{t.key[1]}")
        mwh = _num(op, "mwh")
        if mwh <= 0:
            raise InvalidRefineOpError(f"op {i}: rescale_energy.mwh must be positive")
        basis = op.get("basis", "year")
        if t.window_source and basis != "window":
            raise AnnualEnergyOnWindowSourceError(
                f"op {i}: this series was read from a snapshot window, so the "
                "year outside it is unknown; state the window's MWh as "
                "{mwh, basis: \"window\"}")
        if not t.window_source and basis != "year":
            raise InvalidRefineOpError(
                f"op {i}: rescale_energy.basis {basis!r} is for a series read "
                "from a snapshot window; this one holds its whole year")
        total = float(np.nansum(v))
        if not total > 0:
            raise InvalidRefineOpError(f"op {i}: the series holds no energy to rescale")
        v = v * (mwh / total)
        extra["energy_requested_mwh"] = mwh
        extra["energy_basis"] = basis
    clipped_v, clipped = t.clip(v)
    out = pd.Series(clipped_v, index=s.index, name=s.name)
    stats = {"op": name, "index": i, "peak_before": t.aligned_peak(s),
             "peak_after": t.aligned_peak(out),
             "hours_touched": int((~np.isclose(out.to_numpy(), s.to_numpy(), equal_nan=True)).sum()),
             "hours_clipped": clipped, **extra}
    if t.power:
        stats["energy_before_mwh"] = float(s.sum())
        stats["energy_after_mwh"] = float(out.sum())
        peak = float(out.max())
        stats["load_factor"] = float(out.mean() / peak) if peak > 0 else 0.0
        # Not the edit card's: that one is weighted over the model's snapshots.
        stats["load_factor_basis"] = "hourly mean / peak, source year"
    return out, stats


def _absolute(series: pd.Series, meta: Mapping[str, Any], power: bool) -> pd.Series:
    scale = meta.get("scale_mw") if power else None
    return series.astype(float) * float(scale) if scale else series.astype(float)


def _run(ctx, series, meta, ops, component_type, attribute, *, replaying: bool) -> Refined:
    t = _Target(ctx, meta, component_type, attribute)
    if t.resolution != "hourly":
        raise InvalidRefineOpError("only an hourly series can be refined; regenerate it hourly")
    if not isinstance(ops, list) or not ops:
        raise InvalidRefineOpError("ops must be a non-empty list")
    s = _absolute(series, meta, t.power)
    records, stats, warnings = [], [], []
    for i, op in enumerate(ops):
        if not isinstance(op, Mapping):
            raise InvalidRefineOpError(f"op {i} must be an object")
        smuggled = [k for k in _RESOLVED if k in op]
        if smuggled and not replaying:
            raise InvalidRefineOpError(
                f"op {i} ({op.get('op')}): {', '.join(smuggled)} is recorded by the "
                "server; send the fraction and of only")
        record = {k: v for k, v in op.items()}
        record["target"] = t.key
        if replaying:
            stated = op.get("target")
            if stated is not None and list(stated) != t.key:
                raise RefineReplayError(
                    f"op {i} ({op.get('op')}) was recorded for {stated[0]}/{stated[1]}, "
                    f"not {t.key[0]}/{t.key[1]}")
            try:
                if op.get("connection") and op.get("of") == "connection":
                    recorded = _num(op["connection"], "value") * _num(op, "fraction")
                    if not np.isclose(_num(op, "resolved_mw"), recorded, rtol=1e-9):
                        raise RefineReplayError(
                            f"op {i} (scale_peak_to): {_num(op, 'resolved_mw'):g} MW is "
                            f"not {_num(op, 'fraction'):g} of the recorded connection "
                            f"({recorded:g} MW)")
                    try:
                        now = _connection(ctx)
                    except InvalidRefineOpError:
                        now = None
                    if now is None or now["hash"] != op["connection"].get("hash"):
                        where = (f"the grid connection {now['link']!r} is now "
                                 f"{now['value']:g} MW" if now else
                                 "the point-of-connection link is gone")
                        warnings.append(
                            f"{CONNECTION_CHANGED_WARNING}: op {i} kept its recorded "
                            f"{_num(op, 'resolved_mw'):g} MW; {where}")
                s, st = _apply_op(t, s, op, i, record)
            except RefineError as exc:
                if isinstance(exc, RefineReplayError):
                    raise
                raise RefineReplayError(f"op {i} ({op.get('op')}) no longer fits: {exc}") from None
        else:
            s, st = _apply_op(t, s, op, i, record)
        records.append(record)
        stats.append(st)

    # An earlier target a later op moved.
    final_peak = t.aligned_peak(s)
    final_energy = float(s.sum())
    for st in stats:
        later = [o["op"] + f" (op {o['index']})" for o in stats
                 if o["index"] > st["index"] and o["hours_touched"]]
        if not later:
            continue
        if "target_peak" in st and not np.isclose(final_peak, st["target_peak"], rtol=1e-6):
            warnings.append(
                f"{EARLIER_TARGET_WARNING}: op {st['index']} (scale_peak_to) set the "
                f"peak to {st['target_peak']:g} but {', '.join(later)} left it at "
                f"{final_peak:g}; put the target op last")
        if "energy_requested_mwh" in st and not np.isclose(
                final_energy, st["energy_requested_mwh"], rtol=1e-6):
            warnings.append(
                f"{EARLIER_TARGET_WARNING}: op {st['index']} (rescale_energy) set the "
                f"energy to {st['energy_requested_mwh']:g} MWh but {', '.join(later)} "
                f"left it at {final_energy:g} MWh; put the energy op last")

    new_meta = dict(meta)
    if t.power:
        new_meta["unit"] = "MW"
        new_meta.pop("scale_mw", None)
        new_meta.pop("per_unit_mean_of", None)
    new_meta["refinements"] = [*list(meta.get("refinements") or []), *records] \
        if not replaying else records
    prov = str(meta.get("provenance") or "synthetic/assumed")
    new_meta["provenance"] = prov if prov.endswith("user-refined") else f"{prov}, user-refined"
    return Refined(series=s, meta=new_meta, stats=stats, warnings=warnings)


def refine_series(ctx, series: pd.Series, meta: Mapping[str, Any], ops: list,
                  *, component_type: str, attribute: str) -> Refined:
    """Run ``ops`` over ``series`` (see the module rules)."""
    return _run(ctx, series, meta, ops, component_type, attribute, replaying=False)


def replay(ctx, series: pd.Series, meta: Mapping[str, Any], refinements: list,
           *, component_type: str, attribute: str) -> Refined:
    """Re-run a recorded op list over a regenerated series."""
    return _run(ctx, series, meta, refinements, component_type, attribute, replaying=True)
