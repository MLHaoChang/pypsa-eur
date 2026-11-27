"""
SC-07 S6b: the integrated native journey, setup → readiness → run → results →
stale, in Guided and in Expert (V61, V63, V64, V66, V67, V69). Tests only.

A scripted model drives each turn over a real provider wire
(`journey_wire.run_journey`; only the provider's HTTP is mocked). Everything
else is native: the D07 oracle project is saved, its Site adopted and its
buildings generated through the routes (adopt stays route-only); the chat
binds the buildings through `site_scene_prepare` and the scene card; the solve
runs through the ordinary run route; `site_scene_results` explains a value,
which the test compares with an independent read of the network.
"""

from __future__ import annotations

import copy
import json
import shutil

import pytest

from harness import compose, spill
from services import chat_tools, llm_config
from services import site_service as ss
from services.pypsa_service import PyPSAService
from services.site_scene import chat, proposals, store
from tests.journey_wire import run_journey
from tests.test_sc07_results_read import _leaves, _target
from tests.test_site_scene_results import (
    A,
    B,
    C,
    HALL,
    NAME,
    OFFICE,
    PLANT,
    SHED,
    SITE,
    T3,
    _apply,
    _bind,
    _propose,
    _solve,
    _url,
    oracle_network,
)
from tests.test_workflow_provider_parity import PROVIDERS

MODES = ("expert", "guided")
# Both wires (native Anthropic, OpenAI-compatible). api.openai.com is left out:
# it is offered a query-bounded subset of the catalogue (`wiring._bounded_tools`),
# which is that selection's own test, not this journey's.
WIRES = [p for p in PROVIDERS if p[0] in ("anthropic", "compatible")]
ASSOCIATIONS = [
    {"feature": A, "target": {"class": "Link", "name": "electrolyser"}},
    {"feature": B, "target": {"class": "Link", "name": "haber"}},
    {"feature": B, "target": {"class": "Store", "name": "nh3_tank"}},
    {"feature": B, "target": {"class": "StorageUnit", "name": "n2_buffer"}},
    {"feature": C, "target": {"class": "Load", "name": "office"}},
]
SETUP = "Map the hall, shed and office to their model parts."
EXPLAIN = "Explain these results for each building at 2030-01-01 03:00."
# The buildings in their words, as Site 3D names them (never an id).
HALL_WORDS, SHED_WORDS, OFFICE_WORDS = (f"Proposed building {i}" for i in (1, 2, 3))
SITE_HINT = {"site_scene": {"site_id": "campus", "part": "generate"}}
# What the binding writes, in the form `_bound` reads it back.
EXPECTED = (
    sorted(
        (a["feature"], a["target"]["class"], a["target"]["name"]) for a in ASSOCIATIONS
    ),
    [(PLANT["id"], tuple(sorted(PLANT["members"])))],
)


@pytest.fixture
def scene(client, install_network, project_storage_dir, session_ctx):
    """The D07 oracle project: saved, its Site adopted and three buildings generated, all through the routes; nothing bound."""
    proposals.clear()
    chat._UNKNOWN.clear()
    install_network(oracle_network(), name=NAME)
    assert (
        client.post(
            f"/api/projects/{NAME}", params={"force": True, "rebind": True}
        ).status_code
        == 200
    )
    path = project_storage_dir(NAME)
    assert (
        client.put(
            f"/api/projects/{NAME}/sites",
            json={"version": 1, "sites": [copy.deepcopy(SITE)]},
        ).status_code
        == 200
    )
    digest = store.legacy_site_digest(ss.read_sites(path), "campus")
    assert (
        client.post(_url("/adopt"), json={"expected_digest": digest}).status_code == 200
    )
    _apply(
        client,
        _propose(client, "generate_native", {"greenfield": [HALL, SHED, OFFICE]}),
    )
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        yield ctx
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)
        chat_tools.set_chat_session(None)
        proposals.clear()
        chat._UNKNOWN.clear()


def _bound(client):
    """The scene's associations as the route serves them, order-free."""
    scene = client.get(_url()).json()
    return (
        sorted((a["feature"], a["class"], a["name"]) for a in scene["associations"]),
        sorted((g["id"], tuple(sorted(g["members"]))) for g in scene.get("groups", [])),
    )


def _decide(decision, cards):
    """The browser's half: keep each card shown while the write is blocked, then decide."""

    def on_frame(event, data):
        if event == "tool_pending_confirmation":
            cards.append(data)
            chat_tools.chat_session().record_decision(
                data["confirmation_token"], decision
            )

    return on_frame


