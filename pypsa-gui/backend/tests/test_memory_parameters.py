"""Assistant memory slice 03: reusable parameter suggestions.

GK B's GO conditions (issue 03 ## Comments) are the tests: Save for reuse reads the
value from the project, never the client or the model (P1); a cost value carries its
money stamp and is never converted or relabelled (P2); carrier, unit and vanished
fields refuse by kind (P3, M6); the card's digest binds what it shows and a moved item
refuses (P4); the apply writes through `update_component`, stamps provenance as §7.1,
is undone exactly and bumps `apply_count` without a revision (P5, M5); org separation
holds for user and org items (P6, M1).
"""
from __future__ import annotations

import json
import uuid

import pytest

from services.asset_schema import provenance as PV

SUGGEST = "/api/memory/suggestions"
SAVE = "/api/memory/parameters"
APPLY = "/api/network/memory_parameter/apply"


@pytest.fixture
def proj(api_project, project_row, client, bound_ctx):
    """A real bound project "demo" (Generators gas and solar), and helpers."""
    name = api_project("demo")
    row = project_row(name)

    class P:
        id = str(row.id)
        org_id = row.org_id

        @staticmethod
        def net():
            with bound_ctx(client):
                from services.pypsa_service import PyPSAService

                return PyPSAService.get_network()

        @staticmethod
        def stamp(field="marginal_cost", name="gas", currency="EUR", year=2024, source="pack:x"):
            PV.record(P.net(), "Generator", name, field, status="default", source=source,
                      currency=currency, currency_year=year)

    return P


def _save(client, proj, field="marginal_cost", name="gas", **extra):
    return client.post(SAVE, json={"project_id": proj.id, "component_class": "Generator",
                                   "name": name, "field": field, **extra})


def _chips(client, proj, name="gas"):
    r = client.get(SUGGEST, params={"project_id": proj.id, "component_class": "Generator", "name": name})
    assert r.status_code == 200, r.text
    return r.json()["fields"]


def _preview(client, proj, chip, name="gas"):
    return client.post(f"{SUGGEST}/{chip['id']}/preview", json={
        "project_id": proj.id, "component_class": "Generator", "name": name, "version": chip["version"]})


def _apply(client, proj, chip, digest, name="gas"):
    return client.post(APPLY, json={"item_id": chip["id"], "version": chip["version"], "project_id": proj.id,
                                    "component_class": "Generator", "name": name, "digest": digest})


def _db(_auth_db):
    return _auth_db[1]()


def _row(_auth_db, public_id):
    from db.models import MemoryItem

    with _db(_auth_db) as db:
        return db.get(MemoryItem, uuid.UUID(public_id[4:]))


def _set(_auth_db, public_id, **cols):
    from db.models import MemoryItem

    with _db(_auth_db) as db:
        item = db.get(MemoryItem, uuid.UUID(public_id[4:]))
        for k, v in cols.items():
            setattr(item, k, v)
        db.commit()


def _set_value(proj, value, field="marginal_cost", name="gas"):
    proj.net().generators.at[name, field] = value


# ── Save for reuse ────────────────────────────────────────────────────────────

def test_save_reads_value_unit_carrier_and_money_from_the_project(client, proj):
    proj.stamp()
    r = _save(client, proj)
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["kind"] == "parameter" and item["status"] == "confirmed" and item["origin"] == "button"
    assert item["body"] == {"component_class": "Generator", "field": "marginal_cost", "value": 50.0,
                            "unit": "EUR/MWh", "currency": "EUR", "currency_year": 2024, "carrier": "gas"}
    assert item["concept_keys"] == ["carrier:gas", "param:Generator.marginal_cost"]
    assert item["evidence"][0]["id"] == proj.id and item["evidence"][0]["access"] is True


def test_a_client_sent_value_or_unit_is_refused_not_used(client, proj):
    r = _save(client, proj, field="p_nom", value=1.0)
    assert r.status_code == 422
    r = _save(client, proj, field="p_nom", unit="kW")
    assert r.status_code == 422


def test_a_second_save_is_a_new_version_of_the_same_item(client, proj):
    a = _save(client, proj, field="p_nom").json()
    _set_value(proj, 250.0, "p_nom")
    b = _save(client, proj, field="p_nom").json()
    assert a["id"] == b["id"] and b["version"] == a["version"] + 1 and b["body"]["value"] == 250.0


