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


# AA-05 S4 (V1): a strict job's placeholder for a withheld statement. It
# names the count and the reason only, never a value; the values are in the
# "Numbers to check" appendix, marked withheld. A section that carries one is
# a withheld section: every unverified number in it was withheld (a strict
# job keeps no statement the audit could not verify).
WITHHELD_PREFIX = "Withheld: "
APPENDIX_NAME = "Numbers to check"


def withheld_placeholder(count: int) -> str:
    """The inline placeholder for one withheld statement that quoted `count`
    unchecked numbers (0: the audit could not read it)."""
    if count <= 0:
        return f"{WITHHELD_PREFIX}a statement could not be checked against the study; see {APPENDIX_NAME}."
    noun = "number" if count == 1 else "numbers"
    return f"{WITHHELD_PREFIX}{count} {noun} could not be checked against the study; see {APPENDIX_NAME}."


def is_withheld_section(section: Any) -> bool:
    """True when `section` (a `Section` or its JSON dict) carries a strict
    job's withheld placeholder."""
    blocks = section.get("blocks") if isinstance(section, dict) else getattr(section, "blocks", None)
    for block in blocks or []:
        kind, btype, text = ((block.get("kind"), block.get("type"), block.get("text")) if isinstance(block, dict)
                             else (getattr(block, "kind", None), getattr(block, "type", None), getattr(block, "text", None)))
        if btype == "callout" and kind == "gap" and isinstance(text, str) and text.startswith(WITHHELD_PREFIX):
            return True
    return False


def withheld_marked(section: Any) -> list[str]:
    """The section's unverified numbers as the appendix lists them: marked
    "(unverified, withheld)" in a withheld section (V1), else as they are."""
    audit = section.get("audit") if isinstance(section, dict) else getattr(section, "audit", None)
    values = (audit.get("unverified") if isinstance(audit, dict) else getattr(audit, "unverified", None)) or []
    if not is_withheld_section(section):
        return list(values)
    return [f"{value} (unverified, withheld)" for value in values]
