"""
Values from an uploaded PDF (a datasheet) onto assets, each beside its page and quote.

Data-to-model slice 2 (owner AI-567b; plan `.scratch/data-to-model/slice-2-plan.md`). The
model, or the user in the Expert dialog, proposes a **citation** `{names, field, page,
quote, unit, value_text?}`; it never proposes a value. `plan` checks each citation and
returns the plan shape `equipment_table.plan` returns, so slice 1's `changes`, `apply`,
`require_confirmed` and `require_card_size` write it, cell for cell, through the native
component update. It writes nothing itself.

Rules (server verdicts; gatekeeper GO WITH CHANGES on the plan):

- **Parsed in a child process.** `read_pages` runs `pdf_text_worker.extract_to` in a
  `spawn` child with a hard wall-clock kill (`PARSE_SECONDS`) and an address-space limit (`PARSE_MEMORY`): pypdf has
  no timeout, and the API process never parses a PDF itself. Over the budget, or a parse
  error, refuses `pdf_unreadable`; an encrypted file `pdf_encrypted`. There is no OCR: a
  cited page with no text layer refuses (`no_text_layer`). At most the upload service's
  `PDF_PAGE_CAP` pages and `MAX_PAGE_CHARS` characters per page are read.
- **The quote is on the page.** Both sides are compared after NFKC (PDF ligatures) and
  whitespace collapsing, nothing else (`quote_not_found`, naming the pages where it does
  occur). The raw quote, at most `MAX_QUOTE_CHARS`, is kept in the row and in provenance.
- **The number is read from the quote.** Exactly one number, or the one `value_text`
  names (it must occur once); a range ("10-12", "10 to 12"), or any ± in the quote, is
  `ambiguous_value` (cite a shorter quote). A separator that reads two ways (`1.250`, `1,250`, or both marks)
  is `ambiguous_number` until `decimal_mark` is stated; spaces group thousands only with
  a stated mark. A negative value needs a field whose schema states a negative minimum
  (`negative_not_allowed`).
- **Units from the schema only.** As slice 1 (`equipment_table._column_verdict`): the
  stored unit, or the display unit by `display_scale`. A unit the quote does not state
  is written with a note on the card: the user named it.
- **Money stated, never assumed.** A cost needs a currency (stated, or named in the
  quote) and a money year (stated). A currency the quote names other than the stated one,
  or a stated one other than the project's finance currency, is `currency_mismatch`; a
  project that sets none is disclosed on the card. A money year other than the
  investment case's base year needs the `money_year` confirmation; nothing is adjusted.
- **Text in the document is data.** A quote is only ever matched and parsed; nothing in
  it is followed, and nothing is written without the user's click on the card.
"""
from __future__ import annotations

import hashlib
import re
import threading
import unicodedata
from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

from services import equipment_table as T

MAX_QUOTE_CHARS = 300
MAX_PAGE_CHARS = 20_000
MAX_CITATIONS = 50
PARSE_SECONDS = 20.0
PARSE_MEMORY = 1024 * 1024 * 1024
DECIMAL_MARKS = (".", ",")

# Parsed pages by the bytes' sha256 (the parse is a pure function of them).
_PAGES: OrderedDict[str, dict] = OrderedDict()
_PAGES_LOCK = threading.Lock()
_PAGES_KEPT = 8

_refuse = T._refuse

# The refusal kinds this module adds, as literals so `tests/test_tool_error_kind_manifest.py`
# derives them. The per-citation kinds (`quote_not_found`, `ambiguous_value`, ...) are rows
# in `unmapped`, never a tool_error frame, so they are not listed there.
_ERROR_KINDS = ({"error_kind": "pdf_unreadable"}, {"error_kind": "pdf_encrypted"},
                {"error_kind": "unsupported_document_format"})


# ── Read ───────────────────────────────────────────────────────────────────


