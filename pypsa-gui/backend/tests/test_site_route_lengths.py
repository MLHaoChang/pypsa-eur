"""
The 3D site's distances reach the campus study (visual-layers plan 3, S3).

The site view measures a branch between its connection anchor and the far
bus's yard and — with the project's "Derive lengths from geometry" on
(plan M2) — writes a straight two-point route with source `site` into
`map_layout.json`, then asks `POST /api/network/lengths/from_geometry` for
the branch. The write is frontend-driven (`pages/SiteCanvas.tsx`,
`site3d/siteDistances.ts`); this test exercises the backend half end to
end, mirroring M2's `test_the_campus_draft_reads_the_derived_length`: a
tiny network with two member buses, a site document with placements for
the feeder bay and the two yards, the setting on; the site route PUT
through the map_layout route; the derivation; a save; and the campus
electrical draft's cable carrying the derived length.
"""
from __future__ import annotations

import math

import pytest

from services import map_layout_service as ml
from services.network_geometry import route_length_km

# The site frame (site3d/geo.ts): a tangent plane at the site origin,
# metres east/north; the inverse of `toLocal` is what the site writes.
_EARTH_RADIUS_M = 6378137.0
_DEG = math.pi / 180


def _from_local(origin: tuple[float, float], x_m: float, y_m: float) -> list[float]:
    """`[lng, lat]` of a point `x_m` east, `y_m` north of `origin = (lng, lat)`."""
    per_lat = _EARTH_RADIUS_M * _DEG
    per_lng = per_lat * math.cos(origin[1] * _DEG)
    return [origin[0] + x_m / per_lng, origin[1] + y_m / per_lat]


