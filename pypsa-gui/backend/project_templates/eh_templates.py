"""
Energy Hub project templates (plan 2026-09-26 P19).

Three ready-to-run hubs — one per archetype pack — so an EH study needs no
hand-parameterisation. Each network already carries the P14 tags
(``eh_poc`` / ``eh_critical`` / ``eh_sk_mva`` / ``eh_ibr_mva`` / ``eh_role``),
occurrence data on the thermal fleet and on the Links the archetype needs,
and a small extendable candidate set so the ENS-capped expansion has
something to size. ``TEMPLATE_META`` is written beside each ``network.nc``
as ``eh_template.json`` together with the template's stress-scenario
registry, and both are copied into a project created from the template.

Data are SYNTHETIC and illustrative: representative-week profiles with the
right shape and order of magnitude, costs in the range of public cost
catalogues. They are for learning and demonstrating the workflow, not for a
real project decision — ``TEMPLATE_META[...]["provenance"]`` says so.

Horizon: one representative 168 h week weighted 8760/168 per snapshot for
cost and energy (``objective``, ``generators``), so ``nyears ≈ 1`` (no CAPEX
scaling warning) and every MTTR here (≤ 72 h) fits the MC's MTTR floor (P11,
decision Q2). The ``stores`` weighting stays 1: PyPSA reads it as the hours
each snapshot lasts in the state-of-charge balance, and a snapshot here lasts
one hour (plan 2026-10-07 campus island, S0). Weighting it 52.14 made every
hourly dispatch move 52 hours of stored energy.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa

HOURS = 168
SNAPSHOTS = pd.date_range("2030-07-01", periods=HOURS, freq="h")
WEIGHT = 8760.0 / HOURS

_H = np.arange(HOURS)
_HOD = _H % 24
_DAY = _H // 24

PROVENANCE = ("synthetic illustrative data (P19 template) — representative "
              "week, public-catalogue-range costs; replace with project data "
              "before any decision")


def _solar(peak_hour: float = 13.0, width: float = 3.2,
           cloud: tuple[float, ...] = (1.0, 0.9, 0.55, 0.8, 1.0, 0.95, 0.7)
           ) -> np.ndarray:
    """Per-unit PV availability: a daylight bell times a daily cloud factor."""
    bell = np.exp(-((_HOD - peak_hour) ** 2) / (2 * width ** 2))
    bell[(_HOD < 6) | (_HOD > 20)] = 0.0
    return np.clip(bell * np.array(cloud)[_DAY], 0.0, 1.0)


def _wind(mean: float = 0.38, seed: int = 7) -> np.ndarray:
    """Per-unit wind availability: a slow synoptic swing plus gusts."""
    rng = np.random.default_rng(seed)
    synoptic = mean + 0.25 * np.sin(2 * np.pi * _H / 96.0 + 0.8)
    gust = rng.normal(0.0, 0.07, HOURS)
    return np.clip(synoptic + gust, 0.02, 0.98)


def _day_shape(base: float, peak: float, peak_hour: float = 15.0,
               width: float = 4.5) -> np.ndarray:
    return base + (peak - base) * np.exp(-((_HOD - peak_hour) ** 2) / (2 * width ** 2))


def _base(name: str) -> pypsa.Network:
    n = pypsa.Network()
    n.name = name
    n.set_snapshots(SNAPSHOTS)
    n.snapshot_weightings.loc[:, ["objective", "generators"]] = WEIGHT
    n.snapshot_weightings.loc[:, "stores"] = 1.0          # hours per snapshot (S0)
    return n


def _outage(n, component: str, name: str, rate: float, mttr_h: float) -> None:
    """Forced-outage rate (FOR basis) and MTTR on one asset."""
    df = getattr(n, component)
    for col, default in (("outage_rate_value", float("nan")),
                         ("mttr_hours", float("nan"))):
        if col not in df.columns:
            df[col] = default
    df.at[name, "outage_rate_value"] = rate
    df.at[name, "mttr_hours"] = mttr_h


def _tag_columns(n) -> None:
    """Typed EH tag columns (P14) with their defaults on every row."""
    n.buses["eh_poc"] = False
    n.buses["eh_critical"] = False
    n.buses["eh_sk_mva"] = float("nan")
    n.buses["eh_ibr_mva"] = float("nan")
    n.links["eh_role"] = ""


# ── Investment costs from the generic defaults pack (IC S3 seeds) ─────────
# Owner 2026-10-13: every candidate is priced from the pack through the asset
# schema (overnight parts; PyPSA or `derive.apply_parts` annuitises them at
# the pack's discount rate), never from an annualised literal.
TEMPLATE_PACK_VERSION = "2026-10-13"


def _price(n, component: str, name: str, technology: str) -> None:
    """Price one candidate from the pack (`defaults_pack.pricing.price_from_pack`)."""
    from services.library.defaults_pack.pricing import price_from_pack

    price_from_pack(n, component, name, technology, version=TEMPLATE_PACK_VERSION)


# ── Data Center Energy Hub (weak_flexible) ─────────────────────────────────


def build_eh_datacenter(phases=None) -> pypsa.Network:
    """A 50 MW hyperscale campus behind a weak, capped grid connection.

    Critical IT sits on its own bus behind the site transformer (a near
    loss-free Link, so the P16 per-Load priority is exact); cooling and
    offices share the MV bus and are the non-critical demand. A UPS battery
    rides on the IT bus, a four-unit gas genset fleet on the MV bus.

    ``phases`` (``DC_PHASES``) makes it the staged-growth campus
    (``_staged_datacenter``); None is the single-year template, unchanged.
    """
    n = _base("Data Center Energy Hub")
    for c, co2 in (("AC", 0.0), ("gas", 0.20), ("solar", 0.0),
                   ("battery", 0.0), ("grid", 0.35)):
        n.add("Carrier", c, co2_emissions=co2)
    n.add("Bus", "grid", v_nom=132.0, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "dc_mv", v_nom=33.0, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "it_bus", v_nom=11.0, carrier="AC", x=0.0, y=0.0)

    # Wholesale supply beyond the PoC (not firm capacity of the hub).
    price = 70.0 + 40.0 * np.exp(-((_HOD - 19) ** 2) / 8.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid",
          p_nom=500.0, marginal_cost=0.0)
    n.generators_t.marginal_cost = pd.DataFrame(
        {"grid_supply": price}, index=SNAPSHOTS)

    # The weak connection: 40 MW is the physical rating, so the FMEA
    # worksheet (saved network) and the EH study (pack cap) see one system.
    n.add("Link", "grid_import", bus0="grid", bus1="dc_mv", p_nom=40.0,
          efficiency=0.995, carrier="AC")
    n.add("Link", "site_transformer", bus0="dc_mv", bus1="it_bus", p_nom=80.0,
          efficiency=0.99, carrier="AC")

    rng = np.random.default_rng(11)
    it = 36.0 + rng.normal(0.0, 0.6, HOURS)
    n.add("Load", "it_load", bus="it_bus", carrier="AC")
    n.add("Load", "cooling", bus="dc_mv", carrier="AC")
    n.add("Load", "offices", bus="dc_mv", carrier="AC")
    n.loads_t.p_set = pd.DataFrame({
        "it_load": it,
        "cooling": _day_shape(8.0, 14.0),
        "offices": _day_shape(0.4, 1.6, peak_hour=12.0, width=3.0),
    }, index=SNAPSHOTS)

    for i in range(1, 5):
        g = f"genset_{i}"
        n.add("Generator", g, bus="dc_mv", carrier="gas", p_nom=10.0,
              marginal_cost=165.0)
        _outage(n, "generators", g, 0.03, 24.0)
    n.add("Generator", "rooftop_pv", bus="dc_mv", carrier="solar", p_nom=4.0,
          marginal_cost=0.0)
    n.generators_t.p_max_pu = pd.DataFrame(
        {"rooftop_pv": _solar()}, index=SNAPSHOTS)
    # The UPS carries the IT load it protects (about 36 MW) for a realistic
    # 15 minutes: 40 MW / 10 MWh, enough for the island plan's 600 s bridge
    # (owner decision, plan 2026-10-07 campus island, S0).
    n.add("StorageUnit", "ups_battery", bus="it_bus", carrier="battery",
          p_nom=40.0, max_hours=0.25, efficiency_store=0.95,
          efficiency_dispatch=0.95, cyclic_state_of_charge=True)

    # Candidates the ENS-capped expansion may build, priced from the pack.
    n.add("Generator", "genset_new", bus="dc_mv", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=40.0, marginal_cost=165.0)
    _price(n, "generators", "genset_new", "gas-genset")
    _outage(n, "generators", "genset_new", 0.03, 24.0)
    n.add("StorageUnit", "bess_new", bus="it_bus", carrier="battery",
          p_nom=0.0, p_nom_extendable=True, p_nom_max=30.0, max_hours=4.0,
          efficiency_store=0.95, efficiency_dispatch=0.95,
          cyclic_state_of_charge=True)
    _price(n, "storage_units", "bess_new", "battery")

    _tag_columns(n)
    n.buses.at["grid", "eh_poc"] = True
    # SCR gate inputs at the PoC: a 250 MVA fault level against ~30 MVA of
    # inverter-based resources (PV + BESS inverters; the UPS feeds no fault
    # upstream, its rectifier blocks it) → SCR ≈ 8, which passes. The connection is CAPACITY-weak (40 MW), not SCR-weak.
    n.buses.at["grid", "eh_sk_mva"] = 250.0
    n.buses.at["grid", "eh_ibr_mva"] = 30.0
    n.buses.at["dc_mv", "eh_sk_mva"] = 220.0
    n.buses.at["dc_mv", "eh_ibr_mva"] = 25.0
    n.buses.at["it_bus", "eh_critical"] = True
    n.links.at["grid_import", "eh_role"] = "grid_import"
    # The import is firm only with its own outage data (decision 6 / Q7).
    _outage(n, "links", "grid_import", 0.002, 12.0)
    # Site transformer failure islands the IT bus onto its UPS: a Class-B
    # mode fmea_top should rank.
    _outage(n, "links", "site_transformer", 0.005, 72.0)
    return n if phases is None else _staged_datacenter(n, phases)


# ── Data Center Energy Hub, staged growth (E2E demo plan 2026-10-14, slice 8) ─
#
# The same campus planned from a projected load: three investment periods, the
# IT load ramping as the data halls are fitted out, and the utility energizing
# a 55 MW connection in steps. Everything else is the single-year template.
# Synthetic illustrative numbers, like the rest of this file.

#: The projected load: (period year, IT MW, PUE). Cooling scales with the IT
#: load (the single-year template's 8-14 MW profile is for 36 MW of IT).
DC_PHASES = ((2027, 12.0, 1.3), (2030, 24.0, 1.3), (2035, 36.0, 1.3))
#: The staged campus's PoC rating (MW): sized to its 2035 peak (about 52.5 MW
#: modelled), so the grid-only comparison of the investment case can run (owner
#: decision 2026-10-15). The single-year template keeps its weak 40 MW.
_DC_POC_MW = 55.0
#: The utility's energization schedule of the connection (assumed): the import
#: available in each period, in MW. 2027 is energized below that period's peak
#: (about 18.4 MW), so the early grid limit drives the first build and the
#: grid-only comparison sheds then (owner decision 2026-10-15).
DC_ENERGIZATION_MW = {2027: 15.0, 2030: _DC_POC_MW, 2035: _DC_POC_MW}
#: Years each period stands for (2027-29, 2030-34, 2035-44): the horizon ends in
#: 2045, before any candidate's pack lifetime (25 years) runs out.
DC_PERIOD_YEARS = {2027: 3, 2030: 5, 2035: 10}
_DC_IT_MW = 36.0                                   # the single-year template's IT load

# ── The staged campus's commercial and finance inputs (slice D4) ────────────
# Inline only (no Library refs a fresh project would not have), so the
# investment case computes on a project made from the template. Synthetic
# illustrative numbers; the ledger in TEMPLATE_META says what each one is.

#: The existing campus, in service at the first period (overnight cost 0).
DC_EXISTING = ("genset_1", "genset_2", "genset_3", "genset_4", "rooftop_pv", "ups_battery",
               "site_transformer")
#: A synthetic network-use charge on the metered import (EUR/kWh), on top of
#: the wholesale price the grid supply carries.
DC_TARIFF: dict = {
    "id": "dc_network_tariff_synthetic",
    "name": "Synthetic network tariff (illustrative)",
    "jurisdiction": "DE",
    "valid_from": "2027-01-01",
    "items": [{"id": "network_energy", "kind": "energy", "unit": "per_kwh",
               "periods": [{"name": "day", "rate": 0.035, "start_hour": 8, "end_hour": 20},
                           {"name": "night", "rate": 0.020}]}],
}
DC_FINANCE: dict = {
    "currency": "EUR", "currency_year": 2020, "price_basis": "nominal",
    "financial_close": "2026-01-01",
    "cod_by_asset": {a: "2027-01-01" for a in DC_EXISTING},
    "capex_phasing": [1.0], "stage_capex_lead_years": 1, "contingency_share": 0.0,
    "escalation": {c: 0.02 for c in ("opex", "fuel", "tariff", "ppa", "export", "capex")},
    # Every owned generator and battery needs an entry (the engine never reads
    # a missing one as 0): PV 0.5 %/yr, the gensets and batteries none (the
    # batteries on the no-degradation basis, as the guided case).
    "degradation_by_asset": {
        **{a: 0.0 for a in DC_EXISTING if a not in ("rooftop_pv", "site_transformer")},
        "rooftop_pv": 0.005,
        **{f"{k}_new_{y}": 0.0 for y, _it, _pue in DC_PHASES for k in ("genset", "bess")}},
    "analysis_years": sum(DC_PERIOD_YEARS.values()),            # 2027-2044
    "annualise": False,                                         # each period weighs 8,760 h
    "tax_losses": "offset_other_income", "financing_fee_tax": "not_deducted",
    "replacement_rule": "part_lifetimes",
    "terminal_value": {"method": "remaining_life_annuity"},
    "wacc_nominal": 0.07, "cost_of_equity": 0.07, "inflation": 0.02,
}


def _staged_datacenter(n: pypsa.Network, phases) -> pypsa.Network:
    """The single-year campus as a three-period plan: the week repeated in each
    period, the load scaled to ``phases``, the import capped per period by
    ``DC_ENERGIZATION_MW`` (an absolute MW schedule on the ``_DC_POC_MW`` link), and the two expansion
    candidates replaced by one candidate per period with that period as its
    ``build_year`` (explicit rows, not vintage bounds, so the finance adapter
    sees each stage)."""
    years = [int(y) for y, _it, _pue in phases]
    flat = {
        ("generators_t", "p_max_pu"): n.generators_t.p_max_pu.copy(),
        ("generators_t", "marginal_cost"): n.generators_t.marginal_cost.copy(),
        ("loads_t", "p_set"): n.loads_t.p_set.copy(),
    }
    snaps = pd.MultiIndex.from_product([years, SNAPSHOTS], names=["period", "timestep"])
    n.set_snapshots(snaps)
    n.investment_periods = years
    n.snapshot_weightings.loc[:, ["objective", "generators"]] = WEIGHT
    n.snapshot_weightings.loc[:, "stores"] = 1.0                    # hours per snapshot (S0)
    n.investment_period_weightings["years"] = [float(DC_PERIOD_YEARS[y]) for y in years]
    n.investment_period_weightings["objective"] = [float(DC_PERIOD_YEARS[y]) for y in years]
    snaps = n.snapshots                                 # PyPSA's own index (names and all)
    for (frame, attr), df in flat.items():
        tiled = pd.DataFrame(np.tile(df.to_numpy(), (len(years), 1)), index=snaps, columns=df.columns)
        getattr(n, frame)[attr] = tiled
    p_set = n.loads_t.p_set
    for y, it_mw, _pue in phases:
        scale = it_mw / _DC_IT_MW
        for load in ("it_load", "cooling"):
            p_set.loc[y, load] = (p_set.loc[y, load] * scale).to_numpy()
    # The energization schedule in MW (`services.adequacy.energization`): a
    # solve turns it into p_max_pu = min(own, E / p_nom), so a lever or an
    # overlay that rewrites p_nom composes as min(E, cap) instead of
    # rescaling a stored fraction (gatekeeper review of #266).
    n.links_t["eh_energization_mw"] = pd.DataFrame(
        {"grid_import": np.repeat([DC_ENERGIZATION_MW[y] for y in years], len(SNAPSHOTS))},
        index=snaps)
    n.remove("Generator", "genset_new")
    n.remove("StorageUnit", "bess_new")
    n.links.at["grid_import", "p_nom"] = _DC_POC_MW
    # The investment case's counterfactual bill is the served load at the
    # meter (`finance_case._lossy_poc`): with a lossy PoC chain it is not the
    # metered import, and the case is refused. Losses are not modelled in the
    # staged plan (the ledger says so); the single-year template keeps them.
    n.links.loc[["grid_import", "site_transformer"], "efficiency"] = 1.0
    # The existing campus is in service at the first period: a typed
    # overnight cost of 0 is an established 0 (no capex, never a cost
    # back-calculated from capital_cost). The PoC meter link stays untyped:
    # it is not an investment.
    for frame in ("generators", "storage_units", "links"):
        df = getattr(n, frame)
        rows = [a for a in DC_EXISTING if a in df.index]
        # PyPSA annuitises a typed overnight cost and needs the rate to (the
        # template's 7 %; 0 × anything is 0, so the LP is unchanged).
        df.loc[rows, ["overnight_cost", "discount_rate"]] = [0.0, 0.07]
    for y in years:
        g, b = f"genset_new_{y}", f"bess_new_{y}"
        n.add("Generator", g, bus="dc_mv", carrier="gas", p_nom=0.0, p_nom_extendable=True,
              p_nom_max=40.0, marginal_cost=165.0, build_year=y)
        _price(n, "generators", g, "gas-genset")
        _outage(n, "generators", g, 0.03, 24.0)
        n.add("StorageUnit", b, bus="it_bus", carrier="battery", p_nom=0.0, p_nom_extendable=True,
              p_nom_max=30.0, max_hours=4.0, efficiency_store=0.95, efficiency_dispatch=0.95,
              cyclic_state_of_charge=True, build_year=y)
        _price(n, "storage_units", b, "battery")
    # Storage cycles within each period: each period's week stands for 3-10
    # years, so no state of charge is carried into the next period
    # (gatekeeper review of #304; the same rule as eh_site.stage_network).
    n.storage_units["cyclic_state_of_charge"] = True
    n.storage_units["cyclic_state_of_charge_per_period"] = True
    n.name = "Data Center Energy Hub (staged growth)"
    return n


def build_eh_datacenter_staged() -> pypsa.Network:
    return build_eh_datacenter(phases=DC_PHASES)


# ── Industrial Hydrogen Hub (strong_grid) ──────────────────────────────────


def build_eh_h2_hub() -> pypsa.Network:
    """Grid-connected industrial site with wind, PV and an H₂ chain.

    Electrolyser → H₂ store → fuel cell carry the ``eh_role`` conversion
    roles (redundancy scenarios, multi-energy report); the process load is
    the critical demand and an H₂ offtake is the second energy carrier.
    """
    n = _base("Industrial Hydrogen Hub")
    for c, co2 in (("AC", 0.0), ("H2", 0.0), ("wind", 0.0), ("solar", 0.0),
                   ("grid", 0.35), ("electrolysis", 0.0), ("fuel cell", 0.0)):
        n.add("Carrier", c, co2_emissions=co2)
    n.add("Bus", "grid", v_nom=220.0, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "hub", v_nom=110.0, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "h2", carrier="H2", x=0.0, y=0.0)

    price = 60.0 + 35.0 * np.exp(-((_HOD - 18) ** 2) / 10.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid",
          p_nom=1000.0, marginal_cost=0.0)
    n.generators_t.marginal_cost = pd.DataFrame(
        {"grid_supply": price}, index=SNAPSHOTS)
    n.add("Link", "grid_import", bus0="grid", bus1="hub", p_nom=200.0,
          efficiency=0.995, carrier="AC")

    n.add("Generator", "wind_farm", bus="hub", carrier="wind", p_nom=120.0,
          marginal_cost=0.0)
    n.add("Generator", "solar_park", bus="hub", carrier="solar", p_nom=80.0,
          marginal_cost=0.0)
    n.add("Generator", "wind_new", bus="hub", carrier="wind", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=300.0)
    _price(n, "generators", "wind_new", "onwind")
    n.add("Generator", "solar_new", bus="hub", carrier="solar", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=150.0)
    _price(n, "generators", "solar_new", "solar-utility")
    wind, sun = _wind(), _solar()
    n.generators_t.p_max_pu = pd.DataFrame(
        {"wind_farm": wind, "wind_new": wind,
         "solar_park": sun, "solar_new": sun}, index=SNAPSHOTS)

    n.add("Link", "electrolyser", bus0="hub", bus1="h2", p_nom=60.0,
          efficiency=0.68, carrier="electrolysis")
    n.add("Store", "h2_storage", bus="h2", carrier="H2", e_nom=2000.0,
          e_cyclic=True)
    # Extendable fuel cell: the H₂ chain is the hub's firm backup when the
    # grid connection is lost (DtC planning sizes it).
    n.add("Link", "fuel_cell", bus0="h2", bus1="hub", p_nom=10.0,
          p_nom_extendable=True, p_nom_min=10.0, p_nom_max=80.0,
          efficiency=0.50, carrier="fuel cell")
    _price(n, "links", "fuel_cell", "fuel-cell")
    n.add("Carrier", "battery", co2_emissions=0.0)
    n.add("StorageUnit", "site_battery", bus="hub", carrier="battery",
          p_nom=20.0, max_hours=2.0, efficiency_store=0.95,
          efficiency_dispatch=0.95, cyclic_state_of_charge=True)
    n.add("StorageUnit", "bess_new", bus="hub", carrier="battery", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=200.0, max_hours=6.0,
          efficiency_store=0.95, efficiency_dispatch=0.95,
          cyclic_state_of_charge=True)
    _price(n, "storage_units", "bess_new", "battery")

    n.add("Load", "process_load", bus="hub", carrier="AC")
    n.add("Load", "h2_offtake", bus="h2", carrier="H2", p_set=25.0)
    rng = np.random.default_rng(5)
    n.loads_t.p_set = pd.DataFrame(
        {"process_load": 40.0 + rng.normal(0.0, 1.5, HOURS)}, index=SNAPSHOTS)

    _tag_columns(n)
    n.buses.at["grid", "eh_poc"] = True
    n.buses.at["grid", "eh_sk_mva"] = 5000.0
    n.buses.at["hub", "eh_critical"] = True
    n.buses.at["hub", "eh_sk_mva"] = 900.0
    n.buses.at["hub", "eh_ibr_mva"] = 180.0
    n.links.at["grid_import", "eh_role"] = "grid_import"
    n.links.at["electrolyser", "eh_role"] = "electrolyser"
    n.links.at["fuel_cell", "eh_role"] = "fuel_cell"
    # Link outage data → Class-B contingencies for fmea_top.
    _outage(n, "links", "grid_import", 0.002, 12.0)
    _outage(n, "links", "electrolyser", 0.04, 48.0)
    _outage(n, "links", "fuel_cell", 0.05, 36.0)
    return n


# ── Island Microgrid (off_grid) ─────────────────────────────────────────────


def build_eh_microgrid() -> pypsa.Network:
    """A small island system that must run without its mainland tie.

    The ``off_grid`` pack islands the tagged tie Link; PV, wind, a battery
    and a diesel fleet (with outage data) must carry a critical hospital
    feeder and residential demand. The hospital sits behind a feeder Link
    (η = 0.98 ≥ 1/1.05, so the P16 per-Load priority stays exact).
    """
    n = _base("Island Microgrid")
    for c, co2 in (("AC", 0.0), ("solar", 0.0), ("wind", 0.0),
                   ("diesel", 0.27), ("battery", 0.0), ("grid", 0.35)):
        n.add("Carrier", c, co2_emissions=co2)
    n.add("Bus", "mainland", v_nom=33.0, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "island", v_nom=11.0, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "hospital", v_nom=11.0, carrier="AC", x=0.0, y=0.0)

    n.add("Generator", "mainland_supply", bus="mainland", carrier="grid",
          p_nom=100.0, marginal_cost=110.0)
    # Normally OPEN (p_max_pu = 0): the island runs on its own resources, so
    # the FMEA worksheet sweeps the same islanded system the off_grid study
    # assesses. Set p_max_pu = 1 to study tie-connected operation.
    n.add("Link", "subsea_tie", bus0="mainland", bus1="island", p_nom=20.0,
          p_max_pu=0.0, efficiency=0.97, carrier="AC")
    n.add("Link", "hospital_feeder", bus0="island", bus1="hospital",
          p_nom=10.0, efficiency=0.98, carrier="AC")

    n.add("Generator", "pv_plant", bus="island", carrier="solar", p_nom=12.0)
    n.add("Generator", "wind_turbines", bus="island", carrier="wind", p_nom=6.0)
    n.generators_t.p_max_pu = pd.DataFrame(
        {"pv_plant": _solar(), "wind_turbines": _wind(mean=0.33, seed=3)},
        index=SNAPSHOTS)
    for i in range(1, 4):
        g = f"diesel_{i}"
        n.add("Generator", g, bus="island", carrier="diesel", p_nom=3.0,
              marginal_cost=260.0)
        _outage(n, "generators", g, 0.05, 48.0)
    n.add("StorageUnit", "island_battery", bus="island", carrier="battery",
          p_nom=6.0, max_hours=4.0, efficiency_store=0.94,
          efficiency_dispatch=0.94, cyclic_state_of_charge=True)
    n.add("Generator", "diesel_new", bus="island", carrier="diesel", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=12.0, marginal_cost=260.0)
    _price(n, "generators", "diesel_new", "diesel-genset")
    _outage(n, "generators", "diesel_new", 0.05, 48.0)
    n.add("StorageUnit", "bess_new", bus="island", carrier="battery",
          p_nom=0.0, p_nom_extendable=True, p_nom_max=15.0, max_hours=4.0,
          efficiency_store=0.94, efficiency_dispatch=0.94,
          cyclic_state_of_charge=True)
    _price(n, "storage_units", "bess_new", "battery")

    n.add("Load", "residential", bus="island", carrier="AC")
    n.add("Load", "hospital_load", bus="hospital", carrier="AC")
    n.loads_t.p_set = pd.DataFrame({
        "residential": _day_shape(3.5, 7.5, peak_hour=19.0, width=3.5),
        "hospital_load": 1.5 + 0.2 * np.sin(2 * np.pi * _HOD / 24.0),
    }, index=SNAPSHOTS)

    _tag_columns(n)
    n.buses.at["mainland", "eh_poc"] = True
    n.buses.at["hospital", "eh_critical"] = True
    n.buses.at["island", "eh_sk_mva"] = 45.0
    n.buses.at["island", "eh_ibr_mva"] = 20.0
    n.links.at["subsea_tie", "eh_role"] = "grid_import"
    # Feeder outage data → a Class-B contingency for fmea_top once the tie
    # is islanded (the tie itself is closed by the pack).
    _outage(n, "links", "hospital_feeder", 0.01, 8.0)
    return n


# ── metadata + sidecars ─────────────────────────────────────────────────────

BUILDERS = {
    "eh_datacenter": build_eh_datacenter,
    "eh_datacenter_staged": build_eh_datacenter_staged,
    "eh_h2_hub": build_eh_h2_hub,
    "eh_microgrid": build_eh_microgrid,
}

# What the GUI preselects and the assistant recommends for each template.
# ``pack_overrides`` / ``stages`` / ``dtc_attribution`` are EhStudyRequest
# fields verbatim, so the panel can post them unchanged.
TEMPLATE_META: dict[str, dict] = {
    "eh_datacenter": {
        "id": "eh_datacenter",
        "name": "Data Center Energy Hub",
        "description": (
            "50 MW hyperscale campus behind a weak 40 MW grid connection: "
            "critical IT with UPS battery, gas genset fleet, rooftop PV, "
            "genset/battery expansion candidates. Tagged and ready for an "
            "Energy Hub study."),
        "recommended_archetype": "weak_flexible",
        "pack_overrides": {"import_p_nom_mw": 40.0},
        "stages": None,
        "dtc_attribution": "per_load",
        "study_notes": [
            "IT load is critical (it_bus); cooling and offices are not.",
            "The grid import carries its own outage data, so the MC can "
            "credit it as a firm unit (decision 6).",
            "Try the energy import budget (Pack settings → Import energy) "
            "to see a capped-import plan.",
        ],
        "provenance": PROVENANCE,
    },
    "eh_datacenter_staged": {
        "id": "eh_datacenter_staged",
        "name": "Data Center Energy Hub (staged growth)",
        "description": (
            "The data center campus planned from a projected load: IT grows "
            "12 → 24 → 36 MW over 2027, 2030 and 2035 while the utility "
            f"energizes a {_DC_POC_MW:g} MW connection in steps "
            f"({DC_ENERGIZATION_MW[2027]:g} MW until 2030). "
            "Genset and battery candidates per period, so the plan says what "
            "to build when. Multi-period; tagged for an Energy Hub study."),
        "recommended_archetype": "weak_flexible",
        "pack_overrides": {"import_p_nom_mw": _DC_POC_MW},
        "stages": None,
        "dtc_attribution": "per_load",
        "projected_load": [
            {"year": y, "it_mw": it, "pue": pue,
             "note": "synthetic illustrative fit-out ramp; replace with the client's load projection"}
            for y, it, pue in DC_PHASES],
        "energization": [
            {"year": y, "available_import_mw": DC_ENERGIZATION_MW[y], "connection_mw": _DC_POC_MW,
             "source": "assumed", "note": "utility energization schedule (assumed)"}
            for y, _it, _pue in DC_PHASES],
        "period_years": {str(y): DC_PERIOD_YEARS[y] for y, _it, _pue in DC_PHASES},
        "ledger": [
            {"item": "discount rate", "value": "7 % nominal (the solver settings' discount_rate)",
             "source": "assumed",
             "note": "the solve discounts each period's costs at it (auto_discount_periods)"},
            {"item": "PUE", "value": "1.3 design figure",
             "source": "assumed",
             "note": "metadata: cooling is the single-year template's profile scaled with the IT "
                     "load, so the modelled peak-to-IT ratio is about 1.46 in 2035"},
            {"item": "battery inverter replacement", "value": "after 10 years (inv_power_lifetime)",
             "source": "assumed",
             "note": "bess_new_2027's inverter ends in 2037 and bess_new_2030's in 2040, inside the "
                     "2045 horizon; the template's finance inputs re-buy it at the end of its "
                     "lifetime (replacement_rule part_lifetimes)"},
            {"item": "PoC and transformer losses", "value": "efficiency 1.0 on grid_import and "
                                                            "site_transformer",
             "source": "assumed",
             "note": "losses are not modelled in the staged plan (the single-year template keeps "
                     "0.995 and 0.99), so the investment case's counterfactual bill (the served "
                     "load at the meter) is established; about 1.5 % of the import is left out"},
            {"item": "import tariff", "value": f"{DC_TARIFF['id']}: network energy charge "
                                               "35 EUR/MWh 08-20 h, 20 EUR/MWh otherwise",
             "source": "synthetic",
             "note": "an illustrative network-use charge on the metered import, on top of the "
                     "wholesale price the grid supply carries; no demand charge (the template "
                     "models one July week, so a monthly item would be billed for one month); "
                     "replace with the site's tariff"},
            {"item": "tariff clock", "value": "UTC",
             "source": "assumed",
             "note": "the representative week's hours are the site clock; stating them as UTC "
                     "reads each tariff window on the snapshot hour as is"},
            {"item": "existing campus", "value": "gensets 1-4, rooftop_pv, ups_battery, "
                                                 "site_transformer: COD 2027-01-01, overnight cost 0",
             "source": "assumed",
             "note": "in service at the first period and sunk: no capex in the case; only the "
                     "candidates the plan builds are investments, each a stage at its period"},
            {"item": "finance inputs", "value": "nominal EUR (2020 money, the pack's), escalation "
                                                "2 %/yr, inflation 2 %, all equity at 7 %, "
                                                "close 2026-01-01, 18 years to 2045",
             "source": "assumed",
             "note": "a later stage's capex is paid one year before its COD; pre-tax (no tax "
                     "pack); the terminal value is the remaining-life annuity of each part"},
            {"item": "depreciation of the staged assets",
             "value": "straight-line AfA (not degressive)",
             "source": "assumed",
             "note": "a deliberate deviation: a dated degressive election on a later stage is refused by "
                     "the investment case (P5), so the staged assets are planned on straight-line AfA"},
        ],
        "study_notes": [
            "Each period is the same representative July week, with the period's load and import cap.",
            "Candidates are one row per period (build_year = the period): the plan says what to build when.",
            f"The energization schedule caps the import at {DC_ENERGIZATION_MW[2027]:g} MW in "
            "2027, below that period's peak: the plan builds on site for it.",
            "Load is picked up in 2.5 MW blocks after an islanding (assumed): the island rows then "
            "follow the load, so the plan stages — a small genset in 2027 and 2030, the bulk in 2035.",
            "After a solve the investment case runs on the shipped commercial config and finance "
            "inputs: the existing campus is in service in 2027 at no capex, each candidate the plan "
            "builds is a stage with its own COD, and an unbuilt later candidate is not an investment.",
            "PoC and transformer losses are not modelled in the staged plan (efficiency 1.0), so the "
            "investment case can compare against the grid-only site.",
            f"The {_DC_POC_MW:g} MW connection carries the 2035 peak. The investment case "
            "compares against the site as it stands today: with the plan's new builds removed, "
            f"the site still meets its load (the {DC_ENERGIZATION_MW[2027]:g} MW energized in 2027 "
            "included), and the island capability the new builds buy is shown as a break-even "
            "beside the NPV.",
        ],
        "provenance": PROVENANCE,
    },
    "eh_h2_hub": {
        "id": "eh_h2_hub",
        "name": "Industrial Hydrogen Hub",
        "description": (
            "Grid-connected industrial site: 120 MW wind, 80 MW PV, 60 MW "
            "electrolyser, H₂ storage, an extendable fuel cell and a site "
            "battery, 40 MW critical process load and 25 MW H₂ offtake. "
            "Tagged for an Energy Hub study."),
        "recommended_archetype": "strong_grid",
        "pack_overrides": {},
        "stages": None,
        "dtc_attribution": None,
        "study_notes": [
            "Electrolyser and fuel cell carry conversion roles and outage "
            "data: fmea_top ranks them, redundancy can add a spare.",
            "The frontier runs by default for strong_grid.",
            "Island rows are off: the plan is not sized to island; the island checks "
            "report what islanding would need.",
        ],
        "provenance": PROVENANCE,
    },
    "eh_microgrid": {
        "id": "eh_microgrid",
        "name": "Island Microgrid",
        "description": (
            "Island system that must run without its 20 MW subsea tie: PV, "
            "wind, battery, three diesel units, a critical hospital feeder "
            "and residential demand. Tagged for an off-grid study."),
        "recommended_archetype": "off_grid",
        "pack_overrides": {},
        "stages": None,
        "dtc_attribution": None,
        "study_notes": [
            "The off_grid pack islands the subsea tie; the island must be "
            "adequate on its own resources.",
            "The subsea tie is normally open (p_max_pu = 0), so the FMEA "
            "worksheet sweeps the same islanded system; set it to 1 to "
            "study tie-connected operation.",
            "Class-C stress scenarios (dunkelflaute, heatwave) are "
            "preloaded for the FMEA sweep.",
        ],
        "provenance": PROVENANCE,
    },
}

# Preloaded Class-C registry per template (the stress.py schema).
STRESS_SCENARIOS: dict[str, list[dict]] = {
    "eh_datacenter_staged": [
        {"id": "heatwave", "name": "Heatwave (cooling + PV derate)",
         "kind": "parametric", "frequency_per_year": 0.3,
         "electrical_load_multiplier": 1.12,
         "renewable_availability_multiplier": 0.85},
        {"id": "dunkelflaute", "name": "Dark, still week",
         "kind": "parametric", "frequency_per_year": 0.5,
         "electrical_load_multiplier": 1.0,
         "renewable_availability_multiplier": 0.1},
    ],
    "eh_datacenter": [
        {"id": "heatwave", "name": "Heatwave (cooling + PV derate)",
         "kind": "parametric", "frequency_per_year": 0.3,
         "electrical_load_multiplier": 1.12,
         "renewable_availability_multiplier": 0.85},
        {"id": "dunkelflaute", "name": "Dark, still week",
         "kind": "parametric", "frequency_per_year": 0.5,
         "electrical_load_multiplier": 1.0,
         "renewable_availability_multiplier": 0.1},
    ],
    "eh_h2_hub": [
        {"id": "low_wind_week", "name": "Low-wind week",
         "kind": "parametric", "frequency_per_year": 1.0,
         "electrical_load_multiplier": 1.0,
         "renewable_availability_multiplier": 0.35},
    ],
    "eh_microgrid": [
        {"id": "dunkelflaute", "name": "Dunkelflaute",
         "kind": "parametric", "frequency_per_year": 0.4,
         "electrical_load_multiplier": 1.05,
         "renewable_availability_multiplier": 0.1},
        {"id": "heatwave", "name": "Heatwave (A/C peak)",
         "kind": "parametric", "frequency_per_year": 0.2,
         "electrical_load_multiplier": 1.25,
         "renewable_availability_multiplier": 0.9},
    ],
}

# Solver settings a project from these templates starts with: frontier and
# fmea_top need VOLL > 0 (a template otherwise starts from the default 0).
SOLVER_CONFIG: dict = {"voll": 5000.0}
# Per-template additions. The island rows (campus island I2a) are opt-in per
# project and ship enabled on the templates that carry an island sidecar
# (owner Q5).
SOLVER_CONFIG_EXTRA: dict[str, dict] = {
    "eh_datacenter": {"island_constraints": True},
    # The staged campus is multi-period; the solve discounts the periods
    # (auto_discount_periods) at the config's discount rate.
    "eh_datacenter_staged": {"island_constraints": True, "multi_investment_periods": True,
                             "auto_discount_periods": True, "discount_rate": 0.07},
}


def _staged_commercial_and_finance() -> dict:
    """The staged template's `commercial` and `finance` blocks, stored as the
    routes store them (validated, dumped): the value flows are the
    `single_owner` template built on the template's network."""
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs
    from services.commercial import value_flow_templates as VFT

    base = CommercialConfig.model_validate(
        {"poc_link": "grid_import", "import_tariff": DC_TARIFF, "timezone": "UTC"})
    vf = VFT.build("single_owner", build_eh_datacenter_staged(), base).config
    commercial = base.model_copy(update={"value_flows": vf.model_dump(mode="json")})
    return {"commercial": commercial.model_dump(mode="json"),
            "finance": FinanceInputs.model_validate(DC_FINANCE).model_dump(mode="json")}