def _parse(blob: bytes, max_pages: int) -> dict:
    """The worker's result for `blob`, from a `spawn` child killed at `PARSE_SECONDS`."""
    import multiprocessing

    from services import pdf_text_worker as W

    ctx = multiprocessing.get_context("spawn")
    receive, send = ctx.Pipe(duplex=False)
    child = ctx.Process(target=W.extract_to, args=(send, blob, max_pages, MAX_PAGE_CHARS, PARSE_MEMORY),
                        daemon=True, name="pdf-text")
    child.start()
    send.close()
    try:
        if not receive.poll(PARSE_SECONDS):
            _refuse("pdf_unreadable", f"The PDF could not be read within {PARSE_SECONDS:g} s; it is not parsed "
                    "further. Re-export it from its source, or type the values in.", budget_seconds=PARSE_SECONDS)
        try:
            out = receive.recv()
        except (EOFError, OSError):  # the child died (the memory limit, a crash) before it answered
            out = {}
    finally:
        receive.close()
        if child.is_alive():
            child.kill()
        child.join(5)
    if out.get("encrypted"):
        _refuse("pdf_encrypted", "The PDF is encrypted; it is not opened. Upload an unprotected copy.")
    if "pages" not in out:
        _refuse("pdf_unreadable", "The PDF could not be read" + (f" ({out['error']})" if out.get("error") else "")
                + ". Re-export it from its source, or type the values in.")
    return out


def read_pages(project: str, upload_id: str) -> dict:
    """The upload's text layer: `{source: {upload_id, filename, sha256, pages, truncated},
    pages: [text, ...]}`, page 1 first. `sha256` is of the bytes read."""
    from services import upload_service

    meta = upload_service.get_upload_meta(project, upload_id)
    blob = upload_service.get_upload_bytes(project, upload_id)
    sha = hashlib.sha256(blob).hexdigest()
    if meta.sha256 and meta.sha256 != sha:
        _refuse("source_changed", "The upload's bytes no longer match its recorded digest.")
    if not (meta.filename.lower().endswith(".pdf") or meta.mime == "application/pdf"):
        _refuse("unsupported_document_format", f"{meta.filename!r} is not a PDF.")
    with _PAGES_LOCK:
        parsed = _PAGES.get(sha)
    if parsed is None:
        parsed = _parse(blob, upload_service.PDF_PAGE_CAP)
        with _PAGES_LOCK:
            _PAGES[sha] = parsed
            while len(_PAGES) > _PAGES_KEPT:
                _PAGES.popitem(last=False)
    return {"source": {"upload_id": upload_id, "filename": meta.filename, "sha256": sha,
                       "pages": len(parsed["pages"]), "truncated": parsed.get("truncated", [])},
            "pages": parsed["pages"]}


def normal(text: str) -> str:
    """NFKC (ligatures, no-break and thin spaces) and collapsed whitespace; nothing else."""
    return " ".join(unicodedata.normalize("NFKC", text or "").split())


# ── Numbers ────────────────────────────────────────────────────────────────

_MINUS = "-−"
# A unit may sit between the two numbers of a range ("10 kW - 12 kW"). The separators: the
# hyphen and dashes U+2010-U+2015, the minus sign U+2212, "to", "bis", and two or three dots
# (an ellipsis is "..." after NFKC).
_RANGE_SEP = r"(?:[-\u2010-\u2015\u2212]|to|bis|\.{2,3}|\u2026)"
_UNIT_WORD = r"(?:[A-Za-z%°/²³·]+\s?)?"
_RANGE_AFTER = re.compile(rf"^\s?{_UNIT_WORD}{_RANGE_SEP}\s?[+\-\u2212]?\d", re.IGNORECASE)
_RANGE_BEFORE = re.compile(rf"\d\s?{_UNIT_WORD}{_RANGE_SEP}\s?$", re.IGNORECASE)
# Page text on each side of a quote that its numbers are read with: a quote cannot cut a
# range, a sign, a tolerance or a separator off its number.
CONTEXT_CHARS = 16
_PLUSMINUS = re.compile(r"(?:±|\+/-|\+-)")
_TOLERANCE_AFTER = re.compile(rf"^\s?{_UNIT_WORD}(?:±|\+/-|\+-)")


