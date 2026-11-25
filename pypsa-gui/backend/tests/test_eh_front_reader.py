"""GET /results/eh_front: the reader for the EH study's front stage (DC 01 PR3).

The stage writes ``eh_front`` (test_eh_front_stage.py pins what it writes);
this pins the route: 204 with no table, GET /front's record shape with one,
freshness read off the EH study's own record, and never applicable.
"""

from __future__ import annotations

import json

URL = "/api/results/eh_front"


def _revision(session_ctx, client):
    from services import dirty_state

    return dirty_state.revision(session_ctx(client))


def _point(i: int, cost: float, eue: float, co2: float) -> dict:
    return {
        "index": i,
        "lever": "reserve_margin",
        "level": 0.1 * (i + 1),
        "co2_cap_t_per_yr": None,
        "condition": "optimal",
        "status": "ok",
        "point": {
            "capex_eur_per_yr": cost * 0.6,
            "opex_eur_per_yr": cost * 0.4,
            "total_eur_per_yr": cost,
            "lp_ens_mwh": 0.0,
            "reliability_binding": True,
            "co2_t_per_yr": co2,
            "co2_shadow_eur_per_t": None,
            "co2_binding": None,
            "capacities": {"generators:gas": 10.0 + i},
            "excludes_shed_cost": True,
        },
        "mc": {
            "lole_hours": 2.0 - i,
            "lole_ci": [1.5 - i, 2.5 - i],
            "eue_mwh": eue,
            "eue_ci": [eue * 0.8, eue * 1.2],
            "n_samples": 10,
            "cov_target": 0.05,
            "converged": False,
            "resolution_floor_h": None,
            "below_floor": False,
            "time_basis": "hours_per_year",
        },
        "mc_error": None,
        "dominated_by": [],
        "on_front": True,
        "reliability_indistinct": False,
    }


def _result(aborted: bool = False) -> dict:
    return {
        "lever": "reserve_margin",
        "lever_reason": "the dispatch model has no outages",
        "levels": [0.1, 0.2],
        "co2_caps_t_per_yr": [None],
        "points": [_point(0, 1000.0, 5.0, 200.0), _point(1, 1200.0, 2.0, 210.0)],
        "reliability_basis": "mc_eue",
        "degenerate": {"axes": {}, "all": False, "text": None},
        "warning": None,
        "aborted": aborted,
        "solves": 2,
    }


def test_no_front_table_is_204(client, session_state):
    session_state(client).pop("eh_front", None)
    r = client.get(URL)
    assert r.status_code == 204


def test_the_table_is_served_in_the_front_record_shape(
    client, session_state, session_ctx
):
    st = session_state(client)
    rev = _revision(session_ctx, client)
    st["eh_study"] = {"status": "done", "network_revision": rev, "finished_at": 12.5}
    st["eh_front"] = _result()
    r = client.get(URL)
    assert r.status_code == 200, r.text
    body = r.json()
    json.dumps(body, allow_nan=False)
    assert body["source"] == "eh_study"
    assert body["status"] == "done"
    assert body["applicable"] is False
    assert body["finished_at"] == 12.5
    assert body["edited_since_study"] is False
    # The same point shape GET /results/front serves under `result.points`.
    assert body["result"]["points"] == _result()["points"]
    assert body["result"]["reliability_basis"] == "mc_eue"


def test_an_edit_since_the_study_reads_true(client, session_state, session_ctx):
    st = session_state(client)
    rev = _revision(session_ctx, client)
    st["eh_study"] = {"status": "done", "network_revision": rev - 1}
    st["eh_front"] = _result()
    assert client.get(URL).json()["edited_since_study"] is True


def test_no_study_record_is_unavailable_not_current(client, session_state):
    st = session_state(client)
    st.pop("eh_study", None)
    st["eh_front"] = _result()
    body = client.get(URL).json()
    assert body["edited_since_study"] is None
    assert body["finished_at"] is None


def test_an_aborted_stage_reads_aborted(client, session_state, session_ctx):
    st = session_state(client)
    st["eh_study"] = {
        "status": "aborted",
        "network_revision": _revision(session_ctx, client),
    }
    st["eh_front"] = _result(aborted=True)
    assert client.get(URL).json()["status"] == "aborted"


def test_the_front_and_eh_front_records_share_one_shape(client, session_state):
    """One frontend type serves both: the keys the panel reads are present in
    each record and in each point."""
    import threading

    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()
    st = session_state(client)
    st["front"] = {
        "status": "done",
        "error": None,
        "network_revision": 0,
        "thread": t,
        "result": _result(),
    }
    st["eh_front"] = _result()
    a = client.get("/api/results/front").json()
    b = client.get(URL).json()
    for key in ("status", "result", "error", "edited_since_study"):
        assert key in a and key in b
    assert set(a["result"]["points"][0]) == set(b["result"]["points"][0])
