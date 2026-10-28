"""Re-generating a refined series replays its stored refinements (TSG 06).

A generate for a target whose applied series records `refinements` from the
same generator replays them by default, through `refine.replay`; the caller
opts out with `replay_refinements: false`, an explicit `refinements` list
wins, an upload or another archetype never replays, and a refusal names the
op and its index with the retry that drops them.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services import chat_tools


def _network() -> pypsa.Network:
    n = pypsa.Network()
    idx = pd.date_range("2025-01-01", periods=8760, freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Load", "plant", bus="site", p_set=10.0)
    n.add("Load", "office", bus="site", p_set=5.0)
    n.add("Link", "poc", bus0="grid", bus1="site", p_nom=100.0)
    return n


@pytest.fixture
def project(client, install_network):
    install_network(_network())
    assert client.post("/api/projects/Replay Site",
                       params={"force": True, "rebind": True}).status_code == 200
    return "Replay Site"


_PARAMS = {"shifts": 2, "peak_mw": 80.0, "timezone": "Europe/Berlin"}
_OPS = [{"op": "scale_window", "hours": [5, 10], "factor": 1.3},
        {"op": "scale_peak_to", "mw": 60.0}]


def _post_generate(client, project, *, loads=("plant",), archetype="industrial",
                   params=None, **extra):
    body = {"archetype": archetype, "loads": list(loads), "year": 2025,
            "params": dict(params or _PARAMS), **extra}
    return client.post(f"/api/projects/{project}/profiles/loads/generate", json=body)


def _generate(client, project, **extra):
    resp = _post_generate(client, project, **extra)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _apply(client, project, job_id, component="plant"):
    resp = client.post(f"/api/projects/{project}/profiles/apply",
                       json={"job_id": job_id, "component": component,
                             "time_basis": "UTC"})
    assert resp.status_code == 200, resp.text


def _refine_and_apply(client, project, component="plant"):
    gen = _generate(client, project, loads=(component,))
    refined = client.post(f"/api/projects/{project}/profiles/refine",
                          json={"job_id": gen["job_id"], "ops": _OPS})
    assert refined.status_code == 200, refined.text
    _apply(client, project, refined.json()["job_id"], component)


def test_regenerating_a_refined_load_replays_its_stored_ops(client, project):
    _refine_and_apply(client, project)
    again = _generate(client, project)
    assert again["stats"]["plant"]["peak_mw"] == pytest.approx(60.0)
    assert [r["op"] for r in again["refinements"]] == ["scale_window", "scale_peak_to"]
    assert again["replay"] == {"from": "stored", "ops": 2}
    assert again["provenance"].endswith("user-refined")


def test_the_opt_out_generates_the_unrefined_series(client, project):
    _refine_and_apply(client, project)
    plain = _generate(client, project, replay_refinements=False)
    assert plain["stats"]["plant"]["peak_mw"] == pytest.approx(80.0)
    assert not plain.get("refinements")
    assert "replay" not in plain


def test_an_explicit_list_wins_over_the_stored_one(client, project):
    _refine_and_apply(client, project)
    given = [{"op": "scale_peak_to", "mw": 50.0}]
    again = _generate(client, project, refinements=given)
    assert again["stats"]["plant"]["peak_mw"] == pytest.approx(50.0)
    assert again["replay"] == {"from": "request", "ops": 1}


def test_an_unrefined_load_generates_as_before(client, project):
    _apply(client, project, _generate(client, project)["job_id"])
    again = _generate(client, project)
    assert again["stats"]["plant"]["peak_mw"] == pytest.approx(80.0)
    assert "replay" not in again


def test_another_archetype_does_not_replay(client, project):
    _refine_and_apply(client, project)
    other = _generate(client, project, archetype="commercial",
                      params={"peak_mw": 80.0, "timezone": "Europe/Berlin"})
    assert not other.get("refinements")
    assert "replay" not in other


def test_a_refined_upload_does_not_replay(client, project, bound_ctx):
    _refine_and_apply(client, project)
    with bound_ctx(client) as ctx:
        meta = ctx.user_ts_meta[("loads", "p_set", "plant")]
        meta.pop("archetype")
        meta["source"] = "upload"
    again = _generate(client, project)
    assert again["stats"]["plant"]["peak_mw"] == pytest.approx(80.0)
    assert "replay" not in again


def test_loads_with_different_stored_ops_are_refused_together(client, project):
    _refine_and_apply(client, project)
    _apply(client, project, _generate(client, project, loads=("office",))["job_id"], "office")
    resp = _post_generate(client, project, loads=("plant", "office"))
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "replay_mixed"
    assert "plant" in detail["message"] and "office" in detail["message"]
    assert detail["retry"] == {"replay_refinements": False}
    # The opt-out generates both, unrefined.
    both = _generate(client, project, loads=("plant", "office"), replay_refinements=False)
    assert both["points"] == ["plant", "office"]


def test_a_replay_refusal_names_the_op_and_its_index(client, project, bound_ctx):
    _refine_and_apply(client, project)
    # A list recorded on a snapshot-window series: its window-basis energy op
    # no longer fits the full year the archetype regenerates.
    with bound_ctx(client) as ctx:
        meta = ctx.user_ts_meta[("loads", "p_set", "plant")]
        meta["refinements"] = [*meta["refinements"],
                               {"op": "rescale_energy", "mwh": 100.0, "basis": "window",
                                "target": ["loads", "p_set"]}]
    resp = _post_generate(client, project)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "replay_refused"
    assert detail["op"] == "rescale_energy" and detail["index"] == 2
    assert "op 2 (rescale_energy)" in detail["message"]
    assert detail["retry"] == {"replay_refinements": False}


def test_the_chat_tool_replays_and_opts_out(client, project, bound_ctx):
    _refine_and_apply(client, project)
    with bound_ctx(client):
        again = chat_tools.DISPATCHERS["generate_load_profile"](
            project_id=project, archetype="industrial", loads=["plant"], year=2025,
            params=dict(_PARAMS))
        plain = chat_tools.DISPATCHERS["generate_load_profile"](
            project_id=project, archetype="industrial", loads=["plant"], year=2025,
            params=dict(_PARAMS), replay_refinements=False)
    assert again["stats"]["plant"]["peak_mw"] == pytest.approx(60.0)
    assert again["replay"] == {"from": "stored", "ops": 2}
    assert plain["stats"]["plant"]["peak_mw"] == pytest.approx(80.0)
