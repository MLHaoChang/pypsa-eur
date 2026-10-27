"""
Live-voice 08 slice B2a: typed chat tools for twelve GUI routes.

`list_library_series`, `get_library_series`, `import_library_series`,
`import_meter_data`, `put_library_item`, `get_finance_inputs`,
`set_finance_inputs`, `get_poc_state`, `get_value_flow_designer`,
`preview_billing`, `abort_investment_case` and `list_stress_profile_packs`
each call their route's own router handler in process. Per tool: the schema
and tier, a dispatch through the real handler, and an error path; plus the
gatekeeper conditions (F1 finance if-match/clear, F3 org scope), a large-input
bound and a billing preview that writes nothing.
"""
from __future__ import annotations

import copy
import json
import threading

import pytest
from fastapi import HTTPException

from services import chat_tools
from services.chat_tools_schema import TOOL_ROUTES, TOOLS, safety_tier_for

_CAP = 4000

TIERS = {
    "list_library_series": "read",
    "get_library_series": "read",
    "import_library_series": "write",
    "import_meter_data": "write",
    "put_library_item": "write",
    "get_finance_inputs": "read",
    "set_finance_inputs": "write",
    "get_poc_state": "read",
    "get_value_flow_designer": "read",
    "preview_billing": "read",
    "abort_investment_case": "destructive",
    "list_stress_profile_packs": "read",
}
ROUTES = {
    "list_library_series": ("GET", "/api/library/series"),
    "get_library_series": ("GET", "/api/library/series/{name}"),
    "import_library_series": ("POST", "/api/library/series/upload"),
    "import_meter_data": ("POST", "/api/library/meter_data"),
    "put_library_item": ("PUT", "/api/library/items/{kind}/{name}"),
    "get_finance_inputs": ("GET", "/api/simulation/finance"),
    "set_finance_inputs": ("PUT", "/api/simulation/finance"),
    "get_poc_state": ("GET", "/api/simulation/commercial/poc"),
    "get_value_flow_designer": ("GET", "/api/simulation/value_flows/designer"),
    "preview_billing": ("POST", "/api/results/billing/preview"),
    "abort_investment_case": ("POST", "/api/results/investment_case/abort"),
    "list_stress_profile_packs": ("GET", "/api/projects/{name}/stress_profile_packs"),
}
TARIFF = {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
          "items": [{"id": "energy", "kind": "energy", "unit": "per_kwh",
                     "periods": [{"name": "all", "rate": 0.2}]}]}


def _tool(tool_name, /, **kw):
    return chat_tools.DISPATCHERS[tool_name](**kw)


def _size(obj) -> int:
    return len(json.dumps(obj, default=str))


def _schema(name):
    return next(t for t in TOOLS if t["name"] == name)["input_schema"]


def _project(install_network, tmp_projects_dir, name="P"):
    from tests.fixtures.investment_case.edge_15min import build_edge_15min

    install_network(build_edge_15min(), name=name)
    (tmp_projects_dir / name).mkdir(parents=True, exist_ok=True)
    (tmp_projects_dir / name / "network.nc").write_bytes(b"")


def _upload(project, data: bytes, filename="s.csv"):
    from services import upload_service

    return upload_service.add_upload(project, data, filename, "text/csv").file_id


def _csv(n=96, start="2030-01-01 00:00", freq="15min", value=lambda i: 10.0 + i % 7):
    import pandas as pd

    idx = pd.date_range(start, periods=n, freq=freq)
    rows = ["timestamp,value"] + [f"{t.strftime('%Y-%m-%dT%H:%M:%S')},{value(i)}"
                                  for i, t in enumerate(idx)]
    return ("\n".join(rows) + "\n").encode()


def _put_series(client, name="prices", n=48):
    import pandas as pd

    idx = pd.date_range("2030-01-01", periods=n, freq="h", tz="UTC")
    r = client.post("/api/library/series", json={
        "name": name, "timestamps": [t.isoformat() for t in idx],
        "values": [float(i % 24) for i in range(n)], "meta": {"source": "pytest"}})
    assert r.status_code == 200, r.text
    return r.json()


# ── registration ─────────────────────────────────────────────────────────────


