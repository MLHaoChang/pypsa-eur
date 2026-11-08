"""LC-T19 on a past run's kept sections (LC-03c).

A checked answer about a past run (`checked_run_section`) withholds or refuses
an injected extra number, a wrong unit, the current run's value quoted as the
past run's, and a claim from a section the run did not keep. One matrix per
kept family; every case has a positive control on the same path.
"""

# ruff: noqa: F811 -- imported shared fixtures.

import json

import pytest

from services import run_evidence
from tests.assurance_acceptance.support import (
    approval,
    public_text,
    text_turn,
    tool_turn,
)
from tests.assurance_acceptance.test_p1_revocation import revoke  # noqa: F401
from tests.assurance_acceptance.test_p1_run_as_of import publish
from tests.assurance_acceptance.test_p1_run_sections import section_result

# family -> (metric, unit, run-old value, run-new value, dimensionally wrong unit)
FAMILIES = {
    "loadflow": ("max_line_loading", "MW", 412.25, 377.5, "MWh"),
    "results_summary": ("objective", "EUR", 1250.5, 1310.75, "t"),
    "results_capacity": ("optimal_capacity", "MW", 85.5, 120.25, "MWh"),
    "results_dispatch": ("supply", "MWh", 9100.5, 8800.25, "MW"),
    "results_prices": ("mean_price", "EUR/MWh", 61.4, 73.9, "EUR"),
    "results_emissions": ("co2_emitted", "t", 5400.5, 4950.25, "EUR"),
}
OLD, NEW = "run-old", "run-new"
KEPT_AWAY = "results_gone"  # a section no run records


def section(metric, unit, value):
    return {"metrics": {metric: {"value": value, "unit": unit}}, "note": "text"}


@pytest.fixture
def runs(baseline):
    _ctx, project_id, root = baseline
    for run_id, when, index in (
        (OLD, "2026-10-02T10:00:00+00:00", 2),
        (NEW, "2026-10-03T10:00:00+00:00", 3),
    ):
        publish(
            root,
            project_id,
            run_id,
            "completed",
            when,
            sections={
                name: section(m, u, fam[index])
                for name, fam in FAMILIES.items()
                for m, u in [(fam[0], fam[1])]
            },
        )
    return baseline


def block(block_id, metric, value, unit, path, run_id=OLD):
    return {
        "id": block_id,
        "kind": "observed",
        "entity_id": run_id,
        "metric": metric,
        "value": value,
        "unit": unit,
        "source": {"tool_use_id": "read-section", "path": path},
    }


def run_turns(harness, family, blocks, read_section=None):
    text = json.dumps({"schema_version": 1, "blocks": blocks})
    return harness.run(
        [
            tool_turn(
                "get_run_evidence",
                {"run_id": OLD, "section": read_section or family},
                call_id="read-section",
            ),
            text_turn(text),
            text_turn(text),
        ]
    )


def true_block(family, block_id="true"):
    metric, unit, old, _new, _bad = FAMILIES[family]
    return block(block_id, metric, old, unit, f"/data/metrics/{metric}/value")


def checked_ids(approved):
    return set(approved["receipt"]["checked_block_ids"])


def fact_values(harness, approved):
    values = set()
    for evidence_id in approved["receipt"]["evidence_ids"]:
        record = harness.client.get(f"/api/chat/evidence/{evidence_id}").json()
        values |= {f["value"] for f in record["facts"]}
    return values


def assert_withheld(harness, frames, injected, block_id):
    """Limited; the injected block is not checked and its number not published."""
    approved = approval(frames)
    assert approved["verdict"] == "limited"
    assert block_id not in checked_ids(approved)
    assert injected not in fact_values(harness, approved)
    answer = harness.client.get(f"/api/chat/answers/{approved['answer_id']}")
    assert str(injected) not in answer.text
    assert str(injected) not in public_text(frames)


@pytest.mark.parametrize("family", FAMILIES)
def test_the_matrix_is_not_vacuous(family):
    _m, _u, old, new, bad = FAMILIES[family]
    assert old != new and abs(new - old) > 1 and round(new) != round(old)
    assert round(new, 1) != old


@pytest.mark.parametrize("family", FAMILIES)
def test_control_the_true_past_value_passes(runs, harness, family):
    frames = run_turns(harness, family, [true_block(family)])
    approved = approval(frames)
    assert approved["verdict"] == "passed"
    assert checked_ids(approved) == {"true"}
    metric, unit, old, _n, _b = FAMILIES[family]
    assert f"{metric.replace('_', chr(92) + '_')} = {old} {unit}" in public_text(frames)


@pytest.mark.parametrize("family", FAMILIES)
def test_an_extra_number_is_withheld(runs, harness, family):
    metric, unit, *_ = FAMILIES[family]
    extra = block(
        "extra", "ghost_metric", 31337, unit, "/data/metrics/ghost_metric/value"
    )
    frames = run_turns(harness, family, [true_block(family), extra])
    assert_withheld(harness, frames, 31337, "extra")
    # One unsupported block limits the whole answer: the true block is not published either.
    assert not checked_ids(approval(frames))


@pytest.mark.parametrize("family", FAMILIES)
def test_a_wrong_unit_is_withheld(runs, harness, family):
    metric, unit, old, _n, bad = FAMILIES[family]
    wrong = block("wrong", metric, old, bad, f"/data/metrics/{metric}/value")
    frames = run_turns(harness, family, [wrong])
    approved = approval(frames)
    assert approved["verdict"] == "limited" and not checked_ids(approved)
    assert f"{old} {bad}" not in public_text(frames)


