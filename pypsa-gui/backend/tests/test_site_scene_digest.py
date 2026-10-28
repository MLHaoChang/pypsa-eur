"""
SC-01b: GET .../scene returns the adoption `digest` while a Site is unadopted.

The browser cannot reproduce the server's canonical JSON (a stored 47.0 reads
back as 47 in JS), so the UI adopts with the digest this GET served. Both sides
call `store.legacy_site_digest` on the server's own sites.json (GK B condition 1).
"""
from __future__ import annotations

import os

import pytest

from services import site_service as ss

SITE = {"id": "campus", "name": "Campus", "buses": [], "origin": {"lng": 11.0, "lat": 48.0},
        "boundary": [[10.999, 47.999], [11.001, 47.999], [11.001, 48.001]], "placements": {}}


@pytest.fixture
def legacy(client, api_project, project_storage_dir):
    name = api_project("digest")
    doc = {"version": 1, "sites": [SITE]}
    assert client.put(f"/api/projects/{name}/sites", json=doc).status_code == 200
    return name, project_storage_dir(name)


def _scene(client, name, site_id="campus"):
    return client.get(f"/api/projects/{name}/sites/{site_id}/scene")


def _adopt(client, name, digest):
    return client.post(f"/api/projects/{name}/sites/campus/scene/adopt", json={"expected_digest": digest})


def test_the_unadopted_digest_adopts_an_unchanged_site(client, legacy):
    name, _ = legacy
    body = _scene(client, name).json()
    assert body["state"] == "unadopted"
    assert isinstance(body.get("digest"), str) and len(body["digest"]) == 64
    r = _adopt(client, name, body["digest"])
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "enabled"


def test_the_get_writes_nothing(client, legacy):
    name, project = legacy
    path = project / ss.SITES_FILE
    before, mtime = path.read_bytes(), os.stat(path).st_mtime_ns
    assert _scene(client, name).status_code == 200
    assert path.read_bytes() == before
    assert os.stat(path).st_mtime_ns == mtime


def test_a_changed_site_refuses_the_old_digest_and_a_fresh_get_adopts(client, legacy):
    name, _ = legacy
    old = _scene(client, name).json()["digest"]
    moved = {**SITE, "name": "Campus North"}
    assert client.put(f"/api/projects/{name}/sites", json={"version": 1, "sites": [moved]}).status_code == 200
    r = _adopt(client, name, old)
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "revision_conflict"
    fresh = _scene(client, name).json()["digest"]
    assert fresh != old
    assert _adopt(client, name, fresh).status_code == 200


def test_an_enabled_scene_has_no_digest(client, legacy):
    name, _ = legacy
    assert _adopt(client, name, _scene(client, name).json()["digest"]).status_code == 200
    body = _scene(client, name).json()
    assert body["state"] == "enabled" and "digest" not in body


def test_an_unknown_site_is_404_without_a_digest(client, legacy):
    name, _ = legacy
    r = _scene(client, name, "nope")
    assert r.status_code == 404
    assert "digest" not in r.text
