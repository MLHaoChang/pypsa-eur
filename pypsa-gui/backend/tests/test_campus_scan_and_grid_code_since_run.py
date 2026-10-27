"""The campus run's harmonic-scan stage and the grid code it was held to (DC-SURFACE-1, SCORECARD-STALE-1).

The run keeps the profile it used and its fingerprint (``campus_profile_used.json``), so the results and the
scorecard can say when the code changed since; and it runs the harmonic impedance scan after sizing, whose
failure is shown, never a failed run.
"""
import json

import pytest
import yaml

from services import campus_electrical_service as ce
from services.adequacy import grid_readiness as G
from tests.test_campus_electrical_service import hub, user_and_db  # noqa: F401 - fixtures

BASE = "generic_assumed"
SETTINGS = {"k": 1, "invest": False}


def _publish(project, profile_id="pq_test", **over):
    """Write a published profile (the shipped generic one with ``over``) straight into the project's grid codes."""
    from gridspine.drivers.campus_study import load_grid_code

    p = load_grid_code(BASE, raw=True)
    p.pop("name", None)
    p.update(over)
    ce.grid_codes_dir(project).mkdir(parents=True, exist_ok=True)
    (ce.grid_codes_dir(project) / f"{profile_id}.yaml").write_text(yaml.safe_dump(p, sort_keys=False))
    return p


CLEARANCE = {"value": 0.5, "clause": "Test code §1", "source": "code"}


def _run(project, profile="pq_test"):
    ce.draft(project)
    return ce.run(project, {**SETTINGS, "profile": profile})


# ── the scan stage ───────────────────────────────────────────────────────────

def test_the_run_scans_after_sizing_and_the_state_reads_the_scan_back(hub):  # noqa: F811
    _publish(hub, harmonic_resonance_clearance_h=CLEARANCE)
    stages = []
    ce.draft(hub)
    ce.run(hub, {**SETTINGS, "profile": "pq_test"}, progress=lambda name, done, total: stages.append(name))
    assert stages.index("harmonic_scan") == stages.index("size") + 1
    scan = ce.get_state(hub)["results"]["harmonic_scan"]
    assert scan["limit_h"] == 0.5 and scan["limit_source"] == "code" and scan["clause"] == "Test code §1"
    assert scan["envelope"] and set(scan["envelope"][0]) == {"h", "z_max_ohm", "x_min_ohm", "x_max_ohm"}
    assert isinstance(scan["resonance"], list)
    assert "screening" in scan["label"]
    # The scan keeps IEC 60909's c factor on the grid's source: the basis says so, the app never overrides S_k.
    assert any("c = 1.1 in the max case" in line for line in scan["basis"])
    run = ce.campus_dir(hub) / "run"
    assert not (run / ce.HARMONIC_SCAN_ERROR).exists()


def test_without_a_clearance_in_the_code_the_scan_takes_the_assumed_default(hub):  # noqa: F811
    _publish(hub)
    _run(hub)
    scan = ce.get_state(hub)["results"]["harmonic_scan"]
    assert scan["limit_source"] is None and scan["clause"] is None
    from gridspine.static import harmonic_scan as hs
    assert scan["limit_h"] == hs.DEFAULT_CLEARANCE_H
    assert all(r["assumed"] for r in scan["resonance"])


def test_a_scan_error_never_fails_the_run_leaves_no_scan_file_and_clears_on_success(hub, monkeypatch):  # noqa: F811
    _publish(hub, harmonic_resonance_clearance_h=CLEARANCE)
    good = _run(hub)["results"]
    run = ce.campus_dir(hub) / "run"
    assert (run / ce.cs.HARMONIC_RESONANCE_CSV).is_file()

    real = ce.cs.harmonic_scan_campus

    def broken(run_dir, *a, **kw):
        real(run_dir, *a, **kw)                  # it wrote its CSVs, then fails: none of them may stay
        raise ce.ContractError("harmonic scan: test failure")

    monkeypatch.setattr(ce.cs, "harmonic_scan_campus", broken)
    failed = ce.run(hub, {**SETTINGS, "profile": "pq_test"})["results"]
    assert failed["harmonic_scan"] == {"error": "harmonic scan: test failure"}
    for name in (ce.cs.HARMONIC_SCAN_CSV, ce.cs.HARMONIC_ENVELOPE_CSV, ce.cs.HARMONIC_RESONANCE_CSV,
                 ce.HARMONIC_SCAN_META):
        assert not (run / name).exists()
    # The sizing outputs are the same with the scan or without it.
    for key in ("selection", "transformers", "compensation", "short_circuit", "compliance", "reactive",
                "requirement", "reported_requirements"):
        assert failed[key] == good[key], key

    monkeypatch.setattr(ce.cs, "harmonic_scan_campus", real)
    again = ce.run(hub, {**SETTINGS, "profile": "pq_test"})["results"]
    assert "error" not in again["harmonic_scan"] and not (run / ce.HARMONIC_SCAN_ERROR).exists()


