"""A1.4: corrupt, lost, expired, duplicate and revised records, and restart.

Issue 02 (P1). A stored answer or evidence record that is corrupt, expired,
missing its evidence, from another policy version, or whose current source
changed (including unsaved inputs lost by a reload) never reads as checked:
the answer and evidence reads answer 409 and history says "unavailable",
with no value in any body. A record that is intact reads the same after a
reload of the saved project and a new chat session.
"""

import json
import time
import uuid

import pandas as pd
import pytest
from fastapi import HTTPException

import app_paths
from services import assurance_records as records
from services import chat_service
from tests.assurance_acceptance.support import (
    approval,
    observed_draft,
    text_turn,
)
from tests.assurance_acceptance.test_p1_revocation import (
    PAGE_DRAFT,
    SERIES_DRAFT,
    get_component,
    get_series,
    list_loads,
)

READ_PATHS = (
    "/api/chat/answers/{answer}",
    "/api/chat/answers/{answer}/export",
    "/api/chat/evidence/{evidence}",
)


def record_path(record_id):
    (path,) = (app_paths.app_data_dir() / "assistant_assurance").glob(
        f"*/{record_id}.json"
    )
    return path


def rewrite(record_id, change):
    """Change the stored record and re-seal it as this server would."""
    path = record_path(record_id)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    change(envelope["record"])
    envelope.update(records.seal(envelope["record"], record_id))
    path.write_text(json.dumps(envelope), encoding="utf-8")


def checked(harness):
    approved = approval(harness.load_fact())
    answer_id = approved["answer_id"]
    (evidence_id,) = approved["receipt"]["evidence_ids"]
    paths = [p.format(answer=answer_id, evidence=evidence_id) for p in READ_PATHS]
    # Positive control: all three read before the fault.
    assert [harness.client.get(p).status_code for p in paths] == [200, 200, 200]
    return answer_id, evidence_id, paths


def assert_unavailable(harness, paths, *, evidence_status=409):
    answer, export, evidence = (harness.client.get(p) for p in paths)
    for response in (answer, export):
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error_kind"] == "answer_evidence_unavailable"
    assert evidence.status_code == evidence_status, evidence.text
    if evidence_status == 409:
        assert evidence.json()["detail"]["error_kind"] == "evidence_unavailable"
    for response in (answer, export, evidence):
        assert "5 MW" not in response.text and '"value"' not in response.text
    history = harness.client.get(
        f"/api/chat/history?session_id={harness.session_ids[-1]}"
    )
    assert history.status_code == 200
    (turn,) = history.json()["turns"]
    assert turn["assurance_state"] == "unavailable"
    assert "5 MW" not in json.dumps(turn) and "receipt" not in turn


def flip_byte(path):
    data = bytearray(path.read_bytes())
    at = data.index(b'"value"') + len(b'"value": ')
    data[at : at + 1] = b"7" if data[at : at + 1] != b"7" else b"8"
    path.write_bytes(bytes(data))


CORRUPTIONS = {
    "byte": flip_byte,
    "truncated": lambda p: p.write_bytes(p.read_bytes()[:40]),
    "not_json": lambda p: p.write_bytes(b"\x00\xff garbage"),
    "list": lambda p: p.write_text("[]"),
    "no_checksum": lambda p: p.write_text(
        json.dumps({"record": json.loads(p.read_text())["record"]})
    ),
    "oversize": lambda p: p.write_bytes(p.read_bytes() + b" " * (129 * 1024)),
}


@pytest.mark.parametrize("which", ["evidence", "answer"])
@pytest.mark.parametrize("fault", sorted(CORRUPTIONS))
def test_corrupt_records_read_as_unavailable(baseline, harness, which, fault):
    answer_id, evidence_id, paths = checked(harness)
    CORRUPTIONS[fault](record_path(evidence_id if which == "evidence" else answer_id))
    if which == "evidence":
        assert_unavailable(harness, paths)
        return
    for path in paths[:2]:
        response = harness.client.get(path)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error_kind"] == "answer_evidence_unavailable"
        assert "5 MW" not in response.text


