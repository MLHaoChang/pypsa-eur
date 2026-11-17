"""
homer-parity 03b-1 (GK B V9): the system type map's confirmation data.

The digest freezes the shown arguments plus the saved base's revision and
nothing else; the derived architectures and solve count must equal what the
card computed at approval. The refusals come before any card.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from harness import system_map_card as C
from tests.study_s4_support import enable_studies
from tests.test_homer_supply_architecture_routes import _project


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


def _args(name, sid, **kw):
    args = {
        "project": name,
        "study_id": sid,
        "x_variable": "genset_fuel_price",
        "y_variable": "load",
        "x_values": [0.5, 1.0, 2.0],
        "y_values": [1.0, 2.0, 3.0],
        "architectures": ["grid", "pv"],
    }
    args.update(kw)
    return args


def _refused(args) -> dict:
    with pytest.raises(HTTPException) as exc:
        C.preview(args)
    return exc.value.detail


def test_the_card_names_the_derived_architectures_and_the_solve_count(
    client, studies_on, install_network
):
    sid = _project(client, install_network, "mc-card")
    card = C.preview(_args("mc-card", sid))
    assert card["architectures"] == ["grid", "pv"] and card["solves"] == 18
    assert card["shown"]["x"] == {
        "variable": "genset_fuel_price",
        "values": [0.5, 1.0, 2.0],
    }
    assert card["shown"]["y"] == {"variable": "load", "values": [1.0, 2.0, 3.0]}


def test_the_digest_is_the_shown_arguments_plus_the_base_revision_and_nothing_else(
    client, studies_on, install_network
):
    sid = _project(client, install_network, "mc-digest")
    card = C.preview(_args("mc-digest", sid))
    assert card["digest"] == C.digest_of(card["shown"], card["base_fingerprint"])
    # the derived figures are not in it
    tweaked = {**card, "solves": 999, "architectures": ["x"]}
    assert C.digest_of(tweaked["shown"], tweaked["base_fingerprint"]) == card["digest"]
    # defaults are filled in: omitted values show the three every axis gets
    d = C.preview(_args("mc-digest", sid, x_values=None, y_values=None))
    assert d["shown"]["x"]["values"] == [0.5, 1.0, 1.5]
    # each shown argument and the base revision move it
    for patch in (
        {"x_values": [0.5, 1.0, 3.0]},
        {"y_variable": "grid_energy_price"},
        {"architectures": ["pv"]},
        {"budget_solves": 30},
    ):
        other = C.preview(_args("mc-digest", sid, **patch))
        assert other["digest"] != card["digest"], patch
    assert C.digest_of(card["shown"], "another revision") != card["digest"]


def test_require_current_accepts_the_same_card_and_refuses_a_moved_one(
    client, studies_on, install_network
):
    sid = _project(client, install_network, "mc-cur")
    args = _args("mc-cur", sid)
    card = C.preview(args)
    C.require_current(args, card)
    for field, value in (("solves", 17), ("architectures", ["pv"])):
        with pytest.raises(HTTPException) as exc:
            C.require_current(args, {**card, field: value})
        assert exc.value.detail["error_kind"] == "system_map_preview_stale"
    with pytest.raises(HTTPException) as exc:
        C.require_current(args, {**card, "base_fingerprint": "old", "digest": "old"})
    assert exc.value.detail["error_kind"] == "system_map_preview_stale"


def test_a_base_edited_after_the_card_is_refused_at_execution(
    client, studies_on, install_network, project_storage_dir
):
    import pypsa

    from services.pypsa_service import PyPSAService

    sid = _project(client, install_network, "mc-edit")
    args = _args("mc-edit", sid)
    card = C.preview(args)
    path = project_storage_dir("mc-edit") / "network.nc"
    n = pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.import_network_from_netcdf(n, path)
    n.generators.loc["genset", "marginal_cost"] = 999.0
    n.export_to_netcdf(path)
    with pytest.raises(HTTPException) as exc:
        C.require_current(args, card)
    assert exc.value.detail["error_kind"] == "system_map_preview_stale"


def test_an_unsaved_base_shows_no_card(
    client, studies_on, install_network, registry_key_for
):
    from services import dirty_state
    from services.pypsa_service import PyPSAService

    sid = _project(client, install_network, "mc-dirty")
    ctx = PyPSAService.get_context(registry_key_for("mc-dirty"))
    dirty_state.mark_dirty(ctx)
    assert _refused(_args("mc-dirty", sid))["error_kind"] == "baseline_not_saved"


@pytest.mark.parametrize(
    "patch,kind",
    [
        ({"x_variable": "wind_resource"}, "variable_not_in_network"),
        ({"x_variable": "pv_capital_cost"}, "variable_not_in_network"),
        ({"x_variable": "nonsense"}, "unknown_variable"),
        ({"y_variable": "genset_fuel_price"}, "axes_not_different"),
        ({"x_values": [1.0]}, "axis_values_invalid"),
        ({"x_values": [1.0, 11.0]}, "axis_values_invalid"),
        ({"architectures": ["wind"]}, "unknown_architecture"),
        (
            {
                "architectures": None,
                "x_values": [1, 2, 3, 4, 5],
                "y_values": [1, 2, 3, 4, 5],
            },
            "campaign_budget_invalid",
        ),
    ],
)
def test_a_request_that_cannot_run_shows_no_card(
    client, studies_on, install_network, patch, kind
):
    sid = _project(client, install_network, "mc-bad")
    assert _refused(_args("mc-bad", sid, **patch))["error_kind"] == kind


def test_the_chat_loop_attaches_the_card_only_to_a_start():
    from harness import confirm

    import inspect

    sig = inspect.signature(confirm._confirm_destructive_tool)
    assert "map_card" in sig.parameters
