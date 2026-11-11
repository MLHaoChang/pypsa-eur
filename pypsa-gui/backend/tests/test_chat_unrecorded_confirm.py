"""RP-20 (owner decision RP20-CHAT-1): `confirm_unrecorded_series`, the chat
tool that records series with no provenance record as the network's own.

Carded, the user deciding, never auto-approved; the card lists every series it
stamps with its digest (RP-18 R3, R4); one undo. GK B's plan conditions C1–C10
are named on the tests that hold them.
"""

# ruff: noqa: F811 — the route tests' fixtures (`ctx`, `project`) are imported, then requested by name
from __future__ import annotations

import threading

import pandas as pd
import pytest
from fastapi import HTTPException

from harness import catalogue, change_set, confirm, edit_card
from services import chat_service, chat_tools
from services.profile_gen import unrecorded
from services.profile_gen.apply import current_digest
from services.profile_gen.readiness import profile_kind
from tests.test_chat_tools_dispatch import (
    _hold_foreign_lock,
    foreign_locked_active_project,  # noqa: F401 — a fixture
)
from tests.test_profile_unrecorded import BASE, KEY, SNAPS, ctx, project  # noqa: F401

TOOL = "confirm_unrecorded_series"
NAME = "Legacy Farm"


def _key(name: str) -> tuple[str, str, str]:
    return ("generators", "p_max_pu", name)


def _seed(c, count: int) -> list[str]:
    """`count` more unrecorded series (g_0 … g_{count-1}) beside g_a."""
    names = [f"g_{i}" for i in range(count)]
    for i, nm in enumerate(names):
        c.user_ts[_key(nm)] = pd.Series(0.1 + (i % 50) / 100, index=SNAPS)
        c.user_ts_meta.pop(_key(nm), None)
    return names


def _items(c, names) -> list[dict]:
    return [
        {"component": nm, "digest": current_digest(c.user_ts[_key(nm)])} for nm in names
    ]


@pytest.fixture(autouse=True)
def bound(request):
    """Chat tools run on the context a request would: the fixture's session context."""
    if "ctx" not in request.fixturenames:
        yield
        return
    from services.pypsa_service import PyPSAService

    token = PyPSAService.bind_request_context(request.getfixturevalue("ctx"))
    yield
    PyPSAService.reset_request_context(token)


@pytest.fixture
def session(ctx):
    value = chat_service.ChatSession()
    chat_tools.set_chat_session(value)
    yield value
    chat_tools.set_chat_session(None)


@pytest.fixture
def pushes(monkeypatch):
    """Every undo snapshot pushed, from the turn or from the write."""
    from services import network_undo

    calls = []
    real = network_undo.push_undo_snapshot

    def counted():
        calls.append(1)
        real()

    monkeypatch.setattr(network_undo, "push_undo_snapshot", counted)
    return calls


def dispatch(session, monkeypatch, args, decision="approve", during=None):
    """The ordinary dispatch seam, the user answering the card with `decision`
    (after `during()` runs, for an edit that lands while the card is open)."""
    asked, cards = [], []

    def wait(token):
        asked.append(token)
        if during is not None:
            during()
        return decision

    monkeypatch.setattr(session, "wait_for_decision", wait)
    results = []
    frames = list(
        chat_service._dispatch_real_tool_call(
            session, {"id": f"call-{len(asked)}", "name": TOOL, "input": args}, results
        )
    )
    cards = [p for ev, p in frames if ev == "tool_pending_confirmation"]
    return frames, results, asked, cards


def _error_kind(frames) -> str | None:
    return next((p.get("error_kind") for ev, p in frames if ev == "tool_error"), None)


def _refused(kind, call, *a, **k):
    with pytest.raises(HTTPException) as failure:
        call(*a, **k)
    assert failure.value.detail["error_kind"] == kind, failure.value.detail
    return failure.value.detail


# ── registration ─────────────────────────────────────────────────────────────