def _prepare(read):
    """The model's bind call, on the revision its read returned."""
    return (
        "site_scene_prepare",
        {
            "site_id": "campus",
            "action": "bind_targets",
            "base_revision": read["revision"],
            "inputs": {"associations": ASSOCIATIONS, "groups": [PLANT]},
        },
    )


def _setup_steps():
    return [
        lambda r: ("get_project_flow", {"name": NAME}),
        # The flow is long: the model pages its first item, as the spill says.
        lambda r: (
            "read_result",
            {"locator": r[0]["spill"]["locator"], "pointer": "/sections/0/items/0"},
        ),
        lambda r: ("site_scene_read", {"site_id": "campus"}),
        # V63: the assistant has no way to tick a section complete.
        lambda r: ("advance_workflow", {"step": "site"}),
        lambda r: _prepare(r[2]),
        lambda r: ("site_scene_apply", dict(r[4]["apply_with"]["args"])),
        lambda r: (None, {}),
    ]


def _setup(kind, model, base_url, auth, mode, decision):
    cards = []
    calls, results, frames, session = run_journey(
        kind,
        model,
        base_url,
        auth,
        _setup_steps(),
        SETUP,
        mode=mode,
        on_frame=_decide(decision, cards),
        answer="Done.",
    )
    return calls, results, frames, session, cards


def _unspilled(session, result):
    """A result as the tool returned it: a spilled one is read back from its spill file."""
    if isinstance(result, dict) and "spill" in result:
        return json.loads(
            spill.path_for(session, result["spill"]["locator"]).read_text()
        )
    return result


def _card_and_receipt(cards, receipt):
    """What both modes must agree on: the card and the receipt, row by row."""
    card = cards[0]["edit_card"]
    return (
        card["kind"],
        card["says"],
        [(r.get("label"), r.get("new")) for r in card["rows"]],
        receipt.get("outcome"),
        [(r.get("label"), r.get("readback")) for r in receipt.get("rows", [])],
    )


# ── setup and readiness (V61, V63, V64) ─────────────────────────────────────


def _snapshot(project):
    """The Site's files as they stand, to start the other mode from the same state."""
    src = ss.site_dir(project, "campus")
    keep = project.parent / f"{project.name}-site-start"
    shutil.copytree(src, keep)

    def restore():
        shutil.rmtree(src)
        shutil.copytree(keep, src)
        proposals.clear()
        chat._UNKNOWN.clear()

    return restore


@pytest.mark.unlocated
@pytest.mark.parametrize("kind,model,base_url,auth", WIRES, ids=[p[0] for p in WIRES])
def test_the_chat_binds_the_site_through_its_card_and_reads_it_back_verified(
    client, scene, project_storage_dir, kind, model, base_url, auth
):
    restore, shapes = _snapshot(project_storage_dir(NAME)), {}
    for mode in MODES:
        restore()
        flow_before = client.get(f"/api/projects/{NAME}/flow").json()
        calls, results, frames, session, cards = _setup(
            kind, model, base_url, auth, mode, "approve"
        )
        assert calls == [
            "get_project_flow",
            "read_result",
            "site_scene_read",
            "advance_workflow",
            "site_scene_prepare",
            "site_scene_apply",
            None,
        ]
        # The first item the model reads is the route's own: the Site has no
        # location, and that blocks Run (TEMPLATE-GAPS-1 Q1 a).
        first = flow_before["sections"][0]["items"][0]
        assert first["code"] == "site_location_missing" and first["blocking"]
        assert flow_before["run_blocker"]["code"] == "site_location_missing"
        assert results[1]["value"] == first
        # V63: advance_workflow is a skill-workflow step, never a section tick.
        assert results[3]["error"].startswith("no_active_workflow"), results[3]
        # V64: one card, before the write; the receipt reads every target back.
        assert len(cards) == 1
        receipt = _unspilled(session, results[5])["_edit_receipt"]
        assert receipt["outcome"] == "verified", receipt
        assert _bound(client) == EXPECTED
        # The only refusal on the way is advance_workflow's own.
        assert [
            (d["tool_name"], d["error_kind"])
            for e, d in frames
            if e in {"error", "tool_error"}
        ] == [("advance_workflow", "no_active_workflow")]
        shapes[mode] = _card_and_receipt(cards, receipt)
    assert shapes["expert"] == shapes["guided"]