def _token_re(decimal_mark: str | None) -> re.Pattern:
    if decimal_mark is None:
        return re.compile(r"\d+(?:[.,]\d+)*")
    group = re.escape("," if decimal_mark == "." else ".") + " "
    return re.compile(rf"\d{{1,3}}(?:[{group}]\d{{3}})+(?:{re.escape(decimal_mark)}\d+)?|\d+(?:{re.escape(decimal_mark)}\d+)?")


def _read(text: str, decimal_mark: str | None) -> Decimal | None:
    """The token's value, or None when its separators read two ways."""
    if decimal_mark is not None:
        group = "," if decimal_mark == "." else "."
        return Decimal(text.replace(group, "").replace(" ", "").replace(decimal_mark, "."))
    marks = {c for c in text if c in ".,"}
    if not marks:
        return Decimal(text)
    if len(marks) > 1:
        return None
    parts = text.split(marks.pop())
    if len(parts) > 2:  # a repeated mark can only group thousands
        ok = 1 <= len(parts[0]) <= 3 and parts[0][0] != "0" and all(len(p) == 3 for p in parts[1:])
        return Decimal("".join(parts)) if ok else None
    if len(parts[1]) == 3 and parts[0] != "0":
        return None  # 1.250 or 1,250
    return Decimal(f"{parts[0]}.{parts[1]}")


def numbers(quote: str, decimal_mark: str | None = None) -> list[dict]:
    """Each number in the (normalised) quote: `{text, value, negative, range, ambiguous}`."""
    out = []
    for m in _token_re(decimal_mark).finditer(quote):
        before, after = quote[:m.start()], quote[m.end():]
        negative = bool(before) and before[-1] in _MINUS and not (len(before) > 1 and (before[-2].isalnum()))
        try:
            value = _read(m.group(), decimal_mark)
        except InvalidOperation:
            value = None
        out.append({"text": m.group(), "start": m.start(), "end": m.end(), "value": value, "negative": negative,
                    "range": bool(_RANGE_AFTER.match(after) or _RANGE_BEFORE.search(before)),
                    "plusminus": bool(_PLUSMINUS.search(before[-4:]) or _TOLERANCE_AFTER.match(after))})
    return out


# ── Money in a quote ───────────────────────────────────────────────────────

_SYMBOLS = {"€": "EUR", "£": "GBP"}
_DOLLARS = frozenset({"USD", "CAD", "AUD", "NZD"})
_CODES = frozenset({"EUR", "USD", "GBP", "CHF", "CAD", "AUD", "NZD", "DKK", "SEK", "NOK", "PLN", "CZK",
                    "HUF", "JPY", "CNY"})


def quoted_currencies(quote: str) -> set[str]:
    """The currencies a quote names: ISO codes, € and £; `$` reads as any dollar."""
    found = {c for s, c in _SYMBOLS.items() if s in quote}
    found |= {c for c in re.findall(r"\b[A-Z]{3}\b", quote) if c in _CODES}
    if "$" in quote and not found & _DOLLARS:
        found.add("$")
    return found


def _currency_conflict(stated: str, named: set[str]) -> str | None:
    for c in named:
        if c == "$" and stated not in _DOLLARS or c != "$" and c != stated:
            return c
    return None


# ── Plan ───────────────────────────────────────────────────────────────────


def _gap(kind: str, message: str, citation: dict, **extra) -> dict:
    return {"kind": kind, "message": message, "page": citation.get("page"), "field": citation.get("field"),
            "name": ", ".join(citation.get("names") or []), "quote": str(citation.get("quote") or "")[:MAX_QUOTE_CHARS],
            **extra}


def _negative_allowed(field: dict) -> bool:
    schema = field.get("validation_schema") or {}
    floor = schema.get("minimum", schema.get("exclusiveMinimum"))
    return isinstance(floor, (int, float)) and floor < 0


def on_page(text: str, quote: str) -> int | None:
    """Where the quote first occurs on the (normalised) page text as whole words, or None:
    "2500 kW" is not on a page that reads "12500 kW"."""
    start = text.find(quote)
    while start >= 0:
        end = start + len(quote)
        if (start == 0 or not text[start - 1].isalnum()) and (end == len(text) or not text[end].isalnum()):
            return start
        start = text.find(quote, start + 1)
    return None


