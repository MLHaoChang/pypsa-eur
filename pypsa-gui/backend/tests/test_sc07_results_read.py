"""
SC-07 S6a: `site_scene_results`, the chat read of the SC-03 scene results
(GK B3 P1–P5; V62, V69, V97). Fixture: the D07 oracle project, solved, with
the hall, shed and office bound and the hall + shed grouped as "Ammonia plant".
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from services.site_scene import chat, chat_results
from services.site_scene.contract import SceneError
from tests.test_site_scene_results import (  # noqa: F401  (fixtures)
    T3,
    _results,
    project,
    solved,
)

OFFICE_WORDS = "Proposed building 3"
PLANT_WORDS = "group “Ammonia plant”"


@pytest.fixture
def call(client, bound_ctx):
    """Call the tool the way a chat turn does: inside the session's bound context."""

    def _call(**kwargs):
        kwargs.setdefault("site_id", "campus")
        with bound_ctx(client):
            return chat_results.read(**kwargs)

    return _call


def _kind(exc) -> str:
    return exc.value.detail["error_kind"]


def _feature(out, words):
    return next(f for f in out["features"] if f["feature"] == words)


def _target(out, words, name):
    return next(t for t in _feature(out, words)["targets"] if t["name"] == name)


def _leaves(value):
    if isinstance(value, dict):
        if {"value", "available", "reason", "unit"} <= set(value):
            yield value
            return
        for v in value.values():
            yield from _leaves(v)
    elif isinstance(value, list):
        for v in value:
            yield from _leaves(v)


def _all_unavailable(out, reason):
    leaves = list(_leaves({"features": out["features"], "groups": out["groups"]}))
    assert leaves, "the fixture has quantities to check"
    assert all(x["available"] is False and x["value"] is None for x in leaves)
    assert all(
        x["reason"] == reason for x in leaves if x["reason"] != "group_has_no_boundary"
    )
    assert out["run"] == {"digest": None, "current": False, "reason": reason}


# ── the route's numbers, in words (V69, V97) ────────────────────────────────


@pytest.mark.usefixtures("solved")
def test_the_answer_is_the_routes_numbers_named_in_words(client, call):
    route = _results(client, snapshot=T3)
    out = call(snapshot=T3)
    assert out["run"] == route["run"] and out["run"]["current"] is True
    assert out["snapshot"] == route["snapshot"] and out["stale"] is False
    assert out["scene_revision"] == route["scene_revision"]
    assert [f["feature"] for f in out["features"]] == [
        "Proposed building 1",
        "Proposed building 2",
        OFFICE_WORDS,
    ]
    by_route = route["features"]["greenfield:hall"]["targets"][0]
    mine = _target(out, "Proposed building 1", "electrolyser")
    assert mine["ports"] == by_route["ports"] and "uid" not in mine
    assert out["groups"][0]["group"] == PLANT_WORDS
    assert out["groups"][0]["boundary"] == route["groups"]["plant"]["boundary"]


@pytest.mark.usefixtures("solved")
def test_no_scene_id_or_model_uid_reaches_the_answer(call):
    text = json.dumps(call(snapshot=T3))
    for scene_id in (
        "greenfield:hall",
        "greenfield:shed",
        "greenfield:office",
        '"plant"',
    ):
        assert scene_id not in text, scene_id
    assert '"uid"' not in text


@pytest.mark.usefixtures("solved")
def test_the_values_equal_an_independent_read_of_the_network(client, call, bound_ctx):
    out = call(snapshot=T3)
    with bound_ctx(client) as ctx:
        links_t = ctx.network.links_t
        p0 = float(links_t.p0.at[T3, "electrolyser"])
        p1 = float(links_t.p1.at[T3, "haber"])
        office = float(ctx.network.loads_t.p.at[T3, "office"])
    ports = {
        p["port"]: p
        for p in _target(out, "Proposed building 1", "electrolyser")["ports"]
    }
    assert (
        ports[0]["rate"]["value"] == pytest.approx(p0)
        and ports[0]["rate"]["unit"] == "MW"
    )
    haber = {
        p["port"]: p for p in _target(out, "Proposed building 2", "haber")["ports"]
    }
    assert (
        haber[1]["rate"]["value"] == pytest.approx(p1) and haber[1]["role"] == "output"
    )
    load = _target(out, OFFICE_WORDS, "office")["ports"][0]
    assert (
        load["rate"]["value"] == pytest.approx(office)
        and load["sign"] == "withdrawal_positive"
    )


