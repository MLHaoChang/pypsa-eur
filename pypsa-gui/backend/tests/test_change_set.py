"""LC-47: one change-set card per response, a tick box per row
(`harness/change_set.py`; GK B K1–K9)."""

from __future__ import annotations

import contextvars
import json
import threading
import time

import pypsa
import pytest
from fastapi import HTTPException

from harness import approvals, change_set, confirm, edit_card
from harness import session as harness_session
from routers import simulation as sim_router
from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService
from tests.test_chat_e2e import (
    FakeAnthropicClient,
    _FakeFinalMessage,
    _FakeUsage,
    _text_block,
    _text_event,
    _tool_use_block,
    _tool_use_event,
)

GUIDED = {"ui_mode": "guided", "panel": "hubDesign", "guided_step": "site"}
EXPERT = {"ui_mode": "expert"}

B1_POC = (
    "update_component",
    {"component_class": "Bus", "name": "B1", "attrs": {"eh_poc": True}},
)
B2_CRIT = (
    "update_component",
    {"component_class": "Bus", "name": "B2", "attrs": {"eh_critical": True}},
)
VOLL = ("update_solver_config", {"partial": {"voll": 4321.0}})


@pytest.fixture
def world(tmp_projects_dir, install_network, monkeypatch):
    monkeypatch.setattr(harness_session, "CONFIRMATION_TTL_SECONDS", 5.0)
    monkeypatch.setattr(change_set, "ENABLED", True)
    n = pypsa.Network()
    n.add("Carrier", "AC")  # else each bus edit also creates it, and K2 splits them
    for bus in ("B1", "B2", "B3"):
        n.add("Bus", bus)
    install_network(n, name=None)
    return n


def _flag(bus: str, col: str) -> bool:
    b = PyPSAService.get_network().buses
    return bool(b.at[bus, col]) if col in b.columns else False


def _voll() -> float:
    return float(sim_router._state["solver_config"].voll)


def _client(calls):
    t1 = (
        [
            _tool_use_event(f"tu-{i}", name, args)
            for i, (name, args) in enumerate(calls)
        ],
        _FakeFinalMessage(
            content=[
                _tool_use_block(f"tu-{i}", name, args)
                for i, (name, args) in enumerate(calls)
            ],
            usage=_FakeUsage(),
        ),
    )
    t2 = (
        [_text_event("done")],
        _FakeFinalMessage(content=[_text_block("done")], usage=_FakeUsage()),
    )
    return FakeAnthropicClient([t1, t2])


def _run(session, calls, ui_context, decisions):
    """Drive one turn; answer each card in turn with (decision, ticked)."""
    streamed: list = []
    done = threading.Event()

    def go():
        for ev in chat_service.run_turn(
            session, "do it", client=_client(calls), ui_context=ui_context
        ):
            streamed.append(ev)
        done.set()

    t = threading.Thread(target=contextvars.copy_context().run, args=(go,))
    t.start()
    for decision, ticked in decisions:
        deadline, token = time.monotonic() + 5.0, None
        while time.monotonic() < deadline and token is None:
            with session._lock:
                token = next(iter(session.pending_confirmations), None)
            if token is None:
                time.sleep(0.02)
        assert token, "no confirmation card appeared"
        if decision is not None:
            session.record_decision(token, decision, ticked)
    assert done.wait(10.0), "turn did not finish"
    t.join()
    return streamed


def _cards(events):
    return [p for e, p in events if e == "tool_pending_confirmation"]


def _receipt(events):
    found = [p for e, p in events if e == "change_set_receipt"]
    assert len(found) == 1
    return {r["tool_use_id"]: r["status"] for r in found[0]["rows"]}, found[0]


def _errors(events):
    return {p["tool_use_id"]: p["error_kind"] for e, p in events if e == "tool_error"}


