"""
`PUT /api/simulation/solver_config` refuses a rolling window PyPSA cannot
solve (overlap >= horizon, horizon <= 0, overlap < 0), checked on the merged
pair a partial PUT leaves, with the rule `validation_service` owns.
"""
from __future__ import annotations

import pytest

from routers import simulation as sim_router
from services.validation_service import rolling_window_problems

URL = "/api/simulation/solver_config"


def _stored(client):
    cfg = client.get(URL).json()
    return cfg["rolling_horizon"], cfg["rolling_overlap"]


@pytest.mark.parametrize("body", [
    {"rolling_overlap": 168},                          # == stored horizon
    {"rolling_overlap": 500},                          # > stored horizon
    {"rolling_horizon": 24},                           # == stored overlap
    {"rolling_horizon": 12},                           # < stored overlap
    {"rolling_horizon": 10, "rolling_overlap": 10},
])
def test_overlap_not_shorter_than_horizon_is_refused(client, body):
    before = _stored(client)
    r = client.put(URL, json=body)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "rolling_overlap_exceeds_horizon"
    assert "Lower Overlap or raise Horizon" in detail["message"]
    assert _stored(client) == before


@pytest.mark.parametrize("body,code", [
    ({"rolling_horizon": 0}, "rolling_horizon_invalid"),
    ({"rolling_overlap": -1}, "rolling_overlap_invalid"),
])
def test_out_of_range_window_is_refused(client, body, code):
    before = _stored(client)
    r = client.put(URL, json=body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == code
    assert _stored(client) == before


def test_valid_window_is_stored(client):
    r = client.put(URL, json={"rolling_horizon": 48, "rolling_overlap": 12})
    assert r.status_code == 200, r.text
    assert _stored(client) == (48, 12)


def test_unrelated_put_is_not_blocked_by_a_stored_bad_pair(client):
    """A legacy stored pair is the pre-flight check's to refuse, not an unrelated edit's."""
    sim_router._state["solver_config"].rolling_overlap = 500
    assert _stored(client) == (168, 500)
    r = client.put(URL, json={"voll": 9000})
    assert r.status_code == 200, r.text


def test_the_preflight_check_and_the_route_share_one_rule():
    assert [k for k, _ in rolling_window_problems(168, 24)] == []
    assert [k for k, _ in rolling_window_problems(24, 24)] == ["rolling_overlap_exceeds_horizon"]
    assert [k for k, _ in rolling_window_problems(0, -1)] == ["rolling_horizon_invalid", "rolling_overlap_invalid"]
