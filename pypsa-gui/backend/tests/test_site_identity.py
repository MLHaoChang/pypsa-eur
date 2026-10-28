"""
D06 native target lifecycle identity, through the real routes. A placement or
footprint key (`<Class>:<name>`) binds to one component INSTANCE: the uid that
`services.component_identity` keeps in the network's own meta. The uid moves
with the network through rename, delete, undo, Save-As, scenario copy and
replace, so a stale key never aliases a same-name component, whatever a
sidecar hook did.
"""
from __future__ import annotations

import pytest

from services import site_service as ss

FOOT = {"width_m": 4.0, "depth_m": 2.0}
PLACE = {"x": 5.0, "y": 5.0, "heading": 90}
SITE = {"id": "campus", "name": "Campus", "buses": ["B1"], "origin": {"lng": 11.58, "lat": 48.14},
        "boundary": [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]],
        "placements": {"Generator:gas": PLACE}}
DOC = {"version": 1, "sites": [SITE], "asset_footprints": {"Generator:gas": FOOT}}
KEY = "Generator:gas"


@pytest.fixture
def placed(client, api_project, project_storage_dir):
    name = api_project("ident")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    return name, project_storage_dir(name)


def _sites(client, name):
    r = client.get(f"/api/projects/{name}/sites")
    assert r.status_code == 200, r.text
    return r.json()


def _bound(doc, key=KEY):
    return key in doc["sites"][0]["placements"] and key in doc.get("asset_footprints", {})


def _placed(doc, key=KEY):
    return key in doc["sites"][0]["placements"]


def _absent(doc, key=KEY):
    return key not in doc["sites"][0]["placements"] and key not in doc.get("asset_footprints", {})


def _delete_and_recreate(client):
    assert client.delete("/api/network/generators/gas").status_code in (200, 204)
    r = client.post("/api/network/generators", json={"name": "gas", "bus": "B1", "carrier": "gas"})
    assert r.status_code in (200, 201), r.text


def test_a_put_binds_each_key_to_the_component_instance(client, placed):
    name, project = placed
    doc = _sites(client, name)
    assert _bound(doc) and "unbound_keys" not in doc
    assert isinstance(ss.read_sites(project)["bindings"][KEY], str)


def test_delete_then_same_name_recreate_inherits_nothing(client, placed):
    name, _project = placed
    _delete_and_recreate(client)
    doc = _sites(client, name)
    assert _absent(doc)
    assert doc["unbound_keys"] == [KEY]


