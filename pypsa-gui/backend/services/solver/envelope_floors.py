"""Envelope -> LP floors and headroom (AS-03).

The AI-training load's dynamic envelope (`services.dc_load_profile.dynamic_envelope`, per phase: swing S,
oscillation amplitude A at period T, checkpoint dip D for d s) becomes sizing rows on the fast-tier storage, plus
two backup rows the design intent asks for. Each fast-tier store k is an AS-02 split storage (a `Store`, energy
E_k, with a charger and a discharger `Link`, power P_k = the charger's p_nom, AC-side MW); it gets a smoothing
slice P_k^sm (MW) and E_k^sm (MWh, usable), new LP variables, and:

  1. power floor   sum_k P_k^sm >= S + A. With a ramp limiter the grid takes no instant share, and the step and
                   the oscillation coincide, so the floor is S + A, not the larger of the two;
  2. energy floor  sum_k E_k^sm >= 2 x (step + checkpoint) + oscillation energy, the SUM of the events (they
                   coincide), each from `services.adequacy.smoothing`; the band takes both directions of the step;
  3. headroom      eta_dis x p_dis_k(t) + P_k^sm <= P_k and p_ch_k(t) + P_k^sm <= P_k in every snapshot: the
                   smoothing slice is never sold to arbitrage;
  4. SOC band      e_min_pu x E_k + E_k^sm / 2 <= e_k(t) <= e_max_pu x E_k - E_k^sm / 2, the store's own usable
                   window (its e_min_pu / e_max_pu), so E^sm is usable energy;
  5. backup        sum_b eta_dis_b x (e_b(t) - e_min_pu x E_b - E_b^sm / 2 [tier]) >= backup MWh over the stores
                   flagged `backup_reserve`, in island mode or not: the critical load's AC energy (intent A:
                   P_crit x autonomy hours, AS-AUTONOMY-1; intent D: the bridge reserve) delivered through the
                   discharger, above the smoothing band;
  6. run hours     sum_t w_t p_g(t) <= H_g x p_nom_g per investment period, w = snapshot_weightings.generators,
                   for each Generator with a `run_hours_cap` active in that period.

The floors are the max over the envelope's phases: P^sm and E^sm are single size variables per store, so the
largest phase governs every period. The site-wide values (`power_floor_mw`, `energy_floor_mwh`, `backup_mwh`)
come from `SolverConfig.envelope_floors`; the per-asset flags are custom columns, float 1/0 so a `.nc`
round-trip keeps them (`STORE_COLUMNS`, `GENERATOR_COLUMNS`). All limits are generic engineering, "assumed"
(SAGC-1).
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import xarray as xr

from services.adequacy.smoothing import checkpoint_mwh, oscillation_mwh, step_mwh
from services.design_intent import backup_energy_mwh, bridge_reserve_mwh
from services.solver.runtime import _safe_log
from services.solver.split_storage import _nominal

if TYPE_CHECKING:
    import pypsa

    from models.design_intent import DesignIntentSpec

#: Custom `Store` flags (1 = on): a fast-tier smoothing store, a store that holds the backup reserve.
STORE_COLUMNS = ("smoothing_tier", "backup_reserve")
#: Custom `Generator` column: the annual run-hour cap in h (AS-01's `run_hours_cap` bound key).
GENERATOR_COLUMNS = ("run_hours_cap",)
#: The `SolverConfig.envelope_floors` keys; a missing or None value adds no row.
FLOOR_KEYS = ("power_floor_mw", "energy_floor_mwh", "backup_mwh")


class EnvelopeFloorsError(ValueError):
    """The flagged assets cannot carry the envelope rows; raised before the solve."""


def floors_from_envelope(envelope: dict, ramp_mw_per_min: float) -> dict:
    """`{power_floor_mw, energy_floor_mwh}` from a `dynamic_envelope` result, the max over its phases."""
    if not (math.isfinite(ramp_mw_per_min) and ramp_mw_per_min > 0):
        raise ValueError(f"ramp_mw_per_min must be > 0, got {ramp_mw_per_min}")
    p_floor = e_floor = 0.0
    for ph in envelope["phases"]:
        s = float(ph["swing_mw"])
        ck, osc = ph.get("checkpoint") or {}, ph.get("oscillation") or {}
        amp, period = float(osc.get("amplitude_mw", 0.0)), float(osc.get("period_s", 0.0))
        e_ck = checkpoint_mwh(float(ck.get("depth_mw", 0.0)), float(ck.get("duration_s", 0.0)), ramp_mw_per_min)
        e_osc = oscillation_mwh(amp, period) if amp > 0 and period > 0 else 0.0
        p_floor = max(p_floor, s + amp)
        e_floor = max(e_floor, 2.0 * (step_mwh(s, ramp_mw_per_min) + e_ck) + e_osc)
    return {"power_floor_mw": p_floor, "energy_floor_mwh": e_floor}


def backup_mwh(spec: DesignIntentSpec | None, critical_mw: float) -> float | None:
    """The backup row's MWh for a design intent: A's autonomy, D's bridge reserve, else None."""
    if spec is None:
        return None
    return backup_energy_mwh(spec, critical_mw) if spec.intent == "A" else bridge_reserve_mwh(spec, critical_mw)


