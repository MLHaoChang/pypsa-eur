"""
GeoJSON / KML → routes and sites (plan 2, M3): the parsers, the matcher and
`POST /api/projects/{name}/map_layout/import`.

The pure half is tested on hand-made features; the route half on a saved
project with two placed buses A—B, a Line L1 and a Link K1 between them.
The lock, the cap and the 422s are pinned the way `test_map_layout_routes`
pins the PUT's.
"""
from __future__ import annotations

import json

import pytest

from services import map_geometry_import as mgi
from services import map_layout_service as ml
from services import site_service as ss
from services.network_geometry import route_length_km
from tests.test_worksheet_foreign_lock import _is_lock_refusal, same_org_other_user  # noqa: F401

A = (6.0, 53.0)
B = (7.0, 53.0)
BEND = [6.5, 53.5]
# ~20 m north of A / B: within the 50 m default tolerance, so they ARE the buses.
A_NEAR = [6.0, 53.00018]
B_NEAR = [7.0, 53.00018]
SQUARE = [[5.99, 52.99], [6.01, 52.99], [6.01, 53.01], [5.99, 53.01]]

BUSES = {"A": A, "B": B, "C": (8.0, 53.0)}
BRANCHES = [mgi.Branch("line", "L1", "A", "B"), mgi.Branch("link", "K1", "A", "B"), mgi.Branch("tr", "T1", "B", "C")]


def fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def line(coords, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "LineString", "coordinates": coords}}


def polygon(ring, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [ring + [ring[0]]]}}


# ── parsing ─────────────────────────────────────────────────────────────────

def test_parse_geojson_flattens_collections_and_strips_the_closing_vertex():
    feats = mgi.parse_geojson(fc(
        line([[6, 53], BEND, [7, 53]], name="L1"),
        polygon(SQUARE, name="Plot"),
        {"type": "Feature", "properties": {}, "geometry": {"type": "MultiLineString", "coordinates": [[[1, 1], [2, 2]], [[3, 3], [4, 4]]]}},
        {"type": "Feature", "properties": {"name": "P"}, "geometry": {"type": "Point", "coordinates": [1, 1]}},
    ))
    assert [(f.index, f.geometry, f.name) for f in feats] == [
        (0, "LineString", "L1"), (1, "Polygon", "Plot"), (2, "LineString", None), (2, "LineString", None), (3, "Point", "P"),
    ]
    assert feats[1].coords == SQUARE


@pytest.mark.parametrize("bad", [
    [1, 2],
    {"type": "Nope"},
    {"type": "FeatureCollection", "features": "x"},
    fc({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[1, "x"]]}}),
    fc({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[200, 1], [1, 1]]}}),
    fc({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []}}),
])
def test_parse_geojson_rejects_malformed_input_naming_the_problem(bad):
    with pytest.raises(mgi.GeometryImportInvalid):
        mgi.parse_geojson(bad)


KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>L1</name><LineString><coordinates>
    6.0,53.0,0 6.5,53.5,0 7.0,53.0,0
  </coordinates></LineString></Placemark>
  <Placemark><name>Plot</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
    5.99,52.99 6.01,52.99 6.01,53.01 5.99,53.01 5.99,52.99
  </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
  <Placemark><name>Pin</name><Point><coordinates>6,53</coordinates></Point></Placemark>
