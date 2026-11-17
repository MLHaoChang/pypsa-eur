# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 15 (design §9.3 "Grid-only"; SH-PILOT-1; GK A, ACPF-BUG):
grid_only@1 serves 3bus, ieee14, belgium and ieee39. Its PF rules read only
the AC PF result the engine labels as PF; LOPF flows never satisfy them."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pypsa
import pytest

from services import run_gates, run_profiles

REF = "grid_only@1"
TEMPLATES = ["3bus", "ieee14", "belgium", "ieee39"]


def _profile():
    return run_profiles.resolve(REF)


def _rules(stage="result_gate"):
    return {r["id"]: r for r in _profile()["stages"][stage]["rules"]}


# ── the profile ───────────────────────────────────────────


def test_one_profile_serves_the_four_grid_templates_with_no_params_or_levers():
    p = _profile()
    assert p["templates"] == TEMPLATES and p["scope"] == "flow_scope.preset_for"
    assert p["params"]["required"] == [] and p["params"]["ask"] == []
    assert p["tunable"] == [] and p["envelope"]["edits"] == [] and "eh_study" not in p
    assert "skip" in p["stages"]["explore"]
    # GK B SH-15 A1: no KPI and no hand-off rule reads the PF-overwritten frames (issue 50).
    assert p["kpis"] == [] and p["stages"]["handoff"]["rules"] == []


def test_setup_switches_load_flow_on_with_the_one_setting_the_grant_admits():
    """SH15-PF-1 (Q1 = C): setup writes `run_ac_pf_after_lopf` alone, with the
    args `run_envelope.SETTING_TOOLS` freezes, and the envelope lists it."""
    from services import run_envelope

    p = _profile()
    assert p["stages"]["setup"]["actions"] == [
        {
            "tool": "update_solver_config",
            "args": {"partial": {"run_ac_pf_after_lopf": True}},
        }
    ]
    assert run_envelope.SETTING_TOOLS == {
        "update_solver_config": p["stages"]["setup"]["actions"][0]["args"]
    }
    assert {"tool": "update_solver_config"} in p["envelope"]["runs"]
    run_envelope.check_block(p["envelope"])


def _setting_refusal(monkeypatch, args, call="c", step="c"):
    from services import run_envelope

    monkeypatch.setattr(run_envelope, "_standing", lambda *a, **k: None)
    monkeypatch.setattr(run_envelope, "_names_another_project", lambda *a: False)
    envelope = {"runs": [{"tool": "update_solver_config"}], "edits": []}
    return run_envelope._refusal(
        SimpleNamespace(run_step_call=step),
        envelope,
        {},
        "update_solver_config",
        args,
        "write",
        False,
        None,
        SimpleNamespace(network=None, solver_state={}),
        {"call_id": call},
    )


def test_the_harness_own_load_flow_step_is_admitted(monkeypatch):
    assert (
        _setting_refusal(monkeypatch, {"partial": {"run_ac_pf_after_lopf": True}})
        is None
    )


@pytest.mark.parametrize(
    "call,step", [("model-call", "c"), ("c", None), (None, None), ("", "")]
)
def test_a_model_turns_solver_config_write_is_refused_under_the_grant(
    monkeypatch, call, step
):
    args = {"partial": {"run_ac_pf_after_lopf": True}}
    assert _setting_refusal(monkeypatch, args, call=call, step=step) == "own_step_only"


@pytest.mark.parametrize(
    "args",
    [
        {"partial": {"run_ac_pf_after_lopf": False}},
        {"partial": {"run_ac_pf_after_lopf": True, "solver_name": "highs"}},
        {"partial": {"run_ac_pf_after_lopf": True}, "reset": True},
        {"config": {"run_ac_pf_after_lopf": True}},
        {},
    ],
)
def test_the_own_step_with_any_other_args_is_outside_the_entry(monkeypatch, args):
    assert _setting_refusal(monkeypatch, args) == "outside_entry"


