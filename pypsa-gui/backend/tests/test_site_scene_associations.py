# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""
SC-02a: associations of scene buildings and groups with native model targets
(V11–V16, V20, V14 lifecycle), through the SC-01 proposal routes on a real
project. Plan and review conditions: `.scratch/site-scene-generation/sc-02/plan.md`.

Every target is bound by the D06 uid (`services/component_identity.py`), minted
at Apply under the network lock, then the scene mutex. Nothing is resolved by
name after that.
"""
from __future__ import annotations

import copy
import math

import pytest

from services import site_service as ss

SITE = {"id": "campus", "name": "Campus", "buses": ["B1"], "origin": {"lng": 11.58, "lat": 48.14},
        "boundary": [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]], "placements": {}}
HALL = {"id": "hall", "center": [30, -60], "width": 20, "depth": 12, "height": 8, "heading": 0, "unit": "m"}
SHED = {"id": "shed", "center": [55, -90], "width": 10, "depth": 10, "height": 5, "heading": 0, "unit": "m"}
A, B = "greenfield:hall", "greenfield:shed"
NAME = "assoc"


def _network():
    from tests.conftest import build_network

    n = build_network()
    n.add("Bus", "heat", carrier="heat")
    n.add("Bus", "h2", carrier="H2")
    n.add("Bus", "nh3", carrier="NH3")
    n.add("Load", "it", bus="B1", p_set=10.0)           # a second electricity Load (ambiguity)
    n.add("Load", "heat_demand", bus="heat", p_set=4.0)
    n.add("Load", "h2_offtake", bus="h2", p_set=2.0)
    n.add("Link", "haber", bus0="B1", bus1="nh3", p_nom=5.0, efficiency=0.5)
    n.links.loc["haber", "eh_process"] = True
    n.add("Link", "cable", bus0="B1", bus1="heat", p_nom=1.0)   # an ordinary Link: not a process unit
    n.add("Store", "nh3_tank", bus="nh3", e_nom=100.0)
    n.add("StorageUnit", "bess", bus="B1", p_nom=3.0, max_hours=2.0)
    return n


@pytest.fixture
def scene(client, install_network, project_storage_dir):
    return make_scene(client, install_network, project_storage_dir)


def make_scene(client, install_network, project_storage_dir):
    """A saved project with a multi-energy network and an adopted Site holding two buildings."""
    install_network(_network(), name=NAME)
    assert client.post(f"/api/projects/{NAME}", params={"force": True, "rebind": True}).status_code == 200
    project = project_storage_dir(NAME)
    assert client.put(f"/api/projects/{NAME}/sites", json={"version": 1, "sites": [copy.deepcopy(SITE)]}).status_code == 200
    from services.site_scene import store
    digest = store.legacy_site_digest(ss.read_sites(project), "campus")
    assert client.post(f"/api/projects/{NAME}/sites/campus/scene/adopt",
                       json={"expected_digest": digest}).status_code == 200
    _apply(client, _propose(client, "generate_native", {"greenfield": [HALL, SHED]}))
    return project


def _url(tail=""):
    return f"/api/projects/{NAME}/sites/campus/scene{tail}"


def _get(client):
    r = client.get(_url())
    assert r.status_code == 200, r.text
    return r.json()


def _propose(client, action, inputs, *, ok=True):
    r = client.post(_url("/proposals"), json={"action": action, "base_revision": _get(client)["revision"],
                                              "inputs": inputs})
    if ok:
        assert r.status_code == 200, r.text
        return r.json()
    return r


def _apply(client, proposal, *, ok=True):
    r = client.post(_url(f"/proposals/{proposal['id']}/apply"),
                    json={"expected_revision": proposal["base_revision"], "input_hash": proposal["input_hash"]})
    if ok:
        assert r.status_code == 200, r.text
        return r.json()
    return r


def _bind(client, associations, groups=(), *, ok=True):
    p = _propose(client, "bind_targets", {"associations": list(associations), "groups": list(groups)}, ok=ok)
    return _apply(client, p) if ok else p


def _detail(r):
    return r.json()["detail"]


def _assoc(scene_json, feature, name=None):
    return [a for a in scene_json["associations"] if a["feature"] == feature and (name is None or a["name"] == name)]


def _table(client, attr):
    """A component table as the request's network sees it (the test thread may hold another context)."""
    r = client.get(f"/api/network/{attr}")
    assert r.status_code == 200, r.text
    rows = r.json()
    return rows.get("data", rows) if isinstance(rows, dict) else rows