def test_a_failed_delete_hook_still_leaves_nothing_to_inherit(client, placed, monkeypatch):
    name, project = placed

    def fail(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(ss, "write_sites", fail)
    _delete_and_recreate(client)
    monkeypatch.undo()
    assert KEY in ss.read_sites(project)["asset_footprints"]      # the hook did fail
    assert _absent(_sites(client, name))


def test_rename_follows_and_undo_of_the_rename_brings_the_old_key_back(client, placed):
    name, _project = placed
    r = client.put("/api/network/generators/gas", json={"name": "gas2", "bus": "B1"})
    assert r.status_code == 200, r.text
    doc = _sites(client, name)
    assert _bound(doc, "Generator:gas2") and _absent(doc)
    assert client.post("/api/network/undo").status_code == 200
    doc = _sites(client, name)
    assert _bound(doc) and _absent(doc, "Generator:gas2")


def test_undo_of_a_delete_rebinds_the_restored_component(client, placed):
    name, _project = placed
    assert client.delete("/api/network/generators/gas").status_code in (200, 204)
    assert _absent(_sites(client, name))
    assert client.post("/api/network/undo").status_code == 200
    # The delete hook drops the footprint as before; the placement was kept, hidden, and comes back.
    assert _placed(_sites(client, name))


def test_a_put_carries_hidden_entries_forward_for_undo(client, placed):
    name, project = placed
    assert client.delete("/api/network/generators/gas").status_code in (200, 204)
    doc = _sites(client, name)                       # the client never sees the stale key ...
    assert client.put(f"/api/projects/{name}/sites", json=doc).status_code == 200
    assert client.post("/api/network/undo").status_code == 200
    assert _placed(_sites(client, name))             # ... and a save of what it sees does not drop it


def test_replacing_the_network_unbinds_same_named_components(client, placed, install_network):
    from tests.conftest import build_network

    name, _project = placed
    install_network(build_network())                 # a foreign network that also has a "gas" ...
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text              # ... saved over the project
    doc = _sites(client, name)
    assert _absent(doc) and doc["unbound_keys"] == [KEY]


def test_save_as_keeps_the_binding_in_the_copy_only(client, placed):
    name, _project = placed
    assert client.post("/api/projects/copy", params={"rebind": "true"}).status_code == 200
    assert _bound(_sites(client, "copy"))
    _delete_and_recreate(client)
    assert client.post("/api/projects/copy", params={"force": True, "rebind": True}).status_code == 200
    assert _absent(_sites(client, "copy"))
    assert client.get(f"/api/projects/{name}").status_code == 200
    assert _bound(_sites(client, name))              # the source's component was never touched


def test_scenario_copy_keeps_the_binding(client, placed):
    name, _project = placed
    r = client.post(f"/api/projects/{name}/scenarios", json={"name": "child", "description": "x"})
    assert r.status_code in (200, 201), r.text
    assert client.get("/api/projects/child").status_code == 200
    assert _bound(_sites(client, "child"))


def test_a_legacy_document_is_adopted_once_then_identity_holds(client, api_project, project_storage_dir):
    name = api_project("legacy")
    project = project_storage_dir(name)
    ss.write_sites(project, {**DOC, "sites": [dict(SITE)]})          # pre-D06: no bindings
    assert _bound(_sites(client, name))
    assert KEY in ss.read_sites(project)["bindings"]
    _delete_and_recreate(client)
    assert _absent(_sites(client, name))


def test_a_malformed_binding_is_refused(client, placed):
    name, _project = placed
    r = client.put(f"/api/projects/{name}/sites", json={**DOC, "bindings": {KEY: 7}})
    assert r.status_code == 422 and "bindings" in r.text


def test_a_uid_minted_after_the_last_save_survives_a_discarded_session(client, placed):
    name, _project = placed                          # the PUT minted the uid; the network was never saved
    assert client.get(f"/api/projects/{name}").status_code == 200
    assert _bound(_sites(client, name))


def test_a_saved_delete_and_recreate_stays_unbound_after_reload(client, placed):
    name, _project = placed
    _delete_and_recreate(client)
    assert client.post(f"/api/projects/{name}", params={"force": True, "rebind": True}).status_code == 200
    assert client.get(f"/api/projects/{name}").status_code == 200
    assert _absent(_sites(client, name))


def test_adoption_refuses_a_retired_uid_and_another_lineage():
    import pypsa

    from services import component_identity as ci

    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("Generator", "gas", bus="B1")
    uid = ci.uid_for(n, "Generator", "gas")
    ci.drop(n, "Generator", "gas")
    assert ci.name_of(n, "Generator", uid) is None
    assert not ci.adopt(n, "Generator", "gas", uid)                  # retired: never back
    assert not ci.adopt(n, "Generator", "gas", "0123456789ab-feed")  # another network's lineage
    assert ci.adopt(n, "Generator", "gas", f"{ci.lineage(n)}-feed")  # its own, minted after the last save
    assert ci.name_of(n, "Generator", f"{ci.lineage(n)}-feed") == "gas"


def test_a_scene_site_whose_delete_hook_failed_flags_the_same_name_recreation(client, placed, monkeypatch):
    from services.site_scene import store

    name, project = placed
    digest = store.legacy_site_digest(ss.read_sites(project), "campus")
    assert client.post(f"/api/projects/{name}/sites/campus/scene/adopt",
                       json={"expected_digest": digest}).status_code == 200

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_commit", boom)
    _delete_and_recreate(client)
    monkeypatch.undo()
    scene = client.get(f"/api/projects/{name}/sites/campus/scene").json()
    assert KEY in scene["site"]["placements"]        # the scene write failed, so the old key is still there ...
    assert scene["unbound_keys"] == [KEY]            # ... and is never taken for the new "gas"


def test_a_scene_entry_left_by_a_failed_rename_keeps_its_binding_through_a_put(client, placed, monkeypatch):
    from services.site_scene import store

    name, project = placed
    digest = store.legacy_site_digest(ss.read_sites(project), "campus")
    assert client.post(f"/api/projects/{name}/sites/campus/scene/adopt",
                       json={"expected_digest": digest}).status_code == 200

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_commit", boom)
    assert client.put("/api/network/generators/gas", json={"name": "gas2", "bus": "B1"}).status_code == 200
    monkeypatch.undo()
    r = client.post("/api/network/generators", json={"name": "gas", "bus": "B1", "carrier": "gas"})
    assert r.status_code in (200, 201), r.text
    doc = _sites(client, name)
    assert doc["bindings"][KEY] == doc["bindings"]["Generator:gas2"]   # still the renamed instance, not the new "gas"
    assert KEY in doc["unbound_keys"]
    assert client.put(f"/api/projects/{name}/sites", json=doc).status_code == 200
    scene = client.get(f"/api/projects/{name}/sites/campus/scene").json()
    assert KEY in scene["unbound_keys"]              # the new "gas" never takes the old entry
