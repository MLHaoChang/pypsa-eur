"""
`GET/PUT /api/projects/{name}/sites/{site_id}/thumbnail` — the 3D view's
capture of a site, stored as `sites/<id>/thumb.png` (visual-layers plan 3,
S5). Validation (content type, size, PNG magic), containment (the id goes
through `site_service.site_dir`), the 404s, and the service helpers the
report reads through. The lock test is in test_project_locks.py, beside
the other site routes' (a foreign lock cannot be produced over HTTP).
"""
from __future__ import annotations

import copy

import pytest

import settings as settings_module
from services import site_service as ss


@pytest.fixture
def stub_dist(tmp_path, monkeypatch):
    """
    As test_sites_routes.stub_dist: an id whose decoded form carries a slash
    falls through to `main.serve_spa`, which answers 503 without a built
    frontend; a stub dist makes it answer the shipped 404.
    """
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    for name in ("index.html", "login.html", "spa.html"):
        (d / name).write_text("<html></html>", encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(d))
    settings_module.get_settings.cache_clear()
    try:
        yield d
    finally:
        settings_module.get_settings.cache_clear()

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

DOC = {
    "version": 1,
    "sites": [{
        "id": "site_a", "name": "Campus", "buses": ["B1"],
        "boundary": [[6.83, 53.44], [6.84, 53.44], [6.84, 53.43], [6.83, 53.43]],
        "origin": {"lng": 6.835, "lat": 53.435}, "placements": {},
    }],
}


def _url(name, sid="site_a"):
    return f"/api/projects/{name}/sites/{sid}/thumbnail"


def _put(client, name, data=PNG, sid="site_a", content_type="image/png"):
    return client.put(_url(name, sid), content=data, headers={"Content-Type": content_type})


@pytest.fixture
def site_project(client, api_project):
    name = api_project("thumb")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    return name


# ── round trip ──────────────────────────────────────────────────────────────

def test_get_404_before_any_capture(client, site_project):
    r = client.get(_url(site_project))
    assert r.status_code == 404, r.text


def test_put_then_get_round_trip(client, site_project, project_storage_dir):
    r = _put(client, site_project)
    assert r.status_code == 200, r.text
    assert r.json() == {"saved": site_project, "site": "site_a", "bytes": len(PNG)}
    assert (project_storage_dir(site_project) / ss.SITES_DIR / "site_a" / ss.THUMB_FILE).read_bytes() == PNG
    r = client.get(_url(site_project))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["cache-control"] == "no-store"
    assert r.content == PNG


def test_put_replaces_the_previous_capture(client, site_project):
    assert _put(client, site_project).status_code == 200
    newer = PNG + b"\x01"
    assert _put(client, site_project, newer).status_code == 200
    assert client.get(_url(site_project)).content == newer


# ── validation ──────────────────────────────────────────────────────────────

def test_put_wrong_content_type_415_writes_nothing(client, site_project):
    assert _put(client, site_project, content_type="image/jpeg").status_code == 415
    assert _put(client, site_project, content_type="application/octet-stream").status_code == 415
    assert client.get(_url(site_project)).status_code == 404


def test_put_content_type_with_parameters_is_accepted(client, site_project):
    assert _put(client, site_project, content_type="image/png; charset=binary").status_code == 200


def test_put_over_limit_413(client, site_project):
    big = PNG + b"\x00" * ss.MAX_THUMB_BYTES
    assert _put(client, site_project, big).status_code == 413
    assert client.get(_url(site_project)).status_code == 404


def test_put_not_a_png_422(client, site_project):
    r = _put(client, site_project, b"GIF89a" + b"\x00" * 16)
    assert r.status_code == 422, r.text
    assert "PNG" in r.json()["detail"]
    assert client.get(_url(site_project)).status_code == 404


# ── the site must exist; the id is contained ────────────────────────────────

