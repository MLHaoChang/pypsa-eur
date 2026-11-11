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
        "incomplete, 2 lines are missing" in text and "A missing line is not 0" in text
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
    (
        Path(__file__).resolve().parents[2] / "frontend/src/utils/money.cases.json"
    ).read_text(encoding="utf-8")
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


# --- G2P #22: one sub-row per N-1 site transformer --------------------------------------------------------------


def test_each_n_1_site_transformer_is_a_sub_row_under_the_line_in_the_lines_order(
    monkeypatch,
):
    from tests.test_grid_risk_production import _fake_loss, _prod, _two_trafos

    _fake_loss(
        monkeypatch,
        {
            "site_trafo": {"_": ([_prod(2.0, 200.0)], None)},
            "site_trafo_b": {"_": ([_prod(1.0, None)], "lower")},
        },
    )
    ev = _collect(grid_risk=_section(_two_trafos()))
    table = ev.tables[C.GRID_RISK]
    labels = [r[0] for r in table.rows]
    i = labels.index(G.LABELS["n_1"])
    assert labels[i + 1 : i + 3] == [
        "– site_trafo out of service",
        "– site_trafo_b out of service",
    ]
    a, b = table.rows[i + 1], table.rows[i + 2]
    assert a[1] == C.fmt_tonnes_year(2.0) and a[2] == f"{C.fmt_eur_short(200.0)}/yr"
    assert (
        b[1] == C.fmt_tonnes_year(1.0)
        and b[2] == "Unavailable: no lost-production value"
    )
    assert (
        "lower bound" in b[4] and "lower bound" not in a[4]
    )  # the row's own period flags
    assert a[3] == a[5] == ""  # status and source stay on the line


def test_a_missing_n_1_line_has_no_sub_rows():
    ev = _collect(grid_risk=_section(_network(trafo=False)))
    table, rows = _rows(ev)
    assert rows[G.LABELS["n_1"]][3] == "missing"
    assert not [r for r in table.rows if r[0].endswith("out of service")]


def test_a_sub_row_bounded_by_the_outage_length_never_also_says_units_are_unknown(
    monkeypatch,
):
    from tests.test_grid_risk_production import _fake_loss, _prod, _two_trafos

    _fake_loss(
        monkeypatch,
        {
            "site_trafo": {"_": ([_prod(2.0, 200.0)], "lower")},
            "site_trafo_b": {"_": ([_prod(1.0, 1.0)], None)},
        },
    )
    sec = _section(_two_trafos())
    n1 = next(ln for ln in sec["lines"] if ln["key"] == "n_1")
    n1["rows"][0]["by_period"]["_"]["flags"].insert(
        0, "outage_longer_than_period"
    )  # the screen's reason
    ev = _collect(grid_risk=sec)
    row = next(
        r for r in ev.tables[C.GRID_RISK].rows if r[0] == "– site_trafo out of service"
    )
    assert "the outage is longer than the period" in row[4]
    assert "some units are not known" not in row[4]
    assert C._sub_row_flags(["lower_bound"]) == [
        "lower_bound"
    ]  # alone, it is still said


def _lower_bound_n_1(monkeypatch):
    from tests.test_grid_risk_production import _fake_loss, _prod, _two_trafos

    _fake_loss(
        monkeypatch,
        {
            "site_trafo": {"_": ([_prod(2.0, 200.0)], "lower")},
            "site_trafo_b": {"_": ([_prod(1.0, 1.0)], None)},
        },
    )
    return _section(_two_trafos())


def test_the_n_1_line_bounded_by_the_outage_length_says_so_in_its_own_notes(
    monkeypatch,
):
    """G2P-13: the line's Notes and the disclosure give the true reason, never "some units are not known"."""
    ev = _collect(grid_risk=_lower_bound_n_1(monkeypatch))
    _, rows = _rows(ev)
    line = rows[G.LABELS["n_1"]]
    assert line[3] == "partial"
    assert line[4] == (
        "no inventory credit, so it is conservative; "
        "the outage is longer than the period, so this is a lower bound"
    )
    text = " ".join(ev.required_disclosures)
    assert (
        f"Grid risk to production: lower bound for {G.LABELS['n_1']} "
        "(the outage is longer than the period)." in text
    )
    assert "some units are not known" not in text


def test_a_lower_bound_from_unknown_units_still_says_so():
    line = {
        "label": "Ride-through",
        "status": "partial",
        "stale": False,
        "flags": ["lower_bound"],
        "by_period": {},
    }
    n_1 = {
        **line,
        "label": "N-1",
        "flags": ["outage_longer_than_period", "lower_bound"],
    }
    out = C.grid_risk_disclosures({"lines": [line, n_1], "periods": ["_"]})
    assert out[1] == (
        "Grid risk to production: lower bound for Ride-through (some units are not known) · "
        "N-1 (the outage is longer than the period)."
    )


def test_a_lower_bound_line_without_that_reason_keeps_the_generic_words(net, tmp_path):
    """GK A N28-4: only an outage longer than the period on the same line drops the generic words."""
    sec = _section(net, _write_campus(tmp_path / "run"))
    ride = next(ln for ln in sec["lines"] if ln["key"] == "ride_through")
    ride["flags"], ride["status"] = ["lower_bound"], "partial"
    ev = _collect(grid_risk=sec)
    row = _rows(ev)[1][G.LABELS["ride_through"]]
    assert row[4] == "some units are not known, so this is a lower bound"
    assert f"{G.LABELS['ride_through']} (some units are not known)" in " ".join(
        ev.required_disclosures
    )


def test_a_stamp_with_no_words_leaves_no_empty_brackets_in_the_source(net):
    """G2P-13: an unknown stamp key gives no words, so the Source is the file alone, never "file ()"."""
    sec = _section(net)
    n1 = next(ln for ln in sec["lines"] if ln["key"] == "n_1")
    n1["stamp"] = {"solver": "highs"}
    conn = next(ln for ln in sec["lines"] if ln["key"] == "connection")
    conn["stamp"] = {"revision": 7}
    _, rows = _rows(_collect(grid_risk=sec))
    assert rows[G.LABELS["n_1"]][5] == ", ".join(n1["source_files"])
    assert rows[G.LABELS["connection"]][5] == (
        ", ".join(conn["source_files"]) + " (revision 7)"
    )
    assert not [r for r in _rows(_collect(grid_risk=sec))[0].rows if "()" in r[-1]]


def test_the_disclosures_join_with_a_colon_never_an_em_dash(net, tmp_path):
    """G2P-13 (docs/agents/review-ux.md): a colon, not an em dash, joins clauses."""
    campus = _write_campus(tmp_path / "run")
    stale = G.CampusRun(run_dir=campus.run_dir, stale=True, stamp=campus.stamp)
    two = _network(periods=[2030, 2040])
    lines = [
        d
        for sec in (
            _section(net),  # missing lines
            _section(net, stale),  # out of date
            _section(two),  # two periods
            _section(_network(lpv=None), campus),  # EUR unavailable
        )
        for d in _collect(grid_risk=sec).required_disclosures
        if d.startswith("Grid risk to production")
    ]
    flat = " ".join(lines)
    for kind in (
        "each grid risk",
        "incomplete",
        "out of date",
        "EUR is unavailable",
        "one row set per period",
    ):
        assert kind in flat, kind
    assert all(d.startswith("Grid risk to production: ") for d in lines)
    assert not [d for d in lines if "—" in d]
