# ruff: noqa: F811 -- imported pytest fixtures
# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 12c (design §9.3 "eh_microgrid"; GK B (47) G1-G8, B12, B13):
eh_microgrid@1 and eh_microgrid_staged@1.

The profiles' rules and envelope as data; the islanding contingencies K
counted when the EH study is frozen (G1); the solve accounting of one L2
round (G2); island survival failing closed on every state but `survives`
(G3, one under a real HiGHS time limit); the L2 battery resize bounded by
the card (G4); the `ic.baseline_keeps` words at each reader (G5); the
off-grid scorecard and the LOLE basis (G7); a not-evaluated rule never
counted as met (G8); the planning intake (B13)."""

from __future__ import annotations

import copy
import json
import sys
import types
from pathlib import Path

import pypsa
import pytest

from harness import run_eh, run_handoff, run_loops
from harness import runner as harness_runner
from services import (
    assistant_tasks,
    flow_scope,
    flow_service,
    reliability_goal,
    run_actions,
    run_envelope,
    run_gates,
    run_intake,
    run_profiles,
)
from services import ic_counterfactual as ic
from services.adequacy import grid_readiness as G
from services.adequacy import island_config as IC
from services.adequacy import island_survival as S
from services.adequacy.metrics import horizon_years
from services.reports import harness_chapter
from tests import test_run_action_island_survival as MG
from tests.test_assistant_tasks_study_brief import helpers_state, session  # noqa: F401
from tests.test_campus_electrical_service import hub, user_and_db  # noqa: F401 - fixtures
from tests.test_harness_envelope import granted, profile  # noqa: F401
from tests.test_harness_run_loops import rated  # noqa: F401
from tests.test_harness_runner import detached, fake_report_provider  # noqa: F401

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

REFS = {
    "eh_microgrid": "eh_microgrid@1",
    "eh_microgrid_staged": "eh_microgrid_staged@1",
}
PROJECT = "11111111-1111-1111-1111-111111111111"


def _rules(ref, stage="result_gate"):
    return {r["id"]: r for r in run_profiles.resolve(ref)["stages"][stage]["rules"]}


# ── the profiles ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("template, ref", REFS.items())
def test_each_profile_runs_the_eh_study_with_the_stress_then_the_island_check(
    template, ref
):
    profile = run_profiles.resolve(ref)
    assert (
        profile["templates"] == [template]
        and profile["scope"] == "flow_scope.preset_for"
    )
    assert [a["tool"] for a in profile["stages"]["solve"]["actions"]] == [
        "solve_queue_enqueue",
        "run_eh_study",
        run_actions.MG_SURVIVAL,
    ]
    assert profile["eh_study"] == {
        "stages": ["apply_pack", "ens_solve", "mc_certify", "dtc_stress", "assemble"],
        "budget_solves": 2,
        "contingency_solves": 1,
    }
    # The scope is the template's preset (`_MICROGRID`), the archetype the template card's.
    meta = T.TEMPLATE_META[template]
    assert run_profiles.scope_for(meta)["cards"] == {
        k: list(v) for k, v in flow_scope.TEMPLATE_PRESETS[template].items()
    }
    assert flow_scope.TEMPLATE_PRESETS[template] is flow_scope._MICROGRID
    assert flow_service._request_for(meta, None)["archetype"] == "off_grid"
    assert _rules(ref, "setup")["set.project.template"]["value"] == template


RESULT_RULES = {
    # id: (fact, op, value, severity, unit)
    "mg.island.survival": ("mg.island.survival", "eq", "survives", "block", None),
    "mg.tie.open": ("mg.tie.open", "eq", "open", "block", None),
    "mg.critical.tagged": ("mg.critical.tagged", "eq", "tagged", "block", None),
    "mg.island.critical_served": (
        "mg.island.critical_served",
        "le",
        0.0,
        "block",
        "MWh",
    ),
    "mg.island.frequency": ("mg.island.frequency", "le", 0, "warn", "h"),
    "mg.fuel.autonomy": ("mg.fuel.autonomy", "eq", True, "warn", None),
    "eh.adequacy.lole": ("adequacy.loop.converged", "eq", True, "block", None),
    "mg.ic.counterfactual": ("ic.counterfactual", "eq", "established", "warn", None),
    "mg.ic.baseline_keeps": ("ic.baseline_keeps", "nonempty", None, "warn", None),
}


@pytest.mark.parametrize("ref", REFS.values())
def test_the_result_gate_reads_the_island_facts_lole_and_the_investment_case(ref):
    rules = _rules(ref)
    own = {
        k: v
        for k, v in rules.items()
        if k not in {r["id"] for r in run_profiles.CORE_RESULT_RULES}
    }
    assert set(own) == set(RESULT_RULES)
    for rid, (fact, op, value, severity, unit) in RESULT_RULES.items():
        rule = own[rid]
        assert (rule["fact"], rule["op"], rule.get("value"), rule["severity"]) == (
            fact,
            op,
            value,
            severity,
        ), rid
        assert rule.get("unit") == unit and rule["basis"], rid
    assert "MWh" in rules["mg.island.critical_served"]["basis"]
    assert "not evaluated" in rules["mg.fuel.autonomy"]["basis"]


@pytest.mark.parametrize("ref", REFS.values())
def test_no_scr_rule_on_either_profile(ref):
    """G7: the tie is open, so no rule reads the PoC's fault level."""
    for stage, block in run_profiles.resolve(ref)["stages"].items():
        for rule in block.get("rules", []):
            assert not rule["fact"].startswith(run_gates.SCORECARD), (stage, rule["id"])
            assert "scr" not in rule["id"].split(".") and "scr" not in rule[
                "fact"
            ].split("."), rule["id"]