def test_a_cost_field_with_no_money_basis_refuses_and_saves_nothing(client, proj):
    r = _save(client, proj)  # marginal_cost: no provenance currency, no finance case
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "memory_money_basis_missing"
    assert client.get("/api/memory/items", params={"kind": "parameter"}).json()["items"] == []


@pytest.mark.parametrize("field", ["capital_cost_typo", "carrier", "bus"])
def test_an_unknown_or_non_numeric_field_is_a_422(client, proj, field):
    r = _save(client, proj, field=field)
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "memory_field_unknown"


def test_an_unknown_class_is_a_422_never_a_500(client, proj):
    r = client.get(SUGGEST, params={"project_id": proj.id, "component_class": "Gadget"})
    assert r.status_code == 422
    r = client.post(SAVE, json={"project_id": proj.id, "component_class": "Gadget", "name": "x", "field": "p_nom"})
    assert r.status_code == 422


def test_save_needs_the_open_project(client, proj, other_org_client):
    r = client.post(SAVE, json={"project_id": str(uuid.uuid4()), "component_class": "Generator",
                                "name": "gas", "field": "p_nom"})
    assert r.status_code == 404
    r = other_org_client.post(SAVE, json={"project_id": proj.id, "component_class": "Generator",
                                          "name": "gas", "field": "p_nom"})
    assert r.status_code == 404


# ── P1: the chat path never writes a parameter ───────────────────────────────

def test_the_model_cannot_propose_a_parameter_item():
    from fastapi import HTTPException

    from services import memory_tools

    body = {"component_class": "Generator", "field": "p_nom", "value": 1.0, "unit": "MW"}
    with pytest.raises(HTTPException) as exc:
        memory_tools._checked({"kind": "parameter", "body": body,
                               "concept_keys": ["param:Generator.p_nom"]})
    assert exc.value.detail["error_kind"] == "memory_kind_unavailable"


def test_the_dispatched_propose_memory_tool_refuses_a_parameter_and_writes_nothing(client, proj):
    from fastapi import HTTPException

    from services import chat_tools

    body = {"component_class": "Generator", "field": "p_nom", "value": 1.0, "unit": "MW"}
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["propose_memory"](
            kind="parameter", summary="Gas size", body=body, concept_keys=["param:Generator.p_nom"])
    assert exc.value.detail["error_kind"] == "memory_kind_unavailable"
    assert client.get("/api/memory/items", params={"kind": "parameter", "status": "all"}).json()["items"] == []


def test_the_items_route_refuses_a_parameter_body(client):
    r = client.post("/api/memory/items", json={
        "kind": "parameter", "concept_keys": ["param:Generator.p_nom"],
        "body": {"component_class": "Generator", "field": "p_nom", "value": 1.0, "unit": "MW"}})
    assert r.status_code == 422 and "parameter" in r.text  # not a kind the route takes


def test_a_parameter_summary_edits_but_its_body_does_not(client, proj):
    item = _save(client, proj, field="p_nom").json()
    r = client.patch(f"/api/memory/items/{item['id']}", json={"summary": "Gas size"})
    assert r.status_code == 200, r.text
    r = client.patch(f"/api/memory/items/{item['id']}", json={"body": {**item["body"], "value": 9.0}})
    assert r.status_code in (400, 422) and "memory_kind_unavailable" in r.text


# ── Suggestions (M1, P3, P6) ─────────────────────────────────────────────────

def test_a_saved_value_comes_back_as_a_chip_with_source_date_and_money(client, proj):
    proj.stamp()
    item = _save(client, proj).json()
    chip = _chips(client, proj)["marginal_cost"][0]
    assert chip["id"] == item["id"] and chip["value"] == 50.0 and chip["currency"] == "EUR"
    assert chip["currency_year"] == 2024 and chip["saved_at"] and chip["source"]["label"] == "demo"


def test_no_item_no_chip(client, proj):
    assert _chips(client, proj) == {}


def test_a_carrier_item_is_suggested_only_for_the_same_carrier(client, proj):
    _save(client, proj, field="p_nom")  # gas
    assert "p_nom" in _chips(client, proj, "gas")
    assert "p_nom" not in _chips(client, proj, "solar")