def test_each_tool_is_registered_with_its_tier_and_route():
    for name, tier in TIERS.items():
        assert name in chat_tools.DISPATCHERS, name
        assert safety_tier_for(name) == tier, name
        assert TOOL_ROUTES[name] == [ROUTES[name]], name


def test_set_finance_inputs_requires_if_match_in_its_schema():
    schema = _schema("set_finance_inputs")
    assert schema["required"] == ["if_match"]
    assert set(schema["properties"]) == {"finance", "if_match", "clear"}
    desc = next(t for t in TOOLS if t["name"] == "set_finance_inputs")["description"]
    assert "get_finance_inputs" in desc and "REMOVES EVERY finance input" in desc
    assert _schema("import_meter_data")["properties"]["unit"]["enum"] == [
        "kW", "W", "kWh_per_interval"]
    assert "unit" in _schema("import_meter_data")["required"]
    # No tool exposes another org: the acting user's org decides (F3).
    for name in ("list_library_series", "get_library_series", "import_library_series",
                 "import_meter_data", "put_library_item"):
        assert "org_id" not in _schema(name)["properties"], name


def test_new_error_kinds_are_in_the_manifest():
    from tests.test_tool_error_kind_manifest import MANIFEST

    kinds = json.loads(MANIFEST.read_text(encoding="utf-8"))["kinds"]
    for d in chat_tools._FINANCE_TOOL_ERROR_KINDS:
        assert d["error_kind"] in kinds, d


# ── Library series ───────────────────────────────────────────────────────────


def test_list_and_get_series_read_the_callers_org(client):
    ref = _put_series(client)
    page = _tool("list_library_series")
    assert ref["id"] in [r["id"] for r in page["items"]]
    assert page["total_count"] == len(page["items"]) and not page["has_more"]
    got = _tool("get_library_series", name="prices")
    assert got["ref"] == ref and got["points"] == 48 and got["timezone"] == "UTC"
    assert got["min"] == 0.0 and got["max"] == 23.0 and got["meta"]["source"] == "pytest"
    assert got["first"].startswith("2030-01-01T00:00") and got["page"]["returned"] == 48
    nxt = _tool("get_library_series", name="prices", offset=40, limit=5)
    assert nxt["page"]["returned"] == 5 and nxt["page"]["has_more"] is True
    assert nxt["page"]["values"] == [16.0, 17.0, 18.0, 19.0, 20.0]
    with pytest.raises(HTTPException) as exc:
        _tool("get_library_series", name="nope")
    assert exc.value.status_code == 404


def test_a_year_at_15_min_is_paged_never_returned_whole(client):
    """35 040 points: statistics over all, a bounded page, under the result cap."""
    import pandas as pd

    from services.library import series_store as S
    from services import library_acl

    n = 35_040
    idx = pd.date_range("2030-01-01", periods=n, freq="15min", tz="UTC")
    from db.session import SessionLocal
    from db.models import User
    import uuid

    db = SessionLocal()
    try:
        user = db.get(User, uuid.UUID(chat_tools.acting_user_id()))
        org = library_acl.org_of(db, user)
        S.put_series(db, org, "year", pd.Series([float(i) for i in range(n)], index=idx),
                     {"source": "pytest"}, created_by=user.id)
    finally:
        db.close()
    for limit in (None, 500, 10_000):
        got = _tool("get_library_series", name="year", limit=limit)
        assert got["points"] == n and got["max"] == float(n - 1)
        assert got["mean"] == pytest.approx((n - 1) / 2)
        assert 1 <= got["page"]["returned"] <= 500 and got["page"]["has_more"] is True
        assert _size(got) < _CAP


def test_series_tools_refuse_a_user_without_an_org(client, monkeypatch):
    from services import library_acl

    monkeypatch.setattr(library_acl, "org_of", lambda db, user: None)
    for call in (lambda: _tool("list_library_series"),
                 lambda: _tool("get_library_series", name="prices")):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 403


def test_another_orgs_series_is_invisible(client, other_org_client):
    _put_series(other_org_client, name="theirs")
    assert "theirs" not in [r["id"] for r in _tool("list_library_series")["items"]]
    with pytest.raises(HTTPException) as exc:
        _tool("get_library_series", name="theirs")
    assert exc.value.status_code == 404


# ── import_library_series ────────────────────────────────────────────────────


