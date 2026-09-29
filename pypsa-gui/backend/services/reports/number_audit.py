"""
The number audit (WP3; assessment §3.3, "audit and flag").

A check, not a rewrite. Every numeric token in the prose is matched against
the flattened evidence (`evidence.flatten_numbers`); a hit records the
fact's path, a miss is listed as `unverified` for the viewer and the
appendix. Nothing here edits a paragraph.

Matching rules:

* **The token's own precision.** `3.2` verifies 3.21 and 3.15 (half a unit
  in the last displayed place); `3.21` does not verify 3.3.
* **Separators are read by the LAST one**: `1,234.5` and `1.234,5` and
  `1 234,5` are all 1234.5; a separator repeated (`1.234.568`) groups
  thousands; a lone `,` before exactly three digits groups thousands
  (`1,234`), a lone `.` is a decimal point (the evidence's own format).
* **Scales expand**: `1.2M`, `3.2 M€`, `€3.2k` — the tolerance scales too.
* **Units must agree when both sides have one**: `%` and `‱` never verify
  a `€` fact; `h/yr` on the page is `h` on the fact (`mc_lole_h`). A bare
  number verifies a fact of any unit.
* **Exempt by pattern**: four-digit years 1990–2100 with no unit, ordinal
  and section numbers at the start of a line (`1.`, `2)`), and anything
  inside a `sec:` id.

Pure: no I/O, no model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from models.report import SectionAudit, VerifiedNumber
from services.reports.evidence import NumberFact

AuditResult = SectionAudit

_THIN = "  "
_SCALES = {"k": 1e3, "M": 1e6, "G": 1e9}

_TOKEN = re.compile(
    r"(?<![\w.,])"
    r"(?:(?P<cur>€|EUR)[ " + _THIN + r"]?)?"
    r"(?P<num>\d+(?:[ " + _THIN + r",.]\d{3})*(?:[.,]\d+)?)"
    r"(?:[ " + _THIN + r"]?(?P<scale>[kMG])(?![A-Za-z]))?"
    r"(?:[ " + _THIN + r"]?(?P<unit>%|‱|h/yr|h/a|MWh/yr|MWh|MW|GWh|GW|kWh|kW|"
    r"€/MWh|€/kg|€/yr|€/a|€|EUR|/yr|kg|h)(?![A-Za-z/]))?"
    r"(?![\w])"
)
_SEC_ID = re.compile(r"\bsec:\S+")
_YEAR = re.compile(r"\A(?:199\d|20\d\d|2100)\Z")


@dataclass
class NumberToken:
    text: str
    value: float
    unit_hint: str | None
    start: int
    end: int
    # Half a unit in the last displayed place, already scaled.
    tolerance: float = 0.0


# ── tokeniser ───────────────────────────────────────────────────────────────


def _parse(num: str) -> tuple[float, int]:
    """`(mantissa, displayed decimals)` for the digits-and-separators text."""
    seps = [(i, c) for i, c in enumerate(num) if not c.isdigit()]
    if not seps:
        return float(num), 0
    last_i, last_c = seps[-1]
    digits_after = len(num) - last_i - 1
    decimal_at: int | None = last_i
    if last_c in " " + _THIN:
        decimal_at = None
    elif any(c == last_c for _, c in seps[:-1]):
        decimal_at = None
    elif last_c == "," and digits_after == 3 and len(seps) == 1:
        decimal_at = None
    digits = "".join(c for i, c in enumerate(num) if c.isdigit() or i == decimal_at)
    if decimal_at is None:
        return float(digits), 0
    return float(digits.replace(",", ".")), digits_after


def _is_ordinal(text: str, start: int, end: int) -> bool:
    """
    `1.` at the start of a line, or `2)` anywhere — an enumeration, not a
    quantity. A sentence may END in a number (`solves consumed: 3.`), so the
    dot form is only exempt when nothing precedes it on its line.
    """
    if end >= len(text) or text[end] not in ".)":
        return False
    if end + 1 < len(text) and not text[end + 1].isspace():
        return False
    if text[end] == ")":
        return True
    line_start = text.rfind("\n", 0, start) + 1
    return text[line_start:start].strip() == ""


def _normalise_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    u = unit.replace("EUR", "€")
    for per_year in ("/yr", "/a"):
        if u.endswith(per_year):
            u = u[:-len(per_year)]
    return u or None


def extract_numbers(text: str) -> list[NumberToken]:
    """Every numeric token of `text` that is not exempt, in order."""
    if not text:
        return []
    scrubbed = _SEC_ID.sub(lambda m: " " * (m.end() - m.start()), text)
    out: list[NumberToken] = []
    for m in _TOKEN.finditer(scrubbed):
        num, cur, scale, unit = m.group("num"), m.group("cur"), m.group("scale"), m.group("unit")
        bare = cur is None and scale is None and unit is None
        if bare and _YEAR.fullmatch(num):
            continue
        if bare and _is_ordinal(scrubbed, m.start("num"), m.end("num")):
            continue
        mantissa, decimals = _parse(num)
        factor = _SCALES.get(scale or "", 1.0)
        out.append(NumberToken(
            text=text[m.start():m.end()].strip(),
            value=mantissa * factor,
            unit_hint=unit or cur,
            start=m.start(), end=m.end(),
            tolerance=0.5 * 10.0 ** (-decimals) * factor,
        ))
    return out


# ── matching ────────────────────────────────────────────────────────────────


def _units_compatible(token_unit: str | None, fact_unit: str | None) -> bool:
    if token_unit is None or fact_unit is None:
        return True
    return _normalise_unit(token_unit) == _normalise_unit(fact_unit)


def _matches(token: NumberToken, fact: NumberFact) -> bool:
    if isinstance(fact.value, bool):
        return False
    if not _units_compatible(token.unit_hint, fact.unit):
        return False
    diff = abs(float(fact.value) - token.value)
    return diff <= token.tolerance + 1e-9 * max(1.0, abs(token.value))


def audit(paragraphs: list[str], facts: list[NumberFact]) -> AuditResult:
    """
    Every number in `paragraphs` checked against `facts`; each distinct
    token text is reported once, in order of first appearance.
    """
    verified: list[VerifiedNumber] = []
    unverified: list[str] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        for token in extract_numbers(paragraph or ""):
            if token.text in seen:
                continue
            seen.add(token.text)
            hit = next((f for f in facts if _matches(token, f)), None)
            if hit is None:
                unverified.append(token.text)
            else:
                verified.append(VerifiedNumber(text=token.text, path=hit.path))
    return AuditResult(verified=verified, unverified=unverified)
