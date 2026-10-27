"""
`PUT /api/simulation/solver_config` refuses a rolling window PyPSA cannot
solve (overlap >= horizon, horizon <= 0, overlap < 0) when the strategy is
rolling, checked on the merged values a partial PUT leaves, with the rule
`validation_service` owns. The chat tool forwards the same kind.
"""
from __future__ import annotations

import pytest

from routers import simulation as sim_router
from services.validation_service import rolling_window_problems

URL = "/api/simulation/solver_config"


def _stored(client):
    cfg = client.get(URL).json()
    return cfg["rolling_horizon"], cfg["rolling_overlap"]


ROLLING = {"solve_strategy": "rolling"}


@pytest.mark.parametrize("body", [
    {**ROLLING, "rolling_overlap": 168},               # == stored horizon
    {**ROLLING, "rolling_overlap": 500},               # > stored horizon
    {**ROLLING, "rolling_horizon": 24},                # == stored overlap
    {**ROLLING, "rolling_horizon": 12},                # < stored overlap
    {**ROLLING, "rolling_horizon": 10, "rolling_overlap": 10},
])
def test_overlap_not_shorter_than_horizon_is_refused(client, body):
    before = _stored(client)
    r = client.put(URL, json=body)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error_kind"] == "rolling_overlap_exceeds_horizon"
    assert "Lower Overlap or raise Horizon" in detail["message"]
    assert _stored(client) == before


@pytest.mark.parametrize("body,code", [
    ({**ROLLING, "rolling_horizon": 0}, "rolling_horizon_invalid"),
    ({**ROLLING, "rolling_overlap": -1}, "rolling_overlap_invalid"),
])
def test_out_of_range_window_is_refused(client, body, code):
    before = _stored(client)
    r = client.put(URL, json=body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == code
    assert _stored(client) == before


def test_valid_window_is_stored(client):
    r = client.put(URL, json={**ROLLING, "rolling_horizon": 48, "rolling_overlap": 12})
    assert r.status_code == 200, r.text
    assert _stored(client) == (48, 12)


def test_unrelated_put_is_not_blocked_by_a_stored_bad_pair(client):
    """A legacy stored pair is the pre-flight check's to refuse, not an unrelated edit's."""
    sim_router._state["solver_config"].rolling_overlap = 500
    assert _stored(client) == (168, 500)
    r = client.put(URL, json={"voll": 9000})
    assert r.status_code == 200, r.text


def test_the_settings_form_echo_of_a_stored_bad_pair_is_not_refused(client):
    """A full-form PUT re-sends the rolling values unchanged: only the edit it carries is judged."""
    sim_router._state["solver_config"].solve_strategy = "rolling"
    sim_router._state["solver_config"].rolling_overlap = 500
    r = client.put(URL, json={**client.get(URL).json(), "voll": 9000})
    assert r.status_code == 200, r.text
    assert client.get(URL).json()["voll"] == 9000


def test_changing_the_window_over_a_stored_bad_pair_is_judged(client):
    sim_router._state["solver_config"].solve_strategy = "rolling"
    sim_router._state["solver_config"].rolling_overlap = 500
    r = client.put(URL, json={**client.get(URL).json(), "rolling_horizon": 200})
    assert r.status_code == 422, r.text
    assert client.put(URL, json={"rolling_overlap": 24}).status_code == 200


def test_switching_to_rolling_over_a_stored_bad_pair_is_refused(client):
    sim_router._state["solver_config"].rolling_overlap = 500
    r = client.put(URL, json=ROLLING)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "rolling_overlap_exceeds_horizon"
    assert client.get(URL).json()["solve_strategy"] == "full"


@pytest.mark.parametrize("body", [
    {"solve_strategy": "full"},                                            # turning rolling off
    {"solve_strategy": "full", "rolling_horizon": 168, "rolling_overlap": 500},  # a full-form echo
])
def test_turning_rolling_off_is_never_refused(client, body):
    sim_router._state["solver_config"].solve_strategy = "rolling"
    sim_router._state["solver_config"].rolling_overlap = 500
    r = client.put(URL, json=body)
    assert r.status_code == 200, r.text
    assert client.get(URL).json()["solve_strategy"] == "full"


def test_the_chat_tool_gets_the_refusal_under_its_kind(client):
    from fastapi import HTTPException

    from services import chat_tools

    with pytest.raises(HTTPException) as exc:
        chat_tools.update_solver_config({**ROLLING, "rolling_overlap": 500})
    assert exc.value.status_code == 422
    assert exc.value.detail["error_kind"] == "rolling_overlap_exceeds_horizon"
    assert _stored(client) == (168, 24)


def test_the_preflight_check_and_the_route_share_one_rule():
    assert [k for k, _ in rolling_window_problems(168, 24, "rolling")] == []
    assert [k for k, _ in rolling_window_problems(24, 24, "rolling")] == ["rolling_overlap_exceeds_horizon"]
    assert [k for k, _ in rolling_window_problems(0, -1, "rolling")] == ["rolling_horizon_invalid", "rolling_overlap_invalid"]
    assert rolling_window_problems(24, 500, "full") == []
