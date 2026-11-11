"""LC-43, LC-D22.concurrency_gates: a write that raced, went stale or met a lock changes nothing.

The seam is the copilot's write tools through the real dispatcher (confirmation cards
included) and the checkpoint tools, on the baseline project (G1 20 MW at 10 EUR/MWh).
"Nothing changed" is the input-table fingerprint of ``test_lc06_d22_undo_checkpoints``.

Hand oracle:
- stale card: the card for G1 marginal cost 10 -> 50 is shown, another writer sets it
  to 77 before the approval; the approval is refused edit_preview_stale and G1 stays
  at 77 (the other writer's value, not 50);
- edit during a restore: an edit landing between the restore's safety save and its
  swap is 409 changed_during_restore, and the inputs stay as they were;
- foreign lock: with another user holding the project's edit lock (a real lock row),
  a checkpoint create is 409 project_locked, a restore is 409 project_locked, and an
  edit through the dispatcher is refused project_locked; the fingerprint and the
  checkpoint list are unchanged;
- undo with a later person's edit on the stack (documented in the tool description):
  the copilot sets marginal cost 30, the person then sets p_nom 40 on the canvas; one
  undo reverts only the person's p_nom (back to 20, cost still 30), a second reverts
  the copilot's cost (back to 10), and a third is refused 409 "Nothing to undo";
- missing checkpoint, kept evidence: a checkpoint whose directory is gone is 404 on
  restore with the inputs unchanged, while the solved run's evidence still exports with
  the same sha256 as before.
"""

# ruff: noqa: F811 -- shared public harness fixtures
import shutil
import time
import uuid

import pytest
from fastapi import HTTPException

from services import chat_service, chat_tools, dirty_state, undo_service
from services.pypsa_service import PyPSAService
from tests.test_assistant_capabilities import (
    dispatch,
    session,  # noqa: F401
    solve_to_completion,
)
from tests.test_lc06_d22_undo_checkpoints import fingerprint
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


def g1(attr: str) -> float:
    return float(PyPSAService.get_network().generators.at["G1", attr])


def _refused(call, status: int, kind) -> None:
    with pytest.raises(HTTPException) as failure:
        call()
    assert failure.value.status_code == status, failure.value.detail
    detail = failure.value.detail
    assert (detail.get("error_kind") if isinstance(detail, dict) else detail) == kind, (
        detail
    )


