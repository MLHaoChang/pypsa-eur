"""
Lifted from `routers.results` (get_emissions).

The handler keeps the network lookup, the `_dispatch_ready` gate and every
`_state` read; this module gets the arithmetic and returns the payload, or
`None` where the endpoint answers 204. Result frames arrive through the
injected `result_df` callable where one is needed, so this runs on any
network with no router state — see `tests/test_results_seam.py`.

pandas / numpy / math are imported locally inside each function, the pattern
the router already used, so they are intentionally absent from this header.
"""
from __future__ import annotations

from services.economics import co2_intensity_map
from services.period_utils import (
    period_years_map,
    years_for_period,
)
from typing import Any



def compute_emissions(n, source, *, result_df):
    """
    CO2 emissions by carrier, generator and period.

    Lifted from `routers.results.get_emissions`, which keeps the network
    lookup, the `_dispatch_ready` gate and the `_state` reads. Returns the
    payload dict, or `None` where the handler returns 204.
    """
    import math as _math

    import pandas as _pd
    src = source if source in ("lopf", "ac_pf") else "lopf"
    p = result_df(n, "generators_t", "p", src)
    if p is None or p.empty:
        return None

    # Snapshot weighting for ENERGY: emissions = Σ dispatch × weight × factor,
    # so use the `generators` column — PyPSA's energy basis, matching
    # n.statistics() and the primary-energy CO2 constraint. Falls back
    # generators → objective → None on older netcdf (identical when the two
    # columns coincide).
    try:
        weights = n.snapshot_weightings.generators
    except Exception:
        try:
            weights = n.snapshot_weightings.objective
        except Exception:
            weights = None

    # ── Per-period weighting setup ───────────────────────────────────────
    # PyPSA multi-period scaling = snapshot_weight × investment_period_years.
    # The previous implementation skipped years scaling, under-reporting on
    # any horizon with non-unit period weights. Apply both consistently here.
    is_multi = isinstance(n.snapshots, _pd.MultiIndex)
    period_years = period_years_map(n)

    def _years_for(p_val) -> float:
        return years_for_period(period_years, p_val)

    def _weight_series_for(snapshots) -> _pd.Series:
        """Per-row effective weight = snapshot weight (generators) × period.years."""
        w = _pd.Series(1.0, index=snapshots, dtype=float)
        if weights is not None:
            try:
                w = w.multiply(weights.reindex(snapshots).fillna(1.0), axis=0)
            except Exception:
                pass
        if is_multi and period_years:
            try:
                period_lvl = snapshots.get_level_values(0)
                years_series = _pd.Series(
                    [_years_for(pv) for pv in period_lvl],
                    index=snapshots, dtype=float,
                )
                w = w.multiply(years_series, axis=0)
            except Exception:
                pass
        return w

    w_series = _weight_series_for(p.index)

    # Helper: collapse a (snapshot × asset) dispatch DataFrame to per-period
    # weighted energy. Returns a dict {period → Series[asset → MWh]} on
    # multi-period; on flat networks returns {None → Series} (single bucket
    # so downstream code can iterate uniformly).
    def _energy_by_period(p_df: _pd.DataFrame) -> dict:
        weighted = p_df.multiply(w_series.reindex(p_df.index).fillna(0.0), axis=0)
        if not is_multi:
            return {None: weighted.sum(axis=0)}
        period_lvl = p_df.index.get_level_values(0)
        out: dict = {}
        for p_key, sub in weighted.groupby(period_lvl):
            try:
                p_norm = int(p_key)
            except (TypeError, ValueError):
                p_norm = p_key
            out[p_norm] = sub.sum(axis=0)
        return out

    gen_energy_per_period = _energy_by_period(p)
    # Flat-horizon convenience: also produce a horizon-total Series so the
    # per-generator row can pin its energy_mwh column without per-period
    # bookkeeping.
    gen_energy_horizon = sum(gen_energy_per_period.values()) if not is_multi else (
        p.multiply(w_series, axis=0).sum(axis=0)
    )

    # Carrier intensity lookup. PyPSA's primary-energy constraint reads
    # carriers.co2_emissions × dispatched_energy / efficiency. Lower-case
    # keys to match the lowercase comp.carrier values we look up with.
    co2_by_carrier: dict[str, float] = co2_intensity_map(n)

    gens = n.generators

    # Per-period accumulators. Keyed by period (or None for flat).
    period_totals: dict = {}                          # period → total tCO2
    period_carrier_totals: dict = {}                  # period → {carrier → tCO2}
    period_gen_rows: dict = {}                        # period → [row dicts]

    def _accumulate(period_key, tCO2: float, carrier: str, row: dict) -> None:
        period_totals[period_key] = period_totals.get(period_key, 0.0) + tCO2
        bucket = period_carrier_totals.setdefault(period_key, {})
        bucket[carrier] = bucket.get(carrier, 0.0) + tCO2
        period_gen_rows.setdefault(period_key, []).append(row)

    # Horizon-level mirrors so the headline `total_tCO2` / `by_carrier` /
    # `by_generator` stay populated. These sum across the per-period entries.
    rows_by_gen: list[dict] = []
    total_t = 0.0
    carrier_totals: dict[str, float] = {}

    # ── Generator loop ──────────────────────────────────────────────────
    for name in gens.index:
        g_carrier = (str(gens.at[name, "carrier"]) if "carrier" in gens.columns else "").lower()
        intensity = co2_by_carrier.get(g_carrier, 0.0)
        eff = float(gens.at[name, "efficiency"]) if "efficiency" in gens.columns else 1.0
        if not _math.isfinite(eff) or eff <= 0:
            eff = 1.0
        out_intensity = intensity / eff if intensity != 0 else 0.0
        mwh_horizon = float(gen_energy_horizon.get(name, 0.0))
        if not _math.isfinite(mwh_horizon):
            mwh_horizon = 0.0
        tCO2_horizon = mwh_horizon * out_intensity
        total_t += tCO2_horizon
        if intensity != 0:
            carrier_totals[g_carrier] = carrier_totals.get(g_carrier, 0.0) + tCO2_horizon
        rows_by_gen.append({
            "name": str(name),
            "carrier": g_carrier,
            "energy_mwh": mwh_horizon,
            "tCO2": tCO2_horizon,
            "intensity_tCO2_per_MWh_out": out_intensity,
        })
        # Per-period split. On flat networks period_key=None; on multi-period
        # each (period, generator) gets one row.
        for period_key, energy_p in gen_energy_per_period.items():
            mwh = float(energy_p.get(name, 0.0))
            if not _math.isfinite(mwh):
                mwh = 0.0
            tCO2 = mwh * out_intensity
            _accumulate(period_key, tCO2, g_carrier, {
                "name": str(name),
                "carrier": g_carrier,
                "energy_mwh": mwh,
                "tCO2": tCO2,
                "intensity_tCO2_per_MWh_out": out_intensity,
            })

    # ── Storage emissions ────────────────────────────────────────────────
    # StorageUnit + Store: emissions when the unit's carrier has
    # co2_emissions > 0. Only discharge (positive p) emits.
    for comp_attr, comp_class, t_attr in (
        ("storage_units", "StorageUnit", "p"),
        ("stores", "Store", "p"),
    ):
        comp_df = getattr(n, comp_attr, None)
        if comp_df is None or comp_df.empty:
            continue
        comp_t = getattr(n, f"{comp_attr}_t", None)
        if comp_t is None:
            continue
        p_t = getattr(comp_t, t_attr, None)
        if p_t is None or p_t.empty:
            continue
        p_discharge = p_t.clip(lower=0)
        energy_per_period = _energy_by_period(p_discharge)
        energy_horizon = (
            p_discharge.multiply(w_series.reindex(p_discharge.index).fillna(0.0), axis=0).sum(axis=0)
        )
        for name in comp_df.index:
            s_carrier = (str(comp_df.at[name, "carrier"]) if "carrier" in comp_df.columns else "").lower()
            intensity = co2_by_carrier.get(s_carrier, 0.0)
            if intensity == 0:
                continue
            eff = 1.0
            if comp_attr == "storage_units" and "efficiency_dispatch" in comp_df.columns:
                try:
                    eff = float(comp_df.at[name, "efficiency_dispatch"])
                except (TypeError, ValueError):
                    eff = 1.0
            if not _math.isfinite(eff) or eff <= 0:
                eff = 1.0
            out_intensity = intensity / eff
            mwh_horizon = float(energy_horizon.get(name, 0.0))
            if not _math.isfinite(mwh_horizon) or mwh_horizon <= 0:
                continue
            tCO2_horizon = mwh_horizon * out_intensity
            total_t += tCO2_horizon
            carrier_totals[s_carrier] = carrier_totals.get(s_carrier, 0.0) + tCO2_horizon
            rows_by_gen.append({
                "name": str(name),
                "carrier": s_carrier,
                "energy_mwh": mwh_horizon,
                "tCO2": tCO2_horizon,
                "intensity_tCO2_per_MWh_out": out_intensity,
                "component": comp_class,
            })
            for period_key, energy_p in energy_per_period.items():
                mwh = float(energy_p.get(name, 0.0))
                if not _math.isfinite(mwh) or mwh <= 0:
                    continue
                tCO2 = mwh * out_intensity
                _accumulate(period_key, tCO2, s_carrier, {
                    "name": str(name),
                    "carrier": s_carrier,
                    "energy_mwh": mwh,
                    "tCO2": tCO2,
                    "intensity_tCO2_per_MWh_out": out_intensity,
                    "component": comp_class,
                })

    by_carrier = sorted(
        [
            {
                "carrier": c,
                "tCO2": t,
                "share_pct": (100.0 * t / total_t) if total_t > 0 else 0.0,
            }
            for c, t in carrier_totals.items()
        ],
        key=lambda r: r["tCO2"], reverse=True,
    )
    rows_by_gen.sort(key=lambda r: r["tCO2"], reverse=True)

    # ── Per-period breakdown (multi-period only) ─────────────────────────
    # Build by_period[] = [{period, total_tCO2, by_carrier, by_generator}]
    # sorted by period. Flat networks emit an empty list.
    by_period_payload: list[dict] = []
    if is_multi:
        sorted_periods = sorted(
            [p_key for p_key in period_totals.keys() if p_key is not None],
            key=lambda x: (0, int(x)) if hasattr(x, "__int__") else (1, str(x)),
        )
        for p_key in sorted_periods:
            total_p = float(period_totals.get(p_key, 0.0))
            carrier_p = period_carrier_totals.get(p_key, {})
            bc = sorted(
                [
                    {
                        "carrier": c,
                        "tCO2": t,
                        "share_pct": (100.0 * t / total_p) if total_p > 0 else 0.0,
                    }
                    for c, t in carrier_p.items()
                ],
                key=lambda r: r["tCO2"], reverse=True,
            )
            gen_rows_p = sorted(
                period_gen_rows.get(p_key, []),
                key=lambda r: r["tCO2"], reverse=True,
            )
            by_period_payload.append({
                "period": p_key,
                "total_tCO2": total_p,
                "by_carrier": bc,
                "by_generator": gen_rows_p,
            })

    # ── CO₂ caps ─────────────────────────────────────────────────────────
    # Detect every primary_energy + co2_emissions constraint. Some may be
    # horizon-wide (no investment_period set), others per-period (the
    # `investment_period` column carries an int year). Surface them in
    # `caps[]`; keep the legacy `cap` field as the first active one for
    # backward compatibility.
    caps: list[dict] = []
    cap_info: dict = {"active": False}
    try:
        if not n.global_constraints.empty:
            gc = n.global_constraints
            mask = (
                (gc["type"].astype(str) == "primary_energy")
                & (gc.get("carrier_attribute", "").astype(str) == "co2_emissions")
            )
            for cap_name in gc.index[mask]:
                cap_value = float(gc.at[cap_name, "constant"])
                mu = float(gc.at[cap_name, "mu"]) if "mu" in gc.columns else 0.0
                # Which period does this cap apply to (if any)?
                ip_raw = gc.at[cap_name, "investment_period"] if "investment_period" in gc.columns else None
                ip_norm: Any = None
                try:
                    if ip_raw is not None and ip_raw == ip_raw:  # not NaN
                        ip_int = int(ip_raw)
                        # PyPSA stores no-period sentinel as -1 or 0 on some
                        # versions; treat anything outside a reasonable year
                        # range as horizon-wide.
                        if 1900 <= ip_int <= 2200:
                            ip_norm = ip_int
                except (TypeError, ValueError):
                    ip_norm = None
                # The slack depends on which scope the cap covers:
                #   • horizon-wide → cap − total_t
                #   • per-period → cap − period's total
                if ip_norm is None:
                    used = total_t
                else:
                    used = float(period_totals.get(ip_norm, 0.0))
                slack = (cap_value - used) if _math.isfinite(cap_value) else None
                cap_entry = {
                    "active": True,
                    "name": str(cap_name),
                    "investment_period": ip_norm,
                    "scope": "period" if ip_norm is not None else "horizon",
                    "cap_tCO2": cap_value if _math.isfinite(cap_value) else None,
                    "used_tCO2": used,
                    "shadow_price_eur_per_tCO2": mu if _math.isfinite(mu) else 0.0,
                    "slack_tCO2": slack,
                    "binding": (slack is not None and abs(slack) < max(1.0, abs(cap_value) * 1e-6)),
                }
                caps.append(cap_entry)
                if not cap_info.get("active"):
                    # Legacy `cap` field — first active cap, preserves the
                    # shape older Emissions.tsx code reads.
                    cap_info = {
                        "active": True,
                        "name": str(cap_name),
                        "cap_tCO2": cap_value if _math.isfinite(cap_value) else None,
                        "shadow_price_eur_per_tCO2": mu if _math.isfinite(mu) else 0.0,
                        "slack_tCO2": slack,
                    }
    except Exception:
        pass

    return {
        "total_tCO2": total_t,
        "by_carrier": by_carrier,
        "by_generator": rows_by_gen,
        "cap": cap_info,
        "caps": caps,
        "is_multi_period": is_multi,
        "by_period": by_period_payload,
    }
