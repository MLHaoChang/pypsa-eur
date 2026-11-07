"""
SC-07 S3: a 3D site apply as a task step, and its recovery (issue 08; GK B
G7, G8 and the S3 plan changes N1-N8).

The plan takes a site_scene_apply step only with whole-value refs to one
earlier site_scene_prepare step (N2). The card is the user's on every path (N1).
A failed or uncertain apply reconciles from the scene chain, keyed on the
prepared input_hash (N4), and a dead preview is never retried (N3). A chat
apply whose outcome is unknown is never approved again (N6). Real project,
real scene store, the ordinary dispatch seam; no provider calls.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from harness import confirm, edit_card
from services import assistant_tasks as tasks, chat_service, chat_tools
from services.pypsa_service import PyPSAService
from services.site_scene import chat, proposals, store
from tests.test_sc07_scene_tools import BOX, SITE, _adopt, _save

INPUTS = {"greenfield": [BOX]}
PREPARE = {
    "site_id": "campus",
    "action": "generate_native",
    "base_revision": 1,
    "inputs": INPUTS,
}
REFS = {k: f"$prep.apply_with.args.{k}" for k in tasks.SCENE_APPLY_ARGS}


@pytest.fixture
def geo(client, install_network, project_storage_dir, project_row, monkeypatch):
    """The saved project `geo` in the foreground with the site `campus` adopted (revision 1)."""
    proposals.clear()
    chat._UNKNOWN.clear()
    _save(client, install_network, "geo")
    _adopt(client, project_storage_dir("geo"), "geo", [SITE])
    ctx = PyPSAService.get_active_context()
    monkeypatch.setattr(ctx, "project_uuid", str(project_row("geo").id), raising=False)
    monkeypatch.setattr(
        ctx, "loaded_project", ctx.loaded_project or "geo", raising=False
    )
    yield project_storage_dir("geo")
    proposals.clear()
    chat._UNKNOWN.clear()


@pytest.fixture
def session(geo):
    value = chat_service.ChatSession()
    chat_tools.set_chat_session(value)
    yield value
    chat_tools.set_chat_session(None)


@pytest.fixture
def spy(monkeypatch):
    """Every call that reaches proposals.apply, the write the route and chat share."""
    calls = []
    real = proposals.apply

    def counted(*a, **k):
        calls.append(a[4])
        return real(*a, **k)

    monkeypatch.setattr(proposals, "apply", counted)
    return calls


def refused(kind, call, *args, **kwargs):
    with pytest.raises(HTTPException) as failure:
        call(*args, **kwargs)
    assert failure.value.detail["error_kind"] == kind, failure.value.detail
    return failure.value.detail


def plan(request_id="scene", apply_args=None, extra=(), brief=None):
    steps = [
        {"id": "prep", "tool": "site_scene_prepare", "args": PREPARE},
        *extra,
        {"id": "apply", "tool": "site_scene_apply", "args": apply_args or REFS},
    ]
    return chat_tools.start_task(
        title="Generate the campus buildings",
        steps=steps,
        request_id=request_id,
        **({"brief": brief} if brief else {}),
    )


def dispatch(session, monkeypatch, tool, args, decision):
    """The ordinary dispatch seam, the user answering the card with `decision`."""
    asked = []

    def wait(token):
        asked.append(token)
        return decision

    monkeypatch.setattr(session, "wait_for_decision", wait)
    results = []
    frames = list(
        chat_service._dispatch_real_tool_call(
            session,
            {"id": f"call-{tool}-{len(asked)}", "name": tool, "input": args},
            results,
        )
    )
    return frames, results, asked


def prepared(session, monkeypatch):
    frames, results, asked = dispatch(
        session, monkeypatch, "site_scene_prepare", PREPARE, "approve"
    )
    assert not asked and not results[-1].get("is_error"), results[-1]
    task = tasks._read(session.task_id)[1]
    prep = next(s for s in task["steps"] if s["id"] == "prep")
    assert prep["status"] == "completed"
    return dict(prep["result"]["apply_with"]["args"])


def step(session, name="apply"):
    return next(s for s in tasks._read(session.task_id)[1]["steps"] if s["id"] == name)


# ── N2: the refs come from one prepare step ─────────────────────────────────


def test_a_plan_with_refs_to_one_prepare_step_is_accepted(session):
    task = plan()
    assert [s["tool"] for s in task["steps"]] == [
        "site_scene_prepare",
        "site_scene_apply",
    ]


@pytest.mark.parametrize("key", tasks.SCENE_APPLY_ARGS)
def test_a_literal_apply_arg_is_refused_at_start(session, key):
    literal = {
        "site_id": "campus",
        "proposal_id": "p" * 32,
        "input_hash": "0" * 64,
        "expected_revision": 1,
    }
    refused("invalid_task_reference", plan, apply_args={**REFS, key: literal[key]})


def test_refs_split_across_two_prepare_steps_are_refused(session):
    other = {"id": "prep2", "tool": "site_scene_prepare", "args": PREPARE}
    split = {**REFS, "input_hash": "$prep2.apply_with.args.input_hash"}
    refused("invalid_task_reference", plan, apply_args=split, extra=(other,))


@pytest.mark.parametrize(
    "ref",
    [
        "$prep.input_hash",
        "$prep.apply_with.args.proposal_id",
        "$read.apply_with.args.input_hash",
    ],
)
def test_a_ref_to_another_field_or_tool_is_refused(session, ref):
    read = {"id": "read", "tool": "site_scene_read", "args": {"site_id": "campus"}}
    refused(
        "invalid_task_reference",
        plan,
        apply_args={**REFS, "input_hash": ref},
        extra=(read,),
    )


def test_a_profile_never_lists_the_apply(monkeypatch):
    from services import run_profiles

    profile = json.loads(json.dumps(run_profiles.EH_SITE))
    profile["stages"]["setup"].setdefault("actions", []).append(
        {"tool": "site_scene_apply", "args": {}}
    )
    with pytest.raises(run_profiles.ProfileError, match="prepare step"):
        run_profiles.load(profile)


# ── N1: the card is never skipped ───────────────────────────────────────────


def test_a_plain_task_apply_with_the_card_denied_never_applies(
    geo, session, monkeypatch, spy
):
    plan()
    args = prepared(session, monkeypatch)
    _, results, asked = dispatch(session, monkeypatch, "site_scene_apply", args, "deny")
    assert asked and results[-1]["is_error"] and spy == []
    assert store.read_scene(geo, "campus")["revision"] == 1
    assert step(session)["status"] == "pending"


def test_an_auto_approve_policy_still_shows_the_apply_card(session, monkeypatch, spy):
    monkeypatch.setattr(
        confirm, "AUTO_APPROVE_TIERS", frozenset({"write", "destructive", "execution"})
    )
    plan()
    args = prepared(session, monkeypatch)
    _, results, asked = dispatch(session, monkeypatch, "site_scene_apply", args, "deny")
    assert asked and results[-1]["is_error"] and spy == []


def test_under_a_brief_continue_at_the_boundary_is_no_card(
    geo, session, monkeypatch, spy
):
    brief = {
        "objective": "Generate the campus buildings for the 2035 study.",
        "cards": ["capacity", "grid"],
        "cases": [{"name": "Base"}],
        "phases": [
            {"card": "capacity", "steps": ["prep"]},
            {"card": "grid", "steps": ["apply"]},
        ],
    }
    task = plan(request_id="scene-brief", brief=brief)
    args = prepared(session, monkeypatch)
    assert tasks._read(task["task_id"])[1]["status"] == "paused_at_card"
    tasks.continue_at_card(
        session, task["task_id"]
    )  # the user's Continue on the plan card
    _, results, asked = dispatch(session, monkeypatch, "site_scene_apply", args, "deny")
    assert asked and results[-1]["is_error"] and spy == []
    _, results, asked = dispatch(
        session, monkeypatch, "site_scene_apply", args, "approve"
    )
    assert asked and not results[-1].get("is_error") and len(spy) == 1
    assert step(session)["status"] == "completed"
    assert store.read_scene(geo, "campus")["revision"] == 2


def test_an_approved_card_applies_the_prepared_preview_once(session, monkeypatch, spy):
    plan()
    args = prepared(session, monkeypatch)
    _, results, asked = dispatch(
        session, monkeypatch, "site_scene_apply", args, "approve"
    )
    assert asked and not results[-1].get("is_error"), results[-1]
    assert spy == [args["proposal_id"]] and step(session)["status"] == "completed"


# ── N3 and N4: reconcile from the chain; a dead preview is never retried ────


def failed_apply(session, monkeypatch, kind):
    """The apply step, run and refused with `kind` after its card (the loop's finish)."""
    plan(request_id=f"fail-{kind}")
    args = prepared(session, monkeypatch)
    ticket = tasks.begin_step(session, "site_scene_apply", args)
    tasks.finish_step(ticket, error="HTTPException", error_kind=kind)
    assert step(session)["status"] == "uncertain"
    return session.task_id, args


@pytest.mark.parametrize("kind", sorted(tasks.SCENE_APPLY_DEAD))
def test_a_dead_preview_is_never_retried(session, monkeypatch, kind):
    task_id, _ = failed_apply(session, monkeypatch, kind)
    detail = refused(
        "task_retry_needs_prepare", chat_tools.resolve_task_step, task_id, "retry"
    )
    assert (
        "cancel_task" in detail["message"] and "site_scene_prepare" in detail["message"]
    )
    assert step(session)["status"] == "uncertain"


def test_another_failure_may_still_be_retried(session, monkeypatch):
    task_id, _ = failed_apply(session, monkeypatch, "project_locked")
    assert (
        chat_tools.resolve_task_step(task_id, "retry")["next_step"]["status"]
        == "pending"
    )


def test_the_loop_records_the_refusal_kind_on_the_step(session, monkeypatch):
    plan()
    args = prepared(session, monkeypatch)
    from services.site_scene.contract import SceneError

    def discarded(*_a, **_k):  # discarded in Site 3D between the card and the commit
        raise SceneError(
            "proposal_cancelled", "This preview was discarded: generate again"
        )

    monkeypatch.setattr(proposals, "apply", discarded)
    _, results, _ = dispatch(session, monkeypatch, "site_scene_apply", args, "approve")
    assert results[-1]["is_error"]
    assert step(session)["error_kind"] == "proposal_cancelled"
    refused(
        "task_retry_needs_prepare",
        chat_tools.resolve_task_step,
        session.task_id,
        "retry",
    )


def _owned(args):
    from services import project_registry

    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(
            db, user, PyPSAService.get_active_context().project_uuid
        )
        return (
            project_registry.registry_key(project),
            str(user.id),
            args["site_id"],
            args["proposal_id"],
        )


def test_a_committed_receipt_with_the_prepared_hash_completes_the_step(
    session, monkeypatch
):
    task_id, args = failed_apply(session, monkeypatch, "tool_error")
    chat_tools.site_scene_apply(
        **args
    )  # the commit did land (the receipt is in the chain)
    view = chat_tools.resolve_task_step(task_id, "completed")
    done = step(session)
    assert done["status"] == "completed" and view["status"] == "completed"
    assert done["result"]["receipt"]["input_hash"] == args["input_hash"]
    assert done["result"]["receipt"]["revision"] == 2


def test_a_receipt_with_another_hash_is_unverifiable(session, monkeypatch):
    task_id, args = failed_apply(session, monkeypatch, "tool_error")
    chat_tools.site_scene_apply(**args)
    real = proposals._committed
    monkeypatch.setattr(
        proposals, "_committed", lambda *a: {**real(*a), "input_hash": "f" * 64}
    )
    refused(
        "task_resolution_unverifiable",
        chat_tools.resolve_task_step,
        task_id,
        "completed",
    )


def test_no_receipt_is_unverifiable_and_the_edit_reread_never_stands_in(
    session, monkeypatch
):
    task_id, _ = failed_apply(session, monkeypatch, "tool_error")
    monkeypatch.setattr(
        edit_card, "reread", lambda *a, **k: {"outcome": "verified", "rows": []}
    )
    data = tasks._read(task_id)
    data[1]["steps"][1]["receipt"] = {"rows": []}
    tasks._save(*data)
    refused(
        "task_resolution_unverifiable",
        chat_tools.resolve_task_step,
        task_id,
        "completed",
    )


def test_a_raising_chain_read_is_unverifiable(session, monkeypatch):
    task_id, args = failed_apply(session, monkeypatch, "tool_error")
    chat_tools.site_scene_apply(**args)

    def boom(*_a):
        raise TimeoutError("scene lock")

    monkeypatch.setattr(proposals, "_committed", boom)
    refused(
        "task_resolution_unverifiable",
        chat_tools.resolve_task_step,
        task_id,
        "completed",
    )


# ── N6: a plain-chat apply whose outcome is unknown ─────────────────────────


def _fault(monkeypatch, *, lands: bool):
    """proposals.apply raising a non-typed error, after (lands) or before the commit."""
    real = proposals.apply
    calls = []

    def flaky(*a, **k):
        calls.append(a[4])
        if lands:
            real(*a, **k)
        raise OSError("connection reset")

    monkeypatch.setattr(proposals, "apply", flaky)
    return calls


def test_an_unknown_outcome_with_no_receipt_is_never_approved_again(geo, monkeypatch):
    args = dict(chat_tools.site_scene_prepare(**PREPARE)["apply_with"]["args"])
    calls = _fault(monkeypatch, lands=False)
    detail = refused("scene_apply_outcome_unknown", chat_tools.site_scene_apply, **args)
    assert detail["read_back"] == {"receipt_found": False, "scene_revision": 1}
    # The entry reads "ready" in the map again, yet no card and no second apply.
    assert proposals.get(*_owned(args))["status"] == "ready"
    with pytest.raises(HTTPException) as card:
        edit_card.preview("site_scene_apply", args)
    assert "scene_apply_outcome_unknown" in json.dumps(card.value.detail)
    refused("scene_apply_outcome_unknown", chat_tools.site_scene_apply, **args)
    assert calls == [args["proposal_id"]]
    assert (
        chat_tools.site_scene_read(site_id="campus", proposal_id=args["proposal_id"])[
            "proposal"
        ]["status"]
        == "outcome_unknown"
    )


def test_an_unknown_outcome_that_landed_reads_back_its_receipt(geo, monkeypatch):
    args = dict(chat_tools.site_scene_prepare(**PREPARE)["apply_with"]["args"])
    calls = _fault(monkeypatch, lands=True)
    out = chat_tools.site_scene_apply(**args)
    assert (
        out["receipt"]["input_hash"] == args["input_hash"]
        and out["read_back"]["receipt_found"] is True
    )
    again = chat_tools.site_scene_apply(**args)
    assert again["receipt"]["revision"] == 2 and calls == [args["proposal_id"]]
    assert store.read_scene(geo, "campus")["revision"] == 2


def test_a_failing_receipt_read_is_the_same_unknown(geo, monkeypatch):
    args = dict(chat_tools.site_scene_prepare(**PREPARE)["apply_with"]["args"])
    _fault(monkeypatch, lands=False)

    def boom(*_a):
        raise TimeoutError("scene lock")

    monkeypatch.setattr(proposals, "_committed", boom)
    detail = refused("scene_apply_outcome_unknown", chat_tools.site_scene_apply, **args)
    assert detail["read_back"] == {"receipt_found": False, "scene_revision": None}


def test_a_typed_refusal_stays_typed_and_is_not_marked_unknown(geo, monkeypatch):
    args = dict(chat_tools.site_scene_prepare(**PREPARE)["apply_with"]["args"])
    from services.site_scene.contract import SceneError

    def refuse(*_a, **_k):
        raise SceneError("revision_conflict", "moved")

    monkeypatch.setattr(proposals, "apply", refuse)
    refused("revision_conflict", chat_tools.site_scene_apply, **args)
    assert chat._UNKNOWN == {}


def test_the_route_keeps_s1s_behaviour(geo, client):
    """N6 is chat-only: the route still applies a preview chat marked unknown."""
    args = dict(chat_tools.site_scene_prepare(**PREPARE)["apply_with"]["args"])
    chat._mark_unknown(_owned(args))
    r = client.post(
        f"/api/projects/geo/sites/campus/scene/proposals/{args['proposal_id']}/apply",
        json={"expected_revision": 1, "input_hash": args["input_hash"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 2
    # ... and chat now reads that receipt back instead of refusing.
    assert chat_tools.site_scene_apply(**args)["receipt"]["revision"] == 2


# ── N5: a fault between the bindings and the commit ─────────────────────────


def test_a_fault_after_the_bindings_retries_to_one_set_of_bindings(
    client, install_network, project_storage_dir, monkeypatch
):
    from services import site_service as ss
    from tests.test_site_scene_associations import _apply, _get, _propose, make_scene

    scene = make_scene(client, install_network, project_storage_dir)
    r = client.post(
        "/api/network/generators",
        json={"name": "pv_new", "bus": "B1", "carrier": "solar"},
    )
    assert r.status_code in (200, 201), r.text
    p = _propose(
        client,
        "place_existing",
        {"placements": {"Generator:pv_new": {"x": 60.0, "y": -50.0, "heading": 0}}},
    )
    before = dict(ss.read_sites(scene).get("bindings") or {})
    real = store.apply

    def boom(*a, **k):
        raise OSError("disk full")  # after _record_bindings, before store.apply writes

    monkeypatch.setattr(store, "apply", boom)
    with pytest.raises(OSError):
        _apply(client, p, ok=False)
    monkeypatch.setattr(store, "apply", real)
    after_fault = dict(ss.read_sites(scene)["bindings"])
    assert set(after_fault) - set(before) == {"Generator:pv_new"}
    assert (
        proposals._committed(scene, p["site_id"], p["id"]) is None
    )  # no receipt: reconcile reads unverifiable
    _apply(client, p)  # the retry runs _model_step again
    assert (
        ss.read_sites(scene)["bindings"] == after_fault
    )  # exactly one set: the same instance reused
    assert list(_get(client)["site"]["placements"]).count("Generator:pv_new") == 1
