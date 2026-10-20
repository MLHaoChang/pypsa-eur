"""Refining the series a component carries now (gatekeeper plan M(a)).

* The current series is on the snapshot clock, converted at its first apply;
  the artifact made from it maps by identity, so from-current then apply
  with no ops writes back the stored series bit for bit — with the project's
  timezone set, with a profile drawn on a stated zone, and on a snapshot
  window.
* An upload (no generator record) reads as measured.
* On a window: the energy check is recorded as not applicable rather than
  passed; shift and smooth are refused; rescale_energy states the window's
  MWh and an annual one is refused; no NaN of the padding reaches the store
  or the network.
* Apply refuses an artifact once the stored series has changed since.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from services.profile_gen.apply import (
    CurrentChangedError,
    CurrentSeriesError,
    assign_profile,
)
from services.profile_gen.current import describe_current, series_from_current
from services.profile_gen.engine import generate_profile
from services.profile_gen.refine import (
    AnnualEnergyOnWindowSourceError,
    WindowSourceOpError,
    refine_series,
)
from services.pypsa_service import PyPSAService
from services.user_timeseries import _user_ts, _user_ts_meta

NOW = "2026-10-20T00:00:00+00:00"
KEY = ("loads", "p_set", "plant")


def _ctx():
    return PyPSAService.get_active_context()


def _net(snapshots=None, *, tz=None):
    n = PyPSAService.get_network()
    idx = (snapshots if snapshots is not None
           else pd.date_range("2025-01-01", "2025-12-31T23:00", freq="h")).copy()
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "site")
    n.add("Load", "plant", bus="site")
    state = _ctx().solver_state.setdefault("solver_config", SimpleNamespace())
    state.commercial = {"timezone": tz}
    return n


def _first_apply(params=None, **kw):
    res = generate_profile(points=["plant"], technology="industrial",
                           params={"peak_mw": 80.0, **(params or {})},
                           year=2025, now=NOW, family="load")
    assign_profile(_ctx(), "plant", res.series["plant"], res.meta,
                   component_type="loads", attribute="p_set", now=NOW, **kw)
    return _user_ts[KEY].copy()


def _round_trip(ops=None):
    series, meta = series_from_current(_ctx(), *KEY)
    if ops:
        refined = refine_series(_ctx(), series, meta, ops,
                                component_type="loads", attribute="p_set")
        series, meta = refined.series, refined.meta
    return assign_profile(_ctx(), "plant", series, meta, component_type="loads",
                          attribute="p_set", now="2026-10-20T01:00:00+00:00")


# ── the clock: identity, never a second conversion ──────────────────────────

@pytest.mark.parametrize("setup", [
    {"tz": "Europe/Berlin", "params": None, "kw": {}},
    # No project timezone: a profile drawn on a stated zone lands on that
    # zone's wall clock at its first apply (the conversion trap).
    {"tz": None, "params": {"timezone": "Europe/Berlin"}, "kw": {}},
    {"tz": None, "params": None, "kw": {"time_basis": "Europe/Berlin"}},
])
def test_from_current_then_apply_writes_back_the_same_series(setup):
    _net(tz=setup["tz"])
    stored = _first_apply(setup["params"], **setup["kw"])
    out = _round_trip()
    np.testing.assert_array_equal(_user_ts[KEY].to_numpy(), stored.to_numpy())
    assert list(_user_ts[KEY].index) == list(stored.index)
    assert out["warnings"] == [] or all(not w.startswith("time_basis") for w in out["warnings"])


def test_a_window_round_trip_is_identity_too():
    _net(pd.date_range("2025-06-02", periods=168, freq="h"), tz="Europe/Berlin")
    stored = _first_apply()
    _round_trip()
    np.testing.assert_array_equal(_user_ts[KEY].to_numpy(), stored.to_numpy())


# ── what the editor shows ───────────────────────────────────────────────────

def test_an_upload_reads_as_measured():
    n = _net()
    _user_ts[KEY] = pd.Series(5.0, index=n.snapshots)       # an upload: no meta
    assert describe_current(_ctx(), *KEY)["provenance"] == "uploaded"
    _series, meta = series_from_current(_ctx(), *KEY)
    assert meta["provenance"] == "measured" and meta["source"] == "current"


def test_no_current_series_is_refused():
    _net()
    assert describe_current(_ctx(), *KEY) == {"exists": False}
    with pytest.raises(CurrentSeriesError):
        series_from_current(_ctx(), *KEY)


# ── a snapshot window ───────────────────────────────────────────────────────

def _window():
    n = _net(pd.date_range("2025-06-02", periods=168, freq="h"))
    _user_ts[KEY] = pd.Series(np.linspace(1.0, 2.0, 168), index=n.snapshots)
    return n


def test_a_window_source_records_its_energy_check_as_not_applicable():
    _window()
    out = _round_trip([{"op": "set_floor", "value": 1.5}])
    meta = _user_ts_meta[KEY]
    assert meta["energy_check"] == "not_applicable (window-derived source)"
    assert meta["profile_annual_basis"] == "window_mean"
    assert not [w for w in out["warnings"] if w.startswith("energy_mismatch")]


def test_shift_and_smooth_are_refused_on_a_window_source():
    _window()
    series, meta = series_from_current(_ctx(), *KEY)
    for op in ({"op": "shift", "hours": 2}, {"op": "smooth", "window_h": 4}):
        with pytest.raises(WindowSourceOpError):
            refine_series(_ctx(), series, meta, [op], component_type="loads",
                          attribute="p_set")


def test_rescale_energy_on_a_window_source_states_the_window():
    _window()
    series, meta = series_from_current(_ctx(), *KEY)
    with pytest.raises(AnnualEnergyOnWindowSourceError):
        refine_series(_ctx(), series, meta, [{"op": "rescale_energy", "mwh": 5000.0}],
                      component_type="loads", attribute="p_set")
    refined = refine_series(_ctx(), series, meta,
                            [{"op": "rescale_energy", "mwh": 336.0, "basis": "window"}],
                            component_type="loads", attribute="p_set")
    assign_profile(_ctx(), "plant", refined.series, refined.meta,
                   component_type="loads", attribute="p_set", now=NOW)
    assert float(_user_ts[KEY].sum()) == pytest.approx(336.0)


def test_no_nan_of_the_padding_reaches_the_store_or_the_network():
    n = _window()
    _round_trip([{"op": "scale_window", "hours": [8, 18], "factor": 1.2}])
    assert np.isfinite(_user_ts[KEY].to_numpy()).all()
    assert np.isfinite(n.loads_t.p_set["plant"].to_numpy()).all()
    assert len(_user_ts[KEY]) == 168


# ── the stale guard ─────────────────────────────────────────────────────────

def test_apply_refuses_once_the_current_series_changed():
    n = _net()
    _user_ts[KEY] = pd.Series(5.0, index=n.snapshots)
    series, meta = series_from_current(_ctx(), *KEY)
    _user_ts[KEY] = pd.Series(6.0, index=n.snapshots)       # a new upload since
    with pytest.raises(CurrentChangedError):
        assign_profile(_ctx(), "plant", series, meta, component_type="loads",
                       attribute="p_set", now=NOW)
