"""template-e2e 21: too few simulation runs for the goal are refused before
the run, not reported after it as a straddle.

The MC's first batch is ``min(draws, MAX_DRAWS)``, so a run of ``draws``
draws resolves shortfalls down to ``w_min / draws`` per horizon (``w_min``
the smallest positive snapshot weight). The study start refuses a request
whose draws cannot reach the goal, and readiness files that refusal to
section 8 on the runs field, blocking Run.
"""
from __future__ import annotations

import re

import pytest

LABEL = "Simulation runs per check: "


def _draws(client, value):
    cfg = client.get("/api/simulation/solver_config").json()
    r = client.put("/api/simulation/solver_config", json={**cfg, "mc_draws": value})
    assert r.status_code == 200, r.text


def _runs_items(client, name):
    body = client.get(f"/api/projects/{name}/flow").json()
    return body, [i for s in body["sections"] for i in s["items"]
                  if i["target"]["field"] == "mc_draws" and i["severity"] == "error"]


def _project(client, tid="eh_microgrid", name="floor"):
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": name})
    assert r.status_code == 200, r.text
    return r.json().get("name", name)


def test_one_run_below_the_floor_is_refused_at_the_study_start(client, tmp_projects_dir):
    _project(client)
    _draws(client, 1)
    r = client.post("/api/results/eh_study", json={})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error_kind"] == "unsupported_scope"
    assert detail["field"] == "mc_draws"
    msg = detail["message"]
    assert msg.startswith(LABEL), msg
    assert re.search(r"1 run cannot resolve the 3 h/yr goal \(the smallest "
                     r"shortfall it can see is [\d.]+ h/yr\): set at least \d+\.$", msg), msg


def test_readiness_blocks_run_on_too_few_runs_and_the_minimum_clears_it(
        client, tmp_projects_dir):
    name = _project(client)
    _draws(client, 1)
    body, items = _runs_items(client, name)
    assert body["run_ready"] is False
    assert len(items) == 1, [i["message"] for i in items]
    item = items[0]
    assert item["blocking"] and item["target"]["section"] == "scenarios"
    assert item["message"].startswith(LABEL)
    need = int(re.search(r"set at least (\d+)\.$", item["message"]).group(1))
    assert need > 1
    _draws(client, need - 1)
    assert len(_runs_items(client, name)[1]) == 1
    _draws(client, need)
    assert _runs_items(client, name)[1] == []


@pytest.mark.parametrize("tid", ["eh_h2_hub"])
def test_no_goal_refuses_no_run_count(client, tmp_projects_dir, tid):
    """strong_grid has no pack target: nothing to resolve, nothing refused."""
    name = _project(client, tid, "nogoal")
    _draws(client, 1)
    assert _runs_items(client, name)[1] == []
