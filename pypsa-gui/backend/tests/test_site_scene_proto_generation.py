"""
SC-00 N, D03 / V54 / V59 / V64 / V65 / V79: immutable generations, the atomic
pointer, adoption, receipts, the asset_footprints takeover and the legacy-PUT
guard (tests/site_scene_proto/scene_store.py), over the real
`services.site_service` sites.json.
"""
from __future__ import annotations

import copy
import json
import threading

import pytest

from services import site_service
from tests.site_scene_proto import scene_store as store
from tests.site_scene_proto.contract import SceneError

SITE = {"id": "campus", "name": "Campus", "buses": [], "origin": {"lng": 11.58, "lat": 48.14},
        "boundary": [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]],
        "placements": {"Generator:pv": {"x": 1.0, "y": 2.0, "heading": 0.0}}}
OTHER = {"id": "yard", "name": "Yard", "buses": ["b1"], "origin": {"lng": 5.0, "lat": 52.0},
         "boundary": [[5.0, 52.0], [5.001, 52.0], [5.001, 52.001]], "placements": {}}
DOC = {"version": 1, "sites": [SITE, OTHER],
       "asset_footprints": {"Generator:pv": {"width_m": 4, "depth_m": 2, "height_m": 1.5},
                            "Store:h2": {"width_m": 3, "depth_m": 3}}}
BUILDING = {"id": "greenfield:a", "source": {"namespace": "greenfield", "source_id": "a"},
            "footprint": [[0, 0], [10, 0], [10, 20], [0, 20]], "height_m": 8, "height_evidence": "greenfield",
            "levels": None}


@pytest.fixture
def project(tmp_path):
    site_service.write_sites(tmp_path, copy.deepcopy(DOC))
    return tmp_path


def _adopt(project):
    return store.adopt(project, "campus", store.legacy_site_digest(site_service.read_sites(project), "campus"))


def _proposal(pid, base, changes):
    return {"id": pid, "base_revision": base, "input_hash": store.sha256(changes), "changes": changes}


def test_reading_never_adopts(project):
    assert store.read_scene(project, "campus") == {"state": "unadopted", "site_id": "campus"}
    assert store.enabled_site_ids(project) == set()
    assert not (project / "sites").exists()


def test_adoption_snapshots_the_exact_legacy_site_and_its_owned_footprints(project):
    before = (project / "sites.json").read_bytes()
    pointer = _adopt(project)
    scene = store.read_scene(project, "campus")
    assert pointer["revision"] == 1 and scene["state"] == "enabled"
    assert scene["site"] == SITE and scene["buildings"] == []
    # Only the footprints of equipment placed on THIS site move into the generation.
    assert scene["asset_footprints"] == {"Generator:pv": {"width_m": 4, "depth_m": 2, "height_m": 1.5}}
    assert (project / "sites.json").read_bytes() == before  # no rewrite of sites.json
    assert store.enabled_site_ids(project) == {"campus"}


def test_adoption_refuses_a_stale_digest_and_a_second_adoption(project):
    with pytest.raises(SceneError) as e:
        store.adopt(project, "campus", "0" * 64)
    assert e.value.kind == "revision_conflict"
    _adopt(project)
    with pytest.raises(SceneError) as e:
        _adopt(project)
    assert e.value.kind == "scene_managed_site"


def test_buildings_only_save_and_resume_without_any_model(project):
    # V79: apply buildings, "reload" (fresh read), the same Site and generation chain resume.
    _adopt(project)
    receipt = store.apply(project, "campus", _proposal("p1", 1, {"buildings": [BUILDING]}), 1)
    again = store.read_scene(project, "campus")
    assert receipt == {"proposal_id": "p1", "input_hash": store.sha256({"buildings": [BUILDING]}),
                       "revision": 2, "changed": ["buildings"]}
    assert again["revision"] == 2 and again["buildings"] == [BUILDING] and again["site"]["buses"] == []
    assert again["last_receipt"] == receipt


def test_reapplying_returns_the_committed_receipt_and_a_changed_input_is_refused(project):
    _adopt(project)
    changes = {"buildings": [BUILDING]}
    first = store.apply(project, "campus", _proposal("p1", 1, changes), 1)
    # V65: a disconnect after commit is reconciled by reading the receipt back; no second write.
    assert store.apply(project, "campus", _proposal("p1", 1, changes), 1) == first
    assert store.read_scene(project, "campus")["revision"] == 2
    with pytest.raises(SceneError, match="different inputs"):
        store.apply(project, "campus", _proposal("p1", 1, {"buildings": []}), 1)


@pytest.mark.parametrize("expected,base", [(0, 1), (2, 1), (1, 0), (1, 2)])
def test_a_stale_expected_or_base_revision_is_refused(project, expected, base):
    # V64: a preview against another base never applies.
    _adopt(project)
    with pytest.raises(SceneError) as e:
        store.apply(project, "campus", _proposal("p", base, {"buildings": [BUILDING]}), expected)
    assert e.value.kind == "revision_conflict" and e.value.detail == {"current_revision": 1}


def test_changes_outside_the_scene_are_refused(project):
    _adopt(project)
    with pytest.raises(SceneError):
        store.apply(project, "campus", _proposal("p", 1, {"network": {"Bus": ["x"]}}), 1)


