"""PATCH /campus-electrical/{p}/island: a partial island edit merged into the
stored sidecar (control-strategy 02/03; GK B3 K1, K2, K4)."""

from __future__ import annotations

import json
import threading

import pytest
from fastapi import HTTPException

from services import campus_electrical_service as ce
from tests.test_campus_island_service import (
    _a,
    _config,
    _sidecar_path,
    project,  # noqa: F401
    user_and_db,  # noqa: F401
)


def _stored(project):  # noqa: F811
    return json.loads(_sidecar_path(project).read_text())


@pytest.fixture
def island(project):  # noqa: F811
    ce.save_island(project, _config())
    return project


def test_get_returns_the_revision_and_it_follows_the_file(island):
    rev = ce.get_island(island)["revision"]
    assert isinstance(rev, str) and len(rev) == 64
    ce.patch_island(island, {"requirements": {"sustained_h": 2.0}})
    assert ce.get_island(island)["revision"] != rev


def test_no_sidecar_has_no_revision_and_a_patch_is_refused(project):  # noqa: F811
    assert ce.get_island(project)["revision"] is None
    with pytest.raises(HTTPException) as exc:
        ce.patch_island(project, {"requirements": {"sustained_h": 2.0}})
    assert exc.value.status_code == 422
    assert not _sidecar_path(project).exists()


def test_a_unit_merges_one_level_deep_and_keeps_its_other_numbers(island):
    before = _stored(island)["units"]["bess"]
    out = ce.patch_island(island, {"units": {"bess": {"droop_pct": 5.0}}})
    unit = _stored(island)["units"]["bess"]
    assert unit["droop_pct"] == {"value": 5.0, "source": ce.EDITED_SOURCE}
    for f in ("control", "q_droop_pct", "i_max_pu", "k_gfm_min", "pf_rated", "tau_f_s"):
        assert unit[f] == before[f], f
    assert out["config"]["units"]["bess"]["droop_pct"]["value"] == 5.0
    # Other units untouched.
    assert _stored(island)["units"]["g"] == _config()["units"]["g"]


def test_a_tagged_number_takes_the_value_and_the_server_sets_its_source(island):
    ce.patch_island(island, {"requirements": {"sustained_h": 24.0, "bridge_s": 1.0}})
    req = _stored(island)["requirements"]
    assert req["sustained_h"] == {"value": 24.0, "source": ce.EDITED_SOURCE}
    # Same value sent back: still the template's assumed value.
    assert req["bridge_s"] == _a(1.0)


def test_a_client_cannot_send_a_source(island):
    with pytest.raises(HTTPException) as exc:
        ce.patch_island(
            island,
            {"requirements": {"sustained_h": {"value": 3.0, "source": "measured"}}},
        )
    assert (
        exc.value.status_code == 422 and "requirements.sustained_h" in exc.value.detail
    )
    assert "source is set by the server" in exc.value.detail
    assert _stored(island) == json.loads(json.dumps(_config()))


@pytest.mark.parametrize(
    "patch, says",
    [
        ({"preset": {"name": "grid_following"}}, "preset: cannot be changed here"),
        (
            {"requirements": "seamless"},
            "requirements: send the fields to change, by name",
        ),
    ],
)
def test_each_refusal_is_the_merge_s_own_sentence(island, patch, says):
    text = _sidecar_path(island).read_text()
    with pytest.raises(HTTPException) as exc:
        ce.patch_island(island, patch)
    assert exc.value.status_code == 422 and exc.value.detail == says, exc.value.detail
    assert _sidecar_path(island).read_text() == text


def test_an_untagged_mapping_and_a_new_field_are_set_whole(island):
    limits = _stored(island)["requirements"]["frequency_limits"]
    ce.patch_island(
        island, {"requirements": {"frequency_limits": limits, "linearised_uc": True}}
    )
    req = _stored(island)["requirements"]
    assert req["frequency_limits"] == limits
    assert req["linearised_uc"] is True


def test_lists_are_replaced_whole(island):
    ce.patch_island(island, {"requirements": {"scenarios": ["seamless"]}})
    assert _stored(island)["requirements"]["scenarios"] == ["seamless"]


@pytest.mark.parametrize(
    "patch, where",
    [
        ({"units": {"ghost": {"control": "gfl_pq"}}}, "units.ghost"),
        ({"requirements": {"sustained_h": None}}, "requirements.sustained_h"),
        ({"units": {"bess": {"droop_pct": None}}}, "units.bess.droop_pct"),
        ({"preset": "grid_following"}, "preset"),
        ({"units": {"bess": "gfm_vsm"}}, "units.bess"),
    ],
)
def test_refusals_name_the_field_and_leave_the_file(island, patch, where):
    text = _sidecar_path(island).read_text()
    with pytest.raises(HTTPException) as exc:
        ce.patch_island(island, patch)
    assert exc.value.status_code == 422 and exc.value.detail.startswith(where), (
        exc.value.detail
    )
    assert _sidecar_path(island).read_text() == text


def test_a_bad_mode_is_refused_by_the_schema_and_the_file_is_kept(island):
    text = _sidecar_path(island).read_text()
    with pytest.raises(HTTPException) as exc:
        ce.patch_island(island, {"units": {"bess": {"control": "gfm_magic"}}})
    assert exc.value.status_code == 422
    assert _sidecar_path(island).read_text() == text


def test_a_stale_base_is_409_and_the_file_is_unchanged(island):
    old = ce.get_island(island)["revision"]
    ce.patch_island(island, {"requirements": {"sustained_h": 2.0}}, base=old)
    text = _sidecar_path(island).read_text()
    with pytest.raises(HTTPException) as exc:
        ce.patch_island(island, {"requirements": {"bridge_s": 5.0}}, base=old)
    assert exc.value.status_code == 409
    assert exc.value.detail["error_kind"] == "island_config_changed"
    assert _sidecar_path(island).read_text() == text


def test_a_patch_drops_the_stale_island_study(island):
    _, study = ce._island_paths(island)
    study.write_text("{}")
    ce.patch_island(island, {"requirements": {"sustained_h": 2.0}})
    assert not study.exists()


def test_two_patches_on_different_keys_both_survive(island):
    errors = []

    def run(p):
        try:
            ce.patch_island(island, p)
        except Exception as e:  # pragma: no cover - surfaced below
            errors.append(e)

    ts = [
        threading.Thread(target=run, args=(p,))
        for p in (
            {"units": {"bess": {"droop_pct": 5.0}}},
            {"requirements": {"sustained_h": 9.0}},
        )
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors
    stored = _stored(island)
    assert stored["units"]["bess"]["droop_pct"]["value"] == 5.0
    assert stored["requirements"]["sustained_h"]["value"] == 9.0


def test_a_units_edit_survives_a_later_requirements_patch(island):
    """K4: the panel's requirements save is a PATCH, never a whole-config PUT
    carrying the units it read before."""
    ce.patch_island(island, {"units": {"bess": {"droop_pct": 5.0}}})
    ce.patch_island(island, {"requirements": {"scenarios": ["ups_bridged"]}})
    assert _stored(island)["units"]["bess"]["droop_pct"]["value"] == 5.0


def test_put_and_patch_share_one_lock(island, monkeypatch):
    sidecar, _ = ce._island_paths(island)
    seen = []
    real = ce._lock
    monkeypatch.setattr(ce, "_lock", lambda p: seen.append(str(p)) or real(p))
    ce.save_island(island, _config())
    ce.patch_island(island, {"requirements": {"sustained_h": 2.0}})
    assert seen == [str(sidecar), str(sidecar)]
