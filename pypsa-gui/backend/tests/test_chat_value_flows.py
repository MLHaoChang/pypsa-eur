"""
Chat for value flows (Edge Investment Case P3 WP3.4): `define_participants`
and `get_results(result_kind="value_flows", detail=…)`.

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.4.
`define_participants` drives the template route (a template needing contracts
the project lacks is NOT saved: its unpriced drafts come back) and the
value-flows route (always with the digest it read as If-Match; an existing,
different config is not replaced until `replace=true`). The result is a summary
per participant and stream under the chat's result cap; `detail="lines"` pages
the ledger lines.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from services import chat_tools
from services.chat_tools_schema import RESULTS_TAB_ENUM, TOOL_ROUTES, safety_tier_for
from tests.fixtures.investment_case.edge_15min import build_edge_15min

TARIFF = {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
          "items": [{"id": "energy", "kind": "energy", "unit": "per_kwh",
                     "periods": [{"name": "all", "rate": 0.2}]}]}
PPA = {"type": "ppa", "id": "ppa1", "kind": "pay_as_produced", "price": 20.0,
       "tenor_years": 10, "seller": "Solar BV", "buyer": "site", "asset_ids": ["pv"]}
_CAP = 4000


def _define(**kw):
    return chat_tools.DISPATCHERS["define_participants"](**kw)


@pytest.fixture
def site(client, install_network):
    """The chat's active network with a commercial config set THROUGH the chat
    (the in-process tools read the foreground, not an HTTP session)."""
    install_network(build_edge_15min(), name="chat_vf")
    chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": {
        "poc_link": "import", "import_tariff": TARIFF, "contracts": [PPA]}})
    return client


def _stored(_client=None):
    import routers.simulation as S

    return S.get_value_flows()


# ── registration ───────────────────────────────────────────────────────────


def test_define_participants_is_a_write_tool_on_its_routes():
    assert "define_participants" in chat_tools.DISPATCHERS
    assert safety_tier_for("define_participants") == "write"
    assert ("PUT", "/api/simulation/commercial/value_flows") in TOOL_ROUTES["define_participants"]
    assert "investment" in RESULTS_TAB_ENUM


# ── define_participants ────────────────────────────────────────────────────


def test_a_template_without_drafts_is_saved_with_if_match(site):
    out = _define(template="btm_ppa")
    assert out["saved"] is True and out["template"] == "btm_ppa"
    assert set(out["participants"]) == {"site (offtaker)", "Solar BV (developer)"}
    assert out["participants_total"] == 2
    assert _stored(site)["digest"] == out["digest"]


def test_a_template_needing_contracts_is_not_saved(site):
    out = _define(template="landlord_tenant")
    assert out["saved"] is False and out["status"] == "drafts_need_pricing"
    (draft,) = out["draft_contracts"]
    assert draft["type"] == "lease" and draft["annual_payment"] is None
    assert _stored(site)["value_flows"] is None                     # nothing saved
    assert len(json.dumps(out, default=str)) < _CAP


def test_an_existing_different_config_is_not_replaced_silently(site):
    _define(template="single_owner")
    with pytest.raises(HTTPException) as exc:
        _define(template="btm_ppa")
    assert exc.value.status_code == 409
    assert exc.value.detail["error_kind"] == "value_flows_would_be_replaced"
    assert _stored(site)["value_flows"]["template"] == "single_owner"
    assert _define(template="btm_ppa", replace=True)["template"] == "btm_ppa"
    with pytest.raises(HTTPException) as exc:
        _define(clear=True)
    assert exc.value.detail["error_kind"] == "value_flows_would_be_replaced"
    assert _define(clear=True, replace=True)["saved"] is True
    assert _stored(site)["value_flows"] is None


def test_the_same_config_again_needs_no_confirmation(site):
    _define(template="single_owner")
    assert _define(template="single_owner")["saved"] is True


def test_an_invalid_config_is_refused_with_its_problems(site):
    bad = {"participants": [{"id": "site", "name": "Site", "role": "site_owner"}],
           "asset_owners": [{"asset_id": "ghost", "component": "Generator", "owner": "site"}]}
    with pytest.raises(HTTPException) as exc:
        _define(config=bad)
    d = exc.value.detail
    assert exc.value.status_code == 422 and d["error_kind"] == "value_flows_invalid"
    assert d["problems_total"] >= 1 and any("ghost" in p for p in d["problems"])


def test_a_refused_template_names_its_code(site):
    with pytest.raises(HTTPException) as exc:
        _define(template="energy_hub")
    d = exc.value.detail
    assert d["error_kind"] == "template_refused" and d["code"] == "template_needs_group_contract"


def test_exactly_one_of_template_config_or_clear(site):
    for kw in ({}, {"template": "single_owner", "clear": True},
               {"template": "single_owner", "config": {}}):
        with pytest.raises(HTTPException) as exc:
            _define(**kw)
        assert exc.value.detail["error_kind"] == "value_flows_invalid"


def test_no_commercial_config_and_solver_in_flight(client, install_network, monkeypatch):
    import routers.simulation as S

    install_network(build_edge_15min(), name="chat_vf_bare")
    with pytest.raises(HTTPException) as exc:
        _define(template="single_owner")
    assert exc.value.detail["error_kind"] == "no_commercial_config"
    chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": {"poc_link": "import"}})
    monkeypatch.setattr(S, "_solver_in_flight_ctx", lambda ctx: True)
    with pytest.raises(HTTPException) as exc:
        _define(template="single_owner")
    assert exc.value.detail["error_kind"] == "solver_in_flight"


# ── get_results(value_flows) ────────────────────────────────────────────────


def _payload(n_parties=3, n_lines=40, periods=("_",)):
    parts = {f"party_{i}": {"paid": 1000.0 * i, "received": 10.0, "net": 10.0 - 1000.0 * i,
                            "by_stream": {f"stream_{k}": -1.0 * k for k in range(8)}}
             for i in range(n_parties)}
    line = {"payer": "party_0", "payee": "party_1", "value_stream": "energy_import",
            "source": "bill", "source_id": "energy", "amount": 12.3456, "basis": "cash",
            "contract_id": None, "tariff_item": "energy", "asset": None, "flags": []}
    return {"status": "ok", "template": "single_owner",
            "participants": [{"id": "party_0", "name": "P0", "role": "site_owner"}],
            "conservation_ok": True, "flags": [f"flag_{i}" for i in range(40)], "notes": [],
            "periods": {p: {"lines": [dict(line) for _ in range(n_lines)],
                            "by_participant": parts,
                            "conservation": {"ok": True, "checks": [{"name": "coverage",
                                                                     "ok": True}]},
                            "sankey": {"nodes": [], "links": []}, "disclosures": {}}
                        for p in periods}}


def test_the_summary_fits_the_cap_whatever_the_ledger():
    small = chat_tools._value_flows_summary(_payload())
    assert small["periods"]["_"]["by_participant"]["party_1"]["by_stream"]
    assert "omitted" not in small
    big = chat_tools._value_flows_summary(_payload(n_parties=60, periods=("2030", "2040", "2050")))
    assert len(json.dumps(big, default=str)) <= chat_tools._VF_SUMMARY_CHARS
    assert big["omitted"][:2] == ["by_stream", "externals"]
    assert set(big["periods"]["2030"]["by_participant"]) == {"party_0"}   # participants kept


def test_the_summary_fits_even_with_many_internal_participants():
    """Review #1: 60 PARTICIPANTS (not externals) × 3 periods, and long ids."""
    payload = _payload(n_parties=60, periods=("2030", "2040", "2050"))
    payload["participants"] = [{"id": f"party_{i}", "name": f"P{i}", "role": "other"}
                               for i in range(60)]
    out = chat_tools._value_flows_summary(payload)
    assert len(json.dumps(out, default=str)) <= chat_tools._VF_SUMMARY_CHARS
    assert out["periods"]["2030"]["participants_omitted"] > 0
    long_ids = _payload(n_parties=10)
    long_ids["participants"] = [{"id": "x" * 200 + str(i), "name": "n", "role": "other"}
                                for i in range(10)]
    long_ids["periods"]["_"]["by_participant"] = {
        "x" * 200 + str(i): v for i, v in enumerate(
            long_ids["periods"]["_"]["by_participant"].values())}
    assert len(json.dumps(chat_tools._value_flows_summary(long_ids), default=str)) <= \
        chat_tools._VF_SUMMARY_CHARS


