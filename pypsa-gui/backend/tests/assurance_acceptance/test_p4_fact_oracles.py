"""AA-05 S5a (AA05-FREEZE-1 (iii); GK B (34) K3-K5): raw-data oracles.

One metric per profile module is recomputed by `fact_oracles` from the raw
fixture frames and compared with the fact `capture_scoped` records. The oracle
never calls the service under test; a test holds its imports to numpy, pandas
and the standard library.

- K4: every fixture is solved with non-unit snapshot_weightings whose
  generators, objective and stores columns differ from each other at every
  snapshot, so a wrong column gives a different value. Exact adjacent windows
  add up to the full horizon; an overlapping pair does not. A mean is the
  weight-weighted mean of each half.
- K5: one plausible-but-wrong aggregate per family, each with its own test:
  an unweighted sum, a flipped storage sign, an excluded row included.
- K3: values compare exactly where the oracle's arithmetic matches the
  service's term by term; otherwise pytest.approx(rel=1e-12), with the reason.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import ast
import math
import pathlib

import numpy as np
import pandas as pd
import pytest

from harness import fact_identity
from services import assurance_records as records
from services import chat_tools, project_registry, workflow_tools
from tests.assurance_acceptance import fact_oracles as oracle
from tests.assurance_acceptance.fact_profiles import *  # noqa: F401,F403
from tests.assurance_acceptance.fact_profiles import DRIVERS, owner
from tests.test_investment_assurance import hand, store
from tests.test_lost_load_assurance import solve as solve_through_api

COLUMNS = ("generators", "objective", "stores")

# Per fixture: the weights set before the solve; every column differs from the
# others at each snapshot and changes between the halves.
WEIGHTS = {
    "solved": {
        "generators": [2.0, 0.5],
        "objective": [1.5, 3.0],
        "stores": [0.25, 4.0],
    },
    "storage": {
        "generators": [3.0, 4.0, 5.0],
        "objective": [1.0, 3.0, 1.0],
        "stores": [2.0, 1.5, 0.5],
    },
    "wind": {
        "generators": [2.0, 1.0, 3.0],
        "objective": [0.5, 2.0, 1.5],
        "stores": [4.0, 0.25, 1.0],
    },
    "grid": {
        "generators": [2.0, 1.0, 3.0],
        "objective": [1.5, 0.5, 2.0],
        "stores": [0.75, 3.0, 1.25],
    },
    "short": {"generators": [2.0, 3.0], "objective": [1.0, 2.0], "stores": [4.0, 0.5]},
    "priced": {
        "generators": [1.0, 3.0],
        "objective": [2.0, 0.5],
        "stores": [0.25, 1.5],
    },
    "gas": {"generators": [2.0, 1.0], "objective": [0.5, 3.0], "stores": [1.5, 4.0]},
    "windy": {"generators": [2.0, 1.0], "objective": [3.0, 0.5], "stores": [0.75, 2.5]},
    "built": {"generators": [2.0, 0.5], "objective": [1.5, 3.0], "stores": [4.0, 0.25]},
    "tank": {"generators": [3.0, 1.5], "objective": [0.5, 2.0], "stores": [2.5, 0.5]},
    "exporting": {
        "generators": [2.0, 0.5],
        "objective": [1.5, 3.0],
        "stores": [0.25, 4.0],
    },
    "importing": {
        "generators": [2.0, 1.0],
        "objective": [0.5, 1.5],
        "stores": [3.0, 0.25],
    },
    "project": {
        "generators": [2.0, 0.5],
        "objective": [1.5, 3.0],
        "stores": [0.25, 4.0],
    },
    "classical": {
        "generators": [2.0, 5.0],
        "objective": [1.0, 3.0],
        "stores": [4.0, 0.5],
    },
    "hub": {"generators": [2.0, 0.5], "objective": [1.5, 3.0], "stores": [0.25, 4.0]},
}


def solve_again(ctx):
    """The fixtures' own solve steps, for a fixture that solved before the weights."""
    assert ctx.network.optimize(solver_name="highs") == ("ok", "optimal")
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(ctx.network, ctx.solver_state["solver_config"])
    return ctx


def overloaded(ctx):
    """branch-loading/1's solve, then observed flows above the limit at t0 and t2
    (10 MW on 8, 5 MW on 4), as test_branch_loading_assurance's `overload`."""
    ctx = DRIVERS["branch-loading/1"].prepare(ctx)
    lines = ctx.network.lines_t
    for i, flow in ((0, 10.0), (2, 5.0)):
        lines.p0.iloc[i, 0], lines.p1.iloc[i, 0] = flow, -flow
    return ctx


