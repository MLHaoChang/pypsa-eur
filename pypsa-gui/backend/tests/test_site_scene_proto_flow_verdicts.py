"""
SC-00 N, D02 + "SC-00 N UI contract" / V56 / V58 / V73 / V74 / V79: the
additive /flow scene verdicts (tests/site_scene_proto/flow_verdicts.py), and
the real `GET /api/projects/{name}/flow` left unchanged by anchor and scene
files on an empty, geometry-first project (V53).
"""
from __future__ import annotations

import copy

import pandas as pd
import pypsa
import pytest

from services import site_service
from tests.site_scene_proto import anchor, scene_store
from tests.site_scene_proto import flow_verdicts as fv

ALL_ACTIONS = ["open_site_creation", "create_plot", "generate_native", "assign_native", "show_connectivity",
               "play_results", "export_scene", "apply_proposal", "process_layout", "reconstruct_photos",
               "generate_blender"]
BASE = dict(saved=True, can_edit=True, anchor_confirmed=True, site="none", boundary=False, buildings=0,
            targets=0, native_runs=0, pending_proposal=False)


def _by(verdicts):
    return {v["action"]: v for v in verdicts}


# ── UI contract rule 3: exactly one label per state, exact strings ──────────

@pytest.mark.parametrize("state,expect", [
    (dict(saved=True, can_edit=True, anchor_confirmed=False, site="none"),
     ("Create 3D site", False, "Add site coordinates to create a 3D site")),
    (dict(saved=False, can_edit=True, anchor_confirmed=False, site="none"), ("Save project first", True, None)),
    (dict(saved=False, can_edit=True, anchor_confirmed=True, site="accepted"), ("Save project first", True, None)),
    (dict(saved=True, can_edit=True, anchor_confirmed=True, site="none"), ("Create 3D site", True, None)),
    (dict(saved=True, can_edit=True, anchor_confirmed=True, site="unfinished"), ("Resume site setup", True, None)),
    (dict(saved=True, can_edit=True, anchor_confirmed=True, site="accepted"), ("Open 3D site", True, None)),
    (dict(saved=True, can_edit=False, anchor_confirmed=True, site="accepted"), ("Open 3D site", True, None)),
    (dict(saved=True, can_edit=False, anchor_confirmed=True, site="none"),
     ("Create 3D site", False, "Read-only project: ask an editor to create a 3D site")),
])
def test_entry_shows_one_label_per_state(state, expect):
    e = fv.entry(**state)
    label, enabled, reason = expect
    assert (e["label"], e["enabled"]) == (label, enabled)
    assert (e["blocker"] or {}).get("reason") == reason


def test_the_disabled_create_links_to_the_coordinate_field_in_both_modes():
    b = fv.entry(saved=True, can_edit=True, anchor_confirmed=False, site="none")["blocker"]
    assert b == {"code": "site_anchor_missing", "reason": "Add site coordinates to create a 3D site", "link": "›",
                 "home": {"guided": {"section": "project", "drawer": "site_anchor", "open": "request_then_scroll"},
                          "expert": {"panel": "horizon"}}}


def test_save_first_points_at_the_header_save_not_a_panel():
    e = fv.entry(saved=False, can_edit=True, anchor_confirmed=True, site="none")
    assert e["target"] == {"header_action": "save"}
    assert "guided" not in e["target"] and "expert" not in e["target"]


def test_every_reason_uses_a_colon_never_an_em_dash():
    reasons = [getattr(fv, n) for n in dir(fv) if n.startswith("REASON_")]
    assert len(reasons) == 9
    for r in reasons:
        assert "—" not in r and "–" not in r
    assert fv.REASON_NO_TARGETS == "Buildings only: configure model elements to map them"


# ── per-action verdicts ─────────────────────────────────────────────────────

def test_every_action_has_a_verdict_and_blocked_ones_name_a_reason_and_home():
    verdicts = fv.actions(**BASE)
    assert [v["action"] for v in verdicts] == ALL_ACTIONS
    for v in verdicts:
        assert v["computes"]
        if not v["available"]:
            assert v["blocker"]["code"] and v["blocker"]["reason"] and v["blocker"]["home"]
            assert v["blocker"]["link"] == "›"


