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
