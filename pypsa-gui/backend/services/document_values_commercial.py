"""
Values from an uploaded PDF onto the import tariff and PPA contracts (section 7), each
beside its page and quote: data-to-model slice 2, PR-B (owner AI-567b; plan
`.scratch/data-to-model/slice-2-plan.md`, gatekeeper GO WITH CHANGES).

As `services/document_values.py` for assets: the model, or the user in the Expert dialog,
cites `{target, entity, field, page, quote, unit, value_text?}`; the server finds the quote
on its page and reads the number from it (`document_values.locate` and `_value`, the same
rules). Values only, onto entities that exist: the structure (items, periods, kinds,
parties) is built in the Import tariff and Contracts blocks first.

- **Targets.** `tariff`: the import tariff's item `entity` (its id), `field` one of
  `periods[<name>].rate`, `periods[<name>].tier_rates[<j>]`, `tiers[<j>].rate`. `ppa`: the
  PPA contract `entity` (its id), `field` one of `PPA_FIELDS`. A tier threshold is not
  read here (its volume basis is the item's); it is set in the TariffBuilder.
- **Units from the item, one declared scale.** A tariff value takes its item's own unit
  (`TARIFF_UNITS`); a quote per MWh into a `per_kwh` item is the one extra scale, ×1/1000,
  and the row shows the raw value and unit beside the converted one. Anything else
  refuses (`unit_mismatch`): no /kW/yr into `per_kw_month`.
- **Money stated, never assumed.** Every tariff rate and PPA price needs a currency (the
  stated one, or the one the quote or the unit names); a quote naming another, or a stated
  one other than the project's finance currency, is `currency_mismatch`; none at all is
  `money_basis_missing`. Tariffs carry no currency: it is recorded in provenance. A PPA
  price also needs its money year: the contract's `base_year` is that year (it is what its
  indexation runs from), so an unset one is written with it, a different one refuses
  (`money_year_mismatch`), and one other than the investment case's base year needs the
  `money_year` confirmation. Nothing is adjusted for inflation.
- **Library-pinned items are refused in place (`library_pinned`)**: a tariff under
  `import_tariff_ref`, a contract with a `library_ref`. The engine refuses an inline
  tariff that no longer hashes to its ref (`binding`, `import_tariff_ref_conflict`).
- **Compare-and-set.** The plan reads the stored commercial config and binds its sha256
  (`basis`) into every card row. `apply` re-reads it under the network and solver-state
  locks, refuses a different one (`edit_preview_stale`), validates the result with
  `models.commercial`, runs the commercial preflight (a new error finding refuses,
  `commercial_preflight_failed`), writes through the solver-config route and reads back.
- **Undo.** The network undo stack does not hold the solver config, so `undo_last` does
  not revert this; the receipt says so and lists the values to set back.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException

from services import document_values as D
from services import equipment_table as T

_refuse = T._refuse

# Refusals this module adds as tool_error frames (literals for the manifest test). The
# per-citation kinds (`library_pinned`, `money_year_mismatch`, ...) are `unmapped` rows.
_ERROR_KINDS = ({"error_kind": "commercial_preflight_failed"}, {"error_kind": "no_commercial_config"})

TARGETS = ("tariff", "ppa")
BLOCKS = {"tariff": "Import tariff", "ppa": "Contracts"}

# A unit as written, normalised (`_unit`), → the scale the number is divided by.
TARIFF_UNITS: dict[str, dict[str, int]] = {
    "per_kwh": {"kwh": 1, "mwh": 1000},
    "per_kw_month": {"kw/month": 1, "kw/mo": 1, "kw-month": 1},
    "per_kw_year": {"kw/year": 1, "kw/yr": 1, "kw/a": 1, "kw-year": 1},
    "per_month": {"month": 1, "mo": 1},
    "per_kva_year": {"kva/year": 1, "kva/yr": 1, "kva/a": 1, "kva-year": 1},
    "per_day": {"day": 1, "d": 1},
}
UNIT_LABELS = {"per_kwh": "/kWh", "per_kw_month": "/kW/month", "per_kw_year": "/kW/year",
               "per_month": "/month", "per_kva_year": "/kVA/year", "per_day": "/day"}

_MWH = {"mwh": 1}
# field → (label, units, money, any sign, whole number)
PPA_FIELDS: dict[str, tuple[str, dict[str, int], bool, bool, bool]] = {
    "price": ("Price", _MWH, True, False, False),
    "floor": ("Floor", _MWH, True, True, False),
    "cap": ("Cap", _MWH, True, True, False),
    "premium_eur_per_mwh": ("Premium", _MWH, True, True, False),
    "sleeving_fee_eur_per_mwh": ("Sleeving fee", _MWH, True, False, False),
    "indexation_pct_per_year": ("Indexation", {"%/year": 1, "%/yr": 1, "%/a": 1, "% p.a.": 1}, False, True, False),
    "volume_cap_mwh_per_year": ("Volume cap", {"mwh/year": 1, "mwh/yr": 1, "mwh/a": 1}, False, False, False),
    "baseload_mw": ("Baseload", {"mw": 1}, False, False, False),
    "tenor_years": ("Tenor", {"years": 1, "year": 1, "yr": 1, "yrs": 1}, False, False, True),
    "share": ("Share of output", {"share": 1}, False, False, False),
}
PPA_UNIT_LABELS = {"indexation_pct_per_year": "%/year", "volume_cap_mwh_per_year": "MWh/year",
                   "baseload_mw": "MW", "tenor_years": "years", "share": "share"}

_PERIOD_RATE = re.compile(r"^periods\[(?P<period>[^\]]+)\]\.rate$")
_PERIOD_TIER = re.compile(r"^periods\[(?P<period>[^\]]+)\]\.tier_rates\[(?P<tier>\d+)\]$")
_TIER_RATE = re.compile(r"^tiers\[(?P<tier>\d+)\]\.rate$")
_TIER_THRESHOLD = re.compile(r"^tiers\[\d+\]\.threshold$")

_SIGNED = {"validation_schema": {"minimum": float("-inf")}}
_UNSIGNED: dict = {}


def basis(commercial: dict | None) -> str:
    """The sha256 of the stored commercial config the card was read from (the value flows,
    which their own route owns, left out)."""
    body = {k: v for k, v in (commercial or {}).items() if k != "value_flows"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _unit(text: str) -> tuple[str | None, str]:
    """`(currency, per)` of a unit as written: "EUR/MWh" → ("EUR", "mwh"), "€/kW/month" →
    ("EUR", "kw/month"), "/kWh" → (None, "kwh"), "%/yr" → (None, "%/yr")."""
    s = " ".join(str(text or "").split())
    currency = None
    named = D.quoted_currencies(s)
    for symbol, code in D._SYMBOLS.items():
        if s.startswith(symbol):
            currency, s = code, s[len(symbol):]
    m = re.match(r"^([A-Z]{3})(?=\s*/)", s)
    if currency is None and m and m.group(1) in named:
        currency, s = m.group(1), s[3:]
    s = s.strip().lstrip("/").strip().lower()
    s = re.sub(r"\s*/\s*", "/", s).replace(" per ", "/")
    return currency, s


# ── Targets in the stored config ───────────────────────────────────────────


def _gap(kind: str, message: str, c: dict, **extra) -> dict:
    return {"kind": kind, "message": message, "page": c.get("page"), "field": c.get("field"),
            "name": f"{c.get('target')}:{c.get('entity')}", "target": c.get("target"), "entity": c.get("entity"),
            "quote": str(c.get("quote") or "")[:D.MAX_QUOTE_CHARS], **extra}


def _item_words(item: dict) -> str:
    """The item's name in words: its own name, else its id with spaces and a capital."""
    text = str(item.get("name") or item["id"]).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _tariff_label(item: dict, path: str) -> str:
    """A tariff value's name in words, never the raw path."""
    name = _item_words(item)
    if m := _PERIOD_RATE.match(path):
        return f"{name}, {m.group('period')} period: rate"
    if m := _PERIOD_TIER.match(path):
        return f"{name}, {m.group('period')} period: tier {int(m.group('tier')) + 1} rate"
    if m := _TIER_RATE.match(path):
        return f"{name}: tier {int(m.group('tier')) + 1} rate"
    return f"{name}: {path}"


