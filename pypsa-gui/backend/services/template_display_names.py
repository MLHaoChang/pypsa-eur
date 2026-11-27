"""Plain display names for the components a project template ships (T1).

The key is ``(template id, component class, component id)``. The id stays
the model's own name: a display name is a label shown beside it, never
written into the network, a sidecar or a project file. That keeps the
template builds, ``_network_input_fingerprint`` and the save bytes as
they were.

A row's id may hold one placeholder, for the components a builder names
per planning period or per unit:

- ``{y}``: a planning year, 2025 to 2060, as the planning bounds allow;
- ``{n}``: a unit number, 1 or more.

The name gives the same placeholder back, so ``genset_new_2030`` reads
"New genset (2030)".

The lookup is static, so a component deleted and added again under the
same class and id gets its name back. A user component with that id
under another class gets none, and so does a renamed one: the new id is
the user's own word. A project with no recorded template, or a template
with no table here, falls back to the id.

The grid benchmarks are exempt (``EXEMPT``): their ids are the
benchmark's own bus and line names.
"""

from __future__ import annotations

import json
import pathlib
import re

# The grid benchmark templates: their ids are the standard benchmark bus
# and line names, so a second word would only hide them.
EXEMPT: dict[str, str] = {
    "3bus": "grid benchmark: ids are the benchmark's own bus and line names",
    "ieee14": "grid benchmark: ids are the benchmark's own bus and line names",
    "belgium": "grid benchmark: ids are the benchmark's own bus and line names",
    "ieee39": "grid benchmark: ids are the benchmark's own bus and line names",
}

YEAR_MIN, YEAR_MAX = 2025, 2060

Rows = dict[tuple[str, str], str]


def _rows(**by_class: dict[str, str]) -> Rows:
    return {(cls, cid): name for cls, names in by_class.items() for cid, name in names.items()}


# ── The data center (single year, staged) ───────────────────────────────────

_DC_COMMON = _rows(
    Bus={"grid": "Grid", "dc_mv": "Site MV switchboard", "it_bus": "IT power bus"},
    Generator={
        "grid_supply": "Grid supply",
        "genset_1": "Genset 1", "genset_2": "Genset 2",
        "genset_3": "Genset 3", "genset_4": "Genset 4",
        "rooftop_pv": "Rooftop solar",
    },
    Link={"grid_import": "Grid connection", "site_transformer": "Site transformer"},
    Load={"it_load": "IT load", "cooling": "Cooling", "offices": "Offices"},
    StorageUnit={
        "ups_battery": "UPS battery",
        "backup_bess_{n}": "Backup battery {n}",
        "backup_bess_spare_{n}": "Spare backup battery {n}",
    },
)

_DC = {**_DC_COMMON, **_rows(
    Generator={"genset_new": "New genset"},
    StorageUnit={"bess_new": "New battery"},
)}

_DC_STAGED = {**_DC_COMMON, **_rows(
    Generator={"genset_new_{y}": "New genset ({y})"},
    StorageUnit={"bess_new_{y}": "New battery ({y})"},
)}

# ── The hydrogen hub and the ammonia hub built on it ────────────────────────

_H2_COMMON = _rows(
    Bus={"grid": "Grid", "hub": "Hub power bus", "h2": "Hydrogen"},
    Generator={
        "grid_supply": "Grid supply",
        "solar_park": "Solar park",
        "wind_farm": "Wind farm",
    },
    Link={"grid_import": "Grid connection", "electrolyser": "Electrolyser"},
    Load={"process_load": "Process load"},
    StorageUnit={"site_battery": "Site battery"},
    Store={"h2_storage": "Hydrogen storage"},
)

_H2 = {**_H2_COMMON, **_rows(
    Generator={"solar_new": "New solar", "wind_new": "New wind"},
    Link={"fuel_cell": "Fuel cell"},
    Load={"h2_offtake": "Hydrogen offtake"},
    StorageUnit={"bess_new": "New battery"},
)}

