"""Catalogue hard ranges and the one write validator (expert-catalogue 01a-3a).

Spec §3.2 and §10.1 "Validation parity"; issue 01 C8–C12; GK B2 R1–R9.
Every write path refuses an out-of-range value with the same kind and text;
a value equal to the stored one passes; restores never check.
"""

from __future__ import annotations

import math
import pathlib
import re

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from services import chat_tools, project_registry
from services.asset_schema import catalogue
from services.asset_schema import ranges as R
from services.pypsa_service import PyPSAService

BACKEND = pathlib.Path(__file__).resolve().parents[1]

# ── The table ───────────────────────────────────────────────────────────────


def test_inf_ok_is_the_engines_infinite_defaults():
    """C11: ±inf is accepted exactly where PyPSA defaults to it."""
    defaults = catalogue._engine_defaults()
    numeric = R._numeric_entries()
    derived = {
        k: v
        for k, v in defaults.items()
        if k in numeric and isinstance(v, float) and math.isinf(v)
    }
    assert derived == R.INF_OK


def test_every_hard_row_is_a_numeric_entry_with_a_source():
    numeric = R._numeric_entries()
    assert set(R.HARD) == {
        ("Bus", "v_nom"),
        ("StorageUnit", "efficiency_dispatch"),
        ("StorageUnit", "standing_loss"),
        ("Store", "standing_loss"),
    }
    for key, r in R.HARD.items():
        assert key in numeric, key
        assert "pypsa" in r.source and ".py:" in r.source, key
        assert r.min is not None or r.max is not None, key


def test_the_engine_defaults_are_in_range():
    defaults = catalogue._engine_defaults()
    for key in R.HARD:
        if key in defaults:
            assert R.problem(*key, defaults[key]) is None, key


def test_the_wire_serves_the_hard_range():
    by_id = {e["id"]: e for e in catalogue.payload()["entries"]}
    assert by_id["bus.nominal_voltage"]["range"] == {
        "min": 0.0,
        "max": None,
        "min_open": True,
        "max_open": False,
        "soft_min": None,
        "soft_max": None,
    }
    assert by_id["store.standing_loss"]["range"]["max"] == 1.0
    ranged = {i for i, e in by_id.items() if e["range"] is not None}
    assert len(ranged) == len(R.HARD)


# ── One value ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cls, key, value, text",
    [
        ("Bus", "v_nom", 0.0, "Nominal voltage must be more than 0; got 0.0."),
        ("Bus", "v_nom", -0.0001, "Nominal voltage must be more than 0; got -0.0001."),
        ("Store", "standing_loss", 1.5, "Standing loss must be at most 1; got 1.5."),
        ("Generator", "p_nom", float("nan"), "Capacity must be a number; got NaN."),
        (
            "Generator",
            "p_nom",
            float("inf"),
            "must be a finite number here; got infinity.",
        ),
        (
            "Generator",
            "p_nom_max",
            -math.inf,
            "must be a finite number here; got minus infinity.",
        ),
        ("Bus", "v_nom", "0", "Nominal voltage must be more than 0; got 0.0."),
    ],
)
def test_a_value_out_of_range_names_the_field_and_the_value(cls, key, value, text):
    msg = R.problem(cls, key, value)
    assert msg is not None and msg.endswith(text), msg


@pytest.mark.parametrize(
    "cls, key, value",
    [
        ("Bus", "v_nom", 0.4),
        ("Store", "standing_loss", 1.0),
        ("Store", "standing_loss", -0.5),
        ("Generator", "p_nom_max", math.inf),
        ("Generator", "e_sum_min", -math.inf),
        (
            "Generator",
            "discount_rate",
            float("nan"),
        ),  # NaN is the stored "not set" (K1d)
        ("Link", "efficiency2", float("nan")),  # an unused extra port
        ("Link", "efficiency2", -1.0),  # R1: negative means an input
        ("Generator", "marginal_cost", -50.0),  # R1: negative costs are valid
        ("Generator", "p_min_pu", -1.0),  # R1: no *_pu domain
        ("Link", "efficiency", 3.5),  # R1: COP above 1
        ("Generator", "p_nom", None),
        ("Generator", "not_a_field", -1.0),
    ],
)
def test_a_value_in_range_or_outside_the_table_passes(cls, key, value):
    assert R.problem(cls, key, value) is None


def test_nan_is_accepted_only_where_the_engine_has_no_number():
    unset = R.nan_is_unset()
    assert ("Generator", "discount_rate") in unset
    assert ("Generator", "p_nom") not in unset
    defaults = catalogue._engine_defaults()
    assert {k for k in unset if k in defaults} <= {
        ("Link", f"efficiency{i}") for i in range(2, 10)
    }


