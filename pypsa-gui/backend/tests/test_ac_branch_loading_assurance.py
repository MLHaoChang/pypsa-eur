"""AA-13 (A2.8): the AC branch-loading profile and the warned receipt policy.

`ac-branch-loading/1` reads the AC power flow's own result snapshot with the
Load flow tab's readers; these tests pin its numbers to the tab's
`_summarise_class`, its routing beside `branch-loading/1`, and policy "5".
"""

# ruff: noqa: F811 -- imported shared fixtures.
import pandas as pd
import pytest

from harness import ac_branch_calculations as ac
from harness import calculations
from harness.assurance_scope import from_message
from harness.assurance_shapes import RECEIPTS
from services import assurance_records as records
from services import loadflow_summary as lf
from services import period_utils, workflow_tools
from tests.assurance_acceptance.support import approval, public_text
from tests.assurance_acceptance.test_p2_request_slots import (  # noqa: F401
    AC,
    FIRST_ONLY,
    ac_ask,
    ac_grid,
    grid,
    private_store,
)
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401


def _raw(ctx, entity="L12", component_class="Line"):
    return ac.source(ctx, entity, component_class)


def _scope(ctx, **extra):
    return {
        "version": 1,
        "profile": AC,
        "project_id": ctx.project_uuid,
        "entity_id": "L12",
        "component_class": "Line",
        "retain": False,
        **extra,
    }


def test_ac_loading_matches_the_load_flow_tab_with_a_time_varying_limit(ac_grid):
    ctx = ac_grid((True, False))
    n = ctx.network
    n.lines_t.s_max_pu = pd.DataFrame({"L12": [0.8, 1.0]}, index=n.snapshots)
    # The limit is an input: record it as the solve's, as a solve with it would.
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    raw = _raw(ctx)
    facts, _ = calculations.scoped_calculate(raw, _scope(ctx))
    values = {f["metric"]: f["value"] for f in facts}
    index = n.snapshots
    mask, _ = lf._ac_convergence(n, index, 0, len(index) - 1, ctx.solver_state)
    weights = period_utils.snapshot_weights(n, "generators")
    rows, skipped = lf._summarise_class(
        n,
        "Line",
        "lines",
        "lines_t",
        "ac_pf",
        index,
        weights,
        mask,
        [],
        series_names={"L12"},
        state=ctx.solver_state,
    )
    assert not skipped
    (row,) = rows
    assert row["basis"] == "ac_apparent_power"
    # 10 MVA against 8 x 0.8 = 6.4 MVA at the only converged snapshot.
    assert values["ac_peak_branch_utilisation"] == pytest.approx(100 * 10 / 6.4)
    assert round(values["ac_peak_branch_utilisation"], 2) == row["util_pct"]
    assert round(values["ac_peak_apparent_flow"], 2) == row["peak_flow"]
    assert round(values["ac_hours_above_rating"], 2) == row["h_over_limit"]
    assert values["ac_branch_rating"] == row["rating"]["value"]


def test_a_transformer_without_reactive_flow_falls_back_to_p_and_says_so(
    ac_grid, checked
):
    ctx = ac_grid((True, True))
    n = ctx.network
    ctx.solver_state["ac_pf_results"].update(
        {
            "transformers_t.p0": pd.DataFrame({"T12": [30.0, 50.0]}, index=n.snapshots),
            "transformers_t.p1": pd.DataFrame(
                {"T12": [-30.0, -50.0]}, index=n.snapshots
            ),
        }
    )
    raw = _raw(ctx, "T12", "Transformer")
    scope = _scope(ctx, entity_id="T12", component_class="Transformer")
    facts, slots = calculations.scoped_calculate(raw, scope)
    values = {f["metric"]: f["value"] for f in facts}
    # P proxy: 50 MW against s_nom 40.
    assert values["ac_peak_branch_utilisation"] == pytest.approx(125.0)
    assert "ac_peak_apparent_flow" not in values
    reasons = {s["metric"]: s.get("reason") for s in slots}
    assert reasons["ac_peak_apparent_flow"] == "apparent_power_unavailable"
    assert ac.P_FALLBACK_NOTE in ac.notes(raw, scope)
    frames = ac_ask_class(
        checked,
        ctx,
        "T12",
        "Transformer",
        {
            "ac_peak_branch_utilisation": 125.0,
            "ac_hours_above_rating": 1.0,
            "ac_branch_rating": 40.0,
        },
    )
    assert ac.P_FALLBACK_NOTE in public_text(frames)