#: Template blocks that need the backend's models (built on first use).
_SOLVER_CONFIG_BUILT = {"eh_datacenter_staged": _staged_commercial_and_finance}
_BUILT_CACHE: dict[str, dict] = {}


def solver_config(template_id: str) -> dict:
    """The solver settings written beside one template."""
    import copy

    built = {}
    if template_id in _SOLVER_CONFIG_BUILT:
        if template_id not in _BUILT_CACHE:
            _BUILT_CACHE[template_id] = _SOLVER_CONFIG_BUILT[template_id]()
        built = copy.deepcopy(_BUILT_CACHE[template_id])
    return {**SOLVER_CONFIG, **SOLVER_CONFIG_EXTRA.get(template_id, {}), **built}

# The island sidecar (plan 2026-10-07 campus island, I1): control modes, unit
# dynamics and island requirements, keyed by PyPSA name. Its schema is
# gridspine's (gridspine/schema/island.py), which this module does not
# import (it loads without gridspine on the path), so the content is a
# plain mapping and tests/test_island_config.py validates it through
# gridspine. Every number is {value, source}; all are "assumed" until the
# client's datasheets arrive. Only templates listed here ship the file.
def _a(value):
    return {"value": value, "source": "assumed"}


def _genset_island(**extra):
    return {"kind": "genset", "governor": "droop", "droop_pct": _a(4.0), "q_droop_pct": _a(4.0),
            "H_s": _a(1.5), "T_g_s": _a(0.5), "ramp_pu_per_s": _a(0.2), "start_s": _a(10.0),
            "sync_s": _a(5.0), "p_min_pu": _a(0.3), "load_step_max_pct": _a(50.0),
            "cos_phi_r": _a(0.8), "xd_sat": _a(2.0), "excitation": "avr_pmg",
            "neutral_earthing": "solid", **extra}