# ── V11: one Load per carrier, native units; missing carrier stays unavailable ──

def test_a_building_binds_one_load_per_carrier_in_native_units(client, scene):
    p = _propose(client, "bind_targets", {"associations": [
        {"feature": A, "target": {"class": "Load", "name": "it"}},
        {"feature": A, "target": {"class": "Load", "carrier": "heat"}},
        {"feature": A, "target": {"class": "Load", "carrier": "H2"}},
        {"feature": A, "target": {"class": "Load", "carrier": "cooling"}},
    ], "groups": []})
    rows = {(a["name"], a["status"]) for a in p["preview"]["associations"]}
    assert {("it", "new"), ("heat_demand", "new"), ("h2_offtake", "new")} <= rows
    cooling = [a for a in p["preview"]["associations"] if a["status"] == "unavailable"]
    assert len(cooling) == 1 and cooling[0]["carrier"] == "cooling" and cooling[0]["name"] is None
    assert "cooling" in cooling[0]["reason"]
    _apply(client, p)
    got = _get(client)
    rows = _assoc(got, A)
    missing = [a for a in rows if a["status"] == "unavailable"]
    assert len(missing) == 1 and missing[0]["carrier"] == "cooling"     # kept so the panel can say why
    assert missing[0]["name"] is None and missing[0].get("uid") is None
    bound = {a["name"]: a for a in rows if a["status"] != "unavailable"}
    assert set(bound) == {"it", "heat_demand", "h2_offtake"}
    assert all(a["status"] == "bound" for a in bound.values())
    assert bound["it"]["carrier"] == "AC" and bound["heat_demand"]["carrier"] == "heat"
    assert bound["it"]["unit"] == "MW"
    assert bound["it"]["role"] == "load"


def test_two_loads_on_one_carrier_are_ambiguous_and_never_picked(client, scene):
    r = _propose(client, "bind_targets", {"associations": [
        {"feature": A, "target": {"class": "Load", "carrier": "AC"}}], "groups": []}, ok=False)
    assert r.status_code == 422
    d = _detail(r)
    assert d["code"] == "ambiguous_target" and sorted(d["candidates"]) == ["L1", "it"]


# ── V12: a process Link binds as itself; no Load is created ──

def test_a_process_link_binds_without_adding_a_load(client, scene):
    before = {attr: _table(client, attr) for attr in ("loads", "links")}
    p = _propose(client, "bind_targets", {"associations": [
        {"feature": A, "target": {"class": "Link", "name": "haber"}},
        {"feature": B, "target": {"class": "Load", "name": "L1"}}], "groups": []})
    assert p["preview"]["findings"] == []                               # different features: no double count
    _apply(client, p)
    assert {attr: _table(client, attr) for attr in ("loads", "links")} == before   # the uid lives in meta only
    assert _assoc(_get(client), A)[0]["role"] == "process_unit"


def test_a_process_link_and_a_fixed_load_on_its_feed_bus_is_a_finding(client, scene):
    p = _propose(client, "bind_targets", {"associations": [
        {"feature": A, "target": {"class": "Link", "name": "haber"}},
        {"feature": A, "target": {"class": "Load", "name": "L1"}}], "groups": []})
    kinds = [(f["code"], f["severity"]) for f in p["preview"]["findings"]]
    assert kinds == [("double_counted_demand", "warn")]
    _apply(client, p)                                                    # a finding, not a refusal