def ac_ask_class(checked, ctx, entity, component_class, values):
    from tests.test_capacity_cycles_assurance import ask

    return ask(checked, ctx, AC, entity, component_class, values)


def test_branch_loading_1_still_refuses_ac_results(ac_grid):
    ctx = ac_grid((True, True))
    with pytest.raises(ValueError, match="ac_pf_present"):
        calculations.branch.source(ctx, "L12", False, "Line")


def test_no_ac_results_refuses(grid):
    with pytest.raises(ValueError, match="ac_pf_not_available"):
        _raw(grid)


@pytest.mark.parametrize(
    "message",
    [
        "Show loading for network",
        "Show the loading for line L12",
        "Show the loading for line L12; chart loading, p0",
        "Show the loading for transformer T1",
        "show the loading for all lines; from 2026-01-01T00:00 to 2026-01-01T02:00",
    ],
)
def test_existing_loading_prompts_keep_their_profile(message):
    scope = from_message(message, "p", None)
    assert scope["profile"] in ("branch-loading/1", "network-loading/1")


@pytest.mark.parametrize(
    ("message", "entity", "component_class"),
    [
        ("Show AC branch loading for line L12", "L12", "Line"),
        ("show the ac loading for transformer T1.", "T1", "Transformer"),
    ],
)
def test_the_ac_form_picks_the_ac_profile(message, entity, component_class):
    scope = from_message(message, "p", None)
    assert (scope["profile"], scope["entity_id"], scope["component_class"]) == (
        AC,
        entity,
        component_class,
    )


def test_the_ac_form_has_no_network_or_population_scope():
    assert from_message("Show AC branch loading for network", "p", None) is None
    with pytest.raises(ValueError, match="unsupported_population_scope"):
        from harness.assurance_scope import validate

        validate(
            {
                "version": 1,
                "profile": AC,
                "project_id": "p",
                "entity_id": "network",
                "retain": False,
            }
        )


# ── policy "5" ───────────────────────────────────────────────────────────

WARNING = {
    "metric": "ac_pf_convergence",
    "disposition": "warning",
    "reason": "ac_pf_not_converged (1 of 2)",
    "valid_snapshots": 1,
    "requested_snapshots": 2,
    "excluded_indices": ["2026-01-01T01:00:00"],
}
ANSWERED = {
    "metric": "ac_branch_rating",
    "disposition": "answered",
    "valid_snapshots": 1,
    "requested_snapshots": 2,
    "excluded_indices": ["2026-01-01T01:00:00"],
}


def _receipt(version, slots, **extra):
    return {
        "policy_version": version,
        "content_sha256": "0" * 64,
        "evidence_ids": [],
        "checked_block_ids": [],
        "limitations": [],
        "reviewer_status": "not_required",
        "reviewed_claim_ids": [],
        "request_complete": False,
        "result_slots": slots,
        **extra,
    }


def test_policy_5_needs_a_warning_first_and_an_incomplete_request():
    assert records.receipt_policy_ok(_receipt("5", [WARNING, ANSWERED]))
    assert not records.receipt_policy_ok(_receipt("5", [ANSWERED]))
    assert not records.receipt_policy_ok(_receipt("5", [ANSWERED, WARNING]))
    assert not records.receipt_policy_ok(
        _receipt("5", [WARNING], request_complete=True)
    )
    assert not records.receipt_policy_ok(_receipt("5", [WARNING], derived=[]))