def test_the_result_gate_is_the_core_rules_and_the_four_grid_rules():
    r = _rules()
    assert set(r) == {
        "res.solve.status",
        "res.fresh",
        "grid.pf.converged",
        "grid.pf.loading",
        "grid.pf.voltage",
        "grid.n1.secured",
    }
    for rid in ("grid.pf.converged", "grid.pf.loading"):
        assert r[rid]["severity"] == "block" and r[rid]["unevaluated"] == "needs_user"
    assert (r["grid.pf.loading"]["op"], r["grid.pf.loading"]["value"]) == ("le", 100.0)
    assert (
        r["grid.pf.voltage"]["severity"] == r["grid.n1.secured"]["severity"] == "warn"
    )


# ── setup ─────────────────────────────────────────────────


def _template(monkeypatch, template):
    from services import flow_service

    monkeypatch.setattr(
        flow_service, "read_template", lambda project_dir: {"id": template}
    )


def _setup(monkeypatch, template, exists, gridspine=True):
    import importlib.util
    import pathlib

    _template(monkeypatch, template)
    real = pathlib.Path.exists
    monkeypatch.setattr(
        pathlib.Path,
        "exists",
        lambda self: exists if self.name.endswith(".nc") else real(self),
    )
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: object() if gridspine else None
    )
    env = run_gates.Env(task={}, ctx=None, project=None, project_dir=None)
    return run_gates.evaluate([_rules("setup")["set.template.source"]], env)[0]


@pytest.mark.parametrize("template", ["3bus", "ieee14"])
def test_the_built_in_templates_always_have_a_source(monkeypatch, template):
    assert _setup(monkeypatch, template, exists=False)["verdict"] == "pass"


@pytest.mark.parametrize("template", ["belgium", "ieee39"])
def test_belgium_and_ieee39_are_refused_with_a_reason_when_their_source_is_missing(
    monkeypatch, template
):
    out = _setup(monkeypatch, template, exists=False, gridspine=False)
    assert out["verdict"] == "not_evaluated" and (
        "not built" in out["reason"] or "gridspine" in out["reason"]
    )
    assert _setup(monkeypatch, template, exists=True)["verdict"] == "pass"


def test_a_template_outside_the_four_is_refused(monkeypatch):
    env = run_gates.Env(task={}, ctx=None, project=None, project_dir=None)
    _template(monkeypatch, "eh_site")
    rule = _rules("setup")["set.project.template"]
    assert run_gates.evaluate([rule], env)[0]["verdict"] == "fail"


# ── the PF rules ──────────────────────────────────────────


@pytest.fixture
def direct(monkeypatch):
    monkeypatch.setattr(
        run_gates,
        "_own_solve",
        lambda env, read, typed=False, after_pf=False: read(env.ctx),
    )

    def gate(rule_id, ctx):
        env = run_gates.Env(task={}, ctx=ctx, project=None, project_dir=None)
        return run_gates.evaluate([_rules()[rule_id]], env)[0]

    return gate


def _net(rating=100.0, vband=None):
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(2))
    for b in ("a", "b"):
        n.add("Bus", b)
    if vband:
        n.buses["v_mag_pu_min"], n.buses["v_mag_pu_max"] = vband
    n.add("Line", "l1", bus0="a", bus1="b", x=0.1, s_nom=rating)
    return n


def _ctx(
    n,
    p=(60.0, 60.0),
    q=(0.0, 0.0),
    v=(1.0, 1.0),
    ok=(True, True),
    pf=True,
    revision=0,
    **state,
):
    idx = n.snapshots
    snap = {
        "lines_t.p0": pd.DataFrame({"l1": p}, index=idx),
        "lines_t.q0": pd.DataFrame({"l1": q}, index=idx),
        "lines_t.p1": pd.DataFrame({"l1": [-x for x in p]}, index=idx),
        "lines_t.q1": pd.DataFrame({"l1": [-x for x in q]}, index=idx),
        "buses_t.v_mag_pu": pd.DataFrame({"a": v, "b": v}, index=idx),
    }
    st = {
        "status": "completed",
        "condition": "lopf+ac_pf_ok",
        "ac_pf_network_revision": 0,
        "ac_pf_convergence_list": [
            {"snapshot": str(i), "period": None, "ok": o} for i, o in zip(idx, ok)
        ],
        **state,
    }
    if pf:
        st["ac_pf_results"] = snap
    return SimpleNamespace(network=n, solver_state=st, network_revision=revision)


