# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Business-case 19: report variants "top N by NPV" and "greatest IRR"."""
from __future__ import annotations

import io

import pytest

from services.reports.assemble import evidence_only_document
from services.reports.docx_writer import render_document_docx
from services.reports.evidence import collect_evidence
from services.study import option_ranking as OR
from services.study.render_html import num, pct


def _f(oid, npv, irr, reason=None):
    return {"option_id": oid, "label": oid.upper(), "npv": npv, "irr": irr, "irr_reason": reason}


FIGS = [_f("a", 100.0, 0.05), _f("b", 300.0, 0.04), _f("c", 200.0, None, "no IRR: never positive"),
        _f("d", 300.0, 0.09), _f("e", None, None)]


def test_top_npv_orders_descending_ties_by_other_figure_then_id():
    r = OR.rank(FIGS, "top_npv", 3)
    assert [(x.option_id, x.rank) for x in r.rows] == [("d", 1), ("b", 1), ("c", 3)]
    assert [x.option_id for x in r.unranked] == ["e"]
    assert "top 3" in r.rule and "decision study" in r.rule and "last and not ranked" in r.rule


def test_default_n_is_three_and_fewer_options_say_so():
    assert len(OR.rank(FIGS, "top_npv").rows) == 3
    r = OR.rank(FIGS[:2], "top_npv")
    assert "Shows all 2 options." in r.rule


def test_greatest_irr_names_unranked_with_reason():
    r = OR.rank(FIGS, "greatest_irr")
    assert [x.option_id for x in r.rows] == ["d"]
    assert {x.option_id: x.reason for x in r.unranked}["c"] == "no IRR: never positive"
    rows = OR.table_rows(r)
    assert rows[0] == ["1", "D", "300", "9.0 %"]
    assert any("— (no IRR: never positive)" in cell for row in rows for cell in row)


def test_too_few_rankable_options_names_the_excluded():
    with pytest.raises(OR.VariantError) as e:
        OR.rank([_f("a", 1.0, None), _f("b", 2.0, None), _f("c", 3.0, 0.1)], "greatest_irr")
    assert e.value.kind == "report_variant_too_few_options" and "A, B" in e.value.message


@pytest.mark.parametrize("variant,n", [("nope", None), ("greatest_irr", 3), (None, 3),
                                       ("top_npv", 0), ("top_npv", 11)])
def test_invalid_requests_are_422(variant, n):
    with pytest.raises(OR.VariantError) as e:
        OR.check_request(variant, n)
    assert e.value.kind == "report_variant_invalid" and e.value.status == 422


def test_docx_table_has_options_in_npv_order_with_compare_formatting():
    from docx import Document

    r = OR.rank(FIGS, "top_npv", 3)
    ev = collect_evidence(study_report=None, eh_report=None, worksheet=None)
    doc = evidence_only_document(ev, title="T", report_id="0123456789abcdef",
                                 figure_pngs={}, option_ranking=r)
    sec = next(s for s in doc.sections if s.section_id == "option_ranking")
    assert sec.category == "investment" and r.rule in doc.tables["option_ranking"].caption
    d = Document(io.BytesIO(render_document_docx(doc, figure_bytes={})))
    cells = [[c.text for c in row.cells] for t in d.tables for row in t.rows]
    ranked = [row for row in cells if row and row[0] in {"1", "3"} and row[1] in {"B", "C", "D"}]
    assert [row[1] for row in ranked] == ["D", "B", "C"]
    assert ranked[0][2:] == [num(300.0), pct(0.09)]
    # the NPV header carries the unit; figures are right-aligned, "— (reason)" cells are not
    from docx.enum.text import WD_ALIGN_PARAGRAPH as A

    table = next(t for t in d.tables if t.rows[0].cells[1].text == "Option")
    assert table.rows[0].cells[2].text == "NPV (EUR)"
    assert table.rows[1].cells[2].paragraphs[0].alignment == A.RIGHT
    assert table.rows[1].cells[1].paragraphs[0].alignment != A.RIGHT
    last = table.rows[-1]
    assert last.cells[2].text.startswith("—") and last.cells[2].paragraphs[0].alignment != A.RIGHT


def test_generate_refuses_a_bad_variant_before_anything_starts(client, api_project):
    name = api_project("variant-bad")
    for body, kind in (({"variant": "nope"}, "report_variant_invalid"),
                       ({"variant": "greatest_irr", "n": 2}, "report_variant_invalid"),
                       ({"n": 2}, "report_variant_invalid"),
                       ({"variant": "top_npv"}, "report_variant_too_few_options")):
        r = client.post(f"/api/projects/{name}/reports/generate", json=body)
        assert r.status_code == 422, (body, r.text[:200])
        assert r.json()["detail"]["error_kind"] == kind
    assert client.get(f"/api/projects/{name}/reports").json() == []


def test_report_variants_with_no_study_offers_nothing(client, api_project):
    name = api_project("variant-none")
    r = client.get(f"/api/projects/{name}/report-variants")
    assert r.status_code == 200, r.text[:200]
    assert r.json()["study_id"] is None and r.json()["rankable_npv"] == 0


