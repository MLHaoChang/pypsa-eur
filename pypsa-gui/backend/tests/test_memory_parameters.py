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
        def stamp(
            field="marginal_cost",
            name="gas",
            currency="EUR",
            year=2024,
            source="pack:x",
        ):
            PV.record(
                P.net(),
                "Generator",
                name,
                field,
                status="default",
                source=source,
                currency=currency,
                currency_year=year,
            )

    return P


def _save(client, proj, field="marginal_cost", name="gas", **extra):
    return client.post(
        SAVE,
        json={
            "project_id": proj.id,
            "component_class": "Generator",
            "name": name,
            "field": field,
            **extra,
        },
    )


def _chips(client, proj, name="gas"):
    r = client.get(
        SUGGEST,
        params={"project_id": proj.id, "component_class": "Generator", "name": name},
    )
    assert r.status_code == 200, r.text
    return r.json()["fields"]


def _preview(client, proj, chip, name="gas"):
    return client.post(
        f"{SUGGEST}/{chip['id']}/preview",
        json={
            "project_id": proj.id,
            "component_class": "Generator",
            "name": name,
            "version": chip["version"],
        },
    )


def _apply(client, proj, chip, digest, name="gas"):
    return client.post(
        APPLY,
        json={
            "item_id": chip["id"],
            "version": chip["version"],
            "project_id": proj.id,
            "component_class": "Generator",
            "name": name,
            "digest": digest,
        },
    )


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
    assert (
        item["kind"] == "parameter"
        and item["status"] == "confirmed"
        and item["origin"] == "button"
    )
    assert item["body"] == {
        "component_class": "Generator",
        "field": "marginal_cost",
        "value": 50.0,
        "unit": "EUR/MWh",
        "currency": "EUR",
        "currency_year": 2024,
        "carrier": "gas",
    }
    assert item["concept_keys"] == ["carrier:gas", "param:Generator.marginal_cost"]
    assert (
        item["evidence"][0]["id"] == proj.id and item["evidence"][0]["access"] is True
    )


def test_a_client_sent_value_or_unit_is_refused_not_used(client, proj):
    r = _save(client, proj, field="p_nom", value=1.0)
    assert r.status_code == 422
    r = _save(client, proj, field="p_nom", unit="kW")
    assert r.status_code == 422


def test_a_second_save_is_a_new_version_of_the_same_item(client, proj):
    a = _save(client, proj, field="p_nom").json()
    _set_value(proj, 250.0, "p_nom")
    b = _save(client, proj, field="p_nom").json()
    assert (
        a["id"] == b["id"]
        and b["version"] == a["version"] + 1
        and b["body"]["value"] == 250.0
    )


def test_a_cost_field_with_no_money_basis_refuses_and_saves_nothing(client, proj):
    r = _save(client, proj)  # marginal_cost: no provenance currency, no finance case
    assert (
        r.status_code == 422
        and r.json()["detail"]["error_kind"] == "memory_money_basis_missing"
    )
    assert (
        client.get("/api/memory/items", params={"kind": "parameter"}).json()["items"]
        == []
    )


@pytest.mark.parametrize("field", ["capital_cost_typo", "carrier", "bus"])
def test_an_unknown_or_non_numeric_field_is_a_422(client, proj, field):
    r = _save(client, proj, field=field)
    assert (
        r.status_code == 422
        and r.json()["detail"]["error_kind"] == "memory_field_unknown"
    )


def test_an_unknown_class_is_a_422_never_a_500(client, proj):
    r = client.get(SUGGEST, params={"project_id": proj.id, "component_class": "Gadget"})
    assert r.status_code == 422
    r = client.post(
        SAVE,
        json={
            "project_id": proj.id,
            "component_class": "Gadget",
            "name": "x",
            "field": "p_nom",
        },
    )
    assert r.status_code == 422


def test_save_needs_the_open_project(client, proj, other_org_client):
    r = client.post(
        SAVE,
        json={
            "project_id": str(uuid.uuid4()),
            "component_class": "Generator",
            "name": "gas",
            "field": "p_nom",
        },
    )
    assert r.status_code == 404
    r = other_org_client.post(
        SAVE,
        json={
            "project_id": proj.id,
            "component_class": "Generator",
            "name": "gas",
            "field": "p_nom",
        },
    )
    assert r.status_code == 404


# ── P1: the chat path never writes a parameter ───────────────────────────────


def test_the_model_cannot_propose_a_parameter_item():
    from fastapi import HTTPException

    from services import memory_tools

    body = {
        "component_class": "Generator",
        "field": "p_nom",
        "value": 1.0,
        "unit": "MW",
    }
    with pytest.raises(HTTPException) as exc:
        memory_tools._checked(
            {
                "kind": "parameter",
                "body": body,
                "concept_keys": ["param:Generator.p_nom"],
            }
        )
    assert exc.value.detail["error_kind"] == "memory_kind_unavailable"


def test_the_dispatched_propose_memory_tool_refuses_a_parameter_and_writes_nothing(
    client, proj
):
    from fastapi import HTTPException

    from services import chat_tools

    body = {
        "component_class": "Generator",
        "field": "p_nom",
        "value": 1.0,
        "unit": "MW",
    }
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["propose_memory"](
            kind="parameter",
            summary="Gas size",
            body=body,
            concept_keys=["param:Generator.p_nom"],
        )
    assert exc.value.detail["error_kind"] == "memory_kind_unavailable"
    assert (
        client.get(
            "/api/memory/items", params={"kind": "parameter", "status": "all"}
        ).json()["items"]
        == []
    )


def test_the_items_route_refuses_a_parameter_body(client):
    r = client.post(
        "/api/memory/items",
        json={
            "kind": "parameter",
            "concept_keys": ["param:Generator.p_nom"],
            "body": {
                "component_class": "Generator",
                "field": "p_nom",
                "value": 1.0,
                "unit": "MW",
            },
        },
    )
    assert r.status_code == 422 and "parameter" in r.text  # not a kind the route takes


def test_a_parameter_summary_edits_but_its_body_does_not(client, proj):
    item = _save(client, proj, field="p_nom").json()
    r = client.patch(f"/api/memory/items/{item['id']}", json={"summary": "Gas size"})
    assert r.status_code == 200, r.text
    r = client.patch(
        f"/api/memory/items/{item['id']}", json={"body": {**item["body"], "value": 9.0}}
    )
    assert r.status_code in (400, 422) and "memory_kind_unavailable" in r.text


# ── Suggestions (M1, P3, P6) ─────────────────────────────────────────────────


