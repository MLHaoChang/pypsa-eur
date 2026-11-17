"""The campus run's voltage-control step (plan GC-02): it runs inside the voltage-stability stage, writes the
PoC P–Q chart, the Q(V) droop's load flow and their scorecard rows, and its failure is shown, never a failed
run."""
from services import campus_electrical_service as ce
from services import project_registry
from tests.test_campus_electrical_service import hub_network, user_and_db  # noqa: F401 - fixtures

SETTINGS = {"k": 1, "invest": False, "profile": "generic_assumed"}


def _hub(user_and_db):  # noqa: F811
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Campus Hub")
    n = hub_network(priced=True)
    n.buses["eh_sk_mva"] = [600.0 if b == "grid" else float("nan") for b in n.buses.index]
    n.export_to_netcdf(str(project_registry.ensure_project_dir(row) / "network.nc"))
    return row


def test_the_step_runs_without_a_new_stage_and_its_rows_read_unknown_without_the_profile_keys(user_and_db):  # noqa: F811
    hub = _hub(user_and_db)
    stages = []
    ce.draft(hub)
    ce.run(hub, SETTINGS, progress=lambda name, done, total: stages.append(name))
    assert "voltage_control" not in stages
    vc = ce.get_state(hub)["results"]["voltage_control"]
    assert "error" not in vc and vc["poc_pq"]
    rows = {r["check"]: r for r in vc["scorecard"]}
    assert set(rows) == {"voltage_control_modes", "q_v_droop", "poc_pq_band"}
    assert rows["q_v_droop"]["status_as_is"] == "unknown"          # generic_assumed states no slope
    assert vc["droop"] == []


def test_a_step_exception_never_fails_the_run_and_leaves_no_file(user_and_db, monkeypatch):  # noqa: F811
    hub = _hub(user_and_db)
    ce.draft(hub)
    real = ce.cs.voltage_control_campus

    def broken(run_dir, *a, **kw):
        real(run_dir, *a, **kw)                  # it wrote its CSVs, then fails: they may not stay
        raise RuntimeError("demo failure")

    monkeypatch.setattr(ce.cs, "voltage_control_campus", broken)
    results = ce.run(hub, SETTINGS)["results"]
    assert results["voltage_control"] == {"error": "demo failure"}
    assert results["transformers"]
    run = ce.campus_dir(hub) / "run"
    for name in (ce.cs.POC_PQ_CSV, ce.cs.VOLTAGE_CONTROL_CSV, ce.cs.VOLTAGE_CONTROL_LF_CSV):
        assert not (run / name).exists()
    monkeypatch.setattr(ce.cs, "voltage_control_campus", real)
    again = ce.run(hub, SETTINGS)["results"]
    assert "error" not in again["voltage_control"] and not (run / ce.VOLTAGE_CONTROL_ERROR).exists()


def test_a_run_from_before_the_step_has_none(user_and_db):  # noqa: F811
    hub = _hub(user_and_db)
    ce.draft(hub)
    ce.run(hub, SETTINGS)
    run = ce.campus_dir(hub) / "run"
    (run / ce.cs.VOLTAGE_CONTROL_CSV).unlink()
    assert ce.get_state(hub)["results"]["voltage_control"] is None
