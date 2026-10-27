"""
Live-voice issue 08 slice B2b: the GUI routes the assistant can now reach.

Ten new tools each call their GUI route's own router handler in-process
(`catalogue.TOOL_ROUTES`), and seven existing tools are re-pointed from
`_service_call_` to the route they already shared a helper with. Pinned here:
schema and tier, dispatch through the real handler, the route's errors passing
through, every read under the result cap (F6), the map-geometry import's lock
and preview contract and its upload ownership (F4), and the Library reference
preview writing nothing (F5).
"""
from __future__ import annotations

import json
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from harness import catalogue
from services import chat_tools as T
from services import upload_service
from services.pypsa_service import PyPSAService

ROOT = Path(__file__).resolve().parents[3]
CAP = 4000

NEW_TOOLS = {
    "get_osm_suggestions": ("read", ("GET", "/api/projects/{name}/map_layout/osm_suggestions")),
    "preview_library_reference": ("read", ("POST", "/api/library_reference/preview")),
    "import_map_geometry": ("write", ("POST", "/api/projects/{name}/map_layout/import")),
    "list_result_assets": ("read", ("GET", "/api/results/asset/assets")),
    "get_curtailment_by_cause": ("read", ("GET", "/api/results/curtailment/by_cause")),
    "get_eh_readiness": ("read", ("GET", "/api/results/eh_readiness")),
    "get_report_capabilities": ("read", ("GET", "/api/projects/{name}/reports/capabilities")),
    "get_report_evidence_hash": ("read", ("GET", "/api/projects/{name}/reports/evidence_hash")),
    "get_report_version": ("read", ("GET", "/api/projects/{name}/reports/{report_id}/versions/{version}")),
    "create_evidence_report": ("write", ("POST", "/api/projects/{name}/reports")),
}

REPOINTED = {
    "get_energy_flows": ("GET", "/api/results/energy_flows"),
    "get_load_shape": ("GET", "/api/results/load_shape"),
    "get_loadflow_summary": ("GET", "/api/results/loadflow-view"),
    "get_run_evidence": ("GET", "/api/results/run-view"),
    "preview_document_values": ("POST", "/api/document_values/preview"),
    "preview_equipment_mapping": ("POST", "/api/equipment_table/preview"),
    "preview_commercial_document_values": ("POST", "/api/document_values/commercial/preview"),
}


def _size(payload) -> int:
    return len(json.dumps(payload, default=str))


def _parity_rows() -> dict:
    matrix = json.loads((ROOT / ".scratch/live-voice/route-parity.json").read_text())
    return {(r["method"], r["path"]): r for r in matrix["rows"]}


# ── registration ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(NEW_TOOLS))
def test_new_tool_is_declared_dispatched_routed_and_tiered(name):
    tier, route = NEW_TOOLS[name]
    tool = next(t for t in catalogue.TOOLS if t["name"] == name)
    assert tool["input_schema"]["type"] == "object"
    assert catalogue.safety_tier_for(name) == tier
    assert catalogue.TOOL_ROUTES[name] == [route]
    assert callable(T.DISPATCHERS[name])
    row = _parity_rows()[route]
    assert row["class"] == "tool" and name in row["tools"] and row["via"] == "tool_routes"


@pytest.mark.parametrize("name", sorted(REPOINTED))
def test_repointed_tool_maps_to_its_gui_route(name):
    route = REPOINTED[name]
    assert catalogue.TOOL_ROUTES[name] == [route]
    assert catalogue.safety_tier_for(name) == "read"
    row = _parity_rows()[route]
    assert row["class"] == "tool" and name in row["tools"] and row["via"] == "tool_routes"


def test_import_folder_is_an_app_shell_limitation():
    row = _parity_rows()[("POST", "/api/projects/import-folder")]
    assert row["class"] == "limitation" and row["reason"] == "app-shell"


def test_write_tools_are_classified_for_the_edit_card():
    from harness import edit_card

    assert edit_card.is_edit("import_map_geometry")
    assert "create_evidence_report" in edit_card.NOT_EDITS


