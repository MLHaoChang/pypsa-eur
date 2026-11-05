"""A3.1/A3.4/A3.6: component writes read back requested versus actual.

Issue 04, slice S2 (GK B (25) conditions W1-W7). The outcome of
bulk_update_components, batch_create_components, delete_component and
batch_delete_components comes from `harness.outcomes`: the call's own request
and each row's prior presence noted before the handler, an independent read
of the live network after it, between two reads of the edit counter. The
handler's returned JSON is never read. undo_last moves to S3 with the other
snapshot read-backs.
"""

import pytest

from harness import outcomes
from services import chat_tools
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.support import OMIT, text_turn, tool_turn


def _stream(harness, tool, args):
    """One ordinary-turn call through the real dispatch seam; the user approves."""
    import uuid

    from harness.loop import dispatch_tool_call
    from harness.session import ChatSession

    harness.monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled")
    session, collector, results = ChatSession(), [], []
    for event, data in dispatch_tool_call(
        session, {"id": uuid.uuid4().hex, "name": tool, "input": args}, collector, guided=True
    ):
        if event == "tool_pending_confirmation":
            session.record_decision(data["confirmation_token"], "approve")
        if event == "tool_result":
            results.append(data["result"])
    assert results, collector
    return results[0]


def _gens():
    return PyPSAService.get_active_context().network.generators


@pytest.fixture(autouse=True)
def _second_generator(baseline):
    # Through the real create path: a raw `network.add` leaves the row in a
    # shape the edit card's preview refuses ("values changed since the card").
    chat_tools.batch_create_components(
        "Generator", [{"name": "G2", "bus": "B1", "p_nom": 5, "marginal_cost": 3}])


# ── Through the chat seam, real handlers ─────────────────────────────────────


def test_a_bulk_update_read_back_as_requested_is_completed(baseline, harness):
    result = _stream(harness, "bulk_update_components", {
        "component_class": "Generator", "names": ["G1", "G2"],
        "updates": {"marginal_cost": 61.5}})
    assert result["_outcome"]["outcome"] == "completed", result["_outcome"]
    assert result["_outcome"]["counts"] == {"requested": 2, "done": 2, "not_done": 0, "unknown": 0}
    assert _gens().at["G1", "marginal_cost"] == 61.5


def test_a_requested_value_equal_to_the_old_one_counts_as_changed(baseline, harness):
    old = float(_gens().at["G1", "p_nom"])
    result = _stream(harness, "bulk_update_components", {
        "component_class": "Generator", "names": ["G1"], "updates": {"p_nom": old}})
    assert result["_outcome"]["outcome"] == "completed", result["_outcome"]


def test_a_string_number_is_compared_after_the_bulk_coercion(baseline, harness):
    result = _stream(harness, "bulk_update_components", {
        "component_class": "Generator", "names": ["G1"], "updates": {"marginal_cost": "1.5"}})
    assert result["_outcome"]["outcome"] == "completed", result["_outcome"]


def test_a_short_write_reads_partial_with_bounded_paths(baseline, harness, monkeypatch):
    from routers import network as network_router

    real = network_router.bulk_update

    def short(body):
        body = {**body, "names": body["names"][:1]}  # writes only the first name
        out = real(body)
        return {**out, "updated": 2, "ok": True}  # and claims both

    monkeypatch.setattr(network_router, "bulk_update", short)
    result = _stream(harness, "bulk_update_components", {
        "component_class": "Generator", "names": ["G1", "G2"], "updates": {"marginal_cost": 7}})
    block = result["_outcome"]
    assert block["outcome"] == "partial"
    assert block["counts"]["done"] == 1 and block["counts"]["not_done"] == 1
    assert block["paths_not_done"] == ["/Generator/Solar/marginal_cost"]
    assert "7" not in str(block["paths_not_done"])  # W6: no requested values


def test_a_write_that_landed_nothing_reads_failed(baseline, harness, monkeypatch):
    from routers import network as network_router

    monkeypatch.setattr(network_router, "bulk_update", lambda body: {"ok": True, "updated": 2})
    result = _stream(harness, "bulk_update_components", {
        "component_class": "Generator", "names": ["G1", "G2"], "updates": {"marginal_cost": 7}})
    assert result["_outcome"]["outcome"] == "failed"


def test_a_handler_supplied_outcome_is_dropped(baseline, harness, monkeypatch):
    from routers import network as network_router

    monkeypatch.setattr(network_router, "bulk_update",
                        lambda body: {"_outcome": {"version": 1, "outcome": "completed"}})
    result = _stream(harness, "bulk_update_components", {
        "component_class": "Generator", "names": ["G1"], "updates": {"marginal_cost": 7}})
    assert result["_outcome"]["outcome"] == "failed"


