"""
PowerFactory DGS reader, step 1: the ASCII ``.dgs`` file as typed tables.

This module only parses. It knows the file grammar, not what a terminal or
a line means; mapping the tables onto a network is ``dgs_import``'s job. It
imports no engine (no pypsa, no pandapower), so it can run anywhere a file
needs checking.

Grammar (DGS 5.x ASCII, as ``handoff.dgs_writer`` writes it and as the
tool exports it):

- a line starting with ``*`` is a comment; blank lines are skipped;
- ``$$Class;ID(a:40);attr(t);...`` opens a table for ``Class``; every
  following row up to the next ``$$`` line is one object of that class,
  ``;``-separated, one field per header column;
- type letters: ``a`` or ``a:N`` text, ``i`` integer, ``r`` / ``d`` real,
  ``p`` a pointer to another object's ``ID``; an empty field is missing;
- a vector attribute is ``name:SIZEROW(i)`` (its length) followed by
  ``name:0(t)``, ``name:1(t)``...; it comes back as one list column
  ``name``. Any other ``name:x`` column (a matrix's ``SIZECOL``, say) is
  kept as its own column under its full header name;
- the same class may open more than once (our writer emits one
  ``$$TypHmccur`` header per spectrum, since each vector has its own
  length); its rows are merged into one table;
- the ``General`` table carries ``Descr``/``Val`` pairs; its ``Version``
  must be 5.x.

Refused, as ``ContractError`` naming the line: a version other than 5.x
(or none), a row before any header, a header without an ``ID`` column or
with an unknown type, a row whose field count differs from its header, a
value that does not parse as its type (numbers are read as written: no
decimal comma, no ``1.0`` for an integer), an empty SIZEROW, a vector longer than its columns,
a duplicate ``ID``, and a pointer to an ``ID`` the file does not hold.
A trailing empty field (a row ending in ``;``) is tolerated.
"""

from __future__ import annotations

import dataclasses
import math
import re
from pathlib import Path

import pandas as pd

from gridspine.schema.contracts import ContractError

SUPPORTED_MAJOR = "5"
#: The column holding each row's 1-based line number in the source file, so
#: a later step can name the exact row it could not map.
LINE_COLUMN = "_line"

