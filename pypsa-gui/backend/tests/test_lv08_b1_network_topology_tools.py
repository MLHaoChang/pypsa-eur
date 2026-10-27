"""
Live-voice issue 08, slice B1 (PR 2): the typed tools over the network
topology routes (asset reference reads, geocoding, lengths from geometry,
impedance rescale, reset) and the project site document.

Pins GK B's plan conditions: P1 lock and undo parity derived from TOOL_ROUTES,
P3 the impedance rescale card, P4 the destructive reset unbinding the acting
session as the route does, P5 the upstream timeouts staying inside the
per-tool deadline, P6 a non-member caller getting the route's refusal.
"""
from __future__ import annotations

import functools

import pypsa
import pytest
from fastapi import HTTPException

from harness import budget, catalogue, edit_card
from services import chat_tools

TOOLS = {
    "get_asset_reference": "read",
    "geocode_place": "read",
    "derive_lengths_from_geometry": "write",
    "rescale_line_impedances": "write",
    "reset_network": "destructive",
    "get_project_sites": "read",
    "set_project_sites": "write",
    "fetch_site_context": "write",
}
NETWORK_EDITS = {"derive_lengths_from_geometry", "rescale_line_impedances", "reset_network"}


@pytest.fixture
def outsider(second_identity):
    """Act as a user of ANOTHER organization for the rest of the test."""
    chat_tools.set_acting_user(second_identity["user_id"])
    yield


@pytest.fixture
def grid(install_network):
    n = pypsa.Network()
    for bus in ("B1", "B2"):
        n.add("Bus", bus, v_nom=110)
    n.add("Line", "L1", bus0="B1", bus1="B2", r=0.1, x=12.1, b=0.0002, s_nom=300, length=10)
    install_network(n, name=None)
    return n


def _spy(monkeypatch, module, name: str, calls: list, result=None):
    """Replace a router handler with a recorder keeping its signature for `_route`."""
    original = getattr(module, name)

    @functools.wraps(original)
    def recorder(*args, **kwargs):
        calls.append((name, args))
        return {} if result is None else result

    monkeypatch.setattr(module, name, recorder)


# ── registry, lock and undo (P1) ────────────────────────────────────────────


def test_the_tools_are_registered_with_tiers_matching_their_routes():
    names = {t["name"] for t in catalogue.TOOLS}
    for name, tier in TOOLS.items():
        assert name in names and name in chat_tools.DISPATCHERS, name
        assert catalogue.safety_tier_for(name) == tier, name
        routes = catalogue.TOOL_ROUTES[name]
        assert routes and all(isinstance(r, tuple) for r in routes), name
        if tier == "read":
            assert all(m == "GET" for m, _ in routes), name
        else:
            assert all(m != "GET" for m, _ in routes), name


def test_the_network_edits_are_lock_gated_and_undo_captured_like_the_middleware():
    import main

    gated = chat_tools._lock_gated_tool_names()
    for name in TOOLS:
        ((method, path),) = catalogue.TOOL_ROUTES[name][:1]
        middleware_snapshots = (method != "GET" and path.startswith(main._UNDO_PREFIXES)
                                and path not in main._UNDO_EXCLUDE)
        assert (name in chat_tools.UNDO_CAPTURED_TOOLS) == middleware_snapshots, name
        assert (name in gated) == (name in NETWORK_EDITS), name
        assert catalogue.tool_edits_network(name) == (name in NETWORK_EDITS), name
    assert NETWORK_EDITS <= chat_tools.UNDO_CAPTURED_TOOLS


def test_the_view_enum_lists_exactly_the_routed_views():
    props = next(t for t in catalogue.TOOLS if t["name"] == "get_asset_reference")["input_schema"]["properties"]
    assert props["view"]["enum"] == list(chat_tools._ASSET_REFERENCE_VIEWS)
    assert len(catalogue.TOOL_ROUTES["get_asset_reference"]) == len(chat_tools._ASSET_REFERENCE_VIEWS)


# ── asset reference ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("view, handler, args", [
    ("asset_costs", "get_asset_costs", ()),
    ("asset_schema", "get_asset_schema", ("Generator",)),
    ("attribute_catalog", "get_attribute_catalog", ("Generator",)),
])
def test_each_view_reaches_its_route(monkeypatch, view, handler, args):
    from routers import network as R

    calls: list = []
    _spy(monkeypatch, R, handler, calls, {"view": view})
    assert chat_tools.get_asset_reference(view, component="Generator") == {"view": view}
    assert calls == [(handler, args)]


def test_the_real_asset_reads_and_their_refusals(grid):
    assert "assets" in chat_tools.get_asset_reference("asset_costs")
    assert chat_tools.get_asset_reference("attribute_catalog", "Line")["component"] == "Line"
    assert chat_tools.get_asset_reference("asset_schema", "Line")
    for call in (lambda: chat_tools.get_asset_reference("everything"),
                 lambda: chat_tools.get_asset_reference("asset_schema")):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.detail["error_kind"] == "invalid_tool_args"
    with pytest.raises(HTTPException) as exc:
        chat_tools.get_asset_reference("attribute_catalog", "GlobalConstraint")
    assert exc.value.status_code == 400


# ── geocoding and site context: upstream timeouts (P5) ──────────────────────


def test_the_upstream_timeouts_stay_inside_the_tool_deadline():
    from services import geocode, site_context

    assert geocode.TIMEOUT_S < budget.PER_TOOL_TIMEOUT_SECONDS
    assert site_context.TOTAL_BUDGET_S < budget.PER_TOOL_TIMEOUT_SECONDS