@pytest.mark.parametrize("template, ref", REFS.items())
def test_lole_is_judged_on_the_goal_in_h_per_year_on_the_networks_horizon(
    template, ref
):
    """G7: the rule names its h/yr basis; the loop gets the rule's engine block
    only, so its target is `loop_goal` of the goal on `horizon_years(n)`."""
    lole = _rules(ref)["eh.adequacy.lole"]
    assert "h/yr" in lole["basis"]
    assert lole["engine"] == {
        "tool": "run_coupling_loop",
        "max_solves": 3,
        "restore": "base",
    }
    n = T.BUILDERS[template]()
    years = float(horizon_years(n))
    goal = {
        "target_lole_h": {"value": 2.0, "source": "stored"},
        "mc_draws": {"value": 400, "source": "stored"},
    }
    body = types.SimpleNamespace(target_lole_h=None, draws=None)
    assert reliability_goal.loop_goal(body, goal, years)[0] == pytest.approx(
        2.0 * years
    )


# ── G4: the battery the L2 loop may resize, bounded on the card ─────────────


@pytest.mark.parametrize("template, ref", REFS.items())
def test_the_battery_bounds_come_from_the_template_and_show_with_unit_and_source(
    template, ref
):
    profile = run_profiles.resolve(ref)
    n = T.BUILDERS[template]()
    name = run_profiles.MG_BESS[template][0]
    bess = n.storage_units[n.storage_units.index.str.fullmatch(name.replace("*", ".*"))]
    assert len(bess) and bess["p_nom_extendable"].all()
    cap, hours = float(bess["p_nom_max"].max()), float(bess["max_hours"].max())
    edits = {e["target"]["attr"]: e for e in profile["envelope"]["edits"]}
    assert set(edits) == {"p_nom_max", "max_hours"}
    assert edits["p_nom_max"]["bounds"] == {"min": 0.0, "max": 2 * cap}
    assert edits["max_hours"]["bounds"] == {"min": 1.0, "max": 2 * hours}
    assert {e["unit"] for e in edits.values()} == {"MW", "h"}
    assert all(template in e["source"] for e in edits.values())
    # The survival rule names them as its levers; nothing else is tunable.
    assert _rules(ref)["mg.island.survival"]["tunable"] == [
        "StorageUnit.p_nom_max",
        "StorageUnit.max_hours",
    ]
    assert profile["tunable"] == [
        {"component": "StorageUnit", "attr": a} for a in ("p_nom_max", "max_hours")
    ]
    # The card shows each bound with its value, unit and source.
    proposed = {
        "id": "e",
        "state": "proposed",
        "expires_at": None,
        "audit": [],
        "notify": {},
        "edits": profile["envelope"]["edits"],
        "gaps": [],
        "runs": [],
        "budget": profile["envelope"]["budget"],
        "research": profile["envelope"]["research"],
    }
    detail = run_envelope.describe(proposed)["detail"]
    assert (
        f"p_nom_max (min 0, max {2 * cap:g} MW; source: {edits['p_nom_max']['source']})"
        in detail
    )
    assert (
        f"max_hours (min 1, max {2 * hours:g} h; source: {edits['max_hours']['source']})"
        in detail
    )


def test_a_narrowed_envelope_keeps_the_profiles_unit_and_source():
    block = run_profiles.resolve("eh_microgrid@1")["envelope"]
    mine = copy.deepcopy(block)
    for e in mine["edits"]:
        e["source"], e["unit"] = "the model says so", "kW"
    chosen = run_envelope.narrow(block, mine)
    assert [(e["unit"], e["source"]) for e in chosen["edits"]] == [
        (e["unit"], e["source"]) for e in block["edits"]
    ]
    with pytest.raises(run_envelope.EnvelopeError):
        run_envelope.check_block(
            {**block, "edits": [{**block["edits"][0], "source": ""}]}
        )


# ── G1: K counted at freeze, shown on the card, bound in the digest ─────────


def _ctx(tmp_path, template="eh_microgrid", network=None):
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    (root / flow_service.TEMPLATE_FILE).write_text(
        json.dumps(T.TEMPLATE_META[template])
    )
    return types.SimpleNamespace(
        storage_dir=str(root),
        project_uuid=PROJECT,
        solver_state_lock=__import__("threading").RLock(),
        solver_state={"solver_config": {"target_lole_h": 2.0}},
        network_revision=0,
        network=network if network is not None else T.BUILDERS[template](),
    )


