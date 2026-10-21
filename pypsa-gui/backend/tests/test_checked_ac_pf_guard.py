"""Checked profiles refuse once an AC power flow has run over the solve.

`ac_pf_service` snapshots the LP results and then overwrites the live
generator, storage, load and price outputs with power-flow values; the slack
generator's p is the PF's, not the LP's. A checked answer read from the live
network would then certify PF numbers as the LP solve.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import pytest

from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import run, solved  # noqa: F401
from tests.test_storage_assurance import VALUES, private_store, scope, storage  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401

LP = {"dispatch_energy": 17, "marginal_expenditure": 170, "peak_dispatch": 7}


def after_ac_pf(ctx, key):
    """What an AC PF run leaves: the LP snapshot kept aside, live p overwritten."""
    n = ctx.network
    ctx.solver_state["lopf_results"] = {"generators_t.p": n.generators_t.p.copy()}
    ctx.solver_state["ac_pf_results"] = {"generators_t.p": n.generators_t.p.copy()}
    if key == "lopf_only":
        ctx.solver_state["ac_pf_results"] = None


@pytest.mark.parametrize("key", ["ac_pf", "lopf_only"])
def test_generator_profile_refuses_after_an_ac_power_flow(solved, checked, key):
    after_ac_pf(solved, key)
    frames = run(checked, LP)
    assert approval(frames)["verdict"] == "limited"
    assert "Generator energy: 17 MWh" not in public_text(frames)


def test_storage_profile_refuses_after_an_ac_power_flow(storage, checked):
    after_ac_pf(storage, "ac_pf")
    frames = checked.run(NumericalProvider(VALUES), message="Check battery results.",
                         checked_scope=scope(storage))
    assert (approval(frames).get("receipt") or {}).get("request_complete") is False


def test_without_a_power_flow_the_profile_still_answers(solved, checked):
    assert approval(run(checked, LP))["verdict"] == "passed"
