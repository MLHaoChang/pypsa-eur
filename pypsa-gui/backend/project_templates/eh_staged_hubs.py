"""
The staged Industrial Hydrogen Hub and Island Microgrid (generic-site E2E,
slices H1/M1: template data only, no engine change).

Each is its single-year template planned from a projected load over three
investment periods (2027, 2030, 2035; the horizon ends in 2045), the way the
staged data center and the generic site are: the representative week
repeated per period, one candidate row per period with ``build_year`` =
the period, the existing hub sunk (overnight cost 0, in service at the first
period) and the finance inputs shipped beside it. Staging itself is
``eh_site.stage_network``, loaded at build time.

What each one still lacks is said in its ledger and study notes:

* **The H2 hub** ships its commercial config, but its investment case is
  refused today (``counterfactual_not_established:owner_conversion_load``):
  without the investment the H2 offtake has no stated source. The grey-H2
  counterfactual at a reference price with a CO2 price by period (owner,
  Q3) is the engine slice H2e. It ships no island sidecar: islanding a
  200 MW import is not the H2 hub's question (the control stage follows the
  data center's wiring when it lands).
* **The microgrid** has no grid comparison (its tie is normally open). Its
  investment case compares against the island as it stands today (owner,
  M2e (a)): the existing diesels, PV, wind and battery, without the planned
  candidates, and no grid; #302's baseline re-solve gives it with no engine
  change. Its commercial config names the open tie as the PoC and has no
  tariff. Its island sidecar runs ``seamless``:
  the island is already islanded, so the rows hold the steady island (GFM
  MVA, genset minimum load).

``register(T)`` adds both to ``eh_templates``' registries; it is called from
there, so this module never imports ``eh_templates`` itself. ``T`` is a
snapshot of that module's names taken at import: a test that later
monkeypatches ``eh_templates`` does not reach the staged builders.
"""
from __future__ import annotations

import copy
import importlib.util
import pathlib
import sys

import numpy as np

YEARS = (2027, 2030, 2035)
HORIZON_END = 2045
DISCOUNT_RATE = 0.07

#: The H2 offtake grows as the customer's demand ramps (MW of H2, LHV); the
#: process load stays at the single-year template's 40 MW.
H2_OFFTAKE_MW = {2027: 10.0, 2030: 18.0, 2035: 25.0}
#: The island's load grows; the hospital feeder grows less.
MG_LOAD_SCALE = {2027: {"residential": 1.0, "hospital_load": 1.0},
                 2030: {"residential": 1.2, "hospital_load": 1.1},
                 2035: {"residential": 1.4, "hospital_load": 1.2}}

H2_EXISTING = ("wind_farm", "solar_park", "site_battery", "electrolyser", "h2_storage",
               "fuel_cell")
MG_EXISTING = ("pv_plant", "wind_turbines", "diesel_1", "diesel_2", "diesel_3",
               "island_battery", "hospital_feeder")

SYNTHETIC_TARIFF: dict = {
    "id": "h2_hub_network_tariff_synthetic",
    "name": "Synthetic network tariff (illustrative)",
    "jurisdiction": "DE",
    "valid_from": "2027-01-01",
    "items": [{"id": "network_energy", "kind": "energy", "unit": "per_kwh",
               "periods": [{"name": "day", "rate": 0.025, "start_hour": 8, "end_hour": 20},
                           {"name": "night", "rate": 0.015}]}],
}


