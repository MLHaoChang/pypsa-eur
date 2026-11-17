"""
PLEXOS importer, slice 2a: a PLEXOS model (XML, or a .zip of the XML and its
CSV data files) to a PyPSA network plus an import report.

Plan and conditions: ``.scratch/project-io/plexos-mapping.md``. The XML is read
by :mod:`gridspine.ingest.plexos_xml` (no engine); this module converts it and
is the only PLEXOS module that imports ``pypsa`` (engine cage, IMPORT-CAGE-1).

What this slice maps from base rows (no scenario, band 1, undated, no data
file or variable): grid nodes, lines and links, generators, batteries, fuels
and their CO2 rate, static node loads and the chosen Model's chronological
horizon. Everything else is listed in ``report.unmapped`` with the reason.

Accounting: every property row (``t_data``) and attribute row
(``t_attribute_data``) lands in exactly one of ``network``, ``sidecar``,
``unmapped`` or ``assumed``; :func:`convert` returns that ledger so tests can
count rows in against rows out.
"""

from __future__ import annotations

import io
import json
import math
import re
import stat
import zipfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

import pandas as pd
import pypsa

from gridspine.ingest.import_result import (
    Assumed,
    ImportFormatError,
    ImportReport,
    ImportResult,
    Suggestion,
    Unmapped,
)
from gridspine.ingest.plexos_xml import (
    PlexosModel,
    PlexosRefused,
    PropertyRow,
    read_plexos_xml,
)

AREAS_SIDECAR = "plexos_areas.json"
FUEL_PRICES_SIDECAR = "plexos_fuel_prices.json"

#: Zip guards (GK A slice-1 carry-over): checked on every member before any
#: byte is read, and again while reading.
MAX_ZIP_MEMBERS = 5000
MAX_MEMBER_BYTES = 512 * 2**20
MAX_TOTAL_BYTES = 2 * 2**30
MAX_COMPRESSION_RATIO = 200

#: PLEXOS stores line resistance and reactance in per unit on this base unless
#: the file states another (GK A Q2).
DEFAULT_MVA_BASE = 100.0
MVA_BASE_PROPERTIES = ("MVA Base", "Base MVA")

#: A PLEXOS "no limit" sentinel (1E+30) is read as "not given".
_UNLIMITED = 1e29

#: 1 BTU/kWh in GJ/MWh.
BTU_PER_KWH_TO_GJ_PER_MWH = 1.055056e-3
#: kg/GJ (fuel) to t/MWh_th.
KG_PER_GJ_TO_T_PER_MWH = 0.0036

DESTINATIONS = ("network", "sidecar", "unmapped", "assumed")

#: Classes listed as unmapped as a whole, with the reason a user reads.
_UNSUPPORTED = {
    "Constraint": "generic constraint, no PyPSA equivalent yet",
    "Decision Variable": "generic constraint, no PyPSA equivalent yet",
    "Objective": "generic constraint, no PyPSA equivalent yet",
    "Reserve": "reserves are not imported yet",
    "Transformer": "transformers are not imported yet",
    "Interface": "interfaces (flow-gate limits) are not imported yet",
    "Data File": "data-file time series are read in a later slice",
    "Variable": "variables are applied in a later slice",
    "Timeslice": "timeslices are applied in a later slice",
    "Scenario": "scenario settings are applied in a later slice",
    "Storage": "hydro storage is imported in a later slice",
    "Waterway": "hydro storage is imported in a later slice",
}
_MARKET = (
    "Purchaser", "Contract", "Financial Contract", "Market", "Company",
    "Physical Contract", "Cournot", "RSI", "Power Station",
)
_OTHER_CARRIERS = (
    "Gas Node", "Gas Field", "Gas Pipeline", "Gas Storage", "Gas Demand",
    "Gas Contract", "Gas Plant", "Gas Basin", "Gas Zone", "Gas Transport",
    "Water Node", "Water Pipeline", "Water Storage", "Water Demand",
    "Water Plant", "Heat Node", "Heat Plant", "Facility", "Commodity", "Flow Network",
    "Flow Node", "Flow Path", "Flow Storage", "Process", "Entity", "Vehicle",
    "Charging Station", "Fleet",
)
_SETTINGS = (
    "Model", "Project", "Horizon", "Report", "LT Plan", "PASA", "MT Schedule",
    "ST Schedule", "Transmission", "Production", "Competition", "Stochastic",
    "Performance", "Diagnostic", "List", "Layout", "Global",
)
_ENERGY_LIMITS = re.compile(
    r"^(Max|Min) (Energy|Capacity Factor)( (Hour|Day|Week|Month|Year))?$"
)

_ROW_TABLE = "t_data"
_ATTR_TABLE = "t_attribute_data"


# ----------------------------------------------------------------- the ledger