def test_a_run_from_before_the_scan_stage_has_no_scan(hub):  # noqa: F811
    _publish(hub)
    _run(hub)
    run = ce.campus_dir(hub) / "run"
    for name in (ce.cs.HARMONIC_SCAN_CSV, ce.cs.HARMONIC_ENVELOPE_CSV, ce.cs.HARMONIC_RESONANCE_CSV,
                 ce.HARMONIC_SCAN_META):
        (run / name).unlink()
    assert ce.get_state(hub)["results"]["harmonic_scan"] is None


# ── the grid code since the run ──────────────────────────────────────────────

def test_the_run_keeps_its_profile_and_nothing_has_changed_after_it(hub):  # noqa: F811
    _publish(hub)
    _run(hub)
    used = json.loads((ce.campus_dir(hub) / "run" / ce.PROFILE_USED_FILE).read_text())
    assert used == {"profile": "pq_test", "sha256": ce.profile_fingerprint(hub, "pq_test")}
    assert ce.get_state(hub)["grid_code_changed"] is False
    since = G.grid_readiness_for(hub)["profile"]["since_run"]
    assert since == {"run_profile": "pq_test", "run_profile_title": ce.profiles(hub)["pq_test"], "status": "same"}


@pytest.mark.parametrize("inert", [
    lambda p: p.update(title="Renamed"),
    lambda p: p.update(notes="a note added after the run"),
    lambda p: p["q_range_demand"].update(quote="other wording", page=9),
])
def test_republishing_with_only_inert_edits_is_not_a_changed_code(hub, inert):  # noqa: F811
    from gridspine.drivers.campus_study import load_grid_code

    # A page and a quote need the document they cite.
    quoted = {**load_grid_code(BASE, raw=True)["q_range_demand"], "source": "code", "page": 2,
              "quote": "the reactive range"}
    p = _publish(hub, document={"sha256": "a" * 64, "filename": "code.pdf", "title": "A grid code"},
                 q_range_demand=quoted)
    _run(hub)
    inert(p)
    (ce.grid_codes_dir(hub) / "pq_test.yaml").write_text(yaml.safe_dump(p, sort_keys=False))
    assert ce.get_state(hub)["grid_code_changed"] is False
    assert G.grid_readiness_for(hub)["profile"]["since_run"]["status"] == "same"


@pytest.mark.parametrize("edit", [
    lambda p: p["q_range_demand"].update(value=p["q_range_demand"]["value"] + 0.05),
    lambda p: p["q_range_demand"].update(clause="Test code §2"),
    lambda p: p["q_range_demand"].update(source="code"),
    lambda p: p.update(unbalance_limit_pct={"value": 2.0, "clause": "Test code §3", "source": "code"}),
])
def test_republishing_with_another_limit_flags_the_run(hub, edit):  # noqa: F811
    p = _publish(hub)
    _run(hub)
    edit(p)
    (ce.grid_codes_dir(hub) / "pq_test.yaml").write_text(yaml.safe_dump(p, sort_keys=False))
    assert ce.get_state(hub)["grid_code_changed"] is True
    assert G.grid_readiness_for(hub)["profile"]["since_run"]["status"] == "changed"


def test_a_deleted_profile_is_a_changed_code(hub):  # noqa: F811
    _publish(hub)
    _run(hub)
    _publish(hub, profile_id="other")
    (ce.grid_codes_dir(hub) / "pq_test.yaml").unlink()
    assert ce.get_state(hub)["grid_code_changed"] is True


def test_screening_against_another_profile_than_the_runs_says_so(hub):  # noqa: F811
    _publish(hub)
    _run(hub)
    since = G.grid_readiness_for(hub, BASE)["profile"]["since_run"]
    assert since["status"] == "other_profile" and since["run_profile"] == "pq_test"
    # The run's own results are not stale for that: their code is unchanged.
    assert ce.get_state(hub)["grid_code_changed"] is False


def test_a_run_from_before_the_profile_was_kept_gives_no_notice(hub):  # noqa: F811
    _publish(hub)
    _run(hub)
    (ce.campus_dir(hub) / "run" / ce.PROFILE_USED_FILE).unlink()
    assert ce.get_state(hub)["grid_code_changed"] is False
    assert G.grid_readiness_for(hub)["profile"]["since_run"] is None


@pytest.mark.parametrize("content", ["{not json", '{"profile": "pq_test", "sha256": null}', "[]"])
def test_an_unreadable_record_of_the_runs_profile_gives_no_notice_and_keeps_the_scorecard(hub, content):  # noqa: F811
    _publish(hub)
    _run(hub)
    (ce.campus_dir(hub) / "run" / ce.PROFILE_USED_FILE).write_text(content)
    assert ce.get_state(hub)["grid_code_changed"] is False
    gr = G.grid_readiness_for(hub)
    assert gr["profile"]["since_run"] is None and gr["rows"]


def test_a_project_without_a_campus_run_gives_no_notice_and_makes_no_campus_directory(hub):  # noqa: F811
    from services import project_registry

    assert G.grid_readiness_for(hub)["profile"]["since_run"] is None
    assert not (project_registry.project_dir(hub) / ce.SUBDIR / "run").exists()
