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
from services import parameter_assets as PA
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
    assert all(
        "load_flow" in e.functions
        for e in lookup.search("rating", function="load_flow")
    )
    assert lookup.search("   ") == []
    assert len(lookup.search("a")) <= 8


def test_asset_type_of_follows_the_type_table():
    carriers = {"B1": "AC", "H": "H2"}.get
    assert (
        lookup.asset_type_of("Generator", {"carrier": "rooftop pv"}, carriers).id
        == "pvRoof"
    )
    assert lookup.asset_type_of("Store", {"carrier": "battery"}, carriers).id == "bess"
    assert (
        lookup.asset_type_of(
            "Link", {"carrier": "h2", "bus0": "B1", "bus1": "H"}, carriers
        )
        is not None
    )


# ── find_parameter ───────────────────────────────────────────────────────────


def test_find_parameter_answers_the_entry_and_the_open_projects_assets(baseline):
    out = X.find_parameter("capacity")
    assert out["project_open"] is True and len(out["entries"]) <= X.MAX_ENTRIES
    gen = next(e for e in out["entries"] if e["entry_id"] == "generator.capacity")
    assert gen["plain_name"] == "Capacity" and gen["model_name"] == "p_nom"
    assert gen["component_class"] == "Generator" and gen["unit"]
    assert gen["short_help"] and gen["group"] and isinstance(gen["guide"], str)
    assert gen["assets"] == [
        {
            "name": "G1",
            "asset_type": gen["assets"][0]["asset_type"],
            "value": 20.0,
            "unit": "MW",
            "assumed": False,
        }
    ]
    assert gen["assets_total"] == 1 and gen["assets_truncated"] is False
    assert "pv" in gen["asset_types"] and "bess" not in gen["asset_types"]


def test_a_value_left_at_its_default_is_assumed(baseline):
    eff = next(
        e
        for e in X.find_parameter("efficiency", asset_type=None)["entries"]
        if e["entry_id"].startswith("generator.")
    )
    assert eff["assets"] and eff["assets"][0]["assumed"] is True


@pytest.mark.parametrize(
    "stored, default, assumed",
    [
        (None, 1.0, True),  # blank (a NaN reads as None: the stored "not set")
        ("", 1.0, True),
        (1.0 + 1e-12, 1.0, True),  # float noise
        ("1", 1.0, True),  # a number sent as a string
        (2.0, 1.0, False),
        (5.0, None, False),  # no default: a value is the user's
        ("PQ", "PQ", True),
        (True, False, False),
    ],
)
def test_assumed_compares_after_write_normalisation(stored, default, assumed):
    from services.parameter_assets import _is_assumed, _scalar

    assert _scalar(float("nan")) is None
    assert _is_assumed(stored, default) is assumed


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
        gen = next(
            e
            for e in X.find_parameter("capacity")["entries"]
            if e["entry_id"] == "generator.capacity"
        )
    finally:
        PyPSAService.reset_request_context(binding)
    assert len(gen["assets"]) == X.MAX_ASSETS == 10
    assert gen["assets_total"] == 13 and gen["assets_truncated"] is True


def test_an_asset_on_a_tonne_bus_shows_its_value_per_tonne():
    # As the Expert panel reads it (`fields.cost_units` / `size_units`): per t/h at
    # scale 1 on a tonne bus, the class-level EUR/kW/yr (x 0.001) on an energy bus.
    n = pypsa.Network()
    n.add("Bus", "t0", carrier="AC", unit="t")
    n.add("Bus", "e0", carrier="AC")
    n.add("Bus", "e1", carrier="AC")
    n.add("Link", "tonne", bus0="t0", bus1="e1", p_nom=5, capital_cost=2000.0)
    n.add("Link", "energy", bus0="e0", bus1="e1", p_nom=5, capital_cost=2000.0)
    cost, _ = lookup.resolve("link.yearly_capital_cost")
    size, _ = lookup.resolve("link.capacity")
    got = {a["name"]: (a["value"], a["unit"]) for a in PA.assets(n, cost, None)}
    assert got == {"tonne": (2000.0, "EUR/(t/h)/yr"), "energy": (2.0, "EUR/kW/yr")}
    got = {a["name"]: (a["value"], a["unit"]) for a in PA.assets(n, size, None)}
    assert got == {"tonne": (5.0, "t/h"), "energy": (5.0, "MW")}


def test_with_no_project_open_the_entries_come_without_assets(monkeypatch):
    monkeypatch.setattr(
        chat_tools.PyPSAService, "get_loaded_project", classmethod(lambda cls: None)
    )
    out = X.find_parameter("capacity")
    assert out["project_open"] is False and out["entries"]
    assert all(e["assets"] == [] and e["assets_total"] == 0 for e in out["entries"])


