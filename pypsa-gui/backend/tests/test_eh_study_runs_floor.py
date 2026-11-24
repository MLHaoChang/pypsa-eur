"""template-e2e 21: too few simulation runs for the goal are refused before
the run, not reported after it as a straddle.

The MC's first batch is ``min(draws, MAX_DRAWS)``, so a run of ``draws``
draws resolves shortfalls down to ``w_min / draws`` per horizon (``w_min``
the smallest positive snapshot weight). The study start refuses a request
whose draws cannot reach the goal, and readiness files that refusal to
section 8 on the runs field, blocking Run.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "project_templates")
)
import eh_templates as T  # noqa: E402

STUDY_IDS = sorted(
    t for t in T.BUILDERS if not T.TEMPLATE_META.get(t, {}).get("generation_only")
)

LABEL = "Simulation runs per check: "


def _draws(client, value):
    cfg = client.get("/api/simulation/solver_config").json()
    r = client.put("/api/simulation/solver_config", json={**cfg, "mc_draws": value})
    assert r.status_code == 200, r.text


def _goal(client, value):
    cfg = client.get("/api/simulation/solver_config").json()
    r = client.put(
        "/api/simulation/solver_config", json={**cfg, "target_lole_h": value}
    )
    assert r.status_code == 200, r.text


# Owner decision on #1927: the start refuses only a goal that no draw count up
# to the 2000 cap resolves. The microgrid's 168 h weeks: one draw resolves
# 8760/168 = 52.1 h/yr, 2000 draws 0.0261 h/yr.
CAP_REFUSAL = (
    LABEL + "a check that finds no shortfall cannot show that the "
    "0.01 h/yr goal is met, and even 2000 runs (the most allowed) "
    "could not decide it (its smallest step there is 0.0261 h/yr): "
    "allow more shortfall in the goal."
)


def _runs_items(client, name):
    body = client.get(f"/api/projects/{name}/flow").json()
    return body, [
        i
        for s in body["sections"]
        for i in s["items"]
        if i["target"]["field"] == "mc_draws" and i["severity"] == "error"
    ]


def _project(client, tid="eh_microgrid", name="floor"):
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": name})
    assert r.status_code == 200, r.text
    return r.json().get("name", name)


def test_one_run_below_the_floor_is_not_refused_at_the_study_start(
    client, tmp_projects_dir
):
    """1 run cannot resolve 3 h/yr, but the MC may draw on to the cap: the
    study runs (VOLL unset refuses next, so no study starts here)."""
    _project(client)
    cfg = client.get("/api/simulation/solver_config").json()
    r = client.put(
        "/api/simulation/solver_config", json={**cfg, "mc_draws": 1, "voll": 0.0}
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/results/eh_study", json={})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["field"] == "voll"


def test_a_goal_past_the_draw_cap_is_refused_at_the_study_start(
    client, tmp_projects_dir
):
    _project(client)
    _draws(client, 1)
    _goal(client, 0.01)
    r = client.post("/api/results/eh_study", json={})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error_kind"] == "unsupported_scope"
    assert detail["field"] == "mc_draws"
    msg = detail["message"]
    assert msg.startswith(LABEL), msg
    assert msg == CAP_REFUSAL, msg


def test_readiness_blocks_run_only_past_the_draw_cap_and_a_larger_goal_clears_it(
    client, tmp_projects_dir
):
    name = _project(client)
    _draws(client, 1)
    assert _runs_items(client, name)[1] == []  # 1 run below the floor runs
    _goal(client, 0.01)
    body, items = _runs_items(client, name)
    assert body["run_ready"] is False
    assert len(items) == 1, [i["message"] for i in items]
    item = items[0]
    assert item["blocking"] and item["target"]["section"] == "scenarios"
    assert item["message"] == CAP_REFUSAL
    _draws(client, 2000)  # more runs do not clear it
    assert len(_runs_items(client, name)[1]) == 1
    _goal(client, 0.03)
    assert _runs_items(client, name)[1] == []


@pytest.mark.parametrize("tid", ["eh_h2_hub"])
def test_no_goal_refuses_no_run_count(client, tmp_projects_dir, tid):
    """strong_grid has no pack target: nothing to resolve, nothing refused."""
    name = _project(client, tid, "nogoal")
    _draws(client, 1)
    assert _runs_items(client, name)[1] == []


@pytest.mark.parametrize("tid", STUDY_IDS)
def test_every_shipped_template_runs_at_its_default_draws(
    client, tmp_projects_dir, tid
):
    """GK A V2: the new refusal never blocks a shipped template at the draws it
    ships with (the study's default), whatever goal its site type assumes."""
    name = _project(client, tid, "shipped")
    body, items = _runs_items(client, name)
    assert items == [], (tid, [i["message"] for i in items])


@pytest.mark.parametrize(
    "stages,field",
    [
        (None, "mc_draws"),
        (["apply_pack", "ens_solve", "mc_certify", "assemble"], "mc_draws"),
        (["apply_pack", "ens_solve", "assemble"], "voll"),
    ],
)
def test_the_runs_check_applies_only_when_the_study_certifies(
    client, tmp_projects_dir, stages, field
):
    """A request whose stages skip mc_certify draws nothing: no runs check.
    VOLL is unset so the later VOLL refusal shows the runs check passed,
    without starting a study."""
    _project(client)
    cfg = client.get("/api/simulation/solver_config").json()
    r = client.put(
        "/api/simulation/solver_config",
        json={**cfg, "mc_draws": 1, "voll": 0.0, "target_lole_h": 0.01},
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/results/eh_study", json={"stages": stages} if stages else {})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["field"] == field
