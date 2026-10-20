"""`assign_profile` beyond generator p_max_pu (time-series generators, ticket 01 slice A).

The one write path now takes a ``(component_type, attribute)`` target from a
table (`apply.TARGETS`) and a ``family`` from the profile meta. The table owns
the bounds of each target; the family owns which targets it may write and any
guard of its own (the renewable wind/solar carrier rule). Apply looks the
family up and refuses a target it does not allow, so a hand-made or old meta
cannot bypass the family rule.

Gatekeeper conditions pinned here (review 2026-10-20):

  * the store key is ``(component_type, attribute, component)`` for both the
    series and the meta, so one generator carries p_max_pu and p_min_pu side
    by side, and undoing one leaves the other;
  * p_min_pu above p_max_pu at any snapshot is refused, naming the snapshot;
  * a shape family's realised energy is recorded against the profile's
    annual energy, warned on above 2 %;
  * a marginal_cost series must be in currency/MWh and records its money basis;
  * a negative p_set is refused, never clipped.

Alignment (month-day-hour, leap rules, tiling, time basis) is the existing
code and is covered by tests/test_profile_gen_apply.py; here it only has to
reach the new targets.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.network_undo import apply_undo, push_undo_snapshot
from services.profile_gen.apply import (
    CarrierMismatchError,
    ComponentNotFoundError,
    ENERGY_MISMATCH_WARNING,
    InvalidSeriesUnitError,
    LeapYearMismatchError,
    ProfileApplyError,
    ProfileBoundConflictError,
    UnknownFamilyError,
    UnsupportedTargetError,
    assign_profile,
)
from services.profile_gen.engine import generate_profile
from services.pypsa_service import PyPSAService
from services.snapshot_index import _build_period_multiindex
from services.user_timeseries import _user_ts, _user_ts_meta

NOW = "2026-10-20T00:00:00+00:00"


def _year(year: int) -> pd.DatetimeIndex:
    return pd.date_range(f"{year}-01-01T00:00", f"{year}-12-31T23:00", freq="h")


def _shape(year: int = 2023, *, norm: str = "peak") -> pd.Series:
    """A load-like hourly shape over one year: a daily cycle on a base."""
    idx = _year(year)
    v = 0.6 + 0.4 * np.sin(np.pi * idx.hour.to_numpy() / 24.0)
    s = pd.Series(v, index=idx, dtype=float)
    return s / (s.max() if norm == "peak" else s.mean())


def _load_meta(**over) -> dict:
    meta = {
        "family": "load",
        "source": "archetype",
        "archetype": "test",
        "unit": "per_unit_peak",
        "scale_mw": 50.0,
        "resolution": "hourly",
        "generated_at": NOW,
    }
    meta.update(over)
    return meta


def _ctx():
    return PyPSAService.get_active_context()


def _net(year: int = 2023):
    n = PyPSAService.get_network()
    idx = _year(year)
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1", x=13.4, y=52.5)
    n.add("Carrier", "gas")
    n.add("Load", "L1", bus="B1")
    n.add("Generator", "CHP", bus="B1", carrier="gas", p_nom=100.0)
    return n


# ── loads / p_set ───────────────────────────────────────────────────────────

def test_a_peak_normalised_shape_lands_on_p_set_at_the_stated_peak():
    n = _net()
    out = assign_profile(_ctx(), "L1", _shape(), _load_meta(),
                         component_type="loads", attribute="p_set", now=NOW)
    key = ("loads", "p_set", "L1")
    assert key in _user_ts and key in _user_ts_meta
    assert out["attribute"] == "p_set"
    assert out["peak"] == pytest.approx(50.0)
    assert float(n.loads_t.p_set["L1"].max()) == pytest.approx(50.0)
    meta = _user_ts_meta[key]
    assert meta["family"] == "load"
    assert meta["unit"] == "per_unit_peak"
    assert meta["scale_mw"] == 50.0


def test_a_mean_normalised_shape_lands_at_the_stated_mean():
    _net()
    out = assign_profile(_ctx(), "L1", _shape(norm="mean"),
                         _load_meta(unit="per_unit_mean", scale_mw=20.0),
                         component_type="loads", attribute="p_set", now=NOW)
    assert out["mean"] == pytest.approx(20.0)
    meta = _user_ts_meta[("loads", "p_set", "L1")]
    # The mean is the source year's, and the record says so.
    assert meta["per_unit_mean_of"] == "source_year"
    assert meta["profile_annual_mwh"] == pytest.approx(20.0 * 8760)
    assert meta["modelled_mwh"] == {"total": pytest.approx(20.0 * 8760)}


def test_a_shape_whose_normalisation_disagrees_with_its_unit_is_refused():
    _net()
    with pytest.raises(InvalidSeriesUnitError, match="per_unit_peak"):
        assign_profile(_ctx(), "L1", _shape(norm="mean"), _load_meta(),
                       component_type="loads", attribute="p_set", now=NOW)
    assert ("loads", "p_set", "L1") not in _user_ts


def test_an_absolute_mw_series_is_written_unscaled():
    _net()
    s = _shape() * 7.0
    out = assign_profile(_ctx(), "L1", s, _load_meta(unit="MW", scale_mw=None),
                         component_type="loads", attribute="p_set", now=NOW)
    assert out["peak"] == pytest.approx(7.0)


def test_a_leap_source_dropping_feb29_keeps_the_stated_peak():
    _net(2023)
    out = assign_profile(_ctx(), "L1", _shape(2024), _load_meta(),
                         component_type="loads", attribute="p_set", now=NOW)
    assert out["leap_day"] == "dropped"
    assert out["peak"] == pytest.approx(50.0)


def test_a_negative_load_is_refused_and_nothing_is_written():
    n = _net()
    s = _shape() * 5.0 - 4.0  # min 0.6 * 5 - 4 < 0
    with pytest.raises(ProfileApplyError, match="negative"):
        assign_profile(_ctx(), "L1", s, _load_meta(unit="MW", scale_mw=None),
                       component_type="loads", attribute="p_set", now=NOW)
    assert ("loads", "p_set", "L1") not in _user_ts
    assert "L1" not in n.loads_t.p_set.columns


def test_a_load_profile_on_a_missing_load_is_component_not_found():
    _net()
    with pytest.raises(ComponentNotFoundError, match="load 'Nope'"):
        assign_profile(_ctx(), "Nope", _shape(), _load_meta(),
                       component_type="loads", attribute="p_set", now=NOW)


def test_a_load_profile_tiles_multi_period_snapshots():
    n = PyPSAService.get_network()
    mi = _build_period_multiindex([2030, 2032], [_year(2030), _year(2032)])
    n.set_snapshots(mi)
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1")
    with pytest.raises(LeapYearMismatchError):
        # A non-leap source onto the leap period is the existing refusal.
        assign_profile(_ctx(), "L1", _shape(2023), _load_meta(),
                       component_type="loads", attribute="p_set", now=NOW)
    out = assign_profile(_ctx(), "L1", _shape(2024), _load_meta(),
                         component_type="loads", attribute="p_set", now=NOW)
    assert out["tiled"] is True
    assert len(n.loads_t.p_set["L1"]) == 8760 + 8784
    modelled = _user_ts_meta[("loads", "p_set", "L1")]["modelled_mwh"]
    assert set(modelled) == {"2030", "2032"}


def test_a_modelled_energy_off_by_more_than_two_percent_is_warned():
    # Weightings that do not sum to the year (as a template's sample does)
    # make the modelled energy differ from the profile's annual energy.
    n = _net()
    n.snapshot_weightings.loc[:, "generators"] = 0.5
    out = assign_profile(_ctx(), "L1", _shape(), _load_meta(),
                         component_type="loads", attribute="p_set", now=NOW)
    assert any(w.startswith(ENERGY_MISMATCH_WARNING) for w in out["warnings"])
    meta = _user_ts_meta[("loads", "p_set", "L1")]
    assert meta["modelled_mwh"]["total"] == pytest.approx(
        0.5 * meta["profile_annual_mwh"])


def test_full_year_unit_weightings_carry_no_energy_warning():
    _net()
    out = assign_profile(_ctx(), "L1", _shape(), _load_meta(),
                         component_type="loads", attribute="p_set", now=NOW)
    assert not any(w.startswith(ENERGY_MISMATCH_WARNING) for w in out["warnings"])


# ── generators: p_min_pu beside p_max_pu ────────────────────────────────────

def _avail_meta(**over) -> dict:
    meta = {"family": "availability", "source": "rule", "resolution": "hourly",
            "generated_at": NOW}
    meta.update(over)
    return meta


def test_one_generator_carries_p_max_pu_and_p_min_pu_side_by_side():
    n = _net()
    idx = _year(2023)
    assign_profile(_ctx(), "CHP", pd.Series(0.9, index=idx), _avail_meta(),
                   attribute="p_max_pu", now=NOW)
    assign_profile(_ctx(), "CHP", pd.Series(0.3, index=idx), _avail_meta(),
                   attribute="p_min_pu", now=NOW)
    assert ("generators", "p_max_pu", "CHP") in _user_ts_meta
    assert ("generators", "p_min_pu", "CHP") in _user_ts_meta
    assert float(n.generators_t.p_max_pu["CHP"].iloc[0]) == pytest.approx(0.9)
    assert float(n.generators_t.p_min_pu["CHP"].iloc[0]) == pytest.approx(0.3)


def test_undoing_p_min_pu_leaves_p_max_pu():
    _net()
    idx = _year(2023)
    assign_profile(_ctx(), "CHP", pd.Series(0.9, index=idx), _avail_meta(),
                   attribute="p_max_pu", now=NOW)
    push_undo_snapshot()
    assign_profile(_ctx(), "CHP", pd.Series(0.3, index=idx), _avail_meta(),
                   attribute="p_min_pu", now=NOW)
    apply_undo()
    n = PyPSAService.get_network()
    assert ("generators", "p_min_pu", "CHP") not in _user_ts_meta
    assert ("generators", "p_max_pu", "CHP") in _user_ts_meta
    assert float(n.generators_t.p_max_pu["CHP"].iloc[0]) == pytest.approx(0.9)


def test_p_min_pu_above_p_max_pu_is_refused_naming_the_snapshot():
    n = _net()
    idx = _year(2023)
    cap = pd.Series(1.0, index=idx)
    cap.iloc[100:110] = 0.2  # a maintenance dip
    assign_profile(_ctx(), "CHP", cap, _avail_meta(),
                   attribute="p_max_pu", now=NOW)
    with pytest.raises(ProfileBoundConflictError, match=str(idx[100])):
        assign_profile(_ctx(), "CHP", pd.Series(0.5, index=idx), _avail_meta(),
                       attribute="p_min_pu", now=NOW)
    assert ("generators", "p_min_pu", "CHP") not in _user_ts
    assert "CHP" not in n.generators_t.p_min_pu.columns


def test_p_min_pu_above_a_static_p_max_pu_is_refused():
    n = _net()
    n.generators.loc["CHP", "p_max_pu"] = 0.4
    with pytest.raises(ProfileBoundConflictError):
        assign_profile(_ctx(), "CHP", pd.Series(0.5, index=_year(2023)),
                       _avail_meta(), attribute="p_min_pu", now=NOW)


def test_a_p_max_pu_below_the_existing_p_min_pu_is_refused():
    n = _net()
    n.generators.loc["CHP", "p_min_pu"] = 0.5
    with pytest.raises(ProfileBoundConflictError):
        assign_profile(_ctx(), "CHP", pd.Series(0.3, index=_year(2023)),
                       _avail_meta(), attribute="p_max_pu", now=NOW)


def test_p_min_pu_out_of_range_is_clipped_and_recorded():
    _net()
    s = pd.Series(0.2, index=_year(2023))
    s.iloc[0] = 1.3
    s.iloc[1] = -0.1
    out = assign_profile(_ctx(), "CHP", s, _avail_meta(),
                         attribute="p_min_pu", now=NOW)
    assert out["peak"] == pytest.approx(1.0)
    assert _user_ts_meta[("generators", "p_min_pu", "CHP")]["clipped"] is True


# ── marginal_cost ───────────────────────────────────────────────────────────

def _price_meta(**over) -> dict:
    meta = {"family": "series", "source": "user", "unit": "EUR/MWh",
            "price_basis": "real", "currency_year": 2020,
            "resolution": "hourly", "generated_at": NOW}
    meta.update(over)
    return meta


def test_a_price_series_of_any_sign_lands_on_marginal_cost_with_its_money_basis():
    n = _net()
    s = pd.Series(np.linspace(-20.0, 120.0, 8760), index=_year(2023))
    assign_profile(_ctx(), "CHP", s, _price_meta(),
                   attribute="marginal_cost", now=NOW)
    assert float(n.generators_t.marginal_cost["CHP"].min()) == pytest.approx(-20.0)
    meta = _user_ts_meta[("generators", "marginal_cost", "CHP")]
    assert meta["money"] == "real basis, 2020 EUR"


def test_a_price_series_not_in_currency_per_mwh_is_refused():
    _net()
    with pytest.raises(InvalidSeriesUnitError, match="currency/MWh"):
        assign_profile(_ctx(), "CHP", pd.Series(40.0, index=_year(2023)),
                       _price_meta(unit="EUR/kWh"),
                       attribute="marginal_cost", now=NOW)


# ── the table and the family backstop ───────────────────────────────────────

def test_an_unknown_target_is_refused():
    _net()
    with pytest.raises(UnsupportedTargetError, match="loads/q_set"):
        assign_profile(_ctx(), "L1", _shape(), _load_meta(),
                       component_type="loads", attribute="q_set", now=NOW)


def test_an_unknown_family_is_refused():
    _net()
    with pytest.raises(UnknownFamilyError, match="mystery"):
        assign_profile(_ctx(), "L1", _shape(), _load_meta(family="mystery"),
                       component_type="loads", attribute="p_set", now=NOW)


def test_a_renewable_profile_cannot_target_a_load():
    _net()
    result = generate_profile(points=[("site", 13.4, 52.5)], technology="solar",
                              source="synthetic", year=2023, now=NOW)
    with pytest.raises(CarrierMismatchError, match="loads/p_set"):
        assign_profile(_ctx(), "L1", result.series["site"], result.meta,
                       component_type="loads", attribute="p_set", now=NOW)


def test_a_meta_without_family_is_renewable_and_keeps_the_carrier_guard():
    # Old artifacts and pending jobs carry no `family`.
    _net()
    meta = {"technology": "solar", "source": "synthetic", "resolution": "hourly"}
    with pytest.raises(CarrierMismatchError):
        assign_profile(_ctx(), "CHP", pd.Series(0.5, index=_year(2023)), meta,
                       now=NOW)


def test_a_load_family_cannot_write_a_generator_bound():
    _net()
    with pytest.raises(CarrierMismatchError, match="generators/p_max_pu"):
        assign_profile(_ctx(), "CHP", _shape(), _load_meta(), now=NOW)
