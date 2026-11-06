"""A3.1/A3.4/A3.6: snapshot read-backs, undo_last first.

Issue 04, slice S3 (GK B (25) conditions W4/W5). Before undo_last runs,
`harness.outcomes.note_undo` peeks the undo stack's top entry: the sha256 of
its payload (netcdf + user_ts + template_meta, everything an undo restores,
W4) and the digest of its network read with the app's own reader. After the
handler, `read_undo` reads the pop counter, the sha of the entry actually
popped (W5), the live network digest, the user series and the template meta,
between two reads of the edit counter. The handler's returned JSON is never
read.
"""

import pytest

from harness import outcomes
from services import chat_tools, undo_service
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.test_p3_write_outcomes import _stream


def _gens():
    return PyPSAService.get_active_context().network.generators


@pytest.fixture(autouse=True)
def _second_generator(baseline):
    chat_tools.batch_create_components(
        "Generator", [{"name": "G2", "bus": "B1", "p_nom": 5, "marginal_cost": 3}]
    )


def _edit(harness, value=61.5):
    """One undoable chat edit: the seam pushes the turn's undo snapshot first."""
    before = _gens().at["G1", "marginal_cost"]
    _stream(
        harness,
        "bulk_update_components",
        {
            "component_class": "Generator",
            "names": ["G1"],
            "updates": {"marginal_cost": value},
        },
    )
    assert undo_service.depth() >= 1
    return before


# ── Through the chat seam, real handlers ─────────────────────────────────


def test_an_undo_that_restores_the_peeked_entry_is_completed(baseline, harness):
    before = _edit(harness)
    result = _stream(harness, "undo_last", {})
    assert result["_outcome"] == {"version": 1, "outcome": "completed", "reason": None}
    assert _gens().at["G1", "marginal_cost"] == before


def test_an_undo_that_pops_a_different_entry_is_uncertain(
    baseline, harness, monkeypatch
):
    _edit(harness)
    real_pop = undo_service.pop_entry

    def swapped():
        # Another writer pushes between the read-back's peek and the pop (W5).
        top = undo_service.peek_entry()
        undo_service.push(top[2]["netcdf"], top[2]["user_ts"], b"other meta")
        return real_pop()

    monkeypatch.setattr(undo_service, "pop_entry", swapped)
    result = _stream(harness, "undo_last", {})
    assert result["_outcome"]["outcome"] == "uncertain"
    assert result["_outcome"]["reason"] == "entry_moved"


def test_an_undo_whose_restored_network_differs_is_uncertain(
    baseline, harness, monkeypatch
):
    from services import network_undo

    _edit(harness)
    real = network_undo._reapply_user_ts_to_network

    def drift(n):
        real(n)
        n.generators.loc["G2", "p_nom"] = 99.0  # not what the snapshot holds

    monkeypatch.setattr(network_undo, "_reapply_user_ts_to_network", drift)
    result = _stream(harness, "undo_last", {})
    assert result["_outcome"]["outcome"] == "uncertain"
    assert result["_outcome"]["reason"] == "not_restored"


def test_a_refused_undo_is_a_tool_error_and_keeps_the_stack(
    baseline, harness, monkeypatch
):
    # A handler that raises reaches the model as a tool error, which carries no
    # `_outcome` (as in S2), so it never reads completed.
    import uuid

    from fastapi import HTTPException

    from harness.loop import dispatch_tool_call
    from harness.session import ChatSession

    _edit(harness)
    depth = undo_service.depth()

    def refuse(_what):
        raise HTTPException(409, "A study is running.")

    monkeypatch.setattr(PyPSAService, "refuse_if_study_running", staticmethod(refuse))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled")
    session, collector, results = ChatSession(), [], []
    for event, data in dispatch_tool_call(
        session,
        {"id": uuid.uuid4().hex, "name": "undo_last", "input": {}},
        collector,
        guided=True,
    ):
        if event == "tool_pending_confirmation":
            session.record_decision(data["confirmation_token"], "approve")
        if event == "tool_result":
            results.append(data["result"])
    errors = [c for c in collector if c.get("is_error")]
    assert errors and "A study is running." in errors[0]["content"], collector
    assert not any("_outcome" in str(r) and "completed" in str(r) for r in results)
    assert undo_service.depth() == depth


def test_a_handler_supplied_outcome_is_dropped(baseline, harness, monkeypatch):
    from routers import network as network_router

    _edit(harness)
    real = network_router.undo_last

    def forged():
        return {**real(), "_outcome": {"version": 1, "outcome": "completed"}}

    monkeypatch.setattr(network_router, "undo_last", forged)
    monkeypatch.setattr(undo_service, "pops_and_depth", lambda: (0, 0))
    result = _stream(harness, "undo_last", {})
    assert result["_outcome"]["outcome"] == "uncertain", result