def stored(ctx):
    solve_again(ctx)
    store(ctx, hand)
    return ctx


# Profiles whose fixture solves itself, or whose prepare must differ for the weights.
PREPARE = {
    "generator-dispatch/1": solve_again,
    "storage-unit-dispatch/1": solve_again,
    "branch-loading/1": overloaded,
    "investment-case/1": stored,
}


def weighted(request, profile, weights=None, fixture=None):
    """`build`, with the K4 weights set on the network before it is solved."""
    driver = DRIVERS[profile]
    name = fixture or ("short" if profile == "lost-load/1" else driver.fixture)
    value = request.getfixturevalue(name)
    ctx = value[0] if isinstance(value, tuple) else value
    for column, values in (weights or WEIGHTS[name]).items():
        ctx.network.snapshot_weightings[column] = values
    if profile == "lost-load/1":
        solve_through_api(
            request.getfixturevalue("client"), request.getfixturevalue("session_state")
        )
    else:
        ctx = PREPARE.get(profile, driver.prepare)(value)
    return ctx, driver.scope(ctx)


def fact(checked, scope, metric, **window):
    record = records.capture_scoped(owner(checked), {**scope, **window})
    values = [f["value"] for f in record["facts"] if f["metric"] == metric]
    assert len(values) == 1, record["slots"]
    return values[0]


def stamp(n, i):
    return n.snapshots[i].isoformat()


def readiness_rows(ctx):
    from services.adequacy.grid_readiness import grid_readiness_for

    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, ctx.project_uuid)
    return grid_readiness_for(project)["rows"]


# (profile, metric, oracle over the solved context, exact?)
# Approximate where the order differs: the COPT table is convolved by
# np.convolve and summed by fsum, the service accumulates in place and reads
# prefix cumsums; the NPV's discount factors are numpy powers, the service's
# Python float powers.
CASES = [
    (
        "generator-dispatch/1",
        "dispatch_energy",
        lambda ctx, **k: oracle.dispatch_energy(ctx.network, "G1", **k),
        True,
    ),
    (
        "storage-unit-dispatch/1",
        "discharged_energy",
        lambda ctx, **k: oracle.discharged_energy(ctx.network, "Battery", **k),
        True,
    ),
    (
        "storage-unit-cost/1",
        "storage_discharge_expenditure",
        lambda ctx, **k: oracle.storage_discharge_expenditure(
            ctx.network, "Battery", **k
        ),
        True,
    ),
    (
        "carrier-energy/1",
        "carrier_generator_energy",
        lambda ctx, **k: oracle.carrier_generator_energy(ctx.network, "wind", **k),
        True,
    ),
    (
        "branch-loading/1",
        "hours_above_rating",
        lambda ctx, **k: oracle.hours_above_rating(ctx.network, "L12", **k),
        True,
    ),
    (
        "lost-load/1",
        "lost_load_energy",
        lambda ctx, **k: oracle.lost_load_energy(
            ctx.solver_state["last_lost_load"]["lost_load_t"],
            ctx.network.snapshot_weightings,
            "L1",
            **k,
        ),
        True,
    ),
    (
        "bus-price/1",
        "mean_price",
        lambda ctx, **k: oracle.mean_price(ctx.network, "B1", **k),
        True,
    ),
    (
        "generator-emissions/1",
        "generator_emissions",
        lambda ctx, **k: oracle.generator_emissions(ctx.network, "G1", **k),
        True,
    ),
    (
        "curtailment/1",
        "curtailed_energy",
        lambda ctx, **k: oracle.curtailed_energy(ctx.network, "G1", **k),
        True,
    ),
    (
        "capacity/1",
        "optimal_capacity",
        lambda ctx, **k: oracle.optimal_capacity(ctx.network, "G2"),
        True,
    ),
    (
        "storage-cycles/1",
        "equivalent_cycles",
        lambda ctx, **k: oracle.equivalent_cycles(ctx.network, "Tank", **k),
        True,
    ),
    (
        "energy-balance/1",
        "served_energy",
        lambda ctx, **k: oracle.served_energy(ctx.network, **k),
        True,
    ),
    (
        "site-import/1",
        "grid_import_energy",
        lambda ctx, **k: oracle.grid_import_energy(ctx.network, **k),
        True,
    ),
    (
        "investment-case/1",
        "equity_npv",
        lambda ctx, **k: oracle.equity_npv(
            ctx.solver_state["investment_case_report"]["sections"]["project"]["payload"]
        ),
        False,
    ),
    (
        "copt-screening/1",
        "screening_lole",
        lambda ctx, **k: oracle.screening_lole(ctx.network, **k),
        False,
    ),
    (
        "grid-readiness/1",
        "checked_pass",
        lambda ctx, **k: oracle.checked_pass(readiness_rows(ctx)),
        True,
    ),
]
ORACLES = {profile: compute for profile, _m, compute, _e in CASES}