def test_a_converged_pf_within_rating_passes_convergence_and_loading(direct):
    ctx = _ctx(_net(rating=100.0))
    assert direct("grid.pf.converged", ctx)["verdict"] == "pass"
    out = direct("grid.pf.loading", ctx)
    assert out["verdict"] == "pass" and out["facts"][0]["value"] == pytest.approx(60.0)


def test_a_seeded_overload_fails_the_loading_rule_and_names_the_branch(direct):
    out = direct(
        "grid.pf.loading", _ctx(_net(rating=100.0), p=(60.0, 90.0), q=(0.0, 60.0))
    )
    assert out["verdict"] == "fail" and out["facts"][0]["value"] == pytest.approx(
        108.17, abs=0.01
    )
    assert "Line l1" in out["facts"][0]["detail"][0]


def test_loading_uses_apparent_power_not_active_power(direct):
    out = direct(
        "grid.pf.loading", _ctx(_net(rating=100.0), p=(80.0, 80.0), q=(80.0, 80.0))
    )
    assert out["verdict"] == "fail"


@pytest.mark.parametrize(
    "rule", ["grid.pf.converged", "grid.pf.loading", "grid.pf.voltage"]
)
def test_without_an_ac_pf_result_the_pf_rules_are_not_evaluated_whatever_the_lopf_flows_say(
    direct, rule
):
    n = _net(rating=100.0, vband=(0.95, 1.05))
    n.lines_t.p0["l1"] = [1.0, 1.0]  # LOPF-like live flows within rating
    ctx = _ctx(n, pf=False)
    out = direct(rule, ctx)
    assert out["verdict"] == "not_evaluated" and "AC power-flow" in out["reason"]


def test_a_failed_flow_fails_convergence_and_leaves_the_other_pf_rules_unevaluated_with_its_reason(
    direct,
):
    """GK B SH-15 (1): a flow that raised fails `grid.pf.converged` (never pass, missing or
    not evaluated); loading and voltage read not evaluated with the failure's words; N-1 is
    the LP's own and stays judged."""
    n = _net(rating=100.0, vband=(0.95, 1.05))
    n.lines_t.p0["l1"] = [1.0, 1.0]  # LOPF-like live flows within rating
    cfg = {"sclopf": True, "sclopf_include_all_lines": True}
    ctx = _ctx(
        n,
        pf=False,
        condition="optimal; ac_pf_failed: singular Jacobian",
        solver_config=cfg,
    )
    assert direct("grid.pf.converged", ctx)["verdict"] == "fail"
    for rule in ("grid.pf.loading", "grid.pf.voltage"):
        out = direct(rule, ctx)
        assert out["verdict"] == "not_evaluated", (rule, out)
        assert (
            out["facts"][0]["value"] == "ac_pf_failed"
            and "singular Jacobian" in out["reason"]
        ), out
    assert direct("grid.n1.secured", ctx)["verdict"] == "pass"


@pytest.mark.parametrize(
    "condition", ["optimal; ac_pf_unknown: x", "infeasible; ac_pf_failed: x"]
)
def test_another_suffix_is_no_failed_flow(direct, condition):
    out = direct("grid.pf.converged", _ctx(_net(), pf=False, condition=condition))
    assert out["verdict"] == "not_evaluated" and out["facts"][0]["value"] == "no_ac_pf"


def test_a_live_lopf_frame_never_stands_in_for_the_stored_pf_snapshot(direct):
    n = _net(rating=100.0)
    n.lines_t.p0["l1"] = [
        1.0,
        1.0,
    ]  # the live frames are small; the PF snapshot is the overload
    out = direct("grid.pf.loading", _ctx(n, p=(150.0, 150.0)))
    assert out["verdict"] == "fail"


