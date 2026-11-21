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


def test_a_transformer_without_reactive_flow_answers_its_rating_only(ac_grid, checked):
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
    # No active-power proxy: 50 MW against s_nom 40 is not read as loading.
    assert {f["metric"]: f["value"] for f in facts} == {"ac_branch_rating": 40.0}
    reasons = {s["metric"]: s.get("reason") for s in slots}
    assert reasons == {
        "ac_peak_branch_utilisation": "reactive_flow_unavailable",
        "ac_hours_above_rating": "reactive_flow_unavailable",
        "ac_peak_apparent_flow": "reactive_flow_unavailable",
        "ac_branch_rating": None,
    }
    assert ac.notes(raw, scope) == [
        "Transformer values cover 2 of 2 snapshots, all converged."
    ]
    frames = ac_ask_class(
        checked, ctx, "T12", "Transformer", {"ac_branch_rating": 40.0}
    )
    text = public_text(frames)
    assert "P proxy" not in text and "active power only" not in text


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
    data["q0"][0], data["q1"][0] = 0.0, 0.0
    facts, _ = _slots(raw, ctx)
    values = {f["metric"]: f["value"] for f in facts}
    assert values["ac_peak_branch_utilisation"] == pytest.approx(100.0)
    assert values["ac_hours_above_rating"] == 0


def test_a_branch_without_reactive_flow_names_why_its_flow_is_missing(ac_grid):
    ctx = ac_grid((True, False))
    raw = _raw(ctx)
    # Only the converged snapshot counts: a q0 at the excluded one changes nothing.
    raw["branch"]["q0"][0] = None
    raw["branch"]["q1"] = [None] * len(raw["branch"]["q1"])
    facts, reasons = _slots(raw, ctx)
    assert {f["metric"]: f["value"] for f in facts} == {"ac_branch_rating": 8.0}
    assert reasons == {
        **{m: "reactive_flow_unavailable" for m in ac.METRICS},
        "ac_branch_rating": None,
    }


def test_an_unverified_solve_without_reactive_flow_stays_unverified(ac_grid):
    ctx = ac_grid((True, False))
    raw = _raw(ctx)
    raw["branch"]["q0"] = [None] * len(raw["branch"]["q0"])
    facts, reasons = _slots({**raw, "valid_solve": False}, ctx)
    assert facts == []
    assert set(reasons.values()) == {"unverified_solve"}


@pytest.mark.parametrize("field", ["p1", "q1"])
def test_a_missing_terminal_1_flow_at_a_converged_snapshot_is_unavailable(
    ac_grid, field
):
    # Terminal 0 alone never stands in for the branch (GK B4 (1)).
    ctx = ac_grid((True, False))
    raw = _raw(ctx)
    raw["branch"][field][0] = None
    facts, reasons = _slots(raw, ctx)
    assert facts == []
    assert set(reasons.values()) == {"invalid_or_missing_branch_data"}


def test_terminal_1_carrying_the_larger_flow_sets_the_values(ac_grid):
    ctx = ac_grid((True, True))
    raw = _raw(ctx)
    data = raw["branch"]
    # Terminal 0: hypot(1, 1) and hypot(2, 1); terminal 1: hypot(6, 8) = 10 and
    # hypot(9, 0) = 9 MVA, both above the 8 MVA limit.
    data["p0"], data["q0"] = [1.0, 2.0], [1.0, 1.0]
    data["p1"], data["q1"] = [-6.0, -9.0], [-8.0, 0.0]
    data["s_max_pu"] = [1.0, 1.0]
    raw["energy_weight"] = [2.0, 0.5]
    facts, _ = _slots(raw, ctx)
    values = {f["metric"]: f["value"] for f in facts}
    assert values["ac_peak_branch_utilisation"] == pytest.approx(100 * 10 / 8)
    assert values["ac_peak_apparent_flow"] == pytest.approx(10.0)
    assert values["ac_hours_above_rating"] == pytest.approx(2.5)


