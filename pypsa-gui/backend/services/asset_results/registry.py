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
REQ_NOT_YET = "not_yet"            # phase 2/3 placeholder — always `na`


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
           formula="capital_cost × p_nom_opt", compute=C.gen_capex_annual,
           requires=(REQ_DISPATCH,)),
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
           category="loadflow", classes=("Bus", "Line", "Transformer"), origin="output",
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
           formula="capital_cost × p_nom_opt", compute=C.gen_fixed_cost,
           requires=(REQ_DISPATCH,)),
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

# Phase 2/3 placeholders: every non-Generator class gets one always-`na`
# metric per non-summary category, so the tab strip renders the full eight
# and says WHY rather than silently showing seven.
# Also add storage and loadflow placeholders for Generator (structurally n/a).
_PLACEHOLDERS: tuple[Metric, ...] = tuple(
    Metric(id=f"__pending__{cls}__{cat}", label=CATEGORY_LABELS[cat], unit="",
           kind="scalar", category=cat, classes=(cls,), origin="derived",
           formula="—", compute=C.not_yet, requires=(REQ_NOT_YET,))
    for cls in ALL_CLASSES if cls != "Generator"
    for cat in CATEGORY_IDS if cat != "summary"
) + (
    Metric(id="__pending__Generator__storage", label=CATEGORY_LABELS["storage"],
           unit="", kind="scalar", category="storage", classes=("Generator",),
           origin="derived", formula="—", compute=C.not_yet, requires=(REQ_NOT_YET,)),
    Metric(id="__pending__Generator__loadflow", label=CATEGORY_LABELS["loadflow"],
           unit="", kind="scalar", category="loadflow", classes=("Generator",),
           origin="derived", formula="—", compute=C.not_yet, requires=(REQ_NOT_YET,)),
)

METRICS: tuple[Metric, ...] = (
    _SUMMARY_METRICS + _GENERATOR_METRICS + _PLACEHOLDERS
)

_BY_ID: dict[str, Metric] = {m.id: m for m in METRICS}


def metric_by_id(metric_id: str) -> Metric | None:
    return _BY_ID.get(metric_id)


def metrics_for(component_class: str, category: str) -> tuple[Metric, ...]:
    return tuple(
        m for m in METRICS
        if m.category == category and component_class in m.classes
    )