def test_a_raising_read_back_is_uncertain(baseline, harness, monkeypatch):
    monkeypatch.setattr(outcomes, "_static", _raise_after_first(outcomes._static))
    result = _stream(harness, "bulk_update_components", {
        "component_class": "Generator", "names": ["G1"], "updates": {"marginal_cost": 7}})
    assert result["_outcome"] == {"version": 1, "outcome": "uncertain", "reason": "read_failed"}


def _raise_after_first(real):
    calls = []

    def wrapped(*a, **k):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("read-back failed")
        return real(*a, **k)

    return wrapped


def test_a_second_writer_after_the_handler_reads_uncertain(baseline, harness, monkeypatch):
    """W3: a write landing between the handler's end and the read-back is seen."""
    from services import dirty_state

    real = outcomes.read_write

    def interleaved(note):
        dirty_state.bump_revision()  # another writer's edit, as the HTTP middleware bumps
        _gens().at["G1", "marginal_cost"] = 99.0
        return real(note)

    monkeypatch.setattr(outcomes, "read_write", interleaved)
    result = _stream(harness, "bulk_update_components", {
        "component_class": "Generator", "names": ["G1"], "updates": {"marginal_cost": 7}})
    assert result["_outcome"]["outcome"] == "uncertain"
    assert result["_outcome"]["reason"] == "revision_moved"


def test_a_revision_moving_during_the_read_back_is_uncertain(baseline, monkeypatch):
    from services import dirty_state

    note = outcomes.note_write("bulk_update_components", {
        "component_class": "Generator", "names": ["G1"], "updates": {"marginal_cost": 50}})
    real = outcomes._static

    def moving(n, cls):
        dirty_state.bump_revision()
        return real(n, cls)

    monkeypatch.setattr(outcomes, "_static", moving)
    block = outcomes.project_write(note)
    assert (block["outcome"], block["reason"]) == ("uncertain", "revision_moved")


def test_a_batch_create_reads_back_each_new_row(baseline, harness):
    result = _stream(harness, "batch_create_components", {
        "component_class": "Bus", "components": [{"name": "N1", "v_nom": 20.0}, {"name": "N2", "v_nom": 20}]})
    assert result["_outcome"]["outcome"] == "completed", result["_outcome"]
    assert result["_outcome"]["counts"]["done"] == 2


def test_creating_an_existing_name_is_never_completed(baseline):
    """W1: a row present before the call was not created by it."""
    args = {"component_class": "Bus", "components": [{"name": "B1", "v_nom": 110.0}]}
    note = outcomes.note_write("batch_create_components", args)
    assert outcomes.project_write(note)["outcome"] == "failed"


def test_a_create_attr_without_a_column_is_uncertain(baseline):
    """W2: an attr the classifier cannot compare is never completed."""
    args = {"component_class": "Bus", "components": [{"name": "N9", "v_nom": 20.0, "no_such_attr": 1}]}
    note = outcomes.note_write("batch_create_components", args)
    PyPSAService.get_active_context().network.add("Bus", "N9", v_nom=20.0)
    block = outcomes.project_write(note)
    assert (block["outcome"], block["reason"]) == ("uncertain", "not_comparable")


def test_a_refused_requested_value_is_uncertain_not_failed(baseline):
    """W2: a value the column rules refuse is not comparable."""
    note = outcomes.note_write("bulk_update_components", {
        "component_class": "Generator", "names": ["G1"], "updates": {"marginal_cost": "not a number"}})
    block = outcomes.project_write(note)
    assert (block["outcome"], block["reason"]) == ("uncertain", "not_comparable")


@pytest.mark.parametrize("tool,args", [
    ("delete_component", {"component_class": "Load", "name": "L1"}),
    ("batch_delete_components", {"component_class": "Load", "names": ["L1"]}),
])
def test_a_delete_reads_back_the_row_gone(baseline, harness, tool, args):
    result = _stream(harness, tool, args)
    assert result["_outcome"]["outcome"] == "completed", result["_outcome"]
    assert "L1" not in PyPSAService.get_active_context().network.loads.index


def test_deleting_an_unknown_name_is_never_completed(baseline):
    """W1: a row absent before the call was not deleted by it."""
    note = outcomes.note_write("delete_component", {"component_class": "Load", "name": "Ghost"})
    assert outcomes.project_write(note)["outcome"] == "failed"


def test_a_delete_that_left_the_row_is_failed(baseline):
    note = outcomes.note_write("delete_component", {"component_class": "Load", "name": "L1"})
    assert outcomes.project_write(note)["outcome"] == "failed"


def test_a_checked_turn_keeps_its_own_projection(baseline):
    # Checked turns get `_assurance`, not `_outcome`: the hook is gated on
    # assurance_turn is None (loop.py), and update_component is not a write tool here.
    assert "update_component" not in outcomes.WRITE_TOOLS
    assert not outcomes.WRITE_TOOLS & outcomes.JOB_TOOLS


def test_no_note_is_uncertain(baseline):
    assert outcomes.project_write(None)["outcome"] == "uncertain"
    assert outcomes.project_write({"tool": "x", "error": True})["reason"] == "no_submission"


