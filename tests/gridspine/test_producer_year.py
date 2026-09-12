"""Producer v2: profiles in, rolling-horizon unit commitment.

TWO rolling fixtures, and the reason is measured rather than stylistic.

`rolled` is the brief's shape — 336 h, window 168, overlap 24 — and it covers
the year-scale contract: the dispatch table, the hourly energy balance, and the
commitment texture (some unit off in an RES-rich valley, the whole fleet on at
the peak). It has exactly TWO seams, and that turned out to be too few to test
the seam at all: with the seam carry deliberately broken (`_write_status`
freezing nothing), the assembled 168/24 series still contained ZERO
min-up/min-down violations, because the second window independently re-chose to
keep the two units that had just switched. A green run-length check on that
fixture is therefore evidence of nothing.

`seam_rolled` is 336 h, window 48, overlap 24 — thirteen seams instead of two.
Same mutation on that fixture produces a real violation, so its run-length
check can fail, which is the only reason to trust it when it passes.
`test_seam_carry_holds_across_every_boundary` also asserts the fixture has
enough BINDING unit-seam pairs (a unit that switched one hour before a
boundary, so min-up/min-down reaches across it) to be non-vacuous — that count
is the thing that silently went to nearly zero on the sparse fixture.
"""
import numpy as np
import pandas as pd
import pytest

from gridspine.ingest.pandapower_source import load_case39, load_case39_res
from gridspine.ingest.synthetic_profiles import solar_cf, wind_cf, year_load_shape
from gridspine.producers.pypsa_nodal import (
    LOAD_SHAPE,
    RES_MARGINAL_COST,
    run_uc_rolling,
    to_dispatch_table,
    to_pypsa,
)
from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import validate_dispatch

ROLLING_HOURS = 336
ROLLING_WINDOW = 168
ROLLING_OVERLAP = 24
SEAM_WINDOW = 48   # dense seams: 13 boundaries over the same 336 h
SEAM_OVERLAP = 24
MIN_UP_DOWN = 2  # to_pypsa pins min_up_time == min_down_time == 2
# Lower bound on binding unit-seam pairs in `seam_rolled`. Measured 11 with the
# seam carry intact; the assertion exists so a future profile or cost change
# that quiets the fleet turns the seam test RED instead of vacuously green.
MIN_BINDING_SEAM_PAIRS = 5


def res_profiles(hours):
    """`res_cf` keyed by the sgen canonical names `load_case39_res` invents."""
    wind, solar = wind_cf(hours), solar_cf(hours)
    return {
        "W_BUS_33": wind, "W_BUS_35": wind, "W_BUS_37": wind,
        "S_BUS_34": solar, "S_BUS_36": solar,
    }


# --------------------------------------------------------------------------
# backward compatibility: the inc-1 call must be untouched
# --------------------------------------------------------------------------

def test_default_call_is_byte_identical_to_explicit_none():
    """`to_pypsa(net, 24)` and the fully-defaulted v2 call are the same network."""
    net = load_case39()
    a = to_pypsa(net, snapshots=24)
    b = to_pypsa(net, snapshots=24, load_shape=None, res_cf=None)
    pd.testing.assert_frame_equal(a.generators, b.generators)
    pd.testing.assert_frame_equal(a.buses, b.buses)
    pd.testing.assert_frame_equal(a.lines, b.lines)
    pd.testing.assert_frame_equal(a.transformers, b.transformers)
    pd.testing.assert_frame_equal(a.loads_t.p_set, b.loads_t.p_set)


def test_default_call_still_uses_LOAD_SHAPE_and_adds_no_res():
    net = load_case39()
    n = to_pypsa(net, snapshots=24)
    assert len(n.buses) == len(net.bus)
    assert len(n.generators) == 10
    assert int(n.generators["committable"].sum()) == 9
    per_bus = net.load.groupby("bus")["p_mw"].sum()
    bus_name = net.bus["name"]
    for b, p in per_bus.items():
        expected = np.asarray(LOAD_SHAPE) * float(p)
        got = n.loads_t.p_set[f"LD_{bus_name.at[b]}"].to_numpy()
        assert got == pytest.approx(expected, rel=1e-12)