def test_a_retired_item_is_never_suggested(client, proj):
    item = _save(client, proj, field="p_nom").json()
    assert client.post(f"/api/memory/items/{item['id']}/retire").status_code == 200
    assert _chips(client, proj) == {}


@pytest.mark.parametrize("scope", ["user", "org"])
def test_an_item_of_another_org_is_never_suggested_or_applied(client, proj, _auth_db, second_identity, scope):
    item = _save(client, proj, field="p_nom").json()
    chip = _chips(client, proj)["p_nom"][0]
    _set_value(proj, 10.0, "p_nom")
    digest = _preview(client, proj, chip).json()["digest"]
    _set(_auth_db, item["id"], org_id=second_identity["org_id"], owner_scope=scope,
         user_id=None if scope == "org" else uuid.UUID(str(_row(_auth_db, item["id"]).user_id)))
    assert _chips(client, proj) == {}
    assert _preview(client, proj, chip).status_code == 404
    assert _apply(client, proj, chip, digest).status_code == 404


def test_an_org_item_of_the_projects_org_is_suggested(client, proj, _auth_db):
    item = _save(client, proj, field="p_nom").json()
    _set(_auth_db, item["id"], owner_scope="org", user_id=None)
    assert _chips(client, proj)["p_nom"][0]["scope"] == "org"


def test_another_user_reads_no_suggestions_of_a_project_they_cannot_open(client, proj, other_org_client):
    _save(client, proj, field="p_nom")
    r = other_org_client.get(SUGGEST, params={"project_id": proj.id, "component_class": "Generator",
                                              "name": "gas"})
    assert r.status_code == 404


def test_a_source_label_needs_access_to_the_source_project(client, proj, _auth_db):
    from db.models import MemoryItemVersion
    from sqlalchemy import select

    item = _save(client, proj, field="p_nom").json()
    with _db(_auth_db) as db:
        v = db.scalar(select(MemoryItemVersion).where(MemoryItemVersion.item_id == uuid.UUID(item["id"][4:])))
        v.evidence_json = json.dumps([{"kind": "project", "id": str(uuid.uuid4()), "label": "Secret"}])
        db.commit()
    src = _chips(client, proj)["p_nom"][0]["source"]
    assert src["access"] is False and src["label"] != "Secret"


def test_only_items_of_the_projects_kind_or_any_kind_are_suggested(client, proj, _auth_db):
    item = _save(client, proj, field="p_nom").json()
    _set(_auth_db, item["id"], project_kind="connection")  # the demo project has no kind
    assert _chips(client, proj) == {}
    _set(_auth_db, item["id"], project_kind=None)
    assert [c["id"] for c in _chips(client, proj)["p_nom"]] == [item["id"]]


def test_chips_rank_by_times_applied_and_cap_at_three(client, proj, _auth_db):
    ids = []
    for i in range(4):
        item = _save(client, proj, field="p_nom").json()
        # Four distinct items: a button save on the same key is a new version, so
        # move each aside (as if saved for another kind) before the next save.
        _set(_auth_db, item["id"], project_kind="connection", apply_count=i)
        ids.append(item["id"])
    for i in ids:
        _set(_auth_db, i, project_kind=None)
    assert [c["id"] for c in _chips(client, proj)["p_nom"]] == ids[::-1][:3]


# ── Preview and apply (M6, P2–P5) ────────────────────────────────────────────

def _saved_chip(client, proj, field="p_nom", then=10.0):
    _save(client, proj, field=field)
    _set_value(proj, then, field)
    return _chips(client, proj)[field][0]


def test_preview_shows_the_card_and_every_memory_fact(client, proj):
    chip = _saved_chip(client, proj)
    r = _preview(client, proj, chip)
    assert r.status_code == 200, r.text
    card = r.json()
    m = card["memory"]
    assert m["item"] == chip["id"] and m["version"] == chip["version"] and m["value"] == 200.0
    assert m["unit"] == "MW" and m["carrier"] == "gas" and m["target"]["value"] == 10.0
    assert "target" in m and card["digest"] != card["card_digest"]
    assert any(row.get("field") == "p_nom" for row in card["rows"])
    assert proj.net().generators.at["gas", "p_nom"] == 10.0  # the preview wrote nothing


