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
import math
import threading
import time
from pathlib import Path

import pypsa
import pytest

from services.adequacy.metrics import horizon_years
from services.provenance_graph import eh_study as S
from services.provenance_graph.explain import (
    MAX_STEPS,
    TargetUnknown,
    explain_study_figure,
)
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
        "cov_target": 0.05,
        "verdict": "pass",
    }
    payload.update(payload_over)
    return {
        "status": "done",
        "study": "eh_study",
        "archetype": "strong_grid",
        "network_revision": int(ctx.network_revision or 0),
        # The run choices, as the runner records them for the request Guided sends now.
        "pack_overrides": {"target_lole_h": 1.0, "certification_metric": "mc_lole"},
        "stages": None,
        "budget_solves": 30,
        "dsr_buses": None,
        "dtc_config": None,
        "dtc_attribution": None,
        "assumptions": [],
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
    fields = {
        "target_lole_h": 1.0,
        "mc_draws": 500,
        "site_archetype": "strong_grid",
        **over,
    }
    ctx.solver_state["solver_config"] = dataclasses.replace(cfg, **fields)


@pytest.fixture
def study(install_network):
    install_network(_net())
    ctx = PyPSAService.get_active_context()
    _goal(ctx)
    ctx.solver_state["eh_study"] = _record(ctx)
    return ctx


def _payload(ctx):
    return ctx.solver_state["eh_study"]["report"]["sections"]["certification"][
        "payload"
    ]


def _step(out, node):
    return next(s for s in out["steps"] if s["node"] == node)


def _broken_labels(out):
    return {
        s["broken"]["compared"].get("field")
        for s in out["steps"]
        if s["state"] == "stale" and s["broken"]
    }


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
    assert first["stamp"] is None
    ci = _step(out, S.LOLE_CI)  # lole_ci is a step
    assert ci["value"] == [0.0001, 0.0002]
    run = _step(out, S.RUN)
    assert run["via"]["edge"] == "computed_in" and run["state"] == "fresh"
    for key in (
        "goal/value",
        "goal/basis",
        "goal/unit",
        "site_archetype",
        "mc/draws",
        "mc/horizon_years",
        "mc/seed",
        "network_revision",
    ):
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
    assert set(out["outcome"]["uncompared"]) == {
        S.input_id("finance"),
        S.input_id("pack_prices"),
    }
    assert out["steps"][0]["value"] == 1234.5 and out["steps"][0]["unit"] == "EUR"
    assert out["steps"][0]["stamp"] == {"period_basis": "single_period"}
    for key in ("finance", "pack_prices"):
        step = _step(out, S.input_id(key))
        assert step["state"] == "uncompared" and "unrecorded" in step["via"]["recorded"]


def test_a_finance_edit_does_not_make_design_cost_fresh_or_move_the_revision(study):
    """W1: PUT /finance does not move the revision, so design_cost keeps naming finance uncompared."""
    revision = study.network_revision
    cfg = study.solver_state["solver_config"]
    study.solver_state["solver_config"] = dataclasses.replace(
        cfg, finance={"discount_rate": 0.09}
    )
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
    assert _step(out, S.RUN)["via"]["recorded"] == {
        "unrecorded": "the study recorded no network revision"
    }
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
    assert _broken_labels(explain_study_figure("expected_shortfall")) == {
        "horizon years"
    }


def test_a_changed_seed_reads_stale(study):
    _payload(study)["seed"] = 7
    assert _broken_labels(explain_study_figure("expected_shortfall")) == {"MC seed"}


@pytest.mark.parametrize("seed", ["missing", None], ids=["missing", "null"])
def test_a_seed_the_study_did_not_record_reads_not_recorded_never_current(study, seed):
    """02b W3: the seed keeps its node, reads "not recorded" and the figure is not current."""
    if seed == "missing":
        _payload(study).pop("seed")
    else:
        _payload(study)["seed"] = seed
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert out["outcome"]["uncompared"] == [S.input_id("mc/seed")]
    step = _step(out, S.input_id("mc/seed"))
    assert step["state"] == "uncompared" and step["value"] is None
    assert step["via"]["recorded"]["unrecorded"] == "seed: not recorded"


# ── 02b W1: draws and seed as the runner resolves them: the request's mc, else the pack ──


def _request_now(monkeypatch, **request):
    from services import flow_service

    monkeypatch.setattr(
        flow_service,
        "_request_for",
        lambda t, c=None: {"archetype": "strong_grid", **request},
    )


def _pack_now(monkeypatch, **over):
    from services.adequacy import eh_study_runner

    real = eh_study_runner._PACK_FACTORY["strong_grid"]
    monkeypatch.setitem(
        eh_study_runner._PACK_FACTORY,
        "strong_grid",
        lambda: real().model_copy(update=over),
    )


@pytest.mark.parametrize(
    "mc, payload",
    [
        ({"draws": 800}, {"draws": 800}),
        ({"seed": 3}, {"seed": 3}),
        (
            {"draws": 800, "seed": 3, "cov_target": 0.02},
            {"draws": 800, "seed": 3, "cov_target": 0.02},
        ),
    ],
    ids=["draws", "seed", "all"],
)
def test_a_request_mc_override_equal_to_the_record_reads_current(
    study, monkeypatch, mc, payload
):
    _request_now(monkeypatch, mc=mc)
    _payload(study).update(payload)
    assert explain_study_figure("expected_shortfall")["outcome"]["state"] == "fresh"


@pytest.mark.parametrize(
    "mc, label",
    [({"draws": 800}, "MC draws"), ({"seed": 3}, "MC seed")],
    ids=["draws", "seed"],
)
def test_a_request_mc_override_other_than_the_record_reads_stale(
    study, monkeypatch, mc, label
):
    _request_now(monkeypatch, mc=mc)
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "stale"
    assert _broken_labels(out) == {label}


def test_the_goal_s_draws_with_no_request_mc_read_as_the_goal(study):
    _goal(study, mc_draws=1000)
    _payload(study)["draws"] = 1000
    assert explain_study_figure("expected_shortfall")["outcome"]["state"] == "fresh"


def test_a_request_mc_draws_wins_over_the_goal(study, monkeypatch):
    _goal(study, mc_draws=1000)
    _request_now(monkeypatch, mc={"draws": 800})
    _payload(study)["draws"] = 800
    assert explain_study_figure("expected_shortfall")["outcome"]["state"] == "fresh"


def test_with_neither_request_mc_nor_goal_draws_and_seed_read_as_the_pack(
    study, monkeypatch
):
    _goal(study, mc_draws=None)
    _pack_now(monkeypatch, mc_draws=700, mc_seed=11)
    _payload(study).update(draws=700, seed=11)
    assert explain_study_figure("expected_shortfall")["outcome"]["state"] == "fresh"
    _payload(study).update(draws=500, seed=0)
    assert _broken_labels(explain_study_figure("expected_shortfall")) == {
        "MC draws",
        "MC seed",
    }


def test_with_no_request_seed_the_seed_reads_as_the_pack_even_beside_goal_draws(
    study, monkeypatch
):
    _pack_now(monkeypatch, mc_seed=11)
    _payload(study)["seed"] = 11
    assert explain_study_figure("expected_shortfall")["outcome"]["state"] == "fresh"


# ── 02b W2: an mc the runner refuses leaves nothing to compare the MC settings with ──

_MC_INPUTS = [S.input_id(f"mc/{k}") for k in ("draws", "seed", "cov_target")]


@pytest.mark.parametrize(
    "mc, words",
    [
        ("lots", "not an object"),
        ([500], "not an object"),
        ({"draws": 2001}, "at most 2000"),
        ({"draws": 0}, "one the study refuses"),
        ({"seed": -1}, "one the study refuses"),
        ({"cov_target": 2}, "one the study refuses"),
        ({"draws": "many"}, "one the study refuses"),
        ({"bogus": 1}, "one the study refuses"),
    ],
    ids=[
        "text",
        "list",
        "over-max-draws",
        "zero-draws",
        "negative-seed",
        "cov-over-1",
        "draws-text",
        "unknown-key",
    ],
)
def test_an_mc_the_runner_refuses_reads_the_mc_settings_uncompared(
    study, monkeypatch, mc, words
):
    _request_now(monkeypatch, mc=mc)
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert sorted(out["outcome"]["uncompared"]) == sorted(_MC_INPUTS)
    for node in _MC_INPUTS:
        step = _step(out, node)
        assert step["state"] == "uncompared"
        assert words in step["via"]["recorded"]["unrecorded"]
    # Every other input still compares: only the MC settings lost their current value.
    assert all(
        s["state"] == "fresh"
        for s in out["steps"]
        if s["node"].startswith(S.input_id("run/"))
    )


def test_the_mc_draw_cap_is_the_runner_s_own(study, monkeypatch):
    from services.adequacy import mc as _mc

    _request_now(monkeypatch, mc={"draws": _mc.MAX_DRAWS})
    _payload(study)["draws"] = _mc.MAX_DRAWS
    assert explain_study_figure("expected_shortfall")["outcome"]["state"] == "fresh"


# ── 02b W4: a record the real writer produced ───────────────────────────────


@pytest.mark.live_solve
@pytest.mark.parametrize(
    "mc", [None, {"draws": 600, "seed": 7}], ids=["pack", "override"]
)
def test_a_record_the_study_writer_produced_reads_its_mc_settings_current(
    install_network, monkeypatch, mc
):
    """The certification payload as `run_eh_study` writes it (int seed, eh_study.py `_mc_certify`), with the mc
    the runner passes for the same request, reads fresh."""
    from tests.test_energy_hub_mc_certify import _cert, _cert_network, _pack, _run

    kwargs = {f"mc_{k}": v for k, v in (mc or {"draws": 500}).items()}
    written = _cert(_run(_cert_network(), _pack(), **kwargs)).payload
    assert isinstance(written["seed"], int) and not isinstance(written["seed"], bool)

    install_network(_net())
    ctx = PyPSAService.get_active_context()
    _goal(ctx)
    ctx.solver_state["eh_study"] = _record(ctx)
    _payload(ctx).update({k: written[k] for k in ("draws", "seed", "cov_target")})
    if mc is not None:
        _request_now(monkeypatch, mc=mc)
    out = explain_study_figure("expected_shortfall")
    assert {_step(out, n)["state"] for n in _MC_INPUTS} == {"fresh"}
    assert out["outcome"]["state"] == "fresh"


def test_a_changed_site_type_reads_stale(study):
    _goal(study, site_archetype="off_grid")
    assert "site type" in _broken_labels(explain_study_figure("design_cost"))


@pytest.mark.parametrize("key", ["draws", "horizon_years"])
def test_an_mc_setting_the_study_did_not_record_reads_uncompared(study, key):
    _payload(study).pop(key)
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert out["outcome"]["uncompared"] == [S.input_id(f"mc/{key}")]


# ── the run choices: none moves the revision, so each is pinned ─────────────

_CHOICES = {
    "pack_overrides": (
        "record",
        {
            "target_lole_h": 1.0,
            "certification_metric": "mc_lole",
            "levers": {"sizing": False},
        },
        "pack overrides beyond the goal",
    ),
    "stages": ("record", ["apply_pack", "ens_solve", "assemble"], "stages"),
    "budget_solves": ("record", 12, "solve budget"),
    "dsr_buses": ("record", ["b"], "demand-response buses"),
    "dtc_config": ("record", {"attribution": "aggregate"}, "DtC config"),
    "dtc_attribution": ("record", "aggregate", "DtC attribution"),
    "cov_target": ("payload", 0.02, "MC convergence target"),
}


@pytest.mark.parametrize("key", list(_CHOICES))
def test_a_run_choice_other_than_today_s_request_reads_stale_without_a_revision_bump(
    study, key
):
    where, value, label = _CHOICES[key]
    target = _payload(study) if where == "payload" else study.solver_state["eh_study"]
    revision = study.network_revision
    target[key] = value
    out = explain_study_figure("expected_shortfall")
    assert (
        study.network_revision == revision
    )  # a run choice is no edit: the revision cannot catch it
    assert out["outcome"]["state"] == "stale"
    assert _broken_labels(out) == {label}


@pytest.mark.parametrize(
    "request_now, label",
    [
        (
            {"pack_overrides": {"levers": {"sizing": False}}},
            "pack overrides beyond the goal",
        ),
        ({"stages": ["apply_pack", "ens_solve", "assemble"]}, "stages"),
        ({"dtc_attribution": "aggregate"}, "DtC attribution"),
    ],
)
def test_a_changed_request_guided_would_send_now_reads_stale(
    study, monkeypatch, request_now, label
):
    from services import flow_service

    monkeypatch.setattr(
        flow_service,
        "_request_for",
        lambda t, c=None: {"archetype": "strong_grid", **request_now},
    )
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "stale"
    assert label in _broken_labels(out)


@pytest.mark.parametrize("key", list(_CHOICES))
def test_a_run_choice_the_study_did_not_record_reads_uncompared(study, key):
    where, _value, _label = _CHOICES[key]
    (_payload(study) if where == "payload" else study.solver_state["eh_study"]).pop(key)
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert len(out["outcome"]["uncompared"]) == 1
    step = next(s for s in out["steps"] if s["node"] == out["outcome"]["uncompared"][0])
    assert step["via"]["recorded"]["unrecorded"].startswith("the study did not record")


@pytest.mark.parametrize(
    "key, value",
    [
        ("stages", "all"),
        ("budget_solves", 30.0),
        ("dsr_buses", "b"),
        ("dtc_config", ["x"]),
        ("pack_overrides", "x"),
        ("dtc_attribution", 1),
    ],
)
def test_a_run_choice_of_the_wrong_type_reads_uncompared(study, key, value):
    study.solver_state["eh_study"][key] = value
    assert (
        explain_study_figure("expected_shortfall")["outcome"]["state"] == "uncompared"
    )


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
    study.solver_state["eh_study"]["assumptions"] = [_row()]
    study.network_revision = (
        int(study.network_revision or 0) + 1
    )  # an edit that would read stale
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
        pytest.param(lambda r: {**r, "version": True}, "version", id="bool-version"),
        pytest.param(lambda r: ["not", "a", "dict"], "not an object", id="non-dict"),
        pytest.param(
            lambda r: {**r, "status": "exploded"}, "status", id="unknown-status"
        ),
        pytest.param(lambda r: {**r, "status": None}, "status", id="no-status"),
    ],
)
def test_a_record_of_the_wrong_version_or_shape_reads_uncompared_never_500(
    study, mutate, why
):
    study.solver_state["eh_study"] = mutate(study.solver_state["eh_study"])
    for figure in S.FIGURES:
        out = explain_study_figure(figure)
        assert out["outcome"]["state"] == "uncompared", figure
        # Every comparison names why the record cannot be read, not a field-by-field "not recorded".
        assert why in _step(out, S.RUN)["via"]["recorded"]["unrecorded"]
        assert (
            why in _step(out, S.input_id("goal/value"))["via"]["recorded"]["unrecorded"]
        )
        assert all(
            s["state"] == "uncompared"
            or s["via"] is None
            or s["via"]["edge"] == "derived_from"
            for s in out["steps"]
        )
        assert all(
            s["state"] != "fresh"
            for s in out["steps"][1:]
            if s["via"]["recorded"] is not None
        )


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
        ("payload", "horizon_years", None),
        ("payload", "seed", "0"),
        ("record", "archetype", 3),
        ("record", "network_revision", 3.0),
    ],
)
def test_a_field_of_the_wrong_type_reads_uncompared_never_a_default(
    study, where, key, value
):
    target = _payload(study) if where == "payload" else study.solver_state["eh_study"]
    target[key] = value
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert out["outcome"]["uncompared"], key


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.update(report="a string"), id="report-not-dict"),
        pytest.param(
            lambda r: r["report"].update(sections=[1]), id="sections-not-dict"
        ),
        pytest.param(
            lambda r: r["report"]["sections"].update(certification="ok"),
            id="section-not-dict",
        ),
        pytest.param(
            lambda r: r["report"]["sections"]["certification"].update(payload=None),
            id="no-payload",
        ),
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


