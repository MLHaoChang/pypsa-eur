"""
SC-01a, D03 rename/delete hooks for a scene-enabled Site, through the real
network CRUD routes. Order: the network edit happens first (as for the
sites.json hook); then the scene re-keys or drops the key in a NEW generation
and sites.json is never edited behind the pointer. A failed scene write leaves
the old generation and shows the gone component in `unbound_keys`.
"""
from __future__ import annotations

import pytest

from services import site_service as ss

FOOT = {"width_m": 4.0, "depth_m": 2.0, "height_m": 3.0}
SITE = {"id": "campus", "name": "Campus", "buses": ["B1"], "origin": {"lng": 11.58, "lat": 48.14},
        "boundary": [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]],
        "placements": {"Generator:gas": {"x": 5.0, "y": 5.0, "heading": 90}}}


@pytest.fixture
def adopted(client, api_project, project_storage_dir):
    from services.site_scene import store

    name = api_project("hooks")
    project = project_storage_dir(name)
    doc = {"version": 1, "sites": [SITE], "asset_footprints": {"Generator:gas": FOOT}}
    assert client.put(f"/api/projects/{name}/sites", json=doc).status_code == 200
    digest = store.legacy_site_digest(ss.read_sites(project), "campus")
    assert client.post(f"/api/projects/{name}/sites/campus/scene/adopt",
                       json={"expected_digest": digest}).status_code == 200
    return name, project


def _scene(client, name):
    return client.get(f"/api/projects/{name}/sites/campus/scene").json()


def test_rename_rekeys_inside_a_new_generation(client, adopted):
    name, project = adopted
    before = (project / ss.SITES_FILE).read_bytes()
    r = client.put("/api/network/generators/gas", json={"name": "gas2", "bus": "B1"})
    assert r.status_code == 200, r.text
    scene = _scene(client, name)
    assert scene["revision"] == 2
    assert set(scene["site"]["placements"]) == {"Generator:gas2"}
    assert set(scene["asset_footprints"]) == {"Generator:gas2"}
    assert scene["unbound_keys"] == []
    # The enabled Site's placements in sites.json were not edited behind the pointer.
    legacy = ss.read_sites(project)
    assert set(legacy["sites"][0]["placements"]) == {"Generator:gas"}
    assert before != (project / ss.SITES_FILE).read_bytes()      # only the project-level footprint record moved
    assert set(legacy["asset_footprints"]) == {"Generator:gas2"}


def test_delete_drops_placement_and_footprint_together(client, adopted):
    name, _project = adopted
    assert client.delete("/api/network/generators/gas").status_code in (200, 204)
    scene = _scene(client, name)
    assert scene["revision"] == 2
    assert scene["site"]["placements"] == {} and scene["asset_footprints"] == {}
    # A same-name recreation inherits neither position nor envelope.
    r = client.post("/api/network/generators", json={"name": "gas", "bus": "B1", "carrier": "gas"})
    assert r.status_code in (200, 201), r.text
    assert _scene(client, name)["site"]["placements"] == {}


def test_a_failed_scene_write_is_visible_not_silent(client, adopted, monkeypatch, caplog):
    from services.site_scene import store

    name, _project = adopted

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_commit", boom)
    r = client.put("/api/network/generators/gas", json={"name": "gas2", "bus": "B1"})
    assert r.status_code == 200, r.text          # the model rename stands
    scene = _scene(client, name)
    assert scene["revision"] == 1                 # the old generation is kept whole
    assert scene["unbound_keys"] == ["Generator:gas"]
    assert any("site scene rename hook failed" in m for m in caplog.messages)