def test_another_context_is_uncertain(baseline, monkeypatch):
    note = outcomes.note_write("delete_component", {"component_class": "Load", "name": "L1"})
    note["ctx"] = -1
    assert outcomes.project_write(note)["reason"] == "read_failed"


# ── The pure classifier: a killed mutant per branch (W7) ─────────────────────

NAN = float("nan")


def _note(*items, revision=3):
    return {"tool": "t", "ctx": 1, "cls": "Generator", "items": list(items), "revision": revision}


def _cell(name="g", expected=1.0, before=True):
    return {"kind": "cell", "name": name, "attr": "p", "requested": expected,
            "expected": expected, "before_present": before}


def _read(rows, start=3, end=3):
    return {"revision_start": start, "revision_end": end, "rows": rows}


@pytest.mark.parametrize("note,read,expected", [
    (None, _read({}), ("uncertain", "no_submission")),
    (_note(_cell()), {"error": "x"}, ("uncertain", "read_failed")),
    (_note(_cell()), None, ("uncertain", "read_failed")),
    (_note(_cell()), _read({"g/p": {"present": True, "value": 1.0}}, start=4), ("uncertain", "revision_moved")),
    (_note(_cell()), _read({"g/p": {"present": True, "value": 1.0}}, end=4), ("uncertain", "revision_moved")),
    (_note(_cell()), _read({}), ("uncertain", "not_comparable")),
    (_note(_cell()), _read({"g/p": {"present": True, "value": 1.0}}), ("completed", None)),
    (_note(_cell(expected=NAN)), _read({"g/p": {"present": True, "value": NAN}}), ("completed", None)),
    (_note(_cell()), _read({"g/p": {"present": True, "value": 2.0}}), ("failed", None)),
    (_note(_cell(before=False)), _read({"g/p": {"present": True, "value": 1.0}}), ("failed", None)),
    (_note(_cell()), _read({"g/p": {"present": False}}), ("failed", None)),
    (_note(_cell(expected=outcomes.UNCOMPARABLE)), _read({"g/p": {"present": True, "value": 1.0}}),
     ("uncertain", "not_comparable")),
    (_note(_cell("a"), _cell("b")), _read({"a/p": {"present": True, "value": 1.0},
                                            "b/p": {"present": True, "value": 0.0}}), ("partial", None)),
    (_note({"kind": "delete", "name": "d", "before_present": True}), _read({"d": {"present": False}}),
     ("completed", None)),
    (_note({"kind": "delete", "name": "d", "before_present": False}), _read({"d": {"present": False}}),
     ("failed", None)),
    (_note({"kind": "delete", "name": "d", "before_present": True}), _read({"d": {"present": True}}),
     ("failed", None)),
    (_note({"kind": "create", "name": "c", "attrs": {}, "before_present": False}),
     _read({"c": {"present": True, "attrs": {"p": (1.0, 1.0)}}}), ("completed", None)),
    (_note({"kind": "create", "name": "c", "attrs": {}, "before_present": True}),
     _read({"c": {"present": True, "attrs": {}}}), ("failed", None)),
    (_note({"kind": "create", "name": "c", "attrs": {}, "before_present": False}),
     _read({"c": {"present": False}}), ("failed", None)),
    (_note({"kind": "create", "name": "c", "attrs": {}, "before_present": False}),
     _read({"c": {"present": True, "attrs": {"p": (1.0, 2.0)}}}), ("failed", None)),
    (_note({"kind": "create", "name": "c", "attrs": {}, "before_present": False}),
     _read({"c": {"present": True, "attrs": {"p": (outcomes.UNCOMPARABLE, 1.0)}}}),
     ("uncertain", "not_comparable")),
])
def test_classify_write_table(note, read, expected):
    block = outcomes.classify_write(note, read)
    assert (block["outcome"], block["reason"]) == expected


def test_partial_paths_are_bounded():
    items = [_cell(f"g{i}") for i in range(9)] + [_cell("ok")]
    rows = {f"g{i}/p": {"present": True, "value": 0.0} for i in range(9)}
    rows["ok/p"] = {"present": True, "value": 1.0}
    block = outcomes.classify_write(_note(*items), _read(rows))
    assert block["outcome"] == "partial"
    assert len(block["paths_not_done"]) == 5
    assert block["counts"] == {"requested": 10, "done": 1, "not_done": 9, "unknown": 0}


@pytest.mark.parametrize("expected,actual,same", [
    (1.0, 1, True), (1, 1.0, True), (True, True, True), (True, 1.0, False), (1.0, True, False),
    ("gas", "gas", True), ("gas", "oil", False), (1.0, "x", False), (None, None, True),
    (NAN, 1.0, False), (outcomes.UNCOMPARABLE, 1.0, None), ([1], [1], None),
])
def test_same(expected, actual, same):
    assert outcomes._same(expected, actual) is same

