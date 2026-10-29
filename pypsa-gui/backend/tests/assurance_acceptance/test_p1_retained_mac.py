"""A1.4-RETAINED: a retained run is certified by a server key, not a hash.

Issue 02 (P1). A retained scoped result carries its own raw source, so a
lookup cannot re-read the live project to check it. Before this slice the
record was certified by unkeyed hashes only: rewriting its raw, source hash,
facts, slots and file checksum together read as checked. The record envelope
now carries a MAC under a server key kept outside the record store; a record
rewritten without that key, or sealed under another key, is unavailable.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import json
import os

import pytest

import app_paths
from harness import calculations
from services import assurance_records as records
from tests.assurance_acceptance.support import approval
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider, solved  # noqa: F401
from tests.test_harness_reliability import VALUES, scope
from tests.test_workflow_tools import baseline  # noqa: F401


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))


def retained(solved, checked):
    frames = checked.run(
        NumericalProvider(VALUES), checked_scope=scope(solved, retain=True)
    )
    approved = approval(frames)
    (evidence_id,) = approved["receipt"]["evidence_ids"]
    paths = [
        "/api/chat/answers/" + approved["answer_id"],
        "/api/chat/answers/" + approved["answer_id"] + "/export",
        "/api/chat/evidence/" + evidence_id,
    ]
    # Positive control: every read is checked before the fault.
    assert [checked.client.get(p).status_code for p in paths] == [200, 200, 200]
    return evidence_id, paths


def store():
    return app_paths.app_data_dir() / "assistant_assurance"


def record_path(record_id):
    (path,) = store().glob(f"*/{record_id}.json")
    return path


def double_marginal_cost(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "marginal_cost" and isinstance(item, list):
                value[key] = [v * 2 for v in item]
            else:
                double_marginal_cost(item)
    elif isinstance(value, list):
        for item in value:
            double_marginal_cost(item)


def forge(evidence_id):
    """Rewrite a retained record consistently, as anyone without the key can."""
    path = record_path(evidence_id)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    record = envelope["record"]
    before = json.dumps(record["raw"], sort_keys=True)
    double_marginal_cost(record["raw"])
    assert json.dumps(record["raw"], sort_keys=True) != before
    record["facts"], record["slots"] = calculations.scoped_calculate(
        record["raw"], record["request_scope"]
    )
    record["source_sha256"] = records.digest(record["raw"])
    envelope["sha256"] = records.digest(record)
    path.write_text(json.dumps(envelope), encoding="utf-8")
    return envelope


def assert_unavailable(checked, paths):
    for path in paths:
        response = checked.client.get(path)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error_kind"] in {
            "answer_evidence_unavailable",
            "evidence_unavailable",
        }
        assert "230" not in response.text and '"value"' not in response.text


def test_a_consistently_rewritten_retained_run_is_unavailable(solved, checked):
    evidence_id, paths = retained(solved, checked)
    forge(evidence_id)
    assert_unavailable(checked, paths)


def test_a_record_sealed_under_another_key_is_unavailable(solved, checked):
    evidence_id, paths = retained(solved, checked)
    key = records.key_path()
    key.write_bytes(bytes(32))
    assert_unavailable(checked, paths)


def test_the_key_lives_outside_the_record_store(solved, checked):
    evidence_id, _ = retained(solved, checked)
    key = records.key_path()
    assert key.exists() and store() not in key.parents
    if os.name != "nt":
        assert (key.stat().st_mode & 0o777) == 0o600
    secret = key.read_bytes()
    for path in store().rglob("*.json"):
        assert secret.hex() not in path.read_text(encoding="utf-8")


def test_the_mac_depends_on_the_configured_secret_key(monkeypatch):
    from settings import get_settings

    record = {"record_kind": "evidence", "value": 1}
    sealed = records.seal(record, "id")
    assert records.unseal(sealed, "id") == record
    with monkeypatch.context() as patch:
        patch.setenv("SECRET_KEY", "another-test-secret-not-a-real-key")
        get_settings.cache_clear()
        try:
            with pytest.raises(ValueError):
                records.unseal(sealed, "id")
        finally:
            get_settings.cache_clear()
    assert records.unseal(sealed, "id") == record
    with pytest.raises(ValueError):
        records.unseal(sealed, "another-id")


@pytest.mark.parametrize("fault", ["wrong_mac", "no_mac", "no_checksum"])
def test_an_envelope_without_a_valid_mac_or_checksum_is_unavailable(
    solved, checked, fault
):
    evidence_id, paths = retained(solved, checked)
    path = record_path(evidence_id)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if fault == "wrong_mac":
        envelope["mac"] = "0" * 64
    else:
        envelope.pop("mac" if fault == "no_mac" else "sha256", None)
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert_unavailable(checked, paths[2:])


def test_a_sealed_record_copied_over_another_id_is_unavailable(solved, checked):
    first, _ = retained(solved, checked)
    second, paths = retained(solved, checked)
    record_path(second).write_bytes(record_path(first).read_bytes())
    assert_unavailable(checked, paths)
