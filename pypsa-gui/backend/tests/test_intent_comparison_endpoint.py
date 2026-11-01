"""AS-10: the intent comparison job routes (the runner is stubbed; no LP)."""
from __future__ import annotations

import threading
import time

import pytest

from services.compare import intent_jobs
from tests.test_intent_table import _result

INPUTS = {"link": "poc", "envelope": {"phases": [], "max": {}}, "ramp_mw_per_min": 10.0, "sk_mva": 1000.0,
          "pst_limit": 1.0, "transformer_library_mva": [50.0, 100.0]}
URL = "/api/projects/{}/intent-comparison"


@pytest.fixture(autouse=True)
def _clean():
    intent_jobs.clear()
    yield
    intent_jobs.clear()


def _post(client, name, intents, **kw):
    body = {"intents": intents, "critical_mw": 80.0, "inputs": INPUTS, **kw}
    return client.post(URL.format(name), json=body)


def _wait(client, name, status="running", timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(URL.format(name))
        if r.json()["status"] != status:
            return r.json()
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_two_intents_run_as_a_job_and_give_two_rows_with_the_intent_backup(client, api_project, monkeypatch):
    name = api_project()
    seen = []

    def fake_run(network, config, inp, **kw):
        seen.append(config.envelope_floors["backup_mwh"])
        return _result()

    monkeypatch.setattr("services.adequacy.sizing_loop_runner.run_sizing", fake_run)
    r = _post(client, name, [{"intent": "A", "backup_autonomy_h": 6.0}, {"intent": "B"}])
    assert r.status_code == 202, r.text
    done = _wait(client, name)
    assert done["status"] == "done"
    assert [x["intent"] for x in done["rows"]] == ["A", "B"]
    assert seen == [480.0, None]


def test_runs_on_the_named_projects_network_not_the_active_one(client, api_project, monkeypatch):
    from services.pypsa_service import PyPSAService

    first = api_project("first")
    api_project("second")                       # now the active project
    nets = []

    def fake_run(network, config, inp, **kw):
        nets.append(network)
        return _result()

    monkeypatch.setattr("services.adequacy.sizing_loop_runner.run_sizing", fake_run)
    assert _post(client, first, [{"intent": "A"}, {"intent": "B"}]).status_code == 202
    _wait(client, first)
    assert len(nets) == 2 and all(n is not PyPSAService.get_network() for n in nets)


@pytest.mark.parametrize("intents", [
    [{"intent": "A"}],
    [{"intent": "A"}, {"intent": "A"}],
    [{"intent": "A"}, {"intent": "Z"}],
    [{"intent": x} for x in "ABCDE"] + [{"intent": "A"}],
    [{"intent": "A", "backup_autonomy_h": 0}, {"intent": "B"}],
    [{"intent": "A", "backup_autonomy_h": 1e9}, {"intent": "B"}],
    [{"intent": "A", "backup_autonomy_h": "nan"}, {"intent": "B"}],
    [{"intent": "A", "surprise": 1}, {"intent": "B"}],
])
def test_bad_intents_are_422(client, api_project, intents):
    assert _post(client, api_project(), intents).status_code == 422


def test_unknown_request_field_is_422(client, api_project):
    assert _post(client, api_project(), [{"intent": "A"}, {"intent": "B"}], extra=1).status_code == 422


def test_a_refusal_or_exception_in_one_intent_is_that_rows_failed_status(client, api_project, monkeypatch):
    from services.adequacy.sizing_loop_runner import SizingLoopRefusal

    name = api_project()
    calls = []

    def fake_run(network, config, inp, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise SizingLoopRefusal("link poc carries no AS-05 PQ tags")
        raise RuntimeError("solver crashed")

    monkeypatch.setattr("services.adequacy.sizing_loop_runner.run_sizing", fake_run)
    _post(client, name, [{"intent": "A"}, {"intent": "B"}])
    done = _wait(client, name)
    assert done["status"] == "done"
    rows = done["rows"]
    assert [r["outcome"] for r in rows] == ["failed", "failed"]
    assert "PQ tags" in rows[0]["verdict"] and "crashed" in rows[1]["verdict"]


def test_second_start_while_running_is_409_and_abort_stops_with_aborted_rows(client, api_project, monkeypatch):
    name = api_project()
    started = threading.Event()

    def fake_run(network, config, inp, stop_event=None, **kw):
        started.set()
        assert stop_event.wait(10)
        return {"status": "aborted", "verdict": "aborted during round 1", "final": None}

    monkeypatch.setattr("services.adequacy.sizing_loop_runner.run_sizing", fake_run)
    assert _post(client, name, [{"intent": "A"}, {"intent": "B"}]).status_code == 202
    assert started.wait(10)
    assert _post(client, name, [{"intent": "A"}, {"intent": "B"}]).status_code == 409
    assert client.post(URL.format(name) + "/abort").json()["aborting"] is True
    done = _wait(client, name)
    assert done["status"] == "aborted"
    assert [r["outcome"] for r in done["rows"]] == ["aborted", "aborted"]


def test_get_and_abort_before_any_run_are_404(client, api_project):
    name = api_project()
    assert client.get(URL.format(name)).status_code == 404
    assert client.post(URL.format(name) + "/abort").status_code == 404