def test_import_series_stores_an_uploaded_csv(client, install_network, tmp_projects_dir):
    _project(install_network, tmp_projects_dir)
    fid = _upload("P", _csv(n=24, freq="h"))
    out = _tool("import_library_series", file_id=fid, name="spot")
    assert out["ref"]["id"] == "spot" and out["ref"]["source"] == "upload"
    got = _tool("get_library_series", name="spot")
    assert got["points"] == 24 and got["timezone"] is None      # naive wall clock, stored naive
    # The route's own rule: a zone beside naive timestamps is ambiguous (422).
    with pytest.raises(HTTPException) as exc:
        _tool("import_library_series", file_id=fid, name="spot", timezone="Europe/Berlin")
    assert exc.value.status_code == 422


def test_import_series_refusals_are_the_routes(client, install_network, tmp_projects_dir,
                                               monkeypatch):
    import routers.library as L

    _project(install_network, tmp_projects_dir)
    bad = _upload("P", b"when,price\n2030-01-01,1\n")
    with pytest.raises(HTTPException) as exc:                       # the parser's 422
        _tool("import_library_series", file_id=bad, name="x")
    assert exc.value.status_code == 422 and "timestamp,value" in str(exc.value.detail)
    # The route's size cap (413) applies to the uploaded bytes unchanged.
    real = L.read_capped
    monkeypatch.setattr(L, "read_capped", lambda f: real(f, max_bytes=16))
    fid = _upload("P", _csv(n=24, freq="h"))
    with pytest.raises(HTTPException) as exc:
        _tool("import_library_series", file_id=fid, name="x")
    assert exc.value.status_code == 413


def test_an_upload_of_another_project_is_refused(client, install_network, tmp_projects_dir):
    _project(install_network, tmp_projects_dir, name="Q")
    other = _upload("Q", _csv(n=24, freq="h"))
    _project(install_network, tmp_projects_dir, name="P")
    for call in (lambda: _tool("import_library_series", file_id=other, name="x"),
                 lambda: _tool("import_meter_data", file_id=other, name="x", unit="kW")):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 404


def test_import_series_refuses_a_user_without_library_write(
        client, install_network, tmp_projects_dir, monkeypatch):
    from services import library_acl

    _project(install_network, tmp_projects_dir)
    fid = _upload("P", _csv(n=24, freq="h"))
    monkeypatch.setattr(library_acl, "can_write", lambda db, user, org: False)
    for call in (lambda: _tool("import_library_series", file_id=fid, name="x"),
                 lambda: _tool("import_meter_data", file_id=fid, name="x", unit="kW")):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 403


# ── import_meter_data ────────────────────────────────────────────────────────


def test_meter_data_stores_kw_and_returns_the_monthly_history(
        client, install_network, tmp_projects_dir):
    _project(install_network, tmp_projects_dir)
    # January 2030 at 15 min, as kWh per interval: 2.5 kWh = 10 kW.
    fid = _upload("P", _csv(n=31 * 96, value=lambda i: 2.5))
    out = _tool("import_meter_data", file_id=fid, name="meter", unit="kWh_per_interval",
                timezone="Europe/Berlin")
    assert out["ref"]["id"] == "meter" and out["settlement"] == "15min"
    assert out["meter_history_peaks_kw"]
    assert all(v == pytest.approx(10.0) for v in out["meter_history_peaks_kw"].values())
    assert out["notes_total"] >= len(out["notes"]) and _size(out) < _CAP
    got = _tool("get_library_series", name="meter")
    assert got["meta"]["source_unit"] == "kWh_per_interval" and got["max"] == pytest.approx(10.0)


def test_meter_data_refuses_what_the_route_refuses(client, install_network, tmp_projects_dir):
    _project(install_network, tmp_projects_dir)
    fid = _upload("P", _csv(n=96))
    with pytest.raises(HTTPException) as exc:
        _tool("import_meter_data", file_id=fid, name="m", unit="MWh")
    assert exc.value.status_code == 422 and "unit" in str(exc.value.detail)
    with pytest.raises(HTTPException) as exc:
        _tool("import_meter_data", file_id=fid, name="m", unit="kW", settlement="5min")
    assert exc.value.status_code == 422


# ── put_library_item ─────────────────────────────────────────────────────────