def test_b2b_fit_trims_lists_and_says_so():
    big = {"a": list(range(2000)), "nested": {"b": ["x" * 40] * 200}, "keep": 1}
    out = T._b2b_fit(big)
    assert _size(out) < CAP and out["keep"] == 1
    assert out["truncated"]["a"]["total"] == 2000 and out["truncated"]["a"]["shown"] == len(out["a"])
    assert out["a"] == list(range(len(out["a"])))
    assert len(big["a"]) == 2000  # the input is not mutated
    assert T._b2b_fit({"a": [1, 2]}) == {"a": [1, 2]}


# ── OSM suggestions ─────────────────────────────────────────────────────────

@pytest.fixture
def osm_stub(monkeypatch):
    from services import osm_suggestions as osm
    from tests.test_osm_suggestions import elements

    osm.clear_cache()
    calls = []

    def fake(bbox, **kw):
        calls.append(bbox)
        return osm.suggestions_document(bbox, osm.elements_to_suggestions(elements()))

    monkeypatch.setattr(osm, "fetch_suggestions", fake)
    yield calls
    osm.clear_cache()


def test_osm_suggestions_through_the_route(client, api_project, osm_stub):
    name = api_project("b2b-osm")
    out = T.DISPATCHERS["get_osm_suggestions"](name=name, bbox="6.82,53.43,6.84,53.45")
    assert osm_stub == [(6.82, 53.43, 6.84, 53.45)]
    assert out["lines_total"] == 1 and out["areas_total"] == 1 and out["total_count"] == 2
    assert [r["kind"] for r in out["items"]] == ["line", "substation"]
    assert all(r["n_points"] >= 2 and "points" not in r for r in out["items"])
    assert out["attribution"].startswith("© OpenStreetMap")


def test_osm_suggestions_errors_pass_through(client, api_project, osm_stub):
    name = api_project("b2b-osm-err")
    with pytest.raises(HTTPException) as bad:
        T.get_osm_suggestions(name, "0,0,10,10")
    assert bad.value.status_code == 422
    with pytest.raises(HTTPException) as missing:
        T.get_osm_suggestions("does-not-exist", "6.82,53.43,6.84,53.45")
    assert missing.value.status_code == 404


def test_osm_suggestions_stay_under_the_cap_on_a_large_box(client, api_project, monkeypatch):
    from services import osm_suggestions as osm

    line = {"id": 1, "kind": "line", "points": [[6.8, 53.4]] * 50, "tags": {"name": "L" * 30, "voltage": "380000"}}
    doc = osm.suggestions_document((6.82, 53.43, 6.84, 53.45),
                                   {"lines": [dict(line, id=i) for i in range(500)], "areas": []})
    monkeypatch.setattr(osm, "fetch_suggestions", lambda bbox, **kw: doc)
    name = api_project("b2b-osm-big")
    out = T.get_osm_suggestions(name, "6.82,53.43,6.84,53.45")
    assert _size(out) < CAP and out["total_count"] == 500 and out["has_more"]
    page2 = T.get_osm_suggestions(name, "6.82,53.43,6.84,53.45", offset=out["returned"], limit=5)
    assert page2["items"][0]["osm_id"] == out["returned"]


# ── Library reference preview (F5) ──────────────────────────────────────────

