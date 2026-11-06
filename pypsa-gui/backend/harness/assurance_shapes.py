"""Returned shapes of the version-1 assurance contracts (issue 02).

What a consumer reads: the public evidence record (`GET /api/chat/evidence/{id}`),
the answer receipt and the result a checked tool hands back to the model.
`assurance_contracts.build()` freezes their JSON schemas as `returns`
contracts; `tests/conftest.py` validates every record and projection the test
suite produces against these models, so a field added in code fails CI until
the contract is reviewed and the snapshot rewritten.

Each model forbids unknown fields. A change to an existing model breaks the
contract (owner decision AA-V1-CHANGE-1): add a new adapter version, policy
version or checked tool beside the old one instead.
"""

from __future__ import annotations

import functools
import operator
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from harness.assurance_scope import RequestScope


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


Number = int | float
Value = int | float | str | bool | None


# --- Evidence identity: one public evidence record per adapter version. ---


class Fact(_Closed):
    path: str
    entity_id: str
    metric: str
    value: Value
    unit: str | None
    formula_id: str | None = None  # computed facts name their formula


class Validity(_Closed):
    outcome: Literal["completed"]
    as_of: Number
    coverage: str
    warnings: list[str]


class CalculatedValidity(_Closed):
    outcome: Literal["completed", "partial"]
    as_of: Number
    coverage: Literal["full_horizon"]
    warnings: list[str]


class ScopedValidity(_Closed):
    """A scoped result states its slots' dispositions, not one outcome."""

    as_of: Number
    coverage: Literal["requested_population"]
    warnings: list[str]


class _Evidence(_Closed):
    version: Literal[1]
    evidence_id: str
    project_id: str
    facts: list[Fact]
    validity: Validity


class StaticScope(_Closed):
    kind: Literal["static"]
    component_class: str
    entity_id: str


class StaticPageScope(_Closed):
    kind: Literal["static_page"]
    component_class: str
    offset: int
    returned: int
    total_count: int
    has_more: bool


class SeriesWindow(_Closed):
    start: int
    end: int
    first: Value
    last: Value


class SeriesScope(_Closed):
    kind: Literal["series"]
    component_class: str
    component: str
    entity_id: str
    attribute: str
    points: int
    certified: int
    window: SeriesWindow | None = None


class AggregateLoadScope(_Closed):
    kind: Literal["aggregate_load"]
    selector: Literal["all", "section", "names"]
    entity_id: str
    component_class: Literal["Load"]
    attribute: Literal["p_set"]
    loads: int
    steps: int


class RunScope(_Closed):
    kind: Literal["run"]
    entity_id: str
    run_kind: str | None
    mode: str | None
    finished_at: str
    unsaved_inputs: bool


class RunSectionScope(_Closed):
    kind: Literal["run_section"]
    entity_id: str
    run_kind: str | None
    mode: str | None
    finished_at: str
    unsaved_inputs: bool
    section: str
    section_sha256: str


class RunComparisonScope(_Closed):
    kind: Literal["run_comparison"]
    entity_id: str
    baseline_run_id: str
    mode: str | None
    finished_at: str
    baseline_finished_at: str
    metrics: list[str] | None
    unsaved_inputs: bool


def _input(adapter: str, scope: type, source: str = "configured_input") -> type:
    return create_model(
        f"Evidence_{adapter.replace('-', '_').replace('/', '_v')}",
        __base__=_Evidence,
        __module__=__name__,
        source_kind=(Literal[source], ...),  # type: ignore[valid-type]
        adapter_version=(Literal[adapter], ...),  # type: ignore[valid-type]
        scope=(scope, ...),
    )


class CalculatedScope(_Closed):
    kind: Literal["full_horizon"]
    component_class: Literal["Generator"]
    entity_id: str
    start: str
    end: str


class ScopedScope(_Closed):
    kind: Literal["current_run", "retained_run"]
    entity_id: str
    start: str
    end: str


class Slot(_Closed):
    metric: str
    disposition: Literal["answered", "unavailable", "needs_clarification"]
    reason: str | None = None
    valid_snapshots: int | None = None
    requested_snapshots: int | None = None
    excluded_indices: list[str] | None = None  # snapshot labels
    partial_value: Value = None


class CalculatedEvidence(_Evidence):
    source_kind: Literal["calculated_result"]
    adapter_version: str
    scope: CalculatedScope
    slots: list[Slot]
    raw: dict
    validity: CalculatedValidity


class ScopedEvidence(_Evidence):
    source_kind: Literal["scoped_result"]
    adapter_version: str  # the profile id; `profiles` freezes the list
    request_scope: RequestScope
    scope: ScopedScope
    slots: list[Slot]
    raw: dict
    validity: ScopedValidity


