"""
I9b of the campus island plan: the campus Run runs the island-hours drivers.

Owner, 2026-10-14 (Q2, option 1): inside the campus Run, after
``rank_campus``, whenever the campus has island data:

- ``island_static_campus`` (I4, I5, I5b) when a unit has an island block;
- ``island_frequency_campus`` (I6) when the campus also has island
  requirements and the run has the I3a island tables (the project's
  sidecar), which it reads.

A run without them removes an earlier run's island-hours files, so nothing
stale is read as this run's; a driver's refusal is a 422 with its reason.
The island report then shows the results.
"""
import pytest
import yaml
from fastapi import HTTPException

from gridspine.drivers import campus_study as cs
from gridspine.producers.campus import draft_campus
from gridspine.schema.contracts import ContractError
from gridspine.schema.island import validate_island_config
from services import campus_electrical_service as ce
from services import project_registry
from services.campus_island_report import FREQ_MISSING, STATIC_MISSING, island_report
from tests import test_campus_electrical_service as _ces
from tests.test_campus_island_tables import _sidecar

hub, user_and_db = _ces.hub, _ces.user_and_db          # the campus service's fixtures

STATIC_FILES = (*cs.ISLAND_STATIC_CSV.values(), cs.ISLAND_STATIC_SUMMARY_JSON)
FREQ_FILES = (cs.FREQ_E1_HOURLY_CSV, cs.FREQ_EVENTS_CSV, cs.FREQ_LOSSES_CSV, cs.FREQ_BRIDGE_CSV,
              cs.FREQ_PLANNED_CSV, cs.FREQ_UPS_CSV, cs.FREQ_SUMMARY_JSON,
              cs.FREQ_CUTS_CSV)                  # I7: the cuts the frequency check proposes


def _island_campus(project):
    """The project's campus drafted with its sidecar's island data (I1)."""
    import json

    sidecar = json.loads((project_registry.project_dir(project) / "island_config.json").read_text())
    n = cs.load_solved_network(ce._network(project))
    spec = draft_campus(n, island=validate_island_config(sidecar)).spec
    ce.save_campus(project, yaml.safe_dump(spec, sort_keys=False))


def _run(project):
    ce.run(project, {"k": 1, "invest": False})
    return ce.campus_dir(project) / "run"


def test_a_campus_without_island_data_runs_neither_driver_and_loses_stale_files(hub, monkeypatch):
    ce.draft(hub)
    run = ce.campus_dir(hub) / "run"
    run.mkdir(parents=True, exist_ok=True)
    for name in (*STATIC_FILES, *FREQ_FILES):
        (run / name).write_text("stale")
    monkeypatch.setattr(ce.cs, "island_static_campus", lambda *a: pytest.fail("no island data"))
    monkeypatch.setattr(ce.cs, "island_frequency_campus", lambda *a: pytest.fail("no island data"))
    _run(hub)
    assert [n for n in (*STATIC_FILES, *FREQ_FILES) if (run / n).exists()] == []


def test_a_campus_with_island_data_and_a_sidecar_runs_both_drivers_after_ranking(hub, monkeypatch):
    _sidecar(hub)
    _island_campus(hub)
    calls = []
    for name in ("rank_campus", "island_static_campus", "island_frequency_campus", "cuts_campus",
                 "size_campus"):
        real = getattr(ce.cs, name)
        monkeypatch.setattr(ce.cs, name, lambda *a, _n=name, _r=real, **k: calls.append(_n) or _r(*a, **k))
    run = _run(hub)
    assert calls == ["rank_campus", "island_static_campus", "island_frequency_campus", "cuts_campus",
                     "size_campus"]
    assert all((run / n).is_file() for n in (*STATIC_FILES, *FREQ_FILES))
    report = island_report(hub)
    ids = {t["id"] for t in report["tables"]}
    assert {"static_summary", "static_flow", "freq_summary", "freq_events"} <= ids
    assert STATIC_MISSING not in report["missing"] and FREQ_MISSING not in report["missing"]


def test_without_the_island_tables_the_frequency_check_is_skipped(hub, monkeypatch):
    """Island data in the campus but no sidecar: no I3a tables, so I6 has
    nothing to read; I4–I5b still run."""
    _sidecar(hub)
    _island_campus(hub)
    (project_registry.project_dir(hub) / "island_config.json").unlink()
    monkeypatch.setattr(ce.cs, "island_frequency_campus", lambda *a: pytest.fail("no island tables"))
    monkeypatch.setattr(ce.cs, "cuts_campus", lambda *a: pytest.fail("no frequency check, so no cuts"))
    run = _run(hub)
    assert all((run / n).is_file() for n in STATIC_FILES)
    assert not any((run / n).exists() for n in FREQ_FILES)
    report = island_report(hub)
    assert FREQ_MISSING in report["missing"] and "island_config.json" in report["missing"]
    # the campus's own island data stands without the sidecar: its results show
    assert "static_flow" in {t["id"] for t in report["tables"]}


def test_a_driver_refusal_is_a_422_with_its_reason(hub, monkeypatch):
    _sidecar(hub)
    _island_campus(hub)

    def refuse(run_dir):
        raise ContractError("the island has no grid-forming unit")

    monkeypatch.setattr(ce.cs, "island_static_campus", refuse)
    with pytest.raises(HTTPException) as exc:
        _run(hub)
    assert exc.value.status_code == 422 and "grid-forming" in exc.value.detail


def test_without_island_requirements_the_frequency_check_is_skipped(hub, monkeypatch):
    """Unit blocks but no campus-level island requirements: I4–I5b run; I6,
    which holds the island to them, does not."""
    _sidecar(hub)
    _island_campus(hub)
    spec = yaml.safe_load((ce.campus_dir(hub) / ce.CAMPUS_FILE).read_text())
    del spec["campus"]["island"]
    ce.save_campus(hub, yaml.safe_dump(spec, sort_keys=False))
    monkeypatch.setattr(ce.cs, "island_frequency_campus", lambda *a: pytest.fail("no island requirements"))
    run = _run(hub)
    assert all((run / n).is_file() for n in STATIC_FILES)
    assert not any((run / n).exists() for n in FREQ_FILES)


# ── Draft passes the sidecar (owner, 2026-10-14, Q3 option 1) ─────────────


def test_a_drafted_island_project_gets_the_island_hours_checks_on_run(hub):
    _sidecar(hub)
    out = ce.draft(hub)
    spec = yaml.safe_load(out["campus_yaml"])["campus"]
    assert "island" in spec                                   # the sidecar's requirements
    assert any("island" in u for u in spec["units"].values())
    run = _run(hub)
    assert all((run / n).is_file() for n in (*STATIC_FILES, *FREQ_FILES))


def test_a_draft_without_a_sidecar_is_byte_identical(hub):
    n = cs.load_solved_network(ce._network(hub))
    out = ce.draft(hub)
    assert out["campus_yaml"] == yaml.safe_dump(draft_campus(n).spec, sort_keys=False)
    assert (ce.campus_dir(hub) / ce.CAMPUS_FILE).read_text() == out["campus_yaml"]


def test_a_sidecar_that_does_not_validate_refuses_the_draft_with_its_reason(hub):
    _sidecar(hub, scenarios=["ups_bridged"])                  # ups_bridged needs pickup_blocks
    with pytest.raises(HTTPException) as exc:
        ce.draft(hub)
    assert exc.value.status_code == 422 and "pickup_blocks" in exc.value.detail
    assert not (ce.campus_dir(hub) / ce.CAMPUS_FILE).exists()