_H2_STAGED_NEW = _rows(
    Generator={"solar_new_{y}": "New solar ({y})", "wind_new_{y}": "New wind ({y})"},
    Link={"electrolyser_new_{y}": "New electrolyser ({y})"},
    StorageUnit={"bess_new_{y}": "New battery ({y})"},
)

_H2_STAGED = {**_H2_COMMON, **_H2_STAGED_NEW, **_rows(
    Link={"fuel_cell": "Fuel cell"},
    Load={"h2_offtake": "Hydrogen offtake"},
)}

_AMMONIA = {**_H2_COMMON, **_H2_STAGED_NEW, **_rows(
    Bus={"nh3": "Ammonia"},
    Generator={"__buy_nh3": "Bought-in ammonia", "__lost_nh3": "Ammonia shortfall"},
    Link={"haber_bosch": "Ammonia synthesis (Haber-Bosch)"},
    Load={"nh3_delivery": "Ammonia delivery"},
    Store={"nh3_tank": "Ammonia tank"},
)}

# ── The island microgrid (single year, staged) ──────────────────────────────

_MG_COMMON = _rows(
    Bus={"mainland": "Mainland grid", "island": "Island grid", "hospital": "Hospital"},
    Generator={
        "mainland_supply": "Mainland supply",
        "diesel_1": "Diesel generator 1", "diesel_2": "Diesel generator 2",
        "diesel_3": "Diesel generator 3",
        "pv_plant": "Solar plant", "wind_turbines": "Wind turbines",
    },
    Link={"subsea_tie": "Subsea cable to the mainland", "hospital_feeder": "Hospital feeder"},
    Load={"residential": "Homes", "hospital_load": "Hospital load"},
    StorageUnit={"island_battery": "Island battery"},
)

_MG = {**_MG_COMMON, **_rows(
    Generator={"diesel_new": "New diesel generator"},
    StorageUnit={"bess_new": "New battery"},
)}

_MG_STAGED = {**_MG_COMMON, **_rows(
    Generator={
        "diesel_new_{y}": "New diesel generator ({y})",
        "pv_new_{y}": "New solar ({y})",
        "wind_new_{y}": "New wind ({y})",
    },
    StorageUnit={"bess_new_{y}": "New battery ({y})"},
)}

# ── The generic site ────────────────────────────────────────────────────────

_SITE = _rows(
    Bus={"grid": "Grid", "site_mv": "Site MV switchboard", "critical": "Critical power bus"},
    Generator={
        "grid_supply": "Grid supply",
        "genset_{n}": "Genset {n}",
        "site_pv": "Site solar",
        "genset_new_{y}": "New genset ({y})",
        "pv_new_{y}": "New solar ({y})",
    },
    Link={"grid_import": "Grid connection", "critical_feeder": "Critical feeder"},
    Load={"general_load": "General load", "critical_load": "Critical load"},
    StorageUnit={
        "site_battery": "Site battery",
        "ups_battery": "UPS battery",
        "bess_new_{y}": "New battery ({y})",
    },
)

# ── The mining concentrator ─────────────────────────────────────────────────

_CONCENTRATOR = _rows(
    Bus={
        "grid": "Grid", "site": "Site power bus",
        "ore_rom": "Run-of-mine ore", "ore_fine": "Crushed ore",
        "slurry": "Ground slurry", "concentrate": "Concentrate",
    },
    Generator={
        "grid_supply": "Grid supply",
        "mine": "Mine output",
        "__lost_concentrate": "Concentrate shortfall",
        "pv_new_{y}": "New solar ({y})",
    },
    Link={
        "grid_import": "Grid connection",
        "crusher": "Crusher",
        "mill_1": "Mill 1", "mill_2": "Mill 2", "mill_3": "Mill 3",
        "flotation": "Flotation",
        "flotation_{y}": "Flotation ({y})",
    },
    Load={"shipments": "Concentrate shipments", "site_services": "Site services"},
    StorageUnit={"bess_new_{y}": "New battery ({y})"},
    Store={
        "coarse_ore_stockpile": "Coarse ore stockpile",
        "coarse_ore_stockpile_new_{y}": "New coarse ore stockpile ({y})",
        "concentrate_shed": "Concentrate shed",
    },
)

