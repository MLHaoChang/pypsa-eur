"""
Lengths follow geometry, by consent (plan M2).

`POST /api/network/lengths/from_geometry` rewrites the `length` of Lines AND
Links from the project's map geometry — the routed polyline in
`map_layout.json` when the branch has one, else the bus0→bus1 chord — under
the PyPSA lock, logs it, records the provenance (`route` | `chord`) beside
the route, and returns the same impedance-rescale previews a bus drag
offers. It is an explicit act, so it works whatever the project setting says
(the map's "Use geometry" badge calls it with one key).

The project setting "Derive lengths from geometry" decides what a BUS DRAG
does: off (the default) keeps today's behaviour — every touching Line's
length becomes the chord, Links are untouched; on, Lines and Links follow the
route when there is one, else the chord.
"""
from __future__ import annotations

import json

import pytest

from services import map_layout_service as ml
from services.network_geometry import _recompute_lengths_for_bus, route_length_km
from tests.test_worksheet_foreign_lock import _is_lock_refusal, same_org_other_user  # noqa: F401

URL = "/api/network/lengths/from_geometry"
A = (6.0, 53.0)
B = (7.0, 53.0)
BEND = [6.5, 53.5]                                   # [lng, lat], the document's order
ROUTE_KM = route_length_km([list(A), BEND, list(B)])
CHORD_KM = route_length_km([list(A), list(B)])


def _bus(client, name, xy):
    r = client.post("/api/network/buses", json={"name": name, "v_nom": 110.0, "x": xy[0], "y": xy[1]})
    assert r.status_code == 201, r.text


def _line(client, name, bus0, bus1, length=1.0, r=0.3, x=1.7, b=1e-5):
    resp = client.post("/api/network/lines", json={
        "name": name, "bus0": bus0, "bus1": bus1, "length": length, "r": r, "x": x, "b": b, "s_nom": 100.0,
    })
    assert resp.status_code == 201, resp.text


def _link(client, name, bus0, bus1, length=0.0):
    resp = client.post("/api/network/links", json={
        "name": name, "bus0": bus0, "bus1": bus1, "p_nom": 50.0, "carrier": "DC", "length": length,
    })
    assert resp.status_code == 201, resp.text


def _lengths(client) -> dict[str, float]:
    out = {f"line:{ln['name']}": ln["length"] for ln in client.get("/api/network/lines").json()}
    out.update({f"link:{k['name']}": k["length"] for k in client.get("/api/network/links").json()})
    return out


def _routes(client, name, routes: dict):
    doc = {"version": 1, "routes": routes, "bubbles": {}}
    r = client.put(f"/api/projects/{name}/map_layout", json=doc)
    assert r.status_code == 200, r.text


def _setting(client, name, on: bool):
    r = client.patch(f"/api/projects/{name}/settings", json={"derive_lengths_from_geometry": on})
    assert r.status_code == 200, r.text


def _doc(project_storage_dir, name) -> dict:
    return ml.read_map_layout(project_storage_dir(name))


def _new_log_entries(client, baseline_id):
    return [e for e in client.get("/api/changelog/").json() if e["id"] > baseline_id]


def _baseline(client) -> int:
    return max((e["id"] for e in client.get("/api/changelog/").json()), default=0)


@pytest.fixture
def routed_project(client, api_project):
    """A saved, active project: A—B, line L1 with one bend, link K1 without."""
    name = api_project("lengths-geo")
    _bus(client, "A", A)
    _bus(client, "B", B)
    _line(client, "L1", "A", "B")
    _link(client, "K1", "A", "B")
    _routes(client, name, {"line:L1": {"points": [BEND], "source": "user"}})
    return name


# ── the endpoint ────────────────────────────────────────────────────────────