def tag_store(n: pypsa.Network, name: str, *, smoothing_tier: bool | None = None,
              backup_reserve: bool | None = None) -> None:
    """Set a Store's flags; None leaves a flag as it is."""
    for col, value in zip(STORE_COLUMNS, (smoothing_tier, backup_reserve)):
        if value is None:
            continue
        if col not in n.stores.columns:
            n.stores[col] = pd.Series(np.nan, index=n.stores.index, dtype=float)
        n.stores.loc[name, col] = 1.0 if value else 0.0


def set_run_hours_cap(n: pypsa.Network, name: str, hours: float | None) -> None:
    """Set a Generator's annual run-hour cap (h); None removes it."""
    if hours is not None and not (math.isfinite(hours) and 0 <= hours <= 8760):
        raise ValueError(f"{name}: run_hours_cap must be in [0, 8760] h, got {hours}")
    if "run_hours_cap" not in n.generators.columns:
        n.generators["run_hours_cap"] = pd.Series(np.nan, index=n.generators.index, dtype=float)
    n.generators.loc[name, "run_hours_cap"] = np.nan if hours is None else float(hours)


def _flagged(frame: pd.DataFrame, col: str) -> list[str]:
    if col not in frame.columns:
        return []
    on = pd.to_numeric(frame[col].map(lambda v: float(v) if isinstance(v, (bool, int, float, np.number)) else np.nan),
                       errors="coerce").fillna(0.0) > 0
    return [str(x) for x in frame.index[on]]


def _capped(n) -> dict[str, float]:
    if "run_hours_cap" not in n.generators.columns:
        return {}
    caps = pd.to_numeric(n.generators["run_hours_cap"], errors="coerce")
    return {str(g): float(h) for g, h in caps.items() if math.isfinite(h)}


def _floors(cfg) -> dict[str, float]:
    raw = getattr(cfg, "envelope_floors", None) or {}
    if not isinstance(raw, dict):
        raw = raw.model_dump()
    out = {k: float(raw[k]) for k in FLOOR_KEYS if raw.get(k) is not None}
    bad = [k for k, v in out.items() if not (math.isfinite(v) and v >= 0)]
    if bad:
        raise EnvelopeFloorsError(f"envelope_floors {', '.join(bad)} must be finite and >= 0")
    return out


def _link(n, store: str, col: str) -> str | None:
    if col not in n.stores.columns:
        return None
    name = n.stores.at[store, col]
    return str(name) if isinstance(name, str) and name in n.links.index else None