def test_a_saved_value_comes_back_as_a_chip_with_source_date_and_money(client, proj):
    proj.stamp()
    item = _save(client, proj).json()
    chip = _chips(client, proj)["marginal_cost"][0]
    assert (
        chip["id"] == item["id"] and chip["value"] == 50.0 and chip["currency"] == "EUR"
    )
    assert (
        chip["currency_year"] == 2024
        and chip["saved_at"]
        and chip["source"]["label"] == "demo"
    )


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
def test_an_item_of_another_org_is_never_suggested_or_applied(
    client, proj, _auth_db, second_identity, scope
):
    item = _save(client, proj, field="p_nom").json()
    chip = _chips(client, proj)["p_nom"][0]
    _set_value(proj, 10.0, "p_nom")
    digest = _preview(client, proj, chip).json()["digest"]
    _set(
        _auth_db,
        item["id"],
        org_id=second_identity["org_id"],
        owner_scope=scope,
        user_id=None
        if scope == "org"
        else uuid.UUID(str(_row(_auth_db, item["id"]).user_id)),
    )
    assert _chips(client, proj) == {}
    assert _preview(client, proj, chip).status_code == 404
    assert _apply(client, proj, chip, digest).status_code == 404


def test_an_org_item_of_the_projects_org_is_suggested(client, proj, _auth_db):
    item = _save(client, proj, field="p_nom").json()
    _set(_auth_db, item["id"], owner_scope="org", user_id=None)
    assert _chips(client, proj)["p_nom"][0]["scope"] == "org"


def test_another_user_reads_no_suggestions_of_a_project_they_cannot_open(
    client, proj, other_org_client
):
    _save(client, proj, field="p_nom")
    r = other_org_client.get(
        SUGGEST,
        params={"project_id": proj.id, "component_class": "Generator", "name": "gas"},
    )
    assert r.status_code == 404


def test_a_source_label_needs_access_to_the_source_project(client, proj, _auth_db):
    from db.models import MemoryItemVersion
    from sqlalchemy import select

    item = _save(client, proj, field="p_nom").json()
    with _db(_auth_db) as db:
        v = db.scalar(
            select(MemoryItemVersion).where(
                MemoryItemVersion.item_id == uuid.UUID(item["id"][4:])
            )
        )
        v.evidence_json = json.dumps(
            [{"kind": "project", "id": str(uuid.uuid4()), "label": "Secret"}]
        )
        db.commit()
    src = _chips(client, proj)["p_nom"][0]["source"]
    assert src["access"] is False and src["label"] != "Secret"


def test_only_items_of_the_projects_kind_or_any_kind_are_suggested(
    client, proj, _auth_db
):
    item = _save(client, proj, field="p_nom").json()
    _set(
        _auth_db, item["id"], project_kind="connection"
    )  # the demo project has no kind
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
    assert (
        m["item"] == chip["id"]
        and m["version"] == chip["version"]
        and m["value"] == 200.0
    )
    assert m["unit"] == "MW" and m["carrier"] == "gas" and m["target"]["value"] == 10.0
    assert m["label"] and m["field"] == "p_nom"
    assert (
        m["target"]["component_class"] == "Generator" and m["target"]["name"] == "gas"
    )
    assert "target" in m and card["digest"] != card["card_digest"]
    assert any(row.get("field") == "p_nom" for row in card["rows"])
    assert proj.net().generators.at["gas", "p_nom"] == 10.0  # the preview wrote nothing


def test_apply_writes_stamps_and_bumps_apply_count_without_a_revision(
    client, proj, _auth_db
):
    from db.models import MemoryRevision

    PV.record(
        proj.net(),
        "Generator",
        "gas",
        "p_nom",
        status="customised",
        source="typed:before",
    )
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
    assert (
        entry["status"] == "customised"
        and entry["source"] == f"memory:{chip['id']}@v{chip['version']}"
    )
    assert (
        entry["replaces"] == "typed:before"
        and entry["approved_by"]
        and entry["approved_at"]
    )
    assert out["apply_counted"] is True
    row = _row(_auth_db, chip["id"])
    assert row.apply_count == 1 and row.last_used_at is not None
    with _db(_auth_db) as db:
        assert [
            (r.scope_key, r.revision) for r in db.query(MemoryRevision).all()
        ] == before


def test_undo_restores_the_value_and_the_provenance_entry_exactly(client, proj):
    PV.record(
        proj.net(),
        "Generator",
        "gas",
        "p_nom",
        status="customised",
        source="typed:before",
    )
    chip = _saved_chip(client, proj)
    before = json.dumps(
        PV.get(proj.net(), "Generator", "gas").get("p_nom"), sort_keys=True
    )
    card = _preview(client, proj, chip).json()
    assert _apply(client, proj, chip, card["digest"]).status_code == 200
    assert client.post("/api/network/undo").status_code == 200
    n = proj.net()
    assert n.generators.at["gas", "p_nom"] == 10.0
    assert (
        json.dumps(PV.get(n, "Generator", "gas").get("p_nom"), sort_keys=True) == before
    )


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
    assert newest["component_type"] == "Generator" and newest["org_id"] == str(
        proj.org_id
    )


def test_a_stale_digest_refuses_and_writes_nothing(client, proj):
    chip = _saved_chip(client, proj)
    card = _preview(client, proj, chip).json()
    _set_value(proj, 12.0)  # the target moved after the card was shown
    _set_value(proj, 12.0, "p_nom")
    r = _apply(client, proj, chip, card["digest"])
    assert (
        r.status_code == 409
        and r.json()["detail"]["error_kind"] == "edit_preview_stale"
    )
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
        v = db.scalar(
            select(MemoryItemVersion).where(
                MemoryItemVersion.item_id == uuid.UUID(chip["id"][4:])
            )
        )
        body = json.loads(v.body_json)
        v.body_json = json.dumps({**body, "field": "p_nom_gone"})
        db.commit()
    assert _chips(client, proj) == {}  # never offered (M6)
    r = _preview(client, proj, chip)
    assert (
        r.status_code == 422
        and r.json()["detail"]["error_kind"] == "memory_field_unknown"
    )


def test_a_unit_mismatch_is_refused(client, proj, _auth_db):
    from db.models import MemoryItemVersion
    from sqlalchemy import select

    chip = _saved_chip(client, proj)
    with _db(_auth_db) as db:
        v = db.scalar(
            select(MemoryItemVersion).where(
                MemoryItemVersion.item_id == uuid.UUID(chip["id"][4:])
            )
        )
        v.body_json = json.dumps({**json.loads(v.body_json), "unit": "kW"})
        db.commit()
    assert _chips(client, proj) == {}  # never offered (M6)
    r = _preview(client, proj, chip)
    assert (
        r.status_code == 422
        and r.json()["detail"]["error_kind"] == "memory_unit_mismatch"
    )


def test_a_carrier_mismatch_is_refused(client, proj):
    chip = _saved_chip(client, proj)
    r = _preview(client, proj, chip, name="solar")
    assert (
        r.status_code == 422
        and r.json()["detail"]["error_kind"] == "memory_carrier_mismatch"
    )
    assert "this asset is solar" in r.json()["detail"]["message"]