def _tariff_slot(commercial: dict, c: dict) -> tuple[dict | None, dict | None]:
    """`({item, path, getter, setter, unit, label, sign}, None)` for a tariff citation, else
    `(None, gap)`."""
    ref = commercial.get("import_tariff_ref")
    tariff = commercial.get("import_tariff")
    if isinstance(ref, dict):
        return None, _gap("library_pinned", f"This tariff comes from the Commercial library (v{ref.get('version')}); "
                          "it is not edited here. Update it there.", c,
                          library={"kind": ref.get("kind") or "tariff", "id": ref.get("id"),
                                   "version": ref.get("version")})
    if not isinstance(tariff, dict):
        return None, _gap("component_not_found", "The project has no import tariff. Add it in Import tariff first.", c)
    item = next((x for x in tariff.get("items") or [] if x.get("id") == c.get("entity")), None)
    if item is None:
        return None, _gap("component_not_found", f"The import tariff has no item {c.get('entity')!r}. "
                          "Add it in Import tariff first.", c)
    path = str(c.get("field") or "")
    if _TIER_THRESHOLD.match(path):
        return None, _gap("field_not_supported", "A tier threshold is not read from a document here; set it "
                          "in the Import tariff block.", c)
    label = _tariff_label(item, path)
    m = _PERIOD_RATE.match(path) or _PERIOD_TIER.match(path)
    if m:
        period = next((p for p in item.get("periods") or [] if p.get("name") == m.group("period")), None)
        if period is None:
            return None, _gap("component_not_found", f"Item {item['id']!r} has no period {m.group('period')!r}. "
                              "Add it in Import tariff first.", c)
        if "tier" in m.groupdict() and m.group("tier") is not None:
            j = int(m.group("tier"))
            rates = period.get("tier_rates")
            if not isinstance(rates, list) or j >= len(rates):
                return None, _gap("component_not_found", f"Period {period['name']!r} has no tier rate {j}. "
                                  "Add it in Import tariff first.", c)
            where = ("periods", period["name"], "tier_rates", j)
        else:
            where = ("periods", period["name"], "rate")
    else:
        m = _TIER_RATE.match(path)
        if not m:
            return None, _gap("unknown_field", f"{path!r} is not a tariff value: use periods[<name>].rate, "
                              "periods[<name>].tier_rates[<j>] or tiers[<j>].rate.", c)
        j = int(m.group("tier"))
        if j >= len(item.get("tiers") or []):
            return None, _gap("component_not_found", f"Item {item['id']!r} has no tier {j}. "
                              "Add it in Import tariff first.", c)
        where = ("tiers", j, "rate")
    return {"entity": item["id"], "path": path, "where": where, "units": TARIFF_UNITS[item["unit"]],
            "unit_label": UNIT_LABELS[item["unit"]], "label": label, "money": True, "signed": True,
            "whole": False}, None


