"""
A TSO's contingency list, read onto the campus net (TRGC 15).

The TSO screens N-1 against its own list. This reads that list in one of two forms
and maps every element onto the campus pandapower net. The result has the
``contingency_set`` columns, so ``screen_planning`` takes it unchanged.

**Forms.**

- ``.con``: a subset of the PSS/E contingency description data (POM, "Contingency
  Description Data File"). The grammar is below. Keywords are case-insensitive.
  ``/`` starts a comment outside quotes, and a line starting with ``COM`` is a
  comment.

  ::

      CONTINGENCY <label>
         OPEN|TRIP|DISCONNECT BRANCH|LINE FROM BUS <b> TO BUS <b> [CKT|CIRCUIT <c>]
         DISCONNECT|TRIP BUS <b>
         REMOVE|TRIP|DISCONNECT MACHINE|UNIT <id> FROM|AT BUS <b>
      END
      ...
      END

  ``<b>`` is a bus number or a quoted bus name. ``<c>`` and ``<id>`` may be
  quoted. A circuit that is left out is ``1``, as in PSS/E.

- CSV: the columns of ``handoff/contingencies.write_contingencies``. That writer's
  output is read back as the same element sets.

**Identity.** Bus numbers are the RAW's own numbers (``raw_writer._bus_numbers``).
A branch is ``(from, to, ckt)`` in either direction, with the RAW's circuit
counters. A machine is the RAW's ``(bus, machine ID)`` (``dyr_writer._machines``).
So a list written against the exported .raw maps back onto the net it came from.

**Fail closed.** The importer never matches an element by guesswork and never
drops one silently:

- A malformed line raises ``ContractError``, and so does a missing ``END``, an
  empty ``CONTINGENCY`` block or a repeated label. The message names the line.
- Each unknown, ambiguous or unsupported element is a row of ``unmatched`` with
  its line number and reason.
- A contingency with any such element is left out of ``contingencies`` entirely.
  Its matched elements are listed with the reason ``partial``. Screening only the
  matched part would grade a smaller outage than the TSO's.
- A valid PSS/E command outside the subset (``CLOSE``, ``SET``, ``CHANGE``,
  ``MOVE``, ``INCLUDE``, automatic ``SINGLE`` / ``DOUBLE`` …) is not malformed. It
  is listed as ``unsupported``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from gridspine.handoff.dyr_writer import _machines
from gridspine.handoff.raw_writer import _bus_numbers
from gridspine.schema.contingency import COMPOUND_SEPARATORS, validate_contingency_set
from gridspine.schema.contracts import ContractError
from gridspine.static.contingency_set import branch_contingencies

UNMATCHED_COLUMNS = ["contingency_id", "line", "element", "reason"]

#: Why an element is in ``unmatched``.
REASONS = frozenset({
    "unknown_bus", "ambiguous_bus", "unknown_branch", "unknown_circuit",
    "unknown_machine", "unsupported", "not_representable", "partial",
})

#: Valid PSS/E contingency commands that this subset does not map. They are
#: listed as ``unsupported``, never treated as malformed.
_PSSE_UNSUPPORTED = frozenset({
    "CLOSE", "SET", "CHANGE", "ALTER", "MODIFY", "INCREASE", "DECREASE", "MOVE", "INCLUDE",
    "SINGLE", "DOUBLE", "PARALLEL", "BLOCK", "ADD", "DISPATCH", "BYPASS",
})
_BRANCH_VERBS = {"OPEN", "TRIP", "DISCONNECT"}
_BUS_VERBS = {"DISCONNECT", "TRIP"}
_MACHINE_VERBS = {"REMOVE", "TRIP", "DISCONNECT"}

_TOKEN = re.compile(r"'[^']*'|\"[^\"]*\"|[^\s'\"]+")
_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True)
class ImportedContingencies:
    """
    ``contingencies``: the matched set, valid for ``screen_planning``.
    ``unmatched``: one row per element that is not in it (``UNMATCHED_COLUMNS``).
    """

    contingencies: pd.DataFrame
    unmatched: pd.DataFrame

    @property
    def excluded(self) -> list[str]:
        """The contingencies that were left out, as labelled in the list."""
        ids = self.unmatched["contingency_id"]
        return sorted({c for c in ids if isinstance(c, str) and c})


class _NetIndex:
    """The net's buses, branches and machines under the RAW's identity."""

    def __init__(self, net):
        self.num_of = _bus_numbers(net)
        self.name_at_num = {n: name for name, n in self.num_of.items()}
        names = net.bus["name"].astype(str)
        self.name_count = names.value_counts().to_dict()
        self.slack = {str(net.bus.at[b, "name"]) for b in net.ext_grid["bus"]}
        n1 = branch_contingencies(net)
        # (from, to, ckt) -> n1 row, both directions. branch_keys refuses a net
        # where a line and a transformer share one, so the key is unique.
        self.branch = {}
        self.pair = set()
        for r in n1.itertuples(index=False):
            for f, t in ((r.from_bus, r.to_bus), (r.to_bus, r.from_bus)):
                self.branch[(f, t, str(r.ckt))] = r
                self.pair.add((f, t))
        self.machine = {}  # (bus number, machine id) -> (unit name, kind)
        gens = set(net.gen["name"].astype(str))
        sgens = set(net.sgen["name"].astype(str)) if hasattr(net, "sgen") else set()
        for uid, num, mid, _mb, _sync in _machines(net):
            kind = "gen" if uid in gens else "sgen" if uid in sgens else "ext_grid"
            self.machine[(int(num), str(mid).strip())] = (str(uid), kind)

    def bus(self, token):
        """(bus name, None) or (None, reason) for a bus number or a name."""
        if isinstance(token, int):
            name = self.name_at_num.get(token)
            if name is None:
                return None, "unknown_bus"
        else:
            name = token
            if name not in self.name_count:
                return None, "unknown_bus"
        if self.name_count.get(name, 0) > 1:
            return None, "ambiguous_bus"
        return name, None


def _contingency_id(label: str) -> str:
    """The label in the contingency-set charset: other characters become ``_``."""
    out = _ID_UNSAFE.sub("_", label.strip())
    return out or "_"


def _unquote(tok: str) -> str:
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "'\"":
        return tok[1:-1].strip()
    return tok.strip()


def _tokens(text: str) -> list[str]:
    out = []
    for tok in _TOKEN.findall(text):
        if tok.startswith("/"):
            break
        out.append(tok)
    return out


def _bus_token(tok: str, lineno: int):
    if tok[0] in "'\"":
        name = _unquote(tok)
        if not name:
            raise ContractError(f"line {lineno}: empty bus name")
        return name
    try:
        return int(tok)
    except ValueError:
        raise ContractError(f"line {lineno}: bus {tok!r} is neither a bus number nor a quoted name") from None


# --- the .con form ---------------------------------------------------------


@dataclass
class _Element:
    kind: str          # "branch" | "bus" | "machine" | "unsupported"
    line: int
    text: str
    from_bus: object = None
    to_bus: object = None
    ckt: str = "1"
    bus: object = None
    machine_id: str = ""


def _parse_statement(toks: list[str], lineno: int, text: str) -> _Element:
    up = [t.upper() for t in toks]
    verb = up[0]
    if len(up) >= 2 and up[1] in ("BRANCH", "LINE") and verb in _BRANCH_VERBS:
        rest = up[2:]
        if len(rest) < 6 or rest[0] != "FROM" or rest[1] != "BUS" or rest[3] != "TO" or rest[4] != "BUS":
            raise ContractError(f"line {lineno}: expected 'FROM BUS <b> TO BUS <b>' in {text!r}")
        f, t = _bus_token(toks[4], lineno), _bus_token(toks[7], lineno)
        tail = toks[8:]
        if len(tail) >= 2 and tail[0].upper() == "TO" and tail[1].upper() == "BUS":
            return _Element("unsupported", lineno, text)  # a three-winding transformer
        ckt = "1"
        if tail:
            if tail[0].upper() not in ("CKT", "CIRCUIT") or len(tail) != 2:
                raise ContractError(f"line {lineno}: expected 'CKT <c>' after the buses in {text!r}")
            ckt = _unquote(tail[1])
            if not ckt:
                raise ContractError(f"line {lineno}: empty circuit id")
        return _Element("branch", lineno, text, from_bus=f, to_bus=t, ckt=ckt)
    if len(up) >= 2 and up[1] == "BUS" and verb in _BUS_VERBS:
        if len(up) != 3:
            raise ContractError(f"line {lineno}: expected '{toks[0]} BUS <b>' in {text!r}")
        return _Element("bus", lineno, text, bus=_bus_token(toks[2], lineno))
    if len(up) >= 2 and up[1] in ("MACHINE", "UNIT") and verb in _MACHINE_VERBS:
        if len(up) != 6 or up[3] not in ("FROM", "AT") or up[4] != "BUS":
            raise ContractError(f"line {lineno}: expected '{toks[0]} {toks[1]} <id> FROM BUS <b>' in {text!r}")
        mid = _unquote(toks[2])
        if not mid:
            raise ContractError(f"line {lineno}: empty machine id")
        return _Element("machine", lineno, text, bus=_bus_token(toks[5], lineno), machine_id=mid)
    if verb in _PSSE_UNSUPPORTED or verb in _BRANCH_VERBS | _BUS_VERBS | _MACHINE_VERBS:
        return _Element("unsupported", lineno, text)  # e.g. REMOVE LOAD, OPEN on a DC line
    raise ContractError(f"line {lineno}: not a contingency command: {text!r}")


def _parse_con(text: str):
    """[(label, line, [elements])], [(line, text)] of top-level unsupported commands."""
    blocks, top_unsupported = [], []
    current = None
    closed = False
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("/") or line[:3].upper() == "COM":
            continue
        toks = _tokens(line)
        if not toks:
            continue
        if closed:
            raise ContractError(f"line {lineno}: text after the final END: {line!r}")
        head = toks[0].upper()
        if current is None:
            if head == "CONTINGENCY":
                if len(toks) > 2:
                    raise ContractError(f"line {lineno}: expected 'CONTINGENCY <label>', got {line!r}")
                label = _unquote(toks[1]) if len(toks) == 2 else ""
                if not label:
                    raise ContractError(f"line {lineno}: CONTINGENCY without a label")
                current = (label, lineno, [])
            elif head == "END":
                closed = True
            elif head in _PSSE_UNSUPPORTED:
                top_unsupported.append((lineno, line))
            else:
                raise ContractError(f"line {lineno}: expected CONTINGENCY or END, got {line!r}")
            continue
        if head == "END":
            if len(toks) != 1:
                raise ContractError(f"line {lineno}: expected END alone, got {line!r}")
            if not current[2]:
                raise ContractError(f"line {lineno}: contingency {current[0]!r} (line {current[1]}) is empty")
            blocks.append(current)
            current = None
            continue
        if head == "CONTINGENCY":
            raise ContractError(
                f"line {lineno}: CONTINGENCY inside contingency {current[0]!r} (line {current[1]}); END missing"
            )
        current[2].append(_parse_statement(toks, lineno, line))
    if current is not None:
        raise ContractError(f"line {current[1]}: contingency {current[0]!r} has no END before the end of the file")
    if not closed:
        raise ContractError("END missing at the end of the file: the list must end with a final END")
    return blocks, top_unsupported


def _branch(idx: _NetIndex, el: _Element):
    """(n1 row, None) or (None, reason)."""
    f, why = idx.bus(el.from_bus)
    if why:
        return None, why
    t, why = idx.bus(el.to_bus)
    if why:
        return None, why
    hit = idx.branch.get((f, t, el.ckt))
    if hit is not None:
        return hit, None
    return None, "unknown_circuit" if (f, t) in idx.pair else "unknown_branch"


def _machine(idx: _NetIndex, el: _Element):
    """(unit name, None) or (None, reason)."""
    bus, why = idx.bus(el.bus)
    if why:
        return None, why
    hit = idx.machine.get((idx.num_of[bus], el.machine_id))
    if hit is None:
        return None, "unknown_machine"
    uid, kind = hit
    if kind == "ext_grid":
        return None, "unsupported"  # the slack: the screen does not outage the grid infeed
    return uid, None


def _row_of(cid, matched):
    """One contingency-set row, or None when the elements are not one row's worth."""
    kinds = {k for k, *_ in matched}
    if kinds == {"branch"} and len(matched) <= 2:
        rows = [r for _, r in matched]
        ids = [f"{r.from_bus}-{r.to_bus}-{r.ckt}" for r in rows]
        if len(set(ids)) != len(ids):
            return None
        out = {"contingency_id": cid, "kind": "branch", "element_ids": ids, "order": len(ids)}
        if len(rows) == 1:
            r = rows[0]
            out.update(from_bus=r.from_bus, to_bus=r.to_bus, ckt=str(r.ckt),
                       element_type=r.element_type, element_index=int(r.element_index))
        return out
    if len(matched) == 1 and kinds == {"machine"}:
        uid, bus = matched[0][1]
        return {"contingency_id": cid, "kind": "unit", "element_ids": [uid], "order": 1, "bus": bus}
    if len(matched) == 1 and kinds == {"bus"}:
        return {"contingency_id": cid, "kind": "bus_section", "element_ids": [matched[0][1]], "order": 1}
    return None


