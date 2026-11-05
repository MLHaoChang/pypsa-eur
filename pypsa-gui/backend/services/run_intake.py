"""Chat data intake: attachments and pasted values become a run's setup
inputs, each beside its source (study harness issue 21; owner decisions
SH21-ACCEPT-1, LOAD-SHAPE-1, AI-567b; GK B conditions D1-D4, A4, R4-R6).

The model proposes **citations**, never values. Each citation is checked
here and becomes an intake row `{param, value, unit, source, status}` or a
gap. Nothing is written: the rows go on the plan-review card, and only the
user's grant (bound by `digest`) or a card applies them.

Sources, one rule set:

* **Table** (XLSX/CSV upload): `{param, upload_id, sheet?, cell}`, or for
  `stages`/`energization` `{param, upload_id, sheet?, columns: {year, peak_mw|mw}}`
  read in row order. `equipment_table.read_table` reads it; the unit comes from
  the header ("peak (kW)" or "peak [kW]") through `_column_verdict`.
* **PDF** upload: `{param, upload_id, page, quote, unit, value_text?}` through
  `document_values.read_pages`, `locate` and `_value`.
* **Text** the user typed: `{param, turn_id, quote, unit, value_text?}`, the
  turn's stored user text read as a one-page document through the same
  `locate` and `_value`. Only a turn the run's owner typed counts (D1, R4).

A number counts only with its unit written right after it in the quote (or
in the table header): "40 %" is 0.4, "40" is `unit_missing` (SH21-ACCEPT-1
Q3). Enums and yes/no come from text only; the assistant names the id (`id`)
and the row says it was mapped from the quote (D2). A citation that carries a
`value` is refused (D3).

Uploads resolve only in the host project's own directory (A4, R5): an id
that is not there is `not_found`, the same bytes whether it exists elsewhere
or nowhere, and no other project is read.

The intake and data-request texts are harness text (D4): one fixed line per
item, the quote cut to `document_values.MAX_QUOTE_CHARS`, and no error text
other than a `SiteParamsError`'s own.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from services import document_values as DV
from services import equipment_table as T

STATUSES = ("proposed", "confirmed", "struck", "conflict")
USABLE = ("proposed", "confirmed")
ORIGIN_KEY = "origin"  # set to "user" on a turn whose `user` text the person typed
MAX_CITATIONS = 60
QUOTE_CHARS = DV.MAX_QUOTE_CHARS

_LIST_PATH = re.compile(r"^(stages|energization)\[(\d{1,2})\]\.(year|peak_mw|mw)$")
_HEADER_UNIT = re.compile(r"[\(\[]\s*([^()\[\]]{1,24}?)\s*[\)\]]\s*$")
_A1 = re.compile(r"^([A-Z]{1,3})([1-9]\d{0,6})$")
_LIST_COLUMNS = {"stages": ("year", "peak_mw"), "energization": ("year", "mw")}
_TEXT_ONLY = ("enum", "bool")
_BOOL_IDS = {True: True, False: False, "true": True, "false": False}

# D4: what the intake message and the data request say, per gap kind. A gap's
# own message may quote the source; these never do.
_KIND_TEXT = {
    "not_found": "the file is not among this project's uploads",
    "unit_missing": "no unit is stated with the number",
    "unit_mismatch": "the unit is not one this value takes",
    "quote_not_found": "the quote is not in the source",
    "no_text_layer": "the page has no text to read",
    "ambiguous_value": "the quote holds a range, a ± value or several numbers",
    "ambiguous_number": "the number reads two ways",
    "conflict": "two sources give different values",
    "site_params": "the template refuses the value",
    "turn_not_citable": "only text you typed in this chat can be cited",
    "not_in_enum": "it is not one of the choices",
    "source_not_accepted": "this kind of source cannot give it",
    "missing": "not given yet",
}


class IntakeError(ValueError):
    """A citation list the intake refuses as a whole (`kind`, message)."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Host:
    """The project intake reads: its name and its authorised directory
    (resolved through the owner's project ACL by the caller), the owner, and
    the host thread's stored turns (`harness.history.read_all_turns`)."""

    name: str
    dir: Path
    owner_id: str
    turns: tuple = ()