@pytest.mark.parametrize("ci", [[1.0, 2.0, 3.0], ["a", "b"], "0.1-0.2"])
def test_a_lole_ci_of_the_wrong_shape_is_shown_as_nothing(study, ci):
    _payload(study)["lole_ci"] = ci
    assert _step(explain_study_figure("expected_shortfall"), S.LOLE_CI)["value"] is None


def test_a_null_figure_is_named_not_coerced(study):
    study.solver_state["eh_study"]["report"]["mc_lole_h"] = None
    out = explain_study_figure("expected_shortfall")
    assert out["steps"][0]["value"] is None and out["steps"][0]["stamp"] == {
        "unreadable": "mc_lole_h: null"
    }


def test_a_current_input_that_cannot_be_read_reads_stale(study, monkeypatch):
    from services.adequacy import metrics

    def boom(*_a, **_k):
        raise RuntimeError("no snapshots")

    monkeypatch.setattr(metrics, "horizon_years", boom)
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "stale"
    step = _step(out, S.input_id("mc/horizon_years"))
    assert step["broken"]["reason"] == "cannot be read now: no snapshots"


def test_the_record_is_read_under_the_solver_state_lock(study, monkeypatch):
    entered = []
    real = study.solver_state_lock

    class Spy:
        def __enter__(self):
            entered.append(True)
            return real.__enter__()

        def __exit__(self, *exc):
            return real.__exit__(*exc)

    monkeypatch.setattr(study, "solver_state_lock", Spy())
    explain_study_figure("verdict")
    assert entered == [True]