def _settle(idx: _NetIndex, label: str, line: int, cid: str, elements, unmatched: list):
    """Map one contingency's elements. Returns its row, or None when any element misses."""
    matched, missed, done = [], [], []
    for el in elements:
        if el.kind == "unsupported":
            missed.append((el, "unsupported"))
            continue
        if el.kind == "branch":
            hit, why = _branch(idx, el)
            item = ("branch", hit)
        elif el.kind == "machine":
            uid, why = _machine(idx, el)
            item = ("machine", (uid, idx.name_at_num.get(el.bus) if isinstance(el.bus, int) else el.bus))
            hit = uid
        else:  # bus
            hit, why = idx.bus(el.bus)
            if why is None and hit in idx.slack:
                hit, why = None, "unsupported"  # the slack bus: bus sections exclude it
            item = ("bus", hit)
        if why:
            missed.append((el, why))
        else:
            matched.append(item)
            done.append(el)
    if missed:
        for el, why in missed:
            unmatched.append({"contingency_id": cid, "line": el.line, "element": el.text, "reason": why})
        for el in done:
            unmatched.append({"contingency_id": cid, "line": el.line, "element": el.text, "reason": "partial"})
        return None
    row = _row_of(cid, matched)
    if row is None:
        unmatched.append({"contingency_id": cid, "line": line, "element": label, "reason": "not_representable"})
    return row


