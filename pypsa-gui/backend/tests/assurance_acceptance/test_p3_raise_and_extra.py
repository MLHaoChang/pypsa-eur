"""Honest outcomes on the raise path and for extra edits (issue 04, slice S4b).

GK B (27) G4 (lock order), G5 (R3) and G6:
- G5: a write whose handler raised reads `failed` only when its note's
  read-back compared the state before and after and found them equal; no note,
  an uncomparable read or a read error gives uncertain. A timeout or a call
  that never started is always uncertain and never read back.
- G6: a row write's read-back diffs the whole state (every class's static
  cells, the series, `n.meta`, the weightings, the user series). A changed
  path the call did not name is an extra edit: partial `extra_edits`.
  The return path's `failed` needs that diff empty too.
- G4: an edit-lock refusal reads `denied` only when it comes before any effect
  seam (reservation, undo snapshot, write note, dirty mark, revision bump).
"""

from __future__ import annotations

import uuid

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from harness import outcomes
from services import chat_tools, dirty_state, undo_service
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.test_p3_required_guards import spies  # noqa: F401
from tests.test_chat_tool_executor_starvation import saturated_pool  # noqa: F401

UPDATE = {
    "component_class": "Generator",
    "names": ["G1"],
    "updates": {"marginal_cost": 61.5},
}


def _net():
    return PyPSAService.get_active_context().network


def _frames(monkeypatch, tool, args, *, assurance_turn=None):
    """One ordinary-turn call through the real dispatch seam; the user approves.
    `assurance_turn` stands in for a checked turn's guard when given."""
    from harness.loop import dispatch_tool_call
    from harness.session import ChatSession

    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled")
    session, collector, frames = ChatSession(), [], []
    session.assurance_turn = assurance_turn
    for event, data in dispatch_tool_call(
        session,
        {"id": uuid.uuid4().hex, "name": tool, "input": args},
        collector,
        guided=True,
    ):
        if event == "tool_pending_confirmation":
            session.record_decision(data["confirmation_token"], "approve")
        frames.append((event, data))
    return frames, collector


def _result(frames):
    frames = frames[0] if isinstance(frames, tuple) else frames
    (result,) = [d["result"] for e, d in frames if e == "tool_result"]
    return result


def _error(frames):
    (error,) = [d for e, d in frames if e == "tool_error"]
    return error


def _wrap(monkeypatch, tool, after=None, before=None, raises=False):
    """The real handler, with an extra effect before or after it, then a raise."""
    real = chat_tools.DISPATCHERS[tool]

    def handler(**kw):
        if before:
            before()
        out = real(**kw)
        if after:
            after()
        if raises:
            raise HTTPException(500, "broke after the write")
        return out

    monkeypatch.setitem(chat_tools.DISPATCHERS, tool, handler)


def _set_bus_v_nom():
    _net().buses.at["B1", "v_nom"] = 220.0


def _set_user_series():
    from services.user_timeseries import _user_ts

    _user_ts[("loads", "p_set", "L1")] = pd.Series([1.0, 2.0], index=_net().snapshots)


# ── G6: extra edits ──────────────────────────────────────────────────────────


def test_a_write_that_also_edits_another_class_reads_partial_extra_edits(
    baseline, monkeypatch
):
    _wrap(monkeypatch, "bulk_update_components", after=_set_bus_v_nom)
    block = _result(_frames(monkeypatch, "bulk_update_components", UPDATE))["_outcome"]
    assert (block["outcome"], block["reason"]) == ("partial", "extra_edits")
    assert block["counts"] == {
        "requested": 1,
        "done": 1,
        "not_done": 0,
        "unknown": 0,
        "extra": 1,
    }
    assert block["paths_extra"] == ["/Bus/B1/v_nom"]


def test_a_write_that_also_edits_a_user_series_reads_partial_extra_edits(
    baseline, monkeypatch
):
    _wrap(monkeypatch, "bulk_update_components", after=_set_user_series)
    block = _result(_frames(monkeypatch, "bulk_update_components", UPDATE))["_outcome"]
    assert (block["outcome"], block["reason"]) == ("partial", "extra_edits")
    assert block["paths_extra"] == ["/user_ts/loads/p_set/L1"]


def test_a_write_that_also_edits_n_meta_reads_partial_extra_edits(
    baseline, monkeypatch
):
    def meta():
        _net().meta["note"] = "set by the handler"

    _wrap(monkeypatch, "bulk_update_components", after=meta)
    block = _result(_frames(monkeypatch, "bulk_update_components", UPDATE))["_outcome"]
    assert block["reason"] == "extra_edits"
    assert block["paths_extra"] == ["/meta/note"]


def test_a_write_that_also_edits_the_weightings_reads_partial_extra_edits(
    baseline, monkeypatch
):
    def weights():
        _net().snapshot_weightings.loc[:, "objective"] = 2.0

    _wrap(monkeypatch, "bulk_update_components", after=weights)
    block = _result(_frames(monkeypatch, "bulk_update_components", UPDATE))["_outcome"]
    assert block["paths_extra"] == ["/snapshot_weightings"]


