"""
The frame sequence `run_turn` emits is the contract, and this file is the
recording of it.

Why a recording rather than more assertions
-------------------------------------------
`services/chat_service.py::_run_turn_body` is 566 lines — the largest function
in the backend — and `docs/superpowers/plans/2026-09-09-chat-turn-loop-decomposition.md`
takes it apart in five phases. Phases 1-5 of the earlier god-file work could
prove each cut by re-exporting the moved object and comparing the OpenAPI
document byte-for-byte. Neither instrument is available here: nothing moves
between routers, and every candidate cut crosses a GENERATOR boundary, so the
extracted pieces are new functions rather than the same object under a new name.

What `_run_turn_body` *is*, to every caller, is a generator of
`(event_name, payload)` tuples — the SSE frames the panel renders. The 521
already-collected chat tests drive it exactly that way:

    events = list(chat_service.run_turn(session, "…", client=client))

So the contract check is: record that tuple sequence for a set of scripted
turns, and require it unchanged across every phase. That is strictly stronger
than "the tests still pass" — it pins ORDER and PAYLOAD SHAPE, which is what a
refactor of an interleaved generator is most likely to disturb, and what no
individual assertion covers end to end.

Scenarios were chosen to touch each phase's surface, so that no phase can be
cut without this file having something to say about it:

    plain text turn ................. the happy path, no tools
    read-tier tool .................. tool dispatch + replay (Phase C, E)
    tool-call cap ................... the per-turn cap (Phase C)
    session budget exhausted ........ gate 1 (Phase B)
    daily token cap ................. gate 2 (Phase B)
    no client ....................... the client-build failure frames
    SDK auth error .................. exception mapping (Phase D)

What is normalised, and why
---------------------------
A recording that changes run to run is worse than no recording, so the volatile
fields are replaced with placeholders rather than left to churn: session ids and
their 6-char prefix (fresh per session), the tool-count in `session_init` (it
tracks the tool catalogue, which this file is not about), and any float that
looks like a duration. Everything else is compared verbatim — including token
counts, which are scripted and therefore deterministic.

If a phase changes the recording, that is the phase reporting a behaviour
change. Re-record only with a reason written down.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from services import chat_service
from tests.test_chat_e2e import (
    FakeAnthropicClient,
    _FakeFinalMessage,
    _FakeUsage,
    _text_block,
    _text_event,
    _tool_use_block,
    _tool_use_event,
)

_GOLDEN = pathlib.Path(__file__).resolve().parent / "golden" / "chat_turn_frames.json"

_VOLATILE_KEYS = {"session_id", "session6", "tool_count"}
_DURATION_KEY = re.compile(r"(duration|elapsed|_ms|_s)$")


def _scrub(value):
    """Replace what cannot be deterministic; keep everything that can."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in _VOLATILE_KEYS:
                out[k] = f"<{k}>"
            elif _DURATION_KEY.search(k) and isinstance(v, (int, float)):
                out[k] = "<duration>"
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, tuple):
        return [_scrub(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


def _record(events) -> list:
    return [[name, _scrub(payload)] for name, payload in events]


# ── the scripted turns ───────────────────────────────────────────────────


def _plain_text_client(text="hello there."):
    return FakeAnthropicClient([(
        [_text_event(text)],
        _FakeFinalMessage(content=[_text_block(text)],
                          usage=_FakeUsage(input_tokens=11, output_tokens=7)),
    )])


def _one_read_tool_client():
    return FakeAnthropicClient([
        (
            [_text_event("listing buses."),
             _tool_use_event("tu-1", "list_components", {"component_class": "Bus"})],
            _FakeFinalMessage(
                content=[_text_block("listing buses."),
                         _tool_use_block("tu-1", "list_components", {"component_class": "Bus"})],
                usage=_FakeUsage(input_tokens=50, output_tokens=12)),
        ),
        (
            [_text_event("done.")],
            _FakeFinalMessage(content=[_text_block("done.")],
                              usage=_FakeUsage(input_tokens=8, output_tokens=3)),
        ),
    ])


def _scenarios(install_network):
    """
    name -> a zero-arg callable returning that turn's event list.

    Each scenario that patches anything opens its OWN
    `pytest.MonkeyPatch.context()`. That is not tidiness: the first version of
    this file took the shared `monkeypatch` fixture and let the scenarios use
    it in dict order, so `daily_token_cap`'s patches
    (`PYPSA_GUI_CHAT_DAILY_TOKEN_CAP = 5`, a stubbed `_today_token_spend`)
    were still live when `no_client` ran — and `no_client` recorded the DAILY
    CAP's frame instead of its own. A contaminated recording would then have
    become the gate every later phase was checked against.
    """
    import pypsa

    def plain_text():
        session = chat_service.ChatSession()
        return list(chat_service.run_turn(session, "hi", client=_plain_text_client()))

    def read_tier_tool():
        n = pypsa.Network()
        n.add("Bus", "B1")
        install_network(n, name=None)
        session = chat_service.ChatSession()
        return list(chat_service.run_turn(session, "list all buses",
                                          client=_one_read_tool_client()))

    def session_budget_exhausted():
        session = chat_service.ChatSession()
        session.usage_acc["output_tokens"] = chat_service.MAX_OUTPUT_TOKENS_PER_SESSION
        return list(chat_service.run_turn(session, "hi", client=_plain_text_client()))

    def daily_token_cap():
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(chat_service, "PYPSA_GUI_CHAT_DAILY_TOKEN_CAP", 5)
            mp.setattr(chat_service, "_today_token_spend", lambda _ctx: 9)
            session = chat_service.ChatSession()
            return list(chat_service.run_turn(session, "hi",
                                              client=_plain_text_client()))

    def no_client():
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(chat_service, "_build_anthropic_client",
                       lambda: (None, "missing_api_key"))
            session = chat_service.ChatSession()
            return list(chat_service.run_turn(session, "hi", client=None))

    return {
        "plain_text": plain_text,
        "read_tier_tool": read_tier_tool,
        "session_budget_exhausted": session_budget_exhausted,
        "daily_token_cap": daily_token_cap,
        "no_client": no_client,
    }


@pytest.fixture(autouse=True)
def _clean_sessions():
    chat_service._reset_sessions_for_tests()
    yield
    chat_service._reset_sessions_for_tests()


def test_the_frame_sequence_matches_the_recording(tmp_projects_dir, install_network):
    """
    The gate. A phase that changes any frame's name, order or payload shape
    fails here with a diff naming the scenario.
    """
    scenarios = _scenarios(install_network)
    got = {name: _record(fn()) for name, fn in scenarios.items()}

    if not _GOLDEN.exists():
        pytest.fail(
            f"{_GOLDEN} is missing. Record it deliberately — "
            f"`python -m tests.record_chat_turn_frames` — and read the diff "
            f"before committing it."
        )
    want = json.loads(_GOLDEN.read_text())

    assert sorted(got) == sorted(want), (
        f"scenario set changed: recording has {sorted(want)}, "
        f"this run produced {sorted(got)}"
    )
    for name in sorted(got):
        assert got[name] == want[name], (
            f"the frame sequence for {name!r} changed.\n"
            f"recorded: {json.dumps(want[name], indent=1)}\n"
            f"got:      {json.dumps(got[name], indent=1)}"
        )


def test_the_recording_is_stable_across_runs(tmp_projects_dir, install_network):
    """
    A recording that churns is worse than none — it trains the next person to
    re-record without reading the diff. Two runs of the same scenario must
    scrub to the same thing.
    """
    first = _scenarios(install_network)["plain_text"]()
    chat_service._reset_sessions_for_tests()
    second = _scenarios(install_network)["plain_text"]()
    assert _record(first) == _record(second)


# Every frame name the scenarios are here to pin. A recording that stops
# covering one of these still compares equal to itself, so the net has to be
# checked separately from the sequences.
_EXPECTED_FRAMES = {
    "session_init", "token", "turn_done", "session_done",
    "tool_request", "tool_running", "tool_result", "error",
}


def test_the_recording_covers_every_frame_the_phases_touch():
    """
    Guards the guard, two ways.

    An empty frame list compares equal to an empty recording, so a scenario
    whose fake client is mis-scripted would pass silently. And a recording that
    quietly stops emitting, say, `tool_result` still matches itself — the
    sequences cannot notice their own gaps.

    Not a frame-count threshold: the two budget gates emit exactly ONE frame
    each, correctly, so "at least two" would fail on right behaviour.
    """
    if not _GOLDEN.exists():
        pytest.skip("recording not made yet")
    want = json.loads(_GOLDEN.read_text())

    empty = sorted(name for name, frames in want.items() if not frames)
    assert not empty, (
        f"these scenarios recorded NO frames, which is a mis-scripted fake "
        f"rather than behaviour: {empty}"
    )

    seen = {frame[0] for frames in want.values() for frame in frames}
    missing = sorted(_EXPECTED_FRAMES - seen)
    assert not missing, (
        f"the recording no longer covers these frames: {missing}. Either a "
        f"scenario stopped exercising its path, or the frame was renamed — "
        f"both are behaviour changes a phase must not make silently."
    )


def test_the_budget_gates_each_emit_exactly_one_frame():
    """
    The specific shape Phase B must preserve: each cap yields ONE
    `session_done` and returns, without a `session_init` first. Emitting
    session_init before refusing would make the panel open a turn it then has
    to tear down.
    """
    if not _GOLDEN.exists():
        pytest.skip("recording not made yet")
    want = json.loads(_GOLDEN.read_text())
    for name in ("session_budget_exhausted", "daily_token_cap"):
        frames = want[name]
        assert [f[0] for f in frames] == ["session_done"], (
            f"{name} recorded {[f[0] for f in frames]}; the cap must refuse "
            f"before session_init, in one frame"
        )


@pytest.mark.skipif(
    "not config.getoption('--record-chat-frames', default=False)",
    reason="recording is deliberate; run `python -m tests.record_chat_turn_frames`",
)
def test_record(tmp_projects_dir, install_network):
    """
    Writes the recording. Skipped unless `--record-chat-frames` is passed, so a
    normal suite run can never overwrite the gate it is being checked against.
    """
    scenarios = _scenarios(install_network)
    out = {}
    for name, fn in scenarios.items():
        out[name] = _record(fn())
        chat_service._reset_sessions_for_tests()
    _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    _GOLDEN.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"recorded {len(out)} scenarios to {_GOLDEN}")