def _task(tmp_path, ref="eh_microgrid@1"):
    path = tmp_path / "task.json"
    task = {
        "task_id": "t1",
        "title": "Island microgrid study",
        "project_id": PROJECT,
        "owner_id": "u",
        "status": "running",
        "steps": [],
        "brief": {"objective": "Size the island.", "cards": [], "cases": []},
        "run": {
            "profile": ref,
            "mode": "stepwise",
            "status": "running",
            "stage": "solve",
            "stages": {
                s: {"status": "pending", "attempts": 0} for s in run_profiles.STAGES
            },
        },
    }
    path.write_text(json.dumps(task))
    return path, task


def _grant(path, ref="eh_microgrid@1", audit=(), **extra):
    profile = run_profiles.resolve(ref)
    record = {
        "id": "e1",
        "task_id": "t1",
        "project_id": PROJECT,
        "owner_id": "u",
        "version": 1,
        "granted_at": "2026-11-11T00:00:00+00:00",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "fingerprint": "f",
        "chain": [],
        "profile": ref,
        "edits": profile["envelope"]["edits"],
        "runs": profile["envelope"]["runs"],
        "budget": dict(profile["envelope"]["budget"]),
        "research": profile["envelope"]["research"],
        "notify": {},
        "state": "granted",
        "audit": list(audit),
        **extra,
    }
    run_envelope.path_for(path).write_text(json.dumps(record))
    return record


def _second_tie(n):
    """A second mainland tie the off_grid pack islands: one more contingency."""
    n.add(
        "Link",
        "second_tie",
        bus0="mainland",
        bus1="island",
        p_nom=10.0,
        p_max_pu=0.0,
        efficiency=0.97,
        carrier="AC",
    )
    n.links.at["second_tie", "eh_role"] = "grid_import"


@pytest.mark.parametrize("template, ref", REFS.items())
def test_k_is_counted_at_freeze_shown_on_the_card_and_bound_in_the_digest(
    tmp_path, template, ref
):
    profile = run_profiles.resolve(ref)
    ctx = _ctx(tmp_path, template)
    frozen = run_eh.freeze(ctx, profile, run_profiles.MG_BUDGET_SOLVES)
    assert frozen["contingencies"] == {
        "count": run_profiles.MG_CONTINGENCIES,
        "links": ["subsea_tie"],
    }
    args = frozen["args"]
    assert args["archetype"] == "off_grid" and "dtc_stress" in args["stages"]
    assert args["budget_solves"] == 2 + run_profiles.MG_CONTINGENCIES
    assert args["dtc_config"]["islanding_contingencies"] == ["subsea_tie"]
    assert args["dtc_config"]["critical_bus_ids"] == ["hospital"]
    assert frozen["digest"] == run_eh.digest(args)  # K is in the digested args
    steps = run_profiles.materialise(profile, "solve", PROJECT)
    run_eh.fill(steps, frozen)
    assert steps[1]["card"] == {
        "tool": "run_eh_study",
        "contingencies": 1,
        "links": ["subsea_tie"],
        "budget_solves": 3,
    }
    # The step's card is in the task view the user approves from.
    path, task = _task(tmp_path, ref)
    task["steps"] = steps
    view = assistant_tasks._view(task)
    assert (
        next(s for s in view["steps"] if s["tool"] == "run_eh_study")["card"][
            "contingencies"
        ]
        == 1
    )
    # The grant binds it: the granted envelope carries and states it.
    granted = _grant(path, ref, eh_study=frozen)
    assert (
        "The Energy Hub study stress-tests 1 islanding contingency (subsea_tie) on 3 solves"
        in run_envelope.describe(granted)["detail"]
    )


def test_k_is_whatever_the_project_has_and_the_budget_is_never_capped_down(tmp_path):
    profile = run_profiles.resolve("eh_microgrid@1")
    n = T.build_eh_microgrid()
    _second_tie(n)
    frozen = run_eh.freeze(
        _ctx(tmp_path, network=n), profile, run_profiles.MG_BUDGET_SOLVES
    )
    assert (
        frozen["contingencies"]["count"] == 2 and frozen["args"]["budget_solves"] == 4
    )
    # Never only the first K: too few solves left refuses the freeze outright.
    with pytest.raises(run_eh.FreezeError, match="needs 3 solves"):
        run_eh.freeze(_ctx(tmp_path), profile, 2)


@pytest.mark.parametrize("change", ["added", "removed"])
def test_a_different_k_at_the_solve_refuses_by_its_kind_before_any_solve(
    tmp_path, change
):
    """G1: add (or drop) a contingency after the grant: the step is refused
    as `eh_contingencies`, nothing is reserved and the run stops for the user."""
    profile = run_profiles.resolve("eh_microgrid@1")
    n = T.build_eh_microgrid()
    if change == "removed":
        _second_tie(n)
    ctx = _ctx(tmp_path, network=n)
    frozen = run_eh.freeze(ctx, profile, run_profiles.MG_BUDGET_SOLVES)
    path, task = _task(tmp_path)
    _grant(path, eh_study=frozen)
    if change == "added":
        _second_tie(ctx.network)
    else:
        ctx.network.remove("Link", "second_tie")
    step = {
        "id": "solve.2",
        "tool": "run_eh_study",
        "args": dict(frozen["args"]),
        "frozen": frozen,
        "status": "pending",
        "attempt_id": "a1",
    }
    kind, message = run_eh.admit(path, task, step, step["args"], ctx)
    assert kind == run_eh.CONTINGENCY_KIND == "eh_contingencies"
    assert "islanding contingencies" in message
    assert (
        task["run"]["status"] == "needs_user"
        and task["run"]["reason"] == "eh_contingencies"
    )
    assert not [a for a in run_envelope._load(path)["audit"] if "reserved" in a]


