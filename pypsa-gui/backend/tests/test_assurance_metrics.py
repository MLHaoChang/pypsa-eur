"""Independent numerical oracles through the authenticated publication boundary."""

# ruff: noqa: F811 -- pytest fixtures imported from shared project setup.
import json
import re

import pandas as pd
import pytest

from harness.providers.fake import FakeProvider
from services import assurance_metrics as metrics, workflow_tools
from tests.assurance_acceptance.support import approval, public_text, text_turn
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401


@pytest.fixture
def solved(baseline):
    ctx = baseline[0]
    n = ctx.network
    n.loads_t.p_set = pd.DataFrame({"L1": [5.0, 7.0]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "generators"] = [2.0, 1.0]
    n.snapshot_weightings.loc[:, "objective"] = [2.0, 1.0]
    n.generators.at["G1", "capital_cost"] = 3
    assert n.optimize(solver_name="highs") == ("ok", "optimal")
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    return ctx


class NumericalProvider(FakeProvider):
    def __init__(self, values=None, mutate=None):
        super().__init__([])
        self.values = (
            values
            if values is not None
            else {
                "dispatch_energy": 17,
                "marginal_expenditure": 170,
                "peak_dispatch": 7,
            }
        )
        self.mutate = mutate

    def stream(self, request):
        content = request.messages[-1]["content"]
        text = (
            content
            if isinstance(content, str)
            else "\n".join(b.get("text", "") for b in content)
        )
        matches = re.findall(
            r'<untrusted_data>(\{"schema_version".*?\})</untrusted_data>', text
        )
        assert len(matches) == 1
        ledger = json.loads(matches[0])
        # The model supplies independently specified values; only opaque source
        # IDs/paths are taken from the evidence offered to this request.
        for block in ledger["blocks"]:
            block["value"] = self.values[block["metric"]]
        if self.mutate:
            self.mutate(ledger)
        self._turns.append(text_turn(json.dumps(ledger)))
        yield from super().stream(request)


def run(checked, values=None, mutate=None, **options):
    return checked.run(
        NumericalProvider(values, mutate),
        message="Give me the key results for generator G1",
        **options,
    )


def test_independent_lp_results_and_cost_basis(solved, checked):
    frames = run(checked)
    receipt = approval(frames)["receipt"]
    assert approval(frames)["verdict"] == "passed"
    assert "Generator energy: 17 MWh" in public_text(frames)
    assert "Marginal dispatch expenditure: 170 EUR" in public_text(frames)
    assert "Peak generator output: 7 MW" in public_text(frames)
    assert "not total system cost" in public_text(frames)
    assert len(receipt["result_slots"]) == 3
    assert all(s["disposition"] == "answered" for s in receipt["result_slots"])
    request = checked.provider.requests[0]
    assert '"entity_id": "G1"' not in str(request.system_blocks)
    answer = checked.client.get("/api/chat/answers/" + approval(frames)["answer_id"])
    assert answer.status_code == 200


def test_distinct_energy_and_objective_weights(solved, checked):
    solved.network.snapshot_weightings.loc[:, "objective"] = [1.0, 1.0]
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    frames = run(
        checked,
        {"dispatch_energy": 17, "marginal_expenditure": 120, "peak_dispatch": 7},
    )
    assert approval(frames)["verdict"] == "passed"
    assert "120 EUR" in public_text(frames)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 75])
def test_missing_or_out_of_bounds_dispatch_never_becomes_full_horizon(
    solved, checked, bad
):
    solved.network.generators_t.p.at[solved.network.snapshots[1], "G1"] = bad
    frames = run(checked, {})
    assert approval(frames)["verdict"] == "limited"
    assert (
        "Generator energy: unavailable for the full horizon (1/2 valid snapshots)"
        in public_text(frames)
    )
    assert "Valid subset only: 10 MWh" in public_text(frames)
    assert "Generator energy: 10 MWh" not in public_text(frames)


def test_metric_specific_masks_keep_other_valid_metrics(solved, checked):
    solved.network.generators_t.marginal_cost = pd.DataFrame(
        {"G1": [10.0, float("nan")]}, index=solved.network.snapshots
    )
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    frames = run(checked, {"dispatch_energy": 17, "peak_dispatch": 7})
    assert "Generator energy: 17 MWh" in public_text(frames)
    assert "Marginal dispatch expenditure: unavailable" in public_text(frames)
    assert "Valid subset only: 100 EUR" in public_text(frames)