def _install_template():
    """The datacenter template (custom columns, time series) via the app's own import."""
    import base64
    import pathlib
    import tempfile

    from project_templates.eh_templates import build_eh_datacenter

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "network.nc"
        build_eh_datacenter().export_to_netcdf(path)
        data = base64.b64encode(path.read_bytes()).decode()
    chat_tools.import_network_nc(data, "network.nc")
    n = PyPSAService.get_active_context().network
    assert len(n.snapshots) > 2 and any(not df.empty for df in n.generators_t.values())
    return n


def test_an_undo_on_a_real_template_network_is_completed(baseline, harness):
    # GK B (26) X1: the read-back loads the entry with the loader apply_undo
    # uses, so a template's custom columns and series do not read as a change.
    n = _install_template()
    name = n.generators.index[0]
    before = n.generators.at[name, "marginal_cost"]
    _stream(
        harness,
        "bulk_update_components",
        {
            "component_class": "Generator",
            "names": [name],
            "updates": {"marginal_cost": before + 7},
        },
    )
    result = _stream(harness, "undo_last", {})
    assert result["_outcome"] == {"version": 1, "outcome": "completed", "reason": None}
    assert (
        PyPSAService.get_active_context().network.generators.at[name, "marginal_cost"]
        == before
    )


def test_a_pop_by_another_session_reads_uncertain(baseline, harness, monkeypatch):
    # GK B (26) X2: another session's undo between the note and the read-back
    # (its pop runs outside this call's context) never stands in for this one's.
    import contextvars

    from routers import network as network_router

    _edit(harness)
    top = undo_service.peek_entry()
    undo_service.push(top[2]["netcdf"], top[2]["user_ts"], top[2]["template_meta"])
    real = network_router.undo_last

    def other_session():
        undo_service._POP_WATCH.set(None)  # the same project, not this call
        return undo_service.pop_entry()

    def raced():
        assert contextvars.copy_context().run(other_session) is not None
        return real()

    monkeypatch.setattr(network_router, "undo_last", raced)
    result = _stream(harness, "undo_last", {})
    assert result["_outcome"]["outcome"] == "uncertain"
    assert result["_outcome"]["reason"] == "entry_moved"


# ── The digest ───────────────────────────────────────────────────────────


def test_the_digest_ignores_order_and_sees_values_and_series(baseline):
    n = PyPSAService.get_active_context().network
    base = outcomes.network_digest(n)
    m = n.copy()
    m.generators = m.generators.iloc[::-1]
    assert outcomes.network_digest(m) == base
    m = n.copy()
    m.generators.loc["G1", "marginal_cost"] += 1
    assert outcomes.network_digest(m) != base
    m = n.copy()
    m.generators_t.p_max_pu["G1"] = 0.5
    with_series = outcomes.network_digest(m)
    assert with_series != base
    m.generators_t.p_max_pu.iloc[0, 0] = 0.4
    assert outcomes.network_digest(m) != with_series
    m = n.copy()
    m.generators["carrier"] = [["list"]] * len(m.generators)
    assert outcomes.network_digest(m) is outcomes.UNCOMPARABLE


# ── The pure classifier: one row per branch (W7 style) ────────────────


def _note(**kw):
    note = {
        "tool": "undo_last",
        "revision": 3,
        "pops": 4,
        "depth": 2,
        "digest_before": "d-before",
        "meta_before": b"m0",
        "user_ts_before": {"a": 1},
        "expected": {
            "sha": "s1",
            "digest": "d-snap",
            "user_ts": {"a": 0},
            "template_meta": b"m1",
        },
    }
    note.update(kw)
    return note


def _read(**kw):
    read = {
        "revision_start": 3,
        "revision_end": 3,
        "pops": 5,
        "own_pops": ["s1"],
        "depth": 1,
        "digest": "d-snap",
        "template_meta": b"m1",
        "user_ts": {"a": 0},
    }
    read.update(kw)
    return read


UNCHANGED = {
    "pops": 4,
    "own_pops": [],
    "depth": 2,
    "digest": "d-before",
    "template_meta": b"m0",
    "user_ts": {"a": 1},
}