def test_sgen_rows_are_ignored_when_the_net_has_none():
    """case39 has no sgen, so an empty res_cf is not a missing key."""
    n = to_pypsa(load_case39(), snapshots=24, res_cf={})
    assert len(n.generators) == 10


# --------------------------------------------------------------------------
# profiles in
# --------------------------------------------------------------------------

def test_load_shape_drives_every_load():
    hours = 168
    net = load_case39_res()
    shape = year_load_shape(hours)
    n = to_pypsa(net, snapshots=hours, load_shape=shape, res_cf=res_profiles(hours))
    per_bus = net.load.groupby("bus")["p_mw"].sum()
    bus_name = net.bus["name"]
    assert len(n.snapshots) == hours
    for b, p in per_bus.items():
        expected = shape.to_numpy() * float(p)
        got = n.loads_t.p_set[f"LD_{bus_name.at[b]}"].to_numpy()
        assert got == pytest.approx(expected, rel=1e-12)


def test_res_units_are_curtailable_non_committable_generators():
    hours = 168
    net = load_case39_res()
    cf = res_profiles(hours)
    n = to_pypsa(net, snapshots=hours, load_shape=year_load_shape(hours), res_cf=cf)

    assert len(n.generators) == 15  # 9 thermal + 1 slack + 5 RES
    res_names = list(net.sgen["name"])
    assert set(res_names) <= set(n.generators.index)
    res = n.generators.loc[res_names]
    assert not res["committable"].any()
    assert res["marginal_cost"].to_numpy() == pytest.approx(RES_MARGINAL_COST)
    installed = dict(zip(net.sgen["name"], net.sgen["p_mw"]))
    for name in res_names:
        assert res.at[name, "p_nom"] == pytest.approx(installed[name])
        # curtailable: p_max_pu is the cf series, p_min_pu stays at 0
        assert n.generators_t.p_max_pu[name].to_numpy() == pytest.approx(
            cf[name].to_numpy(), rel=1e-12)
        assert res.at[name, "p_min_pu"] == pytest.approx(0.0)
    # the thermal fleet is untouched by the RES addition
    assert int(n.generators["committable"].sum()) == 9


def test_res_units_sit_on_their_sgen_bus():
    hours = 48
    net = load_case39_res()
    n = to_pypsa(net, snapshots=hours, load_shape=year_load_shape(hours),
                 res_cf=res_profiles(hours))
    bus_name = net.bus["name"]
    for _, s in net.sgen.iterrows():
        assert n.generators.at[s["name"], "bus"] == bus_name.at[s["bus"]]


# --------------------------------------------------------------------------
# contract violations
# --------------------------------------------------------------------------

def test_missing_res_cf_key_is_a_contract_error():
    """A silent 0-profile RES unit would corrupt ranking, so it must raise."""
    hours = 24
    cf = res_profiles(hours)
    del cf["S_BUS_36"]
    with pytest.raises(ContractError, match="S_BUS_36"):
        to_pypsa(load_case39_res(), snapshots=hours,
                 load_shape=year_load_shape(hours), res_cf=cf)


def test_res_cf_omitted_entirely_is_a_contract_error():
    with pytest.raises(ContractError):
        to_pypsa(load_case39_res(), snapshots=24,
                 load_shape=year_load_shape(24), res_cf=None)


def test_res_cf_key_for_no_such_sgen_is_a_contract_error():
    """The sibling of the missing-key rule: a typo'd key must not pass silently."""
    hours = 24
    cf = res_profiles(hours)
    cf["W_BUS_99"] = wind_cf(hours)
    with pytest.raises(ContractError, match="W_BUS_99"):
        to_pypsa(load_case39_res(), snapshots=hours,
                 load_shape=year_load_shape(hours), res_cf=cf)


def test_wrong_length_load_shape_is_a_contract_error():
    with pytest.raises(ContractError, match="load_shape"):
        to_pypsa(load_case39_res(), snapshots=24,
                 load_shape=year_load_shape(48), res_cf=res_profiles(24))


