"""`apply_business_case_preset`' plan: the template's preset (or the generic offered
rows) stored from chat, every default filled by the server (business case 14; spec §7;
GK B (56) plan GO, B1-B9).

The preset is read here, never sent by the model: `presets.preset` (a template's variant,
cost defaults from the pinned pack, marked `assumed`) or `presets.offered_case` (the
offered rows the user picked; a template with no preset file uses the generic rows). Only
`variant` and `rows` come from the model, and each must be a member of the server's own
list (B2). So a default can only ever be the pack's, with its source and year, and a
revenue price is always empty: the assistant asks the user for it and stores it with
`set_business_case_lines`, which needs the user's own words for every price.

One function, `plan`, serves the edit card and the tool's handler, so the card and the
write refuse the same calls (as `chat_edit.plan`), and the write goes through the same
PUT as `set_business_case_lines` (B3). The card (B1) shows the variant or the picked rows,
the pack that filled the defaults, one row per line (added, replaced or removed) with
every value, unit and source, the kept settings and the break-even line; its digest binds
them all, so a pack, template or stored-inputs change since the card refuses.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from services.business_case.chat_edit import CARD_CHARS, CARD_ROW_CAP, NAME, _refuse

# Tool-side refusals, as literals for the error-kind manifest guard.
ERROR_KINDS = (
    {"error_kind": "preset_variant_required"},
    {"error_kind": "preset_rows_required"},
    {"error_kind": "preset_invalid"},
    {"error_kind": "template_not_known"},
)

EMPTY = "empty: ask the user"
#: The stored settings beside the lines; the card shows each one the preset keeps.
_KEPT = ("kw_per_gpu", "lhv_kwh_per_kg", "capacity", "capacity_unit")


def _currency(cfg) -> str:
    from pydantic import ValidationError

    from models.finance import FinanceInputs

    raw_fin = getattr(cfg, "finance", None)
    if raw_fin is None:
        _refuse(
            "finance_inputs_missing",
            "set the finance inputs first: a preset's cost defaults are filled in the "
            "case's currency",
        )
    try:
        return FinanceInputs.model_validate(raw_fin).currency
    except ValidationError as exc:
        _refuse("finance_inputs_invalid", str(exc.errors()[0]["msg"])[:300])


def _pick(template_id: str, variant, rows) -> tuple[str | None, list[str]]:
    """(variant, row labels), each a member of the server's own list, or the refusal
    that lists the choices (B2: never a fuzzy match)."""
    from services.business_case import presets as P

    variants = P.variants(template_id) if P.has_preset(template_id) else {}
    if variants:
        if rows is not None or not isinstance(variant, str) or variant not in variants:
            _refuse(
                "preset_variant_required",
                "this template has business-case presets: ask the user which one and pass "
                "its id as `variant` (and no `rows`)",
                variants=variants,
            )
        return variant, []
    offered = P.offered_labels(template_id)
    ok = (
        variant is None
        and isinstance(rows, list)
        and rows
        and all(isinstance(x, str) and x in offered for x in rows)
        and len(set(rows)) == len(rows)
    )
    if not ok:
        _refuse(
            "preset_rows_required",
            "this template has no preset: ask the user which of the offered rows the "
            "plant has and pass their labels, each once, as `rows` (and no `variant`)",
            offered=offered,
        )
    return None, list(rows)


def _inputs(template_id: str, variant, rows, currency: str):
    """The preset's `BusinessCaseInputs` and the stamp of the pack that filled it."""
    from models.business_case import DefaultUnitMismatch
    from services.business_case import presets as P
    from services.business_case.runner import refusal_detail

    try:
        variant, labels = _pick(template_id, variant, rows)
        stamp = P.pinned_pack(template_id)
        if variant is not None:
            return P.preset(template_id, variant, currency=currency), stamp
        return P.offered_case(template_id, labels, currency=currency), stamp
    except DefaultUnitMismatch as exc:
        raise HTTPException(
            422, {**refusal_detail(exc), "error_kind": "default_unit_mismatch"}
        ) from exc
    except (P.PresetInvalid, LookupError) as exc:
        _refuse(
            "preset_invalid", f"the business-case preset cannot be read: {exc}"[:300]
        )


def _source(line: dict) -> str:
    """`typed`, `linked` or `share_of`: the quantity source's kind (`LineItem.source`)."""
    return str(line.get("quantity_source") or "typed").split(":", 1)[0]


def _break_even_line(old: str | None, lines: list[dict]) -> str | None:
    revenue = [
        ln["name"]
        for ln in lines
        if ln.get("kind") == "revenue" and _source(ln) != "share_of"
    ]
    return old if old in revenue else (revenue[0] if revenue else None)


