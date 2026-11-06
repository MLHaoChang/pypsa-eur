"""Required guards before any effect (issue 04, slice S4a: GK B (27) G1-G4, G7).

`harness.guards` holds the required-guard path, separate from the advisory
PRE_DISPATCH_VALIDATORS: every write/destructive catalogue tool has a
REQUIRED_GUARDS entry (the schema guard at least), a missing or raising guard
denies the call, the confirmation binds a fingerprint of the exact args the
handler receives, and a call that keeps failing on the same args stops
before dispatch. A refusal reads `denied`: nothing ran, so no reservation,
undo snapshot, write note, dirty mark or revision bump, and the project file
bytes are unchanged.
"""

from __future__ import annotations

import uuid

import pytest

from harness import catalogue, guards
from harness.session import ChatSession
from services import chat_tools
from services.pypsa_service import PyPSAService

EFFECT_TIERS = ("write", "destructive")
UPDATE = {
    "component_class": "Generator",
    "names": ["G1"],
    "updates": {"marginal_cost": 61.5},
}


def _effect_tools():
    return [
        t
        for t in catalogue.TOOLS
        if catalogue.safety_tier_for(t["name"]) in EFFECT_TIERS
    ]


def _call(monkeypatch, tool, args, *, session=None, decision="approve", guided=True):
    """One ordinary-turn call through the real dispatch seam."""
    from harness.loop import dispatch_tool_call

    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled")
    session = session or ChatSession()
    frames, collector = [], []
    for event, data in dispatch_tool_call(
        session,
        {"id": uuid.uuid4().hex, "name": tool, "input": args},
        collector,
        guided=guided,
    ):
        frames.append((event, data))
        if event == "tool_pending_confirmation":
            session.record_decision(data["confirmation_token"], decision)
    return frames, collector


def _errors(frames):
    return [d for e, d in frames if e == "tool_error"]


def _marginal_cost():
    return float(
        PyPSAService.get_active_context().network.generators.at["G1", "marginal_cost"]
    )


@pytest.fixture
def spies(monkeypatch, baseline):
    """Every pre-handler effect seam, recorded and passed through, plus the
    project directory's file bytes."""
    from harness import effects, loop, outcomes
    from services import dirty_state

    seen: list[str] = []

    def spy(owner, name):
        real = getattr(owner, name)

        def wrapped(*a, **k):
            seen.append(name)
            return real(*a, **k)

        monkeypatch.setattr(owner, name, wrapped)

    spy(effects, "reserve")
    spy(loop, "_snapshot_for_turn_undo")
    spy(outcomes, "note_write")
    spy(dirty_state, "mark_dirty")
    spy(dirty_state, "bump_revision")
    _ctx, _pid, path = baseline

    def files():
        return {p: p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}

    return seen, files


# ── G1 inventory ─────────────────────────────────────────────────────────────


def test_every_write_and_destructive_tool_has_a_required_guard():
    tools = _effect_tools()
    assert len(tools) >= 160
    missing = [t["name"] for t in tools if not guards.REQUIRED_GUARDS.get(t["name"])]
    assert missing == []


def test_an_unmapped_write_tool_is_denied_before_its_handler(monkeypatch, spies):
    from harness import loop

    calls = []
    monkeypatch.setitem(
        chat_tools.DISPATCHERS, "fake_write_tool", lambda **kw: calls.append(kw) or {}
    )
    real_tier = loop._tool_tier
    monkeypatch.setattr(
        loop,
        "_tool_tier",
        lambda s, n: "write" if n == "fake_write_tool" else real_tier(s, n),
    )
    frames, collector = _call(monkeypatch, "fake_write_tool", {}, guided=False)
    assert calls == []
    (error,) = _errors(frames)
    assert error["error_kind"] == "required_guard_failed"
    assert collector[-1]["is_error"] is True


def test_check_denies_an_unmapped_effect_tool_and_passes_a_read():
    assert guards.check("no_such_tool", {}, "write")[0] == "required_guard_failed"
    assert guards.check("no_such_tool", {}, "destructive")[0] == "required_guard_failed"
    assert guards.check("no_such_tool", {}, "read") is None