def test_apply_writes_stamps_and_bumps_apply_count_without_a_revision(client, proj, _auth_db):
    from db.models import MemoryRevision

    PV.record(proj.net(), "Generator", "gas", "p_nom", status="customised", source="typed:before")
    chip = _saved_chip(client, proj)
    with _db(_auth_db) as db:
        before = [(r.scope_key, r.revision) for r in db.query(MemoryRevision).all()]
    card = _preview(client, proj, chip).json()
    r = _apply(client, proj, chip, card["digest"])
    assert r.status_code == 200, r.text
    out = r.json()
    n = proj.net()
    assert n.generators.at["gas", "p_nom"] == 200.0
    entry = PV.get(n, "Generator", "gas")["p_nom"]
    assert entry["status"] == "customised" and entry["source"] == f"memory:{chip['id']}@v{chip['version']}"
    assert entry["replaces"] == "typed:before" and entry["approved_by"] and entry["approved_at"]
    assert out["apply_counted"] is True
    row = _row(_auth_db, chip["id"])
    assert row.apply_count == 1 and row.last_used_at is not None
    with _db(_auth_db) as db:
        assert [(r.scope_key, r.revision) for r in db.query(MemoryRevision).all()] == before


def test_undo_restores_the_value_and_the_provenance_entry_exactly(client, proj):
    PV.record(proj.net(), "Generator", "gas", "p_nom", status="customised", source="typed:before")
    chip = _saved_chip(client, proj)
    before = json.dumps(PV.get(proj.net(), "Generator", "gas").get("p_nom"), sort_keys=True)
    card = _preview(client, proj, chip).json()
    assert _apply(client, proj, chip, card["digest"]).status_code == 200
    assert client.post("/api/network/undo").status_code == 200
    n = proj.net()
    assert n.generators.at["gas", "p_nom"] == 10.0
    assert json.dumps(PV.get(n, "Generator", "gas").get("p_nom"), sort_keys=True) == before


def test_a_failed_apply_count_leaves_the_apply_standing(client, proj, monkeypatch):
    from services import memory_store as M

    chip = _saved_chip(client, proj)
    card = _preview(client, proj, chip).json()

    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(M, "record_apply", boom)
    r = _apply(client, proj, chip, card["digest"])
    assert r.status_code == 200 and r.json()["apply_counted"] is False
    assert proj.net().generators.at["gas", "p_nom"] == 200.0


def test_the_change_log_entry_belongs_to_the_projects_org(client, proj):
    from services import change_log_service

    chip = _saved_chip(client, proj)
    card = _preview(client, proj, chip).json()
    assert _apply(client, proj, chip, card["digest"]).status_code == 200
    entries = change_log_service.get_all(org_id=str(proj.org_id))
    newest = entries[0]  # get_all is newest first
    assert newest["component_type"] == "Generator" and newest["org_id"] == str(proj.org_id)


def test_a_stale_digest_refuses_and_writes_nothing(client, proj):
    chip = _saved_chip(client, proj)
    card = _preview(client, proj, chip).json()
    _set_value(proj, 12.0)  # the target moved after the card was shown
    _set_value(proj, 12.0, "p_nom")
    r = _apply(client, proj, chip, card["digest"])
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "edit_preview_stale"
    assert proj.net().generators.at["gas", "p_nom"] == 12.0
    r = _apply(client, proj, chip, "never-shown")
    assert r.status_code == 409


def test_a_newer_version_or_a_retired_item_gives_409(client, proj):
    chip = _saved_chip(client, proj)
    card = _preview(client, proj, chip).json()
    _save(client, proj, field="p_nom")  # version 2 (value 10.0)
    r = _apply(client, proj, chip, card["digest"])
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "memory_stale"
    assert client.post(f"/api/memory/items/{chip['id']}/retire").status_code == 200
    r = _apply(client, proj, {**chip, "version": chip["version"] + 1}, card["digest"])
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "memory_status"


def test_a_vanished_field_is_refused_and_never_applied(client, proj, _auth_db):
    from db.models import MemoryItemVersion
    from sqlalchemy import select

    chip = _saved_chip(client, proj)
    with _db(_auth_db) as db:
        v = db.scalar(select(MemoryItemVersion).where(MemoryItemVersion.item_id == uuid.UUID(chip["id"][4:])))
        body = json.loads(v.body_json)
        v.body_json = json.dumps({**body, "field": "p_nom_gone"})
        db.commit()
    assert _chips(client, proj) == {}  # never offered (M6)
    r = _preview(client, proj, chip)
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "memory_field_unknown"


