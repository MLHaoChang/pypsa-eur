"""
SC-07 S4: model targets mapped into the 3D site from chat (issue 08; GK B G9
and W2).

site_scene_prepare admits the model actions the Site 3D routes serve
(bind_targets, place_existing, rebind, detach, undo_detach) through the same
proposal service. An ambiguous, unknown or unsupported target returns a
clarify result, never a guess. The scene card maps targets only: a model change
is its own native card. A target that moved after prepare gets no card and its
apply writes nothing. Real project, real network, the ordinary dispatch seam;
no provider calls.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from harness import edit_card
from services import assistant_tasks as tasks, chat_service, chat_tools, site_service as ss
from services.pypsa_service import PyPSAService
from services.site_scene import chat, proposals, store
from tests.test_site_scene_associations import A, B, NAME, _apply, _get, _propose, make_scene

IT = {"feature": A, "target": {"class": "Load", "name": "it"}}
HEAT = {"feature": A, "target": {"class": "Load", "carrier": "heat"}}
PLACE = {"placements": {"StorageUnit:bess": {"x": 60.0, "y": -50.0, "heading": 0}}}


@pytest.fixture
def site(client, install_network, project_storage_dir, project_row, monkeypatch):
    """The `assoc` project (a multi-energy network, two buildings, scene revision 2) in the foreground."""
    proposals.clear()
    chat._UNKNOWN.clear()
    project = make_scene(client, install_network, project_storage_dir)
    ctx = PyPSAService.get_active_context()
    monkeypatch.setattr(ctx, "project_uuid", str(project_row(NAME).id), raising=False)
    monkeypatch.setattr(ctx, "loaded_project", ctx.loaded_project or NAME, raising=False)
    yield project
    proposals.clear()
    chat._UNKNOWN.clear()


def refused(kind, call, *args, **kwargs):
    with pytest.raises(HTTPException) as failure:
        call(*args, **kwargs)
    assert failure.value.detail["error_kind"] == kind, failure.value.detail
    return failure.value.detail


def revision(client):
    return _get(client)["revision"]


def bind(client, *associations, groups=()):
    return chat_tools.site_scene_prepare(
        "campus", "bind_targets", revision(client), {"associations": list(associations), "groups": list(groups)}
    )


def args_of(prep):
    return dict(prep["apply_with"]["args"])


# ── each model action through the route's own service (G6) ──────────────────


def test_a_chat_bind_lands_what_the_route_lands(client, site):
    prep = bind(client, IT, HEAT)
    assert prep["action"] == "bind_targets" and prep["status"] == "ready"
    rows = {(r["name"], r["status"]) for r in prep["summary"]["associations"]}
    assert rows == {("it", "new"), ("heat_demand", "new")}
    assert {r["unit"] for r in prep["summary"]["associations"]} == {"MW"}
    assert store.read_scene(site, "campus")["revision"] == 2  # nothing written at prepare
    out = chat_tools.site_scene_apply(**args_of(prep))
    assert out["receipt"]["revision"] == 3 and out["read_back"]["receipt_found"] is True
    bound = {a["name"]: a for a in _get(client)["associations"]}
    assert {k: a["status"] for k, a in bound.items()} == {"it": "bound", "heat_demand": "bound"}
    bindings = ss.read_sites(site)["bindings"]
    assert {bindings[f"Load:{k}"] for k in bound} == {a["uid"] for a in bound.values()}  # D06 uids


def test_a_chat_preview_is_the_routes_preview(client, site):
    prep = bind(client, IT, HEAT)
    route = _propose(client, "bind_targets", {"associations": [IT, HEAT], "groups": []})
    key, owner = _scope()
    assert proposals.get(key, owner, "campus", prep["proposal_id"])["preview"] == route["preview"]
    assert prep["input_hash"] != route["input_hash"] or prep["proposal_id"] != route["id"]


def test_a_chat_placement_changes_the_scene_only(client, site):
    prep = chat_tools.site_scene_prepare("campus", "place_existing", revision(client), PLACE)
    assert prep["summary"]["placements"] == [{"item": "bess · battery", "status": "new"}] or prep["summary"]["placements"]
    before = client.get("/api/network/storage_units").json()
    chat_tools.site_scene_apply(**args_of(prep))
    assert _get(client)["site"]["placements"]["StorageUnit:bess"]["x"] == 60.0
    assert client.get("/api/network/storage_units").json() == before


def test_detach_and_undo_go_through_chat(client, site):
    chat_tools.site_scene_apply(**args_of(bind(client, IT)))
    keys = [a["id"] for a in _get(client)["associations"] if a["name"] == "it"]
    detach = chat_tools.site_scene_prepare("campus", "detach", revision(client), {"keys": keys})
    done = chat_tools.site_scene_apply(**args_of(detach))
    assert not [a for a in _get(client)["associations"] if a["name"] == "it" and a["status"] == "bound"]
    undo = chat_tools.site_scene_prepare("campus", "undo_detach", revision(client),
                                         {"proposal": done["receipt"]["proposal_id"]})
    chat_tools.site_scene_apply(**args_of(undo))
    assert [a for a in _get(client)["associations"] if a["name"] == "it" and a["status"] == "bound"]


# ── G9: ambiguity returns clarify, never a guess ────────────────────────────


def test_several_loads_of_a_carrier_ask_which_one(client, site):
    out = bind(client, {"feature": A, "target": {"class": "Load", "carrier": "AC"}})
    assert out["status"] == "clarify" and "proposal_id" not in out and "apply_with" not in out
    assert {c["name"] for c in out["candidates"]} == {"L1", "it"}
    assert all(c["kind"] == "load" and c["unit"] == "MW" for c in out["candidates"])
    assert out["field"] == "target" and out["question"]
    assert not proposals.has_ready(*_scope())


@pytest.mark.parametrize("target, code", [
    ({"class": "Load", "name": "nowhere"}, "unknown_target"),
    ({"class": "Link", "name": "cable"}, "unsupported_target"),
])
def test_an_unknown_or_unsupported_target_asks_instead_of_guessing(client, site, target, code):
    out = bind(client, {"feature": A, "target": target})
    assert out["status"] == "clarify" and out["code"] == code and "apply_with" not in out
    assert not proposals.has_ready(*_scope())


def test_a_split_without_shares_is_refused_not_invented(client, site):
    detail = refused("invalid_input", bind, client, IT, {"feature": B, "target": {"class": "Load", "name": "it"},
                                                         "weight": 0.5})
    assert detail.get("code") == "invalid_allocation"


def test_several_loads_on_one_building_with_their_shares(client, site):
    prep = bind(client, IT, {"feature": A, "target": {"class": "Load", "name": "L1"}, "weight": 0.25},
                {"feature": B, "target": {"class": "Load", "name": "L1"}, "weight": 0.75})
    chat_tools.site_scene_apply(**args_of(prep))
    shares = {(a["feature"], a["name"]): a.get("weight") for a in _get(client)["associations"]}
    assert shares[(A, "L1")] == 0.25 and shares[(B, "L1")] == 0.75 and (A, "it") in shares


def _scope():
    from services import project_registry

    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, PyPSAService.get_active_context().project_uuid)
        return project_registry.registry_key(project), str(user.id)


# ── G9: the card maps targets only, and re-checks the model ─────────────────


def test_the_scene_card_names_each_target_and_changes_no_model_value(client, site):
    args = args_of(bind(client, IT, HEAT))
    card = edit_card.preview("site_scene_apply", args)
    text = repr(card)
    assert "it · load" in text and "heat_demand · load" in text and "MW" in text
    assert "no model value changes" in text.lower()
    assert "only the 3d site changes" not in text.lower()


def test_a_target_that_moved_after_prepare_gets_no_card_and_writes_nothing(client, site):
    args = args_of(bind(client, IT))
    r = client.patch("/api/network/loads/it", json={"bus": "heat"})
    assert r.status_code in (200, 204), r.text
    with pytest.raises(HTTPException) as card:
        edit_card.preview("site_scene_apply", args)
    assert "target_changed" in repr(card.value.detail) or card.value.detail.get("error_kind") == "revision_conflict"
    detail = refused("revision_conflict", chat_tools.site_scene_apply, **args)
    assert detail.get("code") == "target_changed"
    assert store.read_scene(site, "campus")["revision"] == 2
    assert "Load:it" not in (ss.read_sites(site).get("bindings") or {})


def test_a_model_edit_and_its_mapping_are_two_cards(client, site, monkeypatch):
    """add_component (its own card), then prepare, then the scene card: a denied scene card keeps the edit only."""
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    try:
        steps = [
            {"id": "add", "tool": "add_component", "args": {
                "component_class": "Load", "name": "cooling", "attrs": {"bus": "heat", "p_set": 1.0}}},
            {"id": "prep", "tool": "site_scene_prepare", "args": {
                "site_id": "campus", "action": "bind_targets", "base_revision": revision(client),
                "inputs": {"associations": [{"feature": A, "target": {"class": "Load", "name": "cooling"}}],
                           "groups": []}}},
            {"id": "apply", "tool": "site_scene_apply",
             "args": {k: f"$prep.apply_with.args.{k}" for k in tasks.SCENE_APPLY_ARGS}},
        ]
        chat_tools.start_task(title="Add and map the cooling load", steps=steps, request_id="two-cards")
        cards = []

        def run(tool, args, decision):
            monkeypatch.setattr(session, "wait_for_decision", lambda token: (cards.append(tool), decision)[1])
            results = []
            list(chat_service._dispatch_real_tool_call(
                session, {"id": f"call-{tool}-{len(cards)}", "name": tool, "input": args}, results))
            return results[-1]

        assert not run("add_component", steps[0]["args"], "approve").get("is_error")
        assert not run("site_scene_prepare", steps[1]["args"], "approve").get("is_error")
        prep = next(s for s in tasks._read(session.task_id)[1]["steps"] if s["id"] == "prep")["result"]
        assert run("site_scene_apply", args_of(prep), "deny")["is_error"]
        assert cards == ["add_component", "site_scene_apply"]  # two cards, the prepare has none
        names = [row["name"] for row in client.get("/api/network/loads").json().get("data", [])] \
            if isinstance(client.get("/api/network/loads").json(), dict) else \
            [row["name"] for row in client.get("/api/network/loads").json()]
        assert "cooling" in names
        assert "Load:cooling" not in (ss.read_sites(site).get("bindings") or {})
    finally:
        chat_tools.set_chat_session(None)


# ── W2: an unknown outcome cancels its preview ──────────────────────────────


def test_an_unknown_outcome_cancels_the_preview_so_eviction_cannot_revive_it(client, site, monkeypatch):
    args = args_of(bind(client, IT))
    real = proposals.apply

    def lost(*_a, **_k):
        raise OSError("connection reset")

    monkeypatch.setattr(proposals, "apply", lost)
    refused("scene_apply_outcome_unknown", chat_tools.site_scene_apply, **args)
    monkeypatch.setattr(proposals, "apply", real)
    key, owner = _scope()
    assert proposals.get(key, owner, "campus", args["proposal_id"])["status"] == "cancelled"
    chat._UNKNOWN.clear()  # the marker evicted
    with pytest.raises(HTTPException):
        edit_card.preview("site_scene_apply", args)
    refused("proposal_cancelled", chat_tools.site_scene_apply, **args)
    assert store.read_scene(site, "campus")["revision"] == 2