@pytest.mark.parametrize("stored", [0.0, "0", 1e-13])
def test_the_stored_value_resent_is_no_change(stored):
    """C8, R4: after coercion, float noise or a string is the stored value."""
    R.check("Bus", {"v_nom": 0.0}, stored={"v_nom": stored})
    R.check_column("Bus", "v_nom", "0.0", [stored, 0.0])
    with pytest.raises(HTTPException):
        R.check_column("Bus", "v_nom", 0.0, [stored, 110.0])


# ── Every write path (R9) ───────────────────────────────────────────────────

#: The code sites that run the validator; the funnel test greps for them.
FUNNELS = {
    "services/network_crud.py": 2,  # _create_component, _update_component
    "services/network_bulk.py": 2,  # _coerce_bulk_value, apply_bulk_update's loop
    "services/assistant_tools.py": 1,  # _candidate: project changes and experiments
}

TEXT = "Discharging efficiency must be more than 0; got 0.0."


def test_the_funnel_list_is_complete():
    found = {}
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith("tests/"):
            continue
        hits = len(
            re.findall(r"asset_ranges\.check(?:_column)?\(", path.read_text("utf-8"))
        )
        if hits:
            found[rel] = hits
    assert found == FUNNELS


@pytest.fixture
def world(client, session_ctx, install_network):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=2, freq="h"))
    n.add("Bus", "B1", v_nom=110)
    n.add("Generator", "G1", bus="B1", p_nom=20, marginal_cost=10)
    n.add("Load", "L1", bus="B1", p_set=5)
    n.add("StorageUnit", "SU", bus="B1", p_nom=5, max_hours=2, efficiency_dispatch=0.9)
    install_network(n)
    assert client.post("/api/projects/Range World").status_code == 200
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        with chat_tools._acting() as (db, user):
            project = project_registry.resolve_project(db, user, "Range World")
        yield ctx, str(project.id)
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def _message(call) -> str:
    try:
        out = call()
    except HTTPException as exc:
        assert exc.status_code == 422, exc.detail
        assert (
            isinstance(exc.detail, dict) and exc.detail["error_kind"] == R.ERROR_KIND
        ), exc.detail
        return exc.detail["message"]
    pytest.fail(f"accepted: {out!r}")


def _http(r) -> str:
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error_kind"] == R.ERROR_KIND, detail
    return detail["message"]


def _edit_card(tool, args):
    from harness import edit_card

    return lambda: edit_card.preview(tool, args)


def _sensitivity(project_id):
    return lambda: chat_tools.run_sensitivity_sweep(
        project_id,
        [
            {
                "new_name": "Range Case",
                "changes": [
                    {
                        "component_class": "StorageUnit",
                        "names": ["SU"],
                        "attribute": "efficiency_dispatch",
                        "value": 0.0,
                    }
                ],
            }
        ],
    )


def _project_changes():
    return lambda: chat_tools.preview_project_changes(
        [
            {
                "component_class": "StorageUnit",
                "names": ["SU"],
                "attribute": "efficiency_dispatch",
                "value": 0.0,
            }
        ]
    )


def _experiment():
    from services import experiments as ex

    started = ex.start_experiment(
        title="Range",
        request_id="range-1",
        cases=[
            {
                "id": "low",
                "changes": [
                    {
                        "path": "StorageUnit.SU.efficiency_dispatch",
                        "value_type": "number",
                        "value": 0.0,
                    }
                ],
            }
        ],
        limits=None,
    )
    return lambda: ex.prepare_experiment(started["experiment_id"])


#: Every entry point that writes a component value, with how it is called.
PATHS = {
    "rest create": lambda c, pid: _http(
        c.post(
            "/api/network/storage_units",
            json={"name": "SU2", "bus": "B1", "efficiency_dispatch": 0.0},
        )
    ),
    "rest update": lambda c, pid: _http(
        c.put(
            "/api/network/storage_units/SU",
            json={"name": "SU", "bus": "B1", "efficiency_dispatch": 0.0},
        )
    ),
    "rest bulk": lambda c, pid: _http(
        c.patch(
            "/api/network/_bulk",
            json={
                "component_class": "StorageUnit",
                "names": ["SU"],
                "updates": {"efficiency_dispatch": 0.0},
            },
        )
    ),
    "rest bulk rows": lambda c, pid: _http(
        c.patch(
            "/api/network/_bulk",
            json={
                "component_class": "StorageUnit",
                "rows": [{"name": "SU", "updates": {"efficiency_dispatch": 0.0}}],
            },
        )
    ),
    "chat create": lambda c, pid: _message(
        lambda: chat_tools.create_component(
            "StorageUnit", "SU2", {"bus": "B1", "efficiency_dispatch": 0.0}
        )
    ),
    "chat batch create": lambda c, pid: _message(
        lambda: chat_tools.batch_create_components(
            "StorageUnit", [{"name": "SU2", "bus": "B1", "efficiency_dispatch": 0.0}]
        )
    ),
    "chat update": lambda c, pid: _message(
        lambda: chat_tools.update_component(
            "StorageUnit", "SU", attrs={"efficiency_dispatch": 0.0}
        )
    ),
    "chat bulk": lambda c, pid: _message(
        lambda: chat_tools.bulk_update_components(
            "StorageUnit", ["SU"], {"efficiency_dispatch": 0.0}
        )
    ),
    "preview update": lambda c, pid: _message(
        _edit_card(
            "update_component",
            {
                "component_class": "StorageUnit",
                "name": "SU",
                "attrs": {"efficiency_dispatch": 0.0},
            },
        )
    ),
    "preview bulk": lambda c, pid: _message(
        _edit_card(
            "bulk_update_components",
            {
                "component_class": "StorageUnit",
                "names": ["SU"],
                "updates": {"efficiency_dispatch": 0.0},
            },
        )
    ),
    "project changes": lambda c, pid: _message(_project_changes()),
    "sensitivity cases": lambda c, pid: _message(_sensitivity(pid)),
    "experiment": lambda c, pid: _message(_experiment()),
}