def test_an_ordinary_link_and_an_unknown_class_are_unsupported(client, scene):
    for target in ({"class": "Link", "name": "cable"}, {"class": "Bus", "name": "B1"}):
        r = _propose(client, "bind_targets", {"associations": [{"feature": A, "target": target}], "groups": []},
                     ok=False)
        assert r.status_code == 422 and _detail(r)["code"] == "unsupported_target", target


# ── V13: complexes are acyclic and have no orphans ──

def test_a_complex_spans_buildings_a_process_unit_and_inventory(client, scene):
    groups = [{"id": "plant", "label": "Ammonia plant", "members": [A, B]},
              {"id": "site", "label": "Site", "members": ["plant"]}]
    _bind(client, [{"feature": "plant", "target": {"class": "Link", "name": "haber"}},
                   {"feature": "plant", "target": {"class": "Store", "name": "nh3_tank"}}], groups)
    got = _get(client)
    assert [g["id"] for g in got["groups"]] == ["plant", "site"]
    roles = {a["name"]: a["role"] for a in _assoc(got, "plant")}
    assert roles == {"haber": "process_unit", "nh3_tank": "inventory"}


def test_a_placed_member_follows_its_components_rename(client, scene):
    p = _propose(client, "place_existing", {"placements": {"StorageUnit:bess": {"x": 60.0, "y": -50.0, "heading": 0}}})
    _apply(client, p)
    _bind(client, [{"feature": "StorageUnit:bess", "target": {"class": "StorageUnit", "name": "bess"}}],
          [{"id": "yard", "label": "Yard", "members": [A, "StorageUnit:bess"]}])
    assert client.put("/api/network/storage_units/bess", json={"name": "bess2", "bus": "B1"}).status_code == 200
    got = _get(client)
    assert got["groups"][0]["members"] == [A, "StorageUnit:bess2"]
    (row,) = _assoc(got, "StorageUnit:bess2")
    assert row["status"] == "bound" and row["name"] == "bess2"