def test_put_library_item_saves_through_the_route(client):
    ref = _tool("put_library_item", kind="tariff", name="draft", payload=TARIFF,
                meta={"source": "chat"})
    assert ref["kind"] == "tariff" and ref["id"] == "draft" and ref["version"] == 1
    got = _tool("get_library_item", kind="tariff", name="draft", detail="full")
    assert got["payload"]["id"] == "t" and got["meta"]["source"] == "chat"
    ref2 = _tool("put_library_item", kind="tariff", name="draft",
                 payload={**TARIFF, "name": "t2"})
    assert ref2["version"] == 2
    with pytest.raises(HTTPException) as exc:
        _tool("put_library_item", kind="gadget", name="x", payload={})
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        _tool("put_library_item", kind="tariff", name="a/b", payload=TARIFF)
    assert exc.value.status_code == 422


def test_put_library_item_writes_only_the_callers_own_org(client, other_org_client,
                                                          monkeypatch):
    """F3: another org's Library is never the target; without write it is a 403."""
    r = other_org_client.put("/api/library/items/tariff/theirs",
                             json={"payload": TARIFF, "meta": {"source": "them"}})
    assert r.status_code == 200, r.text
    theirs = r.json()
    mine = _tool("put_library_item", kind="tariff", name="theirs",
                 payload={**TARIFF, "name": "mine"})
    assert mine["version"] == 1 and mine["hash"] != theirs["hash"]
    still = other_org_client.get("/api/library/items/tariff/theirs").json()
    assert still["ref"] == theirs and still["payload"]["name"] == "t"
    from services import library_acl

    monkeypatch.setattr(library_acl, "can_write", lambda db, user, org: False)
    with pytest.raises(HTTPException) as exc:
        _tool("put_library_item", kind="tariff", name="x", payload=TARIFF)
    assert exc.value.status_code == 403
    monkeypatch.setattr(library_acl, "org_of", lambda db, user: None)
    with pytest.raises(HTTPException) as exc:
        _tool("put_library_item", kind="tariff", name="x", payload=TARIFF)
    assert exc.value.status_code == 403


# ── finance inputs (gatekeeper F1) ───────────────────────────────────────────


@pytest.fixture
def finance_net(install_network):
    from tests.conftest import build_network

    install_network(build_network(), name="b2a_fin")
    yield
    import routers.simulation as SIM

    SIM.put_finance(SIM.FinanceIn(finance=None), if_match=None)


def _finance():
    from tests.fixtures.investment_case.sam import sam_case as S

    return S.to_finance_case("s2").inputs.model_dump(mode="json")


def test_finance_round_trip_with_the_digest(finance_net):
    first = _tool("get_finance_inputs")
    assert first["status"] == "not_set" and first["finance"] is None
    out = _tool("set_finance_inputs", finance=_finance(), if_match=first["digest"])
    assert out["status"] == "ok" and out["digest"] != first["digest"]
    assert _tool("get_finance_inputs") == out
    cleared = _tool("set_finance_inputs", clear=True, if_match=out["digest"])
    assert cleared["status"] == "not_set" and cleared["finance"] is None


def test_finance_wildcard_and_implicit_clear_are_refused(finance_net):
    digest = _tool("get_finance_inputs")["digest"]
    for tag in ("*", '"*"', " ", ""):
        with pytest.raises(HTTPException) as exc:
            _tool("set_finance_inputs", finance=_finance(), if_match=tag)
        assert exc.value.status_code == 422
        assert exc.value.detail["error_kind"] == "finance_if_match_required"
    with pytest.raises(HTTPException) as exc:                 # null without clear
        _tool("set_finance_inputs", if_match=digest)
    assert exc.value.detail["error_kind"] == "finance_clear_invalid"
    with pytest.raises(HTTPException) as exc:                 # clear with a finance object
        _tool("set_finance_inputs", finance=_finance(), clear=True, if_match=digest)
    assert exc.value.detail["error_kind"] == "finance_clear_invalid"
    assert _tool("get_finance_inputs")["status"] == "not_set"