def host_for(ctx, owner_id: str, project_id: str) -> Host:
    """The open project as the intake's host, resolved through the acting
    owner's project ACL (A4): its directory and its stored turns. A caller
    that is not the owner, or cannot open the project, gets `not_found`."""
    from harness.history import read_all_turns
    from services import chat_tools, project_registry

    with chat_tools._acting() as (db, user):
        if str(user.id) != str(owner_id):
            raise IntakeError("not_found", "This run's project is not open to you.")
        row = project_registry.resolve_project(db, user, project_id)
        return Host(
            name=row.name,
            dir=project_registry.project_dir(row),
            owner_id=str(owner_id),
            turns=tuple(read_all_turns(ctx)),
        )


# ── Fields ──────────────────────────────────────────────────────────────────


def field_key(param: str) -> str:
    """`stages[1].peak_mw` -> `stages[].peak_mw`; other params as they are."""
    m = _LIST_PATH.match(param)
    return f"{m.group(1)}[].{m.group(3)}" if m else param


def _field(fields: dict, param) -> dict | None:
    return fields.get(field_key(param)) if isinstance(param, str) else None


def top(param: str) -> str:
    """The creation param a row fills (`existing.pv_mw` -> `existing`)."""
    return re.split(r"[.\[]", param, maxsplit=1)[0]


def _gap(param, kind, message, **extra) -> dict:
    return {"param": param, "kind": kind, "message": message, **extra}


# ── Turns (D1, R4) ──────────────────────────────────────────────────────────


def turn_id(record: dict) -> str:
    """A turn's id: a digest of who, when, where and what was typed. A turn
    edited or replaced after it was cited no longer has the cited id (R4)."""
    from harness.session import TURN_AUTHOR_KEY

    basis = {k: record.get(k) for k in ("session_id", "ts", TURN_AUTHOR_KEY, "user")}
    return hashlib.sha256(
        json.dumps(basis, sort_keys=True, default=str).encode()
    ).hexdigest()[:32]


def _typed_text(host: Host, tid) -> str | None:
    """The text of turn `tid` when the run's owner typed it, else None (one
    refusal for every reason, so the answer is no oracle). The owner is the
    stored author id, never a name (R4); the runner's own turns, harness
    messages and anything not marked as typed by the person are refused."""
    from harness.session import TURN_AUTHOR_KEY

    if not isinstance(tid, str):
        return None
    for record in host.turns:
        if not isinstance(record, dict) or turn_id(record) != tid:
            continue
        typed = (
            record.get(TURN_AUTHOR_KEY) == host.owner_id
            and record.get(ORIGIN_KEY) == "user"
            and not str(record.get("session_id") or "").startswith("run:")
            and isinstance(record.get("user"), str)
        )
        return record["user"] if typed else None
    return None


# ── Reading one citation ────────────────────────────────────────────────────


def _reader_gap(param, exc: HTTPException) -> dict:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    kind = detail.get("error_kind") or "unreadable"
    if exc.status_code == 404 or kind == "upload_not_found":
        # A4/R5: one body for every miss; the id and the project are not echoed.
        return _gap(param, "not_found", "No upload with that id in this project.")
    return _gap(param, kind, f"The file could not be read ({kind}).")


def _number_verdict(param, field, unit):
    """`(scale, stored unit, None)` or `(None, None, gap)` for a numeric field."""
    if field["kind"] in ("year", "count"):
        return 1, field.get("unit_storage") or "", None
    verdict, reason = T._column_verdict(param, field, unit, {})
    if reason:
        return (
            None,
            None,
            _gap(
                param,
                reason["kind"],
                reason["message"],
                **(
                    {"accepted_units": reason["accepted_units"]}
                    if "accepted_units" in reason
                    else {}
                ),
            ),
        )
    return verdict["scale"], verdict["stored_unit"], None