def test_registered_as_a_card_only_write_with_the_route_and_a_card():
    assert (
        TOOL in {t["name"] for t in catalogue.TOOLS} and TOOL in chat_tools.DISPATCHERS
    )
    assert catalogue.safety_tier_for(TOOL) == "write"
    assert catalogue.TOOL_ROUTES[TOOL] == [
        ("POST", "/api/projects/{name}/profiles/unrecorded/confirm")
    ]
    assert edit_card.is_edit(TOOL) and TOOL in edit_card.ADAPTERS
    assert TOOL in confirm.CARD_ONLY_TOOLS and TOOL not in confirm.GUIDED_UNCARDED
    # C7: the route pushes the one undo step; a turn snapshot would be a second.
    assert (
        TOOL not in chat_tools.UNDO_CAPTURED_TOOLS and TOOL in edit_card.SELF_UNDO_TOOLS
    )


def test_the_old_goal_phrase_now_reaches_the_tool(session):
    """C10: the not_via_chat entry is gone, and no other entry claims the goal."""
    from services.not_via_chat import NOT_VIA_CHAT

    out = chat_tools.find_capabilities(
        "confirm the unrecorded series are the network's own"
    )
    assert TOOL in [i["name"] for i in out["items"]]
    assert not out.get("not_via_chat"), out.get("not_via_chat")
    assert not [
        e["id"]
        for e in NOT_VIA_CHAT
        if "unrecorded" in e["keywords"]
        or any("unrecorded" in p for _, p in e["routes"])
    ]


# ── the card: every series, its digest; the user decides ─────────────────────


def test_approved_card_lists_every_series_and_stamps_only_the_record(
    session, ctx, monkeypatch, pushes
):
    names = ["g_a", *_seed(ctx, 2)]
    args = {"project_id": NAME, "items": _items(ctx, names)}
    before = {nm: ctx.user_ts[_key(nm)].copy() for nm in names}
    p_before = ctx.network.generators_t.p_max_pu.copy()
    frames, results, asked, cards = dispatch(session, monkeypatch, args)
    assert len(asked) == 1 and not results[-1].get("is_error"), results[-1]
    rows = cards[0]["edit_card"]["rows"]
    assert [(r["name"], r["digest"]) for r in rows] == [
        (i["component"], i["digest"]) for i in args["items"]
    ]
    assert all(r["digest"] in r["old"] for r in rows)
    for nm in names:
        assert profile_kind(ctx, nm) == "network_native"
        # C8: the values are untouched, byte for byte.
        assert ctx.user_ts[_key(nm)].equals(before[nm])
        assert pd.util.hash_pandas_object(ctx.user_ts[_key(nm)]).equals(
            pd.util.hash_pandas_object(before[nm])
        )
    pd.testing.assert_frame_equal(ctx.network.generators_t.p_max_pu, p_before)
    assert len(pushes) == 1  # C7: one snapshot per approval


def test_a_denied_card_writes_nothing(session, ctx, monkeypatch, pushes):
    args = {"project_id": NAME, "items": _items(ctx, ["g_a"])}
    frames, results, asked, _ = dispatch(session, monkeypatch, args, decision="deny")
    assert asked and results[-1].get("is_error")
    assert ctx.user_ts_meta.get(KEY) is None and not pushes


def test_no_cap_on_the_card_or_the_result(session, ctx, monkeypatch):
    """C6: about 250 series, above every list cap on the path; counts are the true total."""
    names = ["g_a", *_seed(ctx, 249)]
    args = {"project_id": NAME, "items": _items(ctx, names)}
    frames, results, asked, cards = dispatch(session, monkeypatch, args)
    card = cards[0]["edit_card"]
    assert len(card["rows"]) == 250 and {r["name"] for r in card["rows"]} == set(names)
    assert "250 series" in card["says"]
    assert not results[-1].get("is_error"), results[-1]
    # The result is kept whole (spilled), never cut: the model pages every row.
    text = results[-1]["content"]
    body = chat_tools.json.loads(text[text.index("{") : text.rindex("}") + 1])
    page = chat_tools.read_result(
        body["spill"]["locator"], pointer="/confirmed", limit=50
    )
    assert page["total"] == 250
    rows = chat_tools.read_result(
        body["spill"]["locator"], pointer="/_edit_receipt/rows", limit=50
    )
    assert rows["total"] == 250
    assert all(profile_kind(ctx, nm) == "network_native" for nm in names)


# ── precheck: refused before any card (C1, C3) ──────────────────────────────