def test_a_write_that_also_edits_a_series_reads_partial_extra_edits(
    baseline, monkeypatch
):
    def series():
        _net().loads_t.p_set["L1"] = [7.0, 8.0]

    _wrap(monkeypatch, "bulk_update_components", after=series)
    block = _result(_frames(monkeypatch, "bulk_update_components", UPDATE))["_outcome"]
    assert block["paths_extra"] == ["/Load/L1/p_set@series"]


def test_a_short_write_with_an_extra_edit_keeps_both_counts(baseline, monkeypatch):
    chat_tools.batch_create_components(
        "Generator", [{"name": "G2", "bus": "B1", "p_nom": 5}]
    )
    args = {**UPDATE, "names": ["G1", "G2"]}
    note = outcomes.note_write("bulk_update_components", args)
    chat_tools.bulk_update_components("Generator", ["G1"], {"marginal_cost": 61.5})
    _set_bus_v_nom()
    block = outcomes.project_write(note)
    assert (block["outcome"], block["reason"]) == ("partial", "extra_edits")
    assert block["counts"]["done"] == 1 and block["counts"]["requested"] == 2
    assert block["counts"]["extra"] == 1
    assert block["paths_not_done"] == ["/Generator/G2/marginal_cost"]


def test_extra_paths_are_bounded(baseline):
    note = outcomes.note_write("bulk_update_components", UPDATE)
    chat_tools.bulk_update_components("Generator", ["G1"], {"marginal_cost": 61.5})
    for i in range(8):
        _net().meta[f"k{i}"] = i
    block = outcomes.project_write(note)
    assert block["counts"]["extra"] == 8
    assert len(block["paths_extra"]) == 5


def test_a_create_that_also_adds_an_unrelated_carrier_reads_partial(
    baseline, monkeypatch
):
    def carrier():
        _net().add("Carrier", "wind", co2_emissions=0.5)

    _wrap(monkeypatch, "batch_create_components", after=carrier)
    args = {"component_class": "Bus", "components": [{"name": "N1", "v_nom": 20.0}]}
    block = _result(_frames(monkeypatch, "batch_create_components", args))["_outcome"]
    assert (block["outcome"], block["reason"]) == ("partial", "extra_edits")
    assert "/Carrier/wind/co2_emissions" in block["paths_extra"]
    assert all(p.startswith("/Carrier/wind/") for p in block["paths_extra"])


def test_a_bus_create_with_its_own_new_carrier_is_completed(baseline, monkeypatch):
    # pypsa adds the "AC" carrier a new Bus names: implied, not extra.
    assert "AC" not in _net().carriers.index
    args = {"component_class": "Bus", "components": [{"name": "N1", "v_nom": 20.0}]}
    block = _result(_frames(monkeypatch, "batch_create_components", args))["_outcome"]
    assert block["outcome"] == "completed", block
    assert "AC" in _net().carriers.index


def test_a_delete_with_its_own_series_and_meta_entry_is_completed(
    baseline, monkeypatch
):
    _set_user_series()
    _net().meta["vintage_bounds"] = {"Load": {"L1": {"x": 1}}}
    args = {"component_class": "Load", "name": "L1"}
    block = _result(_frames(monkeypatch, "delete_component", args))["_outcome"]
    assert block["outcome"] == "completed", block


def test_a_delete_that_also_drops_another_rows_series_is_partial(baseline, monkeypatch):
    from services.user_timeseries import _user_ts

    _user_ts[("generators", "p_max_pu", "G1")] = pd.Series(
        [0.5, 0.6], index=_net().snapshots
    )

    def drop():
        del _user_ts[("generators", "p_max_pu", "G1")]

    _wrap(monkeypatch, "delete_component", after=drop)
    args = {"component_class": "Load", "name": "L1"}
    block = _result(_frames(monkeypatch, "delete_component", args))["_outcome"]
    assert block["reason"] == "extra_edits"
    assert block["paths_extra"] == ["/user_ts/generators/p_max_pu/G1"]


def test_a_new_column_empty_on_other_rows_is_no_change(baseline):
    before = outcomes.state_map(_net(), {})
    _net().generators["custom_note"] = np.nan
    _net().generators["custom_text"] = ""
    _net().meta["empty_store"] = {}
    assert outcomes.changed_paths(before, outcomes.state_map(_net(), {})) == []


def test_a_dtype_change_alone_is_no_change(baseline):
    before = outcomes.state_map(_net(), {})
    _net().generators["p_nom"] = _net().generators["p_nom"].astype(object)
    assert outcomes.changed_paths(before, outcomes.state_map(_net(), {})) == []


def test_a_write_that_changed_its_cell_to_another_value_is_not_failed(
    baseline, monkeypatch
):
    """G5 on the return path: nothing done as asked, but the named cell moved."""
    real = chat_tools.DISPATCHERS["bulk_update_components"]

    def wrong(**kw):
        return real(**{**kw, "updates": {"marginal_cost": 99.0}})

    monkeypatch.setitem(chat_tools.DISPATCHERS, "bulk_update_components", wrong)
    block = _result(_frames(monkeypatch, "bulk_update_components", UPDATE))["_outcome"]
    assert (block["outcome"], block["reason"]) == ("uncertain", "not_as_requested")


