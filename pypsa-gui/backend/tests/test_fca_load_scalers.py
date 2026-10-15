"""The FCA stress hours follow the load the LP solves (E2E demo plan
2026-10-14, slice 1b).

An FCA agreement's curtailment hours are the highest-load open snapshots
(`connection.fca_stress_entry`). On a multi-period project with
`load_scalers`, the LP solves the SCALED demand (`demand.lp_demand_frame`),
so the hours are ranked on it too; ranking them on the raw `p_set` picked
the wrong hours. A change of the scalers alone re-ranks the stored entry.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import numpy as np

from models.commercial import ConnectionAgreement
from services.adequacy import stress as ST
from services.commercial import connection as C
from tests.fixtures.investment_case.edge_15min import build_edge_15min

PERIODS = (2030, 2035)


def _two_period():
    """The edge site's first day in two investment periods (identical load)."""
    base = build_edge_15min()
    n = base.copy()
    n.set_snapshots(base.snapshots[:96])
    n.snapshot_weightings.loc[:, :] = 0.25
    n.loads_t.p_set = base.loads_t.p_set.iloc[:96]
    n.generators_t.p_max_pu = base.generators_t.p_max_pu.iloc[:96]
    n.set_investment_periods(list(PERIODS))
    return n


def _fca(hours=876.0):
    return ConnectionAgreement(kind="fca", import_cap_mw=40.0, curtailment_hours_per_year=hours,
                               available_from=date(2030, 1, 1))


def _cfg(**scalers):
    return SimpleNamespace(multi_investment_periods=True, load_scalers_by_carrier={},
                           load_scalers={str(k): v for k, v in scalers.items()})


def _zeroed_periods(n, entry):
    series = np.asarray(entry["links_p_max_pu"]["import"])
    return set(n.snapshots.get_level_values(0)[series == 0.0])


def test_the_hours_are_ranked_on_the_scaled_load():
    n = _two_period()
    # identical load in both periods: the peak hours of both are taken
    raw = C.fca_stress_entry(n, _fca(), poc_link="import")
    assert _zeroed_periods(n, raw) == {2030, 2035}
    # 2035 at three times the load: every curtailed hour moves there
    scaled = C.fca_stress_entry(n, _fca(), poc_link="import", solver_cfg=_cfg(**{"2035": 3.0}))
    assert _zeroed_periods(n, scaled) == {2035}
    assert (np.asarray(scaled["links_p_max_pu"]["import"]) == 0.0).sum() == \
        (np.asarray(raw["links_p_max_pu"]["import"]) == 0.0).sum()


def test_without_scalers_the_entry_is_unchanged():
    n = _two_period()
    raw = C.fca_stress_entry(n, _fca(), poc_link="import")
    assert C.fca_stress_entry(n, _fca(), poc_link="import", solver_cfg=_cfg()) == raw
    off = SimpleNamespace(multi_investment_periods=False, load_scalers={"2035": 3.0},
                          load_scalers_by_carrier={})
    # the LP scales nothing without multi_investment_periods, nor does the ranking
    assert C.fca_stress_entry(n, _fca(), poc_link="import", solver_cfg=off) == raw


def test_the_entry_says_its_hours_are_one_budget_over_the_horizon():
    """Owner decision 2026-10-15: the curtailment budget stays horizon-wide
    (h/yr × the horizon's years, taken in its worst hours wherever they fall),
    and the entry's text says so."""
    n = _two_period()
    entry = C.fca_stress_entry(n, _fca(), poc_link="import")
    assert entry["label"] == ("FCA curtailment on import (876 h/yr, one budget over the "
                              "whole horizon, taken in its highest-load hours)")


# ── through the route ────────────────────────────────────────────────────────


def _saved_two_period(client, install_network, name):
    install_network(_two_period(), name=name)
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _fca_body():
    return {"poc_link": "import", "connection": _fca().model_dump(mode="json")}


def _entry(project_storage_dir, name):
    return next(e for e in ST.load_scenarios(project_storage_dir(name)) if e["id"] == "fca_import")