# Keyed by `adapter_version`, as records.INPUT_ADAPTERS writes it; the two
# calculated kinds by `source_kind`, since their adapter is a profile id.
EVIDENCE = {
    "static-input/1": _input("static-input/1", StaticScope),
    "static-input-page/1": _input("static-input-page/1", StaticPageScope),
    "series-input/2": _input("series-input/2", SeriesScope),
    "aggregate-load/1": _input("aggregate-load/1", AggregateLoadScope),
    "run-metrics/1": _input("run-metrics/1", RunScope, "run_manifest"),
    "run-section/1": _input("run-section/1", RunSectionScope, "run_section"),
    "run-compare/1": _input("run-compare/1", RunComparisonScope, "run_comparison"),
    "calculated_result": CalculatedEvidence,
    "scoped_result": ScopedEvidence,
}


def evidence_model(record: dict) -> type[BaseModel] | None:
    """The model that freezes this public evidence record, or None if unknown."""
    if record.get("source_kind") in ("calculated_result", "scoped_result"):
        return EVIDENCE[record["source_kind"]]
    return EVIDENCE.get(record.get("adapter_version"))


# --- Answer receipt, keyed by policy version. ---

ReviewerStatus = Literal[
    "not_required",
    "passed",
    "failed",
    "unavailable",
    "withheld",
    "withheld_budget",
    "withheld_disabled",
    "withheld_invalid_draft",
    "withheld_limit",
    "withheld_tool_attempt",
    "withheld_usage",
    "withheld_schema",
    "withheld_incomplete",
    "withheld_failure",
    "withheld_timeout",
]


class ViewWindow(_Closed):
    start: str
    end: str


class Navigation(_Closed):
    action_id: str
    outcome: str
    shows: Literal["checked_evidence", "different_view", "not_confirmed"] | None = None
    reason: (
        Literal[
            "window_differs",
            "source_not_lopf",
            "inputs_changed",
            "fingerprint_basis_unknown",
            "project_differs",
        ]
        | None
    ) = None
    window: ViewWindow | None = None


class DerivedEntry(_Closed):
    """One software-recomputed derived claim (AA-03 S2); strict field by field."""

    model_config = ConfigDict(extra="forbid", strict=True)
    id: Annotated[str, Field(min_length=1, max_length=64)]
    formula_id: Annotated[str, Field(min_length=1, max_length=64)]
    operands: Annotated[list[str], Field(min_length=1, max_length=8)]
    value: Annotated[int | float, Field(allow_inf_nan=False)]
    unit: Annotated[str, Field(min_length=1, max_length=16)]


class _PredicateBase(_Closed):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: Annotated[str, Field(min_length=1, max_length=64)]
    evidence_id: Annotated[str, Field(min_length=1, max_length=128)]
    component_class: Annotated[str, Field(min_length=1, max_length=64)]
    metric: Annotated[str, Field(min_length=1, max_length=64)]
    unit: Annotated[str, Field(min_length=1, max_length=16)]
    # N: every entity of the one complete page, all certified (GK B C1).
    population: Annotated[int, Field(ge=1)]
    # The evidence record's validity warnings, kept as they are (GK B C3).
    warnings: Annotated[list[str], Field(max_length=20)]


class ThresholdPredicate(_PredicateBase):
    """All or none of the population meets the threshold; the software's truth."""

    quantifier: Literal["all", "none"]
    comparator: Literal[">", ">=", "<", "<="]
    threshold: Annotated[int | float, Field(allow_inf_nan=False)]
    holds: bool


class TopKPredicate(_PredicateBase):
    """The top k by value, every tie at the boundary included (owner freeze item 4)."""

    quantifier: Literal["top_k"]
    k: Annotated[int, Field(ge=1, le=20)]
    members: Annotated[list[str], Field(min_length=1, max_length=100)]


# One checked predicate (AA-03 S4); strict field by field.
PredicateEntry = Annotated[
    ThresholdPredicate | TopKPredicate, Field(discriminator="quantifier")
]


class Receipt(_Closed):
    policy_version: Literal["1"]
    content_sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    evidence_ids: Annotated[list[str], Field(max_length=20)]
    checked_block_ids: Annotated[list[str], Field(max_length=20)]
    limitations: Annotated[list[str], Field(max_length=20)]
    reviewer_status: ReviewerStatus
    reviewed_claim_ids: list[str]
    operation_ids: list[str] | None = None
    request_complete: bool | None = None
    # Echoes the turn's scope, a client clarification included, with the
    # server's `reason`; the `request_scope` contract freezes what is accepted.
    request_scope: dict | None = None
    result_slots: Annotated[list[Slot], Field(max_length=20)] | None = None
    # The chart's own shape is not frozen yet (see PENDING).
    chart: dict | None = None
    navigation: Annotated[list[Navigation], Field(max_length=20)] | None = None


class DerivedReceipt(Receipt):
    """Policy "2" (AA-03 S2, GK B P1/P2): policy "1" plus at least one derived entry.

    Emitted only for an answer with derived entries; every other answer stays "1".
    """

    policy_version: Literal["2"]
    derived: Annotated[list[DerivedEntry], Field(min_length=1, max_length=8)]


