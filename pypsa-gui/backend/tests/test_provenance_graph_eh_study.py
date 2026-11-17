"""
guided-results 02a: "why this number" for the Guided Energy Hub study's figures (`provenance_graph/eh_study.py`).

Each figure (`expected_shortfall`, `design_cost`, `verdict`) walks to `results:study` -computed_in-> `run:study`, and
the run to its inputs, each pinned recorded-vs-current. GK B conditions W1-W7 are named where each is tested.
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import json
import threading
import time

import pypsa
import pytest

from services.adequacy.metrics import horizon_years
from services.provenance_graph import eh_study as S
from services.provenance_graph.explain import MAX_STEPS, TargetUnknown, explain_study_figure
from services.pypsa_service import PyPSAService


def _net():
    n = pypsa.Network()
    n.set_snapshots(range(3))
    n.add("Bus", "b")
    n.add("Generator", "g", bus="b", p_nom=10.0, marginal_cost=50)
    n.add("Load", "l", bus="b", p_set=5)
    return n


def _row(key="g_size", value=10.0, **over):
    row = {
        "key": key,
        "label": "Generator size",
        "value": value,
        "shown": f"{value} MW",
        "source": "default",
        "reason": None,
        "section": "5",
        "card": "assets",
        "component": "Generator",
        "entity": "g",
        "field": "p_nom",
    }
    return {**row, **over}


def _record(ctx, **payload_over):
    """A finished study as the runner leaves it, at the current revision and goal."""
    payload = {
        "target_lole_h": 1.0,
        "target_basis": "h_per_year",
        "target_unit": "h",
        "horizon_years": horizon_years(ctx.network),
        "lole_ci": [0.0001, 0.0002],
        "mc_lole_h": 0.05,
        "draws": 500,
        "seed": 0,
        "verdict": "pass",
    }
    payload.update(payload_over)
    return {
        "status": "done",
        "study": "eh_study",
        "archetype": "strong_grid",
        "network_revision": int(ctx.network_revision or 0),
        "report": {
            "mc_lole_h": 0.05,
            "cost_at_target_eur": 1234.5,
            "certified": True,
            "period_basis": "single_period",
            "sections": {"certification": {"status": "ok", "payload": payload}},
        },
    }


def _goal(ctx, **over):
    cfg = ctx.solver_state["solver_config"]
    fields = {"target_lole_h": 1.0, "mc_draws": 500, "site_archetype": "strong_grid", **over}
    ctx.solver_state["solver_config"] = dataclasses.replace(cfg, **fields)


@pytest.fixture
def study(install_network):
    install_network(_net())
    ctx = PyPSAService.get_active_context()
    _goal(ctx)
    ctx.solver_state["eh_study"] = _record(ctx)
    return ctx


def _payload(ctx):
    return ctx.solver_state["eh_study"]["report"]["sections"]["certification"]["payload"]


def _step(out, node):
    return next(s for s in out["steps"] if s["node"] == node)


def _broken_labels(out):
    return {s["broken"]["compared"].get("field") for s in out["steps"] if s["state"] == "stale" and s["broken"]}


# ── the wire ────────────────────────────────────────────────────────────────


def test_a_current_study_reads_fresh_outcome_first(study):
    out = explain_study_figure("expected_shortfall")
    assert next(iter(out)) == "outcome"  # W6: the outcome comes first
    assert out["outcome"] == {
        "state": "fresh",
        "uncompared": [],
        "complete": True,
        "truncated": False,
        "steps_total": len(out["steps"]),
    }
    assert out["target"] == {"study": "eh_study", "figure": "expected_shortfall"}
    assert out["node"] == S.figure_id("expected_shortfall")
    first = out["steps"][0]
    assert first["value"] == 0.05 and first["unit"] == "h/yr"
    ci = _step(out, S.LOLE_CI)  # lole_ci is a step
    assert ci["value"] == [0.0001, 0.0002]
    run = _step(out, S.RUN)
    assert run["via"]["edge"] == "computed_in" and run["state"] == "fresh"
    for key in ("goal/value", "goal/basis", "goal/unit", "site_archetype", "mc/draws", "mc/horizon_years",
                "mc/seed", "network_revision"):
        assert _step(out, S.input_id(key))["state"] == "fresh", key


def test_the_verdict_reads_fresh_and_carries_its_value(study):
    out = explain_study_figure("verdict")
    assert out["outcome"]["state"] == "fresh"
    assert out["steps"][0]["value"] == "pass"
    assert _step(out, S.LOLE_CI)


def test_design_cost_never_reads_fresh_on_the_revision_alone(study):
    """W1: finance/currency and the pack's prices sit outside the revision counter; neither is recorded."""
    out = explain_study_figure("design_cost")
    assert out["outcome"]["state"] == "uncompared"
    assert set(out["outcome"]["uncompared"]) == {S.input_id("finance"), S.input_id("pack_prices")}
    assert out["steps"][0]["value"] == 1234.5 and out["steps"][0]["unit"] == "EUR"
    for key in ("finance", "pack_prices"):
        step = _step(out, S.input_id(key))
        assert step["state"] == "uncompared" and "unrecorded" in step["via"]["recorded"]