def _one_result_per_call(session, n):
    results = [
        b
        for m in session.messages
        if m.get("role") == "user" and isinstance(m.get("content"), list)
        for b in m["content"]
        if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    assert sorted(b["tool_use_id"] for b in results) == [f"tu-{i}" for i in range(n)]


# ── one card, ticks, the receipt ────────────────────────────────────────────


@pytest.mark.parametrize("ui", [EXPERT, GUIDED], ids=["expert", "guided"])
def test_n_writes_raise_one_card_and_all_ticked_apply_each_read_back(world, ui):
    session = chat_service.ChatSession()
    events = _run(session, [B1_POC, B2_CRIT, VOLL], ui, [("approve", None)])
    cards = _cards(events)
    assert len(cards) == 1
    items = cards[0]["change_set"]["items"]
    assert [i["tool_use_id"] for i in items] == ["tu-0", "tu-1", "tu-2"]
    assert all(i["edit_card"]["rows"] for i in items)
    assert _flag("B1", "eh_poc") and _flag("B2", "eh_critical") and _voll() == 4321.0
    statuses, receipt = _receipt(events)
    assert statuses == {"tu-0": "applied", "tu-1": "applied", "tu-2": "applied"}
    assert receipt["landed"] == 3 and receipt["all_applied"] is True
    # each row keeps its own native read-back
    results = {p["tool_use_id"]: p["result"] for e, p in events if e == "tool_result"}
    assert all("_edit_receipt" in str(results[t]) for t in ("tu-0", "tu-1", "tu-2"))
    _one_result_per_call(session, 3)


def test_ticking_a_subset_applies_exactly_that_subset(world):
    voll0 = _voll()
    session = chat_service.ChatSession()
    events = _run(
        session, [B1_POC, B2_CRIT, VOLL], EXPERT, [("approve", ["tu-0", "tu-2"])]
    )
    assert len(_cards(events)) == 1
    assert _flag("B1", "eh_poc") and _voll() == 4321.0 != voll0
    assert not _flag("B2", "eh_critical")  # the unticked row is not written
    assert _errors(events) == {"tu-1": "change_unticked"}
    statuses, receipt = _receipt(events)
    assert statuses == {"tu-0": "applied", "tu-1": "unticked", "tu-2": "applied"}
    assert (
        receipt["ticked"] == 2
        and receipt["landed"] == 2
        and receipt["all_applied"] is True
    )
    _one_result_per_call(session, 3)


@pytest.mark.parametrize(
    "decision,ticked,kind",
    [
        ("approve", [], "confirmation_denied"),  # approving no row is a deny
        ("deny", None, "confirmation_denied"),
    ],
)
def test_no_row_or_a_whole_deny_writes_nothing(world, decision, ticked, kind):
    voll0 = _voll()
    session = chat_service.ChatSession()
    events = _run(session, [B1_POC, B2_CRIT, VOLL], EXPERT, [(decision, ticked)])
    assert (
        not _flag("B1", "eh_poc")
        and not _flag("B2", "eh_critical")
        and _voll() == voll0
    )
    assert _errors(events) == {"tu-0": kind, "tu-1": kind, "tu-2": kind}
    _, receipt = _receipt(events)
    assert receipt["landed"] == 0 and receipt["all_applied"] is False
    _one_result_per_call(session, 3)


def test_an_expired_set_denies_every_member(world, monkeypatch):
    monkeypatch.setattr(harness_session, "CONFIRMATION_TTL_SECONDS", 0.2)
    session = chat_service.ChatSession()
    events = _run(session, [B1_POC, B2_CRIT], EXPERT, [(None, None)])
    assert set(_errors(events).values()) == {"confirmation_expired"}
    assert not _flag("B1", "eh_poc") and not _flag("B2", "eh_critical")


def test_a_partly_failed_apply_reports_which_rows_landed(world, monkeypatch):
    """Row 2's handler fails; row 1 has landed and row 3 still runs (pinned)."""

    def broken(*a, **k):
        raise HTTPException(500, "solver config store unavailable")

    monkeypatch.setitem(chat_tools.DISPATCHERS, "update_solver_config", broken)
    session = chat_service.ChatSession()
    calls = [
        B1_POC,
        VOLL,
        (
            "update_component",
            {"component_class": "Bus", "name": "B3", "attrs": {"eh_critical": True}},
        ),
    ]
    events = _run(session, calls, EXPERT, [("approve", None)])
    assert len(_cards(events)) == 1
    statuses, receipt = _receipt(events)
    assert statuses == {"tu-0": "applied", "tu-1": "failed", "tu-2": "applied"}
    assert (
        receipt["landed"] == 2
        and receipt["failed"] == 1
        and receipt["all_applied"] is False
    )
    assert _flag("B1", "eh_poc") and _flag("B3", "eh_critical")
    _one_result_per_call(session, 3)


def test_an_uncertain_read_back_never_counts_as_landed(world, monkeypatch):
    real = edit_card.receipt
    monkeypatch.setattr(
        edit_card,
        "receipt",
        lambda card, args: {**real(card, args), "outcome": "uncertain"},
    )
    events = _run(
        chat_service.ChatSession(), [B1_POC, B2_CRIT], EXPERT, [("approve", None)]
    )
    statuses, receipt = _receipt(events)
    assert statuses == {"tu-0": "uncertain", "tu-1": "uncertain"}
    assert receipt["landed"] == 0 and receipt["all_applied"] is False


def test_a_row_that_changes_nothing_is_not_counted_as_landed(world):
    PyPSAService.get_network().buses.loc["B1", "eh_poc"] = True
    events = _run(
        chat_service.ChatSession(), [B1_POC, B2_CRIT], EXPERT, [("approve", None)]
    )
    statuses, receipt = _receipt(events)
    assert statuses == {"tu-0": "unchanged", "tu-1": "applied"}
    assert receipt["landed"] == 1 and receipt["all_applied"] is False


def test_a_ticked_row_whose_input_moved_is_refused_stale(world, monkeypatch):
    """K3: row 1's write also moves row 2's input (another component, so K2
    cannot see it); row 2 is refused and nothing is written for it."""
    real = chat_tools.DISPATCHERS["update_component"]

    def moving(*a, **k):
        out = real(*a, **k)
        PyPSAService.get_network().buses.loc["B2", "v_nom"] = 33.0
        return out

    monkeypatch.setitem(chat_tools.DISPATCHERS, "update_component", moving)
    session = chat_service.ChatSession()
    calls = [
        B1_POC,
        (
            "update_component",
            {"component_class": "Bus", "name": "B2", "attrs": {"v_nom": 20.0}},
        ),
    ]
    events = _run(session, calls, EXPERT, [("approve", None)])
    assert _errors(events) == {"tu-1": "edit_preview_stale"}
    assert PyPSAService.get_network().buses.at["B2", "v_nom"] == 33.0
    statuses, _ = _receipt(events)
    assert statuses == {"tu-0": "applied", "tu-1": "stale"}


def test_a_column_an_earlier_row_created_is_not_stale(world):
    """Row 1 creates `eh_critical`, so row 2's old moves from "not set" to the
    column default: the same field, written by an earlier landed row of the set."""
    calls = [
        (
            "update_component",
            {"component_class": "Bus", "name": "B1", "attrs": {"eh_critical": True}},
        ),
        B2_CRIT,
    ]
    events = _run(chat_service.ChatSession(), calls, EXPERT, [("approve", None)])
    assert _errors(events) == {}
    assert _flag("B1", "eh_critical") and _flag("B2", "eh_critical")


def test_two_writes_on_one_component_never_share_a_set(world):
    """K2: the later write on B1 keeps its own card, after the set's."""
    calls = [
        B1_POC,
        (
            "update_component",
            {"component_class": "Bus", "name": "B1", "attrs": {"eh_critical": True}},
        ),
        B2_CRIT,
    ]
    events = _run(
        chat_service.ChatSession(),
        calls,
        EXPERT,
        [("approve", None), ("approve", None)],
    )
    cards = _cards(events)
    assert len(cards) == 2
    assert [i["tool_use_id"] for i in cards[0]["change_set"]["items"]] == [
        "tu-0",
        "tu-2",
    ]
    assert "change_set" not in cards[1] and cards[1]["tool_use_id"] == "tu-1"
    assert (
        _flag("B1", "eh_poc")
        and _flag("B1", "eh_critical")
        and _flag("B2", "eh_critical")
    )


def test_one_write_keeps_its_own_card(world):
    events = _run(chat_service.ChatSession(), [B1_POC], EXPERT, [("approve", None)])
    assert [c.get("change_set") for c in _cards(events)] == [None]
    assert not [e for e, _ in events if e == "change_set_receipt"]


def test_off_until_the_card_renders(world, monkeypatch):
    monkeypatch.setattr(change_set, "ENABLED", False)
    events = _run(
        chat_service.ChatSession(),
        [B1_POC, B2_CRIT],
        EXPERT,
        [("approve", None), ("approve", None)],
    )
    assert [c["tool_use_id"] for c in _cards(events)] == ["tu-0", "tu-1"]
    assert not any("change_set" in c for c in _cards(events))


# ── Guided and approvals (K8) ───────────────────────────────────────────────


@pytest.mark.parametrize("ui", [EXPERT, GUIDED], ids=["expert", "guided"])
def test_the_set_is_never_auto_approved(world, monkeypatch, ui):
    monkeypatch.setattr(confirm, "AUTO_APPROVE_TIERS", confirm.DESTRUCTIVE_TIERS)
    events = _run(
        chat_service.ChatSession(), [B1_POC, B2_CRIT], ui, [("approve", ["tu-1"])]
    )
    assert len(_cards(events)) == 1
    assert not _flag("B1", "eh_poc") and _flag("B2", "eh_critical")


def test_approvals_carry_the_set_token_and_by_only_for_ticked_rows(world, monkeypatch):
    asked, decided = [], []
    real_asked, real_decided = approvals.asked, approvals.decided
    monkeypatch.setattr(
        approvals, "asked", lambda s, **k: (asked.append(k), real_asked(s, **k))[1]
    )
    monkeypatch.setattr(
        approvals,
        "decided",
        lambda s, **k: (decided.append(k), real_decided(s, **k))[1],
    )
    events = _run(
        chat_service.ChatSession(),
        [B1_POC, B2_CRIT, VOLL],
        EXPERT,
        [("approve", ["tu-0", "tu-2"])],
    )
    token = _cards(events)[0]["confirmation_token"]
    assert [a["call_id"] for a in asked] == ["tu-0", "tu-1", "tu-2"]
    assert {a["token"] for a in asked} == {token}
    by = {d["call_id"]: (d["outcome"], d["by"]) for d in decided}
    assert by == {
        "tu-0": ("approved", f"change_set:{token[:6]}"),
        "tu-1": ("rejected", "user"),
        "tu-2": ("approved", f"change_set:{token[:6]}"),
    }


def test_the_decider_pattern_is_closed():
    with pytest.raises(ValueError):
        approvals.decided(
            chat_service.ChatSession(),
            call_id="x",
            tool="t",
            tier="write",
            outcome="approved",
            by="change_set:not-hex",
        )


# ── /confirm ticked (K5) ────────────────────────────────────────────────────


def _pending(session, tool_name, args):
    return session.issue_confirmation(
        tool_name=tool_name, args=args, safety_tier="write"
    )


def test_ticked_on_a_single_card_is_a_400_and_the_token_stays_alive(world):
    session = chat_service.ChatSession()
    pc = _pending(session, "update_component", {})
    with pytest.raises(HTTPException) as exc:
        session.record_decision(pc.token, "approve", ["tu-0"])
    assert (
        exc.value.status_code == 400
        and exc.value.detail["error_kind"] == "invalid_decision"
    )
    assert pc.token in session.pending_confirmations
    session.record_decision(pc.token, "approve")  # the retry without ticks succeeds


@pytest.mark.parametrize("ticked", [["tu-9"], ["tu-0", "tu-0"], "tu-0", [1]])
def test_ticked_rows_outside_the_card_are_a_400(world, ticked):
    session = chat_service.ChatSession()
    pc = _pending(session, change_set.TOOL, {"tool_use_ids": ["tu-0", "tu-1"]})
    with pytest.raises(HTTPException) as exc:
        session.record_decision(pc.token, "approve", ticked)
    assert exc.value.status_code == 400
    assert pc.token in session.pending_confirmations


def test_record_decision_stores_the_ticked_rows(world):
    session = chat_service.ChatSession()
    pc = _pending(session, change_set.TOOL, {"tool_use_ids": ["tu-0", "tu-1"]})
    session.record_decision(pc.token, "approve", ["tu-1"])
    assert session.confirmation_ticks[pc.token] == ["tu-1"]


def test_the_confirm_route_passes_ticked(world, monkeypatch):
    from routers import chat as chat_router

    seen = {}

    class _S:
        def record_decision(self, token, decision, ticked=None):
            seen.update(token=token, decision=decision, ticked=ticked)
            return harness_session.PendingConfirmation(
                token=token,
                tool_name=change_set.TOOL,
                args={},
                safety_tier="write",
                created_at=0,
                expires_at=1,
            )

    monkeypatch.setattr(chat_router, "_resolve_owned_session", lambda sid, actor: _S())
    chat_router.chat_confirm(
        "s",
        chat_router.ConfirmRequest(token="t", decision="approve", ticked=["tu-0"]),
        actor=None,
    )
    assert seen == {"token": "t", "decision": "approve", "ticked": ["tu-0"]}


# ── membership (K4, K9) ─────────────────────────────────────────────────────


def _excl(
    session=None,
    name="update_component",
    args=None,
    *,
    tier="write",
    external=False,
    redirects=None,
    tid="tu-0",
):
    return change_set.exclusion(
        session or chat_service.ChatSession(),
        {"id": tid, "name": name, "input": args or B1_POC[1]},
        tier=tier,
        external=external,
        redirects=redirects,
    )


def test_an_ordinary_write_edit_may_join(world):
    assert _excl() is None


@pytest.mark.parametrize(
    "name,kind",
    [
        ("delete_component", "tier"),
        ("import_excel", "tier"),
        ("site_scene_apply", "card_only"),
        ("run_sensitivity_sweep", "not_edit"),
    ],
)
def test_destructive_card_only_and_non_edits_never_join(world, name, kind):
    from harness import catalogue

    assert _excl(name=name, tier=catalogue.safety_tier_for(name)) == kind


def test_always_card_and_requirement_tools_never_join(world, monkeypatch):
    from harness import requirements

    monkeypatch.setattr(edit_card, "is_edit", lambda name: True)
    assert _excl(name="propose_memory") == "card_only"
    assert _excl(name=requirements.TOOL) == "card_only"


def test_external_job_task_assurance_run_and_routed_calls_never_join(
    world, monkeypatch
):
    from harness import job_holds
    from services import assistant_tasks

    assert _excl(external=True) == "external"
    assert _excl(redirects={"tu-0": "use the preview"}) == "routed"
    s = chat_service.ChatSession()
    s.assurance_turn = object()
    assert _excl(s) == "assurance"
    s = chat_service.ChatSession()
    s.decision_guard = lambda *a: True
    assert _excl(s) == "run_session"
    monkeypatch.setattr(assistant_tasks, "is_next_step", lambda *a: True)
    assert _excl() == "task"
    monkeypatch.setattr(assistant_tasks, "is_next_step", lambda *a: False)
    monkeypatch.setattr(assistant_tasks, "governs", lambda *a: True)
    assert _excl() == "task"
    monkeypatch.setattr(assistant_tasks, "governs", lambda *a: False)
    monkeypatch.setattr(job_holds, "is_job_tool", lambda name: True)
    assert _excl() == "job"


def test_a_run_session_never_joins(world, monkeypatch):
    from harness import requirements

    monkeypatch.setattr(requirements, "_is_run_session", lambda s: True)
    assert _excl() == "run_session"


def test_every_destructive_edit_tool_stays_out():
    """The walk: every is_edit tool not of tier write is excluded, and so is
    every delete or import of any tier."""
    from harness import catalogue
    from services import chat_tools_schema

    for tool in chat_tools_schema.TOOLS:
        name = tool["name"]
        if not edit_card.is_edit(name):
            continue
        tier = catalogue.safety_tier_for(name)
        if tier != "write":
            assert _excl(name=name, tier=tier) is not None, name
        if "delete" in name or name.startswith(
            (
                "import_network",
                "import_csv",
                "import_excel",
                "import_matpower",
                "import_uploaded",
            )
        ):
            assert tier != "write", name


def _members(session, calls, **kw):
    tus = [{"id": f"tu-{i}", "name": n, "input": a} for i, (n, a) in enumerate(calls)]
    return change_set.members(
        session,
        tus,
        tier_of=lambda n: "write",
        external_of=lambda n: False,
        redirects=kw.get("redirects"),
    )


def test_a_blocked_restriction_or_a_failing_preview_keeps_the_own_path(
    world, monkeypatch
):
    from harness import restrictions

    s = chat_service.ChatSession()
    gone = (
        "update_component",
        {"component_class": "Bus", "name": "nope", "attrs": {"eh_poc": True}},
    )
    assert [i["tool_use_id"] for i in _members(s, [B1_POC, gone, B2_CRIT])] == [
        "tu-0",
        "tu-2",
    ]
    assert _members(s, [B1_POC, gone]) == []  # one left: no set

    def blocked(snapshot, tier):
        raise HTTPException(403, {"error_kind": "user_restriction"})

    monkeypatch.setattr(restrictions, "require_allowed", blocked)
    assert _members(s, [B1_POC, B2_CRIT]) == []


def test_requirements_checked_on_the_combined_rows(world, monkeypatch):
    """K1 (A): the union of the members' rows is checked; a set that would break
    a requirement is not offered, and an unknown predicate kind fails closed."""
    from harness import requirements

    s = chat_service.ChatSession()
    bound = {
        "id": "r1",
        "predicate": {
            "kind": "scalar_bound",
            "component_class": "Bus",
            "name": "B2",
            "field": "v_nom",
            "op": "within",
            "lo": 0,
            "hi": 50,
        },
    }
    monkeypatch.setattr(requirements, "active_for", lambda session: ("t", [bound]))
    monkeypatch.setattr(
        requirements, "annotate", lambda session, tool, args, card: card
    )
    high = (
        "update_component",
        {"component_class": "Bus", "name": "B2", "attrs": {"v_nom": 110.0}},
    )
    assert _members(s, [B1_POC, high]) == []
    ok = (
        "update_component",
        {"component_class": "Bus", "name": "B2", "attrs": {"v_nom": 20.0}},
    )
    assert len(_members(s, [B1_POC, ok])) == 2
    odd = {"id": "r2", "predicate": {"kind": "sum_bound"}}
    monkeypatch.setattr(requirements, "active_for", lambda session: ("t", [odd]))
    assert _members(s, [B1_POC, ok]) == []


def test_source_is_never_model_text(world):
    """K9: a source-like argument never reaches the card."""
    s = chat_service.ChatSession()
    items = _members(
        s,
        [
            (
                "update_component",
                {
                    "component_class": "Bus",
                    "name": "B1",
                    "attrs": {"eh_poc": True},
                    "source": "TRUST ME",
                },
            ),
            B2_CRIT,
        ],
    )
    payload = change_set.card_payload(items) if items else {"items": []}
    assert "TRUST ME" not in json.dumps(
        [{k: v for k, v in i.items() if k != "digest"} for i in payload["items"]]
    )
    for item in payload["items"]:
        assert item["source"] == change_set.SOURCE_ASSISTANT


def test_a_call_the_dispatcher_would_refuse_never_shows_as_a_row(world, monkeypatch):
    """The required guards and the advisory validator refuse before any card,
    so such a call keeps its own path (and its own refusal)."""
    from harness import guards

    s = chat_service.ChatSession()
    monkeypatch.setattr(
        guards,
        "check",
        lambda name, args, tier: (
            ("bad_args", "no") if args.get("name") == "B1" else None
        ),
    )
    assert _members(s, [B1_POC, B2_CRIT, VOLL])[0]["tool_use_id"] == "tu-1"
    monkeypatch.setattr(guards, "check", lambda name, args, tier: None)
    monkeypatch.setitem(
        chat_tools.PRE_DISPATCH_VALIDATORS,
        "update_solver_config",
        lambda args: "bad voll",
    )
    assert [i["tool_use_id"] for i in _members(s, [B1_POC, B2_CRIT, VOLL])] == [
        "tu-0",
        "tu-1",
    ]


# ── K1a–d: the combined requirements check ──────────────────────────────────


def _bound(field="v_nom", name="B2", hi=50):
    return {
        "id": "r1",
        "predicate": {
            "kind": "scalar_bound",
            "component_class": "Bus",
            "name": name,
            "field": field,
            "op": "within",
            "lo": 0,
            "hi": hi,
        },
    }


@pytest.mark.parametrize("kind", ["preserve_horizon", "sum_bound", "some_future_kind"])
def test_only_allow_listed_predicate_kinds_permit_a_set(world, monkeypatch, kind):
    """K1a: scalar_bound only; any other active kind means no set."""
    from harness import requirements

    s = chat_service.ChatSession()
    monkeypatch.setattr(
        requirements, "annotate", lambda session, tool, args, card: card
    )
    monkeypatch.setattr(requirements, "active_for", lambda session: ("t", [_bound()]))
    assert len(_members(s, [B1_POC, B2_CRIT])) == 2
    other = {"id": "r2", "predicate": {"kind": kind}}
    monkeypatch.setattr(
        requirements, "active_for", lambda session: ("t", [_bound(), other])
    )
    assert _members(s, [B1_POC, B2_CRIT]) == []


def test_a_union_that_trips_a_bound_is_not_offered(world, monkeypatch):
    """K1c: `violations` over the union trips; per-member checks are bypassed
    so only the union check stands between the rows and the card."""
    from harness import requirements

    s = chat_service.ChatSession()
    monkeypatch.setattr(
        requirements, "annotate", lambda session, tool, args, card: card
    )
    monkeypatch.setattr(requirements, "active_for", lambda session: ("t", [_bound()]))
    real = requirements.violations
    monkeypatch.setattr(
        requirements,
        "violations",
        lambda tool, *a: real(tool, *a) if tool == change_set.TOOL else [],
    )
    high = (
        "update_component",
        {"component_class": "Bus", "name": "B2", "attrs": {"v_nom": 110.0}},
    )
    assert _members(s, [B1_POC, high]) == []


def test_two_rows_on_one_component_that_would_trip_a_bound_together_are_split(
    world, monkeypatch
):
    """K1c: the only way two rows meet on one bounded field is one component;
    K2 keeps the later one out, so it gets its own card and its own check."""
    from harness import requirements

    s = chat_service.ChatSession()
    monkeypatch.setattr(
        requirements, "active_for", lambda session: ("t", [_bound(hi=50)])
    )
    first = (
        "update_component",
        {"component_class": "Bus", "name": "B2", "attrs": {"v_nom": 40.0}},
    )
    second = (
        "update_component",
        {"component_class": "Bus", "name": "B2", "attrs": {"v_nom": 45.0}},
    )
    assert [i["tool_use_id"] for i in _members(s, [first, second, B1_POC])] == [
        "tu-0",
        "tu-2",
    ]


def test_a_derived_row_overlap_splits_the_set(
    tmp_projects_dir, install_network, monkeypatch
):
    """K1b: with no AC carrier, each bus edit's own preview also creates
    Carrier AC (a derived row), so two such edits never share a set."""
    monkeypatch.setattr(change_set, "ENABLED", True)
    n = pypsa.Network()
    for bus in ("B1", "B2", "B3"):
        n.add("Bus", bus)
    install_network(n, name=None)
    s = chat_service.ChatSession()
    assert ("Carrier", "AC") in change_set._targets(edit_card.preview(*B1_POC))
    third = ("update_solver_config", {"partial": {"voll": 4321.0}})
    assert [i["tool_use_id"] for i in _members(s, [B1_POC, B2_CRIT, third])] == [
        "tu-0",
        "tu-2",
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"complete": False},
        {"rows": []},
        {"more": True},
    ],
)
def test_an_incomplete_preview_keeps_its_own_card(world, monkeypatch, change):
    """K1b: no complete row list, or a "more" note, is not a member."""
    real = edit_card.preview

    def preview(tool, args, **k):
        card = real(tool, args, **k)
        if tool == "update_solver_config":
            if change.get("more"):
                card["rows"] = card["rows"] + [
                    {
                        "role": "note",
                        "field": "more",
                        "component_class": None,
                        "name": None,
                    }
                ]
            else:
                card.update(change)
        return card

    monkeypatch.setattr(edit_card, "preview", preview)
    s = chat_service.ChatSession()
    assert [i["tool_use_id"] for i in _members(s, [B1_POC, B2_CRIT, VOLL])] == [
        "tu-0",
        "tu-1",
    ]


def test_the_card_says_the_rows_were_checked_together(world, monkeypatch):
    """K1d."""
    from harness import requirements

    s = chat_service.ChatSession()
    monkeypatch.setattr(
        requirements, "active_for", lambda session: ("t", [_bound(), _bound(name="B3")])
    )
    payload = change_set.card_payload(_members(s, [B1_POC, B2_CRIT]))
    assert payload["requirements"]["checked"] == 2
    assert "checked together" in payload["requirements"]["says"]
    monkeypatch.setattr(requirements, "active_for", lambda session: ("t", []))
    payload = change_set.card_payload(_members(s, [B1_POC, B2_CRIT]))
    assert payload["requirements"]["checked"] == 0
