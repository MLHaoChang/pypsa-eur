"""Per-op undo of a refinement (TSG 05).

`POST /profiles/refine` with ``undo_index`` re-refines the artifact's base
(the nearest ancestor along ``based_on`` that holds only the ops before the
undone one) with the recorded op list minus that op: a new pending artifact
behind the same old → new card. A later op that no longer fits is refused,
naming the op and its index. The applied change-log line lists each op with
its parameters.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from services import change_log_service, chat_tools


def _network() -> pypsa.Network:
    n = pypsa.Network()
    idx = pd.date_range("2025-01-01", periods=8760, freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Carrier", "gas")
    n.add("Generator", "CHP", bus="site", p_nom=10.0, carrier="gas")
    n.add("Load", "plant", bus="site", p_set=10.0)
    n.add("Link", "poc", bus0="grid", bus1="site", p_nom=100.0)
    return n


@pytest.fixture
def project(client, install_network):
    install_network(_network())
    assert client.post("/api/projects/Undo Site",
                       params={"force": True, "rebind": True}).status_code == 200
    return "Undo Site"


_WINDOW = {"op": "scale_window", "hours": [5, 10], "factor": 1.3, "weekdays": [0, 1, 2, 3, 4]}
_PEAK = {"op": "scale_peak_to", "mw": 60.0}


def _generate(client, project):
    resp = client.post(f"/api/projects/{project}/profiles/loads/generate",
                       json={"archetype": "industrial", "loads": ["plant"], "year": 2025,
                             "params": {"shifts": 2, "peak_mw": 80.0,
                                        "timezone": "Europe/Berlin"}})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _refine(client, project, job_id, ops=None, undo_index=None):
    body = {"job_id": job_id, **({"ops": ops} if ops is not None else {}),
            **({"undo_index": undo_index} if undo_index is not None else {})}
    return client.post(f"/api/projects/{project}/profiles/refine", json=body)


def _applied(client, project, job_id) -> np.ndarray:
    resp = client.post(f"/api/projects/{project}/profiles/apply",
                       json={"job_id": job_id, "component": "plant", "time_basis": "UTC"})
    assert resp.status_code == 200, resp.text
    payload = client.get("/api/network/timeseries/loads/p_set").json()
    col = payload["columns"].index("plant")
    return np.array([row[col] for row in payload["data"]], dtype=float)


def test_undoing_the_second_op_is_the_series_after_the_first_alone(client, project):
    gen = _generate(client, project)
    both = _refine(client, project, gen["job_id"], [_WINDOW, _PEAK]).json()
    undone = _refine(client, project, both["job_id"], undo_index=1)
    assert undone.status_code == 200, undone.text
    body = undone.json()
    assert body["job_id"] not in (both["job_id"], gen["job_id"])
    assert body["based_on"] == gen["job_id"]
    assert [r["op"] for r in body["refinements"]] == ["scale_window"]
    assert body["undone"] == {"index": 1, "op": "scale_peak_to"}
    assert body["provenance"].endswith("user-refined")
    assert body["weeks"]  # the old → new preview, as for any refinement

    first = _refine(client, project, gen["job_id"], [_WINDOW]).json()
    expected = _applied(client, project, first["job_id"])
    assert client.post("/api/network/undo").status_code == 200
    got = _applied(client, project, body["job_id"])
    np.testing.assert_allclose(got, expected)
    assert got.max() != pytest.approx(60.0)


def test_undoing_the_first_op_replays_the_second_on_the_base(client, project):
    gen = _generate(client, project)
    both = _refine(client, project, gen["job_id"], [_WINDOW, _PEAK]).json()
    body = _refine(client, project, both["job_id"], undo_index=0).json()
    assert [r["op"] for r in body["refinements"]] == ["scale_peak_to"]
    assert body["stats"]["plant"][0]["index"] == 1   # named by its recorded index
    assert body["stats"]["plant"][0]["peak_after"] == pytest.approx(60.0)

    second = _refine(client, project, gen["job_id"], [_PEAK]).json()
    expected = _applied(client, project, second["job_id"])
    assert client.post("/api/network/undo").status_code == 200
    np.testing.assert_allclose(_applied(client, project, body["job_id"]), expected)


def test_undo_walks_back_past_a_refined_base(client, project):
    # Two refine calls: the second artifact's base holds op 0 already, so
    # undoing op 0 starts from the generated series.
    gen = _generate(client, project)
    one = _refine(client, project, gen["job_id"], [_WINDOW]).json()
    two = _refine(client, project, one["job_id"], [_PEAK]).json()
    assert [r["op"] for r in two["refinements"]] == ["scale_window", "scale_peak_to"]

    no_first = _refine(client, project, two["job_id"], undo_index=0).json()
    assert no_first["based_on"] == gen["job_id"]
    assert [r["op"] for r in no_first["refinements"]] == ["scale_peak_to"]

    # Undoing the last op leaves exactly the base: its series, its ops.
    no_second = _refine(client, project, two["job_id"], undo_index=1).json()
    assert no_second["based_on"] == one["job_id"]
    assert no_second["refinements"] == one["refinements"]


def test_an_undo_that_leaves_a_later_op_invalid_is_refused_naming_it(client, project):
    gen = client.post(f"/api/projects/{project}/profiles/availability/generate",
                      json={"kind": "maintenance", "components": ["CHP"], "year": 2025,
                            "params": {"windows": [{"from": "2025-12-10", "to": "2025-12-15",
                                                    "available": 0.0}]}})
    assert gen.status_code == 200, gen.text
    moves = [{"op": "move_window", "by": "-30d"}, {"op": "move_window", "by": "40d"}]
    both = _refine(client, project, gen.json()["job_id"], moves)
    assert both.status_code == 200, both.text
    resp = _refine(client, project, both.json()["job_id"], undo_index=0)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "refine_replay"
    assert detail["op"] == "move_window" and detail["index"] == 1
    assert detail["message"].startswith("op 1 (move_window) no longer fits")


@pytest.mark.parametrize("index, says", [(2, "has 2 edits; there is no edit 3"),
                                         (-1, "has 2 edits; there is no edit 0")])
def test_an_index_out_of_range_is_refused_plainly(client, project, index, says):
    gen = _generate(client, project)
    both = _refine(client, project, gen["job_id"], [_WINDOW, _PEAK]).json()
    resp = _refine(client, project, both["job_id"], undo_index=index)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "invalid_refine_op"
    assert says in detail["message"]


def test_an_unrefined_artifact_has_nothing_to_undo(client, project):
    gen = _generate(client, project)
    resp = _refine(client, project, gen["job_id"], undo_index=0)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "invalid_refine_op"
    assert "has no edits to undo" in resp.json()["detail"]["message"]


def test_ops_and_undo_together_or_neither_are_refused(client, project):
    gen = _generate(client, project)
    assert _refine(client, project, gen["job_id"]).status_code == 422
    assert _refine(client, project, gen["job_id"], [_PEAK], undo_index=0).status_code == 422


def test_the_applied_change_log_line_lists_each_op_with_its_parameters(client, project):
    gen = _generate(client, project)
    both = _refine(client, project, gen["job_id"], [_WINDOW, _PEAK]).json()
    _applied(client, project, both["job_id"])
    line = next(e["description"] for e in change_log_service.get_all()
                if e["action"] == "timeseries" and "refined by" in e["description"])
    assert line.endswith("refined by scale_window ×1.3, 05:00–10:00 Mon–Fri; "
                         "scale_peak_to 60 MW")


def test_the_copilot_undoes_an_op_through_the_refine_tool(client, project, bound_ctx):
    gen = _generate(client, project)
    both = _refine(client, project, gen["job_id"], [_WINDOW, _PEAK]).json()
    with bound_ctx(client):
        undone = chat_tools.DISPATCHERS["refine_generated_profile"](
            project_id=project, job_id=both["job_id"], undo_index=1)
    assert [r["op"] for r in undone["refinements"]] == ["scale_window"]
    assert undone["undone"]["index"] == 1
