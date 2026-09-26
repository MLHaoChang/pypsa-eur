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

Horizon: one representative 168 h week weighted 8760/168 per snapshot, so
``nyears ≈ 1`` (no CAPEX scaling warning) and every MTTR here (≤ 72 h) fits
the MC's MTTR floor (P11, decision Q2).
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
    n.snapshot_weightings.loc[:, :] = WEIGHT
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


# ── Data Center Energy Hub (weak_flexible) ─────────────────────────────────


def build_eh_datacenter() -> pypsa.Network:
    """A 50 MW hyperscale campus behind a weak, capped grid connection.

    Critical IT sits on its own bus behind the site transformer (a near
    loss-free Link, so the P16 per-Load priority is exact); cooling and
    offices share the MV bus and are the non-critical demand. A UPS battery
    rides on the IT bus, a four-unit gas genset fleet on the MV bus.
    """
    n = _base("Data Center Energy Hub")
    for c, co2 in (("AC", 0.0), ("gas", 0.20), ("solar", 0.0),
                   ("battery", 0.0), ("grid", 0.35)):
        n.add("Carrier", c, co2_emissions=co2)
    n.add("Bus", "grid", v_nom=132.0, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "dc_mv", v_nom=33.0, carrier="AC", x=2.0, y=0.0)
    n.add("Bus", "it_bus", v_nom=11.0, carrier="AC", x=3.0, y=0.6)

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
    n.add("StorageUnit", "ups_battery", bus="it_bus", carrier="battery",
          p_nom=15.0, max_hours=1.0, efficiency_store=0.95,
          efficiency_dispatch=0.95, cyclic_state_of_charge=True)

    # Candidates the ENS-capped expansion may build (annualised €/MW/yr).
    n.add("Generator", "genset_new", bus="dc_mv", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=40.0, capital_cost=62_000.0,
          marginal_cost=165.0)
    _outage(n, "generators", "genset_new", 0.03, 24.0)
    n.add("StorageUnit", "bess_new", bus="it_bus", carrier="battery",
          p_nom=0.0, p_nom_extendable=True, p_nom_max=30.0, max_hours=4.0,
          capital_cost=48_000.0, efficiency_store=0.95,
          efficiency_dispatch=0.95, cyclic_state_of_charge=True)

    _tag_columns(n)
    n.buses.at["grid", "eh_poc"] = True
    # SCR gate inputs at the PoC: a weak 250 MVA fault level against ~30 MVA
    # of inverter-based resources (PV + UPS/BESS inverters).
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
    return n


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
    n.add("Bus", "hub", v_nom=110.0, carrier="AC", x=2.0, y=0.0)
    n.add("Bus", "h2", carrier="H2", x=3.0, y=-0.8)

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
          p_nom_extendable=True, p_nom_max=300.0, capital_cost=120_000.0)
    n.add("Generator", "solar_new", bus="hub", carrier="solar", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=150.0, capital_cost=45_000.0)
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
          capital_cost=90_000.0, efficiency=0.50, carrier="fuel cell")
    n.add("Carrier", "battery", co2_emissions=0.0)
    n.add("StorageUnit", "site_battery", bus="hub", carrier="battery",
          p_nom=20.0, max_hours=2.0, efficiency_store=0.95,
          efficiency_dispatch=0.95, cyclic_state_of_charge=True)
    n.add("StorageUnit", "bess_new", bus="hub", carrier="battery", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=200.0, max_hours=6.0,
          capital_cost=48_000.0, efficiency_store=0.95,
          efficiency_dispatch=0.95, cyclic_state_of_charge=True)

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
    n.add("Bus", "island", v_nom=11.0, carrier="AC", x=2.0, y=0.0)
    n.add("Bus", "hospital", v_nom=11.0, carrier="AC", x=2.8, y=0.7)

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
          p_nom_extendable=True, p_nom_max=12.0, capital_cost=45_000.0,
          marginal_cost=260.0)
    _outage(n, "generators", "diesel_new", 0.05, 48.0)
    n.add("StorageUnit", "bess_new", bus="island", carrier="battery",
          p_nom=0.0, p_nom_extendable=True, p_nom_max=15.0, max_hours=4.0,
          capital_cost=48_000.0, efficiency_store=0.94,
          efficiency_dispatch=0.94, cyclic_state_of_charge=True)

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

# Sidecar file names copied by POST /api/projects/from_template/<id>.
META_FILE = "eh_template.json"
STRESS_FILE = "adequacy_stress_scenarios.json"
SOLVER_FILE = "solver_config.json"
SIDECAR_FILES = (META_FILE, STRESS_FILE, SOLVER_FILE)
# services.adequacy.stress.SCHEMA — not imported: _build.py runs with only
# this directory on sys.path. A test pins the two equal.
SCHEMA = 1


def write_sidecars(out_dir, template_id: str) -> None:
    """Write the template's metadata and stress registry beside network.nc."""
    import json
    import pathlib

    out_dir = pathlib.Path(out_dir)
    (out_dir / META_FILE).write_text(
        json.dumps(TEMPLATE_META[template_id], indent=2, sort_keys=True) + "\n")
    (out_dir / STRESS_FILE).write_text(json.dumps(
        {"__schema__": SCHEMA, "scenarios": STRESS_SCENARIOS[template_id]},
        indent=2, sort_keys=True) + "\n")
    (out_dir / SOLVER_FILE).write_text(
        json.dumps(SOLVER_CONFIG, indent=2, sort_keys=True) + "\n")