def test_the_summary_fits_with_long_flags_and_many_periods_and_keeps_unknowns():
    """Review R2-1 / R2-2."""
    payload = _payload(n_parties=60, periods=tuple(str(2030 + i) for i in range(40)))
    payload["participants"] = [{"id": f"party_{i}", "name": "n", "role": "other"}
                               for i in range(60)]
    payload["flags"] = ["contract_not_settled:x:" + "a" * 3000] * 3
    for per in payload["periods"].values():
        per["by_participant"]["party_59"]["net"] = None
    out = chat_tools._value_flows_summary(payload)
    assert len(json.dumps(out, default=str)) <= chat_tools._VF_SUMMARY_CHARS
    first = next(iter(out["periods"].values()))["by_participant"]
    assert "party_59" in first and first["party_59"]["net"] is None


def test_a_drafts_answer_stays_small_on_a_big_site(client, install_network):
    """Review #2: the answer carries no config and fits the draft's id lists."""
    n = build_edge_15min()
    for i in range(80):
        n.add("Generator", f"pv_{i}", bus="site", carrier="solar", p_nom=1.0)
    install_network(n, name="chat_vf_big")
    chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": {
        "poc_link": "import", "import_tariff": TARIFF}})
    out = _define(template="landlord_tenant")
    assert out["saved"] is False and "config" not in out
    assert out["asset_owners_total"] > 80
    (draft,) = out["draft_contracts"]
    assert len(draft["asset_ids"]) == 10 and draft["asset_ids_total"] > 80
    assert len(json.dumps(out, default=str)) < 2500


