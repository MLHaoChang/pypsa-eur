"""
P24-BE — ``GET /api/results/eh_review``: the chat tool's review as a route.

One source: ``eh_review.review_latest`` serves both ``review_eh_study`` and
the route, so their bodies are deep-equal on the same state. 204 is the
``no_data`` case (same convention as ``/eh_reference_design``); a running
study is 200 ``{"status": "running", ...}``. ``stale`` is a boolean — true
when the stored report was cleared by a later solve and the study record's
copy was reviewed — so the frontend never parses the ``source`` prose.
"""
from __future__ import annotations

import threading
import time

import pytest

from services import chat_tools as T
from services.adequacy.eh_report import EH_REPORT_STORE_KEY
from services.chat_tools_schema import TOOL_ROUTES

REVIEW_URL = "/api/results/eh_review"
STUDY_URL = "/api/results/eh_study"


def _setup(client, install_network):
    from tests.test_energy_hub_frontier_fmea import _feeder_hub
    install_network(_feeder_hub())
    r = client.put("/api/simulation/solver_config",
                   json={"solver_name": "highs", "voll": 3000.0})
    assert r.status_code == 200, r.text


def _tool(ctx):
    """``review_eh_study`` run against the client's OWN context (the active
    project is per session; the test thread would read the process
    foreground otherwise — see conftest ``session_ctx``)."""
    from services.pypsa_service import PyPSAService
    token = PyPSAService.bind_request_context(ctx)
    try:
        return T.review_eh_study()
    finally:
        PyPSAService.reset_request_context(token)


def _poll(client, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(STUDY_URL).json()
        if body.get("status") != "running":
            return body
        time.sleep(0.1)
    raise AssertionError("eh_study never finished")


def test_tool_routes_pins_the_review_route():
    assert TOOL_ROUTES["review_eh_study"] == [("GET", REVIEW_URL)]


def test_no_study_is_204_and_the_tool_says_no_data(client, install_network,
                                                    session_ctx):
    _setup(client, install_network)
    r = client.get(REVIEW_URL)
    assert r.status_code == 204
    assert r.content == b""
    assert _tool(session_ctx(client))["status"] == "no_data"


def test_running_is_200_running_and_equals_the_tool(client, install_network,
                                                    monkeypatch, session_ctx):
    reached, release = threading.Event(), threading.Event()

    def fake_run(network, pack, cfg, **kw):
        reached.set()
        release.wait(30)
        raise RuntimeError("stop")

    monkeypatch.setattr("services.adequacy.eh_study.run_eh_study", fake_run)
    _setup(client, install_network)
    try:
        assert client.post(STUDY_URL,
                           json={"archetype": "weak_flexible"}).status_code == 200
        assert reached.wait(10)
        r = client.get(REVIEW_URL)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "running"
        assert isinstance(body["message"], str) and body["message"]
        assert body == _tool(session_ctx(client))
    finally:
        release.set()
        _poll(client)


@pytest.mark.live_solve
def test_done_route_equals_tool_and_stale_follows_the_stored_report(
        client, install_network, session_ctx):
    _setup(client, install_network)
    r = client.post(STUDY_URL, json={
        "archetype": "weak_flexible",
        "stages": ["apply_pack", "ens_solve", "assemble"]})
    assert r.status_code == 200, r.text
    assert _poll(client)["status"] == "done"

    r = client.get(REVIEW_URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["stale"] is False
    assert body["source"] == "stored report"
    assert {"summary", "findings", "next_steps"} <= set(body)
    ctx = session_ctx(client)
    assert body == _tool(ctx)                          # one source, deep-equal

    # A later solve clears the stored report: the study record's copy is
    # reviewed and ``stale`` says so as a boolean.
    ctx.solver_state.pop(EH_REPORT_STORE_KEY, None)
    assert client.get("/api/results/eh_reference_design").status_code == 204
    r = client.get(REVIEW_URL)
    assert r.status_code == 200, r.text
    stale = r.json()
    assert stale["status"] == "ok"
    assert stale["stale"] is True
    assert stale["source"].startswith("study record")
    assert stale == _tool(ctx)


def test_review_latest_is_the_one_source():
    """The pure helper: running / no_data / ok+stale without HTTP."""
    from services.adequacy.eh_review import review_latest

    assert review_latest({}, {"status": "running"})["status"] == "running"
    nd = review_latest({}, None, no_data_message="nothing yet")
    assert nd == {"status": "no_data", "message": "nothing yet"}
    rec_report = {"archetype": "strong_grid", "sections": {}, "notes": []}
    out = review_latest({}, {"status": "done", "report": rec_report})
    assert out["status"] == "ok"
    assert out["stale"] is True
    assert out["source"].startswith("study record")