@pytest.mark.parametrize(
    "field", ["value", "unit", "entity_id", "metric", "source_sha256"]
)
def test_rewritten_evidence_with_a_matching_checksum_is_unavailable(
    baseline, harness, field
):
    """A checksum alone is not integrity: lookups re-derive the facts."""
    _, evidence_id, paths = checked(harness)

    def change(record):
        if field == "source_sha256":
            record[field] = "0" * 64
            return
        fact = record["facts"][0]
        fact[field] = {"value": 7, "unit": "kW", "entity_id": "L2", "metric": "q_set"}[
            field
        ]

    rewrite(evidence_id, change)
    assert_unavailable(harness, paths)


@pytest.mark.parametrize(
    "read, draft",
    [
        (get_component, observed_draft()),
        (list_loads, PAGE_DRAFT),
        (get_series, SERIES_DRAFT),
    ],
    ids=["get_component", "list_components", "get_timeseries"],
)
@pytest.mark.parametrize("change", ["value", "added", "emptied"])
def test_every_current_input_kind_re_derives_its_facts(
    baseline, harness, read, draft, change
):
    frames = harness.run([read(), text_turn(draft), text_turn(draft)])
    approved = approval(frames)
    (evidence_id,) = approved["receipt"]["evidence_ids"]
    path = f"/api/chat/evidence/{evidence_id}"
    assert harness.client.get(path).status_code == 200

    def edit(record):
        facts = record["facts"]
        if change == "value":
            facts[0]["value"] = 7.0
        elif change == "added":
            facts.append({**facts[0], "value": 7.0, "path": facts[0]["path"] + "x"})
        else:
            facts.clear()

    rewrite(evidence_id, edit)
    response = harness.client.get(path)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error_kind"] == "evidence_unavailable"
    assert '"value"' not in response.text


def test_rewritten_answer_text_with_a_matching_checksum_is_unavailable(
    baseline, harness
):
    answer_id, _, paths = checked(harness)
    rewrite(answer_id, lambda r: r.update(text=r["text"].replace("5", "7")))
    for path in paths[:2]:
        response = harness.client.get(path)
        assert response.status_code == 409, response.text
        assert "7 MW" not in response.text


def test_lost_evidence_leaves_the_answer_unavailable(baseline, harness):
    _, evidence_id, paths = checked(harness)
    record_path(evidence_id).unlink()
    # Lost evidence reads like an unknown id; the answer that cited it is 409.
    assert_unavailable(harness, paths, evidence_status=404)
    unknown = harness.client.get(f"/api/chat/evidence/{uuid.uuid4()}")
    assert harness.client.get(paths[2]).text == unknown.text


@pytest.mark.parametrize("which", ["evidence", "answer"])
def test_expired_records_read_as_unavailable(baseline, harness, which):
    answer_id, evidence_id, paths = checked(harness)
    rewrite(
        evidence_id if which == "evidence" else answer_id,
        lambda r: r.update(expires_at=time.time() - 1),
    )
    if which == "evidence":
        assert_unavailable(harness, paths)
        return
    for path in paths[:2]:
        assert harness.client.get(path).status_code == 409
    assert harness.client.get(paths[2]).status_code == 200


@pytest.mark.parametrize("version", ["0", "2", None])
def test_answer_from_another_policy_version_is_unavailable(baseline, harness, version):
    answer_id, _, paths = checked(harness)
    rewrite(answer_id, lambda r: r["receipt"].update(policy_version=version))
    for path in paths[:2]:
        response = harness.client.get(path)
        assert response.status_code == 409, response.text
        assert "5 MW" not in response.text


@pytest.mark.parametrize("receipt", ["text", None, [], {"evidence_ids": 5}])
def test_answer_with_a_malformed_receipt_is_unavailable(baseline, harness, receipt):
    answer_id, _, paths = checked(harness)

    def change(record):
        if isinstance(receipt, dict):
            record["receipt"].update(receipt)
        else:
            record["receipt"] = receipt

    rewrite(answer_id, change)
    for path in paths[:2]:
        response = harness.client.get(path)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error_kind"] == "answer_evidence_unavailable"


