"""
LC-27 — ``campus_island_report(table=...)`` hands the copilot one table whole.

The unnarrowed answer is fitted to the result cap and keeps only the first two
tables, so the frequency tables never reached the model. ``table`` names one by
id; the ids come from one constant that the schema enum, the description and
the refusal all read.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from services import campus_island_report as cir
from services import chat_tools
from services.chat_tools_schema import TOOLS
# The hub builder is another file's fixture; imported, not edited.
from tests.test_lc06_d16_frequency_events import (  # noqa: F401
    P, _network, _sidecar, make_hub, user_and_db)


def _tool() -> dict:
    return next(t for t in TOOLS if t["name"] == "campus_island_report")


def _refusal(exc: HTTPException) -> dict:
    assert exc.status_code == 422
    return exc.detail


def test_schema_enum_description_and_constant_agree():
    spec = _tool()
    assert spec["input_schema"]["properties"]["table"]["enum"] == list(cir.TABLE_IDS)
    assert "table" not in spec["input_schema"]["required"]
    assert all(f"`{tid}`" in spec["description"] for tid in cir.TABLE_IDS)


def test_the_id_literal_matches_the_builders_title_maps():
    derived = [*(f"static_{k}" for k in cir._STATIC_TITLES), *(f"freq_{k}" for k in cir._FREQ_TITLES)]
    fixed = ["requirements", "units", "costs", "binding", "facts", "island_hours",
             "static_summary", "freq_summary"]
    assert sorted(cir.TABLE_IDS) == sorted(fixed + derived)


def test_a_real_full_report_only_produces_ids_the_tool_names(make_hub):
    make_hub(_network(), _sidecar())
    ids = [t["id"] for t in make_hub.report["tables"]]
    assert len(ids) > 2 and "freq_events" in ids
    assert set(ids) <= set(cir.TABLE_IDS)
    assert len(set(cir.TABLE_IDS)) == len(cir.TABLE_IDS)


def test_table_returns_that_table_whole_with_its_rows(make_hub):
    make_hub(_network(), _sidecar())
    full = next(t for t in make_hub.report["tables"] if t["id"] == "freq_events")
    out = chat_tools.campus_island_report(P, table="freq_events")
    assert out["table"] == full
    assert "tables" not in out and "truncated" not in out
    assert "freq_events" in out["table_ids"]


def test_without_table_the_answer_is_the_same_two_tables_plus_the_ids(make_hub):
    make_hub(_network(), _sidecar())
    ids = [t["id"] for t in make_hub.report["tables"]]
    out = chat_tools.campus_island_report(P)
    assert [t["id"] for t in out["tables"]] == ["requirements", "units"]
    assert out["truncated"] == {"tables": {"total": len(ids), "shown": 2}}
    assert out["table_ids"] == ids


@pytest.mark.parametrize("bad", ["nope", "", "FREQ_EVENTS", " freq_events", 5, ["freq_events"]])
def test_an_unknown_empty_wrong_case_or_non_string_id_is_refused(make_hub, bad):
    make_hub(_network(), _sidecar())
    with pytest.raises(HTTPException) as exc:
        chat_tools.campus_island_report(P, table=bad)
    detail = _refusal(exc.value)
    assert detail["error_kind"] == "invalid_tool_args"
    assert all(tid in detail["message"] for tid in cir.TABLE_IDS)


def test_a_valid_id_the_report_lacks_says_so_instead_of_returning_nothing(make_hub):
    make_hub(_network(), _sidecar())
    present = {t["id"] for t in make_hub.report["tables"]}
    absent = next(tid for tid in cir.TABLE_IDS if tid not in present)
    out = chat_tools.campus_island_report(P, table=absent)
    assert out["table"] is None and absent in out["not_in_report"]


def test_an_unknown_campus_is_refused_the_same_way_with_and_without_table(make_hub):
    make_hub(_network(), _sidecar())
    with pytest.raises(HTTPException) as plain:
        chat_tools.campus_island_report("No Such Hub")
    with pytest.raises(HTTPException) as narrowed:
        chat_tools.campus_island_report("No Such Hub", table="freq_events")
    with pytest.raises(HTTPException) as bad_id:
        chat_tools.campus_island_report("No Such Hub", table="nope")
    assert (plain.value.status_code, plain.value.detail) == (
        narrowed.value.status_code, narrowed.value.detail) == (
        bad_id.value.status_code, bad_id.value.detail)


def test_a_table_over_the_budget_says_rows_shown_of_total_and_where_the_rest_is(monkeypatch):
    rows = [[i, "x" * 40] for i in range(2000)]
    payload = {"caveat": "c", "missing": [], "tables": [
        {"id": "freq_events", "columns": ["a", "b"], "rows": rows}]}
    monkeypatch.setattr(chat_tools, "_campus_route", lambda *a, **k: payload)
    out = chat_tools.campus_island_report(P, table="freq_events")
    shown = len(out["table"]["rows"])
    assert 0 < shown < 2000
    assert out["truncated"]["table.rows"] == {"total": 2000, "shown": shown}
    assert "/island/report" in out["truncated_note"]
