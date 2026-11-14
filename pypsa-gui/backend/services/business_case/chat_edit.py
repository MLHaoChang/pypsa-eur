"""`set_business_case_lines`' plan: the request checks, the card's rows and the price
citations (business case 08b; GK B (54) Q1, D1-D3).

One function, `plan`, serves both the edit card (`harness/edit_card_business_case.py`)
and the tool's handler, so the card and the write refuse the same calls:

* `if_match` is required; empty or `*` is refused (`business_case_if_match_required`),
  as `set_finance_inputs` does. A stale tag is `business_case_changed` (412).
* The rows are a diff against the stored config: each value added, changed (old → new)
  or removed. A removed line shows every value it held, so a removal is never silent.
  The digest binds exactly these rows (`edit_card._digest`).
* A price (`price`, `price_by_year[y]`) that is new or changed needs a citation the
  run intake accepts (the owner's own typed turn, an upload's sheet/cell, a PDF
  page/quote), read through `run_intake.read`, never a fork. The cited number must be
  the price. Anything else is `price_not_from_user`, with the verifier's own reason.
  An unchanged price keeps its stored source ("unchanged", or "preset default
  (assumed)" for a pack default the preset filled).
* A card over `CARD_ROW_CAP` rows or `CARD_CHARS` is refused
  (`business_case_card_too_large`), never cut.
"""

from __future__ import annotations

import json
import math
from typing import Any

from fastapi import HTTPException

# Tool-side refusals, as literals for the error-kind manifest guard.
ERROR_KINDS = (
    {"error_kind": "business_case_if_match_required"},
    {"error_kind": "business_case_clear_invalid"},
    {"error_kind": "price_not_from_user"},
    {"error_kind": "business_case_card_too_large"},
)

CARD_ROW_CAP = 60
CARD_CHARS = 24_000
NAME = "Business case"
_PRICE = "price"
_PRICE_BY_YEAR = "price_by_year"

_WORDS = {
    "price": "Price",
    "price_by_year": "Price",
    "quantity": "Quantity",
    "quantity_by_year": "Quantity",
    "quantity_source": "Quantity source",
    "unit": "Unit",
    "kind": "Kind",
    "category": "Category",
    "money_year": "Money year",
    "escalation": "Escalation",
    "indexation": "Indexation",
    "start_year": "Start year",
    "end_year": "End year",
    "depreciation": "Depreciation",
    "life_years": "Life (years)",
    "rate": "Rate",
    "refresh_years": "Refresh (years)",
    "note": "Note",
    "assumed": "Pack default",
    "default_keys": "Default key",
    "default_units": "Default unit",
    "preset_rule": "Preset rule",
    # A commercial share only, never a volume factor such as lease-up (#1430 F2).
    "linked_share": "Sold share",
    "linked_share_by_year": "Sold share",
    "hint": "Hint",
    "kw_per_gpu": "kW per GPU",
    "lhv_kwh_per_kg": "LHV (kWh/kg)",
    "capacity": "Capacity",
    "capacity_unit": "Capacity unit",
    "break_even_line": "Break-even line",
    "inputs.preset": "Preset",
    "inputs.receivable_days": "Receivable days",
}


def _refuse(kind: str, message: str, status: int = 422, **extra) -> None:
    raise HTTPException(status, {"error_kind": kind, "message": message, **extra})


def _blank(value) -> bool:
    return value is None or value == "" or value == {} or value == []


def _leaves(doc, prefix: tuple = ()) -> dict[tuple, Any]:
    """Every non-blank leaf by key path; objects open fully, lists stay leaves."""
    out: dict[tuple, Any] = {}
    for key, value in (doc or {}).items():
        path = prefix + (str(key),)
        if isinstance(value, dict) and value:
            out.update(_leaves(value, path))
        elif not _blank(value):
            out[path] = value
    return out


def _equal(a, b) -> bool:
    if (
        isinstance(a, (int, float))
        and isinstance(b, (int, float))
        and not isinstance(a, bool)
        and not isinstance(b, bool)
    ):
        return float(a) == float(b)
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(
        b, sort_keys=True, default=str
    )


