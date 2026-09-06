"""
Lifted from `routers.results` (get_curtailment).

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
    slice_ts as _slice_ts,
    ts_payload as _ts_payload,
    wants_slice as _wants_slice,
)

# The SAME logger the router uses, not a child of it: `logger.exception(...)`
# text inside the lifted bodies must produce byte-identical log records.
logger = logging.getLogger("pypsa_gui.results")



def compute_curtailment(n, from_, to_):
    """
    Curtailed renewable energy per snapshot.

    Lifted from `routers.results.get_curtailment`, which keeps the network
    lookup, the `_dispatch_ready` gate and the `_state` reads. Returns the
    payload dict, or `None` where the handler returns 204.
    """
    import pandas as _pd
    try:
        p = n.generators_t.p
        if p.empty:
            return None
        # Align p_nom to p.columns up front. Without this, generators in p
        # that aren't in n.generators (rare but possible after carrier edits)
        # would give NaN columns and crash JSON encoding.
        cap_col = "p_nom_opt" if "p_nom_opt" in n.generators.columns else "p_nom"
        p_nom = n.generators[cap_col].reindex(p.columns).fillna(0.0)

        # PyPSA stores time-varying p_max_pu only for generators that actually
        # have a profile — others fall back to the static n.generators.p_max_pu
        # (default 1.0). Build a full-column DataFrame so the subtraction below
        # produces a clean DataFrame with no NaN columns.
        p_max_pu = n.generators_t.p_max_pu
        if p_max_pu.empty:
            # Constant p_max_pu = 1.0 for every generator → p_max ≡ p_nom.
            p_max = (p * 0.0).add(p_nom, axis=1)
        else:
            p_max_pu_full = p_max_pu.reindex(columns=p.columns, fill_value=1.0)
            p_max = p_max_pu_full.multiply(p_nom, axis=1)

        # ── Time-varying effective capacity (multi-period vintages) ─────
        # vintage_service aggregates vintage p_nom_opt into the parent
        # post-solve (parent's column = parent + Σ vintages). But each
        # vintage is only physically active from its build_year onwards
        # — `p` is forced to 0 in earlier snapshots. Using the AGGREGATED
        # p_nom_opt × p_max_pu gives a phantom p_max that exceeds what
        # any of the contributing vintages could actually deliver in
        # that snapshot. The endpoint then reports massive curtailment
        # that doesn't exist (e.g. 920 GWh of fake curtailment in 2026
        # from a 293-MW vintage built in 2028 whose capacity was rolled
        # into Solar2's p_nom_opt).
        #
        # Fix: rebuild a per-(snapshot, generator) effective capacity by
        # walking `n.meta["vintage_results"]` and summing only vintages
        # with build_year ≤ snapshot's period. For generators without
        # vintage_results, use the aggregated p_nom_opt (unchanged).
        if isinstance(p.index, _pd.MultiIndex):
            try:
                period_lvl = p.index.get_level_values(0).astype(int)
                vintage_results = (n.meta or {}).get("vintage_results", {}) if hasattr(n, "meta") else {}
                gen_vr = vintage_results.get("Generator", {}) if isinstance(vintage_results, dict) else {}
                if gen_vr:
                    # Build per-column time-varying effective capacity. Start
                    # from the existing p_max (= p_nom_opt × p_max_pu) and
                    # OVERRIDE columns that have vintage_results data.
                    for gname in p.columns:
                        meta = gen_vr.get(gname)
                        if not meta:
                            continue
                        initial = float(meta.get("initial_capacity", 0.0) or 0.0)
                        periods_meta = meta.get("periods", []) or []
                        # Vector of effective_p_nom per snapshot for this gen.
                        eff = _pd.Series(initial, index=p.index, dtype=float)
                        for entry in periods_meta:
                            try:
                                by = int(entry.get("build_year"))
                                pn = float(entry.get("p_nom_opt", 0.0) or 0.0)
                            except (TypeError, ValueError):
                                continue
                            if pn <= 0:
                                continue
                            # Active in snapshots whose period >= build_year.
                            mask = period_lvl >= by
                            eff.values[mask] += pn
                        # Re-compute p_max for this column using time-varying
                        # effective capacity. p_max_pu_full has it as the
                        # snapshot-indexed profile we built above.
                        if p_max_pu.empty or gname not in p_max_pu_full.columns:
                            p_max[gname] = eff
                        else:
                            p_max[gname] = p_max_pu_full[gname] * eff
            except Exception:
                pass  # defensive — fall back to unmasked behaviour

        # fillna(0) is defensive — covers any residual NaN in `p` or column
        # alignment edge cases. Required because JSON encoders reject NaN.
        curtailment = (p_max - p).clip(lower=0).fillna(0.0)
        # Filter to generators where (p_max - p) is genuinely "curtailment":
        # renewables (free energy that's wasted) OR generators with explicit
        # curtailment_cost > 0 (the LP penalty signals user intent).
        # Without this filter, thermal "headroom" (unused dispatch capacity
        # of a 200 MW thermal running at 50 MW) gets reported as curtailment
        # — confusing in raw CSV exports and downstream consumers.
        RENEW_KEYWORDS = ("wind", "solar", "pv", "ror", "geothermal",
                          "offwind", "onwind", "hydro", "biomass",
                          "wave", "tidal", "rooftop")
        keep_cols: list[str] = []
        gens_df = n.generators
        for col in curtailment.columns:
            if col not in gens_df.index:
                continue
            carrier = str(gens_df.at[col, "carrier"]).lower() if "carrier" in gens_df.columns else ""
            is_renew = any(k in carrier for k in RENEW_KEYWORDS)
            has_subsidy = False
            if "curtailment_cost" in gens_df.columns:
                cc_val = gens_df.at[col, "curtailment_cost"]
                try:
                    has_subsidy = float(cc_val) > 0
                except (TypeError, ValueError):
                    has_subsidy = False
            if is_renew or has_subsidy:
                keep_cols.append(col)
        if keep_cols:
            curtailment = curtailment[keep_cols]
        else:
            # No curtailable generators in the network — return an empty-shaped
            # payload (preserves index, drops all columns) rather than 204.
            curtailment = curtailment.iloc[:, 0:0]
        range_meta = None
        if _wants_slice(from_, to_):
            curtailment, range_meta = _slice_ts(curtailment, from_, to_)
        return _ts_payload(curtailment, range_meta=range_meta)
    except Exception:
        logger.exception("results endpoint failed; returning 204 (see traceback)")
        return None