def _ppa_slot(commercial: dict, c: dict) -> tuple[dict | None, dict | None]:
    contract = next((x for x in commercial.get("contracts") or [] if x.get("id") == c.get("entity")), None)
    if contract is None:
        return None, _gap("component_not_found", f"There is no contract {c.get('entity')!r}. "
                          "Add it in Contracts first.", c)
    if contract.get("type", "ppa") != "ppa":
        return None, _gap("field_not_supported", f"Contract {contract['id']!r} is a {contract.get('type')}; "
                          "values from a document go onto PPAs only.", c)
    ref = contract.get("library_ref")
    if isinstance(ref, dict):
        return None, _gap("library_pinned", f"This contract comes from the Commercial library (v{ref.get('version')}); "
                          "it is not edited here. Update it there.", c,
                          library={"kind": ref.get("kind") or "contract", "id": ref.get("id"),
                                   "version": ref.get("version")})
    field = str(c.get("field") or "")
    if field not in PPA_FIELDS:
        return None, _gap("unknown_field", f"{field!r} is not a PPA value read from a document: one of "
                          f"{sorted(PPA_FIELDS)}.", c)
    label, units, money, signed, whole = PPA_FIELDS[field]
    return {"entity": contract["id"], "path": field, "where": (field,), "units": units,
            "unit_label": "/MWh" if money else PPA_UNIT_LABELS[field], "label": f"{contract['id']}: {label.lower()}",
            "money": money, "signed": signed, "whole": whole, "base_year": contract.get("base_year")}, None


def _owner(commercial: dict, target: str, entity: str) -> dict:
    """The tariff item or contract dict a slot's `where` is read from (in `commercial`)."""
    if target == "tariff":
        return next(x for x in commercial["import_tariff"]["items"] if x["id"] == entity)
    return next(x for x in commercial["contracts"] if x["id"] == entity)


def _walk(owner: dict, where: tuple):
    """`(container, key)` of a slot's value in its owner."""
    node = owner
    for step in where[:-1]:
        if isinstance(node, list) and isinstance(step, str):
            node = next(p for p in node if p.get("name") == step)
        else:
            node = node[step]
    return node, where[-1]


def read(commercial: dict, target: str, entity: str, where: tuple):
    node, key = _walk(_owner(commercial, target, entity), where)
    return node.get(key) if isinstance(node, dict) else node[key]


def _write(commercial: dict, target: str, entity: str, where: tuple, value) -> None:
    node, key = _walk(_owner(commercial, target, entity), where)
    node[key] = value


def _validate(commercial: dict) -> str | None:
    """None when the config validates (`models.commercial`), else the first reason."""
    from pydantic import ValidationError

    from models.commercial import CommercialConfig

    try:
        CommercialConfig.model_validate({k: v for k, v in commercial.items() if k != "value_flows"})
    except (ValidationError, ValueError) as exc:
        errors = exc.errors() if isinstance(exc, ValidationError) else []
        return str(errors[0].get("msg")) if errors else str(exc)
    return None


# ── Plan ───────────────────────────────────────────────────────────────────


