"""
Task 16 — no-split merge precondition trip-wire (see the plan's Global
Constraints and .superpowers/sdd/2026-08-14-llm-provider-config-and-switching/
task-16-brief.md).

WHY THIS FILE EXISTS. On `master`, `redaction.redact_secrets_in_str` is
PATTERN-ONLY: it scrubs `sk-ant-*`, `key=value`, and `bearer <token>` shapes.
That is safe on `master` only because `llm_openai_compat.OpenAICompatProvider`
has no production caller there. THIS BRANCH removes both halves of that
safety net at once:

  1. it gives `OpenAICompatProvider` a real caller (`chat_service._provider_
     for_profile`, Task 6/7), so a live turn can actually reach it, and
  2. it adds per-profile key slots (`PYPSA_GUI_LLM_KEY__<SLOT>`, Task 3)
     whose values are arbitrary strings that match NONE of master's three
     patterns.

Task 4's value-substitution widening (`redaction._substitute_managed_values`,
fed by `app_secrets.live_secret_values()`) is the SOLE compensating control,
and it exists ONLY on this branch. If this branch is ever cherry-picked or
partially reverted such that the provider wiring lands without the
redaction widening, a live third-party key ships into the backend log and
`chat.jsonl` unscrubbed — with every other test in the suite still green,
because nothing else here depends on the widening.

THE PROOF. `test_non_pattern_secret_is_scrubbed_from_log_and_chat_jsonl`
plants a managed key whose value matches none of the three master patterns,
drives ONE real `chat_service.run_turn` call in which:
  * a transient provider error (mapped `rate_limited`, retryable) carries
    the literal value and is logged via the real `logger.warning` retry
    site in `chat_service.run_turn` (services/chat_service.py, the
    `_RETRYABLE_SDK_KINDS` branch) — this is the LOG sink;
  * the user's own message also carries the literal value, and the turn
    completes on the retry, so it is persisted to `chat.jsonl` via
    `_redact_for_persist` (services/chat_service.py, the `append_turn` call
    at the end of `_run_turn_body`) — this is the PERSIST sink.
Both sinks are grepped for the literal value afterwards; it must be absent
from both.

THE DISCRIMINATION. A test that merely asserts absence proves nothing if the
value was never going to be redacted-in in the first place (e.g. if
`_values` is empty, both scrubbing functions just no-op and the assertions
would still pass by never having anything to leak). `test_without_the_
value_substitution_widening_the_same_secret_leaks` reruns THE EXACT SAME
drive with `redaction._substitute_managed_values` monkeypatched to a no-op
(simulating a partial-revert that drops Task 4's widening but keeps
everything else) and asserts the value NOW appears in both sinks — proving
the first test's green run is actually caused by the widening, not by
coincidence.
"""
from __future__ import annotations

import logging
import sys
import types

import pytest

from services import chat_service

# ─────────────────────────────────────────────────────────────────────────
# Minimal local fake SDK plumbing. Deliberately NOT imported from
# test_chat_e2e.py's richer FakeAnthropicClient — this file only needs "fail
# once with a message, then succeed", which is a few lines on its own and
# keeps this trip-wire independent of that module's fixture graph.
# ─────────────────────────────────────────────────────────────────────────


class _FakeStreamEvent:
    def __init__(self, etype, **fields):
        self.type = etype
        for k, v in fields.items():
            setattr(self, k, v)


class _FakeBlock:
    def __init__(self, btype, **fields):
        self.type = btype
        for k, v in fields.items():
            setattr(self, k, v)


class _FakeFinalMessage:
    def __init__(self, content, usage):
        self.content = content
        self.usage = usage


