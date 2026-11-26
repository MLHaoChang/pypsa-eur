"""The study's verdict, in the words the Guided Results card opens with.

The workbook's About sheet carries this line so a reader of the file sees the
same verdict the Guided screen gave. It is the lead of
`frontend/src/pages/hubDesign/headline.ts`: the Guided sentence starts with it
and may continue (the top risk, the interval, what would settle it). Built from
`eh_review.review_report(...)["summary"]` alone; `tests/data/verdict_line_cases.json`
is read by this module's test and by `headline.test.ts`, so the two cannot drift.
"""

from __future__ import annotations

import math
from typing import Any

# `frontend/src/pages/hubDesign/verdictWords.ts`
_WORD = {"pass": "Certified", "inconclusive": "Not decided"}


def _word(verdict: str) -> str:
    return _WORD.get(verdict, "Not certified")


def _num(v: Any) -> float | None:
    return (
        float(v)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        else None
    )


def _g(v: float) -> str:
    return format(v, "g")


def verdict_line(summary: dict | None) -> str | None:
    """The lead of the Guided verdict sentence, or None without a summary."""
    if not isinstance(summary, dict):
        return None
    verdict = (
        summary.get("verdict") if isinstance(summary.get("verdict"), str) else None
    )
    lole = _num(summary.get("mc_lole_h_per_year"))
    target = _num(summary.get("target_lole_h"))
    if verdict == "fail" and lole is not None and target is not None:
        return f"{_word('fail')}: about {lole:.1f} h/yr of shortfall vs a {_g(target)} h/yr goal"
    if verdict == "inconclusive" and target is not None:
        return _word("inconclusive")
    if verdict == "pass" and lole is not None and target is not None:
        if lole < 0.05:
            return f"{_word('pass')}: no shortfall expected, under the {_g(target)} h/yr goal."
        return f"{_word('pass')}: about {lole:.1f} h/yr of shortfall, under the {_g(target)} h/yr goal."
    if lole is not None and target is None:
        if lole < 0.05:
            return "No reliability goal was set: the study expects no shortfall. Set a goal to certify."
        return f"No reliability goal was set: the study reports {lole:.1f} h/yr of shortfall. Set a goal to certify."
    if lole is None and target is None:
        return (
            "No reliability goal is set for this site, so the study did not certify it"
        )
    return "The study could not certify reliability"