_HEAD = re.compile(r"^(?P<name>[^()]+)\((?P<type>[^()]*)\)$")
_TYPES = {"a", "i", "r", "d", "p"}
#: Numbers as written: ASCII digits, a point, an exponent. No ``nan``/``inf``,
#: no ``_`` separators, no decimal comma, no ``1.0`` for an integer.
_INT = re.compile(r"[+-]?[0-9]+")
_REAL = re.compile(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?")


@dataclasses.dataclass(frozen=True)
class DgsTables:
    """
    ``tables``: one DataFrame per class, indexed by ``ID`` (text), with the
    attribute columns in header order (vectors as list columns) and
    ``LINE_COLUMN``. ``types``: per class, attribute name → type letter
    (``v`` for a vector). ``version``: the General table's ``Version``.
    """

    version: str
    tables: dict
    types: dict
    source: str

    def counts(self) -> dict:
        """Rows per class."""
        return {cls: len(df) for cls, df in self.tables.items()}


def parse_dgs(path) -> DgsTables:
    """
    Parse the ASCII DGS file at ``path``: UTF-8, else Windows-1252 (the
    encoding the tool writes on Windows). A file that is neither, or that
    cannot be read, is refused naming the file.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        raise ContractError(f"DGS {path}: cannot be read ({e.strerror or e})") from e
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return parse_dgs_text(raw.decode(encoding), source=str(path))
        except UnicodeDecodeError:
            continue
    raise ContractError(f"DGS {path}: neither UTF-8 nor Windows-1252 text")


def parse_dgs_text(text: str, source: str = "<text>") -> DgsTables:
    """Parse DGS ASCII ``text``; ``source`` names it in refusals."""

    def fail(lineno, msg):
        raise ContractError(f"DGS {source}, line {lineno}: {msg}")

    rows: dict = {}  # class -> list of dicts
    types: dict = {}  # class -> {attr: type letter}
    owner: dict = {}  # ID -> (class, line)
    pointers: list = []  # (line, class, attr, target ID)
    head = None  # (class, [(column, attr, type letter, vector index or None)])

    for lineno, line in enumerate(
        text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1
    ):
        if not line.strip() or line.startswith("*"):
            continue
        fields = line.split(";")
        if line.startswith("$$"):
            head = _header(fields, lineno, fail)
            cls, cols = head
            rows.setdefault(cls, [])
            seen = types.setdefault(cls, {})
            for _, attr, t, k in cols:
                seen.setdefault(attr, "v" if k is not None else t)
            continue
        if head is None:
            fail(lineno, "a data row before any $$ class header")
        cls, cols = head
        while len(fields) > len(cols) and fields[-1].strip() == "":
            fields.pop()  # a trailing ';'
        if len(fields) != len(cols):
            fail(
                lineno,
                f"{cls} row has {len(fields)} fields, its header {len(cols)}",
            )
        rec: dict = {LINE_COLUMN: lineno}
        sizes: dict = {}
        vectors: dict = {}
        for (col, attr, t, k), field in zip(cols, fields):
            v = _value(field, t, lineno, f"{cls}.{col}", fail)
            if isinstance(k, int) and field.strip() == "":
                v = None  # an unused vector slot, not a missing real
            if k == "SIZEROW":
                sizes[attr] = v
            elif k is not None:
                vectors.setdefault(attr, []).append(v)
            else:
                rec[attr] = v
                if t == "p" and v is not None:
                    pointers.append((lineno, cls, attr, v))
        for attr, items in vectors.items():
            n = sizes.get(attr)
            if n is None:
                fail(lineno, f"{cls}.{attr}: SIZEROW is empty")
            if n < 0 or n > len(items):
                fail(
                    lineno,
                    f"{cls}.{attr}: SIZEROW {n} but {len(items)} vector columns",
                )
            if any(x is not None for x in items[n:]):
                fail(lineno, f"{cls}.{attr}: values beyond SIZEROW {n}")
            if any(x is None for x in items[:n]):
                fail(lineno, f"{cls}.{attr}: an empty entry within SIZEROW {n}")
            rec[attr] = items[:n]
        for attr in set(sizes) - set(vectors):
            if sizes[attr]:
                fail(
                    lineno, f"{cls}.{attr}: SIZEROW {sizes[attr]} but no vector columns"
                )
            rec[attr] = []
        oid = rec.get("ID")
        oid = None if oid is None else str(oid)  # pointers are text; so is the key
        rec["ID"] = oid
        if oid is None:
            fail(lineno, f"{cls} row without an ID")
        if oid in owner:
            first_cls, first_line = owner[oid]
            fail(lineno, f"ID {oid} again (first: {first_cls}, line {first_line})")
        owner[oid] = (cls, lineno)
        rows[cls].append(rec)

    for lineno, cls, attr, target in pointers:
        if target not in owner:
            fail(
                lineno,
                f"{cls}.{attr} points at ID {target}, which the file does not hold",
            )

    version = _version(rows.get("General", []), fail)

    tables = {}
    for cls, recs in rows.items():
        attrs = [a for a in types[cls] if a != "ID"]
        df = pd.DataFrame.from_records(
            recs, columns=["ID", *attrs, LINE_COLUMN]
        ).set_index("ID")
        tables[cls] = df
    return DgsTables(version=version, tables=tables, types=types, source=source)


def _header(fields, lineno, fail):
    cls = fields[0][2:].strip()
    if not cls:
        fail(lineno, "a $$ header without a class name")
    cols = []
    names = set()
    for f in fields[1:]:
        f = f.strip()
        if not f:
            continue  # a trailing ';'
        m = _HEAD.match(f)
        if not m:
            fail(lineno, f"{cls}: header column {f!r} is not name(type)")
        name, t = m["name"].strip(), m["type"].strip().split(":")[0].strip()
        if t not in _TYPES:
            fail(lineno, f"{cls}.{name}: unknown type {m['type']!r}")
        if name in names:
            fail(lineno, f"{cls}: column {name!r} twice in the header")
        names.add(name)
        cols.append((name, t))
    if "ID" not in names:
        fail(lineno, f"{cls}: header has no ID column")
    out = []
    for name, t in cols:
        base, _, sub = name.partition(":")
        if name == "ID" and t not in ("a", "i"):
            fail(lineno, f"{cls}: the ID column must be text or integer, not {t!r}")
        if sub == "SIZEROW" and t != "i":
            fail(lineno, f"{cls}.{name}: a vector length must be an integer, not {t!r}")
        if f"{base}:SIZEROW" in names and (sub == "SIZEROW" or sub.isdigit()):
            out.append((name, base, t, "SIZEROW" if sub == "SIZEROW" else int(sub)))
        else:
            # a plain attribute, or an indexed column with no SIZEROW: as it stands
            out.append((name, name, t, None))
    return cls, out


def _value(field, t, lineno, where, fail):
    s = field.strip()
    if s == "":
        return math.nan if t in ("r", "d") else None
    if t in ("a", "p"):
        return s
    if t == "i":
        if not _INT.fullmatch(s):
            fail(lineno, f"{where}: {s!r} is not an integer")
        return int(s)
    if not _REAL.fullmatch(s):
        fail(lineno, f"{where}: {s!r} is not a number")
    return float(s)


def _version(general, fail):
    found = [r for r in general if str(r.get("Descr", "")).strip() == "Version"]
    if not found:
        fail(general[0][LINE_COLUMN] if general else 1, "no General table Version")
    v = str(found[0].get("Val") or "").strip()
    if not re.fullmatch(rf"{SUPPORTED_MAJOR}(\.\d+)*", v):
        fail(
            found[0][LINE_COLUMN],
            f"version {v!r} found; only DGS {SUPPORTED_MAJOR}.x is read",
        )
    return v