def _whole(param, field, value):
    if field["kind"] in ("year", "count"):
        if value != int(value):
            return None, _gap(
                param, "invalid_value", f"{field['label']} is a whole number."
            )
        return int(value), None
    return T._as_value(value), None


def _from_quote(param, field, c, pages, src):
    """A value read from a quote on a page: the number `_value` reads and the
    unit written right after it."""
    found, gap = DV.locate(c, pages, src)
    if gap:
        return None, _gap(param, gap["kind"], gap["message"])
    raw_quote, quote, page, at = found
    unit = (str(c.get("unit") or "")).strip() or None
    scale, stored, gap = _number_verdict(param, field, unit)
    if gap:
        return None, gap
    number, gap = DV._value(
        dict(c, field=param), quote, pages[page - 1], at, c.get("decimal_mark"), field
    )
    if gap:
        return None, _gap(param, gap["kind"], gap["message"])
    if field["kind"] not in ("year", "count"):
        spot = [
            x
            for x in DV.numbers(quote, c.get("decimal_mark"))
            if x["text"] == number["text"]
        ][0]
        if not DV._unit_re(unit).match(quote[spot["end"] :]):
            return None, _gap(
                param,
                "unit_missing",
                f"The quote states no {unit} after {number['text']}; "
                "a number counts only with its unit.",
            )
    value, gap = _whole(param, field, number["value"] / scale)
    if gap:
        return None, gap
    return {
        "value": value,
        "unit": stored,
        "raw": number["text"],
        "raw_unit": unit,
        "quote": raw_quote,
        "page": page,
    }, None


def _mapped(param, field, c):
    """D2: an enum id or a yes/no the assistant chose from the quote."""
    choice = c.get("id")
    if field["kind"] == "bool":
        if (
            choice not in _BOOL_IDS
            or isinstance(choice, (int, float))
            and not isinstance(choice, bool)
        ):
            return None, _gap(
                param,
                "not_in_enum",
                f"{field['label']} is yes or no (id true or false).",
            )
        return _BOOL_IDS[choice], None
    if choice not in field["enum"]:
        return None, _gap(
            param, "not_in_enum", f"{field['label']} is one of {field['enum']}."
        )
    return choice, None


def _text_row(param, field, c, host: Host):
    text = _typed_text(host, c.get("turn_id"))
    if text is None:
        return None, _gap(
            param,
            "turn_not_citable",
            "Only a message the run's owner typed in this chat can be cited.",
        )
    pages = [DV.normal(text)]
    cite = dict(c, page=1)
    src = {"truncated": []}
    sha = hashlib.sha256(text.encode()).hexdigest()
    source = {"kind": "text", "turn_id": c["turn_id"], "sha256": sha}
    if field["kind"] in _TEXT_ONLY:
        found, gap = DV.locate(cite, pages, src)
        if gap:
            return None, _gap(
                param,
                "quote_not_found",
                "The quote is not in that message, word for word.",
            )
        value, gap = _mapped(param, field, c)
        if gap:
            return None, gap
        return {
            "value": value,
            "unit": None,
            "source": dict(source, quote=found[0]),
            "mapped": "assistant",
        }, None
    read, gap = _from_quote(param, field, cite, pages, src)
    if gap:
        return None, gap
    return {
        "value": read["value"],
        "unit": read["unit"],
        "source": dict(
            source, quote=read["quote"], raw=read["raw"], raw_unit=read["raw_unit"]
        ),
    }, None


