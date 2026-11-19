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
  must be 5.x or 7.x.

DGS 7.x (PowerFactory 2021 on) differs in three ways, and only these:

- each object's key is ``FID`` instead of ``ID``, and every pointer holds an
  ``FID``; the tables come back keyed by it, under the same ``ID`` index;
- each class row has an ``OP`` (operation) field. Only ``C`` (create) is
  read: an update or delete row (``U``, ``D``...) changes a network the file
  does not hold, so it is refused. ``OP`` is not kept as a column;
- a ``name:MATRIX`` header column (``GPScoords:MATRIX(a)``) marks a matrix
  attribute. It and any other ``name:...`` column of that name are dropped
  and listed in ``DgsTables.dropped``, for the import to report as not
  imported.

A 5.x file with an ``FID``, ``OP`` or ``MATRIX`` column, and a 7.x file whose
class header has no ``FID``, are refused naming the header line. So is every
other version (4.x, 6.x...): the version decides how the rest reads.

Refused, as ``ContractError`` naming the line: a version other than 5.x
or 7.x (or none), a row before any header, a header without an ``ID`` column or
with an unknown type, a row whose field count differs from its header, a
value that does not parse as its type (numbers are read as written: no
``1.0`` for an integer, no thousands separator), an empty SIZEROW, a vector longer than its columns,
a duplicate ``ID`` (``FID``), and a pointer to an ``ID`` the file does not hold. An
empty pointer means "none"; any other value, ``0`` included, must be an
``ID`` in the file. Also refused: vector columns not numbered 0, 1, 2, ...
in order, a real that overflows to infinity (``1e999``), and a repeated
class header that gives an attribute another type letter. The version is
checked before pointers, so a file of another version is reported as such.
A trailing empty field (a row ending in ``;``) is tolerated.