def test_a_finance_edit_does_not_make_design_cost_fresh_or_move_the_revision(study):
    """W1: PUT /finance does not move the revision, so design_cost keeps naming finance uncompared."""
    revision = study.network_revision
    cfg = study.solver_state["solver_config"]
    study.solver_state["solver_config"] = dataclasses.replace(cfg, finance={"discount_rate": 0.09})
    out = explain_study_figure("design_cost")
    assert study.network_revision == revision
    assert out["outcome"]["state"] == "uncompared"
    assert S.input_id("finance") in out["outcome"]["uncompared"]


def test_an_unknown_figure_is_refused_with_its_kind(study):
    with pytest.raises(TargetUnknown) as exc:
        explain_study_figure("npv")
    assert exc.value.status == 404 and exc.value.error_kind == "why_target_unknown"


@pytest.mark.parametrize("record, status", [(None, 404), ({"status": "running"}, 409)])
def test_no_finished_study_is_refused_study_not_run(study, record, status):
    if record is None:
        study.solver_state.pop("eh_study")
    else:
        study.solver_state["eh_study"] = record
    with pytest.raises(TargetUnknown) as exc:
        explain_study_figure("expected_shortfall")
    assert exc.value.error_kind == "study_not_run" and exc.value.status == status


# ── the run: revision and edited-since ──────────────────────────────────────


def test_an_edit_since_the_study_reads_stale_naming_the_computed_in_edge(study):
    study.network_revision = int(study.network_revision or 0) + 1
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "stale"
    run = _step(out, S.RUN)
    assert run["state"] == "stale" and run["via"]["edge"] == "computed_in"
    assert run["broken"]["compared"]["field"] == "edited since the study ran"
    assert _step(out, S.input_id("network_revision"))["state"] == "stale"


@pytest.mark.parametrize("captured", ["missing", None, "3", True])
def test_a_null_missing_or_mistyped_captured_revision_reads_uncompared(study, captured):
    record = study.solver_state["eh_study"]
    if captured == "missing":
        record.pop("network_revision")
    else:
        record["network_revision"] = captured
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert S.RUN in out["outcome"]["uncompared"]
    assert S.input_id("network_revision") in out["outcome"]["uncompared"]
    assert _step(out, S.RUN)["state"] == "uncompared"
    assert "unrecorded" in _step(out, S.input_id("network_revision"))["via"]["recorded"]


# ── the goal: value, basis and unit, each its own pin ───────────────────────


def test_a_changed_goal_value_reads_stale(study):
    _goal(study, target_lole_h=2.0)
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "stale"
    assert _broken_labels(out) == {"goal value"}


def test_a_changed_goal_basis_reads_stale(study):
    _payload(study)["target_basis"] = "h_per_horizon"
    out = explain_study_figure("verdict")
    assert out["outcome"]["state"] == "stale"
    assert _broken_labels(out) == {"goal basis"}


def test_a_changed_goal_unit_reads_stale(study):
    _payload(study)["target_unit"] = "min"
    out = explain_study_figure("verdict")
    assert out["outcome"]["state"] == "stale"
    assert _broken_labels(out) == {"goal unit"}