def test_an_unconverged_snapshot_fails_convergence_and_is_left_out_of_loading(direct):
    ctx = _ctx(_net(), p=(60.0, 500.0), ok=(True, False))
    assert direct("grid.pf.converged", ctx)["verdict"] == "fail"
    assert direct("grid.pf.loading", ctx)["verdict"] == "pass"


def test_an_unknown_convergence_is_not_evaluated(direct):
    assert (
        direct("grid.pf.converged", _ctx(_net(), ok=(True, None)))["verdict"]
        == "not_evaluated"
    )


def test_a_pf_older_than_the_inputs_is_not_evaluated(direct):
    out = direct("grid.pf.loading", _ctx(_net(), revision=3))
    assert out["verdict"] == "not_evaluated" and "re-run" in out["reason"]


def test_an_unrated_branch_is_named_never_assumed(direct):
    out = direct("grid.pf.loading", _ctx(_net(rating=0.0)))
    assert out["verdict"] == "not_evaluated" and "l1" in out["reason"]
    assert out["facts"][0]["value"] == "branch_unrated"


def test_the_voltage_band_is_the_buses_own_and_unset_is_not_evaluated(direct):
    unset = direct("grid.pf.voltage", _ctx(_net()))
    assert (
        unset["verdict"] == "not_evaluated"
        and unset["facts"][0]["value"] == "no_voltage_band"
    )
    n = _net(vband=(0.95, 1.05))
    assert direct("grid.pf.voltage", _ctx(n, v=(1.0, 1.0)))["verdict"] == "pass"
    bad = direct("grid.pf.voltage", _ctx(n, v=(1.0, 1.08)))
    assert bad["verdict"] == "fail" and bad["facts"][0]["value"] == 2


def _n1(direct, n, **cfg):
    return direct("grid.n1.secured", _ctx(n, solver_config=cfg, condition="optimal"))


def test_n1_is_judged_only_when_the_solver_option_is_on_over_every_branch(direct):
    on = {"sclopf": True, "sclopf_include_all_lines": True}
    assert _n1(direct, _net(), **on)["verdict"] == "pass"
    off = _n1(direct, _net(), **{**on, "sclopf": False})
    assert off["verdict"] == "not_evaluated" and off["facts"][0]["value"] == "n1_off"
    partial = _n1(direct, _net(), **{**on, "sclopf_include_all_lines": False})
    assert (
        partial["verdict"] == "not_evaluated"
        and partial["facts"][0]["value"] == "n1_partial"
    )


def test_n1_needs_the_transformer_flag_only_when_the_network_has_a_transformer(direct):
    n = _net()
    n.add("Bus", "c")
    n.add("Transformer", "t1", bus0="b", bus1="c", x=0.1, s_nom=100.0)
    both = {"sclopf": True, "sclopf_include_all_lines": True}
    assert _n1(direct, n, **both)["facts"][0]["value"] == "n1_partial"
    assert (
        _n1(direct, n, **both, sclopf_include_all_transformers=True)["verdict"]
        == "pass"
    )


def test_n1_is_not_evaluated_when_the_solve_did_not_finish_optimal(direct):
    ctx = _ctx(
        _net(),
        solver_config={"sclopf": True, "sclopf_include_all_lines": True},
        condition="infeasible",
    )
    assert direct("grid.n1.secured", ctx)["facts"][0]["value"] == "n1_no_solve"


# ── the LP's condition under an AC power flow ─────────────


@pytest.mark.parametrize(
    "condition,expected",
    [
        ("optimal", "optimal"),
        ("lopf+ac_pf_ok", "optimal"),
        ("optimal; ac_pf_failed: singular matrix", "optimal"),
        ("lopf+ac_pf_unknown", "lopf+ac_pf_unknown"),
        ("infeasible; ac_pf_failed: x", "infeasible; ac_pf_failed: x"),
        ("lopf+ac_pf_ok; extra", "lopf+ac_pf_ok; extra"),
        (None, None),
    ],
)
def test_the_lp_condition_is_optimal_only_for_the_known_pf_suffixes(
    condition, expected
):
    assert run_gates.lp_condition(condition) == expected


