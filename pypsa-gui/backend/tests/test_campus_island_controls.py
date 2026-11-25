"""CS-B PR 3 (control-strategy issue 02): the section-5 Controls block's
server side. GET /island carries gridspine's control objects and each
converter's checked block (GK B3 K7, K14); a unit PATCH that names a new
``control`` makes the mode switch on the server (K8, K15-K17, K20)."""

from __future__ import annotations

import copy
import json

import pytest
from fastapi import HTTPException

from gridspine.schema import island as schema
from services import campus_electrical_service as ce
from services.adequacy import island_config
from tests.test_campus_island_service import (
    _a,
    _config,
    _sidecar_path,
    project,  # noqa: F401
    user_and_db,  # noqa: F401
)

_PV = {"kind": "pv", "control": "gfl_qu", "pf_rated": _a(0.9),
       "qu_points": _a([[0.9, 0.33], [1.1, -0.33]]), "neg_seq": _a(0.1)}


def _stored(project):  # noqa: F811
    return json.loads(_sidecar_path(project).read_text())


def _cfg(preset=None, **units):
    cfg = _config()
    cfg["units"]["bess"]["neg_seq"] = _a(0.2)
    cfg["units"]["pv"] = copy.deepcopy(_PV)
    cfg["units"].update(units)
    if preset is not None:
        cfg["preset"] = preset
        # Under the preset the bess and pv keep only what the preset does not set.
        for name in ("bess", "pv"):
            u = cfg["units"][name]
            if u["kind"] in schema.PRESETS[preset]["kinds"]:
                cfg["units"][name] = {k: v for k, v in u.items() if k in ("kind", "neg_seq")}
    return cfg


@pytest.fixture
def island(project):  # noqa: F811
    ce.save_island(project, _cfg())
    return project


@pytest.fixture
def preset_island(project):  # noqa: F811
    ce.save_island(project, _cfg(preset="grid_forming_support"))
    return project


# ── GET (K7, K14) ───────────────────────────────────────────────────────────

def test_get_serves_the_schema_s_own_control_objects(island):
    body = ce.get_island(island)
    assert body["controls"] == schema.CONTROLS and body["controls"] is island_config.CONTROLS
    assert body["control_fields"] == schema._CONTROL_FIELDS
    assert body["control_fields"] is island_config.CONTROL_FIELDS is schema._CONTROL_FIELDS
    assert body["pf_choice"] == schema._PF_CHOICE


def test_unit_controls_are_the_checked_converter_blocks(island):
    uc = ce.get_island(island)["unit_controls"]
    assert set(uc) == {"bess", "pv"}            # no genset, no load
    want = schema.validate_island_config(_cfg())["units"]["bess"]
    assert uc["bess"] == {**want, "preset_scope": None}
    assert uc["pv"]["control"] == "gfl_qu" and uc["pv"]["sources"]["pf_rated"] == "assumed"


def test_unit_controls_name_the_preset_that_applied(preset_island):
    uc = ce.get_island(preset_island)["unit_controls"]
    assert uc["bess"]["control"] == "gfm_vsm"
    assert uc["bess"]["preset"] == "grid_forming_support" and uc["bess"]["preset_scope"] == "project"
    assert uc["pv"]["preset_scope"] is None     # the preset sets a bess only


def test_a_unit_s_own_preset_reads_as_its_own(project):  # noqa: F811
    cfg = _cfg()
    cfg["units"]["pv"] = {"kind": "pv", "preset": "voltage_support"}
    ce.save_island(project, cfg)
    assert ce.get_island(project)["unit_controls"]["pv"]["preset_scope"] == "unit"


def test_a_ups_unit_is_listed(project):  # noqa: F811
    cfg = _cfg(ups={"kind": "ups", "it_load": ["crit"], "walk_in_s": _a(10.0), "f_window_hz": _a(2.0)})
    ce.save_island(project, cfg)
    ups = ce.get_island(project)["unit_controls"]["ups"]
    assert ups["values"] == {"walk_in_s": 10.0, "f_window_hz": 2.0}
    assert ups["transfers_on_islanding"] is False


