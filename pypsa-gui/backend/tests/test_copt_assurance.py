"""copt-screening/1 (checked-results S4b): the COPT screening LOLE and EUE.

Hand fleet {60 MW q 0.1, 40 MW q 0.2}: available 100 MW with 0.72, 60 with 0.18,
40 with 0.08, 0 with 0.02. Each fixture is also checked against GET /results/copt.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import pandas as pd
import pytest

from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401

PROFILE = "copt-screening/1"


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", PROFILE)


def fleet(ctx, load, wind):
    n = ctx.network
    n.remove("Generator", "G1")
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", ["gas", "wind"])
    n.add("Generator", "thermal1", bus="B1", carrier="gas", p_nom=60, outage_rate_value=0.1,
          outage_rate_basis="EFORd", mttr_hours=50.0)
    n.add("Generator", "thermal2", bus="B1", carrier="gas", p_nom=40, outage_rate_value=0.2,
          outage_rate_basis="EFORd", mttr_hours=40.0)
    n.add("Generator", "windfarm", bus="B1", carrier="wind", p_nom=50)
    n.generators_t.p_max_pu = pd.DataFrame({"windfarm": wind}, index=n.snapshots)
    n.loads_t.p_set = pd.DataFrame({"L1": load}, index=n.snapshots)
    return ctx


@pytest.fixture
def classical(baseline):
    """Residual 100 - 30 = 70 MW, then 100 MW."""
    return fleet(baseline[0], load=[100.0, 100.0], wind=[0.6, 0.0])


@pytest.fixture
def on_the_grid(baseline):
    """Residual exactly 60 MW (90 - 30), then 60 MW + 1e-13 (inside the engine's 1e-12 snap)."""
    return fleet(baseline[0], load=[90.0, 60.0000000000001], wind=[0.6, 0.0])


def ask(checked, ctx, values):
    scope = {"version": 1, "profile": PROFILE, "project_id": ctx.project_uuid,
             "entity_id": "network", "retain": False}
    return checked.run(NumericalProvider(values), message="Check the COPT.", checked_scope=scope)


def answered(checked, frames):
    record = approval(frames)
    response = checked.client.get("/api/chat/answers/" + record["answer_id"])
    assert response.status_code == 200, response.text
    return {b["metric"]: b["value"] for b in response.json()["blocks"]}


def reasons(frames):
    return {s["metric"]: s["reason"] for s in approval(frames)["receipt"]["result_slots"]
            if s["disposition"] != "answered"}


def published(ctx, expected):
    from harness import calculations

    scope = {"version": 1, "profile": PROFILE, "entity_id": "network"}
    facts, _ = calculations.scoped_calculate(calculations.scoped_source(ctx, scope), scope)
    values = {f["metric"]: f["value"] for f in facts}
    assert values == pytest.approx(expected, rel=1e-12, abs=1e-12)
    return values


def engine():
    import routers.results as R

    return R.get_copt()["metrics"]


def test_the_classical_fleet_by_hand_and_as_the_tab_has_it(classical, checked):
    # LOLP(70) = P[< 70] = 0.18 + 0.08 + 0.02 = 0.28; LOLP(100) = 1 - 0.72 = 0.28; LOLE = 3 x 0.56.
    # EUE(70) = 10 x 0.18 + 30 x 0.08 + 70 x 0.02 = 5.6; EUE(100) = 40 x 0.18 + 60 x 0.08 + 100 x 0.02 = 14.
    values = published(classical, {"screening_lole": 1.68, "screening_eue": 58.8, "screening_lolp_max": 0.28})
    tab = engine()
    assert (tab["lole_hours"], tab["eue_mwh"], tab["lolp_max"]) == pytest.approx((1.68, 58.8, 0.28))
    frames = ask(checked, classical, values)
    assert answered(checked, frames) == values
    text = public_text(frames)
    assert "not a statutory standard" in text and "equals the residual load is not a loss" in text


def test_capacity_equal_to_the_load_is_not_a_loss_and_the_snap_holds(on_the_grid, checked):
    # P[< 60] = 0.08 + 0.02 = 0.1 in both hours; EUE(60) = 20 x 0.08 + 60 x 0.02 = 2.8 (+ ~1e-13).
    values = published(on_the_grid, {"screening_lole": 0.6, "screening_eue": 16.8, "screening_lolp_max": 0.1})
    assert answered(checked, ask(checked, on_the_grid, values)) == values


def refused(checked, ctx):
    return set(reasons(ask(checked, ctx, {})).values())


def test_a_profiled_unit_is_outside_the_plain_table(classical, checked):
    classical.network.generators_t.p_max_pu["thermal1"] = [1.0, 0.5]
    assert refused(checked, classical) == {"copt_conventions_not_reproduced"}


def test_a_folded_static_availability_is_outside_the_plain_table(classical, checked):
    classical.network.generators.at["thermal1", "p_max_pu"] = 0.8
    assert refused(checked, classical) == {"copt_conventions_not_reproduced"}


def test_a_capacity_off_the_grid_is_outside_the_plain_table(classical, checked):
    classical.network.generators.at["thermal1", "p_nom"] = 60.5
    assert refused(checked, classical) == {"copt_conventions_not_reproduced"}


def test_no_fleet_is_named(classical, checked):
    # A blank rate falls back to the carrier default, so only a fleet with no unit is empty.
    classical.network.remove("Generator", ["thermal1", "thermal2"])
    assert refused(checked, classical) == {"no_copt_fleet"}


def test_an_unusable_rate_is_named(classical, checked):
    classical.network.generators.at["thermal1", "outage_rate_value"] = 1.5
    assert refused(checked, classical) == {"unusable_outage_rate"}


def test_a_residual_that_does_not_reproduce_gives_no_figure(classical, checked, monkeypatch):
    from harness import copt_calculations

    real = copt_calculations._residual
    monkeypatch.setattr(copt_calculations, "_residual", lambda n, cfg: real(n, cfg) + 1.0)
    assert refused(checked, classical) == {"residual_mismatch"}


def test_a_residual_beyond_the_fleet_is_a_probability_of_one(baseline, checked):
    ctx = fleet(baseline[0], load=[1000.0, 100.0], wind=[0.0, 0.0])
    values = published(ctx, {"screening_lole": 3 * 1 + 3 * 0.28, "screening_eue": 3 * (1000 - 0.72 * 100 - 0.18 * 60
                                                                                       - 0.08 * 40) + 3 * 14,
                             "screening_lolp_max": 1.0})
    assert values["screening_lolp_max"] == 1.0


def test_a_capacity_a_fraction_off_the_grid_is_refused(classical, checked):
    classical.network.generators.at["thermal1", "p_nom"] = 60 + 5e-10
    assert refused(checked, classical) == {"copt_conventions_not_reproduced"}


def test_a_must_take_built_in_a_later_period_is_refused_by_name(baseline, checked):
    ctx = baseline[0]
    n = ctx.network
    n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], n.snapshots]))
    n.set_investment_periods([2030, 2040])
    fleet(ctx, load=[100.0] * 4, wind=[0.6] * 4)
    n.generators.at["windfarm", "build_year"] = 2040
    assert refused(checked, ctx) == {"copt_conventions_not_reproduced"}


def test_an_infinite_availability_is_refused_by_name(classical, checked):
    classical.network.generators_t.p_max_pu["windfarm"] = [float("inf"), 0.0]
    assert refused(checked, classical) == {"copt_inputs_not_finite"}


def test_a_missing_availability_is_refused_not_filled(classical, checked):
    # The engine reads a NaN availability as 0; the oracle does not share the fill.
    classical.network.generators_t.p_max_pu["windfarm"] = [float("nan"), 0.0]
    assert refused(checked, classical) == {"copt_inputs_not_finite"}


def test_a_missing_demand_is_refused_not_filled(classical, checked):
    classical.network.loads_t.p_set["L1"] = [float("nan"), 100.0]
    assert refused(checked, classical) == {"copt_inputs_not_finite"}
