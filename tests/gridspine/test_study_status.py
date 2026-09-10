"""Increment 4, task 2: what a study directory says about itself.

The backend needs three answers between runs and after a restart — how far did
this get, which hours did it pick, and what did it assume — and it must be
able to answer them with the job gone and the process new. So all three are
derived from the ARTIFACTS, never from live state: `stage_status` reads files,
`ranked_snapshots` reads the two ranking tables, and the ledger the copilot
quotes is `ledger.json` (data), not `ledger.md` (prose for a human).

The spec's error handling makes this a contract, not a convenience: "stage
failure = typed error artifact in the study directory; UI and chatbot render
the same artifact; runs resume from the last valid artifact." A status that
consulted the queue would disagree with the directory the moment a container
was recycled — which is exactly when someone asks.

One module-scoped run (24 h, k=1, screening on) is copied per test that needs
to break it, so the destructive cases never race each other.
"""
import json
import shutil

import pandas as pd
import pytest

from gridspine.drivers.status import STAGE_STATES, ranked_snapshots, stage_status
from gridspine.drivers.study import StudyConfig, run_study
from gridspine.schema.contracts import ContractError
from gridspine.schema.errors import StageError

HOURS, K = 24, 1
STAGES = ("ingest", "dispatch", "ranking", "loadflow", "screening", "handoff")


@pytest.fixture(scope="module")
def done_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("status_run")
    res = run_study(StudyConfig(outdir=out, hours=HOURS, k=K, window=24, overlap=0))
    return out, res


@pytest.fixture
def copy_of(done_run, tmp_path):
    src, _res = done_run
    dst = tmp_path / "copy"
    shutil.copytree(src, dst)
    return dst


# --------------------------------------------------------------------------
# stage_status
# --------------------------------------------------------------------------

def test_a_finished_run_is_done_at_every_stage(done_run):
    out, res = done_run
    st = stage_status(out)
    assert [st["stages"][s]["state"] for s in STAGES] == ["done"] * len(STAGES)
    assert st["status"] == "completed"
    assert st["selected_hours"] == [int(h) for h in res.selected["hour"]]
    assert st["converged_hours"] == st["selected_hours"]
    assert set(st["bundles"]) == {str(h) for h in st["selected_hours"]}
    assert st["error"] is None


def test_every_reported_state_is_one_of_the_documented_five(done_run):
    out, _res = done_run
    assert set(STAGE_STATES) == {"pending", "running", "done", "failed", "aborted"}
    st = stage_status(out)
    assert {s["state"] for s in st["stages"].values()} <= set(STAGE_STATES)


def test_an_empty_directory_is_pending_everywhere(tmp_path):
    st = stage_status(tmp_path)
    assert [st["stages"][s]["state"] for s in STAGES] == ["pending"] * len(STAGES)
    assert st["status"] == "not started"
    assert st["selected_hours"] == []


def test_a_missing_manifest_leaves_the_last_stage_running(copy_of):
    (copy_of / "manifest.json").unlink()
    st = stage_status(copy_of)
    assert st["stages"]["screening"]["state"] == "done"
    assert st["stages"]["handoff"]["state"] == "running"
    assert st["status"] == "running"


def test_a_dispatch_without_metrics_is_running_at_ranking(copy_of):
    for name in ("metrics.csv", "selected.csv", "manifest.json"):
        (copy_of / name).unlink()
    st = stage_status(copy_of)
    assert st["stages"]["dispatch"]["state"] == "done"
    assert st["stages"]["ranking"]["state"] == "running"
    assert st["stages"]["loadflow"]["state"] == "pending"


def test_an_error_artifact_names_the_failed_stage_and_stops_the_rest(copy_of):
    (copy_of / "manifest.json").unlink()
    StageError(stage="handoff", element_ids=[], cause="RuntimeError('disk full')").write(copy_of)
    st = stage_status(copy_of)
    assert st["stages"]["handoff"]["state"] == "failed"
    assert st["status"] == "failed"
    assert st["error"]["stage"] == "handoff"
    assert "disk full" in st["error"]["cause"]


def test_an_aborted_run_reads_as_aborted_not_failed(copy_of):
    (copy_of / "manifest.json").unlink()
    StageError(stage="handoff", element_ids=[], cause="StudyAborted('aborted during handoff')").write(copy_of)
    st = stage_status(copy_of)
    assert st["stages"]["handoff"]["state"] == "aborted"
    assert st["status"] == "aborted"
    assert st["resumable"] is True          # dispatch.csv survived


def test_an_abort_before_the_dispatch_is_not_resumable(tmp_path):
    (tmp_path / "loads.csv").write_text("bus,hour,p_mw,q_mvar\n")
    StageError(stage="dispatch", element_ids=[], cause="StudyAborted('aborted during dispatch')").write(tmp_path)
    st = stage_status(tmp_path)
    assert st["stages"]["dispatch"]["state"] == "aborted"
    assert st["resumable"] is False


def test_the_per_hour_stages_report_how_many_of_how_many(done_run):
    out, res = done_run
    st = stage_status(out)
    n = len(res.selected)
    for stage in ("loadflow", "screening", "handoff"):
        assert st["stages"][stage]["done"] == n, stage
        assert st["stages"][stage]["total"] == n, stage


# --------------------------------------------------------------------------
# ranked_snapshots
# --------------------------------------------------------------------------

def test_ranked_snapshots_joins_the_selection_to_every_metric(done_run):
    out, res = done_run
    table = ranked_snapshots(out)
    metrics = pd.read_csv(out / "metrics.csv").set_index("hour")
    assert list(table["hour"]) == [int(h) for h in res.selected["hour"]]
    for col in metrics.columns:
        assert col in table.columns, col
    for hour, reasons in zip(table["hour"], table["reasons"]):
        assert isinstance(reasons, list) and reasons
        assert table.loc[table["hour"] == hour, "n1_severity_ac"].iloc[0] == pytest.approx(
            metrics.at[int(hour), "n1_severity_ac"]
        )
    assert table["converged"].all()


def test_ranked_snapshots_refuses_a_directory_without_a_selection(tmp_path):
    with pytest.raises(ContractError, match="selected.csv"):
        ranked_snapshots(tmp_path)


# --------------------------------------------------------------------------
# ledger.json
# --------------------------------------------------------------------------

def test_every_bundle_carries_the_ledger_as_data_beside_the_prose(done_run):
    out, res = done_run
    for hour, bundle in res.bundles.items():
        data = json.loads((bundle / "ledger.json").read_text())
        assert data["entries"] == data["entries"], hour
        assert isinstance(data["entries"], list) and len(data["entries"]) > 10
        assert set(data["provenance_counts"]) == {"measured", "datasheet", "assumed"}
        assert set(data["measurements"]) >= {"n2_prune_threshold", "dc_severity_blind_spot"}
        assert data["hour"] == int(hour)
        prose = (bundle / "ledger.md").read_text()
        for entry in data["entries"]:
            assert entry in prose, entry[:60]


def test_ledger_json_is_a_required_bundle_file(done_run):
    from gridspine.handoff.bundle import BUNDLE_FILES

    assert "ledger.json" in BUNDLE_FILES
    _out, res = done_run
    for bundle in res.bundles.values():
        manifest = json.loads((bundle / "manifest.json").read_text())
        assert "ledger.json" in manifest["files"]