def test_library_reference_preview_through_the_route_and_writes_nothing(client, install_network, session_ctx,
                                                                        monkeypatch):
    from services import change_log_service, undo_service
    from tests.test_library_reference_apply import AEC, _net

    log_before = change_log_service.get_all()
    monkeypatch.setattr(undo_service, "claim_push_slot", lambda *a, **k: True)
    install_network(_net())
    depth = client.get("/api/network/undo/info").json()["depth"]
    ctx = session_ctx(client)
    n, revision = ctx.network, ctx.network_revision
    before = n.links.copy(deep=True), n.generators.copy(deep=True), dict(n.meta)
    token = PyPSAService.bind_request_context(ctx)
    try:
        out = T.DISPATCHERS["preview_library_reference"](**AEC)
        assert out["missing"] == [] and out["card"]["tool"] == "apply_library_reference"
        assert out["card"]["digest"] and _size(out) < CAP
        pv = {"component_class": "Generator", "name": "pv", "technology": "solar-utility", "projection_year": 2030}
        needs = T.preview_library_reference(**pv)
        assert needs["card"] is None and needs["missing"] == ["dc_ac"]
    finally:
        PyPSAService.reset_request_context(token)
    live = session_ctx(client).network
    assert session_ctx(client).network_revision == revision
    assert live.links.equals(before[0]) and live.generators.equals(before[1]) and dict(live.meta) == before[2]
    assert client.get("/api/network/undo/info").json()["depth"] == depth
    assert change_log_service.get_all() == log_before


def test_library_reference_preview_refusal_passes_through(install_network):
    from tests.test_library_reference_apply import _net

    install_network(_net())
    with pytest.raises(HTTPException) as exc:
        T.preview_library_reference("Generator", "nope", "onwind", 2030, ["money_year"])
    assert exc.value.status_code == 404


# ── map geometry import (F4) ────────────────────────────────────────────────

@pytest.fixture
def geo(client, session_ctx, project_storage_dir, api_project):
    """`test_map_geometry_import`'s two-bus project, open in the tools' session, with the file uploaded."""
    from tests.test_map_geometry_import import A, B, PAYLOAD, _bus

    name = api_project("b2b-geo-import")
    _bus(client, "A", A)
    _bus(client, "B", B)
    assert client.post("/api/network/lines", json={"name": "L1", "bus0": "A", "bus1": "B", "length": 1.0,
                                                    "r": 0.3, "x": 1.7, "b": 1e-5, "s_nom": 100.0}).status_code == 201
    assert client.post("/api/network/links", json={"name": "K1", "bus0": "A", "bus1": "B", "p_nom": 50.0,
                                                    "carrier": "DC", "length": 0.0}).status_code == 201
    token = PyPSAService.bind_request_context(session_ctx(client))
    try:
        meta = upload_service.add_upload(name, json.dumps(PAYLOAD).encode(), "routes.geojson", "text/plain")
        yield name, meta.file_id, project_storage_dir(name)
    finally:
        PyPSAService.reset_request_context(token)


def test_import_preview_returns_the_report_and_writes_nothing(geo, monkeypatch):
    from services import change_log_service
    from services import map_layout_service as ml
    from services import site_service as ss

    name, file_id, pdir = geo
    log_before = change_log_service.get_all()
    lengths = PyPSAService.get_network().lines["length"].copy()
    out = T.DISPATCHERS["import_map_geometry"](name=name, file_id=file_id)
    assert out["applied"] is False and "written" not in out
    assert [(r["key"], r["how"]) for r in out["routes"]] == [("line:L1", "name"), ("link:K1", "endpoints")]
    assert out["sites"][0]["buses"] == ["A"] and out["sites_total"] == 1 and out["unmatched"] == []
    assert all("points" not in r for r in out["routes"]) and "boundary" not in out["sites"][0]
    assert not (pdir / ml.MAP_LAYOUT_FILE).exists() and not (pdir / ss.SITES_FILE).exists()
    assert change_log_service.get_all() == log_before
    assert PyPSAService.get_network().lines["length"].equals(lengths)


def test_import_apply_writes_through_the_route(geo):
    from services import map_layout_service as ml

    name, file_id, pdir = geo
    out = T.import_map_geometry(name, file_id, apply=True)
    assert out["applied"] is True and out["written"]["routes"] == ["line:L1", "link:K1"]
    assert ml.read_map_layout(pdir)["routes"]["line:L1"]["source"] == "import"