_LIMITS = {"rocof_hz_per_s": _a(1.0), "f_min_hz": _a(47.5), "f_max_hz": _a(51.5), "qss_band_hz": _a(1.0)}

ISLAND_CONFIG: dict[str, dict] = {
    "eh_datacenter": {
        "units": {
            **{f"genset_{i}": _genset_island() for i in range(1, 5)},
            "genset_new": _genset_island(unit_mw=_a(10.0)),
            "rooftop_pv": {"kind": "pv", "control": "gfl_qu", "pf_rated": _a(0.95),
                           "qu_points": _a([[0.9, 0.33], [0.97, 0.0], [1.03, 0.0], [1.1, -0.33]])},
            "bess_new": {"kind": "bess", "control": "gfm_droop", "pf_rated": _a(0.95),
                         "droop_pct": _a(4.0), "q_droop_pct": _a(4.0), "tau_f_s": _a(0.1),
                         "i_max_pu": _a(1.2), "k_gfm_min": _a(1.0)},
            "ups_battery": {"kind": "ups", "it_load": ["it_load"], "walk_in_s": _a(15.0),
                            "f_window_hz": _a(2.0)},
            # Plan Q1: the critical load is the IT and the cooling it needs.
            "it_load": {"kind": "load", "critical": True},
            "cooling": {"kind": "load", "critical": True},
            "offices": {"kind": "load", "critical": False},
        },
        "requirements": {
            "bridge_s": _a(600.0), "sustained_h": _a(24.0), "fuel_autonomy_h": _a(24.0),
            # ups_bridged only, in 5 MW blocks (owner, 2026-10-13, campus island
            # I2b): 15 MW blocks need ~210 MW of H 1.5 s gensets to hold 1 Hz/s,
            # and seamless at 30 MW of BESS cannot form the island at 0⁺. The
            # 4 × 10 MW fleet plus genset_new holds a 5 MW block; seamless stays
            # a sidecar option, and I2c compares the two.
            "genset_redundancy_n": _a(1), "scenarios": ["ups_bridged"],
            "pickup_blocks": _a([5.0] * 11),
            "frequency_limits": {"transition": dict(_LIMITS), "steady_island": dict(_LIMITS)},
            "rocof_window_ms": _a(500.0), "gfm_margin": _a(0.1), "ride_through_storage": ["bess_new"],
        },
    },
}

