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


# Every writer that replaces a stored series drops its recorded meta, so a
# later re-generate cannot replay ops from a series that is no longer there
# (GK B plan condition 1).

def _hourly_csv(value: float) -> str:
    idx = pd.date_range("2025-01-01", periods=8760, freq="h")
    return "snapshot,plant\n" + "".join(f"{t.isoformat()},{value}\n" for t in idx)


def _assert_no_replay(client, project):
    again = _generate(client, project)
    assert again["stats"]["plant"]["peak_mw"] == pytest.approx(80.0)
    assert "replay" not in again and not again.get("refinements")


def test_a_timeseries_edit_drops_the_stored_ops(client, project):
    _refine_and_apply(client, project)
    table = client.get("/api/network/timeseries/loads/p_set").json()
    plant = table["columns"].index("plant")
    data = [[*row[:plant], 12.0, *row[plant + 1:]] for row in table["data"]]
    resp = client.put("/api/network/timeseries/loads/p_set",
                      json={"index": table["index"], "columns": table["columns"],
                            "data": data})
    assert resp.status_code == 200, resp.text
    _assert_no_replay(client, project)


def test_an_unchanged_column_of_a_timeseries_edit_keeps_its_ops(client, project):
    _refine_and_apply(client, project)
    table = client.get("/api/network/timeseries/loads/p_set").json()
    resp = client.put("/api/network/timeseries/loads/p_set", json=table)
    assert resp.status_code == 200, resp.text
    assert _generate(client, project)["replay"] == {"from": "stored", "ops": 2}


def test_a_csv_upload_drops_the_stored_ops(client, project):
    _refine_and_apply(client, project)
    resp = client.post("/api/network/timeseries/upload",
                       params={"component": "loads", "attribute": "p_set"},
                       files={"file": ("plant.csv", _hourly_csv(12.0), "text/csv")})
    assert resp.status_code == 200, resp.text
    _assert_no_replay(client, project)


def test_the_chat_demand_upload_drops_the_stored_ops(client, project, bound_ctx):
    from services import upload_service

    _refine_and_apply(client, project)
    csv = _hourly_csv(12.0).replace("snapshot,plant", "timestamp,demand")
    with bound_ctx(client):
        meta = upload_service.add_upload(project, csv.encode(), "demand.csv", "text/csv")
        result = chat_tools.apply_demand_from_excel(
            file_id=meta.file_id, time_col="timestamp", value_col="demand", load_name="plant")
    assert result["applied"] is True
    _assert_no_replay(client, project)


# Each refusal of a stored replay names its op and index (GK B condition 3).

def _store_ops(client, bound_ctx, ops):
    with bound_ctx(client) as ctx:
        ctx.user_ts_meta[("loads", "p_set", "plant")]["refinements"] = ops


def _refused(client, project):
    resp = _post_generate(client, project)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "replay_refused"
    assert detail["retry"] == {"replay_refinements": False}
    return detail


def test_an_op_recorded_for_another_target_is_named(client, project, bound_ctx):
    _refine_and_apply(client, project)
    _store_ops(client, bound_ctx, [{"op": "set_cap", "value": 0.5,
                                    "target": ["generators", "p_max_pu"]}])
    detail = _refused(client, project)
    assert (detail["op"], detail["index"]) == ("set_cap", 0)
    assert "recorded for generators/p_max_pu" in detail["message"]


def test_a_connection_op_that_disagrees_with_its_record_is_named(client, project, bound_ctx):
    _refine_and_apply(client, project)
    _store_ops(client, bound_ctx, [
        {"op": "scale_window", "hours": [5, 10], "factor": 1.1, "target": ["loads", "p_set"]},
        {"op": "scale_peak_to", "fraction": 0.5, "of": "connection", "resolved_mw": 70.0,
         "connection": {"link": "poc", "value": 100.0, "hash": "x"},
         "target": ["loads", "p_set"]}])
    detail = _refused(client, project)
    assert (detail["op"], detail["index"]) == ("scale_peak_to", 1)


def test_an_op_that_no_longer_fits_is_named(client, project, bound_ctx):
    _refine_and_apply(client, project)
    _store_ops(client, bound_ctx, [{"op": "smooth", "window_h": 1,
                                    "target": ["loads", "p_set"]}])
    detail = _refused(client, project)
    assert (detail["op"], detail["index"]) == ("smooth", 0)
    assert "op 0 (smooth) no longer fits" in detail["message"]


def test_a_coarser_regenerate_is_refused_with_the_retry(client, project, monkeypatch):
    from routers import profiles as P

    _refine_and_apply(client, project)
    real = P.engine.generate_profile

    def daily(*a, **k):
        out = real(*a, **k)
        out.meta["resolution"] = "daily"
        return out

    monkeypatch.setattr(P.engine, "generate_profile", daily)
    detail = _refused(client, project)
    assert detail["op"] is None and detail["index"] is None
    assert "hourly" in detail["message"]


# The request path keeps its refusal (GK B condition 4).

def test_a_refused_request_list_keeps_its_error_kind(client, project):
    resp = _post_generate(client, project, refinements=[
        {"op": "set_cap", "value": 0.5, "target": ["generators", "p_max_pu"]}])
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "refine_replay"
    assert "retry" not in detail and "op" not in detail


def test_an_explicit_empty_list_wins_over_the_stored_one(client, project):
    _refine_and_apply(client, project)
    plain = _generate(client, project, refinements=[])
    assert plain["stats"]["plant"]["peak_mw"] == pytest.approx(80.0)
    assert "replay" not in plain
