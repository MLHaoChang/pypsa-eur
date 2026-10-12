"""
OSM route suggestions (plan 2, M3): the query, the shape read from the
recorded Overpass fixture, the in-memory cache and backoff, and
`GET /api/projects/{name}/map_layout/osm_suggestions`.
"""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest

from services import osm_suggestions as osm
from services import site_context as sc

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "overpass_eemshaven.json"
BBOX = (6.82, 53.43, 6.84, 53.45)
BBOX_Q = "6.82,53.43,6.84,53.45"


def elements():
    return json.loads(FIXTURE.read_text())["elements"]


@pytest.fixture(autouse=True)
def _fresh_cache():
    osm.clear_cache()
    yield
    osm.clear_cache()


# ── pure ────────────────────────────────────────────────────────────────────

def test_query_asks_for_power_ways_and_substations_only():
    q = osm.suggestions_query(BBOX)
    assert q.startswith("[out:json]")
    assert 'way["power"~"^(line|minor_line|cable)$"](53.430000,6.820000,53.450000,6.840000)' in q
    assert 'way["power"="substation"]' in q and 'way["building"]' not in q and q.endswith("out geom;")


def test_fixture_yields_one_power_line_and_one_substation():
    s = osm.elements_to_suggestions(elements())
    assert [ln["kind"] for ln in s["lines"]] == ["line"]
    assert len(s["lines"][0]["points"]) >= 2 and all(len(p) == 2 for p in s["lines"][0]["points"])
    assert [a["kind"] for a in s["areas"]] == ["substation"]
    assert len(s["areas"][0]["polygon"]) >= 3 and s["areas"][0]["polygon"][0] != s["areas"][0]["polygon"][-1]


def test_degenerate_ways_nodes_and_relations_are_ignored():
    s = osm.elements_to_suggestions([
        {"type": "node", "id": 1, "tags": {"power": "pole"}},
        {"type": "relation", "id": 2, "tags": {"power": "line"}},
        {"type": "way", "id": 3, "tags": {"power": "line"}, "geometry": [{"lat": 1, "lon": 1}]},
        {"type": "way", "id": 4, "tags": {"power": "substation"}, "geometry": [{"lat": 1, "lon": 1}, {"lat": 2, "lon": 2}]},
        {"type": "way", "id": 5, "tags": {"highway": "x"}, "geometry": [{"lat": 1, "lon": 1}, {"lat": 2, "lon": 2}]},
    ])
    assert s == {"lines": [], "areas": []}


@pytest.mark.parametrize("raw", ["", "1,2,3", "a,b,c,d", "7,53,6,54", "-200,1,1,2", "0,0,10,10"])
def test_bbox_parsing_refuses_malformed_or_district_sized_boxes(raw):
    with pytest.raises(osm.OsmBboxInvalid):
        osm.parse_bbox(raw)


def test_bbox_parsing_accepts_a_site_sized_box():
    assert osm.parse_bbox(BBOX_Q) == BBOX


# ── the wire ────────────────────────────────────────────────────────────────

def _transport(status=200, body=None, seen=None, raise_exc=None):
    seen = seen if seen is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if raise_exc is not None:
            raise raise_exc
        return httpx.Response(status, content=body if body is not None else json.dumps({"elements": elements()}))

    return httpx.MockTransport(handler), seen


def test_fetch_posts_once_with_the_user_agent_and_carries_attribution(monkeypatch):
    monkeypatch.delenv(sc.OVERPASS_URL_ENV, raising=False)
    transport, seen = _transport()
    doc = osm.fetch_suggestions(BBOX, transport=transport)
    assert len(seen) == 1 and seen[0].method == "POST" and seen[0].url == httpx.URL(sc.DEFAULT_OVERPASS_URL)
    assert seen[0].headers["user-agent"].startswith("pypsa-gui/")
    assert doc["attribution"] == "© OpenStreetMap contributors (ODbL)"
    assert doc["cached"] is False and len(doc["lines"]) == 1 and len(doc["areas"]) == 1 and doc["bbox"] == list(BBOX)