# ── The renewables IPP ──────────────────────────────────────────────────────

_IPP = _rows(
    Bus={"grid": "Grid", "poi": "Point of interconnection"},
    Generator={"grid": "Grid supply", "wind_farm": "Wind farm", "pv_plant": "Solar plant"},
    Link={"grid_import": "Grid import", "grid_export": "Grid export"},
    StorageUnit={"bess": "Battery"},
)

DISPLAY_NAMES: dict[str, Rows] = {
    "eh_datacenter": _DC,
    "eh_datacenter_staged": _DC_STAGED,
    "eh_h2_hub": _H2,
    "eh_h2_hub_staged": _H2_STAGED,
    "eh_ammonia_staged": _AMMONIA,
    "eh_microgrid": _MG,
    "eh_microgrid_staged": _MG_STAGED,
    "eh_site": _SITE,
    "eh_concentrator_staged": _CONCENTRATOR,
    "eh_ipp_hybrid": _IPP,
}

_PLACEHOLDER = {"{y}": r"(20[2-6]\d)", "{n}": r"([1-9]\d*)"}


def _pattern(cid: str) -> tuple[re.Pattern[str], str] | None:
    for token, rx in _PLACEHOLDER.items():
        if token in cid:
            head, _, tail = cid.partition(token)
            return re.compile(re.escape(head) + rx + re.escape(tail)), token
    return None


def name_for(template_id: str | None, component_class: str, component_id: str) -> str | None:
    """The template's display name for one component, or None when it has none."""
    rows = DISPLAY_NAMES.get(template_id) if isinstance(template_id, str) else None
    if not rows:
        return None
    exact = rows.get((component_class, component_id))
    if exact is not None:
        return exact
    for (cls, cid), name in rows.items():
        if cls != component_class:
            continue
        found = _pattern(cid)
        if found is None:
            continue
        rx, token = found
        m = rx.fullmatch(component_id)
        if m is None:
            continue
        value = m.group(1)
        if token == "{y}" and not YEAR_MIN <= int(value) <= YEAR_MAX:
            continue
        return name.replace(token, value)
    return None


def label_for(template_id: str | None, component_class: str, component_id: str) -> str:
    """The display name, falling back to the id itself (T1)."""
    return name_for(template_id, component_class, component_id) or component_id


def recorded_template_id(project_dir: str | pathlib.Path | None) -> str | None:
    """The template id the project's ``metadata.json`` records, or None.

    Fails closed and quietly: no directory, a missing, unreadable or corrupt
    file, a non-object body or a non-string id all read as "no template"."""
    if not project_dir:
        return None
    try:
        meta = json.loads((pathlib.Path(project_dir) / "metadata.json").read_text("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    tid = meta.get("template_id") if isinstance(meta, dict) else None
    return tid if isinstance(tid, str) else None


# The classes a template ships components in, by their network attribute.
COMPONENT_LISTS: dict[str, str] = {
    "Bus": "buses", "Generator": "generators", "Load": "loads", "Link": "links",
    "Store": "stores", "StorageUnit": "storage_units", "Line": "lines",
    "Transformer": "transformers", "ShuntImpedance": "shunt_impedances",
}


def names_for_network(n, project_dir: str | pathlib.Path | None) -> dict[str, dict[str, str]]:
    """``{class: {id: name}}`` for the network's components that have a name.

    Empty for an unbound network, a project with no recorded or no known
    template, and a metadata file that cannot be read."""
    tid = recorded_template_id(project_dir)
    out: dict[str, dict[str, str]] = {}
    for cls, attr in COMPONENT_LISTS.items():
        frame = getattr(n, attr, None)
        if frame is None:
            continue
        named = {str(cid): name for cid in frame.index
                 if (name := name_for(tid, cls, str(cid))) is not None}
        if named:
            out[cls] = named
    return out