def test_an_invalid_sidecar_has_no_unit_controls_and_no_500(island):
    raw = _stored(island)
    raw["units"]["bess"]["control"] = "gfm_magic"
    _sidecar_path(island).write_text(json.dumps(raw))
    body = ce.get_island(island)
    assert body["unit_controls"] is None and body["config"] == raw
    assert body["controls"] == schema.CONTROLS      # the block can still name the modes


def test_unit_controls_come_from_the_same_read_as_the_revision(island, monkeypatch):
    """One read: a file changed between reads cannot pair new controls with an old revision."""
    import hashlib

    calls = []
    real = type(_sidecar_path(island)).read_bytes

    def read_bytes(self):
        calls.append(self)
        return real(self)

    monkeypatch.setattr(type(_sidecar_path(island)), "read_bytes", read_bytes)
    body = ce.get_island(island)
    assert [c for c in calls if c.name == _sidecar_path(island).name] == [_sidecar_path(island)]
    assert body["revision"] == hashlib.sha256(_sidecar_path(island).read_bytes()).hexdigest()


# ── a same mode, or another field: nothing moves (K15) ──────────────────────

def test_a_same_mode_patch_leaves_the_unit_byte_equal(island):
    before = _stored(island)
    ce.patch_island(island, {"units": {"bess": {"control": "gfm_droop"}}})
    assert _stored(island) == before


def test_a_same_mode_patch_under_a_preset_writes_no_control(preset_island):
    before = _stored(preset_island)
    ce.patch_island(preset_island, {"units": {"bess": {"control": "gfm_vsm", "droop_pct": 5.0}}})
    after = _stored(preset_island)
    assert "control" not in after["units"]["bess"] and "preset" not in after["units"]["bess"]
    assert after["units"]["bess"]["droop_pct"] == {"value": 5.0, "source": ce.EDITED_SOURCE}
    after["units"]["bess"].pop("droop_pct")
    assert after == before


def test_another_field_under_a_preset_neither_materialises_nor_opts_out(preset_island):
    before = _stored(preset_island)
    ce.patch_island(preset_island, {"units": {"bess": {"i_max_pu": 1.3}}})
    after = _stored(preset_island)
    assert set(after["units"]["bess"]) == {"kind", "neg_seq", "i_max_pu"}
    assert after["preset"] == before["preset"]


# ── a switch (K8, K15-K17) ─────────────────────────────────────────────────

def test_a_switch_drops_the_old_mode_and_keeps_the_rest_byte_for_byte(island):
    before = _stored(island)
    ce.patch_island(island, {"units": {"bess": {"control": "gfl_pq"}}})
    after = _stored(island)
    bess = after["units"]["bess"]
    assert bess == {"kind": "bess", "control": "gfl_pq", "pf_rated": before["units"]["bess"]["pf_rated"],
                    "neg_seq": before["units"]["bess"]["neg_seq"]}
    for name in ("pv", "g", "crit"):
        assert after["units"][name] == before["units"][name], name
    assert after["requirements"] == before["requirements"]
    assert "preset" not in after and "preset" not in bess


def test_a_switch_fills_the_new_mode_from_its_preset_tagged_assumed(island):
    ce.patch_island(island, {"units": {"bess": {"control": "gfm_vsm"}}})
    bess = _stored(island)["units"]["bess"]
    assert bess["control"] == "gfm_vsm"
    assert bess["H_v_s"] == {"value": 4.0, "source": "assumed"}     # from grid_forming_support
    assert "tau_f_s" not in bess                                      # droop only
    for f in ("droop_pct", "q_droop_pct", "i_max_pu", "k_gfm_min", "pf_rated"):
        assert bess[f] == _cfg()["units"]["bess"][f], f              # kept, not refilled


