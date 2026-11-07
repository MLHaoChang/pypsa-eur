"""AA-05 S5a test support: one solved context and exact checked scope per profile.

For every key of `harness.calculations.PROFILES`, `DRIVERS[profile]` names the
fixture that builds the profile's own test network, the steps that solve it
and add any state the profile reads, and the exact checked scope to pass to
`services.assurance_records.capture_scoped(owner(checked), scope)`.

Each driver reuses the fixtures and helpers of that profile's own test module;
it does not restate their networks. A test module uses it so::

    from tests.assurance_acceptance.fact_profiles import *  # noqa: F401,F403

    @pytest.mark.parametrize("profile", sorted(DRIVERS))
    def test_x(request, checked, profile):
        ctx, scope = build(request, profile)
        record = assurance_records.capture_scoped(owner(checked), scope)

The star import brings in every fixture a driver names (`FIXTURES`) and the
autouse `fact_store`, which gives the module a private app-data directory and
enables every profile, opt-in ones included.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import copy
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
import pytest

from harness import calculations
from services import assurance_records
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import solved  # noqa: F401
from tests.test_branch_loading_assurance import grid  # noqa: F401
from tests.test_branch_loading_assurance import solve as solve_grid
from tests.test_capacity_cycles_assurance import built, tank  # noqa: F401
from tests.test_carrier_assurance import _solve as solve_carrier
from tests.test_carrier_assurance import wind  # noqa: F401
from tests.test_copt_assurance import classical  # noqa: F401
from tests.test_energy_balance_assurance import exporting  # noqa: F401
from tests.test_grid_readiness_assurance import hub  # noqa: F401
from tests.test_harness_reliability import scope as cost_scope
from tests.test_investment_assurance import hand, lcoe_hand, project, store  # noqa: F401
from tests.test_lost_load_assurance import short  # noqa: F401
from tests.test_lost_load_assurance import solve as solve_through_api
from tests.test_result_profiles_assurance import gas, priced, windy  # noqa: F401
from tests.test_result_profiles_assurance import solve
from tests.test_site_import_assurance import importing  # noqa: F401
from tests.test_storage_assurance import storage  # noqa: F401
from tests.test_storage_cost_assurance import BASIS, record_solve
from tests.test_workflow_tools import baseline  # noqa: F401


@pytest.fixture(autouse=True)
def fact_store(tmp_path, monkeypatch):
    """As each profile module's own `private_store`, for all profiles at once."""
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv(
        "PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", ",".join(calculations.PROFILES)
    )


@pytest.fixture
def shed(short, client, session_state):
    """lost-load/1's VoLL slacks exist only in the API solve, so solve through it."""
    solve_through_api(client, session_state)
    return short


def owner(checked):
    """The owner id `capture_scoped` takes: the signed-in actor, as the checked
    stream records it (`checked` signs that actor in through `client`)."""
    return assurance_records.actor_id()


@dataclass(frozen=True)
class Driver:
    fixture: str  # pytest fixture whose value `prepare` receives
    prepare: Callable  # fixture value -> solved ProjectContext
    scope: Callable  # ProjectContext -> exact checked scope


def _scope(profile, entity, **options):
    def make(ctx):
        return {
            "version": 1,
            "profile": profile,
            "project_id": ctx.project_uuid,
            "entity_id": entity,
            "retain": False,
            **copy.deepcopy(options),
        }

    return make


def _same(ctx):
    return ctx


def _storage_cost(ctx):
    """test_storage_cost_assurance's `priced`, on its `storage`."""
    n = ctx.network
    n.storage_units.at["Battery", "capital_cost"] = 3
    n.storage_units_t.marginal_cost = pd.DataFrame(
        {"Battery": [99, 2, 4]}, index=n.snapshots
    )
    n.storage_units_t.marginal_cost_storage = pd.DataFrame(
        {"Battery": [0.5, 1, 7]}, index=n.snapshots
    )
    record_solve(ctx)
    return ctx


def _stored(mutate):
    def prepare(ctx):
        store(ctx, mutate)
        return ctx

    return prepare


DRIVERS = {
    "generator-dispatch/1": Driver(
        "solved", _same, _scope("generator-dispatch/1", "G1")
    ),
    "generator-cost/1": Driver("solved", _same, cost_scope),
    "network-linear-cost/1": Driver(
        "solved",
        _same,
        lambda ctx: cost_scope(
            ctx, profile="network-linear-cost/1", entity_id="network"
        ),
    ),
    "storage-unit-dispatch/1": Driver(
        "storage", _same, _scope("storage-unit-dispatch/1", "Battery")
    ),
    "storage-unit-cost/1": Driver(
        "storage",
        _storage_cost,
        _scope("storage-unit-cost/1", "Battery", cost_basis=BASIS),
    ),
    "carrier-energy/1": Driver(
        "wind", solve_carrier, _scope("carrier-energy/1", "wind")
    ),
    "branch-loading/1": Driver("grid", solve_grid, _scope("branch-loading/1", "L12")),
    "network-loading/1": Driver(
        "grid", solve_grid, _scope("network-loading/1", "network")
    ),
    "lost-load/1": Driver("shed", _same, _scope("lost-load/1", "L1")),
    "bus-price/1": Driver(
        "priced", lambda ctx: solve(ctx, voll=1000), _scope("bus-price/1", "B1")
    ),
    "generator-emissions/1": Driver(
        "gas", solve, _scope("generator-emissions/1", "G1")
    ),
    "network-emissions/1": Driver(
        "gas", solve, _scope("network-emissions/1", "network")
    ),
    "curtailment/1": Driver("windy", solve, _scope("curtailment/1", "G1")),
    "capacity/1": Driver(
        "built", solve, _scope("capacity/1", "G2", component_class="Generator")
    ),
    "storage-cycles/1": Driver(
        "tank", solve, _scope("storage-cycles/1", "Tank", component_class="Store")
    ),
    "energy-balance/1": Driver(
        "exporting", solve, _scope("energy-balance/1", "network")
    ),
    "site-import/1": Driver("importing", solve, _scope("site-import/1", "network")),
    "investment-case/1": Driver(
        "project", _stored(hand), _scope("investment-case/1", "network")
    ),
    "investment-lcoe/1": Driver(
        "project", _stored(lcoe_hand), _scope("investment-lcoe/1", "network")
    ),
    "copt-screening/1": Driver(
        "classical", _same, _scope("copt-screening/1", "network")
    ),
    "grid-readiness/1": Driver(
        "hub", lambda value: value[0], _scope("grid-readiness/1", "network")
    ),
}
assert set(DRIVERS) == set(calculations.PROFILES), "every profile needs a driver"

# Every fixture a driver names, plus those they depend on, for a star import.
FIXTURES = [
    fact_store,
    baseline,
    checked,
    solved,
    storage,
    wind,
    grid,
    short,
    shed,
    priced,
    gas,
    windy,
    built,
    tank,
    exporting,
    importing,
    project,
    classical,
    hub,
]
__all__ = [
    "DRIVERS",
    "Driver",
    "FIXTURES",
    "build",
    "owner",
    *(f.__name__ for f in FIXTURES),
]


def build(request, profile):
    """The solved context and exact checked scope for `profile`."""
    driver = DRIVERS[profile]
    ctx = driver.prepare(request.getfixturevalue(driver.fixture))
    return ctx, driver.scope(ctx)
