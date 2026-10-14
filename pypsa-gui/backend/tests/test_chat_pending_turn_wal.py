"""
Improvement #20 / QA #10 — pending-turn WAL and corruption signalling.

Two defects, one file, because both are about what `chat.jsonl` can no longer
hide:

  * `read_all_turns` skips an unparseable line and says nothing. A transcript
    that lost a turn to a torn write reads as a transcript that never had it,
    so a reload silently shows a shorter conversation than the one the user
    had. The skip count has to reach the caller.

  * A turn that dies between "the user pressed Send" and "the turn completed"
    leaves NO trace at all — `append_turn` only runs on the success path. A
    pending record written at turn start closed that; since issue 13 part b
    it is the session event log's `turn/start` (see the note at the end).
"""
from __future__ import annotations

import json
import pathlib

import pypsa
import pytest

from services import chat_service
from services.project_context import ProjectContext


@pytest.fixture(autouse=True)
def _reset_chat_sessions():
    chat_service._reset_sessions_for_tests()
    yield
    chat_service._reset_sessions_for_tests()


def _make_bound_ctx(name: str, projects_dir: pathlib.Path) -> ProjectContext:
    n = pypsa.Network()
    n.add("Bus", "B1")
    ctx = ProjectContext(network=n, loaded_project=name)
    chat_service.get_persist_path(ctx)
    (projects_dir / name).mkdir(parents=True, exist_ok=True)
    return ctx


# ── corruption signalling ───────────────────────────────────────────────────


def test_read_all_turns_with_gap_counts_the_lines_it_skipped(
    tmp_projects_dir, monkeypatch,
):
    """
    A torn line between two good ones yields two turns AND a gap count of 1.

    The count is the whole point: `read_all_turns` already skips the line, so
    a test that only asserted on the surviving turns would pass against the
    unfixed code.
    """
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("gapproj", tmp_projects_dir)
    path = chat_service.get_persist_path(ctx)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"user": "first"}) + "\n"
        + '{"user": "torn half of a rec'  + "\n"
        + json.dumps({"user": "third"}) + "\n",
        encoding="utf-8",
    )

    turns, gap = chat_service.read_all_turns_with_gap(ctx)

    assert [t["user"] for t in turns] == ["first", "third"]
    assert gap == 1


def test_read_all_turns_with_gap_reports_zero_on_a_clean_transcript(
    tmp_projects_dir, monkeypatch,
):
    """A healthy file must not report a gap — no false 'history damaged' banner."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("cleanproj", tmp_projects_dir)
    chat_service.append_turn(ctx, {"user": "one"})
    chat_service.append_turn(ctx, {"user": "two"})

    turns, gap = chat_service.read_all_turns_with_gap(ctx)

    assert [t["user"] for t in turns] == ["one", "two"]
    assert gap == 0


def test_chat_history_surfaces_the_gap_count(
    install_network, tmp_projects_dir, monkeypatch, client,
):
    """
    GET /history must report `history_gap` so the panel can tell the user its
    transcript is incomplete rather than quietly rendering a shorter one.
    """
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="GapHistory")
    (tmp_projects_dir / "GapHistory").mkdir(exist_ok=True)

    from services.pypsa_service import PyPSAService
    ctx = PyPSAService.get_active_context()
    path = chat_service.get_persist_path(ctx)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"session_id": "s1", "user": "kept", "model": "m"}) + "\n"
        + "{not json at all\n",
        encoding="utf-8",
    )

    r = client.get("/api/chat/history")

    assert r.status_code == 200
    body = r.json()
    assert len(body["turns"]) == 1
    assert body["history_gap"] == 1


# The pending-turn WAL tests that used to follow are retired with the WAL
# (chat harness issue 13, part b): the session event log's `turn/start` took
# its place, and `tests/test_harness_eventlog_truth.py` pins the same four
# behaviours on it (on disk while the model streams, nothing left by a
# completed turn, reported once then closed, a live turn left alone).
