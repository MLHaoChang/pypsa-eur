# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Issue 24 (HH-03): a durable reservation before each model send of a
harness run, settled across restarts."""

# ruff: noqa: F811 -- imported pytest fixtures
import json
import threading

import pytest
from harness import runner as harness_runner, send_reservation
from harness.protocol import LLMRequest, ProviderError
from harness.providers.fake import FakeProvider
from services import run_envelope
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline, dispatch, edit, helpers_state, session, wait_until,
)
from tests.test_harness_envelope import BLOCK, granted, profile  # noqa: F401
from tests.test_harness_run_loops import USAGE, Script, call, energy_source, go, no_series, rated, say  # noqa: F401
from tests.test_harness_run_skeleton import site, start  # noqa: F401
from tests.test_harness_runner import detached, task_path  # noqa: F401

MODEL = "claude-sonnet-5-5"
RATE = harness_runner.DEFAULT_RATES_EUR["claude-sonnet"]


@pytest.fixture
def mode(monkeypatch):
    def set_mode(value):
        monkeypatch.setenv(send_reservation.MODE_ENV, value)
    set_mode("money_eur")
    return set_mode


@pytest.fixture
def run(detached, site):
    _, root = site
    task = detached()
    return task_path(root, task)


def request(text="hello", max_tokens=1000, model=MODEL):
    return LLMRequest(model=model, max_tokens=max_tokens, system_blocks=[{"type": "text", "text": "sys"}],
                      tools=[], tools_stable=True, messages=[{"role": "user", "content": text}],
                      history_stable_anchor=None)


def record(path):
    return json.loads(path.read_text())


def ledger(path):
    return record(path)["run"].get("budgets") or {"reservations": [], "final_charges": []}


def set_limit(path, **limit):
    def change(task):
        harness_runner._budget(task)["limit"].update(limit)
    harness_runner._update(path, change)


def drain(provider, req):
    return list(provider.stream(req))


def test_a_send_killed_between_the_reply_and_the_charge_counts_at_its_bound_after_restart(run, mode):
    # Failing test first (acceptance 1).
    req = request(max_tokens=200_000)  # a bound of about EUR 2.8 on a EUR 5 cap
    hold = send_reservation.bound(req, "money_eur")
    provider = send_reservation.ReservingProvider(
        FakeProvider([{"usage": {"input_tokens": 10, "output_tokens": 10}}]), run, boot="boot-a")
    stream = provider.stream(req)
    for event in stream:
        if event.type == "message_done":
            break  # the provider replied; the process dies before the charge
    del stream  # nothing settles: the hold stays open on disk
    assert [r["key"] for r in ledger(run)["reservations"]]

    swept = harness_runner.Runner("boot-b").boot_sweep()

    assert swept == 1
    after = record(run)
    assert after["run"]["status"] == "needs_user" and after["run"]["reason"] == "uncertain"
    charges = ledger(run)["final_charges"]
    assert [(c["amount"], c["flags"]) for c in charges] == [(hold, ["uncertain"])]
    assert not ledger(run)["reservations"]
    # The hold still counts: a second send of the same size no longer fits.
    with pytest.raises(send_reservation.SendRefused) as refused:
        send_reservation.admit(run, request("again", max_tokens=200_000), boot="boot-b")
    assert refused.value.reason == "budget"


def test_the_boot_sweep_leaves_a_hold_of_a_live_lease_elsewhere(run, mode):
    send_reservation.admit(run, request(), boot="boot-a")

    def lease(task):
        task["run"]["runner"] = {"lease_owner": "boot-a", "lease_until": 2e12}
    harness_runner._update(run, lease)
    assert harness_runner.Runner("boot-b").boot_sweep() == 0
    assert len(ledger(run)["reservations"]) == 1


def test_a_takeover_reconciles_the_lost_send_and_stops_needs_user(run, mode):
    send_reservation.admit(run, request(), boot="boot-a")
    runner = harness_runner.Runner("boot-b")
    with pytest.raises(harness_runner._Stop) as stop:
        runner._recover(run)
    assert (stop.value.status, stop.value.reason) == ("needs_user", "uncertain")
    assert ledger(run)["final_charges"][0]["flags"] == ["uncertain"]


def test_two_concurrent_sends_cannot_together_exceed_the_cap(run, mode):
    req_a, req_b = request("a", max_tokens=200_000), request("b", max_tokens=200_000)
    assert 2 * send_reservation.bound(req_a, "money_eur") > harness_runner.DEFAULT_COST_EUR
    barrier = threading.Barrier(2)
    outcomes = []

    def send(req):
        barrier.wait()
        try:
            outcomes.append(send_reservation.admit(run, req, boot="boot-a"))
        except send_reservation.SendRefused as exc:
            outcomes.append(exc)

    threads = [threading.Thread(target=send, args=(r,)) for r in (req_a, req_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    refused = [o for o in outcomes if isinstance(o, send_reservation.SendRefused)]
    assert len(outcomes) == 2 and len(refused) == 1
    # The refusal is the existing cap stop (issue 02's `budget`).
    assert refused[0].reason == "budget"
    after = record(run)
    assert (after["run"]["status"], after["run"]["reason"]) == ("needs_user", "budget")
    assert len(ledger(run)["reservations"]) == 1


def test_a_retried_send_under_the_same_key_reserves_once(run, mode):
    inner = FakeProvider([ProviderError("rate_limited", "429"), {"usage": {"input_tokens": 50, "output_tokens": 7}}])
    provider = send_reservation.ReservingProvider(inner, run, boot="boot-a")
    req = request()
    with pytest.raises(ProviderError):
        drain(provider, req)
    first = ledger(run)["reservations"]
    drain(provider, req)  # the loop's retry: the same request
    budgets = ledger(run)
    assert len(first) == 1 and not budgets["reservations"]
    assert [c["key"] for c in budgets["final_charges"]] == [first[0]["key"]]
    assert budgets["final_charges"][0]["amount"] == pytest.approx((50 * RATE["input_tokens"] + 7 * RATE["output_tokens"]) / 1e6)
    # The same request sent again later is a new send.
    inner._turns.append({"usage": {"input_tokens": 1, "output_tokens": 1}})
    drain(provider, req)
    assert len(ledger(run)["final_charges"]) == 2


def test_a_reply_with_no_usage_settles_at_the_bound_never_zero(run, mode):
    req = request()
    provider = send_reservation.ReservingProvider(FakeProvider([{"usage": {}}]), run, boot="boot-a")
    drain(provider, req)
    charge = ledger(run)["final_charges"][0]
    assert charge["amount"] == send_reservation.bound(req, "money_eur") > 0
    assert charge["flags"] == ["no_usage"]


def test_a_send_that_errors_settles_uncertain_at_its_bound_at_turn_end(run, mode):
    provider = send_reservation.ReservingProvider(FakeProvider([ProviderError("upstream_error", "boom")]),
                                                  run, boot="boot-a")
    with pytest.raises(ProviderError):
        drain(provider, request())
    assert send_reservation.close_open(run, boot="boot-a") == 1
    assert ledger(run)["final_charges"][0]["flags"] == ["uncertain"]


@pytest.mark.parametrize("raised, max_tokens", [(None, 100_000), (10, 500_000)])
def test_a_research_send_that_fits_its_cap_is_admitted(run, mode, monkeypatch, raised, max_tokens):
    # EUR 1.4 under the EUR 2 default; EUR 7 under an envelope raised to EUR 10.
    monkeypatch.setattr(run_envelope, "read", lambda path: {"research": {"allowed": True, "cost_cap_eur": raised}} if raised else {})
    set_limit(run, cost_eur=20.0)
    req = request(max_tokens=max_tokens)
    provider = send_reservation.ReservingProvider(FakeProvider([{"usage": {"input_tokens": 9, "output_tokens": 9}}]),
                                                  run, boot="boot-a", scope="research")
    drain(provider, req)
    charge = ledger(run)["final_charges"][0]
    assert charge["kind"] == "research_send" and charge["unit"] == "eur" and charge["eur"] > 0
    assert record(run)["run"]["status"] == "running"


def test_a_research_send_over_its_cap_stops_research_and_the_run_goes_on(run, mode, monkeypatch):
    monkeypatch.setattr(run_envelope, "read", lambda path: {"research": {"allowed": True, "cost_cap_eur": 50}})
    assert send_reservation.research_cap(run) == run_envelope.RESEARCH_CAP_EUR == 10.0
    set_limit(run, cost_eur=40.0)
    with pytest.raises(send_reservation.SendRefused) as refused:
        send_reservation.admit(run, request(max_tokens=800_000), boot="boot-a", scope="research")  # about EUR 11
    assert refused.value.reason == "research_budget"
    assert record(run)["run"]["status"] == "running" and not ledger(run)["reservations"]


def test_a_research_hold_also_counts_toward_the_runs_total(run, mode):
    send_reservation.admit(run, request("r", max_tokens=100_000), boot="boot-a", scope="research")
    send_reservation.admit(run, request("m", max_tokens=200_000), boot="boot-a")
    with pytest.raises(send_reservation.SendRefused) as refused:
        send_reservation.admit(run, request("x", max_tokens=60_000), boot="boot-a")
    assert refused.value.reason == "budget"


def test_a_missing_price_or_an_unbounded_input_refuses_money_admission(run, mode):
    with pytest.raises(send_reservation.SendRefused) as refused:
        send_reservation.admit(run, request(model="unknown-model"), boot="boot-a")
    assert refused.value.reason == "no_price"
    image = request()
    image.messages = [{"role": "user", "content": [{"type": "image", "source": {"data": "x"}}]}]
    with pytest.raises(send_reservation.SendRefused) as refused:
        send_reservation.admit(run, image, boot="boot-a")
    assert refused.value.reason == "unbounded_input"
    assert not ledger(run)["reservations"] and record(run)["run"]["status"] == "running"


def test_token_modes_reserve_in_their_own_unit(run, mode):
    mode("output_tokens")
    send_reservation.admit(run, request(max_tokens=1234), boot="boot-a")
    hold = ledger(run)["reservations"][0]
    assert (hold["unit"], hold["amount"]) == ("output_tokens", 1234)
    mode("total_tokens")
    with pytest.raises(send_reservation.SendRefused) as refused:
        send_reservation.admit(run, request("t"), boot="boot-a")
    assert refused.value.reason == "no_cap"  # no total-token cap set: refused, not assumed


def test_an_unknown_mode_refuses_every_send(run, mode):
    mode("tokens")
    with pytest.raises(send_reservation.SendRefused) as refused:
        send_reservation.admit(run, request(), boot="boot-a")
    assert refused.value.reason == "invalid_mode"


def test_mode_off_leaves_the_provider_as_it_is(run, monkeypatch):
    monkeypatch.delenv(send_reservation.MODE_ENV, raising=False)
    provider = FakeProvider([])
    assert harness_runner.Runner("a", agent_provider=provider)._round_provider(run, None) is provider


def test_an_agent_round_reserves_and_settles_each_send(granted, profile, site, rated, monkeypatch):
    monkeypatch.setenv(send_reservation.MODE_ENV, "output_tokens")
    profile(BLOCK)
    no_series()
    task, path, _ = granted()
    script = Script(call("validate_network", {}), say("Done."))
    record_ = go(harness_runner.Runner("a", agent_provider=script), path, task)
    budgets = record_["run"]["budgets"]
    assert not budgets["reservations"]
    assert [c["amount"] for c in budgets["final_charges"]] == [USAGE["output_tokens"]] * len(script.requests)
    assert all(c["kind"] == "model_send" and "flags" not in c for c in budgets["final_charges"])


# ── GK B plan verdict (R1–R6) ────────────────────────────────────────────────


def test_the_lv09_ledger_cores_reserve_once_per_key_and_settle_once():
    # (a) Pins the private cores this module reuses until they go public.
    from fastapi import HTTPException
    from services import durable_conversation as dc
    thread = {}
    assert dc._reserve_in(thread, key="k", kind="model_send", amount=5, unit="eur") is True
    assert dc._reserve_in(thread, key="k", kind="model_send", amount=5, unit="eur") is False
    with pytest.raises(HTTPException) as conflict:
        dc._reserve_in(thread, key="k", kind="model_send", amount=6, unit="eur")
    assert conflict.value.detail["error_kind"] == "budget_key_conflict"
    assert thread["budgets"]["reservations"] == [{"key": "k", "kind": "model_send", "amount": 5, "unit": "eur"}]
    dc._settle_in(thread, key="k", amount=3, unit="eur", flags=["no_usage"])
    assert thread["budgets"]["reservations"] == []
    assert thread["budgets"]["final_charges"] == [
        {"key": "k", "kind": "model_send", "amount": 3, "unit": "eur", "flags": ["no_usage"]}]
    for again in (lambda: dc._reserve_in(thread, key="k", kind="model_send", amount=5, unit="eur"),
                  lambda: dc._settle_in(thread, key="k", amount=3, unit="eur")):
        with pytest.raises(HTTPException) as settled:
            again()
        assert settled.value.detail["error_kind"] == "budget_already_settled"
    # Hold arithmetic: settled charges and open holds both count.
    dc._reserve_in(thread, key="j", kind="model_send", amount=2, unit="eur")
    assert send_reservation._spent(thread, "eur") == 5
    assert send_reservation.summary(thread) == {"unit": "eur", "spent": 5.0, "open": 2.0, "at_bound": 1}


def search_tool(**extra):
    return {"type": "web_search_20250305", "name": "web_search", **extra}


def test_a_server_tool_adds_its_uses_fees_and_content_to_the_bound(monkeypatch, mode):
    plain = request()
    tooled = request()
    tooled.tools = [search_tool(max_uses=3)]
    # No known cost, or no max_uses: unbounded, refused.
    with pytest.raises(send_reservation.SendRefused) as refused:
        send_reservation.bound(tooled, "money_eur")
    assert refused.value.reason == "unbounded_input"
    monkeypatch.setenv(send_reservation.SERVER_TOOL_COSTS_ENV,
                       json.dumps({"web_search": {"fee_eur": 0.01, "content_tokens": 5000}}))
    worst_in = max(RATE["input_tokens"], RATE["cache_read_tokens"], RATE["cache_create_tokens"])
    extra_bytes = len(json.dumps(tooled.tools).encode()) - len(json.dumps([]).encode())
    expected = send_reservation.bound(plain, "money_eur") + 3 * 0.01 + (3 * 5000 + extra_bytes) * worst_in / 1e6
    assert send_reservation.bound(tooled, "money_eur") == pytest.approx(expected, abs=1e-5)
    unlimited = request()
    unlimited.tools = [search_tool()]
    with pytest.raises(send_reservation.SendRefused):
        send_reservation.bound(unlimited, "total_tokens")


def test_the_reservation_sees_the_final_request_inside_a_research_wrapper(run, mode, monkeypatch):
    # R1 order: Research(Reserving(base)); SH-09's wrapper adds its server tools.
    monkeypatch.setenv(send_reservation.SERVER_TOOL_COSTS_ENV,
                       json.dumps({"web_search": {"fee_eur": 0.5, "content_tokens": 1000}}))

    class Research:
        def __init__(self, inner):
            self.inner = inner

        def stream(self, req):
            req.tools = [*req.tools, search_tool(max_uses=2)]
            yield from self.inner.stream(req)

    base = FakeProvider([{"usage": {}}])
    wrapped = Research(send_reservation.ReservingProvider(base, run, boot="boot-a", scope="research"))
    drain(wrapped, request())
    charge = ledger(run)["final_charges"][0]
    assert base.requests[0].tools[-1]["name"] == "web_search"
    assert charge["amount"] == send_reservation.bound(base.requests[0], "money_eur") > 1.0  # the 2 x EUR 0.5 fees


def test_a_refusal_is_never_retried_by_the_loop(run, mode, monkeypatch):
    # R2: the loop's own retry path (`_stream_assistant_message`) does not re-send.
    from tests.test_chat_stream_attempt_seam import _drive
    from services import chat_service
    monkeypatch.setattr(chat_service.time, "sleep", lambda _s: None)
    set_limit(run, cost_eur=0.0)
    admits = []
    real = send_reservation.admit
    monkeypatch.setattr(send_reservation, "admit", lambda *a, **k: admits.append(1) or real(*a, **k))
    base = FakeProvider([{"usage": {"output_tokens": 1}}] * 5)
    frames, out = _drive(send_reservation.ReservingProvider(base, run, boot="boot-a"))
    assert len(admits) == 1 and base.requests == [] and out.stop_turn is True
    assert any(name == "error" for name, _ in frames)
    assert record(run)["run"]["reason"] == "budget"


def test_the_run_view_says_the_spend_includes_bounds(run, mode):
    # R3
    from services import run_monitor
    assert "reserved" not in run_monitor._budgets(record(run), None)
    drain(send_reservation.ReservingProvider(FakeProvider([{"usage": {}}]), run, boot="boot-a"), request())
    reserved = run_monitor._budgets(record(run), None)["reserved"]
    assert reserved["unit"] == "eur" and reserved["at_bound"] == 1 and reserved["open"] == 0
    assert reserved["spent"] == pytest.approx(send_reservation.bound(request(), "money_eur"))


def test_two_identical_requests_in_flight_each_hold_their_own_bound(run, mode):
    req = request()
    provider = send_reservation.ReservingProvider(
        FakeProvider([{"usage": {"output_tokens": 1}}, {"usage": {"output_tokens": 1}}]), run, boot="boot-a")
    first, second = provider.stream(req), provider.stream(request())
    next(first), next(second)  # both admitted, neither settled
    holds = ledger(run)["reservations"]
    assert len(holds) == 2 and holds[0]["key"] != holds[1]["key"]
    list(first), list(second)
    assert len(ledger(run)["final_charges"]) == 2 and not ledger(run)["reservations"]


def test_research_is_enforced_with_the_mode_off(run, monkeypatch):
    # GK B (b): the reservation is the research enforcement, whatever the mode.
    monkeypatch.delenv(send_reservation.MODE_ENV, raising=False)
    send_reservation.admit(run, request(max_tokens=100_000), boot="boot-a", scope="research")
    assert ledger(run)["reservations"][0]["unit"] == "eur"
    with pytest.raises(send_reservation.SendRefused) as refused:
        send_reservation.admit(run, request("more", max_tokens=100_000), boot="boot-a", scope="research")
    assert refused.value.reason == "research_budget"
    with pytest.raises(ValueError):
        send_reservation.admit(run, request(), boot="boot-a")