def plan(commercial: dict | None, doc: dict, citations: list, *, decimal_mark: str | None = None,
         currency: str | None = None, money_year: int | None = None, confirm=(), base_year: int | None = None,
         project_currency: str | None = None) -> dict:
    """What an apply would write: `{source, basis, cells, after, unmapped, needs, missing,
    notes}`. `after` is the commercial config with every cell written. Writes nothing."""
    if decimal_mark is not None and decimal_mark not in D.DECIMAL_MARKS:
        _refuse("invalid_edit_value", "decimal_mark is '.' or ','.")
    if not isinstance(citations, list) or not citations:
        _refuse("empty_mapping", "Cite at least one value: {target, entity, field, page, quote, unit}.")
    if len(citations) > D.MAX_CITATIONS:
        _refuse("mapping_too_large", f"{len(citations)} citations; one card takes at most {D.MAX_CITATIONS}.",
                limits={"citations": D.MAX_CITATIONS})
    if not isinstance(commercial, dict) or not commercial.get("poc_link"):
        _refuse("no_commercial_config", "The project has no commercial config yet: set the site connection and "
                "add the tariff or contract in section 7 first.", 409)
    stored = copy.deepcopy(commercial)
    after = copy.deepcopy(commercial)
    src = doc["source"]
    pages = [D.normal(p) for p in doc["pages"]]
    stated_currency = (currency or "").strip().upper() or None
    cells: list[dict] = []
    unmapped: list[dict] = []
    seen: dict[tuple, int] = {}
    years: dict[int, str] = {}
    for i, c in enumerate(citations):
        c = dict(c or {})
        target = c.get("target")
        if target not in TARGETS:
            unmapped.append(_gap("unknown_field", f"target is one of {list(TARGETS)}.", c))
            continue
        found, gap = D.locate(c, pages, src)
        if gap:
            unmapped.append({**gap, **{k: v for k, v in _gap("", "", c).items() if k in ("name", "target", "entity")}})
            continue
        raw_quote, quote, page, at = found
        slot, gap = (_tariff_slot if target == "tariff" else _ppa_slot)(stored, c)
        if gap:
            unmapped.append(gap)
            continue
        unit_currency, per = _unit(c.get("unit") or "")
        if not per:
            unmapped.append(_gap("unit_missing", f"Name the unit: {slot['unit_label']} for this value.", c,
                                 accepted_units=[slot["unit_label"]]))
            continue
        if per not in slot["units"]:
            unmapped.append(_gap("unit_mismatch", f"{c.get('unit')!r} is not {slot['unit_label']}"
                                 + (" or /MWh" if "mwh" in slot["units"] and slot["unit_label"] != "/MWh" else "")
                                 + "; nothing else is converted.", c, accepted_units=[slot["unit_label"]]))
            continue
        scale = slot["units"][per]
        money = None
        if slot["money"]:
            named = D.quoted_currencies(quote)
            stated = stated_currency or unit_currency or (next(iter(named)) if len(named) == 1 and "$" not in named
                                                          else None)
            if stated is None:
                unmapped.append(_gap("money_basis_missing", "State the currency this value is in; nothing is "
                                     "assumed.", c))
                continue
            clash = D._currency_conflict(stated, named | ({unit_currency} if unit_currency else set()))
            if clash:
                unmapped.append(_gap("currency_mismatch", f"The quote names {clash}; the value is stated as "
                                     f"{stated}. Nothing is converted.", c))
                continue
            if project_currency and project_currency != stated:
                unmapped.append(_gap("currency_mismatch", f"The project's finance inputs are in {project_currency}; "
                                     f"this value is {stated}. Nothing is converted.", c))
                continue
            money = {"currency": stated, "money_year": None}
            if target == "ppa":
                if money_year is None:
                    unmapped.append(_gap("money_basis_missing", "State the money year of this price; nothing is "
                                         "assumed.", c))
                    continue
                if slot["base_year"] is not None and int(slot["base_year"]) != int(money_year):
                    unmapped.append(_gap("money_year_mismatch", f"Contract {slot['entity']!r} states its prices in "
                                         f"{slot['base_year']} money; this one is {money_year}. Nothing is adjusted.",
                                         c))
                    continue
                money["money_year"] = int(money_year)
        number, gap = D._value(c, quote, pages[page - 1], at, decimal_mark,
                               _SIGNED if slot["signed"] else _UNSIGNED)
        if gap:
            unmapped.append({**gap, **{k: v for k, v in _gap("", "", c).items() if k in ("name", "target", "entity")}})
            continue
        exact = number["value"] / Decimal(scale)
        if slot["whole"]:
            if exact != exact.to_integral_value():
                unmapped.append(_gap("invalid_edit_value", f"{slot['label']} is a whole number of years; the "
                                     f"quote reads {number['text']}.", c))
                continue
            value = int(exact)
        else:
            value = T._as_value(exact)
        key = (target, slot["entity"], slot["path"])
        if key in seen:
            unmapped.append(_gap("duplicate_field", f"Citations {seen[key] + 1} and {i + 1} both set "
                                 f"{slot['label']}; neither is applied.", c))
            cells[:] = [x for x in cells if (x["target"], x["entity"], x["path"]) != key]
            continue
        seen[key] = i
        trial = copy.deepcopy(stored)
        _write(trial, target, slot["entity"], slot["where"], value)
        reason = _validate(trial)
        if reason:
            unmapped.append(_gap("invalid_edit_value", f"{slot['label']}: {reason}", c))
            continue
        notes = []
        if per not in D.normal(quote).lower().replace(" ", ""):
            notes.append(f"The quote does not state {slot['unit_label'].lstrip('/')}; it was named for it.")
        if scale != 1:
            notes.append(f"Read as {number['text']} /MWh and written as {value} {slot['unit_label']} (÷{scale}).")
        if money and not project_currency:
            notes.append(f"The project's finance inputs set no currency; recorded as {money['currency']}, as stated.")
        if money and money["money_year"] is not None:
            notes.append("Copied as stated, not adjusted for inflation.")
        lineage = {"upload_id": src["upload_id"], "filename": src["filename"], "sha256": src["sha256"],
                   "page": page, "quote": raw_quote, "raw": number["text"], "raw_unit": str(c.get("unit")),
                   "scale": str(scale), "decimal_mark": decimal_mark}
        cells.append({"target": target, "entity": slot["entity"], "path": slot["path"], "where": slot["where"],
                      "label": slot["label"], "unit": slot["unit_label"], "old": read(stored, target, slot["entity"],
                                                                                   slot["where"]),
                      "value": value, "currency": money and money["currency"],
                      "money_year": money and money["money_year"], "source": lineage, "notes": notes})
    for cell in cells:
        if cell["money_year"] is not None:
            years.setdefault(cell["money_year"], cell["currency"])
    # The PPA prices' money year: an unset `base_year` is written with it, as its own row,
    # beside a price the card changes (one `money_year` per call: every price cited for a
    # contract is in the same year).
    extra = []
    for cell in cells:
        if cell["target"] == "ppa" and cell["money_year"] is not None and not _same(cell["old"], cell["value"]):
            owner = _owner(stored, "ppa", cell["entity"])
            if owner.get("base_year") is None and not any(x["entity"] == cell["entity"] for x in extra):
                extra.append({"target": "ppa", "entity": cell["entity"], "path": "base_year", "where": ("base_year",),
                              "label": f"{cell['entity']} Price money year", "unit": "year", "old": None,
                              "value": cell["money_year"], "currency": None, "money_year": None,
                              "source": None, "notes": ["The year the contract's prices are stated in; its "
                                                        "indexation runs from it."]})
    cells += extra
    for cell in cells:
        _write(after, cell["target"], cell["entity"], cell["where"], cell["value"])
    if cells:
        reason = _validate(after)
        if reason:
            _refuse("invalid_edit_value", f"Together, these values do not make a valid config: {reason}. "
                    "Nothing is written.")
    needs, notes = T.money_needs(years, base_year, said="the document states")
    return {"source": src, "basis": basis(stored), "cells": cells, "after": after, "unmapped": unmapped,
            "needs": needs, "missing": [x["confirmation"] for x in needs if x["confirmation"] not in set(confirm or ())],
            "notes": notes}


