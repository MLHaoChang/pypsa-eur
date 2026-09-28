"""
Library items: tariffs, contracts, connection agreements (Edge Investment Case
P2 WP2.4a).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.4a.
An item is a validated JSON payload stored as a content-hashed file under the
org's Library directory, versioned like a series (identical content returns
the existing version). `CommercialConfig.import_tariff_ref` names a Library
tariff: `PUT /solver_config` resolves it into the inline `import_tariff` the
solve uses (the ref stays as provenance); a stale or missing ref is
`library_ref_stale` (409) at the route, and an inline copy that does not match
its ref is `binding_invalid` in preflight. Contracts and connection agreements
are templates copied inline. Projects pin item refs with their kind (sidecar
schema 2; schema 1 still reads).
"""
from __future__ import annotations

import json

import pytest

from services.commercial import hashing as H
from services.library import bundle_pins as P
from tests.fixtures.investment_case.edge_15min import build_edge_15min

TARIFF = {"id": "nl-tou", "name": "NL TOU", "jurisdiction": "NL", "valid_from": "2030-01-01",
          "items": [{"id": "energy", "kind": "energy", "unit": "per_kwh",
                     "periods": [{"name": "night", "rate": 0.05, "start_hour": 0, "end_hour": 6},
                                 {"name": "day", "rate": 0.20}]}]}
AGREEMENT = {"kind": "firm", "import_cap_mw": 70.0, "available_from": "2030-01-01"}
PPA = {"type": "ppa", "id": "ppa1", "kind": "baseload", "price": 55.0,
       "tenor_years": 10, "seller": "Wind BV", "buyer": "Site", "asset_ids": ["pv"]}


def _put(client, kind, name, payload, meta=None):
    return client.put(f"/api/library/items/{kind}/{name}",
                      json={"payload": payload, "meta": meta or {"source": "pytest"}})


# ── CRUD ───────────────────────────────────────────────────────────────────


def test_put_list_get_round_trip_per_kind(client):
    for kind, name, payload in (("tariff", "nl", TARIFF),
                                ("connection_agreement", "firm70", AGREEMENT)):
        r = _put(client, kind, name, payload)
        assert r.status_code == 200, r.text
        ref = r.json()
        assert ref == {"kind": kind, "id": name, "version": 1, "hash": ref["hash"]}
        assert len(ref["hash"]) == 64
        assert [x["id"] for x in client.get(f"/api/library/items/{kind}").json()] == [name]
        got = client.get(f"/api/library/items/{kind}/{name}").json()
        assert got["ref"] == ref
        assert got["payload"]["import_cap_mw" if kind == "connection_agreement" else "id"] == \
            payload["import_cap_mw" if kind == "connection_agreement" else "id"]


def test_identical_content_returns_the_existing_version(client):
    a = _put(client, "tariff", "nl", TARIFF).json()
    b = _put(client, "tariff", "nl", json.loads(json.dumps(TARIFF))).json()
    changed = json.loads(json.dumps(TARIFF))
    changed["items"][0]["periods"][1]["rate"] = 0.21
    c = _put(client, "tariff", "nl", changed).json()
    assert a == b and c["version"] == 2
    assert client.get("/api/library/items/tariff/nl").json()["ref"]["version"] == 2
    assert client.get("/api/library/items/tariff/nl",
                      params={"version": 1}).json()["ref"] == a


def test_the_hash_is_the_canonical_content_digest(client):
    from models.commercial import Tariff

    ref = _put(client, "tariff", "nl", TARIFF).json()
    assert ref["hash"] == H.library_item_digest(Tariff.model_validate(TARIFF))


def test_an_invalid_payload_or_unknown_kind_is_422(client):
    bad = json.loads(json.dumps(TARIFF))
    bad["items"] = []
    assert _put(client, "tariff", "nl", bad).status_code == 422
    assert _put(client, "connection_agreement", "x", {"kind": "firm"}).status_code == 422
    assert _put(client, "gadget", "x", TARIFF).status_code == 422
    assert client.get("/api/library/items/gadget").status_code == 422


def test_contract_templates_are_stored_and_validated(client):
    r = _put(client, "contract", "ppa1", PPA)
    assert r.status_code == 200, r.text
    got = client.get("/api/library/items/contract/ppa1").json()["payload"]
    assert got["type"] == "ppa" and got["kind"] == "baseload"
    for junk in ({"id": "j", "kind": "baseload", "price": 1.0},            # no type
                 {**PPA, "type": "gadget"},                         # unknown type
                 {**PPA, "kind": "nonsense"},                                # invalid for PPA
                 {**PPA, "surprise": 1}):                                    # unknown key
        assert _put(client, "contract", "junk", junk).status_code == 422, junk


