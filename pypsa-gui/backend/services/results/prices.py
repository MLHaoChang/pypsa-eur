"""
Lifted from `routers.results` (get_prices, get_price_drivers).

The handler keeps the network lookup, the `_dispatch_ready` gate and every
`_state` read; this module gets the arithmetic and returns the payload, or
`None` where the endpoint answers 204. Result frames arrive through the
injected `result_df` callable where one is needed, so this runs on any
network with no router state — see `tests/test_results_seam.py`.

pandas / numpy / math are imported locally inside each function, the pattern
the router already used, so they are intentionally absent from this header.
"""
from __future__ import annotations

import logging

from services.serialization import (
    safe_values as _safe_values,
    slice_ts as _slice_ts,
    ts_payload as _ts_payload,
    wants_slice as _wants_slice,
)

# The SAME logger the router uses, not a child of it: `logger.exception(...)`
# text inside the lifted bodies must produce byte-identical log records.
logger = logging.getLogger("pypsa_gui.results")



def compute_prices(n, source, from_, to_, *, result_df):
    """
    Bus marginal prices as a (windowable) time-series payload.

    Lifted from `routers.results.get_prices`, which keeps the network
    lookup, the `_dispatch_ready` gate and the `_state` reads. Returns the
    payload dict, or `None` where the handler returns 204.
    """
    import numpy as np
    try:
        df = result_df(n, "buses_t", "marginal_price", source)
        if df is None or df.empty:
            return None
        # Replace NaN with 0 explicitly — JSON encoders convert NaN to null
        # which the frontend then has to handle. Zero is the right semantic
        # default for non-binding constraints, and we use a separate `source`
        # field to flag when prices are unreliable.
        df = df.fillna(0.0)

        # Diagnostic: are the duals actually informative? "All zero" usually
        # means the LP didn't surface dual variables (solver config) OR a
        # very cheap (or negative-cost) generator with abundant headroom
        # serves every snapshot, making the dual mathematically 0. Provide a
        # generator-cost-based fallback the UI can show alongside.
        all_zero = bool(np.allclose(df.values, 0.0))
        fallback_per_snapshot: list[float] = []
        if all_zero:
            try:
                gens_p = n.generators_t.p
                if not gens_p.empty:
                    mc = n.generators["marginal_cost"].reindex(gens_p.columns).fillna(0.0)
                    # Per snapshot: highest marginal_cost among generators with p > epsilon.
                    eps = 1e-3
                    for i in range(len(gens_p.index)):
                        row = gens_p.iloc[i]
                        active = row.index[row.abs() > eps]
                        if len(active) == 0:
                            fallback_per_snapshot.append(0.0)
                        else:
                            fallback_per_snapshot.append(float(mc.loc[active].max()))
            except Exception:
                fallback_per_snapshot = []

        # Merit-order ("subsidy-removed") view: the curtailment_cost extra-
        # functionality term in solver_service adds `-cost × p` to the LP
        # objective for any renewable with curtailment_cost > 0. That makes
        # the renewable's effective marginal cost `marginal_cost - cost`,
        # and by LP duality the bus price equals that effective cost when
        # the renewable is the marginal unit — i.e. it dispatches strictly
        # between 0 and p_max_pu × p_nom_opt. The result reads as
        # "negative price" even though physically nothing is being paid.
        #
        # To give users a "merit order" view that ignores the dispatch
        # subsidy, add the curtailment_cost of the marginal renewable back
        # to the LP dual for every (bus, snapshot) where one is active.
        # When no renewable is marginal at that cell the LP dual already
        # reflects the true merit order and we leave it alone.
        data_adjusted: list[list[float]] = []
        negative_hours = 0
        try:
            gens = n.generators
            if (not gens.empty
                    and "curtailment_cost" in gens.columns
                    and not n.generators_t.p.empty):
                subsidised = gens.index[gens["curtailment_cost"].fillna(0) > 0]
                if len(subsidised) > 0:
                    p = n.generators_t.p
                    p_max_pu = n.get_switchable_as_dense("Generator", "p_max_pu")
                    p_nom_opt = (gens["p_nom_opt"]
                                 if "p_nom_opt" in gens.columns
                                 else gens["p_nom"])
                    eps = 1e-6
                    dual_tol = 1.0  # €/MWh — LP duals are exact to numerical eps
                    # bus → list of (gen_name, curtailment_cost, real_marginal_cost)
                    by_bus: dict[str, list[tuple[str, float, float]]] = {}
                    for g in subsidised:
                        if g not in p.columns:
                            continue
                        bus = str(gens.at[g, "bus"])
                        cost = float(gens.at[g, "curtailment_cost"])
                        real_mc = float(gens.at[g, "marginal_cost"]) if "marginal_cost" in gens.columns else 0.0
                        by_bus.setdefault(bus, []).append((g, cost, real_mc))
                    adj = df.copy()
                    # Targeted merit-order adjustment — same logic as
                    # /asset_economics. Fire only when the LP dual at this
                    # (bus, snapshot) actually equals the renewable's
                    # effective LP MC (real_mc - curtailment_cost), within
                    # 1 €/MWh tolerance. That's the unambiguous diagnostic
                    # that THIS renewable is setting the dual via the
                    # subsidy term. The previous loose rule ("fires whenever
                    # renewable is strictly between 0 and ceiling") was
                    # too narrow — it skipped the common case of renewable
                    # AT its ceiling with dual still pinned at effective MC
                    # (QA: 2756 negative cells raw, only 27 lifted by old
                    # rule). The new rule covers AT-the-ceiling cases too
                    # by checking the dual diagnostic instead of the
                    # operational position.
                    for bus, members in by_bus.items():
                        if bus not in adj.columns:
                            continue
                        for i in range(len(p.index)):
                            t = p.index[i]
                            raw_dual = float(adj.at[t, bus])
                            for g, cost, real_mc in members:
                                pv = float(p.at[t, g])
                                float(p_max_pu.at[t, g]) * float(p_nom_opt.loc[g])
                                # Renewable must be dispatching (pv > 0). It
                                # can be either strictly in the middle OR at
                                # the ceiling — both can be setting the dual
                                # at effective_lp_mc under LP duality.
                                if pv <= eps:
                                    continue
                                effective_lp_mc = real_mc - cost
                                if abs(raw_dual - effective_lp_mc) <= dual_tol:
                                    adj.at[t, bus] = real_mc
                                    break  # don't double-adjust
                    adj = adj.fillna(0.0)
                    data_adjusted = _safe_values(adj)
            if not data_adjusted:
                data_adjusted = _safe_values(df)
        except Exception:
            data_adjusted = _safe_values(df)

        # Count snapshots that the user is likely to see as "negative" so
        # the frontend can show a hint without re-scanning the whole grid.
        # A whole-horizon aggregate (not a per-snapshot array) — it does NOT
        # narrow with a `from`/`to` window below; `range.complete` is what
        # tells the frontend whether it reflects the full series or a slice.
        try:
            negative_hours = int((df.values < -1e-6).any(axis=1).sum())
        except Exception:
            negative_hours = 0

        # Use _ts_payload for the index+columns+data+periods shape, then merge
        # in the prices-specific extras. Keeps multi-period periods array
        # consistent with every other /results/* endpoint.
        range_meta = None
        if _wants_slice(from_, to_):
            df, range_meta = _slice_ts(df, from_, to_)
            # data_adjusted / fallback_per_snapshot are per-snapshot arrays
            # computed above against the FULL (unsliced) frame — same
            # positionally-aligned-to-the-rows shape as `data`. Slice them to
            # the bounds slice_ts ACTUALLY served (range_meta, not the raw
            # from_/to_ — slice_ts clamps and may cap) or a ranged response
            # carries N sliced `data` rows next to the full-length arrays,
            # and a UI indexing data_adjusted[i] against data[i] silently
            # renders another snapshot's price as if it were this one's.
            lo, hi = range_meta["from"], range_meta["to"]
            data_adjusted = data_adjusted[lo : hi + 1]
            fallback_per_snapshot = fallback_per_snapshot[lo : hi + 1]
        return _ts_payload(df, extra={
            # Merit-order ("subsidy-removed") version. Same shape as `data`.
            # When the LP-dual ALREADY reflects the merit order at a given
            # cell (no marginal subsidised renewable there), the value
            # equals the LP dual — so flipping the toggle has no effect on
            # those hours, which is the right semantics.
            "data_adjusted": data_adjusted,
            "negative_hours": negative_hours,
            # New diagnostic fields. UI can use these to render a banner like
            # "LP duals were all zero — showing analytical prices instead".
            "source": "fallback" if all_zero and fallback_per_snapshot else "lp",
            "fallback_per_snapshot": fallback_per_snapshot if all_zero else [],
            "note": (
                "LP duals are zero — most likely either (a) solver skipped "
                "dual extraction, or (b) demand is served by ample low-cost "
                "capacity so an extra MW would cost 0. The fallback shows "
                "the highest-marginal-cost dispatching generator per snapshot."
                if all_zero and fallback_per_snapshot else
                ""
            ),
        }, range_meta=range_meta)
    except Exception:
        logger.exception("results endpoint failed; returning 204 (see traceback)")
        return None