# ── G2 the schema guard and a raising guard ──────────────────────────────────


def test_a_raising_guard_is_denied_with_no_handler_call(monkeypatch, spies):
    calls = []
    monkeypatch.setitem(
        chat_tools.DISPATCHERS,
        "bulk_update_components",
        lambda **kw: calls.append(kw) or {},
    )

    def boom(args):
        raise RuntimeError("guard broke")

    monkeypatch.setitem(guards.REQUIRED_GUARDS, "bulk_update_components", (boom,))
    frames, _ = _call(monkeypatch, "bulk_update_components", UPDATE)
    assert calls == []
    assert [e["error_kind"] for e in _errors(frames)] == ["required_guard_failed"]
    assert "tool_pending_confirmation" not in [e for e, _ in frames]


def test_a_schema_invalid_call_is_denied(monkeypatch, spies):
    before = _marginal_cost()
    frames, _ = _call(monkeypatch, "bulk_update_components", {**UPDATE, "names": "G1"})
    (error,) = _errors(frames)
    assert error["error_kind"] == "required_guard_failed"
    assert "names" in error["message"]
    assert _marginal_cost() == before


@pytest.mark.parametrize(
    "schema, good, bad",
    [
        ({"type": "integer"}, [1, 1.0, -3], [1.5, True, "1", None]),
        ({"type": "number"}, [1, 1.5], [True, "1"]),
        ({"type": "boolean"}, [True], [1, 0]),
        ({"type": ["string", "null"]}, ["x", None], [1]),
        ({"enum": [1, "a"]}, [1, 1.0, "a"], [True, "b"]),
        ({"type": "string", "minLength": 1, "maxLength": 2}, ["a", "ab"], ["", "abc"]),
        ({"type": "string", "pattern": "^[a-z]+$"}, ["ab"], ["Ab"]),
        ({"type": "number", "minimum": 0, "maximum": 1}, [0, 1], [-0.1, 1.1]),
        ({"type": "number", "exclusiveMinimum": 0}, [0.1], [0]),
        (
            {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 2,
            },
            [["a"]],
            [[], ["a", "b", "c"], [1]],
        ),
        (
            {
                "type": "object",
                "required": ["a"],
                "properties": {"a": {"type": "string"}},
                "additionalProperties": False,
            },
            [{"a": "x"}],
            [{}, {"a": 1}, {"a": "x", "b": 1}],
        ),
        (
            {"type": "object", "additionalProperties": {"type": "number"}},
            [{"k": 1}],
            [{"k": "1"}],
        ),
    ],
)
def test_the_subset_validator_on_each_keyword(schema, good, bad):
    for value in good:
        assert guards.schema_error(value, schema) is None, value
    for value in bad:
        assert guards.schema_error(value, schema) is not None, value


def test_an_unknown_schema_keyword_raises():
    with pytest.raises(guards.UnsupportedSchema):
        guards.schema_error({}, {"type": "object", "oneOf": []})
    with pytest.raises(guards.UnsupportedSchema):
        guards.check_schema({"type": "object", "properties": {"a": {"format": "uri"}}})


def test_a_schema_with_an_unknown_keyword_denies_its_tool(monkeypatch):
    monkeypatch.setitem(
        guards.REQUIRED_GUARDS,
        "delete_component",
        guards.schema_guards({"type": "object", "if": {}}),
    )
    assert (
        guards.check("delete_component", {}, "destructive")[0]
        == "required_guard_failed"
    )


