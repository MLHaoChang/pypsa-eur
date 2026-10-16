"""
Curated PyPSA-Eur carrier catalog used to auto-populate n.carriers when a
generator/storage/load update references a carrier not yet in the table.

Keeping color + nice_name + co2_emissions on the carrier row makes plots and
statistics() output look correct without forcing the user to manually create
the carrier under the Carrier tab first. Names + values mirror the frontend
catalog at frontend/src/utils/carrierCatalog.ts — keep both in sync.
"""

from __future__ import annotations

CARRIER_CATALOG: dict[str, dict] = {
    # Electricity
    "AC":             {"nice_name": "AC",                 "color": "#70af1d", "co2_emissions": 0.0},
    "DC":             {"nice_name": "DC",                 "color": "#8a1caf", "co2_emissions": 0.0},
    # Renewables
    "onwind":         {"nice_name": "Onshore Wind",       "color": "#235ebc", "co2_emissions": 0.0},
    "offwind-ac":     {"nice_name": "Offshore Wind (AC)", "color": "#6895dd", "co2_emissions": 0.0},
    "offwind-dc":     {"nice_name": "Offshore Wind (DC)", "color": "#74c6f2", "co2_emissions": 0.0},
    "solar":          {"nice_name": "Solar PV (utility)", "color": "#f9d002", "co2_emissions": 0.0},
    "solar-rooftop":  {"nice_name": "Solar PV (rooftop)", "color": "#ffef60", "co2_emissions": 0.0},
    "ror":            {"nice_name": "Run-of-River",       "color": "#3dbfb0", "co2_emissions": 0.0},
    "hydro":          {"nice_name": "Hydro Reservoir",    "color": "#298c81", "co2_emissions": 0.0},
    "geothermal":     {"nice_name": "Geothermal",         "color": "#ba91b1", "co2_emissions": 0.0},
    "biomass":        {"nice_name": "Biomass",            "color": "#0c6013", "co2_emissions": 0.0},
    "wave":           {"nice_name": "Wave",               "color": "#28abc8", "co2_emissions": 0.0},
    # Thermal & nuclear (co2_emissions in tCO2/MWh_thermal)
    "CCGT":           {"nice_name": "CCGT (Gas)",  "color": "#a85522", "co2_emissions": 0.187},
    "OCGT":           {"nice_name": "OCGT (Gas)",  "color": "#d35050", "co2_emissions": 0.187},
    "coal":           {"nice_name": "Hard Coal",   "color": "#545454", "co2_emissions": 0.34},
    "lignite":        {"nice_name": "Lignite",     "color": "#826837", "co2_emissions": 0.41},
    "oil":            {"nice_name": "Oil",         "color": "#262626", "co2_emissions": 0.267},
    "nuclear":        {"nice_name": "Nuclear",     "color": "#ff8c00", "co2_emissions": 0.0},
    # Fuel-level carriers. `gas` is an ordinary PyPSA-Eur carrier name, and its
    # absence is what made a real project report 0 tCO2 for a 300 MW gas plant:
    # ensure_carrier fell back to co2_emissions=0.0 and nothing warned. The
    # factors match this file's own CCGT/OCGT (natural gas) and oil, rather
    # than being invented — a wrong factor is worse than a missing one because
    # it looks authoritative.
    "gas":            {"nice_name": "Natural Gas", "color": "#e0986c", "co2_emissions": 0.187},
    "diesel":         {"nice_name": "Diesel",      "color": "#3b3b3b", "co2_emissions": 0.267},
    # Hydrogen & fuels
    "H2":             {"nice_name": "Hydrogen",         "color": "#dd2727", "co2_emissions": 0.0},
    "H2 electrolysis": {"nice_name": "H2 Electrolysis", "color": "#ff6361", "co2_emissions": 0.0},
    "H2 fuel cell":   {"nice_name": "H2 Fuel Cell",     "color": "#a8463d", "co2_emissions": 0.0},
    # Heat. The creation form's heat-bus pickers match any bus carrier naming "heat"; without a catalogue entry
    # a user could not pick the carrier the filter looks for.
    "heat":           {"nice_name": "Heat",             "color": "#cc1e1e", "co2_emissions": 0.0},
    # Process products (grid-to-process, design D2). `unit` is what a bus of this carrier counts in: MWh (LHV) for
    # an energy product, t for a material. Only these entries carry a unit; the rest read as MWh.
    "NH3":            {"nice_name": "Ammonia",          "color": "#1f9d8b", "co2_emissions": 0.0, "unit": "MWh"},
    "N2":             {"nice_name": "Nitrogen",         "color": "#8fb8c9", "co2_emissions": 0.0, "unit": "t"},
    "ore":            {"nice_name": "Ore",              "color": "#8c6d4f", "co2_emissions": 0.0, "unit": "t"},
    "concentrate":    {"nice_name": "Concentrate",      "color": "#c9a227", "co2_emissions": 0.0, "unit": "t"},
    # Storage
    "battery":        {"nice_name": "Battery",      "color": "#ace37f", "co2_emissions": 0.0},
    "PHS":            {"nice_name": "Pumped Hydro", "color": "#51dbcc", "co2_emissions": 0.0},
}

# Energy per tonne (MWh LHV / t) of an energy product, for showing it in tonnes. NH3: upstream PyPSA-Eur's
# `MWh_NH3_per_tNH3` (config/config.default.yaml:1015).
PRODUCT_MWH_PER_T: dict[str, float] = {"NH3": 5.166}


def bus_tonne_factor(n, bus_name: str) -> float | None:
    """Tonnes per bus unit of a product bus: 1.0 for a bus counted in t, 1 / `mwh_per_t` for one counted in MWh.

    None (Unavailable, ADR-0001) for a bus that is no product bus, or an energy product with no tonne factor,
    never a made-up number.
    """
    buses = n.buses
    if bus_name not in buses.index:
        return None
    unit = str(buses.at[bus_name, "unit"]).strip() if "unit" in buses.columns else ""
    if unit == "t":
        return 1.0
    if unit == "MWh" and "mwh_per_t" in buses.columns:
        mwh_per_t = buses.at[bus_name, "mwh_per_t"]
        if mwh_per_t == mwh_per_t and float(mwh_per_t) > 0:
            return 1.0 / float(mwh_per_t)
    return None


def ensure_carrier(n, carrier_name: str) -> None:
    """
    If `carrier_name` is set and missing from n.carriers, add it with
    catalog defaults (or empty defaults if not in the catalog). Caller is
    expected to already hold PyPSAService.get_lock().
    """
    if not carrier_name:
        return
    if carrier_name in n.carriers.index:
        return
    # OPEN-ITEMS item 8: this CREATES a carrier, so its name is a new name. A
    # carrier the network already has is referenced, not re-checked (above).
    from services.component_names import require_valid_name

    require_valid_name(carrier_name, component_class="Carrier")
    meta = CARRIER_CATALOG.get(carrier_name, {"nice_name": carrier_name, "color": "", "co2_emissions": 0.0})
    n.add("Carrier", carrier_name, **meta)
    if "unit" in meta:
        # A first `unit` makes the column; the carriers that already existed must read "" (no unit), not NaN.
        n.carriers["unit"] = n.carriers["unit"].fillna("")
