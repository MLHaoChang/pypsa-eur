"""Grid-to-process ticket 13: the report's "Grid risk to production" chapter.

The section (`services.results.grid_risk_production`) becomes one reliability chapter with a code-rendered table and
its disclosures, in the Results tab's own words (UX 9): the line labels, "out of date", "upper bound" + "the lines may
overlap", "Missing: <form word>", "Unavailable: no lost-production value", the flags in plain words. No section (no
solve) is a chapter stated not established; a network with no process unit, or another project open, adds none, so
every other report keeps its evidence hash.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.reports import assemble, chapters as C, evidence as E
from services.results import grid_risk_production as G
from tests.test_grid_risk_production import _network, _ts_map, _write_campus

NBSP = " "
_TAB = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "pages"
    / "results"
    / "GridRiskTab.tsx"
)


@pytest.fixture(scope="module")
def net():
    return _network()


def _section(n, campus=None):
    out = G.grid_risk_production(n, campus=campus)
    return {"available": True, "project_id": "p", "omitted": None, **out}


def _collect(**kw):
    return E.collect_evidence(study_report=None, eh_report=None, worksheet=None, **kw)


def _rows(ev):
    t = ev.tables[C.GRID_RISK]
    return t, {r[t.columns.index("Line")].removeprefix("– "): r for r in t.rows}


def test_without_the_section_there_is_no_chapter_and_the_hash_holds():
    base = _collect()
    assert C.GRID_RISK not in base.sections
    assert E.evidence_hash(base) == E.evidence_hash(_collect(grid_risk=None))


def test_the_chapter_has_one_row_per_line_the_island_rows_and_the_upper_bound_total(
    net, tmp_path
):
    ev = _collect(grid_risk=_section(net, _write_campus(tmp_path / "run")))
    sec = ev.sections[C.GRID_RISK]
    assert sec.status == "ok" and sec.kind == "grid_risk"
    table, rows = _rows(ev)
    assert table.columns == ["Line", "t/yr", "EUR/yr", "Status", "Notes", "Source"]
    for key in G.LINE_KEYS:
        assert G.LABELS[key] in rows, key
    for key in ("outside_blocks", "in_block_restart"):
        assert rows[G.LABELS[key]][0].startswith("– "), (
            key
        )  # the island's two rows, marked
    total = rows["Total"]
    assert total[3] == "upper bound" and total[4] == "the lines may overlap"
    # The total row is the payload's sum, in the tab's format.
    s = sum(ln["by_period"]["_"]["t_per_year"] for ln in sec.payload["lines"])
    assert (
        total[1] == C.fmt_tonnes_year(s)
        and f"{NBSP}" in total[1]
        and total[1].endswith("/yr")
    )
    assert total[2].startswith(f"€{NBSP}") and total[2].endswith("/yr")
    # Each line names its source file; the campus lines carry the run's stamp.
    assert G.DIP_TRIPS_CSV in rows[G.LABELS["ride_through"]][5]
    assert G.RESTART_CSV in rows[G.LABELS["island"]][5]
    assert "no inventory credit, so it is conservative" in rows[G.LABELS["island"]][4]
    text = " ".join(ev.required_disclosures)
    assert "upper bound: the lines may overlap" in text and "not a forecast" in text


def test_a_missing_line_reads_missing_with_the_forms_word_never_zero(net):
    ev = _collect(grid_risk=_section(net))  # no campus run
    table, rows = _rows(ev)
    for key in ("ride_through", "island"):
        r = rows[G.LABELS[key]]
        assert (
            r[1] == "Missing: campus study run" and r[2] == "—" and r[3] == "missing"
        ), r
    assert rows["Total"][3] == "upper bound, incomplete"
    assert G.LABELS["outside_blocks"] not in rows  # no island rows without the line
    text = " ".join(ev.required_disclosures)
    assert (
        "incomplete: 2 lines are missing" in text and "A missing line is not 0" in text
    )


def test_no_lost_production_value_reads_unavailable_and_the_tonnes_stand(tmp_path):
    ev = _collect(
        grid_risk=_section(_network(lpv=None), _write_campus(tmp_path / "run"))
    )
    table, rows = _rows(ev)
    for key in G.LINE_KEYS:
        r = rows[G.LABELS[key]]
        assert r[2] == "Unavailable: no lost-production value" and r[1].endswith(
            "/yr"
        ), r
    assert "EUR is unavailable" in " ".join(ev.required_disclosures)


def test_an_out_of_date_line_says_so_never_stale(net, tmp_path):
    campus = _write_campus(tmp_path / "run")
    campus = G.CampusRun(run_dir=campus.run_dir, stale=True, stamp=campus.stamp)
    ev = _collect(grid_risk=_section(net, campus))
    table, rows = _rows(ev)
    assert rows[G.LABELS["island"]][3] == "out of date"
    assert rows[G.LABELS["connection"]][3] == "complete"
    flat = " ".join(" ".join(r) for r in table.rows) + " ".join(ev.required_disclosures)
    assert "stale" not in flat and "out of date" in " ".join(ev.required_disclosures)


def test_two_periods_give_a_period_column_and_a_total_per_period():
    ev = _collect(grid_risk=_section(_network(periods=[2030, 2040])))
    table = ev.tables[C.GRID_RISK]
    assert table.columns[0] == "Period"
    totals = [r for r in table.rows if r[1] == "Total"]
    assert [r[0] for r in totals] == ["2030", "2040"]
    assert "never a horizon total" in " ".join(ev.required_disclosures)


def test_no_section_is_a_not_established_chapter():
    ev = _collect(grid_risk={"available": False, "project_id": "p", "omitted": None})
    assert ev.sections[C.GRID_RISK].status == "not_established"
    doc = assemble.evidence_only_document(ev, title="t", report_id="r", figure_pngs={})
    sec = next(s for s in doc.sections if s.section_id == C.GRID_RISK)
    assert sec.status == "not_established"


def test_the_chapter_is_reliability_after_grid_readiness_and_titled_for_the_docx(
    net, tmp_path
):
    from services.reports import docx_writer
    from tests.test_report_grid_readiness import _scorecard

    ev = _collect(
        grid_risk=_section(net, _write_campus(tmp_path / "run")),
        grid_readiness=_scorecard(),
    )
    doc = assemble.evidence_only_document(ev, title="t", report_id="r", figure_pngs={})
    sec = next(s for s in doc.sections if s.section_id == C.GRID_RISK)
    assert sec.heading == "Grid risk to production" and sec.status == "ok"
    assert any(getattr(b, "table_id", None) == C.GRID_RISK for b in sec.blocks)
    assert assemble.section_category(C.GRID_RISK) == "reliability"
    assert (
        E.SECTION_TITLES[C.GRID_RISK]
        == docx_writer.SECTION_TITLES[C.GRID_RISK]
        == C.TITLES[C.GRID_RISK]
    )


def test_the_words_are_the_tabs():
    """UX 9: the report's words are the Results tab's (`GridRiskTab.tsx`)."""
    text = _TAB.read_text()
    assert f"GRID_RISK_TITLE = '{C.TITLES[C.GRID_RISK]}'" in text
    flags = {k: v.strip("'\"") for k, v in _ts_map(_TAB, "FLAG_WORDS").items()}
    assert flags == C.GRID_RISK_FLAG_WORDS
    places = {k: v.strip("'") for k, v in _ts_map(_TAB, "PLACE_WORDS").items()}
    assert places == C.GRID_RISK_PLACE_WORDS
    for s in (
        "Unavailable:",
        "no lost-production value",
        "upper bound",
        "the lines may overlap",
        "out of date",
        "lower bound",
        "Missing:",
        "'complete'",
        "'partial'",
    ):
        assert s in text, s


@pytest.mark.parametrize(
    "t,want",
    [
        (350.0, f"350{NBSP}t/yr"),
        (1234.0, f"1.23{NBSP}kt/yr"),
        (2.5e6, f"2.5{NBSP}Mt/yr"),
    ],
)
def test_the_tonnes_format_is_the_tabs(t, want):
    assert C.fmt_tonnes_year(t) == want


_MONEY_CASES = json.loads(
    (Path(__file__).resolve().parents[2] / "frontend/src/utils/money.cases.json").read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize("eur,want", _MONEY_CASES)
def test_the_money_format_is_the_one_shared_with_the_tab(eur, want):
    assert C.fmt_eur_short(eur) == want


# ── the route's source: only the open project, only with a process unit ─────


def _project(tmp_path, uuid="p1"):
    return SimpleNamespace(name="g", uuid=uuid, org_id="o", directory=tmp_path)


def _open(monkeypatch, n, uuid="p1"):
    from services.pypsa_service import PyPSAService

    monkeypatch.setattr(
        PyPSAService,
        "get_active_context",
        classmethod(lambda cls: SimpleNamespace(project_uuid=uuid)),
    )
    monkeypatch.setattr(PyPSAService, "get_network", classmethod(lambda cls: n))


def test_the_source_is_the_open_projects_section(monkeypatch, net, tmp_path):
    from routers import reports

    _open(monkeypatch, net)
    out = reports._grid_risk_source(_project(tmp_path), tmp_path)
    assert out["available"] is True and [ln["key"] for ln in out["lines"]] == list(
        G.LINE_KEYS
    )


def test_another_open_project_or_no_process_unit_adds_no_chapter(
    monkeypatch, net, tmp_path
):
    from routers import reports

    _open(monkeypatch, net, uuid="other")
    assert reports._grid_risk_source(_project(tmp_path), tmp_path) is None
    _open(monkeypatch, _network(process=False))
    assert reports._grid_risk_source(_project(tmp_path), tmp_path) is None
