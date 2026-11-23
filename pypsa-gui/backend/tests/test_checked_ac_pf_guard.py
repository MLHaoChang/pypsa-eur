"""Checked profiles after an AC power flow (owner decision PF-SAVE-1).

`ac_pf_service` keeps the flow's tables in `ac_pf_results` and leaves the live
generator, storage, load and price outputs as the LP's, so a checked answer
read from the live network after a flow certifies the LP solve: it answers,
with the LP's numbers. A flow record without its LP record
(`solve_matches_inputs`, GK B M2) still refuses, and a new solve clears it.
"""

# ruff: noqa: F811 -- imported shared fixtures.

from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import run, solved  # noqa: F401
from tests.test_storage_assurance import VALUES, private_store, scope, storage  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401

LP = {"dispatch_energy": 17, "marginal_expenditure": 170, "peak_dispatch": 7}


def after_ac_pf(ctx, key):
    """What an AC PF run leaves: the live network keeps the LP's tables, the LP
    snapshot is kept in `lopf_results` and the flow's own (different) p in
    `ac_pf_results`. `lopf_only` is a half record: the LP kept, no flow."""
    n = ctx.network
    ctx.solver_state["lopf_results"] = {"generators_t.p": n.generators_t.p.copy()}
    ctx.solver_state["ac_pf_results"] = {"generators_t.p": n.generators_t.p * 1.1}
    if key == "lopf_only":
        ctx.solver_state["ac_pf_results"] = None


def test_generator_profile_answers_the_lp_after_an_ac_power_flow(solved, checked):
    after_ac_pf(solved, "ac_pf")
    frames = run(checked, LP)
    assert approval(frames)["verdict"] == "passed"
    # The LP's 17 MWh, not the flow's 18.7.
    assert approval(run(checked, {**LP, "dispatch_energy": 18.7}))["verdict"] == "limited"


def test_generator_profile_refuses_a_half_record(solved, checked):
    after_ac_pf(solved, "lopf_only")
    frames = run(checked, LP)
    assert approval(frames)["verdict"] == "limited"
    assert "Generator energy: 17 MWh" not in public_text(frames)


def test_storage_profile_answers_after_an_ac_power_flow(storage, checked):
    after_ac_pf(storage, "ac_pf")
    frames = checked.run(NumericalProvider(VALUES), message="Check battery results.",
                         checked_scope=scope(storage))
    assert (approval(frames).get("receipt") or {}).get("request_complete") is True


def test_without_a_power_flow_the_profile_still_answers(solved, checked):
    assert approval(run(checked, LP))["verdict"] == "passed"


def test_a_fresh_solve_after_a_power_flow_answers_again(baseline, checked, client, session_state):
    """The guard never latches: a flow record without its LP record refuses,
    and every solve claim wipes the PF result keys."""
    from services.solver_service import SolverConfig

    state = session_state(client)

    def solve():
        state["solver_config"] = SolverConfig()
        assert client.post("/api/simulation/run").status_code == 200
        state["thread"].join(timeout=90)
        assert state["status"] == "completed", state.get("condition")

    solve()
    state["ac_pf_results"], state["lopf_results"] = {}, None
    # G1 serves L1's 5 MW for two unit-weight hours at 10 EUR/MWh.
    values = {"dispatch_energy": 10, "marginal_expenditure": 100, "peak_dispatch": 5}
    assert approval(run(checked, values))["verdict"] == "limited"
    solve()
    assert state["ac_pf_results"] is None and state["lopf_results"] is None
    assert approval(run(checked, values))["verdict"] == "passed"
