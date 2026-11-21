# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""A saved Guided (Energy Hub) study counts as results in the project list
and shows its headline in Compare, base against a what-if child (template-e2e
issue 23 slice a)."""

# ruff: noqa: F811 -- imported shared fixtures.
from __future__ import annotations

import json

import pytest

from tests.test_template_marker import baseline, eh_base  # noqa: F401

REPORT = {
    "cost_at_target_eur": 1_234_567.0,
    "period_basis": "single_period",
    "achieved_ens_permyriad": 0.4,
    "ens_cap_permyriad": 1.0,
    "mc_lole_h": 2.5,
    "certified": True,
}


@pytest.fixture
def studied(client, eh_base):
    """The template base saved with a finished study on its network."""
    ctx = eh_base.ctx
    ctx.solver_state["eh_study"] = {
        "status": "done",
        "report": dict(REPORT),
        "network_revision": int(ctx.network_revision or 0),
    }
    response = client.post("/api/projects/Tool Baseline")
    assert response.status_code == 200, response.text
    return eh_base


def _child(client):
    response = client.post(
        "/api/projects/Tool Baseline/scenarios", json={"name": "Tool What If"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _listed(client) -> dict:
    response = client.get("/api/projects/")
    assert response.status_code == 200, response.text
    return {p["name"]: p for p in response.json()}


def _study(client, name):
    response = client.get(f"/api/projects/{name}/compare-state")
    assert response.status_code == 200, response.text
    return response.json()["eh_study"]


def test_a_saved_study_counts_as_results_in_the_list(client, studied):
    listed = _listed(client)["Tool Baseline"]
    assert listed["study_finished"] is True
    assert listed["objective"] is None  # no LP objective: the study alone counts


def test_the_list_reads_no_pickle(client, studied, monkeypatch):
    from routers import projects

    def forbidden(*_a, **_k):
        raise AssertionError("the project list must not unpickle results")

    monkeypatch.setattr(projects, "_safe_unpickle_results", forbidden)
    assert _listed(client)["Tool Baseline"]["study_finished"] is True


def test_an_unchanged_child_shows_the_bases_numbers(client, studied):
    _child(client)
    base, child = _study(client, "Tool Baseline"), _study(client, "Tool What If")
    assert base == child
    assert base["state"] == "current"
    assert base["cost_at_target_eur"] == REPORT["cost_at_target_eur"]
    assert base["certified"] is True
    assert _listed(client)["Tool What If"]["study_finished"] is True


def test_a_child_edited_after_the_study_reads_out_of_date(client, studied):
    from services import project_registry
    from services import chat_tools

    _child(client)
    with chat_tools._acting() as (db, user):
        d = project_registry.project_dir(
            project_registry.resolve_project(db, user, "Tool What If")
        )
    meta = json.loads((d / "metadata.json").read_text())
    meta["network_revision"] = int(meta.get("network_revision") or 0) + 1
    (d / "metadata.json").write_text(json.dumps(meta))
    assert _study(client, "Tool What If")["state"] == "out_of_date"
    assert _study(client, "Tool Baseline")["state"] == "current"


@pytest.mark.parametrize("status", ["failed", "aborted"])
def test_an_unfinished_study_is_no_result(client, eh_base, status):
    eh_base.ctx.solver_state["eh_study"] = {
        "status": status,
        "report": None,
        "network_revision": 0,
    }
    assert client.post("/api/projects/Tool Baseline").status_code == 200
    assert _study(client, "Tool Baseline") is None
    assert _listed(client)["Tool Baseline"]["study_finished"] is False


def test_a_project_without_a_study_has_none(client, eh_base):
    assert client.post("/api/projects/Tool Baseline").status_code == 200
    assert _study(client, "Tool Baseline") is None
    assert _listed(client)["Tool Baseline"]["study_finished"] is False


def test_a_record_without_a_revision_reads_out_of_date(client, eh_base):
    eh_base.ctx.solver_state["eh_study"] = {"status": "done", "report": dict(REPORT)}
    assert client.post("/api/projects/Tool Baseline").status_code == 200
    assert _study(client, "Tool Baseline")["state"] == "out_of_date"