def test_a_cycle_and_an_orphan_member_are_refused(client, scene):
    cycle = [{"id": "x", "label": "X", "members": ["y"]}, {"id": "y", "label": "Y", "members": ["x"]}]
    r = _propose(client, "bind_targets", {"associations": [], "groups": cycle}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "cycle" and set(_detail(r)["path"]) == {"x", "y"}
    orphan = [{"id": "x", "label": "X", "members": [A, "greenfield:nope"]}]
    r = _propose(client, "bind_targets", {"associations": [], "groups": orphan}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "orphan_member"
    r = _propose(client, "bind_targets", {"associations": [
        {"feature": "nowhere", "target": {"class": "Load", "name": "it"}}], "groups": []}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "orphan_member"


# ── V15/V16: enclosures count once; allocation is explicit and conserved ──

def test_several_enclosures_of_one_battery_count_its_capacity_once(client, scene):
    _bind(client, [{"feature": A, "target": {"class": "StorageUnit", "name": "bess"}},
                   {"feature": B, "target": {"class": "StorageUnit", "name": "bess"}}])
    targets = {t["name"]: t for t in _get(client)["targets"]}
    assert targets["bess"]["enclosures"] == 2
    assert targets["bess"]["capacity"] == {"value": 3.0, "available": True, "reason": None, "unit": "MW"}


def test_an_aggregate_load_in_two_buildings_is_not_split(client, scene):
    _bind(client, [{"feature": A, "target": {"class": "Load", "name": "it"}},
                   {"feature": B, "target": {"class": "Load", "name": "it"}}])
    it = {t["name"]: t for t in _get(client)["targets"]}["it"]
    assert it["allocation"] == {"basis": "none", "shares": []}           # no invented local split


def test_an_explicit_split_shows_allocated_values_that_sum_to_the_native_total(client, scene):
    _bind(client, [{"feature": A, "target": {"class": "Load", "name": "it"}, "weight": 0.4},
                   {"feature": B, "target": {"class": "Load", "name": "it"}, "weight": 0.6}])
    it = {t["name"]: t for t in _get(client)["targets"]}["it"]
    shares = {s["feature"]: s["allocated"]["value"] for s in it["allocation"]["shares"]}
    assert it["allocation"]["basis"] == "explicit"
    assert shares == pytest.approx({A: 4.0, B: 6.0})
    assert it["capacity"]["value"] == 10.0


@pytest.mark.parametrize("weights,ok", [
    ((0.1, 0.2, 0.7), True),
    ((0.3, 0.6), False),
    ((math.nan, 1.0), False),
    ((0.0, 1.0), False),
    ((-0.5, 1.5), False),
])
def test_allocation_weights_are_finite_positive_and_sum_to_one(client, scene, weights, ok):
    feats = [A, B, "greenfield:hall"][:len(weights)]
    groups = [{"id": "g3", "label": "G", "members": [A]}] if len(weights) == 3 else []
    if len(weights) == 3:
        feats = [A, B, "g3"]
    assoc = [{"feature": f, "target": {"class": "Load", "name": "it"}, "weight": w} for f, w in zip(feats, weights)]
    body = {"action": "bind_targets", "base_revision": _get(client)["revision"],
            "inputs": {"associations": assoc, "groups": groups}}
    import json
    r = client.post(_url("/proposals"), content=json.dumps(body, allow_nan=True),
                    headers={"content-type": "application/json"})
    if ok:
        assert r.status_code == 200, r.text
    else:
        assert r.status_code == 422 and _detail(r)["code"] == "invalid_allocation", r.text


def test_a_partial_split_is_refused(client, scene):
    r = _propose(client, "bind_targets", {"associations": [
        {"feature": A, "target": {"class": "Load", "name": "it"}, "weight": 1.0},
        {"feature": B, "target": {"class": "Load", "name": "it"}}], "groups": []}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "invalid_allocation"


# ── V14: lifecycle by uid, never by name ──

def test_rename_keeps_the_binding(client, scene):
    _bind(client, [{"feature": A, "target": {"class": "Load", "name": "it"}}])
    r = client.put("/api/network/loads/it", json={"name": "it2", "bus": "B1"})
    assert r.status_code == 200, r.text
    (row,) = _assoc(_get(client), A)
    assert row["status"] == "bound" and row["name"] == "it2"


def test_delete_unbinds_and_a_same_name_recreation_stays_unbound(client, scene):
    _bind(client, [{"feature": A, "target": {"class": "Load", "name": "it"}}])
    assert client.delete("/api/network/loads/it").status_code in (200, 204)
    (row,) = _assoc(_get(client), A)
    assert row["status"] == "unbound" and row["name"] is None and row["name_at_bind"] == "it"
    r = client.post("/api/network/loads", json={"name": "it", "bus": "B1", "p_set": 1.0})
    assert r.status_code in (200, 201), r.text
    (row,) = _assoc(_get(client), A)
    assert row["status"] == "unbound"                                    # never inherited by name


def _unbound_after_recreate(client):
    _bind(client, [{"feature": A, "target": {"class": "Load", "name": "it"}}])
    old = _assoc(_get(client), A)[0]["uid"]
    assert client.delete("/api/network/loads/it").status_code in (200, 204)
    assert client.post("/api/network/loads", json={"name": "it", "bus": "B1", "p_set": 1.0}).status_code in (200, 201)
    (row,) = _assoc(_get(client), A)
    return row, old


def test_rebind_needs_the_users_confirmation(client, scene):
    row, old = _unbound_after_recreate(client)
    r = _propose(client, "rebind", {"association": row["id"], "target": {"class": "Load", "name": "it"}}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "confirmation_required"
    p = _propose(client, "rebind", {"association": row["id"], "target": {"class": "Load", "name": "it"},
                                    "confirmed": True})
    _apply(client, p)
    (row,) = _assoc(_get(client), A)
    assert row["status"] == "bound" and row["name"] == "it" and row["rebound_from"] == old


def test_a_declined_rebind_leaves_it_unbound_with_its_old_uid(client, scene):
    row, old = _unbound_after_recreate(client)
    p = _propose(client, "rebind", {"association": row["id"], "target": {"class": "Load", "name": "it"},
                                    "confirmed": True})
    assert client.post(_url(f"/proposals/{p['id']}/cancel")).status_code == 200
    (after,) = _assoc(_get(client), A)
    assert after["status"] == "unbound" and after["uid"] == old


def test_a_rebind_prepared_before_a_recreation_refuses_at_apply(client, scene):
    row, _ = _unbound_after_recreate(client)
    # B's bind mints a uid for the new "it"; the rebind is reviewed against that instance ...
    _bind(client, [{"id": row["id"]}, {"feature": B, "target": {"class": "Load", "name": "it"}}])
    p = _propose(client, "rebind", {"association": row["id"], "target": {"class": "Load", "name": "it"},
                                    "confirmed": True})
    # ... which is then replaced by another same-name Load before Apply.
    assert client.delete("/api/network/loads/it").status_code in (200, 204)
    assert client.post("/api/network/loads", json={"name": "it", "bus": "B1", "p_set": 1.0}).status_code in (200, 201)
    revision = _get(client)["revision"]
    r = _apply(client, p, ok=False)
    assert r.status_code == 409 and _detail(r)["code"] == "target_changed"
    assert _get(client)["revision"] == revision                          # nothing written


def test_a_replaced_network_leaves_every_association_unbound(client, scene, install_network):
    _bind(client, [{"feature": A, "target": {"class": "Load", "name": "it"}}])
    install_network(_network())                                          # a foreign network, same names ...
    r = client.post(f"/api/projects/{NAME}", params={"force": True})     # ... saved over the project
    assert r.status_code == 200, r.text
    (row,) = _assoc(_get(client), A)
    assert row["status"] == "unbound"


# ── Apply re-validates against the live network (GK B condition 3) ──

def test_a_target_deleted_between_prepare_and_apply_refuses_and_writes_nothing(client, scene):
    p = _propose(client, "bind_targets", {"associations": [
        {"feature": A, "target": {"class": "Load", "name": "it"}}], "groups": []})
    assert client.delete("/api/network/loads/it").status_code in (200, 204)
    revision = _get(client)["revision"]
    r = _apply(client, p, ok=False)
    assert r.status_code == 409 and _detail(r)["code"] == "target_changed"
    assert _get(client)["revision"] == revision


def test_a_target_whose_carrier_changed_between_prepare_and_apply_refuses(client, scene):
    p = _propose(client, "bind_targets", {"associations": [
        {"feature": A, "target": {"class": "Load", "name": "it"}}], "groups": []})
    assert client.put("/api/network/loads/it", json={"name": "it", "bus": "heat"}).status_code == 200
    r = _apply(client, p, ok=False)
    assert r.status_code == 409 and _detail(r)["code"] == "target_changed"


# ── reads never write (GK B condition 2) ──

def test_a_scene_get_and_a_prepare_never_mint_or_write(client, scene, monkeypatch):
    from services import component_identity

    _bind(client, [{"feature": A, "target": {"class": "Load", "name": "it"}}])
    assert client.delete("/api/network/loads/it").status_code in (200, 204)
    files = sorted(p for p in scene.rglob("*") if p.is_file())
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}

    def no_mint(*_a, **_k):
        raise AssertionError("a read minted a uid")

    monkeypatch.setattr(component_identity, "uid_for", no_mint)
    monkeypatch.setattr(component_identity, "_root", no_mint)
    for _ in range(2):
        _get(client)
        _propose(client, "bind_targets", {"associations": [
            {"feature": B, "target": {"class": "Load", "name": "L1"}}], "groups": []})
        _propose(client, "place_existing", {"placements": {"Generator:gas": {"x": 60.0, "y": -50.0, "heading": 0}}})
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == before
    assert sorted(p for p in scene.rglob("*") if p.is_file()) == files


# ── lock order (GK B condition 1) ──

def test_bind_apply_takes_the_network_lock_before_the_scene_mutex(client, scene, monkeypatch):
    from services.pypsa_service import PyPSAService
    from services.site_scene import store

    real, taken = store.project_lock, []

    def recording(project_dir):
        taken.append(PyPSAService.get_lock()._is_owned())
        return real(project_dir)

    p = _propose(client, "bind_targets", {"associations": [
        {"feature": A, "target": {"class": "Load", "name": "it"}}], "groups": []})
    monkeypatch.setattr(store, "project_lock", recording)
    _apply(client, p)
    assert taken and all(taken), taken


@pytest.mark.parametrize("path", ["crud_rename", "crud_delete", "bus_rename"])
def test_every_legacy_sites_write_holds_the_scene_mutex_and_never_the_reverse(client, api_project,
                                                                             project_storage_dir, monkeypatch, path):
    """The delete and rename hooks wrote sites.json without the scene mutex (pre-D06 risk)."""
    from services.pypsa_service import PyPSAService
    from services.site_scene import store

    name = api_project("hooks")
    project = project_storage_dir(name)
    doc = {"version": 1, "sites": [{**copy.deepcopy(SITE), "placements": {
        "Generator:gas": {"x": 1.0, "y": 1.0, "heading": 0}, "Bus:B1": {"x": 2.0, "y": 2.0, "heading": 0}}}],
        "asset_footprints": {"Generator:gas": {"width_m": 2.0, "depth_m": 2.0}}}
    assert client.put(f"/api/projects/{name}/sites", json=doc).status_code == 200
    scene_mutex = store.project_lock(project)
    net_lock = PyPSAService.get_lock()
    writes, inverted = [], []
    real_write, real_get_lock = ss.write_sites, PyPSAService.get_lock

    class Recording:
        """The network lock, noting any acquisition made while the scene mutex is held without it."""

        def __enter__(self):
            if scene_mutex._is_owned() and not net_lock._is_owned():
                inverted.append(path)
            return net_lock.__enter__()

        def __exit__(self, *exc):
            return net_lock.__exit__(*exc)

        def __getattr__(self, attr):
            return getattr(net_lock, attr)

    def recording_write(pdir, d):
        writes.append(scene_mutex._is_owned())
        return real_write(pdir, d)

    monkeypatch.setattr(ss, "write_sites", recording_write)
    monkeypatch.setattr(PyPSAService, "get_lock", classmethod(lambda cls: Recording()))
    if path == "crud_rename":
        r = client.put("/api/network/generators/gas", json={"name": "gas2", "bus": "B1"})
    elif path == "crud_delete":
        r = client.delete("/api/network/generators/gas")
    else:
        r = client.post("/api/network/buses/B1/rename", json={"new_name": "B9"})
    monkeypatch.setattr(PyPSAService, "get_lock", real_get_lock)
    assert r.status_code in (200, 204), r.text
    assert writes and all(writes), writes                                # every sites.json write under the mutex
    assert not inverted, inverted                                        # never the network lock under it


# ── V12: the double-count finding is about electricity only ──

def test_double_count_fires_for_an_electricity_load_on_another_ac_bus_and_not_for_heat(client, scene):
    assert client.post("/api/network/buses", json={"name": "B2", "carrier": "AC"}).status_code in (200, 201)
    assert client.post("/api/network/loads", json={"name": "l2", "bus": "B2", "p_set": 1.0}).status_code in (200, 201)
    p = _propose(client, "bind_targets", {"associations": [
        {"feature": A, "target": {"class": "Link", "name": "haber"}},
        {"feature": A, "target": {"class": "Load", "name": "l2"}}], "groups": []})
    assert [(f["code"], f["severity"], f["objects"]) for f in p["preview"]["findings"]] == [
        ("double_counted_demand", "warn", ["Link:haber", "Load:l2"])]
    p = _propose(client, "bind_targets", {"associations": [
        {"feature": A, "target": {"class": "Link", "name": "haber"}},
        {"feature": A, "target": {"class": "Load", "name": "heat_demand"}}], "groups": []})
    assert p["preview"]["findings"] == []


# ── V16: a rebind keeps every target's shares at 100 % ──

def test_a_rebind_that_breaks_a_split_is_refused(client, scene):
    _bind(client, [{"feature": A, "target": {"class": "Load", "name": "it"}, "weight": 0.4},
                   {"feature": B, "target": {"class": "Load", "name": "it"}, "weight": 0.6}])
    row = _assoc(_get(client), A)[0]
    r = _propose(client, "rebind", {"association": row["id"], "target": {"class": "Load", "name": "L1"},
                                    "confirmed": True}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "invalid_allocation", r.text


# ── Apply checks access and revision again (GK B condition 8) ──

def test_a_stale_revision_refuses_and_writes_nothing(client, scene):
    first = _propose(client, "bind_targets", {"associations": [
        {"feature": A, "target": {"class": "Load", "name": "it"}}], "groups": []})
    _bind(client, [{"feature": B, "target": {"class": "Load", "name": "L1"}}])
    got = _get(client)
    r = _apply(client, first, ok=False)
    assert r.status_code == 409, r.text
    assert _get(client)["revision"] == got["revision"] and _assoc(_get(client), A) == []


def test_another_org_cannot_apply_a_prepared_bind(client, other_org_client, scene):
    p = _propose(client, "bind_targets", {"associations": [
        {"feature": A, "target": {"class": "Load", "name": "it"}}], "groups": []})
    revision = _get(client)["revision"]
    r = other_org_client.post(_url(f"/proposals/{p['id']}/apply"),
                              json={"expected_revision": p["base_revision"], "input_hash": p["input_hash"]})
    assert r.status_code == 404, r.text
    assert _get(client)["revision"] == revision and _assoc(_get(client), A) == []


def test_a_lock_taken_between_prepare_and_apply_refuses_and_writes_nothing(client, scene, _auth_db,
                                                                          seeded_identity, second_identity,
                                                                          project_row):
    from services import project_locks

    p = _propose(client, "bind_targets", {"associations": [
        {"feature": A, "target": {"class": "Load", "name": "it"}}], "groups": []})
    revision = _get(client)["revision"]
    sites_before = (scene / "sites.json").read_bytes()
    _engine, session_local = _auth_db
    with session_local() as db:
        project_locks.release_lock(db, project_row(NAME).id, seeded_identity["user_id"])
        assert project_locks.acquire_lock(db, project_row(NAME).id, second_identity["user_id"]) is not None
    try:
        r = _apply(client, p, ok=False)
        assert r.status_code == 409 and _detail(r)["error_kind"] == "project_locked", r.text
    finally:
        with session_local() as db:
            project_locks.release_lock(db, project_row(NAME).id, second_identity["user_id"])
            db.commit()
    assert _get(client)["revision"] == revision and _assoc(_get(client), A) == []
    assert (scene / "sites.json").read_bytes() == sites_before


# ── lock order under real concurrency (GK B condition 1) ──

@pytest.mark.parametrize("edit", ["rename", "delete"])
def test_a_threaded_placement_apply_and_a_crud_edit_keep_both_sites_writes(client, scene, edit):
    from concurrent.futures import ThreadPoolExecutor

    _apply(client, _propose(client, "place_existing", {"placements": {
        "Generator:gas": {"x": 70.0, "y": -40.0, "heading": 0}}}))
    p = _propose(client, "place_existing", {"placements": {"StorageUnit:bess": {"x": 60.0, "y": -50.0, "heading": 0}}})

    def crud():
        if edit == "rename":
            return client.put("/api/network/generators/gas", json={"name": "gas2", "bus": "B1"})
        return client.delete("/api/network/generators/gas")

    with ThreadPoolExecutor(2) as pool:
        placing, editing = pool.submit(_apply, client, p), pool.submit(crud)
        placing.result(timeout=60)
        assert editing.result(timeout=60).status_code in (200, 204)
    bindings = ss.read_sites(scene).get("bindings") or {}
    placed = _get(client)["site"]["placements"]
    assert "StorageUnit:bess" in bindings and "StorageUnit:bess" in placed, (bindings, placed)
    assert "Generator:gas" not in placed, placed                         # the edit's scene write kept
    if edit == "rename":                                                 # a delete leaves a dead binding
        assert "Generator:gas2" in placed and "Generator:gas2" in bindings and "Generator:gas" not in bindings