@pytest.mark.parametrize(
    "fault", ["value", "unit", "formula", "omit", "duplicate", "extra"]
)
def test_model_fabrication_or_incomplete_slots_fail_closed(solved, checked, fault):
    def mutate(ledger):
        block = ledger["blocks"][0]
        if fault == "value":
            block["value"] = 999
        elif fault == "unit":
            block["unit"] = "MW"
        elif fault == "formula":
            block["formula_id"] = "made-up/1"
        elif fault == "omit":
            ledger["blocks"].pop()
        elif fault == "duplicate":
            ledger["blocks"].append(dict(block))
        else:
            ledger["narrative"] = "Everything is safe."

    frames = run(checked, mutate=mutate)
    assert approval(frames)["verdict"] == "limited"
    assert not approval(frames)["receipt"]["checked_block_ids"]
    assert all(
        s["disposition"] == "unavailable"
        for s in approval(frames)["receipt"]["result_slots"]
    )
    assert "999" not in public_text(frames)


@pytest.mark.parametrize(
    "fault", ["inputs", "running", "failed", "condition", "provenance"]
)
def test_unverified_solve_cannot_supply_numerical_facts(solved, checked, fault):
    if fault == "inputs":
        solved.network.generators.at["G1", "p_nom"] = 21
    elif fault == "provenance":
        solved.network.meta.pop("assistant_solve_input_fingerprint")
    elif fault == "condition":
        solved.solver_state["condition"] = "infeasible"
    else:
        solved.solver_state["status"] = fault
    frames = run(checked, {})
    assert approval(frames)["verdict"] == "limited"
    assert not approval(frames)["receipt"]["checked_block_ids"]


def test_output_changes_invalidate_public_replay(solved, checked):
    frames = run(checked)
    solved.network.generators_t.p.iloc[1, 0] = 6
    assert (
        checked.client.get(
            "/api/chat/answers/" + approval(frames)["answer_id"]
        ).status_code
        == 409
    )
    for route in ("/api/chat/history", "/api/chat/export"):
        assert "Generator energy: 17 MWh" not in json.dumps(
            checked.client.get(route).json()
        )


def test_no_selection_disposes_every_required_slot(baseline, checked):
    frames = checked.run(
        [text_turn('{"schema_version":1,"blocks":[]}')],
        message="Give me the key results",
    )
    assert approval(frames)["verdict"] == "limited"
    assert len(approval(frames)["receipt"]["result_slots"]) == 3
    assert "Select a generator" in public_text(frames)


def test_selected_generator_uses_the_same_checked_profile(solved, checked):
    frames = checked.run(
        NumericalProvider(),
        message="Show me the key results",
        ui_context={"selected_component": {"class": "Generator", "name": "G1"}},
    )
    assert approval(frames)["verdict"] == "passed"
    assert "Generator energy: 17 MWh" in public_text(frames)


@pytest.mark.parametrize(
    "fault", ["dispatch_axis", "weights_axis", "multiple_periods", "unknown_entity"]
)
def test_unsupported_or_wrong_scope_cannot_publish_a_number(
    solved, checked, fault, monkeypatch
):
    n = solved.network
    entity = "G1"
    if fault == "dispatch_axis":
        n.generators_t.p = n.generators_t.p.iloc[:1]
    elif fault == "weights_axis":
        # PyPSA derives snapshots from the weighting table. Inject a malformed
        # weighting accessor while preserving the independently requested axis.
        original = type(n).snapshot_weightings
        misaligned = n.snapshot_weightings.iloc[:1]
        monkeypatch.setattr(
            type(n),
            "snapshot_weightings",
            property(
                lambda network: misaligned if network is n else original.fget(network)
            ),
        )
    elif fault == "multiple_periods":
        n.set_snapshots(pd.MultiIndex.from_product([[2030], n.snapshots]))
    else:
        entity = "Ghost"
    frames = checked.run(
        NumericalProvider({}), message=f"Give me the key results for generator {entity}"
    )
    assert approval(frames)["verdict"] == "limited"
    assert not approval(frames)["receipt"]["checked_block_ids"]
    assert len(approval(frames)["receipt"]["result_slots"]) == 3
    assert "Generator energy: 17 MWh" not in public_text(frames)


@pytest.mark.parametrize("bad", [0, -1, float("nan")])
def test_invalid_energy_weights_do_not_invalidate_other_formulas(solved, checked, bad):
    solved.network.snapshot_weightings.at[solved.network.snapshots[1], "generators"] = (
        bad
    )
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    frames = run(checked, {"marginal_expenditure": 170, "peak_dispatch": 7})
    assert "Generator energy: unavailable" in public_text(frames)
    assert "Marginal dispatch expenditure: 170 EUR" in public_text(frames)


def test_numeric_overflow_and_nonnumeric_raw_values_are_unavailable():
    assert metrics._number("1") is None and metrics._number(True) is None
    raw = {
        "index": ["a", "b"],
        "capacity": 1e308,
        "valid_solve": True,
        "inputs": {
            "p": [1e308, 1e308],
            "p_min": [0, 0],
            "p_max": [1, 1],
            "energy_weight": [2, 2],
            "objective_weight": [2, 2],
            "marginal_cost": [2, 2],
        },
    }
    facts, slots = metrics.calculate(raw, "G1")
    assert [f["metric"] for f in facts] == ["peak_dispatch"]
    assert slots[0]["disposition"] == "unavailable"