def locate(c: dict, pages: list[str], src: dict) -> tuple[tuple | None, dict | None]:
    """`((raw_quote, quote, page, at), None)` for a citation whose quote is on its page
    (`pages` normalised), else `(None, gap)`."""
    raw_quote = str(c.get("quote") or "")
    quote = normal(raw_quote)
    page = c.get("page")
    if not quote or len(raw_quote) > MAX_QUOTE_CHARS:
        return None, _gap("quote_not_found", f"Cite a quote of 1 to {MAX_QUOTE_CHARS} characters.", c)
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= len(pages):
        return None, _gap("quote_not_found", f"Page {page!r} is not in the document (pages 1 to "
                          f"{len(pages)} are read).", c)
    if not pages[page - 1]:
        return None, _gap("no_text_layer", f"Page {page} has no text layer (a scan or an image); "
                          "nothing is read from it, as there is no OCR. Type the value in.", c)
    at = on_page(pages[page - 1], quote)
    if at is None:
        elsewhere = [k + 1 for k, text in enumerate(pages) if on_page(text, quote) is not None]
        return None, _gap("quote_not_found", f"The quote is not on page {page}" + (
            f"; it is on page {', '.join(map(str, elsewhere))}." if elsewhere else
            " or on any other page read, word for word (whole words: a quote cannot start or end "
            "inside a word or number).")
            + (f" Pages {src['truncated']} are read up to {MAX_PAGE_CHARS} characters." if src["truncated"] else ""),
            c, on_pages=elsewhere)
    return (raw_quote, quote, page, at), None


def _value(citation: dict, quote: str, page: str, at: int, decimal_mark: str | None,
           field: dict) -> tuple[dict | None, dict | None]:
    """`(number, None)` read from the quote at `page[at:]`, else `(None, gap)`. The number is
    picked in the quote and read with `CONTEXT_CHARS` of page text on each side."""
    found = numbers(quote, decimal_mark)
    want = citation.get("value_text")
    if want is not None:
        picked = [x for x in found if x["text"] == normal(str(want))]
        if len(picked) != 1:
            return None, _gap("ambiguous_value", f"{want!r} occurs {len(picked)} times as a number in the quote; "
                              f"it must occur once. The numbers read are {[x['text'] for x in found]}.", citation)
    elif len(found) != 1:
        return None, _gap("ambiguous_value", f"The quote holds {len(found)} numbers "
                          f"({[x['text'] for x in found]}); name the one to read in value_text.", citation)
    else:
        picked = found
    left = max(0, at - CONTEXT_CHARS)
    window = numbers(page[left:at + len(quote) + CONTEXT_CHARS], decimal_mark)
    same = [x for x in window if (x["start"], x["end"]) == (picked[0]["start"] + at - left, picked[0]["end"] + at - left)]
    if not same:
        return None, _gap("ambiguous_value", f"The quote cuts {picked[0]['text']} off a longer number on the page; "
                          "cite the whole number.", citation)
    number = same[0]
    # A tolerance anywhere in the quote: whose number it qualifies is a reading.
    if number["range"] or number["plusminus"] or _PLUSMINUS.search(quote):
        return None, _gap("ambiguous_value", f"{number['text']} is part of a range or a ± value in the quote; "
                          "a single value is not read from it. Type it in if one is meant.", citation)
    if number["value"] is None:
        return None, _gap("ambiguous_number", f"{number['text']!r} reads two ways (a thousands or a decimal "
                          "separator). State the document's decimal_mark ('.' or ',').", citation)
    value = -number["value"] if number["negative"] else number["value"]
    if value < 0 and not _negative_allowed(field):
        return None, _gap("negative_not_allowed", f"{citation.get('field')} takes no negative value; the quote "
                          f"reads {number['text']} with a minus sign.", citation)
    return {"text": number["text"], "value": value}, None


# ── Candidate quotes (the dialog's picker) ─────────────────────────────────

