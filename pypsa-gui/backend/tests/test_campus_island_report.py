"""
I9a of the campus island plan: the island report.

``island_report(project)`` is the payload the panel and the bundle show: a
list of tables, each with its title, the model behind it, the screening
caveat, its columns and rows, and which rows rest on ``assumed`` values.
The plan's rules (I9): every table states that the study is a screening,
not a certificate; it names the model behind each verdict; every
``assumed`` row is marked.

The golden fixture pins the whole payload for the I2c hand case, so a
change to any table is seen in review; regenerate it with
``ISLAND_REPORT_GOLDEN=write``.
"""
import json
import os
import pathlib

import pytest

from services import campus_electrical_service as ce
from services.campus_island_report import CAVEAT, island_report
from tests.test_campus_island_service import _config, project, user_and_db  # noqa: F401

GOLDEN = pathlib.Path(__file__).parent / "fixtures" / "island_report_golden.json"


def _tables(report):
    return {t["id"]: t for t in report["tables"]}


def test_a_project_without_a_sidecar_reports_that_and_no_tables(project):  # noqa: F811
    report = island_report(project)
    assert report["tables"] == []
    assert "island_config.json" in report["missing"]


def test_every_table_states_the_caveat_and_names_its_model(project):  # noqa: F811
    ce.save_island(project, _config())
    ce.run_island_study(project)
    report = island_report(project)
    assert report["tables"]
    for table in report["tables"]:
        assert table["caveat"] == CAVEAT, table["id"]
        assert "not a certificate" in table["caveat"]
        assert table["model"], table["id"]
        assert all(len(row) == len(table["columns"]) for row in table["rows"]), table["id"]


def test_assumed_rows_are_marked(project):  # noqa: F811
    """Every requirement in the hand config is tagged assumed; one changed to
    datasheet is not marked."""
    config = _config()
    config["requirements"]["sustained_h"] = {"value": 1.0, "source": "datasheet"}
    ce.save_island(project, config)
    req = _tables(island_report(project))["requirements"]
    fields = [row[0] for row in req["rows"]]
    assumed = {fields[i] for i in req["assumed_rows"]}
    assert "sustained_h" in fields and "sustained_h" not in assumed
    assert "bridge_s" in assumed


def test_the_costs_come_from_the_stored_study(project):  # noqa: F811
    ce.save_island(project, _config())
    ce.run_island_study(project)
    costs = _tables(island_report(project))["costs"]
    by_scenario = {row[0]: row for row in costs["rows"]}
    cost_col = costs["columns"].index("cost of island capability")
    assert by_scenario["seamless"][cost_col] == pytest.approx(1000.0, abs=1e-4)
    assert by_scenario["ups_bridged"][cost_col] == pytest.approx(0.0, abs=1e-4)
    assert "hub LP" in costs["model"]


def test_without_a_study_the_report_says_it_has_not_run(project):  # noqa: F811
    ce.save_island(project, _config())
    report = island_report(project)
    assert "costs" not in _tables(report)
    # The missing item names the cost-of-islanding study (I2c), not "island
    # study": the campus run's own island checks (I4-I6) are listed apart, so
    # a reader must not take this line to mean they did not run.
    assert "cost of islanding study" in report["missing"]
    assert "island study" not in report["missing"]


def test_the_report_matches_the_golden_fixture(project):  # noqa: F811
    ce.save_island(project, _config())
    ce.run_island_study(project)
    report = json.loads(json.dumps(island_report(project)))
    if os.environ.get("ISLAND_REPORT_GOLDEN") == "write":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    golden = json.loads(GOLDEN.read_text())
    assert _rounded(report) == _rounded(golden)


def _rounded(value):
    """Floats to 6 significant digits, so a solver's last bits do not churn
    the fixture."""
    if isinstance(value, float):
        return float(f"{value:.6g}")
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v) for v in value]
    return value


def test_the_report_route_returns_the_payload(client, _auth_db, seeded_identity, monkeypatch):
    from db.models import Project, User
    from services import campus_island_report, project_registry

    _engine, session_local = _auth_db
    with session_local() as db:
        project_registry.create_root(db, db.get(User, seeded_identity["user_id"]), "Report Hub")
    calls = []
    monkeypatch.setattr(campus_island_report, "island_report",
                        lambda p: calls.append(p) or {"tables": []})
    resp = client.get("/api/campus-electrical/Report Hub/island/report")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"tables": []}
    assert isinstance(calls[0], Project) and calls[0].name == "Report Hub"
