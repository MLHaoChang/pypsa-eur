"""
services/site_context.py — the pure core (Task 5.1) and the wire (Task 5.2).

The Overpass fixture is SYNTHETIC, in the exact shape of an `out geom;`
response (this container cannot reach overpass-api.de); it covers every
branch of the height rule, a degenerate way, a relation and a node. The
Terrarium tiles are built in the tests from known height fields.
"""
from __future__ import annotations

import io
import json
import pathlib

import httpx
import pytest
from PIL import Image

from services import site_context as sc

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "overpass_eemshaven.json"
BOUNDARY = [[6.8291, 53.4412], [6.8351, 53.4412], [6.8351, 53.4381], [6.8291, 53.4381]]


def elements():
    return json.loads(FIXTURE.read_text())["elements"]


def terrarium_png(height_fn) -> bytes:
    """A 256×256 Terrarium tile whose height at (x, y) is height_fn(x, y)."""
    im = Image.new("RGB", (256, 256))
    px = im.load()
    for y in range(256):
        for x in range(256):
            v = height_fn(x, y) + 32768
            r = int(v // 256)
            g = int(v % 256)
            b = int(round((v - int(v)) * 256)) % 256
            px[x, y] = (r, g, b)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


# ── padded bbox / query ─────────────────────────────────────────────────────

def test_padded_bbox_pads_by_the_larger_of_150m_or_a_quarter():
    b = sc.padded_bbox(BOUNDARY)
    # The boundary is ~400 m × ~345 m: a quarter is 100 / 86 m, so the 150 m floor wins.
    m_lat = 111_319.5
    assert (53.4381 - b[1]) * m_lat == pytest.approx(150, rel=0.02)
    assert (b[3] - 53.4412) * m_lat == pytest.approx(150, rel=0.02)
    big = [[6.80, 53.40], [6.90, 53.40], [6.90, 53.48], [6.80, 53.48]]  # ~6.6 km × 8.9 km
    bb = sc.padded_bbox(big)
    assert (53.40 - bb[1]) * m_lat == pytest.approx(8_900 * 0.25, rel=0.05)


def test_overpass_query_names_every_selector_in_overpass_order():
    q = sc.overpass_query((6.8, 53.43, 6.84, 53.45), timeout_s=20)
    assert q.startswith("[out:json][timeout:20];(")
    for sel in ('way["building"]', 'way["highway"]', 'way["railway"]', 'way["barrier"]', 'way["landuse"]', 'way["power"]', 'way["natural"="water"]'):
        assert sel in q
    # (south, west, north, east)
    assert "(53.430000,6.800000,53.450000,6.840000)" in q
    assert q.endswith(");out geom;")


# ── elements → context ──────────────────────────────────────────────────────

def test_elements_to_context_reads_ways_only_and_drops_degenerate_ones():
    ctx = sc.elements_to_context(elements())
    ids = {b["id"] for b in ctx["buildings"]}
    assert ids == {1001, 1002, 1003, 1004, 1005}, ids   # 1006 has two vertices; 9001 is a relation
    assert {l["id"] for l in ctx["lines"]} == {2001, 2002, 2003, 2004}
    assert {a["id"] for a in ctx["areas"]} == {3001, 3002, 3003}
    assert {l["kind"] for l in ctx["lines"]} == {"road", "rail", "fence", "power_line"}
    assert {a["kind"] for a in ctx["areas"]} == {"landuse", "power_substation", "water"}


def test_height_rule_every_branch():
    by_id = {b["id"]: b for b in sc.elements_to_context(elements())["buildings"]}
    assert (by_id[1001]["height_m"], by_id[1001]["height_source"]) == (12.5, "tag")
    assert (by_id[1002]["height_m"], by_id[1002]["height_source"]) == (pytest.approx(9.9), "levels")
    assert (by_id[1003]["height_m"], by_id[1003]["height_source"]) == (8.0, "landuse")   # warehouse type
    assert (by_id[1004]["height_m"], by_id[1004]["height_source"]) == (6.0, "landuse")   # house type
    # An unparsable height tag falls through; 1005 sits outside the landuse
    # area and has a plain `building=yes`, so it takes the fallback.
    assert (by_id[1005]["height_m"], by_id[1005]["height_source"]) == (5.0, "default")


@pytest.mark.parametrize("raw, expected", [
    ("12", 12.0), ("12 m", 12.0), ("12m", 12.0), ("12,5", 12.5), ("3 metres", 3.0),
    ("40 ft", pytest.approx(12.192)), ("40'", pytest.approx(12.192)),
    ("0", None), ("-5", None), ("nan", None), ("12 cm", None), ("tall", None), (None, None),
])
def test_height_tag_parsing_handles_units(raw, expected):
    assert sc._parse_float(raw) == expected


def test_landuse_containment_supplies_a_height_for_a_plain_building():
    els = elements()
    # A plain building inside the industrial landuse polygon.
    els.append({"type": "way", "id": 1007, "tags": {"building": "yes"},
                "geometry": [{"lat": 53.4400, "lon": 6.8310}, {"lat": 53.4400, "lon": 6.8312}, {"lat": 53.4398, "lon": 6.8312}, {"lat": 53.4398, "lon": 6.8310}]})
    b = {x["id"]: x for x in sc.elements_to_context(els)["buildings"]}[1007]
    assert (b["height_m"], b["height_source"]) == (8.0, "landuse")


def test_closing_vertex_is_dropped_from_rings():
    ctx = sc.elements_to_context(elements())
    b = {x["id"]: x for x in ctx["buildings"]}[1001]
    assert len(b["polygon"]) == 4
    assert b["polygon"][0] != b["polygon"][-1]


# ── terrain ─────────────────────────────────────────────────────────────────

def test_decode_terrarium_recovers_the_height():
    png = terrarium_png(lambda x, y: 12.5)
    grid = sc.decode_terrarium(png)
    assert len(grid) == 256 and len(grid[0]) == 256
    assert grid[10][10] == pytest.approx(12.5, abs=0.01)
    assert sc.decode_terrarium(terrarium_png(lambda x, y: -4.5))[0][0] == pytest.approx(-4.5, abs=0.01)


def test_terrain_grid_flat_and_ramp_and_missing_tile():
    bbox = sc.padded_bbox(BOUNDARY)
    keys = sc.terrain_tiles_for(bbox)
    assert keys and all(k[0] == sc.TERRAIN_ZOOM for k in keys)
    flat = {k: sc.decode_terrarium(terrarium_png(lambda x, y: 3.0)) for k in keys}
    g = sc.terrain_grid(flat, bbox, grid=8)
    assert g["grid"] == 8 and len(g["heights_m"]) == 64
    assert all(h == pytest.approx(3.0, abs=0.01) for h in g["heights_m"])
    assert g["missing_tiles"] == 0
    # A ramp rising to the east within each tile: the first row's samples do not decrease.
    ramp = {k: sc.decode_terrarium(terrarium_png(lambda x, y: x / 10)) for k in keys}
    row0 = sc.terrain_grid(ramp, bbox, grid=8)["heights_m"][:8]
    if len({k[1] for k in keys}) == 1:  # one tile column: monotone
        assert all(a <= b + 1e-9 for a, b in zip(row0, row0[1:]))
    # A missing tile is filled with the mean of the known cells: a bbox that
    # straddles two tile columns, one of which never arrived.
    edge_lng = keys[0][1] / 2 ** sc.TERRAIN_ZOOM * 360.0 - 180.0  # west edge of the first tile
    two = (edge_lng - 0.002, bbox[1], edge_lng + 0.002, bbox[3])
    two_keys = sc.terrain_tiles_for(two)
    assert len({k[1] for k in two_keys}) == 2
    holed = {k: (None if k[1] < keys[0][1] else flat[keys[0]]) for k in two_keys}
    g2 = sc.terrain_grid(holed, two, grid=8)
    assert g2["missing_tiles"] == len([k for k in two_keys if k[1] < keys[0][1]]) >= 1
    assert all(h == pytest.approx(3.0, abs=0.01) for h in g2["heights_m"])
    # Every tile missing: zeros, not NaN.
    g3 = sc.terrain_grid({k: None for k in keys}, bbox, grid=4)
    assert g3["heights_m"] == [0.0] * 16


# ── document ────────────────────────────────────────────────────────────────

def test_context_document_carries_attribution_and_version():
    doc = sc.context_document((1, 2, 3, 4), sc.elements_to_context(elements()), None)
    assert doc["version"] == 1 and doc["source"] == "overpass"
    assert any("OpenStreetMap" in a for a in doc["attribution"])
    assert any("Terrain" in a for a in doc["attribution"])
    assert doc["bbox"] == [1, 2, 3, 4] and doc["terrain"] is None
    assert doc["fetched_at"].endswith("Z")
    json.dumps(doc, allow_nan=False)  # serialisable


# ── the wire (Task 5.2) ─────────────────────────────────────────────────────

def _transport(overpass_status=200, overpass_body=None, tile_status=200, seen=None):
    seen = seen if seen is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "overpass" in request.url.host or request.url.path.endswith("interpreter"):
            body = overpass_body if overpass_body is not None else json.dumps({"elements": elements()})
            return httpx.Response(overpass_status, content=body, headers={"content-type": "application/json"})
        if "elevation-tiles-prod" in request.url.path:
            if tile_status != 200:
                return httpx.Response(tile_status)
            return httpx.Response(200, content=terrarium_png(lambda x, y: 2.0), headers={"content-type": "image/png"})
        return httpx.Response(404)

    return httpx.MockTransport(handler), seen


def test_fetch_posts_once_to_overpass_with_a_user_agent_and_returns_the_document(monkeypatch):
    monkeypatch.delenv(sc.OVERPASS_URL_ENV, raising=False)
    transport, seen = _transport()
    doc = sc.fetch_context(BOUNDARY, transport=transport)
    posts = [r for r in seen if r.method == "POST"]
    assert len(posts) == 1
    assert posts[0].url == httpx.URL(sc.DEFAULT_OVERPASS_URL)
    assert posts[0].headers["user-agent"].startswith("pypsa-gui/")
    assert b"data=" in posts[0].content
    assert len(doc["buildings"]) == 5
    assert doc["terrain"] is not None and doc["terrain"]["missing_tiles"] == 0
    assert all(h == pytest.approx(2.0, abs=0.01) for h in doc["terrain"]["heights_m"])
    tile_gets = [r for r in seen if r.method == "GET"]
    assert all(r.headers["user-agent"].startswith("pypsa-gui/") for r in tile_gets)


def test_env_overrides_the_overpass_endpoint(monkeypatch):
    monkeypatch.setenv(sc.OVERPASS_URL_ENV, "https://overpass.example.org/api/interpreter")
    transport, seen = _transport()
    sc.fetch_context(BOUNDARY, transport=transport)
    assert seen[0].url.host == "overpass.example.org"


@pytest.mark.parametrize("status", [429, 504, 500])
def test_upstream_refusal_names_the_status_and_the_setting(monkeypatch, status):
    monkeypatch.delenv(sc.OVERPASS_URL_ENV, raising=False)
    transport, _ = _transport(overpass_status=status)
    with pytest.raises(sc.SiteContextUnavailable) as exc:
        sc.fetch_context(BOUNDARY, transport=transport)
    assert str(status) in str(exc.value)
    assert sc.OVERPASS_URL_ENV in str(exc.value)


def test_unreachable_overpass_is_reported_not_raised_raw(monkeypatch):
    monkeypatch.delenv(sc.OVERPASS_URL_ENV, raising=False)

    def boom(request):
        raise httpx.ConnectError("reset")

    with pytest.raises(sc.SiteContextUnavailable) as exc:
        sc.fetch_context(BOUNDARY, transport=httpx.MockTransport(boom))
    assert "ConnectError" in str(exc.value) and sc.OVERPASS_URL_ENV in str(exc.value)


def test_a_missing_terrain_tile_is_tolerated(monkeypatch):
    monkeypatch.delenv(sc.OVERPASS_URL_ENV, raising=False)
    transport, _ = _transport(tile_status=404)
    doc = sc.fetch_context(BOUNDARY, transport=transport)
    assert doc["terrain"]["missing_tiles"] >= 1
    assert doc["terrain"]["heights_m"] == [0.0] * (sc.TERRAIN_GRID ** 2)


def test_the_budget_stops_terrain_fetching(monkeypatch):
    monkeypatch.delenv(sc.OVERPASS_URL_ENV, raising=False)
    transport, seen = _transport()
    ticks = iter([0.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    doc = sc.fetch_context(BOUNDARY, transport=transport, budget_s=25.0, clock=lambda: next(ticks, 100.0))
    assert not [r for r in seen if r.method == "GET"], "no tile may be fetched once the budget is spent"
    assert doc["terrain"]["missing_tiles"] == len(sc.terrain_tiles_for(sc.padded_bbox(BOUNDARY)))


def test_budget_is_under_the_frontend_timeout():
    assert sc.TOTAL_BUDGET_S <= 25.0 < 30.0


# ── cache ───────────────────────────────────────────────────────────────────

def test_cache_round_trip_and_clear(tmp_path):
    site_dir = tmp_path / "sites" / "s1"
    assert sc.read_cached(site_dir) is None
    doc = sc.context_document((1, 2, 3, 4), sc.elements_to_context(elements()), None)
    sc.write_cached(site_dir, doc)
    assert sc.read_cached(site_dir) == doc
    (site_dir / sc.CONTEXT_FILE).write_text("{corrupt")
    assert sc.read_cached(site_dir) is None
    assert sc.clear_cached(site_dir) is True
    assert sc.clear_cached(site_dir) is False


def test_terrain_url_env_override_points_the_tile_fetch_at_a_mirror(monkeypatch):
    monkeypatch.setenv(sc.TERRAIN_URL_ENV, "https://tiles.example.test/dem/{z}/{x}/{y}.png")
    monkeypatch.delenv(sc.OVERPASS_URL_ENV, raising=False)
    assert sc.terrain_url() == "https://tiles.example.test/dem/{z}/{x}/{y}.png"
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("interpreter"):
            return httpx.Response(200, content=json.dumps({"elements": []}), headers={"content-type": "application/json"})
        return httpx.Response(200, content=terrarium_png(lambda x, y: 1.0), headers={"content-type": "image/png"})

    doc = sc.fetch_context(BOUNDARY, transport=httpx.MockTransport(handler))
    tile_urls = [u for u in seen if "/dem/" in u]
    assert tile_urls and all(u.startswith("https://tiles.example.test/dem/12/") for u in tile_urls)
    assert doc["terrain"]["missing_tiles"] == 0
    monkeypatch.delenv(sc.TERRAIN_URL_ENV)
    assert sc.terrain_url() == sc.TERRARIUM_URL



# ── gate fixes: budget, ceilings, edge cases ────────────────────────────────

def test_a_tile_started_late_is_clamped_to_the_remaining_budget(monkeypatch):
    """Overpass answers at 24 s of a 25 s budget: the first tile may take at most 1 s."""
    monkeypatch.delenv(sc.OVERPASS_URL_ENV, raising=False)
    monkeypatch.delenv(sc.TERRAIN_URL_ENV, raising=False)
    timeouts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            timeouts.append(request.extensions["timeout"]["read"])
            return httpx.Response(200, content=terrarium_png(lambda x, y: 1.0))
        return httpx.Response(200, content=json.dumps({"elements": []}))

    ticks = iter([0.0, 24.0])
    sc.fetch_context(BOUNDARY, transport=httpx.MockTransport(handler), budget_s=25.0, clock=lambda: next(ticks, 24.0))
    assert timeouts and all(t == pytest.approx(1.0) for t in timeouts)


def test_overpass_connect_plus_read_fits_the_budget():
    assert sc.OVERPASS_CONNECT_S + sc.OVERPASS_TIMEOUT_S <= sc.TOTAL_BUDGET_S


def test_a_district_sized_boundary_is_refused_before_any_request():
    seen = []
    transport = httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200, content=b"{}"))
    big = [[6.70, 53.40], [6.80, 53.40], [6.80, 53.45], [6.70, 53.45]]  # ~6.6 km × 5.6 km
    assert sc.bbox_area_km2(sc.padded_bbox(big)) > sc.MAX_BBOX_KM2
    with pytest.raises(sc.SiteContextTooLarge, match="smaller boundary"):
        sc.fetch_context(big, transport=transport)
    assert seen == []


def test_an_oversized_overpass_answer_is_cut_off(monkeypatch):
    monkeypatch.delenv(sc.OVERPASS_URL_ENV, raising=False)
    monkeypatch.setattr(sc, "MAX_OVERPASS_BYTES", 1000)

    def declared(request):
        return httpx.Response(200, headers={"content-length": "5000"}, content=b"x" * 5000)

    with pytest.raises(sc.SiteContextTooLarge, match="MB"):
        sc.fetch_context(BOUNDARY, transport=httpx.MockTransport(declared))

    def streamed(request):
        # No content-length: the cap is enforced while reading.
        return httpx.Response(200, content=iter([b"x" * 600, b"x" * 600]))

    with pytest.raises(sc.SiteContextTooLarge):
        sc.fetch_context(BOUNDARY, transport=httpx.MockTransport(streamed))


def test_features_are_capped_nearest_the_centre(monkeypatch):
    assert sc.elements_to_context(elements())["truncated"] is False
    monkeypatch.setattr(sc, "MAX_FEATURES", 2)
    ctx = sc.elements_to_context(elements())
    assert ctx["truncated"] is True
    assert len(ctx["buildings"]) == 2 and len(ctx["lines"]) == 2
    doc = sc.context_document((1, 2, 3, 4), ctx, None)
    assert doc["truncated"] is True


def test_padding_never_crosses_the_mercator_limit():
    bbox = sc.padded_bbox([[0.0, 85.04], [0.01, 85.04], [0.01, 85.049]])
    assert bbox[3] <= sc.MAX_MERCATOR_LAT
    assert sc.terrain_tiles_for(bbox)  # no math domain error


def test_terrain_grid_samples_any_tile_size():
    """A 512 px mirror: the ramp's east half must be reachable, not just the north-west quadrant."""
    bbox = sc.padded_bbox(BOUNDARY)
    keys = sc.terrain_tiles_for(bbox)
    big = [[float(x) for x in range(512)] for _y in range(512)]  # height = column index
    g = sc.terrain_grid({k: big for k in keys}, bbox, grid=8)
    x0, _ = sc.lnglat_to_tile(bbox[0], bbox[3], sc.TERRAIN_ZOOM)
    x1, _ = sc.lnglat_to_tile(bbox[2], bbox[3], sc.TERRAIN_ZOOM)
    assert g["heights_m"][0] == pytest.approx(int((x0 - int(x0)) * 512), abs=1)
    assert g["heights_m"][7] == pytest.approx(int((x1 - int(x1)) * 512), abs=1)