def cells(line: dict) -> list[tuple[str, str, Any, str]]:
    """Every value a card shows for one line, as (path suffix, label, value, unit): what
    the line is, then each quantity, price and life with its unit, an empty one as
    `EMPTY`, and each default's source with its year first (a card cuts long text, never
    the year). The receipt reads the same cells back from the stored line."""
    src = _source(line)
    what = f"{line.get('kind')}/{line.get('category')}, per {line.get('unit')}"
    if src != "typed":
        what += f", quantity {line.get('quantity_source')}"
    out: list[tuple[str, str, Any, str]] = [("", "line", what, "")]
    cols = [
        c
        for c in ("quantity", "price", "money_year", "life_years")
        if not (c == "quantity" and src == "linked")
        and not (c in ("price", "money_year") and src == "share_of")
        and not (c in ("money_year", "life_years") and line.get(c) is None)
    ]
    assumed = line.get("assumed") or {}
    for col in cols:
        v, by_year = line.get(col), line.get(f"{col}_by_year") or {}
        a = assumed.get(col) or {}
        if v is None and by_year:
            v = ", ".join(f"{y}: {by_year[y]:g}" for y in sorted(by_year))
        unit = a.get("unit") or {
            "quantity": line.get("unit"),
            "life_years": "years",
            "money_year": "",
        }.get(col, f"per {line.get('unit')}")
        out.append((col, col.replace("_", " "), EMPTY if v is None else v, unit or ""))
        if a:
            out.append(
                (
                    f"assumed.{col}",
                    f"{col.replace('_', ' ')} source",
                    f"{a.get('source_year')}, assumed: {a.get('source')}",
                    "",
                )
            )
    return out


def describe(line: dict | None) -> str | None:
    """One line in words (the removed line's row): every cell, joined."""
    if not line:
        return None
    return "; ".join(
        f"{label} {v} {u}".rstrip() if label != "line" else str(v)
        for _p, label, v, u in cells(line)
    )


def _hint(h) -> str | None:
    """A public reference range in words, never a value to store (BCA-SCOPE-1 Q1)."""
    if not isinstance(h, dict):
        return None
    return (
        f"public range {h.get('low'):g}-{h.get('high'):g} {h.get('unit')} "
        f"({h.get('source')}, {h.get('year')}); a range, not the user's price"
    )


def to_ask(case: dict) -> list[dict]:
    """The empty values the user must give (never invented): each line's price, its typed
    quantity or share, and `kw_per_gpu` when a GPU-hour line needs it, with the unit and
    the preset's hint."""
    out = []
    lines = (case.get("inputs") or {}).get("lines") or []
    for ln in lines:
        src = _source(ln)
        if (
            src != "share_of"
            and ln.get("price") is None
            and not ln.get("price_by_year")
        ):
            out.append(
                {
                    "line": ln["name"],
                    "column": "price",
                    "kind": ln.get("kind"),
                    "per": ln.get("unit"),
                    "hint": _hint(ln.get("hint")),
                }
            )
        if (
            src in ("typed", "share_of")
            and ln.get("quantity") is None
            and not ln.get("quantity_by_year")
        ):
            out.append(
                {
                    "line": ln["name"],
                    "column": "quantity",
                    "kind": ln.get("kind"),
                    "per": ln.get("unit"),
                    "hint": None,
                    **(
                        {"share_of": ln["quantity_source"].split(":", 1)[1]}
                        if src == "share_of"
                        else {}
                    ),
                }
            )
    gpu = any(ln.get("unit") == "GPU_h" and _source(ln) == "linked" for ln in lines)
    if gpu and case.get("kw_per_gpu") is None:
        out.append({"setting": "kw_per_gpu", "per": "kW per GPU", "hint": None})
    return out


def _row(path: list[str], label: str, old, new, change: str, line: str | None) -> dict:
    from harness.edit_card import _setting

    return {
        "component_class": "Settings",
        "name": NAME,
        "field": ".".join(path),
        "label": label,
        "old": _setting(old),
        "new": _setting(new),
        "unit": "",
        "role": "typed",
        "says_name": label,
        "path": path,
        "line": line,
        "change": change,
    }


def _lines(doc: dict | None) -> dict[str, dict]:
    return {
        ln["name"]: ln for ln in ((doc or {}).get("inputs") or {}).get("lines") or []
    }


