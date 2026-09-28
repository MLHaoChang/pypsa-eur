"""
Number and status formatting for report tables.

One helper per shape so the writer never formats inline: a ``None``, NaN or
infinite value is the string ``NOT_ESTABLISHED`` everywhere, which is the
document-level form of ADR-0001 ("unresolvable figures ship as null").
"""
from __future__ import annotations

import math
from typing import Any

NOT_ESTABLISHED = "not established"

_STATUS_WORDS = {
    "ok": "established",
    "not_established": "not established",
    "skipped": "not run in this study",
    "run": "run",
    "aborted": "aborted",
    "pending": "pending",
}


def fmt_number(value: Any, unit: str | None = None,
               digits: int | None = None) -> str:
    """
    ``None``/NaN/inf → ``NOT_ESTABLISHED``; bools → yes/no; ints and floats
    grouped by thousands. ``digits`` defaults by magnitude: none at ≥ 1000,
    two at ≥ 1, three below (so a small ‱ or a 0.3 h/yr keeps its meaning).
    """
    if value is None:
        return NOT_ESTABLISHED
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        text = f"{value:,d}"
        return f"{text} {unit}" if unit else text
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(f) or math.isinf(f):
        return NOT_ESTABLISHED
    if digits is None:
        magnitude = abs(f)
        digits = 0 if magnitude >= 1000 else (2 if magnitude >= 1 else 3)
    text = f"{f:,.{digits}f}"
    return f"{text} {unit}" if unit else text


def fmt_ci(interval: Any, unit: str | None = None, digits: int = 2) -> str:
    """A two-element confidence interval as ``lo – hi unit``."""
    if not interval or not isinstance(interval, (list, tuple)) \
            or len(interval) != 2:
        return NOT_ESTABLISHED
    lo, hi = interval
    if lo is None or hi is None:
        return NOT_ESTABLISHED
    lo_s = fmt_number(lo, digits=digits)
    hi_s = fmt_number(hi, digits=digits)
    if NOT_ESTABLISHED in (lo_s, hi_s):
        return NOT_ESTABLISHED
    text = f"{lo_s} – {hi_s}"
    return f"{text} {unit}" if unit else text


def fmt_status(status: Any) -> str:
    """The completeness / pipeline enum in prose."""
    if status is None:
        return NOT_ESTABLISHED
    return _STATUS_WORDS.get(str(status), str(status))


def fmt_text(value: Any) -> str:
    """A free-text cell: ``None`` is NOT_ESTABLISHED, everything else ``str``."""
    if value is None or value == "":
        return NOT_ESTABLISHED
    if isinstance(value, (list, tuple)):
        return ", ".join(fmt_text(v) for v in value) if value else NOT_ESTABLISHED
    return str(value)