@pytest.mark.parametrize("bad", ["a string", ["a", "list"], 7, [], ""])
def test_a_malformed_record_is_not_confirmable_by_route_or_tool(
    client, session, ctx, monkeypatch, bad
):
    """C1: one reader decides, and a malformed meta is refused, never a 500."""
    ctx.user_ts_meta[KEY] = bad
    digest = current_digest(ctx.user_ts[KEY])
    r = client.post(
        f"{BASE}/profiles/unrecorded/confirm",
        json={"items": [{"component": "g_a", "digest": digest}]},
    )
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "not_unrecorded"
    frames, results, asked, _ = dispatch(
        session,
        monkeypatch,
        {"project_id": NAME, "items": [{"component": "g_a", "digest": digest}]},
    )
    assert not asked and _error_kind(frames) == "not_unrecorded"
    assert ctx.user_ts_meta[KEY] == bad
    assert client.get(f"{BASE}/profiles/readiness").status_code == 200


@pytest.mark.parametrize(
    "case,kind",
    [
        ("duplicate", "unrecorded_duplicate"),
        ("recorded", "not_unrecorded"),
        ("missing", "not_unrecorded"),
        ("stale", "unrecorded_stale"),
    ],
)
def test_a_bad_batch_gets_no_card_and_writes_nothing(
    session, ctx, monkeypatch, pushes, case, kind
):
    [other] = _seed(ctx, 1)
    items = _items(ctx, ["g_a", other])
    if case == "duplicate":
        items.append(dict(items[0]))
    elif case == "recorded":
        ctx.user_ts_meta[_key(other)] = {"provenance": "measured", "source": "upload"}
    elif case == "missing":
        items.append({"component": "g_b", "digest": "0" * 16})
    else:
        items[1]["digest"] = "0" * 16
    frames, results, asked, _ = dispatch(
        session, monkeypatch, {"project_id": NAME, "items": items}
    )
    assert not asked and _error_kind(frames) == kind and results[-1].get("is_error")
    assert ctx.user_ts_meta.get(KEY) is None and not pushes


def test_an_empty_batch_is_refused_at_the_service_and_the_tool(
    session, ctx, monkeypatch
):
    with pytest.raises(unrecorded.UnrecordedRefused) as exc:
        unrecorded.check(ctx, [])
    assert exc.value.kind == "unrecorded_empty"
    frames, results, asked, _ = dispatch(
        session, monkeypatch, {"project_id": NAME, "items": []}
    )
    assert not asked and results[-1].get("is_error")


def test_a_duplicate_is_refused_by_the_service_too(ctx):
    """The route gains this refusal too (named in the PR body, C9)."""
    item = _items(ctx, ["g_a"])[0]
    with pytest.raises(unrecorded.UnrecordedRefused) as exc:
        unrecorded.confirm(ctx, [item, dict(item)])
    assert exc.value.kind == "unrecorded_duplicate" and exc.value.components == ["g_a"]
    assert ctx.user_ts_meta.get(KEY) is None


# ── C3: the approval binds the exact item set ────────────────────────────────


@pytest.mark.parametrize("change", ["added", "removed", "digest"])
def test_the_card_binds_each_component_and_digest(ctx, change):
    [other, third] = _seed(ctx, 2)
    shown = {"project_id": NAME, "items": _items(ctx, ["g_a", other])}
    card = edit_card.preview(TOOL, shown, with_state=False)
    now = {"project_id": NAME, "items": list(shown["items"])}
    if change == "added":
        now["items"] = [*now["items"], *_items(ctx, [third])]
    elif change == "removed":
        now["items"] = now["items"][:1]
    else:
        now["items"] = [now["items"][0], {**now["items"][1], "digest": "0" * 16}]
    with pytest.raises(HTTPException) as exc:
        edit_card.require_current(TOOL, now, card)
    assert exc.value.detail["error_kind"] in ("edit_preview_stale", "unrecorded_stale")
    edit_card.require_current(
        TOOL, shown, card
    )  # the shown set itself is still current
    assert all(ctx.user_ts_meta.get(_key(nm)) is None for nm in ("g_a", other, third))


# ── C2: no check-then-act window ─────────────────────────────────────────────


def _edit_g_a(c):
    return lambda: c.user_ts.__setitem__(KEY, pd.Series(0.2, index=SNAPS))