def same(value, expected, exact):
    if exact:
        assert value == expected
    else:
        assert value == pytest.approx(expected, rel=1e-12)


def test_one_oracle_per_profile_module():
    modules = {
        line.removeprefix("# ").strip()
        for line in pathlib.Path(oracle.__file__).read_text().splitlines()
        if line.startswith("# ") and line.endswith(".py")
    }
    assert len(modules) == len(CASES) == 16
    assert len({profile for profile, *_ in CASES}) == 16


@pytest.mark.parametrize(
    "profile,metric,compute,exact", CASES, ids=[c[0] for c in CASES]
)
def test_the_oracle_equals_the_recorded_fact(
    request, checked, profile, metric, compute, exact
):
    ctx, scope = weighted(request, profile)
    value = fact(checked, scope, metric)
    expected = compute(ctx)
    same(value, expected, exact)
    if profile != "grid-readiness/1":
        assert value != 0  # a zero fact would hide a wrong aggregate
    basis = fact_identity.WEIGHT_BASIS[metric]
    weights = ctx.network.snapshot_weightings
    assert not (weights == 1).all().all()
    for column in COLUMNS:
        assert not weights[column].eq(weights[column].iloc[0]).all()
    # K4: the fact's weight basis is the only column that gives it.
    for column in COLUMNS:
        if basis.startswith(column):
            continue
        if basis != "none":
            assert compute(ctx, column=column) != pytest.approx(value, rel=1e-9), column


# ── K4: exact windows ────────────────────────────────────────────────────


SPLITS = [
    # (profile, metric, last snapshot of the first half): a generators-, stores-
    # and objective-weighted sum.
    ("generator-dispatch/1", "dispatch_energy", 0),
    ("storage-unit-dispatch/1", "discharged_energy", 1),
    ("storage-unit-cost/1", "storage_discharge_expenditure", 1),
]


@pytest.mark.parametrize("profile,metric,cut", SPLITS, ids=[s[0] for s in SPLITS])
def test_adjacent_windows_add_up_to_the_full_horizon(
    request, checked, profile, metric, cut
):
    ctx, scope = weighted(request, profile)
    n, last = ctx.network, len(ctx.network.snapshots) - 1
    first = {"start": stamp(n, 0), "end": stamp(n, cut)}
    second = {"start": stamp(n, cut + 1), "end": stamp(n, last)}
    whole = fact(checked, scope, metric)
    halves = [fact(checked, scope, metric, **w) for w in (first, second)]
    compute = ORACLES[profile]
    for value, window in zip(halves, (first, second)):
        assert value == compute(ctx, **window)
        assert value != 0
    # Each half is its own fsum, so their sum is within an ulp of the whole.
    assert math.fsum(halves) == pytest.approx(whole, rel=1e-12)
    # The end is inclusive: windows sharing a boundary count it twice.
    overlap = {"start": stamp(n, cut), "end": stamp(n, last)}
    assert math.fsum(
        [halves[0], fact(checked, scope, metric, **overlap)]
    ) != pytest.approx(whole, rel=1e-9)
    # A one-snapshot window holds that snapshot, not nothing.
    single = {"start": stamp(n, last), "end": stamp(n, last)}
    assert fact(checked, scope, metric, **single) == compute(ctx, **single) != 0


def four_hours(ctx):
    """`priced` over four hours: loads 5, 25, 25, 5 give prices 10, 30, 30, 10."""
    n = ctx.network
    n.set_snapshots(pd.date_range("2026-01-01", periods=4, freq="h"))
    n.loads_t.p_set = pd.DataFrame({"L1": [5.0, 25.0, 25.0, 5.0]}, index=n.snapshots)
    return ctx