@pytest.mark.parametrize(
    "args", [{"asset_type": "spaceship"}, {"function": "teleport"}]
)
def test_an_unknown_filter_is_refused(args):
    with pytest.raises(HTTPException) as exc:
        X.find_parameter("capacity", **args)
    assert exc.value.detail["error_kind"] == "invalid_parameter_filter"


def test_an_internal_asset_type_is_refused_as_the_palette_hides_it(monkeypatch):
    real = lookup.ASSET_TYPES[0]
    hidden = dataclasses.replace(real, internal=True)
    monkeypatch.setattr(lookup, "asset_type", lambda tid: hidden if tid == real.id else None)
    with pytest.raises(HTTPException) as exc:
        X.find_parameter("capacity", asset_type=real.id)
    assert exc.value.detail["error_kind"] == "invalid_parameter_filter"


def test_an_unknown_function_reads_as_the_catalogue_route_refuses_it():
    from services.asset_schema.catalogue import UnknownFilter, check_filters

    with pytest.raises(UnknownFilter) as route:
        check_filters("teleport", None)
    with pytest.raises(HTTPException) as chat:
        X.find_parameter("capacity", function="teleport")
    assert chat.value.detail["message"] == str(route.value)


def test_a_viewer_reads_and_a_non_member_is_refused(world, as_user):
    from services.pypsa_service import PyPSAService

    viewer = as_user(world["viewer"])
    assert viewer.post(f"/api/projects/{world['project']}/activate").status_code == 200
    stranger = gate._member(world["session_local"], gate._org_of(world))
    binding = PyPSAService.bind_request_context(world["ctx"])
    try:
        chat_tools.set_acting_user(world["viewer"])
        gen = next(
            e
            for e in X.find_parameter("capacity")["entries"]
            if e["entry_id"] == "generator.capacity"
        )
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
        "current_id": "generator.capacity",
        "component_class": "Generator",
        "plain_name": "Capacity",
        "name": "G1",
    }
    assert "name" not in X.open_parameter("generator.capacity")


def test_a_former_id_opens_the_current_entry(monkeypatch):
    real = C.entries()
    renamed = tuple(
        dataclasses.replace(
            e, spec=dataclasses.replace(e.spec, former_ids=("generator.p_nom",))
        )
        if e.id == "generator.capacity"
        else e
        for e in real
    )
    monkeypatch.setattr(C, "entries", lambda: renamed)
    lookup.clear_caches()
    try:
        ev = X.open_parameter("generator.p_nom")
        assert (
            ev["entry_id"] == "generator.capacity"
            and ev["current_id"] == "generator.capacity"
        )
        assert ev["former_id"] == "generator.p_nom"
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
    with pytest.raises(HTTPException) as exc:
        X.open_parameter("generator.capacity", asset="L1")  # a Load, not a Generator
    assert exc.value.detail["error_kind"] == "component_not_found"
    assert (
        exc.value.detail["message"]
        == "Capacity: 'L1' is a Load, and Capacity is a Generator parameter."
    )


def test_an_asset_with_no_project_open_is_no_active_project(monkeypatch):
    monkeypatch.setattr(
        chat_tools.PyPSAService, "get_loaded_project", classmethod(lambda cls: None)
    )
    with pytest.raises(HTTPException) as exc:
        X.open_parameter("generator.capacity", asset="G1")
    d = exc.value.detail
    assert d["error_kind"] == "no_active_project" and "upload" not in d["message"]


def test_the_model_sees_the_entry_it_opened():
    from harness.loop import dispatch_tool_call
    from harness.session import ChatSession

    frames = list(
        dispatch_tool_call(
            ChatSession(),
            {
                "id": "open-1",
                "name": "open_parameter",
                "input": {"entry_id": "generator.capacity"},
            },
            [],
        )
    )
    ui = [p for ev, p in frames if ev == "ui_event"]
    assert (
        ui
        and ui[0]["kind"] == "parameter"
        and ui[0]["entry_id"] == "generator.capacity"
    )


# ── plain names in write results ─────────────────────────────────────────────


