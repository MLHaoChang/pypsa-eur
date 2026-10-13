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
    # Anonymous callers are CSRF-exempt, so the answer is exactly 401.
    assert anon_client.post("/api/library/series", json=_body()).status_code == 401


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


# ── Review conditions (WP1.1b review round 1) ───────────────────────────────


@pytest.fixture
def super_admin_client(_auth_db):
    """A super-admin who belongs to NO org (the bootstrap operator)."""
    import uuid
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient

    import main
    from db.models import User
    from tests.conftest import attach_session

    _engine, session_local = _auth_db
    with session_local() as db:
        sa = db.query(User).filter_by(email="library-sa@example.com").first()
        if sa is None:
            sa = User(id=uuid.uuid4(), email="library-sa@example.com", status="active",
                      is_super_admin=True, created_at=datetime.now(tz=timezone.utc))
            db.add(sa)
            db.commit()
        uid = sa.id
    with TestClient(main.app) as c:
        yield attach_session(c, session_local, uid)


def test_super_admin_reads_and_writes_another_org_over_http(super_admin_client, client,
                                                            seeded_identity):
    org = str(seeded_identity["org_id"])
    assert client.post("/api/library/series", json=_body("theirs")).status_code == 200
    # No org of their own: an un-targeted call has nothing to default to.
    assert super_admin_client.get("/api/library/series").status_code == 403
    lst = super_admin_client.get("/api/library/series", params={"org_id": org})
    assert lst.status_code == 200 and [x["id"] for x in lst.json()] == ["theirs"]
    got = super_admin_client.get("/api/library/series/theirs", params={"org_id": org})
    assert got.status_code == 200
    put = super_admin_client.post("/api/library/series", params={"org_id": org},
                                  json=_body("seeded-by-admin"))
    assert put.status_code == 200, put.text
    assert {x["id"] for x in client.get("/api/library/series").json()} == {
        "theirs", "seeded-by-admin"}


def test_an_unknown_org_is_404_not_500(super_admin_client):
    import uuid

    ghost = str(uuid.uuid4())
    assert super_admin_client.get("/api/library/series",
                                  params={"org_id": ghost}).status_code == 404
    assert super_admin_client.post("/api/library/series", params={"org_id": ghost},
                                   json=_body()).status_code == 404


def test_naive_timestamps_with_a_timezone_are_refused(client):
    body = _body()
    body["timestamps"] = [t[:16] for t in body["timestamps"]]  # "2030-01-01T00:00"
    r = client.post("/api/library/series", json=body)
    assert r.status_code == 422
    assert "offset" in r.text


def test_offsets_without_a_timezone_are_stored_as_utc_instants(client):
    # A Berlin year crossing DST: two offsets, no `timezone`.
    idx = pd.date_range("2030-03-31 00:00", periods=16, freq="15min", tz="Europe/Berlin")
    body = {"name": "dst", "timestamps": [t.isoformat() for t in idx],
            "values": [1.0] * 16, "meta": {}}
    r = client.post("/api/library/series", json=body)
    assert r.status_code == 200, r.text
    got = client.get("/api/library/series/dst").json()
    assert got["timezone"] == "UTC"
    assert pd.DatetimeIndex(got["timestamps"]).equals(idx.tz_convert("UTC"))


def test_mixed_naive_and_offset_timestamps_are_refused(client):
    body = _body(n=4)
    body["timestamps"][1] = body["timestamps"][1][:16]
    body["timezone"] = None
    assert client.post("/api/library/series", json=body).status_code == 422


@pytest.mark.parametrize("name", ["a/b", "   ", "/lead", "tab\tname", ".", "..",
                                  "del\x7fname", "a?b", "a#b"])
def test_names_that_could_never_be_fetched_are_refused(client, name):
    body = _body(name=name.replace("\\t", "\t"))
    assert client.post("/api/library/series", json=body).status_code == 422


def test_surrounding_whitespace_is_stripped_from_a_name(client):
    r = client.post("/api/library/series", json=_body(name="  px  "))
    assert r.status_code == 200 and r.json()["id"] == "px"


def test_point_count_is_capped(client):
    from routers.library import MAX_POINTS

    body = {"name": "big", "timestamps": ["2030-01-01T00:00+00:00"] * (MAX_POINTS + 1),
            "values": [0.0] * (MAX_POINTS + 1), "meta": {}}
    r = client.post("/api/library/series", json=body)
    assert r.status_code == 422
    # The refusal must not echo the million-point input back (re-review #1).
    assert len(r.content) < 2_000


def test_a_missing_payload_file_is_409_library_ref_stale(client, seeded_identity):
    import pathlib

    from services.library.series_store import _default_root
    from services.storage_paths import library_dir

    assert client.post("/api/library/series", json=_body()).status_code == 200
    base = library_dir(_default_root(), seeded_identity["org_id"])
    for f in pathlib.Path(base).rglob("*.csv.gz"):
        f.unlink()
    r = client.get("/api/library/series/px")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "library_ref_stale"


def test_library_post_requires_the_csrf_token(client):
    del client.headers["X-CSRF-Token"]
    r = client.post("/api/library/series", json=_body())
    assert r.status_code == 403


def test_a_lower_case_utc_marker_is_still_an_instant(client):
    body = {"name": "lc", "timestamps": ["2030-01-01t00:00z", "2030-01-01t00:15z"],
            "values": [1.0, 2.0], "meta": {}}
    r = client.post("/api/library/series", json=body)
    assert r.status_code == 200, r.text
    assert client.get("/api/library/series/lc").json()["timezone"] == "UTC"


def test_a_named_utc_suffix_counts_as_an_offset(client):
    body = {"name": "named", "timestamps": ["2030-01-01 00:00:00 UTC", "2030-01-01 00:15:00 UTC"],
            "values": [1.0, 2.0], "timezone": "Europe/Berlin", "meta": {}}
    r = client.post("/api/library/series", json=body)
    assert r.status_code == 200, r.text
    got = client.get("/api/library/series/named").json()
    assert got["timezone"] == "Europe/Berlin"
    assert got["timestamps"][0].startswith("2030-01-01T01:00:00+01:00")