def test_a_context_with_no_locks_is_read_with_nothing_to_wait_for(study):
    import types

    bare = types.SimpleNamespace(
        network=study.network,
        solver_state=study.solver_state,
        network_revision=study.network_revision,
    )
    assert (
        explain_study_figure("expected_shortfall", ctx=bare)["outcome"]["state"]
        == "fresh"
    )
    with pytest.raises(
        TargetUnknown
    ) as exc:  # no solver state at all: no study, never a 500
        explain_study_figure(
            "expected_shortfall", ctx=types.SimpleNamespace(network=study.network)
        )
    assert exc.value.error_kind == "study_not_run"


def test_a_text_assumption_row_compares_exactly(study):
    study.network.generators.loc["g", "carrier"] = "solar"
    study.solver_state["eh_study"]["assumptions"] = [
        _row(key="g_carrier", value="solar", field="carrier")
    ]
    assert explain_study_figure("expected_shortfall")["outcome"]["state"] == "fresh"
    study.network.generators.loc["g", "carrier"] = "wind"
    assert explain_study_figure("expected_shortfall")["outcome"]["state"] == "stale"


# ── W6: a walk that compared nothing ────────────────────────────────────────


def test_a_walk_that_compared_nothing_never_reads_fresh(study):
    study.solver_state["eh_study"] = {"status": "done", "report": None}
    for figure in S.FIGURES:
        out = explain_study_figure(figure)
        assert out["outcome"]["state"] == "uncompared", figure
        assert (
            out["outcome"]["complete"] is True and out["outcome"]["truncated"] is False
        )


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
        pytest.param(_row(component=""), id="empty-component"),
        pytest.param(_row(field=""), id="empty-field"),
        pytest.param(_row(entity=""), id="empty-entity"),
        pytest.param(_row(value=None), id="no-value"),
        pytest.param(_row(value={"x": 1}), id="value-not-scalar"),
        pytest.param(_row(value=float("nan")), id="value-nan"),
        pytest.param("a string row", id="row-not-dict"),
    ],
)
def test_an_assumption_row_it_cannot_compare_reads_uncompared(study, row):
    study.solver_state["eh_study"]["assumptions"] = [row]
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert len(out["outcome"]["uncompared"]) == 1
    assert "assumption row: " in json.dumps(_step(out, out["outcome"]["uncompared"][0]))