def test_finance_validation_422_surfaces_unchanged(finance_net):
    digest = _tool("get_finance_inputs")["digest"]
    with pytest.raises(HTTPException) as exc:
        _tool("set_finance_inputs", finance={"no_such_field": 1}, if_match=digest)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "finance_inputs_invalid"
    assert exc.value.detail["errors"]                       # the field paths, as over HTTP


def test_finance_stale_digest_is_412(finance_net):
    stale = _tool("get_finance_inputs")["digest"]
    _tool("set_finance_inputs", finance=_finance(), if_match=stale)
    with pytest.raises(HTTPException) as exc:
        _tool("set_finance_inputs", clear=True, if_match=stale)
    assert exc.value.status_code == 412 and exc.value.detail["code"] == "finance_changed"
    assert _tool("get_finance_inputs")["status"] == "ok"


def test_finance_during_a_solve_is_409(finance_net, monkeypatch):
    import routers.simulation as SIM

    digest = _tool("get_finance_inputs")["digest"]
    monkeypatch.setattr(SIM, "_solver_in_flight_ctx", lambda ctx: True)
    with pytest.raises(HTTPException) as exc:
        _tool("set_finance_inputs", finance=_finance(), if_match=digest)
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "solver_in_flight"




# ── PoC state and value-flow designer ────────────────────────────────────────


@pytest.fixture
def site(client, install_network):
    from tests.fixtures.investment_case.edge_15min import build_edge_15min

    install_network(build_edge_15min(), name="b2a_vf")
    _tool("update_solver_config", partial={"commercial": {
        "poc_link": "import", "import_tariff": TARIFF}})
    return client


def test_poc_state_reads_the_route(site):
    assert _tool("get_poc_state") == {"poc_link": "import", "open": False}


def test_poc_state_is_unknown_while_the_network_is_held(site, monkeypatch):
    import routers.simulation as SIM
    from services.pypsa_service import PyPSAService

    monkeypatch.setattr(SIM, "_POC_LOCK_WAIT_S", 0.05)
    lock, held, release = PyPSAService.get_lock(), threading.Event(), threading.Event()

    def hold():
        with lock:
            held.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        out = _tool("get_poc_state")
    finally:
        release.set()
        t.join()
    assert out == {"poc_link": "import", "open": None, "reason": "network_busy"}


def test_designer_reads_the_route(site):
    out = _tool("get_value_flow_designer")
    assert out["site_party"] and out["assets_total"] == len(out["assets"])
    assert [i["id"] for i in out["tariff_items"]] == ["energy"]
    assert out["assets_page"]["has_more"] is False


def test_designer_refuses_without_a_commercial_config(client, install_network):
    from tests.fixtures.investment_case.edge_15min import build_edge_15min

    install_network(build_edge_15min(), name="b2a_none")
    with pytest.raises(HTTPException) as exc:
        _tool("get_value_flow_designer")
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "no_commercial_config"


def test_a_large_designer_is_paged_under_the_cap(site, monkeypatch):
    from services.commercial import participants as P

    big = {"site_party": "site",
           "assets": [{"component": "Generator", "name": f"pv_{i:04d}", "bus": "site",
                       "side": "site", "ownable": True, "flags": [], "carrier": "solar"}
                      for i in range(2000)],
           "tariff_items": [{"id": f"item_{i}", "kind": "energy", "default_payee": "grid",
                             "stream": "import"} for i in range(300)],
           "contract_parties": [f"party_{i}" for i in range(300)],
           "group_members": [f"m_{i}" for i in range(300)],
           "default_externals": ["grid"]}
    monkeypatch.setattr(P, "designer_context", lambda n, bound: copy.deepcopy(big))
    out = _tool("get_value_flow_designer")
    assert _size(out) < _CAP
    assert out["assets_total"] == 2000 and out["assets_page"]["has_more"] is True
    assert out["tariff_items_total"] == 300
    nxt = _tool("get_value_flow_designer", offset=len(out["assets"]))
    assert nxt["assets"][0]["name"] == f"pv_{len(out['assets']):04d}"


# ── preview_billing ──────────────────────────────────────────────────────────


DRAFT = {"id": "draft", "name": "Draft", "jurisdiction": "DE", "valid_from": "2029-01-01",
         "items": [{"id": "flat", "kind": "energy", "unit": "per_kwh",
                    "periods": [{"name": "all", "rate": 0.10}]}]}