def test_a_write_that_changed_nothing_is_still_failed(baseline):
    note = outcomes.note_write("bulk_update_components", UPDATE)
    assert outcomes.project_write(note)["outcome"] == "failed"


def test_an_uncomparable_state_is_never_completed(baseline, monkeypatch):
    monkeypatch.setattr(outcomes, "state_map", lambda *a: outcomes.UNCOMPARABLE)
    block = _result(_frames(monkeypatch, "bulk_update_components", UPDATE))["_outcome"]
    assert (block["outcome"], block["reason"]) == ("uncertain", "not_comparable")


def test_state_map_cost_is_bounded_on_a_template(baseline):
    """The whole-state map runs twice per row write; stated in the PR."""
    import time

    from tests.assurance_acceptance.test_p3_file_outcomes import _template_bytes

    n = outcomes._load_netcdf(_template_bytes())
    start = time.perf_counter()
    assert isinstance(outcomes.state_map(n, {}), dict)
    assert time.perf_counter() - start < 2.0


# ── G5: the raise path ──────────────────────────────────────────────────────


def test_a_raise_that_changed_nothing_reads_failed(baseline, monkeypatch):
    def boom(**_kw):
        raise HTTPException(409, "refused")

    monkeypatch.setitem(chat_tools.DISPATCHERS, "bulk_update_components", boom)
    frames, collector = _frames(monkeypatch, "bulk_update_components", UPDATE)
    error = _error(frames)
    assert error["_outcome"] == {"version": 1, "outcome": "failed", "reason": None}
    assert collector[-1]["is_error"] is True


def test_a_raise_after_the_write_landed_reads_uncertain(baseline, monkeypatch):
    _wrap(monkeypatch, "bulk_update_components", raises=True)
    error = _error(_frames(monkeypatch, "bulk_update_components", UPDATE)[0])
    assert error["_outcome"]["outcome"] == "uncertain"
    assert error["_outcome"]["reason"] == "changed_before_error"


def test_a_raise_after_an_unasked_edit_only_reads_uncertain(baseline, monkeypatch):
    def boom(**_kw):
        _set_user_series()
        raise HTTPException(500, "broke")

    monkeypatch.setitem(chat_tools.DISPATCHERS, "bulk_update_components", boom)
    error = _error(_frames(monkeypatch, "bulk_update_components", UPDATE)[0])
    assert error["_outcome"]["reason"] == "changed_before_error"


def test_a_raise_with_another_writer_reads_revision_moved(baseline, monkeypatch):
    def boom(**_kw):
        dirty_state.bump_revision()
        raise HTTPException(500, "broke")

    monkeypatch.setitem(chat_tools.DISPATCHERS, "bulk_update_components", boom)
    error = _error(_frames(monkeypatch, "bulk_update_components", UPDATE)[0])
    assert error["_outcome"]["reason"] == "revision_moved"


def test_a_raise_with_an_unreadable_state_is_uncertain(baseline, monkeypatch):
    real = outcomes._live_state
    calls = []

    def second_breaks(ctx):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("unreadable")
        return real(ctx)

    def boom(**_kw):
        raise HTTPException(409, "refused")

    monkeypatch.setattr(outcomes, "_live_state", second_breaks)
    monkeypatch.setitem(chat_tools.DISPATCHERS, "bulk_update_components", boom)
    error = _error(_frames(monkeypatch, "bulk_update_components", UPDATE)[0])
    assert error["_outcome"]["reason"] == "readback_failed"


def test_a_raise_with_no_note_is_uncertain(baseline, monkeypatch):
    def boom(**_kw):
        raise HTTPException(409, "refused")

    monkeypatch.setattr(outcomes, "_live_state", lambda ctx: 1 / 0)
    monkeypatch.setitem(chat_tools.DISPATCHERS, "bulk_update_components", boom)
    error = _error(_frames(monkeypatch, "bulk_update_components", UPDATE)[0])
    assert error["_outcome"]["reason"] == "no_submission"


def test_a_raise_with_an_uncomparable_state_is_uncertain(baseline, monkeypatch):
    def boom(**_kw):
        raise HTTPException(409, "refused")

    monkeypatch.setattr(outcomes, "state_map", lambda *a: outcomes.UNCOMPARABLE)
    monkeypatch.setitem(chat_tools.DISPATCHERS, "bulk_update_components", boom)
    error = _error(_frames(monkeypatch, "bulk_update_components", UPDATE)[0])
    assert error["_outcome"]["reason"] == "not_comparable"


def _undoable_edit(monkeypatch):
    _frames(
        monkeypatch,
        "bulk_update_components",
        {**UPDATE, "updates": {"marginal_cost": 33.0}},
    )
    assert undo_service.depth() >= 1


def test_an_undo_that_raised_before_popping_reads_failed(baseline, monkeypatch):
    _undoable_edit(monkeypatch)

    def boom(**_kw):
        raise HTTPException(409, "refused")

    monkeypatch.setitem(chat_tools.DISPATCHERS, "undo_last", boom)
    error = _error(_frames(monkeypatch, "undo_last", {})[0])
    assert error["_outcome"]["outcome"] == "failed"