@pytest.mark.parametrize(
    "note, read, outcome, reason",
    [
        (None, _read(), "uncertain", "no_submission"),
        ({"tool": "undo_last", "error": True}, _read(), "uncertain", "no_submission"),
        (_note(), {"error": "readback_failed"}, "uncertain", "readback_failed"),
        (_note(), None, "uncertain", "readback_failed"),
        (_note(), _read(revision_start=4), "uncertain", "revision_moved"),
        (_note(), _read(revision_end=4), "uncertain", "revision_moved"),
        (_note(), _read(**UNCHANGED), "failed", None),
        (_note(expected=None), _read(**UNCHANGED), "failed", "nothing_to_undo"),
        (_note(), _read(**{**UNCHANGED, "depth": 1}), "uncertain", "not_restored"),
        (_note(), _read(**{**UNCHANGED, "digest": "x"}), "uncertain", "not_restored"),
        (
            _note(),
            _read(**{**UNCHANGED, "template_meta": b"x"}),
            "uncertain",
            "not_restored",
        ),
        (_note(), _read(**{**UNCHANGED, "user_ts": {}}), "uncertain", "not_restored"),
        (
            _note(digest_before=outcomes.UNCOMPARABLE),
            _read(**{**UNCHANGED, "digest": outcomes.UNCOMPARABLE}),
            "uncertain",
            "not_restored",
        ),
        (_note(), _read(pops=6), "uncertain", "entry_moved"),
        (_note(), _read(**{**UNCHANGED, "pops": 5}), "uncertain", "entry_moved"),
        (_note(), _read(pops=6, own_pops=["s1", "s0"]), "uncertain", "entry_moved"),
        (_note(expected=None), _read(), "uncertain", "entry_moved"),
        (_note(), _read(own_pops=["s2"]), "uncertain", "entry_moved"),
        (
            _note(expected={**_note()["expected"], "digest": outcomes.UNCOMPARABLE}),
            _read(),
            "uncertain",
            "not_comparable",
        ),
        (_note(), _read(digest=outcomes.UNCOMPARABLE), "uncertain", "not_comparable"),
        (_note(), _read(digest="other"), "uncertain", "not_restored"),
        (_note(), _read(user_ts={"a": 9}), "uncertain", "not_restored"),
        (_note(), _read(template_meta=b"x"), "uncertain", "not_restored"),
        (
            _note(expected={**_note()["expected"], "template_meta": None}),
            _read(template_meta=b"anything"),
            "completed",
            None,
        ),
        (_note(), _read(), "completed", None),
    ],
)
def test_classify_undo_table(note, read, outcome, reason):
    got = outcomes.classify_undo(note, read)
    assert (got["outcome"], got["reason"]) == (outcome, reason)


def test_undo_last_is_a_write_tool_and_not_a_row_write():
    assert "undo_last" in outcomes.WRITE_TOOLS
    assert "undo_last" not in outcomes.ROW_WRITE_TOOLS
    assert not outcomes.WRITE_TOOLS & outcomes.JOB_TOOLS


# ── Raise and timeout paths (GK B (26) R3/X3) ────────────────────────────


def _frames(harness, tool, args):
    import uuid

    from harness.loop import dispatch_tool_call
    from harness.session import ChatSession

    harness.monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled")
    session, collector, frames = ChatSession(), [], []
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


_BULK = {
    "component_class": "Generator",
    "names": ["G1"],
    "updates": {"marginal_cost": 61.5},
}


@pytest.mark.parametrize(
    "tool, args", [("bulk_update_components", _BULK), ("undo_last", {})]
)
def test_a_raising_write_is_a_tool_error_with_no_outcome(
    baseline, harness, monkeypatch, tool, args
):
    from fastapi import HTTPException

    if tool == "undo_last":
        _edit(harness)

    def boom(**_kw):
        raise HTTPException(409, "refused")

    monkeypatch.setitem(chat_tools.DISPATCHERS, tool, boom)
    frames, collector = _frames(harness, tool, args)
    assert [d.get("error_kind") for e, d in frames if e == "tool_error"] == [
        "tool_error"
    ]
    assert not [d for e, d in frames if e == "tool_result"]
    assert all("_outcome" not in str(c) for c in collector), collector


def test_a_write_that_times_out_never_reads_completed_or_failed(
    baseline, harness, monkeypatch
):
    import threading

    from harness import budget as harness_budget

    real = chat_tools.DISPATCHERS["bulk_update_components"]
    release, landed = threading.Event(), threading.Event()

    def late(**kw):
        release.wait(10)
        try:
            return real(**kw)  # the orphan still lands after the timeout
        finally:
            landed.set()

    monkeypatch.setitem(chat_tools.DISPATCHERS, "bulk_update_components", late)
    monkeypatch.setattr(harness_budget, "PER_TOOL_TIMEOUT_SECONDS", 0.2)
    try:
        frames, collector = _frames(harness, "bulk_update_components", _BULK)
    finally:
        release.set()
    assert landed.wait(10)
    assert [d.get("error_kind") for e, d in frames if e == "tool_error"] == [
        "tool_timeout"
    ]
    assert not [d for e, d in frames if e == "tool_result"]
    assert all("_outcome" not in str(c) for c in collector), collector