# ── a denied card, and the manual route without a provider (V64, V67) ───────


@pytest.mark.parametrize("mode", MODES)
def test_a_denied_card_binds_nothing_and_the_routes_reach_the_same_site(
    client, scene, mode
):
    before = _bound(client)
    calls, results, _, _, cards = _setup(*WIRES[0], mode, "deny")
    assert calls[-2:] == ["site_scene_apply", None] and len(cards) == 1
    assert "_edit_receipt" not in json.dumps(results[5])
    assert _bound(client) == before == ([], [])
    # V67: the same state by the Site 3D routes alone: no chat session, and the
    # scripted provider's profile is gone.
    chat_tools.set_chat_session(None)
    llm_config.profiles_path().unlink()
    assert "journey" not in [p.id for p in llm_config.load_profiles()[0]]
    _bind(client, ASSOCIATIONS, [PLANT])
    assert _bound(client) == EXPECTED


# ── run, results, stale (V61, V69) ──────────────────────────────────────────


def _explain():
    """The model reads the run, then each building's results at the known hour."""
    return [
        lambda r: ("get_simulation_status", {}),
        *(
            (
                lambda r, w=words: (
                    "site_scene_results",
                    {"site_id": "campus", "snapshot": T3, "building": w},
                )
            )
            for words in (HALL_WORDS, SHED_WORDS, OFFICE_WORDS)
        ),
        lambda r: (None, {}),
    ]


def _ports(out, words, name):
    return {p["port"]: p for p in _target(out, words, name)["ports"]}


def _said(frames):
    return "".join(d.get("delta", "") for e, d in frames if e == "token")


def _answer(results):
    """The model's words: the run's own words, then the hall's value with its unit."""
    out = results[1]
    if out["stale"]:
        return out["run_words"]
    rate = _ports(out, HALL_WORDS, "electrolyser")[0]["rate"]
    return f"{out['run_words']} The electrolyser draws {rate['value']} {rate['unit']}."


def _floats(value):
    if isinstance(value, float):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _floats(v)
    elif isinstance(value, list):
        for v in value:
            yield from _floats(v)


@pytest.mark.parametrize("mode", MODES)
def test_the_results_answer_is_the_networks_value_then_says_out_of_date(
    client, scene, mode
):
    _bind(client, ASSOCIATIONS, [PLANT])
    # The solve is the ordinary run route's, never a chat call; the chat reads the run.
    _solve(client)
    calls, results, frames, session = run_journey(
        *WIRES[0], _explain(), EXPLAIN, mode=mode, answer=_answer
    )
    assert calls == ["get_simulation_status", *["site_scene_results"] * 3, None]
    assert results[0]["status"] == "completed" and not results[0]["running"]
    hall, shed, office = results[1:4]
    assert all(
        out["stale"] is False and out["run_words"] == "From the current solve."
        for out in (hall, shed, office)
    )
    assert [f["feature"] for f in hall["features"]] == [HALL_WORDS]
    # V69: each value equals an independent read of the solved network.
    n = scene.network
    rate = _ports(hall, HALL_WORDS, "electrolyser")[0]["rate"]
    assert rate["value"] == pytest.approx(float(n.links_t.p0.at[T3, "electrolyser"]))
    assert rate["unit"] == "MW"
    haber = _ports(shed, SHED_WORDS, "haber")[1]
    assert haber["role"] == "output"
    assert haber["rate"]["value"] == pytest.approx(float(n.links_t.p1.at[T3, "haber"]))
    load = _target(office, OFFICE_WORDS, "office")["ports"][0]
    assert load["rate"]["value"] == pytest.approx(float(n.loads_t.p.at[T3, "office"]))
    assert load["sign"] == "withdrawal_positive"
    assert _said(frames).startswith("From the current solve.")

    # An input edit, approved on its own card: the next answers carry no numbers and say so.
    # The card, not the network route: a route edit reads "Not solved yet" today
    # (.scratch/site-scene-generation/issues/10-put-edit-resets-solve-to-idle.md),
    # which the test below pins without asserting the wrong reason.
    edit = {"component_class": "Load", "name": "office", "attrs": {"p_set": 9.0}}
    cards = []
    calls, results, frames, _ = run_journey(
        *WIRES[0],
        [lambda r: ("update_component", edit), *_explain()[1:]],
        "Raise the office load to 9 MW, then explain these results again.",
        mode=mode,
        answer=_answer,
        on_frame=_decide("approve", cards),
        session=session,
    )
    assert calls == ["update_component", *["site_scene_results"] * 3, None]
    assert len(cards) == 1 and results[0]["_edit_receipt"]["results_stale"] is True
    for out in results[1:4]:
        # P1: the gate's reason on every leaf; no number anywhere in the answer.
        leaves = list(_leaves({"features": out["features"], "groups": out["groups"]}))
        assert leaves and all(
            x["available"] is False and x["value"] is None for x in leaves
        )
        reasons = {x["reason"] for x in leaves} - {"group_has_no_boundary"}
        assert reasons == {"results_not_current"}
        assert not list(_floats({"features": out["features"], "groups": out["groups"]}))
        assert out["run"]["reason"] == "results_not_current" and out["stale"] is True
    assert _said(frames).startswith("Out of date")