def test_assumptions_that_are_not_a_list_read_uncompared(study):
    study.solver_state["eh_study"]["assumptions"] = {"g_size": 10.0}
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert out["outcome"]["uncompared"] == [S.input_id("assumptions")]


def test_a_row_with_no_key_or_label_is_named_by_its_place(study):
    study.solver_state["eh_study"]["assumptions"] = [_row(key=None, label=None)]
    step = _step(
        explain_study_figure("expected_shortfall"), S.input_id("assumption/#0")
    )
    assert step["label"] == "#0" and step["state"] == "fresh"


def test_two_rows_with_one_key_are_two_inputs(study):
    study.solver_state["eh_study"]["assumptions"] = [_row(), _row(value=11.0)]
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "stale"
    assert (
        len(
            [s for s in out["steps"] if s["node"].startswith(S.input_id("assumption/"))]
        )
        == 2
    )


def test_a_long_walk_is_cut_at_max_steps_and_marked_truncated(study):
    study.solver_state["eh_study"]["assumptions"] = [
        _row(key=f"r{i}") for i in range(MAX_STEPS)
    ]
    study.network.generators.loc["g", "p_nom"] = (
        12.0  # every row is stale, most past the cut
    )
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["truncated"] is True and out["outcome"]["complete"] is False
    assert out["outcome"]["steps_total"] > MAX_STEPS == len(out["steps"])
    assert out["outcome"]["state"] == "stale"
    assert _step(out, S.RUN)  # the run step survives the cut


