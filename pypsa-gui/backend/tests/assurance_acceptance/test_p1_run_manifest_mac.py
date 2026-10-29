"""A1.3-MAC: a checked run is one this server published, not one on disk.

Issue 02 (P1). A run manifest certified its sections by hash, but nothing
certified the manifest: anyone who could write the project directory could
publish a completed run, sections and hashes included, and have it read as
checked. Every terminal manifest now carries a MAC under the record server key;
a checked read or lookup of a run whose manifest lacks it, or carries one made
for other content, another run, another project or another key, is refused.
Unchecked reads of such a run still answer: the MAC gates checking, not history.
"""

# ruff: noqa: F811 -- imported shared fixtures.

import hashlib
import json

import pytest

from services import assurance_records as records
from services import run_evidence
from tests.assurance_acceptance.support import approval
from tests.assurance_acceptance.test_p1_run_as_of import (  # noqa: F401
    checked_run,
    publish,
    read_run,
    run,
    run_draft,
    run_result,
    wrap_handler,
)


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))


def manifest_path(root, run_id="run-old"):
    return root / "run_evidence" / run_id / "manifest.json"


def read_manifest(root, run_id="run-old"):
    return json.loads(manifest_path(root, run_id).read_text(encoding="utf-8"))


def forge(root, run_id, manifest, value=9999.5):
    """Write a completed run as anyone with write access to the project can:
    its section and every hash consistent, only the server key missing."""
    manifest = json.loads(json.dumps(manifest))
    manifest["run_id"] = run_id
    manifest["metrics"]["objective"]["value"] = value
    blob = run_evidence._json_bytes({"objective": value})
    sections = root / "run_evidence" / run_id / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    (sections / "summary.json").write_bytes(blob)
    manifest["sections"] = {
        "summary": {"sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob)}
    }
    manifest_path(root, run_id).write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def refused_before_handler(harness, monkeypatch, run_id, value=9999.5):
    calls = wrap_handler(monkeypatch)
    frames = read_run(harness, run_draft(value=value, run_id=run_id), {"run_id": run_id})
    assert not calls
    refused = run_result(frames)
    assert refused["error_kind"] == "run_not_checkable"
    assert "this run is unverified." in refused["message"]
    assert approval(frames)["verdict"] == "limited"
    return frames


def test_a_published_manifest_carries_a_server_mac(run):
    manifest = read_manifest(run[2])
    assert isinstance(manifest.get("mac"), str) and len(manifest["mac"]) == 64
    assert run_evidence.checked_run_manifest("run-old")[2] == "completed"


def test_a_run_forged_without_the_key_is_not_checkable(run, harness, monkeypatch):
    without_mac = {k: v for k, v in read_manifest(run[2]).items() if k != "mac"}
    forge(run[2], "run-forged", without_mac)
    refused_before_handler(harness, monkeypatch, "run-forged")


def test_a_mac_copied_from_another_run_is_refused(run, harness, monkeypatch):
    # Same metrics and sections as the sealed run: only the run id differs.
    original = read_manifest(run[2])
    forged = forge(run[2], "run-copy", original, value=1250.5)
    blob = (run[2] / "run_evidence" / "run-old" / "sections" / "summary.json").read_bytes()
    (run[2] / "run_evidence" / "run-copy" / "sections" / "summary.json").write_bytes(blob)
    forged["sections"] = original["sections"]
    manifest_path(run[2], "run-copy").write_text(json.dumps(forged), encoding="utf-8")
    assert forged["mac"] == original["mac"]
    refused_before_handler(harness, monkeypatch, "run-copy")


def test_a_manifest_moved_to_another_project_is_refused(run, harness, monkeypatch):
    # The other project's directory receives a sealed manifest, project id rewritten.
    for path in ("/api/projects/Another Baseline", "/api/projects/Another Baseline/activate"):
        assert harness.client.post(path).status_code == 200
    from services.pypsa_service import PyPSAService

    other = PyPSAService.get_active_context()
    other_root, _project, _owner = run_evidence._authority()
    moved = read_manifest(run[2])
    moved["project_id"] = other.project_uuid
    target = other_root / "run_evidence" / "run-old"
    (target / "sections").mkdir(parents=True, exist_ok=True)
    (target / "sections" / "summary.json").write_bytes(
        (run[2] / "run_evidence" / "run-old" / "sections" / "summary.json").read_bytes()
    )
    (target / "manifest.json").write_text(json.dumps(moved), encoding="utf-8")
    assert run_evidence.checked_run_manifest("run-old")[1:] == (None, "unverified")


@pytest.mark.parametrize(
    "fault",
    ["removed", "zeroed", "rewritten_metric"],
)
def test_a_checked_run_whose_mac_no_longer_verifies_is_unavailable(run, harness, fault):
    _, paths = checked_run(harness)
    manifest = read_manifest(run[2])
    if fault == "removed":
        del manifest["mac"]
    elif fault == "zeroed":
        manifest["mac"] = "0" * 64
    else:
        # A note the facts do not read: only the MAC can tell it changed.
        manifest["warnings"] = ["edited"]
    manifest_path(run[2]).write_text(json.dumps(manifest), encoding="utf-8")
    for path in paths:
        assert harness.client.get(path).status_code == 409, path


def test_a_run_sealed_under_another_key_is_not_checkable(run, harness, monkeypatch):
    # The record key changes too, so earlier answers fail on their own seal;
    # a fresh read shows the manifest's MAC is what refuses the run.
    records.key_path().write_bytes(bytes(32))
    refused_before_handler(harness, monkeypatch, "run-old", 1250.5)


def test_an_unsealed_run_still_reads_as_unchecked_data(run, harness, monkeypatch):
    # A manifest written before the MAC existed: history, not evidence.
    manifest = read_manifest(run[2])
    manifest.pop("mac", None)
    manifest_path(run[2]).write_text(json.dumps(manifest), encoding="utf-8")
    shown = run_evidence.get_run_evidence("run-old")
    assert shown["metrics"]["objective"] == {"value": 1250.5, "unit": "EUR"}
    assert shown["sections"] == [{"name": "summary", "available": True}]
    listed = run_evidence.list_project_runs()
    assert [r["run_id"] for r in listed["items"]] == ["run-old"]
    assert run_evidence.checked_run_manifest("run-old")[1:] == (None, "unverified")


def test_republishing_the_same_completion_keeps_the_sealed_manifest(run):
    before = read_manifest(run[2])
    publish(run[2], run[1], "run-old", "completed", "2026-10-02T10:00:00+00:00")
    assert read_manifest(run[2]) == before
    assert run_evidence.checked_run_manifest("run-old")[2] == "completed"
