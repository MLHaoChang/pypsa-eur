"""branch-losses/1 (assistant-assurance 10, S1b; A1.7).

The checked reader of `services/results/losses.py:_validity_rows`. Two kinds
of oracle, neither computed through the service under test:

- hand frames: loss values written into the solved network by hand, so every
  expected figure is a literal worked out in the comment beside it;
- a real HiGHS LP with transmission losses: one line carries all supply, so
  energy conservation gives each snapshot's loss as dispatch minus demand,
  summed by hand-written arithmetic over the LP's own dispatch.

Snapshot weights are 1, 0.5 and 2 throughout.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import math

import numpy as np
import pandas as pd
import pypsa
import pytest

from harness import assurance_scope, calculations
from harness import losses_calculations as losses
from harness.derived import NEAR_ZERO
from services import assurance_records as records
from services import workflow_tools
from tests.test_result_profiles_assurance import solve
from tests.test_workflow_tools import baseline  # noqa: F401

WEIGHTS = [1.0, 0.5, 2.0]
STAMPS = pd.date_range("2026-01-01", periods=3, freq="h")
# Hand loss frames, MW per snapshot.
# L1: 1*1 + 0.5*2 + 2*0.5 = 3 MWh, peak 2 MW.
# L2: a real zero throughout: 0 MWh, peak 0 MW.
# T1: 1*0.4 + 0.5*0 + 2*1 = 2.4 MWh, peak 1 MW.
LINES = {"L1": [1.0, 2.0, 0.5], "L2": [0.0, 0.0, 0.0]}
TRAFOS = {"T1": [0.4, 0.0, 1.0]}
# Demand D at p_set 10, 20, 40: 1*10 + 0.5*20 + 2*40 = 100 MWh.
DEMAND = [10.0, 20.0, 40.0]
# Network: 3 + 0 + 2.4 = 5.4 MWh; peak max(2, 0, 1) = 2 MW; 5.4 / 100 = 5.4 %.
NETWORK = {"loss_energy": 5.4, "peak_loss": 2.0, "demand_energy": 100.0, "loss_pct_of_demand": 5.4}


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", losses.PROFILE)


def _network(demand=DEMAND):
    n = pypsa.Network()
    n.set_snapshots(STAMPS)
    for column in ("objective", "stores", "generators"):
        n.snapshot_weightings[column] = WEIGHTS
    n.add("Bus", ["A", "B"], v_nom=110)
    n.add("Bus", "C", v_nom=20)
    n.add("Line", ["L1", "L2"], bus0="A", bus1="B", x=0.1, r=0.01, s_nom=500)
    n.add("Transformer", "T1", bus0="B", bus1="C", x=0.1, s_nom=500)
    n.add("Generator", "G", bus="A", p_nom=500, marginal_cost=10)
    n.add("Load", "D", bus="C", p_set=demand)
    return n


@pytest.fixture
def hand_losses(baseline):
    """The hand network, unsolved: `write_losses` solves it and writes the frames."""
    ctx = baseline[0]
    ctx.network = _network()
    return ctx


def write_losses(ctx, lines=LINES, trafos=TRAFOS, index=None):
    """Solve without losses, then write the hand loss frames where the LP
    writes its own (`loss` on Line and Transformer)."""
    solve(ctx)
    n = ctx.network
    index = n.snapshots if index is None else index
    n.lines_t.loss = pd.DataFrame(lines, index=index, dtype=float)
    n.transformers_t.loss = pd.DataFrame(trafos, index=index, dtype=float)
    return ctx


@pytest.fixture
def losses_case(baseline):
    """`losses_case(lines, trafos, demand, index)`: a fresh hand network each call."""
    ctx = baseline[0]

    def make(lines=LINES, trafos=TRAFOS, demand=DEMAND, index=STAMPS):
        ctx.network = _network(demand)
        return write_losses(ctx, lines, trafos, index)

    return make


def _checked(ctx, entity, window=None):
    scope = {"version": 1, "profile": losses.PROFILE, "project_id": ctx.project_uuid,
             "entity_id": entity, "retain": False}
    if window:
        scope.update(start=window[0], end=window[1])
    record = records.capture_scoped(records.actor_id(), scope)
    values = {f["metric"]: f["value"] for f in record["facts"]}
    reasons = {s["metric"]: s["reason"] for s in record["slots"] if s["disposition"] == "unavailable"}
    return values, reasons


def test_network_figures_equal_the_hand_arithmetic(losses_case):
    values, reasons = _checked(losses_case(), "network")
    assert reasons == {}
    assert values == NETWORK


def test_a_real_zero_is_answered_as_zero_mwh(losses_case):
    values, reasons = _checked(losses_case(), "L2")
    assert values == {"loss_energy": 0.0, "peak_loss": 0.0}
    assert reasons == dict.fromkeys(losses.NETWORK_ONLY, "network_scope_required")


def test_one_branch_is_its_own_arithmetic(losses_case):
    values, _ = _checked(losses_case(), "T1")
    assert values == pytest.approx({"loss_energy": 2.4, "peak_loss": 1.0}, rel=1e-12)


def test_an_exact_window_reads_only_its_snapshots(losses_case):
    # Snapshots 1..2 of L1: 0.5*2 + 2*0.5 = 2 MWh, peak 2 MW.
    values, _ = _checked(losses_case(), "L1", (STAMPS[1].isoformat(), STAMPS[2].isoformat()))
    assert values == pytest.approx({"loss_energy": 2.0, "peak_loss": 2.0}, rel=1e-12)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_a_missing_or_infinite_value_never_yields_a_loss(losses_case, bad):
    ctx = losses_case(lines={"L1": [1.0, bad, 0.5], "L2": [0.0, 0.0, 0.0]})
    values, reasons = _checked(ctx, "L1")
    assert values == {}
    assert reasons["loss_energy"] == reasons["peak_loss"] == "incomplete"
    network, why = _checked(ctx, "network")
    # The network total needs every branch complete; demand stands on its own.
    assert network == {"demand_energy": 100.0}
    assert why == {"loss_energy": "incomplete", "peak_loss": "incomplete",
                   "loss_pct_of_demand": "incomplete"}
    # The other branches still answer.
    assert _checked(ctx, "L2")[0] == {"loss_energy": 0.0, "peak_loss": 0.0}


def test_a_bad_value_outside_the_window_still_withholds_the_branch(losses_case):
    ctx = losses_case(lines={"L1": [np.nan, 2.0, 0.5], "L2": [0.0, 0.0, 0.0]})
    values, reasons = _checked(ctx, "L1", (STAMPS[1].isoformat(), STAMPS[2].isoformat()))
    assert values == {} and reasons["loss_energy"] == "incomplete"


def test_a_branch_with_no_column_is_absent(losses_case):
    ctx = losses_case(trafos={"other": [0.0, 0.0, 0.0]})
    values, reasons = _checked(ctx, "T1")
    assert values == {} and reasons["loss_energy"] == "absent"
    assert _checked(ctx, "network")[1]["loss_energy"] == "absent"


def test_a_repeated_index_is_misaligned(losses_case):
    ctx = losses_case(index=pd.DatetimeIndex([STAMPS[0], STAMPS[0], STAMPS[2]]))
    values, reasons = _checked(ctx, "network")
    assert reasons["loss_energy"] == reasons["peak_loss"] == "misaligned"
    assert values == {"demand_energy": 100.0}


def test_misaligned_outranks_absent_and_incomplete(losses_case):
    ctx = losses_case(lines={"L1": [np.nan, 2.0, 0.5]}, index=pd.DatetimeIndex([STAMPS[0], STAMPS[0], STAMPS[2]]))
    ctx.network.lines_t.loss = pd.DataFrame({"L1": [np.nan, 2.0, 0.5]}, index=STAMPS)  # L2 absent, L1 NaN
    assert _checked(ctx, "network")[1]["loss_energy"] == "misaligned"  # T1's frame is misaligned


def test_a_short_frame_never_certifies(losses_case):
    ctx = losses_case(lines={k: v[:2] for k, v in LINES.items()},
               trafos={k: v[:2] for k, v in TRAFOS.items()}, index=STAMPS[:2])
    values, reasons = _checked(ctx, "network")
    assert reasons["loss_energy"] == "incomplete" and "loss_energy" not in values


def test_a_zero_row_frame_is_losses_not_recorded(losses_case):
    ctx = losses_case(lines={k: [] for k in LINES}, trafos={k: [] for k in TRAFOS}, index=STAMPS[:0])
    values, reasons = _checked(ctx, "network")
    assert values == {"demand_energy": 100.0}
    assert reasons == dict.fromkeys(("loss_energy", "peak_loss", "loss_pct_of_demand"), "losses_not_recorded")
    assert _checked(ctx, "L2")[1]["loss_energy"] == "losses_not_recorded"


def test_the_share_of_demand_needs_every_load_complete(losses_case):
    ctx = losses_case()
    ctx.network.loads_t.p.loc[STAMPS[1], "D"] = np.nan
    values, reasons = _checked(ctx, "network")
    assert values == pytest.approx({"loss_energy": 5.4, "peak_loss": 2.0}, rel=1e-12)
    assert reasons == {"demand_energy": "incomplete", "loss_pct_of_demand": "incomplete"}


def test_a_zero_demand_is_a_near_zero_denominator(losses_case):
    ctx = losses_case(demand=[0.0, 0.0, 0.0])
    values, reasons = _checked(ctx, "network")
    assert values["demand_energy"] == 0.0
    assert reasons == {"loss_pct_of_demand": "near_zero_denominator"}


def test_the_denominator_guard_is_the_shared_near_zero_rule(losses_case):
    # Demand of exactly NEAR_ZERO MWh is refused; twice that is divided by.
    at = losses_case(demand=[NEAR_ZERO, 0.0, 0.0])
    assert _checked(at, "network")[1] == {"loss_pct_of_demand": "near_zero_denominator"}
    above = losses_case(demand=[2 * NEAR_ZERO, 0.0, 0.0])
    assert "loss_pct_of_demand" in _checked(above, "network")[0]


def test_an_unverified_solve_answers_nothing(losses_case):
    ctx = losses_case()
    ctx.network.add("Load", "late", bus="B", p_set=1)  # inputs changed after the solve
    values, reasons = _checked(ctx, "network")
    assert values == {} and reasons == dict.fromkeys(losses.METRICS, "unverified_solve")


@pytest.mark.parametrize(("entity", "change", "kind"), [
    ("nowhere", None, "unknown_branch"),
    ("L1", "pf", "ac_pf_present"),
    ("network", "multi", "unsupported_snapshot_scope"),
    ("L1", "twin", "ambiguous_branch"),
])
def test_refusals_are_named(losses_case, entity, change, kind):
    ctx = losses_case()
    if change == "pf":
        ctx.solver_state["ac_pf_results"] = {}
    elif change == "multi":
        ctx.network.investment_periods = [2030]
    elif change == "twin":
        ctx.network.add("Transformer", "L1", bus0="B", bus1="C", x=0.1, s_nom=5)
    with pytest.raises(ValueError) as error:
        calculations.scoped_source(ctx, {"profile": losses.PROFILE, "entity_id": entity})
    assert error.value.args[0] == kind
    assert kind in losses.REFUSALS


def test_every_slot_reason_is_declared_and_reached(losses_case):
    """Exact set equality: a reason the profile can give but no test reaches,
    or one reached but not declared, fails."""
    seen = set()
    for kwargs, entity, edit in [
        ({"lines": {"L1": [1.0, np.nan, 0.5], "L2": [0.0] * 3}}, "network", None),
        ({"trafos": {"other": [0.0] * 3}}, "network", None),
        ({"index": pd.DatetimeIndex([STAMPS[0], STAMPS[0], STAMPS[2]])}, "network", None),
        ({"lines": {k: [] for k in LINES}, "trafos": {k: [] for k in TRAFOS}, "index": STAMPS[:0]}, "network", None),
        ({"demand": [0.0, 0.0, 0.0]}, "network", None),
        ({}, "L1", None),
        ({}, "network", "unverify"),
        ({}, "network", "noloads"),
    ]:
        ctx = losses_case(**kwargs)
        if edit == "unverify":
            ctx.network.add("Load", "late", bus="B", p_set=1)
        elif edit == "noloads":
            raw = calculations.scoped_source(ctx, {"profile": losses.PROFILE, "entity_id": "network"})
            raw["loads"] = {}
            _, slots = losses.calculate(raw, {"profile": losses.PROFILE, "entity_id": "network"})
            seen |= {s["reason"] for s in slots if s["disposition"] == "unavailable"}
            continue
        seen |= set(_checked(ctx, entity)[1].values())
    assert seen == losses.REASONS


def test_the_route_shows_the_same_hand_figures(losses_case, client):
    """A4.6: `/results/losses` and the checked answer agree with the hand literals."""
    losses_case()
    route = client.get("/api/results/losses", params={"source": "lopf"}).json()
    assert route["validity"]["complete"] is True
    assert (route["total_mwh"], route["peak_mw"], route["total_demand_mwh"], route["loss_pct_of_demand"]) == (
        5.4, 2.0, 100.0, 5.4)


# ── a real LP with losses, and parity with /results/losses ─────────────────


@pytest.fixture
def lossy(baseline, client):
    """One line A→B carries all supply; demand 10, 20, 40 MW at B."""
    ctx = baseline[0]
    n = pypsa.Network()
    n.set_snapshots(STAMPS)
    for column in ("objective", "stores", "generators"):
        n.snapshot_weightings[column] = WEIGHTS
    n.add("Bus", ["A", "B"], v_nom=1)
    n.add("Line", "AB", bus0="A", bus1="B", x=0.1, r=0.01, s_nom=100)
    n.add("Generator", "G", bus="A", p_nom=200, marginal_cost=10)
    n.add("Load", "L", bus="B", p_set=DEMAND)
    ctx.network = n
    assert n.optimize(solver_name="highs", transmission_losses=True, include_objective_constant=False) == ("ok", "optimal")
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    return ctx


def test_lp_losses_close_the_energy_balance_and_match_the_route(lossy, client):
    g = [float(v) for v in lossy.network.generators_t.p["G"]]
    # Conservation on one line: loss_t = dispatch_t - demand_t.
    per_snapshot = [g[0] - 10.0, g[1] - 20.0, g[2] - 40.0]
    assert all(v > 0 for v in per_snapshot)  # the LP did model losses
    loss_energy = 1.0 * per_snapshot[0] + 0.5 * per_snapshot[1] + 2.0 * per_snapshot[2]
    values, reasons = _checked(lossy, "network")
    assert reasons == {}
    assert values["loss_energy"] == pytest.approx(loss_energy, rel=1e-6)
    assert values["peak_loss"] == pytest.approx(max(per_snapshot), rel=1e-6)
    assert values["demand_energy"] == 100.0
    assert values["loss_pct_of_demand"] == pytest.approx(loss_energy, rel=1e-6)  # of 100 MWh

    response = client.get("/api/results/losses", params={"source": "lopf"})
    assert response.status_code == 200, response.text
    route = response.json()
    assert route["validity"]["complete"] is True
    assert values["loss_energy"] == route["total_mwh"]
    assert values["peak_loss"] == route["peak_mw"]
    assert values["demand_energy"] == route["total_demand_mwh"]
    assert values["loss_pct_of_demand"] == route["loss_pct_of_demand"]


def test_where_the_route_fills_zero_the_checked_answer_withholds(lossy, client):
    lossy.network.lines_t.loss.iloc[1, 0] = np.nan
    route = client.get("/api/results/losses").json()
    assert route["validity"]["complete"] is False and math.isfinite(route["total_mwh"])
    values, reasons = _checked(lossy, "network")
    assert "loss_energy" not in values and reasons["loss_energy"] == "incomplete"


def test_grammar():
    scope = assurance_scope.from_message("Show the losses for network.", "p1", {})
    assert scope["profile"] == losses.PROFILE and scope["entity_id"] == "network"
    branch = assurance_scope.from_message("show losses for branch L1; from 2026-01-01T00:00 to 2026-01-01T01:00", "p1", {})
    assert branch["profile"] == losses.PROFILE and branch["entity_id"] == "L1"
