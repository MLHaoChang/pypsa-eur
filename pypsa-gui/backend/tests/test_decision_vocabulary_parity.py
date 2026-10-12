"""
S8: the guided flow's copy for the study's own honesty codes is a MIRROR of
`services/study/report.py::HELP` (and its prefix table) in
`frontend/src/utils/decisionVocabulary.ts`, the frontend's one source of
novice wording. This test is the parity: the two tables must be equal, key
for key and sentence for sentence, so a code added or reworded on one side
fails the build until the other follows.

DECISION (plan S8): mirror + parity rather than a payload field. The only
payload carrying the sentences today is the assembled report's
`honesty_help`, which does not exist before a report is assembled, and the
verdict page must explain its disclosures before that.

The TS side keeps each table as a JSON literal between marker comments, so
this reads it with `json.loads`, not a TypeScript parser.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from services.study import report

TS = (pathlib.Path(__file__).resolve().parents[2]
      / "frontend" / "src" / "utils" / "decisionVocabulary.ts")


def _block(name: str):
    text = TS.read_text(encoding="utf-8")
    m = re.search(rf"// BEGIN {name}\n.*?=\s*(\{{.*?\}}|\[.*?\])\s*(?:as const)?\s*\n// END {name}",
                  text, re.S)
    assert m, f"{name} block not found in {TS}"
    return json.loads(m.group(1))


def test_the_help_mirror_equals_the_backend_table():
    mirror = _block("HELP MIRROR")
    assert set(mirror) == set(report.HELP), (
        f"missing in TS: {sorted(set(report.HELP) - set(mirror))}; "
        f"extra in TS: {sorted(set(mirror) - set(report.HELP))}")
    for code, text in report.HELP.items():
        assert mirror[code] == text, code


def test_the_prefix_mirror_equals_the_backend_table():
    mirror = [tuple(pair) for pair in _block("PREFIX HELP MIRROR")]
    assert mirror == list(report._PREFIX_HELP)


def test_the_fallback_sentence_is_the_same():
    assert _block("FALLBACK MIRROR") == {"text": report._FALLBACK}


# ── U2 WP8 part B, gate C7: the engine's bill notes reach the user ───────

ENGINE_SRC = (pathlib.Path(__file__).resolve().parents[1]
              / "services" / "commercial" / "tariff_engine.py")
# Gate U2-WP8b Z1: the rating's note writes are read with `ast`, not a regex,
# and every write site must resolve. A note the rating writes is a string
# constant (`note.append("x")`, `notes.setdefault(id, []).append("x")`,
# `notes[id] = ["x"]`) or an f-string with a literal head (`f"x:{…}"`, the
# code being the head); `.extend` takes an iterable of those. A write through
# a variable, a concatenation, a call or a helper given the notes cannot be
# read, and the scan names its line instead of passing it by.
_NOTE_ROOTS = frozenset({"notes"})
_WRITE_METHODS = frozenset({"append", "extend", "insert", "add", "update"})
_NOTE_READERS = frozenset({"setdefault", "get", "values", "items", "keys"})
_BUILTIN_READERS = frozenset({"any", "all", "len", "sorted", "list", "dict", "set", "tuple"})


def _note_head(node) -> str | None:
    """A note code from one written element, or None when it cannot be read."""
    import ast

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values \
            and isinstance(node.values[0], ast.Constant) and isinstance(node.values[0].value, str):
        return node.values[0].value.rstrip(":_") or None
    return None


def _note_elements(node) -> list:
    """The elements an `.extend` / list write carries (one node when not a collection)."""
    import ast

    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return list(node.elts)
    if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        return [node.elt]
    return [node]


def scan_note_writes(source: str) -> tuple[set[str], list[str]]:
    """
    (note codes, unreadable write sites) of the tariff engine's rating:
    every `.append/.extend/.insert/.add/.update` on the notes dict or an alias
    of one of its lists, every `notes[…] = …` / `+=`, a non-empty
    `setdefault` default, and any call that is handed the notes (a helper
    could write through it).
    """
    import ast

    tree = ast.parse(source)
    aliases = set(_NOTE_ROOTS)

    def noteish(e) -> bool:
        if isinstance(e, ast.Name):
            return e.id in aliases
        if isinstance(e, ast.Subscript):
            return noteish(e.value)
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute):
            return e.func.attr in ("setdefault", "get") and noteish(e.func.value)
        return False

    for _ in range(3):                    # aliases of aliases (`note = notes.setdefault(…)`)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and noteish(node.value):
                aliases |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    codes: set[str] = set()
    bad: list[str] = []

    def read(elements, where) -> None:
        for el in elements:
            head = _note_head(el)
            if head is None:
                bad.append(f"line {where.lineno}: {ast.unparse(el)}")
            else:
                codes.add(head)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and noteish(node.func.value):
            attr = node.func.attr
            if attr in ("append", "add"):
                read(node.args[:1], node)
            elif attr == "insert":
                read(node.args[1:2], node)
            elif attr == "extend":
                read(_note_elements(node.args[0]) if node.args else [], node)
            elif attr in _WRITE_METHODS:
                bad.append(f"line {node.lineno}: {ast.unparse(node)}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "setdefault" and noteish(node.func.value) \
                and len(node.args) > 1:
            read(_note_elements(node.args[1]), node)
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Subscript) and noteish(t.value) for t in targets) \
                    or (isinstance(node, ast.AugAssign) and noteish(node.target)):
                read(_note_elements(node.value), node)
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else None
            handed = [a for a in node.args if isinstance(a, ast.Name) and a.id in aliases]
            if handed and name not in _BUILTIN_READERS and not (
                    isinstance(node.func, ast.Attribute) and node.func.attr in _NOTE_READERS):
                bad.append(f"line {node.lineno}: the notes handed to {ast.unparse(node.func)}")
    return codes, bad


def _engine_note_codes() -> set[str]:
    """The tariff engine's notes as the adapter puts them on a bill (`_notes`)."""
    import types

    from services.study import engine_adapter as A

    raw, bad = scan_note_writes(ENGINE_SRC.read_text(encoding="utf-8"))
    assert bad == [], f"note writes the scan cannot read: {bad}"
    out = set(A._notes(types.SimpleNamespace(notes={"i": sorted(raw)}), [], False))
    # the adapter's own partial-period note, and the compile's capacity note
    out |= set(A._notes(types.SimpleNamespace(notes={}), ["2025-01"], True))
    out.add("capacity_charge_assumed_connection_size")
    return out


