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
    # Hydrogen & fuels
    "H2":             {"nice_name": "Hydrogen",         "color": "#dd2727", "co2_emissions": 0.0},
    "H2 electrolysis": {"nice_name": "H2 Electrolysis", "color": "#ff6361", "co2_emissions": 0.0},
    "H2 fuel cell":   {"nice_name": "H2 Fuel Cell",     "color": "#a8463d", "co2_emissions": 0.0},
    # Storage
    "battery":        {"nice_name": "Battery",      "color": "#ace37f", "co2_emissions": 0.0},
    "PHS":            {"nice_name": "Pumped Hydro", "color": "#51dbcc", "co2_emissions": 0.0},
}


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
    meta = CARRIER_CATALOG.get(carrier_name, {"nice_name": carrier_name, "color": "", "co2_emissions": 0.0})
    n.add("Carrier", carrier_name, **meta)