def test_the_same_k_is_admitted_and_its_solves_reserved(tmp_path):
    profile = run_profiles.resolve("eh_microgrid@1")
    ctx = _ctx(tmp_path)
    frozen = run_eh.freeze(ctx, profile, run_profiles.MG_BUDGET_SOLVES)
    path, task = _task(tmp_path)
    _grant(path, eh_study=frozen)
    step = {
        "id": "solve.2",
        "tool": "run_eh_study",
        "args": dict(frozen["args"]),
        "frozen": frozen,
        "status": "pending",
        "attempt_id": "a1",
    }
    assert run_eh.admit(path, task, step, step["args"], ctx) is None
    [row] = [a for a in run_envelope._load(path)["audit"] if a["event"] == run_eh.STUDY]
    assert row["reserved"] == 3
    # A record whose shown K disagrees with its own args is never admitted.
    bad = copy.deepcopy(frozen)
    bad["contingencies"]["links"] = ["elsewhere"]
    (tmp_path / "other").mkdir()
    path2, task2 = _task(tmp_path / "other")
    assert (
        run_eh.admit(path2, task2, {**step, "frozen": bad}, step["args"], ctx)[0]
        == run_eh.ARGS_KIND
    )


def test_the_contingency_count_on_the_shipped_templates_is_the_budgets():
    for template in REFS:
        links, _ = run_eh.islanding(T.BUILDERS[template](), {"archetype": "off_grid"})
        assert len(links) == run_profiles.MG_CONTINGENCIES, template


# ── G2: the solve accounting of one L2 round ─────────────────────────────────


def test_one_l2_round_records_the_profile_comments_arithmetic(tmp_path):
    """G2: the queued solve, the EH study (2 + K) and the island check, on the
    first pass and on one L2 round's re-solve; then the coupling loop's solves
    and its closing re-solve. Each through the reservation and settle the
    runner uses; the run's recorded count equals the profile's arithmetic."""
    profile = run_profiles.resolve("eh_microgrid@1")
    ctx = _ctx(tmp_path)
    frozen = run_eh.freeze(ctx, profile, run_profiles.MG_BUDGET_SOLVES)
    path, task = _task(tmp_path)
    _grant(path, eh_study=frozen)
    k = frozen["contingencies"]["count"]
    for n_pass in (1, 2):  # the first pass, then the one L2 round's
        envelope = run_envelope._load(path)
        run_envelope._audit(
            envelope, "used", tool="solve_queue_enqueue", call_id=f"q{n_pass}"
        )
        run_envelope._save(path, envelope)
        step = {
            "id": f"solve.{n_pass}",
            "tool": "run_eh_study",
            "args": dict(frozen["args"]),
            "frozen": frozen,
            "status": "pending",
            "attempt_id": f"eh{n_pass}",
        }
        assert run_eh.admit(path, task, step, step["args"], ctx) is None
        assert run_eh.release(
            path, f"eh{n_pass}", 2 + k, task
        )  # what a done study charged
        assert run_actions._reserve(
            path,
            f"mg{n_pass}",
            tool=run_actions.MG_SURVIVAL,
            event=run_actions.MG_EVENT,
        )
        assert run_actions.release(path, f"mg{n_pass}", 1, event=run_actions.MG_EVENT)
    max_solves, why = run_envelope.reserve_engine(
        path,
        call_id="loop",
        tool="run_coupling_loop",
        rule="eh.adequacy.lole",
        max_solves=3,
        overhead=run_loops._overhead("run_coupling_loop"),
        restore="base",
    )
    assert why is None and max_solves == 3
    record = json.loads(path.read_text())
    record["steps"] = [
        {
            "id": "result_gate.1",
            "tool": "run_coupling_loop",
            "engine": "eh.adequacy.lole",
            "status": "waiting",
            "attempt_id": "loop",
            "job": {"kind": "engine"},
        }
    ]
    path.write_text(json.dumps(record))
    loop = {
        "status": "met",
        "solves_used": 3,
        "probe_solves": 0,
        "base_restore_status": "ok",
    }
    assistant_tasks.settle_engine(path, ("t1", "result_gate.1", "loop"), loop, "ended")
    used = run_envelope.solves_used(run_envelope._load(path))
    per_pass = 1 + (2 + k) + run_actions.SOLVES
    assert used == per_pass * 2 + 3 + 1 == 14
    assert json.loads(path.read_text())["run"]["budget"]["used"]["solves"] == used
    # The comment's worst case: every L2 round run, every reservation at its cap.
    assert run_profiles.MG_PASS_SOLVES == per_pass
    assert (
        run_profiles.MG_BUDGET_SOLVES
        == per_pass * (1 + run_profiles.L2_ROUNDS) + 3 + 1
        == 19
    )


# ── G3: survival blocks on every state but survives ──────────────────────────


