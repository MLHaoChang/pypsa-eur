"""DC grid readiness 02, PR B: the report's grid-readiness chapter.

The scorecard becomes one chapter with a code-rendered table and its
disclosures (advisory; before UPS/BESS smoothing; EMT needed; the assumed
limits by name). It is present only for a hub project with a saved network,
so every other report keeps its evidence hash; a scorecard that cannot be
read is a chapter stated not established; and the screening alone is never
"something measured".
"""
import pathlib
import sys

from services.reports import assemble, chapters, evidence as E

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402


def _scorecard(envelope=True):
    return {
        "schema": 1, "profile": {"id": "eu_rfg_dcc_ce", "title": "EU RfG and DCC", "error": None},
        "template_id": "eh_datacenter", "envelope": envelope, "basis": "advisory",
        "summary": {"pass": 1, "warn": 0, "fail": 1, "unknown": 2, "assumed": 2, "red_flags": 1},
        "rows": [
            {"id": "ramp_rate", "requirement": "Load ramp rate at the PoC", "limit": 4.0, "unit": "MW/min",
             "source": "template ledger (assumed)", "project_value": 1404.0, "verdict": "fail",
             "assumed": True, "note": "n", "section": "connection", "target": {}},
            {"id": "scr", "requirement": "Short-circuit ratio at the PoC", "limit": 3.0, "unit": "SCR",
             "source": "EH product rule", "project_value": 8.333, "verdict": "pass", "assumed": False,
             "note": "n", "section": "connection", "target": {}},
            {"id": "lole", "requirement": "LOLE within the goal", "limit": 3.0, "unit": "h/yr",
             "source": "ledger", "project_value": {"lole_h_per_year": 1.2, "horizon_years": 1.0},
             "verdict": "unknown", "assumed": True, "note": "n", "section": "scenarios", "target": {}},
            {"id": "dynamic:frt", "requirement": "Fault ride-through", "limit": "TSO profile", "unit": None,
             "source": "DCC", "project_value": None, "verdict": "unknown", "assumed": False, "note": "n",
             "section": "assets", "target": {}},
        ],
    }


def _collect(**kw):
    return E.collect_evidence(study_report=None, eh_report=None, worksheet=None, **kw)


def test_without_the_scorecard_there_is_no_chapter_and_the_hash_holds():
    base = _collect()
    assert chapters.GRID_READINESS not in base.sections
    assert E.evidence_hash(base) == E.evidence_hash(_collect(grid_readiness=None))


def test_the_chapter_has_its_table_and_disclosures():
    ev = _collect(grid_readiness=_scorecard())
    sec = ev.sections[chapters.GRID_READINESS]
    assert sec.status == "ok" and sec.kind == "grid_readiness"
    table = ev.tables[chapters.GRID_READINESS]
    assert table.columns == ["Requirement", "Limit", "Project", "Verdict", "Assumed", "Source"]
    assert table.rows[0][:5] == ["Load ramp rate at the PoC", "4 MW/min", "1404 MW/min", "Fail", "assumed"]
    assert table.rows[2][2] == "1.2 h/yr"                              # LOLE: the annual figure
    text = " ".join(ev.required_disclosures)
    assert "advisory screening" in text and "never a pass" in text
    assert "before UPS/BESS smoothing" in text
    assert "dynamic (EMT) study" in text
    assert "Load ramp rate at the PoC; LOLE within the goal" in text
    assert "1 red flag(s), 2 unknown, 2 on an assumed limit" in text


def test_without_an_envelope_the_smoothing_disclosure_is_not_made():
    ev = _collect(grid_readiness=_scorecard(envelope=False))
    assert not any("smoothing" in d for d in ev.required_disclosures)


def test_an_unreadable_scorecard_is_a_not_established_chapter():
    ev = _collect(grid_readiness={})
    assert ev.sections[chapters.GRID_READINESS].status == "not_established"
    doc = assemble.evidence_only_document(ev, title="t", report_id="r", figure_pngs={})
    sec = next(s for s in doc.sections if s.section_id == chapters.GRID_READINESS)
    assert sec.status == "not_established"


def test_the_screening_alone_is_not_something_measured():
    ev = _collect(grid_readiness=_scorecard())
    assert ev.required_disclosures[0] == E.NOTHING_MEASURED_DISCLOSURE


def test_the_chapter_is_in_the_grid_plan_category_and_titled_for_the_docx():
    from services.reports import docx_writer

    ev = _collect(grid_readiness=_scorecard())
    doc = assemble.evidence_only_document(ev, title="t", report_id="r", figure_pngs={})
    sec = next(s for s in doc.sections if s.section_id == chapters.GRID_READINESS)
    assert sec.heading == "Grid readiness (screening)" and sec.status == "ok"
    assert any(getattr(b, "table_id", None) == chapters.GRID_READINESS for b in sec.blocks)
    assert assemble.section_category(chapters.GRID_READINESS) == "grid_plan"
    assert chapters.GRID_READINESS in E.SECTION_TITLES
    assert chapters.GRID_READINESS in docx_writer.SECTION_TITLES


# ── through the route ───────────────────────────────────────────────────────


def _post(client, name):
    r = client.post(f"/api/projects/{name}/reports", json={"mode": "evidence_only", "title": "Demo"})
    assert r.status_code == 200, r.text[:400]
    return r.json()


def test_the_route_adds_the_chapter_for_a_hub_project(client, api_project, project_storage_dir):
    name = api_project("rep-gr")
    d = project_storage_dir(name)
    T.BUILDERS["eh_datacenter"]().export_to_netcdf(str(d / "network.nc"))
    T.write_sidecars(d, "eh_datacenter")
    by_id = {s["section_id"]: s for s in _post(client, name)["document"]["sections"]}
    gr = by_id[chapters.GRID_READINESS]
    assert gr["status"] == "ok"
    assert any(b["type"] == "table_ref" and b["table_id"] == chapters.GRID_READINESS for b in gr["blocks"])


def test_a_project_without_the_template_keeps_its_report(client, api_project):
    name = api_project("rep-gr-none")
    ids = [s["section_id"] for s in _post(client, name)["document"]["sections"]]
    assert chapters.GRID_READINESS not in ids