def test_only_policy_5_admits_a_warning_slot():
    for version in ("1", "2", "3"):
        with pytest.raises(ValueError):
            RECEIPTS[version].model_validate(_receipt(version, [WARNING]))


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("derived", [{"id": "d1"}]),  # would be policy "2"
        ("predicates", [{"id": "p1"}]),  # would be policy "3"
        ("sections", []),  # would be policy "4"
    ],
)
def test_a_warned_answer_that_would_carry_another_policy_fails_closed(
    ac_grid, checked, monkeypatch, attribute, value
):
    from harness import assurance

    ctx = ac_grid((True, False))
    real = assurance.Turn.validate

    def with_derived(self, blocks):
        out = real(self, blocks)
        setattr(self, attribute, value)
        return out

    monkeypatch.setattr(assurance.Turn, "validate", with_derived)
    receipt = approval(ac_ask(checked, ctx, FIRST_ONLY))["receipt"]
    assert receipt["policy_version"] == "5"
    assert not receipt.keys() & {"derived", "predicates", "sections"}
    assert receipt["evidence_ids"] == [] and receipt["checked_block_ids"] == []
    assert receipt["result_slots"][0]["disposition"] == "warning"


def test_a_warning_covers_only_a_window_with_an_excluded_snapshot(ac_grid):
    ctx = ac_grid((True, False))
    raw = _raw(ctx)
    warnings = calculations.required_warnings(raw, _scope(ctx))
    assert [w["metric"] for w in warnings] == ["ac_pf_convergence"]
    assert "required_warnings" in records.IDENTITY_KEYS
    assert (
        calculations.required_warnings(
            raw, _scope(ctx, start=raw["index"][0], end=raw["index"][0])
        )
        == []
    )


def test_every_other_profile_has_no_required_warnings(grid):
    scope = {
        "version": 1,
        "profile": "branch-loading/1",
        "project_id": "p",
        "entity_id": "L12",
        "component_class": "Line",
        "retain": False,
    }
    assert calculations.required_warnings({}, scope) == []
    assert calculations.MAX_RESULT_SLOTS == 20