def test_a_patch_value_wins_over_the_fill_and_takes_the_edited_source(island):
    ce.patch_island(island, {"units": {"bess": {"control": "gfm_vsm", "H_v_s": 6.0}}})
    assert _stored(island)["units"]["bess"]["H_v_s"] == {"value": 6.0, "source": ce.EDITED_SOURCE}


def test_the_fill_respects_the_kind_and_a_missing_field_is_a_422_naming_it(island):
    """grid_forming_support runs gfm_vsm on a bess only: a pv gets nothing filled."""
    text = _sidecar_path(island).read_text()
    with pytest.raises(HTTPException) as exc:
        ce.patch_island(island, {"units": {"pv": {"control": "gfm_vsm"}}})
    assert exc.value.status_code == 422
    assert "pv" in str(exc.value.detail) and "H_v_s" in str(exc.value.detail)
    assert _sidecar_path(island).read_text() == text


def test_a_mode_no_preset_runs_saves_when_the_patch_gives_its_fields(island):
    ce.patch_island(island, {"units": {"pv": {"control": "gfm_droop", "droop_pct": 3.0, "q_droop_pct": 3.0,
                                              "tau_f_s": 0.1, "i_max_pu": 1.2, "k_gfm_min": 1.0}}})
    pv = _stored(island)["units"]["pv"]
    assert pv["control"] == "gfm_droop" and "qu_points" not in pv
    assert pv["neg_seq"] == _PV["neg_seq"] and pv["pf_rated"] == _PV["pf_rated"]
    assert pv["tau_f_s"] == {"value": 0.1, "source": ce.EDITED_SOURCE}


def test_fast_frequency_response_s_rating_scaled_fields_are_a_422(island):
    """K16: the FFR fields that scale with the rating are not filled (no network read under the lock)."""
    with pytest.raises(HTTPException) as exc:
        ce.patch_island(island, {"units": {"bess": {"control": "gfl_fw"}}})
    assert exc.value.status_code == 422
    for f in schema.FFR_SIZED:
        assert f in str(exc.value.detail), f
    ce.patch_island(island, {"units": {"bess": {"control": "gfl_fw", "fw_k_mw_per_hz": 10.0,
                                                "fw_ramp_mw_per_s": 5.0, "fw_p_limit_mw": 3.0}}})
    bess = _stored(island)["units"]["bess"]
    assert bess["fw_deadband_hz"] == {"value": 0.2, "source": "assumed"}   # from the FFR preset
    assert "droop_pct" not in bess


def test_gfl_pf_keeps_its_power_factor_choice_and_others_drop_it(island):
    ce.patch_island(island, {"units": {"pv": {"control": "gfl_pf", "cos_phi": 0.95}}})
    assert _stored(island)["units"]["pv"]["cos_phi"] == {"value": 0.95, "source": ce.EDITED_SOURCE}
    ce.patch_island(island, {"units": {"pv": {"control": "gfl_pq"}}})
    assert "cos_phi" not in _stored(island)["units"]["pv"]


def test_a_switch_under_the_project_preset_opts_out_this_unit_only(project):  # noqa: F811
    cfg = _cfg(preset="voltage_support")
    ce.save_island(project, cfg)
    before = _stored(project)
    ce.patch_island(project, {"units": {"bess": {"control": "gfm_droop", "tau_f_s": 0.1, "droop_pct": 4.0,
                                                 "q_droop_pct": 4.0, "i_max_pu": 1.2, "k_gfm_min": 1.0}}})
    after = _stored(project)
    bess = after["units"]["bess"]
    assert bess["preset"] is None and bess["control"] == "gfm_droop"
    assert bess["pf_rated"] == {"value": 0.9, "source": "assumed"}          # materialised from the preset
    assert "qu_points" not in bess                                          # the old mode's, dropped
    assert after["preset"] == "voltage_support"
    assert after["units"]["pv"] == before["units"]["pv"]
    uc = ce.get_island(project)["unit_controls"]
    assert uc["bess"].get("preset") is None and uc["pv"]["preset"] == "voltage_support"