def test_an_undo_that_raised_after_restoring_reads_uncertain(baseline, monkeypatch):
    _undoable_edit(monkeypatch)
    _wrap(monkeypatch, "undo_last", raises=True)
    error = _error(_frames(monkeypatch, "undo_last", {})[0])
    assert error["_outcome"]["outcome"] == "uncertain"
    assert error["_outcome"]["reason"] == "changed_before_error"


def test_an_undo_that_popped_then_raised_reads_uncertain(baseline, monkeypatch):
    """The entry is gone though the network is as before: not unchanged."""
    _undoable_edit(monkeypatch)

    def pops_then_raises(**_kw):
        undo_service.pop()
        raise HTTPException(500, "broke")

    monkeypatch.setitem(chat_tools.DISPATCHERS, "undo_last", pops_then_raises)
    error = _error(_frames(monkeypatch, "undo_last", {})[0])
    assert error["_outcome"]["outcome"] == "uncertain"


def test_an_export_that_raised_reads_no_outcome_pending(baseline, monkeypatch):
    """AA04-P9: imports and exports have no unchanged-proof on a raise yet."""

    def boom(**_kw):
        raise HTTPException(409, "refused")

    monkeypatch.setitem(chat_tools.DISPATCHERS, "export_network_nc", boom)
    error = _error(_frames(monkeypatch, "export_network_nc", {})[0])
    assert "_outcome" not in error


def test_a_slow_write_that_changed_nothing_still_reads_uncertain_on_timeout(
    baseline, monkeypatch
):
    """Mutant guard: the timeout branch never consults the read-back, which
    here would find the state unchanged and say failed."""
    import threading

    from harness import budget as harness_budget

    release = threading.Event()

    def slow(**_kw):
        release.wait(10)
        raise HTTPException(409, "refused")

    monkeypatch.setitem(chat_tools.DISPATCHERS, "bulk_update_components", slow)
    monkeypatch.setattr(harness_budget, "PER_TOOL_TIMEOUT_SECONDS", 0.2)
    try:
        error = _error(_frames(monkeypatch, "bulk_update_components", UPDATE)[0])
    finally:
        release.set()
    assert error["error_kind"] == "tool_timeout"
    assert error["_outcome"] == {
        "version": 1,
        "outcome": "uncertain",
        "reason": "tool_timeout",
    }


def test_a_write_that_never_started_reads_uncertain(
    baseline,
    monkeypatch,
    saturated_pool,  # noqa: F811
):
    from harness import budget as harness_budget

    ran = []
    monkeypatch.setitem(
        chat_tools.DISPATCHERS,
        "bulk_update_components",
        lambda **_kw: ran.append(1),
    )
    monkeypatch.setattr(harness_budget, "PER_TOOL_TIMEOUT_SECONDS", 0.3)
    error = _error(_frames(monkeypatch, "bulk_update_components", UPDATE)[0])
    assert error["error_kind"] == "tool_not_started"
    assert error["_outcome"] == {
        "version": 1,
        "outcome": "uncertain",
        "reason": "tool_not_started",
    }
    assert not ran


def test_a_read_tool_that_times_out_carries_no_write_outcome():
    from harness.loop import _timeout_outcome

    assert _timeout_outcome(None, "tool_timeout") == {}


def _raised_note(**over):
    return {
        "tool": "bulk_update_components",
        "revision": 3,
        "state": {"/a": "1"},
        **over,
    }


def _raised_read(**over):
    return {"revision_start": 3, "revision_end": 3, "state": {"/a": "1"}, **over}


@pytest.mark.parametrize(
    "note, read, expected",
    [
        (_raised_note(), _raised_read(), ("failed", None)),
        (None, _raised_read(), ("uncertain", "no_submission")),
        (_raised_note(error=True), _raised_read(), ("uncertain", "no_submission")),
        (_raised_note(), {"error": "x"}, ("uncertain", "readback_failed")),
        (_raised_note(), None, ("uncertain", "readback_failed")),
        (
            _raised_note(),
            _raised_read(revision_start=4),
            ("uncertain", "revision_moved"),
        ),
        (_raised_note(), _raised_read(revision_end=4), ("uncertain", "revision_moved")),
        (
            _raised_note(),
            _raised_read(state={"/a": "2"}),
            ("uncertain", "changed_before_error"),
        ),
        (
            _raised_note(),
            _raised_read(state={"/a": "1", "/b": "1"}),
            ("uncertain", "changed_before_error"),
        ),
        (
            _raised_note(),
            _raised_read(state=outcomes.UNCOMPARABLE),
            ("uncertain", "not_comparable"),
        ),
        (
            _raised_note(state=outcomes.UNCOMPARABLE),
            _raised_read(),
            ("uncertain", "not_comparable"),
        ),
        (_raised_note(), _raised_read(state=None), ("uncertain", "not_comparable")),
    ],
)
def test_classify_raised_table(note, read, expected):
    block = outcomes.classify_raised(note, read)
    assert (block["outcome"], block["reason"]) == expected


def _undo_note(**over):
    return {
        "tool": "undo_last",
        "revision": 3,
        "pops": 0,
        "depth": 1,
        "digest_before": "d0",
        "meta_before": None,
        "user_ts_before": {},
        "expected": {"sha": "s", "digest": "d1", "user_ts": {}, "template_meta": None},
        **over,
    }


