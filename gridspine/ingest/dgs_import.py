"""
PowerFactory DGS import, step 2: the parsed tables as a pypsa network.

``read_dgs(paths, options)`` reads one ASCII ``.dgs`` (``dgs_parse``, DGS 5.x
or 7.x) or its Excel form (``dgs_xlsx``) and returns the shared
``ImportResult``. What becomes what:

- ``ElmTerm`` → Bus (``uknom`` kV). Terminals joined by a closed coupler
  (``ElmCoup`` ``on_off`` 1) of the same voltage become one bus, named after
  the first; every original name stays in the name map. An open coupler joins
  nothing and is listed.
- ``ElmLne`` + ``TypLne`` → Line, in ohm and siemens: r = ``rline`` · ``dline`` /
  ``nlnum``, x likewise, b = ``bline`` · 1e-6 · ``dline`` · ``nlnum``, g from
  ``gline`` the same way (0, assumed, when the type has none),
  s_nom = √3 · ``uline`` · ``sline`` · ``nlnum``.
- ``ElmTr2`` + ``TypTr2`` → Transformer, per unit on s_nom = ``strn`` · ``ntnum``:
  z = ``uktr``/100, r = ``pcutr``/(1000 · ``strn``), x = √(z² − r²); g = ``pfe``/
  (1000 · ``strn``), b = −√((``curmg``/100)² − g²). The tap sits on the HV side as
  pypsa and pandapower model it (``tap_side`` 0): a tap of 1 + ``dutap``/100 ·
  (``nntap`` − ``nntap0``) on the HV side, its inverse on the LV side, times the
  off-nominal ratio (``utrn_h``/HV bus kV)/(``utrn_l``/LV bus kV); the impedance is
  brought to the LV bus voltage, and with an LV-side tap to the tapped LV
  winding (times the tap squared), as pandapower's model does. phase_shift = 30° · ``nt2ag``.
- ``ElmTr3`` + ``TypTr3`` → a star bus and three Transformers (HV, MV, LV legs).
  Each pair's ``uktr3_*``/``pcut3_*`` is on the smaller rating of the pair; it is
  brought to the HV rating, split into legs, and each leg is stated per unit on
  its own winding's rating. Taps are taken at neutral (assumed).
- ``ElmLod`` → Load (``plini``/``qlini`` · ``scale0``); ``ElmGenstat`` → Generator
  PQ; ``ElmSym`` + ``TypSym`` → Generator PV (Slack with ``ip_ctrl`` 1), its
  ``usetp`` the bus voltage set-point; ``ElmXnet`` → Generator Slack (PV or PQ by
  ``bustp``). The file leaves an external grid unbounded, so its p_nom is the
  larger of its short-circuit level ``snss`` (when given) and the never-binding
  bound of ``gridspine.ingest.ratings`` (ceil(max(10 × Σ |S| of the in-service
  loads, the voltage-class floor of its bus)) MVA, used as MW), listed in
  ``assumed`` and marked ``p_nom_source`` = "assumed" (other generators read
  "file"; only "assumed" means assumed, so a row added later, empty or NaN, is
  not). A machine's p_nom is ``sgn`` · ``cosn``, or ``sgn``
  (MVA used as MW, assumed) without ``cosn``.
- ``ElmShnt`` (``shtype`` 1 R-L or 2 C) → ShuntImpedance, b = ±``qtotn`` ·
  ``ncapa``/``ncapx``/``ushnm``² (capacitive positive); g 0, assumed (the file
  carries no shunt loss). Other shunt types (filters) are not mapped.
- ``TypHmccur`` pointed at by a load's or static generator's ``phmc`` → that
  element's harmonic spectrum, "order:% of rated current" for every non-zero
  amplitude (the follow-up writer pads the union of orders with 0).
- ``StaSwitch`` open in a cubicle → that element out of service (listed).
- Out of service (``outserv`` 1) → kept with ``active`` False.

Every other class, every row that cannot be mapped, and every library type no
element uses goes to ``report.unmapped`` with the reason. A negative or zero
length, rating or voltage, ``nlnum``/``ntnum`` below 1, r above z, g above
|y| or any value that is not finite refuses the import, naming the item.

Engine cage: this module may import pypsa (owner ruling 2026-11-17); the
parser it reads with may not.
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pypsa

from gridspine.ingest.dgs_parse import LINE_COLUMN, DgsTables, parse_dgs
from gridspine.ingest.import_result import (
    Assumed,
    ImportFormatError,
    ImportReport,
    ImportResult,
    Unmapped,
)
from gridspine.ingest.ratings import (
    assumed_rating_mva,
    default_marginal_costs,
    source_column,
    total_load_mva,
)
from gridspine.schema.contracts import ContractError

FORMAT_ID = "dgs"
SQRT3 = math.sqrt(3.0)
#: Classes the mapping reads; every other class's rows are reported unmapped.
STRUCTURE = {"General", "ElmNet", "StaCubic", "StaSwitch", "ElmCoup"}
TYPES = {"TypLne", "TypTr2", "TypTr3", "TypSym", "TypHmccur"}
ELEMENTS = {
    "ElmTerm",
    "ElmLne",
    "ElmTr2",
    "ElmTr3",
    "ElmLod",
    "ElmGenstat",
    "ElmSym",
    "ElmXnet",
    "ElmShnt",
}
#: Plain names for the preview (RAW-NAMES-1), per class.
PLAIN = {
    "ElmTerm": "Buses",
    "ElmLne": "Lines",
    "ElmTr2": "Transformers",
    "ElmTr3": "Three-winding transformers",
    "ElmLod": "Loads",
    "ElmGenstat": "Static generators",
    "ElmSym": "Synchronous machines",
    "ElmXnet": "External grids",
    "ElmShnt": "Shunts",
    "ElmCoup": "Couplers",
    "StaSwitch": "Switches",
    "TypHmccur": "Harmonic spectra",
}
SHTYPE_L, SHTYPE_C = 1, 2
#: The metadata.json key the name map and switch list go under.
METADATA_KEY = "import"


def read_dgs(paths: Sequence[Path], options: Mapping[str, Any] | None = None):
    """
    Read one PowerFactory export into an ``ImportResult``: an ASCII ``.dgs``
    file (DGS 5.x or 7.x) or its Excel form (``.xlsx``, see ``dgs_xlsx``).
    """
    paths = [Path(p) for p in paths]
    if len(paths) != 1:
        raise ImportFormatError(
            f"A PowerFactory DGS import reads one .dgs or .xlsx file, got {len(paths)}"
        )
    path = paths[0]
    suffix = path.suffix.lower()
    if suffix not in (".dgs", ".xlsx"):
        raise ImportFormatError(f"{path.name} is not a .dgs or .xlsx file")
    try:
        if suffix == ".xlsx":
            from gridspine.ingest.dgs_xlsx import parse_dgs_xlsx

            tables = parse_dgs_xlsx(path)
        else:
            tables = parse_dgs(path)
    except ImportFormatError as exc:  # a workbook limit or an unreadable file
        raise ImportFormatError(f"{path.name}: {exc}") from exc
    except ContractError as exc:
        raise ImportFormatError(str(exc)) from exc
    try:
        return _Mapper(tables, path.name).run()
    except ContractError as exc:
        raise ImportFormatError(f"{path.name}: {exc}") from exc


def _int(item: str, what: str, v) -> int:
    """``v`` as an integer; text or a fraction is refused naming ``what``."""
    if (
        isinstance(v, bool)
        or not isinstance(v, numbers.Real)
        or not float(v).is_integer()  # a fraction, inf or nan
    ):
        raise ContractError(f"{item}: {what} {v!r} is not an integer")
    return int(v)


def _finite(item: str, what: str, v, *, positive=False, at_least=None) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ContractError(f"{item}: {what} is missing or not a number") from None
    if not math.isfinite(v):
        raise ContractError(f"{item}: {what} is missing or not finite")
    if positive and v <= 0:
        raise ContractError(f"{item}: {what} {v:g} must be above zero")
    if at_least is not None and v < at_least:
        raise ContractError(f"{item}: {what} {v:g} must be at least {at_least:g}")
    return v


class _Mapper:
    def __init__(self, tables: DgsTables, source: str):
        self.t = tables.tables
        self.dropped = tables.dropped
        self.where = tables.where
        self.source = source
        self.n = pypsa.Network()
        self.unmapped: list[Unmapped] = []
        self.assumed: list[Assumed] = []
        self.warnings: list[str] = []
        if tables.decimal_comma:
            self.warnings.append(
                "Decimal comma read as decimal mark: the file writes its numbers "
                "with a comma (158,77 is read as 158.77)."
            )
        self.counts: dict[str, int] = {}
        self.names: list[dict] = []  # DGS → network, every object
        self.switches: list[dict] = []
        self.dgs_names: dict[str, list[str]] = {}  # bus → its terminals' names
        # which DGS extras (DGS-EXTRAS-1) the import filled, named for the preview
        self.filled = {
            "harmonic_spectrum": {"Load": [], "Generator": []},
            "short_circuit": [],
        }
        self.used_types: set = set()
        self.cub = self._cubicles()
        self.bus_of = {}  # ElmTerm ID → bus name

    # --- helpers ----------------------------------------------------------
    def rows(self, cls):
        df = self.t.get(cls)
        return [] if df is None else list(df.iterrows())

    def label(self, cls, oid, row) -> str:
        name = row.get("loc_name") if hasattr(row, "get") else None
        name = name if isinstance(name, str) and name else f"ID {oid}"
        return f"{cls} {name!r} ({self.where(row[LINE_COLUMN])})"

    def skip(self, cls, oid, row, reason):
        self.unmapped.append(Unmapped(self.label(cls, oid, row), reason))

    def assume(self, asset, parameter, value, why):
        self.assumed.append(Assumed(asset, parameter, value, why))

    def get(self, row, attr, default=None):
        v = row.get(attr, default) if attr in row.index else default
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
        return v

    def opt(self, asset, row, attr, default, parameter):
        """``row[attr]``, or ``default`` listed as assumed when the file omits it."""
        v = self.get(row, attr)
        if v is None:
            self.assume(asset, parameter, default, f"the file gives no {attr}")
            return default
        return v

    def holds(self, cls, oid) -> bool:
        return oid is not None and cls in self.t and oid in self.t[cls].index

    def typ(self, cls, oid, row, tcls):
        tid = self.get(row, "typ_id")
        if not self.holds(tcls, tid):
            self.skip(cls, oid, row, f"no {tcls} type")
            return None
        self.used_types.add(tid)
        return self.t[tcls].loc[tid]

    def unique(self, component, name) -> str:
        df = self.n.components[component].static
        out, k = name, 1
        while out in df.index:
            k += 1
            out = f"{name} ({k})"
        if out != name:
            self.warnings.append(
                f"Two {component} items are named {name!r}; the second is {out!r}"
            )
        return out

    def _cubicles(self) -> dict:
        """Element ID → {side: (terminal ID, cubicle ID)}."""
        out: dict = {}
        for cid, c in self.rows("StaCubic"):
            obj, term = self.get(c, "obj_id"), self.get(c, "fold_id")
            if obj is None or term is None:
                self.skip(
                    "StaCubic", cid, c, "a cubicle without its element or terminal"
                )
                continue
            side = _int(
                self.label("StaCubic", cid, c), "obj_bus", self.get(c, "obj_bus", 0)
            )
            out.setdefault(obj, {})[side] = (term, cid)
        return out

    def ends(self, cls, oid, row, sides) -> list | None:
        """The bus names at ``sides``, or None (reported) when one is missing."""
        got = self.cub.get(oid, {})
        buses = []
        for s in sides:
            if s not in got or got[s][0] not in self.bus_of:
                self.skip(cls, oid, row, f"side {s} is not connected to a terminal")
                return None
            buses.append(self.bus_of[got[s][0]])
        return buses

    def cls_of(self, oid) -> str:
        """The class holding ``oid`` (IDs are unique across the file)."""
        return next(c for c, df in self.t.items() if oid in df.index)

    def active(self, oid, row) -> bool:
        out = _int(
            self.label(self.cls_of(oid), oid, row),
            "outserv",
            self.get(row, "outserv", 0),
        )
        return out == 0 and oid not in self.open_ends

    def record(self, cls, oid, row, component, name):
        self.names.append(
            {
                "dgs_class": cls,
                "dgs_id": str(oid),
                "dgs_name": str(self.get(row, "loc_name", "")),
                "component": component,
                "name": name,
            }
        )
        self.counts[PLAIN[cls]] = self.counts.get(PLAIN[cls], 0) + 1

    # --- the run ----------------------------------------------------------
    def run(self) -> ImportResult:
        self._switches()
        self._buses()
        self._lines()
        self._trafos()
        self._trafo3()
        self._phase_warning()
        self._loads()
        self._sgens()
        self._machines()
        self._xnets()
        self._shunts()
        # no cost attribute is read from DGS, so every generator takes the default
        for row in default_marginal_costs(self.n.generators):
            self.assume(*row)
        for component in ("Load", "Generator"):
            df = self.n.components[component].static
            for col in ("harmonic_source", "harmonic_spectrum"):
                if col in df.columns:  # units added after the first spectrum
                    df[col] = df[col].fillna("")
        self._leftovers()
        self._islands()
        for label, names in (
            (
                "Loads given a harmonic spectrum",
                self.filled["harmonic_spectrum"]["Load"],
            ),
            (
                "Generators given a harmonic spectrum",
                self.filled["harmonic_spectrum"]["Generator"],
            ),
            ("Slack generators given short-circuit data", self.filled["short_circuit"]),
        ):
            if names:
                self.counts[label] = len(names)
        report = ImportReport(
            source_format=FORMAT_ID,
            source_files=[self.source],
            counts=self.counts,
            unmapped=self.unmapped,
            assumed=self.assumed,
            warnings=self.warnings,
        )
        sidecars = {
            "metadata.json": {
                METADATA_KEY: {
                    "format": FORMAT_ID,
                    "source": self.source,
                    "names": self.names,
                    "switches": self.switches,
                    "dgs_names": self.dgs_names,
                    "filled": self.filled,
                }
            }
        }
        return ImportResult(network=self.n, report=report, sidecars=sidecars)

    def _switches(self):
        """Closed couplers join terminals; open switches take their element out."""
        parent = {tid: tid for tid, _ in self.rows("ElmTerm")}
        kv = {tid: self.get(r, "uknom") for tid, r in self.rows("ElmTerm")}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for oid, row in self.rows("ElmCoup"):
            closed = (
                _int(
                    self.label("ElmCoup", oid, row),
                    "on_off",
                    self.get(row, "on_off", 1),
                )
                == 1
            )
            ends = self.cub.get(oid, {})
            t0, t1 = (ends.get(s, (None,))[0] for s in (0, 1))
            if t0 not in parent or t1 not in parent:
                self.skip("ElmCoup", oid, row, "a coupler not between two terminals")
                continue
            self.switches.append(
                {
                    "dgs_name": str(self.get(row, "loc_name", "")),
                    "kind": "coupler",
                    "closed": closed,
                }
            )
            self.counts[PLAIN["ElmCoup"]] = self.counts.get(PLAIN["ElmCoup"], 0) + 1
            if not closed:
                continue
            if kv[t0] != kv[t1]:
                self.skip(
                    "ElmCoup",
                    oid,
                    row,
                    f"joins terminals of {kv[t0]} kV and {kv[t1]} kV; left open",
                )
                self.switches[-1]["closed"] = False
                continue
            a, b = find(t0), find(t1)
            if a != b:
                first, second = sorted((a, b), key=self._term_order)
                parent[second] = first
        self.root = {tid: find(tid) for tid in parent}
        self.open_ends: set = set()
        cub_owner = {
            cid: obj for obj, sides in self.cub.items() for _, cid in sides.values()
        }
        for sid, row in self.rows("StaSwitch"):
            closed = (
                _int(
                    self.label("StaSwitch", sid, row),
                    "on_off",
                    self.get(row, "on_off", 1),
                )
                == 1
            )
            obj = cub_owner.get(self.get(row, "fold_id"))
            self.switches.append(
                {
                    "dgs_name": str(self.get(row, "loc_name", "")),
                    "kind": "switch",
                    "closed": closed,
                }
            )
            self.counts[PLAIN["StaSwitch"]] = self.counts.get(PLAIN["StaSwitch"], 0) + 1
            if obj is None:
                self.skip(
                    "StaSwitch", sid, row, "its cubicle belongs to no imported element"
                )
            elif not closed:
                self.open_ends.add(obj)

    def _term_order(self, tid):
        return int(self.t["ElmTerm"].loc[tid, LINE_COLUMN])

    def _buses(self):
        for tid, row in self.rows("ElmTerm"):
            item = self.label("ElmTerm", tid, row)
            root = self.root[tid]
            if root == tid:
                v = _finite(
                    item, "nominal voltage (kV)", self.get(row, "uknom"), positive=True
                )
                name = self.unique("Bus", str(self.get(row, "loc_name", f"bus {tid}")))
                self.n.add("Bus", name, v_nom=v)
                self.bus_of[tid] = name
        for tid, row in self.rows("ElmTerm"):
            root = self.root[tid]
            self.bus_of[tid] = self.bus_of[root]
            # every terminal's own name, the merged ones included (GK A 28)
            self.dgs_names.setdefault(self.bus_of[tid], []).append(
                str(self.get(row, "loc_name", ""))
            )
            self.record("ElmTerm", tid, row, "Bus", self.bus_of[tid])
            if root != tid:
                self.counts[PLAIN["ElmTerm"]] -= 1  # one bus, two terminals
                self.assume(
                    self.bus_of[tid],
                    "Terminals joined",
                    str(self.get(row, "loc_name", "")),
                    "a closed coupler joins this terminal to the bus",
                )

    def _lines(self):
        for oid, row in self.rows("ElmLne"):
            item = self.label("ElmLne", oid, row)
            typ = self.typ("ElmLne", oid, row, "TypLne")
            ends = self.ends("ElmLne", oid, row, (0, 1)) if typ is not None else None
            if ends is None:
                continue
            km = _finite(item, "length (km)", self.get(row, "dline"), positive=True)
            par = _finite(item, "parallel lines", self.get(row, "nlnum", 1), at_least=1)
            ukv = _finite(
                item, "type rated voltage (kV)", self.get(typ, "uline"), positive=True
            )
            ika = _finite(
                item, "type rated current (kA)", self.get(typ, "sline"), positive=True
            )
            r = _finite(item, "resistance per km", self.get(typ, "rline"))
            x = _finite(item, "reactance per km", self.get(typ, "xline"))
            name = self.unique("Line", str(self.get(row, "loc_name", f"line {oid}")))
            b = _finite(
                item,
                "susceptance per km",
                self.opt(name, typ, "bline", 0.0, "Susceptance"),
            )
            g = self.get(typ, "gline")
            if g is None:
                g = 0.0
                self.assume(name, "Conductance", 0.0, "the line type gives no gline")
            g = _finite(item, "conductance per km", g)
            if r < 0 or x <= 0 or b < 0 or g < 0:
                raise ContractError(
                    f"{item}: r, b and g must not be negative and x must be above zero"
                )
            self.n.add(
                "Line",
                name,
                bus0=ends[0],
                bus1=ends[1],
                r=r * km / par,
                x=x * km / par,
                b=b * 1e-6 * km * par,
                g=g * 1e-6 * km * par,
                s_nom=SQRT3 * ukv * ika * par,
                length=km,
                active=self.active(oid, row),
            )
            self.record("ElmLne", oid, row, "Line", name)

    def _trafos(self):
        for oid, row in self.rows("ElmTr2"):
            item = self.label("ElmTr2", oid, row)
            typ = self.typ("ElmTr2", oid, row, "TypTr2")
            ends = self.ends("ElmTr2", oid, row, (0, 1)) if typ is not None else None
            if ends is None:
                continue
            name = self.unique(
                "Transformer", str(self.get(row, "loc_name", f"trafo {oid}"))
            )
            sn = _finite(item, "rating (MVA)", self.get(typ, "strn"), positive=True)
            uh = _finite(
                item, "HV rated voltage (kV)", self.get(typ, "utrn_h"), positive=True
            )
            ul = _finite(
                item, "LV rated voltage (kV)", self.get(typ, "utrn_l"), positive=True
            )
            uk = _finite(
                item, "short-circuit voltage (%)", self.get(typ, "uktr"), positive=True
            )
            pcu = _finite(
                item,
                "copper losses (kW)",
                self.opt(name, typ, "pcutr", 0.0, "Copper losses (kW)"),
            )
            pfe = _finite(
                item,
                "no-load losses (kW)",
                self.opt(name, typ, "pfe", 0.0, "No-load losses (kW)"),
            )
            i0 = _finite(
                item,
                "magnetising current (%)",
                self.opt(name, typ, "curmg", 0.0, "Magnetising current (%)"),
            )
            par = _finite(item, "parallel units", self.get(row, "ntnum", 1), at_least=1)
            z, r = uk / 100.0, pcu / (1000.0 * sn)
            if r < 0 or r > z:
                raise ContractError(f"{item}: copper losses give r {r:g} above z {z:g}")
            y, g = i0 / 100.0, pfe / (1000.0 * sn)
            if g < 0 or g > y:
                raise ContractError(
                    f"{item}: no-load losses give g {g:g} above |y| {y:g}"
                )
            hv, lv = ends
            vh, vl = self.n.buses.at[hv, "v_nom"], self.n.buses.at[lv, "v_nom"]
            step = _finite(
                item, "tap step (%)", self.opt(name, typ, "dutap", 0.0, "Tap step (%)")
            )
            n0 = _int(
                item, "nntap0", self.opt(name, typ, "nntap0", 0, "Neutral tap position")
            )
            pos = _int(item, "nntap", self.opt(name, row, "nntap", n0, "Tap position"))
            tap = 1.0 + step / 100.0 * (pos - n0)
            if tap <= 0:
                raise ContractError(f"{item}: tap position {pos} gives ratio {tap:g}")
            side = _int(
                item, "tap_side", self.opt(name, typ, "tap_side", 0, "Tap side")
            )
            if side not in (0, 1):
                raise ContractError(f"{item}: tap side {side} is not 0 (HV) or 1 (LV)")
            lv_side = side == 1
            off = (uh / vh) / (ul / vl)
            if not (math.isclose(uh, vh) and math.isclose(ul, vl)):
                self.assume(
                    name,
                    "Off-nominal ratio",
                    round(off, 9),
                    f"rated {uh:g}/{ul:g} kV on buses of {vh:g}/{vl:g} kV: "
                    "folded into the tap ratio, impedance on the LV bus voltage",
                )
            zbase = (ul / vl) ** 2  # rated LV voltage to the LV bus voltage
            if lv_side:
                zbase *= tap**2  # the impedance follows the tapped LV winding
            self.n.add(
                "Transformer",
                name,
                bus0=hv,
                bus1=lv,
                s_nom=sn * par,
                r=r * zbase,
                x=math.sqrt(z * z - r * r) * zbase,
                g=g / zbase,
                b=-math.sqrt(max(y * y - g * g, 0.0)) / zbase,
                tap_ratio=off * (1.0 / tap if lv_side else tap),
                tap_side=0,
                tap_position=pos - n0,
                phase_shift=30.0
                * _finite(
                    item,
                    "vector group number",
                    self.opt(name, typ, "nt2ag", 0.0, "Vector group number"),
                ),
                active=self.active(oid, row),
            )
            self.record("ElmTr2", oid, row, "Transformer", name)

    def _trafo3(self):
        for oid, row in self.rows("ElmTr3"):
            item = self.label("ElmTr3", oid, row)
            typ = self.typ("ElmTr3", oid, row, "TypTr3")
            ends = self.ends("ElmTr3", oid, row, (0, 1, 2)) if typ is not None else None
            if ends is None:
                continue
            if not self.active(oid, row):
                # D6: pypsa honours a leg's ``active``, but the star bus would
                # remain as an island, so the whole unit is left out
                self.skip(
                    "ElmTr3", oid, row, "out of service: left out with its star point"
                )
                continue
            name = str(self.get(row, "loc_name", f"trafo3 {oid}"))
            par = _finite(
                item,
                "parallel units",
                self.opt(name, row, "nt3nm", 1, "Parallel units"),
            )
            if par != 1:
                raise ContractError(
                    f"{item}: {par:g} parallel three-winding units are not imported"
                )
            w = ("h", "m", "l")
            s = {
                k: _finite(
                    item,
                    f"{k.upper()}V rating (MVA)",
                    self.get(typ, f"strn3_{k}"),
                    positive=True,
                )
                for k in w
            }
            u = {
                k: _finite(
                    item,
                    f"{k.upper()}V rated voltage (kV)",
                    self.get(typ, f"utrn3_{k}"),
                    positive=True,
                )
                for k in w
            }
            # Pair data: uktr3_h/pcut3_h is HV-MV, _m is MV-LV, _l is LV-HV, each
            # on the smaller rating of its pair. Brought to the HV rating as
            # (r, x), each leg is half the sum of its two pairs minus the third.
            pair = {}
            for k, (i, j) in {"h": "hm", "m": "ml", "l": "lh"}.items():
                base = min(s[i], s[j])
                uk = _finite(
                    item,
                    f"short-circuit voltage {i}{j} (%)",
                    self.get(typ, f"uktr3_{k}"),
                    positive=True,
                )
                pcu = _finite(
                    item, f"copper losses {i}{j} (kW)", self.get(typ, f"pcut3_{k}", 0.0)
                )
                z, r = uk / 100.0, pcu / (1000.0 * base)
                if r < 0 or r > z:
                    raise ContractError(f"{item}: copper losses {i}{j} give r above z")
                to_hv = s["h"] / base
                pair[frozenset((i, j))] = (r * to_hv, math.sqrt(z * z - r * r) * to_hv)
            leg = {}
            for i, j, k in ("hml", "mlh", "lhm"):
                (r1, x1), (r2, x2), (r3, x3) = (
                    pair[frozenset(p)] for p in ((i, j), (k, i), (j, k))
                )
                leg[i] = ((r1 + r2 - r3) / 2.0, (x1 + x2 - x3) / 2.0)
            # vector group: each leg shifts by its winding's group against HV,
            # 30° · (nt3ag_k − nt3ag_h), as pandapower's PowerFactory import
            group = {
                k: _finite(
                    item,
                    f"{k.upper()}V vector group number",
                    self.opt(
                        name,
                        typ,
                        f"nt3ag_{k}",
                        0.0,
                        f"{k.upper()}V vector group number",
                    ),
                )
                for k in w
            }
            # magnetising on the HV leg, on the HV rating
            i0 = _finite(
                item,
                "magnetising current (%)",
                self.opt(name, typ, "curm3", 0.0, "Magnetising current (%)"),
            )
            pfe = _finite(
                item,
                "no-load losses (kW)",
                self.opt(name, typ, "pfe", 0.0, "No-load losses (kW)"),
            )
            y, g = i0 / 100.0, pfe / (1000.0 * s["h"])
            if g < 0 or g > y:
                raise ContractError(
                    f"{item}: no-load losses give g {g:g} above |y| {y:g}"
                )
            star = self.unique("Bus", f"{name} star")
            self.n.add("Bus", star, v_nom=self.n.buses.at[ends[0], "v_nom"])
            self.assume(
                star,
                "Star point",
                self.n.buses.at[star, "v_nom"],
                "a three-winding transformer becomes three legs on a star bus at the HV voltage",
            )
            self.assume(
                name, "Tap position", 0, "three-winding taps are taken at neutral"
            )
            for k, bus in zip(w, ends):
                r, x = leg[k]
                scale = s[k] / s["h"]  # HV-rating per unit → own rating
                vb = self.n.buses.at[bus, "v_nom"]
                vh = self.n.buses.at[ends[0], "v_nom"]
                off = (u["h"] / vh) / (u[k] / vb)
                leg_name = self.unique("Transformer", f"{name} {k.upper()}V")
                if not math.isclose(off, 1.0):
                    self.assume(
                        leg_name,
                        "Off-nominal ratio",
                        round(off, 9),
                        f"rated {u['h']:g}/{u[k]:g} kV on buses of {vh:g}/{vb:g} kV: "
                        "folded into the tap ratio, impedance on the bus voltage",
                    )
                self.n.add(
                    "Transformer",
                    leg_name,
                    bus0=star,
                    bus1=bus,
                    s_nom=s[k],
                    r=r * scale * (u[k] / vb) ** 2,
                    x=x * scale * (u[k] / vb) ** 2,
                    g=g / (u[k] / vb) ** 2 if k == "h" else 0.0,
                    b=-math.sqrt(y * y - g * g) / (u[k] / vb) ** 2 if k == "h" else 0.0,
                    tap_ratio=off,
                    tap_side=0,
                    phase_shift=30.0 * (group[k] - group["h"]),
                )
            self.record("ElmTr3", oid, row, "Transformer", f"{name} HV/MV/LV")

    def _phase_warning(self):
        if (self.n.transformers.phase_shift % 360 != 0).any():
            self.warnings.append(
                "Some transformers shift the phase angle (vector group). The AC load "
                "flow converges from a linear-flow start, not from a flat start."
            )

    def _harmonics(self, cls, oid, row) -> str | None:
        hid = self.get(row, "phmc")
        if hid is None:
            return None
        if not self.holds("TypHmccur", hid):
            self.skip(
                cls, oid, row, "its harmonic source is not a harmonic current type"
            )
            return None
        h = self.t["TypHmccur"].loc[hid]
        new = hid not in self.used_types  # the count is of spectra, not of uses
        self.used_types.add(hid)
        orders, ampl = list(self.get(h, "norder", [])), list(self.get(h, "ampl", []))
        if len(orders) != len(ampl):
            self.skip("TypHmccur", hid, h, "orders and amplitudes differ in length")
            return None
        pairs = [(int(o), float(a)) for o, a in zip(orders, ampl) if float(a) != 0.0]
        for o, a in pairs:
            if o < 2 or not math.isfinite(a) or a < 0:
                raise ContractError(
                    f"{self.label('TypHmccur', hid, h)}: order {o} amplitude {a:g}"
                )
        if new:
            self.counts[PLAIN["TypHmccur"]] = self.counts.get(PLAIN["TypHmccur"], 0) + 1
        return ", ".join(f"{o}:{a:g}" for o, a in pairs)

    def _injection(self, cls, component):
        for oid, row in self.rows(cls):
            ends = self.ends(cls, oid, row, (0,))
            if ends is None:
                continue
            item = self.label(cls, oid, row)
            name = self.unique(
                component, str(self.get(row, "loc_name", f"{cls} {oid}"))
            )
            yield oid, row, item, name, ends[0]

    def _loads(self):
        for oid, row, item, name, bus in self._injection("ElmLod", "Load"):
            sc = _finite(item, "scaling", self.get(row, "scale0", 1.0))
            self.n.add(
                "Load",
                name,
                bus=bus,
                p_set=_finite(
                    item,
                    "active power (MW)",
                    self.opt(name, row, "plini", 0.0, "Active power (MW)"),
                )
                * sc,
                q_set=_finite(
                    item,
                    "reactive power (Mvar)",
                    self.opt(name, row, "qlini", 0.0, "Reactive power (Mvar)"),
                )
                * sc,
                active=self.active(oid, row),
            )
            self._spectrum("Load", name, self._harmonics("ElmLod", oid, row), None)
            self.record("ElmLod", oid, row, "Load", name)

    def _p_nom(self, item, name, row) -> float:
        sn = _finite(item, "rating (MVA)", self.get(row, "sgn"), positive=True)
        cosn = self.get(row, "cosn")
        if cosn is None:
            self.assume(
                name,
                "Rating (MW)",
                sn,
                "no rated power factor: the MVA rating is used as MW",
            )
            return sn
        cosn = _finite(item, "rated power factor", cosn, positive=True)
        if cosn > 1:
            raise ContractError(f"{item}: rated power factor {cosn:g} is above 1")
        return sn * cosn

    def _sgens(self):
        for oid, row, item, name, bus in self._injection("ElmGenstat", "Generator"):
            sc = _finite(item, "scaling", self.get(row, "scale0", 1.0))
            self.n.add(
                "Generator",
                name,
                bus=bus,
                control="PQ",
                p_nom=self._p_nom(item, name, row),
                p_set=_finite(
                    item,
                    "active power (MW)",
                    self.opt(name, row, "pgini", 0.0, "Active power (MW)"),
                )
                * sc,
                q_set=_finite(
                    item,
                    "reactive power (Mvar)",
                    self.opt(name, row, "qgini", 0.0, "Reactive power (Mvar)"),
                )
                * sc,
                active=self.active(oid, row),
            )
            self._spectrum(
                "Generator",
                name,
                self._harmonics("ElmGenstat", oid, row),
                float(self.get(row, "sgn")),
            )
            self.record("ElmGenstat", oid, row, "Generator", name)

    def _machines(self):
        for oid, row, item, name, bus in self._injection("ElmSym", "Generator"):
            typ = self.typ("ElmSym", oid, row, "TypSym")
            if typ is None:
                continue
            sc = _finite(item, "scaling", self.get(row, "scale0", 1.0))
            slack = _int(item, "ip_ctrl", self.get(row, "ip_ctrl", 0)) == 1
            self.n.add(
                "Generator",
                name,
                bus=bus,
                control="Slack" if slack else "PV",
                p_nom=self._p_nom(item, name, typ),
                p_set=_finite(
                    item,
                    "active power (MW)",
                    self.opt(name, row, "pgini", 0.0, "Active power (MW)"),
                )
                * sc,
                active=self.active(oid, row),
            )
            if self.active(oid, row):  # an idle machine leaves the bus set-point
                self._setpoint(item, bus, row)
            self.record("ElmSym", oid, row, "Generator", name)

    def _setpoint(self, item, bus, row):
        v = _finite(
            item, "voltage set-point (p.u.)", self.get(row, "usetp", 1.0), positive=True
        )
        self.n.buses.at[bus, "v_mag_pu_set"] = v

    def _xnets(self):
        bounded = []
        for oid, row, item, name, bus in self._injection("ElmXnet", "Generator"):
            kind = str(self.get(row, "bustp", "SL")).strip().upper()
            control = {"SL": "Slack", "PV": "PV", "PQ": "PQ"}.get(kind)
            if control is None:
                self.skip("ElmXnet", oid, row, f"bus type {kind!r} is not SL, PV or PQ")
                continue
            p = q = 0.0
            if control != "Slack":
                p = _finite(
                    item,
                    "active power (MW)",
                    self.opt(name, row, "pgini", 0.0, "Active power (MW)"),
                )
            if control == "PQ":
                q = _finite(
                    item,
                    "reactive power (Mvar)",
                    self.opt(name, row, "qgini", 0.0, "Reactive power (Mvar)"),
                )
            p_nom, why = self._grid_limit(item, bus, row)
            self.n.add(
                "Generator",
                name,
                bus=bus,
                control=control,
                p_nom=p_nom,
                p_set=p,
                q_set=q,
                active=self.active(oid, row),
            )
            self.assume(name, "active power limit (MW)", p_nom, why)
            bounded.append(name)
            if control != "PQ" and self.active(oid, row):
                self._setpoint(item, bus, row)
            self._grid_strength(item, name, row)
            self.record("ElmXnet", oid, row, "Generator", name)
        if bounded:  # marks a limit results must not read as real
            gens = self.n.generators
            gens["p_nom_source"] = source_column(gens.index, bounded)

    def _grid_limit(self, item, bus, row):
        """``(p_nom, why)`` for an external grid, which the file leaves unbounded."""
        load = total_load_mva(self.n.loads)
        kv = self.n.buses.at[bus, "v_nom"]
        bound = assumed_rating_mva(load, kv)
        why = (
            "the external grid is unbounded in the file (ElmXnet), so a limit that "
            f"never binds is used: 10 × the total load {load:.1f} MVA, or the "
            f"{kv:g} kV class floor if higher"
        )
        sk = self.get(row, "snss")
        if sk is not None:
            sk = _finite(item, "sk max mva", sk, positive=True)
            if sk > bound:
                return sk, (
                    f"{why}; its short-circuit level {sk:g} MVA is higher, so that "
                    "is used"
                )
        return bound, why

    def _shunts(self):
        for oid, row, item, name, bus in self._injection("ElmShnt", "ShuntImpedance"):
            kind = _int(item, "shtype", self.get(row, "shtype", -1))
            if kind not in (SHTYPE_L, SHTYPE_C):
                self.skip(
                    "ElmShnt", oid, row, f"shunt type {kind} (a filter) is not imported"
                )
                continue
            un = _finite(
                item, "rated voltage (kV)", self.get(row, "ushnm"), positive=True
            )
            q = _finite(item, "rated reactive power (Mvar)", self.get(row, "qtotn"))
            steps = _finite(item, "steps", self.get(row, "ncapx", 1), at_least=1)
            on = _finite(item, "steps in service", self.get(row, "ncapa", steps))
            if q < 0 or on < 0 or on > steps:
                raise ContractError(
                    f"{item}: reactive power and steps in service must be 0..rated"
                )
            b = q * on / steps / un**2
            self.n.add(
                "ShuntImpedance",
                name,
                bus=bus,
                g=0.0,
                b=b if kind == SHTYPE_C else -b,
                active=self.active(oid, row),
            )
            self.assume(
                name, "Active power loss", 0.0, "the DGS file carries no shunt loss"
            )
            self.record("ElmShnt", oid, row, "ShuntImpedance", name)

    def _spectrum(self, component, name, spectrum, rated_mva):
        """
        Where an imported spectrum goes (owner decision DGS-EXTRAS-1): the
        fields the harmonic screen reads. ``harmonic_source`` stays empty,
        since the screen accepts only its topologies there; with a spectrum
        it reads it as the unit's own ("vendor"). The screen scales the % by
        ``rated_mva``; a load's DGS row carries no rating, so it is warned.
        """
        if spectrum is None:
            return
        df = self.n.components[component].static
        for col in ("harmonic_source", "harmonic_spectrum"):
            if col not in df.columns:
                df[col] = ""
        df.at[name, "harmonic_spectrum"] = spectrum
        self.filled["harmonic_spectrum"][component].append(name)
        if "rated_mva" not in df.columns:
            df["rated_mva"] = math.nan
        if rated_mva is None:
            self.warnings.append(
                f"{component} {name!r} has a harmonic spectrum but the file gives no "
                "rated apparent power: enter it for the harmonic screen"
            )
        else:
            df.at[name, "rated_mva"] = rated_mva

    def _grid_strength(self, item, name, row):
        """The grid's short-circuit level and R/X, max and min, on the slack."""
        cols = {
            "sk_max_mva": ("snss", True),
            "sk_min_mva": ("snssmin", True),
            "rx_max": ("rntxn", False),
            "rx_min": ("rntxnmin", False),
        }
        df = self.n.generators
        for col, (attr, positive) in cols.items():
            v = self.get(row, attr)
            if v is None:
                continue
            v = _finite(item, f"{col.replace('_', ' ')}", v, positive=positive)
            if v < 0:
                raise ContractError(f"{item}: {col} {v:g} is negative")
            if col not in df.columns:
                df[col] = math.nan
            df.at[name, col] = v
            if name not in self.filled["short_circuit"]:
                self.filled["short_circuit"].append(name)

    def _leftovers(self):
        for cls, cols in self.dropped.items():
            for col in cols:  # every class, its rows imported or not
                self.unmapped.append(
                    Unmapped(
                        f"{cls} column {col!r}",
                        "a DGS 7.x matrix attribute is not imported",
                    )
                )
        for cls, df in self.t.items():
            if cls in STRUCTURE or cls in ELEMENTS:
                continue
            for oid, row in df.iterrows():
                if cls in TYPES:
                    if oid not in self.used_types:
                        self.skip(cls, oid, row, "a type no imported element uses")
                else:
                    self.skip(
                        cls, oid, row, f"PowerFactory class {cls} is not imported"
                    )

    def _islands(self):
        """Report bus groups that have no slack or PV source after open switches."""
        buses = list(self.n.buses.index)
        if not buses:
            return
        parent = {b: b for b in buses}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for comp in ("Line", "Transformer"):
            df = self.n.components[comp].static
            for b0, b1 in df.loc[df.active, ["bus0", "bus1"]].itertuples(index=False):
                parent[find(b0)] = find(b1)
        gens = self.n.generators
        fed = {find(b) for b in gens.loc[gens.active & (gens.control != "PQ"), "bus"]}
        dead = sorted(b for b in buses if find(b) not in fed)
        if dead:
            self.warnings.append(
                "These buses have no slack or voltage-controlling source after open "
                f"switches and out-of-service items, so the AC load flow cannot solve "
                f"them: {', '.join(dead)}"
            )