def test_reactive_flow_at_only_some_converged_snapshots_is_unavailable(ac_grid):
    ctx = ac_grid((True, True))
    for k in (0, 1):
        raw = _raw(ctx)
        raw["branch"]["q0"][k] = None
        facts, reasons = _slots(raw, ctx)
        assert facts == []
        assert set(reasons.values()) == {"invalid_or_missing_branch_data"}


def test_a_zero_weight_snapshot_is_answered_and_counts_no_hours(ac_grid):
    ctx = ac_grid((True, True))
    raw = _raw(ctx)
    # 125 % at the first snapshot, weighted 0; 62.5 % at the second.
    raw["energy_weight"] = [0.0, 1.0]
    facts, _ = _slots(raw, ctx)
    values = {f["metric"]: f["value"] for f in facts}
    assert values["ac_peak_branch_utilisation"] == pytest.approx(125.0)
    assert values["ac_hours_above_rating"] == 0.0


class _Snapshots:
    """The network with another snapshot index (and investment periods)."""

    def __init__(self, n, snapshots, periods=()):
        self._n = n
        self.snapshots = snapshots
        self.investment_periods = pd.Index(periods)

    def __getattr__(self, attr):
        return getattr(self._n, attr)


@pytest.mark.parametrize(
    "scope", ["duplicate", "descending", "too_many", "multiindex", "periods", "range"]
)
def test_an_unsupported_snapshot_scope_is_refused(ac_grid, monkeypatch, scope):
    ctx = ac_grid((True, True))
    n = ctx.network
    t0, t1 = n.snapshots
    index, periods = {
        "duplicate": (pd.DatetimeIndex([t0, t0]), ()),
        "descending": (pd.DatetimeIndex([t1, t0]), ()),
        "too_many": (
            pd.date_range(t0, periods=calculations.MAX_SNAPSHOTS + 1, freq="h"),
            (),
        ),
        "multiindex": (pd.MultiIndex.from_product([[2030], [t0, t1]]), ()),
        "periods": (n.snapshots, (2030,)),
        "range": (pd.RangeIndex(2), ()),
    }[scope]
    monkeypatch.setattr(ctx, "network", _Snapshots(n, index, periods))
    with pytest.raises(ValueError, match="^unsupported_snapshot_scope$"):
        _raw(ctx)


def test_a_name_shared_by_a_line_and_a_transformer_is_ambiguous(ac_grid):
    ctx = ac_grid((True, True))
    ctx.network.add("Transformer", "L12", bus0="B1", bus1="B2", s_nom=40, x=0.1)
    with pytest.raises(ValueError, match="^ambiguous_branch$"):
        ac.source(ctx, "L12", None)
    assert ac.source(ctx, "L12", "Line")["branch"]["class"] == "Line"


def test_weights_not_aligned_with_the_snapshots_are_refused(ac_grid, monkeypatch):
    ctx = ac_grid((True, True))
    monkeypatch.setattr(
        period_utils,
        "snapshot_weights",
        lambda n, column="objective", sns=None: pd.Series(1.0, n.snapshots[::-1]),
    )
    with pytest.raises(ValueError, match="^misaligned_source$"):
        _raw(ctx)


@pytest.mark.parametrize(
    "change, matches",
    [
        ({"status": "failed"}, True),
        ({"condition": "lopf+ac_pf_failed"}, True),
        ({}, False),
    ],
)
def test_each_part_of_a_valid_solve_is_required(ac_grid, monkeypatch, change, matches):
    ctx = ac_grid((True, True))
    ctx.solver_state.update(change)
    monkeypatch.setattr(
        calculations, "solve_matches_inputs", lambda ctx, after_pf=False: matches
    )
    assert _raw(ctx)["valid_solve"] is False


def test_a_valid_solve_checks_the_inputs_after_the_power_flow(ac_grid, monkeypatch):
    ctx = ac_grid((True, True))
    monkeypatch.setattr(
        calculations,
        "solve_matches_inputs",
        lambda ctx, after_pf=False: after_pf is True,
    )
    assert _raw(ctx)["valid_solve"] is True


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


