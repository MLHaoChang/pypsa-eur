"""
Heat as a modelled product: temperature and pressure levels (grid-to-process issue 15, design §2.6).

A heat bus states the supply temperature it delivers at (`temperature_c`), a steam bus its header pressure
(`pressure_bar`, gauge, barg); a device states its reach on the Link (`max_supply_temp_c`, `max_pressure_bar`).
One bus per level; the unit stays MWh (thermal), as the catalogue counts energy. The validator compares the
numbers (`services/validation_service.py`, `_check_heat_levels`). The LP never reads these columns.

The bands (low / medium / high, LP / MP / HP) are display labels derived here from edges in the ledger. They are
never stored on a bus. The edges and every value of the builders below are **demo**: synthetic, ledgered as
`source: synthetic`, not a standard and not the paper's numbers (research note §7.4).

This module holds:
* `level_label`: the derived band label of a level bus;
* `add_heat_demand`: the band-wise heat demand input (a table of level, peak or annual MW_th and a load shape:
  flat while the unit runs, through the profile generator's `industrial` archetype, or a given series);
* `add_hybrid_supply`: a gas Link and an electric Link in parallel onto one level's bus, the gas Link committable
  through the process-unit commitment path (issue 03) and kept as backup;
* `build_two_level_heat_demo`: the generic two-level network of the issue's acceptance (no refinery template).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

# Upper band edges, demo (ledger below). A level at or above an edge is in the next band.
HEAT_BANDS_C: tuple[tuple[str, float], ...] = (
    ("low", 100.0),
    ("medium", 400.0),
    ("high", math.inf),
)
STEAM_CLASSES_BAR: tuple[tuple[str, float], ...] = (
    ("LP", 5.0),
    ("MP", 20.0),
    ("HP", math.inf),
)

DEMO_LEDGER: tuple[dict[str, Any], ...] = (
    {
        "entity": "heat bands",
        "field": "edges_c",
        "value": "low < 100 <= medium < 400 <= high",
        "unit": "°C",
        "source": "synthetic",
        "label": "demo",
        "note": "Display bands for heat levels; demo edges, not a standard. Replace with the client's own.",
    },
    {
        "entity": "steam classes",
        "field": "edges_bar",
        "value": "LP < 5 <= MP < 20 <= HP",
        "unit": "barg",
        "source": "synthetic",
        "label": "demo",
        "note": "Display classes for steam headers; demo edges, not a standard. Replace with the client's own.",
    },
    {
        "entity": "two-level heat demo",
        "field": "all values",
        "value": "see build_two_level_heat_demo",
        "unit": "",
        "source": "synthetic",
        "label": "demo",
        "note": "Demands, efficiencies, reaches, prices and commitment of the two-level demo are synthetic.",
    },
)


# The level columns of a Bus: a heat level wins when a bus states both.
LEVEL_COLUMNS: tuple[str, str] = ("temperature_c", "pressure_bar")


class HeatLevelError(ValueError):
    """A heat demand or supply input the builders refuse (a misaligned series, a missing level, a bad scale)."""


def _finite(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _band(value: float, edges: tuple[tuple[str, float], ...]) -> str:
    for name, upper in edges:
        if value < upper:
            return name
    return edges[-1][0]  # pragma: no cover - the last edge is +inf


def heat_band(temperature_c: float) -> str:
    """The demo band of a supply temperature: low, medium or high."""
    return _band(float(temperature_c), HEAT_BANDS_C)


def steam_class(pressure_bar: float) -> str:
    """The demo class of a header pressure: LP, MP or HP."""
    return _band(float(pressure_bar), STEAM_CLASSES_BAR)


def bus_level(n, bus: str) -> tuple[str, float] | None:
    """("temperature_c", value) or ("pressure_bar", value) of a level bus; None when it states no level.

    A heat level wins when a bus states both (a heat bus is checked by temperature)."""
    if bus not in n.buses.index:
        return None
    for col in LEVEL_COLUMNS:
        if col in n.buses.columns:
            v = _finite(n.buses.at[bus, col])
            if v is not None:
                return col, v
    return None


def served_sink_c(n, link: str) -> float | None:
    """Supply temperature (°C) of the heat band a Link's `bus1` delivers to; None when it serves no heat band."""
    if link not in n.links.index:
        return None
    level = bus_level(n, str(n.links.at[link, "bus1"]))
    return level[1] if level and level[0] == "temperature_c" else None