def _finish(rows, unmatched) -> ImportedContingencies:
    extra = ["from_bus", "to_bus", "ckt", "element_type", "element_index", "bus"]
    if rows:
        cset = validate_contingency_set(pd.DataFrame(rows, columns=["contingency_id", "kind", "element_ids", "order", *extra]))
    else:
        cset = pd.DataFrame(columns=["contingency_id", "kind", "element_ids", "order", *extra])
    um = pd.DataFrame(unmatched, columns=UNMATCHED_COLUMNS)
    um["line"] = um["line"].astype("int64")
    return ImportedContingencies(cset.reset_index(drop=True), um)


def _from_con(text: str, net) -> ImportedContingencies:
    blocks, top = _parse_con(text)
    idx = _NetIndex(net)
    seen, rows, unmatched = {}, [], []
    for lineno, line in top:
        unmatched.append({"contingency_id": "", "line": lineno, "element": line, "reason": "unsupported"})
    for label, line, elements in blocks:
        cid = _contingency_id(label)
        if cid in seen:
            raise ContractError(
                f"line {line}: contingency label {label!r} repeats the one at line {seen[cid]}"
                + ("" if cid == label else f" (both read as {cid!r})")
            )
        seen[cid] = line
        row = _settle(idx, label, line, cid, elements, unmatched)
        if row is not None:
            rows.append(row)
    return _finish(rows, unmatched)