def _pdf_row(param, field, c, host: Host):
    try:
        doc = DV.read_pages(
            host.name, str(c.get("upload_id") or ""), project_dir=host.dir
        )
    except HTTPException as exc:
        return None, _reader_gap(param, exc)
    pages = [DV.normal(p) for p in doc["pages"]]
    read, gap = _from_quote(param, field, c, pages, doc["source"])
    if gap:
        return None, gap
    src = doc["source"]
    return {
        "value": read["value"],
        "unit": read["unit"],
        "source": {
            "kind": "pdf",
            "upload_id": src["upload_id"],
            "filename": src["filename"],
            "sha256": src["sha256"],
            "page": read["page"],
            "quote": read["quote"],
            "raw": read["raw"],
            "raw_unit": read["raw_unit"],
        },
    }, None


def _header_unit(header: str) -> str | None:
    m = _HEADER_UNIT.search(header or "")
    return m.group(1) if m else None


def _cell_value(param, field, header, cell, source):
    unit = _header_unit(header)
    scale, stored, gap = _number_verdict(param, field, unit)
    if gap:
        return None, dict(gap, cell=cell.get("cell"))
    number = T._number(cell.get("raw"))
    if number is None:
        return None, _gap(
            param,
            "not_a_number",
            f"Cell {cell.get('cell')} holds no number.",
            cell=cell.get("cell"),
        )
    value, gap = _whole(param, field, number / scale)
    if gap:
        return None, gap
    return {
        "value": value,
        "unit": stored,
        "source": dict(
            source,
            cell=cell["cell"],
            header=header[:QUOTE_CHARS],
            raw=str(cell.get("raw"))[:64],
            raw_unit=unit,
        ),
    }, None


def _table_rows(param, fields, c, host: Host):
    """Table citations: one cell, or a list param's columns in row order."""
    try:
        table = T.read_table(
            host.name,
            str(c.get("upload_id") or ""),
            c.get("sheet"),
            project_dir=host.dir,
        )
    except HTTPException as exc:
        return [], [_reader_gap(param, exc)]
    src = table["source"]
    source = {
        "kind": "table",
        "upload_id": src["upload_id"],
        "filename": src["filename"],
        "sha256": src["sha256"],
        "sheet": src["sheet"],
    }
    if param in _LIST_COLUMNS:
        columns = c.get("columns") or {}
        wanted = _LIST_COLUMNS[param]
        if (
            not isinstance(columns, dict)
            or set(columns) != set(wanted)
            or any(columns[k] not in table["headers"] for k in wanted)
        ):
            return [], [
                _gap(
                    param,
                    "column_not_found",
                    f"Name the table's columns for {', '.join(wanted)}.",
                )
            ]
        rows, gaps = [], []
        data = [
            r
            for r in table["rows"]
            if any(r["cells"][columns[k]]["raw"] not in (None, "") for k in wanted)
        ]
        for i, r in enumerate(data):
            for k in wanted:
                sub = f"{param}[{i}].{k}"
                row, gap = _cell_value(
                    sub,
                    fields[f"{param}[].{k}"],
                    columns[k],
                    r["cells"][columns[k]],
                    source,
                )
                (rows.append(dict(row, param=sub)) if row else gaps.append(gap))
        if not data:
            gaps.append(_gap(param, "missing", "The named columns hold no rows."))
        return rows, gaps
    m = _A1.match(str(c.get("cell") or ""))
    if not m:
        return [], [
            _gap(
                param,
                "cell_not_found",
                "Cite one cell as A1 (column letters, row number).",
            )
        ]
    for r in table["rows"]:
        for header, cell in r["cells"].items():
            if cell.get("cell") == c["cell"]:
                row, gap = _cell_value(
                    param, _field(fields, param), header, cell, source
                )
                return ([dict(row, param=param)], []) if row else ([], [gap])
    return [], [
        _gap(param, "cell_not_found", f"No cell {c['cell']} in the table's data rows.")
    ]


# Issue 21c: an intake row that is a planned write on the created network.
# One tool today (GK B (19) Q2); its row's param is `load_series.<load>`, so a
# profile's `inputs.required` may name `load_series`, and two uploads for one
# load are a conflict.
WRITE_TOOLS = ("apply_demand_from_excel",)
WRITE_PARAM = "load_series"
_WRITE_KEYS = {"write", "upload_id", "sheet", "time_col", "value_col", "load"}