@pytest.mark.parametrize("family", FAMILIES)
def test_a_scale_equivalent_unit_is_not_converted(runs, harness, family):
    """Units match exactly: a rescaled value in another unit never checks."""
    metric, unit, old, _n, _b = FAMILIES[family]
    scaled = block(
        "scaled", metric, old / 1000, f"k{unit}", f"/data/metrics/{metric}/value"
    )
    frames = run_turns(harness, family, [scaled])
    approved = approval(frames)
    assert approved["verdict"] == "limited" and not checked_ids(approved)


@pytest.mark.parametrize("variant", ["exact", "rounded", "integer"])
@pytest.mark.parametrize("family", FAMILIES)
def test_the_current_value_quoted_as_the_past_runs_is_withheld(
    runs, harness, family, variant
):
    metric, unit, old, new, _b = FAMILIES[family]
    value = {"exact": new, "rounded": round(new, 1), "integer": round(new)}[variant]
    assert value != old
    frames = run_turns(
        harness,
        family,
        [block("cur", metric, value, unit, f"/data/metrics/{metric}/value")],
    )
    approved = approval(frames)
    assert approved["verdict"] == "limited" and not checked_ids(approved)
    assert str(value) not in public_text(frames)


@pytest.mark.parametrize("family", FAMILIES)
def test_the_current_runs_id_cannot_borrow_the_past_runs_read(runs, harness, family):
    """The past run's value quoted for the current run's id is not checked."""
    frames = run_turns(harness, family, [{**true_block(family), "entity_id": NEW}])
    approved = approval(frames)
    assert approved["verdict"] == "limited" and not checked_ids(approved)


def stub_run(runs, family):
    _ctx, project_id, root = runs
    publish(
        root,
        project_id,
        "run-stub",
        "completed",
        "2026-10-04T10:00:00+00:00",
        sections={family: run_evidence.not_kept_stub("not digested", 99)},
    )


@pytest.mark.parametrize("family", FAMILIES)
def test_a_claim_from_a_not_kept_section_is_refused(runs, harness, family):
    stub_run(runs, family)
    metric, unit, old, *_ = FAMILIES[family]
    text = json.dumps(
        {
            "schema_version": 1,
            "blocks": [
                block(
                    "stub",
                    metric,
                    old,
                    unit,
                    f"/data/metrics/{metric}/value",
                    run_id="run-stub",
                )
            ],
        }
    )
    frames = harness.run(
        [
            tool_turn(
                "get_run_evidence",
                {"run_id": "run-stub", "section": family},
                call_id="read-section",
            ),
            text_turn(text),
            text_turn(text),
        ]
    )
    result = section_result(frames)
    assert result["error_kind"] == "run_not_checkable"
    assert "kept no data" in result["message"]
    approved = approval(frames)
    assert approved["verdict"] == "limited" and not approved["receipt"]["evidence_ids"]
    assert str(old) not in public_text(frames)


@pytest.mark.parametrize("family", FAMILIES)
def test_a_claim_from_an_unrecorded_section_is_refused(runs, harness, family):
    metric, unit, old, *_ = FAMILIES[family]
    frames = run_turns(
        harness,
        family,
        [block("gone", metric, old, unit, f"/data/metrics/{metric}/value")],
        read_section=KEPT_AWAY,
    )
    result = section_result(frames)
    assert result["error_kind"] == "run_not_checkable"
    assert "does not record this section" in result["message"]
    approved = approval(frames)
    assert approved["verdict"] == "limited" and not approved["receipt"]["evidence_ids"]


@pytest.mark.parametrize("family", FAMILIES)
def test_control_a_kept_section_beside_a_stub_still_checks(runs, harness, family):
    """The stub path's control: the same read and draft on a kept section passes."""
    stub_run(runs, family)
    frames = run_turns(harness, family, [true_block(family)])
    assert approval(frames)["verdict"] == "passed"


# Mutants: each must make the matrix fail.
@pytest.mark.parametrize("family", FAMILIES)
def test_mutant_the_past_run_reading_the_current_runs_facts_is_caught(
    runs, harness, monkeypatch, family
):
    real = run_evidence.checked_run_section
    monkeypatch.setattr(
        run_evidence, "checked_run_section", lambda run_id, sec: real(NEW, sec)
    )
    metric, unit, old, new, _b = FAMILIES[family]
    frames = run_turns(harness, family, [true_block(family)])
    # With the mutant, the true past value no longer checks: the control fails.
    assert approval(frames)["verdict"] == "limited"


@pytest.mark.parametrize("family", FAMILIES)
def test_mutant_a_stub_read_as_evidence_is_caught(runs, harness, monkeypatch, family):
    """With a stub admitted as data, the not-kept refusal test's read succeeds."""
    stub_run(runs, family)
    real = run_evidence.checked_run_section

    def lax(run_id, sec):
        project_id, manifest, data, status = real(run_id, sec)
        if status == run_evidence.NOT_KEPT:
            _p, manifest, _d, _s = real(OLD, sec)
            return project_id, manifest, section(*FAMILIES[family][:2], 1), "completed"
        return project_id, manifest, data, status

    monkeypatch.setattr(run_evidence, "checked_run_section", lax)
    with pytest.raises(AssertionError):
        test_a_claim_from_a_not_kept_section_is_refused(runs, harness, family)