def test_import_apply_is_refused_under_a_foreign_lock(client, geo, _auth_db, seeded_identity):
    from db.models import OrgMembership, User
    from services import map_layout_service as ml
    from services import site_service as ss
    from services.auth_service import hash_password

    name, file_id, pdir = geo
    assert client.post(f"/api/projects/{name}/lock").status_code == 200   # the seeded user holds it
    _engine, session_local = _auth_db
    with session_local() as db:
        other = User(id=_uuid.uuid4(), email=f"b2b-{_uuid.uuid4().hex[:6]}@example.com",
                     password_hash=hash_password("irrelevant"), status="active",
                     is_super_admin=False, created_at=datetime.now(tz=timezone.utc))
        db.add(other)
        db.flush()
        db.add(OrgMembership(id=_uuid.uuid4(), user_id=other.id, org_id=seeded_identity["org_id"], role="admin"))
        db.commit()
        other_id = str(other.id)
    T.set_acting_user(other_id)
    try:
        with pytest.raises(HTTPException) as exc:
            T.import_map_geometry(name, file_id, apply=True)
        assert exc.value.status_code == 409 and exc.value.detail["error_kind"] == "project_locked"
        with pytest.raises(HTTPException) as report:
            T.create_evidence_report()
        assert report.value.status_code == 409 and report.value.detail["error_kind"] == "project_locked"
    finally:
        T.set_acting_user(str(seeded_identity["user_id"]))
    assert not (pdir / ml.MAP_LAYOUT_FILE).exists() and not (pdir / ss.SITES_FILE).exists()


def test_import_refuses_an_unknown_or_foreign_file_id(geo, tmp_path):
    name, _file_id, pdir = geo
    # An upload of ANOTHER project (its own directory) is not readable as the active project's.
    foreign_dir = tmp_path / "b2b-foreign"
    foreign_dir.mkdir()
    foreign = upload_service.add_upload("b2b-foreign", b'{"type": "FeatureCollection", "features": []}',
                                        "other.geojson", "text/plain", project_dir=foreign_dir)
    assert not (pdir / "uploads" / foreign.file_id).exists()
    for bad in (foreign.file_id, "0" * 64):
        with pytest.raises(HTTPException) as exc:
            T.import_map_geometry(name, bad)
        assert exc.value.status_code == 404, exc.value.detail
        assert exc.value.detail["error_kind"] == "upload_not_found"


def test_import_route_validation_runs_unchanged(geo, monkeypatch):
    from services import map_geometry_import as mgi

    name, file_id, _pdir = geo
    with pytest.raises(HTTPException) as tol:
        T.import_map_geometry(name, file_id, tolerance_m=0)
    assert tol.value.status_code == 422
    monkeypatch.setattr(mgi, "MAX_IMPORT_BYTES", 64)
    with pytest.raises(HTTPException) as cap:
        T.import_map_geometry(name, file_id)
    assert cap.value.status_code == 413
    bad = upload_service.add_upload(name, b"{not json", "bad.geojson", "text/plain")
    monkeypatch.undo()
    with pytest.raises(HTTPException) as malformed:
        T.import_map_geometry(name, bad.file_id)
    assert malformed.value.status_code == 422


def test_import_report_stays_under_the_cap(geo):
    from tests.test_map_geometry_import import A_NEAR, B_NEAR, fc, line

    name, _file_id, _pdir = geo
    many = fc(*[line([A_NEAR, [6.5, 53.0 + i / 1000], B_NEAR], name=f"stray-{i}") for i in range(300)])
    meta = upload_service.add_upload(name, json.dumps(many).encode(), "many.geojson", "text/plain")
    out = T.import_map_geometry(name, meta.file_id)
    assert _size(out) < CAP
    assert out["routes_total"] + out["unmatched_total"] == 300 and "truncated" in out


# ── results reads ───────────────────────────────────────────────────────────

def test_list_result_assets_pages_the_route(install_network):
    from tests.oj_grid import build_oj_grid

    n = install_network(build_oj_grid())
    out = T.DISPATCHERS["list_result_assets"]()
    assert {"items", "total_count", "offset", "returned", "has_more"} <= set(out)
    names = {(r["class"], r["name"]) for r in out["items"]}
    assert ("Generator", n.generators.index[0]) in names
    assert set(out["items"][0]) == {"class", "name", "carrier", "bus"}