# ── #1645's row shapes (GK B 65 B1) ─────────────────────────────────────────

# `run_assumptions(_assumed_net(), {}, None)` from #1645 (services/adequacy/run_assumptions.py @ 8995402d), saved
# as it returned: two goal rows (no component), a carrier outage rate joining two units, a repair time and a
# basis left blank on a unit with its own rate, a storage unit's carrier outage rate, and a lossless conversion.
ASSUMED_ROWS = json.loads(
    (Path(__file__).parent / "fixtures" / "eh_study_run_assumptions.json").read_text()
)


def _assumed_net():
    n = pypsa.Network()
    n.set_snapshots(range(3))
    n.add("Bus", "b", carrier="AC")
    n.add("Bus", "h", carrier="heat")
    n.add("Generator", "d1", bus="b", carrier="gas", p_nom=10.0)
    n.add("Generator", "d2", bus="b", carrier="gas", p_nom=5.0)
    n.add("Generator", "o1", bus="b", carrier="oil", p_nom=3.0)
    n.add("Link", "boiler", bus0="b", bus1="h", p_nom=4.0)
    n.add("StorageUnit", "bat", bus="b", carrier="battery", p_nom=2.0, max_hours=2)
    n.add("Load", "l", bus="b", p_set=5)
    n.generators["outage_rate_value"] = [math.nan, math.nan, 0.05]
    n.generators["mttr_hours"] = math.nan
    n.generators["outage_rate_basis"] = ""
    return n


