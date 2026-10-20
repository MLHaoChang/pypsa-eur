"""Refinement over HTTP, through the chat tools and behind the edit card (ticket 04).

`POST /profiles/refine` turns a pending artifact into a refined one; the
generate routes replay a recorded op list; `apply_generated_profile`'s
old → new card (`harness.edit_card`, adapter `series_edit`) shows the peak,
energy and load factor the target holds now and would hold, and its receipt
reads them back.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from harness import edit_card
from services import chat_tools


def _network() -> pypsa.Network:
    n = pypsa.Network()
    idx = pd.date_range("2025-01-01", periods=8760, freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Load", "plant", bus="site", p_set=10.0)
    n.add("Link", "poc", bus0="grid", bus1="site", p_nom=100.0)
    return n


@pytest.fixture
def project(client, install_network):
    install_network(_network())
    assert client.post("/api/projects/Refine Site",
                       params={"force": True, "rebind": True}).status_code == 200
    return "Refine Site"


def _generate(client, project, **extra):
    body = {"archetype": "industrial", "loads": ["plant"], "year": 2025,
            "params": {"shifts": 2, "peak_mw": 80.0, "timezone": "Europe/Berlin"},
            **extra}
    resp = client.post(f"/api/projects/{project}/profiles/loads/generate", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


_OPS = [{"op": "scale_window", "hours": [5, 10], "factor": 1.3},
        {"op": "scale_peak_to", "mw": 60.0}]


def test_refine_writes_a_new_pending_artifact(client, project):
    gen = _generate(client, project)
    resp = client.post(f"/api/projects/{project}/profiles/refine",
                       json={"job_id": gen["job_id"], "ops": _OPS})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["based_on"] == gen["job_id"]
    assert body["job_id"] != gen["job_id"]
    assert [r["op"] for r in body["refinements"]] == ["scale_window", "scale_peak_to"]
    assert body["stats"]["plant"][1]["peak_after"] == pytest.approx(60.0)
    assert body["provenance"].endswith("user-refined")


def test_a_bad_op_is_a_typed_refusal(client, project):
    gen = _generate(client, project)
    resp = client.post(f"/api/projects/{project}/profiles/refine",
                       json={"job_id": gen["job_id"], "ops": [{"op": "teleport"}]})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "invalid_refine_op"


def test_regenerating_with_the_refinements_replays_them(client, project):
    gen = _generate(client, project)
    refined = client.post(f"/api/projects/{project}/profiles/refine",
                          json={"job_id": gen["job_id"], "ops": _OPS}).json()
    again = _generate(client, project, refinements=refined["refinements"])
    assert again["stats"]["plant"]["peak_mw"] == pytest.approx(60.0)


def test_the_card_shows_old_and_new_and_the_receipt_verifies(client, project, bound_ctx):
    with bound_ctx(client):
        gen = chat_tools.DISPATCHERS["generate_load_profile"](
            project_id=project, archetype="industrial", loads=["plant"], year=2025,
            params={"shifts": 2, "peak_mw": 80.0, "timezone": "Europe/Berlin"})
        refined = chat_tools.DISPATCHERS["refine_generated_profile"](
            project_id=project, job_id=gen["job_id"], ops=_OPS)
        args = {"project_id": project, "job_id": refined["job_id"],
                "component": "plant", "time_basis": "UTC"}
        card = edit_card.preview("apply_generated_profile", args)
        assert card["kind"] == "series_edit" and card["complete"] is True
        rows = {r["field"]: r for r in card["rows"] if r["role"] == "typed"}
        assert rows["p_set.peak"]["old"] == pytest.approx(10.0)
        assert rows["p_set.peak"]["new"] == pytest.approx(60.0)
        assert rows["p_set.energy_mwh"]["old"] == pytest.approx(87600.0)
        notes = [r["note"] for r in card["rows"] if r["role"] == "note"]
        assert any("refined by scale_window, scale_peak_to" in t for t in notes)

        edit_card.require_current("apply_generated_profile", args, card)
        chat_tools.DISPATCHERS["apply_generated_profile"](**args)
        receipt = edit_card.receipt(card, args)
    assert receipt["outcome"] == "verified"


def test_undo_reverts_an_applied_refinement(client, project):
    gen = _generate(client, project)
    refined = client.post(f"/api/projects/{project}/profiles/refine",
                          json={"job_id": gen["job_id"], "ops": _OPS}).json()
    applied = client.post(f"/api/projects/{project}/profiles/apply",
                          json={"job_id": refined["job_id"], "component": "plant",
                                "time_basis": "UTC"})
    assert applied.status_code == 200, applied.text
    assert client.post("/api/network/undo").status_code == 200
    payload = client.get("/api/network/timeseries/loads/p_set").json()
    assert "plant" not in payload["columns"]