@pytest.mark.parametrize("mode", MODES)
def test_an_edit_through_the_network_route_leaves_no_number(client, scene, mode):
    """
    The same input edit through `PUT /api/network/loads/{name}`: what is right is
    asserted here. The reason the gate gives is not (it reads the solve as never
    run): manifest row `LC-D01.site_scene_results_after_route_edit`, ticket
    .scratch/site-scene-generation/issues/10-put-edit-resets-solve-to-idle.md.
    """
    _bind(client, ASSOCIATIONS, [PLANT])
    _solve(client)
    bus = str(scene.network.loads.at["office", "bus"])
    edit = client.put(
        "/api/network/loads/office", json={"name": "office", "bus": bus, "p_set": 9.0}
    )
    assert edit.status_code == 200, edit.text
    calls, results, _, _ = run_journey(
        *WIRES[0], _explain()[1:], EXPLAIN, mode=mode, answer="No numbers."
    )
    assert calls == [*["site_scene_results"] * 3, None]
    for out in results[:3]:
        leaves = list(_leaves({"features": out["features"], "groups": out["groups"]}))
        assert leaves and all(
            x["available"] is False and x["value"] is None for x in leaves
        )
        assert not list(_floats({"features": out["features"], "groups": out["groups"]}))
        assert out["run"]["current"] is False and out["run"]["digest"] is None


# ── session loss, reload and a project switch (V66) ─────────────────────────


@pytest.mark.parametrize("mode", MODES)
def test_a_reload_resumes_the_open_preview_and_reexecutes_nothing(
    client, scene, install_network, mode
):
    # The first session prepares, then is lost before its card.
    _, first, _, _ = run_journey(
        *WIRES[0],
        [
            lambda r: ("site_scene_read", {"site_id": "campus"}),
            lambda r: _prepare(r[0]),
            lambda r: (None, {}),
        ],
        SETUP,
        mode=mode,
        ui_context_extra=SITE_HINT,
    )
    preview = first[1]["apply_with"]["args"]
    revision = client.get(_url()).json()["revision"]
    # A reload: a new session reads the Site, finds the open preview and applies it once.
    cards = []
    calls, results, frames, session = run_journey(
        *WIRES[0],
        [
            lambda r: ("site_scene_read", {"site_id": "campus"}),
            lambda r: ("site_scene_apply", dict(preview)),
            lambda r: (None, {}),
        ],
        "Resume site setup.",
        mode=mode,
        on_frame=_decide("approve", cards),
        ui_context_extra=SITE_HINT,
        session=None,
    )
    # The reload lists the lost session's preview, the one open preview.
    open_previews = [p for p in results[0]["my_previews"] if p["status"] == "open"]
    assert [p["id"] for p in open_previews] == [preview["proposal_id"]]
    # Nothing ran again on reload: the Site stood as the lost session left it,
    # and the one apply moved it by exactly one revision.
    assert results[0]["revision"] == revision
    assert results[0]["counts"]["associations"] == 0
    assert len(cards) == 1
    assert _unspilled(session, results[1])["_edit_receipt"]["outcome"] == "verified"
    assert client.get(_url()).json()["revision"] == revision + 1
    assert _bound(client) == EXPECTED

    # The [site 3d] block is read for the open project; after a switch it shows nothing of this one.
    assert "site 3d: revision: " in compose._format_ui_context(SITE_HINT)
    install_network(oracle_network(), name="d07_other")
    assert (
        client.post(
            "/api/projects/d07_other", params={"force": True, "rebind": True}
        ).status_code
        == 200
    )
    shown = compose._format_ui_context(SITE_HINT)
    assert "site 3d: site: unavailable" in shown and "revision" not in shown
