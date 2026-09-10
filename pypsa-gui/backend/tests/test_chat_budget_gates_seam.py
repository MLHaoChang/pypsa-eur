"""
Phase B tripwire — `_turn_budget_block` lifted out of `_run_turn_body`.

Two caps guard a turn, and they are different animals:

  * **The session output ceiling** (`MAX_OUTPUT_TOKENS_PER_SESSION`) is
    in-memory and per session.
  * **The daily spend cap** (`PYPSA_GUI_CHAT_DAILY_TOKEN_CAP`) is durable and
    per project per day, read from disk via `_today_token_spend`, and disabled
    at `0` — the default — so an operator who never opts in pays no disk cost.

Three properties the seam has to keep, none of which is obvious from the code:

**They refuse BEFORE `session_init`.** The panel treats `session_init` as "a
turn has started" and has to tear it down again if the next frame ends it. Both
caps emit exactly one `session_done` and stop. `test_chat_turn_frame_contract`
records that; this file states it as a rule so a reader knows it is deliberate.

**They refuse before the SDK client is built.** No API call happens on a capped
turn. Cheap to lose in a refactor — moving the gate below the client build
would still pass every frame assertion while spending money.

**The daily cap is read from the module at call time.** Tests monkeypatch
`chat_service.PYPSA_GUI_CHAT_DAILY_TOKEN_CAP`; capturing it in a default
argument or a module-level constant snapshot would make every such test
silently ineffective rather than failing.

The gate is checked against the P0-pinned `turn_ctx` — the project this turn
would PERSIST to — not the live active context, so switching project mid-turn
cannot move the turn onto another project's daily budget.
"""
from __future__ import annotations

import inspect

import pytest

from services import chat_service


class _Ctx:
    """Stand-in for a ProjectContext; only its identity matters to the gate."""
    def __init__(self, name="P"):
        self.loaded_project = name


def _seam():
    fn = getattr(chat_service, "_turn_budget_block", None)
    assert fn is not None, "chat_service._turn_budget_block does not exist yet"
    return fn


def test_the_seam_exists_and_is_an_ordinary_function():
    """Returns a frame or None; a generator would defeat the point."""
    assert not inspect.isgeneratorfunction(_seam())


def test_an_unspent_session_is_not_blocked():
    session = chat_service.ChatSession()
    assert _seam()(session, _Ctx()) is None


def test_the_session_ceiling_blocks_at_the_limit_not_past_it():
    """
    `>=`, not `>`. A session that has spent exactly its ceiling is done; one
    token short is not.
    """
    limit = chat_service.MAX_OUTPUT_TOKENS_PER_SESSION
    at = chat_service.ChatSession()
    at.usage_acc["output_tokens"] = limit
    frame = _seam()(at, _Ctx())
    assert frame is not None, "a session at exactly its ceiling must be blocked"
    name, payload = frame
    assert name == "session_done"
    assert payload == {
        "reason": "budget_exhausted",
        "kind": "output_tokens",
        "limit": limit,
    }

    under = chat_service.ChatSession()
    under.usage_acc["output_tokens"] = limit - 1
    assert _seam()(under, _Ctx()) is None, "one token short must not be blocked"


def test_the_daily_cap_is_off_at_zero(monkeypatch):
    """
    The default. `0` means disabled, and disabled must mean `_today_token_spend`
    is never called — it reads the day's chat log off disk, and an operator who
    has not opted in should pay nothing for the feature.
    """
    calls = []
    monkeypatch.setattr(chat_service, "PYPSA_GUI_CHAT_DAILY_TOKEN_CAP", 0)
    monkeypatch.setattr(chat_service, "_today_token_spend",
                        lambda ctx: calls.append(ctx) or 10**9)
    assert _seam()(chat_service.ChatSession(), _Ctx()) is None
    assert calls == [], "the disabled cap still read the day's spend from disk"


def test_the_daily_cap_blocks_and_reports_what_was_spent(monkeypatch):
    monkeypatch.setattr(chat_service, "PYPSA_GUI_CHAT_DAILY_TOKEN_CAP", 100)
    monkeypatch.setattr(chat_service, "_today_token_spend", lambda _ctx: 100)
    frame = _seam()(chat_service.ChatSession(), _Ctx())
    assert frame is not None
    name, payload = frame
    assert name == "session_done"
    assert payload == {
        "reason": "daily_budget_exhausted",
        "kind": "daily_tokens",
        "limit": 100,
        "spent": 100,
    }


def test_the_daily_cap_is_read_from_the_module_at_call_time(monkeypatch):
    """
    The property that keeps every daily-cap test honest. Capture the value in a
    default argument and this passes while those tests stop testing anything.
    """
    monkeypatch.setattr(chat_service, "PYPSA_GUI_CHAT_DAILY_TOKEN_CAP", 0)
    session = chat_service.ChatSession()
    assert _seam()(session, _Ctx()) is None
    monkeypatch.setattr(chat_service, "PYPSA_GUI_CHAT_DAILY_TOKEN_CAP", 1)
    monkeypatch.setattr(chat_service, "_today_token_spend", lambda _ctx: 5)
    assert _seam()(session, _Ctx()) is not None, (
        "the cap was captured rather than read at call time"
    )


def test_the_daily_cap_is_measured_against_the_context_it_was_given(monkeypatch):
    """
    Against the pinned turn context, not whatever is active now — otherwise a
    mid-turn project switch moves the turn onto another project's budget.
    """
    seen = []
    monkeypatch.setattr(chat_service, "PYPSA_GUI_CHAT_DAILY_TOKEN_CAP", 100)
    monkeypatch.setattr(chat_service, "_today_token_spend",
                        lambda ctx: (seen.append(ctx), 0)[1])
    pinned = _Ctx("the-pinned-one")
    _seam()(chat_service.ChatSession(), pinned)
    assert seen == [pinned]


def test_neither_cap_builds_an_sdk_client(monkeypatch, tmp_projects_dir):
    """
    End to end, and the property most easily lost: a capped turn must not reach
    the SDK. Moving the gate below the client build would keep every frame
    assertion passing while spending money.
    """
    chat_service._reset_sessions_for_tests()
    built = []
    monkeypatch.setattr(chat_service, "_build_anthropic_client",
                        lambda: (built.append(1), (None, "x"))[1])
    session = chat_service.ChatSession()
    session.usage_acc["output_tokens"] = chat_service.MAX_OUTPUT_TOKENS_PER_SESSION
    events = list(chat_service.run_turn(session, "hi", client=None))
    assert [n for n, _ in events] == ["session_done"], events
    assert built == [], "a capped turn built an Anthropic client"
