"""
`POST /api/results/billing/preview` (Edge Investment Case P3 WP3.7b): the last
solve's dispatch billed under a DRAFT tariff. Nothing is stored; the drift flags
are replaced by `preview_dispatch_not_optimised_for_draft`; no gap; 409 during
a solve; 204 before one; 422 for a draft that does not validate or bind.
"""
from __future__ import annotations

import copy

import pytest

from tests.test_value_flow_reconciliation import _commercial, _network, _solve

DRAFT = {"id": "draft", "name": "Draft", "jurisdiction": "DE", "valid_from": "2029-01-01",
         "items": [{"id": "flat", "kind": "energy", "unit": "per_kwh",
                    "periods": [{"name": "all", "rate": 0.10}]}]}


def _preview(tariff):
    import routers.results as R

    return R.preview_billing(R.BillingPreviewIn(tariff=tariff))


@pytest.mark.live_solve
def test_the_preview_rates_the_dispatch_and_changes_nothing(reset_backend):
    import routers.results as R
    import routers.simulation as S
    from services.commercial.billing import bill_site

    n, cfg = _solve(_network(), _commercial())
    before_cfg = copy.deepcopy(S._state["solver_config"].commercial)
    before_meta = copy.deepcopy(dict(n.meta))
    state_keys = set(R._state)
    out = _preview(DRAFT)
    p0 = n.links_t.p0
    energy_mwh = float((n.snapshot_weightings.objective * p0["import"].clip(lower=0)).sum())
    assert out["summary"]["_"]["per_item"]["flat"] == pytest.approx(energy_mwh * 100.0, rel=1e-9)
    assert "preview_dispatch_not_optimised_for_draft" in out["flags"]
    assert "config_changed_since_solve" not in out["flags"]           # the drift flags go
    assert out["gap"] is None and out["provenance"]["preview"] is True
    assert S._state["solver_config"].commercial == before_cfg          # nothing stored
    assert dict(n.meta) == before_meta and set(R._state) == state_keys
    # The real bill is untouched.
    assert "flat" not in bill_site(n, cfg.commercial).per_period[None].per_item


@pytest.mark.live_solve
def test_a_draft_that_does_not_validate_or_bind_is_a_422(reset_backend):
    from fastapi import HTTPException

    _solve(_network(), _commercial())
    with pytest.raises(HTTPException) as exc:
        _preview({**DRAFT, "items": []})
    assert exc.value.status_code == 422 and exc.value.detail["code"] == "tariff_invalid"
    unrated = {**DRAFT, "items": [{"id": "night", "kind": "energy", "unit": "per_kwh",
                                   "periods": [{"name": "n", "rate": 0.1, "start_hour": 0,
                                                "end_hour": 6}]}]}
    with pytest.raises(HTTPException) as exc:
        _preview(unrated)
    assert exc.value.status_code == 422 and exc.value.detail["code"] == "tariff_not_bindable"


def test_409_during_a_solve_or_without_a_config_and_204_before_a_solve(
        client, install_network, monkeypatch):
    import routers.results as R
    from tests.fixtures.investment_case.edge_15min import build_edge_15min

    install_network(build_edge_15min(), name="preview")
    r = client.post("/api/results/billing/preview", json={"tariff": DRAFT})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_commercial_config"
    client.put("/api/simulation/solver_config", json={"commercial": {"poc_link": "import"}})
    assert client.post("/api/results/billing/preview", json={"tariff": DRAFT}).status_code == 204
    monkeypatch.setattr(R, "_solver_in_flight", lambda: True)
    r = client.post("/api/results/billing/preview", json={"tariff": DRAFT})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "solver_in_flight"