def test_an_edit_while_the_card_is_open_refuses_the_whole_batch(
    session, ctx, monkeypatch, pushes
):
    names = ["g_a", *_seed(ctx, 2)]
    args = {"project_id": NAME, "items": _items(ctx, names)}
    frames, results, asked, _ = dispatch(
        session, monkeypatch, args, during=_edit_g_a(ctx)
    )
    assert asked and results[-1].get("is_error")
    assert all(ctx.user_ts_meta.get(_key(nm)) is None for nm in names) and not pushes


def test_the_write_refuses_a_stale_series_without_the_card_check(
    session, ctx, monkeypatch, pushes
):
    """Layer two alone: `require_current` disabled, the service still refuses (#1127 M10)."""
    monkeypatch.setattr(edit_card, "require_current", lambda *a, **k: None)
    names = ["g_a", *_seed(ctx, 2)]
    args = {"project_id": NAME, "items": _items(ctx, names)}
    frames, results, asked, _ = dispatch(
        session, monkeypatch, args, during=_edit_g_a(ctx)
    )
    assert asked and _error_kind(frames) == "unrecorded_stale"
    assert all(ctx.user_ts_meta.get(_key(nm)) is None for nm in names) and not pushes


def test_check_snapshot_and_write_hold_the_profile_writers_lock(ctx, monkeypatch):
    """The re-read, the snapshot and the stamp are one critical section: another
    thread cannot take the store lock or the network lock in between."""
    from services import network_undo
    from services.pypsa_service import PyPSAService
    from services.user_timeseries import _user_ts_lock

    held = {}

    def probe():
        net = (
            PyPSAService.get_lock()
        )  # this context's lock; a thread does not inherit the context

        def free(lock):
            got = lock.acquire(timeout=0.05)
            if got:
                lock.release()
            return got

        t = threading.Thread(
            target=lambda: held.update(ts=free(_user_ts_lock), net=free(net))
        )
        t.start()
        t.join()

    monkeypatch.setattr(network_undo, "push_undo_snapshot", probe)
    unrecorded.confirm(ctx, _items(ctx, ["g_a"]))
    assert held == {"ts": False, "net": False}
    assert profile_kind(ctx, "g_a") == "network_native"


# ── C4: the route's gates on the chat path ───────────────────────────────────


def test_a_project_that_is_not_open_is_refused_before_any_card(
    session, ctx, monkeypatch, pushes
):
    monkeypatch.setattr(ctx, "loaded_project", "Some Other Project", raising=False)
    frames, results, asked, _ = dispatch(
        session, monkeypatch, {"project_id": NAME, "items": _items(ctx, ["g_a"])}
    )
    assert not asked and _error_kind(frames) == "project_not_open"
    assert ctx.user_ts_meta.get(KEY) is None and not pushes


def test_another_users_edit_lock_refuses_the_tool(
    foreign_locked_active_project, second_identity
):  # noqa: F811
    name, project_id, c, session_local = foreign_locked_active_project
    c.user_ts[KEY] = pd.Series(0.9, index=SNAPS)
    c.user_ts_meta.pop(KEY, None)
    item = {"component": "g_a", "digest": current_digest(c.user_ts[KEY])}
    _hold_foreign_lock(session_local, project_id, second_identity["user_id"])
    _refused(
        "project_locked", edit_card.preview, TOOL, {"project_id": name, "items": [item]}
    )
    _refused("project_locked", chat_tools.confirm_unrecorded_series, name, [item])
    assert c.user_ts_meta.get(KEY) is None


# ── C5: never admitted without the user's card ───────────────────────────────


@pytest.mark.parametrize("path", ["none", "policy", "auto_approve_write", "envelope"])
def test_no_policy_tier_task_or_grant_stands_in_for_the_card(
    session, ctx, monkeypatch, path
):
    from services import run_envelope

    admitted = []
    if path in ("none", "policy"):
        monkeypatch.setattr(confirm, "confirmation_policy", lambda *a, **k: path)
    elif path == "auto_approve_write":
        monkeypatch.setattr(
            confirm, "AUTO_APPROVE_TIERS", frozenset({"write", "destructive"})
        )
    else:
        # A run envelope that grants the tool: `admit` (and its `_refusal`) is never asked.
        monkeypatch.setattr(
            run_envelope,
            "admit",
            lambda *a, **k: admitted.append(k.get("tool")) or "admitted",
        )
    frames, results, asked, cards = dispatch(
        session,
        monkeypatch,
        {"project_id": NAME, "items": _items(ctx, ["g_a"])},
        decision="deny",
    )
    assert len(asked) == 1 and cards and not admitted
    assert ctx.user_ts_meta.get(KEY) is None