def test_a_second_fetch_within_the_ttl_is_served_from_memory(monkeypatch):
    transport, seen = _transport()
    ticks = iter([0.0, 1.0, osm.SUGGESTION_TTL_S + 2.0])
    clock = lambda: next(ticks)  # noqa: E731
    osm.fetch_suggestions(BBOX, transport=transport, clock=clock)
    again = osm.fetch_suggestions(BBOX, transport=transport, clock=clock)
    assert again["cached"] is True and len(seen) == 1
    osm.fetch_suggestions(BBOX, transport=transport, clock=clock)
    assert len(seen) == 2


@pytest.mark.parametrize("status,expected", [(429, 429), (504, 504), (500, 502)])
def test_upstream_refusal_maps_to_a_status_and_names_the_setting(monkeypatch, status, expected):
    monkeypatch.setenv(sc.OVERPASS_URL_ENV, "https://overpass.example/api/interpreter")
    transport, _ = _transport(status=status)
    with pytest.raises(osm.OsmUnavailable) as exc:
        osm.fetch_suggestions(BBOX, transport=transport)
    assert exc.value.status == expected
    assert sc.OVERPASS_URL_ENV in str(exc.value) and "overpass.example" in str(exc.value)


def test_a_refusal_backs_off_without_a_second_request():
    transport, seen = _transport(status=429)
    ticks = iter([0.0, 1.0, osm.BACKOFF_S + 1.0])
    clock = lambda: next(ticks)  # noqa: E731
    with pytest.raises(osm.OsmUnavailable):
        osm.fetch_suggestions(BBOX, transport=transport, clock=clock)
    with pytest.raises(osm.OsmUnavailable) as exc:
        osm.fetch_suggestions(BBOX, transport=transport, clock=clock)
    assert exc.value.status == 429 and len(seen) == 1
    with pytest.raises(osm.OsmUnavailable):
        osm.fetch_suggestions(BBOX, transport=transport, clock=clock)
    assert len(seen) == 2


def test_a_timeout_is_504_and_an_unreachable_host_is_502():
    transport, _ = _transport(raise_exc=httpx.ReadTimeout("slow"))
    with pytest.raises(osm.OsmUnavailable) as exc:
        osm.fetch_suggestions(BBOX, transport=transport)
    assert exc.value.status == 504
    osm.clear_cache()
    transport, _ = _transport(raise_exc=httpx.ConnectError("down"))
    with pytest.raises(osm.OsmUnavailable) as exc:
        osm.fetch_suggestions(BBOX, transport=transport)
    assert exc.value.status == 502


# ── the route ───────────────────────────────────────────────────────────────

def test_route_serves_suggestions_and_maps_errors(client, api_project, monkeypatch):
    name = api_project("osm")
    calls = []

    def fake(bbox, **kw):
        calls.append(bbox)
        return osm.suggestions_document(bbox, osm.elements_to_suggestions(elements()))

    monkeypatch.setattr(osm, "fetch_suggestions", fake)
    r = client.get(f"/api/projects/{name}/map_layout/osm_suggestions", params={"bbox": BBOX_Q})
    assert r.status_code == 200, r.text
    assert len(r.json()["lines"]) == 1 and r.json()["attribution"].startswith("© OpenStreetMap")
    assert calls == [BBOX]

    assert client.get(f"/api/projects/{name}/map_layout/osm_suggestions", params={"bbox": "1,2,3"}).status_code == 422
    assert client.get(f"/api/projects/{name}/map_layout/osm_suggestions", params={"bbox": "0,0,10,10"}).status_code == 422

    def refuse(bbox, **kw):
        raise osm.OsmUnavailable(429, f"rate limited; set {sc.OVERPASS_URL_ENV}")

    monkeypatch.setattr(osm, "fetch_suggestions", refuse)
    r = client.get(f"/api/projects/{name}/map_layout/osm_suggestions", params={"bbox": BBOX_Q})
    assert r.status_code == 429 and sc.OVERPASS_URL_ENV in r.json()["detail"]
    assert client.get("/api/projects/nope/map_layout/osm_suggestions", params={"bbox": BBOX_Q}).status_code == 404