def _h2_hub_island(bess_new, wind_new, solar_new) -> dict:
    """The H2 hub's island sidecar: no genset and no UPS, so its batteries
    form the island (``seamless``) and carry the critical process load. The
    battery block is the data center's ``bess_new`` block and the fuel cell
    gets gridspine's drafted default (``producers/campus.py``
    ``DEFAULT_ISLAND``), so draft and sidecar agree; all demo values. The
    island rows ship off (owner, 2026-10-16): the plan is not sized to
    island, and the island checks report what islanding would need."""
    import copy

    bess = copy.deepcopy(ISLAND_CONFIG["eh_datacenter"]["units"]["bess_new"])
    qu = _a([[0.9, 0.33], [0.97, 0.0], [1.03, 0.0], [1.1, -0.33]])

    def gfl(kind):
        return {"kind": kind, "control": "gfl_qu", "pf_rated": _a(0.95), "qu_points": copy.deepcopy(qu)}

    units = {"site_battery": copy.deepcopy(bess), "fuel_cell": gfl("fuel_cell"),
             "process_load": {"kind": "load", "critical": True}}
    for name, kind in (("wind_farm", "wind"), *((w, "wind") for w in wind_new),
                       ("solar_park", "pv"), *((s, "pv") for s in solar_new)):
        units[name] = gfl(kind)
    for b in bess_new:
        units[b] = copy.deepcopy(bess)
    return {"units": units, "requirements": {
        "bridge_s": _a(600.0), "sustained_h": _a(1.0), "scenarios": ["seamless"],
        "frequency_limits": {"transition": dict(_LIMITS), "steady_island": dict(_LIMITS)},
        "rocof_window_ms": _a(500.0), "gfm_margin": _a(0.1),
        "ride_through_storage": ["site_battery", *bess_new]}}