def _write_row(c, host: Host):
    """`({row}, None)` or `(None, gap)` for a planned-write citation: the
    upload's table read through the owner's ACL, its columns checked, its
    bytes' sha256 bound. The model names where the series is, never values."""
    load = c.get("load")
    param = f"{WRITE_PARAM}.{load}" if isinstance(load, str) and load else WRITE_PARAM
    if (
        c.get("write") not in WRITE_TOOLS
        or set(c) - _WRITE_KEYS
        or not all(
            isinstance(c.get(k), str) and c[k]
            for k in ("upload_id", "time_col", "value_col", "load")
        )
        or (c.get("sheet") is not None and not isinstance(c["sheet"], str))
    ):
        return None, _gap(
            param,
            "source_not_accepted",
            "A load series names its upload, its time and value columns and the load.",
        )
    try:
        table = T.read_table(
            host.name, c["upload_id"], c.get("sheet"), project_dir=host.dir
        )
    except HTTPException as exc:
        return None, _reader_gap(param, exc)
    missing_cols = [
        k for k in ("time_col", "value_col") if c[k] not in table["headers"]
    ]
    if missing_cols:
        return None, _gap(
            param,
            "column_not_found",
            f"The table has no column {', '.join(repr(c[k]) for k in missing_cols)}.",
        )
    src = table["source"]
    args = {
        "file_id": src["upload_id"],
        "time_col": c["time_col"],
        "value_col": c["value_col"],
        "load_name": c["load"],
        **({"sheet_name": c["sheet"]} if c.get("sheet") else {}),
    }
    source = {
        "kind": "table",
        "upload_id": src["upload_id"],
        "filename": src["filename"],
        "sha256": src["sha256"],
        "sheet": src["sheet"],
        "cell": f"{c['time_col']}/{c['value_col']}",
    }
    return {
        "param": param,
        "write": c["write"],
        "args": args,
        "value": dict(args),
        "unit": "",
        "source": source,
    }, None


def _source_kind(c) -> str | None:
    if "turn_id" in c:
        return "text"
    if "upload_id" in c and "page" in c:
        return "pdf"
    if "upload_id" in c:
        return "table"
    return None


def read(citations: list, fields: dict, host: Host) -> dict:
    """`{rows, gaps}` for the model's citations. Rows are `proposed`; two
    sources that disagree on a param make every row of it a `conflict`
    (neither used) and one gap naming both. Raises IntakeError for a list
    the intake does not take at all."""
    if not isinstance(citations, list):
        raise IntakeError("invalid_intake", "Citations are a list.")
    if len(citations) > MAX_CITATIONS:
        raise IntakeError(
            "invalid_intake", f"At most {MAX_CITATIONS} citations per intake."
        )
    rows, gaps = [], []
    for c in citations:
        if not isinstance(c, dict):
            raise IntakeError("invalid_intake", "Each citation is an object.")
        if "value" in c:
            # D3 (AI-567b): the model points at a value; the harness reads it.
            raise IntakeError(
                "intake_value_supplied",
                "A citation names where a value is, never the value.",
            )
        if "write" in c:
            row, gap = _write_row(c, host)
            if gap:
                gaps.append(gap)
            else:
                rows.append(row)
            continue
        param = c.get("param")
        field = (
            _field(fields, param)
            if param not in _LIST_COLUMNS
            else fields.get(f"{param}[].year")
        )
        if field is None:
            gaps.append(
                _gap(
                    param if isinstance(param, str) else None,
                    "unknown_param",
                    "That is not one of this run's inputs.",
                )
            )
            continue
        kind = _source_kind(c)
        if kind is None or (
            kind not in field["sources"] and param not in _LIST_COLUMNS
        ):
            gaps.append(
                _gap(
                    param,
                    "source_not_accepted",
                    f"{field['label']} comes from {', '.join(field['sources'])} only.",
                )
            )
            continue
        if kind == "table":
            got, missed = _table_rows(param, fields, c, host)
            rows += got
            gaps += missed
            continue
        if param in _LIST_COLUMNS:
            gaps.append(
                _gap(
                    param,
                    "source_not_accepted",
                    "A list of stages comes from a table's columns, or "
                    "one value per citation (stages[0].peak_mw).",
                )
            )
            continue
        row, gap = (_text_row if kind == "text" else _pdf_row)(param, field, c, host)
        if gap:
            gaps.append(gap)
        else:
            rows.append(dict(row, param=param))
    return _conflicts([dict(r, status="proposed") for r in rows], gaps)


