"""
Lost-load comparison from the solver's VOLL slack capture.

Moved from `routers/compare.py`. `routers.compare` re-exports every name here
under the same name — or wraps it, where the function now takes the solver
config / result lookup as keyword-only arguments instead of reading router
state — so no call site changed. See the decomposition spec, Phase 3 addendum.

math / pandas are imported locally inside functions where the router did the
same; module-level imports below are only what the bodies reference at module
scope.
"""
from __future__ import annotations

from models.schemas import (
    LostLoadComparison,
    CarrierPeriodValue,
)
from services.compare.support import (
    _build_snapshot_weights,
    _to_pv,
)


def compute_lost_load_summary(
    cap, n, periods, is_multi, has_solve,
) -> LostLoadComparison:
    """
    Lost-load (VOLL slack dispatch) view.

    Reads from ``results_state.pkl`` because the VOLL slack DataFrame can't
    survive a netcdf round-trip — the slack generators are stripped right
    after capture inside solver_service. The capture format is:
      ``{lost_load_t: DataFrame(snapshot × bus), lost_load_total_mwh: float,
         lost_load_cost_eur: float}``
    Returns ``available=False`` when the pickle is absent, the capture key
    is missing, or the DataFrame is empty — all three are "no shedding"
    states from the user's perspective. Multi-period split uses the snapshot
    weight matrix the rest of the comparison view shares.
    """
    from models.schemas import LostLoadBus, LostLoadByCarrier, LostLoadComparison

    if not has_solve:
        return LostLoadComparison()
    # Shared reader — the economics summary consumes the same capture, and two
    # independent readers of one pickle key is how they drift apart.
    if not cap or cap.get("lost_load_t") is None:
        return LostLoadComparison()
    df = cap.get("lost_load_t")
    if df is None or getattr(df, "empty", True):
        return LostLoadComparison()

    total_mwh_scalar = float(cap.get("lost_load_total_mwh", 0.0) or 0.0)
    total_cost_scalar = float(cap.get("lost_load_cost_eur", 0.0) or 0.0)
    voll = total_cost_scalar / total_mwh_scalar if total_mwh_scalar > 0 else 0.0

    # Align to current snapshots. The capture is keyed on the snapshot index
    # used at solve time; if the project was re-saved with a different snapshot
    # set, reindex drops orphans and inserts zeros for missing — defensively
    # consistent rather than crashing.
    import math as _math
    # ENERGY basis (generators): lost-load is shed energy (MWh), so weight it on
    # the same basis as dispatch/served-load rather than the cost column. Whole
    # function is energy (× VOLL only for the derived € cost, applied after).
    weights = _build_snapshot_weights(n, "generators")
    sns = n.snapshots
    try:
        df_aligned = df.reindex(sns).fillna(0.0).astype(float)
        weighted = df_aligned.mul(weights, axis=0)
    except Exception:
        return LostLoadComparison()

    total_e = float(weighted.values.sum())
    if not _math.isfinite(total_e) or total_e <= 1e-9:
        # The capture exists but produced zero after reindex — treat as
        # "no shedding" rather than "available but zero."
        return LostLoadComparison(available=False, voll_eur_per_mwh=voll)

    total_e_bucket = {"total": total_e, "by_period": {}}
    total_c_bucket = {"total": total_e * voll / 1e6, "by_period": {}}
    if is_multi:
        try:
            by_period_e = weighted.groupby(sns.get_level_values(0)).sum().sum(axis=1)
            for p, v in by_period_e.items():
                v_f = float(v)
                if not _math.isfinite(v_f):
                    continue
                total_e_bucket["by_period"][str(int(p))] = v_f
                total_c_bucket["by_period"][str(int(p))] = v_f * voll / 1e6
        except Exception:
            pass

    # Per-bus rows sorted by horizon-wide energy desc. Cap at 24 entries to
    # keep the payload bounded on large networks; the frontend table renders
    # them ranked.
    by_bus_list: list[LostLoadBus] = []
    try:
        bus_totals = weighted.sum(axis=0).sort_values(ascending=False)
    except Exception:
        bus_totals = None
    if bus_totals is not None:
        for bus_name, energy in bus_totals.items():
            try:
                e_v = float(energy)
            except (TypeError, ValueError):
                continue
            if not _math.isfinite(e_v) or e_v <= 1e-6:
                continue
            e_bucket = {"total": e_v, "by_period": {}}
            c_bucket = {"total": e_v * voll / 1e6, "by_period": {}}
            if is_multi:
                try:
                    bus_series = weighted[bus_name]
                    grouped = bus_series.groupby(sns.get_level_values(0)).sum()
                    for p, vv in grouped.items():
                        vv_f = float(vv)
                        if not _math.isfinite(vv_f):
                            continue
                        e_bucket["by_period"][str(int(p))] = vv_f
                        c_bucket["by_period"][str(int(p))] = vv_f * voll / 1e6
                except Exception:
                    pass
            by_bus_list.append(LostLoadBus(
                bus=str(bus_name),
                energy_mwh=_to_pv(e_bucket),
                cost_meur=_to_pv(c_bucket),
            ))
            if len(by_bus_list) >= 24:
                break

    # Per-bus-carrier roll-up. Group buses by their `carrier` attribute on
    # the network and accumulate energy + cost. Each carrier's energy is
    # the sum of its bus shedding (horizon total + per-period). Empty when
    # the network lacks a `buses.carrier` column (rare).
    by_carrier_list: list[LostLoadByCarrier] = []
    buses_df = getattr(n, "buses", None)
    if (buses_df is not None and not buses_df.empty
            and "carrier" in buses_df.columns):
        carrier_acc: dict[str, dict] = {}
        for entry in by_bus_list:
            bus_name = entry.bus
            try:
                raw_c = buses_df.at[bus_name, "carrier"]
                carrier = str(raw_c).lower() if raw_c not in (None, "") else "unspecified"
            except Exception:
                carrier = "unspecified"
            acc = carrier_acc.setdefault(carrier, {
                "bus_count": 0,
                "e_total": 0.0,
                "e_pp": {},
                "c_total": 0.0,
                "c_pp": {},
            })
            acc["bus_count"] += 1
            acc["e_total"] += entry.energy_mwh.total
            for p_key, v in entry.energy_mwh.by_period.items():
                acc["e_pp"][p_key] = acc["e_pp"].get(p_key, 0.0) + v
            acc["c_total"] += entry.cost_meur.total
            for p_key, v in entry.cost_meur.by_period.items():
                acc["c_pp"][p_key] = acc["c_pp"].get(p_key, 0.0) + v
        for carrier, acc in carrier_acc.items():
            by_carrier_list.append(LostLoadByCarrier(
                carrier=carrier,
                bus_count=acc["bus_count"],
                energy_mwh=CarrierPeriodValue(total=acc["e_total"], by_period=acc["e_pp"]),
                cost_meur=CarrierPeriodValue(total=acc["c_total"], by_period=acc["c_pp"]),
            ))
        by_carrier_list.sort(key=lambda e: e.energy_mwh.total, reverse=True)

    return LostLoadComparison(
        available=True,
        voll_eur_per_mwh=voll,
        total_mwh=_to_pv(total_e_bucket),
        total_cost_meur=_to_pv(total_c_bucket),
        by_bus=by_bus_list,
        by_carrier=by_carrier_list,
    )