# ── freshness after the solve's own AC power flow ─────────


def _fresh_ctx(**over):
    n = _net()
    ctx = _ctx(n, **over)
    ctx.solver_state.setdefault("lopf_results", None)
    return ctx


@pytest.fixture
def fingerprints(monkeypatch):
    from services import workflow_tools

    monkeypatch.setattr(
        workflow_tools, "_network_input_fingerprint", lambda ctx, exact=False: "fp"
    )
    monkeypatch.setattr(
        _calc.workflow_tools,
        "_network_input_fingerprint",
        lambda ctx, exact=False: "fp",
    )
    return lambda ctx: ctx.network.meta.update(
        assistant_solve_input_fingerprint="fp",
        assistant_solve_exact_input_fingerprint="fp",
    )


from harness import calculations as _calc  # noqa: E402


def test_a_pf_state_is_current_only_after_pf_and_only_for_the_solves_own_flow(
    fingerprints,
):
    ctx = _fresh_ctx(condition="lopf+ac_pf_ok")
    fingerprints(ctx)
    assert _calc.solve_matches_inputs(ctx, after_pf=True) is True
    assert _calc.solve_matches_inputs(ctx) is False  # the LP-only guard is unchanged


@pytest.mark.parametrize(
    "over",
    [
        {
            "condition": "optimal"
        },  # an LP result with a PF snapshot: not the PF-ok condition
        {"condition": "lopf+ac_pf_unknown"},
        {"condition": "optimal; ac_pf_failed: x"},
        {
            "condition": "lopf+ac_pf_ok",
            "ac_pf_network_revision": 4,
        },  # the flow ran on another edit counter
        {"condition": "lopf+ac_pf_ok", "status": "running"},
    ],
)
def test_each_part_of_the_pf_freshness_guard_refuses_on_its_own(fingerprints, over):
    ctx = _fresh_ctx(**over)
    fingerprints(ctx)
    assert _calc.solve_matches_inputs(ctx, after_pf=True) is False


def test_a_failed_flow_leaves_the_lp_current_only_for_a_profile_that_reads_the_pf(
    fingerprints,
):
    """GK B SH-15 (1): the flow's failure feeds only grid.pf.converged (the stage restores the
    LP's frames), so with `after_pf` the LP is still current; the LP-only guard is unchanged."""
    ctx = _fresh_ctx(condition="optimal; ac_pf_failed: singular", pf=False)
    fingerprints(ctx)
    assert _calc.solve_matches_inputs(ctx, after_pf=True) is True
    assert _calc.solve_matches_inputs(ctx) is False


@pytest.mark.parametrize(
    "condition",
    ["optimal; ac_pf_unknown: x", "infeasible; ac_pf_failed: x", "lopf+ac_pf_ok", None],
)
def test_without_a_flow_only_the_lp_or_a_failed_flow_is_current(
    fingerprints, condition
):
    ctx = _fresh_ctx(condition=condition, pf=False)
    fingerprints(ctx)
    assert _calc.solve_matches_inputs(ctx, after_pf=True) is False


def test_a_changed_input_is_never_current_after_a_pf(fingerprints):
    ctx = _fresh_ctx(condition="lopf+ac_pf_ok")
    ctx.network.meta.update(
        assistant_solve_input_fingerprint="old",
        assistant_solve_exact_input_fingerprint="old",
    )
    assert _calc.solve_matches_inputs(ctx, after_pf=True) is False