def level_label(n, bus: str) -> str | None:
    """The derived label of a level bus, e.g. "high band (demo edges) at 450 °C"; None when it has no level."""
    level = bus_level(n, bus)
    if level is None:
        return None
    col, v = level
    if col == "temperature_c":
        return f"{heat_band(v)} band (demo edges) at {v:g}\u00a0°C"
    return f"{steam_class(v)} steam (demo edges) at {v:g}\u00a0barg"


# ── Band-wise heat demand ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class HeatDemand:
    """One row of the band-wise demand table.

    `bus` is the level bus (created when missing, carrier `heat` or `steam`). State exactly one of
    `temperature_c` (a heat level) and `pressure_bar` (a steam level), and exactly one of `peak_mw` and
    `annual_mwh` (MW_th, MWh_th). `shape` is "flat" (flat while the unit runs: the profile generator's
    `industrial` archetype, continuous, off in `shutdown_weeks`) or a series on exactly `n.snapshots`, in MW_th
    (then the scale fields stay empty)."""

    bus: str
    temperature_c: float | None = None
    pressure_bar: float | None = None
    peak_mw: float | None = None
    annual_mwh: float | None = None
    shape: Any = "flat"
    shutdown_weeks: tuple[int, ...] = ()
    load: str | None = None


def _times(index):
    """The time level of a snapshot index (the last level of a multi-period one)."""
    return index.get_level_values(-1) if isinstance(index, pd.MultiIndex) else index


def _tz(index):
    return getattr(_times(index), "tz", None)


def _snapshot_times(n) -> pd.DatetimeIndex:
    times = _times(n.snapshots)
    if not isinstance(times, pd.DatetimeIndex):
        raise HeatLevelError("A flat heat demand needs snapshots that are timestamps.")
    if times.tz is not None:
        raise HeatLevelError(
            "A flat heat demand needs time-zone-naive (UTC) snapshots, as the profile "
            f"generator writes them; these are in {times.tz}."
        )
    return times


def _flat_series(n, row: HeatDemand) -> np.ndarray:
    from services.profile_gen.load_archetypes import (
        InvalidArchetypeParamsError,
        generate_frame,
    )

    if (row.peak_mw is None) == (row.annual_mwh is None):
        raise HeatLevelError(
            f"Heat demand on \u201c{row.bus}\u201d: state exactly one of peak MW_th and annual MWh_th."
        )
    times = _snapshot_times(n)
    params = {
        "shifts": "continuous",
        "base_share": 0.0,
        "shutdown_weeks": list(row.shutdown_weeks),
        "peak_mw": row.peak_mw,
        "annual_mwh": row.annual_mwh,
        "noise": 0.0,
        "seasonal_amplitude": 0.0,
    }
    out = np.empty(len(times))
    for year in sorted(set(times.year)):
        try:
            frame, meta = generate_frame(["d"], "industrial", params, int(year))
        except InvalidArchetypeParamsError as exc:
            raise HeatLevelError(
                f"Heat demand on \u201c{row.bus}\u201d: {exc}"
            ) from None
        sel = times.year == year
        vals = frame["d"].reindex(times[sel])
        if vals.isna().any():
            raise HeatLevelError(
                f"Heat demand on \u201c{row.bus}\u201d: the snapshots are not on whole hours, so the flat "
                "shape cannot be placed on them. Give the shape as a series instead."
            )
        out[sel] = vals.to_numpy() * float(meta["scale_mw"])
    return out