@pytest.fixture
def assumed(install_network):
    """A study on the fixture's network, recording #1645's rows for it."""
    install_network(_assumed_net())
    ctx = PyPSAService.get_active_context()
    _goal(ctx)
    ctx.solver_state["eh_study"] = _record(ctx)
    ctx.solver_state["eh_study"]["assumptions"] = copy.deepcopy(ASSUMED_ROWS)
    return ctx


def _row_steps(out) -> dict[str, str]:
    return {
        s["label"]: s["state"]
        for s in out["steps"]
        if s["node"].startswith(S.input_id("assumption/"))
    }


def test_real_run_assumptions_rows_read_fresh_when_unchanged(assumed):
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "fresh"
    # One pin per row, one per unit of a joined row; the goal rows compare with the current goal (GK B 65).
    assert _row_steps(out) == {
        "Simulation runs per reliability check": "fresh",
        "Site type": "fresh",
        "Outage rate of gas generating units (d1, d2): d1": "fresh",
        "Outage rate of gas generating units (d1, d2): d2": "fresh",
        "Repair time of oil generating units (o1)": "fresh",
        "Outage rate basis of generating units (o1)": "fresh",
        "Outage rate of battery storage units (bat)": "fresh",
        "Conversion efficiency of boiler": "fresh",
    }


@pytest.mark.parametrize(
    "over, broken",
    [
        pytest.param(
            {"mc_draws": 1000}, "Simulation runs per reliability check", id="draws"
        ),
        pytest.param({"site_archetype": "off_grid"}, "Site type", id="site-type"),
    ],
)
def test_a_goal_change_breaks_its_goal_row(assumed, over, broken):
    _goal(assumed, **over)
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "stale"
    assert broken in _broken_labels(out)


@pytest.mark.parametrize(
    "over",
    [
        pytest.param({"field": "target_lole_h"}, id="another-field"),
        pytest.param({"entity": "g"}, id="goal-key-with-an-entity"),
    ],
)
def test_a_row_with_no_component_that_is_not_a_goal_row_stays_uncompared(assumed, over):
    assumed.solver_state["eh_study"]["assumptions"] = [
        {**ASSUMED_ROWS[0], "key": "other", "label": "Other", **over}
    ]
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert "names no network field" in json.dumps(
        _step(out, S.input_id("assumption/other"))
    )


def test_without_goal_rows_the_real_rows_read_fresh(assumed):
    assumed.solver_state["eh_study"]["assumptions"] = [
        r for r in ASSUMED_ROWS if r["component"]
    ]
    assert explain_study_figure("expected_shortfall")["outcome"]["state"] == "fresh"