def _undo_read(**over):
    return {
        "revision_start": 3,
        "revision_end": 3,
        "pops": 0,
        "own_pops": [],
        "depth": 1,
        "digest": "d0",
        "template_meta": None,
        "user_ts": {},
        **over,
    }


@pytest.mark.parametrize(
    "read, expected",
    [
        (_undo_read(), ("failed", None)),
        (_undo_read(digest="dx"), ("uncertain", "not_restored")),
        (_undo_read(depth=0), ("uncertain", "not_restored")),
        (
            _undo_read(pops=1, own_pops=["s"], depth=0, digest="d1"),
            ("uncertain", "changed_before_error"),
        ),
        (_undo_read(pops=1, own_pops=[]), ("uncertain", "entry_moved")),
        ({"error": "x"}, ("uncertain", "readback_failed")),
    ],
)
def test_classify_raised_undo_table(read, expected):
    block = outcomes.classify_raised(_undo_note(), read)
    assert (block["outcome"], block["reason"]) == expected


# ── G4: the edit-lock refusal ───────────────────────────────────────────────


@pytest.fixture
def held(baseline):
    from services import harness_locks

    ctx, _pid, _path = baseline
    uuid_ = PyPSAService.get_active_context().project_uuid
    assert uuid_
    yield lambda: harness_locks.hold(uuid_, "another-run")
    harness_locks._reset_for_tests()


def test_bulk_update_is_lock_gated():
    assert "bulk_update_components" in chat_tools._lock_gated_tool_names()


def test_a_lock_refusal_before_any_effect_reads_denied(monkeypatch, spies, held):  # noqa: F811
    seen, files = spies
    held()
    before = files()
    seen.clear()
    frames, collector = _frames(monkeypatch, "bulk_update_components", UPDATE)
    error = _error(frames)
    assert error["error_kind"] == "project_locked"
    assert error["_outcome"] == {
        "version": 1,
        "outcome": "denied",
        "reason": "project_locked",
    }
    assert collector[-1]["is_error"] is True
    assert "project_locked" in str(collector[-1]["content"])
    assert seen == []
    assert files() == before
    assert float(_net().generators.at["G1", "marginal_cost"]) == 10.0


def test_a_lock_that_cannot_be_checked_reads_denied(monkeypatch, spies):  # noqa: F811
    seen, _files = spies

    def unavailable(tool_name, task_id=None):
        raise HTTPException(
            503,
            detail={"error_kind": "project_lock_unavailable", "message": "retry"},
        )

    monkeypatch.setattr(chat_tools, "_check_foreign_lock", unavailable)
    seen.clear()
    error = _error(_frames(monkeypatch, "bulk_update_components", UPDATE)[0])
    assert error["_outcome"]["outcome"] == "denied"
    assert error["_outcome"]["reason"] == "project_lock_unavailable"
    assert seen == []


def test_a_lock_taken_after_the_dirty_mark_is_never_denied(monkeypatch, spies, held):  # noqa: F811
    """The order test: the lock appears inside mark_dirty, so the refusal comes
    from the handler's own check, after effect seams ran; it reads failed
    (proven unchanged) or uncertain, never denied."""
    seen, _files = spies
    mark = dirty_state.mark_dirty

    def mark_then_lock(*a, **k):
        out = mark(*a, **k)
        held()
        return out

    monkeypatch.setattr(dirty_state, "mark_dirty", mark_then_lock)
    seen.clear()
    error = _error(_frames(monkeypatch, "bulk_update_components", UPDATE)[0])
    assert error["error_kind"] == "project_locked"
    assert error["_outcome"]["outcome"] == "failed"
    assert "note_write" in seen


def test_a_run_control_on_the_callers_own_run_is_not_denied(monkeypatch):
    from harness import guards

    monkeypatch.setattr(chat_tools, "_lock_gated_tool_names", lambda: frozenset({"x"}))
    calls = []
    monkeypatch.setattr(
        chat_tools,
        "_check_foreign_lock",
        lambda name, task_id=None: calls.append((name, task_id)),
    )
    assert guards.lock_refusal("x", {"task_id": "t-1"}) is None
    assert calls == [("x", "t-1")]
    assert guards.lock_refusal("y", {}) is None
    assert calls == [("x", "t-1")]


def test_another_refusal_from_the_lock_check_is_not_swallowed(monkeypatch):
    from harness import guards

    def other(tool_name, task_id=None):
        raise HTTPException(401, detail={"error_kind": "unauthorized"})

    monkeypatch.setattr(chat_tools, "_check_foreign_lock", other)
    with pytest.raises(HTTPException):
        guards.lock_refusal("bulk_update_components", {})


# ── GK B (28) H1–H8 ─────────────────────────────────────────────────────────