def test_unknown_item_is_404_and_the_org_acl_applies(client, other_org_client, anon_client):
    assert client.get("/api/library/items/tariff/nope").status_code == 404
    _put(client, "tariff", "nl", TARIFF)
    assert client.get("/api/library/items/tariff/nl", params={"version": 9}).status_code == 404
    assert other_org_client.get("/api/library/items/tariff").json() == []
    assert other_org_client.get("/api/library/items/tariff/nl").status_code == 404
    assert anon_client.get("/api/library/items/tariff").status_code == 401


def test_series_and_items_share_names_without_colliding(client):
    import pandas as pd

    idx = pd.date_range("2030-01-01", periods=4, freq="h", tz="UTC")
    assert client.post("/api/library/series", json={
        "name": "nl", "timestamps": [t.isoformat() for t in idx], "values": [1.0] * 4,
        "meta": {"source": "t"}}).status_code == 200
    assert _put(client, "tariff", "nl", TARIFF).json()["version"] == 1
    assert [s["id"] for s in client.get("/api/library/series").json()] == ["nl"]


# ── import_tariff_ref through the route ────────────────────────────────────


def _ref_config(ref, **extra):
    return {"commercial": {"poc_link": "import", "import_tariff_ref": ref, **extra}}


def test_put_solver_config_resolves_the_tariff_ref_into_the_inline_tariff(
        client, install_network, session_ctx):
    install_network(build_edge_15min())
    ref = _put(client, "tariff", "nl", TARIFF).json()
    r = client.put("/api/simulation/solver_config", json=_ref_config(ref))
    assert r.status_code == 200, r.text
    stored = session_ctx(client).solver_state["solver_config"].commercial
    assert stored["import_tariff_ref"] == ref
    assert stored["import_tariff"]["id"] == "nl-tou"
    assert stored["import_tariff"]["items"][0]["periods"][1]["rate"] == 0.20


def test_a_stale_or_missing_tariff_ref_is_409_library_ref_stale(client, install_network):
    install_network(build_edge_15min())
    ref = _put(client, "tariff", "nl", TARIFF).json()
    for bad in ({**ref, "hash": "0" * 64}, {**ref, "version": 7}, {**ref, "id": "ghost"}):
        r = client.put("/api/simulation/solver_config", json=_ref_config(bad))
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "library_ref_stale"


def test_preflight_refuses_an_inline_tariff_that_is_not_its_ref():
    from services.commercial.preflight import commercial_findings

    from models.commercial import Tariff

    n = build_edge_15min()
    digest = H.library_item_digest(Tariff.model_validate(TARIFF))
    ref = {"kind": "tariff", "id": "nl", "version": 1, "hash": digest}
    ok = {"poc_link": "import", "import_tariff_ref": ref, "import_tariff": TARIFF}
    assert not [f for f in commercial_findings(n, ok) if f[0] == "error"]
    edited = json.loads(json.dumps(TARIFF))
    edited["items"][0]["periods"][1]["rate"] = 0.9
    codes = {f[1] for f in commercial_findings(n, {**ok, "import_tariff": edited})
             if f[0] == "error"}
    assert codes == {"commercial.binding_invalid"}
    unresolved = {"poc_link": "import", "import_tariff_ref": ref}
    assert {f[1] for f in commercial_findings(n, unresolved) if f[0] == "error"} == \
        {"commercial.binding_invalid"}


def test_a_connection_agreement_template_is_copied_inline_with_its_ref(client):
    """Templates are copied, never resolved at solve time: the inline object
    carries `library_ref` as provenance and validates on its own."""
    from models.commercial import CommercialConfig

    ref = _put(client, "connection_agreement", "firm70", AGREEMENT).json()
    got = client.get("/api/library/items/connection_agreement/firm70").json()
    cfg = CommercialConfig.model_validate({"poc_link": "import",
                                           "connection": {**got["payload"], "library_ref": ref}})
    assert cfg.connection.library_ref.version == 1
    assert cfg.connection.import_cap_mw == 70.0


def test_new_fields_are_registered_for_hash_recipe_1():
    assert H.FIELDS_AFTER_V1[("CommercialConfig", "import_tariff_ref")] is None
    assert H.FIELDS_AFTER_V1[("ConnectionAgreement", "library_ref")] is None


# ── pins ───────────────────────────────────────────────────────────────────


def test_item_refs_are_collected_with_their_kind_apart_from_series():
    s = {"id": "px", "version": 1, "hash": "a" * 64, "source": "s"}
    t = {"kind": "tariff", "id": "nl", "version": 2, "hash": "b" * 64}
    pins = P.collect_pins({"x": s, "y": {"import_tariff_ref": t}})
    assert pins == [{"kind": "series", "id": "px", "version": 1, "hash": "a" * 64},
                    {"kind": "tariff", "id": "nl", "version": 2, "hash": "b" * 64}]


