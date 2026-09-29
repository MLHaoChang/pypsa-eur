"""
`GET /api/results/value_flows` (Edge Investment Case P3 WP3.4).

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.4. The
participants' ledger per period: its lines, per-participant totals (None when a
party has an unknown line), a bipartite Sankey (a DAG by construction: no node
is both a source and a target) and the conservation checks. 204 before a solve
or without a commercial config; 200 `not_established` without a value-flows
config; 200 `value_flows_invalid` for a stored value that does not validate.
"""
from __future__ import annotations

import time

import pytest

from tests.test_value_flow_reconciliation import VF, _commercial, _network, _solve


def _get():
    import routers.results as R

    return R.get_value_flows()


def _assert_dag(sankey):
    sources = {l["source"] for l in sankey["links"]}
    targets = {l["target"] for l in sankey["links"]}
    assert not sources & targets, sources & targets
    ids = {x["id"] for x in sankey["nodes"]}
    assert sources | targets <= ids
    assert all(l["value"] > 0 for l in sankey["links"])
    assert all(x["side"] == ("payer" if x["id"].startswith("p:") else "payee")
               for x in sankey["nodes"])


@pytest.mark.live_solve
@pytest.mark.parametrize("multi", [False, True], ids=["flat", "multi"])
def test_v1_value_flows_payload(reset_backend, multi):
    n, cfg = _solve(_network(multi=multi), _commercial(), multi=multi)
    t0 = time.perf_counter()
    out = _get()
    elapsed = time.perf_counter() - t0
    assert out["status"] == "ok" and out["conservation_ok"] is True, out.get("flags")
    assert out["template"] == "single_owner"
    assert [x["id"] for x in out["participants"]] == ["site"]
    assert set(out["periods"]) == ({"2030", "2040"} if multi else {"_"})
    for per in out["periods"].values():
        assert per["conservation"]["ok"] is True
        _assert_dag(per["sankey"])
        assert per["lines"] and all("payer" in ln and "amount" in ln for ln in per["lines"])
        site = per["by_participant"]["site"]
        assert site["net"] == pytest.approx(site["received"] - site["paid"])
        # The Sankey carries every known line's money.
        known = sum(ln["amount"] for ln in per["lines"] if ln["amount"])
        assert sum(l["value"] for l in per["sankey"]["links"]) == pytest.approx(known)
    assert out["provenance"]["basis"] == "unweighted_per_period"
    assert out["provenance"]["tariff_payees"]
    # WP3.4: no cache unless the GET measures over 2 s on the P1 fixture.
    assert elapsed < 2.0 * (2 if multi else 1), elapsed


@pytest.mark.live_solve
def test_no_value_flows_config_is_not_established_not_204(reset_backend):
    n, cfg = _solve(_network(), _commercial(vf=None))
    out = _get()
    assert out == {"status": "not_established", "reason": "no_value_flows_config"}


@pytest.mark.live_solve
def test_a_stored_value_that_does_not_validate_is_answered(reset_backend):
    n, cfg = _solve(_network(), _commercial(vf={"participants": "garbage"}))
    out = _get()
    assert out["status"] == "value_flows_invalid" and out["reason"]


@pytest.mark.live_solve
def test_a_party_the_config_no_longer_lists_is_unknown_not_true(reset_backend):
    """The lease's lessor is neither a participant nor an external (the config
    predates the contract): its lines are flagged and the result is None,
    never True. (Null totals for an unknown AMOUNT: test_value_flow_ledger.)"""
    vf = {**VF, "externals": [e for e in VF["externals"] if e != "Leasing GmbH"]}
    n, cfg = _solve(_network(), _commercial(vf=vf))
    out = _get()
    per = out["periods"]["_"]
    assert out["conservation_ok"] is None
    lease = [ln for ln in per["lines"] if ln["contract_id"] == "lease1"]
    assert lease and all("party_not_established:Leasing GmbH" in ln["flags"] for ln in lease)
    _assert_dag(per["sankey"])


def test_204_before_a_solve_and_409_during_one(client, install_network, monkeypatch):
    import routers.results as R
    from tests.fixtures.investment_case.edge_15min import build_edge_15min

    install_network(build_edge_15min(), name="vf_route")
    assert client.get("/api/results/value_flows").status_code == 204
    monkeypatch.setattr(R, "_solver_in_flight", lambda: True)
    r = client.get("/api/results/value_flows")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "solver_in_flight"


def test_the_sankey_is_bipartite_even_when_parties_pay_each_other():
    """A cycle in the money (a pays b, b pays a) is two payer and two payee
    nodes, never a loop."""
    from models.commercial import ValueFlowConfig
    from services.commercial import participants as P
    from services.results.value_flows import _sankey

    vf = ValueFlowConfig.model_validate({"participants": [
        {"id": "a", "name": "A", "role": "site_owner"}, {"id": "b", "name": "B", "role": "other"}]})
    lines = [P.ValueFlowLine("_", "a", "b", "lease", "contract", "x", 10.0),
             P.ValueFlowLine("_", "B", "a", "eaas_fee", "contract", "y", 4.0),
             P.ValueFlowLine("_", "a", "retailer", "energy_import", "bill", "e", 0.0),
             P.ValueFlowLine("_", "a", "dso", "demand_charge", "bill", "d", None)]
    sankey, dropped = _sankey(lines, vf)
    _assert_dag(sankey)
    assert {x["id"] for x in sankey["nodes"]} == {"p:a", "p:b", "r:a", "r:b"}
    assert dropped == {"unknown": 1, "zero": 1}