def request(if_match, business_case, clear) -> tuple[dict | None, str]:
    """The PUT's body value and `If-Match` tag, or the tool's refusal (no '*', no
    implicit clear: as `chat_tools._finance_request`)."""
    tag = (if_match if isinstance(if_match, str) else "").strip()
    if tag.strip('"').strip() in ("", "*"):
        _refuse(
            "business_case_if_match_required",
            "pass the `digest` get_business_case(detail='inputs') returned as if_match; "
            "'*' is refused",
        )
    if clear and business_case is not None:
        _refuse(
            "business_case_clear_invalid",
            "clear=true removes every business-case line: pass no `business_case` with it",
        )
    if not clear and business_case is None:
        _refuse(
            "business_case_clear_invalid",
            "pass the whole `business_case` object, or clear=true to remove every line",
        )
    if business_case is not None and not isinstance(business_case, dict):
        _refuse("business_case_inputs_invalid", "`business_case` is an object")
    return (None if clear else business_case), tag


def _lines(doc: dict | None) -> dict[str, dict]:
    lines = ((doc or {}).get("inputs") or {}).get("lines") or []
    return {ln.get("name"): ln for ln in lines if isinstance(ln, dict)}


def _rest(doc: dict | None) -> dict:
    """The config without its lines."""
    out = {k: v for k, v in (doc or {}).items() if k != "inputs"}
    inputs = {
        k: v for k, v in ((doc or {}).get("inputs") or {}).items() if k != "lines"
    }
    if inputs:
        out["inputs"] = inputs
    return out


def _is_price(path: tuple) -> bool:
    return path == (_PRICE,) or (len(path) == 2 and path[0] == _PRICE_BY_YEAR)


def _label(line: str | None, path: tuple) -> str:
    joined = ".".join(path)
    head = (
        _WORDS.get(joined)
        or _WORDS.get(path[0])
        or path[0].replace("_", " ").capitalize()
    )
    tail = [p for p in path[1:]] if joined not in _WORDS else []
    words = " ".join([head, *tail])
    return f"{line} · {words}" if line is not None else words


def _price_unit(line: dict, currency: str | None) -> str:
    cur = currency or "currency"
    unit = line.get("unit")
    return cur if unit == "lump" else f"{cur}/{unit}"


def _unit(line: dict | None, path: tuple, currency: str | None) -> str:
    if line is None:
        return ""
    if _is_price(path):
        return _price_unit(line, currency)
    if path[0] in ("quantity", "quantity_by_year"):
        return str(line.get("unit") or "")
    return ""


def _row(line: str | None, path: tuple, old, new, change: str, unit: str) -> dict:
    from harness.edit_card import _setting

    full = (["inputs", "lines", line] if line is not None else []) + list(path)
    label = _label(line, path)
    return {
        "component_class": "Settings",
        "name": NAME,
        "field": ".".join(full),
        "label": label,
        "old": _setting(old),
        "new": _setting(new),
        "unit": unit,
        "role": "typed",
        "says_name": label,
        "path": full,
        "line": line,
        "change": change,
    }


def _money(stored_finance) -> tuple[str, int | None]:
    from pydantic import ValidationError

    from models.finance import FinanceInputs

    if stored_finance is None:
        _refuse(
            "finance_inputs_missing",
            "set the finance inputs first: a price is read in the case's currency",
        )
    try:
        fin = FinanceInputs.model_validate(stored_finance)
    except ValidationError as exc:
        _refuse("finance_inputs_invalid", str(exc.errors()[0].get("msg"))[:300])
    # A line's money_year None is the case's base year (models/business_case.py, G5).
    return fin.currency, fin.currency_year or fin.financial_close.year


def _not_from_user(line: str, year, reason: str, message: str) -> None:
    where = f"{line} price" + (f" {year}" if year is not None else "")
    _refuse(
        "price_not_from_user",
        f"{where}: {message} A price comes only from the user: cite their own message "
        "(turn_id and quote, from get_business_case(detail='inputs').citable_turns), an "
        "upload's sheet and cell, or a PDF page and quote, with the unit right after the "
        "number; or ask the user for it.",
        line=str(line)[:80],
        year=year,
        reason=reason,
    )


def _citation(citations, line: str, year) -> dict | None:
    for c in citations or []:
        if isinstance(c, dict) and c.get("line") == line and c.get("year") == year:
            return c
    return None


class _Host:
    """The run intake's host, read once and only when a price needs a citation."""

    def __init__(self):
        self._host = None

    def get(self):
        if self._host is None:
            from services import chat_tools, run_intake
            from services.pypsa_service import PyPSAService

            ctx = PyPSAService.get_active_context()
            with chat_tools._acting() as (_db, user):
                owner = str(user.id)
            if not getattr(ctx, "project_uuid", None):
                raise run_intake.IntakeError("not_found", "Save the project first.")
            self._host = run_intake.host_for(ctx, owner, ctx.project_uuid)
        return self._host


