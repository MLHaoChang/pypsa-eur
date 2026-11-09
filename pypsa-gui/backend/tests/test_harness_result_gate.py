"""The result gate's scorecard rules (study harness issue 05; design §5,
GK A): `dc.readiness.<row>` and `dc.readiness.<family>:` gate by verdict.
Only `pass` satisfies a rule; `warn`, `unknown`, `fail` and a missing row
never do. A pass that rests on an assumed row is "within the screening
limit" on a warn rule, and not evaluated on a block rule (study harness 10a, D3). A family passes only when every row passes; zero rows is
`not_evaluated`."""

from __future__ import annotations

import pytest

from services import run_gates
from services.adequacy import grid_readiness as G

# Study harness 10a (D2): rows read only with the run's own readiness step's frozen profile.
CODE = {"id": "sagc_dc", "label": "SAGC (project)", "digest": "d" * 64}
READ = {
    "id": "solve.4",
    "tool": "dc_grid_readiness",
    "native": "action",
    "status": "completed",
    "args": {"frozen": "dc_grid_code"},
    "frozen": {"grid_code": CODE},
    "result": {"profile": CODE["id"], "digest": CODE["digest"], "rows_digest": "rows"},
}
ENV = dict(task={"steps": [READ]}, ctx=None, project=object(), project_dir=None)


@pytest.fixture(autouse=True)
def _frozen_profile_unchanged(monkeypatch):
    from services import run_actions

    monkeypatch.setattr(run_actions, "unchanged", lambda project, code: code == CODE)
    # the rows' digest is pinned in test_run_profile_datacenter; here every read matches the step's
    monkeypatch.setattr(run_actions, "rows_digest", lambda card: "rows")


def _row(id_, verdict, assumed=False):
    return {"id": id_, "verdict": verdict, "assumed": assumed}


def _gate(monkeypatch, rows, fact, severity="block", excluded=()):
    calls = []

    def card(project, profile=None):
        calls.append(project)
        return {"rows": rows, "excluded": list(excluded)}

    monkeypatch.setattr(G, "grid_readiness_for", card)
    rule = run_gates.check_rule(
        {
            "id": "res." + fact,
            "fact": fact,
            "op": "eq",
            "value": "pass",
            "severity": severity,
            "basis": "issue 05",
        }
    )
    results = run_gates.evaluate([rule], run_gates.Env(**ENV))
    return results[0], calls


@pytest.mark.parametrize("verdict", ["warn", "unknown", "fail"])
def test_only_pass_satisfies_a_scorecard_rule(monkeypatch, verdict):
    result, _ = _gate(monkeypatch, [_row("scr", verdict)], "dc.readiness.scr")
    assert result["verdict"] == "fail"
    assert not run_gates.passed([result])


def test_a_missing_row_fails_a_block_rule(monkeypatch):
    result, _ = _gate(monkeypatch, [_row("scr", "pass")], "dc.readiness.tdd")
    assert (
        result["verdict"] == "not_evaluated" and "no scorecard row" in result["reason"]
    )
    assert not run_gates.passed([result])


def test_a_verified_pass_passes_and_is_not_a_screening_pass(monkeypatch):
    result, _ = _gate(monkeypatch, [_row("scr", "pass")], "dc.readiness.scr")
    assert result["verdict"] == "pass" and "screening" not in result
    assert result["facts"][0]["rows"] == [
        {"id": "scr", "verdict": "pass", "assumed": False}
    ]


def test_an_assumed_pass_is_a_screening_pass_in_the_gate_record(monkeypatch):
    result, _ = _gate(
        monkeypatch,
        [_row("tdd", "pass", assumed=True)],
        "dc.readiness.tdd",
        severity="warn",
    )
    assert result["verdict"] == "pass"
    assert (
        result["screening"] is True and result["note"] == "within the screening limit"
    )
    assert result["facts"][0]["rows"][0]["assumed"] is True


def test_a_family_passes_only_when_every_row_passes(monkeypatch):
    rows = [
        _row("energization_peak:a", "pass"),
        _row("energization_peak:b", "pass"),
        _row("motor_start:m", "fail"),
    ]
    result, _ = _gate(monkeypatch, rows, "dc.readiness.energization_peak:")
    assert result["verdict"] == "pass"
    assert [r["id"] for r in result["facts"][0]["rows"]] == [
        "energization_peak:a",
        "energization_peak:b",
    ]
    rows[1] = _row("energization_peak:b", "warn")
    result, _ = _gate(monkeypatch, rows, "dc.readiness.energization_peak:")
    assert result["verdict"] == "fail"


def test_a_family_with_one_assumed_row_is_a_screening_pass(monkeypatch):
    rows = [_row("motor_start:a", "pass"), _row("motor_start:b", "pass", assumed=True)]
    result, _ = _gate(monkeypatch, rows, "dc.readiness.motor_start:", severity="warn")
    assert (
        result["verdict"] == "pass" and result["note"] == "within the screening limit"
    )


def test_a_family_with_zero_rows_is_not_evaluated(monkeypatch):
    excluded = [{"id": "motor_start:a", "reason": "off grid"}]
    result, _ = _gate(
        monkeypatch,
        [_row("scr", "pass")],
        "dc.readiness.motor_start:",
        excluded=excluded,
    )
    assert (
        result["verdict"] == "not_evaluated" and "no scorecard row" in result["reason"]
    )


def test_the_scorecard_is_read_once_per_gate(monkeypatch):
    calls = []

    def card(project, profile=None):
        calls.append(project)
        return {"rows": [_row("scr", "pass"), _row("tdd", "pass")], "excluded": []}

    monkeypatch.setattr(G, "grid_readiness_for", card)
    rules = [
        run_gates.check_rule(
            {
                "id": f"r.{i}",
                "fact": f"dc.readiness.{i}",
                "op": "eq",
                "value": "pass",
                "severity": "block",
                "basis": "issue 05",
            }
        )
        for i in ("scr", "tdd")
    ]
    results = run_gates.evaluate(rules, run_gates.Env(**ENV))
    assert [r["verdict"] for r in results] == ["pass", "pass"] and len(calls) == 1


@pytest.mark.parametrize(
    "fact",
    [
        "dc.readiness.*",  # a wildcard
        "dc.readiness.",  # no row
        "dc.readiness.ride_through:n1",  # one row of a family: a reader never picks one
        "dc.readiness.no_such_family:",  # an unknown family
    ],
)
def test_load_refuses_what_is_not_one_row_or_one_family(fact):
    with pytest.raises(run_gates.RuleError):
        run_gates.check_rule(
            {
                "id": "r",
                "fact": fact,
                "op": "eq",
                "value": "pass",
                "severity": "block",
                "basis": "issue 05",
            }
        )


@pytest.mark.parametrize(
    "op,value", [("ne", "fail"), ("in", ["pass", "warn"]), ("eq", "warn")]
)
def test_load_refuses_a_scorecard_rule_that_lets_anything_but_pass_through(op, value):
    with pytest.raises(run_gates.RuleError):
        run_gates.check_rule(
            {
                "id": "r",
                "fact": "dc.readiness.scr",
                "op": op,
                "value": value,
                "severity": "block",
                "basis": "issue 05",
            }
        )


def test_a_scorecard_rule_names_its_reader():
    rule = run_gates.check_rule(
        {
            "id": "r",
            "fact": "dc.readiness.ride_through:",
            "op": "eq",
            "value": "pass",
            "severity": "block",
            "basis": "issue 05",
        }
    )
    assert (
        rule["reads"]
        == "services.adequacy.grid_readiness.grid_readiness_for:rows[].verdict"
    )
