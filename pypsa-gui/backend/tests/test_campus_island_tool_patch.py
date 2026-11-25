"""campus_set_island_config with ``patch``: chat's partial island edit, the
same service as PATCH /campus-electrical/{p}/island (GK B3 K9-K12)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import harness.edit_card_campus  # noqa: F401  (registers the adapters)
from harness import edit_card
from services import campus_electrical_service as ce
from services import chat_tools
from tests.test_campus_island_service import (
    _config,
    _sidecar_path,
    project as island_hub,  # noqa: F401
    user_and_db,  # noqa: F401
)
from tests.test_edit_card import _dispatch, grid  # noqa: F401

HUB = "Island Hub"
PATCH = {"requirements": {"sustained_h": 4.0, "scenarios": ["seamless"]}}


@pytest.fixture
def saved(island_hub):  # noqa: F811
    ce.save_island(island_hub, _config())
    return island_hub


def _rev(project):
    return ce.get_island(project)["revision"]


def _kind(exc):
    return exc.value.detail["error_kind"]


# ── K9: config and patch are exclusive; patch needs base ─────────────────────


@pytest.mark.parametrize(
    "args",
    [
        {},  # neither
        {"config": _config(), "patch": PATCH, "base": "x"},  # both
        {"patch": PATCH},  # no base
        {"patch": PATCH, "base": ""},  # empty base
        {"patch": ["sustained_h"], "base": "x"},  # not an object
    ],
)
def test_the_tool_takes_exactly_one_of_config_or_patch_with_base(saved, args):
    before = _sidecar_path(saved).read_bytes()
    with pytest.raises(HTTPException) as exc:
        chat_tools.campus_set_island_config(HUB, **args)
    assert exc.value.status_code == 422 and _kind(exc) == "invalid_tool_args"
    with pytest.raises(HTTPException) as card:
        edit_card.preview("campus_set_island_config", {"project_id": HUB, **args})
    assert card.value.status_code == 422 and _kind(card) == "invalid_tool_args"
    assert _sidecar_path(saved).read_bytes() == before


def test_a_whole_config_still_saves_without_base(saved):
    changed = _config(scenarios=("planned",))
    chat_tools.campus_set_island_config(HUB, config=changed)
    assert ce.get_island(saved)["config"]["requirements"]["scenarios"] == ["planned"]


# ── K10: the patch's card shows only the fields it changes; no approval, no write ──


def test_the_patch_card_shows_old_to_new_for_the_changed_fields_only(grid, saved):  # noqa: F811
    frames, cards, result = _dispatch(
        grid,
        "campus_set_island_config",
        {"project_id": HUB, "patch": PATCH, "base": _rev(saved)},
    )
    assert len(cards) == 1
    rows = {r["field"]: r for r in cards[0]["edit_card"]["rows"] if r["role"] != "note"}
    assert set(rows) == {
        "requirements.sustained_h.value",
        "requirements.sustained_h.source",
        "requirements.scenarios",
    }
    assert (
        rows["requirements.sustained_h.value"]["old"],
        rows["requirements.sustained_h.value"]["new"],
    ) == (1.0, 4.0)
    assert rows["requirements.sustained_h.source"]["new"] == ce.EDITED_SOURCE
    stored = ce.get_island(saved)["config"]["requirements"]
    assert stored["sustained_h"] == {"value": 4.0, "source": ce.EDITED_SOURCE}
    assert stored["scenarios"] == ["seamless"]


def test_a_denied_patch_card_writes_nothing(grid, saved):  # noqa: F811
    before = _sidecar_path(saved).read_bytes()
    frames, cards, result = _dispatch(
        grid,
        "campus_set_island_config",
        {"project_id": HUB, "patch": PATCH, "base": _rev(saved)},
        decision="deny",
    )
    assert len(cards) == 1
    assert _sidecar_path(saved).read_bytes() == before


def test_a_patch_that_changes_nothing_is_an_empty_edit(saved):
    with pytest.raises(HTTPException) as exc:
        edit_card.preview(
            "campus_set_island_config",
            {
                "project_id": HUB,
                "patch": {"requirements": {"sustained_h": 1.0}},
                "base": _rev(saved),
            },
        )
    assert _kind(exc) == "empty_edit"


def test_a_patch_the_service_refuses_is_refused_on_the_card(saved):
    with pytest.raises(HTTPException) as exc:
        edit_card.preview(
            "campus_set_island_config",
            {
                "project_id": HUB,
                "patch": {"units": {"nope": {"droop_pct": 5.0}}},
                "base": _rev(saved),
            },
        )
    assert exc.value.status_code == 422 and _kind(exc) == "invalid_edit_value"


def test_a_patch_without_a_stored_config_is_refused(island_hub):  # noqa: F811
    with pytest.raises(HTTPException) as exc:
        edit_card.preview(
            "campus_set_island_config", {"project_id": HUB, "patch": PATCH, "base": "x"}
        )
    assert exc.value.status_code == 422
    assert not _sidecar_path(island_hub).exists()


# ── K11: a stale base is the route's typed 409, never an overwrite ───────────


def test_a_stale_base_is_refused_by_the_card_and_by_the_tool(saved):
    stale = _rev(saved)
    ce.patch_island(saved, {"requirements": {"bridge_s": 2.0}})
    before = _sidecar_path(saved).read_bytes()
    with pytest.raises(HTTPException) as card:
        edit_card.preview(
            "campus_set_island_config",
            {"project_id": HUB, "patch": PATCH, "base": stale},
        )
    assert card.value.status_code == 409 and _kind(card) == "island_config_changed"
    with pytest.raises(HTTPException) as tool:
        chat_tools.campus_set_island_config(HUB, patch=PATCH, base=stale)
    assert tool.value.status_code == 409 and _kind(tool) == "island_config_changed"
    assert _sidecar_path(saved).read_bytes() == before


# ── K12: the route's service and the tool leave the same bytes ───────────────


def test_route_and_tool_patch_leave_byte_equal_config(saved):
    base = _rev(saved)
    ce.patch_island(saved, PATCH, base)  # what PATCH /island calls
    via_route = _sidecar_path(saved).read_bytes()
    ce.save_island(saved, _config())
    assert _rev(saved) == base
    chat_tools.campus_set_island_config(HUB, patch=PATCH, base=base)
    assert _sidecar_path(saved).read_bytes() == via_route


# ── CS-B PR 3: a mode switch through the tool (GK B3 K18, K20) ───────────────

SWITCH = {"units": {"bess": {"control": "gfm_vsm"}}}


def test_a_switch_card_shows_each_dropped_field_as_removed_and_each_filled_one(
    grid, saved
):  # noqa: F811
    frames, cards, result = _dispatch(
        grid,
        "campus_set_island_config",
        {"project_id": HUB, "patch": SWITCH, "base": _rev(saved)},
    )
    assert len(cards) == 1
    rows = {r["field"]: r for r in cards[0]["edit_card"]["rows"] if r["role"] != "note"}
    assert (rows["units.bess.control"]["old"], rows["units.bess.control"]["new"]) == (
        "gfm_droop",
        "gfm_vsm",
    )
    dropped = rows["units.bess.tau_f_s.value"]  # gfm_droop's, dropped
    assert (dropped["old"], dropped["new"], dropped["words"]["new"]) == (
        0.1,
        None,
        "removed",
    )
    assert (
        rows["units.bess.H_v_s.value"]["old"],
        rows["units.bess.H_v_s.value"]["new"],
    ) == (None, 4.0)
    assert rows["units.bess.H_v_s.source"]["new"] == "assumed"
    assert not any(
        f.startswith(("units.bess.droop_pct", "units.bess.pf_rated")) for f in rows
    )  # kept


def test_route_and_tool_switch_leave_byte_equal_config(saved):
    base = _rev(saved)
    ce.patch_island(saved, SWITCH, base)
    via_route = _sidecar_path(saved).read_bytes()
    ce.save_island(saved, _config())
    assert _rev(saved) == base
    chat_tools.campus_set_island_config(HUB, patch=SWITCH, base=base)
    assert _sidecar_path(saved).read_bytes() == via_route
