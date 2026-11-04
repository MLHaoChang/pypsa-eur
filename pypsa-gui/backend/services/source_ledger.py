"""The source ledger of a research step (study harness issue 09, design §8;
owner decisions SH-RESEARCH-1, AI-567b; #970 fold, HH-07).

Pure functions over plain dicts: the harness keeps the rows in the run block
(`run.research.rows`) and calls these to build, verify and settle them.

- The model only *proposes* a row: value, unit, basis, url, title, quote.
  Only `propose` (server code) writes `verification`, `content_sha256`,
  `locator` and `fetched_at`, from the pages fetched in the same turn.
- A row is `verified` only when its url was fetched in that turn and its
  quote is verbatim in that page's retained text (whitespace-normalised, NFC,
  no case folding; GK B R2). Anything else is `unverified`: never shown as
  sourced, never offered as a value.
- The value carries the gap's unit and basis; another unit or basis is
  refused, never converted (`research_unit_mismatch`).
- `proposed` → `confirmed` (the card's click) or `rejected`; a rejected row
  stays in the ledger and the report. Competing rows for one gap sit side by
  side (`for_gap`).
"""

from __future__ import annotations

import math
import re
import unicodedata
import uuid
from datetime import datetime, UTC
from typing import Any
from collections.abc import Iterable

QUOTE_CHARS = 400
_JURISDICTION = re.compile(r"[A-Z]{2}(-[A-Z0-9]{1,6})?")
STATUSES = ("proposed", "confirmed", "rejected")
_SPACE = re.compile(r"\s+")


class LedgerError(ValueError):
    """A refused row or transition; `kind` is its tool-error kind."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def normalise(text: str) -> str:
    """NFC, runs of whitespace as one space, trimmed. Nothing else (R2)."""
    return _SPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _locate(quote: str, text: str) -> dict[str, int] | None:
    """Where the normalised quote sits in the normalised page text."""
    page = normalise(text)
    needle = normalise(quote)
    start = page.find(needle) if needle else -1
    return None if start < 0 else {"start": start, "end": start + len(needle)}


def propose(
    proposal: dict[str, Any],
    gap: dict[str, Any],
    fetched: Iterable[Any],
    *,
    turn_id: str,
) -> dict[str, Any]:
    """A `proposed` SourceRow for `gap` from the model's proposal, verified
    against this turn's fetched pages (each with url, fetched_at, title,
    sha256, text). Raises LedgerError for a malformed proposal or a unit or
    basis that is not the gap's."""
    if not isinstance(proposal, dict):
        raise LedgerError("invalid_source_row", "A source row is an object.")
    value = proposal.get("value")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise LedgerError(
            "invalid_source_row", "A source row's value is a finite number."
        )
    unit, basis = proposal.get("unit"), proposal.get("basis")
    want = gap["research"]
    if (
        not isinstance(unit, str)
        or unit.strip() != want["unit"]
        or not isinstance(basis, str)
        or basis.strip() != want["basis"]
    ):
        raise LedgerError(
            "research_unit_mismatch",
            f"This gap takes a value in {want['unit']} ({want['basis']}); "
            f"{unit!r} ({basis!r}) is refused, not converted.",
        )
    url, quote = proposal.get("url"), proposal.get("quote")
    if (
        not isinstance(url, str)
        or not url
        or not isinstance(quote, str)
        or not quote.strip()
    ):
        raise LedgerError(
            "invalid_source_row", "A source row names its url and a quote."
        )
    if len(quote) > QUOTE_CHARS:
        raise LedgerError(
            "invalid_source_row", f"A quote is at most {QUOTE_CHARS} characters."
        )
    extra = {}
    if gap.get("tool") == "attach_tariff":  # a Library tariff names its jurisdiction
        jurisdiction = proposal.get("jurisdiction")
        if not isinstance(jurisdiction, str) or not _JURISDICTION.fullmatch(
            jurisdiction.strip()
        ):
            raise LedgerError(
                "invalid_source_row",
                "A tariff row names its jurisdiction, e.g. DE or DE-BY.",
            )
        extra["jurisdiction"] = jurisdiction.strip()
    row = {
        "id": f"src_{uuid.uuid4().hex[:12]}",
        "for": dict(gap["target"]) | {"gap": gap["key"]},
        "value": float(value),
        "unit": want["unit"],
        "basis": want["basis"],
        "url": url,
        "title": str(proposal.get("title") or "")[:200],
        "quote": quote,
        "turn_id": turn_id,
        "status": "proposed",
        **extra,
    }
    page = next((f for f in fetched if f.url == url), None)
    if page is None:
        return row | {
            "verification": "unverified",
            "verification_reason": "url_not_fetched",
        }
    locator = _locate(quote, page.text)
    if locator is None:
        return row | {
            "verification": "unverified",
            "verification_reason": "quote_not_in_page",
            "fetched_at": page.fetched_at,
            "content_sha256": page.sha256,
        }
    return row | {
        "verification": "verified",
        "fetched_at": page.fetched_at,
        "content_sha256": page.sha256,
        "locator": locator,
        **({"title": page.title[:200]} if page.title and not row["title"] else {}),
    }


def sourced(row: dict[str, Any]) -> bool:
    """Whether a row may be shown as a source for its value."""
    return row.get("verification") == "verified"


def for_gap(rows: Iterable[dict[str, Any]], gap_key: str) -> list[dict[str, Any]]:
    """Competing rows of one gap, side by side, oldest first."""
    return [r for r in rows if r.get("for", {}).get("gap") == gap_key]


def settle(row: dict[str, Any], status: str, *, by: str) -> dict[str, Any]:
    """`proposed` → `confirmed` or `rejected`. An unverified row is never
    confirmed: it may only be rejected."""
    if status not in ("confirmed", "rejected"):
        raise LedgerError("invalid_source_row", "A row is confirmed or rejected.")
    if row.get("status") != "proposed":
        raise LedgerError(
            "research_row_settled", f"This row is already {row.get('status')}."
        )
    if status == "confirmed" and not sourced(row):
        raise LedgerError(
            "research_unverified", "An unverified source cannot be confirmed."
        )
    return row | {"status": status, f"{status}_by": by, f"{status}_at": _now()}


def card_view(row: dict[str, Any]) -> dict[str, Any]:
    """What a card shows beside the value: the source when verified, else
    the words "unverified source" and no link or quote presented as sourced."""
    base = {
        "row": row["id"],
        "value": row["value"],
        "unit": row["unit"],
        "basis": row["basis"],
        "status": row["status"],
        **({"jurisdiction": row["jurisdiction"]} if "jurisdiction" in row else {}),
    }
    if not sourced(row):
        return base | {
            "source": "unverified source",
            "reason": row.get("verification_reason"),
        }
    return base | {
        "source": "sourced",
        "url": row["url"],
        "title": row.get("title", ""),
        "quote": row["quote"],
        "fetched_at": row.get("fetched_at"),
    }
