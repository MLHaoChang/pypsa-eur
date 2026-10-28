"""The campus run's P–V/Q–V stage (study harness issue 18): it runs after the harmonic scan, writes its margins
and meta, and its failure (no S_k''min entered among them) is shown, never a failed run; the scorecard reads it
back as the two voltage-stability rows."""
import json

import pytest

from services import campus_electrical_service as ce
from services import project_registry
from services.adequacy import grid_readiness as G
from tests.test_campus_electrical_service import hub_network, user_and_db  # noqa: F401 - fixtures

SETTINGS = {"k": 1, "invest": False, "profile": "generic_assumed"}
IDS = ("voltage_stability_pv", "voltage_stability_qv")


def _hub(user_and_db, sk=600.0):  # noqa: F811
    """The service tests' hub with the PoC's fault level ``sk`` (None: none entered)."""
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Campus Hub")
    n = hub_network(priced=True)
    n.buses["eh_sk_mva"] = [(float("nan") if sk is None else sk) if b == "grid" else float("nan")
                            for b in n.buses.index]
    n.export_to_netcdf(str(project_registry.ensure_project_dir(row) / "network.nc"))
    return row


def _rows(project):
    return {r["id"]: r for r in G.grid_readiness_for(project)["rows"]}


def test_the_stage_runs_after_the_scan_and_the_scorecard_reads_it(user_and_db):  # noqa: F811
    hub = _hub(user_and_db, sk=600.0)
    stages = []
    ce.draft(hub)
    ce.run(hub, SETTINGS, progress=lambda name, done, total: stages.append(name))
    assert stages.index("voltage_stability") == stages.index("harmonic_scan") + 1
    vs = ce.get_state(hub)["results"]["voltage_stability"]
    assert "error" not in vs and vs["rows"] and {r["verdict"] for r in vs["rows"]} == {"solved"}
    assert vs["basis"] == "screening; binding answer is the RMS study"
    assert any("c_min" in line for line in vs["ledger"])
    run = ce.campus_dir(hub) / "run"
    assert (run / ce.cs.VOLTAGE_STABILITY_CSV).is_file() and (run / ce.VOLTAGE_STABILITY_META).is_file()
    rows = _rows(hub)
    for rid in IDS:
        assert rows[rid]["verdict"] == "pass" and rows[rid]["project_value"] is not None
        assert rows[rid]["limit"] is not None and rows[rid]["source"]


def test_no_sk_min_entered_writes_the_error_and_the_run_succeeds(user_and_db):  # noqa: F811
    hub = _hub(user_and_db, sk=None)            # no fault level: the draft takes the typical one, not the user's
    ce.draft(hub)
    results = ce.run(hub, SETTINGS)["results"]
    assert results["voltage_stability"] == {"error": ce.cs.NO_SK_MIN}
    assert results["transformers"]                               # the sizing is all there
    run = ce.campus_dir(hub) / "run"
    assert not (run / ce.cs.VOLTAGE_STABILITY_CSV).exists() and not (run / ce.VOLTAGE_STABILITY_META).exists()
    for rid in IDS:
        r = _rows(hub)[rid]
        assert r["verdict"] == "unknown" and r["note"].startswith("No minimum short-circuit power entered at the PCC")


def test_a_stage_exception_never_fails_the_run_and_clears_on_success(user_and_db, monkeypatch):  # noqa: F811
    hub = _hub(user_and_db, sk=600.0)
    ce.draft(hub)
    good = ce.run(hub, SETTINGS)["results"]
    real = ce.cs.voltage_stability_campus

    def broken(run_dir, *a, **kw):
        real(run_dir, *a, **kw)                  # it wrote its CSV, then fails: it may not stay
        raise RuntimeError("demo failure")

    monkeypatch.setattr(ce.cs, "voltage_stability_campus", broken)
    failed = ce.run(hub, SETTINGS)["results"]
    assert failed["voltage_stability"] == {"error": "demo failure"}
    run = ce.campus_dir(hub) / "run"
    assert not (run / ce.cs.VOLTAGE_STABILITY_CSV).exists()
    for key in ("selection", "transformers", "compensation", "short_circuit", "harmonic_scan"):
        assert failed[key] == good[key], key
    for rid in IDS:
        r = _rows(hub)[rid]
        assert r["verdict"] == "unknown" and "demo failure" in r["note"]

    monkeypatch.setattr(ce.cs, "voltage_stability_campus", real)
    again = ce.run(hub, SETTINGS)["results"]
    assert "error" not in again["voltage_stability"] and not (run / ce.VOLTAGE_STABILITY_ERROR).exists()


def test_a_run_from_before_the_stage_has_none(user_and_db):  # noqa: F811
    hub = _hub(user_and_db, sk=600.0)
    ce.draft(hub)
    ce.run(hub, SETTINGS)
    run = ce.campus_dir(hub) / "run"
    for name in (ce.cs.VOLTAGE_STABILITY_CSV, ce.VOLTAGE_STABILITY_META):
        (run / name).unlink()
    assert ce.get_state(hub)["results"]["voltage_stability"] is None
    assert _rows(hub)[IDS[0]]["note"].startswith("No grid study with the voltage stability check yet: run the grid study")


@pytest.mark.live_solve
@pytest.mark.slow
def test_a_campus_run_on_eh_datacenter_gives_both_rows(user_and_db, monkeypatch, tmp_path):  # noqa: F811
    """The shipped data-centre template, solved, then its campus run: both rows carry a margin, a limit and its
    source (the placeholders: no profile ships a margin, D3-KEEP)."""
    from tests.test_eh_staged_hubs import T, _solve

    n, _cfg, _state = _solve("eh_datacenter", monkeypatch, tmp_path)
    db, user = user_and_db
    row = project_registry.create_root(db, user, "eh_datacenter")
    d = project_registry.ensure_project_dir(row)
    n.export_to_netcdf(str(d / "network.nc"))
    T.write_sidecars(d, "eh_datacenter")
    ce.draft(row)
    ce.run(row, SETTINGS)
    run = ce.campus_dir(row) / "run"
    assert (run / ce.cs.VOLTAGE_STABILITY_CSV).is_file(), json.loads((run / ce.VOLTAGE_STABILITY_ERROR).read_text())
    assert (run / ce.VOLTAGE_STABILITY_META).is_file()
    rows = _rows(row)
    for rid in IDS:
        r = rows[rid]
        assert r["verdict"] in ("pass", "warn") and r["project_value"] is not None, r
        assert r["limit"] in (G.GENERIC_PV_MARGIN_PCT, G.GENERIC_QV_MARGIN_MVAR)
        assert "planning practice placeholder" in r["source"]