def changed(p: dict) -> list[dict]:
    return [c for c in p["cells"] if not _same(c["old"], c["value"])]


def _same(a, b) -> bool:
    from harness import edit_card

    return edit_card._same(a, b)


def stored_commercial() -> dict | None:
    from dataclasses import asdict

    from services.pypsa_service import PyPSAService

    config = (PyPSAService.get_active_context().solver_state or {}).get("solver_config")
    if config is None:
        return None
    commercial = (asdict(config) if not isinstance(config, dict) else config).get("commercial")
    return copy.deepcopy(commercial) if isinstance(commercial, dict) else commercial


def plan_active(args: dict) -> dict:
    """`plan` for the active project, from the tools' arguments."""
    from services import chat_tools
    from services.library import reference_apply as R
    from services.profile_gen.apply import _project_money
    from services.pypsa_service import PyPSAService

    doc = D.read_pages(chat_tools._require_active_project(), args.get("upload_id"))
    want = args.get("source_sha256")
    if want is not None and want != doc["source"]["sha256"]:
        _refuse("source_changed", "The document's bytes differ from the ones previewed. Preview it again.",
                previewed=want, now=doc["source"]["sha256"])
    ctx = PyPSAService.get_active_context()
    config = ctx.solver_state.get("solver_config")
    stored = stored_commercial()
    out = plan(stored, doc, args.get("citations") or [], decimal_mark=args.get("decimal_mark"),
               currency=args.get("currency"), money_year=args.get("money_year"), confirm=args.get("confirm") or (),
               base_year=R.case_base_year(ctx.network, config), project_currency=_project_money(ctx).get("currency"))
    # The preflight's errors before this change: shown on the card, never this change's refusal.
    out["preflight_errors"] = sorted(_errors(ctx.network, stored))
    return out