def _samples(schema, depth=0):
    """Valid-looking and broken values for one schema node."""
    out = [
        None,
        True,
        0,
        1,
        1.0,
        1.5,
        -1,
        "",
        "x",
        "Generator",
        [],
        ["x"],
        {},
        {"x": 1},
    ]
    if "enum" in schema:
        out += list(schema["enum"])
    for key in ("minimum", "maximum", "exclusiveMinimum"):
        if key in schema:
            out += [schema[key], schema[key] - 1, schema[key] + 1, schema[key] + 0.5]
    for key in ("minLength", "maxLength"):
        if key in schema:
            out += [
                "a" * schema[key],
                "a" * (schema[key] + 1),
                "a" * max(0, schema[key] - 1),
            ]
    if depth < 2 and isinstance(schema.get("items"), dict):
        items = _samples(schema["items"], depth + 1)
        out += [[v] for v in items[:20]] + [items[:3]]
    props = schema.get("properties") or {}
    if depth < 2 and props:
        full = {k: _samples(v, depth + 1)[-1] for k, v in props.items()}
        out.append(full)
        out.append({**full, "zz_extra": 1})
        for k, v in props.items():
            for value in _samples(v, depth + 1)[:12]:
                out.append({**full, k: value})
            out.append({kk: vv for kk, vv in full.items() if kk != k})
    return out


def test_the_subset_validator_agrees_with_jsonschema_on_every_effect_schema():
    jsonschema = pytest.importorskip("jsonschema")
    checked = 0
    for tool in _effect_tools():
        schema = tool["input_schema"]
        guards.check_schema(schema)  # no unknown keyword anywhere
        reference = jsonschema.Draft202012Validator(schema)
        for value in _samples(schema):
            assert (guards.schema_error(value, schema) is None) == reference.is_valid(
                value
            ), (tool["name"], value)
            checked += 1
    assert checked > 5000


# ── G3 the confirmation binds a fingerprint ──────────────────────────────────


def test_fingerprint_folds_only_what_is_the_same_value():
    fp = guards.fingerprint
    s = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "n": {"type": "number"}},
    }
    assert fp({"a": "x", "n": 1}, s) == fp({"n": 1.0, "a": "x"}, s)
    assert fp({"a": ""}, s) != fp({"a": "x"}, s)
    assert fp({"n": 1}, s) != fp({"n": 1.5}, s)
    assert fp({"n": True}, s) != fp({"n": 1}, s)
    assert fp({}, s) != fp({"a": None}, s)
    assert fp({"a": ""}, s) == fp({"a": None}, s)  # a string property
    assert fp({"n": ""}, s) != fp({"n": None}, s)  # not a string property
    assert fp({"z": ""}, s) != fp({"z": None}, s)  # no schema says string
    nested = {
        "type": "object",
        "properties": {"l": {"type": "array", "items": {"type": "string"}}},
    }
    assert fp({"l": [""]}, nested) == fp({"l": [None]}, nested)


def test_args_changed_during_the_confirmation_wait_are_denied(monkeypatch, spies):
    from harness import loop

    real = loop._confirm_destructive_tool

    def changes_args(*a, **k):
        approved = yield from real(*a, **k)
        k["args"]["updates"]["marginal_cost"] = 99
        return approved

    monkeypatch.setattr(loop, "_confirm_destructive_tool", changes_args)
    calls = []
    monkeypatch.setitem(
        chat_tools.DISPATCHERS,
        "bulk_update_components",
        lambda **kw: calls.append(kw) or {},
    )
    frames, collector = _call(monkeypatch, "bulk_update_components", UPDATE)
    assert calls == []
    (error,) = _errors(frames)
    assert error["error_kind"] == "confirmation_args_changed"
    assert error["_outcome"] == {
        "version": 1,
        "outcome": "denied",
        "reason": "confirmation_args_changed",
    }
    assert collector[-1]["is_error"] is True


def test_unchanged_args_through_the_wait_still_run(monkeypatch, spies):
    seen, _ = spies
    frames, _ = _call(monkeypatch, "bulk_update_components", UPDATE)
    assert _errors(frames) == []
    assert _marginal_cost() == 61.5
    # The spies see the seams when the call does run.
    assert {
        "_snapshot_for_turn_undo",
        "note_write",
        "mark_dirty",
        "bump_revision",
    } <= set(seen)


# ── G4 a denial means nothing ran ────────────────────────────────────────────


def _deny_unmapped(monkeypatch):
    monkeypatch.delitem(guards.REQUIRED_GUARDS, "bulk_update_components")
    return UPDATE, "approve"


def _deny_schema(monkeypatch):
    return {**UPDATE, "names": "G1"}, "approve"


