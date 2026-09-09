"""
Phase E tripwire — `_confirm_destructive_tool` lifted out of
`_dispatch_real_tool_call`.

The gate that stands between the model asking to delete something and it
happening. For a destructive tier it emits `tool_pending_confirmation` carrying
a token and a TTL, BLOCKS on the user's decision, and only then lets the
dispatch continue.

Three properties, each of which a careless extraction breaks differently:

**Not every destructive tool is gated.** `AUTO_APPROVE_TIERS` exempts some, and
the condition is `tier in DESTRUCTIVE_TIERS and tier not in AUTO_APPROVE_TIERS`.
Drop the second half and every auto-approved tool starts blocking on a prompt
nobody sent; drop the first and destructive tools run unprompted. The guard
covers both directions.

**Every refusal still pairs a `tool_result`.** Deny, expire and abort each
append an `is_error` result for this `tool_use_id`. Anthropic requires one
result per `tool_use`, so a refusal that only emits a frame leaves the
conversation invalid on the next turn — which surfaces much later, as an SDK
400 rather than as a permissions bug.

**The decision maps to a specific `error_kind`.** `deny`/`expired`/`aborted` are
distinct strings the panel renders differently, and anything unrecognised is
`unknown_decision` rather than a crash.

Aborting THIS tool is not aborting the turn: the gate returns False and the
dispatcher returns, but the turn loop carries on with the remaining tools.
"""
from __future__ import annotations

import inspect

import pytest

from services import chat_service


class _Pending:
    def __init__(self, token="tok-1"):
        self.token = token


class _Session:
    """Only the confirmation lifecycle matters here."""

    def __init__(self, decision="approve"):
        self._decision = decision
        self.issued = []
        self.waited = []

    def issue_confirmation(self, *, tool_name, args, safety_tier):
        self.issued.append((tool_name, args, safety_tier))
        return _Pending()

    def wait_for_decision(self, token):
        self.waited.append(token)
        return self._decision


def _seam():
    fn = getattr(chat_service, "_confirm_destructive_tool", None)
    assert fn is not None, "chat_service._confirm_destructive_tool does not exist yet"
    return fn


def _drive(session, tier, *, collector=None, tool_name="delete_project"):
    collector = collector if collector is not None else []
    frames = []
    gen = _seam()(
        session,
        tool_use_id="tu-1",
        tool_name=tool_name,
        args={"name": "P"},
        tier=tier,
        tool_results_collector=collector,
    )
    try:
        while True:
            frames.append(next(gen))
    except StopIteration as stop:
        return frames, stop.value, collector


def _a_gated_tier():
    # Recomputed per call so a monkeypatched AUTO_APPROVE_TIERS is respected.
    gated = set(chat_service.DESTRUCTIVE_TIERS) - set(chat_service.AUTO_APPROVE_TIERS)
    assert gated, "no destructive tier requires confirmation; the fixture is wrong"
    return sorted(gated)[0]


def test_the_seam_is_a_generator_returning_approval():
    assert inspect.isgeneratorfunction(_seam())


def test_a_read_tier_tool_is_not_gated_at_all():
    session = _Session()
    frames, approved, collector = _drive(session, "read")
    assert approved is True
    assert frames == [] and collector == []
    assert session.issued == [], "a read-tier tool asked for confirmation"


def test_an_auto_approved_destructive_tier_is_not_gated(monkeypatch):
    """
    The half of the condition most easily dropped. These tiers ARE destructive
    but the operator has exempted them; gating them blocks on a prompt nobody
    sent.

    `AUTO_APPROVE_TIERS` is built from `PYPSA_GUI_CHAT_AUTO_APPROVE_TIERS` and is
    EMPTY by default, so this has to set it — the first version of this test
    skipped when the set was empty, and skipping made the mutation that deletes
    `and tier not in AUTO_APPROVE_TIERS` undetectable. The module is written for
    exactly this: "Read at call time via the module attribute so a test can
    monkeypatch AUTO_APPROVE_TIERS directly."
    """
    tier = sorted(chat_service.DESTRUCTIVE_TIERS)[0]
    monkeypatch.setattr(chat_service, "AUTO_APPROVE_TIERS", frozenset([tier]))
    session = _Session()
    frames, approved, collector = _drive(session, tier)
    assert approved is True
    assert session.issued == [], f"{tier!r} is auto-approved but was gated"
    assert frames == [] and collector == []


def test_a_gated_tier_is_still_gated_when_a_DIFFERENT_tier_is_exempt(monkeypatch):
    """
    The other direction: exempting one tier must not exempt the rest.
    """
    tiers = sorted(chat_service.DESTRUCTIVE_TIERS)
    if len(tiers) < 2:
        pytest.skip("only one destructive tier exists")
    monkeypatch.setattr(chat_service, "AUTO_APPROVE_TIERS", frozenset([tiers[0]]))
    session = _Session(decision="approve")
    frames, approved, _c = _drive(session, tiers[1])
    assert [n for n, _ in frames] == ["tool_pending_confirmation"]
    assert approved is True


def test_a_gated_tier_prompts_with_a_token_and_ttl():
    session = _Session(decision="approve")
    frames, approved, collector = _drive(session, _a_gated_tier())
    assert approved is True
    names = [n for n, _ in frames]
    assert names == ["tool_pending_confirmation"], names
    payload = frames[0][1]
    assert payload["confirmation_token"] == "tok-1"
    assert payload["ttl_seconds"] == chat_service.CONFIRMATION_TTL_SECONDS
    assert payload["safety_tier"] == _a_gated_tier()
    assert session.waited == ["tok-1"], "it did not block on the decision"
    assert collector == [], "an approved tool must not pre-pair a result"


@pytest.mark.parametrize("decision,error_kind", [
    ("deny", "confirmation_denied"),
    ("expired", "confirmation_expired"),
    ("aborted", "aborted"),
    ("something-else", "unknown_decision"),
])
def test_each_refusal_maps_to_its_kind_and_pairs_a_result(decision, error_kind):
    session = _Session(decision=decision)
    frames, approved, collector = _drive(session, _a_gated_tier())
    assert approved is False
    names = [n for n, _ in frames]
    assert names == ["tool_pending_confirmation", "tool_error"], names
    assert frames[-1][1]["error_kind"] == error_kind
    assert collector == [{
        "type": "tool_result",
        "tool_use_id": "tu-1",
        "is_error": True,
        "content": error_kind,
    }], (
        "a refused tool must still pair a tool_result — Anthropic requires one "
        "per tool_use, and a gap surfaces later as an SDK 400"
    )