def test_a_unit_mismatch_is_refused(client, proj, _auth_db):
    from db.models import MemoryItemVersion
    from sqlalchemy import select

    chip = _saved_chip(client, proj)
    with _db(_auth_db) as db:
        v = db.scalar(select(MemoryItemVersion).where(MemoryItemVersion.item_id == uuid.UUID(chip["id"][4:])))
        v.body_json = json.dumps({**json.loads(v.body_json), "unit": "kW"})
        db.commit()
    assert _chips(client, proj) == {}  # never offered (M6)
    r = _preview(client, proj, chip)
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "memory_unit_mismatch"


def test_a_carrier_mismatch_is_refused(client, proj):
    chip = _saved_chip(client, proj)
    r = _preview(client, proj, chip, name="solar")
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "memory_carrier_mismatch"


@pytest.mark.parametrize(("currency", "year", "kind"), [
    ("USD", 2024, "currency_mismatch"),
    ("EUR", 2020, "memory_money_year_mismatch"),
])
def test_money_is_never_converted_or_relabelled(client, proj, currency, year, kind):
    proj.stamp()
    _save(client, proj)
    _set_value(proj, 70.0)
    proj.stamp(currency=currency, year=year)
    chip = _chips(client, proj)["marginal_cost"][0]
    r = _preview(client, proj, chip)
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == kind
    assert proj.net().generators.at["gas", "marginal_cost"] == 70.0


def test_a_cost_target_with_no_money_basis_is_refused(client, proj):
    proj.stamp()
    _save(client, proj)
    _set_value(proj, 70.0)
    PV.drop(proj.net(), "Generator", "gas")
    chip = _chips(client, proj)["marginal_cost"][0]
    r = _preview(client, proj, chip)
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "memory_money_basis_missing"


def test_a_no_change_pick_is_refused(client, proj):
    _save(client, proj, field="p_nom")
    chip = _chips(client, proj)["p_nom"][0]
    r = _preview(client, proj, chip)
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "memory_no_change"


def test_node_reads_one_version_for_the_provenance_graph(client, proj, _auth_db, seeded_identity):
    from db.models import User
    from services import memory_store as M

    item = _save(client, proj, field="p_nom").json()
    with _db(_auth_db) as db:
        user = db.get(User, seeded_identity["user_id"])
        node = M.node(db, user, item["id"], 1)
        assert node["id"] == f"{item['id']}@v1" and node["kind"] == "parameter"
        assert node["state"] == "from memory v1, user-approved"  # M9
        assert node["evidence"][0]["access"] is True
        with pytest.raises(M.MemoryError) as exc:
            M.node(db, user, item["id"], 2)
        assert exc.value.status == 404


def test_save_preview_and_apply_refuse_a_project_that_is_no_longer_open(client, proj, api_project):
    chip = _saved_chip(client, proj)
    card = _preview(client, proj, chip).json()
    api_project("other")  # the session now has another project open
    r = _save(client, proj, field="p_nom")
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "memory_project_not_active"
    assert _preview(client, proj, chip).status_code == 409
    assert _apply(client, proj, chip, card["digest"]).status_code == 409


@pytest.mark.parametrize(("change", "kind"), [
    ({"unit": "kW"}, "memory_unit_mismatch"),
    ({"field": "p_nom_gone"}, "memory_field_unknown"),
    ({"field": "carrier"}, "memory_field_unknown"),
])
def test_the_parameter_body_itself_checks_field_and_stored_unit(change, kind):
    from services import memory_store as M

    body = {"component_class": "Generator", "field": "p_nom", "value": 1.0, "unit": "MW", **change}
    with pytest.raises(M.MemoryError) as exc:
        M.validate_body("parameter", body, parameter_ok=True)
    assert exc.value.kind == kind and exc.value.status == 422
    assert M.validate_body("parameter", {**body, **{"unit": "MW", "field": "p_nom"}},
                           parameter_ok=True)["unit"] == "MW"


def test_a_non_parameter_item_is_not_found_by_preview_or_apply(client, proj):
    pref = client.post("/api/memory/items", json={
        "kind": "preference", "concept_keys": ["topic:units"],
        "body": {"key": "units.power", "value": "MW"}})
    assert pref.status_code == 201, pref.text
    chip = {"id": pref.json()["id"], "version": 1}
    assert _preview(client, proj, chip).status_code == 404
    assert _apply(client, proj, chip, "any").status_code == 404