def test_list_result_assets_stays_under_the_cap(install_network):
    import pypsa

    n = pypsa.Network()
    n.add("Bus", "b")
    for i in range(600):
        n.add("Generator", f"generator-with-a-long-name-{i:04d}", bus="b", carrier="solar")
    install_network(n)
    out = T.list_result_assets()
    assert _size(out) < CAP and out["total_count"] == 601 and out["has_more"]  # the bus too


def _curtailment_network():
    from tests.test_curtailment_by_cause import _network

    return _network()


def test_curtailment_by_cause_through_the_route(install_network, monkeypatch):
    from types import SimpleNamespace

    import routers.results as R
    from tests.test_curtailment_by_cause import COMMERCIAL

    install_network(_curtailment_network())
    monkeypatch.setattr(R, "_dispatch_ready", lambda n: True)
    monkeypatch.setattr(R, "_state", {"solver_config": SimpleNamespace(commercial=COMMERCIAL)})
    out = T.DISPATCHERS["get_curtailment_by_cause"]()
    assert out["status"] == "ok" and out["export_link"] == "grid_export" and out["total_count"] == 1
    pv = out["items"][0]
    assert pv["plant"] == "pv" and pv["by_cause_mwh"] == {"export_limit": 2.0, "negative_price": 7.0,
                                                          "both": 2.0, "other": 4.0}


def test_curtailment_by_cause_unsolved_is_no_data(install_network):
    import pypsa

    n = pypsa.Network()
    n.add("Bus", "b")
    install_network(n)
    out = T.get_curtailment_by_cause()
    assert out["status"] == "no_data" and out["kind"] == "curtailment_by_cause"


def test_curtailment_by_cause_stays_under_the_cap(install_network, monkeypatch):
    import routers.results as R
    from services.results import curtailment as C

    install_network(_curtailment_network())
    monkeypatch.setattr(R, "_dispatch_ready", lambda n: True)
    plants = {f"plant-{i:04d}": {"total_mwh": 1.0, "available_mwh": 2.0,
                                 "by_cause_mwh": {"export_limit": 1.0, "negative_price": 0.0,
                                                  "both": 0.0, "other": 0.0}} for i in range(400)}
    monkeypatch.setattr(C, "curtailment_by_cause", lambda n, c: {"status": "ok", "plants": plants})
    out = T.get_curtailment_by_cause()
    assert _size(out) < CAP and out["total_count"] == 400 and out["has_more"]


def test_eh_readiness_through_the_route(install_network):
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    install_network(_feeder_hub())
    out = T.DISPATCHERS["get_eh_readiness"](archetype="weak_flexible", dtc_attribution="per_load")
    assert out["archetype"] == "weak_flexible" and out["dtc"]["attribution"] == "per_load"
    assert _size(out) < CAP
    base = T.get_eh_readiness("strong_grid")
    over = T.get_eh_readiness("strong_grid", pack_overrides={"target_lole_h": 3.0,
                                                             "certification_metric": "mc_lole"})
    assert over["pack_hash"] != base["pack_hash"]
    staged = T.get_eh_readiness("strong_grid", stages=["apply_pack", "ens_solve"])
    assert staged["stages"] != base["stages"]


@pytest.mark.parametrize("kwargs", [
    {"archetype": "nope"},
    {"archetype": "strong_grid", "budget_solves": 0},
    {"archetype": "strong_grid", "dtc_attribution": "auto"},
    {"archetype": "strong_grid", "pack_overrides": {"nope": 1}},
    {"archetype": "strong_grid", "pack_overrides": [1]},
])
def test_eh_readiness_validation_is_the_routes(install_network, kwargs):
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    install_network(_feeder_hub())
    with pytest.raises(HTTPException) as exc:
        T.get_eh_readiness(**kwargs)
    assert exc.value.status_code == 422