def test_geocode_passes_the_query_and_the_routes_refusals(monkeypatch):
    from services import geocode

    seen: list[str] = []
    monkeypatch.setattr(geocode, "search", lambda q: seen.append(q) or [{"lat": 1.0, "lon": 2.0}])
    assert chat_tools.geocode_place("  Berlin  ") == [{"lat": 1.0, "lon": 2.0}]
    assert seen == ["Berlin"]

    def unset(q):
        raise geocode.GeocoderUnset("No geocoder is configured.")
    monkeypatch.setattr(geocode, "search", unset)
    with pytest.raises(HTTPException) as exc:
        chat_tools.geocode_place("Berlin")
    assert exc.value.status_code == 503
    with pytest.raises(HTTPException) as exc:
        chat_tools.geocode_place("x" * (geocode.MAX_QUERY_CHARS + 1))
    assert exc.value.detail["error_kind"] == "invalid_tool_args"


# ── lengths and impedances (P3) ─────────────────────────────────────────────


def test_lengths_reach_their_route_with_the_keys(monkeypatch):
    from routers import network as R

    calls: list = []
    _spy(monkeypatch, R, "lengths_from_geometry", calls, {"updated": 0})
    chat_tools.derive_lengths_from_geometry(["line:L1"])
    assert calls[0][1][0].keys == ["line:L1"]


def test_lengths_on_a_scratch_network_are_refused_as_the_route_does(grid):
    with pytest.raises(HTTPException) as exc:
        chat_tools.derive_lengths_from_geometry()
    assert exc.value.status_code == 409


def test_the_rescale_card_shows_each_lines_old_and_new_values(grid):
    args = {"lines": [{"name": "L1", "r": 0.2, "x": 24.2, "b": 0.0004}]}
    card = edit_card.preview("rescale_line_impedances", args, with_state=False)
    rows = {r["field"]: (r["old"], r["new"]) for r in card["rows"] if r["name"] == "L1"}
    assert rows["r"] == (pytest.approx(0.1), pytest.approx(0.2))
    assert rows["x"] == (pytest.approx(12.1), pytest.approx(24.2))
    assert rows["b"] == (pytest.approx(0.0002), pytest.approx(0.0004))
    assert grid.lines.at["L1", "r"] == pytest.approx(0.1)  # the preview changed nothing


def test_the_rescale_writes_exactly_the_values_sent_and_never_defaults(grid):
    from services.pypsa_service import PyPSAService

    out = chat_tools.rescale_line_impedances([{"name": "L1", "r": 0.2, "x": 24.2, "b": 0.0004}])
    assert out == {"updated": 1, "skipped": []}
    line = PyPSAService.get_network().lines.loc["L1"]
    assert (line.r, line.x, line.b) == (pytest.approx(0.2), pytest.approx(24.2), pytest.approx(0.0004))
    with pytest.raises(HTTPException) as exc:  # ImpedanceRescaleRequest: no defaulted value
        chat_tools.rescale_line_impedances([{"name": "L1", "r": 0.3}])
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        edit_card.preview("rescale_line_impedances", {"lines": [{"name": "L1", "r": 0.3}]},
                          with_state=False)
    assert exc.value.detail["error_kind"] == "invalid_edit_value"


# ── reset (P4) ──────────────────────────────────────────────────────────────


def test_a_chat_reset_unbinds_the_acting_session_as_the_route_does(client, api_project, _auth_db):
    from services.auth_service import resolve_session_row
    from settings import get_settings

    _engine, session_local = _auth_db
    name = api_project("b1-reset")
    client.post(f"/api/projects/{name}/activate")
    raw = client.cookies.get(get_settings().session_cookie_name)

    def pointer():
        with session_local() as db:
            return resolve_session_row(db, raw).active_project_id

    assert pointer() is not None
    with session_local() as db:
        session_id = resolve_session_row(db, raw).id
    chat_tools.set_acting_session(session_id)
    try:
        assert chat_tools.reset_network() == {"status": "reset"}
    finally:
        chat_tools.set_acting_session(None)
    assert pointer() is None


def test_reset_needs_an_acting_user():
    chat_tools.set_acting_user(None)
    with pytest.raises(HTTPException) as exc:
        chat_tools.reset_network()
    assert exc.value.status_code == 401


# ── project sites ───────────────────────────────────────────────────────────


def test_the_site_document_round_trips_and_a_missing_context_is_404(client, api_project):
    name = api_project("b1-sites")
    doc = chat_tools.get_project_sites(name)["sites"]
    assert chat_tools.set_project_sites(name, doc) is not None
    assert chat_tools.get_project_sites(name)["sites"] == doc
    with pytest.raises(HTTPException) as exc:
        chat_tools.get_project_sites(name, site_id="nowhere")
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        chat_tools.set_project_sites(name, {"sites": "not a list"})
    assert exc.value.status_code == 422


def test_fetch_site_context_reaches_its_route(client, api_project, monkeypatch):
    from routers import projects as R

    name = api_project("b1-ctx")
    calls: list = []
    _spy(monkeypatch, R, "fetch_site_context", calls, {"buildings": []})
    assert chat_tools.fetch_site_context(name, "s1") == {"buildings": []}
    assert calls == [("fetch_site_context", (name, "s1"))]


@pytest.mark.parametrize("call", [
    lambda p: chat_tools.get_project_sites(p),
    lambda p: chat_tools.set_project_sites(p, {}),
    lambda p: chat_tools.fetch_site_context(p, "s1"),
], ids=["get_project_sites", "set_project_sites", "fetch_site_context"])
def test_a_non_member_gets_the_routes_refusal(client, api_project, call, request):
    name = api_project("b1-sites-acl")
    request.getfixturevalue("outsider")
    with pytest.raises(HTTPException) as exc:
        call(name)
    assert exc.value.status_code in (403, 404)