def test_series_only_pins_keep_schema_1_and_item_pins_write_schema_2(tmp_path):
    s = {"kind": "series", "id": "px", "version": 1, "hash": "a" * 64}
    t = {"kind": "tariff", "id": "nl", "version": 2, "hash": "b" * 64}
    P.write_pins(tmp_path, [s])
    assert json.loads((tmp_path / P.SIDECAR_NAME).read_text())["schema"] == 1
    P.write_pins(tmp_path, [s, t])
    side = json.loads((tmp_path / P.SIDECAR_NAME).read_text())
    assert side["schema"] == 2 and {r["kind"] for r in side["refs"]} == {"series", "tariff"}
    pins, issues = P.read_pins(tmp_path)
    assert issues == [] and sorted(p["kind"] for p in pins) == ["series", "tariff"]


@pytest.mark.parametrize("schema1", [
    {"schema": 1, "refs": [{"id": "px", "version": 1, "hash": "a" * 64}]}])
def test_a_schema_1_sidecar_still_reads_as_series(tmp_path, schema1):
    (tmp_path / P.SIDECAR_NAME).write_text(json.dumps(schema1))
    pins, issues = P.read_pins(tmp_path)
    assert issues == [] and pins == [{"kind": "series", "id": "px", "version": 1,
                                      "hash": "a" * 64}]


def test_a_bundle_into_another_org_reports_its_tariff_pin(client, other_org_client, api_project,
                                                         session_ctx):
    import io
    import zipfile

    name = api_project("tariffed")
    ref = _put(client, "tariff", "nl", TARIFF).json()
    ctx = session_ctx(client)
    ctx.solver_state["solver_config"].commercial = {
        "poc_link": "import", "import_tariff_ref": ref, "import_tariff": TARIFF}
    assert client.post(f"/api/projects/{name}",
                       params={"force": True, "expect": name}).status_code == 200
    r = client.get(f"/api/projects/{name}/bundle")
    assert P.SIDECAR_NAME in zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    same = client.post("/api/projects/import_bundle?name=tariffed_again",
                       files={"file": ("b.zip", r.content, "application/zip")})
    assert same.json()["library_issues"] == []
    other = other_org_client.post("/api/projects/import_bundle?name=tariffed_there",
                                  files={"file": ("b.zip", r.content, "application/zip")})
    issues = other.json()["library_issues"]
    assert [(i["reason"], i["kind"], i["id"]) for i in issues] == [("missing", "tariff", "nl")]


# ── WP2.4a review round 1 ──────────────────────────────────────────────────


def test_an_edited_inline_tariff_with_its_ref_is_a_conflict_not_a_silent_revert(
        client, install_network, session_ctx):
    """#1: the submitted edit is never replaced by the Library copy."""
    install_network(build_edge_15min())
    ref = _put(client, "tariff", "nl", TARIFF).json()
    edited = json.loads(json.dumps(TARIFF))
    edited["items"][0]["periods"][1]["rate"] = 99.0
    r = client.put("/api/simulation/solver_config", json=_ref_config(ref, import_tariff=edited))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "import_tariff_ref_conflict"
    same = client.put("/api/simulation/solver_config", json=_ref_config(ref, import_tariff=TARIFF))
    assert same.status_code == 200, same.text                     # the unchanged copy is fine


def test_a_ref_of_another_kind_is_refused_cleanly(client, install_network):
    """#2: `import_tariff_ref` names a tariff; anything else is one clear error."""
    from services.commercial.preflight import commercial_findings

    install_network(build_edge_15min())
    ref = _put(client, "connection_agreement", "firm70", AGREEMENT).json()
    r = client.put("/api/simulation/solver_config", json=_ref_config(ref))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "commercial_binding_invalid"
    assert "not a tariff" in r.json()["detail"]["message"]
    from models.commercial import Tariff

    fake = {"kind": "contract", "id": "x", "version": 1,
            "hash": H.library_item_digest(Tariff.model_validate(TARIFF))}
    codes = {f[1] for f in commercial_findings(build_edge_15min(), {
        "poc_link": "import", "import_tariff_ref": fake, "import_tariff": TARIFF})
        if f[0] == "error"}
    assert codes == {"commercial.binding_invalid"}


@pytest.mark.parametrize("kind,payload", [
    ("tariff", {**TARIFF, "valid_too": "2031-01-01"}),
    ("tariff", {**TARIFF, "items": [{**TARIFF["items"][0], "surprise": 1}]}),
    ("contract", {**PPA, "reference_price": {"id": "px", "version": 1, "hash": "a" * 64,
                                             "source": "t", "junk": 1}}),
    ("connection_agreement", {**AGREEMENT, "capacity_fee": {
        "id": "f", "kind": "capacity", "unit": "per_kw_year",
        "periods": [{"name": "all", "rate": 1.0, "typo": 2}]}}),
])
def test_unknown_keys_are_refused_at_every_level(client, kind, payload):
    """#3: a typo never silently vanishes from a Library item."""
    r = _put(client, kind, "x", payload)
    assert r.status_code == 422 and "unknown" in r.text


def test_get_normalises_the_name_like_put(client):
    """#4."""
    assert _put(client, "tariff", " sp ", TARIFF).json()["id"] == "sp"
    assert client.get("/api/library/items/tariff/%20sp%20").status_code == 200