def summary(p: dict) -> dict:
    """What the preview tool and the dialog report of a plan."""
    from collections import Counter

    todo = changed(p)
    return {"source": p["source"], "changed_cells": len(todo), "unchanged_cells": len(p["cells"]) - len(todo),
            "cells": [{k: c[k] for k in ("target", "entity", "path", "label", "old", "value", "unit", "currency",
                                         "money_year", "notes")} for c in p["cells"]][:T.MAX_CARD_CELLS],
            "unmapped": p["unmapped"][:T.MAX_LISTED_GAPS],
            "unmapped_by_kind": dict(Counter(g["kind"] for g in p["unmapped"])),
            "needs": p["needs"], "missing": p["missing"], "notes": p["notes"],
            "limits": {"citations": D.MAX_CITATIONS, "card_cells": T.MAX_CARD_CELLS,
                       "quote_chars": D.MAX_QUOTE_CHARS, "listed_gaps": T.MAX_LISTED_GAPS}}


# ── Card ───────────────────────────────────────────────────────────────────


def _money_unit(c: dict) -> str:
    """The unit as shown: a money value carries its stated currency ("EUR/kWh")."""
    unit = c["unit"]
    return f"{c['currency']}{unit}" if c["currency"] and unit.startswith("/") else unit


def card_rows(p: dict) -> list[dict]:
    """The edit card's rows: one typed row per changed value (with its `source` and the
    `basis` it was read from), then the notes, confirmations and left-out citations."""
    from harness import edit_card

    T.require_confirmed(p)
    todo = changed(p)
    if not todo:
        _refuse("nothing_to_change", "Every cited value already equals the stored one; nothing to apply.")
    if len(todo) > T.MAX_CARD_CELLS:
        _refuse("mapping_too_large", f"{len(todo)} values change; one card takes at most {T.MAX_CARD_CELLS}.",
                limits={"card_cells": T.MAX_CARD_CELLS})
    rows, notes = [], []
    for c in todo:
        name = f"{BLOCKS[c['target']]} › {c['entity']}"
        row = {"component_class": "Commercial", "name": name, "field": c["path"], "label": c["label"],
               "old": edit_card._scalar(c["old"]), "new": c["value"], "unit": _money_unit(c), "role": "typed",
               "says_name": f"{BLOCKS[c['target']]} › {c['label']}", "target_kind": c["target"], "entity": c["entity"], "basis": p["basis"]}
        if c["source"] is not None:
            row["source"] = c["source"]
        if c["money_year"] is not None or c["currency"]:
            row["money"] = {"currency": c["currency"], "money_year": c["money_year"]}
        rows.append(row)
        notes += [{**edit_card._note("Commercial", name, c["path"], text)} for text in c["notes"]]
    rows += notes
    for need in p["needs"]:
        rows.append({**edit_card._note("Commercial", "", need["confirmation"], need["text"]), "confirmed": True})
    for gap in p["unmapped"][:T.MAX_LISTED_GAPS]:
        rows.append({**edit_card._note("Commercial", gap.get("name") or "", gap.get("field") or "", gap["message"]),
                     "gap": gap["kind"], **({"library": gap["library"]} if "library" in gap else {})})
    if len(p["unmapped"]) > T.MAX_LISTED_GAPS:
        rows.append(edit_card._note("Commercial", "", "", f"{len(p['unmapped']) - T.MAX_LISTED_GAPS} more citations "
                                    "are left out."))
    rows += [edit_card._note("Commercial", "", "money_year", text) for text in p["notes"]]
    for code, _klass, _name, message in p.get("preflight_errors") or ():
        rows.append(edit_card._note("Commercial", "", "preflight", f"Already failing before this change ({code}): "
                                    f"{message[:200]}"))
    rows.append(edit_card._note("Commercial", "", "undo", "Undo does not revert this change (the commercial "
                                "settings are not on the network's undo history); set the old values back to revert it."))
    return rows


def readback(args: dict, rows: list[dict]) -> list:
    """Each typed row's stored value now; a note reads back as its own `new`."""
    commercial = stored_commercial() or {}
    out = []
    for row in rows:
        if row.get("role") != "typed":
            out.append(row.get("new"))
            continue
        try:
            where = _where(row)
            out.append(read(commercial, row["target_kind"], row["entity"], where))
        except (KeyError, StopIteration, IndexError, TypeError):
            out.append(None)
    return out


def _where(row: dict) -> tuple:
    path = row["field"]
    if row["target_kind"] == "ppa":
        return (path,)
    m = _PERIOD_TIER.match(path)
    if m:
        return ("periods", m.group("period"), "tier_rates", int(m.group("tier")))
    m = _PERIOD_RATE.match(path)
    if m:
        return ("periods", m.group("period"), "rate")
    m = _TIER_RATE.match(path)
    return ("tiers", int(m.group("tier")), "rate")