def _conflicts(rows, gaps):
    by = {}
    for r in rows:
        by.setdefault(r["param"], []).append(r)
    for param, same in by.items():
        if len({json.dumps(r["value"]) for r in same}) > 1:
            for r in same:
                r["status"] = "conflict"
            gaps.append(
                _gap(
                    param,
                    "conflict",
                    "Two sources give different values; neither is used.",
                    conflicting=[describe_source(r) for r in same],
                )
            )
    return {"rows": rows, "gaps": gaps}


# ── The confirmed set and the template's check ──────────────────────────────


def _put(params: dict, path: str, value):
    m = _LIST_PATH.match(path)
    if m:
        items = params.setdefault(m.group(1), [])
        i = int(m.group(2))
        while len(items) <= i:
            items.append({})
        items[i][m.group(3)] = value
        return
    node = params
    *head, last = path.split(".")
    for part in head:
        node = node.setdefault(part, {})
    node[last] = value


def params(rows: list) -> dict:
    """The creation params the usable rows give (first row of a param wins;
    rows of one param agree, or they are a conflict and not usable)."""
    out, seen = {}, set()
    for r in rows:
        if r.get("write"):
            continue  # a planned write runs on the created network, never a creation param
        if r["status"] in USABLE and r["param"] not in seen:
            seen.add(r["param"])
            _put(out, r["param"], r["value"])
    return out


def check(rows: list, template_check) -> dict | None:
    """The template's own check over the usable rows: None, or the gap for
    the field `SiteParamsError` names, with its own text (D4)."""
    from project_templates.eh_site import SiteParamsError

    try:
        template_check(params(rows))
    except SiteParamsError as exc:
        return _gap(exc.field, "site_params", str(exc)[:QUOTE_CHARS])
    return None


def missing(profile: dict, rows: list, gaps: list) -> list[dict]:
    """§3: the items to ask for. Required params and inputs no usable row
    gives, every conflict, and every refused citation of a param still
    without a usable row. `params.ask` items take the preset default and are
    not asked."""
    given = {
        name
        for name, value in params(rows).items()
        if name not in _LIST_COLUMNS
        or all(set(_LIST_COLUMNS[name]) <= set(item) for item in value)
    } | {top(r["param"]) for r in rows if r.get("write") and r["status"] in USABLE}
    fields = profile["params"].get("fields", {})
    items, seen = [], set()

    def add(param, kind, extra=None):
        key = (top(param) if kind == "missing" else param, kind)
        if key in seen:
            return
        seen.add(key)
        items.append({"param": param, "kind": kind, **(extra or {})})

    for gap in gaps:
        if gap["kind"] in ("conflict", "site_params"):
            add(
                gap["param"],
                gap["kind"],
                {k: gap[k] for k in ("conflicting", "message") if k in gap},
            )
    asked = {top(item["param"]) for item in items}
    required = list(profile["params"]["required"]) + list(
        (profile.get("inputs") or {}).get("required", [])
    )
    for param in required:
        if param not in given and param not in asked:
            reasons = [
                g["kind"]
                for g in gaps
                if g.get("param")
                and top(g["param"]) == param
                and g["kind"] not in ("conflict", "site_params")
            ]
            add(param, "missing", {"tried": sorted(set(reasons))} if reasons else None)
    for item in items:
        item["label"], item["units"], item["sources"] = _ask_shape(
            fields, item["param"]
        )
    return items