def test_eh_readiness_stays_under_the_cap(install_network, monkeypatch):
    import services.adequacy.eh_readiness as E
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    install_network(_feeder_hub())
    big = {"archetype": "strong_grid", "critical_buses": [f"bus-{i}" for i in range(500)],
           "outage_units": [{"name": f"u{i}", "class": "Generator"} for i in range(300)],
           "stages": [{"stage": "ens_solve", "prediction": "run"}]}
    monkeypatch.setattr(E, "eh_readiness", lambda *a, **k: big)
    out = T.get_eh_readiness("strong_grid")
    assert _size(out) < CAP and out["truncated"]["critical_buses"]["total"] == 500
    assert out["stages"] == big["stages"]


# ── reports ─────────────────────────────────────────────────────────────────

@pytest.fixture
def project(client, api_project, project_storage_dir, session_ctx):
    """`test_chat_report_tools`'s fixture: a real project, active for the tools."""
    name = api_project("b2b-reports")
    token = PyPSAService.bind_request_context(session_ctx(client))
    try:
        yield project_storage_dir(name)
    finally:
        PyPSAService.reset_request_context(token)


def test_report_capabilities_and_evidence_hash(project, monkeypatch):
    from services.reports import pdf

    monkeypatch.setattr(pdf, "pdf_available", lambda: False)
    assert T.DISPATCHERS["get_report_capabilities"]() == {"pdf": False}
    h = T.DISPATCHERS["get_report_evidence_hash"]()
    assert set(h) == {"evidence_hash", "sections_ok", "sections_total"} and len(h["evidence_hash"]) == 64


def test_create_evidence_report_then_read_its_version(project):
    from services.reports import store
    from tests.test_chat_report_tools import _store_eh_report

    _store_eh_report()
    out = T.DISPATCHERS["create_evidence_report"](title="B2b evidence")
    assert out["title"] == "B2b evidence" and "document" not in out and _size(out) < CAP
    assert out["sections"] and {"section_id", "heading", "status"} <= set(out["sections"][0])
    assert [m.report_id for m in store.list_reports(project)] == [out["report_id"]]
    version = T.DISPATCHERS["get_report_version"](report_id=out["report_id"], version=1)
    assert version["version"] == 1 and version["title"] == "B2b evidence"
    assert [s["section_id"] for s in version["sections"]] == [s["section_id"] for s in out["sections"]]


def test_get_report_version_summary_and_one_section_stay_under_the_cap(project):
    from services.reports import store
    from tests.test_chat_report_tools import _big_doc

    long = "A long paragraph about section {i}. " * 40
    store.create_report(project, _big_doc(n_sections=40, paragraph=long))
    out = T.get_report_version("0123456789abcdef", 1)
    assert _size(out) < CAP and out["report_id"] == "0123456789abcdef"
    assert "blocks" not in out["sections"][0] and out["sections"][0]["heading"] == "Section 0"
    assert out["truncated"]["sections"] == {"total": 40, "shown": len(out["sections"])}
    assert len(out["sections"]) > 20  # trimmed to what fits, not halved
    one = T.get_report_version("0123456789abcdef", 1, section_id="s3")
    assert one["section"]["section_id"] == "s3" and _size(one) < CAP
    assert one["section"]["blocks"][0]["md"] == long.format(i=3)
    big = T.get_report_version("0123456789abcdef", 1, section_id="s0")
    table = next(b for b in big["section"]["blocks"] if b["type"] == "table")
    assert table["n_rows"] == 50 and "rows" not in table


def test_get_report_version_errors(project):
    from services.reports import store
    from tests.test_chat_report_tools import _big_doc

    store.create_report(project, _big_doc())
    for args, status, kind in (
        (("not-hex", 1), 400, "invalid_report_id"),
        (("0123456789abcdef", 9), 404, "report_not_found"),
        (("0123456789abcdef", 1, "nope"), 404, "report_section_not_found"),
    ):
        with pytest.raises(HTTPException) as exc:
            T.get_report_version(*args)
        assert exc.value.status_code == status and exc.value.detail["error_kind"] == kind