def _deny_raising(monkeypatch):
    def boom(args):
        raise ValueError("x")

    monkeypatch.setitem(guards.REQUIRED_GUARDS, "bulk_update_components", (boom,))
    return UPDATE, "approve"


def _deny_args_changed(monkeypatch):
    from harness import loop

    real = loop._confirm_destructive_tool

    def changes_args(*a, **k):
        approved = yield from real(*a, **k)
        k["args"]["names"].append("G1")
        return approved

    monkeypatch.setattr(loop, "_confirm_destructive_tool", changes_args)
    return UPDATE, "approve"


def _deny_decision(decision):
    def setup(monkeypatch):
        monkeypatch.setattr(
            ChatSession, "wait_for_decision", lambda self, token, timeout=None: decision
        )
        return UPDATE, "approve"

    return setup


def _deny_user_restriction(monkeypatch):
    from harness import confirm

    monkeypatch.setattr(confirm, "confirmation_policy", lambda *a, **k: "blocked")
    return UPDATE, "approve"


def _deny_envelope(verdict):
    def setup(monkeypatch):
        from services import run_envelope

        monkeypatch.setattr(run_envelope, "admit", lambda *a, **k: verdict)
        return UPDATE, "approve"

    return setup


def _deny_policy_unrecorded(monkeypatch):
    from harness import approvals, confirm

    monkeypatch.setattr(confirm, "confirmation_policy", lambda *a, **k: "policy")
    monkeypatch.setattr(approvals, "decided", lambda *a, **k: False)
    return UPDATE, "approve"


def _deny_card_unrecorded(monkeypatch):
    from harness import approvals

    monkeypatch.setattr(approvals, "asked", lambda *a, **k: False)
    return UPDATE, "approve"


@pytest.mark.parametrize(
    "setup, kind",
    [
        (_deny_unmapped, "required_guard_failed"),
        (_deny_schema, "required_guard_failed"),
        (_deny_raising, "required_guard_failed"),
        (_deny_args_changed, "confirmation_args_changed"),
        (_deny_decision("deny"), "confirmation_denied"),
        (_deny_decision("expired"), "confirmation_expired"),
        (_deny_decision("aborted"), "aborted"),
        (_deny_decision("revoked"), "confirmation_revoked"),
        (_deny_user_restriction, "user_restriction"),
        (_deny_envelope("round_change_cap"), "round_change_cap"),
        (_deny_envelope("unavailable"), "approval_unavailable"),
        (_deny_policy_unrecorded, "approval_unavailable"),
        (_deny_card_unrecorded, "approval_unavailable"),
    ],
)
def test_a_denial_runs_no_effect_seam(monkeypatch, spies, setup, kind):
    seen, files = spies
    args, decision = setup(monkeypatch)
    before, cost = files(), _marginal_cost()
    seen.clear()
    frames, collector = _call(
        monkeypatch, "bulk_update_components", args, decision=decision
    )
    (error,) = _errors(frames)
    assert error["error_kind"] == kind
    assert error["_outcome"] == {"version": 1, "outcome": "denied", "reason": kind}
    assert collector[-1]["is_error"] is True
    assert seen == []
    assert files() == before
    assert _marginal_cost() == cost


def test_a_no_progress_stop_runs_no_effect_seam(monkeypatch, spies):
    seen, files = spies
    real = chat_tools.DISPATCHERS["bulk_update_components"]
    calls = []

    def fails_twice(**kw):
        calls.append(kw)
        if len(calls) <= 2:
            raise RuntimeError("busy")
        return real(**kw)

    monkeypatch.setitem(chat_tools.DISPATCHERS, "bulk_update_components", fails_twice)
    session = ChatSession()
    _turn(monkeypatch, session, [UPDATE, UPDATE])
    assert len(calls) == 2
    before, cost = files(), _marginal_cost()
    seen.clear()
    frames, _ = _turn(monkeypatch, session, [UPDATE], start=False)
    (error,) = _errors(frames)
    assert error["error_kind"] == "no_progress_stop"
    assert error["_outcome"] == {
        "version": 1,
        "outcome": "denied",
        "reason": "no_progress_stop",
    }
    assert "tool_request" not in [e for e, _ in frames]
    assert len(calls) == 2
    assert seen == [] and files() == before and _marginal_cost() == cost


