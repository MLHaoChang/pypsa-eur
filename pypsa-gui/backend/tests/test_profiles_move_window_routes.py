"""Moving a maintenance window over HTTP and through the chat tools (TSG 07).

The copilot proposes ``move_window`` through `refine_generated_profile`; the
old → new card names the window's dates before and after (gatekeeper
condition 7, #857). The refusals keep their kinds: ``invalid_refine_op``, and
``maintenance_year`` for a dated window carried out of its year. A stored
move replays on a re-generate (TSG 06), relative to the regenerated params.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from harness import edit_card
from services import chat_tools

_WINDOWS = {"windows": [{"from": "06-01", "to": "06-07", "available": 0.0}]}
_MOVE = [{"op": "move_window", "by": "14d"}]


def _network() -> pypsa.Network:
    n = pypsa.Network()
    idx = pd.date_range("2025-01-01", periods=8760, freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "site")
    n.add("Carrier", "gas")
    n.add("Generator", "CHP", bus="site", p_nom=10.0, carrier="gas")
    n.add("Load", "plant", bus="site", p_set=5.0)
    return n


@pytest.fixture
def project(client, install_network):
    install_network(_network())
    assert client.post("/api/projects/Outage Site",
                       params={"force": True, "rebind": True}).status_code == 200
    return "Outage Site"


def _generate(client, project, params=None):
    resp = client.post(f"/api/projects/{project}/profiles/availability/generate",
                       json={"kind": "maintenance", "components": ["CHP"],
                             "year": 2025, "params": dict(params or _WINDOWS)})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _refine(client, project, job_id, ops):
    return client.post(f"/api/projects/{project}/profiles/refine",
                       json={"job_id": job_id, "ops": ops})


def test_the_copilot_proposes_the_move_behind_the_card(client, project, bound_ctx):
    with bound_ctx(client):
        gen = chat_tools.DISPATCHERS["generate_availability_profile"](
            project_id=project, kind="maintenance", generators=["CHP"], year=2025,
            params=_WINDOWS)
        refined = chat_tools.DISPATCHERS["refine_generated_profile"](
            project_id=project, job_id=gen["job_id"], ops=_MOVE)
        assert refined["params"]["windows"][0]["from"] == "06-15"
        st = refined["stats"]["CHP"][0]
        assert st["window_before"] == {"from": "06-01", "to": "06-07"}
        assert st["window_after"] == {"from": "06-15", "to": "06-21"}
        args = {"project_id": project, "job_id": refined["job_id"],
                "component": "CHP", "time_basis": "UTC"}
        card = edit_card.preview("apply_generated_profile", args)
        assert card["kind"] == "series_edit" and card["complete"] is True
        notes = " ".join(r["note"] for r in card["rows"] if r["role"] == "note")
        assert "maintenance window 1 Jun to 7 Jun moved to 15 Jun to 21 Jun" in notes
        assert "keep their hours" not in notes  # no edit before the move
        edit_card.require_current("apply_generated_profile", args, card)
        chat_tools.DISPATCHERS["apply_generated_profile"](**args)
        receipt = edit_card.receipt(card, args)
    assert receipt["outcome"] == "verified"


def test_a_load_series_refuses_the_move(client, project):
    resp = client.post(f"/api/projects/{project}/profiles/loads/generate",
                       json={"archetype": "industrial", "loads": ["plant"], "year": 2025,
                             "params": {"peak_mw": 5.0}})
    assert resp.status_code == 200, resp.text
    resp = _refine(client, project, resp.json()["job_id"], _MOVE)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "invalid_refine_op"
    assert "no maintenance window to move" in detail["message"]


def test_two_windows_without_an_index_name_both(client, project):
    gen = _generate(client, project, {"windows": [
        {"from": "06-01", "to": "06-07"}, {"from": "09-01", "to": "09-03"}]})
    resp = _refine(client, project, gen["job_id"], _MOVE)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "invalid_refine_op"
    assert "06-01" in detail["message"] and "09-01" in detail["message"]


def test_a_dated_window_past_the_year_keeps_its_kind(client, project):
    gen = _generate(client, project, {"windows": [
        {"from": "2025-12-20", "to": "2025-12-28", "available": 0.0}]})
    resp = _refine(client, project, gen["job_id"], _MOVE)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "maintenance_year"
    assert "31 Dec 2025" in detail["message"]


def test_a_regenerate_replays_the_stored_move(client, project):
    gen = _generate(client, project)
    refined = _refine(client, project, gen["job_id"], _MOVE)
    assert refined.status_code == 200, refined.text
    resp = client.post(f"/api/projects/{project}/profiles/apply",
                       json={"job_id": refined.json()["job_id"], "component": "CHP",
                             "time_basis": "UTC"})
    assert resp.status_code == 200, resp.text
    again = _generate(client, project)
    assert again["params"]["windows"][0] == {"from": "06-15", "to": "06-21",
                                             "available": 0.0}
    assert [r["op"] for r in again["refinements"]] == ["move_window"]