@pytest.mark.live_solve
def test_preview_billing_rates_the_dispatch_and_writes_nothing(reset_backend):
    import routers.results as R
    import routers.simulation as S
    from tests.test_value_flow_reconciliation import _commercial, _network, _solve

    n, _cfg = _solve(_network(), _commercial())
    before_cfg = copy.deepcopy(S._state["solver_config"])
    before_meta = copy.deepcopy(dict(n.meta))
    before_keys = {k: id(v) for k, v in R._state.items()}
    out = _tool("preview_billing", tariff=DRAFT)
    assert "flat" in out["summary"]["_"]["per_item"]
    assert "preview_dispatch_not_optimised_for_draft" in out["flags"]
    assert out["provenance"]["preview"] is True and out["gap"] is None
    assert _size(out) < _CAP
    # Nothing stored: the solver config, the network meta, the solver state.
    assert S._state["solver_config"] == before_cfg
    assert dict(n.meta) == before_meta
    assert {k: id(v) for k, v in R._state.items()} == before_keys
    with pytest.raises(HTTPException) as exc:
        _tool("preview_billing", tariff={**DRAFT, "items": []})
    assert exc.value.status_code == 422 and exc.value.detail["code"] == "tariff_invalid"


def test_preview_billing_before_a_solve_is_no_data(site):
    out = _tool("preview_billing", tariff=DRAFT)
    assert out["status"] == "no_data" and out["kind"] == "billing_preview"


def test_preview_billing_without_a_commercial_config_is_409(client, install_network):
    from tests.fixtures.investment_case.edge_15min import build_edge_15min

    install_network(build_edge_15min(), name="b2a_bill")
    with pytest.raises(HTTPException) as exc:
        _tool("preview_billing", tariff=DRAFT)
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "no_commercial_config"


def test_preview_billing_is_declared_non_mutating():
    from pathlib import Path

    decl = json.loads((Path(__file__).resolve().parents[1] / "smoke"
                       / "assistant_route_parity_declarations.json").read_text())
    entry = [e for e in decl["non_mutating_routes"]
             if (e["method"], e["path"]) == ("POST", "/api/results/billing/preview")]
    assert len(entry) == 1 and entry[0]["reason"]


# ── abort_investment_case ────────────────────────────────────────────────────


def test_abort_investment_case_matches_the_study_abort_contract(install_network):
    import routers.results as R
    from tests.conftest import build_network

    install_network(build_network(), name="b2a_abort")
    R._state["investment_case"] = None
    with pytest.raises(HTTPException) as exc:                   # never ran: 404
        _tool("abort_investment_case")
    assert exc.value.status_code == 404
    stop = threading.Event()
    R._state["investment_case"] = {"status": "running", "stop_event": stop}
    try:
        assert _tool("abort_investment_case") == {"status": "running", "aborting": True}
        assert stop.is_set()
        R._state["investment_case"] = {"status": "done", "stop_event": None}
        assert _tool("abort_investment_case") == {"status": "done", "aborting": False}
    finally:
        R._state["investment_case"] = None


# ── list_stress_profile_packs ────────────────────────────────────────────────


def test_stress_profile_packs_read_through_project_authorization(api_project):
    from services.adequacy.stress import list_profile_packs

    name = api_project("b2a-stress")
    out = _tool("list_stress_profile_packs", name=name)
    assert out["items"] == list_profile_packs() and out["total_count"] == len(out["items"])
    with pytest.raises(HTTPException) as exc:
        _tool("list_stress_profile_packs", name="no-such-project")
    assert exc.value.status_code in (403, 404)


def test_many_stress_profile_packs_are_paged(api_project, monkeypatch):
    import routers.adequacy_worksheet as W

    packs = [{"id": f"pack_{i:04d}", "name": f"Pack {i}", "frequency_per_year": 0.1,
              "snapshots": 24, "loads": [f"l{j}" for j in range(20)],
              "generators": [f"g{j}" for j in range(20)], "provenance": "x"}
             for i in range(500)]
    monkeypatch.setattr(W, "list_profile_packs", lambda: packs)
    name = api_project("b2a-stress-many")
    out = _tool("list_stress_profile_packs", name=name)
    assert out["total_count"] == 500 and out["has_more"] is True and _size(out) < _CAP
