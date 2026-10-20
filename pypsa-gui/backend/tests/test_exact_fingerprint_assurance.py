"""Checked profiles require the exact solve-input fingerprint the Load flow tab uses.

The recorded fingerprint serialises floats through `to_json` (ten decimal
places), so an edit below that precision leaves it unchanged. A checked
answer must still refuse that solve as unverified.
"""

# ruff: noqa: F811 -- imported shared fixtures.
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import run, solved  # noqa: F401
from tests.test_storage_assurance import VALUES, answer, private_store, scope, storage  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401
from services import workflow_tools

TINY = 1e-12


def nudge(ctx):
    """An input no checked metric reads, moved below to_json's precision."""
    ctx.network.buses.at["B1", "v_nom"] += TINY


def test_recorded_fingerprint_misses_a_sub_precision_edit(solved):
    before = (workflow_tools._network_input_fingerprint(solved),
              workflow_tools._network_input_fingerprint(solved, exact=True))
    nudge(solved)
    assert workflow_tools._network_input_fingerprint(solved) == before[0]
    assert workflow_tools._network_input_fingerprint(solved, exact=True) != before[1]


def test_generator_profile_refuses_a_solve_the_exact_fingerprint_rejects(solved, checked):
    nudge(solved)
    frames = run(checked, {"dispatch_energy": 17, "marginal_expenditure": 170, "peak_dispatch": 7})
    assert approval(frames)["verdict"] == "limited"
    assert "Generator energy: 17 MWh" not in public_text(frames)


def test_storage_profile_refuses_a_solve_the_exact_fingerprint_rejects(storage, checked):
    nudge(storage)
    frames = checked.run(NumericalProvider(VALUES), message="Check battery results.",
                         checked_scope=scope(storage))
    receipt = approval(frames).get("receipt") or {}
    assert receipt.get("request_complete") is False


def test_a_fresh_solve_after_the_edit_answers_again(solved, checked):
    nudge(solved)
    n = solved.network
    assert n.optimize(solver_name="highs", include_objective_constant=False) == ("ok", "optimal")
    workflow_tools._record_solve_inputs(n, solved.solver_state["solver_config"])
    frames = run(checked, {"dispatch_energy": 17, "marginal_expenditure": 170, "peak_dispatch": 7})
    assert approval(frames)["verdict"] == "passed"
    assert "Generator energy: 17 MWh" in public_text(frames)
