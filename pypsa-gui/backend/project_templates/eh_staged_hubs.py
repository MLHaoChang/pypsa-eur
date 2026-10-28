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

* **The H2 hub** ships its commercial config with a grey-H2 reference
  purchase for the offtake and a CO2 price by year (synthetic demo values;
  owner, Q3 and #312): its investment case compares against the hub as it
  stands today, which buys grey H2 whenever that is cheaper than running
  its electrolyser. It ships no island sidecar: islanding a
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
import types

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


def _period_years(planning=None) -> dict[int, int]:
    years, end = (YEARS, HORIZON_END) if planning is None else (planning.years, planning.end_year)
    return _site().period_years(list(years), end)


class _HubPlan:
    """A staged hub for one ``Planning`` (E1b): the periods, the horizon and
    each growth value by period (the example's same-stage value), with
    whether it is the example's own (``set``) or a placeholder."""

    def __init__(self, T, template_id: str, planning, values: dict):
        native = T.NATIVE_PLANNING[template_id]
        self.planning = planning or native
        self.native = native
        self.is_native = self.planning == native
        staged = T._by_stage(self.planning, native, values)
        self.years = list(self.planning.years)
        self.end = self.planning.end_year
        self.values = {y: v for y, v, _set in staged}
        self.set = {y: is_set for y, _v, is_set in staged}


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


def _h2_plan(T, planning=None) -> _HubPlan:
    return _HubPlan(T, "eh_h2_hub_staged", planning, H2_OFFTAKE_MW)


def _mg_plan(T, planning=None) -> _HubPlan:
    return _HubPlan(T, "eh_microgrid_staged", planning, MG_LOAD_SCALE)


def build_eh_h2_hub_staged(T, planning=None) -> "pypsa.Network":  # noqa: F821
    plan = _h2_plan(T, planning)
    years, offtake = plan.years, plan.values
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
    n.loads_t.p_set["h2_offtake"] = np.full(len(n.snapshots), offtake[years[-1]])
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
            net.generators_t.p_max_pu[g] = np.tile(prof, len(years))
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
    last = offtake[years[-1]]
    _site().stage_network(
        n, years,
        load_scale={y: {"h2_offtake": offtake[y] / last} for y in years},
        energization={}, weights=_period_years(planning), existing=H2_EXISTING,
        candidates=candidates)
    _sink_stores(n, H2_EXISTING)
    _in_service(n, H2_EXISTING)
    n.name = "Industrial Hydrogen Hub (staged growth)"
    return n


def _h2_finance(planning=None) -> dict:
    """The finance inputs over the planning horizon (None: the example's
    years): close the year before the first period, the existing hub in
    service then, the case running to the end year."""
    years, end = (YEARS, HORIZON_END) if planning is None else (planning.years, planning.end_year)
    cand = [f"{k}_new_{y}" for y in years for k in ("wind", "solar", "bess")]
    return {
        "currency": "EUR", "currency_year": 2025, "price_basis": "nominal",
        "financial_close": f"{years[0] - 1}-01-01",
        "cod_by_asset": {a: f"{years[0]}-01-01" for a in H2_EXISTING},
        "capex_phasing": [1.0], "stage_capex_lead_years": 1, "contingency_share": 0.0,
        "escalation": {c: 0.02 for c in ("opex", "fuel", "tariff", "ppa", "export", "capex")},
        "degradation_by_asset": {
            "wind_farm": 0.0, "solar_park": 0.005, "site_battery": 0.0,
            **{a: (0.005 if a.startswith("solar") else 0.0) for a in cand}},
        "analysis_years": end - years[0],
        "annualise": False,
        "tax_losses": "offset_other_income", "financing_fee_tax": "not_deducted",
        "replacement_rule": "part_lifetimes",
        "terminal_value": {"method": "remaining_life_annuity"},
        "wacc_nominal": DISCOUNT_RATE, "cost_of_equity": DISCOUNT_RATE, "inflation": 0.02,
    }


#: The H2 hub's grey-H2 reference purchase and CO2 price (synthetic demo
#: values, ledgered): what the site buys without its electrolysers.
GREY_H2 = {"load": "h2_offtake", "price_eur_per_kg": 2.0, "lhv_kwh_per_kg": 33.33,
           "co2_t_per_kg": 0.010, "source": "synthetic demo value (grey H2 by SMR)"}
CO2_PRICE = {"eur_per_t_by_year": {2027: 80.0, 2030: 120.0}, "money_year": 2020,
             "source": "synthetic demo path"}


def _co2_price(planning=None) -> dict:
    """The CO2 path for ``planning``: a start before the path's first year
    holds the first point from the start year (ledgered "assumed, held from
    <year>"; gatekeeper E1 condition 5); otherwise the path as shipped. The
    case's CO2 lines and the baseline's grey-H2 cost both read this one path
    from the commercial config."""
    path = CO2_PRICE["eur_per_t_by_year"]
    first = min(path)
    if planning is None or planning.start_year >= first:
        return CO2_PRICE
    return {**CO2_PRICE, "eur_per_t_by_year": {planning.start_year: path[first], **path}}


def _tariff(planning=None) -> dict:
    """The synthetic tariff, valid from the first period."""
    if planning is None:
        return SYNTHETIC_TARIFF
    return {**SYNTHETIC_TARIFF, "valid_from": f"{planning.start_year}-01-01"}


#: The staged H2 hub's island-rows ledger row, measured on the staged plan
#: with the rows on (2026-10-15, re-measured 2026-10-21 at the GS-2b electrolysis price; live solves); the sidecar and unit-data rows
#: are shared with the single-year hub (``eh_templates.H2_ISLAND_LEDGER``).
H2_ISLAND_NOTE = ("Island rows are off: the plan is not sized to island; the island checks "
                  "report what islanding would need.")
H2_ISLAND_ROWS = {
    "item": "island rows", "value": "off (island_constraints false)",
    "source": "assumed",
    "note": "the plan is not sized to island; turn them on to plan for islanding. Measured on "
            "this plan (electrolysis priced per MW of electrical input, GS-2b, 2026-10-21): at 1 h "
            "sustained they add about 43 MW of battery in 2027, hold the grid import to about "
            "3 MW and take the demo NPV from -2.6 M to -30.5 M EUR; at 4 h the NPV is -51.9 M "
            "EUR; at 24 h no plan meets them (owner: island data on, rows off, the user opts "
            "in)"}


def _h2_blocks(n, finance: dict, planning=None) -> dict:
    """An H2 hub's `commercial` and `finance` blocks on its network ``n``:
    the grey-H2 reference purchase and the CO2 path, the synthetic tariff and
    the single-owner value flows, stored as the routes store them."""
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs
    from services.commercial import value_flow_templates as VFT

    base = CommercialConfig.model_validate(
        {"poc_link": "grid_import", "import_tariff": _tariff(planning), "timezone": "UTC",
         "reference_purchases": [GREY_H2], "co2_price": _co2_price(planning)})
    vf = VFT.build("single_owner", n, base).config
    commercial = base.model_copy(update={"value_flows": vf.model_dump(mode="json")})
    return {"commercial": commercial.model_dump(mode="json"),
            "finance": FinanceInputs.model_validate(finance).model_dump(mode="json")}


def _h2_commercial_and_finance(T, planning=None) -> dict:
    return _h2_blocks(build_eh_h2_hub_staged(T, planning), _h2_finance(planning), planning)


# ── Industrial Hydrogen Hub, single-year: its investment case ───────────────
#: The single-year hub's candidates (``eh_templates.build_eh_h2_hub``); its
#: existing rows are ``T.H2_HUB_EXISTING``.
H2_HUB_CANDIDATES = ("wind_new", "solar_new", "bess_new", "fuel_cell")


def _h2_single_finance(T, planning=None) -> dict:
    """The staged hub's finance inputs for one period (study harness 19):
    every asset's COD the start year (a single period has no stage), close a
    year before it, the case to the end year (2045 natively)."""
    p = planning or T.NATIVE_PLANNING["eh_h2_hub"]
    first = f"{p.start_year}-01-01"
    return {**_h2_finance(p),
            "cod_by_asset": {a: first for a in (*T.H2_HUB_EXISTING, *H2_HUB_CANDIDATES)},
            "degradation_by_asset": {"wind_farm": 0.0, "solar_park": 0.005, "site_battery": 0.0,
                                     "wind_new": 0.0, "solar_new": 0.005, "bess_new": 0.0}}


def _h2_single_commercial_and_finance(T, planning=None) -> dict:
    return _h2_blocks(T.build_eh_h2_hub(planning=planning), _h2_single_finance(T, planning),
                      planning)


# ── Island Microgrid, staged ────────────────────────────────────────────────


def build_eh_microgrid_staged(T, planning=None) -> "pypsa.Network":  # noqa: F821
    plan = _mg_plan(T, planning)
    years = plan.years
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
            net.generators_t.p_max_pu[v] = np.tile(prof, len(years))

    _site().stage_network(
        n, years, load_scale=plan.values, energization={}, weights=_period_years(planning),
        existing=MG_EXISTING, candidates=candidates)
    _in_service(n, MG_EXISTING)
    n.name = "Island Microgrid (staged growth)"
    return n


def _mg_finance(planning=None) -> dict:
    years, end = (YEARS, HORIZON_END) if planning is None else (planning.years, planning.end_year)
    cand = [f"{k}_new_{y}" for y in years for k in ("diesel", "bess", "pv", "wind")]
    owned_gen = [a for a in MG_EXISTING if a != "hospital_feeder"]
    return {
        "currency": "EUR", "currency_year": 2025, "price_basis": "nominal",
        "financial_close": f"{years[0] - 1}-01-01",
        "cod_by_asset": {a: f"{years[0]}-01-01" for a in MG_EXISTING},
        "capex_phasing": [1.0], "stage_capex_lead_years": 1, "contingency_share": 0.0,
        "escalation": {c: 0.02 for c in ("opex", "fuel", "tariff", "ppa", "export", "capex")},
        "degradation_by_asset": {
            **{a: (0.005 if a == "pv_plant" else 0.0) for a in owned_gen},
            **{a: (0.005 if a.startswith("pv") else 0.0) for a in cand}},
        "analysis_years": end - years[0],
        "annualise": False,
        "tax_losses": "offset_other_income", "financing_fee_tax": "not_deducted",
        "replacement_rule": "part_lifetimes",
        "terminal_value": {"method": "remaining_life_annuity"},
        "wacc_nominal": DISCOUNT_RATE, "cost_of_equity": DISCOUNT_RATE, "inflation": 0.02,
    }


def _mg_commercial_and_finance(T, planning=None) -> dict:
    """The island's commercial config: the open subsea tie is the PoC (the
    site/grid split seeds from it; it carries nothing), no tariff, and the
    single-owner value flows. The case compares against the island as it
    stands today, which the investment-case route re-solves (#302)."""
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs
    from services.commercial import value_flow_templates as VFT

    base = CommercialConfig.model_validate({"poc_link": "subsea_tie", "timezone": "UTC"})
    vf = VFT.build("single_owner", build_eh_microgrid_staged(T, planning), base).config
    commercial = base.model_copy(update={"value_flows": vf.model_dump(mode="json")})
    return {"commercial": commercial.model_dump(mode="json"),
            "finance": FinanceInputs.model_validate(_mg_finance(planning)).model_dump(mode="json")}


#: The island's transition RoCoF limit (Hz/s over the 500 ms window): a demo
#: value, relaxed from the data center's 1 Hz/s so a tuned grid-forming
#: battery can meet it (owner, GFM demo tuning). The steady island keeps 1 Hz/s.
MG_TRANSITION_ROCOF = 2.0


def _mg_island_blocks(T) -> tuple[dict, dict, dict]:
    """The island's three converter blocks: the grid-forming battery, PV and wind."""
    a = T._a
    bess = {"kind": "bess", "control": "gfm_droop", "pf_rated": a(0.95), "droop_pct": a(4.0),
            "q_droop_pct": a(4.0), "tau_f_s": a(0.1), "i_max_pu": a(1.2), "k_gfm_min": a(1.0)}
    pv = {"kind": "pv", "control": "gfl_qu", "pf_rated": a(0.95),
          "qu_points": a([[0.9, 0.33], [0.97, 0.0], [1.03, 0.0], [1.1, -0.33]])}
    wind = {"kind": "wind", "control": "gfl_qu", "pf_rated": a(0.95),
            "qu_points": a([[0.9, 0.33], [0.97, 0.0], [1.03, 0.0], [1.1, -0.33]])}
    return bess, pv, wind


def _mg_requirements(T, ride_through_storage: list[str]) -> dict:
    a = T._a
    return {
        "bridge_s": a(600.0), "sustained_h": a(24.0), "fuel_autonomy_h": a(24.0),
        "genset_redundancy_n": a(1), "scenarios": ["seamless"],
        "frequency_limits": {"transition": {**T._LIMITS, "rocof_hz_per_s": a(MG_TRANSITION_ROCOF)},
                             "steady_island": dict(T._LIMITS)},
        "rocof_window_ms": a(500.0), "gfm_margin": a(0.1),
        "ride_through_storage": ride_through_storage,
    }


def _mg_island(T, years=YEARS) -> dict:
    """The island's own sidecar: diesel droop and a grid-forming battery
    holding the steady island (``seamless``; the tie is open, so there is no
    import to lose at 0⁺), with one candidate of each kind per period."""
    bess, pv, wind = _mg_island_blocks(T)
    units: dict[str, dict] = {f"diesel_{i}": T._genset_island() for i in range(1, 4)}
    units["island_battery"] = copy.deepcopy(bess)
    units["pv_plant"] = copy.deepcopy(pv)
    units["wind_turbines"] = copy.deepcopy(wind)
    for y in years:
        units[f"diesel_new_{y}"] = T._genset_island(unit_mw=T._a(3.0))
        units[f"bess_new_{y}"] = copy.deepcopy(bess)
        units[f"pv_new_{y}"] = copy.deepcopy(pv)
        units[f"wind_new_{y}"] = copy.deepcopy(wind)
    units["hospital_load"] = {"kind": "load", "critical": True}
    units["residential"] = {"kind": "load", "critical": False}
    return {"units": units,
            "requirements": _mg_requirements(T, ["island_battery", *[f"bess_new_{y}" for y in years]])}


def _mg_island_single_year(T) -> dict:
    """The single-year island microgrid's sidecar (UX review N2): the staged island's
    blocks on its own unit names, so the grid checks have island requirements to read."""
    bess, pv, wind = _mg_island_blocks(T)
    units: dict[str, dict] = {f"diesel_{i}": T._genset_island() for i in range(1, 4)}
    units["diesel_new"] = T._genset_island(unit_mw=T._a(3.0))
    units["island_battery"] = copy.deepcopy(bess)
    units["bess_new"] = copy.deepcopy(bess)
    units["pv_plant"] = copy.deepcopy(pv)
    units["wind_turbines"] = copy.deepcopy(wind)
    units["hospital_load"] = {"kind": "load", "critical": True}
    units["residential"] = {"kind": "load", "critical": False}
    return {"units": units, "requirements": _mg_requirements(T, ["island_battery", "bess_new"])}


# ── The registries ──────────────────────────────────────────────────────────


def _stages_meta(peaks: dict[int, float], note: str, plan: _HubPlan | None = None) -> list[dict]:
    """The projected-load stages (``plan`` None: the example's years, as the ammonia hub reads)."""
    if plan is None:
        return [{"year": y, "peak_mw": round(peaks[y], 2), "note": note} for y in YEARS]
    return [{"year": y, "peak_mw": round(peaks[y], 2),
             "note": note if plan.set[y] else
             f"placeholder: the example's same-stage value; not set for {y}"}
            for y in plan.years]


def _common_ledger(plan: _HubPlan | None = None) -> list[dict]:
    """The ledger rows both staged hubs share (``plan`` None: the example's years)."""
    native = plan is None or plan.is_native
    y0, end = (YEARS[0], HORIZON_END) if plan is None else (plan.years[0], plan.end)
    return [
        {"item": "discount rate", "value": "7 % nominal", "source": "assumed",
         "note": "the solve discounts each period's costs at it"},
        {"item": "money year", "value": "pack prices (2025 EUR) are read as real base-year EUR",
             "source": "assumed",
             "note": "held flat to the base year, then escalated from it at the case's rates; "
                     "nothing is converted with a price index"
                     + ("" if native else
                        "; entering other planning years does not re-date the pack's money "
                        "year (per-period cost sourcing is a separate follow-up)")},
        {"item": "finance inputs", "value": "nominal EUR, escalation 2 %/yr, all equity at 7 %, "
                                            f"close {y0 - 1}-01-01, to {end}",
         "source": "assumed",
         "note": "pre-tax; a later stage's capex is paid a year before its COD"},
    ]


#: The example's shared rows (the ammonia hub reads them).
_COMMON_LEDGER = _common_ledger()


def _planned_meta(T, meta: dict, plan: _HubPlan, kind: str, values: dict,
                  extra_ledger: list[dict]) -> dict:
    """``meta`` built for entered years (E1): ``growth_inputs`` (each growth
    value set or a placeholder), the planning block, and the planning-period
    and placeholder ledger rows. The example's own years: ``meta`` as is."""
    if plan.is_native:
        return meta
    native = plan.native
    growth = T._growth_rows(kind, [(y, values[y], plan.set[y]) for y in plan.years])
    meta["growth_inputs"] = growth
    meta["planning"] = {"start_year": plan.years[0], "end_year": plan.end,
                        "periods": plan.years, "native_periods": list(native.years),
                        "native_end_year": native.end_year}
    meta["ledger"] = [
        {"item": "planning period",
         "value": f"{' / '.join(map(str, plan.years))} → {plan.end} (entered)",
         "source": "entered",
         "note": (f"the example is built for {T._years_text(native.years)} → "
                  f"{native.end_year}; each period's years run to the next, the last to the end "
                  "year, and the solve discounts them (auto_discount_periods)")},
        *meta["ledger"], *extra_ledger, *T._placeholder_ledger(growth, native)]
    return meta


def _measured(text: str, plan: _HubPlan) -> str:
    """A note measured on the example's plan, said so when built for other years."""
    if plan.is_native:
        return text
    return text.replace("on this plan", "on the example's plan (" + " / ".join(
        map(str, plan.native.years)) + ")")


def register(T) -> None:
    """Add both staged hubs to ``eh_templates``' registries (``T`` is that
    module, passed in so this one never imports it)."""
    T.BUILDERS["eh_h2_hub_staged"] = \
        lambda load_spec=None, planning=None: build_eh_h2_hub_staged(T, planning)
    T.BUILDERS["eh_microgrid_staged"] = \
        lambda load_spec=None, planning=None: build_eh_microgrid_staged(T, planning)
    T.STRESS_SCENARIOS["eh_h2_hub_staged"] = copy.deepcopy(T.STRESS_SCENARIOS["eh_h2_hub"])
    T.STRESS_SCENARIOS["eh_microgrid_staged"] = copy.deepcopy(T.STRESS_SCENARIOS["eh_microgrid"])
    staged = {"multi_investment_periods": True, "auto_discount_periods": True,
              "discount_rate": DISCOUNT_RATE}
    T.SOLVER_CONFIG_EXTRA["eh_h2_hub_staged"] = dict(staged)
    T.SOLVER_CONFIG_EXTRA["eh_microgrid_staged"] = {**staged, "island_constraints": True}
    T._SOLVER_CONFIG_BUILT["eh_h2_hub_staged"] = \
        lambda planning=None: _h2_commercial_and_finance(T, planning)
    T._SOLVER_CONFIG_BUILT["eh_microgrid_staged"] = \
        lambda planning=None: _mg_commercial_and_finance(T, planning)
    # The single-year hub's investment case (study harness 19): the same
    # grey-H2 basis on its own network, for entered years too (E1d).
    T._SOLVER_CONFIG_BUILT["eh_h2_hub"] = \
        lambda planning=None: _h2_single_commercial_and_finance(T, planning)
    T.TEMPLATE_META["eh_h2_hub"] = _h2_single_meta(T, T.TEMPLATE_META["eh_h2_hub"])
    T._PLANNED_META["eh_h2_hub"] = \
        lambda p, _spec: _h2_single_meta(T, T._single_year_meta("eh_h2_hub", p), p)
    T.ISLAND_CONFIG["eh_microgrid_staged"] = _mg_island(T)
    # Island data on, rows off (the template's solve is unchanged): the grid checks read it.
    T.ISLAND_CONFIG["eh_microgrid"] = _mg_island_single_year(T)
    T.ISLAND_CONFIG["eh_h2_hub_staged"] = _h2_island(T)
    # Built for entered planning years too (E1b).
    for tid in ("eh_h2_hub_staged", "eh_microgrid_staged"):
        T.NATIVE_PLANNING[tid] = T.Planning(YEARS, HORIZON_END)
    T.TEMPLATE_META["eh_h2_hub_staged"] = _h2_meta(T)
    T.TEMPLATE_META["eh_microgrid_staged"] = _mg_meta(T)
    T._PLANNED_META["eh_h2_hub_staged"] = lambda p, _spec: _h2_meta(T, p)
    T._PLANNED_META["eh_microgrid_staged"] = lambda p, _spec: _mg_meta(T, p)
    T._PLANNED_ISLAND["eh_h2_hub_staged"] = lambda p: _h2_island(T, p.years)
    T._PLANNED_ISLAND["eh_microgrid_staged"] = lambda p: _mg_island(T, p.years)


def _h2_island(T, years=YEARS) -> dict:
    return T._h2_hub_island([f"bess_new_{y}" for y in years], [f"wind_new_{y}" for y in years],
                            [f"solar_new_{y}" for y in years])


#: The H2 hubs' study note on the investment case.
H2_CASE_NOTE = ("After a solve the investment case compares the plan with the hub as it stands "
                "today, which buys grey H2 (synthetic demo price and CO2 path) whenever that is "
                "cheaper than running its electrolyser.")


def _h2_case_rows(y0: int) -> list[dict]:
    """The H2 hubs' investment-case ledger rows (staged and single-year): the
    basis, the grey-H2 reference purchase and the CO2 path, money of ``y0``."""
    return [
        {"item": "investment case",
         "value": "compared with the hub as it stands today, buying grey H2 whenever "
                  "that is cheaper than running its electrolyser",
         "source": "assumed",
         "note": "the investment-case route re-solves the hub without the candidates, "
                 "with a grey-H2 supply at the reference price plus that period's CO2"},
        {"item": "grey H2 reference price",
         "value": "2 EUR/kg (LHV 33.33 kWh/kg), 0.010 t CO2/kg",
         "source": "synthetic",
         "note": f"synthetic demo value, in the case's base-year ({y0}) money, escalating "
                 "2 %/yr as fuel; a real study uses the site's SMR supply contract and "
                 "its certified carbon intensity"},
        {"item": "CO2 price", "value": "80 EUR/t from 2027, 120 EUR/t from 2030",
         "source": "synthetic",
         "note": "synthetic demo path, real prices in 2020 money, converted at the "
                 "case's 2 % inflation (the case is nominal); applied to the grey-H2 "
                 "purchase only; a real study uses the ETS forward curve or a stated "
                 "policy path"},
        {"item": "grid CO2 intensity", "value": "not set",
         "source": "assumed",
         "note": "grid imports carry no CO2 cost, so the avoided CO2 is the grey-H2 "
                 "purchase's alone"},
    ]


def _h2_single_meta(T, meta: dict, planning=None) -> dict:
    """The single-year hub's ``meta`` (shipped, or built for entered years)
    with its investment case: the staged hub's basis on one period."""
    p = planning or T.NATIVE_PLANNING["eh_h2_hub"]
    first, end = p.start_year, p.end_year
    rows = [
        *_h2_case_rows(first),
        {"item": "import tariff", "value": f"{SYNTHETIC_TARIFF['id']}: 25 EUR/MWh 08-20 h, "
                                           "15 EUR/MWh otherwise", "source": "synthetic",
         "note": "the staged hub's: an illustrative network-use charge on top of the wholesale "
                 "price; read in the case's money (2025)"},
        {"item": "dates", "value": f"every COD {first}-01-01, financial close {first - 1}-01-01",
         "source": "assumed",
         "note": "a single period has no stage, so every asset shares one COD; close one year "
                 "before it and capex paid then, as the staged templates"},
        {"item": "existing hub", "value": ", ".join(T.H2_HUB_EXISTING)
                                          + ": overnight cost 0, lifetime unlimited",
         "source": "assumed",
         "note": "in service and sunk: no capex in the case; the candidates the plan builds "
                 "(the fuel cell above its 10 MW floor included) are the investments. The "
                 "solve is unchanged"},
        {"item": "finance inputs", "value": "the staged hub's: nominal EUR, escalation 2 %/yr, "
                                            f"all equity at 7 %, close {first - 1}-01-01, "
                                            f"to {end}",
         "source": "assumed",
         "note": f"pre-tax; the week weighs a year and stands for each of the {end - first} "
                 f"operating years {first}-{end - 1}, so nothing is annualised"},
        # _held_ledger reads only the first year and the entered planning.
        *_held_ledger(types.SimpleNamespace(years=[first], planning=planning)),
    ]
    return {**meta, "ledger": [*meta["ledger"], *rows],
            "study_notes": [*meta["study_notes"], H2_CASE_NOTE]}


def _h2_meta(T, planning=None) -> dict:
    """The staged H2 hub's ``eh_template.json`` for ``planning`` (None: the example's years)."""
    plan = _h2_plan(T, planning)
    years, off, y0 = plan.years, plan.values, plan.years[0]
    py = plan.planning.period_years      # eh_site is not loaded while registering
    meta = {
        "id": "eh_h2_hub_staged",
        "name": "Industrial Hydrogen Hub (staged growth)",
        "description": (
            "The hydrogen hub planned from a projected H2 demand: the offtake grows "
            f"{' → '.join(f'{off[y]:g}' for y in years)} MW over {T._years_text(years)} beside a "
            "40 MW process load on a 200 MW connection. "
            "Wind, solar, battery and electrolyser candidates per period. Multi-period; tagged "
            "for an Energy Hub study."),
        "recommended_archetype": "strong_grid",
        "pack_overrides": {},
        "stages": None,
        "dtc_attribution": None,
        "projected_load": _stages_meta({y: 40.0 + off[y] / 0.68 for y in years},
                                       "process load plus the electrolysis the offtake needs; "
                                       "synthetic", plan),
        "energization": [{"year": y, "available_import_mw": 200.0, "connection_mw": 200.0,
                          "source": "assumed", "note": "fully energized"} for y in years],
        "period_years": {str(y): py[y] for y in years},
        "ledger": [
            *_common_ledger(plan),
            {"item": "H2 offtake", "value": f"{' → '.join(f'{off[y]:g}' for y in years)} MW (LHV)",
             "source": "synthetic",
             "note": "the customer's demand ramp; replace with the offtake contract"},
            {"item": "PoC losses", "value": "efficiency 1.0 on grid_import", "source": "assumed",
             "note": "not modelled, so the counterfactual bill is the served load at the meter"},
            {"item": "import tariff", "value": f"{SYNTHETIC_TARIFF['id']}: 25 EUR/MWh 08-20 h, "
                                               "15 EUR/MWh otherwise", "source": "synthetic",
             "note": "an illustrative network-use charge on top of the wholesale price; "
                     "synthetic, illustrative; no money year of its own; read in the case's money (2025)"},
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
            *_h2_case_rows(y0),
            *T.H2_ISLAND_LEDGER, {**H2_ISLAND_ROWS, "note": _measured(H2_ISLAND_ROWS["note"], plan)},
        ],
        "study_notes": [
            "Each period is the same representative week, with the period's H2 offtake.",
            "Candidates are one row per period (build_year = the period): the plan says what "
            "to build when.",
            H2_CASE_NOTE,
            "The connection (200 MW) carries the grid-only site in every period.",
            H2_ISLAND_NOTE,
        ],
        "provenance": T.PROVENANCE,
    }
    return _planned_meta(T, meta, plan, "h2_offtake_mw", off, _held_ledger(plan))


def _held_ledger(plan: _HubPlan) -> list[dict]:
    """The assumed rows for a start before the CO2 path or the synthetic
    tariff (gatekeeper E1 condition 5); the ammonia hub reads them too."""
    y0 = plan.years[0]
    co2 = CO2_PRICE["eur_per_t_by_year"]
    held = [] if _co2_price(plan.planning) is CO2_PRICE else [
        {"item": "CO2 price before the path", "value": f"{co2[min(co2)]:g} EUR/t in {y0}",
         "source": "assumed",
         "note": f"assumed, held from {min(co2)}: the demo path starts in {min(co2)}, so the "
                 "first point is held from the start year; never extrapolated"}]
    tariff_year = int(SYNTHETIC_TARIFF["valid_from"][:4])
    if y0 < tariff_year:
        held.append(
            {"item": "import tariff before its year",
             "value": f"{SYNTHETIC_TARIFF['id']}'s rates from {y0}-01-01", "source": "assumed",
             "note": (f"assumed, held from {tariff_year}: the synthetic tariff is dated "
                      f"{SYNTHETIC_TARIFF['valid_from']}, so its rates are held from the start "
                      "year; replace with the site's tariff for those years")})
    return held


def _mg_meta(T, planning=None) -> dict:
    """The staged microgrid's ``eh_template.json`` for ``planning`` (None: the example's years)."""
    plan = _mg_plan(T, planning)
    years, scale, y0 = plan.years, plan.values, plan.years[0]
    py = plan.planning.period_years      # eh_site is not loaded while registering
    last = scale[years[-1]]
    later = years[1:]
    res = " / ".join(f"×{scale[y]['residential']:g}" for y in later)
    hosp = " / ".join(f"×{scale[y]['hospital_load']:g}" for y in later)
    growth = (f"residential {res}, hospital {hosp} by {' / '.join(map(str, later))}"
              if later else "none (one period)")
    meta = {
        "id": "eh_microgrid_staged",
        "name": "Island Microgrid (staged growth)",
        "description": (
            "The island microgrid planned from a growing load: residential demand grows "
            f"{round((last['residential'] - 1) * 100):g} % and the hospital "
            f"{round((last['hospital_load'] - 1) * 100):g} % by {years[-1]}. Diesel, battery, PV "
            "and wind candidates per period; the island runs without its subsea tie. "
            "Multi-period; tagged for an off-grid study."),
        "recommended_archetype": "off_grid",
        "pack_overrides": {},
        "stages": None,
        "dtc_attribution": None,
        "projected_load": _stages_meta(
            {y: 7.5 * scale[y]["residential"] + 1.7 * scale[y]["hospital_load"]
             for y in years}, "sum of peaks (upper bound): the residential and hospital peaks do not coincide; synthetic",
            plan),
        "energization": [],
        "period_years": {str(y): py[y] for y in years},
        "ledger": [
            *_common_ledger(plan),
            {"item": "load growth", "value": growth, "source": "synthetic",
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
             "note": f"the single-year template's marginal cost, in base-year ({y0}) money, "
                     "escalating 2 %/yr as fuel; replace with the island's fuel contract"},
            {"item": "transition RoCoF limit",
             "value": f"{MG_TRANSITION_ROCOF:g} Hz/s over 500 ms (the steady island keeps 1 Hz/s)",
             "source": "assumed",
             "note": "demo value, relaxed from the data center's 1 Hz/s so a tuned grid-forming "
                     "battery can meet it; the order of RoCoF withstand settings used in several "
                     "RfG implementations, not taken from a grid code"},
            {"item": "island rows",
             "value": "on, seamless: the islanding step and the island's own events (the largest "
                      "load step, any one unit's trip, the PV lost)",
             "source": "assumed",
             "note": _measured("measured 2026-10-16 on this plan: battery 6 (existing) + 6.08 / 6.08 / 3.27 MW "
                     "built in 2027 / 2030 / 2035, demo pre-tax NPV +43.2 M EUR (+43.9 M EUR when "
                     "the rows held the islanding step alone); the GFM settings recommender then "
                     "finds settings at the Guided defaults", plan)},
            {"item": "existing island", "value": ", ".join(a for a in MG_EXISTING)
                                                 + f": COD {y0}-01-01, overnight cost 0",
             "source": "assumed", "note": "in service at the first period and sunk"},
        ],
        "study_notes": [
            "Each period is the same representative week, with the period's load.",
            "Candidates are one row per period (build_year = the period).",
            "The island rows hold the steady island: grid-forming battery and diesel droop. They "
            "also keep the headroom the frequency check asks for: the largest load step, any one "
            "unit's trip and the PV lost.",
            f"The transition RoCoF limit is {MG_TRANSITION_ROCOF:g} Hz/s over 500 ms, a demo value "
            "relaxed from the data center's 1 Hz/s: the GFM settings recommender can then tune "
            "the batteries to meet it.",
            "After a solve the investment case compares the plan with the island as it stands "
            "today (the existing diesels, PV, wind and battery, no grid), re-solved without the "
            "candidates.",
        ],
        "provenance": T.PROVENANCE,
    }
    return _planned_meta(T, meta, plan, "load_scale", scale, [])


def _period_years_static() -> dict[int, int]:
    ends = [*YEARS[1:], HORIZON_END]
    return {y: e - y for y, e in zip(YEARS, ends)}