class PredicateReceipt(Receipt):
    """Policy "3" (AA-03 S4, GK B C8): policy "1" plus at least one predicate,
    and derived entries only when there are some (absent, never empty or null).
    """

    policy_version: Literal["3"]
    predicates: Annotated[list[PredicateEntry], Field(min_length=1, max_length=8)]
    derived: Annotated[  # type: ignore[assignment]  # absent by default, never null
        list[DerivedEntry], Field(default=None, min_length=1, max_length=8)
    ]


RECEIPTS = {"1": Receipt, "2": DerivedReceipt, "3": PredicateReceipt}


# --- Checked tool results, keyed by tool name. ---


class ToolAssurance(_Closed):
    version: Literal[1]
    outcome: Literal["completed", "submitted", "uncertain"]
    evidence_ids: list[str]


class EffectAssurance(_Closed):
    version: Literal[1]
    outcome: str
    operation_id: str | None


class Uncertain(_Closed):
    """No evidence was captured: the result is withheld but for its metadata."""

    assurance: ToolAssurance = Field(alias="_assurance")


class Row(BaseModel):
    """One list_components row: its name and each certified attribute."""

    model_config = ConfigDict(extra="allow")
    name: str


class ComponentResult(BaseModel):
    """get_component: each certified attribute at the top level."""

    model_config = ConfigDict(extra="allow")
    assurance: ToolAssurance = Field(alias="_assurance")


class PageResult(_Closed):
    items: list[Row]
    offset: int
    returned: int
    total_count: int
    has_more: bool
    assurance: ToolAssurance = Field(alias="_assurance")


class SeriesResult(_Closed):
    name: str
    attribute: str
    unit: str | None
    points: list[list[Value]]
    steps: int
    certified_steps: int
    window: SeriesWindow | None = None
    assurance: ToolAssurance = Field(alias="_assurance")


class AggregateResult(BaseModel):
    """get_aggregate_load: each certified statistic at the top level."""

    model_config = ConfigDict(extra="allow")
    selection: str
    attribute: str
    unit: str | None
    loads: int
    steps: int
    assurance: ToolAssurance = Field(alias="_assurance")


class MetricValue(_Closed):
    value: Value
    unit: str | None


class RunResult(_Closed):
    run_id: str
    status: Literal["completed"]
    finished_at: str
    mode: str | None
    unsaved_inputs: bool
    metrics: dict[str, MetricValue]
    assurance: ToolAssurance = Field(alias="_assurance")


class SectionValue(_Closed):
    path: str
    metric: str
    value: Value
    unit: str | None


class RunSectionResult(_Closed):
    run_id: str
    status: Literal["completed"]
    finished_at: str
    mode: str | None
    unsaved_inputs: bool
    section: str
    values: list[SectionValue]
    assurance: ToolAssurance = Field(alias="_assurance")


class RunRef(_Closed):
    run_id: str
    finished_at: str


class Delta(_Closed):
    delta: MetricValue | None = None
    delta_pct: Value = None


class CompareResult(_Closed):
    run: RunRef
    baseline: RunRef
    comparable: Literal[True]
    mode: str | None
    unsaved_inputs: bool
    deltas: dict[str, Delta]
    assurance: ToolAssurance = Field(alias="_assurance")


class Submitted(_Closed):
    assurance: ToolAssurance = Field(alias="_assurance")


class EffectResult(_Closed):
    assurance: EffectAssurance = Field(alias="_assurance")


TOOL_RESULTS = {
    "get_aggregate_load": [AggregateResult, Uncertain],
    "get_component": [ComponentResult],
    "get_run_evidence": [RunResult, RunSectionResult, Uncertain],
    "compare_project_runs": [CompareResult, Uncertain],
    "get_timeseries": [SeriesResult, Uncertain],
    "list_components": [PageResult, Uncertain],
    "ui_open_panel": [Submitted],
    "update_component": [EffectResult],
}


def schema(models) -> dict:
    """The JSON schema of one contract entry (a model or a list of alternatives)."""
    from pydantic import TypeAdapter

    models = models if isinstance(models, list) else [models]
    target = functools.reduce(operator.or_, models)
    return _without_notes(TypeAdapter(target).json_schema(by_alias=True))


# Map keywords whose keys are names (a property may be called "title").
_NAMED = {"properties", "$defs", "patternProperties"}


def _without_notes(node, named=False):
    """Drop `title` and `description`: a docstring edit is not a contract change."""
    if isinstance(node, list):
        return [_without_notes(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: _without_notes(value, key in _NAMED and not named)
        for key, value in node.items()
        if named or key not in ("title", "description")
    }


def validate_tool_result(tool_name: str, result: dict) -> None:
    """Raise ValueError unless `result` matches one of the tool's frozen shapes."""
    from pydantic import ValidationError

    errors = []
    for model in TOOL_RESULTS[tool_name]:
        try:
            model.model_validate(result)
            return
        except ValidationError as exc:
            errors.append(str(exc))
    raise ValueError(f"{tool_name} result outside its contract: " + " | ".join(errors))