# ── G7 the no-progress stop ──────────────────────────────────────────────────


def _turn(
    monkeypatch,
    session,
    calls,
    *,
    tool="bulk_update_components",
    start=True,
    guided=False,
):
    """One model step's tool calls through the turn loop's dispatch."""
    from harness.loop import _dispatch_tool_uses

    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled")
    if start:
        guards.start_turn(session)
    ctx = PyPSAService.get_active_context()
    collector, frames = [], []
    uses = [{"id": uuid.uuid4().hex, "name": tool, "input": args} for args in calls]
    gen = _dispatch_tool_uses(
        session,
        uses,
        tool_call_count=0,
        turn_ctx=ctx,
        turn_project_holder=[ctx.loaded_project],
        project_switched=lambda: False,
        tool_results_for_next_turn=collector,
        char_budget={"used": 0},
        offered_tool_names={tool},
        guided=guided,
    )
    try:
        while True:
            event, data = next(gen)
            frames.append((event, data))
            if event == "tool_pending_confirmation":
                session.record_decision(data["confirmation_token"], "approve")
    except StopIteration:
        pass
    assert len(collector) == len(calls)
    return frames, collector


def _kinds(frames):
    return [e["error_kind"] for e in _errors(frames)]


@pytest.fixture
def probe(monkeypatch):
    """A write tool with a schema guard and a scripted handler: None answers
    `{"ok": True}`, an exception is raised."""
    from harness import loop

    script: list = []
    calls: list = []

    def handler(**kw):
        calls.append(kw)
        step = script.pop(0) if script else None
        if isinstance(step, BaseException):
            raise step
        return {"ok": True}

    monkeypatch.setitem(chat_tools.DISPATCHERS, "probe_write", handler)
    real_tier = loop._tool_tier
    monkeypatch.setattr(
        loop,
        "_tool_tier",
        lambda s, n: "write" if n == "probe_write" else real_tier(s, n),
    )
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    monkeypatch.setitem(
        guards.REQUIRED_GUARDS, "probe_write", guards.schema_guards(schema)
    )
    return script, calls


BAD = {"n": "x"}
GOOD = {"n": 1}


def test_the_third_identical_failing_call_in_a_turn_is_stopped(
    monkeypatch, baseline, probe
):
    frames, _ = _turn(monkeypatch, ChatSession(), [BAD, BAD, BAD], tool="probe_write")
    assert _kinds(frames) == [
        "required_guard_failed",
        "required_guard_failed",
        "no_progress_stop",
    ]
    stop = _errors(frames)[-1]
    assert "correct" in stop["message"].lower()


def test_a_failing_handler_counts_and_stops_before_dispatch(
    monkeypatch, baseline, probe
):
    script, calls = probe
    script += [RuntimeError("busy"), RuntimeError("busy")]
    frames, _ = _turn(
        monkeypatch, ChatSession(), [GOOD, GOOD, GOOD], tool="probe_write"
    )
    assert len(calls) == 2
    assert _kinds(frames) == ["tool_error", "tool_error", "no_progress_stop"]


def test_different_args_are_another_key(monkeypatch, baseline, probe):
    other = {"n": "y"}
    frames, _ = _turn(
        monkeypatch, ChatSession(), [BAD, BAD, other, BAD], tool="probe_write"
    )
    assert _kinds(frames) == ["required_guard_failed"] * 3 + ["no_progress_stop"]


def test_the_same_args_folded_are_the_same_key(monkeypatch, baseline, probe):
    script, _ = probe
    script += [RuntimeError("a"), RuntimeError("b")]
    frames, _ = _turn(
        monkeypatch, ChatSession(), [{"n": 1}, {"n": 1.0}, {"n": 1}], tool="probe_write"
    )
    assert _kinds(frames)[-1] == "no_progress_stop"


