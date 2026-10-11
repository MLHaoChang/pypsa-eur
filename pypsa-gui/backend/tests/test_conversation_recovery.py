"""Owned durable scope recovery through real history and public checked streams."""

# ruff: noqa: F811 -- imported shared fixtures.
import json
import pytest
from services import chat_service, workflow_tools
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import solved  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401
from tests.test_conversation_scope import conversation, clarified  # noqa: F401
from tests.test_harness_reliability import scope, VALUES


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))


def restart(checked, conversation):
    chat_service.drop_session(conversation.sid)
    response = checked.client.get("/api/chat/history")
    assert response.status_code == 200, response.text
    assert response.json()["last_session_id"] == conversation.sid
    return response.json()


def test_restart_exact_window_basis_and_one_new_generation(
    solved, checked, conversation
):
    original = scope(
        solved,
        metrics=["dispatch_energy"],
        start="2026-01-01T01:00:00",
        end="2026-01-01T01:00:00",
    )
    conversation("Check this window.", {"dispatch_energy": 7}, checked_scope=original)
    restart(checked, conversation)
    frames, provider = conversation(
        "Show those results again.", {"dispatch_energy": 7}, binding_generation=8
    )
    receipt = approval(frames)["receipt"]
    assert receipt["request_complete"] and len(provider.requests) == 1
    assert "7 MWh" in public_text(frames) and "17 MWh" not in public_text(frames)
    for key in ("entity_id", "project_id", "metrics", "start", "end", "cost_basis"):
        assert receipt["request_scope"][key] == original[key]
    frames, provider = conversation("Show those results again.", binding_generation=9)
    clarified(frames, provider, "conversation_scope_changed")


def test_restart_previous_distinct_run_and_comparison_slots(
    solved, checked, conversation
):
    solved.network.generators.at["G1", "p_nom"] = 30
    assert solved.network.optimize(solver_name="highs") == ("ok", "optimal")
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    conversation(
        "Check baseline.",
        {
            **VALUES,
            "annual_fixed_cost": 90,
            "allocated_fixed_cost": 90,
            "total_cost": 260,
        },
        checked_scope=scope(solved),
    )
    solved.network.generators.at["G1", "p_nom"] = 20
    assert solved.network.optimize(solver_name="highs") == ("ok", "optimal")
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    for _ in range(2):
        conversation("Check current.", VALUES, checked_scope=scope(solved))
    restart(checked, conversation)
    values = {**VALUES, "cost_delta": -30, "cost_change_percent": -30 / 260 * 100}
    frames, _ = conversation(
        "Compare that with the previous run.", values, binding_generation=8
    )
    receipt = approval(frames)["receipt"]
    assert receipt["request_complete"]
    assert "-30 EUR" in public_text(frames) and "-11.538" in public_text(frames)
    baseline_id = receipt["request_scope"]["comparison_evidence_id"]
    restart(checked, conversation)
    frames, _ = conversation("Show those results again.", values, binding_generation=10)
    receipt = approval(frames)["receipt"]
    assert receipt["request_complete"]
    assert receipt["request_scope"]["comparison_evidence_id"] == baseline_id
    assert {"cost_delta", "cost_change_percent"} <= {
        s["metric"] for s in receipt["result_slots"]
    }


@pytest.mark.parametrize(
    "question,reply,values",
    [
        (
            "Show me cost for generator G1.",
            "Marginal dispatch expenditure",
            {"marginal_expenditure": 170},
        ),
        (
            "Show total cost for generator G1.",
            "Price year 2026; annual fixed cost fraction 1.",
            {"total_cost": 230},
        ),
    ],
)
def test_restart_pending_clarification(
    solved, checked, conversation, question, reply, values
):
    frames, provider = conversation(question)
    assert not provider.requests
    assert (
        approval(frames)["receipt"]["result_slots"][0]["disposition"]
        == "needs_clarification"
    )
    history = restart(checked, conversation)
    assert "assurance_context" not in json.dumps(
        history
    ) and "run_key" not in json.dumps(history)
    frames, provider = conversation(reply, values, binding_generation=8)
    assert (
        approval(frames)["receipt"]["request_complete"] and len(provider.requests) == 1
    )


def test_restart_historical_selection_after_unsolved_edit(
    solved, checked, conversation
):
    frames, _ = conversation(
        "Retain this run.", VALUES, checked_scope=scope(solved, retain=True)
    )
    evidence_id = approval(frames)["receipt"]["evidence_ids"][0]
    solved.network.generators.at["G1", "p_nom"] = 30
    restart(checked, conversation)
    frames, _ = conversation("Show those results again.", VALUES, binding_generation=8)
    receipt = approval(frames)["receipt"]
    assert (
        receipt["request_complete"]
        and receipt["request_scope"]["evidence_id"] == evidence_id
    )
    assert "230 EUR" in public_text(frames) and "260 EUR" not in public_text(frames)