def _given_series(n, row: HeatDemand) -> np.ndarray:
    if row.peak_mw is not None or row.annual_mwh is not None:
        raise HeatLevelError(
            f"Heat demand on \u201c{row.bus}\u201d: a given series is in MW_th already; leave peak and "
            "annual empty."
        )
    s = row.shape
    if not isinstance(s, pd.Series):
        raise HeatLevelError(
            f"Heat demand on \u201c{row.bus}\u201d: the shape is flat or a series of MW_th per snapshot."
        )
    idx = s.index
    snaps = n.snapshots
    if str(_tz(idx)) != str(_tz(snaps)):
        raise HeatLevelError(
            f"Heat demand on \u201c{row.bus}\u201d: the series is in time zone {_tz(idx)}, the snapshots "
            f"in {_tz(snaps)}. Convert it; it is never shifted silently."
        )
    if len(idx) != len(snaps) or not idx.equals(snaps):
        raise HeatLevelError(
            f"Heat demand on \u201c{row.bus}\u201d: the series' index does not match the network's "
            f"snapshots exactly ({len(idx)} vs {len(snaps)} entries). It is never reindexed "
            "silently; give one value per snapshot."
        )
    v = pd.to_numeric(s, errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(v).all() or (v < 0).any():
        raise HeatLevelError(
            f"Heat demand on \u201c{row.bus}\u201d: the series must be finite and at least 0 MW_th."
        )
    return v


def add_heat_demand(n, rows: list[HeatDemand]) -> list[str]:
    """Add each row's level bus (when missing) and its heat Load; returns the Load names.

    The bus gets carrier `heat` (or `steam`), unit MWh and its level; an existing bus keeps its carrier and gets
    the level written. Every refusal is a `HeatLevelError` naming the bus."""
    from services.adequacy.eh_columns import coerce_eh_value, ensure_eh_column

    names = []
    for row in rows:
        if (row.temperature_c is None) == (row.pressure_bar is None):
            raise HeatLevelError(
                f"Heat demand on \u201c{row.bus}\u201d: state exactly one of a supply temperature (°C) and "
                "a header pressure (barg)."
            )
        col = "temperature_c" if row.temperature_c is not None else "pressure_bar"
        try:
            level = coerce_eh_value(
                "Bus",
                col,
                row.temperature_c if col == "temperature_c" else row.pressure_bar,
            )
        except ValueError as exc:
            raise HeatLevelError(
                f"Heat demand on \u201c{row.bus}\u201d: {exc}"
            ) from None
        p_set = (
            _flat_series(n, row)
            if isinstance(row.shape, str) and row.shape == "flat"
            else _given_series(n, row)
        )
        if row.bus not in n.buses.index:
            n.add(
                "Bus",
                row.bus,
                carrier="heat" if col == "temperature_c" else "steam",
                unit="MWh",
            )
        ensure_eh_column(n, "Bus", col)
        n.buses.loc[row.bus, col] = level
        load = row.load or f"{row.bus} demand"
        n.add(
            "Load",
            load,
            bus=row.bus,
            carrier=str(n.buses.at[row.bus, "carrier"]),
            p_set=pd.Series(p_set, index=n.snapshots),
        )
        names.append(load)
    return names


# ── Hybrid supply per level ───────────────────────────────────────────────────

# The demo commitment of a fired gas device. `eh_process` puts it on the issue-03 path (`solver/process_uc.py`
# reads a committable process unit: linearised plan, binary check, year-base relaxation). Values are demo.
# The flag also makes the boilers process units for results/process_view and the motor-start screen, which
# GK A accepted (#1142) only while `add_hybrid_supply` and `build_two_level_heat_demo` have no caller outside
# tests. Before giving either a route, chat tool or template, let process UC plan a committable non-process
# Link, or keep heat devices out of the process-unit readers.
GAS_DEFAULTS: dict[str, Any] = {
    "efficiency": 0.9,
    "eh_process": True,
    "committable": True,
    "p_min_pu": 0.3,
    "min_up_time": 2,
    "start_up_cost": 0.0,
}
ELECTRIC_DEFAULTS: dict[str, Any] = {"efficiency": 0.99}


def _set_reach(n, link: str, attrs: Mapping[str, Any]) -> None:
    from services.adequacy.eh_columns import coerce_eh_value, ensure_eh_column

    for col in ("max_supply_temp_c", "max_pressure_bar"):
        if attrs.get(col) is not None:
            ensure_eh_column(n, "Link", col)
            n.links.loc[link, col] = coerce_eh_value("Link", col, attrs[col])


def add_hybrid_supply(
    n,
    level_bus: str,
    *,
    gas_bus: str,
    elec_bus: str,
    gas: Mapping[str, Any],
    electric: Mapping[str, Any],
) -> tuple[str, str]:
    """A gas Link and an electric Link in parallel onto `level_bus`; returns (gas link, electric link).

    `gas` and `electric` are Link attributes plus `name` and an optional reach (`max_supply_temp_c`,
    `max_pressure_bar`). The gas Link is a committable process unit by default (`eh_process`, so issue 03's
    commitment path plans it: minimum load and start-up, no fork) and stays as backup. The electric Link is an e-boiler or heater
    (efficiency near 1) or a heat pump: give `bus2` (its low-grade source) with `efficiency2 = -(COP - 1)`."""
    if level_bus not in n.buses.index:
        raise HeatLevelError(
            f"Hybrid supply: no bus \u201c{level_bus}\u201d. Add the level's demand first."
        )
    out: list[str] = []
    process: list[str] = []
    for attrs, bus0, defaults in (
        (gas, gas_bus, GAS_DEFAULTS),
        (electric, elec_bus, ELECTRIC_DEFAULTS),
    ):
        a = {
            **defaults,
            **{
                k: v
                for k, v in attrs.items()
                if k not in ("name", "max_supply_temp_c", "max_pressure_bar")
            },
        }
        name = attrs.get("name")
        if not name:
            raise HeatLevelError(
                f"Hybrid supply on \u201c{level_bus}\u201d: every device needs a name."
            )
        if a.pop("eh_process", False):
            process.append(name)
        n.add("Link", name, bus0=bus0, bus1=level_bus, **a)
        out.append(name)
    # The custom columns go on after both Links exist: a Link added while one is present gets NaN in it, and
    # PyPSA does not fill a port column it adds for the later Link into the earlier rows.
    from services.adequacy.eh_columns import ensure_eh_column, normalise_eh_columns

    for name, attrs in zip(out, (gas, electric)):
        _set_reach(n, name, attrs)
    if process:
        ensure_eh_column(n, "Link", "eh_process")
        n.links.loc[process, "eh_process"] = True
    normalise_eh_columns(n)  # a Link added after the column exists reads NaN, not False
    return out[0], out[1]


# ── The generic two-level demo (acceptance fixture) ──────────────────────────


def build_two_level_heat_demo(
    hours: int = 24, *, electricity_price: Any = None, gas_price: float = 40.0
):
    """Two heat levels, every value demo (ledger: `DEMO_LEDGER`).

    * low band (90 °C, 10 MW_th flat): heat pump (COP 3, reach 120 °C, ambient source bus) plus gas boiler;
    * high band (450 °C, 5 MW_th flat): gas furnace plus electric heater (reach 1000 °C).

    `electricity_price` is EUR/MWh per snapshot (a number or a sequence of `hours`); default 60. Gas costs
    `gas_price` EUR/MWh."""
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-07", periods=hours, freq="h"))
    from services.carrier_catalog import ensure_carrier

    for c in ("AC", "gas", "heat"):
        ensure_carrier(
            n, c
        )  # the catalogue's CO2 factors, so gas is not counted as zero-carbon
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "gas", carrier="gas", unit="MWh")
    n.add("Bus", "ambient", carrier="heat", unit="MWh")
    price = 60.0 if electricity_price is None else electricity_price
    n.add(
        "Generator",
        "grid supply",
        bus="grid",
        carrier="AC",
        p_nom=1e3,
        marginal_cost=pd.Series(
            np.broadcast_to(np.asarray(price, dtype=float), (hours,)).copy(),
            index=n.snapshots,
        ),
    )
    n.add(
        "Generator",
        "gas supply",
        bus="gas",
        carrier="gas",
        p_nom=1e3,
        marginal_cost=gas_price,
    )
    n.add(
        "Generator",
        "ambient heat",
        bus="ambient",
        carrier="heat",
        p_nom=1e3,
        marginal_cost=0.0,
    )
    add_heat_demand(
        n,
        [
            HeatDemand("heat low", temperature_c=90.0, peak_mw=10.0),
            HeatDemand("heat high", temperature_c=450.0, peak_mw=5.0),
        ],
    )
    add_hybrid_supply(
        n,
        "heat low",
        gas_bus="gas",
        elec_bus="grid",
        gas={
            "name": "gas boiler",
            "carrier": "gas boiler",
            "p_nom": 15.0,
            "max_supply_temp_c": 200.0,
        },
        electric={
            "name": "heat pump",
            "carrier": "heat pump",
            "p_nom": 5.0,
            "efficiency": 3.0,
            "bus2": "ambient",
            "efficiency2": -2.0,
            "max_supply_temp_c": 120.0,
        },
    )
    add_hybrid_supply(
        n,
        "heat high",
        gas_bus="gas",
        elec_bus="grid",
        gas={
            "name": "gas furnace",
            "carrier": "gas furnace",
            "p_nom": 8.0,
            "max_supply_temp_c": 900.0,
        },
        electric={
            "name": "electric heater",
            "carrier": "electric heater",
            "p_nom": 8.0,
            "max_supply_temp_c": 1000.0,
        },
    )
    return n