@pytest.mark.parametrize(
    "edit, broken",
    [
        pytest.param(
            lambda n: n.storage_units.__setitem__("outage_rate_value", 0.02),
            "Outage rate of battery storage units (bat)",
            id="storage-unit-rate-set",
        ),
        pytest.param(
            lambda n: n.generators.__setitem__(
                "outage_rate_value", [math.nan, 0.05, 0.05]
            ),
            "Outage rate of gas generating units (d1, d2): d2",
            id="joined-rate-set-on-second-unit",
        ),
        pytest.param(
            lambda n: n.generators.loc.__setitem__(("d1", "outage_rate_value"), 0.05),
            "Outage rate of gas generating units (d1, d2): d1",
            id="rate-set-to-the-default-itself",
        ),
        pytest.param(
            lambda n: n.generators.loc.__setitem__(("o1", "mttr_hours"), 50.0),
            "Repair time of oil generating units (o1)",
            id="mttr-set",
        ),
        pytest.param(
            lambda n: n.generators.loc.__setitem__(("o1", "outage_rate_basis"), "FOR"),
            "Outage rate basis of generating units (o1)",
            id="basis-set",
        ),
        pytest.param(
            lambda n: n.links.loc.__setitem__(("boiler", "efficiency"), 0.9),
            "Conversion efficiency of boiler",
            id="efficiency-set",
        ),
        pytest.param(
            lambda n: n.remove("Generator", "d2"),
            "Outage rate of gas generating units (d1, d2): d2",
            id="unit-removed",
        ),
    ],
)
def test_an_edit_to_each_row_kind_reads_stale(assumed, edit, broken):
    assumed.solver_state["eh_study"]["assumptions"] = [
        r for r in ASSUMED_ROWS if r["component"]
    ]
    edit(assumed.network)
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "stale"
    assert _broken_labels(out) == {broken}


def test_a_blank_efficiency_reads_as_the_lossless_default_it_was(assumed):
    assumed.solver_state["eh_study"]["assumptions"] = [
        r for r in ASSUMED_ROWS if r["component"]
    ]
    assumed.network.links.loc["boiler", "efficiency"] = math.nan
    assert explain_study_figure("expected_shortfall")["outcome"]["state"] == "fresh"


def test_a_defaulted_field_the_network_has_no_column_for_is_still_unset(assumed):
    assumed.solver_state["eh_study"]["assumptions"] = [
        r for r in ASSUMED_ROWS if r["component"]
    ]
    assumed.network.generators.drop(columns=["mttr_hours"], inplace=True)
    assert explain_study_figure("expected_shortfall")["outcome"]["state"] == "fresh"


def test_a_record_without_assumptions_did_not_record_them(study):
    del study.solver_state["eh_study"]["assumptions"]
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "uncompared"
    assert out["outcome"]["uncompared"] == [S.input_id("assumptions")]
    step = _step(out, S.input_id("assumptions"))
    assert S.ASSUMPTIONS_NOT_RECORDED in json.dumps(step)


def test_an_empty_assumptions_list_is_none_assumed(study):
    study.solver_state["eh_study"]["assumptions"] = []
    out = explain_study_figure("expected_shortfall")
    assert out["outcome"]["state"] == "fresh"
    assert not [
        s for s in out["steps"] if s["node"].startswith(S.input_id("assumption"))
    ]


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
    return json.dumps(
        {"meta": n.meta, "generators": n.generators.to_dict()},
        sort_keys=True,
        default=str,
    )


@pytest.mark.parametrize("figure", S.FIGURES)
def test_the_route_is_explain_study_figure_and_writes_nothing(
    client, project, bound_ctx, figure
):
    before = (
        _net_bytes(project.network),
        copy.deepcopy(project.solver_state["eh_study"]),
        _state_bytes(project),
        project.network_revision,
    )
    res = client.get(URL.format(figure), params={"project_id": project.project_uuid})
    assert res.status_code == 200, res.text
    with bound_ctx(client):
        assert res.json() == explain_study_figure(figure)
    after = (
        _net_bytes(project.network),
        project.solver_state["eh_study"],
        _state_bytes(project),
        project.network_revision,
    )
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
        res = anon.get(
            URL.format("verdict"), params={"project_id": project.project_uuid}
        )
    assert res.status_code == 401
