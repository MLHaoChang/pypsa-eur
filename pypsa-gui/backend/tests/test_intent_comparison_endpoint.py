"""AS-10: POST /api/projects/{name}/intent-comparison (the runner is stubbed; no LP)."""
from __future__ import annotations

from tests.test_intent_table import _result

INPUTS = {"link": "poc", "envelope": {"phases": [], "max": {}}, "ramp_mw_per_min": 10.0, "sk_mva": 1000.0,
          "pst_limit": 1.0, "transformer_library_mva": [50.0, 100.0]}


def _post(client, name, intents, **kw):
    body = {"intents": intents, "critical_mw": 80.0, "inputs": INPUTS, **kw}
    return client.post(f"/api/projects/{name}/intent-comparison", json=body)


def test_two_intents_give_two_rows_and_the_intent_backup_reaches_the_config(client, api_project, monkeypatch):
    name = api_project()
    seen = []

    def fake_run(network, config, inp, **kw):
        seen.append(config.envelope_floors["backup_mwh"])
        return _result()

    monkeypatch.setattr("services.adequacy.sizing_loop_runner.run_sizing", fake_run)
    r = _post(client, name, [{"intent": "A", "backup_autonomy_h": 6.0}, {"intent": "B"}])
    assert r.status_code == 200, r.text
    assert [x["intent"] for x in r.json()["rows"]] == ["A", "B"]
    assert seen == [480.0, None]


def test_one_intent_is_refused(client, api_project):
    name = api_project()
    assert _post(client, name, [{"intent": "A"}]).status_code == 422


def test_unknown_intent_is_refused(client, api_project):
    name = api_project()
    assert _post(client, name, [{"intent": "A"}, {"intent": "Z"}]).status_code == 422