@pytest.mark.parametrize(
    ("currency", "year", "kind"),
    [
        ("USD", 2024, "currency_mismatch"),
        ("EUR", 2020, "memory_money_year_mismatch"),
    ],
)
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
    assert (
        r.status_code == 422
        and r.json()["detail"]["error_kind"] == "memory_money_basis_missing"
    )


def test_a_no_change_pick_is_refused(client, proj):
    _save(client, proj, field="p_nom")
    chip = _chips(client, proj)["p_nom"][0]
    r = _preview(client, proj, chip)
    assert (
        r.status_code == 422 and r.json()["detail"]["error_kind"] == "memory_no_change"
    )


def test_node_reads_one_version_for_the_provenance_graph(
    client, proj, _auth_db, seeded_identity
):
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


def test_save_preview_and_apply_refuse_a_project_that_is_no_longer_open(
    client, proj, api_project
):
    chip = _saved_chip(client, proj)
    card = _preview(client, proj, chip).json()
    api_project("other")  # the session now has another project open
    r = _save(client, proj, field="p_nom")
    assert (
        r.status_code == 409
        and r.json()["detail"]["error_kind"] == "memory_project_not_active"
    )
    assert _preview(client, proj, chip).status_code == 409
    assert _apply(client, proj, chip, card["digest"]).status_code == 409


@pytest.mark.parametrize(
    ("change", "kind"),
    [
        ({"unit": "kW"}, "memory_unit_mismatch"),
        ({"field": "p_nom_gone"}, "memory_field_unknown"),
        ({"field": "carrier"}, "memory_field_unknown"),
        ({"carrier": "wind(on)"}, "memory_carrier_unsupported"),
        ({"carrier": "solar "}, "memory_carrier_unsupported"),
    ],
)
def test_the_parameter_body_itself_checks_field_and_stored_unit(change, kind):
    from services import memory_store as M

    body = {
        "component_class": "Generator",
        "field": "p_nom",
        "value": 1.0,
        "unit": "MW",
        **change,
    }
    with pytest.raises(M.MemoryError) as exc:
        M.validate_body("parameter", body, parameter_ok=True)
    assert exc.value.kind == kind and exc.value.status == 422
    assert (
        M.validate_body(
            "parameter",
            {k: v for k, v in body.items() if k not in change}
            | {"unit": "MW", "field": "p_nom"},
            parameter_ok=True,
        )["unit"]
        == "MW"
    )


def test_a_non_parameter_item_is_not_found_by_preview_or_apply(client, proj):
    pref = client.post(
        "/api/memory/items",
        json={
            "kind": "preference",
            "concept_keys": ["topic:units"],
            "body": {"key": "units.power", "value": "MW"},
        },
    )
    assert pref.status_code == 201, pref.text
    chip = {"id": pref.json()["id"], "version": 1}
    assert _preview(client, proj, chip).status_code == 404
    assert _apply(client, proj, chip, "any").status_code == 404


# ── Survivors of scripts/coord/mutants.py (GK B P7) ──────────────────────────


def test_another_members_private_item_in_the_same_org_is_never_suggested(
    client, proj, _auth_db
):
    from tests.conftest import _seed_org_and_user

    other_user, _other_org = _seed_org_and_user(
        _auth_db[1],
        email=f"m{uuid.uuid4().hex[:6]}@example.com",
        org_name=f"Org {uuid.uuid4().hex[:6]}",
    )
    item = _save(client, proj, field="p_nom").json()
    _set(_auth_db, item["id"], user_id=other_user)  # same org, someone else's own item
    assert _chips(client, proj) == {}


def _direct_item(_auth_db, seeded_identity, proj, body):
    from db.models import User
    from services import memory_store as M

    with _db(_auth_db) as db:
        user = db.get(User, seeded_identity["user_id"])
        return M.create_item(
            db,
            user,
            kind="parameter",
            body=body,
            concept_keys=[f"param:{body['component_class']}.{body['field']}"],
            org_id=proj.org_id,
            parameter_ok=True,
        ).public_id


def test_an_item_of_another_class_is_neither_suggested_nor_applied(
    client, proj, _auth_db, seeded_identity
):
    iid = _direct_item(
        _auth_db,
        seeded_identity,
        proj,
        {"component_class": "Link", "field": "p_nom", "value": 7.0, "unit": "MW"},
    )
    assert _chips(client, proj) == {}
    r = _preview(client, proj, {"id": iid, "version": 1})
    assert (
        r.status_code == 422
        and r.json()["detail"]["error_kind"] == "memory_field_unknown"
    )


def test_an_item_with_no_carrier_applies_to_any_carrier(
    client, proj, _auth_db, seeded_identity
):
    iid = _direct_item(
        _auth_db,
        seeded_identity,
        proj,
        {"component_class": "Generator", "field": "p_nom", "value": 7.0, "unit": "MW"},
    )
    assert [c["id"] for c in _chips(client, proj, "solar")["p_nom"]] == [iid]
    r = _preview(client, proj, {"id": iid, "version": 1}, name="solar")
    assert r.status_code == 200, r.text


def test_a_field_with_no_finite_value_is_not_saved(client, proj):
    _set_value(proj, float("inf"), "p_nom_max")
    r = _save(client, proj, field="p_nom_max")
    assert (
        r.status_code == 422
        and r.json()["detail"]["error_kind"] == "memory_value_missing"
    )


def test_an_unknown_asset_is_a_404_by_kind(client, proj):
    r = _save(client, proj, field="p_nom", name="nope")
    assert (
        r.status_code == 404
        and r.json()["detail"]["error_kind"] == "memory_target_unknown"
    )


@pytest.mark.parametrize(("currency", "year"), [(None, 2024), ("EUR", None)])
def test_save_needs_both_currency_and_money_year(client, proj, currency, year):
    proj.stamp(currency=currency, year=year)
    r = _save(client, proj)
    assert (
        r.status_code == 422
        and r.json()["detail"]["error_kind"] == "memory_money_basis_missing"
    )


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


@pytest.mark.parametrize(
    ("entry", "fin", "base", "want"),
    [
        ({}, None, 2030, (None, None)),  # nothing states it: nothing guessed
        (
            {},
            _Fin("CHF", 2022),
            2030,
            ("CHF", 2022),
        ),  # the case's currency and money year
        (
            {},
            _Fin("CHF", None),
            2030,
            ("CHF", 2030),
        ),  # the case's base year when no money year
        (
            {"currency": "USD", "currency_year": 2019},
            _Fin("CHF", 2022),
            2030,
            ("USD", 2019),
        ),  # provenance first
    ],
)
def test_money_stamp_reads_provenance_then_the_case(
    monkeypatch, entry, fin, base, want
):
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
    r = client.get(
        SUGGEST, params={"project_id": proj.id, "component_class": "Generator"}
    )
    assert r.status_code == 422