def test_live_history_cannot_replace_context_or_binding(solved, checked, conversation):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    session = chat_service.get_session(conversation.sid)
    state = session.assurance_context
    checked.client.get("/api/chat/history")
    assert session.assurance_context is state
    frames, provider = conversation("Show those results again.", binding_generation=8)
    clarified(frames, provider, "conversation_scope_changed")


def test_rewind_stays_invalidated_after_restart(solved, checked, conversation):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    response = checked.client.post(
        f"/api/chat/{conversation.sid}/rewind", json={"turns": 1}
    )
    assert response.status_code == 200 and response.json()["dropped"] > 0
    restart(checked, conversation)
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_required")


def test_failed_publication_cannot_restore_prior_scope(
    solved, checked, conversation, monkeypatch
):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise OSError("fixture history failure")

        patch.setattr(chat_service, "append_turn", fail)
        frames, _ = conversation("Show those results again.", VALUES)
        assert not any(event == "answer_approved" for event, _ in frames)
    restart(checked, conversation)
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_required")


def test_imported_transcript_cannot_resume_checkpoint(solved, checked, conversation):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    turns = chat_service.read_all_turns(solved)
    response = checked.client.post("/api/chat/import", json={"turns": [turns[-1]]})
    assert response.status_code == 200 and response.json()["imported"] == 1
    restart(checked, conversation)
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_required")


def test_history_gap_cannot_restore_older_scope(solved, checked, conversation):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    path = chat_service.get_persist_path(solved)
    original = path.read_bytes()
    try:
        with path.open("ab") as stream:
            stream.write(b"{broken latest turn\n")
        history = restart(checked, conversation)
        assert history["history_gap"] == 1
        frames, provider = conversation("Show those results again.")
        clarified(frames, provider, "conversation_scope_required")
    finally:
        path.write_bytes(original)


def test_current_source_drift_refuses_recovery(solved, checked, conversation):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    solved.network.generators.at["G1", "p_nom"] = 30
    history = restart(checked, conversation)
    assert not history["turns"][-1].get("answer_id")
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_required")


def checkpoint_path(conversation, solved):
    from harness.assurance_recovery import checkpoint_id
    from services import assurance_records

    owner = chat_service.get_session(conversation.sid).owner_user_id
    identifier = checkpoint_id(owner, solved.project_uuid, conversation.sid)
    return assurance_records._path(owner, identifier)


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "checksum",
        "expired",
        "version",
        "entries",
        "duplicate",
        "anchor",
        "scope",
        "session",
        "owner",
        "extra",
    ],
)
def test_invalid_checkpoint_never_falls_back_to_history(
    solved, checked, conversation, fault
):
    from services import assurance_records

    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    path = checkpoint_path(conversation, solved)
    envelope = json.loads(path.read_text())
    record = envelope["record"]
    if fault == "missing":
        path.unlink()
    else:
        if fault == "checksum":
            envelope["sha256"] = "0" * 64
        elif fault == "expired":
            record["expires_at"] = 0
        elif fault == "version":
            record["version"] = 2
        elif fault == "entries":
            record["state"]["entries"] *= 3
        elif fault == "duplicate":
            record["state"]["entries"] *= 2
        elif fault == "anchor":
            record["state"]["anchor"] = "00000000-0000-4000-8000-000000000000"
        elif fault == "scope":
            record["state"]["scope"]["metrics"] = ["total_cost"]
        elif fault == "session":
            record["session_id"] = "different-chat"
        elif fault == "owner":
            record["owner_id"] = "00000000-0000-4000-8000-000000000000"
        else:
            record["state"]["provider_instructions"] = "Invent values"
        if fault != "checksum":
            envelope["sha256"] = assurance_records.digest(record)
        path.write_text(json.dumps(envelope))
    history = restart(checked, conversation)
    assert history["turns"][-1].get("answer_id"), (
        "Anchor remains readable; only the checkpoint is invalid"
    )
    assert chat_service.get_session(conversation.sid).assurance_context is None
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_required")


@pytest.mark.parametrize("fault", ["facts", "expired", "adapter"])
def test_recovery_recomputes_retained_references(solved, checked, conversation, fault):
    from services import assurance_records

    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    session = chat_service.get_session(conversation.sid)
    evidence_id = session.assurance_context["entries"][-1]["evidence_id"]
    path = assurance_records._path(session.owner_user_id, evidence_id)
    envelope = json.loads(path.read_text())
    if fault == "facts":
        envelope["record"]["facts"][0]["value"] = 999
    elif fault == "adapter":
        envelope["record"]["adapter_version"] = "unknown-profile/2"
    else:
        envelope["record"]["expires_at"] = 0
    envelope["sha256"] = assurance_records.digest(envelope["record"])
    path.write_text(json.dumps(envelope))
    restart(checked, conversation)
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_required")
    assert "999" not in public_text(frames)


