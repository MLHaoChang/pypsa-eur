"""
PSS/E RAW case reader: the file's records as tables, converted to plain units.

This module is the pure half of the PSS/E import (owner decision IMPORTS-1). It
reads a RAW file into one ``pandas.DataFrame`` per record type and turns PSS/E's
per-unit conventions into physical values (ohms, siemens, MW, kV) and plain
per-unit ratios. It builds no network: the importer maps these tables onto one.

**Versions.** v33, v35 and v36. A file is read section by section in the fixed
order its version defines (``raw_writer.SECTIONS``, the order PSS/E itself
writes); the ``0 / END OF ...`` comments are not trusted, since real files spell
section names differently. v34 and any other revision is refused: no v34 file
under a licence we can ship is available to test against.

**Free format.** Fields are separated by commas or blanks; text in single or
double quotes is one field; ``/`` starts a comment outside quotes; an empty field
between two commas, and every field left off the end of a record, takes PSS/E's
default for it. A record whose first field is ``0`` ends its section; ``Q`` ends
the file. A required field that is missing or not a number raises
``ContractError`` naming the line.

**Unit conventions** (the PSS/E Program Operation Manual is licensed, so the
formulas are cited from PowerModels.jl ``src/io/psse.jl``, BSD-3, which follows
the PSS/E Program Application Guide eq. 4.21b):

- Branch R, X, B and the line-end shunts GI, BI, GJ, BJ are per unit on the system
  base SBASE and the from-bus base kV. Ohms: ``R * kV**2 / SBASE``; siemens:
  ``B * SBASE / kV**2``.
- Fixed and switched shunts: GL (MW) and BL (Mvar) at 1.0 pu voltage; siemens:
  ``GL / kV**2``. BL > 0 is capacitive.
- Loads: constant power PL/QL, constant current IP/IQ and constant admittance
  YP/YQ, each given at 1.0 pu voltage. YQ is negative for an inductive load, so the
  reactive demand at 1.0 pu is ``QL + IQ - YQ``.
- Transformer impedance, CZ: 1 pu on SBASE and winding bus base; 2 pu on the
  winding base SBASEij and NOMVi (re-based by ``SBASE / SBASEij * (NOMVi / kVi)**2``,
  NOMVi = 0 meaning the bus base); 3 R is the load loss in watts
  (``R = W * 1e-6 / SBASEij``) and X the impedance magnitude (``X = sqrt(Z**2 - R**2)``),
  then as CZ = 2.
- Transformer ratio, CW: 1 WINDV in pu of the bus base kV; 2 WINDV in kV; 3 WINDV in
  pu of NOMV. Each winding's ratio in pu of its bus base is ``t_k``. A two-winding
  transformer is an ideal ``t1/t2`` ratio at the winding-1 bus in series with the
  impedance scaled by ``t2**2`` (PAG eq. 4.21b).
- Magnetising admittance, CM: 1 G, B pu on SBASE at the winding-1 bus; 2 MAG1 is the
  no-load loss in watts and MAG2 the exciting current in pu on SBASE1-2 and NOMV1, so
  ``G = W * 1e-6 / SBASE * (kV1 / NOMV1)**2`` and ``B = -sqrt(Y**2 - G**2)`` with
  ``Y = I0 * SBASE1-2 / SBASE * (kV1 / NOMV1)**2``.
- Three-winding transformers become a star: ``Z1 = (Z12 + Z31 - Z23) / 2`` and its
  rotations, each winding an ideal ``t_k`` ratio and ``Z_k`` from its bus to the star
  point. STAT 2, 3 and 4 take winding 2, 3 and 1 out of service.

**Never silently.** Every record of a section this reader does not map (DC links,
FACTS, GNE, induction machines, impedance-correction tables, inter-area transfers,
switching devices, substations) is one row of ``unmapped`` with its line number;
a section whose record boundaries depend on fields this reader does not decode is
one row for the whole section with its line count. Every default the reader
assumes for a value the model needs is a row of ``assumed``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from gridspine.handoff.raw_writer import SECTIONS
from gridspine.schema.contracts import ContractError

VERSIONS = (33, 35, 36)

# A field spec is (name, default). ``REQ`` marks a field with no default.
REQ = object()
#: Defaults that depend on another value: resolved per record.
_BUS = "<bus>"  # the record's bus AREA / ZONE / OWNER
_SBASE = "<sbase>"  # the system base
_CW = "<cw>"  # 1.0, or the bus base kV when CW = 2


def _owners(n=4):
    out = []
    for k in range(1, n + 1):
        out += [(f"O{k}", _BUS if k == 1 else 0), (f"F{k}", 1.0)]
    return out


_BUS_FIELDS = [
    ("I", REQ),
    ("NAME", ""),
    ("BASKV", 0.0),
    ("IDE", 1),
    ("AREA", 1),
    ("ZONE", 1),
    ("OWNER", 1),
    ("VM", 1.0),
    ("VA", 0.0),
    ("NVHI", 1.1),
    ("NVLO", 0.9),
    ("EVHI", 1.1),
    ("EVLO", 0.9),
]
_LOAD_FIELDS = [
    ("I", REQ),
    ("ID", "1"),
    ("STATUS", 1),
    ("AREA", _BUS),
    ("ZONE", _BUS),
    ("PL", 0.0),
    ("QL", 0.0),
    ("IP", 0.0),
    ("IQ", 0.0),
    ("YP", 0.0),
    ("YQ", 0.0),
    ("OWNER", _BUS),
    ("SCALE", 1),
    ("INTRPT", 0),
]
_LOAD_FIELDS_35 = _LOAD_FIELDS + [
    ("DGENP", 0.0),
    ("DGENQ", 0.0),
    ("DGENM", 0),
    ("LOADTYPE", ""),
]
_FSHUNT_FIELDS = [("I", REQ), ("ID", "1"), ("STATUS", 1), ("GL", 0.0), ("BL", 0.0)]
_GEN_HEAD = [
    ("I", REQ),
    ("ID", "1"),
    ("PG", 0.0),
    ("QG", 0.0),
    ("QT", 9999.0),
    ("QB", -9999.0),
    ("VS", 1.0),
    ("IREG", 0),
]
_GEN_MID = [
    ("MBASE", _SBASE),
    ("ZR", 0.0),
    ("ZX", 1.0),
    ("RT", 0.0),
    ("XT", 0.0),
    ("GTAP", 1.0),
    ("STAT", 1),
    ("RMPCT", 100.0),
    ("PT", 9999.0),
    ("PB", -9999.0),
]
_GEN_FIELDS = _GEN_HEAD + _GEN_MID + _owners() + [("WMOD", 0), ("WPF", 1.0)]
_GEN_FIELDS_35 = (
    _GEN_HEAD
    + [("NREG", 0)]
    + _GEN_MID
    + [("BASLOD", 0)]
    + _owners()
    + [("WMOD", 0), ("WPF", 1.0)]
)
_BR_HEAD = [("I", REQ), ("J", REQ), ("CKT", "1"), ("R", 0.0), ("X", REQ), ("B", 0.0)]
_BR_TAIL = [
    ("GI", 0.0),
    ("BI", 0.0),
    ("GJ", 0.0),
    ("BJ", 0.0),
    ("ST", 1),
    ("MET", 1),
    ("LEN", 0.0),
]
_BRANCH_FIELDS = (
    _BR_HEAD + [("RATEA", 0.0), ("RATEB", 0.0), ("RATEC", 0.0)] + _BR_TAIL + _owners()
)
_BRANCH_FIELDS_35 = (
    _BR_HEAD
    + [("NAME", "")]
    + [(f"RATE{k}", 0.0) for k in range(1, 13)]
    + _BR_TAIL
    + _owners()
)
_TR_LINE1 = (
    [
        ("I", REQ),
        ("J", REQ),
        ("K", 0),
        ("CKT", "1"),
        ("CW", 1),
        ("CZ", 1),
        ("CM", 1),
        ("MAG1", 0.0),
        ("MAG2", 0.0),
        ("NMETR", 2),
        ("NAME", ""),
        ("STAT", 1),
    ]
    + _owners()
    + [("VECGRP", ""), ("ZCOD", 0)]
)
_TR_LINE2_2W = [("R1-2", 0.0), ("X1-2", REQ), ("SBASE1-2", _SBASE)]
_TR_LINE2_3W = _TR_LINE2_2W + [
    ("R2-3", 0.0),
    ("X2-3", REQ),
    ("SBASE2-3", _SBASE),
    ("R3-1", 0.0),
    ("X3-1", REQ),
    ("SBASE3-1", _SBASE),
    ("VMSTAR", 1.0),
    ("ANSTAR", 0.0),
]


def _winding_fields(k, version):
    if version >= 35:
        rates = [(f"RATE{k}-{n}", 0.0) for n in range(1, 13)]
        ctl = [(f"COD{k}", 0), (f"CONT{k}", 0), (f"NOD{k}", 0)]
    else:
        rates = [(f"RAT{c}{k}", 0.0) for c in "ABC"]
        ctl = [(f"COD{k}", 0), (f"CONT{k}", 0)]
    return (
        [(f"WINDV{k}", _CW), (f"NOMV{k}", 0.0), (f"ANG{k}", 0.0)]
        + rates
        + ctl
        + [
            (f"RMA{k}", 1.1),
            (f"RMI{k}", 0.9),
            (f"VMA{k}", 1.1),
            (f"VMI{k}", 0.9),
            (f"NTP{k}", 33),
            (f"TAB{k}", 0),
            (f"CR{k}", 0.0),
            (f"CX{k}", 0.0),
            (f"CNXA{k}", 0.0),
        ]
    )


_AREA_FIELDS = [("I", REQ), ("ISW", 0), ("PDES", 0.0), ("PTOL", 10.0), ("ARNAME", "")]
_ZONE_FIELDS = [("I", REQ), ("ZONAME", "")]
_OWNER_FIELDS = [("I", REQ), ("OWNAME", "")]


def _sshunt_fields(version):
    if version >= 35:
        head = [
            ("I", REQ),
            ("ID", "1"),
            ("MODSW", 1),
            ("ADJM", 0),
            ("STAT", 1),
            ("VSWHI", 1.0),
            ("VSWLO", 1.0),
            ("SWREG", 0),
            ("NREG", 0),
            ("RMPCT", 100.0),
            ("RMIDNT", ""),
            ("BINIT", 0.0),
        ]
        return head + [
            f for k in range(1, 9) for f in ((f"S{k}", 1), (f"N{k}", 0), (f"B{k}", 0.0))
        ]
    head = [
        ("I", REQ),
        ("MODSW", 1),
        ("ADJM", 0),
        ("STAT", 1),
        ("VSWHI", 1.0),
        ("VSWLO", 1.0),
        ("SWREM", 0),
        ("RMPCT", 100.0),
        ("RMIDNT", ""),
        ("BINIT", 0.0),
    ]
    return head + [f for k in range(1, 9) for f in ((f"N{k}", 0), (f"B{k}", 0.0))]


#: Plain names of the sections this reader lists as not mapped, with the reason.
UNMAPPED_SECTIONS = {
    "TWO-TERMINAL DC": "two-terminal DC link: no DC link mapping yet",
    "VSC DC LINE": "VSC DC link: no DC link mapping yet",
    "MULTI-TERMINAL DC": "multi-terminal DC system: no DC link mapping yet",
    "FACTS DEVICE": "FACTS device: no equivalent asset",
    "GNE": "GNE device: user-written model, no equivalent asset",
    "INDUCTION MACHINE": "induction machine: no equivalent asset",
    "IMPEDANCE CORRECTION": "impedance correction table: tap-dependent impedance not applied",
    "INTER-AREA TRANSFER": "scheduled inter-area transfer: not modelled",
    "MULTI-SECTION LINE": "multi-section line grouping: the sections are imported as separate lines",
    "SYSTEM SWITCHING DEVICE": "system switching device: no switch asset",
    "SUBSTATION": "substation (node-breaker) data: no switch asset",
    "VOLTAGE DROOP CONTROL": "voltage droop control: not modelled",
    "SWITCHING DEVICE RATING SET": "switching device rating set: no switch asset",
    "LOAD TYPE": "load type: not modelled",
    "INTERFACE": "interface definition: not modelled",
    "INTERFACE ELEMENT": "interface element: not modelled",
}
#: Lines per record where it is fixed; others are listed per section.
_LINES_PER_RECORD = {
    "TWO-TERMINAL DC": 3,
    "VSC DC LINE": 3,
    "FACTS DEVICE": 1,
    "INTER-AREA TRANSFER": 1,
    "MULTI-SECTION LINE": 1,
    "SYSTEM SWITCHING DEVICE": 1,
}


@dataclass
class RawCase:
    """A RAW file read into tables (field names as PSS/E names them) and plain units."""

    version: int
    sbase: float
    basfrq: float
    title: str
    tables: dict = field(default_factory=dict)
    #: ``(source item, reason)``
    unmapped: list = field(default_factory=list)
    #: ``(asset, parameter, value, why)``
    assumed: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------- tokenising
def _split(line: str, lineno: int) -> list:
    """Fields of one line; a comma right after a blank-ended field is not a new empty field."""
    out, cur, quoted = [], None, False
    pending = False  # a field ended by blanks; the next comma closes it, not a new one
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c in "'\"":
            end = line.find(c, i + 1)
            if end < 0:
                raise ContractError(
                    f"RAW line {lineno}: unclosed quote: {line.strip()!r}"
                )
            if pending:
                out.append(cur)
                pending = False
            cur, quoted = line[i + 1 : end], True
            i = end + 1
            continue
        if c == "/":
            break
        if c == ",":
            out.append(cur)
            cur, quoted, pending = None, False, False
        elif c.isspace():
            if cur is not None or quoted:
                pending = True
        else:
            if pending:
                out.append(cur)
                cur, quoted, pending = None, False, False
            cur = (cur or "") + c
        i += 1
    if cur is not None or quoted:
        out.append(cur)
    return out


def _is_end(fields) -> bool:
    return bool(fields) and fields[0] is not None and fields[0].strip() == "0"


def _is_q(fields) -> bool:
    return bool(fields) and fields[0] is not None and fields[0].strip().upper() == "Q"


# ---------------------------------------------------------------- record decoding
def _num(text, lineno, name, as_int):
    try:
        v = float(text.replace("D", "E").replace("d", "e"))
    except ValueError:
        raise ContractError(
            f"RAW line {lineno}: {name} is not a number: {text!r}"
        ) from None
    if not math.isfinite(v):
        raise ContractError(f"RAW line {lineno}: {name} is not finite: {text!r}")
    if as_int:
        if v != int(v):
            raise ContractError(
                f"RAW line {lineno}: {name} must be a whole number: {text!r}"
            )
        return int(v)
    return v


_TEXT = {
    "CKT",
    "ID",
    "NAME",
    "VECGRP",
    "RMIDNT",
    "ARNAME",
    "ZONAME",
    "OWNAME",
    "LOADTYPE",
}


def _decode(fields, spec, lineno, what):
    """
    Map ``fields`` onto ``spec``. Empty or left-off fields take the spec's default
    (markers resolved by the caller); a required one raises. ``_extra`` counts
    trailing fields beyond the spec.
    """
    rec = {"_line": lineno}
    for k, (name, default) in enumerate(spec):
        text = fields[k] if k < len(fields) else None
        if text is None:
            if default is REQ:
                raise ContractError(
                    f"RAW line {lineno}: {what} field {name} is missing"
                )
            rec[name] = default
        elif name in _TEXT:
            rec[name] = text.strip()
        else:
            as_int = (
                name in ("I", "J", "K")
                or default == _BUS
                or (isinstance(default, int) and not isinstance(default, bool))
            )
            rec[name] = _num(text.strip(), lineno, f"{what} {name}", as_int)
    rec["_extra"] = max(len(fields) - len(spec), 0)
    return rec


# ---------------------------------------------------------------- reading
def read_raw_text(text: str, version: int | None = None) -> RawCase:
    """Read RAW text. ``version`` is used only when the case line leaves REV empty."""
    lines = text.splitlines()
    if len(lines) < 3:
        raise ContractError("RAW file has fewer than the three header lines")
    head = _split(lines[0], 1)
    rev = head[2] if len(head) > 2 else None
    if rev is None or rev.strip() == "":
        if version is None:
            raise ContractError(
                "RAW line 1: REV (the file's PSS/E version) is empty; pass the version"
            )
        rev_v = int(version)
    else:
        rev_v = _num(rev, 1, "REV", True)
    if rev_v not in VERSIONS:
        raise ContractError(
            f"RAW line 1: PSS/E version {rev_v} not supported (supported: {VERSIONS})"
        )
    sbase = _num(head[1], 1, "SBASE", False) if len(head) > 1 and head[1] else 100.0
    basfrq = _num(head[5], 1, "BASFRQ", False) if len(head) > 5 and head[5] else 60.0
    if sbase <= 0:
        raise ContractError(f"RAW line 1: SBASE must be positive, got {sbase}")
    case = RawCase(version=rev_v, sbase=sbase, basfrq=basfrq, title=lines[1].strip())
    if len(head) <= 1 or not head[1]:
        case.assumed.append(
            ("case", "system base (MVA)", 100.0, "SBASE empty; PSS/E default")
        )
    if len(head) <= 5 or not head[5]:
        case.assumed.append(
            ("case", "base frequency (Hz)", 60.0, "BASFRQ empty; PSS/E default")
        )

    order = SECTIONS[rev_v]
    raw = {name: [] for name in order}  # section -> list of (lineno, fields)
    k = 3
    sec = 0
    ended = False
    while k < len(lines) and sec < len(order):
        lineno = k + 1
        fields = _split(lines[k], lineno)
        k += 1
        if not fields:
            continue
        if _is_q(fields):
            ended = True
            break
        name = order[sec]
        if name == "SYSTEM-WIDE":
            if _is_end(fields):
                sec += 1
            else:
                raw[name].append((lineno, fields))
            continue
        if name == "SUBSTATION":
            # a substation record is followed by three sub-blocks, each ended by 0
            if _is_end(fields):
                sec += 1
                continue
            raw[name].append((lineno, fields))
            closed = 0
            while closed < 3 and k < len(lines):
                if _is_end(_split(lines[k], k + 1)):
                    closed += 1
                k += 1
            continue
        if _is_end(fields):
            sec += 1
            continue
        raw[name].append((lineno, fields))
    if not ended:
        while k < len(lines):
            f = _split(lines[k], k + 1)
            k += 1
            if _is_q(f):
                ended = True
                break
            if f:
                raise ContractError(
                    f"RAW line {k}: data after the last section: {lines[k - 1].strip()!r}"
                )
    if sec < len(order) and not ended:
        raise ContractError(
            f"RAW file ends inside {order[sec]} DATA (no terminating 0 or Q)"
        )

    _build_tables(case, raw)
    return case


def read_raw(path) -> RawCase:
    return read_raw_text(Path(path).read_text(encoding="latin-1"))


def _resolve(rec, bus_rec, sbase):
    for key, v in list(rec.items()):
        if v == _BUS and isinstance(v, str):
            src = {"AREA": "AREA", "ZONE": "ZONE", "OWNER": "OWNER", "O1": "OWNER"}[key]
            rec[key] = bus_rec[src] if bus_rec is not None else 1
        elif v == _SBASE and isinstance(v, str):
            rec[key] = sbase
    return rec


def _build_tables(case: RawCase, raw: dict):
    v = case.version
    t = case.tables
    sbase = case.sbase
    buses = [_decode(f, _BUS_FIELDS, ln, "bus") for ln, f in raw["BUS"]]
    bus_by = {}
    for b in buses:
        if b["I"] in bus_by:
            raise ContractError(
                f"RAW line {b['_line']}: bus number {b['I']} repeated "
                f"(first at line {bus_by[b['I']]['_line']})"
            )
        if not 1 <= b["I"] <= 999997:
            raise ContractError(
                f"RAW line {b['_line']}: bus number {b['I']} outside 1..999997"
            )
        bus_by[b["I"]] = b
    for b in buses:
        if b["BASKV"] <= 0:
            case.assumed.append(
                (
                    f"bus {b['I']}",
                    "base voltage (kV)",
                    1.0,
                    "BASKV is 0 in the file; 1 kV keeps per-unit values unchanged",
                )
            )
            b["BASKV"] = 1.0
    t["bus"] = pd.DataFrame(buses)

    def bus_of(n, ln, what):
        if n not in bus_by:
            raise ContractError(
                f"RAW line {ln}: {what} refers to bus {n}, which is not in BUS DATA"
            )
        return bus_by[n]

    def table(section, spec, what, bus_keys=("I",)):
        rows = []
        for ln, f in raw[section]:
            rec = _decode(f, spec, ln, what)
            for key in bus_keys:
                if key in rec and rec[key] not in (0, None):
                    rec[key] = abs(rec[key])
                    bus_of(rec[key], ln, what)
            rows.append(_resolve(rec, bus_by.get(rec.get("I")), sbase))
        return (
            pd.DataFrame(rows, columns=["_line", *[n for n, _ in spec], "_extra"])
            if not rows
            else pd.DataFrame(rows)
        )

    t["load"] = table("LOAD", _LOAD_FIELDS_35 if v >= 35 else _LOAD_FIELDS, "load")
    t["fixed_shunt"] = table("FIXED SHUNT", _FSHUNT_FIELDS, "fixed shunt")
    t["generator"] = table(
        "GENERATOR", _GEN_FIELDS_35 if v >= 35 else _GEN_FIELDS, "generator"
    )
    t["branch"] = table(
        "BRANCH",
        _BRANCH_FIELDS_35 if v >= 35 else _BRANCH_FIELDS,
        "branch",
        bus_keys=("I", "J"),
    )
    t["switched_shunt"] = table("SWITCHED SHUNT", _sshunt_fields(v), "switched shunt")
    t["area"] = table("AREA", _AREA_FIELDS, "area", bus_keys=())
    t["zone"] = table("ZONE", _ZONE_FIELDS, "zone", bus_keys=())
    t["owner"] = table("OWNER", _OWNER_FIELDS, "owner", bus_keys=())
    t["transformer"] = _transformers(case, raw["TRANSFORMER"], bus_by)

    for name in raw:
        if name not in UNMAPPED_SECTIONS or not raw[name]:
            continue
        reason = UNMAPPED_SECTIONS[name]
        per = _LINES_PER_RECORD.get(name)
        recs = raw[name]
        if name == "MULTI-TERMINAL DC" and v == 33:
            per = None
            groups = _mtdc_groups(recs)
        elif per:
            if len(recs) % per:
                raise ContractError(
                    f"RAW line {recs[-1][0]}: {name} DATA has {len(recs)} lines, "
                    f"not a multiple of the {per} lines per record"
                )
            groups = [recs[i : i + per] for i in range(0, len(recs), per)]
        else:
            groups = None
        if name == "SUBSTATION":
            groups = [[r] for r in recs]
        if groups is None:
            case.unmapped.append(
                (
                    f"{name.lower()} data, lines {recs[0][0]}-{recs[-1][0]} "
                    f"({len(recs)} lines)",
                    reason,
                )
            )
        else:
            for g in groups:
                case.unmapped.append(
                    (
                        f"{name.lower()} record at line {g[0][0]}: "
                        f"{', '.join(x for x in g[0][1][:4] if x)}",
                        reason,
                    )
                )


def _mtdc_groups(recs):
    """v33 multi-terminal DC: header ('NAME', NCONV, NDCBS, NDCLN, ...) then that many lines."""
    groups, i = [], 0
    while i < len(recs):
        ln, f = recs[i]
        try:
            n = sum(int(float(f[k])) for k in (1, 2, 3))
        except (IndexError, TypeError, ValueError):
            raise ContractError(
                f"RAW line {ln}: multi-terminal DC header needs NCONV, NDCBS, NDCLN"
            ) from None
        groups.append(recs[i : i + 1 + n])
        i += 1 + n
    return groups


_CODE_NAMES = {
    "CW": "winding ratio units (CW)",
    "CZ": "impedance units (CZ)",
    "CM": "magnetising admittance units (CM)",
}


def _transformers(case, recs, bus_by):
    v, sbase = case.version, case.sbase
    out, i = [], 0
    while i < len(recs):
        ln, f1 = recs[i]
        rec = _decode(f1, _TR_LINE1, ln, "transformer")
        three = rec["K"] not in (0, None)
        n_lines = 5 if three else 4
        if i + n_lines > len(recs):
            raise ContractError(
                f"RAW line {ln}: transformer record needs {n_lines} lines; "
                "TRANSFORMER DATA ends first"
            )
        for key in ("I", "J", "K"):
            if rec[key]:
                rec[key] = abs(rec[key])
                if rec[key] not in bus_by:
                    raise ContractError(
                        f"RAW line {ln}: transformer refers to bus {rec[key]}, "
                        "which is not in BUS DATA"
                    )
        rec.update(
            {
                k: x
                for k, x in _decode(
                    recs[i + 1][1],
                    _TR_LINE2_3W if three else _TR_LINE2_2W,
                    recs[i + 1][0],
                    "transformer impedance",
                ).items()
                if not k.startswith("_")
            }
        )
        windings = 3 if three else 2
        for w in range(1, windings + 1):
            lnw, fw = recs[i + 1 + w]
            spec = (
                _winding_fields(w, v)
                if (three or w == 1)
                else [("WINDV2", _CW), ("NOMV2", 0.0)]
            )
            rec.update(
                {
                    k: x
                    for k, x in _decode(
                        fw, spec, lnw, f"transformer winding {w}"
                    ).items()
                    if not k.startswith("_")
                }
            )
        bus_keys = ("I", "J", "K")[:windings]
        for w, key in enumerate(bus_keys, start=1):
            if rec.get(f"WINDV{w}") == _CW:
                rec[f"WINDV{w}"] = bus_by[rec[key]]["BASKV"] if rec["CW"] == 2 else 1.0
        _resolve(rec, bus_by[rec["I"]], sbase)
        for key in ("CW", "CZ", "CM"):
            allowed = (1, 2) if key == "CM" else (1, 2, 3)
            if rec[key] not in allowed:
                raise ContractError(
                    f"RAW line {ln}: transformer {_CODE_NAMES[key]} is {rec[key]}, not one of {allowed}; "
                    "a wrong code misreads every impedance or ratio of this transformer"
                )
        out.append(rec)
        i += n_lines
    return pd.DataFrame(out)


# ---------------------------------------------------------------- unit conversion
def bus_kv(case: RawCase) -> dict:
    return dict(zip(case.tables["bus"]["I"], case.tables["bus"]["BASKV"]))


def branch_physical(case: RawCase, br) -> dict:
    """One branch row in ohms and siemens on its from-bus base kV."""
    kv = bus_kv(case)[br["I"]]
    zb = kv**2 / case.sbase
    return {
        "r_ohm": br["R"] * zb,
        "x_ohm": br["X"] * zb,
        "b_siemens": br["B"] / zb,
        "g_from_siemens": br["GI"] / zb,
        "b_from_siemens": br["BI"] / zb,
        "g_to_siemens": br["GJ"] / zb,
        "b_to_siemens": br["BJ"] / zb,
        "base_kv": kv,
    }


def shunt_siemens(kv: float, g_mw: float, b_mvar: float) -> tuple:
    """MW / Mvar at 1.0 pu voltage to siemens: ``G = GL / kV**2``."""
    return g_mw / kv**2, b_mvar / kv**2


def load_at_nominal(ld) -> tuple:
    """``(P MW, Q Mvar)`` at 1.0 pu voltage: constant power + current + admittance parts."""
    return ld["PL"] + ld["IP"] + ld["YP"], ld["QL"] + ld["IQ"] - ld["YQ"]


def _pair_to_system(case, r, x, cz, sbase_w, nomv, kv):
    """R, X of one winding pair on the system base at the bus base (CZ 1, 2, 3)."""
    if cz == 1:
        return r, x
    if sbase_w <= 0:
        raise ContractError(
            f"transformer winding base SBASE must be positive for CZ={cz}, got {sbase_w}"
        )
    if cz == 3:
        r = r * 1e-6 / sbase_w
        if x < abs(r):
            raise ContractError(
                f"transformer CZ=3: impedance magnitude {x} below its resistance {r}"
            )
        x = math.sqrt(x**2 - r**2)
    scale = case.sbase / sbase_w * ((nomv / kv) ** 2 if nomv else 1.0)
    return r * scale, x * scale


def winding_ratio(cw, windv, nomv, kv) -> float:
    """A winding's turns ratio in pu of its bus base kV (CW 1, 2, 3)."""
    if cw == 1:
        return windv
    if cw == 2:
        return windv / kv
    return windv * (nomv / kv if nomv else 1.0)