MAX_CANDIDATES = 12
_CURRENCY = r"(?-i:€|£|\$|[A-Z]{3})"
_PER = r"\s?(?:/|per\s)\s?"
# A unit goes on after "/", or after "per" and a unit word ("EUR per kW per year"); "MW
# per block" is MW.
_YEAR = r"(?:yr|year|a|annum)"
_GOES_ON = r"(?!\s?/)(?!\s?per\s(?:year|yr|annum|a|month|mo|day|d|hour|h|[kmg]wh|[km]w|[km]va|km)\b)"
_LABEL_WORD = re.compile(r"[A-Za-z]{4,}")
# The sign is the plan's check, with the field: a candidate is only offered.
_ANY_SIGN = {"validation_schema": {"minimum": float("-inf")}}


def _unit_re(unit: str) -> re.Pattern:
    """A unit as a quote may write it after its number, as whole words: "EUR/kW/yr" also
    reads "€/kW/yr", "EUR per kW/yr" and "/kW/yr" (the currency before the number). A unit
    that goes on ("kW" in "kW/yr", "kWh", "kW per year") is not it."""
    head, slash, rest = unit.partition("/")
    money = bool(slash) and (head in ("", "currency") or re.fullmatch(_CURRENCY, head) is not None)
    parts = rest.split("/") if money else unit.split("/")
    body = _PER.join(_YEAR if p.lower() in ("yr", "year", "a") else re.escape(p).replace(r"\ ", r"\s?")
                     for p in parts)
    lead = rf"(?:{_CURRENCY}\s?)?{_PER}" if money else ""
    return re.compile(rf"^\s?{lead}{body}(?![A-Za-z0-9]){_GOES_ON}", re.IGNORECASE)


def find(pages: list[str], wanted: dict[str, dict], decimal_mark: str | None = None) -> dict[str, dict]:
    """Lines of the document that could be cited for each wanted field: `{key: {candidates:
    [{page, quote, value_text, value, unit, label}], more}}`, from `wanted` = `{key: {label,
    units}}`. A candidate is a line (normalised, at most `MAX_QUOTE_CHARS`) holding a number
    followed by one of the field's units; it is offered only when it would pass `locate`
    and `_value` as cited. `value_text` names the number when the line holds several.
    Lines naming a word of the field's label come first, then by page."""
    units = {k: [(u, _unit_re(u)) for u in sorted(w.get("units") or [], key=len, reverse=True)]
             for k, w in wanted.items()}
    words = {k: {x.lower() for x in _LABEL_WORD.findall(str(w.get("label") or ""))} for k, w in wanted.items()}
    found: dict[str, list[dict]] = {k: [] for k in wanted}
    for p, raw in enumerate(pages, start=1):
        text = normal(raw)
        for line in dict.fromkeys(normal(x) for x in (raw or "").splitlines()):
            at = on_page(text, line) if line and len(line) <= MAX_QUOTE_CHARS else None
            if at is None:
                continue
            nums = numbers(line, decimal_mark)
            lowered = line.lower()
            for x in nums:
                pick = x["text"] if len(nums) > 1 else None
                number, gap = _value({"value_text": pick}, line, text, at, decimal_mark, _ANY_SIGN)
                if gap:
                    continue
                for k, options in units.items():
                    unit = next((u for u, rx in options if rx.match(line[x["end"]:])), None)
                    if unit is not None:
                        found[k].append({"page": p, "quote": line, "value_text": pick, "value": str(number["value"]),
                                         "unit": unit, "label": any(w in lowered for w in words[k])})
    out = {}
    for k, rows in found.items():
        rows.sort(key=lambda r: not r["label"])  # stable: page order within each group
        out[k] = {"candidates": rows[:MAX_CANDIDATES], "more": max(0, len(rows) - MAX_CANDIDATES)}
    return out