def test_a_goal_unit_the_study_did_not_record_reads_uncompared(study):
    _payload(study).pop("target_unit")
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert out["outcome"]["uncompared"] == [S.input_id("goal/unit")]


def test_no_goal_on_both_sides_compares_equal(study):
    _goal(study, target_lole_h=None)  # strong_grid's pack has no LOLE target
    _payload(study)["target_lole_h"] = None
    assert explain_study_figure("expected_shortfall")["outcome"]["state"] == "fresh"


def test_a_goal_left_to_the_pack_reads_the_pack_s_value(study):
    _goal(study, target_lole_h=None, site_archetype="weak_flexible")
    study.solver_state["eh_study"]["archetype"] = "weak_flexible"
    _payload(study)["target_lole_h"] = 3.0  # weak_flexible's pack target
    assert explain_study_figure("expected_shortfall")["outcome"]["state"] == "fresh"


@pytest.mark.parametrize(
    "recorded, current",
    [(0.1 + 0.2, 0.3), (1.0 + 1e-12, 1.0)],
    ids=["float-sum", "tiny-delta"],
)
def test_the_goal_compares_exactly_never_after_rounding(study, recorded, current):
    """W3: equal only after rounding is not equal."""
    assert round(recorded, 9) == round(current, 9) and recorded != current
    _goal(study, target_lole_h=current)
    _payload(study)["target_lole_h"] = recorded
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "stale"
    assert _broken_labels(out) == {"goal value"}


# ── the Monte Carlo settings and the site type ──────────────────────────────


def test_changed_draws_read_stale(study):
    _goal(study, mc_draws=1000)
    assert _broken_labels(explain_study_figure("expected_shortfall")) == {"MC draws"}


def test_a_changed_horizon_reads_stale_exactly(study):
    _payload(study)["horizon_years"] = horizon_years(study.network) * (1 + 1e-12)
    assert _broken_labels(explain_study_figure("expected_shortfall")) == {"horizon years"}


def test_a_changed_seed_reads_stale_and_an_unrecorded_seed_has_no_node(study):
    _payload(study)["seed"] = 7
    assert _broken_labels(explain_study_figure("expected_shortfall")) == {"MC seed"}
    _payload(study).pop("seed")
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "fresh"
    assert all(s["node"] != S.input_id("mc/seed") for s in out["steps"])


def test_a_changed_site_type_reads_stale(study):
    _goal(study, site_archetype="off_grid")
    assert "site type" in _broken_labels(explain_study_figure("design_cost"))


@pytest.mark.parametrize("key", ["draws", "horizon_years"])
def test_an_mc_setting_the_study_did_not_record_reads_uncompared(study, key):
    _payload(study).pop(key)
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert out["outcome"]["uncompared"] == [S.input_id(f"mc/{key}")]


# ── W2: one bounded locked read ─────────────────────────────────────────────


@contextlib.contextmanager
def _held(lock):
    got, release = threading.Event(), threading.Event()

    def hold():
        with lock:
            got.set()
            release.wait(10)

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    assert got.wait(5)
    try:
        yield
    finally:
        release.set()
        t.join(5)


def test_a_busy_lock_reads_network_busy_uncompared_never_stale_and_never_waits(study):
    study.network_revision = int(study.network_revision or 0) + 1  # an edit that would read stale
    _goal(study, target_lole_h=9.0)
    with _held(study.mutation_lock):
        t0 = time.monotonic()
        out = explain_study_figure("expected_shortfall")
        assert time.monotonic() - t0 < 2
    assert out["outcome"]["state"] == "uncompared"
    assert all(s["state"] != "stale" for s in out["steps"])
    for s in out["steps"][1:]:
        if s["via"]["edge"] != "derived_from" or s["via"]["recorded"] is not None:
            assert s["via"]["recorded"] == {"unrecorded": S.BUSY}, s["node"]
    assert out["steps"][0]["value"] == 0.05  # the record's own figure is still shown


def test_the_read_never_blocks_on_the_mutation_lock(study, monkeypatch):
    calls = []
    real = study.mutation_lock

    class Spy:
        def acquire(self, blocking=True, timeout=-1):
            calls.append(blocking)
            return real.acquire(blocking=False)

        def release(self):
            real.release()

    monkeypatch.setattr(study, "mutation_lock", Spy())
    explain_study_figure("verdict")
    assert calls == [False]
    assert real.acquire(blocking=False)  # released after the read
    real.release()


