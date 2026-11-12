# ruff: noqa: F811 -- imported pytest fixtures
# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 12c A2 (GK B (47) G5, G8; B12): the `ic.baseline_keeps`
reason in words at each of its readers, and the result gate's rules counted
by verdict, a rule not evaluated never counted as met, on the microgrid
profiles and on every profile whose output changes."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from harness import run_handoff, run_loops
from services import assistant_tasks, run_gates, run_profiles
from services import ic_counterfactual as ic
from services.reports import harness_chapter
from tests import test_run_action_island_survival as MG
from tests.test_campus_electrical_service import hub, user_and_db  # noqa: F401 - fixtures

BACKEND = Path(__file__).resolve().parents[1]


def _rules(ref, stage="result_gate"):
    return {r["id"]: r for r in run_profiles.resolve(ref)["stages"][stage]["rules"]}


# ── G5 (B12): the `ic.baseline_keeps` words reach each reader ────────────────

KEEPS = "mg.ic.baseline_keeps"
DROPS_WORDS = "The case's baseline drops the owned asset “diesel_1”"


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
            DROPS_WORDS
            + " · the case's baseline drops the owned asset “island_battery”",
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
        "run_handoff.checks",
    ):
        assert reader in listed


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


@pytest.mark.parametrize(
    "run", [{}, {"stages": None}, {"stages": {"result_gate": None}}]
)
def test_a_run_with_no_result_gate_yet_counts_nothing(run):
    assert run_handoff.checks(run) == {
        "counts": {"pass": 0, "fail": 0, "not_evaluated": 0},
        "not_evaluated": [],
    }


# ── G8 per profile: "Stated targets" and the not-evaluated count ────────────


def _verdicts(ref, unevaluated):
    """The profile's own result gate, read: every rule passes but those in
    `unevaluated`, which read not evaluated with a reason (warn rules only:
    the gate still passes)."""
    rules = run_profiles.resolve(ref)["stages"]["result_gate"]["rules"]
    assert set(unevaluated) <= {r["id"] for r in rules if r["severity"] == "warn"}, (
        unevaluated
    )
    out = [
        {
            "rule": r["id"],
            "severity": r["severity"],
            "verdict": "not_evaluated" if r["id"] in unevaluated else "pass",
            **(
                {"reason": "an input the row needs is missing"}
                if r["id"] in unevaluated
                else {}
            ),
            "facts": [{"fact": r["fact"], "value": None, "unit": None, "source": "x"}],
        }
        for r in rules
    ]
    gate = {"rules": out, "passed": run_gates.passed(out)}
    assert gate["passed"] is True
    return _run_with(gate), len(rules)


def _targets(run):
    return next(
        p
        for p in run_handoff.coverage({"run": run}, {}, None)
        if p["part"] == "Stated targets"
    )


@pytest.mark.parametrize(
    "ref, unevaluated",
    [
        ("eh_datacenter@1", ["dc.ramp_rate"]),
        ("eh_datacenter_staged@1", ["dc.ramp_rate"]),
        ("eh_h2_hub@1", ["h2.electrolyser.utilisation", "h2.ic.counterfactual"]),
        ("eh_h2_hub_staged@1", ["h2.electrolyser.utilisation", "h2.ic.counterfactual"]),
        ("eh_microgrid@1", ["mg.island.frequency", "mg.fuel.autonomy"]),
        ("eh_microgrid_staged@1", ["mg.island.frequency", "mg.fuel.autonomy"]),
    ],
)
def test_a_passed_gate_with_a_warn_not_evaluated_states_its_targets_partly(
    ref, unevaluated
):
    run, total = _verdicts(ref, unevaluated)
    targets = _targets(run)
    assert targets["status"] == run_handoff.PARTLY
    assert targets["basis"] == "the result gate passed; not evaluated: " + ", ".join(
        unevaluated
    )
    counts = run_handoff.checks(run)["counts"]
    assert counts == {
        "pass": total - len(unevaluated),
        "fail": 0,
        "not_evaluated": len(unevaluated),
    }


@pytest.mark.parametrize("ref", ["eh_site@1", "eh_site@2"])
def test_eh_site_has_no_warn_rule_so_its_targets_stay_covered(ref):
    rules = run_profiles.resolve(ref)["stages"]["result_gate"]["rules"]
    assert not [r for r in rules if r["severity"] == "warn"]
    run, total = _verdicts(ref, [])
    assert _targets(run) == {
        "part": "Stated targets",
        "kind": "target",
        "status": run_handoff.COVERED,
        "basis": "the result gate passed",
    }
    assert run_handoff.checks(run)["counts"] == {
        "pass": total,
        "fail": 0,
        "not_evaluated": 0,
    }
