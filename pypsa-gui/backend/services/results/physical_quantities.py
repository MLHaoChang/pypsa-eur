"""
Physical-quantity seam (Edge Investment Case, WP0.3).

One accessor for the quantities every economic surface consumes: optimised
capacity, build year, lifetime, annualised capital cost, FOM, dispatched
energy, and point-of-connection (PoC) flows. `compute_asset_economics`,
the Energy-Hub TEA and the finance layer are meant to read these numbers
from here so they cannot drift apart (spec decision 11; trustworthy-numbers
design). This module returns pandas frames, not a JSON payload; it is a
service seam, not a `/results/*` handler.

Conventions (identical to `asset_economics.py`):
  * COST weights  = `snapshot_weights(n, "objective")`  (× period years)
  * ENERGY weights = `snapshot_weights(n, "generators")` (× period years)
  * fixed_cost_eur = capital_cost_annualised × capacity × Σ period years
  * fom_cost_eur_annual = fom_cost × capacity  (annual; the horizon figure is
    fom_cost_eur_annual × Σ years — exposed separately as `fom_cost_eur`)
  * Link output = −p1 (− p2 − p3 − p4 for multi-port), falling back to
    p0 × efficiency PER LINK when p1 has no column for it
  * StorageUnit / Store: discharge = p⁺, charge = (−p)⁺
  * a NaN snapshot weight counts as 1.0 (asset_economics' `fillna(1.0)`)

Deliberate superset: the seam lists every asset of a class, including ones
asset_economics skips (a bus with no price column, a Link absent from p0);
their energy is 0, not missing.

PoC flows are GROSS per link at the grid-side port (p0): with several PoC
links, one importing while another exports in the same hour counts in both
`import_mw` and `export_mw`; `net_mw` = import − export. WP1.8's arbitrage
check needs the gross form, the bill needs the net one.

Dispatch frames arrive ONLY through the injected `result_df(n, accessor,
attr, source)` callable, never from router state. Nothing here imports a
router.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import numpy as np
import pandas as pd

from services.period_utils import is_multi_period, period_years_map, snapshot_weights
from services.solver_service import periodized_capital_costs

logger = logging.getLogger(__name__)

# Same role set as services/adequacy/redundancy.py `_IMPORT_ROLES`; the
# archetype packs write only "grid_import", older networks carry the others.
POC_ROLES: frozenset[str] = frozenset({"grid_import", "eh_import", "import"})

_CLASSES: tuple[tuple[str, str, str, str], ...] = (
    # (frame attr on n, result accessor, capacity column, cost-facts key)
    ("generators", "generators_t", "p_nom_opt", "generators"),
    ("storage_units", "storage_units_t", "p_nom_opt", "storage_units"),
    ("stores", "stores_t", "e_nom_opt", "stores"),
    ("links", "links_t", "p_nom_opt", "links"),
)


def _series_or_zero(df: pd.DataFrame | None, name: str, index: pd.Index) -> pd.Series:
    if df is None or name not in getattr(df, "columns", []):
        return pd.Series(0.0, index=index, dtype=float)
    return df[name].reindex(index).fillna(0.0).astype(float)


def _capacity(df: pd.DataFrame, opt_col: str) -> pd.Series:
    base_col = opt_col.replace("_opt", "")
    if opt_col in df.columns:
        base = df[base_col] if base_col in df.columns else 0.0
        return df[opt_col].fillna(base).fillna(0.0).astype(float)
    if base_col in df.columns:
        return df[base_col].fillna(0.0).astype(float)
    return pd.Series(0.0, index=df.index, dtype=float)


def _by_period(series: pd.DataFrame, weights: pd.Series, periods: list) -> pd.DataFrame:
    """Σ (value × weight) bucketed by the snapshot's period level."""
    weighted = series.multiply(weights, axis=0)
    if isinstance(series.index, pd.MultiIndex) and periods:
        grouped = weighted.groupby(level=0).sum().T
        grouped = grouped.reindex(columns=periods).fillna(0.0)
        return grouped
    return pd.DataFrame({None: weighted.sum(axis=0)})