def plan(n, doc: dict, component_class: str, citations: list, *, decimal_mark: str | None = None,
         currency: str | None = None, money_year: int | None = None, confirm=(), base_year: int | None = None,
         project_currency: str | None = None, fields: dict | None = None) -> dict:
    """What an apply would write, in `equipment_table.plan`'s shape: `{component_class,
    source, targets, cells, unmapped, needs, missing, notes}`. Reads `n`; writes nothing."""
    from harness import edit_card

    if decimal_mark is not None and decimal_mark not in DECIMAL_MARKS:
        _refuse("invalid_edit_value", "decimal_mark is '.' or ','.")
    if not isinstance(citations, list) or not citations:
        _refuse("empty_mapping", "Cite at least one value: {names, field, page, quote, unit}.")
    if len(citations) > MAX_CITATIONS:
        _refuse("mapping_too_large", f"{len(citations)} citations; one card takes at most {MAX_CITATIONS}.",
                limits={"citations": MAX_CITATIONS})
    attr = edit_card._component_attr(component_class)
    frame = getattr(n, attr)
    series = getattr(n, f"{attr}_t", {})
    src = doc["source"]
    pages = [normal(p) for p in doc["pages"]]
    targets: dict[str, dict] = {}
    cells: list[dict] = []
    unmapped: list[dict] = []
    seen: dict[tuple[str, str], int] = {}
    years: dict[int, str] = {}
    for i, c in enumerate(citations):
        c = dict(c or {})
        found, gap = locate(c, pages, src)
        if gap:
            unmapped.append(gap)
            continue
        raw_quote, quote, page, at = found
        field_name = str(c.get("field") or "")
        field = (fields or {}).get(field_name)
        unit = (str(c.get("unit") or "")).strip() or None
        named = quoted_currencies(quote)
        stated = (currency or "").strip().upper() or (next(iter(named)) if len(named) == 1 and "$" not in named else None)
        verdict, reason = T._column_verdict(field_name, field, unit, {"currency": stated, "money_year": money_year})
        if reason:
            unmapped.append(_gap(reason["kind"], reason["message"], c,
                                 **({"accepted_units": reason["accepted_units"]} if "accepted_units" in reason else {})))
            continue
        if verdict["currency"]:
            clash = _currency_conflict(verdict["currency"], named)
            if clash:
                unmapped.append(_gap("currency_mismatch", f"The quote names {clash}; the value is stated as "
                                     f"{verdict['currency']}. Nothing is converted.", c))
                continue
            if project_currency and project_currency != verdict["currency"]:
                unmapped.append(_gap("currency_mismatch", f"The project's finance inputs are in {project_currency}; "
                                     f"this value is {verdict['currency']}. Nothing is converted.", c))
                continue
        number, reason = _value(c, quote, pages[page - 1], at, decimal_mark, field)
        if reason:
            unmapped.append(reason)
            continue
        names = [str(x).strip() for x in (c.get("names") or []) if str(x).strip()]
        if not names:
            unmapped.append(_gap("name_missing", f"Name the {component_class} this value is for.", c))
            continue
        lineage = {"upload_id": src["upload_id"], "filename": src["filename"], "sha256": src["sha256"],
                   "page": page, "quote": raw_quote, "raw": number["text"], "raw_unit": verdict["unit"],
                   "scale": str(verdict["scale"]), "decimal_mark": decimal_mark}
        value = T._as_value(number["value"] / verdict["scale"])
        for nm in names:
            if nm not in frame.index:
                unmapped.append(_gap("component_not_found", f"No {component_class} named {nm!r}.", c, name=nm))
                continue
            if (nm, verdict["field"]) in seen:
                unmapped.append(_gap("duplicate_field", f"Citations {seen[(nm, verdict['field'])] + 1} and {i + 1} "
                                     f"both set {nm}.{verdict['field']}; neither is applied.", c, name=nm))
                targets.get(nm, {}).pop(verdict["field"], None)
                cells[:] = [x for x in cells if (x["name"], x["field"]) != (nm, verdict["field"])]
                continue
            seen[(nm, verdict["field"])] = i
            held = series.get(verdict["field"]) if hasattr(series, "get") else None
            if held is not None and nm in getattr(held, "columns", ()):
                unmapped.append(_gap("time_series_not_supported_here", f"{nm}.{verdict['field']} is held as a "
                                     "time series; a static value would not apply. It is left as it is.", c, name=nm))
                continue
            try:
                edit_card._check_value(component_class, verdict["field"], value, field)
                edit_card._check_rating(nm, verdict["field"], value, field)
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                unmapped.append(_gap(detail.get("error_kind", "invalid_edit_value"), detail.get("message", ""), c, name=nm))
                continue
            targets.setdefault(nm, {})[verdict["field"]] = value
            notes = []
            if not re.search(rf"(?<![A-Za-z]){re.escape(normal(verdict['unit']))}(?![A-Za-z])", quote):
                notes.append(f"The quote does not state {verdict['unit']}; it was named for it.")
            if verdict["currency"] and not project_currency:
                notes.append(f"The project's finance inputs set no currency; recorded as {verdict['currency']}, "
                             "as stated.")
            cells.append({"name": nm, "field": verdict["field"], "label": verdict["label"], "value": value,
                          "unit": verdict["stored_unit"], "currency": verdict["currency"],
                          "money_year": verdict["money_year"], "source": lineage, "notes": notes})
            if verdict["money_year"] is not None:
                years.setdefault(verdict["money_year"], verdict["currency"])
    needs, notes = T.money_needs(years, base_year, said="the document states")
    return {"component_class": component_class, "source": src, "targets": {k: v for k, v in targets.items() if v},
            "cells": cells, "unmapped": unmapped, "needs": needs,
            "missing": [x["confirmation"] for x in needs if x["confirmation"] not in set(confirm or ())],
            "notes": notes}