def _ask_shape(fields, param):
    keys = [k for k in fields if top(k) == top(param)]
    field = _field(fields, param) or (fields[keys[0]] if keys else {})
    label = field.get("label", param)
    units = T._accepted_units(field) if field.get("kind") == "number" else []
    return label, units, list(field.get("sources", []))


# ── Texts (D4) ──────────────────────────────────────────────────────────────


_SOURCE_WORDS = {
    "table": "a table cell",
    "pdf": "a PDF quote",
    "text": "typed in chat with its unit",
}


def request_lines(items: list) -> list[str]:
    """One line per item: what, in which unit, from which sources, and why
    it is asked. Harness text: no document text beyond the bounded quote and
    no error text but `SiteParamsError`'s."""
    lines = []
    for item in items:
        unit = f" in {' or '.join(item['units'])}" if item.get("units") else ""
        how = ", ".join(
            _SOURCE_WORDS[s] for s in item.get("sources", []) if s in _SOURCE_WORDS
        )
        why = _KIND_TEXT.get(item["kind"], "it could not be read")
        if item["kind"] == "site_params":
            why = f"the template refuses it: {item['message']}"
        elif item["kind"] == "conflict":
            why += " (" + "; ".join(item.get("conflicting", [])) + ")"
        elif item.get("tried"):
            why += " (" + ", ".join(_KIND_TEXT.get(k, k) for k in item["tried"]) + ")"
        lines.append(
            f"- {item['label']} (`{item['param']}`){unit}: {why}."
            + (f" Give it as {how}." if how else "")
        )
    return lines


def request_text(items: list) -> str:
    if not items:
        return "Every required input is given."
    return "The run needs these before it can start:\n" + "\n".join(
        request_lines(items)
    )


def describe_source(row) -> str:
    """The card's source line: file and page and quote, sheet!A1, or what
    the user wrote; a mapped enum says the assistant chose it (D2)."""
    s = row["source"]
    quote = (s.get("quote") or "")[:QUOTE_CHARS]
    if s["kind"] == "pdf":
        text = f"{s['filename']}, page {s['page']}: '{quote}'"
    elif s["kind"] == "table":
        text = f"{s['filename']} {s['sheet']}!{s['cell']}"
    elif s["kind"] == "card":
        text = "you entered it on the card"
    else:
        text = f"you wrote '{quote}'"
    return f"mapped by the assistant from: '{quote}'" if row.get("mapped") else text


# ── The card and the grant (R6) ─────────────────────────────────────────────


def card_view(rows: list, profile: dict, template_check, entered=None) -> dict:
    """The plan-review card's "Inputs from the chat" section: one row per
    intake row, then every param not given as a preset default."""
    fields = profile["params"].get("fields", {})
    shown = [
        {
            "index": i,
            "param": r["param"],
            "label": (
                f"Load {r['args']['load_name']}: demand series (replaces its current demand)"
                if r.get("write")
                else (_field(fields, r["param"]) or {}).get("label", r["param"])
            ),
            **({"write": r["write"]} if r.get("write") else {}),
            "value": r["value"],
            "unit": r["unit"],
            "status": r["status"],
            "source": describe_source(r),
            "from_document": r["source"]["kind"] in ("pdf", "table"),
            "mapped": bool(r.get("mapped")),
        }
        for i, r in enumerate(rows)
    ]
    given = params(rows)
    entered = set(entered(given) if entered else [])
    try:
        resolved = template_check(given)
    except Exception:  # noqa: BLE001 — the card shows no defaults while the check refuses
        resolved = None
    defaults = []
    if resolved is not None:
        for key in fields:
            name = top(key)
            if (
                name not in given
                and name not in {d["param"] for d in defaults}
                and not any(e.split(".")[0] == name for e in entered)
            ):
                defaults.append(
                    {
                        "param": name,
                        "value": copy.deepcopy(resolved.get(name)),
                        "preset_default": True,
                    }
                )
    return {"rows": shown, "defaults": defaults}