# ── Apply ──────────────────────────────────────────────────────────────────


def _errors(n, commercial: dict) -> set[tuple[str, str]]:
    from services.commercial.preflight import commercial_findings

    return {(code, klass, name, message) for sev, code, klass, name, message in commercial_findings(n, commercial)
            if sev == "error"}


def _new_errors(n, before: dict, after: dict) -> list[tuple]:
    """The error findings `after` has and `before` has not, matched by code and component:
    an old finding whose message quotes a value this change edits is not new."""
    old = {(code, klass, name) for code, klass, name, _m in _errors(n, before)}
    return sorted(e for e in _errors(n, after) if e[:3] not in old)


def provenance(p: dict, *, approved_by: str | None) -> dict:
    """`commercial.provenance` entries for the written cells: `<target>:<entity>/<path>` →
    `{source, value, unit, currency, currency_year, approved_by, approved_at}`."""
    when = datetime.now(UTC).isoformat(timespec="seconds")
    out = {}
    for c in changed(p):
        if c["source"] is None:
            continue
        out[f"{c['target']}:{c['entity']}/{c['path']}"] = {
            "source": T.source_id(c["source"]), "value": c["value"], "unit": c["unit"], "currency": c["currency"],
            "currency_year": c["money_year"], "approved_by": approved_by, "approved_at": when}
    return out


def _slot_of(commercial: dict, key: str):
    """The value stored at a provenance key, or raises (KeyError, StopIteration, ...) when its
    item, period, tier or contract no longer exists."""
    target, _, rest = key.partition(":")
    entity, _, path = rest.partition("/")
    return read(commercial, target, entity, _where({"target_kind": target, "field": path}))


def _live(commercial: dict, entries: dict) -> dict:
    """The entries whose tariff item or contract (and path) still exist."""
    out = {}
    for key, entry in (entries or {}).items():
        try:
            _slot_of(commercial, key)
        except (KeyError, StopIteration, IndexError, TypeError, AttributeError):
            continue
        out[key] = entry
    return out


def document_source(commercial: dict | None, key: str) -> dict | None:
    """The provenance entry for `key` while the stored value still equals the one the
    document gave; None after a later edit, or for an entry whose item is gone."""
    entry = ((commercial or {}).get("provenance") or {}).get(key)
    if not isinstance(entry, dict):
        return None
    try:
        now = _slot_of(commercial, key)
    except (KeyError, StopIteration, IndexError, TypeError, AttributeError):
        return None
    return entry if _same(now, entry.get("value")) else None


def apply(p: dict, card: dict, *, approved_by: str | None) -> dict:
    """Write exactly the card's values, compare-and-set on the stored config's `basis`.

    Under the network lock (the preflight reads the network) and the solver-state lock
    (an RLock the solver-config route takes again for its merge), so nothing lands
    between the check and the write: the stored config must still hash to the card's
    `basis` and the plan must change exactly the card's typed rows to their `new`, else
    `edit_preview_stale`. A preflight error the change adds refuses
    (`commercial_preflight_failed`). Nothing is written on any refusal."""
    from models.schemas import SolverConfigSchema
    from routers.simulation import update_solver_config
    from services import chat_tools
    from services.pypsa_service import PyPSAService

    shown = {(r["target_kind"], r["entity"], r["field"]): r for r in card["rows"] if r.get("role") == "typed"}
    with PyPSAService.get_lock(), PyPSAService.get_solver_state_lock():
        stored = stored_commercial()
        todo = {(c["target"], c["entity"], c["path"]): c for c in changed(p)}
        if basis(stored) != p["basis"] or any(r.get("basis") != p["basis"] for r in shown.values()) \
                or set(todo) != set(shown) or any(not _same(todo[k]["value"], shown[k]["new"]) for k in todo):
            _refuse("edit_preview_stale", "The commercial config changed since the card was shown; nothing was "
                    "applied. Propose the edit again.", 409)
        after = copy.deepcopy(p["after"])
        after["provenance"] = _live(after, {**(stored.get("provenance") or {}),
                                            **provenance(p, approved_by=approved_by)})
        n = PyPSAService.get_network()
        added = _new_errors(n, stored, after)
        if added:
            code, _klass, _name, message = added[0]
            _refuse("commercial_preflight_failed", f"With these values the commercial preflight fails ({code}): "
                    f"{message[:300]}. Nothing is written.", findings=[e[0] for e in added][:10])
        after.pop("value_flows", None)  # owned by its own route, which keeps what is stored
        try:
            # The solver-config route's own save: it binds the config (its validation and
            # Library resolution) and merges under the solver-state lock, which is held here
            # already (an RLock), so nothing lands between this check and the readback.
            chat_tools._library_call(update_solver_config, SolverConfigSchema(commercial=after))
        except HTTPException as exc:
            d = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            if "error_kind" in d:
                raise
            raise HTTPException(exc.status_code, {"error_kind": "commercial_preflight_failed",
                                                  "code": str(d.get("code", ""))[:80],
                                                  "message": str(d.get("message", ""))[:500]}) from exc
        written = stored_commercial() or {}
        readback = {k: read(written, c["target"], c["entity"], c["where"]) for k, c in todo.items()}
    return {"source": p["source"], "written": [{k: c[k] for k in ("target", "entity", "path", "value")}
                                               for c in todo.values()],
            "read_back": all(_same(readback[k], c["value"]) for k, c in todo.items()),
            "unmapped": p["unmapped"], "notes": p["notes"],
            "undo": "undo_last does not revert this; set the card's old values back in section 7 to revert it."}


