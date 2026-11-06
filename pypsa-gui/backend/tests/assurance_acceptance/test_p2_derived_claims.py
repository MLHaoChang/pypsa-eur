"""AA-03 S2: derived claims are recomputed by the software, or the draft is limited.

GK B S2 verdict (issue 03, D-1 to D-8) and owner freeze AA03-FREEZE-1 item 2.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import json

import pytest
from pydantic import ValidationError

from harness import derived
from harness.assurance import Derived
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_capacity_cycles_assurance import PROFILES, solve
from tests.test_workflow_tools import baseline  # noqa: F401


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", ",".join(PROFILES))


# ── derived.check, one refusal per test ──────────────────────────────────


def record(run="R1", start="2026-01-01", end="2026-01-02", basis=None, project="P"):
    return {
        "project_id": project,
        "scope": {"kind": "run_section", "entity_id": run, "start": start, "end": end},
        "request_scope": {"cost_basis": basis} if basis else {},
    }


SCOPE = derived.scope_key(record())


def entry(formula, operands, value, unit, **extra):
    return Derived.model_validate(
        {
            "id": extra.pop("id", "d1"),
            "kind": "derived",
            "formula_id": formula,
            "operands": operands,
            "value": value,
            "unit": unit,
            **extra,
        }
    )


def published(**blocks):
    return {k: (v[0], v[1], v[2] if len(v) > 2 else SCOPE) for k, v in blocks.items()}


BASE = published(
    a=(30, "MW"),
    b=(20, "MW"),
    c=(5, "MW"),
    e=(10, "MWh"),
    p=(40, "%"),
    q=(25, "%"),
    s=(8, "MVA"),
)


def refused(entries, blocks=BASE):
    with pytest.raises(ValueError) as caught:
        derived.check(entries, blocks)
    return str(caught.value).removeprefix("derived_")


@pytest.mark.parametrize(
    ("formula", "operands", "value", "unit", "extra"),
    [
        ("difference/1", ["a", "b"], 10, "MW", {}),
        ("sum/1", ["a", "b", "c"], 55, "MW", {}),
        ("percent_change/1", ["a", "b"], 50, "%", {"denominator": "b"}),
        ("percentage_point_delta/1", ["p", "q"], 15, "pp", {}),
        ("share_percent/1", ["c"], 25, "%", {"basis": "b"}),
        ("unit_convert/1", ["a"], 30000, "kW", {"to_unit": "kW"}),
    ],
)
def test_each_registered_formula_is_recomputed(formula, operands, value, unit, extra):
    [public] = derived.check([entry(formula, operands, value, unit, **extra)], BASE)
    assert public["value"] == pytest.approx(value, rel=1e-12)
    assert public == {
        "id": "d1",
        "kind": "derived",
        "formula_id": formula,
        "operands": operands + [v for k, v in extra.items() if k == "basis"],
        "value": public["value"],
        "unit": unit,
    }


def test_an_unknown_formula_is_refused():
    assert refused([entry("ratio/1", ["a", "b"], 1.5, "MW")]) == "unknown_formula"


def test_the_wrong_number_of_operands_is_refused():
    assert refused([entry("difference/1", ["a"], 30, "MW")]) == "wrong_arity"
    assert refused([entry("difference/1", ["a", "a"], 0, "MW")]) == "wrong_arity"


def test_an_unpublished_operand_is_refused():
    # D-2: an id with no published checked number (an unavailable slot has none).
    assert (
        refused([entry("difference/1", ["a", "unavailable"], 10, "MW")])
        == "operand_not_published"
    )


def test_a_forward_or_self_reference_is_refused():
    later = entry("difference/1", ["a", "d2"], 0, "MW")
    second = entry("difference/1", ["a", "b"], 10, "MW", id="d2")
    assert refused([later, second]) == "forward_or_self_reference"
    assert (
        refused([entry("difference/1", ["a", "d1"], 0, "MW")])
        == "forward_or_self_reference"
    )


def test_an_earlier_entry_is_a_valid_operand():
    first = entry("difference/1", ["a", "b"], 10, "MW")
    second = entry("unit_convert/1", ["d1"], 10000, "kW", id="d2", to_unit="kW")
    assert [e["value"] for e in derived.check([first, second], BASE)] == [10, 10000]


@pytest.mark.parametrize(
    "other",
    [
        record(run="R2"),  # Q=(a): two runs
        record(end="2026-01-03"),  # another window
        record(
            basis={
                "currency": "EUR",
                "price_year": 2025,
                "annual_fixed_cost_fraction": 0.1,
            }
        ),
        record(project="P2"),
    ],
)
def test_operands_from_another_run_window_basis_or_project_are_refused(other):
    blocks = {**BASE, "x": (20, "MW", derived.scope_key(other))}
    assert (
        refused([entry("difference/1", ["a", "x"], 10, "MW")], blocks) == "mixed_scope"
    )


def test_power_against_energy_is_refused():
    assert (
        refused([entry("difference/1", ["a", "e"], 20, "MW")]) == "power_energy_refused"
    )
    assert (
        refused([entry("unit_convert/1", ["e"], 10, "MW", to_unit="MW")])
        == "power_energy_refused"
    )


def test_apparent_power_never_converts_to_active_power():
    # D-4: capacity/1 emits MVA for lines; MVA is not MW.
    assert (
        refused([entry("unit_convert/1", ["s"], 8, "MW", to_unit="MW")])
        == "unit_mismatch"
    )
    assert refused([entry("difference/1", ["a", "s"], 22, "MW")]) == "unit_mismatch"


def test_percent_on_percent_is_only_a_percentage_point_delta():
    assert refused([entry("difference/1", ["p", "q"], 15, "%")]) == "unit_mismatch"
    assert refused([entry("sum/1", ["p", "q"], 65, "%")]) == "unit_mismatch"
    assert (
        refused([entry("percent_change/1", ["p", "q"], 60, "%", denominator="q")])
        == "unit_mismatch"
    )
    assert (
        refused([entry("percentage_point_delta/1", ["a", "b"], 10, "pp")])
        == "unit_mismatch"
    )


def test_percentage_points_are_not_percent():
    assert (
        refused([entry("percentage_point_delta/1", ["p", "q"], 15, "%")])
        == "unit_mismatch"
    )


def test_percent_change_names_its_baseline_as_denominator():
    assert (
        refused([entry("percent_change/1", ["a", "b"], 50, "%")])
        == "missing_denominator"
    )
    # D-3: a published third block is not the baseline.
    assert (
        refused([entry("percent_change/1", ["a", "b"], 50, "%", denominator="c")])
        == "denominator_not_operand"
    )
    assert (
        refused([entry("percent_change/1", ["a", "b"], 50, "%", denominator="a")])
        == "denominator_not_operand"
    )


def test_share_percent_names_a_basis_in_the_parts_unit_and_scope():
    assert refused([entry("share_percent/1", ["c"], 25, "%")]) == "missing_basis"
    assert (
        refused([entry("share_percent/1", ["c"], 100, "%", basis="c")])
        == "wrong_operands"
    )
    assert (
        refused([entry("share_percent/1", ["c"], 50, "%", basis="e")])
        == "power_energy_refused"
    )
    other = {**BASE, "x": (20, "MW", derived.scope_key(record(run="R2")))}
    assert (
        refused([entry("share_percent/1", ["c"], 25, "%", basis="x")], other)
        == "mixed_scope"
    )


def test_an_option_the_formula_does_not_take_is_refused():
    assert (
        refused([entry("difference/1", ["a", "b"], 10, "MW", denominator="b")])
        == "wrong_operands"
    )
    assert (
        refused([entry("unit_convert/1", ["a"], 30000, "kW")]) == "missing_target_unit"
    )


def test_a_near_zero_denominator_is_refused():
    blocks = {**BASE, "z": (1e-12, "MW", SCOPE)}
    assert (
        refused(
            [entry("percent_change/1", ["a", "z"], 1, "%", denominator="z")], blocks
        )
        == "near_zero_denominator"
    )
    assert (
        refused([entry("share_percent/1", ["a"], 1, "%", basis="z")], blocks)
        == "near_zero_denominator"
    )


def test_a_value_off_by_more_than_the_tolerance_is_refused():
    # D-6: 1e-9 relative; a model rounding 33.333… to 33.33 limits the draft.
    blocks = {**BASE, "t": (30, "MW", SCOPE), "u": (90, "MW", SCOPE)}
    assert (
        refused([entry("share_percent/1", ["t"], 33.33, "%", basis="u")], blocks)
        == "value_mismatch"
    )
    [ok] = derived.check(
        [entry("share_percent/1", ["t"], 100 / 3, "%", basis="u")], blocks
    )
    assert ok["value"] == pytest.approx(100 / 3, rel=1e-12)


def test_a_wrong_output_unit_is_refused():
    assert refused([entry("difference/1", ["a", "b"], 10, "kW")]) == "unit_mismatch"


def test_kilowatts_convert_exactly_to_megawatts():
    blocks = {**BASE, "k": (1500, "kW", SCOPE)}
    [mw] = derived.check(
        [entry("unit_convert/1", ["k"], 1.5, "MW", to_unit="MW")], blocks
    )
    assert mw["value"] == 1.5
    assert (
        refused([entry("unit_convert/1", ["k"], 1.5000001, "MW", to_unit="MW")], blocks)
        == "value_mismatch"
    )


def test_a_repeated_or_colliding_id_is_refused():
    assert (
        refused([entry("difference/1", ["a", "b"], 10, "MW", id="a")]) == "repeated_id"
    )


def test_more_than_eight_entries_are_refused():
    entries = [
        entry("difference/1", ["a", "b"], 10, "MW", id=f"d{i}") for i in range(9)
    ]
    assert refused(entries) == "too_many"


def test_a_locale_string_or_an_expression_is_not_a_number():
    with pytest.raises(ValidationError):
        entry("difference/1", ["a", "b"], "1,5", "MW")
    with pytest.raises(ValidationError):
        entry("difference/1", ["a", "b"], 10, "MW", expression="a - b")


# ── end to end on capacity/1 (configured vs optimized) ───────────────────


@pytest.fixture
def grid(baseline):
    ctx = baseline[0]
    n = ctx.network
    n.generators.loc["G1", "p_nom_extendable"] = True
    n.generators.loc["G1", "p_nom_max"] = 1e6
    return ctx


METRICS = ["optimal_capacity", "configured_capacity"]


def ask(checked, grid, values, derive, *, interpretations=None):
    def mutate(ledger):
        ids = {b["metric"]: b["id"] for b in ledger["blocks"]}
        ledger["derived"] = derive(ids)
        if interpretations:
            ledger["interpretations"] = interpretations(ids)

    scope = {
        "version": 1,
        "profile": "capacity/1",
        "project_id": grid.project_uuid,
        "entity_id": "G1",
        "component_class": "Generator",
        "retain": False,
        "metrics": METRICS,
    }
    return checked.run(
        NumericalProvider(values, mutate), message="Check results.", checked_scope=scope
    )


def stored(checked, frames):
    response = checked.client.get("/api/chat/answers/" + approval(frames)["answer_id"])
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def values(grid, checked):
    solve(grid)
    g = grid.network.generators.loc["G1"]
    return {
        "optimal_capacity": float(g["p_nom_opt"]),
        "configured_capacity": float(g["p_nom"]),
    }


def change(ids, value):
    return [
        {
            "id": "built",
            "kind": "derived",
            "formula_id": "difference/1",
            "operands": [ids["optimal_capacity"], ids["configured_capacity"]],
            "value": value,
            "unit": "MW",
        }
    ]


def test_a_derived_difference_publishes_the_software_value(grid, checked, values):
    expected = values["optimal_capacity"] - values["configured_capacity"]
    frames = ask(checked, grid, values, lambda ids: change(ids, expected))
    data = approval(frames)
    assert data["verdict"] == "passed", data["receipt"]["limitations"]
    [entry] = data["receipt"]["derived"]
    assert entry["formula_id"] == "difference/1" and entry["value"] == expected
    assert set(entry["operands"]) <= set(data["receipt"]["checked_block_ids"])
    assert stored(checked, frames)["derived"][0]["value"] == expected
    assert "Derived built (difference/1" in public_text(frames)


def test_a_rounded_model_value_limits_the_draft(grid, checked, values):
    expected = values["optimal_capacity"] - values["configured_capacity"]
    frames = ask(
        checked, grid, values, lambda ids: change(ids, round(expected, 1) + 0.01)
    )
    data = approval(frames)
    assert data["verdict"] == "limited"
    assert (
        "derived" not in data["receipt"] and data["receipt"]["checked_block_ids"] == []
    )


def test_an_answer_without_derived_entries_is_stored_as_before(grid, checked, values):
    # D-1: no `derived` key at all in the record or the receipt.
    frames = ask(checked, grid, values, lambda ids: [])
    data = approval(frames)
    assert data["verdict"] == "passed", (
        data["receipt"]["limitations"],
        data["receipt"].get("result_slots"),
    )
    assert "derived" not in data["receipt"]
    assert "derived" not in stored(checked, frames)
    assert "Derived" not in public_text(frames)


def test_an_unavailable_slot_is_not_an_operand(grid, checked, values):
    # D-2: available_capacity is a named, unavailable slot with no block.
    def derive(ids):
        return [
            {
                "id": "x",
                "kind": "derived",
                "formula_id": "difference/1",
                "operands": [ids["optimal_capacity"], "available_capacity"],
                "value": 0,
                "unit": "MW",
            }
        ]

    scope_metrics = [*METRICS, "available_capacity"]
    frames = checked.run(
        NumericalProvider(
            values,
            lambda ledger: ledger.update(
                derived=derive({b["metric"]: b["id"] for b in ledger["blocks"]})
            ),
        ),
        message="Check results.",
        checked_scope={
            "version": 1,
            "profile": "capacity/1",
            "project_id": grid.project_uuid,
            "entity_id": "G1",
            "component_class": "Generator",
            "retain": False,
            "metrics": scope_metrics,
        },
    )
    assert approval(frames)["verdict"] == "limited"
    assert "derived" not in approval(frames)["receipt"]


def test_a_derived_value_cannot_put_a_number_in_the_prose(grid, checked, values):
    # D-5: the prose may not quote even a model-rounded derived value.
    expected = values["optimal_capacity"] - values["configured_capacity"]
    rounded = f"{expected:.1f}"
    frames = ask(
        checked,
        grid,
        values,
        lambda ids: change(ids, expected),
        interpretations=lambda ids: [
            {
                "id": "i1",
                "text": f"The solve built {rounded} MW more.",
                "block_ids": [ids["optimal_capacity"]],
            }
        ],
    )
    data = approval(frames)
    assert data["verdict"] == "limited"
    assert data["receipt"]["reviewer_status"] == "withheld_invalid_draft"
    assert f"built {rounded} MW more" not in public_text(frames)


def test_nine_derived_entries_limit_the_draft_instead_of_dropping_one(
    grid, checked, values
):
    # D-7: the receipt never loses a derived entry silently.
    expected = values["optimal_capacity"] - values["configured_capacity"]

    def derive(ids):
        return [{**change(ids, expected)[0], "id": f"d{i}"} for i in range(9)]

    data = approval(ask(checked, grid, values, derive))
    assert data["verdict"] == "limited" and "derived" not in data["receipt"]


def test_only_the_policy_two_receipt_shape_admits_derived_entries():
    from harness.assurance_shapes import RECEIPTS

    receipt = {
        "policy_version": "2",
        "content_sha256": "0" * 64,
        "evidence_ids": [],
        "checked_block_ids": [],
        "limitations": [],
        "reviewer_status": "not_required",
        "reviewed_claim_ids": [],
        "derived": [
            {
                "id": "d1",
                "formula_id": "difference/1",
                "operands": ["a", "b"],
                "value": 1.0,
                "unit": "MW",
            }
        ],
    }
    RECEIPTS["2"].model_validate(json.loads(json.dumps(receipt)))
    with pytest.raises(ValidationError):
        RECEIPTS["1"].model_validate({**receipt, "policy_version": "1"})
    with pytest.raises(ValidationError):
        RECEIPTS["2"].model_validate({**receipt, "derived": []})


# ── receipt policy "2" (GK B P1-P3) ──────────────────────────────────────

ENTRY = {
    "id": "d1",
    "formula_id": "difference/1",
    "operands": ["a", "b"],
    "value": 1,
    "unit": "MW",
}


@pytest.mark.parametrize(
    "receipt",
    [
        {"policy_version": "1", "derived": [ENTRY]},  # P1: "1" never carries derived
        {"policy_version": "2"},  # P2: "2" always does
        {"policy_version": "2", "derived": []},
        {"policy_version": "2", "derived": [ENTRY] * 9},
        {"policy_version": "2", "derived": [{**ENTRY, "expression": "a - b"}]},
        {"policy_version": "2", "derived": [{**ENTRY, "value": "1"}]},
        {
            "policy_version": "2",
            "derived": [{k: v for k, v in ENTRY.items() if k != "unit"}],
        },
        {"policy_version": "3", "derived": [ENTRY]},
    ],
)
def test_the_server_refuses_a_receipt_whose_policy_and_derived_entries_disagree(
    receipt,
):
    from services import assurance_records as records

    assert records.receipt_policy_ok(receipt) is False


def test_the_server_admits_policy_one_without_and_two_with_derived_entries():
    from services import assurance_records as records

    assert records.receipt_policy_ok({"policy_version": "1"}) is True
    assert (
        records.receipt_policy_ok({"policy_version": "2", "derived": [ENTRY]}) is True
    )


def test_only_an_answer_with_derived_entries_uses_policy_two(grid, checked, values):
    expected = values["optimal_capacity"] - values["configured_capacity"]
    with_derived = ask(checked, grid, values, lambda ids: change(ids, expected))
    assert approval(with_derived)["receipt"]["policy_version"] == "2"
    assert stored(checked, with_derived)["receipt"]["policy_version"] == "2"
    plain = ask(checked, grid, values, lambda ids: [])
    assert approval(plain)["receipt"]["policy_version"] == "1"