def _survival_rule(ref="eh_microgrid@1"):
    return _rules(ref)["mg.island.survival"]


def _judge(hub, task, ctx=None):
    [result] = run_gates.evaluate([_survival_rule()], MG._env(hub, task, ctx))
    return result


@pytest.mark.parametrize(
    "state, verdict, value",
    [
        ("survives", "pass", "survives"),
        ("fails", "fail", "fails"),  # an infeasible LP is a failed plan
        ("not_evaluated", "not_evaluated", "not_evaluated"),
        ("not_solved", "not_evaluated", "not_solved"),
        ("interrupted", "not_evaluated", "not_run"),
        ("stale_revision", "not_evaluated", "edited"),
        ("island_changed", "not_evaluated", "island_changed"),
        ("inputs_changed", "not_evaluated", "inputs_changed"),
    ],
)
def test_survival_blocks_on_every_state_but_survives(hub, state, verdict, value):
    ctx = MG._ctx(hub)
    if state in ("survives", "fails", "not_evaluated", "not_solved"):
        task = MG._done(
            hub,
            {
                "state": state,
                "reason": None if state == "survives" else f"{state} words",
            },
        )
    elif (
        state == "interrupted"
    ):  # a restart mid-check: `run_actions.settle` leaves it uncertain
        task = MG._done(hub)
        task["steps"][0]["status"] = "uncertain"
    elif state == "stale_revision":
        task = MG._done(hub, revision=6)
    elif state == "island_changed":
        task = MG._done(hub)
        MG._change_sidecar(hub)
    else:
        task = MG._done(
            hub,
            {
                "state": "inputs_changed",
                "reason": "the network was edited while it ran",
            },
        )
    result = _judge(hub, task, ctx)
    assert result["severity"] == "block"
    assert result["verdict"] == verdict and result["facts"][0]["value"] == value, result
    assert run_gates.passed([result]) is (state == "survives")


@pytest.mark.live_solve
def test_survival_under_a_real_highs_time_limit_blocks(hub, monkeypatch):
    """G3: the check's own solve on the shipped template, held to a time limit
    HiGHS reaches: not evaluated, and the block rule fails the gate."""
    from tests.test_island_survival import _own_solve

    defaults = pypsa.Network().components.links.defaults
    before = set(defaults.index)
    try:
        n = _own_solve(T.BUILDERS["eh_microgrid"](), "eh_microgrid")
        MG._sidecar(hub)
        path = MG._task_file(hub, run_actions.mg_freeze(hub))
        MG._granted(path, 3)
        monkeypatch.setattr(run_actions, "MG_TIME_LIMIT_S", 1e-6)
        ctx = MG._ctx(hub, n)
        assert MG._start(path, ctx) == run_actions.MG_SURVIVAL
        result = MG._read(path)["steps"][0]["result"]
        assert (
            result["state"] == S.NOT_EVALUATED
            and result["solver"]["condition"] == "time_limit"
        ), result
        assert result["solves"] == 1 and MG._row(path)["solves"] == 1
        judged = _judge(hub, MG._read(path), ctx)
        assert (
            judged["verdict"] == "not_evaluated"
            and judged["facts"][0]["value"] == S.NOT_EVALUATED
        )
        assert "time limit" in judged["reason"]
        assert run_gates.passed([judged]) is False
    finally:
        defaults.drop(
            index=[a for a in defaults.index if a not in before], inplace=True
        )


# ── G5 (B12): the `ic.baseline_keeps` words reach each reader ────────────────

KEEPS = "mg.ic.baseline_keeps"
DROPS_WORDS = "The case's baseline drops the owned asset diesel_1"


@pytest.mark.parametrize(
    "fact, words",
    [
        (
            {
                "state": ic.NOT_ESTABLISHED,
                "kept": ["pv_plant"],
                "reasons": ["baseline_drops:diesel_1"],
            },
            DROPS_WORDS,
        ),
        (
            {"state": ic.NOT_ESTABLISHED, "kept": [], "reasons": ["no_baseline_plan"]},
            "The case's baseline has no plan to read",
        ),
        (
            {
                "state": ic.NOT_ESTABLISHED,
                "kept": [],
                "reasons": ["baseline_drops:diesel_1", "baseline_drops:island_battery"],
            },
            DROPS_WORDS + "; the case's baseline drops the owned asset island_battery",
        ),
        (
            {"state": ic.NOT_RUN, "kept": [], "reasons": ["no_case"]},
            "No investment case has been run",
        ),
    ],
)
def test_the_gate_keeps_the_typed_state_and_gives_the_reason_in_words(
    monkeypatch, fact, words
):
    """Reader 1, the gate record (`run_gates.evaluate`)."""
    monkeypatch.setattr(
        ic, "read_baseline_keeps", lambda ctx, on, tid: copy.deepcopy(fact)
    )
    monkeypatch.setattr(run_gates, "_template", lambda env: "eh_microgrid")
    env = run_gates.Env(task={}, ctx=object(), project=None, project_dir=None)
    [result] = run_gates.evaluate([_rules("eh_microgrid@1")[KEEPS]], env)
    assert result["verdict"] == "not_evaluated" and result["severity"] == "warn"
    assert result["facts"][0]["value"] == fact["state"]
    assert result["reason"] == words
    assert ":" not in result["reason"] and "_" not in result["reason"].replace(
        "diesel_1", ""
    ).replace("island_battery", "")
    assert run_gates.passed([result])


