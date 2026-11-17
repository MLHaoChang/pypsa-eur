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
from services import chat_tools
from services import site_service as ss
from services.pypsa_service import PyPSAService
from services.site_scene import chat, proposals, store
from tests.journey_wire import run_journey
from tests.test_sc07_results_read import _leaves
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
ASK = {"snapshot": T3, "building": "Proposed building 1"}
EXPLAIN = "Explain these results for Proposed building 1 at 2030-01-01 03:00."


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


EXPECTED = (
    sorted(
        (a["feature"], a["target"]["class"], a["target"]["name"]) for a in ASSOCIATIONS
    ),
    [(PLANT["id"], tuple(sorted(PLANT["members"])))],
)


def _decide(decision, cards):
    """The browser's half: read the card while the write is blocked, then decide."""

    def on_frame(event, data):
        if event == "tool_pending_confirmation":
            cards.append(data)
            chat_tools.chat_session().record_decision(
                data["confirmation_token"], decision
            )

    return on_frame


def _setup_steps():
    return [
        lambda r: ("get_project_flow", {"name": NAME}),
        # The flow is long: the model pages its first blocker, as the spill says.
        lambda r: (
            "read_result",
            {"locator": r[0]["spill"]["locator"], "pointer": "/sections/0/items/0"},
        ),
        lambda r: ("site_scene_read", {"site_id": "campus"}),
        # V63: the assistant has no way to tick a section complete.
        lambda r: ("advance_workflow", {"step": "site"}),
        lambda r: (
            "site_scene_prepare",
            {
                "site_id": "campus",
                "action": "bind_targets",
                "base_revision": r[2]["revision"],
                "inputs": {"associations": ASSOCIATIONS, "groups": [PLANT]},
            },
        ),
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


def _whole(session, result):
    """A result as the tool returned it: a spilled one is read back from its spill file."""
    if isinstance(result, dict) and "spill" in result:
        return json.loads(
            spill.path_for(session, result["spill"]["locator"]).read_text()
        )
    return result


def _shape(cards, receipt):
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
        # The first blocker is the flow's own, as the route serves it.
        # The first blocker the model reads is the flow route's own.
        assert results[1]["value"] == flow_before["sections"][0]["items"][0]
        # V63: advance_workflow is a skill-workflow step, never a section tick.
        assert results[3]["error"].startswith("no_active_workflow"), results[3]
        # V64: one card, before the write; the receipt reads every target back.
        assert len(cards) == 1
        receipt = _whole(session, results[5])["_edit_receipt"]
        assert receipt["outcome"] == "verified", receipt
        assert _bound(client) == EXPECTED
        # The only refusal on the way is advance_workflow's own.
        assert [
            (d["tool_name"], d["error_kind"])
            for e, d in frames
            if e in {"error", "tool_error"}
        ] == [("advance_workflow", "no_active_workflow")]
        shapes[mode] = _shape(cards, receipt)
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
    # V67: the same state by the Site 3D routes alone, no provider configured.
    chat_tools.set_chat_session(None)
    _apply(
        client,
        _propose(
            client, "bind_targets", {"associations": ASSOCIATIONS, "groups": [PLANT]}
        ),
    )
    assert _bound(client) == EXPECTED


# ── run, results, stale (V61, V69) ──────────────────────────────────────────


def _explain(results_args):
    return [
        lambda r: ("site_scene_results", {"site_id": "campus", **results_args}),
        lambda r: (None, {}),
    ]


def _ports(out, words, name):
    target = next(
        t
        for f in out["features"]
        if f["feature"] == words
        for t in f["targets"]
        if t["name"] == name
    )
    return {p["port"]: p for p in target["ports"]}


def _said(frames):
    return "".join(d.get("delta", "") for e, d in frames if e == "token")


def _answer(results):
    """The model's words from the tool's: the value with its unit, or the gate's reason."""
    out = results[0]
    if out["stale"]:
        return out["run_words"]
    rate = _ports(out, "Proposed building 1", "electrolyser")[0]["rate"]
    return f"{out['run_words']} The electrolyser draws {rate['value']} {rate['unit']}."


@pytest.mark.parametrize("mode", MODES)
def test_the_results_answer_is_the_networks_value_then_says_out_of_date(
    client, scene, mode
):
    _apply(
        client,
        _propose(
            client, "bind_targets", {"associations": ASSOCIATIONS, "groups": [PLANT]}
        ),
    )
    # The solve is the ordinary run route's, never a chat call.
    _solve(client)
    calls, results, frames, session = run_journey(
        *WIRES[0], _explain(ASK), EXPLAIN, mode=mode, answer=_answer
    )
    assert calls == ["site_scene_results", None]
    out = results[0]
    # V69: the value equals an independent read of the solved network.
    p0 = float(scene.network.links_t.p0.at[T3, "electrolyser"])
    assert out["stale"] is False and out["run_words"] == "From the current solve."
    assert [f["feature"] for f in out["features"]] == ["Proposed building 1"]
    rate = _ports(out, "Proposed building 1", "electrolyser")[0]["rate"]
    assert rate["value"] == pytest.approx(p0) and rate["unit"] == "MW"
    said = _said(frames)
    assert f"draws {rate['value']} MW" in said, said

    # An input edit, approved on its own card: the next answer has no numbers and says so.
    edit = {"component_class": "Load", "name": "office", "attrs": {"p_set": 9.0}}
    cards = []
    calls, results, frames, _ = run_journey(
        *WIRES[0],
        [lambda r: ("update_component", edit), *_explain(ASK)],
        "Raise the office load to 9 MW, then explain these results again.",
        mode=mode,
        answer=lambda r: _answer(r[1:]),
        on_frame=_decide("approve", cards),
        session=session,
    )
    assert calls == ["update_component", "site_scene_results", None] and len(cards) == 1
    assert results[0]["_edit_receipt"]["results_stale"] is True
    results = results[1:]
    out = results[0]
    leaves = list(_leaves({"features": out["features"], "groups": out["groups"]}))
    assert leaves and all(
        x["available"] is False and x["value"] is None for x in leaves
    )
    assert {x["reason"] for x in leaves} <= {
        "results_not_current",
        "group_has_no_boundary",
    }
    assert out["run"]["reason"] == "results_not_current" and out["stale"] is True
    said = _said(frames)
    assert said.startswith("Out of date"), said


# ── session loss, reload and a project switch (V66) ─────────────────────────


def _prepare(read):
    return (
        "site_scene_prepare",
        {
            "site_id": "campus",
            "action": "bind_targets",
            "base_revision": read["revision"],
            "inputs": {"associations": ASSOCIATIONS, "groups": [PLANT]},
        },
    )


@pytest.mark.parametrize("mode", MODES)
def test_a_reload_resumes_the_open_preview_and_reexecutes_nothing(
    client, scene, install_network, mode
):
    hint = {"site_scene": {"site_id": "campus", "part": "generate"}}
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
        ui_context_extra=hint,
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
        ui_context_extra=hint,
        session=None,
    )
    mine = {p["id"]: p for p in results[0]["my_previews"]}
    assert mine[preview["proposal_id"]]["status"] == "open"
    # Nothing ran again on reload: the Site stood as the lost session left it.
    assert results[0]["revision"] == revision
    assert results[0]["counts"]["associations"] == 0
    assert len(cards) == 1
    assert _whole(session, results[1])["_edit_receipt"]["outcome"] == "verified"
    assert _bound(client) == EXPECTED

    # The [site 3d] block is read for the open project; after a switch it shows nothing of this one.
    assert "site 3d: revision: " in compose._format_ui_context(hint)
    install_network(oracle_network(), name="d07_other")
    assert (
        client.post(
            "/api/projects/d07_other", params={"force": True, "rebind": True}
        ).status_code
        == 200
    )
    shown = compose._format_ui_context(hint)
    assert "site 3d: site: unavailable" in shown and "revision" not in shown
