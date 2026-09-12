"""
Representative-week sampling writes EVERY snapshot-weighting column equally.

This pins the premise behind a "known limitation" in
`docs/superpowers/findings/2026-08-03-compare-tab-correctness.md`, which
said the `binding_hours` basis fix (suspect S2) was:

    verified ONLY by the dedicated `build_weighting_basis_network` fixture
    — a real representative-week run (where the two columns genuinely
    differ [...]) has not been observed by this examination.

That run cannot exist. `sample_representative_weeks` assigns the SAME
vector to every column of `n.snapshot_weightings`, deliberately — its own
comment says "Set all three weight columns so the LP objective, generator
energy balance and storage SoC equations scale consistently". So a
representative-week network is weighting-basis-degenerate by construction,
and the `objective`/`generators` split can only be created by a manual edit
(the weightings CSV upload or PATCH), which is exactly what
`build_weighting_basis_network` models.

The examination's coverage was therefore already appropriate. What was
missing is this guard: if anyone ever makes rep-week differentiate the
columns, `_compute_loading_summary`'s choice of basis becomes load-bearing
on real networks and must be re-examined. This test fails loudly at that
moment instead of letting it pass unnoticed.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from models.schemas import SampleWeeksConfig
from routers.network import (
    _user_ts,
    _user_ts_lock,
    sample_representative_weeks,
)

# One full non-leap year of hourly snapshots — sample_representative_weeks
# samples ISO weeks per calendar month out of the existing index.
HOURS_IN_2030 = 8760


def _annual_hourly_network() -> pypsa.Network:
    n = pypsa.Network()
    idx = pd.date_range("2030-01-01", periods=HOURS_IN_2030, freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Generator", "g", bus="B", carrier="gas", p_nom=100.0,
          marginal_cost=10.0)
    n.add("Load", "L", bus="B", p_set=50.0)
    return n


@pytest.fixture
def sampled(install_network):
    """
    Sampling reads its reference index from `_user_ts`, not from the network,
    so a full-year uploaded profile has to be present. Restored afterwards —
    `_user_ts` is module-global and would otherwise leak an 8760-row series
    into every later test in the session.
    """
    n = _annual_hourly_network()
    install_network(n)

    key = ("loads", "p_set", "L")
    with _user_ts_lock:
        previous = dict(_user_ts)
        _user_ts[key] = pd.Series(50.0, index=n.snapshots)
    try:
        sample_representative_weeks(SampleWeeksConfig(n_weeks=1, seed=42))
        yield n
    finally:
        with _user_ts_lock:
            _user_ts.clear()
            _user_ts.update(previous)


def test_rep_week_sampling_sets_every_weighting_column_identically(sampled):
    """
    The invariant. If this ever fails, the Line-loading tab's `binding_hours`
    basis choice (ENERGY, not COST) becomes observable on real
    representative-week networks and suspect S2 must be re-measured there.
    """
    sw = sampled.snapshot_weightings
    assert not sw.empty, "sampling produced no weightings"
    assert len(sw.columns) > 1, (
        "expected several weighting columns (objective / generators / stores)"
    )

    reference = sw[sw.columns[0]]
    for col in sw.columns[1:]:
        assert sw[col].equals(reference), (
            f"snapshot_weightings column {col!r} differs from "
            f"{sw.columns[0]!r} after representative-week sampling — the "
            f"objective/generators basis split is now reachable from a real "
            f"run, so binding_hours must be re-measured against it"
        )


def test_the_sampled_weights_are_not_all_default(sampled):
    """
    Guards the test above from passing vacuously.

    Columns of a network whose weights were never touched are trivially
    identical at 1.0. This asserts sampling actually scaled them, so the
    equality above is a real observation about the sampler.
    """
    sw = sampled.snapshot_weightings
    assert (sw[sw.columns[0]] != 1.0).any(), (
        "no weight departed from the 1.0 default — sampling did not scale "
        "anything and the equality assertion would be vacuous"
    )