def test_the_route_ranks_on_the_submitted_scalers_and_rebinds_when_they_change(
        client, install_network, session_ctx, project_storage_dir):
    _saved_two_period(client, install_network, "fca_ls")
    n = session_ctx(client).network
    r = client.put("/api/simulation/solver_config", json={
        "multi_investment_periods": True, "load_scalers": {"2035": 3.0},
        "commercial": _fca_body()})
    assert r.status_code == 200, r.text
    assert _zeroed_periods(n, _entry(project_storage_dir, "fca_ls")) == {2035}
    # the scalers alone change: the stored FCA entry is re-ranked
    r = client.put("/api/simulation/solver_config",
                   json={"load_scalers": {"2030": 3.0, "2035": 1.0}})
    assert r.status_code == 200, r.text
    assert _zeroed_periods(n, _entry(project_storage_dir, "fca_ls")) == {2030}
    assert r.json()["commercial"]["connection"]["kind"] == "fca"


def test_an_unrelated_change_does_not_rebind(client, install_network, project_storage_dir,
                                             monkeypatch):
    _saved_two_period(client, install_network, "fca_ls2")
    assert client.put("/api/simulation/solver_config", json={
        "multi_investment_periods": True, "commercial": _fca_body()}).status_code == 200
    from services.commercial import binding

    calls = []
    real = binding.bind_commercial
    monkeypatch.setattr(binding, "bind_commercial", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert client.put("/api/simulation/solver_config", json={"voll": 4000.0}).status_code == 200
    assert calls == []


def test_a_scalers_only_put_with_an_invalid_stored_config_is_a_409(
        client, install_network, project_storage_dir, monkeypatch):
    from pydantic import ValidationError

    from models.commercial import CommercialConfig

    _saved_two_period(client, install_network, "fca_ls3")
    assert client.put("/api/simulation/solver_config", json={
        "multi_investment_periods": True, "commercial": _fca_body()}).status_code == 200
    # a stored config that no longer validates (the load path does not re-validate it)
    try:
        CommercialConfig.model_validate({"poc_link": 3})
    except ValidationError as exc:
        stale = exc

    def invalid(*_a, **_k):
        raise stale

    monkeypatch.setattr(CommercialConfig, "model_validate", invalid)
    r = client.put("/api/simulation/solver_config", json={"load_scalers": {"2035": 3.0}})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "commercial_config_invalid"
    assert "load-scaler" in r.json()["detail"]["message"]


def test_a_refused_put_leaves_the_registry_and_the_scalers_untouched(
        client, install_network, project_storage_dir, monkeypatch):
    from fastapi import HTTPException

    from routers import simulation as S

    _saved_two_period(client, install_network, "fca_ls4")
    assert client.put("/api/simulation/solver_config", json={
        "multi_investment_periods": True, "load_scalers": {"2035": 3.0},
        "commercial": _fca_body()}).status_code == 200
    before = _entry(project_storage_dir, "fca_ls4")

    def refuse(*_a, **_k):
        raise HTTPException(403, {"code": "user_code_forbidden", "message": "no"})

    monkeypatch.setattr(S, "_gate_user_code", refuse)
    r = client.put("/api/simulation/solver_config", json={"load_scalers": {"2030": 3.0}})
    assert r.status_code == 403
    assert _entry(project_storage_dir, "fca_ls4") == before
    stored = client.get("/api/simulation/solver_config").json()
    assert stored["load_scalers"] == {"2035": 3.0}


def test_a_scalers_only_put_during_a_solve_names_the_solver_config(
        client, install_network, project_storage_dir, monkeypatch):
    from routers import simulation as S

    _saved_two_period(client, install_network, "fca_ls5")
    assert client.put("/api/simulation/solver_config", json={
        "multi_investment_periods": True, "commercial": _fca_body()}).status_code == 200
    monkeypatch.setattr(S, "_solver_in_flight_ctx", lambda ctx: True)
    r = client.put("/api/simulation/solver_config", json={"load_scalers": {"2035": 3.0}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "solver_in_flight"
    assert "load-scaler" in r.json()["detail"]["message"]
    assert "solver config" in r.json()["detail"]["message"]