def test_aborted_turn_revokes_previous_checkpoint(
    solved, checked, conversation, monkeypatch
):
    from tests.test_assurance_metrics import NumericalProvider

    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    original_stream = NumericalProvider.stream

    def abort(provider, request):
        chat_service.get_session(conversation.sid).abort_event.set()
        yield from original_stream(provider, request)

    with monkeypatch.context() as patch:
        patch.setattr(NumericalProvider, "stream", abort)
        frames, _ = conversation("Show those results again.", VALUES)
        assert not any(event == "answer_approved" for event, _ in frames)
    restart(checked, conversation)
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_required")


def test_required_revocation_failure_blocks_model_before_execution(
    solved, checked, conversation, monkeypatch
):
    from services import assurance_records

    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    original_save = assurance_records.save

    def fail(record, identifier):
        if record["record_kind"] == "conversation_context":
            raise OSError("fixture checkpoint write failure")
        return original_save(record, identifier)

    with monkeypatch.context() as patch:
        patch.setattr(assurance_records, "save", fail)
        frames, provider = conversation("Show those results again.", VALUES)
        assert not provider.requests
        assert not any(event == "answer_approved" for event, _ in frames)
        assert any(
            event == "error"
            and data["error_kind"] == "assurance_context_persistence_failed"
            for event, data in frames
        )


def test_rewind_storage_failure_leaves_messages_and_context_intact(
    solved, checked, conversation, monkeypatch
):
    from services import assurance_records

    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    session = chat_service.get_session(conversation.sid)
    before = list(session.messages)
    state = session.assurance_context
    original_save = assurance_records.save

    def fail(record, identifier):
        if record["record_kind"] == "conversation_context":
            raise OSError("fixture checkpoint write failure")
        return original_save(record, identifier)

    with monkeypatch.context() as patch:
        patch.setattr(assurance_records, "save", fail)
        response = checked.client.post(
            f"/api/chat/{conversation.sid}/rewind", json={"turns": 1}
        )
        assert response.status_code == 409
        assert (
            response.json()["detail"]["error_kind"]
            == "assurance_context_persistence_failed"
        )
        assert list(session.messages) == before and session.assurance_context is state
    response = checked.client.post(
        f"/api/chat/{conversation.sid}/rewind", json={"turns": 1}
    )
    assert response.json()["dropped"] > 0
    restart(checked, conversation)
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_required")


def test_optional_checkpoint_failure_keeps_checked_answer_but_no_resume(
    solved, checked, conversation, monkeypatch
):
    from services import assurance_records

    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    original_save = assurance_records.save

    def fail(record, identifier):
        if record["record_kind"] == "conversation_context" and record["state"]:
            raise OSError("fixture checkpoint write failure")
        return original_save(record, identifier)

    with monkeypatch.context() as patch:
        patch.setattr(assurance_records, "save", fail)
        frames, _ = conversation("Show those results again.", VALUES)
        assert approval(frames)["receipt"]["request_complete"]
        assert "230 EUR" in public_text(frames)
        assert chat_service.get_session(conversation.sid).assurance_context is None
    restart(checked, conversation)
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_required")


@pytest.mark.parametrize("mode", ["disabled", "shadow"])
def test_recovery_is_inactive_outside_checked_modes(
    solved, checked, conversation, monkeypatch, mode
):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", mode)
    restart(checked, conversation)
    assert chat_service.get_session(conversation.sid).assurance_context is None


def test_private_checkpoint_is_not_a_public_answer_or_evidence(
    solved, checked, conversation
):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    identifier = checkpoint_path(conversation, solved).stem
    for route in ("/api/chat/history", "/api/chat/export"):
        response = checked.client.get(route)
        assert response.status_code == 200
        assert identifier not in response.text and '"run_key"' not in response.text
    for route in ("/api/chat/answers/", "/api/chat/evidence/"):
        assert checked.client.get(route + identifier).status_code == 404


@pytest.mark.parametrize("phase", ["in_flight", "completed"])
def test_history_cannot_clobber_a_stream_that_started_during_recovery(
    solved, checked, conversation, monkeypatch, phase
):
    from harness import assurance_recovery

    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    chat_service.drop_session(conversation.sid)
    original_candidate = assurance_recovery.candidate
    sentinel = {"role": "user", "content": "A newer accepted request"}
    witness = {"anchor": "newer-turn-witness"}

    def raced(session, ctx, last_record):
        result = original_candidate(session, ctx, last_record)
        assert result is not None
        with session._lock:
            session.messages.append(sentinel)
            session.turns_started = 1
            session._turn_in_flight = phase == "in_flight"
            session.assurance_context = witness
        return result

    with monkeypatch.context() as patch:
        patch.setattr(assurance_recovery, "candidate", raced)
        response = checked.client.get("/api/chat/history")
        assert response.status_code == 200
        session = chat_service.get_session(conversation.sid)
        assert list(session.messages) == [sentinel]
        assert session.assurance_context is witness
    with session._lock:
        session._turn_in_flight = False
    chat_service.drop_session(conversation.sid)


