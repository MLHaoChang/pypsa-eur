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

from services.results.carrier_kpis import _without_hydrogen_words
from services.serialization import (
    slice_ts as _slice_ts,
    ts_payload as _ts_payload,
    wants_slice as _wants_slice,
)

# The SAME logger the router uses, not a child of it: `logger.exception(...)`
# text inside the lifted bodies must produce byte-identical log records.
logger = logging.getLogger("pypsa_gui.results")



def curtailment_frame(n):
    """
    Curtailed renewable power per snapshot (MW), one column per curtailable
    generator, or None before a solve. The arithmetic behind
    `compute_curtailment` and `curtailment_by_cause` (one implementation).
    """
    import pandas as _pd
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
        carrier = _without_hydrogen_words(str(gens_df.at[col, "carrier"])) if "carrier" in gens_df.columns else ""
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
    return curtailment


def compute_curtailment(n, from_, to_):
    """
    Curtailed renewable energy per snapshot.

    Lifted from `routers.results.get_curtailment`, which keeps the network
    lookup, the `_dispatch_ready` gate and the `_state` reads. Returns the
    payload dict, or `None` where there is nothing to show; an internal
    exception is logged and raised (the route answers 204).
    """
    try:
        curtailment = curtailment_frame(n)
        if curtailment is None:
            return None
        range_meta = None
        if _wants_slice(from_, to_):
            curtailment, range_meta = _slice_ts(curtailment, from_, to_)
        return _ts_payload(curtailment, range_meta=range_meta)
    except Exception:
        logger.exception("results endpoint failed (see traceback)")
        raise


#: The causes, in display order. `other` is curtailment at neither cause (a full
#: battery, an import-side limit), so the causes always sum to the total.
CURTAILMENT_CAUSES = ("export_limit", "negative_price", "both", "other")
# The export Link counts as at its limit within this share of its effective limit.
_AT_LIMIT_REL = 1e-6


def curtailment_by_cause(n, commercial):
    """
    Each curtailable plant's curtailed energy (MWh, snapshot-weighted) split by why
    (IPP renewables + BESS ticket 05, spec §5): the export Link at its effective limit
    (`p_nom_opt`, or `p_nom` when not extendable, × `p_max_pu` per snapshot, capped by an
    energization schedule as the solve caps it), a negative
    export price, both, or neither. Splits `curtailment_frame` per snapshot, so the
    causes sum to the curtailment total exactly; a multi-period run is also split per
    period. The price is the one the dispatch was solved with: the solve's commercial
    record (`ic_poc_links`) must match the current config, else nothing is split.
    Without an export Link, an export price or that record, the totals are given and
    `status` is `not_established` with the reason. None before a solve (204).
    """
    import numpy as np
    import pandas as pd

    from services.commercial import lp_bindings as LP
    from services.period_utils import snapshot_weights

    frame = curtailment_frame(n)
    if frame is None:
        return None
    w = snapshot_weights(n, "generators", frame.index).to_numpy(dtype=float)
    p = n.generators_t.p.reindex(columns=frame.columns).fillna(0.0)
    plants = {
        str(g): {"total_mwh": float((frame[g].to_numpy() * w).sum()),
                 "available_mwh": float(((p[g] + frame[g]).to_numpy() * w).sum())}
        for g in frame.columns}

    def not_established(reason: str) -> dict:
        return {"status": "not_established", "reason": reason, "plants": plants}

    cfg = LP._parse(commercial) if commercial else None
    link = cfg.export_link if cfg is not None else None
    if link is None or link not in n.links.index:
        return not_established("export_link_missing")
    prices = n.links_t.get(LP.EXPORT_PRICE_ATTR)
    if prices is None or link not in getattr(prices, "columns", ()):
        return not_established("export_price_missing")
    record = (getattr(n, "meta", None) or {}).get(LP.META_LINKS)
    if not record:
        return not_established("no_commercial_solve_record")
    if LP.energy_record_state(n, cfg, record) == "config":
        # The price frame is the current config's, not the one this dispatch was solved with.
        return not_established("export_price_changed_since_solve")
    price = prices[link].reindex(frame.index).to_numpy(dtype=float)
    if np.isnan(price).any():
        return not_established("export_price_missing")
    cap = float(n.links.at[link, "p_nom_opt" if n.links.at[link, "p_nom_extendable"]
                           else "p_nom"])
    pu_t = n.links_t.p_max_pu
    pu = (pu_t[link].reindex(frame.index).fillna(float(n.links.at[link, "p_max_pu"]))
          if link in pu_t.columns else pd.Series(float(n.links.at[link, "p_max_pu"]),
                                                  index=frame.index)).to_numpy(dtype=float)
    # An energization schedule caps the Link only during the solve (`apply_energization`,
    # undone after it), so its cap is composed here the same way, by the one helper.
    from services.adequacy.energization import capped_mw

    limit = capped_mw(n, link, cap * pu)
    flow = n.links_t.p0[link].reindex(frame.index).fillna(0.0).to_numpy(dtype=float)
    at_limit = flow >= limit * (1.0 - _AT_LIMIT_REL) - 1e-9
    negative = price < 0.0
    masks = {"export_limit": at_limit & ~negative, "negative_price": negative & ~at_limit,
             "both": at_limit & negative, "other": ~at_limit & ~negative}
    periods = (frame.index.get_level_values(0) if isinstance(frame.index, pd.MultiIndex)
               else None)
    for g in frame.columns:
        mwh = frame[g].to_numpy() * w
        row = plants[str(g)]
        row["by_cause_mwh"] = {c: float(mwh[masks[c]].sum()) for c in CURTAILMENT_CAUSES}
        if periods is not None:
            row["by_period"] = {
                str(per): {c: float(mwh[masks[c] & (periods == per)].sum())
                           for c in CURTAILMENT_CAUSES}
                for per in pd.unique(periods)}
    return {"status": "ok", "export_link": str(link), "export_capacity_mw": cap,
            "negative_price_hours": float((negative * w).sum()), "plants": plants}