def test_a_real_foreign_user_lock_is_denied_before_any_seam(
    monkeypatch,
    spies,  # noqa: F811
    baseline,
    _auth_db,
    second_identity,
):
    """H1/H2: another user's edit lock in the DB, the real check, the acting
    identity and context the handler would get; nothing ran, begin_step included."""
    from services import assistant_tasks
    from tests.test_chat_tools_dispatch import _hold_foreign_lock

    seen, files = spies
    real_begin = assistant_tasks.begin_step

    def begin(*a, **k):
        seen.append("begin_step")
        return real_begin(*a, **k)

    monkeypatch.setattr(assistant_tasks, "begin_step", begin)
    called = []
    real = chat_tools.DISPATCHERS["bulk_update_components"]
    monkeypatch.setitem(
        chat_tools.DISPATCHERS,
        "bulk_update_components",
        lambda **kw: (called.append(1), real(**kw))[1],
    )
    _ctx, project_id, _path = baseline
    _engine, session_local = _auth_db
    _hold_foreign_lock(session_local, uuid.UUID(project_id), second_identity["user_id"])
    before = files()
    seen.clear()
    frames, collector = _frames(monkeypatch, "bulk_update_components", UPDATE)
    error = _error(frames)
    assert error["error_kind"] == "project_locked"
    assert error["_outcome"]["outcome"] == "denied"
    assert "another user" in error["message"]
    assert called == [] and seen == []
    assert files() == before


def test_the_harness_run_hold_is_denied_with_its_kind(
    monkeypatch,
    spies,  # noqa: F811
    held,
):
    from services import harness_locks

    seen, _files = spies
    held()
    seen.clear()
    error = _error(_frames(monkeypatch, "bulk_update_components", UPDATE)[0])
    assert harness_locks.detail()["error_kind"] == error["_outcome"]["reason"]
    assert error["message"] == harness_locks.MESSAGE
    assert seen == []


def test_an_import_that_raised_with_nothing_changed_reads_failed(baseline, monkeypatch):
    """H5: an import uses the same whole-state compare on a raise."""
    from tests.assurance_acceptance.test_p3_file_outcomes import _b64, _template_bytes

    def boom(**_kw):
        raise HTTPException(422, "bad file")

    monkeypatch.setitem(chat_tools.DISPATCHERS, "import_network_nc", boom)
    args = {"bytes_b64": _b64(_template_bytes())}
    error = _error(_frames(monkeypatch, "import_network_nc", args)[0])
    assert error["_outcome"]["outcome"] == "failed"


def test_an_import_that_changed_the_network_then_raised_reads_uncertain(
    baseline, monkeypatch
):
    from tests.assurance_acceptance.test_p3_file_outcomes import _b64, _template_bytes

    def boom(**_kw):
        _set_bus_v_nom()
        raise HTTPException(500, "broke half way")

    monkeypatch.setitem(chat_tools.DISPATCHERS, "import_network_nc", boom)
    args = {"bytes_b64": _b64(_template_bytes())}
    error = _error(_frames(monkeypatch, "import_network_nc", args)[0])
    assert error["_outcome"]["outcome"] == "uncertain"
    assert error["_outcome"]["reason"] == "changed_before_error"


def test_an_import_that_landed_then_raised_is_never_failed(baseline, monkeypatch):
    from tests.assurance_acceptance.test_p3_file_outcomes import _b64, _template_bytes

    _wrap(monkeypatch, "import_network_nc", raises=True)
    args = {"bytes_b64": _b64(_template_bytes())}
    error = _error(_frames(monkeypatch, "import_network_nc", args)[0])
    assert error["_outcome"]["outcome"] == "uncertain"


def test_an_export_that_also_edits_the_network_reads_partial_extra_edits(
    baseline, monkeypatch
):
    """H8: an export edits nothing; a handler that does reads extra_edits."""
    _wrap(monkeypatch, "export_network_nc", before=_set_bus_v_nom)
    block = _result(_frames(monkeypatch, "export_network_nc", {}))["_outcome"]
    assert (block["outcome"], block["reason"]) == ("partial", "extra_edits")
    assert block["paths_extra"] == ["/Bus/B1/v_nom"]
    assert block["counts"]["extra"] == 1


def test_an_export_with_an_uncomparable_state_is_never_completed(
    baseline, monkeypatch
):
    """Mutant guard: the export's extras check needs a comparable map."""
    monkeypatch.setattr(outcomes, "state_map", lambda *a: outcomes.UNCOMPARABLE)
    block = _result(_frames(monkeypatch, "export_network_nc", {}))["_outcome"]
    assert (block["outcome"], block["reason"]) == ("uncertain", "not_comparable")


def test_a_plain_export_still_reads_completed(baseline, monkeypatch):
    block = _result(_frames(monkeypatch, "export_network_nc", {}))["_outcome"]
    assert block["outcome"] == "completed", block


def test_an_effect_tool_outside_the_write_set_times_out_uncertain(
    baseline, monkeypatch
):
    """H6: every write/destructive-tier call in an ordinary turn, not only WRITE_TOOLS."""
    import threading

    from harness import budget as harness_budget
    from harness import catalogue

    tool = "set_snapshots"
    assert catalogue.safety_tier_for(tool) in ("write", "destructive")
    assert tool not in outcomes.WRITE_TOOLS
    release = threading.Event()
    monkeypatch.setitem(chat_tools.DISPATCHERS, tool, lambda **_kw: release.wait(10))
    monkeypatch.setattr(harness_budget, "PER_TOOL_TIMEOUT_SECONDS", 0.2)
    args = {"start": "2026-01-01", "end": "2026-01-02", "freq": "h"}
    try:
        frames, _c = _frames(monkeypatch, tool, args)
    finally:
        release.set()
    errors = [d for e, d in frames if e == "tool_error"]
    assert errors and errors[0]["error_kind"] == "tool_timeout", errors
    assert errors[0]["_outcome"]["reason"] == "tool_timeout"