# --- the CSV form ----------------------------------------------------------


def _num(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and not v.strip()):
        return None
    f = float(v)
    if not f.is_integer():
        raise ValueError(v)
    return int(f)


def _text(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _csv_bus(row, num_col, name_col, lineno):
    try:
        num = _num(row.get(num_col))
    except (TypeError, ValueError):
        raise ContractError(f"line {lineno}: {num_col} {row.get(num_col)!r} is not a bus number") from None
    if num is not None:
        return num
    name = _text(row.get(name_col))
    if not name:
        raise ContractError(f"line {lineno}: neither {num_col} nor {name_col} is given")
    return name


def _from_csv(text: str, net) -> ImportedContingencies:
    import io

    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    missing = {"contingency_id", "kind"} - set(df.columns)
    if missing:
        raise ContractError(f"line 1: contingency CSV is missing column(s) {sorted(missing)}")
    idx = _NetIndex(net)
    seen, rows, unmatched = {}, [], []
    known_branch = {f"{r.from_bus}-{r.to_bus}-{r.ckt}" for r in idx.branch.values()}
    for i, rec in enumerate(df.to_dict("records")):
        lineno = i + 2  # the header is line 1
        label = _text(rec["contingency_id"])
        kind = _text(rec["kind"])
        if not label:
            raise ContractError(f"line {lineno}: empty contingency_id")
        if label in seen:
            raise ContractError(f"line {lineno}: contingency_id {label!r} repeats the one at line {seen[label]}")
        seen[label] = lineno
        try:
            order = _num(rec.get("order")) or 1
        except (TypeError, ValueError):
            raise ContractError(f"line {lineno}: order {rec.get('order')!r} is not an integer") from None
        cid = _contingency_id(label) if kind not in COMPOUND_SEPARATORS else label
        text_of = ",".join(_text(rec.get(c)) for c in df.columns)
        if kind == "branch" and order == 1:
            el = _Element("branch", lineno, text_of,
                          from_bus=_csv_bus(rec, "from_bus_number", "from_bus", lineno),
                          to_bus=_csv_bus(rec, "to_bus_number", "to_bus", lineno),
                          ckt=_text(rec.get("ckt")) or "1")
        elif kind == "unit":
            mid = _text(rec.get("machine_id"))
            if not mid:
                raise ContractError(f"line {lineno}: a unit row needs machine_id")
            el = _Element("machine", lineno, text_of,
                          bus=_csv_bus(rec, "bus_number", "bus", lineno), machine_id=mid)
        elif kind == "bus_section":
            el = _Element("bus", lineno, text_of, bus=_csv_bus(rec, "bus_number", "bus", lineno))
        elif kind == "branch" or kind in COMPOUND_SEPARATORS:
            # N-2 and compound rows carry their branches in element_ids only
            ids = [e for e in _text(rec.get("element_ids")).split("|") if e]
            if len(ids) != 2:
                raise ContractError(f"line {lineno}: a {kind} row of order 2 needs two element_ids, got {ids}")
            bad = [e for e in ids if e not in known_branch]
            if bad:
                for e in bad:
                    unmatched.append({"contingency_id": cid, "line": lineno, "element": e, "reason": "unknown_branch"})
                for e in ids:
                    if e not in bad:
                        unmatched.append({"contingency_id": cid, "line": lineno, "element": e, "reason": "partial"})
                continue
            rows.append({"contingency_id": cid, "kind": kind, "element_ids": ids, "order": 2})
            continue
        else:
            raise ContractError(f"line {lineno}: kind {kind!r} is not branch, unit, bus_section, "
                                f"{', '.join(sorted(COMPOUND_SEPARATORS))}")
        row = _settle(idx, label, lineno, cid, [el], unmatched)
        if row is not None:
            rows.append(row)
    return _finish(rows, unmatched)


def read_contingency_list(source, net, *, fmt: str | None = None) -> ImportedContingencies:
    """
    Read a TSO contingency list (``.con`` or CSV) onto ``net``.

    ``source`` is a path, or the file's text with ``fmt`` given (``"con"`` or
    ``"csv"``). For a path, ``fmt`` defaults to its suffix.
    """
    if isinstance(source, Path) or (isinstance(source, str) and fmt is None):
        path = Path(source)
        if fmt is None:
            fmt = path.suffix.lower().lstrip(".")
        text = path.read_text()
    else:
        text = source
    if fmt == "con":
        return _from_con(text, net)
    if fmt == "csv":
        return _from_csv(text, net)
    raise ContractError(f"contingency list format must be 'con' or 'csv', got {fmt!r}")