</Document></kml>"""


def test_parse_kml_reads_placemarks_like_geojson_features():
    feats = mgi.parse_kml(KML)
    assert [(f.geometry, f.name) for f in feats] == [("LineString", "L1"), ("Polygon", "Plot"), ("unknown", "Pin")]
    assert feats[0].coords == [[6.0, 53.0], [6.5, 53.5], [7.0, 53.0]]
    assert feats[1].coords == SQUARE


@pytest.mark.parametrize("bad", ["<kml><Document></Document></kml>", "<kml><Placemark><LineString><coordinates>6</coordinates></LineString></Placemark></kml>", "<kml>"])
def test_parse_kml_rejects_an_empty_or_broken_document(bad):
    with pytest.raises(mgi.GeometryImportInvalid):
        mgi.parse_kml(bad)


def test_parse_features_sniffs_the_format():
    assert mgi.parse_features(KML.encode(), "x.kml")[0].name == "L1"
    assert mgi.parse_features(json.dumps(fc(line([[1, 1], [2, 2]]))).encode(), "x.geojson")[0].geometry == "LineString"
    with pytest.raises(mgi.GeometryImportInvalid):
        mgi.parse_features(b"not json, not xml")
    with pytest.raises(mgi.GeometryImportInvalid):
        mgi.parse_features(b"\xff\xfe")


# ── matching ────────────────────────────────────────────────────────────────

def test_match_by_name_keeps_the_interior_and_drops_end_vertices_at_the_buses():
    feats = mgi.parse_geojson(fc(line([A_NEAR, BEND, B_NEAR], name="L1")))
    r = mgi.match_features(feats, BRANCHES, BUSES)
    assert r["unmatched"] == [] and r["sites"] == []
    assert r["routes"] == [{"feature": 0, "label": "L1", "key": "line:L1", "how": "name", "points": [BEND], "reversed": False}]


def test_match_by_name_reverses_a_feature_drawn_bus1_to_bus0():
    feats = mgi.parse_geojson(fc(line([B_NEAR, BEND, A_NEAR], name="L1")))
    r = mgi.match_features(feats, BRANCHES, BUSES)
    assert r["routes"][0]["reversed"] is True and r["routes"][0]["points"] == [BEND]


def test_match_by_name_keeps_a_far_end_vertex_as_the_first_bend():
    far = [6.0, 53.01]  # ~1.1 km north of A: not the bus, a bend
    feats = mgi.parse_geojson(fc(line([far, BEND, B_NEAR], name="L1")))
    r = mgi.match_features(feats, BRANCHES, BUSES)
    assert r["routes"][0]["points"] == [far, BEND]


def test_match_by_name_honours_a_kind_hint():
    feats = mgi.parse_geojson(fc(line([A_NEAR, BEND, B_NEAR], name="K1", kind="link")))
    assert mgi.match_features(feats, BRANCHES, BUSES)["routes"][0]["key"] == "link:K1"


def test_match_by_endpoints_takes_the_first_unclaimed_branch_between_the_buses():
    feats = mgi.parse_geojson(fc(
        line([A_NEAR, BEND, B_NEAR], name="L1"),
        line([B_NEAR, [6.5, 52.5], A_NEAR]),          # unnamed, A—B: L1 is claimed, so K1
    ))
    r = mgi.match_features(feats, BRANCHES, BUSES)
    assert [(x["key"], x["how"], x["reversed"]) for x in r["routes"]] == [("line:L1", "name", False), ("link:K1", "endpoints", True)]
    assert r["routes"][1]["points"] == [[6.5, 52.5]]


def test_unmatched_reports_say_why():
    feats = mgi.parse_geojson(fc(
        line([[6.2, 53.2], [6.8, 53.2]]),                    # no bus near either end
        line([A_NEAR, [6.0, 53.0001]]),                       # both ends snap to A
        line([A_NEAR, [8.0, 53.00018]]),                      # A—C: no branch joins them
        line([[1, 1]]),                                       # one vertex
        line([A_NEAR, B_NEAR], name="L1"), line([A_NEAR, B_NEAR], name="L1"),   # the second claims a taken key
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [1, 1]}},
    ))
    r = mgi.match_features(feats, BRANCHES, BUSES)
    reasons = [u["reason"] for u in r["unmatched"]]
    assert "no placed bus within 50 m of either end" in reasons
    assert "both ends snap to the same bus" in reasons
    assert "no branch joins A and C" in reasons
    assert "fewer than two vertices" in reasons
    assert any("already matched" in x for x in reasons)
    assert "unsupported geometry Point" in reasons
    assert len(r["routes"]) == 1 and r["routes"][0]["points"] == []


def test_tolerance_is_a_parameter():
    far = [6.0, 53.002]  # ~220 m from A
    feats = mgi.parse_geojson(fc(line([far, B_NEAR])))
    assert mgi.match_features(feats, BRANCHES, BUSES)["routes"] == []
    assert mgi.match_features(feats, BRANCHES, BUSES, tolerance_m=300)["routes"][0]["key"] == "line:L1"


def test_polygon_becomes_a_site_with_the_buses_inside_and_the_centroid_origin():
    feats = mgi.parse_geojson(fc(polygon(SQUARE, name="Plot"), polygon([[1, 1], [2, 2]])))
    r = mgi.match_features(feats, BRANCHES, BUSES, existing_site_ids={"taken"})
    assert len(r["sites"]) == 1
    s = r["sites"][0]
    assert s["name"] == "Plot" and s["buses"] == ["A"] and s["boundary"] == SQUARE
    assert s["origin"]["lng"] == pytest.approx(6.0, abs=1e-6) and s["origin"]["lat"] == pytest.approx(53.0, abs=1e-6)
    assert ss.SITE_ID_RE.match(s["id"]) and s["id"] != "taken"
    assert r["unmatched"] == [{"feature": 1, "label": "feature 1", "reason": "polygon needs at least three vertices"}]


def test_site_service_boundary_maths_match_the_frontend_rules():
    assert ss.point_in_boundary(6.0, 53.0, SQUARE)
    assert ss.point_in_boundary(5.99, 53.0, SQUARE)       # on the edge counts as inside
    assert not ss.point_in_boundary(6.02, 53.0, SQUARE)
    assert not ss.point_in_boundary(6.0, 53.0, SQUARE[:2])
    assert ss.buses_inside({"A": A, "B": B}, SQUARE) == ["A"]
    assert ss.centroid([[0, 0], [2, 0]]) == (1.0, 0.0)   # degenerate: the vertex mean


# ── apply ───────────────────────────────────────────────────────────────────

def test_apply_writes_routes_as_import_and_appends_sites(tmp_path):
    ml.write_map_layout(tmp_path, {"version": 1, "routes": {"line:L1": {"points": [[1, 1]], "source": "user"}, "link:K1": {"points": [[2, 2]], "source": "user"}}, "bubbles": {}})
    ss.write_sites(tmp_path, {"version": 1, "sites": [{"id": "old", "name": "Old", "buses": [], "boundary": SQUARE, "origin": {"lng": 6, "lat": 53}, "placements": {}}]})
    report = {
        "routes": [{"key": "line:L1", "points": [BEND]}, {"key": "link:K1", "points": []}],
        "sites": [{"id": "old", "name": "Plot", "buses": ["A"], "boundary": SQUARE, "origin": {"lng": 6.0, "lat": 53.0}}],
    }
    written = mgi.apply_report(tmp_path, report)
    doc = ml.read_map_layout(tmp_path)
    assert doc["routes"] == {"line:L1": {"points": [BEND], "source": "import"}}   # K1: a chord removes the old route
    sites = ss.read_sites(tmp_path)["sites"]
    assert [s["id"] for s in sites][0] == "old" and len(sites) == 2 and sites[1]["id"] != "old"
    assert sites[1]["buses"] == ["A"] and sites[1]["placements"] == {}
    assert written == {"routes": ["line:L1", "link:K1"], "sites": [sites[1]["id"]]}


# ── the route ───────────────────────────────────────────────────────────────

def _bus(client, name, xy):
    r = client.post("/api/network/buses", json={"name": name, "v_nom": 110.0, "x": xy[0], "y": xy[1]})
    assert r.status_code == 201, r.text


@pytest.fixture
def geo_project(client, api_project):
    name = api_project("geo-import")
    _bus(client, "A", A)
    _bus(client, "B", B)
    r = client.post("/api/network/lines", json={"name": "L1", "bus0": "A", "bus1": "B", "length": 1.0, "r": 0.3, "x": 1.7, "b": 1e-5, "s_nom": 100.0})
    assert r.status_code == 201, r.text
    r = client.post("/api/network/links", json={"name": "K1", "bus0": "A", "bus1": "B", "p_nom": 50.0, "carrier": "DC", "length": 0.0})
    assert r.status_code == 201, r.text
    return name


URL = "/api/projects/{}/map_layout/import"
PAYLOAD = fc(line([A_NEAR, BEND, B_NEAR], name="L1"), line([B_NEAR, [6.5, 52.5], A_NEAR]), polygon(SQUARE, name="Plot"))


def test_preview_returns_the_report_and_writes_nothing(client, geo_project, project_storage_dir):
    r = client.post(URL.format(geo_project), json=PAYLOAD)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is False
    assert [(x["key"], x["how"]) for x in body["routes"]] == [("line:L1", "name"), ("link:K1", "endpoints")]
    assert body["sites"][0]["buses"] == ["A"] and body["unmatched"] == []
    pdir = project_storage_dir(geo_project)
    assert not (pdir / ml.MAP_LAYOUT_FILE).exists() and not (pdir / ss.SITES_FILE).exists()


def test_apply_writes_both_sidecars_and_leaves_lengths_alone_with_the_setting_off(client, geo_project, project_storage_dir):
    r = client.post(URL.format(geo_project), params={"apply": "true"}, json=PAYLOAD)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is True and body["written"]["routes"] == ["line:L1", "link:K1"]
    assert "lengths" not in body
    doc = ml.read_map_layout(project_storage_dir(geo_project))
    assert doc["routes"]["line:L1"] == {"points": [BEND], "source": "import"}
    assert doc["routes"]["link:K1"]["source"] == "import"
    sites = ss.read_sites(project_storage_dir(geo_project))["sites"]
    assert len(sites) == 1 and sites[0]["name"] == "Plot" and sites[0]["buses"] == ["A"]
    assert client.get(f"/api/projects/{geo_project}/sites").json()["sites"][0]["id"] == sites[0]["id"]
    lengths = {ln["name"]: ln["length"] for ln in client.get("/api/network/lines").json()}
    assert lengths["L1"] == 1.0


def test_apply_with_the_setting_on_derives_the_applied_lengths(client, geo_project):
    assert client.patch(f"/api/projects/{geo_project}/settings", json={"derive_lengths_from_geometry": True}).status_code == 200
    r = client.post(URL.format(geo_project), params={"apply": "true"}, json=PAYLOAD)
    assert r.status_code == 200, r.text
    lengths = r.json()["lengths"]
    assert lengths["updated"] == 2 and lengths["sources"] == {"line:L1": "route", "link:K1": "route"}
    assert [p["name"] for p in lengths["rescale"]] == ["L1"]
    got = {ln["name"]: ln["length"] for ln in client.get("/api/network/lines").json()}
    assert got["L1"] == pytest.approx(route_length_km([list(A), BEND, list(B)]), rel=1e-9)


def test_multipart_kml_upload_is_accepted(client, geo_project):
    r = client.post(URL.format(geo_project), files={"file": ("survey.kml", KML.encode(), "application/vnd.google-earth.kml+xml")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["key"] for x in body["routes"]] == ["line:L1"] and body["sites"][0]["name"] == "Plot"
    assert body["unmatched"] == [{"feature": 2, "label": "Pin", "reason": "unsupported geometry unknown"}]


def test_multipart_without_a_file_part_is_422(client, geo_project):
    r = client.post(URL.format(geo_project), files={"other": ("x.txt", b"x", "text/plain")})
    assert r.status_code == 422


@pytest.mark.parametrize("body", [b"{not json", b'{"type": "Nope"}', b"<kml>"])
def test_malformed_geojson_or_kml_is_422(client, geo_project, body):
    r = client.post(URL.format(geo_project), content=body, headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text


def test_over_the_cap_is_413(client, geo_project, monkeypatch):
    monkeypatch.setattr(mgi, "MAX_IMPORT_BYTES", 64)
    r = client.post(URL.format(geo_project), json=PAYLOAD)
    assert r.status_code == 413, r.text


def test_tolerance_query_param_is_validated_and_used(client, geo_project):
    far = [6.0, 53.002]
    payload = fc(line([far, B_NEAR]))
    assert client.post(URL.format(geo_project), params={"tolerance_m": "0"}, json=payload).status_code == 422
    assert client.post(URL.format(geo_project), json=payload).json()["routes"] == []
    assert client.post(URL.format(geo_project), params={"tolerance_m": "300"}, json=payload).json()["routes"][0]["key"] == "line:L1"


def test_apply_refused_under_a_foreign_lock(client, geo_project, same_org_other_user):  # noqa: F811
    assert client.post(f"/api/projects/{geo_project}/lock").status_code == 200
    r = same_org_other_user.post(URL.format(geo_project), params={"apply": "true"}, json=PAYLOAD)
    assert _is_lock_refusal(r), f"{r.status_code} {r.text[:160]}"


def test_a_project_that_is_not_open_is_409(client, api_project):
    other = api_project("other-geo")
    api_project("open-geo")
    r = client.post(URL.format(other), json=PAYLOAD)
    assert r.status_code == 409 and "Open project" in r.json()["detail"]


def test_missing_project_404(client):
    assert client.post(URL.format("does-not-exist"), json=PAYLOAD).status_code == 404