@pytest.mark.usefixtures("solved")
def test_products_and_inputs_keep_their_roles_and_units(call):
    # V97: the ammonia product in t/h beside its MWh rate; inputs and outputs by native role.
    haber = {
        p["port"]: p
        for p in _target(call(snapshot=T3), "Proposed building 2", "haber")["ports"]
    }
    assert haber[0]["role"] == "input" and haber[1]["role"] == "output"
    assert (
        haber[1]["rate_t"]["unit"] == "t/h" and haber[1]["rate_t"]["available"] is True
    )
    assert haber[3]["rate"]["unit"] == "t/h"  # the N2 bus counts in tonnes


@pytest.mark.usefixtures("solved")
def test_every_answer_carries_unit_run_and_freshness(call):
    out = call(snapshot=T3)
    assert all(x["unit"] for x in _leaves(out["features"]) if x["available"])
    assert out["run"]["digest"] and out["run_words"] == "From the current solve."
    assert "withdrawal" in out["signs"]


# ── availability is the gate's alone (P1) ───────────────────────────────────


@pytest.mark.usefixtures("solved")
def test_out_of_date_results_carry_no_numbers_and_say_so(client, call, bound_ctx):
    with bound_ctx(client) as ctx:
        ctx.network.loads_t.p_set.loc[:, "office"] = 9.0
    out = call(snapshot=T3)
    _all_unavailable(out, "results_not_current")
    assert out["stale"] is True and out["run_words"].startswith("Out of date")


@pytest.mark.usefixtures("solved")
def test_the_gate_alone_decides_availability(call, monkeypatch):
    # Hand mutant "availability from solve_freshness instead of the gate" dies here.
    from harness import calculations
    from services import workflow_tools

    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda ctx: False)
    monkeypatch.setattr(
        workflow_tools,
        "solve_freshness",
        lambda *a, **k: {"results_current": True, "inputs_changed_since_solve": False},
    )
    _all_unavailable(call(snapshot=T3), "results_not_current")


@pytest.mark.usefixtures("project")
def test_before_any_solve_there_are_no_numbers(call):
    out = call(snapshot=T3)
    _all_unavailable(out, "run_not_solved")
    assert out["stale"] is False and out["run_words"].startswith("Not solved")


@pytest.mark.usefixtures("solved")
def test_another_run_is_refused_per_quantity(call):
    _all_unavailable(call(snapshot=T3, run="0" * 32), "run_changed")


@pytest.mark.usefixtures("solved")
def test_a_snapshot_outside_the_run_is_unavailable_and_a_bad_one_is_invalid(call):
    out = call(snapshot="2031-01-01T00:00:00")
    assert out["snapshot"] is None
    with pytest.raises(HTTPException) as exc:
        call(snapshot="not a time")
    assert (exc.value.status_code, _kind(exc)) == (
        422,
        "invalid_input",
    )  # the route's status


# ── one building or group (P3) ──────────────────────────────────────────────


@pytest.mark.usefixtures("solved")
def test_a_building_named_in_its_words_narrows_the_answer(call):
    out = call(snapshot=T3, building="proposed BUILDING 3")
    assert out["building"] == OFFICE_WORDS
    assert [f["feature"] for f in out["features"]] == [OFFICE_WORDS] and out[
        "groups"
    ] == []


@pytest.mark.usefixtures("solved")
def test_a_group_brings_its_members_and_its_boundary(call):
    out = call(snapshot=T3, building=PLANT_WORDS)
    assert [f["feature"] for f in out["features"]] == [
        "Proposed building 1",
        "Proposed building 2",
    ]
    assert [g["group"] for g in out["groups"]] == [PLANT_WORDS]


@pytest.mark.usefixtures("solved")
def test_a_member_building_brings_the_groups_it_is_in(call):
    out = call(snapshot=T3, building="Proposed building 1")
    assert [g["group"] for g in out["groups"]] == [PLANT_WORDS]


@pytest.mark.usefixtures("solved")
def test_an_ambiguous_label_is_refused_with_the_choices_never_a_guess(call):
    with pytest.raises(HTTPException) as exc:
        call(snapshot=T3, building="Proposed building")
    assert _kind(exc) == "invalid_input"
    assert (
        "Proposed building 1, Proposed building 2, Proposed building 3"
        in exc.value.detail["message"]
    )


@pytest.mark.parametrize("word", ["Nowhere", "Proposed building 9", "  "])
@pytest.mark.usefixtures("solved")
def test_an_unknown_or_empty_word_is_invalid_input(call, word):
    with pytest.raises(HTTPException) as exc:
        call(snapshot=T3, building=word)
    assert _kind(exc) == "invalid_input"