@dataclass
class Ledger:
    """Where each source row went: ``{(table, row key): destination}``."""

    dest: dict[tuple[str, str], str] = field(default_factory=dict)

    def put(self, table: str, key: str, destination: str) -> None:
        if destination not in DESTINATIONS:
            raise ValueError(f"unknown destination {destination!r}")
        if (table, key) in self.dest:
            raise AssertionError(
                f"{table} row {key} counted twice "
                f"({self.dest[(table, key)]} and {destination})"
            )
        self.dest[(table, key)] = destination

    def totals(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for (table, _), d in self.dest.items():
            out.setdefault(table, dict.fromkeys(DESTINATIONS, 0))[d] += 1
        return out


def _attr_key(obj: tuple[str, str], name: str) -> str:
    return f"{obj[0]}|{obj[1]}|{name}"


# ------------------------------------------------------------------ helpers


def excel_date(serial: float) -> datetime | None:
    """
    An Excel/PLEXOS serial date. The epoch is 1899-12-30 from serial 61 on;
    below that Excel counts the non-existent 1900-02-29 (serial 60), so the
    epoch is one day later and serial 60 itself is None.
    """
    if serial is None or not math.isfinite(serial) or serial < 1:
        return None
    if int(serial) == 60:
        return None
    epoch = datetime(1899, 12, 30) if serial >= 61 else datetime(1899, 12, 31)
    return epoch + timedelta(days=serial)


def _percent(value: float | None) -> float | None:
    """A percentage in (0, 100] as a fraction; None when out of range."""
    if value is None or not math.isfinite(value) or not 0 < value <= 100:
        return None
    return value / 100.0


def _label(obj: tuple[str, str]) -> str:
    return f"{obj[0]} {obj[1]!r}"


@dataclass
class _Input:
    xml_name: str
    xml: bytes
    data_files: list[str]  # names in the zip or loose CSVs, for the next slice
    source_files: list[str]


def _bad_member(name: str) -> str | None:
    norm = name.replace("\\", "/")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        return "absolute path"
    if ".." in PurePosixPath(norm).parts:
        return "path leaves the archive ('..')"
    if any(ord(c) < 32 for c in norm):
        return "control character in the name"
    return None


def _read_zip(path: Path) -> _Input:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ImportFormatError(f"{path.name} is not a readable zip file: {exc}") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_MEMBERS:
            raise ImportFormatError(
                f"{path.name} holds {len(infos)} files; at most {MAX_ZIP_MEMBERS} are read"
            )
        total = 0
        xmls: list[zipfile.ZipInfo] = []
        data: list[str] = []
        for info in infos:
            why = _bad_member(info.filename)
            if why is None and stat.S_ISLNK(info.external_attr >> 16):
                why = "symbolic link"
            if why is None and info.flag_bits & 0x1:
                why = "encrypted"
            if why is None and info.file_size > MAX_MEMBER_BYTES:
                why = f"larger than {MAX_MEMBER_BYTES // 2**20} MiB unpacked"
            if (
                why is None
                and info.file_size > 2**20
                and info.file_size > MAX_COMPRESSION_RATIO * max(info.compress_size, 1)
            ):
                why = f"compressed more than {MAX_COMPRESSION_RATIO}:1"
            if why:
                raise ImportFormatError(
                    f"{path.name} refused: member {info.filename!r} is unsafe ({why})"
                )
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ImportFormatError(
                    f"{path.name} refused: more than {MAX_TOTAL_BYTES // 2**30} GiB unpacked"
                )
            if info.is_dir():
                continue
            lower = info.filename.lower()
            if lower.endswith(".xml"):
                xmls.append(info)
            else:
                data.append(info.filename.replace("\\", "/"))
        if len(xmls) != 1:
            names = ", ".join(i.filename for i in xmls) or "none"
            raise ImportFormatError(
                f"{path.name} must hold exactly one PLEXOS .xml file; found {names}"
            )
        info = xmls[0]
        buf = bytearray()
        with zf.open(info) as fh:  # the stated size is checked again while reading
            while chunk := fh.read(2**20):
                buf += chunk
                if len(buf) > MAX_MEMBER_BYTES or len(buf) > info.file_size:
                    raise ImportFormatError(
                        f"{path.name} refused: {info.filename!r} unpacks larger than it states"
                    )
    return _Input(info.filename, bytes(buf), sorted(data), [path.name])


def _read_input(paths: Sequence[Path]) -> _Input:
    paths = [Path(p) for p in paths]
    main = [p for p in paths if p.suffix.lower() in (".xml", ".zip")]
    other = [p for p in paths if p.suffix.lower() not in (".xml", ".zip", ".csv")]
    if other:
        raise ImportFormatError(
            "A PLEXOS import reads one .xml or .zip file plus optional .csv data "
            f"files; not {', '.join(p.name for p in other)}"
        )
    if len(main) != 1:
        raise ImportFormatError(
            "A PLEXOS import needs exactly one .xml or .zip file; "
            f"got {len(main)}"
        )
    path = main[0]
    csvs = sorted(p.name for p in paths if p.suffix.lower() == ".csv")
    if path.suffix.lower() == ".zip":
        got = _read_zip(path)
        got.data_files += csvs
        got.source_files += csvs
        return got
    try:
        xml = path.read_bytes()
    except OSError as exc:
        raise ImportFormatError(f"{path.name} cannot be read: {exc}") from exc
    return _Input(path.name, xml, csvs, [path.name, *csvs])


# --------------------------------------------------------------- conversion


@dataclass
class Conversion:
    network: pypsa.Network
    report: ImportReport
    sidecars: dict[str, Any]
    ledger: Ledger
    #: (asset, MW) of every static load row, by destination; GK A Q3.
    load_mw: dict[str, float]


class _Converter:
    def __init__(self, model: PlexosModel, options: Mapping[str, Any], report: ImportReport):
        self.m = model
        self.opt = options
        self.r = report
        self.ledger = Ledger()
        self.n = pypsa.Network()
        self.sidecars: dict[str, Any] = {}
        self.load_mw = {"network": 0.0, "unmapped": 0.0}
        # grouped unmapped entries: (source item, reason) -> property names
        self._skip: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.names: dict[str, set[str]] = defaultdict(set)
        self.base: dict[tuple[str, str], dict[str, PropertyRow]] = defaultdict(dict)
        self.children: dict[tuple[str, str], dict[str, list[tuple[str, str]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for mem in model.memberships:
            self.children[mem.parent][mem.collection[2]].append(mem.child)

    # -- ledger shortcuts
    def used(self, row: PropertyRow, dest: str = "network") -> None:
        self.ledger.put(_ROW_TABLE, row.data_id, dest)

    def skip(self, row: PropertyRow, reason: str, item: str | None = None) -> None:
        self.ledger.put(_ROW_TABLE, row.data_id, "unmapped")
        self._skip[(item or _label(row.child), reason)].append(row.property)

    def skip_attr(self, obj, name: str, reason: str) -> None:
        self.ledger.put(_ATTR_TABLE, _attr_key(obj, name), "unmapped")
        self._skip[(_label(obj), reason)].append(name)

    def assume(self, asset: str, parameter: str, value: Any, why: str) -> None:
        self.r.assumed.append(Assumed(asset, parameter, value, why))

    def unique(self, component: str, name: str, source: str) -> str:
        """A name not yet used in ``component``; a clash gets a fixed suffix (Q7)."""
        taken = self.names[component]
        out, i = name, 1
        while out in taken:
            i += 1
            out = f"{name} ({source} {i})" if i > 2 else f"{name} ({source})"
        if out != name:
            self.assume(out, "name", out, f"{component} name {name!r} is already used; renamed")
        taken.add(out)
        return out

    # -- row selection
    def pick_model(self) -> tuple[str | None, set[str]]:
        models = sorted(o.name for o in self.m.objects_of("Model"))
        wanted = self.opt.get("model")
        if wanted is not None:
            if wanted not in models:
                raise ImportFormatError(
                    f"PLEXOS Model {wanted!r} is not in this file; it has: "
                    + (", ".join(models) or "none")
                )
            chosen = wanted
        elif models:
            chosen = models[0]
            others = [m for m in models if m != chosen]
            why = (
                "the only Model in the file"
                if not others
                else "first Model by name; pick another in the import options. "
                f"Other Models: {', '.join(others)}"
            )
            self.assume("PLEXOS model", "Model", chosen, why)
        else:
            return None, set()
        on = {c[1] for c in self.children[("Model", chosen)].get("Scenarios", [])}
        return chosen, on

    def sort_rows(self, on: set[str]) -> None:
        """Base rows go to ``self.base``; every other row to unmapped, with why."""
        seen: dict[tuple[tuple[str, str], tuple[str, str], str], list[PropertyRow]] = defaultdict(list)
        for row in self.m.rows:
            scen = row.scenarios
            if row.value is None:
                self.skip(row, f"value unreadable ({row.unreadable or 'empty'})")
            elif scen:
                s = scen[0]
                why = (
                    "is on in the chosen Model; scenario rows are applied in a later slice"
                    if s in on
                    else "not selected"
                )
                self.skip(row, f"rows of scenario {s!r}: {why}", item=f"Scenario {s!r}")
            elif row.band != 1:
                self.skip(row, f"band {row.band}: only band 1 is used")
            elif row.date_from or row.date_to:
                self.skip(row, "dated row: dates are applied in a later slice")
            elif any(t.cls == "Data File" for t in row.tags):
                self.skip(row, "time series from a data file: read in a later slice")
            elif row.tags:
                self.skip(row, f"scaled by {row.tags[0].cls} {row.tags[0].name!r}: later slice")
            else:
                seen[(row.parent, row.child, row.property)].append(row)
        for (parent, child, prop), rows in seen.items():
            if len(rows) > 1:
                for row in rows:
                    self.skip(row, f"{len(rows)} base values for {prop!r}; none used")
                continue
            key = child if parent[0] == "System" else (parent, child)
            self.base[key][prop] = rows[0]  # type: ignore[index]

    def take(self, obj, prop: str) -> PropertyRow | None:
        return self.base.get(obj, {}).pop(prop, None)

    def leftovers(self, obj, reason: str = "property not imported yet") -> None:
        for prop, row in self.base.pop(obj, {}).items():
            if _ENERGY_LIMITS.match(prop):
                self.skip(row, "energy limit over a period, no per-hour equivalent yet")
            elif prop.startswith(("Offer ", "Bid ")):
                self.skip(row, "market bids and offers are not imported")
            else:
                self.skip(row, reason)

    def unit_ok(self, row: PropertyRow, *units: str) -> bool:
        """True when the row's unit is one this code converts; otherwise unmapped (Q1)."""
        if row.unit in units:
            return True
        self.skip(row, f"unit {row.unit!r} for {row.property!r} is not converted (expects {', '.join(units)})")
        return False

    def one_child(self, obj, collection: str, what: str) -> tuple[str, str] | None | str:
        kids = self.children.get(obj, {}).get(collection, [])
        if len(kids) == 1:
            return kids[0]
        return f"{len(kids)} {what} memberships" if kids else f"no {what}"

    def drop_object(self, obj, reason: str) -> None:
        for row in self.base.pop(obj, {}).values():
            self.skip(row, reason, item=_label(obj))
        self._skip.setdefault((_label(obj), reason), [])

    # -- classes
    def horizon(self, model: str | None) -> None:
        attrs = defaultdict(dict)
        for a in self.m.attributes:
            attrs[a.object][a.name] = a
        chosen = None
        if model:
            hs = self.children[("Model", model)].get("Horizon", [])
            if len(hs) == 1:
                chosen = hs[0]
            elif hs:
                self.r.warnings.append(f"Model {model!r} has {len(hs)} horizons; none used")
        for obj, by_name in attrs.items():
            for name, a in by_name.items():
                key = _attr_key(obj, name)
                if obj[0] == "Node" and name in ("Latitude", "Longitude"):
                    continue  # read with the nodes
                if obj != chosen:
                    why = "horizon of another Model" if obj[0] == "Horizon" else (
                        "Model setting" if obj[0] == "Model" else "attribute not imported yet")
                    self.skip_attr(obj, name, why)
        if chosen is None:
            self.assume("network", "snapshots", "one snapshot",
                        "the chosen Model has no horizon")
            return
        h = attrs.get(chosen, {})

        def val(name):
            a = h.get(name)
            return None if a is None else a.value

        start_serial = val("Chrono Date From") or val("Date From")
        start = excel_date(start_serial) if start_serial is not None else None
        ppd = val("Periods per Day") or 24
        days = val("Chrono Step Count")
        minutes = 1440 / ppd if ppd > 0 else None
        ok = start is not None and minutes and minutes == int(minutes) and ppd == int(ppd)
        used = {"Chrono Date From", "Date From", "Periods per Day", "Chrono Step Count"}
        if not ok:
            for name in h:
                self.skip_attr(chosen, name, "horizon start or periods per day unreadable")
            self.assume("network", "snapshots", "one snapshot", f"horizon {chosen[1]!r} is unreadable")
            return
        if days is None or days <= 0 or days != int(days):
            days = 366 if pd.Timestamp(start).is_leap_year else 365
            self.assume("network", "snapshots", f"{int(days)} days",
                        f"horizon {chosen[1]!r} gives no chronological length; one year used")
        if "Chrono Step Type" in h:
            self.assume("network", "snapshots", "days",
                        "Chrono Step Type is read as days (the PLEXOS default)")
        n_steps = int(days) * int(ppd)
        snaps = pd.date_range(start, periods=n_steps, freq=f"{int(minutes)}min")
        self.n.set_snapshots(snaps)
        self.n.snapshot_weightings.loc[:, :] = minutes / 60.0
        for name in h:
            key = _attr_key(chosen, name)
            if name in used or (name == "Chrono Step Type"):
                self.ledger.put(_ATTR_TABLE, key, "network")
            else:
                self.skip_attr(chosen, name, "horizon setting not imported yet")
        if ppd != 24:
            self.assume("network", "snapshot step", f"{int(minutes)} min",
                        f"{int(ppd)} periods per day")

    def nodes(self) -> None:
        coords = {
            (a.object, a.name): a for a in self.m.attributes
            if a.object[0] == "Node" and a.name in ("Latitude", "Longitude")
        }
        self.voltage: dict[str, float | None] = {}
        for node in sorted(self.m.objects_of("Node"), key=lambda o: o.name):
            obj = node.key
            kw: dict[str, Any] = {}
            v = self.take(obj, "Voltage")
            if v is not None and self.unit_ok(v, "kV"):
                if v.value > 0:
                    kw["v_nom"] = v.value
                    self.used(v)
                else:
                    self.skip(v, "voltage must be above 0")
            if "v_nom" not in kw:
                self.assume(node.name, "voltage", None,
                            "voltage unknown; lines with reactance on this node are not converted")
            for attr, col in (("Longitude", "x"), ("Latitude", "y")):
                a = coords.get((obj, attr))
                if a is None:
                    continue
                key = _attr_key(obj, attr)
                if a.value is None:
                    self.ledger.put(_ATTR_TABLE, key, "unmapped")
                    self._skip[(_label(obj), "coordinate unreadable")].append(attr)
                else:
                    kw[col] = a.value
                    self.ledger.put(_ATTR_TABLE, key, "network")
            units = self.take(obj, "Units")
            if units is not None:
                self.used(units, "assumed" if units.value == 0 else "network")
                if units.value == 0:
                    self.assume(node.name, "in service", False, "Units = 0: node out of service in PLEXOS")
            name = self.unique("Bus", node.name, "Node")
            self.n.add("Bus", name, **kw)
            self.voltage[node.name] = kw.get("v_nom")
            load = self.take(obj, "Load")
            if load is not None and self.unit_ok(load, "MW"):
                self.used(load)
                self.load_mw["network"] += load.value
                self.n.add("Load", self.unique("Load", node.name, "Load"), bus=name, p_set=load.value)
            self.leftovers(obj)

    def areas(self) -> None:
        out = []
        for cls, coll in (("Region", "Region"), ("Zone", "Zone")):
            members = defaultdict(list)
            for node in self.m.objects_of("Node"):
                for a in self.children.get(node.key, {}).get(coll, []):
                    members[a[1]].append(node.name)
            for obj in sorted(self.m.objects_of(cls), key=lambda o: o.name):
                entry: dict[str, Any] = {"name": obj.name, "kind": cls.lower(),
                                         "buses": sorted(members.get(obj.name, []))}
                voll = self.take(obj.key, "VoLL")
                if voll is not None:
                    entry["voll"] = voll.value
                    entry["voll_unit"] = voll.unit
                    self.used(voll, "sidecar")
                units = self.take(obj.key, "Units")
                if units is not None:
                    entry["units"] = units.value
                    self.used(units, "sidecar")
                load = self.take(obj.key, "Load")
                if load is not None:
                    self.load_mw["unmapped"] += load.value
                    self.skip(load, "region-level load: distributing it by load participation factor is not supported yet")
                self.leftovers(obj.key)
                out.append(entry)
        for node in self.m.objects_of("Node"):
            lpf = self.take(node.key, "Load Participation Factor")
            if lpf is not None:
                self.skip(lpf, "load participation factor: region-level load is not distributed yet")
        if out:
            self.sidecars[AREAS_SIDECAR] = {"source": "PLEXOS import", "areas": out}

    def mva_base(self) -> float:
        for obj in list(self.base):
            if isinstance(obj, tuple) and len(obj) == 2 and isinstance(obj[0], str) and obj[0] in ("System", "Transmission"):
                for prop in MVA_BASE_PROPERTIES:
                    row = self.take(obj, prop)
                    if row is not None and row.unit in ("MVA", "MW") and row.value > 0:
                        self.used(row)
                        return row.value
                    if row is not None:
                        self.skip(row, "system MVA base unreadable")
        self.assume("network", "MVA base", DEFAULT_MVA_BASE,
                    "the file gives no MVA base; per-unit line impedances read on 100 MVA")
        return DEFAULT_MVA_BASE

    def lines(self) -> None:
        lines = sorted(self.m.objects_of("Line"), key=lambda o: o.name)
        base = None
        for line in lines:
            obj = line.key
            ends = [self.one_child(obj, c, c.lower()) for c in ("Node From", "Node To")]
            bad = next((e for e in ends if isinstance(e, str)), None)
            if bad:
                self.drop_object(obj, f"line needs one node at each end ({bad})")
                continue
            a, b = ends[0][1], ends[1][1]
            if a == b:
                self.drop_object(obj, "both ends on the same node")
                continue
            units = self.take(obj, "Units")
            if units is not None and units.value == 0:
                self.used(units, "unmapped")
                self.drop_object(obj, "0 units: not built")
                continue
            if units is not None:
                self.used(units, "network" if units.value == 1 else "unmapped")
                if units.value != 1:
                    self.drop_object(obj, f"{units.value:g} parallel units are not converted")
                    continue
            mx = self.take(obj, "Max Flow")
            mn = self.take(obj, "Min Flow")
            x = self.take(obj, "Reactance")
            r = self.take(obj, "Resistance")
            wheel = self.take(obj, "Wheeling Charge")
            if mx is None or not self.unit_ok(mx, "MW") or mx.value >= _UNLIMITED:
                if mx is not None and mx.value >= _UNLIMITED:
                    self.skip(mx, "unlimited Max Flow is not converted")
                for row in (mn, x, r, wheel):
                    if row is not None:
                        self.skip(row, "line has no usable Max Flow")
                self.drop_object(obj, "line has no usable Max Flow")
                continue
            if mx.value <= 0:
                self.skip(mx, f"Max Flow {mx.value:g} MW: line has no capacity")
                for row in (mn, x, r, wheel):
                    if row is not None:
                        self.skip(row, "line has no capacity")
                self.drop_object(obj, "line has no capacity")
                continue
            if x is not None:
                if base is None:
                    base = self.mva_base()
                self._ac_line(line.name, a, b, mx, mn, x, r, wheel, base)
            else:
                self._link(line.name, a, b, mx, mn, r, wheel)
            self.leftovers(obj)

    def _ac_line(self, name, a, b, mx, mn, x, r, wheel, base) -> None:
        va, vb = self.voltage.get(a), self.voltage.get(b)
        rows = [row for row in (mx, mn, x, r, wheel) if row is not None]
        why = None
        if va is None or vb is None:
            why = "needs a node voltage to convert reactance"
        elif va != vb:
            why = f"ends at {va:g} kV and {vb:g} kV: needs a transformer"
        elif x.unit != "pu" or (r is not None and r.unit != "pu"):
            why = "impedance unit is not per unit"
        elif not x.value or x.value < 0 or (r is not None and r.value < 0):
            why = "reactance must be above 0"
        if why:
            for row in rows:
                self.skip(row, why, item=f"Line {name!r}")
            return
        z = va**2 / base
        kw = {"bus0": a, "bus1": b, "s_nom": mx.value, "x": x.value * z,
              "r": (r.value * z) if r is not None else 0.0}
        for row in (mx, x, r):
            if row is not None:
                self.used(row)
        if mn is not None:
            self.used(mn, "assumed")
            if mn.value != -mx.value:
                self.assume(name, "Min Flow", mn.value,
                            "an AC line's limit is symmetric; Max Flow used both ways")
        if wheel is not None:
            self.skip(wheel, "wheeling charge on an AC line has no PyPSA field")
        self.n.add("Line", self.unique("Line", name, "Line"), **kw)

    def _link(self, name, a, b, mx, mn, r, wheel) -> None:
        kw: dict[str, Any] = {"bus0": a, "bus1": b, "p_nom": mx.value}
        self.used(mx)
        if mn is not None and self.unit_ok(mn, "MW") and mn.value > -_UNLIMITED:
            if mn.value > mx.value:
                self.skip(mn, "Min Flow above Max Flow")
                kw["p_min_pu"] = -1.0
                self.assume(name, "Min Flow", -mx.value, "Min Flow unusable; taken as −Max Flow")
            else:
                kw["p_min_pu"] = mn.value / mx.value
                self.used(mn)
        else:
            if mn is not None and mn.unit == "MW":
                self.used(mn, "assumed")
            kw["p_min_pu"] = -1.0
            self.assume(name, "Min Flow", -mx.value, "Min Flow not given; taken as −Max Flow")
        if wheel is not None and self.unit_ok(wheel, "$/MWh"):
            kw["marginal_cost"] = wheel.value
            self.used(wheel)
        if r is not None:
            self.skip(r, "resistance without reactance: transport model, losses not imported")
        self.n.add("Link", self.unique("Link", name, "Line"), carrier="DC", **kw)
        self.carriers.add("DC")

    def fuels(self) -> None:
        self.price: dict[str, float] = {}
        prices = []
        for fuel in sorted(self.m.objects_of("Fuel"), key=lambda o: o.name):
            row = self.take(fuel.key, "Price")
            if row is not None and self.unit_ok(row, "$/GJ"):
                self.price[fuel.name] = row.value
                self.used(row, "sidecar")
                prices.append({"fuel": fuel.name, "price": row.value, "unit": row.unit,
                               "source": "static Price"})
            self.leftovers(fuel.key)
        if prices:
            self.sidecars[FUEL_PRICES_SIDECAR] = {"source": "PLEXOS import", "fuels": prices}
        self.co2: dict[str, float] = {}
        for em in self.m.objects_of("Emission"):
            for fuel in self.children.get(em.key, {}).get("Fuels", []):
                row = self.take((em.key, fuel), "Production Rate")
                if row is None:
                    continue
                if "co2" not in em.name.lower().replace("₂", "2"):
                    self.skip(row, "only CO2 emission rates are imported", item=f"Emission {em.name!r}")
                elif self.unit_ok(row, "kg/GJ"):
                    self.co2[fuel[1]] = row.value * KG_PER_GJ_TO_T_PER_MWH
                    self.used(row)
            for key in [k for k in self.base if isinstance(k[0], tuple) and k[0] == em.key]:
                for row in self.base.pop(key).values():
                    self.skip(row, "emission property not imported yet")
            self.leftovers(em.key)

    def _units(self, obj, name: str) -> float | None:
        u = self.take(obj, "Units")
        if u is None:
            self.drop_object(obj, "Units not given (PLEXOS default 0: not built)")
            return None
        if u.value == 0:
            self.used(u, "unmapped")
            self.drop_object(obj, "0 units: not built")
            return None
        if u.value < 0 or u.value != int(u.value):
            self.skip(u, "Units must be a whole number above 0")
            self.drop_object(obj, "Units unusable")
            return None
        self.used(u)
        return u.value

    def _node_of(self, obj, collection: str) -> str | None:
        node = self.one_child(obj, collection, "node")
        if isinstance(node, str):
            self.drop_object(obj, f"needs exactly one node ({node})")
            return None
        return node[1]

    def generators(self) -> None:
        for gen in sorted(self.m.objects_of("Generator"), key=lambda o: o.name):
            obj = gen.key
            bus = self._node_of(obj, "Nodes")
            if bus is None:
                continue
            cap = self.take(obj, "Max Capacity")
            units = self._units(obj, gen.name)
            if units is None:
                if cap is not None:
                    self.skip(cap, "not built", item=_label(obj))
                continue
            if cap is None or not self.unit_ok(cap, "MW") or not cap.value > 0:
                if cap is not None and cap.unit == "MW":
                    self.skip(cap, "Max Capacity must be above 0")
                self.drop_object(obj, "no usable Max Capacity")
                continue
            self.used(cap)
            p_nom = cap.value * units
            kw: dict[str, Any] = {"bus": bus, "p_nom": p_nom}
            fuels = self.children.get(obj, {}).get("Fuels", [])
            if len(fuels) == 1:
                carrier = fuels[0][1]
            else:
                carrier = gen.category or "unknown"
            kw["carrier"] = carrier
            self.carriers.add(carrier)
            kw["marginal_cost"] = self._cost(obj, gen.name, fuels)
            self._min_stable(obj, gen.name, cap.value)
            rf = self.take(obj, "Rating Factor")
            if rf is not None and self.unit_ok(rf, "%"):
                f = _percent(rf.value)
                if f is None:
                    self.skip(rf, "Rating Factor outside (0, 100] %")
                else:
                    kw["p_max_pu"] = f
                    self.used(rf)
            sc = self.take(obj, "Start Cost")
            if sc is not None and self.unit_ok(sc, "$"):
                kw["start_up_cost"] = sc.value
                self.used(sc)
            for prop, col in (("Max Ramp Up", "ramp_limit_up"), ("Max Ramp Down", "ramp_limit_down")):
                row = self.take(obj, prop)
                if row is not None and self.unit_ok(row, "MW/min"):
                    if row.value > 0:
                        kw[col] = min(1.0, row.value * 60 / cap.value)
                        self.used(row)
                    else:
                        self.skip(row, "ramp limit must be above 0")
            self._dates(obj, gen.name, kw)
            self._outages(obj, gen.name, kw)
            self.n.add("Generator", self.unique("Generator", gen.name, "Generator"), **kw)
            self.leftovers(obj)

    def _cost(self, obj, name: str, fuels) -> float:
        vom = self.take(obj, "VO&M Charge")
        cost = 0.0
        if vom is not None and self.unit_ok(vom, "$/MWh"):
            cost = vom.value
            self.used(vom)
        hr = self.take(obj, "Heat Rate")
        forms = [self.take(obj, p) for p in ("Heat Rate Base", "Heat Rate Incr", "Fuel Offtake")]
        for row in forms:
            if row is not None:
                self.skip(row, "heat-rate curve form is not converted; no guessed cost")
        if hr is None:
            if fuels:
                self.assume(name, "marginal cost", cost,
                            "no plain Heat Rate: fuel cost not included, VO&M only")
            return cost
        if len(fuels) != 1:
            self.skip(hr, f"{len(fuels)} fuels: multi-fuel cost is not converted" if fuels
                      else "heat rate without a fuel")
            self.assume(name, "marginal cost", cost, "fuel cost not included, VO&M only")
            return cost
        if hr.unit == "GJ/MWh":
            rate = hr.value
        elif hr.unit == "BTU/kWh":
            rate = hr.value * BTU_PER_KWH_TO_GJ_PER_MWH
        else:
            self.skip(hr, f"unit {hr.unit!r} for 'Heat Rate' is not converted (expects GJ/MWh, BTU/kWh)")
            self.assume(name, "marginal cost", cost, "fuel cost not included, VO&M only")
            return cost
        fuel = fuels[0][1]
        if fuel not in self.price:
            self.used(hr, "assumed")
            self.assume(name, "marginal cost", cost, f"fuel {fuel!r} has no static price; VO&M only")
            return cost
        self.used(hr)
        total = rate * self.price[fuel] + cost
        self.assume(name, "marginal cost", round(total, 6),
                    f"heat rate {rate:g} GJ/MWh × {fuel} price {self.price[fuel]:g} $/GJ"
                    f" + VO&M {cost:g} $/MWh; PLEXOS $ read as the project currency")
        return total

    def _min_stable(self, obj, name: str, cap: float) -> None:
        """
        Min Stable Factor (%) and Min Stable Level (MW): PLEXOS applies both as
        lower bounds while the unit runs, so the larger wins (GK A Q5). It binds
        only with unit commitment, so it is offered as a suggestion, not set.
        """
        msf = self.take(obj, "Min Stable Factor")
        msl = self.take(obj, "Min Stable Level")
        vals = []
        if msf is not None and self.unit_ok(msf, "%"):
            if msf.value == 0:
                self.used(msf)
            elif (f := _percent(msf.value)) is None:
                self.skip(msf, "Min Stable Factor outside (0, 100] %")
            else:
                vals.append((f, msf))
        if msl is not None and self.unit_ok(msl, "MW"):
            if msl.value == 0:
                self.used(msl)
            elif not 0 < msl.value <= cap:
                self.skip(msl, "Min Stable Level outside (0, Max Capacity]")
            else:
                vals.append((msl.value / cap, msl))
        if not vals:
            return
        pu = max(v for v, _ in vals)
        if len(vals) == 2 and not math.isclose(vals[0][0], vals[1][0]):
            self.assume(name, "minimum stable level", pu,
                        "Min Stable Factor and Min Stable Level disagree; the larger is used")
        for _, row in vals:
            self.skip(row, "minimum stable level needs unit commitment; offered as a suggestion")
        self.r.suggestions.append(Suggestion(
            target=name, parameter="minimum stable level (p_min_pu, unit commitment on)",
            current=0.0, proposed=round(pu, 6),
            why="PLEXOS minimum stable level; it applies only while the unit runs, "
                "so it needs unit commitment, which makes the solve slower",
            source="PLEXOS Min Stable Factor / Min Stable Level", confidence="high"))

    def _dates(self, obj, name: str, kw: dict) -> None:
        com = self.take(obj, "Commission Date")
        ret = self.take(obj, "Retirement Date")
        start = excel_date(com.value) if com is not None else None
        if com is not None:
            if start is None:
                self.skip(com, "Commission Date unreadable")
            else:
                kw["build_year"] = start.year
                self.used(com)
        if ret is None:
            return
        end = excel_date(ret.value)
        if end is None:
            self.skip(ret, "Retirement Date unreadable")
        elif start is None:
            self.skip(ret, "Retirement Date without a Commission Date: no lifetime")
        elif end.year <= start.year:
            self.skip(ret, "Retirement Date not after Commission Date")
            self.r.warnings.append(f"{name}: retirement {end.year} is not after commission {start.year}")
        else:
            kw["lifetime"] = end.year - start.year
            self.used(ret)

    def _outages(self, obj, name: str, kw: dict) -> None:
        f = self.take(obj, "Forced Outage Rate")
        if f is not None and self.unit_ok(f, "%"):
            q = 0.0 if f.value == 0 else _percent(f.value)
            if q is None or q >= 1:
                self.skip(f, "Forced Outage Rate outside [0, 100) %")
            else:
                kw["outage_rate_value"] = q
                kw["outage_rate_basis"] = "FOR"
                self.used(f)
        m = self.take(obj, "Mean Time to Repair")
        if m is not None and self.unit_ok(m, "h"):
            if m.value > 0:
                kw["mttr_hours"] = m.value
                self.used(m)
            else:
                self.skip(m, "Mean Time to Repair must be above 0")
        mr = self.take(obj, "Maintenance Rate")
        if mr is not None:
            self.skip(mr, "planned maintenance has no network field yet")

    def batteries(self) -> None:
        for bat in sorted(self.m.objects_of("Battery"), key=lambda o: o.name):
            obj = bat.key
            bus = self._node_of(obj, "Node") if "Node" in self.children.get(obj, {}) else self._node_of(obj, "Nodes")
            if bus is None:
                continue
            units = self._units(obj, bat.name)
            if units is None:
                continue
            power = self.take(obj, "Max Power")
            energy = self.take(obj, "Capacity")
            bad = None
            if power is None or not self.unit_ok(power, "MW") or not power.value > 0:
                bad = "no usable Max Power"
            elif energy is None or not self.unit_ok(energy, "MWh") or not energy.value > 0:
                bad = "no usable Capacity (MWh)"
            if bad:
                for row in (power, energy):
                    if row is not None and (_ROW_TABLE, row.data_id) not in self.ledger.dest:
                        self.skip(row, bad)
                self.drop_object(obj, bad)
                continue
            self.used(power)
            self.used(energy)
            kw: dict[str, Any] = {"bus": bus, "p_nom": power.value * units,
                                  "max_hours": energy.value / power.value, "carrier": "battery"}
            self.carriers.add("battery")
            for prop, col in (("Charge Efficiency", "efficiency_store"),
                              ("Discharge Efficiency", "efficiency_dispatch")):
                row = self.take(obj, prop)
                if row is None:
                    self.assume(bat.name, col, 1.0, f"{prop} not given; PLEXOS default 100 %")
                    continue
                if not self.unit_ok(row, "%"):
                    continue
                f = _percent(row.value)
                if f is None:
                    self.skip(row, f"{prop} outside (0, 100] %")
                    self.assume(bat.name, col, 1.0, f"{prop} unusable; 100 % used")
                else:
                    kw[col] = f
                    self.used(row)
            soc = self.take(obj, "Initial SoC")
            if soc is not None and self.unit_ok(soc, "%"):
                if soc.value == 0:
                    self.used(soc)
                elif (f := _percent(soc.value)) is None:
                    self.skip(soc, "Initial SoC outside [0, 100] %")
                else:
                    kw["state_of_charge_initial"] = f * energy.value * units
                    self.used(soc)
            self.n.add("StorageUnit", self.unique("StorageUnit", bat.name, "Battery"), **kw)
            self.leftovers(obj)

    def rest(self, model: str | None) -> None:
        """Rows of objects no handler took: one grouped entry per object and reason."""
        for key in list(self.base):
            obj = key[1] if isinstance(key[0], tuple) else key
            cls = obj[0]
            if cls in _UNSUPPORTED:
                reason = _UNSUPPORTED[cls]
            elif cls in _MARKET:
                reason = "market and contract objects are not imported"
            elif cls in _OTHER_CARRIERS:
                reason = "gas, water and heat systems are not imported"
            elif cls in _SETTINGS:
                reason = "execution setting, not an asset"
            else:
                reason = "class not supported"
            for row in self.base.pop(key).values():
                self.skip(row, reason, item=_label(obj))
        for obj in self.m.objects.values():
            if obj.cls == "Scenario":
                continue  # listed with their rows, per scenario
            if obj.cls in _UNSUPPORTED or obj.cls in _MARKET or obj.cls in _OTHER_CARRIERS:
                reason = _UNSUPPORTED.get(obj.cls) or (
                    "market and contract objects are not imported" if obj.cls in _MARKET
                    else "gas, water and heat systems are not imported")
                self._skip.setdefault((_label(obj.key), reason), [])

    def run(self) -> None:
        model, on = self.pick_model()
        self.carriers: set[str] = set()
        self.sort_rows(on)
        self.horizon(model)
        self.nodes()
        self.areas()
        self.lines()
        self.fuels()
        self.generators()
        self.batteries()
        self.rest(model)
        for name in sorted(self.carriers):
            kw = {"co2_emissions": self.co2[name]} if name in self.co2 else {}
            self.n.add("Carrier", name, **kw)
        for fuel, rate in self.co2.items():
            if fuel not in self.carriers:
                self.r.warnings.append(f"CO2 rate of fuel {fuel!r} unused: no generator burns it")
        for (item, reason), props in self._skip.items():
            what = f" ({', '.join(sorted(set(props)))})" if props else ""
            if item.startswith("Scenario "):
                what = f" ({len(props)} rows)"
            self.r.unmapped.append(Unmapped(item + what, reason))
        n = self.n
        self.r.counts.update({
            "grid nodes": len(n.buses), "lines": len(n.lines), "links": len(n.links),
            "generators": len(n.generators), "batteries": len(n.storage_units),
            "loads": len(n.loads), "fuels": len(self.m.objects_of("Fuel")),
            "snapshots": len(n.snapshots),
        })


def convert(model: PlexosModel, options: Mapping[str, Any] | None = None,
            report: ImportReport | None = None) -> Conversion:
    """Convert a read PLEXOS model; the ledger says where every row went."""
    report = report or ImportReport("plexos", ["model.xml"])
    conv = _Converter(model, options or {}, report)
    conv.run()
    for b in model.broken:
        report.unmapped.append(Unmapped(f"{b.table} row {b.row_id}", b.reason))
    report.warnings.extend(model.warnings)
    return Conversion(conv.n, report, conv.sidecars, conv.ledger, conv.load_mw)


def read_plexos(paths: Sequence[Path], options: Mapping[str, Any]) -> ImportResult:
    """Read a PLEXOS .xml or .zip into an :class:`ImportResult`."""
    got = _read_input(paths)
    try:
        model = read_plexos_xml(io.BytesIO(got.xml))
    except PlexosRefused as exc:
        raise ImportFormatError(f"{got.xml_name}: {exc}") from exc
    report = ImportReport("plexos", got.source_files)
    report.assumed.append(Assumed(
        "PLEXOS file", "version", model.version or "unknown",
        f"saved with {model.saved_with or 'an unknown PLEXOS build'}"))
    if got.data_files:
        report.warnings.append(
            f"{len(got.data_files)} data file(s) given; data-file time series are read in a later slice")
    conv = convert(model, options, report)
    json.dumps(conv.sidecars, allow_nan=False)
    return ImportResult(conv.network, conv.report, conv.sidecars)