class _FakeUsage:
    def __init__(self, input_tokens=5, output_tokens=5,
                 cache_read_input_tokens=0, cache_creation_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class _FakeStream:
    """Context-manager mimicking anthropic.MessagesStream."""

    def __init__(self, events, final_message):
        self._events = events
        self._final = final_message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._final


class _FakeMessages:
    def __init__(self, client):
        self._client = client

    def stream(self, **kwargs):
        return self._client._next_turn(**kwargs)


class _RaiseOnceThenSucceedClient:
    """First `messages.stream()` call raises `exc`; the second replays `turn`."""

    def __init__(self, exc, turn):
        self._raised = False
        self._exc = exc
        self._turn = turn
        self.messages = _FakeMessages(self)
        self.calls = []

    def _next_turn(self, **kwargs):
        self.calls.append(kwargs)
        if not self._raised:
            self._raised = True
            raise self._exc
        events, final = self._turn
        return _FakeStream(events, final)


def _install_fake_anthropic_module():
    """A `sys.modules["anthropic"]` stand-in with just the exception classes
    `llm_anthropic.map_sdk_exception` isinstance-checks against."""
    mod = types.ModuleType("anthropic")

    class RateLimitError(Exception):
        pass

    class AuthenticationError(Exception):
        pass

    class APIStatusError(Exception):
        def __init__(self, msg, status_code=None):
            super().__init__(msg)
            self.status_code = status_code

    mod.RateLimitError = RateLimitError
    mod.AuthenticationError = AuthenticationError
    mod.APIStatusError = APIStatusError
    mod.Anthropic = object  # unused — client= is injected directly
    return mod


@pytest.fixture
def fake_anthropic_module(monkeypatch):
    mod = _install_fake_anthropic_module()
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return mod


# A random 24-char alphanumeric value: no `sk-ant-` prefix, no `=`
# character (so SECRET_KV_RE's `key=value` shape never matches), and no
# `bearer` substring — deliberately outside every pattern in
# services/redaction.py (SECRET_KV_RE, BEARER_RE, SK_ANT_RE). Long enough to
# clear MIN_SUBSTITUTION_LENGTH (8).
SECRET = "qzB7nR3tY0pLxK9mWs2VhC8u"


def _drive_one_turn(tmp_projects_dir, install_network, fake_anthropic_module,
                     monkeypatch):
    """
    Shared drive: plant the non-pattern managed key, then run ONE
    `chat_service.run_turn` whose first attempt fails carrying the secret
    (retried and logged) and whose user message also carries the secret
    (persisted to chat.jsonl on the successful retry).

    Returns (log_text, chat_jsonl_text).
    """
    import pypsa

    from routers import projects as projects_router

    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="NoSplitProj")
    (tmp_projects_dir / "NoSplitProj").mkdir(exist_ok=True)

    # Retryable errors sleep BASE_STREAM_RETRY_DELAY * 2**attempt between
    # attempts by default — zero it so this test doesn't actually wait.
    monkeypatch.setattr(chat_service, "BASE_STREAM_RETRY_DELAY", 0.0)
    monkeypatch.setattr(chat_service, "MAX_STREAM_RETRY_DELAY", 0.0)
    monkeypatch.setattr(chat_service, "MAX_STREAM_RETRIES", 3)

    # The managed, non-pattern-matching key value this precondition is about.
    monkeypatch.setenv("PYPSA_GUI_LLM_KEY__ZZTEST", SECRET)

    session = chat_service.ChatSession()
    success = (
        [_FakeStreamEvent("text", text="noted.")],
        _FakeFinalMessage(content=[_FakeBlock("text", text="noted.")],
                          usage=_FakeUsage()),
    )
    # A realistic transient-upstream shape: a gateway/proxy 500 whose body
    # echoes back the credential it rejected. rate_limited is RETRYABLE
    # (services/chat_service.py:_RETRYABLE_SDK_KINDS), so this attempt logs
    # via the retry-warning site and a second attempt is made.
    client = _RaiseOnceThenSucceedClient(
        fake_anthropic_module.RateLimitError(
            f"upstream 500 — rejected credential {SECRET}"
        ),
        success,
    )

    user_message = f"my other provider key is {SECRET} and it just errored"

    logger = logging.getLogger("pypsa_gui.chat")
    records: list[str] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    collector = _Collector()
    logger.addHandler(collector)
    prev_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        events = list(chat_service.run_turn(session, user_message, client=client))
    finally:
        logger.removeHandler(collector)
        logger.setLevel(prev_level)

    assert len(client.calls) == 2, "expected exactly one retry then success"
    assert any(ev == "turn_done" for ev, _ in events), (
        "the drive must complete successfully so the turn reaches append_turn"
    )
    assert not any(ev == "error" for ev, _ in events), (
        "a terminal error frame would mean the retry never succeeded"
    )

    log_text = "\n".join(records)

    chat_path = tmp_projects_dir / "NoSplitProj" / "chat.jsonl"
    chat_text = chat_path.read_text(encoding="utf-8")

    return log_text, chat_text


def test_non_pattern_secret_is_scrubbed_from_log_and_chat_jsonl(
    tmp_projects_dir, install_network, fake_anthropic_module, monkeypatch,
):
    """THE PROOF (module docstring). Both real sinks must be clean."""
    log_text, chat_text = _drive_one_turn(
        tmp_projects_dir, install_network, fake_anthropic_module, monkeypatch,
    )

    assert SECRET not in log_text, (
        "a managed key value with no sk-ant-/key=/bearer shape leaked into "
        "the backend log — the value-substitution widening (Task 4) is "
        "not scrubbing the retry-warning log site"
    )
    assert SECRET not in chat_text, (
        "a managed key value with no sk-ant-/key=/bearer shape leaked into "
        "the durable chat.jsonl record — the value-substitution widening "
        "(Task 4) is not scrubbing _redact_for_persist"
    )


def test_without_the_value_substitution_widening_the_same_secret_leaks(
    tmp_projects_dir, install_network, fake_anthropic_module, monkeypatch,
):
    """
    THE DISCRIMINATION (module docstring). Disable ONLY the value-
    substitution pass (simulating a partial revert / cherry-pick that keeps
    the provider wiring but drops Task 4's widening) and prove the exact
    same drive now leaks the secret into BOTH sinks — the pattern-only
    regexes (sk-ant-*/key=value/bearer) never match this value's shape.

    This is what makes the first test meaningful: without this half, a
    broken widening that always no-ops would still pass the first test
    (nothing to redact != successfully redacted).
    """
    from services import redaction

    monkeypatch.setattr(
        redaction, "_substitute_managed_values", lambda text, values: text
    )

    log_text, chat_text = _drive_one_turn(
        tmp_projects_dir, install_network, fake_anthropic_module, monkeypatch,
    )

    assert SECRET in log_text, (
        "disabling value-substitution should have let the secret through "
        "to the log — if it didn't, the log assertion above proves nothing"
    )
    assert SECRET in chat_text, (
        "disabling value-substitution should have let the secret through "
        "to chat.jsonl — if it didn't, the persist assertion above proves "
        "nothing"
    )
