"""
Bug reproduction + fix verification for the flat→multi-period profile
preservation bug.

Setup: build a flat-snapshot network, upload a Solar p_max_pu profile,
promote to multi-period, then assert that:
  1. `n.generators_t.p_max_pu` index matches `n.snapshots` (no mixed
     flat/MultiIndex mess).
  2. Every (period, gen) cell has the right profile value (broadcast from
     the user's flat upload).
  3. After a fresh `_reapply_user_ts_to_network`, the index stays clean.

Pre-fix: parent's column ended up on stale flat-DatetimeIndex rows that
didn't overlap with the new MultiIndex n.snapshots, so PyPSA's LP fell
back to scalar p_max_pu=1.0 → renewable dispatch flat at p_nom.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

import numpy as np
import pandas as pd
import pypsa
from routers import network as net_router
from services.pypsa_service import PyPSAService

PASS = 0
FAIL = 0


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}" + (f" — {msg}" if msg else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {msg}" if msg else ""))


def _crashed(label: str, exc: BaseException) -> None:
    """
    A scenario that raised never ran its assertions, so it must COUNT as a
    failure — printing the traceback and moving on leaves the driver exiting 0
    while testing nothing. That is exactly how the stale
    `routers.simulation.get_*` references below went unnoticed: the scenarios
    crashed on every run and the summary still read "Fail: 0".
    """
    _step(f"{label} ran without crashing", False, f"{type(exc).__name__}: {exc}")

def _solar_shape(snapshots: pd.DatetimeIndex) -> np.ndarray:
    """Sinusoidal solar profile — peaks at noon, zero at night. Average ~0.2."""
    hours = snapshots.hour.values.astype(float)
    raw = np.exp(-0.5 * ((hours - 13.0) / 3.5) ** 2)
    raw = np.where((hours < 5) | (hours > 21), 0.0, raw)
    return np.clip(raw, 0.0, 1.0)


def test_flat_to_multi_profile_preserved() -> None:
    print("\n[1] Flat upload + promote-to-multi-period preserves the profile")
    n = pypsa.Network()
    snaps_1y = pd.date_range("2026-01-01", periods=24 * 7, freq="h")  # 1 week for speed
    n.set_snapshots(snaps_1y)
    n.add("Bus", "B1")
    n.add("Carrier", "solar", co2_emissions=0.0)
    n.add("Generator", "Solar2", bus="B1", carrier="solar",
          p_nom=300.0, p_nom_extendable=False, marginal_cost=0.0)

    PyPSAService.set_network(n)
    # Mimic user upload: store the flat profile in _user_ts.
    profile = pd.Series(_solar_shape(snaps_1y), index=snaps_1y, dtype=float)
    net_router._user_ts[('generators', 'p_max_pu', 'Solar2')] = profile
    net_router._reapply_user_ts_to_network(n)

    # Sanity check before promote.
    assert 'Solar2' in n.generators_t.p_max_pu.columns
    flat_mean = float(n.generators_t.p_max_pu['Solar2'].mean())
    # Solar shape — anywhere in [0, 1], biased toward day hours. Loose bound
    # because the synthetic Gaussian's mean depends on width / horizon.
    _step("profile present on flat snapshots", 0.05 < flat_mean < 0.6,
          f"mean = {flat_mean:.3f}")

    # Promote to multi-period: set 3 periods replicating the same week.
    periods = [2026, 2027, 2028]
    mi = pd.MultiIndex.from_product([periods, snaps_1y], names=['period', 'timestep'])
    mi.name = 'snapshot'
    n.snapshots = mi
    n.investment_periods = periods
    # Re-apply user_ts to the new MultiIndex.
    net_router._reapply_user_ts_to_network(n)

    # Post-fix assertions:
    p_max_pu = n.generators_t.p_max_pu
    n_rows = len(p_max_pu.index)
    _step("p_max_pu index has same length as n.snapshots",
          n_rows == len(n.snapshots),
          f"got {n_rows} rows for {len(n.snapshots)} snapshots")
    _step("p_max_pu index is MultiIndex",
          isinstance(p_max_pu.index, pd.MultiIndex))
    _step("Solar2 column present after promote",
          'Solar2' in p_max_pu.columns)

    if 'Solar2' in p_max_pu.columns:
        # Every period should have the same profile (broadcast).
        for p in periods:
            slc = p_max_pu['Solar2'].xs(p, level='period')
            slc_mean = float(slc.mean())
            _step(f"Solar2 in period {p}: mean ≈ flat profile mean",
                  abs(slc_mean - flat_mean) < 0.02,
                  f"got {slc_mean:.3f}, expected {flat_mean:.3f}")


def test_reapply_no_index_drift_on_double_call() -> None:
    print("\n[2] Re-running _reapply on a clean multi-period network is idempotent")
    n = pypsa.Network()
    snaps = pd.date_range("2026-01-01", periods=48, freq="h")
    periods = [2026, 2027]
    mi = pd.MultiIndex.from_product([periods, snaps], names=['period', 'timestep'])
    mi.name = 'snapshot'
    n.snapshots = mi
    n.investment_periods = periods
    n.add("Bus", "B1")
    n.add("Carrier", "solar", co2_emissions=0.0)
    n.add("Generator", "Solar2", bus="B1", carrier="solar",
          p_nom=300.0, p_nom_extendable=False, marginal_cost=0.0)

    PyPSAService.set_network(n)
    profile = pd.Series(_solar_shape(snaps), index=snaps, dtype=float)
    net_router._user_ts[('generators', 'p_max_pu', 'Solar2')] = profile
    net_router._reapply_user_ts_to_network(n)
    rows_first = len(n.generators_t.p_max_pu.index)
    net_router._reapply_user_ts_to_network(n)
    rows_second = len(n.generators_t.p_max_pu.index)
    _step("row count stable across reapply calls",
          rows_first == rows_second == len(n.snapshots),
          f"first={rows_first} second={rows_second} expected={len(n.snapshots)}")


def main() -> int:
    # Always start fresh — the _user_ts dict is module-level state.
    net_router._user_ts.clear()
    try:
        test_flat_to_multi_profile_preserved()
    except Exception as e:
        _crashed("scenario 1", e)
        import traceback; traceback.print_exc()
    net_router._user_ts.clear()
    try:
        test_reapply_no_index_drift_on_double_call()
    except Exception as e:
        _crashed("scenario 2", e)
        import traceback; traceback.print_exc()
    print()
    print("=" * 60)
    print(f"Total: {PASS + FAIL}  Pass: {PASS}  Fail: {FAIL}")
    print("=" * 60)
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