# ── W4: a record of the wrong version or shape ──────────────────────────────


@pytest.mark.parametrize(
    "mutate, why",
    [
        pytest.param(lambda r: {**r, "version": 2}, "version", id="wrong-version"),
        pytest.param(lambda r: ["not", "a", "dict"], "not an object", id="non-dict"),
        pytest.param(lambda r: {**r, "status": "exploded"}, "status", id="unknown-status"),
        pytest.param(lambda r: {**r, "status": None}, "status", id="no-status"),
    ],
)
def test_a_record_of_the_wrong_version_or_shape_reads_uncompared_never_500(study, mutate, why):
    study.solver_state["eh_study"] = mutate(study.solver_state["eh_study"])
    for figure in S.FIGURES:
        out = explain_study_figure(figure)
        assert out["outcome"]["state"] == "uncompared", figure
        # Every comparison names why the record cannot be read, not a field-by-field "not recorded".
        assert why in _step(out, S.RUN)["via"]["recorded"]["unrecorded"]
        assert why in _step(out, S.input_id("goal/value"))["via"]["recorded"]["unrecorded"]
        assert all(s["state"] == "uncompared" or s["via"] is None or s["via"]["edge"] == "derived_from"
                   for s in out["steps"])
        assert all(s["state"] != "fresh" for s in out["steps"][1:] if s["via"]["recorded"] is not None)


@pytest.mark.parametrize(
    "where, key, value",
    [
        ("payload", "target_lole_h", "1"),
        ("payload", "target_lole_h", True),
        ("payload", "target_lole_h", float("nan")),
        ("payload", "target_basis", 1),
        ("payload", "target_unit", ["h"]),
        ("payload", "draws", 500.0),
        ("payload", "draws", True),
        ("payload", "horizon_years", "0.1"),
        ("payload", "seed", "0"),
        ("record", "archetype", 3),
        ("record", "network_revision", 3.0),
    ],
)
def test_a_field_of_the_wrong_type_reads_uncompared_never_a_default(study, where, key, value):
    target = _payload(study) if where == "payload" else study.solver_state["eh_study"]
    target[key] = value
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert out["outcome"]["uncompared"], key


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.update(report="a string"), id="report-not-dict"),
        pytest.param(lambda r: r["report"].update(sections=[1]), id="sections-not-dict"),
        pytest.param(lambda r: r["report"]["sections"].update(certification="ok"), id="section-not-dict"),
        pytest.param(lambda r: r["report"]["sections"]["certification"].update(payload=None), id="no-payload"),
    ],
)
def test_a_report_of_the_wrong_shape_reads_uncompared(study, mutate):
    mutate(study.solver_state["eh_study"])
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert S.input_id("goal/value") in out["outcome"]["uncompared"]


def test_a_figure_of_the_wrong_type_is_shown_as_unreadable_not_coerced(study):
    study.solver_state["eh_study"]["report"]["mc_lole_h"] = "0.05"
    out = explain_study_figure("expected_shortfall")
    assert out["steps"][0]["value"] is None
    assert out["steps"][0]["stamp"]["unreadable"]


# ── W6: a walk that compared nothing ────────────────────────────────────────


def test_a_walk_that_compared_nothing_never_reads_fresh(study):
    study.solver_state["eh_study"] = {"status": "done", "report": None}
    for figure in S.FIGURES:
        out = explain_study_figure(figure)
        assert out["outcome"]["state"] == "uncompared", figure
        assert out["outcome"]["complete"] is True and out["outcome"]["truncated"] is False


# ── the assumptions list ────────────────────────────────────────────────────