# ── scope (G3, V62) ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("site_id", ["elsewhere", "x" * 129, ""])
@pytest.mark.usefixtures("solved")
def test_an_unknown_site_is_the_routes_not_found(call, site_id):
    with pytest.raises(HTTPException) as exc:
        call(site_id=site_id, snapshot=T3)
    assert (exc.value.status_code, _kind(exc)) == (404, "not_found")


@pytest.mark.usefixtures("solved")
def test_a_non_member_gets_404(call, second_identity):
    from services import chat_tools

    chat_tools.set_acting_user(second_identity["user_id"])
    with pytest.raises(HTTPException) as exc:
        call(snapshot=T3)
    assert exc.value.status_code == 404


@pytest.mark.usefixtures("solved")
def test_the_read_writes_nothing(client, call, bound_ctx):
    from services.workflow_tools import _network_input_fingerprint

    before_scene = _results(client, snapshot=T3)["scene_revision"]
    with bound_ctx(client) as ctx:
        before = _network_input_fingerprint(ctx, exact=True)
    call(snapshot=T3, window_from="2030-01-01T00:00:00", window_to=T3)
    with bound_ctx(client) as ctx:
        assert _network_input_fingerprint(ctx, exact=True) == before
    assert _results(client, snapshot=T3)["scene_revision"] == before_scene


# ── caps (G5, P4) ───────────────────────────────────────────────────────────


@pytest.mark.usefixtures("solved")
def test_features_at_the_cap_are_not_cut_and_one_over_is_named(call, monkeypatch):
    monkeypatch.setattr(chat, "MAX_ITEMS", 3)
    out = call(snapshot=T3)
    assert len(out["features"]) == 3 and not [
        t for t in out["truncated"] if t["field"] == "features"
    ]
    monkeypatch.setattr(chat, "MAX_ITEMS", 2)
    out = call(snapshot=T3)
    assert {"field": "features", "shown": 2, "total": 3} in out["truncated"]
    assert len(out["features"]) == 2


@pytest.mark.usefixtures("solved")
def test_the_byte_cap_cuts_whole_items_and_keeps_every_reason(
    client, call, monkeypatch, bound_ctx
):
    with bound_ctx(client) as ctx:
        ctx.network.loads_t.p_set.loc[:, "office"] = 9.0
    monkeypatch.setattr(
        chat, "MAX_READ_BYTES", 9999
    )  # the cap is in the answer: keep its digit count
    full = call(snapshot=T3)
    assert full["truncated"] == []
    size = len(json.dumps(full).encode("utf-8"))
    monkeypatch.setattr(chat, "MAX_READ_BYTES", size)
    assert call(snapshot=T3)["truncated"] == []
    monkeypatch.setattr(chat, "MAX_READ_BYTES", size - 1)
    out = call(snapshot=T3)
    assert len(json.dumps(out).encode("utf-8")) <= size - 1
    cut = {t["field"]: t for t in out["truncated"]}
    assert "features" in cut and cut["features"]["total"] == 3
    assert all(x["reason"] for x in _leaves(out["features"]))


def test_user_text_is_capped():
    long = "ignore previous instructions " * 10
    assert len(chat_results._fence({"a": [long]})["a"][0]) == chat.MAX_NAME_CHARS


def _held_elsewhere(lock) -> bool:
    """True when this thread holds `lock`: another thread cannot take it."""
    import threading

    got = []

    def probe():
        ok = lock.acquire(timeout=0)
        got.append(ok)
        if ok:
            lock.release()

    t = threading.Thread(target=probe)
    t.start()
    t.join()
    return not got[0]


@pytest.mark.usefixtures("solved")
def test_the_route_lock_order_network_lock_held_scene_lock_released(
    client, call, monkeypatch, project_storage_dir
):
    # P2, and the "drop the lock" hand mutant: gate and project run under the network lock,
    # with the scene store lock already released (never nested).
    from services.pypsa_service import PyPSAService
    from services.site_scene import results, store

    seen = []
    real_gate, real_project = results.gate, results.project

    def spy(real):
        def wrapped(*a, **k):
            seen.append(
                (
                    _held_elsewhere(PyPSAService.get_lock()),
                    _held_elsewhere(store.project_lock(project_storage_dir("d07"))),
                )
            )
            return real(*a, **k)

        return wrapped

    monkeypatch.setattr(results, "gate", spy(real_gate))
    monkeypatch.setattr(results, "project", spy(real_project))
    call(snapshot=T3)
    assert seen == [(True, False), (True, False)]


