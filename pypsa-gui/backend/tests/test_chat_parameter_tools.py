# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""expert-catalogue 05 (slice 01b): the chat side of the parameter catalogue.

`find_parameter` (the chat counterpart of `GET /api/catalogue`: the palette's
ranking, the open project's assets capped at 10, read under the project ACL so a
viewer reads and a non-member is refused), `open_parameter` (the `parameter`
navigate kind; a former id opens the current entry; an unknown id is the typed
`unknown_parameter`), and plain names in the generic write tools' results and
refusals (RAW-NAMES-1)."""

from __future__ import annotations

import dataclasses

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from harness import catalogue
from services import chat_plain_names, chat_tools
from services.asset_schema import catalogue as C
from services.asset_schema import lookup
from services.chat_dispatchers import expert_catalogue as X
from tests import test_viewer_role_gate as gate

baseline = gate.baseline
helpers_state = gate.helpers_state
world = gate.world
as_user = gate.as_user


# ── schema, tier, route ──────────────────────────────────────────────────────


def test_both_tools_are_reads_and_find_parameter_covers_the_catalogue_route():
    assert catalogue.safety_tier_for("find_parameter") == "read"
    assert catalogue.safety_tier_for("open_parameter") == "read"
    assert catalogue.TOOL_ROUTES["find_parameter"] == [("GET", "/api/catalogue")]
    assert catalogue.TOOL_ROUTES["open_parameter"] == ["_ui_event_"]
    assert not catalogue.tool_edits_network("find_parameter")
    assert "find_parameter" not in chat_tools.UNDO_CAPTURED_TOOLS


# ── the lookups ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query, first",
    [
        ("Capacity", "generator.capacity"),  # exact plain name
        ("p_nom", "generator.capacity"),  # the model name
        ("p nom", "generator.capacity"),  # the model name, spaced
        ("tap", "transformer.tap_ratio"),  # a word prefix
        ("running cost", "generator.running_cost"),
    ],
)
def test_search_ranks_as_the_palette_does(query, first):
    assert lookup.search(query)[0].id == first


def test_search_narrows_to_an_asset_type_and_a_function():
    hits = lookup.search("capacity", asset_type_id="bess")
    assert hits and {e.scope for e in hits} <= {"Store", "StorageUnit"}
    assert all("load_flow" in e.functions for e in lookup.search("rating", function="load_flow"))
    assert lookup.search("   ") == []
    assert len(lookup.search("a")) <= 8


def test_asset_type_of_follows_the_type_table():
    carriers = {"B1": "AC", "H": "H2"}.get
    assert lookup.asset_type_of("Generator", {"carrier": "rooftop pv"}, carriers).id == "pvRoof"
    assert lookup.asset_type_of("Store", {"carrier": "battery"}, carriers).id == "bess"
    assert lookup.asset_type_of("Link", {"carrier": "h2", "bus0": "B1", "bus1": "H"}, carriers) is not None


# ── find_parameter ───────────────────────────────────────────────────────────


def test_find_parameter_answers_the_entry_and_the_open_projects_assets(baseline):
    out = X.find_parameter("capacity")
    assert out["project_open"] is True and len(out["entries"]) <= X.MAX_ENTRIES
    gen = next(e for e in out["entries"] if e["entry_id"] == "generator.capacity")
    assert gen["plain_name"] == "Capacity" and gen["model_name"] == "p_nom"
    assert gen["component_class"] == "Generator" and gen["unit"]
    assert gen["short_help"] and gen["group"] and isinstance(gen["guide"], str)
    assert gen["assets"] == [{"name": "G1", "asset_type": gen["assets"][0]["asset_type"], "value": 20.0, "assumed": False}]
    assert gen["assets_total"] == 1 and gen["assets_truncated"] is False


def test_a_value_left_at_its_default_is_assumed(baseline):
    eff = next(e for e in X.find_parameter("efficiency", asset_type=None)["entries"] if e["entry_id"].startswith("generator."))
    assert eff["assets"] and eff["assets"][0]["assumed"] is True


def test_assets_are_capped_at_ten_with_the_total(client, session_ctx, install_network):
    from services.pypsa_service import PyPSAService

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=1, freq="h"))
    n.add("Bus", "B1", v_nom=11)
    for i in range(13):
        n.add("Generator", f"G{i:02d}", bus="B1", p_nom=i + 1)
    install_network(n)
    assert client.post("/api/projects/Cap Test").status_code == 200
    binding = PyPSAService.bind_request_context(session_ctx(client))
    try:
        gen = next(e for e in X.find_parameter("capacity")["entries"] if e["entry_id"] == "generator.capacity")
    finally:
        PyPSAService.reset_request_context(binding)
    assert len(gen["assets"]) == X.MAX_ASSETS == 10
    assert gen["assets_total"] == 13 and gen["assets_truncated"] is True


def test_with_no_project_open_the_entries_come_without_assets(monkeypatch):
    monkeypatch.setattr(chat_tools.PyPSAService, "get_loaded_project", classmethod(lambda cls: None))
    out = X.find_parameter("capacity")
    assert out["project_open"] is False and out["entries"]
    assert all(e["assets"] == [] and e["assets_total"] == 0 for e in out["entries"])


@pytest.mark.parametrize("args", [{"asset_type": "spaceship"}, {"function": "teleport"}])
def test_an_unknown_filter_is_refused(args):
    with pytest.raises(HTTPException) as exc:
        X.find_parameter("capacity", **args)
    assert exc.value.detail["error_kind"] == "invalid_parameter_filter"


def test_a_viewer_reads_and_a_non_member_is_refused(world, as_user):
    from services.pypsa_service import PyPSAService

    viewer = as_user(world["viewer"])
    assert viewer.post(f"/api/projects/{world['project']}/activate").status_code == 200
    stranger = gate._member(world["session_local"], gate._org_of(world))
    binding = PyPSAService.bind_request_context(world["ctx"])
    try:
        chat_tools.set_acting_user(world["viewer"])
        gen = next(e for e in X.find_parameter("capacity")["entries"] if e["entry_id"] == "generator.capacity")
        assert [a["name"] for a in gen["assets"]] == ["G1"]
        chat_tools.set_acting_user(stranger)
        with pytest.raises(HTTPException) as exc:
            X.find_parameter("capacity")
        assert exc.value.status_code == 404
    finally:
        PyPSAService.reset_request_context(binding)


# ── open_parameter ───────────────────────────────────────────────────────────


def test_open_parameter_emits_the_parameter_kind(baseline):
    ev = X.open_parameter("generator.capacity", asset="G1")
    assert ev == {
        "_ui_event": True,
        "kind": "parameter",
        "entry_id": "generator.capacity",
        "component_class": "Generator",
        "plain_name": "Capacity",
        "name": "G1",
    }
    assert "name" not in X.open_parameter("generator.capacity")


def test_a_former_id_opens_the_current_entry(monkeypatch):
    real = C.entries()
    renamed = tuple(
        dataclasses.replace(e, spec=dataclasses.replace(e.spec, former_ids=("generator.p_nom",)))
        if e.id == "generator.capacity"
        else e
        for e in real
    )
    monkeypatch.setattr(C, "entries", lambda: renamed)
    lookup.clear_caches()
    try:
        ev = X.open_parameter("generator.p_nom")
        assert ev["entry_id"] == "generator.capacity" and ev["renamed_from"] == "generator.p_nom"
    finally:
        monkeypatch.undo()
        lookup.clear_caches()


def test_an_unknown_id_is_unknown_parameter_and_an_absent_asset_is_not_found(baseline):
    with pytest.raises(HTTPException) as exc:
        X.open_parameter("generator.warp_drive")
    assert exc.value.detail["error_kind"] == "unknown_parameter"
    with pytest.raises(HTTPException) as exc:
        X.open_parameter("generator.capacity", asset="Nope")
    assert exc.value.detail["error_kind"] == "component_not_found"
    assert exc.value.detail["message"].startswith("Capacity")


def test_the_model_sees_the_entry_it_opened():
    from harness.loop import dispatch_tool_call
    from harness.session import ChatSession

    frames = list(
        dispatch_tool_call(
            ChatSession(),
            {"id": "open-1", "name": "open_parameter", "input": {"entry_id": "generator.capacity"}},
            [],
        )
    )
    ui = [p for ev, p in frames if ev == "ui_event"]
    assert ui and ui[0]["kind"] == "parameter" and ui[0]["entry_id"] == "generator.capacity"


# ── plain names in write results ─────────────────────────────────────────────


def test_write_results_carry_plain_names_and_refusals_open_with_one():
    def ok(component_class, name, attrs):
        return {"updated": name}

    def refuse(component_class, name, attrs):
        raise HTTPException(422, detail={"error_kind": "invalid_value", "field": "p_nom", "message": "must be at least 0"})

    def refuse_text(component_class, name, attrs):
        raise HTTPException(400, "marginal_cost is not a number")

    wrap = lambda f: chat_plain_names.with_plain_names(f, columns=chat_plain_names._attr_keys)  # noqa: E731
    out = wrap(ok)("Generator", "G1", {"p_nom": 5, "not_a_column": 1})
    assert out == {"updated": "G1", "plain_names": {"p_nom": "Capacity"}}
    with pytest.raises(HTTPException) as exc:
        wrap(refuse)("Generator", "G1", {"p_nom": -1})
    assert exc.value.detail["message"] == "Capacity (p_nom): must be at least 0"
    with pytest.raises(HTTPException) as exc:
        wrap(refuse_text)("Generator", "G1", {"marginal_cost": "x"})
    assert exc.value.detail == "Running cost (marginal_cost): marginal_cost is not a number"


def test_the_generic_write_tools_are_wrapped():
    for name in chat_plain_names.COLUMNS:
        fn = chat_tools.DISPATCHERS[name]
        while fn is not None and not getattr(fn, "plain_names", False):
            fn = getattr(fn, "__wrapped__", None)
        assert fn is not None, name


def test_update_component_answers_with_plain_names(baseline):
    out = chat_tools.DISPATCHERS["update_component"]("Generator", "G1", {"p_nom": 30})
    assert out["plain_names"] == {"p_nom": "Capacity"}