def test_every_note_write_in_the_tariff_engine_is_read():
    """Gate Z1: no write site the scan cannot resolve to a literal (named by line)."""
    _codes, bad = scan_note_writes(ENGINE_SRC.read_text(encoding="utf-8"))
    assert bad == []


@pytest.mark.parametrize("snippet, read", [
    ('notes.setdefault(i, []).append("x_y")', {"x_y"}),
    ("notes.setdefault(i, []).append('x_y')", {"x_y"}),
    ('note = notes.setdefault(i, [])\nnote.append(f"x_y:{m}")', {"x_y"}),
    ('notes[i] = ["x_y"]', {"x_y"}),
    ('notes.setdefault(i, []).extend(f"x_y:{m}" for m in ms)', {"x_y"}),
    ('notes.setdefault(i, ["x_y"])', {"x_y"}),
])
def test_the_scan_reads_each_literal_way_of_writing_a_note(snippet, read):
    assert scan_note_writes(snippet) == (read, [])


@pytest.mark.parametrize("snippet", [
    'code = "x_y"\nnotes.setdefault(i, []).append(code)',      # gate O4a
    'notes.setdefault(i, []).append("x_" + "y")',               # gate O4d
    'note = notes.setdefault(i, [])\nnote.append(CODE)',
    'notes.setdefault(i, []).extend(CODES)',
    'notes[i] = codes',
    'notes.setdefault(i, []).append(f"{x}_y")',
    '_add_note(notes, i, "x_y")',
    'notes.update(extra)',
])
def test_the_scan_refuses_a_write_it_cannot_read(snippet):
    _codes, bad = scan_note_writes(snippet)
    assert bad, snippet


def test_the_engine_bill_notes_are_found():
    """The guard on the scan: the notes gate C7 names are among them."""
    codes = _engine_note_codes()
    assert {"bill_resolution_differs_from_settlement", "fixed_charge_prorated_on_partial_period",
            "demand_on_partial_month", "capacity_charge_prorated_by_hours"} <= codes
    assert not {c for c in codes if ":" in c or not re.fullmatch(r"[a-z]+(_[a-z]+)*", c)}


def test_every_engine_bill_note_has_the_studys_own_sentence():
    """
    Gate C7: a note the tariff engine puts on a bill reaches the user (the
    case's notes, the report, the bill preview), so the study explains it in
    plain words — never the fallback.
    """
    missing = sorted(c for c in _engine_note_codes() if report.help_for(c)[1] != "study")
    assert missing == []