def test_the_browser_words_every_warning_state():
    """UX 26 (AA-13): each warning reason code has the card's plain wording."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "frontend/src/utils/answerAssurance.ts"
    ).read_text()
    table = source[
        source.index("const WARNING_TEXT") : source.index(
            "export const WARNING_REASON_CODES"
        )
    ]
    states = {"ac_pf_not_converged", "ac_pf_convergence_unknown"}
    assert states <= ac.REFUSALS
    import re

    assert set(re.findall(r"^\s+(\w+): \(", table, re.M)) == states


# ── source and loading guards (mutant survivors, AA-13) ──────────────────


def _slots(raw, ctx, **extra):
    facts, slots = ac.calculate(raw, _scope(ctx, **extra))
    return facts, {s["metric"]: s.get("reason") for s in slots}


@pytest.mark.parametrize("ok", [0, 1, "", "false", "true"])
def test_a_non_bool_convergence_flag_reads_as_unknown(ac_grid, ok):
    ctx = ac_grid((True, False))
    ctx.solver_state["ac_pf_convergence_list"][1]["ok"] = ok
    assert _raw(ctx)["converged"] == [True, None]


def test_the_evidence_names_the_ac_disclosure(ac_grid):
    ctx = ac_grid((True, False))
    assert calculations.disclosure(_scope(ctx)) == ac.DISCLOSURE


def test_an_unknown_branch_is_refused(ac_grid):
    ctx = ac_grid((True, False))
    with pytest.raises(ValueError, match="^unknown_branch$"):
        ac.source(ctx, "nope", "Line")


@pytest.mark.parametrize(
    "change",
    [{"version": 2}, {"profile": "branch-loading/1"}, {"entity_id": "L13"}],
)
def test_calculate_refuses_a_source_of_another_request(ac_grid, change):
    ctx = ac_grid((True, False))
    with pytest.raises(ValueError, match="^ac_branch_source_identity$"):
        ac.calculate({**_raw(ctx), **change}, _scope(ctx))


def test_an_unverified_solve_answers_nothing(ac_grid):
    ctx = ac_grid((True, False))
    facts, reasons = _slots({**_raw(ctx), "valid_solve": False}, ctx)
    assert facts == []
    assert set(reasons.values()) == {"unverified_solve"}


@pytest.mark.parametrize(
    "field, value",
    [
        ("p0", None),
        ("s_max_pu", None),
        ("s_max_pu", 0.0),
        ("weight", None),
        ("weight", -1.0),
        ("rating", None),
        ("rating", 0.0),
    ],
)
def test_missing_branch_data_at_a_converged_snapshot_is_unavailable(
    ac_grid, field, value
):
    ctx = ac_grid((True, False))
    raw = _raw(ctx)
    if field == "weight":
        raw["energy_weight"][0] = value
    elif field == "rating":
        raw["branch"]["rating"] = value
    else:
        raw["branch"][field][0] = value
    facts, reasons = _slots(raw, ctx)
    assert facts == []
    assert set(reasons.values()) == {"invalid_or_missing_branch_data"}


def test_missing_data_at_an_excluded_snapshot_is_ignored(ac_grid):
    ctx = ac_grid((True, False))
    raw = _raw(ctx)
    raw["branch"]["p0"][1] = None
    facts, _ = _slots(raw, ctx)
    assert {f["metric"] for f in facts} == set(ac.METRICS)


def test_a_flow_exactly_at_the_limit_is_not_above_it(ac_grid):
    ctx = ac_grid((True, False))
    raw = _raw(ctx)
    data = raw["branch"]
    limit = data["rating"] * data["s_max_pu"][0]
    data["p0"][0], data["p1"][0] = limit, -limit
    data["q0"] = [None] * len(data["q0"])
    data["q1"] = [None] * len(data["q1"])
    facts, _ = _slots(raw, ctx)
    values = {f["metric"]: f["value"] for f in facts}
    assert values["ac_peak_branch_utilisation"] == pytest.approx(100.0)
    assert values["ac_hours_above_rating"] == 0


def test_a_p_only_branch_names_why_apparent_flow_is_missing(ac_grid):
    ctx = ac_grid((True, False))
    raw = _raw(ctx)
    raw["branch"]["q0"] = [None] * len(raw["branch"]["q0"])
    raw["branch"]["q1"] = [None] * len(raw["branch"]["q1"])
    facts, reasons = _slots(raw, ctx)
    assert reasons["ac_peak_apparent_flow"] == "apparent_power_unavailable"
    assert {f["metric"] for f in facts} == set(ac.METRICS) - {"ac_peak_apparent_flow"}


# ── a warned request tells the model what fails closed ────────────────────


class _Seen(ac_ask.__globals__["NumericalProvider"]):
    def __init__(self, values, seen):
        super().__init__(values)
        self.seen = seen

    def stream(self, request):
        content = request.messages[-1]["content"]
        self.seen.append(
            content
            if isinstance(content, str)
            else "".join(b.get("text", "") for b in content)
        )
        yield from super().stream(request)


@pytest.mark.parametrize(
    "converged, warned", [((True, False), True), ((True, True), False)]
)
def test_only_a_warned_request_forbids_derived_and_predicate_entries(
    ac_grid, checked, converged, warned
):
    from harness.assurance import WARNED_LEDGER_NOTE

    ctx = ac_grid(converged)
    seen = []
    scope = _scope(ctx)
    frames = checked.run(
        _Seen(FIRST_ONLY, seen), message="Check results.", checked_scope=scope
    )
    assert len(seen) == 1
    assert (WARNED_LEDGER_NOTE in seen[0]) is warned
    if warned:
        # The note costs a well-behaved draft nothing: every metric is answered.
        receipt = approval(frames)["receipt"]
        assert receipt["policy_version"] == "5"
        assert [s["disposition"] for s in receipt["result_slots"]] == ["warning"] + [
            "answered"
        ] * len(ac.METRICS)