def _site():
    """``eh_site`` (for ``stage_network``), loaded by file path at build time."""
    key = "_eh_site_for_staged_hubs"
    mod = sys.modules.get(key)
    if mod is None:
        path = pathlib.Path(__file__).resolve().with_name("eh_site.py")
        spec = importlib.util.spec_from_file_location(key, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return mod


def _period_years() -> dict[int, int]:
    return _site().period_years(list(YEARS), HORIZON_END)


def _sink_stores(n, existing) -> None:
    """``stage_network`` types Generators, StorageUnits and Links; a Store
    (the H2 tank) is sunk the same way."""
    rows = [a for a in existing if a in n.stores.index]
    n.stores.loc[rows, ["overnight_cost", "discount_rate"]] = [0.0, DISCOUNT_RATE]


def _in_service(n, existing) -> None:
    """The existing hub is in service in every period: an infinite lifetime
    (a pack-priced row carries the pack's, from build year 0, and would be
    inactive from 2027 on; gatekeeper review of #304)."""
    for frame in ("generators", "storage_units", "links", "stores"):
        df = getattr(n, frame)
        rows = [a for a in existing if a in df.index]
        df.loc[rows, "lifetime"] = float("inf")


# ── Industrial Hydrogen Hub, staged ─────────────────────────────────────────


def build_eh_h2_hub_staged(T) -> "pypsa.Network":  # noqa: F821
    n = T.build_eh_h2_hub()
    # The single-year candidates give way to one row per period; the fuel
    # cell is the existing 10 MW unit (the pack's fuel cell lives 10 years,
    # so a staged one would retire inside the horizon).
    for comp, name in (("Generator", "wind_new"), ("Generator", "solar_new"),
                       ("StorageUnit", "bess_new")):
        n.remove(comp, name)
        for frame in ("generators_t",):
            df = getattr(n, frame).p_max_pu
            if name in df.columns:
                df.drop(columns=name, inplace=True)
    n.links.loc["fuel_cell", ["p_nom_extendable", "p_nom"]] = [False, 10.0]
    # The offtake as a series, so each period scales it.
    n.loads_t.p_set["h2_offtake"] = np.full(len(n.snapshots), H2_OFFTAKE_MW[YEARS[-1]])
    n.loads.at["h2_offtake", "p_set"] = 0.0
    # Loss-free PoC: the investment case's counterfactual is the served load
    # at the meter (the ledger says so).
    n.links.at["grid_import", "efficiency"] = 1.0
    wind, sun = n.generators_t.p_max_pu["wind_farm"].to_numpy(), \
        n.generators_t.p_max_pu["solar_park"].to_numpy()

    def candidates(net, y):
        for g, carrier, tech, pmax, prof in (
                (f"wind_new_{y}", "wind", "onwind", 150.0, wind),
                (f"solar_new_{y}", "solar", "solar-utility", 80.0, sun)):
            net.add("Generator", g, bus="hub", carrier=carrier, p_nom=0.0,
                    p_nom_extendable=True, p_nom_max=pmax, build_year=y)
            T._price(net, "generators", g, tech)
            net.generators_t.p_max_pu[g] = np.tile(prof, len(YEARS))
        b = f"bess_new_{y}"
        net.add("StorageUnit", b, bus="hub", carrier="battery", p_nom=0.0,
                p_nom_extendable=True, p_nom_max=80.0, max_hours=4.0, efficiency_store=0.95,
                efficiency_dispatch=0.95, cyclic_state_of_charge=True, build_year=y)
        T._price(net, "storage_units", b, "battery")
        e = f"electrolyser_new_{y}"
        net.add("Link", e, bus0="hub", bus1="h2", p_nom=0.0, p_nom_extendable=True,
                p_nom_max=40.0, efficiency=0.68, carrier="electrolysis", build_year=y)
        T._price(net, "links", e, "electrolysis")
        net.links.at[e, "eh_role"] = "electrolyser"
        T._outage(net, "links", e, 0.04, 48.0)

    # The existing electrolyser is 30 MW: 20 MW of H2, the 2027 offtake with room.
    n.links.at["electrolyser", "p_nom"] = 30.0
    last = H2_OFFTAKE_MW[YEARS[-1]]
    _site().stage_network(
        n, list(YEARS),
        load_scale={y: {"h2_offtake": H2_OFFTAKE_MW[y] / last} for y in YEARS},
        energization={}, weights=_period_years(), existing=H2_EXISTING, candidates=candidates)
    _sink_stores(n, H2_EXISTING)
    _in_service(n, H2_EXISTING)
    n.name = "Industrial Hydrogen Hub (staged growth)"
    return n


def _h2_finance() -> dict:
    cand = [f"{k}_new_{y}" for y in YEARS for k in ("wind", "solar", "bess")]
    return {
        "currency": "EUR", "currency_year": 2020, "price_basis": "nominal",
        "financial_close": f"{YEARS[0] - 1}-01-01",
        "cod_by_asset": {a: f"{YEARS[0]}-01-01" for a in H2_EXISTING},
        "capex_phasing": [1.0], "stage_capex_lead_years": 1, "contingency_share": 0.0,
        "escalation": {c: 0.02 for c in ("opex", "fuel", "tariff", "ppa", "export", "capex")},
        "degradation_by_asset": {
            "wind_farm": 0.0, "solar_park": 0.005, "site_battery": 0.0,
            **{a: (0.005 if a.startswith("solar") else 0.0) for a in cand}},
        "analysis_years": HORIZON_END - YEARS[0],
        "annualise": False,
        "tax_losses": "offset_other_income", "financing_fee_tax": "not_deducted",
        "replacement_rule": "part_lifetimes",
        "terminal_value": {"method": "remaining_life_annuity"},
        "wacc_nominal": DISCOUNT_RATE, "cost_of_equity": DISCOUNT_RATE, "inflation": 0.02,
    }


def _h2_commercial_and_finance(T) -> dict:
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs
    from services.commercial import value_flow_templates as VFT

    base = CommercialConfig.model_validate(
        {"poc_link": "grid_import", "import_tariff": SYNTHETIC_TARIFF, "timezone": "UTC"})
    vf = VFT.build("single_owner", build_eh_h2_hub_staged(T), base).config
    commercial = base.model_copy(update={"value_flows": vf.model_dump(mode="json")})
    return {"commercial": commercial.model_dump(mode="json"),
            "finance": FinanceInputs.model_validate(_h2_finance()).model_dump(mode="json")}


# ── Island Microgrid, staged ────────────────────────────────────────────────


def build_eh_microgrid_staged(T) -> "pypsa.Network":  # noqa: F821
    n = T.build_eh_microgrid()
    for comp, name in (("Generator", "diesel_new"), ("StorageUnit", "bess_new")):
        n.remove(comp, name)
    # The feeder to the hospital is loss-free in the staged plan, like every
    # staged template's PoC chain.
    n.links.at["hospital_feeder", "efficiency"] = 1.0
    sun = n.generators_t.p_max_pu["pv_plant"].to_numpy()
    wind = n.generators_t.p_max_pu["wind_turbines"].to_numpy()

    def candidates(net, y):
        g = f"diesel_new_{y}"
        net.add("Generator", g, bus="island", carrier="diesel", p_nom=0.0, p_nom_extendable=True,
                p_nom_max=8.0, marginal_cost=260.0, build_year=y)
        T._price(net, "generators", g, "diesel-genset")
        T._outage(net, "generators", g, 0.05, 48.0)
        b = f"bess_new_{y}"
        net.add("StorageUnit", b, bus="island", carrier="battery", p_nom=0.0,
                p_nom_extendable=True, p_nom_max=10.0, max_hours=4.0, efficiency_store=0.94,
                efficiency_dispatch=0.94, cyclic_state_of_charge=True, build_year=y)
        T._price(net, "storage_units", b, "battery")
        for v, carrier, tech, pmax, prof in ((f"pv_new_{y}", "solar", "solar-utility", 10.0, sun),
                                             (f"wind_new_{y}", "wind", "onwind", 6.0, wind)):
            net.add("Generator", v, bus="island", carrier=carrier, p_nom=0.0,
                    p_nom_extendable=True, p_nom_max=pmax, build_year=y)
            T._price(net, "generators", v, tech)
            net.generators_t.p_max_pu[v] = np.tile(prof, len(YEARS))

    _site().stage_network(
        n, list(YEARS), load_scale=MG_LOAD_SCALE, energization={}, weights=_period_years(),
        existing=MG_EXISTING, candidates=candidates)
    _in_service(n, MG_EXISTING)
    n.name = "Island Microgrid (staged growth)"
    return n


def _mg_finance() -> dict:
    cand = [f"{k}_new_{y}" for y in YEARS for k in ("diesel", "bess", "pv", "wind")]
    owned_gen = [a for a in MG_EXISTING if a != "hospital_feeder"]
    return {
        "currency": "EUR", "currency_year": 2020, "price_basis": "nominal",
        "financial_close": f"{YEARS[0] - 1}-01-01",
        "cod_by_asset": {a: f"{YEARS[0]}-01-01" for a in MG_EXISTING},
        "capex_phasing": [1.0], "stage_capex_lead_years": 1, "contingency_share": 0.0,
        "escalation": {c: 0.02 for c in ("opex", "fuel", "tariff", "ppa", "export", "capex")},
        "degradation_by_asset": {
            **{a: (0.005 if a == "pv_plant" else 0.0) for a in owned_gen},
            **{a: (0.005 if a.startswith("pv") else 0.0) for a in cand}},
        "analysis_years": HORIZON_END - YEARS[0],
        "annualise": False,
        "tax_losses": "offset_other_income", "financing_fee_tax": "not_deducted",
        "replacement_rule": "part_lifetimes",
        "terminal_value": {"method": "remaining_life_annuity"},
        "wacc_nominal": DISCOUNT_RATE, "cost_of_equity": DISCOUNT_RATE, "inflation": 0.02,
    }


def _mg_commercial_and_finance(T) -> dict:
    """The island's commercial config: the open subsea tie is the PoC (the
    site/grid split seeds from it; it carries nothing), no tariff, and the
    single-owner value flows. The case compares against the island as it
    stands today, which the investment-case route re-solves (#302)."""
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs
    from services.commercial import value_flow_templates as VFT

    base = CommercialConfig.model_validate({"poc_link": "subsea_tie", "timezone": "UTC"})
    vf = VFT.build("single_owner", build_eh_microgrid_staged(T), base).config
    commercial = base.model_copy(update={"value_flows": vf.model_dump(mode="json")})
    return {"commercial": commercial.model_dump(mode="json"),
            "finance": FinanceInputs.model_validate(_mg_finance()).model_dump(mode="json")}


def _mg_island(T) -> dict:
    """The island's own sidecar: diesel droop and a grid-forming battery
    holding the steady island (``seamless``; the tie is open, so there is no
    import to lose at 0⁺)."""
    a = T._a
    bess = {"kind": "bess", "control": "gfm_droop", "pf_rated": a(0.95), "droop_pct": a(4.0),
            "q_droop_pct": a(4.0), "tau_f_s": a(0.1), "i_max_pu": a(1.2), "k_gfm_min": a(1.0)}
    pv = {"kind": "pv", "control": "gfl_qu", "pf_rated": a(0.95),
          "qu_points": a([[0.9, 0.33], [0.97, 0.0], [1.03, 0.0], [1.1, -0.33]])}
    wind = {"kind": "wind", "control": "gfl_qu", "pf_rated": a(0.95),
            "qu_points": a([[0.9, 0.33], [0.97, 0.0], [1.03, 0.0], [1.1, -0.33]])}
    units: dict[str, dict] = {f"diesel_{i}": T._genset_island() for i in range(1, 4)}
    units["island_battery"] = copy.deepcopy(bess)
    units["pv_plant"] = copy.deepcopy(pv)
    units["wind_turbines"] = copy.deepcopy(wind)
    for y in YEARS:
        units[f"diesel_new_{y}"] = T._genset_island(unit_mw=a(3.0))
        units[f"bess_new_{y}"] = copy.deepcopy(bess)
        units[f"pv_new_{y}"] = copy.deepcopy(pv)
        units[f"wind_new_{y}"] = copy.deepcopy(wind)
    units["hospital_load"] = {"kind": "load", "critical": True}
    units["residential"] = {"kind": "load", "critical": False}
    return {"units": units, "requirements": {
        "bridge_s": a(600.0), "sustained_h": a(24.0), "fuel_autonomy_h": a(24.0),
        "genset_redundancy_n": a(1), "scenarios": ["seamless"],
        "frequency_limits": {"transition": dict(T._LIMITS), "steady_island": dict(T._LIMITS)},
        "rocof_window_ms": a(500.0), "gfm_margin": a(0.1),
        "ride_through_storage": ["island_battery", *[f"bess_new_{y}" for y in YEARS]],
    }}


# ── The registries ──────────────────────────────────────────────────────────


def _stages_meta(peaks: dict[int, float], note: str) -> list[dict]:
    return [{"year": y, "peak_mw": round(peaks[y], 2), "note": note} for y in YEARS]


_COMMON_LEDGER = [
    {"item": "discount rate", "value": "7 % nominal", "source": "assumed",
     "note": "the solve discounts each period's costs at it"},
    {"item": "finance inputs", "value": "nominal EUR (2020 money), escalation 2 %/yr, all equity "
                                        "at 7 %, close 2026-01-01, to 2045", "source": "assumed",
     "note": "pre-tax; a later stage's capex is paid a year before its COD"},
]


def register(T) -> None:
    """Add both staged hubs to ``eh_templates``' registries (``T`` is that
    module, passed in so this one never imports it)."""
    T.BUILDERS["eh_h2_hub_staged"] = lambda: build_eh_h2_hub_staged(T)
    T.BUILDERS["eh_microgrid_staged"] = lambda: build_eh_microgrid_staged(T)
    T.STRESS_SCENARIOS["eh_h2_hub_staged"] = copy.deepcopy(T.STRESS_SCENARIOS["eh_h2_hub"])
    T.STRESS_SCENARIOS["eh_microgrid_staged"] = copy.deepcopy(T.STRESS_SCENARIOS["eh_microgrid"])
    staged = {"multi_investment_periods": True, "auto_discount_periods": True,
              "discount_rate": DISCOUNT_RATE}
    T.SOLVER_CONFIG_EXTRA["eh_h2_hub_staged"] = dict(staged)
    T.SOLVER_CONFIG_EXTRA["eh_microgrid_staged"] = {**staged, "island_constraints": True}
    T._SOLVER_CONFIG_BUILT["eh_h2_hub_staged"] = lambda: _h2_commercial_and_finance(T)
    T._SOLVER_CONFIG_BUILT["eh_microgrid_staged"] = lambda: _mg_commercial_and_finance(T)
    T.ISLAND_CONFIG["eh_microgrid_staged"] = _mg_island(T)

    py = _period_years_static()
    T.TEMPLATE_META["eh_h2_hub_staged"] = {
        "id": "eh_h2_hub_staged",
        "name": "Industrial Hydrogen Hub (staged growth)",
        "description": (
            "The hydrogen hub planned from a projected H2 demand: the offtake grows 10 → 18 → "
            "25 MW over 2027, 2030 and 2035 beside a 40 MW process load on a 200 MW connection. "
            "Wind, solar, battery and electrolyser candidates per period. Multi-period; tagged "
            "for an Energy Hub study."),
        "recommended_archetype": "strong_grid",
        "pack_overrides": {},
        "stages": None,
        "dtc_attribution": None,
        "projected_load": _stages_meta({y: 40.0 + H2_OFFTAKE_MW[y] / 0.68 for y in YEARS},
                                       "process load plus the electrolysis the offtake needs; "
                                       "synthetic"),
        "energization": [{"year": y, "available_import_mw": 200.0, "connection_mw": 200.0,
                          "source": "assumed", "note": "fully energized"} for y in YEARS],
        "period_years": {str(y): py[y] for y in YEARS},
        "ledger": [
            *_COMMON_LEDGER,
            {"item": "H2 offtake", "value": "10 → 18 → 25 MW (LHV)", "source": "synthetic",
             "note": "the customer's demand ramp; replace with the offtake contract"},
            {"item": "PoC losses", "value": "efficiency 1.0 on grid_import", "source": "assumed",
             "note": "not modelled, so the counterfactual bill is the served load at the meter"},
            {"item": "import tariff", "value": f"{SYNTHETIC_TARIFF['id']}: 25 EUR/MWh 08-20 h, "
                                               "15 EUR/MWh otherwise", "source": "synthetic",
             "note": "an illustrative network-use charge on top of the wholesale price"},
            {"item": "existing electrolyser", "value": "30 MW (the single-year template has 60)",
             "source": "assumed",
             "note": "the 2027 offtake (10 MW of H2) needs about 15 MW; the plan adds the rest by "
                     "period as the offtake grows"},
            {"item": "storage across periods", "value": "cyclic within each period",
             "source": "assumed",
             "note": "each period is one representative week standing for several years, so no "
                     "energy is carried from one period into the next"},
            {"item": "fuel cell", "value": "the existing 10 MW unit, no staged candidate",
             "source": "assumed",
             "note": "the pack's fuel cell lives 10 years: a staged one would retire before 2045"},
            {"item": "investment case", "value": "refused today (owner_conversion_load)",
             "source": "assumed",
             "note": "without the investment the H2 offtake has no source; the grey-H2 "
                     "counterfactual with a CO2 price by period comes in a later slice"},
        ],
        "study_notes": [
            "Each period is the same representative week, with the period's H2 offtake.",
            "Candidates are one row per period (build_year = the period): the plan says what "
            "to build when.",
            "The investment case is not available yet: without the investment the H2 offtake "
            "has no stated source. A grey-H2 comparison at a reference price comes next.",
            "The connection (200 MW) carries the grid-only site in every period.",
        ],
        "provenance": T.PROVENANCE,
    }
    T.TEMPLATE_META["eh_microgrid_staged"] = {
        "id": "eh_microgrid_staged",
        "name": "Island Microgrid (staged growth)",
        "description": (
            "The island microgrid planned from a growing load: residential demand grows 40 % and "
            "the hospital 20 % by 2035. Diesel, battery, PV and wind candidates per period; the "
            "island runs without its subsea tie. Multi-period; tagged for an off-grid study."),
        "recommended_archetype": "off_grid",
        "pack_overrides": {},
        "stages": None,
        "dtc_attribution": None,
        "projected_load": _stages_meta(
            {y: 7.5 * MG_LOAD_SCALE[y]["residential"] + 1.7 * MG_LOAD_SCALE[y]["hospital_load"]
             for y in YEARS}, "sum of peaks (upper bound): the residential and hospital peaks do not coincide; synthetic"),
        "energization": [],
        "period_years": {str(y): py[y] for y in YEARS},
        "ledger": [
            *_COMMON_LEDGER,
            {"item": "load growth", "value": "residential ×1.2 / ×1.4, hospital ×1.1 / ×1.2 by "
                                             "2030 / 2035", "source": "synthetic",
             "note": "replace with the island's load forecast"},
            {"item": "hospital feeder losses", "value": "efficiency 1.0", "source": "assumed",
             "note": "not modelled in the staged plan"},
            {"item": "investment case",
             "value": "compared with the island as it stands today: the existing diesels, PV, "
                      "wind and battery, without the planned candidates, and no grid",
             "source": "assumed",
             "note": "owner decision (M2e a); the investment-case route re-solves the island "
                     "without the candidates. The analytic grid comparison does not apply"},
            {"item": "point of connection", "value": "subsea_tie, normally open (p_max_pu 0), "
                                                     "no tariff",
             "source": "assumed",
             "note": "it seeds the site/grid split and carries nothing; its 0.97 efficiency is "
                     "kept (no flow, so the solve is unchanged)"},
            {"item": "diesel running cost", "value": "260 EUR/MWh (fuel and variable O&M)",
             "source": "synthetic",
             "note": "the single-year template's marginal cost, in base-year (2027) money, "
                     "escalating 2 %/yr as fuel; replace with the island's fuel contract"},
            {"item": "existing island", "value": ", ".join(a for a in MG_EXISTING)
                                                 + f": COD {YEARS[0]}-01-01, overnight cost 0",
             "source": "assumed", "note": "in service at the first period and sunk"},
        ],
        "study_notes": [
            "Each period is the same representative week, with the period's load.",
            "Candidates are one row per period (build_year = the period).",
            "The island rows hold the steady island: grid-forming battery and diesel droop.",
            "After a solve the investment case compares the plan with the island as it stands "
            "today (the existing diesels, PV, wind and battery, no grid), re-solved without the "
            "candidates.",
        ],
        "provenance": T.PROVENANCE,
    }


def _period_years_static() -> dict[int, int]:
    ends = [*YEARS[1:], HORIZON_END]
    return {y: e - y for y, e in zip(YEARS, ends)}