def test_each_assumption_row_is_an_input_pinned_to_its_value(study):
    study.solver_state["eh_study"]["assumptions"] = [_row()]
    out = explain_study_figure("expected_shortfall")
    node = S.input_id("assumption/g_size")
    assert out["outcome"]["state"] == "fresh"
    assert _step(out, node)["label"] == "Generator size"
    study.network.generators.loc["g", "p_nom"] = 10.0000001
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "stale"
    assert _broken_labels(out) == {"Generator size"}


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(_row(component=None), id="no-component"),
        pytest.param(_row(value=None), id="no-value"),
        pytest.param(_row(value={"x": 1}), id="value-not-scalar"),
        pytest.param("a string row", id="row-not-dict"),
    ],
)
def test_an_assumption_row_it_cannot_compare_reads_uncompared(study, row):
    study.solver_state["eh_study"]["assumptions"] = [row]
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert len(out["outcome"]["uncompared"]) == 1


def test_assumptions_that_are_not_a_list_read_uncompared(study):
    study.solver_state["eh_study"]["assumptions"] = {"g_size": 10.0}
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert out["outcome"]["uncompared"] == [S.input_id("assumptions")]


def test_two_rows_with_one_key_are_two_inputs(study):
    study.solver_state["eh_study"]["assumptions"] = [_row(), _row(value=11.0)]
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "stale"
    assert len([s for s in out["steps"] if s["node"].startswith(S.input_id("assumption/"))]) == 2


def test_a_long_walk_is_cut_at_max_steps_and_marked_truncated(study):
    study.solver_state["eh_study"]["assumptions"] = [_row(key=f"r{i}") for i in range(MAX_STEPS)]
    study.network.generators.loc["g", "p_nom"] = 12.0  # every row is stale, most past the cut
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["truncated"] is True and out["outcome"]["complete"] is False
    assert out["outcome"]["steps_total"] > MAX_STEPS == len(out["steps"])
    assert out["outcome"]["state"] == "stale"
    assert _step(out, S.RUN)  # the run step survives the cut


# ── W5: the route ───────────────────────────────────────────────────────────


URL = "/api/results/eh_study/why/{}"


@pytest.fixture
def project(client, install_network, session_ctx):
    install_network(_net())
    res = client.post("/api/projects/Why Hub", params={"force": True, "rebind": True})
    assert res.status_code == 200, res.text
    ctx = session_ctx(client)
    _goal(ctx)
    ctx.solver_state["eh_study"] = _record(ctx)
    ctx.solver_state["eh_study"]["assumptions"] = [_row()]
    return ctx


def _state_bytes(ctx) -> str:
    state = {k: v for k, v in ctx.solver_state.items()}
    return json.dumps(state, sort_keys=True, default=repr)


def _net_bytes(n) -> str:
    return json.dumps({"meta": n.meta, "generators": n.generators.to_dict()}, sort_keys=True, default=str)


@pytest.mark.parametrize("figure", S.FIGURES)
def test_the_route_is_explain_study_figure_and_writes_nothing(client, project, bound_ctx, figure):
    before = (_net_bytes(project.network), copy.deepcopy(project.solver_state["eh_study"]),
              _state_bytes(project), project.network_revision)
    res = client.get(URL.format(figure), params={"project_id": project.project_uuid})
    assert res.status_code == 200, res.text
    with bound_ctx(client):
        assert res.json() == explain_study_figure(figure)
    after = (_net_bytes(project.network), project.solver_state["eh_study"],
             _state_bytes(project), project.network_revision)
    assert after == before


def test_the_route_refuses_another_project_409(client, project):
    res = client.get(URL.format("verdict"), params={"project_id": "not-the-open-one"})
    assert res.status_code == 409
    assert res.json()["detail"]["error_kind"] == "project_not_open"


def test_the_route_refuses_an_unknown_figure_404_with_its_kind(client, project):
    res = client.get(URL.format("npv"), params={"project_id": project.project_uuid})
    assert res.status_code == 404
    assert res.json()["detail"]["error_kind"] == "why_target_unknown"


def test_the_route_refuses_no_study_with_study_not_run(client, project):
    project.solver_state.pop("eh_study")
    res = client.get(URL.format("verdict"), params={"project_id": project.project_uuid})
    assert res.status_code == 404
    assert res.json()["detail"]["error_kind"] == "study_not_run"


def test_the_route_needs_a_signed_in_reader(client, project):
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as anon:
        res = anon.get(URL.format("verdict"), params={"project_id": project.project_uuid})
    assert res.status_code == 401