ISLAND_CONFIG["eh_h2_hub"] = _h2_hub_island(["bess_new"], ["wind_new"], ["solar_new"])


def _staged_island(cfg: dict) -> dict:
    """The single-year island sidecar with the expansion candidates renamed
    per period (``genset_new_<y>``, ``bess_new_<y>``)."""
    import copy

    out = copy.deepcopy(cfg)
    units = out["units"]
    gen, bess = units.pop("genset_new"), units.pop("bess_new")
    for y, _it, _pue in DC_PHASES:
        units[f"genset_new_{y}"] = copy.deepcopy(gen)
        units[f"bess_new_{y}"] = copy.deepcopy(bess)
    out["requirements"]["ride_through_storage"] = [f"bess_new_{y}" for y, _it, _pue in DC_PHASES]
    # The halls are fitted out in smaller steps: 2.5 MW pickup blocks (the
    # single-year campus's 5 MW blocks need 36.7 MW more genset inertia in
    # every period, whatever its load, so the plan could not stage).
    out["requirements"]["pickup_blocks"] = {"value": [2.5] * 22, "source": "assumed"}
    return out


ISLAND_CONFIG["eh_datacenter_staged"] = _staged_island(ISLAND_CONFIG["eh_datacenter"])


def _register_staged_hubs() -> None:
    """The staged H2 hub and microgrid (``eh_staged_hubs.py``, generic-site
    E2E H1/M1) add themselves to the registries above. Loaded by file path
    like this module; it gets a view of this module's names, never imports
    it."""
    import importlib.util
    import pathlib
    import types

    path = pathlib.Path(__file__).resolve().with_name("eh_staged_hubs.py")
    if not path.is_file():           # this module copied on its own (a test's tmp dir)
        return
    spec = importlib.util.spec_from_file_location("_eh_staged_hubs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.register(types.SimpleNamespace(**globals()))


_register_staged_hubs()

# Sidecar file names copied by POST /api/projects/from_template/<id>.
META_FILE = "eh_template.json"
STRESS_FILE = "adequacy_stress_scenarios.json"
SOLVER_FILE = "solver_config.json"
SIDECAR_FILES = (META_FILE, STRESS_FILE, SOLVER_FILE)
# Written only for the templates in ISLAND_CONFIG.
ISLAND_FILE = "island_config.json"
# services.adequacy.stress.SCHEMA — not imported, so this module loads
# without the backend's packages at import time. A test pins the two equal.
SCHEMA = 1


def write_sidecars(out_dir, template_id: str) -> None:
    """Write the template's metadata and stress registry beside network.nc,
    and its island sidecar when it has one."""
    import json
    import pathlib

    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / META_FILE).write_text(
        json.dumps(TEMPLATE_META[template_id], indent=2, sort_keys=True) + "\n")
    (out_dir / STRESS_FILE).write_text(json.dumps(
        {"__schema__": SCHEMA, "scenarios": STRESS_SCENARIOS[template_id]},
        indent=2, sort_keys=True) + "\n")
    (out_dir / SOLVER_FILE).write_text(
        json.dumps(solver_config(template_id), indent=2, sort_keys=True) + "\n")
    if template_id in ISLAND_CONFIG:
        (out_dir / ISLAND_FILE).write_text(
            json.dumps(ISLAND_CONFIG[template_id], indent=2, sort_keys=True) + "\n")