def test_last_used_ranks_before_last_updated(client, proj, _auth_db):
    from datetime import UTC, datetime

    used = _save(client, proj, field="p_nom").json()
    _set(_auth_db, used["id"], project_kind="connection")
    fresh = _save(client, proj, field="p_nom").json()  # updated later, never used
    _set(
        _auth_db,
        used["id"],
        project_kind=None,
        last_used_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert [c["id"] for c in _chips(client, proj)["p_nom"]] == [used["id"], fresh["id"]]


@pytest.mark.parametrize(("currency", "year"), [(None, 2024), ("EUR", None)])
def test_a_cost_target_missing_currency_or_money_year_is_refused(
    client, proj, monkeypatch, currency, year
):
    from services import memory_parameters as P

    proj.stamp()
    _save(client, proj)
    _set_value(proj, 70.0)
    monkeypatch.setattr(P, "money_stamp", lambda *_a: (currency, year))
    chip = _chips(client, proj)["marginal_cost"][0]
    r = _preview(client, proj, chip)
    assert (
        r.status_code == 422
        and r.json()["detail"]["error_kind"] == "memory_money_basis_missing"
    )


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


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        (None, None),
        (
            {"currency": "USD", "currency_year": 2020, "financial_close": "2025-01-01"},
            ("USD", 2020),
        ),
    ],
)
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


# ── GK B (43) findings ───────────────────────────────────────────────────────


def _other_member(_auth_db):
    from tests.conftest import _seed_org_and_user

    user_id, _org = _seed_org_and_user(
        _auth_db[1],
        email=f"m{uuid.uuid4().hex[:6]}@example.com",
        org_name=f"Org {uuid.uuid4().hex[:6]}",
    )
    return user_id


def test_an_admin_never_uses_a_members_nominated_private_value(
    client, proj, _auth_db, seeded_identity
):
    from db.models import User
    from services import memory_store as M

    chip = _saved_chip(client, proj)
    _set(_auth_db, chip["id"], user_id=_other_member(_auth_db), nomination="pending")
    with _db(_auth_db) as db:
        admin = db.get(User, seeded_identity["user_id"])
        assert M.is_admin_of(db, admin, proj.org_id)  # may read it, to review it
        assert M._can_read(db, admin, db.get(M.MemoryItem, M.parse_id(chip["id"])))
    assert _chips(client, proj) == {}
    r = _preview(client, proj, chip)
    assert r.status_code == 404
    assert _apply(client, proj, chip, "0" * 64).status_code == 404
    assert proj.net().generators.at["gas", "p_nom"] == 10.0


def test_only_a_memory_fact_moving_makes_the_apply_stale(client, proj):
    chip = _saved_chip(client, proj)
    card = _preview(client, proj, chip).json()
    proj.stamp(field="p_nom", source="pack:moved")  # the card's old → new rows stay
    assert _preview(client, proj, chip).json()["card_digest"] == card["card_digest"]
    r = _apply(client, proj, chip, card["digest"])
    assert (
        r.status_code == 409
        and r.json()["detail"]["error_kind"] == "edit_preview_stale"
    )
    assert proj.net().generators.at["gas", "p_nom"] == 10.0


def test_money_is_decided_by_the_field_not_the_stored_unit():
    from services import memory_parameters as P
    from services import memory_store as M

    assert P.is_money(
        M.parameter_spec("Bus", "product_value")
    )  # stores "", shows EUR/unit
    assert P.is_money(M.parameter_spec("Generator", "marginal_cost"))
    assert not P.is_money(M.parameter_spec("Generator", "p_nom"))
    with pytest.raises(M.MemoryError) as exc:  # t, MW or EUR by the constraint's type
        M.parameter_spec("GlobalConstraint", "constant")
    assert exc.value.kind == "memory_field_unknown" and exc.value.status == 422