def _dropped_gate(monkeypatch):
    fact = {
        "state": ic.NOT_ESTABLISHED,
        "kept": [],
        "reasons": ["baseline_drops:diesel_1"],
    }
    monkeypatch.setattr(ic, "read_baseline_keeps", lambda ctx, on, tid: fact)
    monkeypatch.setattr(run_gates, "_template", lambda env: "eh_microgrid")
    env = run_gates.Env(task={}, ctx=object(), project=None, project_dir=None)
    rules = run_gates.evaluate([_rules("eh_microgrid@1")[KEEPS]], env)
    return {"rules": rules, "passed": run_gates.passed(rules)}


def _run_with(gate):
    stages = {s: {"status": "pending"} for s in run_profiles.STAGES}
    stages["result_gate"] = {"status": "passed", "gate": gate}
    return {
        "profile": "eh_microgrid@1",
        "mode": "stepwise",
        "status": "running",
        "stage": "explore",
        "stages": stages,
    }


def test_the_words_reach_the_task_view_the_chat_tool_and_the_monitor_read(monkeypatch):
    """Reader 2: `assistant_tasks._run_view` (`get_task`, the run monitor)."""
    view = assistant_tasks._run_view(
        {"run": _run_with(_dropped_gate(monkeypatch)), "steps": []}
    )
    [rule] = next(s for s in view["stages"] if s["stage"] == "result_gate")["gate"]
    assert (
        rule["rule"] == KEEPS
        and rule["reason"] == DROPS_WORDS
        and rule["value"] == ic.NOT_ESTABLISHED
    )


def test_the_words_reach_the_reports_harness_chapter(monkeypatch):
    """Reader 3: the report's gates table (`harness_chapter.gates_rows`)."""
    rows = harness_chapter.gates_rows(_run_with(_dropped_gate(monkeypatch)))
    [row] = [r for r in rows if r[2] == KEEPS]
    assert row[3] == "not_evaluated" and row[5] == DROPS_WORDS


def test_a_warn_rule_never_reaches_the_l2_message(monkeypatch):
    """Reader 4, by construction: L2 carries the failing block rules only."""
    gate = _dropped_gate(monkeypatch)
    assert run_loops._failing(gate) == []


def test_the_readers_are_listed():
    listed = (
        BACKEND.parents[1] / ".scratch" / "study-harness" / "issues" / "12c-readers.md"
    ).read_text()
    for reader in (
        "run_gates.evaluate",
        "assistant_tasks._run_view",
        "harness_chapter.gates_rows",
        "run_loops._l2_message",
    ):
        assert reader in listed


# ── G7: the off-grid scorecard reads the island rows ─────────────────────────


@pytest.mark.parametrize("template", REFS)
def test_the_off_grid_scorecard_excludes_the_sk_rows_with_their_reason(
    tmp_path, template
):
    from tests.test_grid_readiness import PROFILE

    (tmp_path / flow_service.TEMPLATE_FILE).write_text(
        json.dumps(T.TEMPLATE_META[template])
    )
    (tmp_path / IC.SIDECAR_NAME).write_text(json.dumps(T.ISLAND_CONFIG[template]))
    out = G.grid_readiness(tmp_path, T.BUILDERS[template](), PROFILE)
    ids = {r["id"] for r in out["rows"]}
    excluded = {e["id"]: e["reason"] for e in out["excluded"]}
    assert "scr" not in ids and excluded["scr"] == G.SK_OFF_GRID
    assert {
        "rapid_voltage_change",
        "harmonic_distortion",
        "flicker",
        "voltage_unbalance",
    } <= set(excluded)
    assert all(reason in (G.SK_OFF_GRID, G.VS_OFF_GRID) for reason in excluded.values())
    assert not ids & set(excluded)
    # The island rows stay: the grid former and the ride-through scenario.
    assert "grid_former" in ids and "ride_through:seamless" in ids


# ── G8: a rule not evaluated is never met ────────────────────────────────────


def _gate_with_warns(hub):
    rules = _rules("eh_microgrid@1")
    env = MG._env(hub)
    MG._sidecar(hub)
    results = run_gates.evaluate(
        [rules["mg.fuel.autonomy"], rules["mg.island.frequency"]], env
    )
    results.append(
        {
            "rule": "res.fresh",
            "severity": "block",
            "verdict": "pass",
            "facts": [{"fact": "fresh", "value": True, "unit": None, "source": "x"}],
        }
    )
    return {"rules": results, "passed": run_gates.passed(results)}