def plan_active(args: dict) -> dict:
    """`plan` for the active project and network, from the tools' arguments."""
    from harness import edit_card
    from services import chat_tools
    from services.library import reference_apply as R
    from services.profile_gen.apply import _project_money
    from services.pypsa_service import PyPSAService

    doc = read_pages(chat_tools._require_active_project(), args.get("upload_id"))
    want = args.get("source_sha256")
    if want is not None and want != doc["source"]["sha256"]:
        _refuse("source_changed", "The document's bytes differ from the ones previewed. Preview it again.",
                previewed=want, now=doc["source"]["sha256"])
    ctx = PyPSAService.get_active_context()
    config = ctx.solver_state.get("solver_config")
    klass = args.get("component_class")
    return plan(ctx.network, doc, klass, args.get("citations") or [], decimal_mark=args.get("decimal_mark"),
                currency=args.get("currency"), money_year=args.get("money_year"), confirm=args.get("confirm") or (),
                base_year=R.case_base_year(ctx.network, config), project_currency=_project_money(ctx).get("currency"),
                fields=edit_card._fields(klass))


def summary(n, p: dict) -> dict:
    """What `preview_document_values` and the dialog's preview report of a plan."""
    out = T.summary(n, p)
    out.pop("limits", None)
    return {**out, "limits": {"citations": MAX_CITATIONS, "card_cells": T.MAX_CARD_CELLS,
                              "quote_chars": MAX_QUOTE_CHARS, "listed_gaps": T.MAX_LISTED_GAPS}}


def preview(args: dict) -> dict:
    """The dialog's preview, for the active project: `{document, fields?, candidates?, plan?,
    card?}`. `document` = `{source}`; with `component_class`, `fields` (`equipment_table.
    mappable_fields`) and, with `find`, `candidates` (`find`); with `citations` too, `plan`
    (`summary`) and `card`, the edit card the apply checks, None while a confirmation is
    missing or nothing changes. Writes nothing."""
    from harness import edit_card
    from services import chat_tools
    from services.pypsa_service import PyPSAService

    doc = read_pages(chat_tools._require_active_project(), args.get("upload_id"))
    out: dict = {"document": {"source": doc["source"]}}
    klass = args.get("component_class")
    if not klass:
        return out
    out["fields"] = T.mappable_fields(edit_card._fields(klass))
    if args.get("find"):
        out["candidates"] = find(doc["pages"], {f["field"]: f for f in out["fields"]}, args.get("decimal_mark"))
    if not args.get("citations"):
        return out
    # `find` is the picker's, not the edit's: the card is the one the apply builds.
    args = {**{k: v for k, v in args.items() if k != "find"}, "source_sha256": doc["source"]["sha256"]}
    with PyPSAService.get_lock():
        p = plan_active(args)
        out["plan"] = summary(PyPSAService.get_network(), p)
        out["card"] = None if p["missing"] or not out["plan"]["changed_cells"] else edit_card.preview(
            "apply_document_values", args)
    return out