def _cited(
    line_name: str,
    line: dict,
    year,
    value,
    citations,
    host: _Host,
    money: tuple[str, int | None],
) -> dict:
    """The verified source of one new or changed price, or `price_not_from_user`."""
    from services import run_intake

    c = _citation(citations, line_name, year)
    if c is None:
        _not_from_user(
            line_name, year, "no_citation", "it is new or changed and cites no source."
        )
    currency, currency_year = money
    money_year = line.get("money_year") or currency_year
    field = {
        "label": f"{line_name} price",
        "kind": "number",
        "sources": ["table", "pdf", "text"],
        "editable": True,
        "native_type": "float",
        "unit_storage": _price_unit(line, currency),
        "money": {"currency": currency, "money_year": money_year},
    }
    cite = {k: v for k, v in c.items() if k not in ("line", "year")}
    cite["param"] = "price"
    try:
        out = run_intake.read([cite], {"price": field}, host.get())
    except run_intake.IntakeError as exc:
        _not_from_user(line_name, year, exc.kind, str(exc))
    if out["gaps"]:
        gap = out["gaps"][0]
        _not_from_user(line_name, year, str(gap.get("kind")), str(gap.get("message")))
    read = out["rows"][0]
    if not math.isclose(float(read["value"]), float(value), rel_tol=1e-9, abs_tol=0.0):
        _not_from_user(
            line_name,
            year,
            "value_differs",
            f"the cited number reads {read['value']:g}, not {float(value):g}.",
        )
    source = {k: v for k, v in read["source"].items() if k != "raw"}
    return {"source": source, "price_source": run_intake.describe_source(read)}


def _price_source_kept(old_line: dict, path: tuple) -> str:
    assumed = (old_line.get("assumed") or {}).get(_PRICE)
    if path == (_PRICE,) and isinstance(assumed, dict):
        return "preset default (assumed)"
    return "unchanged"


def _line_rows(
    name: str,
    old: dict | None,
    new: dict | None,
    citations,
    host: _Host,
    money_reader,
    currency: str | None,
) -> list[dict]:
    old_l, new_l = _leaves(old), _leaves(new)
    line = new if new is not None else old
    change = "added" if old is None else "removed" if new is None else "changed"
    # A price is read in its line's unit and money year: 45 USD/t is not 45 USD/kg, so a
    # changed unit or money year changes every price of the line (spec review, Q1).
    basis_moved = change == "changed" and any(
        not _equal(old.get(k), new.get(k)) for k in ("unit", "money_year")
    )
    rows: list[dict] = []
    for path in sorted(set(old_l) | set(new_l), key=lambda p: tuple(map(str, p))):
        o, n = old_l.get(path), new_l.get(path)
        differs = not _equal(o, n) if (path in old_l and path in new_l) else True
        price = _is_price(path)
        differs = differs or (price and basis_moved)
        if not differs and not (price and change == "changed"):
            continue  # an unchanged value; a changed line shows its prices
        row = _row(
            name,
            path,
            o,
            n,
            change if differs else "unchanged",
            _unit(line, path, currency),
        )
        if price:
            if not differs:
                row["price_source"] = _price_source_kept(old, path)
            elif n is None:
                row["price_source"] = "removed"
            else:
                year = int(path[1]) if path[0] == _PRICE_BY_YEAR else None
                row.update(_cited(name, line, year, n, citations, host, money_reader()))
        rows.append(row)
    return rows


def _currency(stored_finance) -> str | None:
    """The case's currency for a unit label, or None (only a citation needs it read)."""
    try:
        return _money(stored_finance)[0]
    except HTTPException:
        return None