# ── Survivors of scripts/coord/mutants.py (GK B P7) ──────────────────────────

def test_another_members_private_item_in_the_same_org_is_never_suggested(client, proj, _auth_db):
    from tests.conftest import _seed_org_and_user

    other_user, _other_org = _seed_org_and_user(_auth_db[1], email=f"m{uuid.uuid4().hex[:6]}@example.com",
                                                org_name=f"Org {uuid.uuid4().hex[:6]}")
    item = _save(client, proj, field="p_nom").json()
    _set(_auth_db, item["id"], user_id=other_user)  # same org, someone else's own item
    assert _chips(client, proj) == {}


def _direct_item(_auth_db, seeded_identity, proj, body):
    from db.models import User
    from services import memory_store as M

    with _db(_auth_db) as db:
        user = db.get(User, seeded_identity["user_id"])
        return M.create_item(db, user, kind="parameter", body=body,
                             concept_keys=[f"param:{body['component_class']}.{body['field']}"],
                             org_id=proj.org_id, parameter_ok=True).public_id


def test_an_item_of_another_class_is_neither_suggested_nor_applied(client, proj, _auth_db, seeded_identity):
    iid = _direct_item(_auth_db, seeded_identity, proj,
                       {"component_class": "Link", "field": "p_nom", "value": 7.0, "unit": "MW"})
    assert _chips(client, proj) == {}
    r = _preview(client, proj, {"id": iid, "version": 1})
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "memory_field_unknown"


def test_an_item_with_no_carrier_applies_to_any_carrier(client, proj, _auth_db, seeded_identity):
    iid = _direct_item(_auth_db, seeded_identity, proj,
                       {"component_class": "Generator", "field": "p_nom", "value": 7.0, "unit": "MW"})
    assert [c["id"] for c in _chips(client, proj, "solar")["p_nom"]] == [iid]
    r = _preview(client, proj, {"id": iid, "version": 1}, name="solar")
    assert r.status_code == 200, r.text


def test_a_field_with_no_finite_value_is_not_saved(client, proj):
    _set_value(proj, float("inf"), "p_nom_max")
    r = _save(client, proj, field="p_nom_max")
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "memory_value_missing"


def test_an_unknown_asset_is_a_404_by_kind(client, proj):
    r = _save(client, proj, field="p_nom", name="nope")
    assert r.status_code == 404 and r.json()["detail"]["error_kind"] == "memory_target_unknown"


@pytest.mark.parametrize(("currency", "year"), [(None, 2024), ("EUR", None)])
def test_save_needs_both_currency_and_money_year(client, proj, currency, year):
    proj.stamp(currency=currency, year=year)
    r = _save(client, proj)
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "memory_money_basis_missing"


def test_an_unknown_class_frame_is_refused_by_kind():
    import pypsa

    from services import memory_parameters as P
    from services import memory_store as M

    with pytest.raises(M.MemoryError) as exc:
        P._frame(pypsa.Network(), "Gadget")
    assert exc.value.kind == "memory_field_unknown" and exc.value.status == 422


class _Fin:
    def __init__(self, currency="CHF", currency_year=None):
        self.currency, self.currency_year = currency, currency_year


@pytest.mark.parametrize(("entry", "fin", "base", "want"), [
    ({}, None, 2030, (None, None)),                          # nothing states it: nothing guessed
    ({}, _Fin("CHF", 2022), 2030, ("CHF", 2022)),             # the case's currency and money year
    ({}, _Fin("CHF", None), 2030, ("CHF", 2030)),             # the case's base year when no money year
    ({"currency": "USD", "currency_year": 2019}, _Fin("CHF", 2022), 2030, ("USD", 2019)),  # provenance first
])
def test_money_stamp_reads_provenance_then_the_case(monkeypatch, entry, fin, base, want):
    from services import memory_parameters as P
    from services.asset_schema import provenance
    from services.library import reference_apply

    monkeypatch.setattr(provenance, "get", lambda *_a: {"marginal_cost": entry})
    monkeypatch.setattr(P, "_finance", lambda: fin)
    monkeypatch.setattr(P, "_finance_config", lambda: object())
    monkeypatch.setattr(reference_apply, "case_base_year", lambda *_a: base)
    assert P.money_stamp(None, "Generator", "gas", "marginal_cost") == want