def test_fuel_autonomy_and_frequency_are_their_own_state_on_the_hand_off(hub):
    gate = _gate_with_warns(hub)
    assert gate["passed"] is True  # warn rules never block
    verdicts = {
        r["rule"]: (r["verdict"], r["facts"][0]["value"]) for r in gate["rules"]
    }
    assert verdicts["mg.fuel.autonomy"] == ("not_evaluated", "not_modelled")
    assert verdicts["mg.island.frequency"] == ("not_evaluated", "not_run")
    run = _run_with(gate)
    checks = run_handoff.checks(run)
    assert checks["counts"] == {"pass": 1, "fail": 0, "not_evaluated": 2}
    assert [c["rule"] for c in checks["not_evaluated"]] == [
        "mg.fuel.autonomy",
        "mg.island.frequency",
    ]
    assert checks["not_evaluated"][0]["reason"] == run_gates.MG_NO_FUEL
    # The stated targets are never "covered" while a rule is not evaluated.
    [targets] = [
        p
        for p in run_handoff.coverage({"run": run}, {}, None)
        if p["part"] == "Stated targets"
    ]
    assert targets["status"] == run_handoff.PARTLY
    assert (
        targets["basis"]
        == "the result gate passed; not evaluated: mg.fuel.autonomy, mg.island.frequency"
    )


def test_the_reports_counts_keep_not_evaluated_apart_from_pass(hub):
    run = _run_with(_gate_with_warns(hub))
    payload, _ = harness_chapter.build(
        {"run": run}, None, [], run_handoff.coverage({"run": run}, {}, None)
    )
    counts = payload["counts"]
    assert (
        counts["result_checks_pass"],
        counts["result_checks_fail"],
        counts["result_checks_not_evaluated"],
    ) == (1, 0, 2)
    assert "Request part partly: Stated targets" in payload["gaps"]


def test_an_unknown_verdict_counts_as_not_evaluated():
    run = _run_with(
        {
            "rules": [{"rule": "x", "severity": "warn", "verdict": "maybe"}],
            "passed": True,
        }
    )
    assert run_handoff.checks(run)["counts"] == {
        "pass": 0,
        "fail": 0,
        "not_evaluated": 1,
    }


# ── B13: the planning intake ─────────────────────────────────────────────────


def _planning(start, end):
    rows = [
        {"param": f"planning.{k}", "status": "confirmed", "value": v}
        for k, v in (("start_year", start), ("end_year", end))
    ]
    return run_intake.params(rows)["planning"]


@pytest.mark.parametrize("ref", REFS.values())
def test_the_profile_asks_for_the_planning_years(ref):
    fields = run_profiles.resolve(ref)["params"]["fields"]
    assert (
        fields["planning.start_year"]["kind"]
        == fields["planning.end_year"]["kind"]
        == "year"
    )
    assert "planning" in run_profiles.resolve(ref)["params"]["ask"]


@pytest.mark.parametrize("end", [2030, 2029])
def test_the_single_year_microgrid_refuses_an_end_year_not_after_the_start(end):
    with pytest.raises(T.PlanningError) as exc:
        T.planning_spec("eh_microgrid", _planning(2030, end))
    assert exc.value.field == "planning.end_year"
    assert T.planning_spec("eh_microgrid", _planning(2030, 2045)) == T.Planning(
        (2030,), 2045
    )


def test_the_staged_microgrid_keeps_its_staged_planning():
    plan = T.planning_spec("eh_microgrid_staged", _planning(2027, 2045))
    assert len(plan.years) > 1 and plan.years[0] == 2027 and plan.end_year == 2045
    with pytest.raises(T.PlanningError):
        T.planning_spec("eh_microgrid_staged", _planning(2030, 2030))


# ── G4: the L2 resize, end to end on the shipped template ────────────────────


@pytest.fixture
def baseline(client, session_ctx, install_network, tmp_path):
    """The shipped eh_microgrid network as the run's project, its battery
    candidate capped at 5 MW (the seeded undersized battery), with the
    template's card and island settings (as `test_run_profile_h2_hub`)."""
    from services import chat_tools, project_registry
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    n = T.build_eh_microgrid()
    n.storage_units.at["bess_new", "p_nom_max"] = 5.0
    n.export_to_netcdf(tmp_path / "template.nc")
    install_network(pypsa.Network(tmp_path / "template.nc"))
    name = "Island Microgrid run"
    response = client.post(f"/api/projects/{name}")
    assert response.status_code == 200, response.text
    ctx = session_ctx(client)
    ctx.solver_state["solver_config"] = SolverConfig(
        **{**T.solver_config("eh_microgrid"), "target_lole_h": 2.0}
    )
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        with chat_tools._acting() as (db, user):
            project = project_registry.resolve_project(db, user, name)
            path = project_registry.project_dir(project)
        yield ctx, str(project.id), path
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


@pytest.fixture
def site(baseline):
    _, project_id, root = baseline
    (root / flow_service.TEMPLATE_FILE).write_text(
        json.dumps(T.TEMPLATE_META["eh_microgrid"])
    )
    (root / IC.SIDECAR_NAME).write_text(json.dumps(T.ISLAND_CONFIG["eh_microgrid"]))
    return project_id, root