def test_the_same_raw_config_twice_needs_no_confirmation(site):
    """Review #5: compared as the server stores it."""
    raw = {"participants": [{"id": "site", "name": "Site", "role": "site_owner"}],
           "externals": ["retailer", "dso", "tso", "market", "tax_authority",
                         "capex_supplier", "om_contractor", "Solar BV"]}
    assert _define(config=raw)["saved"] is True
    assert _define(config=raw)["saved"] is True


def test_the_lines_are_paged_under_the_cap():
    page = chat_tools._value_flow_lines_page(_payload(n_lines=500), 0, None)
    assert page["kind"] == "value_flows_lines" and page["total_count"] == 500
    assert page["has_more"] is True and len(json.dumps(page, default=str)) < _CAP
    assert page["items"][0]["amount"] == 12.35 and "asset" not in page["items"][0]
    nxt = chat_tools._value_flow_lines_page(_payload(n_lines=500), page["returned"], None)
    assert nxt["items"]


def test_a_line_with_huge_ids_and_flags_still_fits_a_page():
    """IC P3 gate note: party ids and flags are unbounded user text; a single
    row is cut so a page never degrades to a preview."""
    payload = _payload(n_lines=3)
    for ln in payload["periods"]["_"]["lines"]:
        ln.update(payer="p" * 5000, payee="q" * 5000, asset="a" * 5000,
                  flags=["f" * 5000] * 6)
    page = chat_tools._value_flow_lines_page(payload, 0, None)
    row = page["items"][0]
    assert page["returned"] >= 2 and len(json.dumps(page, default=str)) < _CAP
    assert len(row["payer"]) == 80 and len(row["asset"]) == 80
    assert len(row["flags"]) == 4 and all(len(f) == 160 for f in row["flags"])


@pytest.mark.live_solve
def test_get_results_value_flows_on_a_solved_site(reset_backend):
    from tests.test_value_flow_reconciliation import _commercial, _network, _solve

    _solve(_network(), _commercial())
    out = chat_tools.get_results("value_flows")
    assert out["status"] == "ok" and out["conservation_ok"] is True
    assert len(json.dumps(out, default=str)) < _CAP
    assert out["periods"]["_"]["by_participant"]["site"]["net"] is not None
    lines = chat_tools.get_results("value_flows", detail="lines", limit=5)
    assert lines["kind"] == "value_flows_lines" and len(lines["items"]) <= 5
    # `detail` is ignored where it means nothing.
    assert "total" in chat_tools.get_results("cost_breakdown", detail="lines")


@pytest.mark.live_solve
def test_not_established_passes_through(reset_backend):
    from tests.test_value_flow_reconciliation import _commercial, _network, _solve

    _solve(_network(), _commercial(vf=None))
    assert chat_tools.get_results("value_flows") == {"status": "not_established",
                                                     "reason": "no_value_flows_config"}
