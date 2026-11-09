"""`chart_spec@1`: the settings a result view or chart is drawn with (assistant
memory plan §7.2, DASHBOARD-1).

A plain, closed object: which view, which series (class, carrier, metric,
unit), the chart type and a few style toggles. Nothing in it is a formula or
code, so a stored spec can only ever pick among things the app already draws.
Validated against the component classes and the result views.

This module imports nothing from memory: the dashboard package reuses it for
its added charts and owns any ``chart_spec@2`` (GK B, plan review 38).
"""

from __future__ import annotations

import re
from typing import Any

SCHEMA = "chart_spec@1"

CHART_TYPES = ("line", "bar", "stacked_area", "stacked_bar", "duration", "heatmap")
POWER_UNITS = ("kW", "MW", "GW")
ENERGY_UNITS = ("kWh", "MWh", "GWh", "TWh")
# Units a series may be drawn in: power, energy, and the dimensionless ones.
SERIES_UNITS = (
    POWER_UNITS + ENERGY_UNITS + ("%", "h", "tCO2", "currency", "currency/MWh", "1")
)
SORTS = ("none", "asc", "desc")
X_AXES = ("snapshot", "period", "component", "carrier", "bus")
GROUP_BY = ("carrier", "component_class", "bus", "country")
MAX_SERIES = 12

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.\- ]{0,63}$")
_METRIC = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class ChartSpecError(ValueError):
    """A spec that is not a valid ``chart_spec@1``; ``kind`` is the error kind."""

    kind = "chart_spec_invalid"


def _known_classes() -> frozenset[str]:
    from services.chat_tools import _COMPONENT_CREATE_SCHEMAS

    return frozenset(_COMPONENT_CREATE_SCHEMAS)


def _view_ids() -> frozenset[str]:
    from services.result_views import RESULT_VIEWS

    return frozenset(v.view_id for v in RESULT_VIEWS)


def _keys(
    obj: Any, where: str, allowed: set[str], required: set[str] = frozenset()
) -> dict:
    if not isinstance(obj, dict):
        raise ChartSpecError(f"{where} must be an object")
    extra = set(obj) - allowed
    if extra:
        raise ChartSpecError(f"{where} has unknown keys: {', '.join(sorted(extra))}")
    missing = set(required) - set(obj)
    if missing:
        raise ChartSpecError(f"{where} is missing: {', '.join(sorted(missing))}")
    return obj


def _choice(value: Any, allowed: tuple, where: str) -> Any:
    if value not in allowed:
        raise ChartSpecError(f"{where} must be one of {', '.join(map(str, allowed))}")
    return value


def validate(spec: Any) -> dict:
    """The normalised spec, or ChartSpecError naming the first problem.

    Optional keys that are absent stay absent; the result is a fresh dict, so
    the caller's object is never changed."""
    s = _keys(
        spec,
        "chart spec",
        {"schema", "view_id", "series", "chart", "units", "style"},
        {"schema", "series", "chart"},
    )
    if s["schema"] != SCHEMA:
        raise ChartSpecError(f"schema must be {SCHEMA!r}")
    out: dict[str, Any] = {"schema": SCHEMA}
    if "view_id" in s:
        if s["view_id"] not in _view_ids():
            raise ChartSpecError("view_id is not a result view")
        out["view_id"] = s["view_id"]
    series = s["series"]
    if not isinstance(series, list) or not 1 <= len(series) <= MAX_SERIES:
        raise ChartSpecError(f"series must be a list of 1 to {MAX_SERIES} entries")
    classes = None
    out["series"] = []
    for i, raw in enumerate(series):
        where = f"series[{i}]"
        e = _keys(
            raw,
            where,
            {"component_class", "carrier", "metric", "unit"},
            {"metric", "unit"},
        )
        item: dict[str, Any] = {}
        if "component_class" in e:
            classes = classes if classes is not None else _known_classes()
            if e["component_class"] not in classes:
                raise ChartSpecError(
                    f"{where}.component_class is not a component class"
                )
            item["component_class"] = e["component_class"]
        if "carrier" in e:
            if not isinstance(e["carrier"], str) or not _ID.match(e["carrier"]):
                raise ChartSpecError(f"{where}.carrier is not a carrier name")
            item["carrier"] = e["carrier"]
        if not isinstance(e["metric"], str) or not _METRIC.match(e["metric"]):
            raise ChartSpecError(f"{where}.metric must be a lower-case identifier")
        item["metric"] = e["metric"]
        item["unit"] = _choice(e["unit"], SERIES_UNITS, f"{where}.unit")
        out["series"].append(item)
    c = _keys(s["chart"], "chart", {"type", "x", "group_by"}, {"type"})
    chart = {"type": _choice(c["type"], CHART_TYPES, "chart.type")}
    if "x" in c:
        chart["x"] = _choice(c["x"], X_AXES, "chart.x")
    if "group_by" in c:
        chart["group_by"] = _choice(c["group_by"], GROUP_BY, "chart.group_by")
    out["chart"] = chart
    if "units" in s:
        u = _keys(s["units"], "units", {"power", "energy"})
        units = {}
        if "power" in u:
            units["power"] = _choice(u["power"], POWER_UNITS, "units.power")
        if "energy" in u:
            units["energy"] = _choice(u["energy"], ENERGY_UNITS, "units.energy")
        out["units"] = units
    if "style" in s:
        st = _keys(s["style"], "style", {"stacked", "log_y", "sort"})
        style: dict[str, Any] = {}
        for flag in ("stacked", "log_y"):
            if flag in st:
                if not isinstance(st[flag], bool):
                    raise ChartSpecError(f"style.{flag} must be true or false")
                style[flag] = st[flag]
        if "sort" in st:
            style["sort"] = _choice(st["sort"], SORTS, "style.sort")
        out["style"] = style
    return out


def summary(spec: dict) -> str:
    """One line for a prompt block or the Memory page, from a validated spec."""
    head = spec.get("view_id") or "chart"
    series = ", ".join(
        "/".join(
            p for p in (e.get("component_class"), e.get("carrier"), e["metric"]) if p
        )
        + f" [{e['unit']}]"
        for e in spec["series"]
    )
    line = f"{head}: {spec['chart']['type']} of {series}"
    style = spec.get("style") or {}
    flags = [k for k in ("stacked", "log_y") if style.get(k)]
    if flags:
        line += " (" + ", ".join(flags) + ")"
    return line