def test_unknown_site_404_both_ways(client, site_project):
    assert client.get(_url(site_project, "nope")).status_code == 404
    assert _put(client, site_project, sid="nope").status_code == 404


@pytest.mark.parametrize("sid", ["%2E%2E", "..%2F..%2Fetc", "a%2Fb", "x" * 65, "sp%20ace", "a.b"])
def test_traversal_shaped_ids_are_404_and_write_nothing(client, stub_dist, site_project, project_storage_dir, sid):
    """
    As test_sites_routes::test_context_route_rejects_traversal_id: an id whose
    decoded form carries a slash never reaches the handler (404/405 from the
    router); every other shape is 404 from `site_dir`'s check, before any I/O.
    """
    pdir = project_storage_dir(site_project)
    before = sorted(p.as_posix() for p in pdir.rglob("*"))
    expected = (404, 405) if "%2F" in sid else (404,)
    assert client.get(_url(site_project, sid)).status_code in expected
    assert _put(client, site_project, sid=sid).status_code in expected
    assert sorted(p.as_posix() for p in pdir.rglob("*")) == before


def test_unknown_project_404(client):
    assert client.get(_url("does-not-exist")).status_code == 404
    assert _put(client, "does-not-exist").status_code == 404


def test_other_org_cannot_read_or_write(client, other_org_client, site_project):
    assert _put(client, site_project).status_code == 200
    assert other_org_client.get(_url(site_project)).status_code == 404
    assert other_org_client.put(_url(site_project), content=PNG, headers={"Content-Type": "image/png"}).status_code == 404


def test_put_sites_dropping_the_site_removes_its_thumbnail(client, site_project, project_storage_dir):
    """The thumbnail lives in the site dir, so the orphan prune takes it with the context cache."""
    assert _put(client, site_project).status_code == 200
    assert client.put(f"/api/projects/{site_project}/sites", json={"version": 1, "sites": []}).status_code == 200
    assert not (project_storage_dir(site_project) / ss.SITES_DIR / "site_a").exists()


def test_thumbnail_travels_with_a_scenario(client, site_project, project_storage_dir):
    assert _put(client, site_project).status_code == 200
    r = client.post(f"/api/projects/{site_project}/scenarios", json={"name": "thumb_child", "description": "x"})
    assert r.status_code in (200, 201), r.text
    child = r.json().get("name") or "thumb_child"
    assert client.get(_url(child)).content == PNG


# ── service helpers ─────────────────────────────────────────────────────────

def test_validate_thumbnail_rules():
    ss.validate_thumbnail(PNG)
    with pytest.raises(ss.ThumbnailInvalid):
        ss.validate_thumbnail(b"not png")
    with pytest.raises(ss.ThumbnailTooLarge):
        ss.validate_thumbnail(PNG + b"\x00" * ss.MAX_THUMB_BYTES)


def test_first_thumbnail_takes_document_order_and_skips_sites_without_one(tmp_path):
    doc = copy.deepcopy(DOC)
    doc["sites"].append({**copy.deepcopy(DOC["sites"][0]), "id": "site_b"})
    ss.write_sites(tmp_path, doc)
    assert ss.first_thumbnail(tmp_path) is None
    ss.write_thumbnail(ss.site_dir(tmp_path, "site_b"), PNG + b"b")
    assert ss.first_thumbnail(tmp_path) == PNG + b"b"
    ss.write_thumbnail(ss.site_dir(tmp_path, "site_a"), PNG + b"a")
    assert ss.first_thumbnail(tmp_path) == PNG + b"a"


def test_first_thumbnail_ignores_a_bad_id_and_a_missing_document(tmp_path):
    assert ss.first_thumbnail(tmp_path) is None
    doc = copy.deepcopy(DOC)
    doc["sites"][0]["id"] = "../x"   # never written by the route; the reader must still not join it
    (tmp_path / ss.SITES_FILE).write_text(ss.serialise(doc))
    assert ss.first_thumbnail(tmp_path) is None
