"""
Library router + org-level access rule (Edge Investment Case P1 WP1.1b).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.1b

The Library is an ORG resource, not a project one: `project_acl` (scenario-tree
scoped) does not apply. `services/library_acl` says: a member of the org reads
and writes its Library; a super-admin may address any org; nobody else.
Routes default to the caller's own org; naming another org is 403 unless
super-admin. Library chat tools are deferred to P2 WP2.4 (plan deviation,
recorded there).
"""
from __future__ import annotations

import pandas as pd
import pytest


def _body(name="px", n=8, tz="Europe/Berlin", offset=0.0):
    idx = pd.date_range("2030-01-01", periods=n, freq="15min", tz=tz)
    return {"name": name, "timestamps": [t.isoformat() for t in idx],
            "values": [40.0 + i + offset for i in range(n)], "timezone": tz,
            "meta": {"source": "test upload", "provider": "pytest"}}


def test_put_list_get_round_trip(client):
    r = client.post("/api/library/series", json=_body())
    assert r.status_code == 200, r.text
    ref = r.json()
    assert ref["id"] == "px" and ref["version"] == 1 and len(ref["hash"]) == 64
    lst = client.get("/api/library/series").json()
    assert [x["id"] for x in lst] == ["px"]
    got = client.get("/api/library/series/px", params={"version": 1}).json()
    assert got["ref"] == ref
    assert got["values"] == _body()["values"]
    assert got["timezone"] == "Europe/Berlin"
    assert pd.DatetimeIndex(got["timestamps"]).equals(
        pd.DatetimeIndex(_body()["timestamps"]))


def test_same_content_is_idempotent_through_the_route(client):
    a = client.post("/api/library/series", json=_body()).json()
    b = client.post("/api/library/series", json=_body()).json()
    c = client.post("/api/library/series", json=_body(offset=1.0)).json()
    assert a == b and c["version"] == 2


def test_latest_version_by_default(client):
    client.post("/api/library/series", json=_body())
    client.post("/api/library/series", json=_body(offset=1.0))
    got = client.get("/api/library/series/px").json()
    assert got["ref"]["version"] == 2


def test_bad_input_is_422(client):
    bad = _body()
    bad["values"] = bad["values"][:-1]
    assert client.post("/api/library/series", json=bad).status_code == 422
    bad = _body()
    bad["meta"] = {"surprise": 1}
    assert client.post("/api/library/series", json=bad).status_code == 422
    bad = _body()
    bad["values"][2] = None
    assert client.post("/api/library/series", json=bad).status_code == 422


def test_unknown_series_is_404(client):
    assert client.get("/api/library/series/nope").status_code == 404
    client.post("/api/library/series", json=_body())
    assert client.get("/api/library/series/px", params={"version": 9}).status_code == 404


def test_unauthenticated_is_401(anon_client):
    assert anon_client.get("/api/library/series").status_code == 401
    assert anon_client.post("/api/library/series", json=_body()).status_code in (401, 403)


def test_another_org_cannot_see_or_address_this_org(client, other_org_client, seeded_identity):
    assert client.post("/api/library/series", json=_body("secret")).status_code == 200
    # Their own org has no such series …
    assert other_org_client.get("/api/library/series/secret").status_code == 404
    assert other_org_client.get("/api/library/series").json() == []
    # … and naming our org explicitly is forbidden.
    org = str(seeded_identity["org_id"])
    assert other_org_client.get("/api/library/series", params={"org_id": org}).status_code == 403
    assert other_org_client.get("/api/library/series/secret",
                                params={"org_id": org}).status_code == 403
    assert other_org_client.post("/api/library/series", params={"org_id": org},
                                 json=_body("planted")).status_code == 403


def test_router_is_registered_in_the_openapi_schema(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/library/series" in paths
    assert "/api/library/series/{name}" in paths


def test_acl_rules_directly(_auth_db, seeded_identity, second_identity):
    import uuid

    from db.models import User
    from services import library_acl

    _engine, session_local = _auth_db
    with session_local() as db:
        me = db.get(User, seeded_identity["user_id"])
        org = seeded_identity["org_id"]
        other = second_identity["org_id"]
        assert library_acl.org_of(db, me) == org
        assert library_acl.can_read(db, me, org) and library_acl.can_write(db, me, org)
        assert not library_acl.can_read(db, me, other)
        assert not library_acl.can_write(db, me, other)
        admin = User(id=uuid.uuid4(), email="sa@example.com", status="active",
                     is_super_admin=True, created_at=me.created_at)
        assert library_acl.can_read(db, admin, other) and library_acl.can_write(db, admin, other)
        loner = User(id=uuid.uuid4(), email="x@example.com", status="active",
                     is_super_admin=False, created_at=me.created_at)
        assert library_acl.org_of(db, loner) is None
        assert not library_acl.can_read(db, loner, org)