def refusals(n: pypsa.Network, floors: dict[str, float]) -> list[str]:
    """Why the rows cannot be built, one line per problem, naming the asset."""
    out = []
    tier, backup = _flagged(n.stores, "smoothing_tier"), _flagged(n.stores, "backup_reserve")
    for s in tier:
        if _link(n, s, "charger") is None or _link(n, s, "discharger") is None:
            out.append(f"Store '{s}' is flagged smoothing_tier but has no AS-02 charger and discharger Link")
    for s in backup:
        if _link(n, s, "discharger") is None:
            out.append(f"Store '{s}' is flagged backup_reserve but has no AS-02 discharger Link")
    if (floors.get("power_floor_mw", 0) > 0 or floors.get("energy_floor_mwh", 0) > 0) and not tier:
        out.append("envelope_floors sets a power or energy floor, but no Store is flagged smoothing_tier")
    if floors.get("backup_mwh", 0) > 0 and not backup:
        out.append("envelope_floors sets backup_mwh, but no Store is flagged backup_reserve")
    return out


def _wrap_with_envelope_floors(network: pypsa.Network, user_fn, cfg, log_queue=None):
    """Chain the envelope rows after `user_fn`; `user_fn` unchanged when nothing asks for them. Raises
    `EnvelopeFloorsError` before the solve when the flagged assets cannot carry them."""
    floors = _floors(cfg)
    if not (floors or _flagged(network.stores, "smoothing_tier") or _flagged(network.stores, "backup_reserve")
            or _capped(network)):
        return user_fn
    why = refusals(network, floors)
    if why:
        raise EnvelopeFloorsError("; ".join(why))

    def emit(msg: str) -> None:
        _safe_log(log_queue, f"[ENVELOPE] {msg}")

    def envelope_floors_fn(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        _add_rows(n, snapshots, floors, emit)

    return envelope_floors_fn


# ── rows ─────────────────────────────────────────────────────────────────────


def _per_t(var, values) -> xr.DataArray:
    """`values` (one per snapshot of `var`) as a DataArray on `var`'s snapshot coordinate."""
    return xr.DataArray(np.asarray(values, dtype=float), coords=[var.indexes["snapshot"]], dims=["snapshot"])


def _pu(n, attr: str, store: str, var):
    s = n.get_switchable_as_dense("Store", attr, var.indexes["snapshot"])[store]
    return float(s.iloc[0]) if s.nunique() <= 1 else _per_t(var, s.values)


def _power_floor(m, ctx) -> int:
    if ctx["floors"].get("power_floor_mw") is None:
        return 0
    m.add_constraints(ctx["p_sm"].sum() >= ctx["floors"]["power_floor_mw"], name="envelope_floors_power")
    return 1


def _energy_floor(m, ctx) -> int:
    if ctx["floors"].get("energy_floor_mwh") is None:
        return 0
    m.add_constraints(ctx["e_sm"].sum() >= ctx["floors"]["energy_floor_mwh"], name="envelope_floors_energy")
    return 1


def _headroom(m, ctx) -> int:
    n, added = ctx["n"], 0
    for k, t in ctx["tier"].items():
        psm = ctx["p_sm"].sel(name=k)
        dis, ch = m["Link-p"].sel(name=t["discharger"]), m["Link-p"].sel(name=t["charger"])
        eta = float(n.links.at[t["discharger"], "efficiency"])
        m.add_constraints(eta * dis + psm - t["P"] <= 0, name=f"envelope_floors_headroom_dis-{k}")
        m.add_constraints(ch + psm - t["P"] <= 0, name=f"envelope_floors_headroom_ch-{k}")
        added += 2
    return added


def _soc_band(m, ctx) -> int:
    n, added = ctx["n"], 0
    for k, t in ctx["tier"].items():
        e, esm = m["Store-e"].sel(name=k), ctx["e_sm"].sel(name=k)
        lo, hi = _pu(n, "e_min_pu", k, e), _pu(n, "e_max_pu", k, e)
        m.add_constraints(e - lo * t["E"] - 0.5 * esm >= 0, name=f"envelope_floors_soc_low-{k}")
        m.add_constraints(e + 0.5 * esm - hi * t["E"] <= 0, name=f"envelope_floors_soc_high-{k}")
        added += 2
    return added


def _backup(m, ctx) -> int:
    need, n = ctx["floors"].get("backup_mwh"), ctx["n"]
    if need is None or not ctx["backup"]:
        return 0
    lhs = 0
    for b in ctx["backup"]:
        e = m["Store-e"].sel(name=b)
        eta = float(n.links.at[_link(n, b, "discharger"), "efficiency"])
        part = e - _pu(n, "e_min_pu", b, e) * ctx["E"](b)
        if b in ctx["tier"]:
            part = part - 0.5 * ctx["e_sm"].sel(name=b)
        lhs = lhs + eta * part
    m.add_constraints(lhs >= need, name="envelope_floors_backup")
    return 1


def _run_hours(m, ctx) -> int:
    n, added = ctx["n"], 0
    caps = _capped(n)
    if not caps:
        return 0
    p = m["Generator-p"]
    sns = p.indexes["snapshot"]
    w = n.snapshot_weightings.generators.reindex(sns).to_numpy()
    periods = list(n.investment_periods) if isinstance(sns, pd.MultiIndex) else [None]
    for g, h in caps.items():
        if g not in p.indexes["name"]:
            continue
        p_nom = _nominal(n, "Generator", "p_nom", g)
        if p_nom is None:
            continue
        for per in periods:
            if per is None:
                mask = np.ones(len(sns), dtype=bool)
            else:
                if not bool(n.components["Generator"].get_active_assets(per).get(g, False)):
                    continue
                mask = sns.get_level_values(0) == per
            lhs = (p.sel(name=g) * _per_t(p, np.where(mask, w, 0.0))).sum()
            tag = g if per is None else f"{g}-{per}"
            m.add_constraints(lhs - h * p_nom <= 0, name=f"envelope_floors_run_hours-{tag}")
            added += 1
    return added


#: Each row family by name, in order (a seam for the one-row-dropped tests).
ROWS = {"power_floor": _power_floor, "energy_floor": _energy_floor, "headroom": _headroom,
        "soc_band": _soc_band, "backup": _backup, "run_hours": _run_hours}


def _add_rows(n, snapshots, floors: dict[str, float], emit) -> None:
    m = n.model
    tier: dict[str, dict] = {}
    for k in _flagged(n.stores, "smoothing_tier"):
        P = _nominal(n, "Link", "p_nom", _link(n, k, "charger"))
        E = _nominal(n, "Store", "e_nom", k)
        if P is None or E is None:
            emit(f"{k}: not active in any period; no smoothing rows")
            continue
        tier[k] = {"P": P, "E": E, "charger": _link(n, k, "charger"), "discharger": _link(n, k, "discharger")}
    ctx = {"n": n, "floors": floors, "tier": tier,
           "backup": [b for b in _flagged(n.stores, "backup_reserve") if b in m["Store-e"].indexes["name"]],
           "E": lambda s: _nominal(n, "Store", "e_nom", s) or 0.0}
    if tier:
        coords = [pd.Index(list(tier), name="name")]
        ctx["p_sm"] = m.add_variables(lower=0, coords=coords, name="EnvelopeFloor-p_smooth")
        ctx["e_sm"] = m.add_variables(lower=0, coords=coords, name="EnvelopeFloor-e_smooth")
    elif floors.get("power_floor_mw", 0) > 0 or floors.get("energy_floor_mwh", 0) > 0:
        raise EnvelopeFloorsError("a power or energy floor is set, but no smoothing_tier Store is active")
    added = {}
    for kind, fn in ROWS.items():
        if kind in ("power_floor", "energy_floor", "headroom", "soc_band") and not tier:
            continue
        added[kind] = fn(m, ctx)
    emit(f"{sum(added.values())} envelope rows ({', '.join(f'{k} {v}' for k, v in added.items() if v)}) on "
         f"{len(tier)} smoothing store(s)")