def test_geometry_first_needs_no_bus_and_stages_are_separate():
    # V74: anchor only -> open campus mapping; create needs a boundary; generate needs a Site.
    v = _by(fv.actions(**BASE))
    assert v["open_site_creation"]["available"] and not v["create_plot"]["available"]
    assert v["create_plot"]["blocker"]["code"] == "boundary_missing"
    v = _by(fv.actions(**{**BASE, "boundary": True}))
    assert v["create_plot"]["available"] and not v["generate_native"]["available"]
    v = _by(fv.actions(**{**BASE, "boundary": True, "site": "unfinished"}))
    assert v["generate_native"]["available"]


@pytest.mark.parametrize("change,code", [({"saved": False}, "save_first"), ({"can_edit": False}, "read_only")])
def test_unsaved_or_read_only_refuses_every_mutation(change, code):
    v = _by(fv.actions(**{**BASE, "boundary": True, "site": "unfinished", "buildings": 3, "targets": 2,
                          "pending_proposal": True, **change}))
    for name in ("open_site_creation", "create_plot", "generate_native", "assign_native", "apply_proposal"):
        assert not v[name]["available"] and v[name]["blocker"]["code"] == code


def test_buildings_only_is_a_valid_stop_with_an_in_place_link():
    # V79: no Loads/assets/processes -> assignment explains itself; generation still works.
    v = _by(fv.actions(**{**BASE, "boundary": True, "site": "accepted", "buildings": 4}))
    assert v["generate_native"]["available"]
    assert v["assign_native"]["blocker"] == {"code": "no_model_targets", "reason":
                                            "Buildings only: configure model elements to map them", "link": "›",
                                            "home": fv.HOME_LOADS}
    assert _by(fv.actions(**{**BASE, "buildings": 4, "targets": 1}))["assign_native"]["available"]


@pytest.mark.parametrize("engines", [frozenset(), frozenset({"blender", "colmap", "cuda"})])
def test_optional_engines_never_enable_and_never_block_native(engines):
    # V58: installed or not, nothing is silently enabled; native actions are unaffected.
    v = _by(fv.actions(**{**BASE, "boundary": True, "site": "unfinished"}, installed_engines=engines))
    for name in fv.OPTIONAL_ENGINE_ACTIONS:
        assert not v[name]["available"] and v[name]["blocker"]["code"] == "worker_unavailable"
    assert v["generate_native"]["available"]


# ── the real /flow is unchanged (V53, V56: electrical run_ready keeps its value) ──

def _empty_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=24, freq="h"))
    return n


def test_anchor_and_scene_files_leave_the_real_flow_unchanged(client, install_network, tmp_projects_dir,
                                                               project_storage_dir):
    install_network(_empty_network(), name="geo")
    assert client.post("/api/projects/geo", params={"force": True, "rebind": True}).status_code == 200
    project = project_storage_dir("geo")
    assert (project / "network.nc").exists()
    before = client.get("/api/projects/geo/flow").json()

    anchor.write_anchor(project, {"lat": 48.14, "lng": 11.58, "source": "manual", "confirmed": True}, 0)
    site = {"id": "campus", "name": "Campus", "buses": [], "origin": {"lng": 11.58, "lat": 48.14},
            "boundary": [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]], "placements": {}}
    site_service.write_sites(project, {"version": 1, "sites": [copy.deepcopy(site)]})
    scene_store.adopt(project, "campus", scene_store.legacy_site_digest(site_service.read_sites(project), "campus"))

    after = client.get("/api/projects/geo/flow").json()
    # SC-01a adds only the `scene` key; every electrical key keeps its value.
    assert {k: v for k, v in after.items() if k != "scene"} == {k: v for k, v in before.items() if k != "scene"}
    assert before["scene"]["entry"]["label"] == "Create 3D site" and not before["scene"]["entry"]["enabled"]
    assert after["scene"]["entry"]["label"] == "Resume site setup"
    # The geometry-first Site has no buses and the network stays empty.
    assert site_service.read_sites(project)["sites"][0]["buses"] == []