def _stub_record(monkeypatch, stale):
    from types import SimpleNamespace as NS

    import routers.studies as studies

    def case(irr, reason=None):
        return NS(kpis=NS(irr=irr, unavailable={"irr": reason} if reason else {}))

    opts = [{"option_id": o, "label": o.upper(), "option_npv": n}
            for o, n in (("a", 10.0), ("b", 30.0), ("c", 20.0))]
    report = NS(sections={"question": NS(payload={"options": opts})}, facts={}, currency_year=2020)
    cases = {"a": case(0.1), "b": case(0.2), "c": case(None, "no IRR: never positive")}
    monkeypatch.setattr(studies, "_report_record", lambda p, sid: (NS(), report, cases, None, {}))
    monkeypatch.setattr(studies, "_report_stale", lambda *a: ["ledger changed"] if stale else [])
    return studies


def test_ranking_for_study_matches_the_report_rows(monkeypatch):
    studies = _stub_record(monkeypatch, stale=False)
    r = studies.ranking_for_study(None, "s1", None, "top_npv", None)
    assert [x.option_id for x in r.rows] == ["b", "c", "a"] and r.study_id == "s1"
    r = studies.ranking_for_study(None, "s1", None, "greatest_irr", None)
    assert [x.option_id for x in r.rows] == ["b"] and r.unranked[0].reason == "no IRR: never positive"


def test_ranking_for_a_stale_compare_is_409(monkeypatch):
    from fastapi import HTTPException

    studies = _stub_record(monkeypatch, stale=True)
    with pytest.raises(HTTPException) as e:
        studies.ranking_for_study(None, "s1", None, "top_npv", None)
    assert e.value.status_code == 409 and e.value.detail["error_kind"] == "option_compare_stale"


def test_the_ranking_carries_the_compare_views_money_unit(monkeypatch):
    studies = _stub_record(monkeypatch, stale=False)
    assert studies.ranking_for_study(None, "s1", None, "top_npv", None).money == "EUR 2020"


def test_a_reason_code_falls_back_to_the_plain_sentence():
    assert OR._plain("irr_undefined") is None
    r = OR.rank([_f("a", 1.0, 0.1), _f("b", 2.0, 0.2), _f("c", 3.0, None, OR._plain("irr_undefined"))],
                "greatest_irr")
    assert r.unranked[0].reason == OR.NO_IRR_REASON


def test_ids_show_only_where_labels_clash():
    figs = [{**_f("a", 1.0, 0.1), "label": "Same"}, {**_f("b", 2.0, 0.2), "label": "Same"},
            _f("c", 3.0, 0.3)]
    cells = [row[1] for row in OR.table_rows(OR.rank(figs, "top_npv"))]
    assert cells == ["C", "Same (b)", "Same (a)"]


def test_report_variants_chat_words_are_owned():
    from services import not_via_chat as N

    TITLE = "Report variants (top 3 by NPV, greatest IRR)"

    def first(*words):
        return [m["title"] for m in N.matches(set(words))]

    assert "Report variants (top 3 by NPV, greatest IRR)" not in first("run", "the", "business", "hours", "report")
    assert "Report variants (top 3 by NPV, greatest IRR)" not in first("generate", "a", "report")
    assert "Report variants (top 3 by NPV, greatest IRR)" not in first("what", "is", "the", "irr", "of", "option", "b")
    assert first("report", "variant")[0].startswith("Report variants")
    for ask in ("create a variant of this scenario", "compare the template variants",
                "ranked list of generators by capacity"):
        assert TITLE not in first(*ask.split()), ask
    for ask in ("a report with the top 3 options by npv", "report the option with the greatest irr"):
        assert first(*ask.split())[:1] == [TITLE], ask


def test_a_tie_over_the_cut_off_is_said_and_unranked_columns_carry_their_own_reason():
    figs = [_f("a", 5.0, 0.1), _f("b", 5.0, 0.1), _f("c", 9.0, 0.2), _f("d", None, 0.3)]
    r = OR.rank(figs, "top_npv", 2)
    assert [x.rank for x in r.rows] == [1, 2, 2] and "tie on a rank are all shown" in r.rule
    last = OR.table_rows(r)[-1]
    assert last[2] == f"— ({OR.NO_NPV_REASON})" and last[3] == "30.0 %"
    cut = OR.rank([_f("a", 1.0, 0.1), _f("b", 2.0, 0.2), _f("c", None, None)], "top_npv", 1)
    assert f"— ({OR.NO_IRR_REASON})" == OR.table_rows(cut)[-1][3]


def test_a_ranked_row_without_an_irr_says_why():
    figs = [_f("a", 9.0, None, OR._plain("irr_undefined")), _f("b", 5.0, 0.1), _f("c", 1.0, None)]
    rows = OR.table_rows(OR.rank(figs, "top_npv", 3))
    assert [r[1] for r in rows] == ["A", "B", "C"]
    assert rows[0][3] == f"— ({OR.NO_IRR_REASON})" and rows[2][3] == f"— ({OR.NO_IRR_REASON})"
    assert rows[1][3] == "10.0 %"
