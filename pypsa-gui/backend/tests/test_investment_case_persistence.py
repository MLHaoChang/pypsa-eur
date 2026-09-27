"""
Edge Investment Case — persistence of the report, billing cache and the
solved commercial terms (Phase 0, WP0.5).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP0.5
Spec: docs/superpowers/specs/2026-09-26-edge-investment-case-design.md §5.1

Three new `RESULT_STATE_KEYS` ride `results_state.pkl`:
  * `investment_case_report` — the `InvestmentCaseReport`, as a JSON dict
  * `billing_frames`         — the billing pass's per-item frames (P2)
  * `last_commercial_terms`  — solved `ic_*` variable values the cost
                               breakdown needs to reconcile after a reload (P1)

The pickle is read back through a RESTRICTED unpickler (untrusted bundles), so
the round trip is the real test: a value type the allow-list refuses would be
silently dropped on load.
"""
from __future__ import annotations

import pickle

import pandas as pd
import pytest

from models import finance as F
from services.project_context import RESULT_STATE_KEYS, ProjectSolverState

IC_KEYS = ("investment_case_report", "billing_frames", "last_commercial_terms")


def _report() -> F.InvestmentCaseReport:
    comp = F.empty_ic_completeness()
    comp["design"] = "ok"
    return F.InvestmentCaseReport(
        case_id="case-1", assumptions_hash="a" * 16, completeness=comp,
        cost_at_target_eur=1.25e6,
        sections={"design": F.IcSectionState(status="ok", payload={"mw": 12.5})})


def _billing_frames() -> dict[str, pd.DataFrame]:
    idx = pd.date_range("2030-01-01", periods=4, freq="15min")
    return {"energy_tou": pd.DataFrame(
        {"quantity": [1.0, 2.0, 3.0, 4.0], "rate": 0.12, "amount": [0.12, 0.24, 0.36, 0.48]},
        index=idx)}


def _terms() -> dict:
    return {"ic_peak_import": {"2030-01": 42.5}, "flags": ["ratchet_seed_missing"]}


def test_the_three_keys_are_persisted_result_state():
    for k in IC_KEYS:
        assert k in RESULT_STATE_KEYS, k
        assert hasattr(ProjectSolverState(solver_config=None), k), k


def test_store_and_load_report_round_trip_through_a_plain_dict():
    from services.finance.report import load_ic_report, store_ic_report

    store: dict = {}
    assert load_ic_report(store) is None
    store_ic_report(store, _report())
    assert isinstance(store["investment_case_report"], dict)   # JSON, not a model
    assert load_ic_report(store) == _report()


def test_http_payload_is_204_then_the_export_view():
    from services.finance.report import ic_report_http_payload, store_ic_report

    store: dict = {}
    assert ic_report_http_payload(store) == (None, 204)
    store_ic_report(store, _report())
    body, status = ic_report_http_payload(store)
    assert status == 200
    assert set(body) == set(F.IC_EXPORT_KEYS)
    assert body["cost_at_target_eur"] == 1.25e6
    assert body["completeness"]["design"] == "ok"


def test_the_restricted_unpickler_accepts_every_ic_value_type():
    from routers.projects import (
        _RESULTS_STATE_SCHEMA,
        _safe_unpickle_results,
        _unwrap_results_state,
    )

    payload = {"__schema__": _RESULTS_STATE_SCHEMA, "data": {
        "investment_case_report": _report().model_dump(mode="json"),
        "billing_frames": _billing_frames(),
        "last_commercial_terms": _terms(),
    }}
    data = _unwrap_results_state(_safe_unpickle_results(pickle.dumps(payload)))
    assert data is not None
    assert F.InvestmentCaseReport.model_validate(data["investment_case_report"]) == _report()
    pd.testing.assert_frame_equal(data["billing_frames"]["energy_tou"],
                                  _billing_frames()["energy_tou"])
    assert data["last_commercial_terms"] == _terms()


def test_save_then_load_restores_all_three(client, api_project, session_state,
                                            project_storage_dir):
    from routers.projects import _safe_unpickle_results, _unwrap_results_state

    name = api_project("ic_persist")
    st = session_state(client)
    st["investment_case_report"] = _report().model_dump(mode="json")
    st["billing_frames"] = _billing_frames()
    st["last_commercial_terms"] = _terms()
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text

    pkl = project_storage_dir(name) / "results_state.pkl"
    assert pkl.exists()
    on_disk = _unwrap_results_state(_safe_unpickle_results(pkl.read_bytes()))
    for k in IC_KEYS:
        assert on_disk.get(k) is not None, k

    for k in IC_KEYS:
        st[k] = None
    r = client.get(f"/api/projects/{name}")
    assert r.status_code == 200, r.text
    st = session_state(client)
    assert F.InvestmentCaseReport.model_validate(st["investment_case_report"]) == _report()
    pd.testing.assert_frame_equal(st["billing_frames"]["energy_tou"],
                                  _billing_frames()["energy_tou"])
    assert st["last_commercial_terms"] == _terms()


def test_a_reset_clears_the_three_keys(client, api_project, session_state):
    """Loading a project WITHOUT these keys must not inherit them from the
    previously loaded one (the restore path clears every RESULT_STATE key)."""
    a = api_project("ic_has")
    st = session_state(client)
    st["investment_case_report"] = _report().model_dump(mode="json")
    st["last_commercial_terms"] = _terms()
    assert client.post(f"/api/projects/{a}", params={"force": True, "rebind": True}).status_code == 200
    b = api_project("ic_hasnt")
    st = session_state(client)
    st["investment_case_report"] = None
    st["last_commercial_terms"] = None
    assert client.post(f"/api/projects/{b}", params={"force": True, "rebind": True}).status_code == 200
    assert client.get(f"/api/projects/{a}").status_code == 200
    assert session_state(client)["investment_case_report"] is not None
    assert client.get(f"/api/projects/{b}").status_code == 200
    st = session_state(client)
    assert st["investment_case_report"] is None
    assert st["last_commercial_terms"] is None