def test_wrong_length_res_cf_is_a_contract_error():
    hours = 24
    cf = res_profiles(hours)
    cf["W_BUS_33"] = wind_cf(hours + 1)
    with pytest.raises(ContractError, match="W_BUS_33"):
        to_pypsa(load_case39_res(), snapshots=hours,
                 load_shape=year_load_shape(hours), res_cf=cf)


@pytest.mark.parametrize("window, overlap", [
    (100, 24),   # window not a whole number of days
    (0, 0),      # empty window
    (-24, 0),    # negative window
    (168, 168),  # overlap == window would never advance
    (168, 200),  # overlap > window
    (168, -1),   # negative overlap
])
def test_run_uc_rolling_rejects_bad_windows(window, overlap):
    n = to_pypsa(load_case39(), snapshots=24)
    with pytest.raises(ContractError):
        run_uc_rolling(n, window=window, overlap=overlap)


# --------------------------------------------------------------------------
# the rolling solve itself
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rolled():
    net = load_case39_res()
    n = to_pypsa(net, snapshots=ROLLING_HOURS,
                 load_shape=year_load_shape(ROLLING_HOURS),
                 res_cf=res_profiles(ROLLING_HOURS))
    return run_uc_rolling(n, window=ROLLING_WINDOW, overlap=ROLLING_OVERLAP)


@pytest.fixture(scope="module")
def rolled_table(rolled):
    return to_dispatch_table(rolled)


@pytest.fixture(scope="module")
def seam_rolled():
    """Same horizon, thirteen seams. See the module docstring for why."""
    net = load_case39_res()
    n = to_pypsa(net, snapshots=ROLLING_HOURS,
                 load_shape=year_load_shape(ROLLING_HOURS),
                 res_cf=res_profiles(ROLLING_HOURS))
    return run_uc_rolling(n, window=SEAM_WINDOW, overlap=SEAM_OVERLAP)


@pytest.fixture(scope="module")
def seam_table(seam_rolled):
    return to_dispatch_table(seam_rolled)


def test_rolling_dispatch_table_validates(rolled, rolled_table):
    validate_dispatch(rolled_table)
    assert len(rolled_table) == 15 * ROLLING_HOURS
    assert sorted(rolled_table["hour"].unique()) == list(range(ROLLING_HOURS))


def test_rolling_energy_balance_each_hour(rolled, rolled_table):
    gen = rolled_table.groupby("hour")["p_mw"].sum()
    load = rolled.loads_t.p_set.sum(axis=1)
    load.index = range(len(load))
    err = ((gen - load).abs() / load)
    assert err.max() < 0.01, f"worst hour {err.idxmax()}: {err.max():.4%}"


def test_rolling_year_has_real_commitment_texture(rolled, rolled_table):
    """Some hour with a committable unit OFF, some hour with all of them ON."""
    committable = list(rolled.generators.index[rolled.generators["committable"]])
    st = (rolled_table[rolled_table["unit_id"].isin(committable)]
          .pivot(index="hour", columns="unit_id", values="status"))
    on_count = st.sum(axis=1)
    assert on_count.min() < len(committable), "no unit was ever de-committed"
    assert (on_count == len(committable)).any(), "the peak never commits the fleet"


def _run_length_violations(status: np.ndarray) -> np.ndarray:
    """(T, U) int status matrix -> (n_runs, U) bool mask of too-short interior runs.

    A run is a maximal block of equal status in one unit's series. The FIRST and
    LAST run of each series are exempt: the first is truncated by the start of
    the horizon (PyPSA's `up_time_before` lets a unit that has been up one hour
    already shut down after one more) and the last by its end.
    """
    t, u = status.shape
    change = np.diff(status, axis=0) != 0                      # (T-1, U)
    run_id = np.vstack([np.zeros((1, u), int), np.cumsum(change, axis=0)])
    n_runs = int(run_id.max()) + 1
    counts = np.zeros((n_runs, u), int)
    np.add.at(counts, (run_id, np.tile(np.arange(u), (t, 1))), 1)
    last = run_id[-1]                                          # (U,)
    idx = np.arange(n_runs)[:, None]
    interior = (idx >= 1) & (idx <= last[None, :] - 1)
    return interior & (counts < MIN_UP_DOWN)