def test_a_timeout_in_a_checked_turn_gets_no_ordinary_outcome(
    baseline, monkeypatch
):
    """H6 is for ordinary turns: a checked turn has its own projection, so
    the loop adds no `_outcome` to its timeout frame."""
    import threading
    from types import SimpleNamespace

    from harness import budget as harness_budget

    release = threading.Event()
    monkeypatch.setitem(
        chat_tools.DISPATCHERS, "bulk_update_components", lambda **_kw: release.wait(10)
    )
    monkeypatch.setattr(harness_budget, "PER_TOOL_TIMEOUT_SECONDS", 0.2)
    turn = SimpleNamespace(tool_guard=lambda *_a: True)
    try:
        frames, _c = _frames(
            monkeypatch, "bulk_update_components", UPDATE, assurance_turn=turn
        )
    finally:
        release.set()
    errors = [d for e, d in frames if e == "tool_error"]
    assert errors and errors[0]["error_kind"] == "tool_timeout", frames
    assert "_outcome" not in errors[0]


def test_timeout_outcome_by_tier():
    from harness.loop import _timeout_outcome

    assert _timeout_outcome(False, "tool_timeout") == {}
    assert _timeout_outcome(True, "tool_not_started")["_outcome"] == {
        "version": 1,
        "outcome": "uncertain",
        "reason": "tool_not_started",
    }


# H8: one rule, both ways (a table over classify_write's new branches).
def _wnote(state, *items):
    return {
        "tool": "bulk_update_components",
        "ctx": 1,
        "cls": "Generator",
        "items": list(items)
        or [
            {
                "kind": "cell",
                "name": "g",
                "attr": "p",
                "requested": 1.0,
                "expected": 1.0,
                "before_present": True,
            }
        ],
        "revision": 3,
        "state": state,
    }


def _wread(value, state):
    return {
        "revision_start": 3,
        "revision_end": 3,
        "rows": {"g/p": {"present": True, "value": value}},
        "state": state,
    }


BEFORE = {"/Generator/g/p": "a", "/Bus/b/v": "x"}


@pytest.mark.parametrize(
    "note, read, expected",
    [
        # completed, no change elsewhere
        (
            _wnote(BEFORE),
            _wread(1.0, {**BEFORE, "/Generator/g/p": "b"}),
            ("completed", None),
        ),
        # completed + extra → partial extra_edits
        (
            _wnote(BEFORE),
            _wread(1.0, {**BEFORE, "/Generator/g/p": "b", "/Bus/b/v": "y"}),
            ("partial", "extra_edits"),
        ),
        # an added path counts as extra
        (
            _wnote(BEFORE),
            _wread(1.0, {**BEFORE, "/meta/k": "1"}),
            ("partial", "extra_edits"),
        ),
        # a removed path counts as extra
        (
            _wnote(BEFORE),
            _wread(1.0, {"/Generator/g/p": "a"}),
            ("partial", "extra_edits"),
        ),
        # nothing done, nothing changed → failed
        (_wnote(BEFORE), _wread(0.0, dict(BEFORE)), ("failed", None)),
        # nothing done, the named cell moved → uncertain not_as_requested
        (
            _wnote(BEFORE),
            _wread(0.0, {**BEFORE, "/Generator/g/p": "c"}),
            ("uncertain", "not_as_requested"),
        ),
        # nothing done, only an extra path moved → uncertain, never partial
        (
            _wnote(BEFORE),
            _wread(0.0, {**BEFORE, "/Bus/b/v": "y"}),
            ("uncertain", "not_as_requested"),
        ),
        # uncomparable state → uncertain
        (
            _wnote(BEFORE),
            _wread(1.0, outcomes.UNCOMPARABLE),
            ("uncertain", "not_comparable"),
        ),
        (
            _wnote(outcomes.UNCOMPARABLE),
            _wread(1.0, BEFORE),
            ("uncertain", "not_comparable"),
        ),
        # a note without a state map fails closed
        (
            {k: v for k, v in _wnote(BEFORE).items() if k != "state"},
            _wread(1.0, BEFORE),
            ("uncertain", "not_comparable"),
        ),
    ],
)
def test_classify_write_extra_table(note, read, expected):
    block = outcomes.classify_write(note, read)
    assert (block["outcome"], block["reason"]) == expected


def test_a_partial_write_with_extras_keeps_both_counts_pure():
    items = [
        {
            "kind": "cell",
            "name": n,
            "attr": "p",
            "requested": 1.0,
            "expected": 1.0,
            "before_present": True,
        }
        for n in ("g", "h")
    ]
    read = {
        "revision_start": 3,
        "revision_end": 3,
        "rows": {
            "g/p": {"present": True, "value": 1.0},
            "h/p": {"present": True, "value": 0.0},
        },
        "state": {**BEFORE, "/Generator/g/p": "b", "/Bus/b/v": "y"},
    }
    block = outcomes.classify_write(_wnote(BEFORE, *items), read)
    assert (block["outcome"], block["reason"]) == ("partial", "extra_edits")
    assert block["counts"]["done"] == 1 and block["counts"]["extra"] == 1


