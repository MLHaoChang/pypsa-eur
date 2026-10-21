"""capacity/1 and storage-cycles/1 (checked-results S6). Expected values by hand."""

# ruff: noqa: F811 -- imported shared fixtures.
import pandas as pd
import pytest

from services import workflow_tools
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_storage_assurance import storage  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401

PROFILES = ("capacity/1", "storage-cycles/1", "storage-unit-dispatch/1")


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", ",".join(PROFILES))


def solve(ctx):
    n = ctx.network
    assert n.optimize(solver_name="highs", include_objective_constant=False) == ("ok", "optimal")
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    return ctx


def ask(checked, ctx, profile, entity, component_class, values, **options):
    scope = {"version": 1, "profile": profile, "project_id": ctx.project_uuid, "entity_id": entity,
             "component_class": component_class, "retain": False, **options}
    return checked.run(NumericalProvider(values), message="Check results.", checked_scope=scope)


def answered(checked, frames):
    record = approval(frames)
    response = checked.client.get("/api/chat/answers/" + record["answer_id"])
    assert response.status_code == 200, response.text
    return {b["metric"]: b["value"] for b in response.json()["blocks"]}


def reasons(frames):
    return {s["reason"] for s in approval(frames)["receipt"]["result_slots"] if s["disposition"] != "answered"}


# ── capacity ──────────────────────────────────────────────────────────────


@pytest.fixture
def built(baseline):
    """A free extendable G2 capped at 3 MW is built to its bound; G1 stays fixed at 20 MW."""
    ctx = baseline[0]
    n = ctx.network
    n.add("Generator", "G2", bus="B1", p_nom_extendable=True, p_nom_max=3, capital_cost=1, marginal_cost=0)
    n.loads_t.p_set = pd.DataFrame({"L1": [5, 7]}, index=n.snapshots)
    return ctx


def test_an_extendable_generator_built_to_its_bound(built, checked):
    ctx = solve(built)
    values = {"optimal_capacity": 3, "capacity_change": 3}
    frames = ask(checked, ctx, "capacity/1", "G2", "Generator", values)
    assert answered(checked, frames) == pytest.approx(values)
    text = public_text(frames)
    assert "Generator G2" in text and "Built to its upper bound (p_nom_max)." in text
    assert "p_nom in MW: active power." in text and "not applied" in text


def test_a_fixed_generator_says_it_was_not_optimised(built, checked):
    ctx = solve(built)
    frames = ask(checked, ctx, "capacity/1", "G1", "Generator", {"optimal_capacity": 20, "capacity_change": 0})
    assert answered(checked, frames) == pytest.approx({"optimal_capacity": 20, "capacity_change": 0})
    assert "Fixed capacity (not optimised)." in public_text(frames)


def test_the_named_class_must_hold_the_component(built, checked):
    ctx = solve(built)
    assert reasons(ask(checked, ctx, "capacity/1", "G2", "Link", {})) == {"unknown_component"}


# ── cycles ────────────────────────────────────────────────────────────────


def test_a_non_cyclic_unit_matches_the_storage_profile(storage, checked):
    # The StorageUnit profile's checked discharge is 4 MWh (store weights 1, 1 on 2 MW);
    # energy capacity 10 MW x 2 h = 20 MWh: 0.2 cycles.
    s2 = checked.run(NumericalProvider({"discharged_energy": 4}), message="Check battery results.",
                     checked_scope={"version": 1, "profile": "storage-unit-dispatch/1",
                                    "project_id": storage.project_uuid, "entity_id": "Battery",
                                    "retain": False, "metrics": ["discharged_energy"]})
    values = {"window_discharged_energy": 4, "energy_capacity": 20, "equivalent_cycles": 0.2}
    s6 = ask(checked, storage, "storage-cycles/1", "Battery", "StorageUnit", values)
    assert answered(checked, s6) == pytest.approx(values)
    assert answered(checked, s2)["discharged_energy"] == answered(checked, s6)["window_discharged_energy"]