# ── Second mutant pass ───────────────────────────────────────────────────────

def test_suggestions_need_the_asset_so_the_carrier_filter_always_holds(client, proj):
    _save(client, proj, field="p_nom")  # gas
    r = client.get(SUGGEST, params={"project_id": proj.id, "component_class": "Generator"})
    assert r.status_code == 422


def test_last_used_ranks_before_last_updated(client, proj, _auth_db):
    from datetime import UTC, datetime

    used = _save(client, proj, field="p_nom").json()
    _set(_auth_db, used["id"], project_kind="connection")
    fresh = _save(client, proj, field="p_nom").json()  # updated later, never used
    _set(_auth_db, used["id"], project_kind=None, last_used_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert [c["id"] for c in _chips(client, proj)["p_nom"]] == [used["id"], fresh["id"]]


@pytest.mark.parametrize(("currency", "year"), [(None, 2024), ("EUR", None)])
def test_a_cost_target_missing_currency_or_money_year_is_refused(client, proj, monkeypatch, currency, year):
    from services import memory_parameters as P

    proj.stamp()
    _save(client, proj)
    _set_value(proj, 70.0)
    monkeypatch.setattr(P, "money_stamp", lambda *_a: (currency, year))
    chip = _chips(client, proj)["marginal_cost"][0]
    r = _preview(client, proj, chip)
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "memory_money_basis_missing"


def test_the_preview_note_names_the_replaced_values_source(client, proj):
    chip = _saved_chip(client, proj)
    assert "(source: typed)" in _preview(client, proj, chip).json()["memory_note"]
    proj.stamp(field="p_nom", source="pack:x")
    assert "(source: pack:x)" in _preview(client, proj, chip).json()["memory_note"]


def test_a_carrier_refusal_names_an_asset_without_a_carrier(client, proj):
    chip = _saved_chip(client, proj)
    proj.net().generators.at["solar", "carrier"] = ""
    r = _preview(client, proj, chip, name="solar")
    assert r.status_code == 422 and "without a carrier" in r.json()["detail"]["message"]


@pytest.mark.parametrize(("raw", "want"), [
    (None, None),
    ({"currency": "USD", "currency_year": 2020, "financial_close": "2025-01-01"}, ("USD", 2020)),
])
def test_finance_reads_the_case_only_when_it_states_one(monkeypatch, raw, want):
    from services import memory_parameters as P

    monkeypatch.setattr(P, "_finance_config", lambda: type("C", (), {"finance": raw})())
    fin = P._finance()
    assert (None if fin is None else (fin.currency, fin.currency_year)) == want


# ── Spec review ──────────────────────────────────────────────────────────────

def test_each_carrier_keeps_its_own_saved_value(client, proj):
    gas = _save(client, proj, field="p_nom", name="gas").json()
    solar = _save(client, proj, field="p_nom", name="solar").json()
    assert gas["id"] != solar["id"] and gas["version"] == solar["version"] == 1
    assert [c["id"] for c in _chips(client, proj, "gas")["p_nom"]] == [gas["id"]]
    assert [c["id"] for c in _chips(client, proj, "solar")["p_nom"]] == [solar["id"]]


def test_a_carrier_name_with_spaces_saves_and_suggests(client, proj):
    proj.net().generators.at["solar", "carrier"] = "solar rooftop"
    r = _save(client, proj, field="p_nom", name="solar")
    assert r.status_code == 201, r.text
    assert "carrier:solar rooftop" in r.json()["concept_keys"]
    assert _chips(client, proj, "solar")["p_nom"][0]["carrier"] == "solar rooftop"


def test_a_chip_equal_to_the_fields_value_is_marked_current(client, proj):
    _save(client, proj, field="p_nom")
    assert _chips(client, proj)["p_nom"][0]["current"] is True
    _set_value(proj, 10.0, "p_nom")
    assert _chips(client, proj)["p_nom"][0]["current"] is False


def test_the_preview_note_shows_no_internal_id(client, proj):
    chip = _saved_chip(client, proj)
    note = _preview(client, proj, chip).json()["memory_note"]
    assert chip["id"] not in note and "mem_" not in note and "Replaces 10 " in note