def test_a_product_value_needs_a_money_basis(client, proj):
    net = proj.net()
    bus = net.buses.index[0]
    net.buses["product_value"] = float("nan")
    net.buses.at[bus, "product_value"] = 30.0
    r = client.post(
        SAVE,
        json={
            "project_id": proj.id,
            "component_class": "Bus",
            "name": bus,
            "field": "product_value",
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "memory_money_basis_missing"


def test_a_field_with_a_time_series_is_neither_saved_nor_applied(client, proj):
    proj.stamp()
    chip_item = _save(client, proj).json()
    _set_value(proj, 70.0)
    net = proj.net()
    net.generators_t.marginal_cost["gas"] = 5.0
    r = _save(client, proj)
    assert (
        r.status_code == 422
        and r.json()["detail"]["error_kind"] == "memory_field_has_series"
    )
    chip = _chips(client, proj)["marginal_cost"][0]
    assert chip["id"] == chip_item["id"]
    r = _preview(client, proj, chip)
    assert (
        r.status_code == 422
        and r.json()["detail"]["error_kind"] == "memory_field_has_series"
    )
    assert "time series" in r.json()["detail"]["message"]


def test_a_series_added_after_the_preview_refuses_the_apply(client, proj):
    proj.stamp()
    chip = _saved_chip(client, proj, field="marginal_cost", then=70.0)
    card = _preview(client, proj, chip).json()
    proj.net().generators_t.marginal_cost["gas"] = 5.0
    r = _apply(client, proj, chip, card["digest"])
    assert r.json()["detail"]["error_kind"] == "memory_field_has_series"


def test_a_parameter_items_keys_are_not_edited(client, proj, _auth_db, seeded_identity):
    from db.models import User
    from services import memory_store as M

    item = _save(client, proj, field="p_nom").json()
    r = client.patch(
        f"/api/memory/items/{item['id']}",
        json={"concept_keys": ["param:Generator.p_nom"]},
    )
    assert r.json()["detail"]["error_kind"] == "memory_kind_unavailable"
    with _db(_auth_db) as db:
        user = db.get(User, seeded_identity["user_id"])
        with pytest.raises(M.MemoryError) as exc:
            M.edit_item(db, user, item["id"], project_kind="connection")
        assert exc.value.kind == "memory_kind_unavailable"
    assert _row(_auth_db, item["id"]).current_version == 1


def test_a_parameter_item_is_not_nominated(client, proj, _auth_db, seeded_identity):
    from db.models import User
    from services import memory_store as M

    item = _save(client, proj, field="p_nom").json()
    with _db(_auth_db) as db:
        user = db.get(User, seeded_identity["user_id"])
        with pytest.raises(M.MemoryError) as exc:
            M.nominate(db, user, item["id"])
        assert exc.value.kind == "memory_kind_unavailable"
    assert _row(_auth_db, item["id"]).nomination is None


def test_apply_refuses_a_no_change_pick_too(client, proj, _auth_db):
    chip = _saved_chip(client, proj)
    card = _preview(client, proj, chip).json()
    _set_value(proj, 200.0, "p_nom")  # the field now holds the saved value
    r = _apply(client, proj, chip, card["digest"])
    assert (
        r.status_code == 422 and r.json()["detail"]["error_kind"] == "memory_no_change"
    )
    assert _row(_auth_db, chip["id"]).apply_count == 0


def test_an_item_of_another_project_kind_is_not_applied_by_id(client, proj, _auth_db):
    chip = _saved_chip(client, proj)
    _set(_auth_db, chip["id"], project_kind="connection")
    assert _preview(client, proj, chip).status_code == 404
    assert _apply(client, proj, chip, "0" * 64).status_code == 404


def test_an_unknown_class_with_an_asset_named_is_a_422(client, proj):
    r = client.get(
        SUGGEST,
        params={"project_id": proj.id, "component_class": "Gadget", "name": "gas"},
    )
    assert (
        r.status_code == 422
        and r.json()["detail"]["error_kind"] == "memory_field_unknown"
    )


@pytest.mark.parametrize(
    ("currency", "year", "kind"),
    [
        ("USD", 2024, "currency_mismatch"),
        ("EUR", 2020, "memory_money_year_mismatch"),
    ],
)
def test_money_mismatch_is_refused_at_apply_too(
    client, proj, _auth_db, currency, year, kind
):
    proj.stamp()
    _save(client, proj)
    _set_value(proj, 70.0)
    proj.stamp(source="typed:x")
    chip = _chips(client, proj)["marginal_cost"][0]
    card = _preview(client, proj, chip).json()
    proj.stamp(currency=currency, year=year, source="typed:x")
    r = _apply(client, proj, chip, card["digest"])
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == kind
    assert proj.net().generators.at["gas", "marginal_cost"] == 70.0
    assert _row(_auth_db, chip["id"]).apply_count == 0


def test_a_refused_write_leaves_provenance_and_count_untouched(
    client, proj, _auth_db, monkeypatch
):
    from fastapi import HTTPException

    from services import chat_tools

    chip = _saved_chip(client, proj)
    proj.stamp(field="p_nom", source="pack:x")
    card = _preview(client, proj, chip).json()
    before = PV.get(proj.net(), "Generator", "gas").get("p_nom")

    def refuse(*_a, **_k):
        raise HTTPException(422, {"error_kind": "invalid_value", "message": "no"})

    monkeypatch.setattr(chat_tools, "update_component", refuse)
    r = _apply(client, proj, chip, card["digest"])
    assert r.status_code == 422
    assert PV.get(proj.net(), "Generator", "gas").get("p_nom") == before
    assert _row(_auth_db, chip["id"]).apply_count == 0


@pytest.mark.parametrize(
    "carrier",
    [
        "wind(on)",
        "a+b",
        "a/b",
        "a,b",
        "a:b",
        "éolien",
        "x" * 65,
        "trailing ",
    ],
)
def test_a_carrier_outside_the_key_pattern_is_a_typed_422(client, proj, carrier):
    proj.net().generators.at["solar", "carrier"] = carrier
    r = _save(client, proj, field="p_nom", name="solar")
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "memory_carrier_unsupported"


def test_a_double_space_carrier_is_saved_and_suggested_for_its_asset(client, proj):
    proj.net().generators.at["solar", "carrier"] = "solar  rooftop"
    assert _save(client, proj, field="p_nom", name="solar").status_code == 201
    assert _chips(client, proj, "solar")["p_nom"][0]["carrier"] == "solar  rooftop"
    assert "p_nom" not in _chips(client, proj, "gas")


def test_the_applied_stamp_carries_the_items_currency_and_money_year(client, proj):
    proj.stamp(currency="EUR", year=2024)
    _save(client, proj)
    _set_value(proj, 70.0)
    proj.stamp(currency="EUR", year=2024, source="typed:x")
    chip = _chips(client, proj)["marginal_cost"][0]
    card = _preview(client, proj, chip).json()
    assert _apply(client, proj, chip, card["digest"]).status_code == 200
    entry = PV.get(proj.net(), "Generator", "gas")["marginal_cost"]
    assert entry["source"] == f"memory:{chip['id']}@v{chip['version']}"
    assert entry["currency"] == "EUR" and entry["currency_year"] == 2024  # GK B F8


_BODY = {"component_class": "Generator", "field": "p_nom", "value": 1.0, "unit": "MW"}


@pytest.mark.parametrize(
    ("change", "kind"),
    [
        ({"component_class": 5}, "memory_field_unknown"),
        ({"field": 5}, "memory_field_unknown"),
        ({"field": "p nom"}, "memory_field_unknown"),
        ({"field": "capital_cost"}, "memory_field_unknown"),  # derived
        ({"value": True}, "memory_invalid"),
        ({"value": "1"}, "memory_invalid"),
        ({"value": float("inf")}, "memory_invalid"),
        ({"currency": "XYZ"}, "memory_invalid"),
        ({"currency_year": True}, "memory_invalid"),
        ({"currency_year": 2024.0}, "memory_invalid"),
        ({"currency_year": 1899}, "memory_invalid"),
        ({"currency_year": 2201}, "memory_invalid"),
        ({"carrier": 5}, "memory_carrier_unsupported"),
    ],
)
def test_the_parameter_body_refuses_each_malformed_key(change, kind):
    """Each guard of the stored body on its own, as the Save route never sends
    these (a hand-built body or a future writer would)."""
    from services import memory_store as M

    with pytest.raises(M.MemoryError) as exc:
        M.validate_body("parameter", _BODY | change, parameter_ok=True)
    assert exc.value.kind == kind


@pytest.mark.parametrize("year", [1900, 2200])
def test_a_money_year_at_either_bound_is_kept(year):
    from services import memory_store as M

    body = M.validate_body(
        "parameter",
        _BODY | {"currency": "EUR", "currency_year": year},
        parameter_ok=True,
    )
    assert body["currency_year"] == year and body["currency"] == "EUR"


def test_a_unit_refusal_names_the_stored_unit():
    from services import memory_store as M

    with pytest.raises(M.MemoryError) as exc:
        M.validate_body("parameter", _BODY | {"unit": "GW"}, parameter_ok=True)
    assert "stored in MW" in str(exc.value)
    with pytest.raises(M.MemoryError) as exc:
        M.validate_body(
            "parameter",
            _BODY | {"field": "dc_ac_ratio", "unit": "MW"},
            parameter_ok=True,
        )
    assert "stored in no unit" in str(exc.value)


def test_the_kind_refusal_points_to_save_for_reuse():
    from services import memory_store as M

    with pytest.raises(M.MemoryError) as exc:
        M.validate_body("parameter", _BODY)
    assert exc.value.kind == "memory_kind_unavailable"
    assert "Save for reuse" in str(exc.value)


def test_a_preferences_keys_still_edit(client):
    item = client.post(
        "/api/memory/items",
        json={
            "kind": "preference",
            "body": {"key": "units.power", "value": "GW"},
            "concept_keys": ["topic:units"],
        },
    ).json()
    r = client.patch(
        f"/api/memory/items/{item['id']}", json={"concept_keys": ["topic:units"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2


@pytest.mark.parametrize("version", [0, True, "1", 1.0])
def test_node_reads_only_a_whole_version_number(
    client, proj, _auth_db, seeded_identity, version
):
    from db.models import User
    from services import memory_store as M

    item = _save(client, proj, field="p_nom").json()
    with _db(_auth_db) as db:
        user = db.get(User, seeded_identity["user_id"])
        with pytest.raises(M.MemoryError) as exc:
            M.node(db, user, item["id"], version)
        assert exc.value.status == 404


@pytest.mark.parametrize(
    "change", [{"component_class": 5}, {"field": 5}, {"field": "p nom"}]
)
def test_a_malformed_class_or_field_is_named_as_such(change):
    from services import memory_store as M

    with pytest.raises(M.MemoryError) as exc:
        M.validate_body("parameter", _BODY | change, parameter_ok=True)
    assert "needs a component class and a field" in str(exc.value)


# ── GK B (45) N1–N8 ───────────────────────────────────────────────────────────


def _on_bus(proj, name, unit):
    """Put the asset on a bus of its own that counts in `unit`."""
    n = proj.net()
    bus = f"bus_{name}"
    if bus not in n.buses.index:
        n.add("Bus", bus)
    n.buses.at[bus, "unit"] = unit
    n.generators.at[name, "bus"] = bus


def _no_carrier(proj, *names):
    for name in names:
        proj.net().generators.at[name, "carrier"] = ""


def test_an_mw_value_is_not_applied_onto_a_bus_with_its_own_unit(client, proj):
    _no_carrier(proj, "gas", "solar")
    chip = _saved_chip(client, proj)  # gas p_nom on an MW bus
    assert chip["unit"] == "MW"
    _on_bus(proj, "solar", "MW_th")  # the bus states its own unit
    assert "p_nom" not in _chips(client, proj, "solar")
    r = _preview(client, proj, chip, name="solar")
    assert r.json()["detail"]["error_kind"] == "memory_unit_mismatch"
    card = _preview(client, proj, chip).json()
    _on_bus(proj, "gas", "t/h")  # the target's unit moves after the preview
    r = _apply(client, proj, chip, card["digest"])
    assert r.json()["detail"]["error_kind"] == "memory_unit_mismatch"
    assert proj.net().generators.at["gas", "p_nom"] == 10.0


def _ore_bus(proj, name):
    n = proj.net()
    n.add(
        "Bus", f"bus_{name}", carrier="ore"
    )  # no unit column value: the catalogue's t
    n.generators.at[name, "bus"] = f"bus_{name}"


@pytest.mark.parametrize(
    ("put", "says"),
    [
        (
            lambda proj: _on_bus(proj, "gas", "t"),
            "is on bus bus_gas, which counts in t.",
        ),
        (lambda proj: _on_bus(proj, "gas", "t/h"), "which counts in t/h."),
        (lambda proj: _ore_bus(proj, "gas"), "is on bus bus_gas, which counts in t."),
        (
            lambda proj: proj.net().generators.__setitem__(
                "bus",
                proj.net()
                .generators["bus"]
                .where(proj.net().generators.index != "gas", "nowhere"),
            ),
            "gas is on a missing bus nowhere.",
        ),
    ],
    ids=["t", "t_per_h", "ore_catalogue", "dangling"],
)
def test_a_value_on_a_product_or_missing_bus_is_not_saved_or_offered(
    client, proj, put, says
):
    _no_carrier(proj, "gas", "solar")
    chip = _saved_chip(client, proj)  # saved on the plain bus first
    card = _preview(client, proj, chip).json()
    put(proj)
    r = _save(client, proj, field="p_nom", name="gas")
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == (
        "memory_unit_unsupported"
    )
    assert says in r.json()["detail"]["message"]
    assert _chips(client, proj, "gas") == {}
    refused = _preview(client, proj, chip).json()["detail"]
    assert refused["error_kind"] == "memory_unit_mismatch"
    assert says in refused["message"]
    r = _apply(client, proj, chip, card["digest"])
    assert r.json()["detail"]["error_kind"] == "memory_unit_mismatch"
    assert proj.net().generators.at["gas", "p_nom"] == 10.0


@pytest.mark.parametrize("unit", ["MW", " MW ", "None", ""])
def test_a_bus_that_states_mw_or_nothing_is_plain(client, proj, unit):
    _no_carrier(proj, "gas", "solar")
    _on_bus(proj, "gas", unit)
    chip = _saved_chip(client, proj)
    assert chip["unit"] == "MW"
    _set_value(proj, 10.0, "p_nom", "solar")
    assert _chips(client, proj, "solar")["p_nom"][0]["id"] == chip["id"]
    card = _preview(client, proj, chip, name="solar")
    assert card.status_code == 200, card.text
    row = next(r for r in card.json()["rows"] if r.get("field") == "p_nom")
    assert row["unit"] == "MW"  # the card row and the memory block agree


@pytest.mark.parametrize("unit", ["MWh", "MW_th"])
def test_a_bus_that_states_another_unit_is_refused_by_that_unit(client, proj, unit):
    """An explicit MWh bus shows MWh on its card rows, where the item says MW (N4)."""
    _on_bus(proj, "gas", unit)
    r = _save(client, proj, field="p_nom", name="gas")
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error_kind"] == "memory_unit_unsupported"
    assert f"gas is on bus bus_gas, which counts in {unit}." in detail["message"]
    assert _chips(client, proj, "gas") == {}


def test_a_catalogue_carrier_that_counts_in_mwh_is_plain(client, proj):
    from services.process_products import CARRIER_CATALOG

    carrier = next(c for c, row in CARRIER_CATALOG.items() if row.get("unit") == "MWh")
    n = proj.net()
    n.add("Bus", "bus_gas", carrier=carrier)  # no unit of its own
    n.generators.at["gas", "bus"] = "bus_gas"
    assert _save(client, proj, field="p_nom", name="gas").status_code == 201


def test_a_bus_price_on_a_tonnes_bus_is_not_saved(client, proj):
    n = proj.net()
    n.add("Bus", "ore_site", unit="t")
    n.buses.at["ore_site", "product_value"] = 500.0
    r = client.post(
        SAVE,
        json={
            "project_id": proj.id,
            "component_class": "Bus",
            "name": "ore_site",
            "field": "product_value",
        },
    )
    assert r.json()["detail"]["error_kind"] == "memory_unit_unsupported"


def test_a_link_efficiency_into_a_tonnes_bus_is_not_saved(client, proj):
    n = proj.net()
    gas_bus = n.generators.at["gas", "bus"]
    n.add("Bus", "ore_out", unit="t")
    n.add("Link", "smelter", bus0=gas_bus, bus1="ore_out", efficiency=0.3)
    # An unused extra port ("") is not a bus: the plain link still saves.
    n.add("Link", "plain", bus0=gas_bus, bus1=gas_bus, bus2="", efficiency=0.5)
    body = {"project_id": proj.id, "component_class": "Link", "field": "efficiency"}
    r = client.post(SAVE, json={**body, "name": "smelter"})
    assert r.json()["detail"]["error_kind"] == "memory_unit_unsupported"
    r = client.post(SAVE, json={**body, "name": "plain"})
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("port", ["bus2", "bus3"])
def test_a_link_with_an_extra_port_on_a_tonnes_bus_is_not_saved(client, proj, port):
    """MW in, MW out, and a by-product in tonnes: `efficiency2` is t per MWh (N1)."""
    n = proj.net()
    gas_bus = n.generators.at["gas", "bus"]
    n.add("Bus", "power_out")
    n.add("Bus", "slag", unit="t")
    ports = {"bus2": gas_bus, "efficiency2": 0.1} if port == "bus3" else {}
    n.add(
        "Link",
        "furnace",
        bus0=gas_bus,
        bus1="power_out",
        **ports,
        **{port: "slag", f"efficiency{port[3:]}": 0.2},
    )
    body = {"project_id": proj.id, "component_class": "Link", "name": "furnace"}
    for field in ("efficiency2", "efficiency"):
        r = client.post(SAVE, json={**body, "field": field})
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["error_kind"] == "memory_unit_unsupported"
        assert "furnace is on bus slag, which counts in t." in detail["message"]


def test_an_empty_port_of_any_index_is_skipped(client, proj):
    """A 6-port link gives every link a bus5 column; a plain link still saves (N3)."""
    n = proj.net()
    gas_bus = n.generators.at["gas", "bus"]
    n.add(
        "Link",
        "six",
        bus0=gas_bus,
        bus1=gas_bus,
        **{f"bus{i}": gas_bus for i in range(2, 6)},
    )
    n.add("Link", "plain", bus0=gas_bus, bus1=gas_bus, efficiency=0.5)
    assert "bus5" in n.links.columns and not n.links.at["plain", "bus5"]
    body = {"project_id": proj.id, "component_class": "Link", "field": "efficiency"}
    r = client.post(SAVE, json={**body, "name": "plain"})
    assert r.status_code == 201, r.text
    n.add("Bus", "slag", unit="t")
    n.links.at["plain", "bus5"] = "slag"  # a used port 5 counts
    r = client.post(SAVE, json={**body, "name": "plain"})
    assert r.json()["detail"]["error_kind"] == "memory_unit_unsupported"


@pytest.mark.parametrize("port", ["bus0", "bus1"])
def test_a_link_with_an_unconnected_main_port_is_refused(client, proj, port):
    n = proj.net()
    gas_bus = n.generators.at["gas", "bus"]
    n.add("Link", "loose", **({"bus0": gas_bus, "bus1": gas_bus} | {port: ""}))
    r = client.post(
        SAVE,
        json={
            "project_id": proj.id,
            "component_class": "Link",
            "name": "loose",
            "field": "efficiency",
        },
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error_kind"] == "memory_unit_unsupported"
    assert "Efficiency: loose is not connected to a bus." in detail["message"]


def test_a_bus_price_note_shows_the_unit_the_field_displays(client, proj):
    """`Bus.product_value` stores no unit; the note falls back to EUR/unit (N5)."""
    n = proj.net()
    first = n.buses.index[0]
    n.add("Bus", "second", carrier=n.buses.at[first, "carrier"])
    n.buses["product_value"] = float("nan")
    n.buses.at[first, "product_value"] = 500.0
    n.buses.at["second", "product_value"] = 20.0
    for bus in (first, "second"):
        PV.record(
            n,
            "Bus",
            bus,
            "product_value",
            status="default",
            source="pack:x",
            currency="EUR",
            currency_year=2024,
        )
    body = {"project_id": proj.id, "component_class": "Bus", "field": "product_value"}
    r = client.post(SAVE, json={**body, "name": first})
    assert r.status_code == 201, r.text
    chip = client.get(
        SUGGEST,
        params={"project_id": proj.id, "component_class": "Bus", "name": "second"},
    ).json()["fields"]["product_value"][0]
    r = client.post(
        f"{SUGGEST}/{chip['id']}/preview",
        json={
            "project_id": proj.id,
            "version": chip["version"],
            "component_class": "Bus",
            "name": "second",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["memory"]["unit"] == ""
    assert r.json()["memory"]["unit_display"] == "EUR/unit"
    assert "500 EUR/unit, 2024 EUR" in r.json()["memory_note"]


def test_a_held_user_series_is_neither_saved_nor_applied(client, proj, bound_ctx):
    import pandas as pd

    proj.stamp()
    chip = _saved_chip(client, proj, field="marginal_cost", then=70.0)
    card = _preview(client, proj, chip).json()
    with bound_ctx(client):
        from services.pypsa_service import PyPSAService

        # Held for another year: it does not overlap the snapshots, so it is not
        # in the network's own table, and a reapply puts it back later.
        idx = pd.date_range("1990-01-01", periods=3, freq="h")
        PyPSAService.get_user_ts()[("generators", "marginal_cost", "gas")] = pd.Series(
            5.0, index=idx
        )
    assert "gas" not in proj.net().generators_t.marginal_cost.columns
    r = _save(client, proj)
    assert r.json()["detail"]["error_kind"] == "memory_field_has_series"
    r = _preview(client, proj, chip)
    assert r.json()["detail"]["error_kind"] == "memory_field_has_series"
    r = _apply(client, proj, chip, card["digest"])
    assert r.json()["detail"]["error_kind"] == "memory_field_has_series"
    assert proj.net().generators.at["gas", "marginal_cost"] == 70.0


def test_a_card_only_change_after_the_preview_makes_the_apply_stale(client, proj):
    chip = _saved_chip(client, proj)
    card = _preview(client, proj, chip).json()
    n = proj.net()
    if "gas" not in n.carriers.index:
        n.add("Carrier", "gas")
    again = _preview(client, proj, chip).json()
    assert again["memory"] == card["memory"]  # the memory block did not move
    r = _apply(client, proj, chip, card["digest"])
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == (
        "edit_preview_stale"
    )
    assert proj.net().generators.at["gas", "p_nom"] == 10.0


def test_the_card_names_the_source_the_apply_replaces(client, proj):
    proj.stamp(field="p_nom", source="pack:before")
    chip = _saved_chip(client, proj)
    proj.stamp(field="p_nom", source="pack:before")
    card = _preview(client, proj, chip).json()
    assert card["memory"]["target"]["source"] == "pack:before"
    assert "pack:before" in card["memory_note"]
    assert _apply(client, proj, chip, card["digest"]).status_code == 200
    entry = PV.get(proj.net(), "Generator", "gas")["p_nom"]
    assert entry["replaces"] == "pack:before"


@pytest.mark.parametrize("carrier", ["gas\n", "gas\r"])
def test_a_carrier_with_a_line_break_is_a_typed_422(client, proj, carrier):
    proj.net().generators.at["gas", "carrier"] = carrier
    r = _save(client, proj, field="p_nom")
    assert r.json()["detail"]["error_kind"] == "memory_carrier_unsupported"


def test_an_org_item_is_not_suggested_to_someone_outside_its_org(
    client, proj, _auth_db, monkeypatch
):
    from services import memory_store as M

    item = _save(client, proj, field="p_nom").json()
    _set(_auth_db, item["id"], owner_scope="org", user_id=None)
    assert _chips(client, proj)["p_nom"]
    monkeypatch.setattr(M, "org_of", lambda *_a, **_k: uuid.uuid4())
    assert "p_nom" not in _chips(client, proj)


def test_a_failing_apply_count_rolls_back_and_the_session_stays_usable(
    client, proj, monkeypatch, bound_ctx, _auth_db, seeded_identity
):
    from db.models import MemoryItem, Project, User
    from services import memory_parameters as P
    from services import memory_store as M

    chip = _saved_chip(client, proj)
    card = _preview(client, proj, chip).json()

    def broken(db, *_a, **_k):
        db.add(MemoryItem(id=uuid.uuid4()))  # no kind, no owner: the flush fails
        db.flush()

    monkeypatch.setattr(M, "record_apply", broken)
    with bound_ctx(client), _db(_auth_db) as db:
        user = db.get(User, seeded_identity["user_id"])
        project = db.get(Project, uuid.UUID(proj.id))
        out = P.apply(
            db,
            user,
            project,
            chip["id"],
            chip["version"],
            "Generator",
            "gas",
            card["digest"],
        )
        assert out["apply_counted"] is False
        # The failed flush was rolled back: the same session still reads.
        assert db.get(MemoryItem, uuid.UUID(chip["id"][4:])).apply_count == 0
    assert proj.net().generators.at["gas", "p_nom"] == 200.0


def test_another_orgs_or_users_item_answers_the_same_404_as_a_ghost(
    client, proj, _auth_db
):
    from tests.conftest import _seed_org_and_user

    other_user, other_org = _seed_org_and_user(
        _auth_db[1],
        email=f"m{uuid.uuid4().hex[:6]}@example.com",
        org_name=f"Org {uuid.uuid4().hex[:6]}",
    )
    item = _save(client, proj, field="p_nom").json()
    _set_value(proj, 10.0, "p_nom")
    chip = _chips(client, proj)["p_nom"][0]
    ghost = _preview(client, proj, {**chip, "id": "mem_" + uuid.uuid4().hex})
    _set(_auth_db, item["id"], org_id=other_org)
    other_org_reply = _preview(client, proj, chip)
    _set(_auth_db, item["id"], org_id=proj.org_id, user_id=other_user)
    other_user = _preview(client, proj, chip)
    assert ghost.status_code == other_org_reply.status_code == 404
    assert other_user.status_code == 404
    assert ghost.content == other_org_reply.content == other_user.content


# ── 03b: the UI's Save button and Undo (GK B 47 A1, A2) ───────────────────────


def _saveable(client, proj, name="gas"):
    r = client.get(
        SUGGEST,
        params={"project_id": proj.id, "component_class": "Generator", "name": name},
    )
    assert r.status_code == 200, r.text
    return set(r.json()["saveable"])


@pytest.mark.parametrize("setup", ["plain_stamped", "plain_unstamped", "tonnes_bus"])
def test_saveable_is_exactly_where_save_succeeds(client, proj, setup):
    """Every schema field of the asset, derived and money fields included: listed in
    `saveable` if and only if Save for reuse accepts it, in both directions."""
    from services.asset_schema.fields import fields_for

    if setup == "plain_stamped":
        proj.stamp()  # marginal_cost has a money basis
    elif setup == "tonnes_bus":
        _on_bus(proj, "gas", "t")
    keys = [f.key for f in fields_for("Generator")]
    assert any(f.derived for f in fields_for("Generator"))
    offered = _saveable(client, proj)
    succeeded = {k for k in keys if _save(client, proj, field=k).status_code == 201}
    assert offered == succeeded
    if setup == "tonnes_bus":
        assert offered == set()
    else:
        assert "p_nom" in offered
        assert ("marginal_cost" in offered) is (setup == "plain_stamped")


def test_saveable_drops_a_field_set_by_a_time_series(client, proj):
    proj.stamp()
    assert "marginal_cost" in _saveable(client, proj)
    proj.net().generators_t.marginal_cost["gas"] = 5.0
    assert "marginal_cost" not in _saveable(client, proj)
    assert _save(client, proj).status_code == 422


def test_save_says_whether_it_created_the_item(client, proj):
    a = _save(client, proj, field="p_nom")
    assert a.status_code == 201 and a.json()["created"] is True
    _set_value(proj, 250.0, "p_nom")
    b = _save(client, proj, field="p_nom").json()
    assert b["created"] is False and b["id"] == a.json()["id"]
    versions = client.get(f"/api/memory/items/{b['id']}/versions").json()["versions"]
    assert sorted(v["body"]["value"] for v in versions) == sorted(
        [a.json()["body"]["value"], 250.0]
    )


def test_undo_after_a_first_save_retires_exactly_that_item(client, proj):
    other = _save(client, proj, field="p_nom", name="solar").json()
    a = _save(client, proj, field="p_nom").json()
    assert a["created"] is True
    r = client.post(f"/api/memory/items/{a['id']}/retire")
    assert r.status_code == 200, r.text
    rows = client.get(
        "/api/memory/items", params={"kind": "parameter", "status": "all"}
    ).json()["items"]
    status = {row["id"]: row["status"] for row in rows}
    assert status == {a["id"]: "retired", other["id"]: "confirmed"}


def test_a_user_with_no_org_is_offered_no_save_and_save_refuses(
    client, proj, monkeypatch
):
    """Save's own org rule, so `saveable` stays exactly where Save succeeds (A1)."""
    from services import memory_store as M

    assert "p_nom" in _saveable(client, proj)
    monkeypatch.setattr(M, "org_of", lambda db, user: None)
    assert _saveable(client, proj) == set()
    r = _save(client, proj, field="p_nom")
    assert r.status_code == 403 and r.json()["detail"]["error_kind"] == "memory_no_org"