def test_a_client_cannot_opt_out_with_null(preset_island):
    text = _sidecar_path(preset_island).read_text()
    with pytest.raises(HTTPException) as exc:
        ce.patch_island(preset_island, {"units": {"bess": {"preset": None}}})
    assert exc.value.status_code == 422
    assert _sidecar_path(preset_island).read_text() == text


def test_a_client_source_is_still_refused_on_a_switch(island):
    with pytest.raises(HTTPException) as exc:
        ce.patch_island(island, {"units": {"bess": {"control": "gfm_vsm",
                                                    "H_v_s": {"value": 4.0, "source": "measured"}}}})
    assert exc.value.status_code == 422


@pytest.mark.parametrize("patch,where", [
    ({"units": {"bess": {"control": "gfm_magic"}}}, "units.bess.control"),
    ({"units": {"g": {"control": "gfm_droop"}}}, "units.g.control"),
])
def test_a_bad_mode_is_a_422_naming_the_field(island, patch, where):
    text = _sidecar_path(island).read_text()
    with pytest.raises(HTTPException) as exc:
        ce.patch_island(island, patch)
    assert exc.value.status_code == 422 and str(exc.value.detail).startswith(where)
    assert _sidecar_path(island).read_text() == text


def test_a_switch_with_a_stale_base_is_409_and_the_file_is_kept(island):
    old = ce.get_island(island)["revision"]
    ce.patch_island(island, {"requirements": {"sustained_h": 2.0}}, base=old)
    text = _sidecar_path(island).read_text()
    with pytest.raises(HTTPException) as exc:
        ce.patch_island(island, {"units": {"bess": {"control": "gfl_pq"}}}, base=old)
    assert exc.value.status_code == 409
    assert _sidecar_path(island).read_text() == text


def test_the_switch_runs_after_the_base_check_under_the_lock(island, monkeypatch):
    """K20: (a)-(d) happen inside patch_island's lock, after _check_base."""
    order = []
    real_lock, real_check, real_switch = ce._lock, ce._check_base, ce._switch_control

    class Spy:
        def __init__(self, path):
            self.inner = real_lock(path)

        def __enter__(self):
            order.append("lock")
            return self.inner.__enter__()

        def __exit__(self, *a):
            order.append("unlock")
            return self.inner.__exit__(*a)

    monkeypatch.setattr(ce, "_lock", Spy)
    monkeypatch.setattr(ce, "_check_base", lambda *a: (order.append("base"), real_check(*a))[1])
    monkeypatch.setattr(ce, "_switch_control", lambda *a: (order.append("switch"), real_switch(*a))[1])
    ce.patch_island(island, {"units": {"bess": {"control": "gfl_pq"}}})
    assert order[:3] == ["lock", "base", "switch"] and order[-1] == "unlock"


def test_the_fill_never_overwrites_a_kept_value_that_differs_from_the_preset(island):
    """(c) fills only what the unit lacks: a kept droop of 5 % (the preset says 4) stays, with its source."""
    ce.patch_island(island, {"units": {"bess": {"droop_pct": 5.0}}})
    ce.patch_island(island, {"units": {"bess": {"control": "gfm_vsm"}}})
    assert _stored(island)["units"]["bess"]["droop_pct"] == {"value": 5.0, "source": ce.EDITED_SOURCE}


def test_a_patch_value_equal_to_the_preset_s_is_still_the_user_s(island):
    """K17: a value the patch sends takes EDITED_SOURCE even when it equals the preset default."""
    ce.patch_island(island, {"units": {"bess": {"control": "gfm_vsm", "H_v_s": 4.0}}})
    assert _stored(island)["units"]["bess"]["H_v_s"] == {"value": 4.0, "source": ce.EDITED_SOURCE}
