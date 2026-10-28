# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""
SC-02a: `place_existing` (V17 backend half, V20, V21). A placement is a scene
change only; the native model and its inputs never move with it. Plan:
`.scratch/site-scene-generation/sc-02/plan.md`.
"""
from __future__ import annotations

import pytest

from services import site_service as ss
from tests.test_site_scene_associations import A, NAME, _network, _apply, _detail, _get, _propose, _table, make_scene


@pytest.fixture
def scene(client, install_network, project_storage_dir):
    return make_scene(client, install_network, project_storage_dir)

MODEL = ("buses", "generators", "loads", "links", "lines", "stores", "storage_units")


def _model(client):
    return {attr: _table(client, attr) for attr in MODEL}


def test_placing_existing_equipment_changes_the_scene_only(client, scene):
    nc = sorted(scene.rglob("network.nc"))
    nc_bytes = [p.read_bytes() for p in nc]
    before = _model(client)
    p = _propose(client, "place_existing", {"placements": {
        "StorageUnit:bess": {"x": 60.0, "y": -50.0, "heading": 0}}})
    _apply(client, p)
    got = _get(client)
    assert got["site"]["placements"]["StorageUnit:bess"]["x"] == 60.0
    assert "StorageUnit:bess" not in got["unbound_keys"]
    assert _model(client) == before
    assert [p.read_bytes() for p in nc] == nc_bytes


def test_placing_a_component_the_model_lacks_is_refused(client, scene):
    r = _propose(client, "place_existing", {"placements": {"Generator:ghost": {"x": 0, "y": 0, "heading": 0}}},
                 ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "unknown_target"


def test_pv_goes_on_an_eligible_roof_and_other_kinds_do_not(client, scene):
    p = _propose(client, "place_existing", {"placements": {
        "Generator:solar": {"x": 30.0, "y": -60.0, "heading": 0, "on": f"roof:{A}"}}})
    assert p["preview"]["findings"] == []
    _apply(client, p)
    assert _get(client)["site"]["placements"]["Generator:solar"]["on"] == f"roof:{A}"
    r = _propose(client, "place_existing", {"placements": {
        "StorageUnit:bess": {"x": 30.0, "y": -60.0, "heading": 0, "on": f"roof:{A}"}}}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "not_roof_eligible"
    r = _propose(client, "place_existing", {"placements": {
        "Generator:solar": {"x": 0.0, "y": 0.0, "heading": 0, "on": f"roof:{A}"}}}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "off_roof"


def test_a_ground_placement_inside_a_building_is_a_finding_naming_it(client, scene):
    p = _propose(client, "place_existing", {"placements": {
        "StorageUnit:bess": {"x": 30.0, "y": -60.0, "heading": 0}}})
    (f,) = p["preview"]["findings"]
    assert f["code"] == "overlap" and f["severity"] == "warn"
    assert f["objects"] == ["StorageUnit:bess", A]
    _apply(client, p)                                                    # a finding, not a refusal


def test_a_failed_placement_after_a_create_is_retried_without_a_second_create(client, scene, monkeypatch):
    from services.site_scene import store

    r = client.post("/api/network/generators", json={"name": "pv_new", "bus": "B1", "carrier": "solar"})
    assert r.status_code in (200, 201), r.text
    place = {"placements": {"Generator:pv_new": {"x": 60.0, "y": -50.0, "heading": 0}}}
    p = _propose(client, "place_existing", place)

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_commit", boom)
    with pytest.raises(OSError):
        _apply(client, p, ok=False)
    monkeypatch.undo()
    assert "Generator:pv_new" not in _get(client)["site"]["placements"]
    uid = ss.read_sites(scene)["bindings"]["Generator:pv_new"]              # recorded before the failed commit
    _apply(client, p)                                                    # the retry: placement only
    assert ss.read_sites(scene)["bindings"]["Generator:pv_new"] == uid      # the same instance, not a second
    assert [g["name"] for g in _table(client, "generators")].count("pv_new") == 1
    assert list(_get(client)["site"]["placements"]).count("Generator:pv_new") == 1


@pytest.mark.parametrize("derive", [False, True])
def test_a_visual_move_leaves_line_lengths_whatever_the_length_setting(client, scene, derive):
    assert client.post("/api/network/buses", json={"name": "B2", "x": 11.58, "y": 48.14}).status_code in (200, 201)
    r = client.post("/api/network/lines", json={"name": "ln", "bus0": "B1", "bus1": "B2", "length": 1.5,
                                                "x": 0.1, "s_nom": 10.0})
    assert r.status_code in (200, 201), r.text
    r = client.patch(f"/api/projects/{NAME}/settings", json={"derive_lengths_from_geometry": derive})
    assert r.status_code == 200, r.text
    p = _propose(client, "place_existing", {"placements": {"Bus:B2": {"x": 60.0, "y": -50.0, "heading": 0}}})
    _apply(client, p)
    assert [row["length"] for row in _table(client, "lines") if row["name"] == "ln"] == [1.5]


def test_a_stale_bound_placement_is_refused_until_the_user_rebinds_it(client, scene, install_network):
    _apply(client, _propose(client, "place_existing", {"placements": {
        "StorageUnit:bess": {"x": 60.0, "y": -50.0, "heading": 0}}}))
    install_network(_network())                                          # a foreign network, same names ...
    assert client.post(f"/api/projects/{NAME}", params={"force": True}).status_code == 200
    move = {"placements": {"StorageUnit:bess": {"x": 61.0, "y": -50.0, "heading": 0}}}
    r = _propose(client, "place_existing", move, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "unbound_target"   # never re-bound by name
    r = client.post("/api/network/storage_units", json={"name": "bess2", "bus": "B1", "p_nom": 1.0})
    assert r.status_code in (200, 201), r.text
    _apply(client, _propose(client, "rebind", {"placement": "StorageUnit:bess", "confirmed": True,
                                               "target": {"class": "StorageUnit", "name": "bess2"}}))
    placed, bindings = _get(client)["site"]["placements"], ss.read_sites(scene)["bindings"]
    assert "StorageUnit:bess2" in placed and "StorageUnit:bess" not in placed
    assert placed["StorageUnit:bess2"]["x"] == 60.0                         # the same placement, re-keyed
    assert "StorageUnit:bess" not in bindings and isinstance(bindings["StorageUnit:bess2"], str)