def _magnetising(case, tr, kv1):
    if tr["CM"] == 1:
        return tr["MAG1"], tr["MAG2"]
    nomv = tr["NOMV1"] or kv1
    vscale = (kv1 / nomv) ** 2
    g = tr["MAG1"] * 1e-6 / case.sbase * vscale
    y = tr["MAG2"] * tr["SBASE1-2"] / case.sbase * vscale
    if y < abs(g):
        raise ContractError(
            f"RAW line {tr['_line']}: transformer CM=2 exciting current {tr['MAG2']} "
            "below its no-load loss"
        )
    return g, -math.sqrt(y**2 - g**2)


def transformer_windings(case: RawCase, tr) -> list:
    """
    The branches one transformer row becomes, each from a winding bus: a dict with
    ``bus`` (winding bus), ``to`` (the other bus, or ``None`` for the star point),
    ``r_pu``/``x_pu``/``g_pu``/``b_pu`` on the system base, ``tap`` (ideal ratio at
    ``bus``), ``shift_deg``, ``rate_mva``, ``in_service``, ``winding``.
    """
    kv = bus_kv(case)
    k1 = kv[tr["I"]]
    g, b = _magnetising(case, tr, k1)
    rate_key = "RATE{k}-1" if case.version >= 35 else "RATA{k}"
    if not tr["K"]:
        k2 = kv[tr["J"]]
        r, x = _pair_to_system(
            case, tr["R1-2"], tr["X1-2"], tr["CZ"], tr["SBASE1-2"], tr["NOMV1"], k1
        )
        t1 = winding_ratio(tr["CW"], tr["WINDV1"], tr["NOMV1"], k1)
        t2 = winding_ratio(tr["CW"], tr["WINDV2"], tr["NOMV2"], k2)
        if t2 == 0 or t1 == 0:
            raise ContractError(
                f"RAW line {tr['_line']}: transformer winding ratio is 0"
            )
        return [
            {
                "winding": 0,
                "bus": tr["I"],
                "to": tr["J"],
                "r_pu": r * t2**2,
                "x_pu": x * t2**2,
                "g_pu": g,
                "b_pu": b,
                "tap": t1 / t2,
                "shift_deg": tr["ANG1"],
                "rate_mva": tr[rate_key.format(k=1)],
                "in_service": tr["STAT"] != 0,
            }
        ]
    k2, k3 = kv[tr["J"]], kv[tr["K"]]
    z12 = _pair_to_system(
        case, tr["R1-2"], tr["X1-2"], tr["CZ"], tr["SBASE1-2"], tr["NOMV1"], k1
    )
    z23 = _pair_to_system(
        case, tr["R2-3"], tr["X2-3"], tr["CZ"], tr["SBASE2-3"], tr["NOMV2"], k2
    )
    z31 = _pair_to_system(
        case, tr["R3-1"], tr["X3-1"], tr["CZ"], tr["SBASE3-1"], tr["NOMV3"], k3
    )
    star = [
        tuple(0.5 * (a[c] + b_[c] - m[c]) for c in (0, 1))
        for a, b_, m in ((z12, z31, z23), (z23, z12, z31), (z31, z23, z12))
    ]
    out_winding = {0: {1, 2, 3}, 1: set(), 2: {2}, 3: {3}, 4: {1}}.get(tr["STAT"])
    if out_winding is None:
        raise ContractError(
            f"RAW line {tr['_line']}: three-winding STAT={tr['STAT']} not in 0..4"
        )
    res = []
    for w, bus, kvw in ((1, tr["I"], k1), (2, tr["J"], k2), (3, tr["K"], k3)):
        t = winding_ratio(tr["CW"], tr[f"WINDV{w}"], tr[f"NOMV{w}"], kvw)
        if t == 0:
            raise ContractError(
                f"RAW line {tr['_line']}: transformer winding {w} ratio is 0"
            )
        res.append(
            {
                "winding": w,
                "bus": bus,
                "to": None,
                "r_pu": star[w - 1][0],
                "x_pu": star[w - 1][1],
                "g_pu": g if w == 1 else 0.0,
                "b_pu": b if w == 1 else 0.0,
                "tap": t,
                "shift_deg": tr[f"ANG{w}"],
                "rate_mva": tr[rate_key.format(k=w)],
                "in_service": w not in out_winding,
            }
        )
    return res
