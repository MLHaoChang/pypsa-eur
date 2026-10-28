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


def test_an_index_out_of_range_is_refused_plainly(client, project):
    gen = _generate(client, project)
    both = _refine(client, project, gen["job_id"], [_WINDOW, _PEAK]).json()
    resp = _refine(client, project, both["job_id"], undo_index=2)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "invalid_refine_op"
    assert "this series has 2 refinements; there is no step 3" in detail["message"]


@pytest.mark.parametrize("index", [-1, True, "1", 1.0])
def test_an_index_that_is_not_a_whole_number_from_0_is_refused(client, project, index):
    # Python's -1 would silently undo the last op; True would undo op 1.
    gen = _generate(client, project)
    both = _refine(client, project, gen["job_id"], [_WINDOW, _PEAK]).json()
    resp = client.post(f"/api/projects/{project}/profiles/refine",
                       json={"job_id": both["job_id"], "undo_index": index})
    assert resp.status_code == 422, resp.text


def test_an_unrefined_artifact_has_nothing_to_undo(client, project):
    gen = _generate(client, project)
    resp = _refine(client, project, gen["job_id"], undo_index=0)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "invalid_refine_op"
    assert "this series has no refinements to undo" in resp.json()["detail"]["message"]


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


# -- gatekeeper plan conditions (GK B, 2026-10-28) ------------------------------

_FLOOR = {"op": "set_floor", "value": 20.0}


def _stats_without_index(body):
    return [{k: v for k, v in st.items() if k != "index"} for st in body["stats"]["plant"]]


def test_undo_in_the_middle_replays_the_ops_on_both_sides(client, project):
    # call 1 = [op0, op1], call 2 = [op2]; undoing op1 walks to the root and
    # replays op0 and op2. Oracle: one refine of [op0, op2] on the root.
    gen = _generate(client, project)
    one = _refine(client, project, gen["job_id"], [_WINDOW, _FLOOR]).json()
    two = _refine(client, project, one["job_id"], [_PEAK]).json()
    undone = _refine(client, project, two["job_id"], undo_index=1).json()
    direct = _refine(client, project, gen["job_id"], [_WINDOW, _PEAK]).json()
    assert undone["based_on"] == gen["job_id"]
    assert undone["refinements"] == direct["refinements"]
    assert [st["index"] for st in undone["stats"]["plant"]] == [0, 2]
    assert _stats_without_index(undone) == _stats_without_index(direct)
    expected = _applied(client, project, direct["job_id"])
    assert client.post("/api/network/undo").status_code == 200
    assert np.array_equal(_applied(client, project, undone["job_id"]), expected)


def test_undo_with_a_refined_base_replays_only_the_later_ops(client, project):
    # call 1 = [op0], call 2 = [op1, op2]; undoing op2 starts from call 1's
    # artifact (j = 1) and replays op1 on it.
    gen = _generate(client, project)
    one = _refine(client, project, gen["job_id"], [_WINDOW]).json()
    two = _refine(client, project, one["job_id"], [_FLOOR, _PEAK]).json()
    undone = _refine(client, project, two["job_id"], undo_index=2).json()
    direct = _refine(client, project, one["job_id"], [_FLOOR]).json()
    assert undone["based_on"] == one["job_id"]
    assert undone["refinements"] == direct["refinements"]
    assert _stats_without_index(undone) == _stats_without_index(direct)
    expected = _applied(client, project, direct["job_id"])
    assert client.post("/api/network/undo").status_code == 200
    assert np.array_equal(_applied(client, project, undone["job_id"]), expected)


def test_an_undo_can_be_undone_again_and_undone_does_not_leak(client, project):
    gen = _generate(client, project)
    three = _refine(client, project, gen["job_id"], [_WINDOW, _FLOOR, _PEAK]).json()
    once = _refine(client, project, three["job_id"], undo_index=1).json()
    assert [r["op"] for r in once["refinements"]] == ["scale_window", "scale_peak_to"]
    assert all("undone" not in r for r in once["refinements"])
    twice = _refine(client, project, once["job_id"], undo_index=0).json()
    assert [r["op"] for r in twice["refinements"]] == ["scale_peak_to"]
    assert twice["undone"] == {"index": 0, "op": "scale_window"}
    direct = _refine(client, project, gen["job_id"], [_PEAK]).json()
    assert twice["refinements"] == direct["refinements"]
    # A plain refine of an undone artifact does not carry the undo along.
    more = _refine(client, project, once["job_id"], [_FLOOR]).json()
    assert "undone" not in more


def _pending_dir(client, session_ctx):
    from pathlib import Path

    return Path(session_ctx(client).storage_dir) / "profiles_pending"


def _rewrite(path, **changes):
    import json

    meta = json.loads(path.read_text())
    meta.update(changes)
    path.write_text(json.dumps(meta))


def test_a_base_whose_ops_differ_is_not_a_base(client, project, session_ctx):
    gen = _generate(client, project)
    one = _refine(client, project, gen["job_id"], [_WINDOW]).json()
    two = _refine(client, project, one["job_id"], [_PEAK]).json()
    # Forge call 1's record: the same length, another op.
    _rewrite(_pending_dir(client, session_ctx) / f"{one['job_id']}.json",
             refinements=[{**one["refinements"][0], "factor": 2.0}])
    resp = _refine(client, project, two["job_id"], undo_index=1)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "invalid_refine_op"
    assert detail["message"].startswith("the series before step 2 is no longer kept: ")


@pytest.mark.parametrize("forged", ["not-a-kept-job", "../../Other Site/profiles_pending/x",
                                    None])
def test_a_based_on_outside_this_projects_store_is_refused_unread(client, project,
                                                                 session_ctx, forged,
                                                                 monkeypatch):
    import routers.profiles as profiles_router

    gen = _generate(client, project)
    both = _refine(client, project, gen["job_id"], [_WINDOW, _PEAK]).json()
    pending = _pending_dir(client, session_ctx)
    if forged and "/" in forged:  # a genuine artifact in a sibling project's store
        other = pending.parent.parent / "Other Site" / "profiles_pending"
        other.mkdir(parents=True, exist_ok=True)
        for ext in ("nc", "json"):
            (other / f"x.{ext}").write_bytes((pending / f"{gen['job_id']}.{ext}").read_bytes())
    _rewrite(pending / f"{both['job_id']}.json", based_on=forged)
    read = []
    real = profiles_router._pending_frame
    monkeypatch.setattr(profiles_router, "_pending_frame",
                        lambda nc, meta: read.append(str(nc)) or real(nc, meta))
    resp = _refine(client, project, both["job_id"], undo_index=0)
    assert resp.status_code == 422, resp.text
    assert "is no longer kept" in resp.json()["detail"]["message"]
    assert all(str(pending.resolve()) in r for r in read[1:])
    assert not any("Other Site" in r for r in read)


def test_echoed_values_are_cut_as_tsg07_cuts_them():
    from services.profile_gen.refine import op_words

    words = op_words({"op": "move_window", "by": "9" * 40})
    assert words == "move_window by 999999999999…"
    assert op_words({"op": "x" * 40}) == "x" * 20 + "…"
