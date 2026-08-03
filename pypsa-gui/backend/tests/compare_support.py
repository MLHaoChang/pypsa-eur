"""Shared support for the Compare tab correctness suite. NOT a test module."""
from __future__ import annotations

EXTENSIVE, INTENSIVE, STOCK = "extensive", "intensive", "stock"

KIND: dict[str, str] = {
    # ── CapacityComparison ────────────────────────────────────────────────
    # Installed levels are stocks; "new_*" are per-period increments and so
    # do sum. capex is M€ committed over the horizon and sums (see
    # _compute_total_annuitised_capex: b["total"] = sum(by_period.values())).
    "CapacityComparison.capacity_mw_by_carrier": STOCK,
    "CapacityComparison.storage_mw_by_carrier": STOCK,
    "CapacityComparison.storage_mwh_by_carrier": STOCK,
    "CapacityComparison.link_capacity_mw_by_carrier": STOCK,
    "CapacityComparison.capex_meur_by_carrier": EXTENSIVE,
    "CapacityComparison.new_capex_meur_by_carrier": EXTENSIVE,
    "CapacityComparison.new_capacity_mw_by_carrier": EXTENSIVE,
    "CapacityComparison.new_storage_mw_by_carrier": EXTENSIVE,
    "CapacityComparison.new_storage_mwh_by_carrier": EXTENSIVE,
    "CapacityComparison.new_link_capacity_mw_by_carrier": EXTENSIVE,
    # ── DispatchComparison ────────────────────────────────────────────────
    "DispatchComparison.dispatch_gwh_by_carrier": EXTENSIVE,
    "DispatchComparison.opex_meur": EXTENSIVE,
    "DispatchComparison.total_load_gwh": EXTENSIVE,
    # Cycles is a per-year RATE. _compute_storage_cycling_summary's docstring
    # states the horizon value is the AVERAGE of per-period cycles, so that a
    # unit cycling 100×/yr in every period reads 100, not 300.
    "DispatchComparison.storage_cycles_by_carrier": INTENSIVE,
    # ── LoadingComparison / LineLoadingEntry ──────────────────────────────
    "LineLoadingEntry.peak_loading": INTENSIVE,
    "LineLoadingEntry.mean_loading": INTENSIVE,
    "LineLoadingEntry.binding_hours": EXTENSIVE,
    # ── PricesComparison / CarrierPriceStats ──────────────────────────────
    "PricesComparison.mean_price": INTENSIVE,
    "PricesComparison.median_price": INTENSIVE,
    # p90_price was missing from the original inventory (schemas.py already
    # carried it as a CarrierPeriodValue field). compare.py's `_stats` helper
    # computes it exactly like mean/median — a weighted quantile over pooled
    # snapshot values (routers/compare.py:1291-1304) — so it is the same
    # non-additive statistic family, just a different percentile.
    "PricesComparison.p90_price": INTENSIVE,
    "CarrierPriceStats.mean_price": INTENSIVE,
    "CarrierPriceStats.median_price": INTENSIVE,
    "CarrierPriceStats.p90_price": INTENSIVE,
    # ── EmissionsComparison ───────────────────────────────────────────────
    "EmissionsComparison.total_kt": EXTENSIVE,
    "EmissionsComparison.by_carrier_kt": EXTENSIVE,
    "EmissionsComparison.intensity_kg_per_mwh": INTENSIVE,
    # ── EconomicsComparison / CarrierEconomics / AssetLCOHEntry ───────────
    "CarrierEconomics.revenue_meur": EXTENSIVE,
    "CarrierEconomics.opex_meur": EXTENSIVE,
    "CarrierEconomics.gen_cost_meur": EXTENSIVE,
    "CarrierEconomics.storage_charge_cost_meur": EXTENSIVE,
    "CarrierEconomics.curtailment_cost_meur": EXTENSIVE,
    "CarrierEconomics.lost_load_cost_meur": EXTENSIVE,
    "CarrierEconomics.capex_meur": EXTENSIVE,
    "CarrierEconomics.dispatch_gwh": EXTENSIVE,
    "CarrierEconomics.lcoe_eur_per_mwh": INTENSIVE,
    "AssetLCOHEntry.capex_meur": EXTENSIVE,
    "AssetLCOHEntry.opex_meur": EXTENSIVE,
    "AssetLCOHEntry.input_cost_meur": EXTENSIVE,
    "AssetLCOHEntry.output_gwh": EXTENSIVE,
    "AssetLCOHEntry.lcoh_eur_per_mwh": INTENSIVE,
    # ── CurtailmentComparison ─────────────────────────────────────────────
    "CurtailmentComparison.total_gwh": EXTENSIVE,
    "CurtailmentComparison.by_carrier_gwh": EXTENSIVE,
    "CurtailmentComparison.rate_pct_by_carrier": INTENSIVE,
    "CurtailmentComparison.system_rate_pct": INTENSIVE,
    # ── LostLoadComparison ────────────────────────────────────────────────
    "LostLoadComparison.total_mwh": EXTENSIVE,
    "LostLoadComparison.total_cost_meur": EXTENSIVE,
    "LostLoadBus.energy_mwh": EXTENSIVE,
    "LostLoadBus.cost_meur": EXTENSIVE,
    "LostLoadByCarrier.energy_mwh": EXTENSIVE,
    "LostLoadByCarrier.cost_meur": EXTENSIVE,
    # ── StorageCyclingComparison / StorageUnitCycles ──────────────────────
    "StorageCyclingComparison.cycles_by_carrier": INTENSIVE,
    "StorageUnitCycles.throughput_mwh": EXTENSIVE,
    "StorageUnitCycles.cycles": INTENSIVE,
}

EXTENSIVE_FIELDS = frozenset(k for k, v in KIND.items() if v == EXTENSIVE)
INTENSIVE_FIELDS = frozenset(k for k, v in KIND.items() if v == INTENSIVE)
STOCK_FIELDS = frozenset(k for k, v in KIND.items() if v == STOCK)


def classify(model_name: str, field: str) -> str:
    """Kind for `<model>.<field>`; KeyError if unclassified (deliberate)."""
    return KIND[f"{model_name}.{field}"]
