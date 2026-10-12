"""Queued corrections cannot inherit another utterance's explicit scope."""

# ruff: noqa: F811 -- imported shared fixtures.
import uuid

import pytest

from services import assurance_records, chat_service, utterance_admission as ua
from tests.assurance_acceptance.support import approval, parse_sse, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider, solved  # noqa: F401
from tests.test_harness_reliability import scope
from tests.test_workflow_tools import baseline  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_admission(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    ua._reset_for_tests()
    yield
    ua._reset_for_tests()


@pytest.mark.parametrize("constrained", [False, True])
def test_queued_checked_followup_resolves_its_own_scope(solved, checked, constrained):
    sid = "checked-drain-" + uuid.uuid4().hex
    checked.session_ids.append(sid)
    followup = "Show those results again."
    if constrained:
        followup = "Show those results again, but only for 2025."

    class QueuingProvider(NumericalProvider):
        def stream(self, request):
            if not self.requests:
                decision = ua.admit_utterance(
                    ua.StableUtterance(
                        utterance_id="correction", text=followup,
                        generation=1, session_id=sid,
                    )
                )
                assert decision.status == "queued"
            yield from super().stream(request)

    provider = checked.install(QueuingProvider({"dispatch_energy": 17}))
    exact = scope(
        solved, metrics=["dispatch_energy"],
        start="2026-01-01T00:00:00", end="2026-01-01T01:00:00",
    )
    response = checked.client.post(
        "/api/chat/stream",
        json={
            "session_id": sid, "message": "Check this exact run.",
            "utterance_id": "initial", "generation": 1,
            "binding_generation": 3, "checked_scope": exact,
        },
    )
    assert response.status_code == 200, response.text
    frames = parse_sse(response.text)
    completed = [i for i, (event, _) in enumerate(frames) if event == "turn_done"]
    assert len(completed) == 2
    first = approval(frames[:completed[0] + 1])
    second_frames = frames[completed[0] + 1:completed[1] + 1]
    second = approval(second_frames)
    assert first["receipt"]["request_complete"]
    assert ua._DEFAULT.active_lease(sid) is None
    session = chat_service.get_session(sid)
    if constrained:
        assert second["verdict"] == "limited"
        assert second["receipt"]["request_complete"] is False
        assert second["receipt"]["checked_block_ids"] == []
        assert second["receipt"]["evidence_ids"] == []
        assert all(
            slot["reason"] == "unsupported_followup"
            for slot in second["receipt"]["result_slots"]
        )
        assert len(provider.requests) == 1
        assert "17 MWh" not in public_text(second_frames)
        assert session.assurance_context is None
        from harness.assurance_recovery import KIND, checkpoint_id
        checkpoint = assurance_records.load(
            session.owner_user_id,
            checkpoint_id(session.owner_user_id, solved.project_uuid, sid), KIND,
        )
        assert checkpoint["state"] is None
    else:
        assert second["receipt"]["request_complete"]
        assert "17 MWh" in public_text(second_frames)
        assert len(provider.requests) == 2
        for key in ("project_id", "entity_id", "metrics", "start", "end", "cost_basis"):
            assert second["receipt"]["request_scope"][key] == exact[key]
        assert session.assurance_context["anchor"] == second["answer_id"]