def rows(old: dict | None, new: dict | None, citations, stored_finance) -> list[dict]:
    """The card's rows: the diff of `old` (stored) against `new` (None: cleared)."""
    host = _Host()
    cache: list = []

    def money_reader():
        if not cache:
            cache.append(_money(stored_finance))
        return cache[0]

    currency = _currency(stored_finance)
    out: list[dict] = []
    old_rest, new_rest = _leaves(_rest(old)), _leaves(_rest(new))
    for path in sorted(set(old_rest) | set(new_rest), key=lambda p: tuple(map(str, p))):
        o, n = old_rest.get(path), new_rest.get(path)
        if path in old_rest and path in new_rest and _equal(o, n):
            continue
        change = "added" if o is None else "removed" if n is None else "changed"
        out.append(_row(None, path, o, n, change, ""))
    old_lines, new_lines = _lines(old), _lines(new)
    order = list(new_lines) + [k for k in old_lines if k not in new_lines]
    for name in order:
        o, n = old_lines.get(name), new_lines.get(name)
        if o is not None and n is not None and _equal(o, n):
            continue
        out.extend(_line_rows(name, o, n, citations, host, money_reader, currency))
    return out


def says(rows_: list[dict]) -> str:
    counts: dict[str, set] = {"added": set(), "changed": set(), "removed": set()}
    settings = 0
    for r in rows_:
        if r.get("line") is None:
            settings += 1
        elif r["change"] in counts:
            counts[r["change"]].add(r["line"])
        else:
            counts["changed"].add(r["line"])
    for name in counts["added"] | counts["removed"]:
        counts["changed"].discard(name)
    parts = [
        f"{len(v)} line{'' if len(v) == 1 else 's'} {k}" for k, v in counts.items() if v
    ]
    if settings:
        parts.append(f"{settings} setting{'' if settings == 1 else 's'} changed")
    return "business case: " + (", ".join(parts) or "no change")


def plan(args: dict) -> dict:
    """The checked call: `{new, old, tag, rows}`, or a refusal (HTTPException with an
    `error_kind`). Reads the stored inputs; writes nothing."""
    from routers import results
    from routers.simulation import _entity_tag
    from services.business_case.runner import config_digest, config_or_422

    new_raw, tag = request(
        args.get("if_match"), args.get("business_case"), bool(args.get("clear"))
    )
    citations = args.get("citations")
    if citations is not None and not isinstance(citations, list):
        _refuse(
            "price_not_from_user", "`citations` is a list", reason="invalid_citations"
        )
    if results._solver_in_flight():
        _refuse(
            "solver_in_flight",
            "a solve is running on this project; change the "
            "business-case inputs after it finishes",
            409,
        )
    new = None
    if new_raw is not None:
        try:
            new = config_or_422(new_raw).model_dump(mode="json")
        except HTTPException as exc:
            d = exc.detail if isinstance(exc.detail, dict) else {}
            _refuse(
                "business_case_inputs_invalid",
                str(d.get("message") or exc.detail)[:300],
                errors=list(d.get("errors") or [])[:20],
            )
        names = [ln.get("name") for ln in new["inputs"]["lines"]]
        twice = sorted({n for n in names if names.count(n) > 1})
        if twice:  # the card keys lines by name: a duplicate would hide a line
            _refuse(
                "business_case_inputs_invalid",
                f"each line has its own name; named twice: {', '.join(twice)[:200]}",
            )
    cfg = results._state["solver_config"]
    old = getattr(cfg, "business_case", None)
    if _entity_tag(tag) != config_digest(old):
        _refuse(
            "business_case_changed",
            "the business-case inputs changed since they were "
            "read; read them again (get_business_case detail='inputs') and re-apply",
            412,
        )
    if new is None and old is None:
        _refuse(
            "empty_edit",
            "no business-case inputs are stored; there is nothing to clear",
        )
    out = rows(old, new, citations, getattr(cfg, "finance", None))
    if not out:
        _refuse("empty_edit", "the business-case inputs already have these values")
    if len(out) > CARD_ROW_CAP or len(json.dumps(out, default=str)) > CARD_CHARS:
        _refuse(
            "business_case_card_too_large",
            f"this edit changes {len(out)} values, more than one card shows "
            f"(at most {CARD_ROW_CAP}): split it into smaller edits",
            rows=len(out),
        )
    return {"new": new, "old": old, "tag": tag, "rows": out}


def read_cell(row: dict, stored: dict | None):
    """The stored value at a card row's path, as the card shows it (receipt read-back)."""
    from harness.edit_card import _setting

    path = list(row.get("path") or [])
    doc: Any = stored
    if path[:2] == ["inputs", "lines"] and len(path) >= 3:
        doc = _lines(stored).get(path[2])
        path = path[3:]
    for part in path:
        if not isinstance(doc, dict):
            return None
        doc = doc.get(part)
    return None if _blank(doc) else _setting(doc)
