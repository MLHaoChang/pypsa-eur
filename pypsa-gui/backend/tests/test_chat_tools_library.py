"""
Library chat tools (Edge Investment Case P2 WP2.4c).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.4c.
`list_library_items`, `get_library_item`, `import_urdb_tariff` (an UPLOADED
file id, never an LLM-emitted blob; Safety: write) and `attach_tariff` (sets
`import_tariff_ref` through the solver-config route) call the router handlers
with the acting user, so the org Library ACL applies exactly as over HTTP.
The four guard suites (schema match, arg shape, endpoint map, identity) cover
them through `TOOLS` / `TOOL_ROUTES` / `DISPATCHERS`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from services import chat_tools
from services.chat_tools_schema import safety_tier_for

ORACLES = Path(__file__).parent / "fixtures" / "investment_case" / "oracles"
TARIFF = {"id": "nl-tou", "name": "NL TOU", "jurisdiction": "NL", "valid_from": "2030-01-01",
          "items": [{"id": "energy", "kind": "energy", "unit": "per_kwh",
                     "periods": [{"name": "night", "rate": 0.05, "start_hour": 0, "end_hour": 6},
                                 {"name": "day", "rate": 0.20}]}]}
TOOLS = ("list_library_items", "get_library_item", "import_urdb_tariff", "attach_tariff")


def _put(client, name="nl", payload=TARIFF):
    r = client.put(f"/api/library/items/tariff/{name}",
                   json={"payload": payload, "meta": {"source": "pytest"}})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_tools_are_registered_with_their_safety_tiers():
    for name in TOOLS:
        assert name in chat_tools.DISPATCHERS
    assert [safety_tier_for(n) for n in TOOLS] == ["read", "read", "write", "write"]


def test_list_and_get_read_the_callers_org_library(client):
    ref = _put(client)
    assert chat_tools.DISPATCHERS["list_library_items"](kind="tariff") == [ref]
    got = chat_tools.DISPATCHERS["get_library_item"](kind="tariff", name="nl")
    assert got["ref"] == ref and got["payload"]["id"] == "nl-tou"
    assert got["meta"]["source"] == "pytest"
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["get_library_item"](kind="tariff", name="nope")
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["list_library_items"](kind="gadget")
    assert exc.value.status_code == 422


def _project(install_network, tmp_projects_dir):
    from tests.fixtures.investment_case.edge_15min import build_edge_15min

    install_network(build_edge_15min(), name="P")
    (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
    (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")


def test_import_reads_an_uploaded_file_by_id(client, install_network, tmp_projects_dir):
    from services import upload_service

    _project(install_network, tmp_projects_dir)
    scenario = (ORACLES / "r1_leap_year.reopt.json").read_bytes()      # a REopt scenario
    meta = upload_service.add_upload("P", scenario, "leap_year.json", "application/json")
    out = chat_tools.DISPATCHERS["import_urdb_tariff"](file_id=meta.file_id, name="r1",
                                                       valid_from="2023-01-01")
    assert out["ref"]["id"] == "r1" and out["refusals"] == []
    assert "demandwindow_absent_assumed_15min" in out["notes"]
    got = chat_tools.DISPATCHERS["get_library_item"](kind="tariff", name="r1")
    assert [i["id"] for i in got["payload"]["items"]] == ["energy", "demand_tou", "demand",
                                                          "fixed"]
    # A refused field is a 422 listing it, unless the partial import is accepted.
    rate = json.loads((ORACLES / "r2_tiered_tou_demand.urdb.json").read_text())
    bad = upload_service.add_upload("P", json.dumps({"items": [{**rate, "mincharge": 5}]})
                                    .encode(), "openei.json", "application/json")
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["import_urdb_tariff"](file_id=bad.file_id, name="r2",
                                                     valid_from="2017-01-01")
    assert exc.value.status_code == 422
    assert [r["field"] for r in exc.value.detail["refusals"]] == ["mincharge"]
    part = chat_tools.DISPATCHERS["import_urdb_tariff"](
        file_id=bad.file_id, name="r2", valid_from="2017-01-01", accept_partial=True)
    assert part["unsupported_fields"] == ["mincharge"]


def test_import_refuses_an_upload_that_is_not_json(client, install_network, tmp_projects_dir):
    from services import upload_service

    _project(install_network, tmp_projects_dir)
    meta = upload_service.add_upload("P", b"not json", "x.json", "application/json")
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["import_urdb_tariff"](file_id=meta.file_id, name="x",
                                                     valid_from="2030-01-01")
    assert exc.value.status_code == 422


def test_attach_sets_the_ref_through_the_route(client, install_network, tmp_projects_dir,
                                               session_ctx):
    ref = _put(client)
    _project(install_network, tmp_projects_dir)   # the chat's active network, after the PUT
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["attach_tariff"](name="nl")
    assert exc.value.status_code == 409          # no commercial config yet
    chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": {
        "poc_link": "import", "import_tariff": {**TARIFF, "id": "inline"}}})
    out = chat_tools.DISPATCHERS["attach_tariff"](name="nl")
    assert out["import_tariff_ref"] == ref
    cfg = chat_tools.DISPATCHERS["get_solver_config"]()["commercial"]
    assert cfg["import_tariff_ref"] == ref
    assert cfg["import_tariff"]["id"] == "nl-tou"          # resolved from the Library
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["attach_tariff"](name="nl", version=9)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("call", [
    lambda: chat_tools.list_library_items("tariff"),
    lambda: chat_tools.get_library_item("tariff", "nl"),
    lambda: chat_tools.attach_tariff("nl"),
])
def test_library_tools_refuse_without_an_acting_user(call):
    previous = chat_tools.acting_user_id()
    chat_tools.set_acting_user(None)
    try:
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 401
    finally:
        chat_tools.set_acting_user(previous)
