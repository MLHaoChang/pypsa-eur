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
# A note the rating writes: `note.append("x")`, `notes.setdefault(id, []).append("x")`,
# `.append(f"x:{…}")`, `.extend(f"x:{m}" …)` or `notes[id] = ["x"]`.
_NOTE_LITERAL = re.compile(
    r"""(?:note|notes\.setdefault\([^)]*\))\s*\.\s*(?:append|extend)\(\s*\n?\s*f?"([^"]+)"""
    r"""|notes\[[^\]]+\]\s*=\s*\[\s*f?"([^"]+)\"""")


def _engine_note_codes() -> set[str]:
    """The tariff engine's notes as the adapter puts them on a bill (`_notes`)."""
    import types

    from services.study import engine_adapter as A

    raw = {a or b for a, b in _NOTE_LITERAL.findall(ENGINE_SRC.read_text(encoding="utf-8"))}
    raw = {r.split("{", 1)[0].rstrip(":_") if "{" in r else r for r in raw}
    out = set(A._notes(types.SimpleNamespace(notes={"i": sorted(raw)}), [], False))
    # the adapter's own partial-period note, and the compile's capacity note
    out |= set(A._notes(types.SimpleNamespace(notes={}), ["2025-01"], True))
    out.add("capacity_charge_assumed_connection_size")
    return out


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