def test_mean_price_is_the_weighted_mean_of_each_half(request, checked):
    ctx = four_hours(request.getfixturevalue("priced"))
    weights = {
        "generators": [1.0, 3.0, 0.5, 2.0],
        "objective": [2.0, 0.5, 1.5, 4.0],
        "stores": [0.25, 1.5, 3.0, 0.75],
    }
    ctx, scope = weighted(request, "bus-price/1", weights)
    n = ctx.network
    assert list(n.buses_t.marginal_price.B1) == pytest.approx([10, 30, 30, 10])
    halves = [
        {"start": stamp(n, 0), "end": stamp(n, 1)},
        {"start": stamp(n, 2), "end": stamp(n, 3)},
    ]
    means = [fact(checked, scope, "mean_price", **w) for w in halves]
    # (1 x 10 + 3 x 30) / 4 = 25 and (0.5 x 30 + 2 x 10) / 2.5 = 14; plain means are 20.
    assert means == [oracle.mean_price(n, "B1", **w) for w in halves] == [25.0, 14.0]
    assert means != [20.0, 20.0]
    whole = fact(checked, scope, "mean_price")
    assert whole == oracle.mean_price(n, "B1") == 135 / 6.5
    # The halves recombine by their weight, never as a mean of means.
    assert (4.0 * means[0] + 2.5 * means[1]) / 6.5 == pytest.approx(whole, rel=1e-12)
    assert sum(means) / 2 != pytest.approx(whole, rel=1e-9)


# ── K5: one wrong aggregate per family, each killed here ─────────────────


def test_an_unweighted_sum_is_not_dispatch_energy(request, checked):
    ctx, scope = weighted(request, "generator-dispatch/1")
    value = fact(checked, scope, "dispatch_energy")
    assert value == oracle.dispatch_energy(ctx.network, "G1")
    # 2 x 5 + 0.5 x 7 = 13.5 MWh; the unweighted 5 + 7 = 12 is not it.
    assert value == 13.5
    assert math.fsum(ctx.network.generators_t.p.G1) == pytest.approx(12) != value


def test_a_flipped_storage_unit_sign_is_not_discharged_energy(request, checked):
    ctx, scope = weighted(request, "storage-unit-dispatch/1")
    n = ctx.network
    value = fact(checked, scope, "discharged_energy")
    assert value == oracle.discharged_energy(n, "Battery")
    # p = p_dispatch - p_store; reading -p as discharge counts the charge instead.
    p = n.storage_units_t.p.Battery.to_numpy()
    w = n.snapshot_weightings.stores.to_numpy()
    flipped = math.fsum((w * np.maximum(-p, 0.0)).tolist())
    assert flipped == pytest.approx(math.fsum(w * n.storage_units_t.p_store.Battery))
    assert flipped != pytest.approx(value, rel=1e-9)


def test_a_signed_store_flow_is_not_equivalent_cycles(request, checked):
    ctx, scope = weighted(request, "storage-cycles/1")
    n = ctx.network
    value = fact(checked, scope, "equivalent_cycles")
    assert value == oracle.equivalent_cycles(n, "Tank")
    # Store p > 0 leaves the store. A lossless store that starts and ends empty
    # charges what it discharges, so -p clipped gives the same figure; summing
    # the signed p subtracts the charging hour and gives 0 instead.
    w, p = n.snapshot_weightings.stores.to_numpy(), n.stores_t.p.Tank.to_numpy()
    signed = math.fsum((w * p).tolist()) / n.stores.at["Tank", "e_nom"]
    assert value == pytest.approx(0.2)
    assert signed != pytest.approx(value, rel=1e-9)


def test_an_excluded_row_included_is_not_checked_pass(request, checked):
    ctx, scope = weighted(request, "grid-readiness/1")
    rows = readiness_rows(ctx)
    value = fact(checked, scope, "checked_pass")
    assert value == oracle.checked_pass(rows)
    # Rows that pass on inputs the scorecard does not store, or on a yes-or-no
    # requirement, are excluded; counting every stored pass includes them.
    excluded = [
        r["id"]
        for r in rows
        if r.get("verdict") == "pass" and oracle.readiness_verdict(r) is None
    ]
    assert excluded
    assert (
        sum(r.get("verdict") == "pass" for r in rows) == value + len(excluded) != value
    )


# ── the oracle reads raw data only ───────────────────────────────────────


ALLOWED = {
    "numpy",
    "pandas",
    "math",
    "typing",
    "collections",
    "dataclasses",
    "__future__",
}


def test_the_oracle_imports_no_code_under_test():
    tree = ast.parse(pathlib.Path(oracle.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            imported.add(node.module.split(".")[0])
    assert imported and imported <= ALLOWED, imported - ALLOWED