def test_it_never_joins_a_change_set(session):
    tool_use = {"id": "t1", "name": TOOL, "input": {"project_id": NAME, "items": []}}
    assert (
        change_set.exclusion(
            session, tool_use, tier="write", external=False, redirects=None
        )
        == "card_only"
    )


# ── C7: one undo; chat equals route ──────────────────────────────────────────


def test_one_undo_restores_the_empty_record_and_a_second_does_not_restamp(
    client, session, ctx, monkeypatch, session_ctx
):
    frames, results, asked, _ = dispatch(
        session, monkeypatch, {"project_id": NAME, "items": _items(ctx, ["g_a"])}
    )
    text = results[-1]["content"]
    out = chat_tools.json.loads(text[text.index("{") : text.rindex("}") + 1])
    assert out["_edit_receipt"]["outcome"] == "verified"
    assert out["_edit_receipt"]["undo"] == {"tool": "undo_last", "available": True}
    assert client.post("/api/network/undo").status_code == 200
    c = session_ctx(client)
    assert c.user_ts_meta.get(KEY) is None and profile_kind(c, "g_a") == "unrecorded"
    client.post("/api/network/undo")
    assert session_ctx(client).user_ts_meta.get(KEY) is None


def test_chat_and_route_give_the_same_result(client, ctx):
    item = _items(ctx, ["g_a"])[0]
    via_chat = chat_tools.confirm_unrecorded_series(NAME, [item])
    ctx.user_ts_meta.pop(KEY)
    via_route = client.post(
        f"{BASE}/profiles/unrecorded/confirm", json={"items": [item]}
    ).json()
    assert via_chat == via_route == {"confirmed": ["g_a"], "undo": unrecorded.UNDO_NOTE}


def test_a_record_only_write_leaves_the_lp_inputs_alone(session, ctx, monkeypatch):
    """C8: the record is not an LP input: neither the input fingerprint nor the
    network revision moves (as on the route since RP-18)."""
    from services import dirty_state
    from services.workflow_tools import _network_input_fingerprint

    fp, rev = _network_input_fingerprint(ctx), dirty_state.revision(ctx)
    frames, results, asked, _ = dispatch(
        session, monkeypatch, {"project_id": NAME, "items": _items(ctx, ["g_a"])}
    )
    assert not results[-1].get("is_error"), results[-1]
    assert _network_input_fingerprint(ctx) == fp
    assert dirty_state.revision(ctx) == rev


def test_a_refusal_names_every_failing_series(ctx):
    [b, c] = _seed(ctx, 2)
    for nm in (b, c):
        ctx.user_ts_meta[_key(nm)] = {"provenance": "measured"}
    with pytest.raises(unrecorded.UnrecordedRefused) as one:
        unrecorded.check(ctx, _items(ctx, ["g_a", b]))
    assert one.value.components == [b] and f"{b} no longer has a series" in str(
        one.value
    )
    with pytest.raises(unrecorded.UnrecordedRefused) as two:
        unrecorded.check(ctx, _items(ctx, ["g_a", b, c]))
    assert two.value.components == [
        b,
        c,
    ] and f"{b}, {c} no longer have a series" in str(two.value)
    assert two.value.detail()["component"] == b


def test_the_card_binds_the_project(ctx, monkeypatch):
    """A card names the project by its id, so a same-named project is another card."""
    from routers import deps

    card = edit_card.preview(
        TOOL, {"project_id": NAME, "items": _items(ctx, ["g_a"])}, with_state=False
    )
    real = deps.require_project_access

    def renamed(*a, **k):
        proj = real(*a, **k)
        return (
            type(proj)(
                **{**proj.__dict__, "uuid": "00000000-0000-0000-0000-000000000001"}
            )
            if hasattr(proj, "__dict__")
            else proj
        )

    monkeypatch.setattr(deps, "require_project_access", renamed)
    with pytest.raises(HTTPException) as exc:
        edit_card.require_current(
            TOOL, {"project_id": NAME, "items": _items(ctx, ["g_a"])}, card
        )
    assert exc.value.detail["error_kind"] == "edit_preview_stale"