A real may carry a decimal comma (``158,7713``), as PowerFactory writes it
under a German locale: one comma between digits, with no point. Fields are
``;``-separated and DGS has no thousands separator, so that comma can only
be the decimal mark (D1a). ``1.234,5``, two commas, a leading or trailing
comma, or a comma in an integer field are still refused; a text field keeps
its comma as text. One file uses one decimal mark: a file with a comma real
and a point real is refused, naming the first of each. ``DgsTables.
decimal_comma`` says the file used commas, for the report to say so.
"""

from __future__ import annotations

import dataclasses
import math
import re
from pathlib import Path

import pandas as pd

from gridspine.schema.contracts import ContractError

SUPPORTED_MAJORS = ("5", "7")
#: Columns only a 7.x file has; a 5.x header holding one is refused.
_V7_ONLY = ("FID", "OP")
#: The column holding each row's 1-based line number in the source file, so
#: a later step can name the exact row it could not map.
LINE_COLUMN = "_line"

_HEAD = re.compile(r"^(?P<name>[^()]+)\((?P<type>[^()]*)\)$")
_TYPES = {"a", "i", "r", "d", "p"}
#: Numbers as written: ASCII digits, a point, an exponent. No ``nan``/``inf``,
#: no ``_`` separators, no decimal comma, no ``1.0`` for an integer.
_INT = re.compile(r"[+-]?[0-9]+")
_REAL = re.compile(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?")
#: A real with a decimal comma: digits, one comma, digits, no point.
_COMMA_REAL = re.compile(r"[+-]?[0-9]+,[0-9]+([eE][+-]?[0-9]+)?")


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
    #: per class, the header columns read but not kept (7.x matrices)
    dropped: dict = dataclasses.field(default_factory=dict)
    #: whether the file's reals use a decimal comma (D1a)
    decimal_comma: bool = False
    #: text line -> where it came from, when not a line of a file
    #: (``dgs_xlsx``: "sheet ElmTerm, row 3")
    locations: dict = dataclasses.field(default_factory=dict)

    def where(self, line) -> str:
        """Where text line ``line`` came from: "line 7", or a sheet and row."""
        line = int(line)
        return self.locations.get(line, f"line {line}")

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
        raise ContractError(f"DGS {path}: cannot be read ({e})") from e
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return parse_dgs_text(raw.decode(encoding), source=str(path))
        except UnicodeDecodeError:
            continue
    raise ContractError(f"DGS {path}: neither UTF-8 nor Windows-1252 text")


def parse_dgs_text(
    text: str, source: str = "<text>", locations: dict | None = None
) -> DgsTables:
    """
    Parse DGS ASCII ``text``; ``source`` names it in refusals. ``locations``
    maps a text line to the place it came from, for refusals and
    ``DgsTables.where``; a line it does not hold is "line <n>".
    """
    locations = dict(locations or {})

    def fail(lineno, msg):
        at = locations.get(lineno, f"line {lineno}")
        raise ContractError(f"DGS {source}, {at}: {msg}")

    rows: dict = {}  # class -> list of dicts
    types: dict = {}  # class -> {attr: type letter}
    owner: dict = {}  # ID -> (class, line)
    pointers: list = []  # (line, class, attr, target ID)
    head = None  # (class, [(column, attr, type letter, vector index or None)])
    headers: list = []  # (line, class, set of column names)
    ops: list = []  # (line, class, OP value)
    dropped: dict = {}  # class -> [column]
    marks: dict = {}  # decimal mark -> (line, "Class.attr", field): its first use

    for lineno, line in enumerate(
        text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1
    ):
        if not line.strip() or line.startswith("*"):
            continue
        fields = line.split(";")
        if line.startswith("$$"):
            head = _header(fields, lineno, fail)
            cls, cols = head
            headers.append((lineno, cls, {c[0] for c in cols}))
            rows.setdefault(cls, [])
            seen = types.setdefault(cls, {})
            for col, attr, t, k in cols:
                if k == "MATRIX":
                    if col not in dropped.setdefault(cls, []):
                        dropped[cls].append(col)
                    continue
                if attr == "OP":
                    continue
                attr = "ID" if attr == "FID" else attr  # the key, either way
                letter = "v" if k is not None else t
                if seen.setdefault(attr, letter) != letter:
                    fail(
                        lineno,
                        f"{cls}.{col}: type {letter!r} here, {seen[attr]!r} in an "
                        "earlier header of this class",
                    )
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
            if k == "MATRIX":
                continue  # dropped (listed in the report), so never read
            v = _value(field, t, lineno, f"{cls}.{col}", fail)
            if t in ("r", "d"):
                mark = "," if "," in field else "." if "." in field else None
                if mark:
                    marks.setdefault(mark, (lineno, f"{cls}.{col}", field.strip()))
            if isinstance(k, int) and field.strip() == "":
                v = None  # an unused vector slot, not a missing real
            if attr == "OP":
                ops.append((lineno, cls, v))
                continue
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
        key = "FID" if any(c[0] == "FID" for c in cols) else "ID"
        oid = rec.pop(key, None)
        oid = None if oid is None else str(oid)  # pointers are text; so is the key
        rec["ID"] = oid
        if oid is None:
            fail(lineno, f"{cls} row without an {key}")
        if oid in owner:
            first_cls, first_line = owner[oid]
            fail(lineno, f"ID {oid} again (first: {first_cls}, line {first_line})")
        owner[oid] = (cls, lineno)
        rows[cls].append(rec)

    version = _version(
        rows.get("General", []), fail
    )  # before pointers: a v6 file says so
    major = version.split(".")[0]
    if len(marks) == 2:
        (cl, ca, cf), (pl, pa, pf) = marks[","], marks["."]
        fail(
            max(cl, pl),
            f"decimal marks are mixed: {ca} {cf!r} ({locations.get(cl, f'line {cl}')}) "
            f"uses a comma, {pa} {pf!r} ({locations.get(pl, f'line {pl}')}) a point; "
            "a file uses one decimal mark",
        )
    for lineno, cls, names in headers:
        if major == "5":
            extra = [c for c in names if c in _V7_ONLY]
            if cls in dropped and any(c in dropped[cls] for c in names):
                extra.append(next(c for c in dropped[cls] if c in names))
            if extra:
                fail(
                    lineno,
                    f"{cls}: column {extra[0]!r} belongs to DGS 7.x; "
                    f"this file says version {version}",
                )
        elif "FID" not in names or "ID" in names:
            fail(
                lineno,
                f"{cls}: DGS {version} keys objects by FID; this header has "
                + ("an ID column" if "FID" in names else "no FID column"),
            )
    for lineno, cls, op in ops:
        if op != "C":
            fail(
                lineno,
                f"{cls}: OP {op!r}; only C (create) rows are read, since an update "
                "or delete changes a network this file does not hold",
            )

    for lineno, cls, attr, target in pointers:
        if target not in owner:
            fail(
                lineno,
                f"{cls}.{attr} points at ID {target}, which the file does not hold",
            )

    tables = {}
    for cls, recs in rows.items():
        attrs = [a for a in types[cls] if a != "ID"]
        df = pd.DataFrame.from_records(
            recs, columns=["ID", *attrs, LINE_COLUMN]
        ).set_index("ID")
        tables[cls] = df
    return DgsTables(
        version=version,
        tables=tables,
        types=types,
        source=source,
        dropped=dropped,
        decimal_comma="," in marks,
        locations=locations,
    )


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
    if "ID" not in names and "FID" not in names:
        fail(lineno, f"{cls}: header has no ID column (nor a 7.x FID)")
    out = []
    for name, t in cols:
        base, _, sub = name.partition(":")
        if name in ("ID", "FID") and t not in ("a", "i"):
            fail(lineno, f"{cls}: the {name} column must be text or integer, not {t!r}")
        if sub == "SIZEROW" and t != "i":
            fail(lineno, f"{cls}.{name}: a vector length must be an integer, not {t!r}")
        if f"{base}:MATRIX" in names:
            out.append((name, base, t, "MATRIX"))
        elif f"{base}:SIZEROW" in names and (sub == "SIZEROW" or sub.isdigit()):
            out.append((name, base, t, "SIZEROW" if sub == "SIZEROW" else int(sub)))
        else:
            # a plain attribute, or an indexed column with no SIZEROW: as it stands
            out.append((name, name, t, None))
    order: dict = {}
    for name, attr, _t, k in out:
        if isinstance(k, int):
            order.setdefault(attr, []).append(k)
    for attr, ks in order.items():
        if ks != list(range(len(ks))):
            fail(
                lineno,
                f"{cls}.{attr}: vector columns {ks} are not 0, 1, 2, ... in order",
            )
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
    if _COMMA_REAL.fullmatch(s):
        s = s.replace(",", ".")
    if not _REAL.fullmatch(s):
        fail(lineno, f"{where}: {s!r} is not a number")
    v = float(s)
    if not math.isfinite(v):
        fail(lineno, f"{where}: {s!r} is not finite")
    return v


def _version(general, fail):
    found = [r for r in general if str(r.get("Descr", "")).strip() == "Version"]
    if not found:
        fail(general[0][LINE_COLUMN] if general else 1, "no General table Version")
    v = str(found[0].get("Val") or "").strip()
    if not re.fullmatch(rf"[{''.join(SUPPORTED_MAJORS)}](\.\d+)*", v):
        fail(
            found[0][LINE_COLUMN],
            f"version {v!r} found; only DGS 5.x and 7.x are read",
        )
    return v
