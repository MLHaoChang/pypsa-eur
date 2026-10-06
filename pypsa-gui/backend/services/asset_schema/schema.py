"""
Which investment parts each component class has, and where they are stored.

Investment is typed as overnight (upfront) **parts**, never as an annuity. A single-part asset maps onto PyPSA's
own columns (`overnight_cost`, `lifetime`, `fom_cost`) and PyPSA annuitises it. A composite asset has parts with
different lifetimes, which PyPSA cannot hold in one `overnight_cost`, so each part has its own custom columns
(same mechanism as `outage_rate_value`: a column on the component frame, round-tripped through netCDF) and the
annual `capital_cost` is derived from them (`derive`).

Today the only composite class is the StorageUnit battery: a power part (inverter, per MW) and an energy part
(storage block, per MWh, scaled by `max_hours` to a per-MW figure). HVDC links and Store + Link batteries follow
in a later step (plan §10 S4).
"""
from __future__ import annotations

from dataclasses import dataclass

# The three numbers every part carries. `overnight` is in the stored unit, `lifetime` in years, `fom_share` a
# fraction of `overnight` per year.
PART_FIELDS = ("overnight", "lifetime", "fom_share")


@dataclass(frozen=True)
class PartSpec:
    name: str
    basis: str            # what one unit of `overnight` buys: "per_MW", "per_MWh", ...
    prefix: str           # custom column prefix, e.g. "inv_power" -> inv_power_overnight
    scale_by: str | None  # component column that turns this part into "per unit of p_nom" (max_hours for energy)
    display_unit: str     # what catalogues publish and the UI shows
    stored_unit: str      # what the column holds

    def column(self, field: str) -> str:
        return f"{self.prefix}_{field}"


COMPOSITE: dict[str, tuple[PartSpec, ...]] = {
    "StorageUnit": (
        PartSpec("power", "per_MW", "inv_power", None, "EUR/kW", "EUR/MW"),
        PartSpec("energy", "per_MWh", "inv_energy", "max_hours", "EUR/kWh", "EUR/MWh"),
    ),
}

CLASS_ATTR = {
    "Generator": "generators",
    "StorageUnit": "storage_units",
    "Store": "stores",
    "Link": "links",
    "Line": "lines",
    "Transformer": "transformers",
}


def composite_parts(component_class: str) -> tuple[PartSpec, ...]:
    """The parts of a composite class; empty for a single-part class."""
    return COMPOSITE.get(component_class, ())


def part_columns(component_class: str) -> list[str]:
    """Every custom column a composite class stores its parts in."""
    return [p.column(f) for p in composite_parts(component_class) for f in PART_FIELDS]