def test_run_length_helper_catches_a_planted_violation():
    """The check must be able to fail — a green run-length test is worthless if
    the helper cannot see a 1-hour blip."""
    good = np.array([[0], [0], [1], [1], [1], [0], [0]])
    assert not _run_length_violations(good).any()
    bad = good.copy()
    bad[3, 0] = 0  # 1 1 1 -> 1 0 1: a one-hour shutdown between two interior runs
    assert _run_length_violations(bad).any()


def _status_matrix(n, table):
    committable = list(n.generators.index[n.generators["committable"]])
    st = (table[table["unit_id"].isin(committable)]
          .pivot(index="hour", columns="unit_id", values="status")
          .sort_index())
    return committable, st


def test_no_min_up_down_violation_anywhere(seam_rolled, seam_table):
    """THE seam contract. Windows are solved independently over their own
    snapshots; only the carried statuses stop a window from undoing a start-up
    the previous window committed to one hour before the boundary."""
    committable, st = _status_matrix(seam_rolled, seam_table)
    status = st.to_numpy().astype(int)

    # non-vacuity: a series with no transitions has no interior runs and passes
    # for free. The seam can only be tested if units actually cycle.
    transitions = int((np.diff(status, axis=0) != 0).sum())
    assert transitions > 10, f"only {transitions} status changes: test is vacuous"

    bad = _run_length_violations(status)
    if bad.any():
        units = [committable[i] for i in sorted(set(np.nonzero(bad)[1]))]
        pytest.fail(f"min_up/min_down violated for units {units}")


def test_seam_carry_holds_across_every_boundary(seam_rolled, seam_table):
    """The sharp, local form of the same contract, plus its own non-vacuity
    guard: a unit that switched at `seam - 1` has one hour of its min-up (or
    min-down) left to serve, and that hour lies in the NEXT window."""
    _, st = _status_matrix(seam_rolled, seam_table)
    step = SEAM_WINDOW - SEAM_OVERLAP
    seams = [s for s in range(step, ROLLING_HOURS, step) if s >= 2]
    assert seams, "no seam in the horizon: the rolling path is untested"

    binding = 0
    for seam in seams:
        just_switched = st.loc[seam - 1] != st.loc[seam - 2]
        for unit in st.columns[just_switched]:
            binding += 1
            assert st.at[seam, unit] == st.at[seam - 1, unit], (
                f"{unit} switched at hour {seam - 1} and the window starting at "
                f"{seam} switched it straight back: min_up/min_down = "
                f"{MIN_UP_DOWN} h was not carried across the seam"
            )
    assert binding >= MIN_BINDING_SEAM_PAIRS, (
        f"only {binding} binding unit-seam pairs: the seam carry is barely "
        f"exercised, so a passing run proves little"
    )


def test_sparse_seam_fixture_also_has_no_violation(rolled, rolled_table):
    """The brief's 168/24 shape, checked for completeness. Kept deliberately
    weak in the docstring's terms: with only two seams this passed even with
    the seam carry removed, so it is a regression guard, not the seam test."""
    committable, st = _status_matrix(rolled, rolled_table)
    bad = _run_length_violations(st.to_numpy().astype(int))
    if bad.any():
        units = [committable[i] for i in sorted(set(np.nonzero(bad)[1]))]
        pytest.fail(f"min_up/min_down violated for units {units}")


def test_non_optimal_window_names_the_window():
    """An infeasible horizon must fail loudly with the window in the message."""
    hours = 48
    net = load_case39_res()
    n = to_pypsa(net, snapshots=hours, load_shape=year_load_shape(hours),
                 res_cf=res_profiles(hours))
    # starve the fleet: no unit can serve the load -> infeasible in window 0
    n.generators["p_nom"] = 0.0
    with pytest.raises(RuntimeError, match=r"window \[0"):
        run_uc_rolling(n, window=24, overlap=0)