def test_a_create_allows_its_rows_meta_series_and_new_carrier_only():
    note = {
        "cls": "Bus",
        "items": [{"kind": "create", "name": "N1"}],
        "list_name": "buses",
    }
    assert outcomes._allowed(note, "/Bus/N1/v_nom")
    assert outcomes._allowed(note, "/Bus/N1/v_mag_pu_set@series")
    assert outcomes._allowed(note, "/meta/vintage_bounds/Bus/N1")
    assert outcomes._allowed(note, "/user_ts/buses/v_mag_pu_set/N1")
    assert outcomes._allowed(note, "/user_ts/$meta/buses/v_mag_pu_set/N1")
    assert outcomes._allowed(note, "/Carrier/AC/color", {"AC"})
    assert not outcomes._allowed(note, "/Carrier/AC/color")
    assert not outcomes._allowed(note, "/Bus/N2/v_nom")
    assert not outcomes._allowed(note, "/Bus/N1x/v_nom")
    assert not outcomes._allowed(note, "/meta/vintage_bounds/Bus/N2")
    assert not outcomes._allowed(note, "/user_ts/loads/p_set/N1")
    assert not outcomes._allowed(note, "/Generator/N1/p_nom")
    cell = {
        "cls": "Bus",
        "items": [{"kind": "cell", "name": "N1", "attr": "v_nom"}],
        "list_name": "buses",
    }
    assert outcomes._allowed(cell, "/Bus/N1/v_nom")
    assert not outcomes._allowed(cell, "/Bus/N1/x")
    assert not outcomes._allowed(cell, "/meta/vintage_bounds/Bus/N1")


def test_new_carriers_are_only_those_absent_before_and_named_by_a_created_row():
    note = {"carriers_before": ["AC"]}
    read = {
        "rows": {
            "a": {"present": True, "carrier": "AC"},
            "b": {"present": True, "carrier": "DC"},
            "c": {"present": False, "carrier": "H2"},
        }
    }
    assert outcomes._new_carriers(note, read) == {"DC"}


# H7: canonical before hashing, on an in-memory template and an edited one.


def test_the_map_ignores_dtype_index_names_and_negative_zero():
    from project_templates.eh_templates import build_eh_datacenter

    n = build_eh_datacenter()
    before = outcomes.state_map(n, {})
    n.generators.index.name = "renamed"
    n.generators["p_nom"] = n.generators["p_nom"].astype(object)
    n.loads_t.p_set = n.loads_t.p_set * 1.0 - 0.0
    n.snapshot_weightings.index.name = "renamed"
    first = n.links.index[0]
    n.links.at[first, "p_nom"] = (
        -0.0 if n.links.at[first, "p_nom"] == 0 else n.links.at[first, "p_nom"]
    )
    assert outcomes.changed_paths(before, outcomes.state_map(n, {})) == []


def test_an_edit_restored_reads_no_change_and_an_edit_reads_one_path():
    from project_templates.eh_templates import build_eh_datacenter

    n = build_eh_datacenter()
    before = outcomes.state_map(n, {})
    name = n.generators.index[0]
    old = n.generators.at[name, "marginal_cost"]
    n.generators.at[name, "marginal_cost"] = old + 1
    assert outcomes.changed_paths(before, outcomes.state_map(n, {})) == [
        f"/Generator/{name}/marginal_cost"
    ]
    n.generators.at[name, "marginal_cost"] = old
    assert outcomes.changed_paths(before, outcomes.state_map(n, {})) == []


def test_an_eh_flag_reads_missing_as_its_default_false(baseline):
    """normalise_eh_columns writes a missing flag as False: no change."""
    from services.adequacy.eh_columns import _all_columns, normalise_eh_columns

    # Generator has no bool EH column; Bus carries eh_poc and eh_critical.
    flag = next(c for c, k in _all_columns()["Bus"].items() if k == "bool")
    n = _net()
    bus = n.buses.index[0]
    n.buses[flag] = None
    before = outcomes.state_map(n, {})
    normalise_eh_columns(n)
    assert outcomes.changed_paths(before, outcomes.state_map(n, {})) == []
    n.buses.at[bus, flag] = True
    assert outcomes.changed_paths(before, outcomes.state_map(n, {})) == [
        f"/Bus/{bus}/{flag}"
    ]


def test_a_series_value_change_is_its_own_path():
    from project_templates.eh_templates import build_eh_datacenter

    n = build_eh_datacenter()
    before = outcomes.state_map(n, {})
    attr, df = next((a, d) for a, d in n.loads_t.items() if not d.empty)
    col = df.columns[0]
    df.iloc[0, 0] = df.iloc[0, 0] + 1
    assert outcomes.changed_paths(before, outcomes.state_map(n, {})) == [
        f"/Load/{col}/{attr}@series"
    ]


def test_derived_classes_are_left_out():
    assert outcomes.DERIVED_CLASSES == frozenset({"SubNetwork"})