def test_route_wins_over_chord_and_links_are_included(client, routed_project, project_storage_dir):
    r = client.post(URL, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == 2 and body["skipped"] == []
    assert body["sources"] == {"line:L1": "route", "link:K1": "chord"}
    got = _lengths(client)
    assert got["line:L1"] == pytest.approx(ROUTE_KM)
    assert got["link:K1"] == pytest.approx(CHORD_KM)
    assert ROUTE_KM > CHORD_KM          # the bend is why the two differ
    doc = _doc(project_storage_dir, routed_project)
    assert ml.length_source(doc, "line:L1") == "route"
    assert ml.length_source(doc, "link:K1") == "chord"
    assert doc["routes"]["line:L1"]["points"] == [BEND]      # the route itself is untouched


def test_keys_scope_the_rewrite(client, routed_project):
    r = client.post(URL, json={"keys": ["link:K1", "line:nope", "tr:T1", "bus:A", "junk"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == 1 and body["sources"] == {"link:K1": "chord"}
    assert {s["key"]: s["reason"] for s in body["skipped"]} == {
        "line:nope": "unknown",
        "tr:T1": "no-length",
        "bus:A": "no-length",
        "junk": "no-length",
    }
    got = _lengths(client)
    assert got["line:L1"] == 1.0                          # not named, not touched
    assert got["link:K1"] == pytest.approx(CHORD_KM)


def test_rescale_previews_are_offered_for_lines_only_and_nothing_is_applied(client, routed_project):
    before = {ln["name"]: ln for ln in client.get("/api/network/lines").json()}["L1"]
    r = client.post(URL, json={})
    (prev,) = r.json()["rescale"]
    assert prev["name"] == "L1" and prev["old_length"] == 1.0
    assert prev["new_length"] == pytest.approx(ROUTE_KM)
    ratio = prev["new_length"] / prev["old_length"]
    assert prev["new"]["r"] == pytest.approx(before["r"] * ratio)
    assert prev["skipped_reason"] is None
    # Preview only: r/x/b unchanged until the user consents.
    after = {ln["name"]: ln for ln in client.get("/api/network/lines").json()}["L1"]
    assert (after["r"], after["x"], after["b"]) == (before["r"], before["x"], before["b"])


def test_the_change_log_names_the_rewrite(client, routed_project):
    baseline = _baseline(client)
    client.post(URL, json={})
    entries = _new_log_entries(client, baseline)
    assert any(
        e["component_type"] == "Branches" and e["name"] == "(geometry)"
        and "2 branch length(s) from map geometry" in e["description"]
        and "1 route" in e["description"] and "1 chord" in e["description"]
        for e in entries
    ), entries


def test_an_unplaced_bus_is_skipped_not_measured_to_null_island(client, routed_project):
    r = client.post("/api/network/buses", json={"name": "NOWHERE", "v_nom": 110.0})
    assert r.status_code == 201
    _line(client, "L2", "A", "NOWHERE", length=42.0)
    body = client.post(URL, json={"keys": ["line:L2"]}).json()
    assert body["updated"] == 0 and body["skipped"] == [{"key": "line:L2", "reason": "unplaced"}]
    assert _lengths(client)["line:L2"] == 42.0


def test_a_scratch_network_has_no_geometry_to_read(client):
    """Routes live in the project's map_layout.json; an unsaved network has none."""
    _bus(client, "A", A)
    _bus(client, "B", B)
    _line(client, "L1", "A", "B")
    r = client.post(URL, json={})
    assert r.status_code == 409, r.text
    assert "save" in r.json()["detail"].lower()


def test_refused_under_a_foreign_lock(client, routed_project, same_org_other_user):  # noqa: F811
    assert client.post(f"/api/projects/{routed_project}/lock").status_code == 200
    b = same_org_other_user
    assert b.post(f"/api/projects/{routed_project}/activate").status_code == 200
    r = b.post(URL, json={})
    assert _is_lock_refusal(r), f"{r.status_code} {r.text[:200]}"
    assert _lengths(client)["line:L1"] == 1.0


def test_a_malformed_body_is_422(client, routed_project):
    assert client.post(URL, json={"keys": "line:L1"}).status_code == 422


# ── a bus drag, setting off / on ────────────────────────────────────────────

def _drag_b(client):
    r = client.put("/api/network/buses/B", json={"name": "B", "v_nom": 110.0, "x": 7.2, "y": 53.1})
    assert r.status_code == 200, r.text
    return r.json()


def test_bus_drag_with_the_setting_off_is_todays_behaviour(client, routed_project, project_storage_dir):
    """Lines get the chord (the route is ignored), Links are never touched."""
    body = _drag_b(client)
    got = _lengths(client)
    chord = route_length_km([list(A), [7.2, 53.1]])
    assert got["line:L1"] == pytest.approx(chord)
    assert got["link:K1"] == 0.0
    assert body["length_sources"] == {"line:L1": "chord"}
    assert ml.length_source(_doc(project_storage_dir, routed_project), "line:L1") == "chord"
    assert len(body["rescale"]) == 1 and body["rescale"][0]["name"] == "L1"


def test_bus_drag_with_the_setting_on_follows_the_route_and_covers_links(client, routed_project, project_storage_dir):
    _setting(client, routed_project, True)
    baseline = _baseline(client)
    body = _drag_b(client)
    got = _lengths(client)
    moved = [7.2, 53.1]
    assert got["line:L1"] == pytest.approx(route_length_km([list(A), BEND, moved]))
    assert got["link:K1"] == pytest.approx(route_length_km([list(A), moved]))
    assert body["length_sources"] == {"line:L1": "route", "link:K1": "chord"}
    doc = _doc(project_storage_dir, routed_project)
    assert ml.length_source(doc, "line:L1") == "route" and ml.length_source(doc, "link:K1") == "chord"
    # The rescale is still offered exactly as before (lines only: links carry no impedance).
    assert [p["name"] for p in body["rescale"]] == ["L1"]
    assert any(
        "Auto-rewrote 2 line/link length(s) from geometry" in e["description"]
        for e in _new_log_entries(client, baseline)
    )


def test_bus_drag_with_the_setting_on_and_no_route_is_the_chord(client, api_project):
    name = api_project("lengths-drag-chord")
    _bus(client, "A", A)
    _bus(client, "B", B)
    _line(client, "L1", "A", "B")
    _setting(client, name, True)
    body = _drag_b(client)
    assert body["length_sources"] == {"line:L1": "chord"}
    assert _lengths(client)["line:L1"] == pytest.approx(route_length_km([list(A), [7.2, 53.1]]))


# ── the pure recompute ──────────────────────────────────────────────────────

def _net():
    import pypsa

    n = pypsa.Network()
    n.add("Bus", "A", x=A[0], y=A[1])
    n.add("Bus", "B", x=B[0], y=B[1])
    n.add("Bus", "C", x=8.0, y=53.0)
    n.add("Line", "L1", bus0="A", bus1="B", length=1.0, r=0.3, x=1.7, s_nom=10.0)
    n.add("Line", "L2", bus0="B", bus1="C", length=1.0, r=0.0, x=0.0, b=0.0, s_nom=10.0)
    n.add("Link", "K1", bus0="A", bus1="B", p_nom=5.0)
    return n


def test_recompute_default_is_lines_by_chord():
    n = _net()
    res = _recompute_lengths_for_bus(n, "B")
    assert res.updated == 2 and res.sources == {"line:L1": "chord", "line:L2": "chord"}
    assert float(n.links.at["K1", "length"]) == 0.0
    assert [p["name"] for p in res.previews] == ["L1"]         # L2 is zero-impedance: no offer


def test_recompute_prefers_a_route_and_takes_links_when_asked():
    n = _net()
    res = _recompute_lengths_for_bus(n, "B", routes={"line:L1": [BEND], "link:K1": [BEND]}, include_links=True)
    assert res.updated == 3
    assert res.sources == {"line:L1": "route", "line:L2": "chord", "link:K1": "route"}
    assert float(n.lines.at["L1", "length"]) == pytest.approx(ROUTE_KM)
    assert float(n.links.at["K1", "length"]) == pytest.approx(ROUTE_KM)
    assert float(n.lines.at["L2", "length"]) == pytest.approx(route_length_km([list(B), [8.0, 53.0]]))
    assert [p["name"] for p in res.previews] == ["L1"]         # links carry no r/x/b


def test_recompute_ignores_a_route_for_another_kind():
    n = _net()
    res = _recompute_lengths_for_bus(n, "B", routes={"link:L1": [BEND]}, include_links=False)
    assert res.sources == {"line:L1": "chord", "line:L2": "chord"}


# ── the campus study reads derived lengths ──────────────────────────────────

def test_the_campus_draft_reads_the_derived_length(client, install_network, project_storage_dir, session_ctx):
    """
    End to end: a routed line in a saved hub project, the setting on, the
    lengths derived, the project saved — and the campus electrical draft's
    cable carries the derived length, not the typed 1 km.

    The derivation is called as the service, bound to the client's context
    the way the chat tools are, rather than over HTTP: every `/api/network/`
    write invalidates the solve's dispatch tables (an edited network is no
    longer solved), and the draft refuses an unsolved project. In the app
    the user re-solves between the two steps; here the point is that the
    length the service wrote is the one the study reads.
    """
    pytest.importorskip("gridspine")
    import numpy as np
    import pandas as pd
    import pypsa
    from gridspine.drivers.campus_study import draft_from_project

    from services.network_lengths import apply_lengths_from_geometry
    from services.pypsa_service import PyPSAService

    hours = 12
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-07-01", periods=hours, freq="h"))
    n.add("Bus", "grid", v_nom=110.0, x=A[0], y=A[1])
    n.add("Bus", "mv", v_nom=20.0, x=A[0] + 0.001, y=A[1])
    n.add("Bus", "mv2", v_nom=20.0, x=B[0], y=B[1])
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

    name = "Campus Lengths"
    install_network(n, name=name)
    assert client.post(f"/api/projects/{name}", params={"force": True, "rebind": True}).status_code == 200
    _routes(client, name, {"line:feeder": {"points": [BEND], "source": "import"}})
    _setting(client, name, True)
    ctx = session_ctx(client)
    token = PyPSAService.bind_request_context(ctx)
    try:
        body = apply_lengths_from_geometry(["line:feeder"])
    finally:
        PyPSAService.reset_request_context(token)
    assert body["sources"] == {"line:feeder": "route"}
    derived = float(ctx.network.lines.at["feeder", "length"])
    assert derived == pytest.approx(route_length_km([[A[0] + 0.001, A[1]], BEND, list(B)]))
    assert client.post(f"/api/projects/{name}", params={"force": True}).status_code == 200

    draft = draft_from_project(project_storage_dir(name) / "network.nc")
    cables = draft.spec["campus"]["cables"]
    (cable,) = [c for c in cables.values() if c["pypsa_name"] == "feeder"]
    assert cable["length_km"]["value"] == pytest.approx(derived, rel=1e-6)
    assert cable["length_km"]["value"] != 1.0
    # The sidecar carries the provenance the study could later cite.
    assert ml.length_source(_doc(project_storage_dir, name), "line:feeder") == "route"
    assert json.loads((project_storage_dir(name) / "metadata.json").read_text())["settings"] == {
        "derive_lengths_from_geometry": True,
    }