def _rows(old: dict | None, new: dict, picked: str, stamp: str) -> list[dict]:
    rows = [
        _row(["preset"], "preset", None, picked, "set", None),
        _row(["defaults_pack"], "defaults pack", None, stamp, "set", None),
    ]
    o, n = _lines(old), _lines(new)
    for name, line in n.items():
        change = "replaced" if name in o else "added"
        for suffix, label, value, unit in cells(line):
            path = ["inputs", "lines", name] + ([suffix] if suffix else [])
            r = _row(
                path,
                name if not suffix else f"{name} · {label}",
                describe(o.get(name)) if not suffix else None,
                value,
                change,
                name,
            )
            r["unit"] = unit
            rows.append(r)
    for name in (x for x in o if x not in n):
        rows.append(
            _row(
                ["inputs", "lines", name],
                name,
                describe(o[name]),
                None,
                "removed",
                name,
            )
        )
    for key in _KEPT:
        if (old or {}).get(key) not in (None, {}):
            rows.append(
                _row([key], key.replace("_", " "), old[key], new.get(key), "kept", None)
            )
    b, a = (old or {}).get("break_even_line"), new.get("break_even_line")
    rows.append(
        _row(
            ["break_even_line"],
            "break-even line",
            b,
            a,
            "kept" if a == b else "changed",
            None,
        )
    )
    return rows


def plan(args: dict) -> dict:
    """The checked call: `{new, old, tag, rows, to_ask}`, or a refusal (HTTPException
    with an `error_kind`). Reads the stored inputs and the preset; writes nothing."""
    from routers import results
    from routers.simulation import _entity_tag
    from services.business_case import presets as P
    from services.business_case.chat_edit import request
    from services.business_case.runner import config_digest, config_or_422

    _, tag = request(args.get("if_match"), {}, False)
    if results._solver_in_flight():
        _refuse(
            "solver_in_flight",
            "a solve is running on this project; apply the preset after it finishes",
            409,
        )
    template_id = (results._active_template_meta() or {}).get("id")
    if not template_id:
        _refuse(
            "template_not_known",
            "this project was not made from a template; add the "
            "business-case lines with set_business_case_lines from the user's figures",
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
    variant, rows = args.get("variant"), args.get("rows")
    inputs, stamp = _inputs(template_id, variant, rows, _currency(cfg))
    merged: dict[str, Any] = {k: v for k, v in (old or {}).items() if k != "inputs"}
    merged["inputs"] = inputs.model_dump(mode="json")
    merged["break_even_line"] = _break_even_line(
        merged.get("break_even_line"), merged["inputs"]["lines"]
    )
    try:
        new = config_or_422(merged).model_dump(mode="json")
    except HTTPException as exc:
        d = exc.detail if isinstance(exc.detail, dict) else {}
        _refuse(
            "business_case_inputs_invalid",
            str(d.get("message") or exc.detail)[:300],
            errors=list(d.get("errors") or [])[:20],
        )
    names = [ln["name"] for ln in new["inputs"]["lines"]]
    twice = sorted({x for x in names if names.count(x) > 1})
    if twice:  # the card keys lines by name (as chat_edit.plan)
        _refuse(
            "business_case_inputs_invalid",
            f"each line has its own name; named twice: {', '.join(twice)[:200]}",
        )
    if variant is not None:
        picked = f"{template_id} preset '{variant}': {P.variants(template_id)[variant]}"
    else:
        picked = f"{template_id}: offered rows " + ", ".join(rows)
    out = _rows(old, new, picked, stamp)
    if len(out) > CARD_ROW_CAP or len(json.dumps(out, default=str)) > CARD_CHARS:
        _refuse(
            "business_case_card_too_large",
            f"this preset shows {len(out)} rows, more "
            f"than one card holds (at most {CARD_ROW_CAP})",
            rows=len(out),
        )
    return {"new": new, "old": old, "tag": tag, "rows": out, "to_ask": to_ask(new)}


def read_cell(row: dict, stored: dict | None):
    """The stored value a card row names, as the card shows it (receipt read-back). The
    preset and pack rows name the call, not a stored value: they read as shown."""
    from harness.edit_card import _setting

    path = list(row.get("path") or [])
    if path in (["preset"], ["defaults_pack"]):
        return row.get("new")
    if path[:2] == ["inputs", "lines"] and len(path) >= 3:
        line = _lines(stored).get(path[2])
        if line is None:
            return None
        suffix = ".".join(path[3:])
        found = next((v for p, _l, v, _u in cells(line) if p == suffix), None)
        return _setting(found)
    doc: Any = stored
    for part in path:
        doc = doc.get(part) if isinstance(doc, dict) else None
    return _setting(doc)


def says(rows: list[dict]) -> str:
    """The card's one line: the lines added, replaced and removed, each named once."""
    counts: dict[str, list[str]] = {"added": [], "replaced": [], "removed": []}
    for r in rows:
        names = counts.get(r["change"])
        if r["line"] and names is not None and r["line"] not in names:
            names.append(r["line"])
    parts = [f"{w}: {', '.join(names)}" for w, names in counts.items() if names]
    return "business-case preset; " + ("; ".join(parts) or "no line change")
