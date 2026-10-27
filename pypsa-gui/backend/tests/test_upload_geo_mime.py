"""
GeoJSON / KML through `POST /api/projects/{name}/uploads`: accepted only when
the bytes parse as the map import reads them (`upload_service.geo_mime_for`),
refused when the name or declared type says geo and the bytes do not.
"""
from __future__ import annotations

import json

import pytest

from services import map_geometry_import as mgi
from services import upload_service

GEOJSON = json.dumps({
    "type": "FeatureCollection",
    "features": [{"type": "Feature", "properties": {"name": "L1"},
                  "geometry": {"type": "LineString", "coordinates": [[8.0, 50.0], [8.1, 50.1]]}}],
}).encode()

KML = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
       b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>L1</name>'
       b'<LineString><coordinates>8.0,50.0 8.1,50.1</coordinates></LineString>'
       b'</Placemark></Document></kml>')

BILLION_LAUGHS = (b'<?xml version="1.0"?>\n<!DOCTYPE kml [<!ENTITY a "aaaaaaaaaa">'
                  b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>\n'
                  b'<kml><Placemark><name>&b;</name></Placemark></kml>')

ELF = (b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8 + b"\x02\x00\x3e\x00\x01\x00\x00\x00" + b"\x00" * 64)


@pytest.fixture
def route_project(api_project):
    return api_project("demo")


def _post(client, project, filename, data, declared):
    return client.post(f"/api/projects/{project}/uploads", files={"file": (filename, data, declared)})


@pytest.mark.parametrize("declared", ["application/geo+json", "application/json", "application/octet-stream"])
def test_geojson_upload_is_stored_as_geo_json(client, route_project, declared):
    r = _post(client, route_project, "routes.geojson", GEOJSON, declared)
    assert r.status_code == 200, r.text
    assert r.json()["mime"] == "application/geo+json"


@pytest.mark.parametrize("declared", ["application/vnd.google-earth.kml+xml", "application/octet-stream"])
def test_kml_upload_is_stored_as_kml(client, route_project, declared):
    r = _post(client, route_project, "routes.kml", KML, declared)
    assert r.status_code == 200, r.text
    assert r.json()["mime"] == "application/vnd.google-earth.kml+xml"


@pytest.mark.parametrize("filename,data,declared", [
    ("routes.geojson", ELF, "application/geo+json"),                        # executable renamed
    ("routes.kml", ELF, "application/vnd.google-earth.kml+xml"),
    ("routes.geojson", b'{"hello": "world"}', "application/geo+json"),     # JSON, not GeoJSON
    ("routes.geojson", KML, "application/geo+json"),                        # KML named .geojson
    ("routes.kml", GEOJSON, "application/vnd.google-earth.kml+xml"),        # GeoJSON named .kml
    ("routes.kml", BILLION_LAUGHS, "application/vnd.google-earth.kml+xml"),  # entity expansion
    ("data.json", b'{"hello": "world"}', "application/geo+json"),          # declared type only
])
def test_spoofed_geo_upload_is_refused(client, route_project, filename, data, declared):
    r = _post(client, route_project, filename, data, declared)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error_kind"] == "unsupported_mime"


def test_declared_geo_type_is_still_checked_without_libmagic(client, route_project, monkeypatch):
    """With no libmagic the sniff falls back to the declared type; the content check still runs."""
    import builtins

    real_import = builtins.__import__

    def no_magic(name, *args, **kwargs):
        if name == "magic":
            raise ImportError("no libmagic")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_magic)
    refused = _post(client, route_project, "x.geojson", b'{"hello": "world"}', "application/geo+json")
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"]["error_kind"] == "unsupported_mime"
    accepted = _post(client, route_project, "x.geojson", GEOJSON, "application/geo+json")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["mime"] == "application/geo+json"


def test_geo_upload_over_the_import_cap_is_refused(client, route_project, monkeypatch):
    monkeypatch.setattr(mgi, "MAX_IMPORT_BYTES", len(GEOJSON) - 1)
    r = _post(client, route_project, "routes.geojson", GEOJSON, "application/geo+json")
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["error_kind"] == "file_too_large"


def test_bare_json_and_xml_stay_refused():
    assert "application/json" not in upload_service.ALLOWED_MIME_TYPES
    assert "text/xml" not in upload_service.ALLOWED_MIME_TYPES
    assert "application/xml" not in upload_service.ALLOWED_MIME_TYPES
    assert "application/octet-stream" not in upload_service.ALLOWED_MIME_TYPES


@pytest.mark.parametrize("data,match", [
    (BILLION_LAUGHS, "DOCTYPE or entity"),
    (b'<?xml version="1.0"?><!doctype kml SYSTEM "file:///etc/passwd"><kml/>', "DOCTYPE or entity"),
    (b'<kml><!eNtItY x "y"></kml>', "DOCTYPE or entity"),
    (BILLION_LAUGHS.decode().encode("utf-16"), "not UTF-8"),
    (b'<?xml version="1.0" encoding="UTF-16"?><kml/>', "UTF-16 encoding"),
    (b'<?xml version="1.0" encoding="iso-8859-1"?><!DOCTYPE kml><kml/>', "encoding"),
])
def test_kml_the_import_parser_must_not_expand_is_refused(data, match):
    with pytest.raises(mgi.GeometryImportInvalid, match=match):
        mgi.parse_features(data, "routes.kml")


def test_utf16_kml_upload_is_refused(client, route_project):
    r = _post(client, route_project, "routes.kml", BILLION_LAUGHS.decode().encode("utf-16"),
              "application/vnd.google-earth.kml+xml")
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error_kind"] == "unsupported_mime"


def test_the_size_cap_is_checked_before_any_parse(client, route_project, monkeypatch):
    monkeypatch.setattr(mgi, "MAX_IMPORT_BYTES", len(KML) - 1)
    monkeypatch.setattr(mgi, "parse_features", lambda *a, **k: pytest.fail("parsed over the cap"))
    r = _post(client, route_project, "routes.kml", KML, "application/vnd.google-earth.kml+xml")
    assert r.status_code == 413, r.text