@pytest.mark.parametrize("path", sorted(PATHS))
def test_every_write_path_refuses_with_the_same_kind_and_text(client, world, path):
    _, project_id = world
    message = PATHS[path](client, project_id)
    assert message == TEXT, message
    n = PyPSAService.get_network()
    assert n.storage_units.at["SU", "efficiency_dispatch"] == 0.9
    assert "SU2" not in n.storage_units.index


@pytest.mark.parametrize(
    "path", ["rest update", "rest bulk", "chat update", "chat bulk"]
)
def test_an_asset_stored_out_of_range_stays_editable(client, world, path):
    """C8: the stored value loads, and an edit to another field, or a re-send
    of the stored value itself, is never refused."""
    n = PyPSAService.get_network()
    n.storage_units.at["SU", "efficiency_dispatch"] = 0.0
    other = {"max_hours": 3.0}
    resend = {"efficiency_dispatch": "0"}
    for updates in (other, resend):
        if path == "rest update":
            r = client.put(
                "/api/network/storage_units/SU",
                json={"name": "SU", "bus": "B1", **updates},
            )
            assert r.status_code == 200, r.text
        elif path == "rest bulk":
            r = client.patch(
                "/api/network/_bulk",
                json={
                    "component_class": "StorageUnit",
                    "names": ["SU"],
                    "updates": updates,
                },
            )
            assert r.status_code == 200, r.text
        elif path == "chat update":
            chat_tools.update_component("StorageUnit", "SU", attrs=updates)
        else:
            chat_tools.bulk_update_components("StorageUnit", ["SU"], updates)
    n = PyPSAService.get_network()
    assert n.storage_units.at["SU", "max_hours"] == 3.0
    assert n.storage_units.at["SU", "efficiency_dispatch"] == 0.0


def test_a_bus_on_another_unit_is_checked_in_its_stored_unit(client, world):
    """C10: the bounds are sign bounds, so a tonne bus reads the same rule."""
    r = client.post(
        "/api/network/buses", json={"name": "ore", "v_nom": 1.0, "unit": "t"}
    )
    assert r.status_code in (200, 201), r.text
    r = client.post(
        "/api/network/storage_units",
        json={
            "name": "pile",
            "bus": "ore",
            "p_nom": 500.0,
            "efficiency_dispatch": 0.95,
        },
    )
    assert r.status_code in (200, 201), r.text
    r = client.put(
        "/api/network/storage_units/pile",
        json={"name": "pile", "bus": "ore", "efficiency_dispatch": 0.0},
    )
    assert _http(r) == TEXT


def test_undo_back_to_a_stored_out_of_range_value_is_not_checked(client, world):
    """R5: undo restores, it never runs the validator."""
    n = PyPSAService.get_network()
    n.storage_units.at["SU", "efficiency_dispatch"] = 0.0
    r = client.put(
        "/api/network/storage_units/SU",
        json={"name": "SU", "bus": "B1", "max_hours": 4.0},
    )
    assert r.status_code == 200, r.text
    assert client.post("/api/network/undo").status_code == 200
    n = PyPSAService.get_network()
    assert n.storage_units.at["SU", "efficiency_dispatch"] == 0.0
    assert n.storage_units.at["SU", "max_hours"] == 2.0


def test_the_bulk_coercion_copy_refuses_like_the_bulk_loop(client, world):
    """R9, #1127 M10: `_coerce_bulk_value` (the outcome reader's copy of the
    bulk coercion) refuses the same value with the same text."""
    from services.network_bulk import _coerce_bulk_value

    df = PyPSAService.get_network().storage_units
    assert (
        _message(
            lambda: _coerce_bulk_value(df, "efficiency_dispatch", 0.0, "StorageUnit")
        )
        == TEXT
    )
    assert _coerce_bulk_value(df, "efficiency_dispatch", 0.5, "StorageUnit") == 0.5