def _l2_only(monkeypatch):
    """eh_microgrid@1 without its EH study, the facts that read it and the
    LOLE loop (the EH study and the loop run in `tests/test_harness_run_eh*`):
    the solve, the island check and L2 only. The stage file is PR B's."""
    profile = copy.deepcopy(run_profiles.PROFILES["eh_microgrid@1"])
    solve = profile["stages"]["solve"]
    solve["actions"] = [a for a in solve["actions"] if a["tool"] != "run_eh_study"]
    solve["rules"] = [r for r in solve["rules"] if r["fact"] != "eh.study.status"]
    gate = profile["stages"]["result_gate"]
    gate["rules"] = [
        r
        for r in gate["rules"]
        if not r.get("engine") and r["fact"] != "mg.island.critical_served"
    ]
    monkeypatch.setitem(
        run_profiles.PROFILES,
        "eh_microgrid@1",
        {k: v for k, v in profile.items() if k != "eh_study"},
    )
    monkeypatch.setattr(
        run_loops, "instruction", lambda *a: "Resize the battery within its bounds."
    )


def _check_by_cap(monkeypatch, need=30.0):
    """The island check: its one solve, then `survives` once the battery's cap
    on the network it was handed is at least `need` MW, else `fails` naming it."""
    seen = []

    def fake(n, island, *, solve=None):
        solve(n, None)
        cap = float(n.storage_units.at["bess_new", "p_nom_max"])
        seen.append(cap)
        if cap >= need:
            return MG._out(S.SURVIVES)
        return MG._out(
            S.FAILS,
            shortfalls=[
                {"row": "n_k_ride_through", "sentence": f"bess_new {cap:g} MW is short"}
            ],
        )

    monkeypatch.setattr(S, "survival", fake)
    monkeypatch.setattr(S, "_solve", lambda n, ef, **kw: ("ok", "optimal"))
    return seen


def _bess(value, n=0):
    from tests.test_harness_run_loops import call

    return call(
        "update_component",
        {
            "component_class": "StorageUnit",
            "name": "bess_new",
            "attrs": {"p_nom_max": value},
        },
        n,
    )


def _live():
    from services.pypsa_service import PyPSAService

    return PyPSAService.get_active_context()


def test_a_resize_at_the_bound_passes_and_the_island_check_runs_again_on_the_new_revision(
    granted, rated, monkeypatch
):
    from tests.test_harness_run_loops import Script, go, say
    from tests.test_harness_run_skeleton import RUN

    _l2_only(monkeypatch)
    seen = _check_by_cap(monkeypatch)
    task, path, _ = granted(run={**RUN, "profile": "eh_microgrid@1"})
    bound = run_profiles.resolve("eh_microgrid@1")["envelope"]["edits"][0]["bounds"][
        "max"
    ]
    script = Script(
        _bess(bound),
        say("The island check found the battery short; raised its cap to the bound."),
    )
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    run = record["run"]
    assert "loop" in run, (
        run["status"],
        run["stage"],
        [(s["id"], s["status"], s.get("error")) for s in record["steps"]],
    )
    first = run["loop"]["history"][0]
    survival = next(f for f in first["findings"] if f["rule"] == "mg.island.survival")
    assert survival["verdict"] == "fail" and survival["facts"][0]["value"] == "fails"
    assert run["loop"]["kind"] == "L2" and run["loop"]["ended"] == "passed", run["loop"]
    [row] = run["ledger"]
    assert row["target"] == {
        "component": "StorageUnit",
        "name": "bess_new",
        "attr": "p_nom_max",
    }
    assert row["new"] == bound and row["rule"] == "mg.island.survival"
    # The island check ran again, on the resized network's revision; the
    # first check is no evidence for it.
    checks = [s for s in record["steps"] if s["tool"] == run_actions.MG_SURVIVAL]
    assert len(checks) == 2 and seen == [5.0, bound]
    old, new = (s["result"]["network_revision"] for s in checks)
    assert new > old
    from services import dirty_state

    live = dirty_state.revision(_live())
    assert new == live
    digest = checks[0]["result"]["settings_digest"]
    first_check = {
        k: v for k, v in checks[0].items() if k != "superseded"
    }  # read as if it were the latest
    assert (
        run_actions.mg_outcome({"steps": [first_check]}, live, digest)["no_evidence"]
        == "edited"
    )
    verdicts = {r["rule"]: r for r in run["stages"]["result_gate"]["gate"]["rules"]}
    assert verdicts["mg.island.survival"]["verdict"] == "pass"
    assert verdicts["mg.island.survival"]["facts"][0]["value"] == "survives"
    assert float(_live().network.storage_units.at["bess_new", "p_nom_max"]) == bound


def test_a_resize_just_over_the_bound_is_refused_and_ends_needs_user(
    granted, rated, monkeypatch
):
    from tests.test_harness_run_loops import Script, go, refused, say
    from tests.test_harness_run_skeleton import RUN

    _l2_only(monkeypatch)
    _check_by_cap(monkeypatch)
    task, path, _ = granted(run={**RUN, "profile": "eh_microgrid@1"})
    bound = run_profiles.resolve("eh_microgrid@1")["envelope"]["edits"][0]["bounds"][
        "max"
    ]
    script = Script(
        _bess(bound + 0.1),
        say("Tried just over the bound."),
        say("Nothing more within the bounds."),
    )
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    assert record["run"]["status"] == "needs_user", record["run"]
    assert [t for t, _ in refused(path)] == ["update_component"]
    assert record["run"]["ledger"] == []
    assert float(_live().network.storage_units.at["bess_new", "p_nom_max"]) == 5.0