def test_a_success_on_the_key_resets_it(monkeypatch, baseline, probe):
    script, calls = probe
    script += [RuntimeError("busy"), None, RuntimeError("busy"), RuntimeError("busy")]
    frames, _ = _turn(monkeypatch, ChatSession(), [GOOD] * 4, tool="probe_write")
    assert len(calls) == 4
    assert _kinds(frames) == ["tool_error"] * 3


def test_a_success_resets_the_per_run_count_too(monkeypatch, baseline, probe):
    script, calls = probe
    session = ChatSession()
    script += [RuntimeError("1"), RuntimeError("2"), None]
    _turn(monkeypatch, session, [GOOD, GOOD], tool="probe_write")
    _turn(monkeypatch, session, [GOOD], tool="probe_write")  # the success
    for i in range(3):  # without the reset the run count would reach 5 here
        script.append(RuntimeError(str(i)))
        frames, _ = _turn(monkeypatch, session, [GOOD], tool="probe_write")
        assert _kinds(frames) == ["tool_error"], i
    assert len(calls) == 6


def test_the_per_run_bound_holds_across_turns(monkeypatch, baseline, probe):
    from harness import budget

    session = ChatSession()
    frames, _ = _turn(monkeypatch, session, [BAD, BAD], tool="probe_write")
    assert _kinds(frames) == ["required_guard_failed"] * 2
    frames, _ = _turn(monkeypatch, session, [BAD, BAD], tool="probe_write")
    assert _kinds(frames) == ["required_guard_failed"] * 2
    frames, _ = _turn(monkeypatch, session, [BAD], tool="probe_write")
    assert _kinds(frames) == ["no_progress_stop"]
    assert budget.NO_PROGRESS_PER_RUN == 5 and budget.NO_PROGRESS_PER_TURN == 3


def test_a_denied_outcome_counts_as_failing(monkeypatch, baseline, probe):
    _, calls = probe
    monkeypatch.setattr(
        ChatSession, "wait_for_decision", lambda self, token, timeout=None: "deny"
    )
    frames, _ = _turn(
        monkeypatch, ChatSession(), [GOOD, GOOD, GOOD], tool="probe_write", guided=True
    )
    assert calls == []
    assert _kinds(frames) == [
        "confirmation_denied",
        "confirmation_denied",
        "no_progress_stop",
    ]


def test_a_refusal_is_counted_as_failing(monkeypatch, baseline, probe):
    session = ChatSession()
    frames, _ = _turn(monkeypatch, session, [BAD, BAD, BAD], tool="probe_write")
    assert _kinds(frames)[-1] == "no_progress_stop"
    # Run count 3 (the refusal is a failing call): one more failure, then stop.
    frames, _ = _turn(monkeypatch, session, [BAD, BAD], tool="probe_write")
    assert _kinds(frames) == ["required_guard_failed", "no_progress_stop"]


def test_a_failed_outcome_counts_as_failing(monkeypatch, baseline):
    calls = []
    monkeypatch.setitem(
        chat_tools.DISPATCHERS,
        "bulk_update_components",
        lambda **kw: calls.append(kw) or {},  # lands nothing: the read-back says failed
    )
    frames, _ = _turn(monkeypatch, ChatSession(), [UPDATE, UPDATE, UPDATE])
    results = [
        d["result"]["_outcome"]["outcome"] for e, d in frames if e == "tool_result"
    ]
    assert results == ["failed", "failed"]
    assert len(calls) == 2
    assert _kinds(frames) == ["no_progress_stop"]


def test_a_model_only_refusal_counts_as_failing(monkeypatch, baseline, probe):
    from harness import run_lifecycle

    _, calls = probe
    refusal = {"error_kind": "results_current", "message": "already current"}
    monkeypatch.setattr(run_lifecycle, "gate", lambda name, args: (refusal, args))
    frames, collector = _turn(
        monkeypatch, ChatSession(), [GOOD] * 3, tool="probe_write"
    )
    assert calls == [] and all(r["is_error"] for r in collector)
    assert _kinds(frames) == ["no_progress_stop"]  # the first two had no frame