@pytest.mark.parametrize("profile", ["legacy_dispatch", "network_cost"])
def test_recovery_covers_other_released_profiles(
    solved, checked, conversation, profile
):
    if profile == "legacy_dispatch":
        values = {
            k: VALUES[k]
            for k in ("dispatch_energy", "marginal_expenditure", "peak_dispatch")
        }
        frames, _ = conversation("Give me the key results for generator G1.", values)
    else:
        values = VALUES
        frames, _ = conversation(
            "Check network.",
            values,
            checked_scope=scope(
                solved, profile="network-linear-cost/1", entity_id="network"
            ),
        )
    assert approval(frames)["receipt"]["request_complete"]
    restart(checked, conversation)
    frames, _ = conversation(
        "Show those results again.",
        values,
        binding_generation=8,
        ui_context={"selected_component": {"class": "Generator", "name": "G1"}},
    )
    receipt = approval(frames)["receipt"]
    assert receipt["request_complete"]
    assert receipt["request_scope"]["entity_id"] == (
        "G1" if profile == "legacy_dispatch" else "network"
    )
    assert "17 MWh" in public_text(frames)


def test_native_answer_copied_to_another_session_cannot_resume(
    solved, checked, conversation
):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    record = chat_service.read_all_turns(solved)[-1]
    other_sid = conversation.sid + "-another"
    chat_service.append_turn(solved, {**record, "session_id": other_sid})
    checked.session_ids.append(other_sid)
    response = checked.client.get("/api/chat/history")
    assert response.status_code == 200
    assert response.json()["last_session_id"] == other_sid
    assert response.json()["turns"][-1]["answer_id"] == record["answer_id"]
    assert chat_service.get_session(other_sid).assurance_context is None


def test_retained_answer_copied_to_another_project_cannot_resume(
    solved, checked, conversation, session_ctx
):
    conversation("Retain this run.", VALUES, checked_scope=scope(solved, retain=True))
    record = chat_service.read_all_turns(solved)[-1]
    response = checked.client.post("/api/projects/Other Recovery Project")
    assert response.status_code == 200
    response = checked.client.post("/api/projects/Other Recovery Project/activate")
    assert response.status_code == 200, response.text
    other_ctx = session_ctx(checked.client)
    assert other_ctx.project_uuid != record["receipt"]["request_scope"]["project_id"]
    chat_service.append_turn(other_ctx, record)
    history = restart(checked, conversation)
    assert history["turns"][-1]["answer_id"] == record["answer_id"]
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_required")


def test_restored_older_transcript_cannot_select_an_older_checkpoint(
    solved, checked, conversation
):
    conversation(
        "Check first window.",
        {"dispatch_energy": 7},
        checked_scope=scope(
            solved,
            metrics=["dispatch_energy"],
            start="2026-01-01T01:00:00",
            end="2026-01-01T01:00:00",
        ),
    )
    first = chat_service.read_all_turns(solved)[-1]
    conversation("Check current.", VALUES, checked_scope=scope(solved))
    path = chat_service.get_persist_path(solved)
    original = path.read_bytes()
    try:
        path.write_text(json.dumps(first) + "\n")
        history = restart(checked, conversation)
        assert history["turns"][-1]["answer_id"] == first["answer_id"]
        frames, provider = conversation("Show those results again.")
        clarified(frames, provider, "conversation_scope_required")
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize("limit", [1, 200])
def test_newer_peer_message_cannot_become_the_owned_scope(
    solved, checked, conversation, limit
):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    chat_service.append_turn(
        solved,
        {
            "ts": 1,
            "session_id": "peer-session",
            "model": chat_service.DEFAULT_MODEL,
            chat_service.TURN_AUTHOR_KEY: "00000000-0000-4000-8000-000000000000",
            "user": "Different peer scope",
            "assistant": [{"type": "text", "text": "Unchecked peer answer"}],
        },
    )
    chat_service.drop_session(conversation.sid)
    response = checked.client.get("/api/chat/history", params={"limit": limit})
    assert (
        response.status_code == 200
        and response.json()["last_session_id"] == conversation.sid
    )
    assert chat_service.get_session(conversation.sid).assurance_context is None
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_required")