def physical_quantities(n, cfg, *, result_df: Callable[..., Any]) -> dict[str, Any]:
    sns = n.snapshots
    is_multi = bool(is_multi_period(n))
    years_map = period_years_map(n) if is_multi else {}
    periods = list(years_map) if years_map else []
    total_years_factor = float(sum(years_map.values())) if years_map else 1.0
    w_cost = snapshot_weights(n, "objective").fillna(1.0)
    w_energy = snapshot_weights(n, "generators").fillna(1.0)

    capital_costs_available = True
    try:
        cost_facts = periodized_capital_costs(n, cfg)
    except Exception:
        logger.exception("periodized_capital_costs failed in physical_quantities")
        cost_facts = {}
        capital_costs_available = False

    components: dict[str, pd.DataFrame] = {}
    energy_by_period: dict[str, pd.DataFrame] = {}

    for frame_attr, accessor, cap_col, facts_key in _CLASSES:
        df = getattr(n, frame_attr, None)
        if df is None or df.empty:
            components[frame_attr] = pd.DataFrame()
            continue
        facts = cost_facts.get(facts_key, {}) if cost_facts else {}
        cap = _capacity(df, cap_col)
        out = pd.DataFrame(index=df.index.astype(str))
        out.index.name = "name"
        out["bus"] = (df["bus"] if "bus" in df.columns else df.get("bus0", "")).astype(str).values
        out["carrier"] = df["carrier"].astype(str).values if "carrier" in df.columns else ""
        out["p_nom_opt"] = cap.values
        out["build_year"] = (df["build_year"] if "build_year" in df.columns
                             else pd.Series(np.nan, index=df.index)).astype(float).values
        out["lifetime"] = (df["lifetime"] if "lifetime" in df.columns
                           else pd.Series(np.nan, index=df.index)).astype(float).values
        cc = pd.Series({str(k): float(v.get("capital_cost", np.nan)) for k, v in facts.items()},
                       dtype=float).reindex(out.index)
        oc = pd.Series({str(k): v.get("overnight_cost") for k, v in facts.items()},
                       dtype="float64").reindex(out.index)
        oca = pd.Series({str(k): bool(v.get("overnight_cost_available", False))
                         for k, v in facts.items()}).reindex(out.index).fillna(False)
        if not capital_costs_available:
            cc[:] = np.nan
        out["capital_cost_annualised"] = cc.values
        out["overnight_cost"] = oc.values
        out["overnight_cost_available"] = oca.astype(bool).values
        fom = (df["fom_cost"].fillna(0.0) if "fom_cost" in df.columns
               else pd.Series(0.0, index=df.index)).astype(float)
        out["fom_cost_per_unit"] = fom.values
        out["fixed_cost_eur"] = (out["capital_cost_annualised"] * out["p_nom_opt"]
                                 * total_years_factor).values
        out["fom_cost_eur_annual"] = (out["fom_cost_per_unit"] * out["p_nom_opt"]).values
        out["fom_cost_eur"] = (out["fom_cost_eur_annual"] * total_years_factor).values

        # ── dispatch → energy ────────────────────────────────────────────
        cols = list(df.index)
        if frame_attr == "links":
            p0 = result_df(n, accessor, "p0", "lopf")
            p1 = result_df(n, accessor, "p1", "lopf")
            p0_df = pd.DataFrame({c: _series_or_zero(p0, c, sns) for c in cols})
            eff = df["efficiency"].fillna(1.0).astype(float) if "efficiency" in df.columns \
                else pd.Series(1.0, index=df.index)
            p1_cols = set(getattr(p1, "columns", []))
            out_df = pd.DataFrame({
                c: (-_series_or_zero(p1, c, sns) if c in p1_cols
                    else p0_df[c] * float(eff.get(c, 1.0)))
                for c in cols
            })
            for port in ("2", "3", "4"):
                bus_col = f"bus{port}"
                if bus_col not in df.columns:
                    continue
                pn = result_df(n, accessor, f"p{port}", "lopf")
                if pn is None:
                    continue
                for c in cols:
                    bus_n = str(df.at[c, bus_col] or "").strip()
                    if bus_n and c in getattr(pn, "columns", []):
                        out_df[c] = out_df[c] - pn[c].reindex(sns).fillna(0.0)
            out_df.columns = out.index
            p0_df.columns = out.index
            out["output_mwh"] = out_df.multiply(w_energy, axis=0).sum(axis=0).values
            out["input_mwh"] = p0_df.multiply(w_energy, axis=0).sum(axis=0).values
            out["energy_mwh"] = out["output_mwh"]
            energy_frame = out_df
        elif frame_attr in ("storage_units", "stores"):
            p = result_df(n, accessor, "p", "lopf")
            p_df = pd.DataFrame({c: _series_or_zero(p, c, sns) for c in cols})
            p_df.columns = out.index
            dis = p_df.clip(lower=0.0)
            chg = (-p_df).clip(lower=0.0)
            out["discharge_mwh"] = dis.multiply(w_energy, axis=0).sum(axis=0).values
            out["charge_mwh"] = chg.multiply(w_energy, axis=0).sum(axis=0).values
            out["energy_mwh"] = out["discharge_mwh"]
            energy_frame = dis
        else:
            p = result_df(n, accessor, "p", "lopf")
            p_df = pd.DataFrame({c: _series_or_zero(p, c, sns) for c in cols})
            p_df.columns = out.index
            out["energy_mwh"] = p_df.multiply(w_energy, axis=0).sum(axis=0).values
            energy_frame = p_df
        components[frame_attr] = out
        energy_by_period[frame_attr] = _by_period(energy_frame, w_energy, periods)

    # ── point of connection ──────────────────────────────────────────────
    poc: dict[str, Any] | None = None
    links = getattr(n, "links", None)
    if links is not None and not links.empty and "eh_role" in links.columns:
        poc_links = [str(i) for i in links.index if str(links.at[i, "eh_role"]) in POC_ROLES]
        if poc_links:
            p0 = result_df(n, "links_t", "p0", "lopf")
            imp = sum(_series_or_zero(p0, c, sns).clip(lower=0.0) for c in poc_links)
            exp = sum((-_series_or_zero(p0, c, sns)).clip(lower=0.0) for c in poc_links)
            poc = {
                "links": poc_links,
                "import_mw": imp,
                "export_mw": exp,
                "net_mw": imp - exp,
                "import_mwh": float((imp * w_energy).sum()),
                "export_mwh": float((exp * w_energy).sum()),
            }

    return {
        "is_multi_period": is_multi,
        "periods": periods,
        "total_years_factor": total_years_factor,
        "capital_costs_available": capital_costs_available,
        "weights": {"cost": w_cost, "energy": w_energy},
        "components": components,
        "energy_by_period": energy_by_period,
        "poc": poc,
    }
