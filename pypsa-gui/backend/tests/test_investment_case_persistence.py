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


# ── review WP0.5 #1: local-time frames must survive the restricted unpickler ──
#
# The billing pass rates in LOCAL time (DST matters for TOU windows). A
# tz-aware index pickles a pytz/zoneinfo object the allow-list refuses, and
# one refused value drops EVERY side result on reload. So the store helpers
# normalise to UTC and record the zone name as a plain string.


def _berlin_dst_frame() -> pd.DataFrame:
    # 2030-03-31 is the spring-forward day in Europe/Berlin: 92 quarter-hours.
    idx = pd.date_range("2030-03-31 00:00", "2030-03-31 23:45", freq="15min",
                        tz="Europe/Berlin")
    return pd.DataFrame({"quantity": range(len(idx)), "amount": 0.5}, index=idx)


def test_raw_tz_aware_frame_is_refused_by_the_unpickler():
    """Documents WHY the helpers exist: this is the failure they prevent."""
    import pickle as _p

    from routers.projects import _safe_unpickle_results
    with pytest.raises(_p.UnpicklingError):
        _safe_unpickle_results(_p.dumps({"x": _berlin_dst_frame()}))


def test_billing_frames_round_trip_local_time_through_the_unpickler():
    import pickle as _p

    from routers.projects import _safe_unpickle_results
    from services.finance.report import load_billing_frames, store_billing_frames

    frame = _berlin_dst_frame()
    assert len(frame) == 92
    store: dict = {}
    store_billing_frames(store, {"energy_tou": frame})
    raw = _safe_unpickle_results(_p.dumps(store))       # must not raise
    back = load_billing_frames(raw)
    pd.testing.assert_frame_equal(back["energy_tou"], frame, check_freq=False)
    assert str(back["energy_tou"].index.tz) == "Europe/Berlin"


def test_naive_billing_frames_stay_naive():
    from services.finance.report import load_billing_frames, store_billing_frames

    store: dict = {}
    store_billing_frames(store, _billing_frames())
    back = load_billing_frames(store)
    pd.testing.assert_frame_equal(back["energy_tou"], _billing_frames()["energy_tou"])


def test_commercial_terms_keys_and_values_become_plain_json():
    import pickle as _p

    from routers.projects import _safe_unpickle_results
    from services.finance.report import store_commercial_terms

    store: dict = {}
    store_commercial_terms(store, {
        "ic_peak_import": {pd.Period("2030-01", "M"): 42.5,
                           pd.Timestamp("2030-02-01", tz="Europe/Berlin"): 40.0},
        "flags": ("ratchet_seed_missing",),
    })
    terms = _safe_unpickle_results(_p.dumps(store))["last_commercial_terms"]
    assert terms["ic_peak_import"] == {"2030-01": 42.5, "2030-02-01T00:00:00+01:00": 40.0}
    assert terms["flags"] == ["ratchet_seed_missing"]


# ── review WP0.5 #2: a new solve claim clears the per-solve IC keys ──────────


def test_a_foreground_solve_clears_the_ic_keys(client, install_network, session_state):
    from tests.conftest import build_network

    install_network(build_network(), name="ic_claim")
    st = session_state(client)
    st["investment_case_report"] = _report().model_dump(mode="json")
    st["billing_frames"] = _billing_frames()
    st["last_commercial_terms"] = _terms()
    resp = client.post("/api/simulation/run")
    assert resp.status_code == 200, resp.text
    t = session_state(client).get("thread")
    t.join(timeout=120)
    st = session_state(client)
    for k in IC_KEYS:
        assert st.get(k) is None, k


def test_a_queued_solve_clears_the_ic_keys(client, install_network, tmp_projects_dir,
                                            project_storage_dir, session_state):
    import time
    import uuid

    from routers.projects import _safe_unpickle_results, _unwrap_results_state
    from services.solve_queue import solve_queue
    from tests.conftest import build_network

    install_network(build_network(), name="ic_q")
    st = session_state(client)
    st["last_commercial_terms"] = _terms()
    st["investment_case_report"] = _report().model_dump(mode="json")
    assert client.post("/api/projects/ic_q", params={"force": True, "rebind": True}).status_code == 200
    pkl = project_storage_dir("ic_q") / "results_state.pkl"
    before = _unwrap_results_state(_safe_unpickle_results(pkl.read_bytes()))
    assert before["last_commercial_terms"] == _terms()

    r = client.post("/api/simulation/queue", json={"project_id": "ic_q"})
    assert r.status_code == 200, r.text
    job_id = uuid.UUID(str(r.json()["id"]))
    deadline = time.time() + 90
    while time.time() < deadline:
        if (solve_queue.get_job(job_id) or {}).get("status") in ("completed", "failed", "aborted"):
            break
        time.sleep(0.2)
    assert solve_queue.get_job(job_id)["status"] == "completed"
    # The save after a queued solve DELETES the pickle when no side result is
    # left (projects.py save path) — which is exactly "all cleared".
    after = (_unwrap_results_state(_safe_unpickle_results(pkl.read_bytes()))
             if pkl.exists() else {})
    for k in IC_KEYS:
        assert after.get(k) is None, k
