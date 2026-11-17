"""
Asset types: the kinds of thing a user places (spec expert-catalogue §3.3).

The rows are moved, as data, from `frontend/src/utils/assetTypes.ts` `TYPES`
(same ids, same order: the first type with a matching rule wins). The glyph
code and the canvases stay in the frontend; slice 04 switches them to read
these rows. Until then `tests/test_catalogue_asset_types.py` pins the ids,
order, labels and icons to the TypeScript table so the two cannot drift.

Patterns are the TypeScript regex sources, matched case-insensitively.
`colour` is a token name, never a hex (the tokens land with THEME-LIGHT /
slice 04). Several asset types map onto one Component class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.asset_schema.functions import Function

CATEGORIES = ("supply", "storage", "conversion", "demand", "network", "site")

# The regex sources of frontend/src/utils/assetTypes.ts and busCarriers.ts.
H2_BUS = r"^(h2|hydrogen|h2[ _]pipeline)$"
HEAT_BUS = r"heat"
GAS_BUS = r"gas|biomass|^(oil|fuel)$"
H2_CARRIER = r"^(h2|hydrogen|h₂)([ _].*)?$"
GENSET_CARRIERS = r"^(gas|diesel|oil|biogas|biomass)$"
NON_ELECTRICAL_BUS = f"{H2_BUS}|{HEAT_BUS}|{GAS_BUS}"
SOLAR_PV = r"^(?!.*thermal)(?=.*(solar|(^|[^a-z])pv([^a-z]|$)))"
OFFTAKE = r"(^|[^a-z])(h2|hydrogen)([^a-z]|$)|heat|gas"
BATTERY = r"batter|li-?ion|bess"
ELECTRICAL = r"^(ac|dc|electricity|electric|el|power)?$"
NEVER = r"(?!)"


@dataclass(frozen=True)
class Port:
    id: str            # bus, bus0, bus1, bus2
    plain_name: str
    carrier: str = "any"
    side: str = ""     # left | right | top | bottom; empty: the canvas decides

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "plain_name": self.plain_name, "model_name": self.id,
                "carrier": self.carrier, "side": self.side}


@dataclass(frozen=True)
class Match:
    component: str
    carrier: str | None = None                       # regex on the component's carrier
    far_carrier: tuple[str, str] | None = None       # (port, regex on that bus's carrier)
    has_bus2: bool = False
    port: str | None = None
    generic_unless: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"component": self.component}
        if self.carrier is not None:
            d["carrier"] = self.carrier
        if self.far_carrier is not None:
            d["far_carrier"] = {"port": self.far_carrier[0], "carrier": self.far_carrier[1]}
        if self.has_bus2:
            d["has_bus2"] = True
        if self.port is not None:
            d["port"] = self.port
        if self.generic_unless is not None:
            d["generic_unless"] = self.generic_unless
        return d


@dataclass(frozen=True)
class AssetTypeSpec:
    id: str
    plain_name: str
    plural: str
    category: str
    icon: str
    match: tuple[Match, ...]
    ports: tuple[Port, ...] = ()
    symbols: dict[str, str] = field(default_factory=dict, hash=False)   # {sld, map, site3d}; absent: category default
    only_for: tuple[str, ...] = ()       # a function-only type shows only through that lens or once placed
    internal: bool = False               # hidden from lists, palette and canvases only (never from results)

    @property
    def components(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(m.component for m in self.match))

    @property
    def colour(self) -> str:
        return "--color-asset-" + "".join("-" + c.lower() if c.isupper() else c for c in self.id)


_ONE = (Port("bus", "Connected to"),)
_TWO = (Port("bus0", "From"), Port("bus1", "To"))


def _gen(id_, name, plural, icon, carrier=None, unless=None):
    return AssetTypeSpec(id_, name, plural, "supply", icon,
                         (Match("Generator", carrier=carrier, generic_unless=unless),), _ONE)


ASSET_TYPES: tuple[AssetTypeSpec, ...] = (
    # Generators
    _gen("pvRoof", "Rooftop PV", "Rooftop PV", "sun", carrier=r"rooftop"),
    _gen("pv", "PV field", "PV fields", "sun", carrier=SOLAR_PV),
    _gen("wind", "Wind turbines", "Wind turbines", "renewable", carrier=r"wind"),
    _gen("gasTurbine", "Gas turbine plant", "Gas turbine plants", "thermal", carrier=r"^(ccgt|ocgt)$"),
    # The Generator fallback: engine gensets, or a generic plant block named by its carrier.
    _gen("thermal", "Engine gensets", "Engine gensets", "thermal", unless=GENSET_CARRIERS),
    # Storage
    AssetTypeSpec("flywheel", "Flywheels", "Flywheels", "storage", "flywheel",
                  (Match("StorageUnit", carrier=r"flywheel"),), _ONE),
    AssetTypeSpec("pumpedHydro", "Pumped hydro", "Pumped hydro plants", "storage", "psh",
                  (Match("StorageUnit", carrier=r"^(hydro|phs|psh|pumped.?hydro)$"),), _ONE),
    AssetTypeSpec("caes", "Compressed air", "Compressed air stores", "storage", "caes",
                  (Match("StorageUnit", carrier=r"^(air|caes|compressed.?air)$"),), _ONE),
    AssetTypeSpec("h2store", "H₂ storage", "H₂ stores", "storage", "hydrogen",
                  (Match("Store", carrier=H2_CARRIER), Match("StorageUnit", carrier=H2_CARRIER)), _ONE),
    AssetTypeSpec("thermalStore", "Thermal store", "Thermal stores", "storage", "thermal_storage",
                  (Match("Store", carrier=r"heat"), Match("StorageUnit", carrier=r"heat|thermal")), _ONE),
    AssetTypeSpec("bess", "Battery storage", "Batteries", "storage", "battery",
                  (Match("Store", carrier=BATTERY), Match("StorageUnit", generic_unless=BATTERY)), _ONE),
    AssetTypeSpec("store", "Energy store", "Energy stores", "storage", "box",
                  (Match("Store", generic_unless=NEVER),), _ONE),
    # Conversion (Links)
    AssetTypeSpec("electrolyser", "Electrolyser", "Electrolysers", "conversion", "electrolyzer",
                  (Match("Link", carrier=r"electroly", port="bus0"),
                   Match("Link", carrier=r"^h2$", far_carrier=("bus1", H2_BUS), port="bus0")),
                  (Port("bus0", "Power in", "AC"), Port("bus1", "Hydrogen out", "H2"))),
    AssetTypeSpec("fuelCell", "Fuel cells", "Fuel cells", "conversion", "fuel_cell",
                  (Match("Link", carrier=r"fuel.?cell", port="bus1"),
                   Match("Link", carrier=r"^h2$", far_carrier=("bus0", H2_BUS), port="bus1")),
                  (Port("bus0", "Hydrogen in", "H2"), Port("bus1", "Power out", "AC"))),
    AssetTypeSpec("heatPump", "Heat pumps / e-boilers", "Heat pumps / e-boilers", "conversion", "power_to_heat",
                  (Match("Link", carrier=r"heat.?pump|resistive|electric.?boiler|^e-?boiler$", port="bus0"),),
                  (Port("bus0", "Power in", "AC"), Port("bus1", "Heat out", "heat"))),
    AssetTypeSpec("chp", "CHP plant", "CHP plants", "conversion", "chp",
                  (Match("Link", carrier=r"^(gas|biogas|.*\bchp\b.*)$", has_bus2=True, port="bus1"),),
                  (Port("bus0", "Fuel in", "gas"), Port("bus1", "Power out", "AC"), Port("bus2", "Heat out", "heat"))),
    # Demand
    AssetTypeSpec("offtake", "Offtake (H₂ / heat)", "Offtakes", "demand", "factory",
                  (Match("Load", carrier=OFFTAKE),), _ONE),
    AssetTypeSpec("load", "Data hall", "Data halls", "demand", "load_elec",
                  (Match("Link", carrier=r"data.?cent", port="bus0"), Match("Load", generic_unless=ELECTRICAL)),
                  _ONE),
    # Infrastructure
    AssetTypeSpec("transformer", "Transformer", "Transformers", "network", "transformer",
                  (Match("Transformer"),), (Port("bus0", "High-voltage side", "AC"), Port("bus1", "Low-voltage side", "AC"))),
    AssetTypeSpec("feeder", "Feeder bay", "Feeder bays", "network", "line",
                  (Match("Line"), Match("Link", generic_unless=ELECTRICAL)), _TWO),
    AssetTypeSpec("manifold", "Pipe manifold", "Pipe manifolds", "network", "waypoints",
                  (Match("Bus", carrier=NON_ELECTRICAL_BUS),)),
    AssetTypeSpec("switchyard", "Switchyard", "Switchyards", "network", "bus",
                  (Match("Bus", generic_unless=ELECTRICAL),)),
)

#: The functions each category takes part in (an asset type may narrow this with `only_for`).
_CATEGORY_FUNCTIONS: dict[str, tuple[Function, ...]] = {
    "supply": (Function.DISPATCH, Function.EXPANSION, Function.LOAD_FLOW, Function.SHORT_CIRCUIT,
               Function.GRID_COMPLIANCE, Function.ADEQUACY, Function.FINANCE),
    "storage": (Function.DISPATCH, Function.EXPANSION, Function.LOAD_FLOW, Function.SHORT_CIRCUIT,
                Function.GRID_COMPLIANCE, Function.ADEQUACY, Function.FINANCE),
    "conversion": (Function.DISPATCH, Function.EXPANSION, Function.ADEQUACY, Function.FINANCE),
    "demand": (Function.DISPATCH, Function.LOAD_FLOW, Function.GRID_COMPLIANCE, Function.ADEQUACY),
    "network": (Function.DISPATCH, Function.EXPANSION, Function.LOAD_FLOW, Function.SHORT_CIRCUIT,
                Function.GRID_COMPLIANCE, Function.ADEQUACY, Function.FINANCE),
    "site": (),
}


def functions_of(t: AssetTypeSpec) -> tuple[str, ...]:
    if t.only_for:
        return t.only_for
    return tuple(f.value for f in _CATEGORY_FUNCTIONS[t.category])