def test_a_cyclic_unit_is_checked_against_its_bounds_and_says_so(storage, checked):
    storage.network.storage_units.at["Battery", "cyclic_state_of_charge"] = True
    ctx = solve(storage)
    su = ctx.network.storage_units_t
    expected = float((ctx.network.snapshot_weightings["stores"] * su.p_dispatch["Battery"]).sum())
    values = {"window_discharged_energy": expected, "energy_capacity": 20, "equivalent_cycles": expected / 20}
    frames = ask(checked, ctx, "storage-cycles/1", "Battery", "StorageUnit", values)
    assert answered(checked, frames) == pytest.approx(values)
    assert "Cyclic unit: the state-of-charge trajectory is not verified, only its bounds." in public_text(frames)


@pytest.fixture
def tank(baseline):
    """A Store charges in the cheap first hour (2 h at 2 MW) and serves 4 MW in the dear second."""
    ctx = baseline[0]
    n = ctx.network
    n.add("Store", "Tank", bus="B1", e_nom=10, e_initial=0)
    n.generators_t.marginal_cost = pd.DataFrame({"G1": [1, 100]}, index=n.snapshots)
    n.generators_t.p_max_pu = pd.DataFrame({"G1": [1.0, 0.0]}, index=n.snapshots)
    n.loads_t.p_set = pd.DataFrame({"L1": [0, 4]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "stores"] = [2, 1]
    return ctx


def test_a_store_counts_only_energy_leaving_it(tank, checked):
    ctx = solve(tank)
    # PyPSA Store p > 0 leaves the store: -2 MW (charging) then +4 MW.
    assert list(ctx.network.stores_t.p.Tank) == pytest.approx([-2, 4])
    # Discharge 1 h x 4 MW = 4 MWh (the charging hour adds 0, not -4); e_nom 10: 0.4 cycles.
    values = {"window_discharged_energy": 4, "energy_capacity": 10, "equivalent_cycles": 0.4}
    assert answered(checked, ask(checked, ctx, "storage-cycles/1", "Tank", "Store", values)) == (
        pytest.approx(values))


def test_a_store_state_off_its_balance_is_unavailable(tank, checked):
    ctx = solve(tank)
    ctx.network.stores_t.e.iloc[0, 0] = 3  # the balance says 4
    assert reasons(ask(checked, ctx, "storage-cycles/1", "Tank", "Store", {})) == {
        "invalid_or_missing_storage_trajectory"}


def test_a_cyclic_store_is_checked_around_its_loop(tank, checked):
    tank.network.stores.at["Tank", "e_cyclic"] = True
    ctx = solve(tank)
    e = list(ctx.network.stores_t.e.Tank)
    assert e[-1] == pytest.approx(0)  # the loop closes where it started
    values = {"window_discharged_energy": 4, "energy_capacity": 10, "equivalent_cycles": 0.4}
    frames = ask(checked, ctx, "storage-cycles/1", "Tank", "Store", values)
    assert answered(checked, frames) == pytest.approx(values)
    assert "not verified" not in public_text(frames)


def test_a_store_with_a_reversed_sign_is_refused(tank, checked):
    tank.network.stores.at["Tank", "sign"] = -1
    ctx = solve(tank)
    assert reasons(ask(checked, ctx, "storage-cycles/1", "Tank", "Store", {})) == {"unsupported_storage_boundary"}


def test_a_line_states_mva_and_an_unbounded_build_never_reads_as_at_its_bound(built, checked):
    n = built.network
    n.add("Bus", "B2", v_nom=110)
    n.add("Line", "L12", bus0="B1", bus1="B2", s_nom=8, x=0.1)
    n.add("Generator", "G3", bus="B1", p_nom_extendable=True, capital_cost=1, marginal_cost=0)
    ctx = solve(built)
    frames = ask(checked, ctx, "capacity/1", "L12", "Line", {"optimal_capacity": 8, "capacity_change": 0})
    assert "Line s_nom in MVA: apparent power." in public_text(frames)
    built_g3 = float(ctx.network.generators.at["G3", "p_nom_opt"])
    frames = ask(checked, ctx, "capacity/1", "G3", "Generator",
                 {"optimal_capacity": built_g3, "capacity_change": built_g3})
    assert answered(checked, frames) == pytest.approx({"optimal_capacity": built_g3, "capacity_change": built_g3})
    assert "upper bound" not in public_text(frames)


def test_a_window_on_a_capacity_question_is_not_silently_dropped():
    from harness import assurance_scope

    assert assurance_scope.from_message("Show the capacity for generator G2; from a to b", "p", {}) is None
