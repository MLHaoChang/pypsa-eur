"""
The metric registry — the single source of truth for per-asset results.

Every metric is declared exactly once here: which category it belongs to,
which component classes it applies to, its unit, whether it came from the
solver or from user input, what preconditions it needs, and how to compute
it. The endpoint reflects these fields straight into its response, so the
frontend holds NO metric knowledge of its own — adding a metric is one edit
in this file plus its compute function, and nothing in TypeScript changes.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from . import compute as C

Kind = Literal["series", "scalar"]
Origin = Literal["output", "input", "derived"]

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("summary", "Summary"),
    ("capacity", "Capacity"),
    ("dispatch", "Dispatch"),
    ("storage", "Storage"),
    ("loadflow", "Load flow"),
    ("prices", "Prices & duals"),
    ("economics", "Economics"),
    ("emissions", "Emissions"),
)
CATEGORY_IDS: tuple[str, ...] = tuple(c for c, _ in CATEGORIES)
CATEGORY_LABELS: dict[str, str] = dict(CATEGORIES)

ALL_CLASSES: tuple[str, ...] = (
    "Bus", "Generator", "Load", "Line", "Transformer",
    "Link", "StorageUnit", "Store",
)

# ── Preconditions ───────────────────────────────────────────────────────────
# A metric lists the ids it needs; the resolver looks each up in the
# precondition map computed per request and returns the first that is not ok.
REQ_DISPATCH = "dispatch"          # a FRESH solve (dispatch_status == "fresh")
REQ_AC_PF = "ac_pf"                # AC PF stage results present in _state
REQ_DUALS = "duals"                # LP duals captured on this component class
REQ_COMMITTABLE = "committable"    # this asset has committable=True
REQ_CO2 = "co2"                    # this asset's carrier declares co2_emissions
REQ_ANNUITY = "annuity"            # overnight_cost-priced asset resolves a
                                    # discount_rate and a finite lifetime


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    unit: str
    kind: Kind
    category: str
    classes: tuple[str, ...]
    origin: Origin
    compute: Callable[..., Any]
    formula: str = ""
    requires: tuple[str, ...] = field(default_factory=tuple)
    # Metrics that only exist in the AC PF snapshot pin their own source, so
    # they read the right frame whatever the panel's lopf/ac_pf toggle says.
    source_override: str | None = None


# Two metrics every class shares. Declared ONCE with classes=ALL_CLASSES —
# not per-class — so their ids stay stable, human-readable strings that mean
# the same thing in the API, the checklist and the exported workbook.
_SUMMARY_METRICS: tuple[Metric, ...] = (
    Metric(id="identity", label="Identity", unit="", kind="scalar",
           category="summary", classes=ALL_CLASSES, origin="input",
           compute=C.summary_identity),
    Metric(id="params", label="Parameters", unit="", kind="scalar",
           category="summary", classes=ALL_CLASSES, origin="input",
           compute=C.summary_params),
)


_GENERATOR_METRICS: tuple[Metric, ...] = (
    # ── capacity ─────────────────────────────────────────────────────────
    Metric(id="p_nom", label="Installed capacity", unit="MW", kind="scalar",
           category="capacity", classes=("Generator",), origin="input",
           compute=C.gen_p_nom),
    Metric(id="p_nom_opt", label="Optimised capacity", unit="MW", kind="scalar",
           category="capacity", classes=("Generator",), origin="output",
           compute=C.gen_p_nom_opt, requires=(REQ_DISPATCH,)),
    Metric(id="p_nom_delta", label="Capacity expansion", unit="MW", kind="scalar",
           category="capacity", classes=("Generator",), origin="derived",
           formula="p_nom_opt − p_nom", compute=C.gen_p_nom_delta,
           requires=(REQ_DISPATCH,)),
    Metric(id="capex_annual", label="Annualised CAPEX", unit="EUR/a", kind="scalar",
           category="capacity", classes=("Generator",), origin="derived",
           formula="capital_cost × p_nom_opt (capital_cost = overnight_cost × annuity(discount_rate, lifetime) × nyears when priced via overnight_cost; the raw capital_cost column otherwise)", compute=C.gen_capex_annual,
           requires=(REQ_DISPATCH, REQ_ANNUITY)),
    Metric(id="p_nom_opt_by_vintage", label="Capacity by vintage", unit="MW",
           kind="scalar", category="capacity", classes=("Generator",),
           origin="derived", formula="Σ p_nom_opt over `<name>@<year>` rows",
           compute=C.gen_p_nom_by_vintage, requires=(REQ_DISPATCH,)),

    # ── dispatch ─────────────────────────────────────────────────────────
    Metric(id="p", label="Active power", unit="MW", kind="series",
           category="dispatch", classes=("Generator",), origin="output",
           compute=C.gen_p, requires=(REQ_DISPATCH,)),
    Metric(id="p_max_pu", label="Availability", unit="pu", kind="series",
           category="dispatch", classes=("Generator",), origin="input",
           compute=C.gen_p_max_pu, requires=(REQ_DISPATCH,)),
    Metric(id="available", label="Available power", unit="MW", kind="series",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="p_nom_opt × p_max_pu", compute=C.gen_available,
           requires=(REQ_DISPATCH,)),
    Metric(id="curtailment", label="Curtailment", unit="MW", kind="series",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="p_nom_opt × p_max_pu − p", compute=C.gen_curtailment,
           requires=(REQ_DISPATCH,)),
    Metric(id="capacity_factor", label="Capacity factor", unit="pu", kind="series",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="p ÷ p_nom_opt", compute=C.gen_capacity_factor,
           requires=(REQ_DISPATCH,)),
    Metric(id="status", label="Committed", unit="", kind="series",
           category="dispatch", classes=("Generator",), origin="output",
           compute=C.gen_status, requires=(REQ_DISPATCH, REQ_COMMITTABLE)),
    Metric(id="start_up", label="Start-up", unit="1", kind="series",
           category="dispatch", classes=("Generator",), origin="output",
           compute=C.gen_start_up, requires=(REQ_DISPATCH, REQ_COMMITTABLE)),
    Metric(id="shut_down", label="Shut-down", unit="1", kind="series",
           category="dispatch", classes=("Generator",), origin="output",
           compute=C.gen_shut_down, requires=(REQ_DISPATCH, REQ_COMMITTABLE)),
    Metric(id="energy_mwh", label="Energy", unit="MWh", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="Σ p × snapshot_weighting(generators) × period years",
           compute=C.gen_energy, requires=(REQ_DISPATCH,)),
    Metric(id="full_load_hours", label="Full-load hours", unit="h", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="energy ÷ p_nom_opt", compute=C.gen_full_load_hours,
           requires=(REQ_DISPATCH,)),
    Metric(id="mean_capacity_factor", label="Mean capacity factor", unit="pu",
           kind="scalar", category="dispatch", classes=("Generator",),
           origin="derived", formula="energy ÷ (p_nom_opt × weighted hours)",
           compute=C.gen_mean_cf, requires=(REQ_DISPATCH,)),
    Metric(id="curtailed_mwh", label="Curtailed energy", unit="MWh", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="Σ curtailment × weighting", compute=C.gen_curtailed_energy,
           requires=(REQ_DISPATCH,)),
    Metric(id="peak_mw", label="Peak output", unit="MW", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="max p", compute=C.gen_peak, requires=(REQ_DISPATCH,)),
    Metric(id="zero_hours", label="Zero-output hours", unit="h", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="count of snapshots where p ≈ 0, weighted",
           compute=C.gen_zero_hours, requires=(REQ_DISPATCH,)),
    Metric(id="max_ramp_up", label="Max ramp up", unit="MW/h", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="max Δp between consecutive snapshots",
           compute=C.gen_max_ramp_up, requires=(REQ_DISPATCH,)),
    Metric(id="max_ramp_down", label="Max ramp down", unit="MW/h", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="min Δp between consecutive snapshots",
           compute=C.gen_max_ramp_down, requires=(REQ_DISPATCH,)),
    Metric(id="n_starts", label="Start-up count", unit="", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="Σ start_up", compute=C.gen_n_starts,
           requires=(REQ_DISPATCH, REQ_COMMITTABLE)),

    # ── loadflow (reactive only — hence the ○ in the spec matrix) ─────────
    Metric(id="q", label="Reactive power", unit="MVAr", kind="series",
           category="loadflow", classes=("Generator",), origin="output",
           compute=C.gen_q, requires=(REQ_DISPATCH, REQ_AC_PF),
           source_override="ac_pf"),

    # ── prices ───────────────────────────────────────────────────────────
    Metric(id="bus_marginal_price", label="Bus marginal price", unit="EUR/MWh",
           kind="series", category="prices", classes=("Generator",),
           origin="output", compute=C.gen_bus_price,
           requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="mu_upper", label="μ upper", unit="EUR/MWh", kind="series",
           category="prices", classes=("Generator",), origin="output",
           compute=C.gen_mu_upper, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="mu_lower", label="μ lower", unit="EUR/MWh", kind="series",
           category="prices", classes=("Generator",), origin="output",
           compute=C.gen_mu_lower, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="capture_price", label="Capture price", unit="EUR/MWh",
           kind="scalar", category="prices", classes=("Generator",),
           origin="derived", formula="Σ p·λ·w ÷ Σ p·w",
           compute=C.gen_capture_price, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="capture_rate", label="Capture rate", unit="pu", kind="scalar",
           category="prices", classes=("Generator",), origin="derived",
           formula="capture price ÷ time-weighted mean bus price",
           compute=C.gen_capture_rate, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="binding_hours", label="Binding hours", unit="h", kind="scalar",
           category="prices", classes=("Generator",), origin="derived",
           formula="count of snapshots where μ_upper or μ_lower ≠ 0",
           compute=C.gen_binding_hours, requires=(REQ_DISPATCH, REQ_DUALS)),

    # ── economics ────────────────────────────────────────────────────────
    Metric(id="revenue_eur", label="Revenue", unit="EUR", kind="scalar",
           category="economics", classes=("Generator",), origin="derived",
           formula="Σ p × bus price × weighting", compute=C.gen_revenue,
           requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="vom_cost_eur", label="Variable O&M", unit="EUR", kind="scalar",
           category="economics", classes=("Generator",), origin="derived",
           formula="Σ |p| × marginal_cost × weighting", compute=C.gen_vom,
           requires=(REQ_DISPATCH,)),
    Metric(id="fixed_cost_eur", label="Fixed cost", unit="EUR/a", kind="scalar",
           category="economics", classes=("Generator",), origin="derived",
           formula="capital_cost × p_nom_opt (capital_cost = overnight_cost × annuity(discount_rate, lifetime) × nyears when priced via overnight_cost; the raw capital_cost column otherwise)", compute=C.gen_fixed_cost,
           requires=(REQ_DISPATCH, REQ_ANNUITY)),
    Metric(id="net_profit_eur", label="Net profit", unit="EUR", kind="scalar",
           category="economics", classes=("Generator",), origin="derived",
           formula="revenue − (fixed cost + VOM)", compute=C.gen_net_profit,
           requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="lcoe_eur_per_mwh", label="LCOE", unit="EUR/MWh", kind="scalar",
           category="economics", classes=("Generator",), origin="derived",
           formula="(fixed cost + VOM) ÷ energy", compute=C.gen_lcoe,
           requires=(REQ_DISPATCH,)),

    # ── emissions ────────────────────────────────────────────────────────
    Metric(id="co2_rate", label="CO₂ rate", unit="t/h", kind="series",
           category="emissions", classes=("Generator",), origin="derived",
           formula="p ÷ efficiency × carrier.co2_emissions",
           compute=C.gen_co2_rate, requires=(REQ_DISPATCH, REQ_CO2)),
    Metric(id="co2_total_t", label="CO₂ emitted", unit="t", kind="scalar",
           category="emissions", classes=("Generator",), origin="derived",
           formula="Σ CO₂ rate × weighting", compute=C.gen_co2_total,
           requires=(REQ_DISPATCH, REQ_CO2)),
    Metric(id="co2_intensity", label="CO₂ intensity", unit="t/MWh", kind="scalar",
           category="emissions", classes=("Generator",), origin="derived",
           formula="CO₂ emitted ÷ energy", compute=C.gen_co2_intensity,
           requires=(REQ_DISPATCH, REQ_CO2)),

    # ── storage — structurally n/a for Generator, so nothing is declared ──
)


_BUS_METRICS: tuple[Metric, ...] = (
    # A bus has almost no results of its own. What a user wants to know about
    # a node — how much capacity sits here, how much it generates, how much
    # load it serves — is an aggregate over the components attached to it, so
    # most of these compute functions walk the other frames.

    # ── capacity ─────────────────────────────────────────────────────────
    Metric(id="bus_gen_p_nom", label="Installed generation", unit="MW",
           kind="scalar", category="capacity", classes=("Bus",), origin="input",
           formula="Σ p_nom over generators at this bus",
           compute=C.bus_gen_p_nom),
    Metric(id="bus_gen_p_nom_opt", label="Optimised generation capacity",
           unit="MW", kind="scalar", category="capacity", classes=("Bus",),
           origin="derived", formula="Σ p_nom_opt over generators at this bus",
           compute=C.bus_gen_p_nom_opt, requires=(REQ_DISPATCH,)),
    Metric(id="bus_capacity_by_carrier", label="Capacity by carrier", unit="MW",
           kind="scalar", category="capacity", classes=("Bus",), origin="derived",
           formula="Σ p_nom_opt grouped by carrier",
           compute=C.bus_capacity_by_carrier, requires=(REQ_DISPATCH,)),
    Metric(id="bus_storage_p_nom_opt", label="Storage power capacity", unit="MW",
           kind="scalar", category="capacity", classes=("Bus",), origin="derived",
           formula="Σ p_nom_opt over storage units at this bus",
           compute=C.bus_storage_p_nom_opt, requires=(REQ_DISPATCH,)),
    Metric(id="bus_store_e_nom_opt", label="Store energy capacity", unit="MWh",
           kind="scalar", category="capacity", classes=("Bus",), origin="derived",
           formula="Σ e_nom_opt over stores at this bus",
           compute=C.bus_store_e_nom_opt, requires=(REQ_DISPATCH,)),
    Metric(id="bus_load_p_set", label="Mean nameplate load", unit="MW",
           kind="scalar", category="capacity", classes=("Bus",), origin="input",
           formula="weighted mean of Σ p_set over loads at this bus",
           compute=C.bus_load_p_set),

    # ── dispatch ─────────────────────────────────────────────────────────
    Metric(id="bus_generation", label="Generation", unit="MW", kind="series",
           category="dispatch", classes=("Bus",), origin="derived",
           formula="Σ p over generators at this bus",
           compute=C.bus_generation, requires=(REQ_DISPATCH,)),
    Metric(id="bus_load", label="Load", unit="MW", kind="series",
           category="dispatch", classes=("Bus",), origin="input",
           formula="Σ p over loads at this bus (p_set when unsolved)",
           compute=C.bus_load_series),
    Metric(id="bus_p", label="Net injection", unit="MW", kind="series",
           category="dispatch", classes=("Bus",), origin="output",
           compute=C.bus_net_injection, requires=(REQ_DISPATCH,)),
    Metric(id="bus_net_import", label="Net import", unit="MW", kind="series",
           category="dispatch", classes=("Bus",), origin="derived",
           formula="−net injection", compute=C.bus_net_import,
           requires=(REQ_DISPATCH,)),
    Metric(id="bus_storage_net", label="Net storage power", unit="MW",
           kind="series", category="dispatch", classes=("Bus",), origin="derived",
           formula="Σ p over storage units and stores at this bus",
           compute=C.bus_storage_net, requires=(REQ_DISPATCH,)),
    Metric(id="bus_generation_mwh", label="Generation", unit="MWh",
           kind="scalar", category="dispatch", classes=("Bus",), origin="derived",
           formula="Σ generation × weighting", compute=C.bus_generation_mwh,
           requires=(REQ_DISPATCH,)),
    Metric(id="bus_load_mwh", label="Load served", unit="MWh", kind="scalar",
           category="dispatch", classes=("Bus",), origin="derived",
           formula="Σ load × weighting", compute=C.bus_load_mwh),
    Metric(id="bus_net_import_mwh", label="Net import", unit="MWh",
           kind="scalar", category="dispatch", classes=("Bus",), origin="derived",
           formula="Σ net import × weighting", compute=C.bus_net_import_mwh,
           requires=(REQ_DISPATCH,)),
    Metric(id="bus_peak_generation", label="Peak generation", unit="MW",
           kind="scalar", category="dispatch", classes=("Bus",), origin="derived",
           formula="max generation", compute=C.bus_peak_generation,
           requires=(REQ_DISPATCH,)),
    Metric(id="bus_peak_load", label="Peak load", unit="MW", kind="scalar",
           category="dispatch", classes=("Bus",), origin="derived",
           formula="max load", compute=C.bus_peak_load),
    Metric(id="bus_load_factor", label="Load factor", unit="pu", kind="scalar",
           category="dispatch", classes=("Bus",), origin="derived",
           formula="load energy ÷ (peak load × weighted hours)",
           compute=C.bus_load_factor),
    Metric(id="bus_self_sufficiency", label="Self-sufficiency", unit="pu",
           kind="scalar", category="dispatch", classes=("Bus",), origin="derived",
           formula="generation ÷ load served", compute=C.bus_self_sufficiency,
           requires=(REQ_DISPATCH,)),

    # ── loadflow ─────────────────────────────────────────────────────────
    Metric(id="bus_v_mag_pu", label="Voltage magnitude", unit="pu", kind="series",
           category="loadflow", classes=("Bus",), origin="output",
           compute=C.raw("v_mag_pu"), requires=(REQ_DISPATCH, REQ_AC_PF),
           source_override="ac_pf"),
    Metric(id="bus_v_ang", label="Voltage angle", unit="rad", kind="series",
           category="loadflow", classes=("Bus",), origin="output",
           compute=C.raw("v_ang"), requires=(REQ_DISPATCH, REQ_AC_PF),
           source_override="ac_pf"),
    Metric(id="bus_q", label="Reactive power", unit="MVAr", kind="series",
           category="loadflow", classes=("Bus",), origin="output",
           compute=C.raw("q"), requires=(REQ_DISPATCH, REQ_AC_PF),
           source_override="ac_pf"),
    Metric(id="bus_v_min", label="Min voltage", unit="pu", kind="scalar",
           category="loadflow", classes=("Bus",), origin="derived",
           formula="min v_mag_pu", compute=C.bus_v_min,
           requires=(REQ_DISPATCH, REQ_AC_PF), source_override="ac_pf"),
    Metric(id="bus_v_max", label="Max voltage", unit="pu", kind="scalar",
           category="loadflow", classes=("Bus",), origin="derived",
           formula="max v_mag_pu", compute=C.bus_v_max,
           requires=(REQ_DISPATCH, REQ_AC_PF), source_override="ac_pf"),
    Metric(id="bus_v_mean", label="Mean voltage", unit="pu", kind="scalar",
           category="loadflow", classes=("Bus",), origin="derived",
           formula="weighted mean v_mag_pu", compute=C.bus_v_mean,
           requires=(REQ_DISPATCH, REQ_AC_PF), source_override="ac_pf"),

    # ── prices ───────────────────────────────────────────────────────────
    Metric(id="bus_marginal_price", label="Marginal price", unit="EUR/MWh",
           kind="series", category="prices", classes=("Bus",), origin="output",
           compute=C.bus_price, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="bus_price_mean", label="Mean price", unit="EUR/MWh",
           kind="scalar", category="prices", classes=("Bus",), origin="derived",
           formula="Σ λ·w ÷ Σ w", compute=C.bus_price_mean,
           requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="bus_price_min", label="Min price", unit="EUR/MWh", kind="scalar",
           category="prices", classes=("Bus",), origin="derived",
           formula="min λ", compute=C.bus_price_min,
           requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="bus_price_max", label="Max price", unit="EUR/MWh", kind="scalar",
           category="prices", classes=("Bus",), origin="derived",
           formula="max λ", compute=C.bus_price_max,
           requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="bus_load_weighted_price", label="Load-weighted price",
           unit="EUR/MWh", kind="scalar", category="prices", classes=("Bus",),
           origin="derived", formula="Σ load·λ·w ÷ Σ load·w",
           compute=C.bus_load_weighted_price, requires=(REQ_DISPATCH, REQ_DUALS)),

    # ── economics ────────────────────────────────────────────────────────
    Metric(id="bus_load_cost", label="Cost of load served", unit="EUR",
           kind="scalar", category="economics", classes=("Bus",), origin="derived",
           formula="Σ load × λ × weighting", compute=C.bus_load_cost,
           requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="bus_generation_revenue", label="Generation revenue", unit="EUR",
           kind="scalar", category="economics", classes=("Bus",), origin="derived",
           formula="Σ generation × λ × weighting",
           compute=C.bus_generation_revenue, requires=(REQ_DISPATCH, REQ_DUALS)),
)


def _branch_metrics(cls: str, rating_unit: str) -> tuple[Metric, ...]:
    """
    Line and Transformer declare an identical metric set — same PyPSA output
    columns, same arithmetic, same units. Generating both from one function
    keeps them from drifting apart, which is exactly what happened to the
    two hand-maintained copies this replaced.
    """
    return (
        # ── capacity ─────────────────────────────────────────────────────
        Metric(id="s_nom", label="Nominal rating", unit=rating_unit,
               kind="scalar", category="capacity", classes=(cls,), origin="input",
               compute=C.nom_capacity),
        Metric(id="s_nom_opt", label="Optimised rating", unit=rating_unit,
               kind="scalar", category="capacity", classes=(cls,), origin="output",
               compute=C.nom_capacity_opt, requires=(REQ_DISPATCH,)),
        Metric(id="s_nom_delta", label="Capacity expansion", unit=rating_unit,
               kind="scalar", category="capacity", classes=(cls,), origin="derived",
               formula="s_nom_opt − s_nom", compute=C.nom_capacity_delta,
               requires=(REQ_DISPATCH,)),
        Metric(id="capex_annual", label="Annualised CAPEX", unit="EUR/a",
               kind="scalar", category="capacity", classes=(cls,), origin="derived",
               formula="capital_cost × s_nom_opt (capital_cost = overnight_cost × annuity(discount_rate, lifetime) × nyears when priced via overnight_cost; the raw capital_cost column otherwise)", compute=C.capex_annual,
               requires=(REQ_DISPATCH, REQ_ANNUITY)),

        # ── loadflow ─────────────────────────────────────────────────────
        Metric(id="p0", label="Active power at bus0", unit="MW", kind="series",
               category="loadflow", classes=(cls,), origin="output",
               compute=C.raw("p0"), requires=(REQ_DISPATCH,)),
        Metric(id="p1", label="Active power at bus1", unit="MW", kind="series",
               category="loadflow", classes=(cls,), origin="output",
               compute=C.raw("p1"), requires=(REQ_DISPATCH,)),
        Metric(id="q0", label="Reactive power at bus0", unit="MVAr",
               kind="series", category="loadflow", classes=(cls,), origin="output",
               compute=C.raw("q0"), requires=(REQ_DISPATCH, REQ_AC_PF),
               source_override="ac_pf"),
        Metric(id="q1", label="Reactive power at bus1", unit="MVAr",
               kind="series", category="loadflow", classes=(cls,), origin="output",
               compute=C.raw("q1"), requires=(REQ_DISPATCH, REQ_AC_PF),
               source_override="ac_pf"),
        Metric(id="loading", label="Loading", unit="%", kind="series",
               category="loadflow", classes=(cls,), origin="derived",
               formula="|p0| ÷ s_nom_opt × 100", compute=C.br_loading,
               requires=(REQ_DISPATCH,)),
        Metric(id="losses", label="Losses", unit="MW", kind="series",
               category="loadflow", classes=(cls,), origin="derived",
               formula="p0 + p1", compute=C.br_losses, requires=(REQ_DISPATCH,)),
        Metric(id="max_loading", label="Max loading", unit="%", kind="scalar",
               category="loadflow", classes=(cls,), origin="derived",
               formula="max loading", compute=C.br_max_loading,
               requires=(REQ_DISPATCH,)),
        Metric(id="mean_loading", label="Mean loading", unit="%", kind="scalar",
               category="loadflow", classes=(cls,), origin="derived",
               formula="weighted mean loading", compute=C.br_mean_loading,
               requires=(REQ_DISPATCH,)),
        Metric(id="congested_hours", label="Congested hours", unit="h",
               kind="scalar", category="loadflow", classes=(cls,), origin="derived",
               formula="weighted hours where loading ≥ 99 %",
               compute=C.br_congested_hours, requires=(REQ_DISPATCH,)),
        Metric(id="peak_flow", label="Peak flow", unit="MW", kind="scalar",
               category="loadflow", classes=(cls,), origin="derived",
               formula="max |p0|", compute=C.br_peak_flow, requires=(REQ_DISPATCH,)),
        Metric(id="gross_transfer_mwh", label="Gross transfer", unit="MWh",
               kind="scalar", category="loadflow", classes=(cls,), origin="derived",
               formula="Σ |p0| × weighting", compute=C.br_gross_transfer_mwh,
               requires=(REQ_DISPATCH,)),
        Metric(id="net_transfer_mwh", label="Net transfer", unit="MWh",
               kind="scalar", category="loadflow", classes=(cls,), origin="derived",
               formula="Σ p0 × weighting (positive = bus0 → bus1)",
               compute=C.br_net_transfer_mwh, requires=(REQ_DISPATCH,)),
        Metric(id="losses_mwh", label="Losses", unit="MWh", kind="scalar",
               category="loadflow", classes=(cls,), origin="derived",
               formula="Σ losses × weighting", compute=C.br_losses_mwh,
               requires=(REQ_DISPATCH,)),
        Metric(id="loss_rate", label="Loss rate", unit="pu", kind="scalar",
               category="loadflow", classes=(cls,), origin="derived",
               formula="losses ÷ gross transfer", compute=C.br_loss_rate,
               requires=(REQ_DISPATCH,)),
        Metric(id="utilisation", label="Utilisation", unit="pu", kind="scalar",
               category="loadflow", classes=(cls,), origin="derived",
               formula="gross transfer ÷ (s_nom_opt × weighted hours)",
               compute=C.br_utilisation, requires=(REQ_DISPATCH,)),
        Metric(id="reverse_hours", label="Reverse-flow hours", unit="h",
               kind="scalar", category="loadflow", classes=(cls,), origin="derived",
               formula="weighted hours where p0 < 0", compute=C.br_reverse_hours,
               requires=(REQ_DISPATCH,)),
        Metric(id="idle_hours", label="Idle hours", unit="h", kind="scalar",
               category="loadflow", classes=(cls,), origin="derived",
               formula="weighted hours where p0 ≈ 0", compute=C.br_idle_hours,
               requires=(REQ_DISPATCH,)),

        # ── prices ───────────────────────────────────────────────────────
        Metric(id="price0", label="Price at bus0", unit="EUR/MWh", kind="series",
               category="prices", classes=(cls,), origin="output",
               compute=C.br_price0, requires=(REQ_DISPATCH, REQ_DUALS)),
        Metric(id="price1", label="Price at bus1", unit="EUR/MWh", kind="series",
               category="prices", classes=(cls,), origin="output",
               compute=C.br_price1, requires=(REQ_DISPATCH, REQ_DUALS)),
        Metric(id="price_spread", label="Price spread", unit="EUR/MWh",
               kind="series", category="prices", classes=(cls,), origin="derived",
               formula="λ(bus1) − λ(bus0)", compute=C.br_price_spread,
               requires=(REQ_DISPATCH, REQ_DUALS)),
        Metric(id="mu_upper", label="μ upper", unit=f"EUR/{rating_unit}",
               kind="series", category="prices", classes=(cls,), origin="output",
               compute=C.raw("mu_upper"), requires=(REQ_DISPATCH, REQ_DUALS)),
        Metric(id="mu_lower", label="μ lower", unit=f"EUR/{rating_unit}",
               kind="series", category="prices", classes=(cls,), origin="output",
               compute=C.raw("mu_lower"), requires=(REQ_DISPATCH, REQ_DUALS)),
        Metric(id="mean_spread", label="Mean price spread", unit="EUR/MWh",
               kind="scalar", category="prices", classes=(cls,), origin="derived",
               formula="weighted mean of λ(bus1) − λ(bus0)",
               compute=C.br_mean_spread, requires=(REQ_DISPATCH, REQ_DUALS)),
        Metric(id="binding_hours", label="Binding hours", unit="h",
               kind="scalar", category="prices", classes=(cls,), origin="derived",
               formula="weighted hours where μ_upper or μ_lower ≠ 0",
               compute=C.binding_hours, requires=(REQ_DISPATCH, REQ_DUALS)),

        # ── economics ────────────────────────────────────────────────────
        Metric(id="congestion_rent_eur", label="Congestion rent", unit="EUR",
               kind="scalar", category="economics", classes=(cls,),
               origin="derived", formula="Σ p0 × (λ(bus1) − λ(bus0)) × weighting",
               compute=C.br_congestion_rent, requires=(REQ_DISPATCH, REQ_DUALS)),
        Metric(id="fixed_cost_eur", label="Fixed cost", unit="EUR/a",
               kind="scalar", category="economics", classes=(cls,),
               origin="derived", formula="capital_cost × s_nom_opt (capital_cost = overnight_cost × annuity(discount_rate, lifetime) × nyears when priced via overnight_cost; the raw capital_cost column otherwise)",
               compute=C.br_fixed_cost, requires=(REQ_DISPATCH, REQ_ANNUITY)),
    )


_LINK_METRICS: tuple[Metric, ...] = (
    # ── capacity ─────────────────────────────────────────────────────────
    Metric(id="p_nom", label="Installed capacity", unit="MW", kind="scalar",
           category="capacity", classes=("Link",), origin="input",
           compute=C.nom_capacity),
    Metric(id="p_nom_opt", label="Optimised capacity", unit="MW", kind="scalar",
           category="capacity", classes=("Link",), origin="output",
           compute=C.nom_capacity_opt, requires=(REQ_DISPATCH,)),
    Metric(id="p_nom_delta", label="Capacity expansion", unit="MW", kind="scalar",
           category="capacity", classes=("Link",), origin="derived",
           formula="p_nom_opt − p_nom", compute=C.nom_capacity_delta,
           requires=(REQ_DISPATCH,)),
    Metric(id="capex_annual", label="Annualised CAPEX", unit="EUR/a",
           kind="scalar", category="capacity", classes=("Link",), origin="derived",
           formula="capital_cost × p_nom_opt (capital_cost = overnight_cost × annuity(discount_rate, lifetime) × nyears when priced via overnight_cost; the raw capital_cost column otherwise)", compute=C.capex_annual,
           requires=(REQ_DISPATCH, REQ_ANNUITY)),

    # ── dispatch ─────────────────────────────────────────────────────────
    Metric(id="p0", label="Withdrawal at bus0", unit="MW", kind="series",
           category="dispatch", classes=("Link",), origin="output",
           compute=C.raw("p0"), requires=(REQ_DISPATCH,)),
    Metric(id="link_output", label="Delivery at bus1", unit="MW", kind="series",
           category="dispatch", classes=("Link",), origin="derived",
           formula="−p1", compute=C.link_output, requires=(REQ_DISPATCH,)),
    Metric(id="losses", label="Conversion losses", unit="MW", kind="series",
           category="dispatch", classes=("Link",), origin="derived",
           formula="p0 + p1", compute=C.br_losses, requires=(REQ_DISPATCH,)),
    Metric(id="status", label="Committed", unit="", kind="series",
           category="dispatch", classes=("Link",), origin="output",
           compute=C.raw("status"), requires=(REQ_DISPATCH, REQ_COMMITTABLE)),
    Metric(id="start_up", label="Start-up", unit="1", kind="series",
           category="dispatch", classes=("Link",), origin="output",
           compute=C.raw("start_up"), requires=(REQ_DISPATCH, REQ_COMMITTABLE)),
    Metric(id="shut_down", label="Shut-down", unit="1", kind="series",
           category="dispatch", classes=("Link",), origin="output",
           compute=C.raw("shut_down"), requires=(REQ_DISPATCH, REQ_COMMITTABLE)),
    Metric(id="energy_in_mwh", label="Energy withdrawn", unit="MWh",
           kind="scalar", category="dispatch", classes=("Link",), origin="derived",
           formula="Σ p0 × weighting", compute=C.link_energy_in,
           requires=(REQ_DISPATCH,)),
    Metric(id="energy_out_mwh", label="Energy delivered", unit="MWh",
           kind="scalar", category="dispatch", classes=("Link",), origin="derived",
           formula="Σ −p1 × weighting", compute=C.link_energy_out,
           requires=(REQ_DISPATCH,)),
    Metric(id="losses_mwh", label="Conversion losses", unit="MWh", kind="scalar",
           category="dispatch", classes=("Link",), origin="derived",
           formula="Σ (p0 + p1) × weighting", compute=C.link_losses_mwh,
           requires=(REQ_DISPATCH,)),
    Metric(id="mean_efficiency", label="Realised efficiency", unit="pu",
           kind="scalar", category="dispatch", classes=("Link",), origin="derived",
           formula="energy delivered ÷ energy withdrawn",
           compute=C.link_mean_efficiency, requires=(REQ_DISPATCH,)),
    Metric(id="peak_flow", label="Peak throughput", unit="MW", kind="scalar",
           category="dispatch", classes=("Link",), origin="derived",
           formula="max |p0|", compute=C.br_peak_flow, requires=(REQ_DISPATCH,)),
    Metric(id="full_load_hours", label="Full-load hours", unit="h",
           kind="scalar", category="dispatch", classes=("Link",), origin="derived",
           formula="energy withdrawn ÷ p_nom_opt", compute=C.link_full_load_hours,
           requires=(REQ_DISPATCH,)),
    Metric(id="mean_capacity_factor", label="Mean capacity factor", unit="pu",
           kind="scalar", category="dispatch", classes=("Link",), origin="derived",
           formula="Σ |p0| × w ÷ (p_nom_opt × weighted hours)",
           compute=C.link_mean_capacity_factor, requires=(REQ_DISPATCH,)),
    Metric(id="idle_hours", label="Idle hours", unit="h", kind="scalar",
           category="dispatch", classes=("Link",), origin="derived",
           formula="weighted hours where p0 ≈ 0", compute=C.br_idle_hours,
           requires=(REQ_DISPATCH,)),
    Metric(id="reverse_hours", label="Reverse-flow hours", unit="h",
           kind="scalar", category="dispatch", classes=("Link",), origin="derived",
           formula="weighted hours where p0 < 0", compute=C.br_reverse_hours,
           requires=(REQ_DISPATCH,)),
    Metric(id="max_ramp_up", label="Max ramp up", unit="MW/h", kind="scalar",
           category="dispatch", classes=("Link",), origin="derived",
           formula="max Δp0 between consecutive snapshots",
           compute=C.link_max_ramp_up, requires=(REQ_DISPATCH,)),
    Metric(id="max_ramp_down", label="Max ramp down", unit="MW/h", kind="scalar",
           category="dispatch", classes=("Link",), origin="derived",
           formula="min Δp0 between consecutive snapshots",
           compute=C.link_max_ramp_down, requires=(REQ_DISPATCH,)),
    Metric(id="n_starts", label="Start-up count", unit="", kind="scalar",
           category="dispatch", classes=("Link",), origin="derived",
           formula="Σ start_up", compute=C.link_n_starts,
           requires=(REQ_DISPATCH, REQ_COMMITTABLE)),

    # ── loadflow ─────────────────────────────────────────────────────────
    Metric(id="loading", label="Loading", unit="%", kind="series",
           category="loadflow", classes=("Link",), origin="derived",
           formula="|p0| ÷ p_nom_opt × 100", compute=C.br_loading,
           requires=(REQ_DISPATCH,)),
    Metric(id="max_loading", label="Max loading", unit="%", kind="scalar",
           category="loadflow", classes=("Link",), origin="derived",
           formula="max loading", compute=C.br_max_loading,
           requires=(REQ_DISPATCH,)),
    Metric(id="mean_loading", label="Mean loading", unit="%", kind="scalar",
           category="loadflow", classes=("Link",), origin="derived",
           formula="weighted mean loading", compute=C.br_mean_loading,
           requires=(REQ_DISPATCH,)),
    Metric(id="congested_hours", label="Congested hours", unit="h",
           kind="scalar", category="loadflow", classes=("Link",), origin="derived",
           formula="weighted hours where loading ≥ 99 %",
           compute=C.br_congested_hours, requires=(REQ_DISPATCH,)),
    Metric(id="utilisation", label="Utilisation", unit="pu", kind="scalar",
           category="loadflow", classes=("Link",), origin="derived",
           formula="gross transfer ÷ (p_nom_opt × weighted hours)",
           compute=C.br_utilisation, requires=(REQ_DISPATCH,)),

    # ── prices ───────────────────────────────────────────────────────────
    Metric(id="price0", label="Price at bus0", unit="EUR/MWh", kind="series",
           category="prices", classes=("Link",), origin="output",
           compute=C.br_price0, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="price1", label="Price at bus1", unit="EUR/MWh", kind="series",
           category="prices", classes=("Link",), origin="output",
           compute=C.br_price1, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="price_spread", label="Price spread", unit="EUR/MWh", kind="series",
           category="prices", classes=("Link",), origin="derived",
           formula="λ(bus1) − λ(bus0)", compute=C.br_price_spread,
           requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="mu_upper", label="μ upper", unit="EUR/MW", kind="series",
           category="prices", classes=("Link",), origin="output",
           compute=C.raw("mu_upper"), requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="mu_lower", label="μ lower", unit="EUR/MW", kind="series",
           category="prices", classes=("Link",), origin="output",
           compute=C.raw("mu_lower"), requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="mean_spread", label="Mean price spread", unit="EUR/MWh",
           kind="scalar", category="prices", classes=("Link",), origin="derived",
           formula="weighted mean of λ(bus1) − λ(bus0)",
           compute=C.br_mean_spread, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="binding_hours", label="Binding hours", unit="h", kind="scalar",
           category="prices", classes=("Link",), origin="derived",
           formula="weighted hours where μ_upper or μ_lower ≠ 0",
           compute=C.binding_hours, requires=(REQ_DISPATCH, REQ_DUALS)),

    # ── economics ────────────────────────────────────────────────────────
    Metric(id="input_cost_eur", label="Input energy cost", unit="EUR",
           kind="scalar", category="economics", classes=("Link",),
           origin="derived", formula="Σ p0 × λ(bus0) × weighting",
           compute=C.link_input_cost, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="output_revenue_eur", label="Output revenue", unit="EUR",
           kind="scalar", category="economics", classes=("Link",),
           origin="derived", formula="Σ −p1 × λ(bus1) × weighting",
           compute=C.link_output_revenue, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="vom_cost_eur", label="Variable O&M", unit="EUR", kind="scalar",
           category="economics", classes=("Link",), origin="derived",
           formula="Σ |p0| × marginal_cost × weighting", compute=C.link_vom,
           requires=(REQ_DISPATCH,)),
    Metric(id="fixed_cost_eur", label="Fixed cost", unit="EUR/a", kind="scalar",
           category="economics", classes=("Link",), origin="derived",
           formula="capital_cost × p_nom_opt (capital_cost = overnight_cost × annuity(discount_rate, lifetime) × nyears when priced via overnight_cost; the raw capital_cost column otherwise)", compute=C.capex_annual,
           requires=(REQ_DISPATCH, REQ_ANNUITY)),
    Metric(id="net_profit_eur", label="Net profit", unit="EUR", kind="scalar",
           category="economics", classes=("Link",), origin="derived",
           formula="output revenue − (input cost + VOM + fixed cost)",
           compute=C.link_net_profit, requires=(REQ_DISPATCH, REQ_DUALS)),

    # ── emissions ────────────────────────────────────────────────────────
    Metric(id="co2_rate", label="CO₂ rate", unit="t/h", kind="series",
           category="emissions", classes=("Link",), origin="derived",
           formula="p0 × carrier.co2_emissions", compute=C.link_co2_rate,
           requires=(REQ_DISPATCH, REQ_CO2)),
    Metric(id="co2_total_t", label="CO₂ emitted", unit="t", kind="scalar",
           category="emissions", classes=("Link",), origin="derived",
           formula="Σ CO₂ rate × weighting", compute=C.link_co2_total,
           requires=(REQ_DISPATCH, REQ_CO2)),
    Metric(id="co2_intensity", label="CO₂ intensity", unit="t/MWh",
           kind="scalar", category="emissions", classes=("Link",),
           origin="derived", formula="CO₂ emitted ÷ energy delivered",
           compute=C.link_co2_intensity, requires=(REQ_DISPATCH, REQ_CO2)),
)


_STORAGE_UNIT_METRICS: tuple[Metric, ...] = (
    # ── capacity ─────────────────────────────────────────────────────────
    Metric(id="p_nom", label="Installed power", unit="MW", kind="scalar",
           category="capacity", classes=("StorageUnit",), origin="input",
           compute=C.nom_capacity),
    Metric(id="p_nom_opt", label="Optimised power", unit="MW", kind="scalar",
           category="capacity", classes=("StorageUnit",), origin="output",
           compute=C.nom_capacity_opt, requires=(REQ_DISPATCH,)),
    Metric(id="p_nom_delta", label="Capacity expansion", unit="MW", kind="scalar",
           category="capacity", classes=("StorageUnit",), origin="derived",
           formula="p_nom_opt − p_nom", compute=C.nom_capacity_delta,
           requires=(REQ_DISPATCH,)),
    Metric(id="energy_capacity", label="Energy capacity", unit="MWh",
           kind="scalar", category="capacity", classes=("StorageUnit",),
           origin="derived", formula="p_nom_opt × max_hours",
           compute=C.su_energy_capacity, requires=(REQ_DISPATCH,)),
    Metric(id="capex_annual", label="Annualised CAPEX", unit="EUR/a",
           kind="scalar", category="capacity", classes=("StorageUnit",),
           origin="derived", formula="capital_cost × p_nom_opt (capital_cost = overnight_cost × annuity(discount_rate, lifetime) × nyears when priced via overnight_cost; the raw capital_cost column otherwise)",
           compute=C.capex_annual, requires=(REQ_DISPATCH, REQ_ANNUITY)),

    # ── dispatch ─────────────────────────────────────────────────────────
    Metric(id="p", label="Net power", unit="MW", kind="series",
           category="dispatch", classes=("StorageUnit",), origin="output",
           formula="p_dispatch − p_store (positive = discharging)",
           compute=C.raw("p"), requires=(REQ_DISPATCH,)),
    Metric(id="p_dispatch", label="Discharge", unit="MW", kind="series",
           category="dispatch", classes=("StorageUnit",), origin="output",
           compute=C.raw("p_dispatch"), requires=(REQ_DISPATCH,)),
    Metric(id="p_store", label="Charge", unit="MW", kind="series",
           category="dispatch", classes=("StorageUnit",), origin="output",
           compute=C.raw("p_store"), requires=(REQ_DISPATCH,)),
    Metric(id="spill", label="Spillage", unit="MW", kind="series",
           category="dispatch", classes=("StorageUnit",), origin="output",
           compute=C.raw("spill"), requires=(REQ_DISPATCH,)),
    Metric(id="energy_discharged_mwh", label="Energy discharged", unit="MWh",
           kind="scalar", category="dispatch", classes=("StorageUnit",),
           origin="derived", formula="Σ p_dispatch × weighting",
           compute=C.su_energy_discharged, requires=(REQ_DISPATCH,)),
    Metric(id="energy_charged_mwh", label="Energy charged", unit="MWh",
           kind="scalar", category="dispatch", classes=("StorageUnit",),
           origin="derived", formula="Σ p_store × weighting",
           compute=C.su_energy_charged, requires=(REQ_DISPATCH,)),
    Metric(id="spilled_mwh", label="Energy spilled", unit="MWh", kind="scalar",
           category="dispatch", classes=("StorageUnit",), origin="derived",
           formula="Σ spill × weighting", compute=C.su_spilled,
           requires=(REQ_DISPATCH,)),
    Metric(id="peak_discharge", label="Peak discharge", unit="MW", kind="scalar",
           category="dispatch", classes=("StorageUnit",), origin="derived",
           formula="max p_dispatch", compute=C.su_peak_discharge,
           requires=(REQ_DISPATCH,)),
    Metric(id="peak_charge", label="Peak charge", unit="MW", kind="scalar",
           category="dispatch", classes=("StorageUnit",), origin="derived",
           formula="max p_store", compute=C.su_peak_charge,
           requires=(REQ_DISPATCH,)),
    Metric(id="discharge_hours", label="Discharging hours", unit="h",
           kind="scalar", category="dispatch", classes=("StorageUnit",),
           origin="derived", formula="weighted hours where p_dispatch > 0",
           compute=C.su_discharge_hours, requires=(REQ_DISPATCH,)),
    Metric(id="charge_hours", label="Charging hours", unit="h", kind="scalar",
           category="dispatch", classes=("StorageUnit",), origin="derived",
           formula="weighted hours where p_store > 0",
           compute=C.su_charge_hours, requires=(REQ_DISPATCH,)),
    Metric(id="idle_hours", label="Idle hours", unit="h", kind="scalar",
           category="dispatch", classes=("StorageUnit",), origin="derived",
           formula="weighted hours where p ≈ 0", compute=C.su_idle_hours,
           requires=(REQ_DISPATCH,)),
    Metric(id="max_ramp_up", label="Max ramp up", unit="MW/h", kind="scalar",
           category="dispatch", classes=("StorageUnit",), origin="derived",
           formula="max Δp between consecutive snapshots",
           compute=C.su_max_ramp_up, requires=(REQ_DISPATCH,)),
    Metric(id="max_ramp_down", label="Max ramp down", unit="MW/h", kind="scalar",
           category="dispatch", classes=("StorageUnit",), origin="derived",
           formula="min Δp between consecutive snapshots",
           compute=C.su_max_ramp_down, requires=(REQ_DISPATCH,)),

    # ── storage ──────────────────────────────────────────────────────────
    Metric(id="state_of_charge", label="State of charge", unit="MWh",
           kind="series", category="storage", classes=("StorageUnit",),
           origin="output", compute=C.raw("state_of_charge"),
           requires=(REQ_DISPATCH,)),
    Metric(id="soc_pu", label="State of charge", unit="pu", kind="series",
           category="storage", classes=("StorageUnit",), origin="derived",
           formula="state_of_charge ÷ (p_nom_opt × max_hours)",
           compute=C.su_soc_pu, requires=(REQ_DISPATCH,)),
    Metric(id="soc_min", label="Min state of charge", unit="MWh", kind="scalar",
           category="storage", classes=("StorageUnit",), origin="derived",
           formula="min state_of_charge", compute=C.su_soc_min,
           requires=(REQ_DISPATCH,)),
    Metric(id="soc_max", label="Max state of charge", unit="MWh", kind="scalar",
           category="storage", classes=("StorageUnit",), origin="derived",
           formula="max state_of_charge", compute=C.su_soc_max,
           requires=(REQ_DISPATCH,)),
    Metric(id="soc_mean", label="Mean state of charge", unit="MWh",
           kind="scalar", category="storage", classes=("StorageUnit",),
           origin="derived", formula="weighted mean state_of_charge",
           compute=C.su_soc_mean, requires=(REQ_DISPATCH,)),
    Metric(id="full_cycles", label="Equivalent full cycles", unit="",
           kind="scalar", category="storage", classes=("StorageUnit",),
           origin="derived", formula="energy discharged ÷ energy capacity",
           compute=C.su_full_cycles, requires=(REQ_DISPATCH,)),
    Metric(id="round_trip_efficiency", label="Realised round-trip efficiency",
           unit="pu", kind="scalar", category="storage", classes=("StorageUnit",),
           origin="derived", formula="energy discharged ÷ energy charged",
           compute=C.su_round_trip_efficiency, requires=(REQ_DISPATCH,)),
    Metric(id="depth_of_discharge", label="Depth of discharge", unit="pu",
           kind="scalar", category="storage", classes=("StorageUnit",),
           origin="derived", formula="(max SOC − min SOC) ÷ energy capacity",
           compute=C.su_depth_of_discharge, requires=(REQ_DISPATCH,)),

    # ── loadflow ─────────────────────────────────────────────────────────
    Metric(id="q", label="Reactive power", unit="MVAr", kind="series",
           category="loadflow", classes=("StorageUnit",), origin="output",
           compute=C.raw("q"), requires=(REQ_DISPATCH, REQ_AC_PF),
           source_override="ac_pf"),

    # ── prices ───────────────────────────────────────────────────────────
    Metric(id="bus_marginal_price", label="Bus marginal price", unit="EUR/MWh",
           kind="series", category="prices", classes=("StorageUnit",),
           origin="output", compute=C.bus_price_series,
           requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="mu_upper", label="μ upper", unit="EUR/MWh", kind="series",
           category="prices", classes=("StorageUnit",), origin="output",
           compute=C.raw("mu_upper"), requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="mu_lower", label="μ lower", unit="EUR/MWh", kind="series",
           category="prices", classes=("StorageUnit",), origin="output",
           compute=C.raw("mu_lower"), requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="mu_energy_balance", label="μ energy balance", unit="EUR/MWh",
           kind="series", category="prices", classes=("StorageUnit",),
           origin="output", compute=C.raw("mu_energy_balance"),
           requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="capture_price", label="Discharge capture price", unit="EUR/MWh",
           kind="scalar", category="prices", classes=("StorageUnit",),
           origin="derived", formula="Σ p_dispatch·λ·w ÷ Σ p_dispatch·w",
           compute=C.su_capture_price, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="charge_price", label="Charge cost price", unit="EUR/MWh",
           kind="scalar", category="prices", classes=("StorageUnit",),
           origin="derived", formula="Σ p_store·λ·w ÷ Σ p_store·w",
           compute=C.su_charge_price, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="captured_spread", label="Captured spread", unit="EUR/MWh",
           kind="scalar", category="prices", classes=("StorageUnit",),
           origin="derived", formula="capture price − charge price",
           compute=C.su_captured_spread, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="binding_hours", label="Binding hours", unit="h", kind="scalar",
           category="prices", classes=("StorageUnit",), origin="derived",
           formula="weighted hours where μ_upper or μ_lower ≠ 0",
           compute=C.binding_hours, requires=(REQ_DISPATCH, REQ_DUALS)),

    # ── economics ────────────────────────────────────────────────────────
    Metric(id="revenue_eur", label="Discharge revenue", unit="EUR",
           kind="scalar", category="economics", classes=("StorageUnit",),
           origin="derived", formula="Σ p_dispatch × λ × weighting",
           compute=C.su_revenue, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="charging_cost_eur", label="Charging cost", unit="EUR",
           kind="scalar", category="economics", classes=("StorageUnit",),
           origin="derived", formula="Σ p_store × λ × weighting",
           compute=C.su_charging_cost, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="vom_cost_eur", label="Variable O&M", unit="EUR", kind="scalar",
           category="economics", classes=("StorageUnit",), origin="derived",
           formula="Σ |p| × marginal_cost × weighting", compute=C.su_vom,
           requires=(REQ_DISPATCH,)),
    Metric(id="fixed_cost_eur", label="Fixed cost", unit="EUR/a", kind="scalar",
           category="economics", classes=("StorageUnit",), origin="derived",
           formula="capital_cost × p_nom_opt (capital_cost = overnight_cost × annuity(discount_rate, lifetime) × nyears when priced via overnight_cost; the raw capital_cost column otherwise)", compute=C.capex_annual,
           requires=(REQ_DISPATCH, REQ_ANNUITY)),
    Metric(id="net_profit_eur", label="Net profit", unit="EUR", kind="scalar",
           category="economics", classes=("StorageUnit",), origin="derived",
           formula="revenue − (charging cost + VOM + fixed cost)",
           compute=C.su_net_profit, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="lcos_eur_per_mwh", label="LCOS", unit="EUR/MWh", kind="scalar",
           category="economics", classes=("StorageUnit",), origin="derived",
           formula="(fixed cost + VOM + charging cost) ÷ energy discharged",
           compute=C.su_lcos, requires=(REQ_DISPATCH, REQ_DUALS)),
)


_STORE_METRICS: tuple[Metric, ...] = (
    # ── capacity ─────────────────────────────────────────────────────────
    Metric(id="e_nom", label="Installed energy capacity", unit="MWh",
           kind="scalar", category="capacity", classes=("Store",), origin="input",
           compute=C.nom_capacity),
    Metric(id="e_nom_opt", label="Optimised energy capacity", unit="MWh",
           kind="scalar", category="capacity", classes=("Store",), origin="output",
           compute=C.nom_capacity_opt, requires=(REQ_DISPATCH,)),
    Metric(id="e_nom_delta", label="Capacity expansion", unit="MWh",
           kind="scalar", category="capacity", classes=("Store",), origin="derived",
           formula="e_nom_opt − e_nom", compute=C.nom_capacity_delta,
           requires=(REQ_DISPATCH,)),
    Metric(id="capex_annual", label="Annualised CAPEX", unit="EUR/a",
           kind="scalar", category="capacity", classes=("Store",), origin="derived",
           formula="capital_cost × e_nom_opt (capital_cost = overnight_cost × annuity(discount_rate, lifetime) × nyears when priced via overnight_cost; the raw capital_cost column otherwise)", compute=C.capex_annual,
           requires=(REQ_DISPATCH, REQ_ANNUITY)),

    # ── dispatch ─────────────────────────────────────────────────────────
    Metric(id="p", label="Power", unit="MW", kind="series", category="dispatch",
           classes=("Store",), origin="output",
           formula="positive = discharging into the bus",
           compute=C.raw("p"), requires=(REQ_DISPATCH,)),
    Metric(id="discharge", label="Discharge", unit="MW", kind="series",
           category="dispatch", classes=("Store",), origin="derived",
           formula="max(p, 0)", compute=C.store_discharge,
           requires=(REQ_DISPATCH,)),
    Metric(id="charge", label="Charge", unit="MW", kind="series",
           category="dispatch", classes=("Store",), origin="derived",
           formula="max(−p, 0)", compute=C.store_charge, requires=(REQ_DISPATCH,)),
    Metric(id="energy_out_mwh", label="Energy discharged", unit="MWh",
           kind="scalar", category="dispatch", classes=("Store",), origin="derived",
           formula="Σ max(p, 0) × weighting", compute=C.store_energy_out,
           requires=(REQ_DISPATCH,)),
    Metric(id="energy_in_mwh", label="Energy charged", unit="MWh", kind="scalar",
           category="dispatch", classes=("Store",), origin="derived",
           formula="Σ max(−p, 0) × weighting", compute=C.store_energy_in,
           requires=(REQ_DISPATCH,)),
    Metric(id="peak_discharge", label="Peak discharge", unit="MW", kind="scalar",
           category="dispatch", classes=("Store",), origin="derived",
           formula="max p", compute=C.store_peak_discharge,
           requires=(REQ_DISPATCH,)),
    Metric(id="peak_charge", label="Peak charge", unit="MW", kind="scalar",
           category="dispatch", classes=("Store",), origin="derived",
           formula="max −p", compute=C.store_peak_charge, requires=(REQ_DISPATCH,)),

    # ── storage ──────────────────────────────────────────────────────────
    Metric(id="e", label="Energy level", unit="MWh", kind="series",
           category="storage", classes=("Store",), origin="output",
           compute=C.raw("e"), requires=(REQ_DISPATCH,)),
    Metric(id="e_pu", label="Energy level", unit="pu", kind="series",
           category="storage", classes=("Store",), origin="derived",
           formula="e ÷ e_nom_opt", compute=C.store_e_pu, requires=(REQ_DISPATCH,)),
    Metric(id="e_min", label="Min energy level", unit="MWh", kind="scalar",
           category="storage", classes=("Store",), origin="derived",
           formula="min e", compute=C.store_e_min, requires=(REQ_DISPATCH,)),
    Metric(id="e_max", label="Max energy level", unit="MWh", kind="scalar",
           category="storage", classes=("Store",), origin="derived",
           formula="max e", compute=C.store_e_max, requires=(REQ_DISPATCH,)),
    Metric(id="e_mean", label="Mean energy level", unit="MWh", kind="scalar",
           category="storage", classes=("Store",), origin="derived",
           formula="weighted mean e", compute=C.store_e_mean,
           requires=(REQ_DISPATCH,)),
    Metric(id="full_cycles", label="Equivalent full cycles", unit="",
           kind="scalar", category="storage", classes=("Store",), origin="derived",
           formula="energy discharged ÷ e_nom_opt", compute=C.store_full_cycles,
           requires=(REQ_DISPATCH,)),
    Metric(id="round_trip_efficiency", label="Realised round-trip efficiency",
           unit="pu", kind="scalar", category="storage", classes=("Store",),
           origin="derived", formula="energy discharged ÷ energy charged",
           compute=C.store_round_trip_efficiency, requires=(REQ_DISPATCH,)),
    Metric(id="net_absorbed_mwh", label="Net energy absorbed", unit="MWh",
           kind="scalar", category="storage", classes=("Store",), origin="derived",
           formula="energy charged − energy discharged (= Δlevel + standing "
                   "loss; on a cyclic store over the full horizon, the loss)",
           compute=C.store_net_absorbed_mwh, requires=(REQ_DISPATCH,)),

    # ── loadflow ─────────────────────────────────────────────────────────
    Metric(id="q", label="Reactive power", unit="MVAr", kind="series",
           category="loadflow", classes=("Store",), origin="output",
           compute=C.raw("q"), requires=(REQ_DISPATCH, REQ_AC_PF),
           source_override="ac_pf"),

    # ── prices ───────────────────────────────────────────────────────────
    Metric(id="bus_marginal_price", label="Bus marginal price", unit="EUR/MWh",
           kind="series", category="prices", classes=("Store",), origin="output",
           compute=C.bus_price_series, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="mu_upper", label="μ upper", unit="EUR/MWh", kind="series",
           category="prices", classes=("Store",), origin="output",
           compute=C.raw("mu_upper"), requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="mu_lower", label="μ lower", unit="EUR/MWh", kind="series",
           category="prices", classes=("Store",), origin="output",
           compute=C.raw("mu_lower"), requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="mu_energy_balance", label="μ energy balance", unit="EUR/MWh",
           kind="series", category="prices", classes=("Store",), origin="output",
           compute=C.raw("mu_energy_balance"), requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="capture_price", label="Discharge capture price", unit="EUR/MWh",
           kind="scalar", category="prices", classes=("Store",), origin="derived",
           formula="Σ discharge·λ·w ÷ Σ discharge·w",
           compute=C.store_capture_price, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="charge_price", label="Charge cost price", unit="EUR/MWh",
           kind="scalar", category="prices", classes=("Store",), origin="derived",
           formula="Σ charge·λ·w ÷ Σ charge·w", compute=C.store_charge_price,
           requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="binding_hours", label="Binding hours", unit="h", kind="scalar",
           category="prices", classes=("Store",), origin="derived",
           formula="weighted hours where μ_upper or μ_lower ≠ 0",
           compute=C.binding_hours, requires=(REQ_DISPATCH, REQ_DUALS)),

    # ── economics ────────────────────────────────────────────────────────
    Metric(id="revenue_eur", label="Discharge revenue", unit="EUR",
           kind="scalar", category="economics", classes=("Store",),
           origin="derived", formula="Σ discharge × λ × weighting",
           compute=C.store_revenue, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="charging_cost_eur", label="Charging cost", unit="EUR",
           kind="scalar", category="economics", classes=("Store",),
           origin="derived", formula="Σ charge × λ × weighting",
           compute=C.store_charging_cost, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="vom_cost_eur", label="Variable O&M", unit="EUR", kind="scalar",
           category="economics", classes=("Store",), origin="derived",
           formula="Σ |p| × marginal_cost × weighting", compute=C.store_vom,
           requires=(REQ_DISPATCH,)),
    Metric(id="fixed_cost_eur", label="Fixed cost", unit="EUR/a", kind="scalar",
           category="economics", classes=("Store",), origin="derived",
           formula="capital_cost × e_nom_opt (capital_cost = overnight_cost × annuity(discount_rate, lifetime) × nyears when priced via overnight_cost; the raw capital_cost column otherwise)", compute=C.capex_annual,
           requires=(REQ_DISPATCH, REQ_ANNUITY)),
    Metric(id="net_profit_eur", label="Net profit", unit="EUR", kind="scalar",
           category="economics", classes=("Store",), origin="derived",
           formula="revenue − (charging cost + VOM + fixed cost)",
           compute=C.store_net_profit, requires=(REQ_DISPATCH, REQ_DUALS)),
)


_LOAD_METRICS: tuple[Metric, ...] = (
    # ── dispatch ─────────────────────────────────────────────────────────
    # Load is an input, so unlike every other class these do NOT require a
    # solve: `load_profile` falls back to the dense p_set profile, and a user
    # opening an unsolved network should still see the demand they entered.
    Metric(id="load_p", label="Demand", unit="MW", kind="series",
           category="dispatch", classes=("Load",), origin="input",
           formula="loads_t.p, or the p_set input profile when unsolved",
           compute=C.load_profile),
    Metric(id="load_p_set", label="Set point", unit="MW", kind="series",
           category="dispatch", classes=("Load",), origin="input",
           compute=C.raw("p_set")),
    Metric(id="energy_mwh", label="Energy consumed", unit="MWh", kind="scalar",
           category="dispatch", classes=("Load",), origin="derived",
           formula="Σ demand × weighting", compute=C.load_energy),
    Metric(id="peak_mw", label="Peak demand", unit="MW", kind="scalar",
           category="dispatch", classes=("Load",), origin="derived",
           formula="max demand", compute=C.load_peak),
    Metric(id="min_mw", label="Minimum demand", unit="MW", kind="scalar",
           category="dispatch", classes=("Load",), origin="derived",
           formula="min demand", compute=C.load_min),
    Metric(id="mean_mw", label="Mean demand", unit="MW", kind="scalar",
           category="dispatch", classes=("Load",), origin="derived",
           formula="weighted mean demand", compute=C.load_mean),
    Metric(id="load_factor", label="Load factor", unit="pu", kind="scalar",
           category="dispatch", classes=("Load",), origin="derived",
           formula="energy ÷ (peak × weighted hours)", compute=C.load_factor),

    # ── loadflow ─────────────────────────────────────────────────────────
    Metric(id="q", label="Reactive power", unit="MVAr", kind="series",
           category="loadflow", classes=("Load",), origin="output",
           compute=C.raw("q"), requires=(REQ_DISPATCH, REQ_AC_PF),
           source_override="ac_pf"),

    # ── prices ───────────────────────────────────────────────────────────
    Metric(id="bus_marginal_price", label="Bus marginal price", unit="EUR/MWh",
           kind="series", category="prices", classes=("Load",), origin="output",
           compute=C.bus_price_series, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="price_paid", label="Demand-weighted price", unit="EUR/MWh",
           kind="scalar", category="prices", classes=("Load",), origin="derived",
           formula="Σ demand·λ·w ÷ Σ demand·w", compute=C.load_price_paid,
           requires=(REQ_DISPATCH, REQ_DUALS)),

    # ── economics ────────────────────────────────────────────────────────
    Metric(id="energy_cost_eur", label="Cost of energy", unit="EUR",
           kind="scalar", category="economics", classes=("Load",),
           origin="derived", formula="Σ demand × λ × weighting",
           compute=C.load_cost, requires=(REQ_DISPATCH, REQ_DUALS)),
)


METRICS: tuple[Metric, ...] = (
    _SUMMARY_METRICS
    + _GENERATOR_METRICS
    + _BUS_METRICS
    + _branch_metrics("Line", "MVA")
    + _branch_metrics("Transformer", "MVA")
    + _LINK_METRICS
    + _STORAGE_UNIT_METRICS
    + _STORE_METRICS
    + _LOAD_METRICS
)


# ── Headline metrics ────────────────────────────────────────────────────────
# The Summary tab lifts these out of the other categories so a user sees the
# handful of numbers that actually characterise an asset without clicking
# through seven tabs. Ids only — the registry entry above stays the single
# definition of each metric's label, unit, formula and preconditions, and
# `service.build_response` resolves them through the same applicability path
# as any other metric, so a headline that is blocked says so rather than
# silently vanishing.
HEADLINE: dict[str, tuple[str, ...]] = {
    "Bus": ("bus_gen_p_nom_opt", "bus_load_mwh", "bus_generation_mwh",
            "bus_peak_load", "bus_price_mean", "bus_self_sufficiency",
            "bus_v_min", "bus_v_max"),
    "Generator": ("p_nom_opt", "energy_mwh", "mean_capacity_factor",
                  "full_load_hours", "curtailed_mwh", "capture_price",
                  "revenue_eur", "net_profit_eur", "lcoe_eur_per_mwh",
                  "co2_total_t"),
    "Load": ("energy_mwh", "peak_mw", "load_factor", "price_paid",
             "energy_cost_eur"),
    "Line": ("s_nom_opt", "max_loading", "mean_loading", "congested_hours",
             "gross_transfer_mwh", "losses_mwh", "congestion_rent_eur"),
    "Transformer": ("s_nom_opt", "max_loading", "mean_loading",
                    "congested_hours", "gross_transfer_mwh", "losses_mwh",
                    "congestion_rent_eur"),
    "Link": ("p_nom_opt", "energy_in_mwh", "energy_out_mwh",
             "mean_efficiency", "mean_capacity_factor", "congested_hours",
             "net_profit_eur", "co2_total_t"),
    "StorageUnit": ("p_nom_opt", "energy_capacity", "energy_discharged_mwh",
                    "full_cycles", "round_trip_efficiency", "captured_spread",
                    "net_profit_eur", "lcos_eur_per_mwh"),
    "Store": ("e_nom_opt", "energy_out_mwh", "full_cycles",
              "round_trip_efficiency", "capture_price", "net_profit_eur"),
}

# Metric ids are unique within a (class, category) pair, NOT globally: `p0`
# means one thing on a Line and another on a Link, and `mu_upper` is declared
# by six classes. Every consumer — the response's `metrics`/`columns` arrays,
# the frontend's tick-set memory (keyed by class + category), the chat tool's
# `metrics` argument, the workbook's per-category sheets — already works
# inside one class and one category, so scoping the ids there keeps them
# short and readable instead of forcing `link_p_nom_opt`-style prefixes on
# every user-visible surface. `test_metric_ids_are_unique_within_class_and_category`
# is the guard that keeps that invariant true.
_BY_CLASS_ID: dict[tuple[str, str], Metric] = {
    (cls, m.id): m for m in METRICS for cls in m.classes
}
_FIRST_BY_ID: dict[str, Metric] = {}
for _m in METRICS:
    _FIRST_BY_ID.setdefault(_m.id, _m)


def metric_for(component_class: str, metric_id: str) -> Metric | None:
    """The one metric a class declares under this id. Prefer this."""
    return _BY_CLASS_ID.get((component_class, metric_id))


def metric_by_id(metric_id: str) -> Metric | None:
    """
    Class-agnostic lookup, first declaration wins.

    Ambiguous by construction now that ids are class-scoped — use
    `metric_for` whenever the class is known. Kept for callers that only
    have an id and want its label/unit for display.
    """
    return _FIRST_BY_ID.get(metric_id)


def metrics_for(component_class: str, category: str) -> tuple[Metric, ...]:
    return tuple(
        m for m in METRICS
        if m.category == category and component_class in m.classes
    )


def headline_ids(component_class: str) -> tuple[str, ...]:
    """Ordered headline metric ids for a class; empty when none are curated."""
    return HEADLINE.get(component_class, ())