def compute_price_drivers(n, threshold, limit):
    """
    Snapshots whose price exceeds `threshold`, with the marginal unit.

    Lifted from `routers.results.get_price_drivers`, which keeps the network
    lookup, the `_dispatch_ready` gate and the `_state` reads. Returns the
    payload dict, or `None` where the handler returns 204.
    """
    import math
    try:
        prices = n.buses_t.marginal_price
        if prices.empty:
            return None
        gens_p = n.generators_t.p if hasattr(n.generators_t, "p") else None
        if gens_p is None or gens_p.empty:
            return None
        gens = n.generators
        # Bus → list of generator names that connect to it. Pre-built so
        # we don't re-scan n.generators per cell.
        gens_by_bus: dict[str, list[str]] = {}
        for name in gens.index:
            bus = str(gens.at[name, "bus"]) if "bus" in gens.columns else ""
            gens_by_bus.setdefault(bus, []).append(str(name))
        thr = float(threshold)
        # Collect rows above threshold first, then sort + truncate.
        rows: list[dict] = []
        for col in prices.columns:
            series = prices[col]
            for t, v in series.items():
                try:
                    pv = float(v)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(pv) or abs(pv) <= thr:
                    continue
                # Find marginal generator: dispatching > 1e-3 at this t,
                # connected to this bus, with marginal_cost closest to |pv|.
                bus = str(col)
                candidates = gens_by_bus.get(bus, [])
                best_name: str | None = None
                best_diff = float("inf")
                best_mc = 0.0
                best_carrier = ""
                best_dispatch = 0.0
                voll_slack_active = False
                voll_dispatch = 0.0
                for g in candidates:
                    if g not in gens_p.columns:
                        continue
                    try:
                        disp = float(gens_p.at[t, g])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if abs(disp) <= 1e-3:
                        continue
                    mc = float(gens.at[g, "marginal_cost"]) if "marginal_cost" in gens.columns else 0.0
                    carrier = str(gens.at[g, "carrier"]) if "carrier" in gens.columns else ""
                    if g.startswith("__voll_") or carrier == "load_shedding":
                        voll_slack_active = True
                        voll_dispatch = disp
                        # VOLL slack wins unconditionally for diagnosis — any
                        # dispatch from it means the LP is shedding load.
                        best_name = g
                        best_mc = mc
                        best_carrier = carrier
                        best_dispatch = disp
                        break
                    diff = abs(mc - abs(pv))
                    if diff < best_diff:
                        best_diff = diff
                        best_name = g
                        best_mc = mc
                        best_carrier = carrier
                        best_dispatch = disp
                # Diagnosis tag — one of:
                #   load_shedding    — VOLL slack dispatching, OR observed
                #                       price grossly exceeds any dispatching
                #                       gen's MC (price ≥ 10× max(mc)).
                #                       Catches cases where VOLL slack wasn't
                #                       named __voll_* but was added inline.
                #   thermal_peaker   — marginal gen's mc is within 5% of |price|
                #                       AND > 100 €/MWh
                #   transmission     — price >> any dispatching gen's mc but
                #                       within a reasonable scarcity multiple;
                #                       contingency / line bind is what's driving
                #   unattributed     — couldn't find any dispatching gen on the bus
                MAX_MC_MULTIPLIER = 10.0  # price ≥ N × max(MC) → load_shedding
                if voll_slack_active:
                    diag = "load_shedding"
                elif best_name is None:
                    diag = "unattributed"
                elif best_mc > 0 and abs(pv) >= MAX_MC_MULTIPLIER * best_mc and abs(pv) >= 1000.0:
                    # Price is orders of magnitude above the gen's MC. Even if
                    # the slack generator wasn't found, the LP is effectively
                    # shedding (or near-shedding) load at this bus.
                    diag = "load_shedding"
                elif best_mc > 100 and abs(best_mc - abs(pv)) / max(abs(pv), 1.0) < 0.05:
                    diag = "thermal_peaker"
                else:
                    diag = "transmission"
                # Multi-period: `t` is a (period, timestep) tuple — has no
                # `.isoformat`, so the fallback `str(t)` would produce the
                # tuple-string repr ("(2026, Timestamp('...'))") consumers
                # can't parse. Split into period + ISO timestep instead.
                if isinstance(t, tuple) and len(t) == 2:
                    period_val = t[0]
                    ts_val = t[1]
                    try:
                        period_out = int(period_val)
                    except (TypeError, ValueError):
                        period_out = str(period_val)
                    snap_iso = ts_val.isoformat() if hasattr(ts_val, "isoformat") else str(ts_val)
                else:
                    period_out = None
                    snap_iso = t.isoformat() if hasattr(t, "isoformat") else str(t)
                row = {
                    "snapshot": snap_iso,
                    "bus": bus,
                    "price": pv,
                    "marginal_gen": best_name,
                    "marginal_cost": best_mc,
                    "carrier": best_carrier,
                    "dispatch": best_dispatch,
                    "voll_slack_active": voll_slack_active,
                    "voll_dispatch": voll_dispatch,
                    "diagnosis": diag,
                }
                if period_out is not None:
                    row["period"] = period_out
                rows.append(row)
        rows.sort(key=lambda r: abs(r["price"]), reverse=True)
        truncated = len(rows) > limit
        return {
            "threshold": thr,
            "total_above_threshold": len(rows),
            "truncated": truncated,
            "rows": rows[:limit],
        }
    except Exception:
        logger.exception("results endpoint failed; returning 204 (see traceback)")
        return None
