"""
PSS/E dynamic data (``.dyr``) reader, without pypsa (owner decision IMPORTS-1).

``read_dyr_text(text)`` returns one ``DyrRecord`` per record, in file order.
``parameters(record)`` names a record's values when its model has a layout here.

**Grammar.** A record is ``IBUS 'MODEL' ID value ... /``: the bus number, the
quoted model name, the machine ID (quoted or bare), then the ICONs and CONs in
the model's order. A record may run over several lines and ends at ``/``; the
rest of that line is a comment. A line starting with ``@!`` is a comment. A
value is a finite number, or a quoted text ICON (REPCA1's circuit). Anything else
raises ``ContractError`` naming the line: a value that is neither, a quote that
is not closed, a record with no ``/`` before the end of the file, and a second
record for the same bus, machine ID and model.

**Machine IDs** (GK A D3): compared as text after stripping the quotes and the
blanks around them, so ``'1'``, ``1`` and ``' 1'`` are one machine. Case is kept,
so ``'A'`` and ``'a'`` are two machines, as in PSS/E.

**Names.** The layouts are the ones our own writer emits
(``gridspine.handoff.dyr_writer``: ``DYR_CONS``, ``DYR_CONTROLS`` and the WECC
converter records), so a field reads back under the name it was written from.
A model without a layout here, or a record whose value count differs from its
layout (GK A D1), keeps its values in order; the caller lists it as unmapped.
Values are never truncated or padded to fit a layout.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from gridspine.handoff.dyr_writer import (
    DYR_CONS,
    DYR_CONTROLS,
    IBR_RECORDS,
    WT3_RECORDS,
)
from gridspine.schema.contracts import ContractError
from gridspine.templates.unit_params import (
    REECC1_CONS,
    REECC1_ICONS,
    REPCA1_CONS,
    REPCA1_ICONS,
)

#: model -> (ICON names, CON names), in file order. The leading BUSR of the
#: electrical controllers and REPCA1's BUSR, BUS1, BUS2, CKT are ICONs the writer
#: authors itself; they read back under these names.
LAYOUTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    **{m: ((), tuple(cons)) for m, cons in DYR_CONS.items()},
    **{m: (tuple(i), tuple(c)) for m, (i, c) in DYR_CONTROLS.items()},
    IBR_RECORDS[0][0]: (tuple(IBR_RECORDS[0][1]), tuple(IBR_RECORDS[0][2])),
    IBR_RECORDS[1][0]: (("busr", *IBR_RECORDS[1][1]), tuple(IBR_RECORDS[1][2])),
    "REECC1": (("busr", *REECC1_ICONS), tuple(REECC1_CONS)),
    "REPCA1": (("busr", "bus1", "bus2", "ckt", *REPCA1_ICONS), tuple(REPCA1_CONS)),
    **{m: (tuple(i), tuple(c)) for m, i, c in WT3_RECORDS},
}

_TOKEN = re.compile(r"\s*('[^']*'|\"[^\"]*\"|/|[^\s'\"/]+)")
_INT = re.compile(r"[+-]?\d+")


@dataclass(frozen=True)
class DyrRecord:
    """One record: its first line, bus, model, machine ID and values in order."""

    line: int
    bus: int
    model: str
    machine: str
    values: tuple[float | int | str, ...]


def machine_id(text) -> str:
    """A machine ID as compared (D3): quotes and surrounding blanks stripped."""
    s = str(text).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1]
    return s.strip()


def _tokens(text: str):
    """(line, token) pairs; ``/`` ends a record and the rest of its line."""
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if raw.lstrip().startswith("@!"):
            continue
        pos = 0
        while pos < len(raw):
            m = _TOKEN.match(raw, pos)
            if m is None:
                if raw[pos:].strip():
                    raise ContractError(
                        f"line {lineno}: unclosed quote: {raw.strip()!r}"
                    )
                break
            tok = m.group(1)
            yield lineno, tok
            if tok == "/":
                break
            pos = m.end()


def _value(tok: str, lineno: int, model: str):
    if tok[0] in "'\"":
        return tok[1:-1].strip()
    if _INT.fullmatch(tok):
        return int(tok)
    try:
        v = float(tok)
    except ValueError:
        raise ContractError(
            f"line {lineno}: {model} value {tok!r} is not a number"
        ) from None
    if not math.isfinite(v):
        raise ContractError(
            f"line {lineno}: {model} value {tok!r} is not a finite number"
        )
    return v


def _record(toks: list[tuple[int, str]]) -> DyrRecord:
    line = toks[0][0]
    if len(toks) < 3:
        raise ContractError(
            f"line {line}: a record needs 'IBUS MODEL ID' before '/', got "
            f"{' '.join(t for _, t in toks)!r}"
        )
    bus_tok = toks[0][1]
    if not _INT.fullmatch(bus_tok):
        raise ContractError(f"line {line}: bus {bus_tok!r} is not a bus number")
    model = machine_id(toks[1][1]).upper()
    if not model:
        raise ContractError(f"line {line}: empty model name")
    mid = machine_id(toks[2][1])
    if not mid:
        raise ContractError(f"line {line}: empty machine ID in {model} record")
    values = tuple(_value(t, ln, model) for ln, t in toks[3:])
    return DyrRecord(line, int(bus_tok), model, mid, values)


def read_dyr_text(text: str) -> list[DyrRecord]:
    """Every record of a ``.dyr`` file, in file order."""
    out: list[DyrRecord] = []
    seen: dict[tuple[int, str, str], int] = {}
    pending: list[tuple[int, str]] = []
    for lineno, tok in _tokens(text):
        if tok != "/":
            pending.append((lineno, tok))
            continue
        if not pending:
            raise ContractError(f"line {lineno}: '/' with no record before it")
        rec = _record(pending)
        pending = []
        key = (rec.bus, rec.machine, rec.model)
        if key in seen:
            raise ContractError(
                f"line {rec.line}: second {rec.model} record for machine "
                f"{rec.machine!r} at bus {rec.bus} (the first is at line {seen[key]})"
            )
        seen[key] = rec.line
        out.append(rec)
    if pending:
        raise ContractError(
            f"line {pending[0][0]}: record has no '/' before the end of the file"
        )
    return out


def parameters(rec: DyrRecord) -> tuple[dict | None, str | None]:
    """
    ``(named values, None)`` when the record fits its model's layout, else
    ``(None, reason)``. ICONs keep their read value; CONs are floats.
    """
    layout = LAYOUTS.get(rec.model)
    if layout is None:
        return None, f"no parameter names for model {rec.model}"
    icons, cons = layout
    expected = len(icons) + len(cons)
    if len(rec.values) != expected:
        return None, f"parameter count {len(rec.values)}, expected {expected}"
    out = dict(zip(icons, rec.values[: len(icons)]))
    for name, v in zip(cons, rec.values[len(icons) :]):
        if isinstance(v, str):
            raise ContractError(
                f"line {rec.line}: {rec.model} {name} {v!r} is not a number"
            )
        out[name] = float(v)
    return out, None
