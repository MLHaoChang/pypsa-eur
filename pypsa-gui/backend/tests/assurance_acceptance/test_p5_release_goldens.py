"""Release goldens (AA-03 S5): every valid golden answered, zero false accepts.

One valid golden per checked answer kind the acceptance fixtures reach, each
driven through the authenticated /api/chat/stream seam with a scripted model
(no paid inference). Each golden has hand-written fault variants; a fault must
end limited for its own reason (the refusal `Turn.validate` raised, or the
interpretation marker), never on an incidental failure (#807 decoy rule). The
oracles are literals written by hand from the fixtures, not service output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from harness import assurance, interpretation_markers
from tests.assurance_acceptance.support import (
    approval,
    observed_draft,
    public_text,
    text_turn,
    tool_turn,
)
from tests.assurance_acceptance.test_p1_aggregate import loads  # noqa: F401
from tests.assurance_acceptance.test_p1_run_compare import runs  # noqa: F401
from tests.assurance_acceptance.test_p1_timeseries import series  # noqa: F401
from tests.assurance_acceptance.test_p4_population_predicates import add_loads


def _block(id, entity, metric, value, unit, call, path):
    return {
        "id": id,
        "kind": "observed",
        "entity_id": entity,
        "metric": metric,
        "value": value,
        "unit": unit,
        "source": {"tool_use_id": call, "path": path},
    }


def _draft(*blocks, **extra):
    return json.dumps({"schema_version": 1, "blocks": list(blocks), **extra})


@dataclass(frozen=True)
class Golden:
    kind: str
    fixture: str
    tool: str
    args: dict
    call: str
    valid: str
    # A phrase only the valid golden's software answer shows.
    shown: str
    receipt_policy: str
    # fault name -> (draft text, the refusal it must trip)
    faults: dict = field(default_factory=dict)
    setup: tuple = ()


def _fact(value=5, unit="MW", entity="L1", path="/p_set", call="read-load"):
    return _block("load-input", entity, "p_set", value, unit, call, path)


def _row(value=5, entity="L1", path="/L1/p_set", call="list-loads"):
    return _block("fact-1", entity, "p_set", value, "MW", call, path)


def _step(value=5.5, path="/L1/2026-01-01T01:00:00"):
    return _block("series-step", "L1", "p_set", value, "MW", "read-series", path)


def _aggregate(metric="peak", value=8.0, unit="MW", path=None):
    return _block(
        "aggregate",
        "electricity",
        metric,
        value,
        unit,
        "read-aggregate",
        path or f"/{metric}",
    )


def _run(value=110, unit="EUR", path="/objective", entity="run-new"):
    return _block("objective", entity, "objective", value, unit, "read-run", path)


def _delta(value=10, unit="EUR", path="/deltas/objective/delta/value"):
    return _block("delta", "run-new", "objective", value, unit, "compare", path)


def _derived(value=5000, formula="unit_convert/1", to_unit="kW"):
    return {
        "id": "d1",
        "kind": "derived",
        "formula_id": formula,
        "operands": ["load-input"],
        "to_unit": to_unit,
        "value": value,
        "unit": "kW",
    }


def _predicate(**fields):
    data = {
        "id": "p1",
        "kind": "predicate",
        "quantifier": "all",
        "source": {"tool_use_id": "list-loads"},
        "metric": "p_set",
        "unit": "MW",
        "comparator": ">=",
        "threshold": 5,
        "holds": True,
    }
    return {**data, **fields}


def _interpreted(text):
    return _draft(
        _fact(),
        interpretations=[{"id": "i1", "text": text, "block_ids": ["load-input"]}],
    )


# The observed-claim check compares entity, metric, value, unit and source
# together, so every such fault trips this one reason; the decoy guard below
# makes each differ from its golden in exactly one field.
MISMATCH = "claim does not match exact evidence"
UNKNOWN = "unknown source"

COMPONENT = {"component_class": "Load", "name": "L1"}
PAGE = {"component_class": "Load", "limit": 20}

GOLDENS = (
    Golden(
        "component_fact",
        "baseline",
        "get_component",
        COMPONENT,
        "read-load",
        observed_draft(),
        "5 MW",
        "1",
        faults={
            "wrong_value": (_draft(_fact(value=6)), MISMATCH),
            "wrong_unit": (_draft(_fact(unit="kW")), MISMATCH),
            "wrong_entity": (_draft(_fact(entity="L9")), MISMATCH),
            "wrong_path": (_draft(_fact(path="/q_set")), MISMATCH),
            "unread_source": (_draft(_fact(call="never-called")), UNKNOWN),
            "marker_interpretation": (
                _interpreted("The configured load is the largest."),
                "interpretation_marker",
            ),
        },
    ),
    Golden(
        "page_row",
        "baseline",
        "list_components",
        PAGE,
        "list-loads",
        _draft(_row()),
        "5 MW",
        "1",
        faults={
            "wrong_value": (_draft(_row(value=7)), MISMATCH),
            "row_of_another_entity": (_draft(_row(path="/L2/p_set")), MISMATCH),
        },
        setup=(("L2", 7),),
    ),
    Golden(
        "series_step",
        "series",
        "get_timeseries",
        {"component": "loads", "name": "L1", "attribute": "p_set"},
        "read-series",
        _draft(_step()),
        "5.5 MW",
        "1",
        faults={
            "wrong_value": (_draft(_step(value=4.0)), MISMATCH),
            "missing_step": (_draft(_step(path="/L1/2026-01-01T02:00:00")), MISMATCH),
        },
    ),
    Golden(
        "aggregate_load",
        "loads",
        "get_aggregate_load",
        {"section": "electricity"},
        "read-aggregate",
        _draft(_aggregate()),
        "8 MW",
        "1",
        faults={
            "wrong_peak": (_draft(_aggregate(value=10.0)), MISMATCH),
            "mean_called_peak": (_draft(_aggregate(path="/mean")), MISMATCH),
        },
    ),
    Golden(
        "run_evidence",
        "runs",
        "get_run_evidence",
        {"run_id": "run-new"},
        "read-run",
        _draft(_run()),
        "110 EUR",
        "1",
        faults={
            "wrong_value": (_draft(_run(value=100)), MISMATCH),
            "other_run": (_draft(_run(entity="run-base")), MISMATCH),
        },
    ),
    Golden(
        "run_comparison",
        "runs",
        "compare_project_runs",
        {"run_id": "run-new", "baseline_run_id": "run-base"},
        "compare",
        _draft(_delta()),
        "10 EUR",
        "1",
        faults={
            "wrong_delta": (_draft(_delta(value=-10)), MISMATCH),
            "percent_as_eur": (
                _draft(_delta(path="/deltas/objective/delta_pct")),
                MISMATCH,
            ),
        },
    ),
    Golden(
        "derived",
        "baseline",
        "get_component",
        COMPONENT,
        "read-load",
        _draft(_fact(), derived=[_derived()]),
        "5000 kW",
        "2",
        faults={
            "wrong_value": (
                _draft(_fact(), derived=[_derived(value=5001)]),
                "derived_value_mismatch",
            ),
            "unknown_formula": (
                _draft(_fact(), derived=[_derived(formula="scale/1")]),
                "derived_unknown_formula",
            ),
            "power_to_energy": (
                _draft(_fact(), derived=[{**_derived(to_unit="MWh"), "unit": "MWh"}]),
                "derived_power_energy_refused",
            ),
        },
    ),
    Golden(
        "predicate",
        "baseline",
        "list_components",
        PAGE,
        "list-loads",
        _draft(_row(), predicates=[_predicate()]),
        "every one of all 3 Load rows",
        "3",
        faults={
            "wrong_truth": (
                _draft(_row(), predicates=[_predicate(holds=False)]),
                "predicate_truth_mismatch",
            ),
            "wrong_top_k": (
                _draft(
                    _row(),
                    predicates=[
                        _predicate(
                            quantifier="top_k",
                            comparator=None,
                            threshold=None,
                            holds=None,
                            k=1,
                            members=["L1"],
                        )
                    ],
                ),
                "predicate_members_mismatch",
            ),
        },
        setup=(("L2", 7), ("L3", 9)),
    ),
)

FAULTS = tuple(
    (golden, name, text, reason)
    for golden in GOLDENS
    for name, (text, reason) in golden.faults.items()
)

# Checked answer kinds the acceptance fixtures cannot reach here, each named
# with its owner; recorded, never skipped.
_pending = {
    "computed_profiles": (
        "covered by the pilot goldens in tests/test_pilot_evaluation.py "
        "(five profiles, adverse drafts 0 accepted)"
    ),
}


def _reason(exc):
    if isinstance(exc, HTTPException):
        detail = exc.detail
        return detail.get("error_kind") if isinstance(detail, dict) else str(detail)
    if isinstance(exc, ValidationError):
        return "schema:" + exc.errors()[0]["type"]
    return str(exc.args[0]) if exc.args else type(exc).__name__


@pytest.fixture
def refusals(monkeypatch):
    seen = []
    real_validate = assurance.Turn.validate
    real_find = interpretation_markers.find

    def validate(self, blocks):
        try:
            return real_validate(self, blocks)
        except Exception as exc:
            seen.append(_reason(exc))
            raise

    def find(text):
        hit = real_find(text)
        if hit:
            seen.append("interpretation_marker")
        return hit

    monkeypatch.setattr(assurance.Turn, "validate", validate)
    monkeypatch.setattr(interpretation_markers, "find", find)
    return seen


def _ask(request, harness, golden, text):
    request.getfixturevalue(golden.fixture)
    if golden.setup:
        add_loads(**dict(golden.setup))
    return harness.run(
        [
            tool_turn(golden.tool, golden.args, call_id=golden.call),
            text_turn(text),
            text_turn(text),
        ]
    )


@pytest.mark.parametrize("golden", GOLDENS, ids=lambda g: g.kind)
def test_every_valid_golden_is_answered(request, harness, refusals, golden):
    frames = _ask(request, harness, golden, golden.valid)
    approved = approval(frames)
    assert approved["verdict"] == "passed", approved["receipt"].get("limitations")
    assert approved["receipt"]["policy_version"] == golden.receipt_policy
    assert golden.shown in public_text(frames)
    assert refusals == []


@pytest.mark.parametrize(
    ("golden", "fault", "text", "reason"),
    FAULTS,
    ids=[f"{g.kind}-{name}" for g, name, _, _ in FAULTS],
)
def test_every_fault_is_refused_for_its_own_reason(
    request, harness, refusals, golden, fault, text, reason
):
    frames = _ask(request, harness, golden, text)
    approved = approval(frames)
    assert approved["verdict"] == "limited"
    assert reason in refusals, refusals


def _leaves(value, prefix=""):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out.update(_leaves(item, f"{prefix}/{key}"))
        return out
    if isinstance(value, list):
        out = {}
        for index, item in enumerate(value):
            out.update(_leaves(item, f"{prefix}/{index}"))
        return out
    return {prefix: value}


@pytest.mark.parametrize(
    ("golden", "fault", "text"),
    [(g, name, text) for g, name, text, _ in FAULTS],
    ids=[f"{g.kind}-{name}" for g, name, _, _ in FAULTS],
)
def test_each_fault_changes_one_thing_of_its_golden(golden, fault, text):
    """Decoy guard: a fault cannot pass on an unrelated defect."""
    valid, broken = _leaves(json.loads(golden.valid)), _leaves(json.loads(text))
    changed = {k for k in valid.keys() | broken.keys() if valid.get(k) != broken.get(k)}
    if fault == "marker_interpretation":
        assert changed == {
            "/interpretations/0/id",
            "/interpretations/0/text",
            "/interpretations/0/block_ids/0",
        }
    elif fault == "power_to_energy":
        assert changed == {"/derived/0/to_unit", "/derived/0/unit"}
    elif fault == "wrong_top_k":
        assert {k.split("/")[-1] for k in changed} == {
            "quantifier",
            "comparator",
            "threshold",
            "holds",
            "k",
            "0",
        }
    else:
        assert len(changed) == 1, changed


def test_the_release_denominators_are_exact():
    # From the literal fixture lists, not from counted results.
    assert len(GOLDENS) == 8
    assert len(FAULTS) == 21
    assert {g.kind for g in GOLDENS} == {
        "component_fact",
        "page_row",
        "series_step",
        "aggregate_load",
        "run_evidence",
        "run_comparison",
        "derived",
        "predicate",
    }
    assert set(_pending) == {"computed_profiles"}
