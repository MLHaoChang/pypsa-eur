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
# Gate U2-WP8b Z1, U2-WP9 V1: the rating's notes are read with `ast`, and
# EVERY use of the notes — the `notes` dict, `.notes` on any object, and every
# alias made of them (assignment, `:=`, a `for` over `.values()` / `.items()`,
# `dict(notes)`) — must be one the scan can classify, or it fails naming its
# line. A write is a string constant or an f-string whose literal head runs
# to a `:` (the prefix codes the study knows, `resolution:…`,
# `tiers_on_represented_volume:…`; the code is the head up to that `:`); a
# dict literal given as the notes (`notes = {…}`, `RatingResult(notes={…})`)
# is read entry by entry. A read is a membership test, a lookup, a builtin
# reader or the rating's own result (`RatingResult(notes=notes)`). A write
# through a variable, a concatenation, a call, a helper handed the notes
# (positionally or by keyword), `getattr`, or an unbound `list.append` cannot
# be read.
_NOTE_ROOTS = frozenset({"notes"})
_NOTE_SINKS = frozenset({("RatingResult", "notes")})
_BUILTIN_READERS = frozenset({"any", "all", "len", "sorted", "list", "set", "tuple", "bool"})


def _note_head(node) -> str | None:
    """A note code from one written element, or None when it cannot be read."""
    import ast

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values \
            and isinstance(node.values[0], ast.Constant) and isinstance(node.values[0].value, str):
        head = node.values[0].value
        if ":" in head:                    # a prefix code: the head up to its ':'
            return head[: head.index(":") + 1]
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
    (note codes, unclassified uses) of the tariff engine's rating. Every use
    of the notes or an alias of them is classified (see above); anything
    else is returned by line.
    """
    import ast

    tree = ast.parse(source)
    parent: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    codes: set[str] = set()
    bad: list[str] = []
    # An alias lives in the scope that binds it: a comprehension's target in
    # that comprehension, a `for` target in its loop, an assignment's or a
    # `:=`'s in the enclosing function (or the module).
    aliases: dict[str, set] = {}

    def ancestors(node):
        while node in parent:
            node = parent[node]
            yield node

    def scope_of(target, binder):
        if isinstance(binder, ast.comprehension):
            return parent[binder]
        if isinstance(binder, ast.For):
            return binder
        return next((a for a in ancestors(target)
                     if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                                       ast.Module))), tree)

    def fail(node, why: str) -> None:
        bad.append(f"line {getattr(node, 'lineno', '?')}: {why}: {ast.unparse(node)}")

    def read(elements, where) -> None:
        for el in elements:
            head = _note_head(el)
            if head is None:
                fail(el, "a note that is not a literal code")
            else:
                codes.add(head)

    def read_dict(d) -> None:
        """A dict literal given as the notes: each value is a list of codes."""
        for k, v in zip(d.keys, d.values, strict=True):
            if k is None:                  # `**other`: that mapping is a use of its own
                if not is_use(v):
                    fail(v, "notes unpacked from something the scan does not track")
                continue
            read(_note_elements(v), v)

    def is_use(e) -> bool:
        if isinstance(e, ast.Name):
            if e.id in _NOTE_ROOTS:
                return True
            scopes = aliases.get(e.id, set())
            return any(a in scopes for a in ancestors(e))
        if isinstance(e, ast.Attribute):
            return e.attr in _NOTE_ROOTS
        return False

    def bind(target, origin, binder=None) -> bool:
        """A new alias from `target`; False when the target cannot be tracked."""
        if isinstance(target, ast.Name):
            scope = scope_of(target, binder if binder is not None else origin)
            if scope not in aliases.setdefault(target.id, set()):
                aliases[target.id].add(scope)
                changed.append(target.id)
            return True
        fail(origin, "the notes bound to a target the scan does not track")
        return False

    def iterated(call) -> None:
        """`.values()` / `.items()`: a `for` over them binds each list as an alias."""
        p = parent.get(call)
        loop = p if isinstance(p, (ast.For, ast.comprehension)) and p.iter is call else None
        if loop is None:
            classify(call, collection=True)
            return
        tgt = loop.target
        if call.func.attr == "items" and isinstance(tgt, ast.Tuple) and len(tgt.elts) == 2:
            tgt = tgt.elts[1]
        bind(tgt, call, loop)

    def classify(e, *, collection: bool = False) -> None:
        p = parent.get(e)
        if isinstance(p, ast.Attribute) and p.value is e:
            call = parent.get(p)
            if not (isinstance(call, ast.Call) and call.func is p):
                fail(p, "a method of the notes taken without calling it")
                return
            attr = p.attr
            if attr in ("append", "add"):
                read(call.args[:1], call)
            elif attr == "insert":
                read(call.args[1:2], call)
            elif attr == "extend":
                read(_note_elements(call.args[0]) if call.args else [], call)
            elif attr == "setdefault":
                if len(call.args) > 1:
                    read(_note_elements(call.args[1]), call)
                classify(call)
            elif attr == "get":
                classify(call)
            elif attr in ("values", "items"):
                iterated(call)
            elif attr in ("keys", "count", "index"):
                return
            else:
                fail(call, f"`.{attr}` on the notes")
            return
        if isinstance(p, ast.Subscript) and p.value is e:
            if isinstance(p.ctx, ast.Store):
                owner = parent.get(p)
                if isinstance(owner, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    read(_note_elements(owner.value), owner)
                else:
                    fail(p, "a note stored by a form the scan does not read")
            else:
                classify(p)
            return
        if isinstance(p, ast.Compare):
            return                         # `"x" in note`, a membership read
        if isinstance(p, ast.IfExp) and p.test is not e or isinstance(p, ast.BoolOp):
            classify(p)                    # the expression's value may BE the notes
            return
        if isinstance(p, (ast.If, ast.While, ast.UnaryOp, ast.IfExp, ast.Assert,
                          ast.FormattedValue, ast.Expr)):
            return                         # truthiness, a format, a discarded value: a read
        if isinstance(p, (ast.For, ast.comprehension)) and p.iter is e:
            return                         # iterating codes or keys: a read
        if isinstance(p, ast.Call) and e in p.args:
            name = p.func.id if isinstance(p.func, ast.Name) else None
            if name in _BUILTIN_READERS and not collection:
                return
            if name in _BUILTIN_READERS and collection:
                return
            if name == "dict":
                assign = parent.get(p)
                if isinstance(assign, ast.Assign) and assign.value is p:
                    for t in assign.targets:
                        bind(t, p)
                    return
            fail(p, f"the notes handed to {ast.unparse(p.func)}")
            return
        if isinstance(p, ast.keyword):
            call = parent.get(p)
            name = call.func.id if isinstance(call.func, ast.Name) else (
                call.func.attr if isinstance(call.func, ast.Attribute) else None)
            if (name, p.arg) not in _NOTE_SINKS:
                fail(call, f"the notes handed to {ast.unparse(call.func)} as {p.arg}=")
            return
        if isinstance(p, ast.Assign) and p.value is e:
            for t in p.targets:
                bind(t, p)
            return
        if isinstance(p, ast.NamedExpr) and p.value is e:
            bind(p.target, p)
            return
        if isinstance(p, ast.Dict) and e in p.values and p.keys[p.values.index(e)] is None:
            return                         # `{**notes, ...}`: read by its own dict
        fail(p if p is not None else e, "a use of the notes the scan cannot classify")

    changed: list[str] = []
    for _ in range(10):                    # until no new alias appears
        codes.clear()
        bad.clear()
        changed.clear()
        for node in ast.walk(tree):
            # The notes defined or replaced: a dict literal is read, a
            # dataclass field's factory is a declaration, anything else fails.
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any((isinstance(t, ast.Name) and t.id in _NOTE_ROOTS)
                       or (isinstance(t, ast.Attribute) and t.attr in _NOTE_ROOTS)
                       for t in targets) and node.value is not None:
                    v = node.value
                    if isinstance(v, ast.Dict):
                        read_dict(v)
                    elif not (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                              and v.func.id == "field") and not is_use(v):
                        fail(node, "the notes replaced by a value the scan cannot read")
            if isinstance(node, ast.keyword) and node.arg in _NOTE_ROOTS \
                    and isinstance(node.value, ast.Dict):
                read_dict(node.value)
            if isinstance(node, (ast.Name, ast.Attribute)) and is_use(node) \
                    and isinstance(getattr(node, "ctx", None), ast.Load):
                classify(node)
        if not changed:
            break
    return codes, sorted(set(bad))


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
    ('note = notes.setdefault(i, [])\nnote.append(f"x_y:{m}")', {"x_y:"}),
    ('notes[i] = ["x_y"]', {"x_y"}),
    ('notes.setdefault(i, []).extend(f"x_y:{m}" for m in ms)', {"x_y:"}),
    ('notes.setdefault(i, ["x_y"])', {"x_y"}),
    # Gate U2-WP9 V1: a dict literal is read entry by entry; the reads the
    # real rating makes are classified, not ignored.
    ('notes = {i: ["x_y"]}', {"x_y"}),
    ('r = RatingResult(total=1, notes={i: ["x_y"]})', {"x_y"}),
    ('notes: dict = {}\nok = any("x_y" in v for v in notes.values())', set()),
    ('r = RatingResult(notes=notes)', set()),
    ('notes = {}\nfor lst in notes.values():\n    lst.append("x_y")', {"x_y"}),
    ('if (lst := notes.setdefault(i, [])):\n    lst.append("x_y")', {"x_y"}),
    ('n2 = dict(notes)\nn2.setdefault(i, []).append("x_y")', {"x_y"}),
    ('r.notes.setdefault(i, []).append("x_y")', {"x_y"}),
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
    # Gate U2-WP9 V1 (G4a, G4b, G4c and the probe's other forms):
    '_add_note(notes=notes, item=i)',                           # G4a: a keyword hand-off
    'r = RatingResult(notes={**extra, i: ["x_y"]})',           # unpacked from an untracked map
    'notes.setdefault(i, []).append(f"energy_on_partial_coverage{SUFFIX}")',   # G4c
    'notes.setdefault(i, []).append(f"sneaky_{kind}_code")',
    'for lst in notes.values():\n    lst.append(CODE)',
    'if (lst := notes.setdefault(i, [])):\n    lst.append(CODE)',
    'n2 = dict(notes)\nn2.setdefault(i, []).append(CODE)',
    'r.notes.setdefault(i, []).append(CODE)',
    'getattr(notes.setdefault(i, []), "append")("x_y")',
    'list.append(notes.setdefault(i, []), "x_y")',
    'notes = build_notes()',
    'f = notes.setdefault',
    'return notes',
    '(a if c else notes.setdefault(i, [])).append(CODE)',       # a ternary
    '(notes.get(i) or fresh).append(CODE)',
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