@pytest.mark.parametrize("stored", ["profile", None])
def test_scoped_evidence_from_an_unregistered_profile_is_unavailable(
    baseline, harness, client, session_state, monkeypatch, stored
):
    """A result profile this server no longer registers is not re-read."""
    from harness import calculations
    from tests.test_assurance_metrics import NumericalProvider
    from tests.test_lost_load_assurance import PROFILE, SHED, scope, solve

    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", PROFILE)
    ctx = baseline[0]
    n = ctx.network
    n.loads_t.p_set = pd.DataFrame({"L1": [5, 25]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "generators"] = [2, 3]
    n.snapshot_weightings.loc[:, "objective"] = [1, 2]
    solve(client, session_state)
    frames = harness.run(
        NumericalProvider(SHED),
        message="Check lost load.",
        checked_scope=scope(ctx),
    )
    approved = approval(frames)
    answer_id = approved["answer_id"]
    paths = [p.format(answer=answer_id, evidence="") for p in READ_PATHS[:2]] + [
        f"/api/chat/evidence/{e}" for e in approved["receipt"]["evidence_ids"]
    ]
    assert [harness.client.get(p).status_code for p in paths] == [200] * len(paths)
    profiles = dict(calculations.PROFILES)
    del profiles[PROFILE]
    monkeypatch.setattr(calculations, "PROFILES", profiles)
    if stored is None:
        # A null adapter version must not match "no registered profile".
        for evidence_id in approved["receipt"]["evidence_ids"]:
            rewrite(evidence_id, lambda r: r.update(adapter_version=None))
    for path in paths:
        response = harness.client.get(path)
        assert response.status_code == 409, response.text
        assert "10000" not in response.text


@pytest.mark.parametrize("field", ["version", "adapter_version"])
def test_evidence_from_another_schema_or_adapter_is_unavailable(
    baseline, harness, field
):
    _, evidence_id, paths = checked(harness)
    rewrite(evidence_id, lambda r: r.update({field: {"version": 2}.get(field, "x/9")}))
    assert_unavailable(harness, paths)


def test_duplicate_completion_cannot_replace_a_record(baseline, harness):
    answer_id, evidence_id, paths = checked(harness)
    for record_id in (evidence_id, answer_id):
        path = record_path(record_id)
        before = path.read_bytes()
        record = json.loads(before)["record"]
        changed = (
            {**record, "facts": []} if "facts" in record else {**record, "text": ""}
        )
        with pytest.raises(HTTPException) as refused:
            records.save(changed, record_id)
        assert refused.value.status_code == 409
        assert path.read_bytes() == before
        # Saving the identical record again is an idempotent no-op.
        records.save(record, record_id)
        assert path.read_bytes() == before
    assert [harness.client.get(p).status_code for p in paths] == [200, 200, 200]


def reopen(harness):
    """A restart as far as this process can show one: the chat session goes,
    the project is read back from disk, and the in-memory input is replaced."""
    for session_id in harness.session_ids:
        chat_service.drop_session(session_id)
    response = harness.client.get("/api/projects/Tool Baseline")
    assert response.status_code == 200, response.text[:300]


def test_saved_inputs_stay_checked_across_a_restart(baseline, harness):
    _, _, paths = checked(harness)
    saved = [harness.client.get(p).json() for p in paths]
    reopen(harness)
    after = [harness.client.get(p) for p in paths]
    assert [r.status_code for r in after] == [200, 200, 200]
    assert [r.json() for r in after] == saved


def test_unsaved_inputs_lost_on_restart_leave_the_answer_unavailable(baseline, harness):
    from services.pypsa_service import PyPSAService

    network = PyPSAService.get_active_context().network
    network.loads.loc["L1", "p_set"] = 6.0  # an unsaved foreground edit
    approved = approval(harness.load_fact(value=6))
    (evidence_id,) = approved["receipt"]["evidence_ids"]
    paths = [
        p.format(answer=approved["answer_id"], evidence=evidence_id) for p in READ_PATHS
    ]
    assert [harness.client.get(p).status_code for p in paths] == [200, 200, 200]
    reopen(harness)
    # The saved project says 5 MW; the answer was about the unsaved 6 MW.
    assert PyPSAService.get_active_context().network.loads.at["L1", "p_set"] == 5
    for path in paths:
        response = harness.client.get(path)
        assert response.status_code == 409, response.text
        assert "6 MW" not in response.text