def _completed_run(timeout: float = 10.0) -> str:
    """The solved run, once its record is final (the record completes just after the solve thread)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = [
            r
            for r in chat_tools.list_project_runs()["items"]
            if r["kind"] == "simulation"
        ]
        if runs and runs[0]["status"] == "completed" and runs[0]["evidence_available"]:
            return runs[0]["run_id"]
        time.sleep(0.05)
    raise AssertionError(f"the run never completed: {runs!r}")


def _errors(frames) -> list[str]:
    return [d["error_kind"] for e, d in frames if e == "tool_error"]


def test_a_card_approved_after_another_write_applies_nothing(baseline, session):
    frames = []
    call = {
        "id": uuid.uuid4().hex,
        "name": "update_component",
        "input": {
            "component_class": "Generator",
            "name": "G1",
            "attrs": {"marginal_cost": 50},
        },
    }
    for event, data in chat_service._dispatch_real_tool_call(session, call, []):
        frames.append((event, data))
        if event == "tool_pending_confirmation":
            assert g1("marginal_cost") == 10.0
            PyPSAService.get_network().generators.at["G1", "marginal_cost"] = 77.0
            session.record_decision(data["confirmation_token"], "approve")
    assert _errors(frames) == ["edit_preview_stale"]
    assert not [d for e, d in frames if e == "tool_result"]
    assert g1("marginal_cost") == 77.0


def test_an_edit_during_a_restore_refuses_the_restore(baseline, session, monkeypatch):
    from routers import snapshots

    checkpoint = chat_tools.create_project_snapshot("Tool Baseline", "S0")
    dispatch(
        session,
        "update_component",
        {"component_class": "Generator", "name": "G1", "attrs": {"marginal_cost": 30}},
    )
    before = fingerprint()
    original = snapshots._create_snapshot_internal

    def save_then_edit(*args, **kwargs):
        info = original(*args, **kwargs)
        dirty_state.bump_revision()
        return info

    monkeypatch.setattr(snapshots, "_create_snapshot_internal", save_then_edit)
    _refused(
        lambda: chat_tools.restore_project_snapshot("Tool Baseline", checkpoint.id),
        409,
        "changed_during_restore",
    )
    assert fingerprint() == before and g1("marginal_cost") == 30.0


def test_another_users_lock_refuses_checkpoint_restore_and_edit(
    baseline, session, _auth_db, second_identity
):
    from db.models import ProjectLock
    from services import project_locks

    _ctx, project_id, _root = baseline
    checkpoint = chat_tools.create_project_snapshot("Tool Baseline", "S0")
    listed = [s.id for s in chat_tools.list_project_snapshots("Tool Baseline")]
    before = fingerprint()
    _engine, session_local = _auth_db
    with session_local() as db:
        held = db.get(ProjectLock, uuid.UUID(project_id))
        if held is not None:  # the save made the acting user the holder
            db.delete(held)
            db.commit()
        assert (
            project_locks.acquire_lock(
                db, uuid.UUID(project_id), second_identity["user_id"]
            )
            is not None
        )

    _refused(
        lambda: chat_tools.create_project_snapshot("Tool Baseline", "S1"),
        409,
        "project_locked",
    )
    _refused(
        lambda: chat_tools.restore_project_snapshot("Tool Baseline", checkpoint.id),
        409,
        "project_locked",
    )
    frames = dispatch(
        session,
        "update_component",
        {"component_class": "Generator", "name": "G1", "attrs": {"marginal_cost": 30}},
    )
    assert _errors(frames) == ["project_locked"]
    assert fingerprint() == before and g1("marginal_cost") == 10.0
    assert [s.id for s in chat_tools.list_project_snapshots("Tool Baseline")] == listed


def test_undo_reverts_the_newest_step_even_when_it_is_the_persons(
    baseline, session, client
):
    undo_service.clear()
    session._undo_snapshotted = []
    original = fingerprint()
    dispatch(
        session,
        "update_component",
        {"component_class": "Generator", "name": "G1", "attrs": {"marginal_cost": 30}},
    )
    response = client.put(
        "/api/network/generators/G1", json={"name": "G1", "bus": "B1", "p_nom": 40}
    )
    assert response.status_code == 200, response.text
    assert (g1("marginal_cost"), g1("p_nom")) == (30.0, 40.0)

    assert chat_tools.undo_last() == {"undone": True, "remaining": 1}
    assert (g1("marginal_cost"), g1("p_nom")) == (30.0, 20.0)
    assert chat_tools.undo_last() == {"undone": True, "remaining": 0}
    assert (g1("marginal_cost"), g1("p_nom")) == (10.0, 20.0)
    assert fingerprint() == original
    _refused(chat_tools.undo_last, 409, "Nothing to undo")
    assert fingerprint() == original


def test_a_missing_checkpoint_restores_nothing_and_the_run_evidence_stays(
    baseline, session
):
    _ctx, _project_id, root = baseline
    solve_to_completion(session)
    run_id = _completed_run()
    kept = chat_tools.export_run_evidence(run_id)["sha256"]
    checkpoint = chat_tools.create_project_snapshot("Tool Baseline", "S0")
    dispatch(
        session,
        "update_component",
        {"component_class": "Generator", "name": "G1", "attrs": {"marginal_cost": 30}},
    )
    before = fingerprint()

    shutil.rmtree(root / "snapshots" / checkpoint.id)
    _refused(
        lambda: chat_tools.restore_project_snapshot("Tool Baseline", checkpoint.id),
        404,
        f"Snapshot '{checkpoint.id}' not found (or incomplete)",
    )
    assert fingerprint() == before and g1("marginal_cost") == 30.0
    assert chat_tools.export_run_evidence(run_id)["sha256"] == kept