def test_a_site_route_with_the_setting_on_reaches_the_campus_draft(client, install_network, project_storage_dir, session_ctx):
    pytest.importorskip("gridspine")
    import numpy as np
    import pandas as pd
    import pypsa
    from gridspine.drivers.campus_study import draft_from_project

    from services.network_lengths import apply_lengths_from_geometry
    from services.pypsa_service import PyPSAService

    # Two member buses 200 m apart on the site, the grid bus off-site.
    origin = (6.835, 53.435)
    mv_xy, mv2_xy = _from_local(origin, 0.0, 0.0), _from_local(origin, 200.0, 0.0)
    hours = 12
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-07-01", periods=hours, freq="h"))
    n.add("Bus", "grid", v_nom=110.0, x=6.80, y=53.40)
    n.add("Bus", "mv", v_nom=20.0, x=mv_xy[0], y=mv_xy[1])
    n.add("Bus", "mv2", v_nom=20.0, x=mv2_xy[0], y=mv2_xy[1])
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=500.0)
    n.add("Link", "grid_import", bus0="grid", bus1="mv", p_nom=60.0, efficiency=0.99)
    n.add("Line", "feeder", bus0="mv", bus1="mv2", length=1.0, r=0.2, x=0.1, b=0.0, s_nom=10.0)
    n.add("Load", "dc_load", bus="mv2", p_set=40.0)
    n.buses["eh_poc"] = [True, False, False]
    n.buses["eh_sk_mva"] = [2000.0, float("nan"), float("nan")]
    load = np.full(hours, 40.0)
    n.generators_t.p = pd.DataFrame({"grid_supply": load}, index=n.snapshots)
    n.loads_t.p = pd.DataFrame({"dc_load": load}, index=n.snapshots)
    n.links_t.p0 = pd.DataFrame({"grid_import": load / 0.99}, index=n.snapshots)
    n.links_t.p1 = -0.99 * n.links_t.p0

    name = "Campus Site Lengths"
    install_network(n, name=name)
    assert client.post(f"/api/projects/{name}", params={"force": True, "rebind": True}).status_code == 200

    # The site: both yards placed at their buses, the feeder bay in the mv
    # yard turned towards mv2 (heading 90), its connection anchor 6 m on.
    site = {
        "version": 1,
        "sites": [{
            "id": "campus",
            "name": "Campus",
            "buses": ["mv", "mv2"],
            "boundary": [_from_local(origin, -100, -100), _from_local(origin, 300, -100), _from_local(origin, 300, 100), _from_local(origin, -100, 100)],
            "origin": {"lng": origin[0], "lat": origin[1]},
            "placements": {
                "Bus:mv": {"x": 0.0, "y": 0.0, "heading": 0},
                "Bus:mv2": {"x": 200.0, "y": 0.0, "heading": 0},
                "Line:feeder": {"x": 30.0, "y": 0.0, "heading": 90},
            },
        }],
    }
    assert client.put(f"/api/projects/{name}/sites", json=site).status_code == 200, "site document"
    assert client.patch(f"/api/projects/{name}/settings", json={"derive_lengths_from_geometry": True}).status_code == 200

    # What the site writes for the feeder (siteDistances.siteRoutePoints):
    # its connection anchor and the far yard, source `site`.
    anchor, far = _from_local(origin, 36.0, 0.0), _from_local(origin, 200.0, 0.0)
    doc = {"version": 1, "routes": {"line:feeder": {"points": [anchor, far], "source": "site"}}, "bubbles": {}}
    r = client.put(f"/api/projects/{name}/map_layout", json=doc)
    assert r.status_code == 200, r.text
    assert ml.read_map_layout(project_storage_dir(name))["routes"]["line:feeder"]["source"] == "site"

    ctx = session_ctx(client)
    token = PyPSAService.bind_request_context(ctx)
    try:
        body = apply_lengths_from_geometry(["line:feeder"])
    finally:
        PyPSAService.reset_request_context(token)
    assert body["sources"] == {"line:feeder": "route"}
    derived = float(ctx.network.lines.at["feeder", "length"])
    # bus0 → anchor → far yard → bus1: the far leg is zero because the yard stands at its bus.
    assert derived == pytest.approx(route_length_km([mv_xy, anchor, far, mv2_xy]), rel=1e-9)
    assert derived == pytest.approx(0.2, rel=0.02)          # 36 m + 164 m on the tangent plane
    assert derived != 1.0
    assert client.post(f"/api/projects/{name}", params={"force": True}).status_code == 200

    draft = draft_from_project(project_storage_dir(name) / "network.nc")
    cables = draft.spec["campus"]["cables"]
    (cable,) = [c for c in cables.values() if c["pypsa_name"] == "feeder"]
    assert cable["length_km"]["value"] == pytest.approx(derived, rel=1e-6)
    assert cable["length_km"]["value"] != 1.0
    # The provenance beside the route says the length is the route's; the route is still the site's.
    sidecar = ml.read_map_layout(project_storage_dir(name))
    assert ml.length_source(sidecar, "line:feeder") == "route"
    assert sidecar["routes"]["line:feeder"] == {"points": [anchor, far], "source": "site"}


def test_a_site_route_is_measured_like_any_other_by_the_endpoint(client, api_project):
    """`source: site` changes nothing for the derivation: a route is a route."""
    name = api_project("site-route")
    for bus, xy in (("A", (6.0, 53.0)), ("B", (7.0, 53.0))):
        assert client.post("/api/network/buses", json={"name": bus, "v_nom": 110.0, "x": xy[0], "y": xy[1]}).status_code == 201
    assert client.post("/api/network/lines", json={
        "name": "L1", "bus0": "A", "bus1": "B", "length": 1.0, "r": 0.3, "x": 1.7, "b": 1e-5, "s_nom": 100.0,
    }).status_code == 201
    bend = [6.5, 53.5]
    doc = {"version": 1, "routes": {"line:L1": {"points": [bend], "source": "site"}}, "bubbles": {}}
    assert client.put(f"/api/projects/{name}/map_layout", json=doc).status_code == 200
    body = client.post("/api/network/lengths/from_geometry", json={"keys": ["line:L1"]}).json()
    assert body["sources"] == {"line:L1": "route"}
    (line,) = client.get("/api/network/lines").json()
    assert line["length"] == pytest.approx(route_length_km([[6.0, 53.0], bend, [7.0, 53.0]]))