def test_only_a_profile_that_reads_the_pf_counts_its_flow_as_fresh(monkeypatch):
    seen = []
    from services import workflow_tools

    monkeypatch.setattr(
        workflow_tools,
        "solve_freshness",
        lambda ctx=None, after_pf=False: (
            seen.append(after_pf) or {"results_current": after_pf}
        ),
    )
    monkeypatch.setattr(run_gates, "_profile", lambda env: {"pf_results": True})
    env = run_gates.Env(task={}, ctx=None, project=None, project_dir=None)
    assert run_gates._fresh(env) is True
    monkeypatch.setattr(run_gates, "_profile", lambda env: {})
    assert run_gates._fresh(env) is False and seen == [True, False]


# ── no solve moves a Lines network's own input identity ───


@pytest.mark.parametrize("template", ["3bus", "ieee14", "belgium", "ieee39"])
@pytest.mark.parametrize("with_pf", [False, True])
def test_a_solve_leaves_the_input_fingerprint_where_it_was(template, with_pf):
    import logging
    import pathlib
    import queue

    from services import workflow_tools
    from services.ac_pf_service import run_ac_pf_stage
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "project_templates"
        / template
        / "network.nc"
    )
    if not path.exists():
        pytest.skip(f"{template} is not built here")
    logging.disable(logging.CRITICAL)
    try:
        n = pypsa.Network(str(path))
        ctx = PyPSAService.build_context()
        ctx.network = n
        before = [
            workflow_tools._network_input_fingerprint(ctx, exact=e)
            for e in (False, True)
        ]
        status, _ = n.optimize(solver_name="highs")
        assert status == "ok"
        if with_pf:
            run_ac_pf_stage(n, SolverConfig(), queue.Queue())
        after = [
            workflow_tools._network_input_fingerprint(ctx, exact=e)
            for e in (False, True)
        ]
    finally:
        logging.disable(logging.NOTSET)
    assert before == after


@pytest.mark.parametrize("template", ["3bus", "ieee14", "belgium", "ieee39"])
def test_the_in_place_flow_adds_no_edit_and_leaves_the_branch_carriers(template):
    """GK B SH-15 (2) A3: the AC power flow runs in place on the solved network; it pushes
    no undo entry, moves no edit counter and leaves the saved branch carriers as the LP left them."""
    import logging
    import pathlib
    import queue

    from services.ac_pf_service import run_ac_pf_stage
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "project_templates"
        / template
        / "network.nc"
    )
    if not path.exists():
        pytest.skip(f"{template} is not built here")
    logging.disable(logging.CRITICAL)
    n = pypsa.Network(str(path))
    ctx = PyPSAService.build_context()
    ctx.network = n
    token = PyPSAService.bind_request_context(ctx)
    try:
        assert n.optimize(solver_name="highs")[0] == "ok"
        carriers = {
            c: n.static(c).get("carrier", pd.Series(dtype=object)).copy()
            for c in ("Line", "Transformer")
        }
        undo, revision = len(ctx.undo.stack), ctx.network_revision
        run_ac_pf_stage(n, SolverConfig(), queue.Queue())
        assert len(ctx.undo.stack) == undo and ctx.network_revision == revision
        for c, before in carriers.items():
            pd.testing.assert_series_equal(
                n.static(c).get("carrier", pd.Series(dtype=object)), before
            )
    finally:
        PyPSAService.reset_request_context(token)
        logging.disable(logging.NOTSET)


def _fingerprint(n):
    from services import workflow_tools
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.build_context()
    ctx.network = n
    return workflow_tools._network_input_fingerprint(ctx)


def test_only_an_empty_branch_carrier_reads_as_ac_in_the_fingerprint():
    def lines(carrier):
        n = _net()
        n.lines["carrier"] = carrier
        return _fingerprint(n)

    assert lines("") == lines("AC")
    assert lines("AC") != lines("DC") and lines("") != lines("DC")


def test_the_fingerprint_leaves_the_live_carrier_as_it_was():
    n = _net()
    n.lines["carrier"] = ""
    _fingerprint(n)
    assert set(n.lines["carrier"]) == {""}


def test_a_failed_loading_rule_ends_the_run_for_the_user_with_no_agent_edit():
    rule = _rules()["grid.pf.loading"]
    assert rule["unevaluated"] == "needs_user" and _profile()["tunable"] == []