def test_a_crash_before_the_pointer_commit_keeps_the_old_generation(project):
    # V59.
    _adopt(project)
    before = store.read_scene(project, "campus")
    with pytest.raises(OSError, match="before the pointer"):
        store.apply(project, "campus", _proposal("p1", 1, {"buildings": [BUILDING]}), 1, crash="before_commit")
    assert store.read_scene(project, "campus") == before
    # The orphan generation exists on disk but is not in the closure.
    gens = list((store.scene_dir(project, "campus") / "generations").iterdir())
    assert len(gens) == 2 and len(store.closure(project, "campus")) == 2
    # The retry succeeds normally.
    assert store.apply(project, "campus", _proposal("p1", 1, {"buildings": [BUILDING]}), 1)["revision"] == 2


def test_a_crash_after_the_pointer_commit_is_read_back_not_reapplied(project):
    _adopt(project)
    with pytest.raises(OSError, match="after the pointer"):
        store.apply(project, "campus", _proposal("p1", 1, {"buildings": [BUILDING]}), 1, crash="after_commit")
    scene = store.read_scene(project, "campus")
    assert scene["revision"] == 2 and scene["last_receipt"]["proposal_id"] == "p1"
    # The client's retry with its old expected revision returns the receipt.
    assert store.apply(project, "campus", _proposal("p1", 1, {"buildings": [BUILDING]}), 1)["revision"] == 2


def test_a_tampered_generation_is_an_integrity_error(project):
    _adopt(project)
    rel = store.closure(project, "campus")[1]
    data = json.loads((project / rel).read_text())
    data["site"]["name"] = "Changed"
    (project / rel).write_text(json.dumps(data))
    with pytest.raises(SceneError) as e:
        store.read_scene(project, "campus")
    assert e.value.kind == "integrity_error"


def test_concurrent_applies_on_one_revision_let_exactly_one_win(project):
    # V54: gizmo save vs proposal Apply racing on the same expected revision.
    _adopt(project)
    results: list[object] = []
    barrier = threading.Barrier(8)

    def worker(i):
        barrier.wait()
        try:
            results.append(store.apply(project, "campus", _proposal(f"p{i}", 1, {"buildings": [{**BUILDING, "levels": i}]}), 1))
        except SceneError as e:
            results.append(e.kind)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(isinstance(r, dict) for r in results) == 1
    assert results.count("revision_conflict") == 7
    assert store.read_scene(project, "campus")["revision"] == 2


# ── the legacy sites.json seam (#712 asset_footprints takeover) ─────────────

def test_get_projects_enabled_sites_from_the_generation_in_one_shape(project):
    _adopt(project)
    store.apply(project, "campus", _proposal("p", 1, {
        "placements": {"Generator:pv": {"x": 9.0, "y": 9.0, "heading": 90.0}},
        "asset_footprints": {"Generator:pv": {"width_m": 5, "depth_m": 2, "height_m": 1.5}}}), 1)
    got = store.project_sites(project)
    assert got["sites"][0]["placements"]["Generator:pv"] == {"x": 9.0, "y": 9.0, "heading": 90.0}
    assert got["sites"][1] == OTHER
    assert got["asset_footprints"]["Generator:pv"]["width_m"] == 5
    assert got["asset_footprints"]["Store:h2"] == {"width_m": 3, "depth_m": 3}
    site_service.validate_sites(got)  # the same record shape the legacy client reads


def test_legacy_put_echoing_the_projection_is_accepted(project):
    _adopt(project)
    store.guard_legacy_put(project, store.project_sites(project))


@pytest.mark.parametrize("mutate", [
    lambda d: d["sites"][0]["placements"].update({"Generator:pv": {"x": 0, "y": 0, "heading": 0}}),
    lambda d: d["sites"].pop(0),
    lambda d: d["asset_footprints"].update({"Generator:pv": {"width_m": 1, "depth_m": 1}}),
    lambda d: d["asset_footprints"].pop("Generator:pv"),
])
def test_legacy_put_that_changes_an_enabled_site_is_refused(project, mutate):
    # V54: a stale tab's debounce or unload whole-document PUT after adoption.
    _adopt(project)
    stale = store.project_sites(project)
    mutate(stale)
    with pytest.raises(SceneError) as e:
        store.guard_legacy_put(project, stale)
    assert e.value.kind == "scene_managed_site" and e.value.status == 409


def test_a_client_scene_enabled_flag_is_ignored(project):
    _adopt(project)
    doc = store.project_sites(project)
    doc["sites"][0]["scene_enabled"] = False
    doc["sites"][0]["name"] = "Renamed by a stale tab"
    with pytest.raises(SceneError):
        store.guard_legacy_put(project, doc)
    # And unadopted sites keep their legacy freedom.
    ok = store.project_sites(project)
    ok["sites"][1]["name"] = "Yard 2"
    ok["asset_footprints"]["Store:h2"] = {"width_m": 9, "depth_m": 9}
    store.guard_legacy_put(project, ok)


def test_rename_and_delete_hooks_rekey_inside_a_new_generation(project):
    _adopt(project)
    legacy_before = (project / "sites.json").read_bytes()
    assert store.rename_component(project, "Generator", "pv", "pv_roof") == ["campus"]
    scene = store.read_scene(project, "campus")
    assert scene["revision"] == 2
    assert list(scene["site"]["placements"]) == ["Generator:pv_roof"]
    assert list(scene["asset_footprints"]) == ["Generator:pv_roof"]
    assert store.remove_component(project, "Generator", "pv_roof") == ["campus"]
    scene = store.read_scene(project, "campus")
    assert scene["revision"] == 3 and scene["site"]["placements"] == {} and scene["asset_footprints"] == {}
    assert store.rename_component(project, "Generator", "absent", "x") == []
    assert (project / "sites.json").read_bytes() == legacy_before  # never edited behind the pointer