def test_write_results_carry_plain_names_and_refusals_name_the_column():
    from harness.results import _error_result_content

    def ok(component_class, name, attrs):
        return {"updated": name}

    def refuse(component_class, name, attrs):
        raise HTTPException(
            422,
            detail={
                "error_kind": "value_out_of_range",
                "field": "p_nom",
                "message": "must be at least 0",
            },
        )

    def refuse_naming(component_class, name, attrs):
        raise HTTPException(
            400,
            detail={
                "error_kind": "invalid",
                "message": "marginal_cost is not a number",
            },
        )

    def refuse_text(component_class, name, attrs):
        raise HTTPException(400, "marginal_cost is not a number")

    def wrap(f):
        return chat_plain_names.with_plain_names(
            f, columns=chat_plain_names._keys_of("attrs")
        )  # noqa: E731

    out = wrap(ok)("Generator", "G1", {"p_nom": 5, "not_a_column": 1})
    assert out == {"updated": "G1", "plain_names": {"p_nom": "Capacity"}}
    with pytest.raises(HTTPException) as exc:
        wrap(refuse)("Generator", "G1", {"p_nom": -1})
    # The shared message is untouched (one text on every write path, 01a-3a R7) ...
    assert exc.value.detail["message"] == "must be at least 0"
    assert exc.value.detail["plain_name"] == "Capacity"
    # ... and the model's copy opens with the plain name.
    content = _error_result_content(exc.value.detail, exc.value, "value_out_of_range")
    assert (
        content.startswith("value_out_of_range\n")
        and "Capacity (p_nom): must be at least 0" in content
    )
    with pytest.raises(HTTPException) as exc:
        wrap(refuse_naming)("Generator", "G1", {"marginal_cost": "x"})
    assert (
        exc.value.detail["field"] == "marginal_cost"
        and exc.value.detail["plain_name"] == "Running cost"
    )
    with pytest.raises(HTTPException) as exc:
        wrap(refuse_text)("Generator", "G1", {"marginal_cost": "x"})
    assert (
        exc.value.detail == "marginal_cost is not a number"
    )  # plain text: passed on as it is


def test_a_refusal_names_the_column_its_text_names_first():
    def refuse(component_class, name, attrs):
        raise HTTPException(
            422, detail={"error_kind": "invalid", "message": "p_nom_min must not exceed p_nom"}
        )

    wrapped = chat_plain_names.with_plain_names(
        refuse, columns=chat_plain_names._keys_of("attrs")
    )
    with pytest.raises(HTTPException) as exc:
        wrapped("Generator", "G1", {"p_nom": 1, "p_nom_min": 2})
    assert exc.value.detail["field"] == "p_nom_min"


def test_a_schema_refusal_names_the_column_with_the_same_kind_and_text():
    from pydantic import BaseModel, ValidationError
    from harness.results import _error_result_content

    class Body(BaseModel):
        p_nom: float
        other: float = 0

    def create(component_class, name, attrs):
        return Body(**attrs)

    wrapped = chat_plain_names.with_plain_names(
        create, columns=chat_plain_names._keys_of("attrs")
    )
    with pytest.raises(ValidationError) as raw:
        create("Generator", "G1", {"p_nom": "big"})
    with pytest.raises(HTTPException) as exc:
        wrapped("Generator", "G1", {"p_nom": "big"})
    d = exc.value.detail
    assert d["error_kind"] == "validation_error" and d["message"] == str(raw.value)
    assert d["field"] == "p_nom" and d["plain_name"] == "Capacity"
    assert "Capacity (p_nom): " in _error_result_content(d, exc.value, d["error_kind"])
    # A column the call does not write keeps the raw ValidationError.
    with pytest.raises(ValidationError):
        wrapped("Generator", "G1", {"p_nom": 1, "other": "x"})


def test_the_generic_write_tools_are_wrapped():
    for name in chat_plain_names.COLUMNS:
        fn = chat_tools.DISPATCHERS[name]
        while fn is not None and not getattr(fn, "plain_names", False):
            fn = getattr(fn, "__wrapped__", None)
        assert fn is not None, name


def test_update_component_answers_with_plain_names(baseline):
    out = chat_tools.DISPATCHERS["update_component"]("Generator", "G1", {"p_nom": 30})
    assert out["plain_names"] == {"p_nom": "Capacity"}


# ── the pilot scorer (P5: catalogue model names only) ────────────────────────


def test_the_scorer_counts_model_names_and_never_component_ids():
    from harness.pilot_evaluation import raw_model_names

    assert raw_model_names("Set p_nom to 5.") == ["p_nom"]
    assert raw_model_names("Set `p_nom` to 5.") == []  # shown as its plain name
    assert raw_model_names("```\np_nom_max = 3\n```") == []  # code
    # A project's snake_case component ids are not model names.
    assert (
        raw_model_names(
            "wind_farm and genset_new_2030 run; site_transformer is loaded."
        )
        == []
    )