# ── guards from the mutants.py re-run at 7135343f (GK B4 (3)) ─────────────


def test_a_missing_convergence_mask_is_refused(ac_grid, monkeypatch):
    ctx = ac_grid((True, False))
    monkeypatch.setattr(lf, "_ac_convergence", lambda *a, **k: (None, None))
    with pytest.raises(ValueError, match="^ac_pf_convergence_unavailable$"):
        _raw(ctx)


def test_a_false_convergence_flag_stays_not_converged(ac_grid):
    ctx = ac_grid((True, False))
    assert _raw(ctx)["converged"] == [True, False]


@pytest.mark.parametrize(
    "converged, state",
    [
        ([True, False], "ac_pf_not_converged"),
        ([True, None], "ac_pf_convergence_unknown"),
        ([None, False], "ac_pf_convergence_unknown"),
        ([True, True], "converged"),
    ],
)
def test_the_window_state_names_unknown_only_when_a_flag_is_unknown(converged, state):
    raw = {"converged": converged, "index": ["t0", "t1"]}
    assert ac._convergence(raw, 0, 1)[2] == state


@pytest.mark.parametrize(
    "converged, state",
    [
        ((False, False), "ac_pf_not_converged"),
        ((None, False), "ac_pf_convergence_unknown"),
    ],
)
def test_calculate_refuses_a_window_with_nothing_converged(ac_grid, converged, state):
    ctx = ac_grid((True, True))
    raw = {**_raw(ctx), "converged": list(converged)}
    with pytest.raises(ValueError, match=f"^{state}$"):
        ac.calculate(raw, _scope(ctx))


def test_only_a_window_with_an_excluded_snapshot_names_it_on_each_slot(ac_grid):
    ctx = ac_grid((True, False))
    raw = _raw(ctx)
    _, slots = ac.calculate(raw, _scope(ctx))
    assert slots and all(s["excluded_indices"] == [raw["index"][1]] for s in slots)
    ctx = ac_grid((True, True))
    _, slots = ac.calculate(_raw(ctx), _scope(ctx))
    assert slots and not any("excluded_indices" in s for s in slots)


@pytest.mark.parametrize("rating", [0.0, float("nan"), float("inf")])
def test_a_branch_without_reactive_flow_and_an_invalid_rating_answers_nothing(
    ac_grid, rating
):
    ctx = ac_grid((True, False))
    raw = _raw(ctx)
    raw["branch"]["q0"] = [None] * len(raw["index"])
    raw["branch"]["q1"] = [None] * len(raw["index"])
    raw["branch"]["rating"] = rating
    facts, reasons = _slots(raw, ctx)
    assert facts == []
    assert set(reasons.values()) == {"invalid_or_missing_branch_data"}


@pytest.mark.parametrize(
    "rating, s_max_pu",
    [(8.0, float("inf")), (8.0, float("nan")), (1.0, 1e-9)],
)
def test_a_non_finite_or_vanishing_limit_is_unavailable(ac_grid, rating, s_max_pu):
    ctx = ac_grid((True, False))
    raw = _raw(ctx)
    raw["branch"]["rating"] = rating
    raw["branch"]["s_max_pu"][0] = s_max_pu
    facts, reasons = _slots(raw, ctx)
    assert facts == []
    assert set(reasons.values()) == {"invalid_or_missing_branch_data"}


def test_a_flow_exactly_at_the_tolerance_is_not_above_the_rating(ac_grid):
    ctx = ac_grid((True, False))
    raw = _raw(ctx)
    data = raw["branch"]
    edge = 1 + ac.branch.OVER_LIMIT_TOLERANCE
    data["rating"], data["s_max_pu"][0] = 1.0, 1.0
    data["p0"][0], data["p1"][0] = edge, -edge
    data["q0"][0], data["q1"][0] = 0.0, 0.0
    facts, _ = _slots(raw, ctx)
    values = {f["metric"]: f["value"] for f in facts}
    assert values["ac_hours_above_rating"] == 0