def preview(args: dict) -> dict:
    """The dialog's preview in commercial mode: `{document, targets, candidates?, plan?,
    card?}`. `targets` lists what can take a value (the import tariff's items and periods,
    the PPA contracts, each with its unit and whether it is library-pinned); with `find`,
    `candidates` (`wanted`); with `citations`,
    `plan` (`summary`) and `card`, None while a confirmation is missing or nothing
    changes. Writes nothing."""
    from harness import edit_card
    from services import chat_tools
    from services.pypsa_service import PyPSAService

    doc = D.read_pages(chat_tools._require_active_project(), args.get("upload_id"))
    commercial = stored_commercial()
    out: dict = {"document": {"source": doc["source"]}, "targets": targets(commercial)}
    if args.get("find"):
        out["candidates"] = D.find(doc["pages"], wanted(commercial), args.get("decimal_mark"))
    if not args.get("citations"):
        return out
    # `find` is the picker's, not the edit's: the card is the one the apply builds.
    args = {**{k: v for k, v in args.items() if k != "find"}, "source_sha256": doc["source"]["sha256"]}
    with PyPSAService.get_lock():
        p = plan_active(args)
        out["plan"] = summary(p)
        out["card"] = None if p["missing"] or not out["plan"]["changed_cells"] else edit_card.preview(
            "apply_commercial_document_values", args)
    return out


def targets(commercial: dict | None) -> dict:
    """What a citation can name: `{tariff: {pinned, items: [{id, unit, fields}]} | None,
    ppa: [{id, pinned, fields}]}`."""
    commercial = commercial or {}
    tariff = commercial.get("import_tariff")
    out: dict = {"tariff": None, "ppa": []}
    if isinstance(tariff, dict):
        items = []
        for item in tariff.get("items") or []:
            fields = []
            for period in item.get("periods") or []:
                fields.append(f"periods[{period['name']}].rate")
                fields += [f"periods[{period['name']}].tier_rates[{j}]"
                           for j in range(len(period.get("tier_rates") or []))]
            fields += [f"tiers[{j}].rate" for j in range(len(item.get("tiers") or []))]
            items.append({"id": item["id"], "unit": UNIT_LABELS[item["unit"]], "fields": fields})
        out["tariff"] = {"pinned": isinstance(commercial.get("import_tariff_ref"), dict), "items": items}
    for c in commercial.get("contracts") or []:
        if c.get("type", "ppa") == "ppa":
            out["ppa"].append({"id": c["id"], "pinned": isinstance(c.get("library_ref"), dict),
                               "fields": [{"field": f, "label": v[0],
                                           "unit": "/MWh" if v[2] else PPA_UNIT_LABELS[f]}
                                          for f, v in PPA_FIELDS.items()]})
    return out


_CASE = (("kva", "kVA"), ("kwh", "kWh"), ("mwh", "MWh"), ("kw", "kW"), ("mw", "MW"))


def _cased(unit: str) -> str:
    for low, cased in _CASE:
        unit = re.sub(rf"(?<![a-z]){low}(?![a-z])", cased, unit)
    return unit


def wanted(commercial: dict | None) -> dict[str, dict]:
    """What the quote finder (`document_values.find`) looks for: `{"tariff:<item>": ...}`
    for each import-tariff item (every rate of an item takes the same units) and
    `{"ppa:<field>": ...}` for each PPA field (the same for every contract), each `{label,
    units}` in the units `plan` accepts."""
    out: dict[str, dict] = {}
    tariff = (commercial or {}).get("import_tariff")
    for item in (tariff.get("items") or []) if isinstance(tariff, dict) else []:
        out[f"tariff:{item['id']}"] = {"label": f"{item['id']} {item.get('kind', '')}",
                                       "units": [_cased(f"/{u}") for u in TARIFF_UNITS.get(item.get("unit"), {})]}
    if any(c.get("type", "ppa") == "ppa" for c in (commercial or {}).get("contracts") or []):
        for f, (label, units, money, _, _) in PPA_FIELDS.items():
            out[f"ppa:{f}"] = {"label": label, "units": [_cased(f"/{u}" if money else u) for u in units]}
    return out