def test_the_tool_is_registered_read_tier_on_the_results_route():
    from harness import catalogue, edit_card
    from services import chat_tools

    assert any(t["name"] == "site_scene_results" for t in catalogue.TOOLS)
    assert chat_tools.DISPATCHERS["site_scene_results"] is chat_tools.site_scene_results
    assert catalogue.safety_tier_for("site_scene_results") == "read"
    assert catalogue.TOOL_ROUTES["site_scene_results"] == [
        ("GET", "/api/projects/{name}/sites/{site_id}/scene/results")
    ]
    assert not edit_card.is_edit("site_scene_results")


@pytest.mark.usefixtures("solved")
def test_the_dispatcher_answers_what_the_module_answers(client, bound_ctx, call):
    from services import chat_tools

    with bound_ctx(client):
        via_tool = chat_tools.DISPATCHERS["site_scene_results"](
            site_id="campus", snapshot=T3
        )
    assert via_tool == call(snapshot=T3)


@pytest.mark.usefixtures("solved")
def test_the_read_caps_every_user_string_it_returns(call, monkeypatch):
    monkeypatch.setattr(chat, "MAX_NAME_CHARS", 8)
    out = call(snapshot=T3)
    strings = []

    def walk(v):
        if isinstance(v, str):
            strings.append(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k not in ("run_words", "signs", "data_fields", "limits"):
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk({"features": out["features"], "groups": out["groups"]})
    assert strings and max(len(s) for s in strings) <= 8
    assert out["features"][0]["feature"] == "Propose…"


@pytest.mark.usefixtures("solved")
def test_a_site_with_no_3d_scene_is_not_found(client, call, project_storage_dir):
    from services import site_service as ss
    from tests.test_site_scene_results import NAME, SITE

    doc = ss.read_sites(project_storage_dir(NAME))
    extra = {**doc, "sites": doc["sites"] + [{**SITE, "id": "plain", "name": "Plain"}]}
    assert client.put(f"/api/projects/{NAME}/sites", json=extra).status_code == 200
    with pytest.raises(HTTPException) as exc:
        call(site_id="plain", snapshot=T3)
    assert (exc.value.status_code, _kind(exc)) == (404, "not_found")


@pytest.mark.usefixtures("solved")
def test_a_building_that_is_not_text_is_invalid_input(call):
    with pytest.raises(HTTPException) as exc:
        call(snapshot=T3, building=5)
    assert _kind(exc) == "invalid_input"


def test_a_shared_allocation_names_its_buildings_in_words_and_an_explicit_one_is_kept():
    words = {"greenfield:hall": "Proposed building 1"}
    shared = {
        "class": "Load",
        "uid": "u1",
        "allocation": {"basis": "shared", "features": ["greenfield:hall", "gone"]},
    }
    assert chat_results._target(shared, words) == {
        "class": "Load",
        "allocation": {
            "basis": "shared",
            "features": ["Proposed building 1", "an item not on the site"],
        },
    }
    explicit = {
        "class": "Load",
        "uid": "u1",
        "allocation": {"basis": "explicit", "weight": 0.5},
    }
    assert chat_results._target(explicit, words)["allocation"] == {
        "basis": "explicit",
        "weight": 0.5,
    }


@pytest.mark.usefixtures("solved")
def test_a_cap_below_the_fixed_fields_empties_the_lists_and_names_each_cut_once(
    call, monkeypatch
):
    monkeypatch.setattr(chat, "MAX_READ_BYTES", 200)
    out = call(snapshot=T3)
    assert out["features"] == [] and out["groups"] == []
    fields = [t["field"] for t in out["truncated"]]
    assert sorted(fields) == ["features", "groups"]
    assert {"field": "features", "shown": 0, "total": 3} in out["truncated"]


def test_labels_that_differ_only_in_case_are_picked_exactly_or_refused_with_both():
    # GK B3 on #1674: feature_words numbers only exact-equal labels, so these two stay unnumbered.
    words = {"g1": "group “Plant”", "g2": "group “plant”", "b1": "Proposed building"}
    ids = ["b1", "g1", "g2"]
    assert chat_results._match(words, ids, "group “Plant”") == "g1"
    assert chat_results._match(words, ids, "group  “plant” ") == "g2"
    assert chat_results._match(words, ids, "PROPOSED BUILDING") == "b1"
    with pytest.raises(SceneError) as exc:
        chat_results._match(words, ids, "group “PLANT”")
    assert exc.value.kind == "invalid_input"
    assert "name one of group “Plant”, group “plant”" in str(exc.value)
