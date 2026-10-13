"""
Representative-week sampling must keep ``snapshot_weightings.stores`` at the
snapshot step, not the annual scale factor.

PyPSA (1.1.x) reads the ``stores`` column as the ELAPSED HOURS in the
StorageUnit and Store state-of-charge balance
(``pypsa/optimization/constraints.py``: ``elapsed_h``). The sampler used to
write its per-hour scale factor (days_in_month / (weeks × 7), about 4.3 for one
week per month) into every column, so each hourly dispatch moved ~4.3 h of
energy through storage. ``objective`` and ``generators`` keep the scale factor;
they turn the sampled hours into a year. Matches the Energy Hub template fix
(S0, #135) and the limited-foresight fix (#96).
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from routers.network import _user_ts, _user_ts_lock

STEP_HOURS = 1.0  # the sampler always builds hourly 168-step weeks


def _annual_hourly_network() -> pypsa.Network:
    n = pypsa.Network()
    idx = pd.date_range("2030-01-01", periods=8760, freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Generator", "g", bus="B", carrier="gas", p_nom=100.0, marginal_cost=10.0)
    n.add("Load", "L", bus="B", p_set=50.0)
    n.add("StorageUnit", "bat", bus="B", p_nom=10.0, max_hours=4.0,
          efficiency_store=0.9, efficiency_dispatch=0.9)
    return n


@pytest.fixture
def sampled(client, install_network):
    n = _annual_hourly_network()
    install_network(n)
    with _user_ts_lock:
        previous = dict(_user_ts)
        _user_ts[("loads", "p_set", "L")] = pd.Series(50.0, index=n.snapshots)
    try:
        r = client.post("/api/network/snapshots/sample_weeks",
                        json={"n_weeks": 1, "seed": 42})
        assert r.status_code == 200, r.text
        yield n, r.json()
    finally:
        with _user_ts_lock:
            _user_ts.clear()
            _user_ts.update(previous)


def test_objective_and_generators_carry_the_scale_factor(sampled):
    n, body = sampled
    sw = n.snapshot_weightings
    expected = pd.concat([
        pd.Series(w["weight"], index=pd.date_range(w["start"], periods=168, freq="h"))
        for w in body["weeks"]
    ]).sort_index()
    for col in ("objective", "generators"):
        assert sw[col].to_numpy() == pytest.approx(expected.to_numpy(), abs=1e-4), col
    assert (sw["objective"] > 1.0).all()
    # Σ objective weight reconstructs the year.
    assert sw["objective"].sum() == pytest.approx(8760.0, abs=1e-6)


def test_stores_is_the_snapshot_step_in_hours(sampled):
    n, _ = sampled
    stores = n.snapshot_weightings["stores"]
    assert (stores == STEP_HOURS).all(), (
        "snapshot_weightings.stores is PyPSA's elapsed hours in the SoC balance; "
        f"sampling wrote {stores.unique()[:3]} instead of the {STEP_HOURS} h step"
    )


def test_one_discharging_snapshot_lowers_soc_by_p_over_eta(sampled):
    """Hand step of PyPSA's StorageUnit balance:
    soc_t = soc_{t-1} + elapsed_h · (η_store·p_store − p_dispatch/η_dispatch)."""
    n, _ = sampled
    su = n.storage_units.loc["bat"]
    elapsed_h = float(n.snapshot_weightings["stores"].iloc[1])
    p, soc_prev = 5.0, 40.0
    soc = soc_prev + elapsed_h * (su.efficiency_store * 0.0
                                  - p / su.efficiency_dispatch)
    assert soc_prev - soc == pytest.approx(p * 1.0 / 0.9)
