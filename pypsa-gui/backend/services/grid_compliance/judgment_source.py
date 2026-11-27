"""The ``SOURCES["GC-07"]`` provider: the pack read-back judgment the campus
run stores (``services.campus_judgment``), as report tables.

The tables carry the judgment as the read-back wrote it: the case summary
(each case's status) and every judged row. Nothing is re-judged here, so an
unknown case or row stays unknown, and a status the read-back does not write
reads unknown. A measured value is printed only for a row judged pass or
fail, and never alike its limit when the two differ. A run with no stored
judgment, or one whose judgment no longer matches its pack, is pending (None).
"""

from __future__ import annotations

from models.report import Table

CASE_COLUMNS = ["Case", "Kind", "Status", "Detail"]
ROW_COLUMNS = [
    "Case",
    "Check",
    "Role",
    "Status",
    "Measured",
    "Limit",
    "Margin",
    "Unit",
    "Clause",
    "Source",
    "Detail",
]


def _status(value) -> str:
    from services.campus_judgment import STATUSES

    return value if value in STATUSES else "unknown"


def _row(case_id, r: dict) -> list[str]:
    from services.grid_compliance.ssti_source import _apart, _number, _text

    status = _status(r.get("status"))
    if status in ("pass", "fail"):
        measured, limit = _apart(r.get("measured"), r.get("limit"))
        margin = _number(r.get("margin"))
    else:  # a value the judgment did not judge is not shown
        measured, limit, margin = "", _number(r.get("limit")), ""
    return [
        _text(case_id),
        _text(r.get("check")),
        _text(r.get("role")),
        status,
        measured,
        limit,
        margin,
        _text(r.get("unit")),
        _text(r.get("clause")),
        _text(r.get("source")),
        _text(r.get("detail")),
    ]


def gc07_tables(project) -> list[Table] | None:
    """The case summary and the judged rows of the stored judgment; None when
    the latest campus run has none or it is stale. An unreadable judgment
    raises, so the tracker shows the source as unavailable, never present."""
    from services import campus_judgment
    from services.grid_compliance.ssti_source import _text

    got = campus_judgment.stored(project)
    if got is None or got["stale"]:
        return None
    cases = got["judgment"]["cases"]
    n = campus_judgment.counts(cases)
    caption = (
        f"Pack read-back: {n['pass']} pass, {n['fail']} fail, {n['unknown']} unknown."
    )
    return [
        Table(
            table_id="gc07_cases",
            columns=CASE_COLUMNS,
            caption=caption,
            rows=[
                [
                    _text(c.get("case_id")),
                    _text(c.get("kind")),
                    _status(c.get("status")),
                    _text(c.get("detail")),
                ]
                for c in cases
            ],
            source_path="/compliance/judgment.json/cases",
        ),
        Table(
            table_id="gc07_rows",
            columns=ROW_COLUMNS,
            caption="Judged rows, per case.",
            rows=[
                _row(c.get("case_id"), r) for c in cases for r in (c.get("rows") or [])
            ],
            source_path="/compliance/judgment.json/cases/rows",
        ),
    ]
