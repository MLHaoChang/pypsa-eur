# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
SC-02d: detach placed equipment from the scene (the component stays in the
model) and undo it; several items placed in one proposal. Plan conditions:
.scratch/site-scene-generation/issues/03-bindings-and-placement.md, ## Comments.
"""
from __future__ import annotations

import pytest

from services import site_service as ss
from tests.test_site_scene_associations import A, NAME, _apply, _detail, _get, _network, _propose, _table, \
    _unsaved, _url, make_scene

BESS, TANK, GAS, SOLAR = "StorageUnit:bess", "Store:nh3_tank", "Generator:gas", "Generator:solar"
MODEL = ("buses", "generators", "loads", "links", "lines", "stores", "storage_units")


@pytest.fixture
def scene(client, install_network, project_storage_dir):
    return make_scene(client, install_network, project_storage_dir)


def _place(client, *keys, x=60.0):
    spots = {k: {"x": x + 5 * i, "y": -50.0, "heading": 0} for i, k in enumerate(keys)}
    return _apply(client, _propose(client, "place_existing", {"placements": spots}))


def _bind(client, associations, groups):
    return _apply(client, _propose(client, "bind_targets", {"associations": associations, "groups": groups}))


def _detach(client, *keys):
    p = _propose(client, "detach", {"keys": list(keys)})
    return p, _apply(client, p)


def _model(client):
    return {attr: _table(client, attr) for attr in MODEL}


def _complex(client):
    """bess and gas placed; group yard = {bess}; site = {hall, yard}; the tank attached to bess."""
    _place(client, BESS, GAS)
    _bind(client, [{"feature": BESS, "target": {"class": "Store", "name": "nh3_tank"}}],
          [{"id": "yard", "label": "Yard", "members": [BESS]},
           {"id": "site", "label": "Site", "members": [A, "yard", GAS]}])


def _state(got):
    return {"placements": got["site"]["placements"], "groups": got["groups"],
            "associations": sorted((a["id"], a["feature"], a["uid"]) for a in got["associations"])}


# ── detach ──

def test_detach_takes_the_item_off_the_scene_and_keeps_component_and_binding(client, scene):
    _place(client, BESS)
    before, bindings = _model(client), ss.read_sites(scene)["bindings"]
    unsaved = _unsaved(client)
    p, receipt = _detach(client, BESS)
    assert p["preview"]["items"] == [{"key": BESS, "label": "bess · storage unit", "kept_in_model": True,
                                      "groups": [], "associations": []}]
    got = _get(client)
    assert BESS not in got["site"]["placements"]
    assert BESS not in got["unbound_keys"]                       # detached is not unbound
    assert _model(client) == before                              # the component stays in the model
    assert ss.read_sites(scene)["bindings"][BESS] == bindings[BESS]   # mutant: detach drops the binding
    assert _unsaved(client) == unsaved                           # nothing minted
    assert receipt["revision"] == got["revision"]


def test_detach_prunes_memberships_and_attachments_and_lists_them(client, scene):
    _complex(client)
    p, _ = _detach(client, BESS)
    item = p["preview"]["items"][0]
    assert item["groups"] == [{"id": "yard", "label": "Yard", "left_empty": True}]
    assert [(a["class"], a["label"]) for a in item["associations"]] == [("Store", "nh3_tank · store")]
    got = _get(client)
    assert {g["id"]: g["members"] for g in got["groups"]} == {"yard": [], "site": [A, "yard", GAS]}
    assert [a for a in got["associations"] if a["feature"] == BESS] == []


@pytest.mark.parametrize("inputs, code", [
    ({"keys": []}, "invalid_placements"),
    ({"keys": "StorageUnit:bess"}, "invalid_placements"),
    ({"keys": ["Carrier:AC"]}, "unsupported_target"),
    ({"keys": [BESS]}, "not_placed"),
    ({"keys": [GAS, GAS]}, "duplicate"),
])
def test_detach_refusals_name_the_item_and_write_nothing(client, scene, inputs, code):
    _place(client, GAS)
    revision = _get(client)["revision"]
    r = _propose(client, "detach", inputs, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == code, r.text
    if code in ("not_placed", "duplicate"):                       # named in words, never as a key
        assert "Generator:" not in _detail(r)["message"] and "bess" in _detail(r)["message"] or "gas" in _detail(r)["message"]
    assert _get(client)["revision"] == revision


def test_detach_over_the_place_cap_is_refused(client, scene, monkeypatch):
    from services.site_scene import associations

    monkeypatch.setattr(associations, "MAX_ASSOCIATIONS", 1)
    _place(client, GAS)
    r = _propose(client, "detach", {"keys": [GAS, BESS]}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "invalid_placements"


def test_a_detach_that_breaks_a_split_is_refused(client, scene):
    _place(client, BESS)
    _bind(client, [{"feature": A, "target": {"class": "Store", "name": "nh3_tank"}, "weight": 0.5},
                   {"feature": BESS, "target": {"class": "Store", "name": "nh3_tank"}, "weight": 0.5}], [])
    r = _propose(client, "detach", {"keys": [BESS]}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "invalid_allocation"


# ── undo ──

def test_undo_restores_what_the_detach_removed(client, scene):
    _complex(client)
    before = _state(_get(client))
    detach, _ = _detach(client, BESS)
    u = _propose(client, "undo_detach", {"proposal": detach["id"]})
    assert [i["key"] for i in u["preview"]["items"]] == [BESS] and u["preview"]["findings"] == []
    _apply(client, u)
    assert _state(_get(client)) == before


def test_undo_is_a_delta_on_the_current_head(client, scene):
    # GK B condition 1 (mutant: undo restores the parent generation wholesale)
    _complex(client)
    detach, _ = _detach(client, BESS)
    _place(client, TANK, x=80.0)                                  # an unrelated edit after the detach
    _bind(client, [{"id": a["id"]} for a in _get(client)["associations"]]
          + [{"feature": GAS, "target": {"class": "Load", "name": "L1"}}],
          [{"id": g["id"], "label": g["label"], "members": g["members"]} for g in _get(client)["groups"]]
          + [{"id": "tanks", "label": "Tanks", "members": [TANK]}])                  # a group made after the detach
    _apply(client, _propose(client, "undo_detach", {"proposal": detach["id"]}))
    got = _get(client)
    assert {BESS, TANK, GAS} <= set(got["site"]["placements"])
    assert [a["name"] for a in got["associations"] if a["feature"] == GAS] == ["L1"]
    assert [a["name"] for a in got["associations"] if a["feature"] == BESS] == ["nh3_tank"]
    assert {g["id"]: g["members"] for g in got["groups"]}["yard"] == [BESS]
    assert {g["id"]: g["members"] for g in got["groups"]}["tanks"] == [TANK]


def test_undoing_twice_is_already_placed_and_a_reapply_reads_back(client, scene):
    # GK B condition 2 (mutant: drop the already_placed check)
    _place(client, BESS)
    detach, detached = _detach(client, BESS)
    u = _propose(client, "undo_detach", {"proposal": detach["id"]})
    receipt = _apply(client, u)
    assert _apply(client, u) == receipt                          # the same proposal: its committed receipt
    assert _apply(client, detach) == detached                    # the detach too
    r = _propose(client, "undo_detach", {"proposal": detach["id"]}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "already_placed", r.text
    assert "bess" in _detail(r)["message"]


def test_undo_after_the_item_was_placed_again_is_refused(client, scene):
    _place(client, BESS)
    detach, _ = _detach(client, BESS)
    _place(client, BESS, x=90.0)
    r = _propose(client, "undo_detach", {"proposal": detach["id"]}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "already_placed"


def test_undo_of_a_replaced_component_is_refused_never_rebound_by_name(client, scene, install_network):
    _place(client, BESS)
    detach, _ = _detach(client, BESS)
    install_network(_network())                                   # a foreign network, same names
    assert client.post(f"/api/projects/{NAME}", params={"force": True}).status_code == 200
    r = _propose(client, "undo_detach", {"proposal": detach["id"]}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "unbound_target", r.text


def test_undo_follows_a_rename_made_after_the_detach(client, scene):
    _complex(client)
    detach, _ = _detach(client, BESS)
    r = client.put("/api/network/storage_units/bess", json={"name": "bess2", "bus": "B1"})
    assert r.status_code == 200, r.text
    _apply(client, _propose(client, "undo_detach", {"proposal": detach["id"]}))
    got = _get(client)
    assert "StorageUnit:bess2" in got["site"]["placements"] and BESS not in got["site"]["placements"]
    assert {g["id"]: g["members"] for g in got["groups"]}["yard"] == ["StorageUnit:bess2"]
    assert [a["feature"] for a in got["associations"] if a["name"] == "nh3_tank"] == ["StorageUnit:bess2"]


def test_an_attachment_target_deleted_between_preview_and_apply_refuses(client, scene):
    # GK B condition 2: Apply re-derives and refuses, writing nothing
    _complex(client)
    detach, _ = _detach(client, BESS)
    u = _propose(client, "undo_detach", {"proposal": detach["id"]})
    assert client.delete("/api/network/stores/nh3_tank").status_code in (200, 204)
    revision, sites = _get(client)["revision"], (scene / "sites.json").read_bytes()
    r = client.post(_url(f"/proposals/{u['id']}/apply"),
                    json={"expected_revision": revision, "input_hash": u["input_hash"]})
    assert r.status_code == 409, r.text                           # target_changed, or the revision moved
    assert BESS not in _get(client)["site"]["placements"]
    assert (scene / "sites.json").read_bytes() == sites


def test_undo_of_a_deleted_attachment_target_is_target_changed(client, scene):
    _complex(client)
    detach, _ = _detach(client, BESS)
    assert client.delete("/api/network/stores/nh3_tank").status_code in (200, 204)
    r = _propose(client, "undo_detach", {"proposal": detach["id"]}, ok=False)
    assert r.status_code == 409 and _detail(r)["code"] == "target_changed", r.text


def test_a_membership_whose_group_was_deleted_is_a_warning_not_a_refusal(client, scene):
    _complex(client)
    detach, _ = _detach(client, BESS)
    got = _get(client)
    _bind(client, [{"id": a["id"]} for a in got["associations"]],
          [{"id": "site", "label": "Site", "members": [A, GAS]}])            # yard deleted
    u = _propose(client, "undo_detach", {"proposal": detach["id"]})
    assert [(f["code"], f["severity"], f["group"]) for f in u["preview"]["findings"]] == \
        [("membership_not_restored", "warn", "Yard")]
    _apply(client, u)
    got = _get(client)
    assert BESS in got["site"]["placements"] and [g["id"] for g in got["groups"]] == ["site"]


@pytest.mark.parametrize("proposal", ["nope", "p-" + "0" * 32, "../g000001", 7, None])
def test_undo_input_that_names_no_detach_of_this_site_is_not_found(client, scene, proposal):
    # GK B condition 3: one text for every miss, no existence oracle
    r = _propose(client, "undo_detach", {"proposal": proposal}, ok=False)
    assert r.status_code == 404 and _detail(r)["code"] == "detach_not_found", r.text
    assert _detail(r)["message"] == "This detach can’t be undone now: the scene changed since"


def test_undo_of_a_proposal_that_is_not_a_detach_is_not_found(client, scene):
    placed = _propose(client, "place_existing", {"placements": {BESS: {"x": 60.0, "y": -50.0, "heading": 0}}})
    _apply(client, placed)
    r = _propose(client, "undo_detach", {"proposal": placed["id"]}, ok=False)
    assert r.status_code == 404 and _detail(r)["message"] == "This detach can’t be undone now: the scene changed since"


def test_detach_and_undo_prepare_never_write(client, scene, monkeypatch):
    from services import component_identity

    _complex(client)
    detach, _ = _detach(client, BESS)
    files = sorted(p for p in scene.rglob("*") if p.is_file())
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}

    def no_mint(*_a, **_k):
        raise AssertionError("a read minted a uid")

    monkeypatch.setattr(component_identity, "uid_for", no_mint)
    for _ in range(2):
        _get(client)
        _propose(client, "detach", {"keys": [GAS]})
        _propose(client, "undo_detach", {"proposal": detach["id"]})
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == before


def test_another_org_cannot_apply_a_prepared_detach(client, other_org_client, scene):
    _place(client, BESS)
    p = _propose(client, "detach", {"keys": [BESS]})
    r = other_org_client.post(_url(f"/proposals/{p['id']}/apply"),
                              json={"expected_revision": p["base_revision"], "input_hash": p["input_hash"]})
    assert r.status_code == 404, r.text
    assert BESS in _get(client)["site"]["placements"]


def test_a_stale_detach_refuses_and_writes_nothing(client, scene):
    _place(client, BESS, GAS)
    p = _propose(client, "detach", {"keys": [BESS]})
    _detach(client, GAS)
    r = _apply(client, p, ok=False)
    assert r.status_code == 409, r.text
    assert BESS in _get(client)["site"]["placements"]


# ── several items in one proposal (group editing) ──

def test_several_items_place_in_one_revision(client, scene):
    revision = _get(client)["revision"]
    p = _propose(client, "place_existing", {"placements": {
        BESS: {"x": 60.0, "y": -50.0, "heading": 0}, GAS: {"x": 65.0, "y": -50.0, "heading": 90},
        TANK: {"x": 70.0, "y": -50.0, "heading": 180}}})
    _apply(client, p)
    got = _get(client)
    assert got["revision"] == revision + 1 and {BESS, GAS, TANK} <= set(got["site"]["placements"])


def test_one_refused_item_refuses_the_whole_group_move(client, scene):
    _place(client, BESS, GAS)
    before = _get(client)
    r = _propose(client, "place_existing", {"placements": {
        BESS: {"x": 61.0, "y": -50.0, "heading": 0},
        SOLAR: {"x": 0.0, "y": 0.0, "heading": 0, "on": f"roof:{A}"}}}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "off_roof" and "solar" in _detail(r)["message"]
    assert _detail(r)["key"] == SOLAR                              # the UI names the refused item
    got = _get(client)
    assert got["revision"] == before["revision"] and got["site"]["placements"] == before["site"]["placements"]


def test_findings_of_a_group_move_are_per_item(client, scene):
    p = _propose(client, "place_existing", {"placements": {
        BESS: {"x": 30.0, "y": -60.0, "heading": 0}, GAS: {"x": 300.0, "y": 300.0, "heading": 0}}})
    assert [f["objects"][0] for f in p["preview"]["findings"]] == [BESS]


# ── the states detach makes reachable: a binding with no placement, an empty group (GK B condition 5) ──

def test_a_sites_put_after_a_detach_is_accepted_and_undo_still_finds_the_instance(client, scene):
    # stamp() prunes the binding of a key no longer placed; undo checks the uid its receipt kept
    _complex(client)
    detach, _ = _detach(client, BESS)
    doc = client.get(f"/api/projects/{NAME}/sites")
    assert doc.status_code == 200, doc.text
    r = client.put(f"/api/projects/{NAME}/sites", json=doc.json())
    assert r.status_code == 200, r.text
    got = _get(client)
    assert BESS not in got["unbound_keys"] and {g["id"]: g["members"] for g in got["groups"]}["yard"] == []
    _apply(client, _propose(client, "undo_detach", {"proposal": detach["id"]}))
    assert BESS in _get(client)["site"]["placements"]
    assert isinstance(ss.read_sites(scene)["bindings"][BESS], str)


def test_a_component_deleted_after_its_detach_cannot_be_restored(client, scene):
    _place(client, BESS)
    detach, _ = _detach(client, BESS)
    assert client.delete("/api/network/storage_units/bess").status_code in (200, 204)
    got = _get(client)                                             # no 500, nothing listed unbound
    assert BESS not in got["site"]["placements"] and BESS not in got["unbound_keys"]
    r = _propose(client, "undo_detach", {"proposal": detach["id"]}, ok=False)
    assert r.status_code == 422 and _detail(r)["code"] == "unbound_target"


def test_an_empty_group_reads_back_and_binds_again(client, scene):
    _complex(client)
    _detach(client, BESS)
    got = _get(client)
    _bind(client, [{"id": a["id"]} for a in got["associations"]],
          [{"id": g["id"], "label": g["label"], "members": g["members"]} for g in got["groups"]])
    assert {g["id"]: g["members"] for g in _get(client)["groups"]}["yard"] == []


def test_other_actions_keep_their_receipt_shape(client, scene):
    # GK B condition 6: only a detach's receipt carries `detached`
    receipt = _place(client, BESS)
    assert set(receipt) == {"proposal_id", "input_hash", "revision", "changed"}
    _, detached = _detach(client, BESS)
    assert set(detached) == {"proposal_id", "input_hash", "revision", "changed", "detached"}