def digest(rows: list) -> str:
    """The grant binds exactly these rows and each source's sha256 (R6), as
    the equipment card binds its plan (`edit_card` digest)."""
    basis = [
        {
            "param": r["param"],
            "value": r["value"],
            "unit": r["unit"],
            "status": r["status"],
            # Issue 21c: a planned write binds its tool and args as well.
            **({"write": r["write"], "args": r["args"]} if r.get("write") else {}),
            "source": {
                k: r["source"].get(k)
                for k in (
                    "kind",
                    "upload_id",
                    "turn_id",
                    "sha256",
                    "page",
                    "cell",
                    "sheet",
                    "quote",
                )
            },
        }
        for r in rows
    ]
    return hashlib.sha256(
        json.dumps(basis, sort_keys=True, default=str).encode()
    ).hexdigest()


def _current_sha(row, host: Host) -> str | None:
    from services import upload_service

    s = row["source"]
    if s["kind"] == "text":
        text = _typed_text(host, s.get("turn_id"))
        return hashlib.sha256(text.encode()).hexdigest() if text is not None else None
    if s["kind"] == "card":
        return s.get("sha256")
    try:
        blob = upload_service.get_upload_bytes(
            host.name, s["upload_id"], project_dir=host.dir
        )
    except HTTPException:
        return None
    return hashlib.sha256(blob).hexdigest()


def check_grant(rows: list, granted_digest, host: Host) -> None:
    """Refuses a grant whose digest is not these rows' (a row changed after
    the card was shown) or whose sources changed since they were read (R6)."""
    if not isinstance(granted_digest, str) or granted_digest != digest(rows):
        raise IntakeError(
            "intake_stale",
            "The inputs changed since the card was shown; review them again.",
        )
    for r in rows:
        if r["status"] in USABLE and _current_sha(r, host) != r["source"].get("sha256"):
            raise IntakeError(
                "intake_stale",
                "A source changed since it was read; review the inputs again.",
            )


def strike(rows: list, index: int) -> list:
    """The user strikes a row on the card (the digest changes with it)."""
    out = copy.deepcopy(rows)
    if not isinstance(index, int) or not 0 <= index < len(out):
        raise IntakeError("invalid_intake", "No such row.")
    out[index]["status"] = "struck"
    return out


def confirm(rows: list) -> list:
    """The grant confirms exactly the usable rows it showed."""
    return [
        dict(r, status="confirmed") if r["status"] == "proposed" else dict(r)
        for r in rows
    ]


def take(request, profile: dict, ctx, owner_id: str, project_id: str) -> dict:
    """A run's intake (`start_task`'s `run.intake`): the model's citations
    read on the open project (the host) into rows, gaps and the missing set,
    with the digest the grant binds and the harness's one request text (§3).
    Nothing is written to any project."""
    if (
        not isinstance(request, dict)
        or set(request) - {"project_name", "citations"}
        or not isinstance(request.get("project_name"), str)
        or not request["project_name"].strip()
    ):
        raise IntakeError(
            "invalid_intake", "An intake names the new project and lists citations."
        )
    if profile["templates"] != ["eh_site"]:
        raise IntakeError(
            "invalid_intake", "This run's profile takes no chat intake yet."
        )
    from project_templates import eh_site

    host = host_for(ctx, owner_id, project_id)
    out = read(
        request.get("citations") or [], profile["params"].get("fields", {}), host
    )
    rows, gaps = out["rows"], list(out["gaps"])
    refused = check(rows, eh_site.site_params)
    if refused is not None:
        gaps.append(refused)
    items = missing(profile, rows, gaps)
    return {
        "project_name": request["project_name"].strip()[:120],
        "rows": rows,
        "gaps": gaps,
        "missing": items,
        "digest": digest(rows),
        "request": request_text(items) if items else None,
    }
