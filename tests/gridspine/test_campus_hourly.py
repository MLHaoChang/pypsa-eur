"""The campus's hourly results, per investment period (plan C2).

``campus_hourly(n, campus)`` reads a solved hub network and the campus
description, and returns:

* ``hourly``: one row per (unit, period, hour). ``p_mw`` is the power
  injected at the unit's bus, so a load or a charging battery is negative.
  ``status`` is 0 for an asset not yet built (or already retired) in that
  period, and its power is then 0.
* ``pcc``: one row per (period, hour). It carries the snapshot weight and
  the model's own import at the PCC (``import_mw``, negative when
  exporting), read from the Links that touch the PCC bus.

Units are found by their ``pypsa_name``. The network is built here and
"solved" by writing its time series directly, so no solver runs.
"""
import numpy as np
import pandas as pd
import pypsa
import pytest

from gridspine.producers.campus import campus_hourly, draft_campus
from gridspine.schema.campus import validate_hourly
from gridspine.schema.contracts import ContractError

H = 6


def solved_hub(periods=None):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-07-01", periods=H, freq="h"))
    if periods:
        n.set_investment_periods(periods)
    n.add("Bus", "grid", v_nom=110.0)
    n.add("Bus", "mv", v_nom=20.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=500.0)
    n.add("Link", "grid_import", bus0="grid", bus1="mv", p_nom=60.0, efficiency=0.99)
    n.add("Load", "dc_load", bus="mv", p_set=40.0)
    n.add("Generator", "pv", bus="mv", carrier="solar", p_nom=20.0)
    n.add("StorageUnit", "bess", bus="mv", carrier="battery", p_nom=10.0, max_hours=2.0)
    if periods:
        # the battery is built in the second period
        n.storage_units.loc["bess", "build_year"] = periods[1]
        n.storage_units.loc["bess", "lifetime"] = 30.0
    n.buses["eh_poc"] = [True, False]
    n.buses["eh_sk_mva"] = [2000.0, float("nan")]
    k = np.arange(len(n.snapshots))
    pv = np.clip(np.sin(np.pi * (k % H) / H), 0, None) * 20.0
    bess = np.where(k % 2 == 0, -5.0, 5.0)                        # charge, discharge, ...
    load = np.full(len(n.snapshots), 40.0)
    n.generators_t.p = pd.DataFrame({"pv": pv, "grid_supply": load - pv - bess}, index=n.snapshots)
    n.storage_units_t.p = pd.DataFrame({"bess": bess}, index=n.snapshots)
    n.loads_t.p = pd.DataFrame({"dc_load": load}, index=n.snapshots)
    n.links_t.p0 = pd.DataFrame({"grid_import": (load - pv - bess) / 0.99}, index=n.snapshots)
    n.links_t.p1 = -0.99 * n.links_t.p0
    n.snapshot_weightings.loc[:, :] = 8760.0 / H
    return n


def _unit(hourly, campus, pypsa_name):
    units = campus["campus"]["units"]
    uid = next(k for k, v in units.items() if v["pypsa_name"] == pypsa_name)
    return hourly[hourly["unit_id"] == uid].sort_values(["period", "hour"])


def test_a_single_period_project_gives_one_period_of_signed_injections():
    n = solved_hub()
    campus = draft_campus(n).spec
    hourly, pcc = campus_hourly(n, campus)
    validate_hourly(hourly)
    assert sorted(hourly["period"].unique()) == [2030]
    assert sorted(hourly["hour"].unique()) == list(range(H))
    load = _unit(hourly, campus, "dc_load")
    assert (load["p_mw"] == -40.0).all()                          # a load draws: negative
    pv = _unit(hourly, campus, "pv")
    assert list(pv["p_mw"]) == pytest.approx(list(n.generators_t.p["pv"]))
    bess = _unit(hourly, campus, "bess")
    assert list(bess["p_mw"]) == pytest.approx(list(n.storage_units_t.p["bess"]))
    assert (bess["p_mw"] < 0).any() and (bess["p_mw"] > 0).any()  # charging is negative


def test_the_pcc_table_carries_the_models_import_and_the_hour_weight():
    n = solved_hub()
    _, pcc = campus_hourly(n, draft_campus(n).spec)
    assert list(pcc["import_mw"]) == pytest.approx(list(n.links_t.p0["grid_import"]))
    assert (pcc["weight"] == 8760.0 / H).all()


def test_an_export_link_into_the_pcc_counts_as_negative_import():
    n = solved_hub()
    n.add("Link", "export", bus0="mv", bus1="grid", p_nom=30.0)
    n.links_t.p0["export"] = 3.0
    n.links_t.p1["export"] = -3.0                                  # 3 MW delivered into the PCC
    _, pcc = campus_hourly(n, draft_campus(n).spec)
    expected = n.links_t.p0["grid_import"] - 3.0
    assert list(pcc["import_mw"]) == pytest.approx(list(expected))


def test_each_investment_period_is_its_own_year_and_an_unbuilt_asset_is_off():
    n = solved_hub(periods=[2030, 2040])
    campus = draft_campus(n).spec
    hourly, pcc = campus_hourly(n, campus)
    assert sorted(hourly["period"].unique()) == [2030, 2040]
    assert len(pcc) == 2 * H
    for period in (2030, 2040):                                    # hours restart each period
        assert sorted(hourly.loc[hourly["period"] == period, "hour"].unique()) == list(range(H))
        assert sorted(pcc.loc[pcc["period"] == period, "hour"]) == list(range(H))
    bess = _unit(hourly, campus, "bess")
    first, second = bess[bess["period"] == 2030], bess[bess["period"] == 2040]
    assert (first["status"] == 0).all() and (first["p_mw"] == 0).all()
    assert (second["status"] == 1).all()
    assert list(second["p_mw"]) == pytest.approx(list(n.storage_units_t.p["bess"].loc[2040]))


def test_an_electrolyser_link_draws_its_input_power_as_a_load():
    n = solved_hub()
    n.add("Bus", "h2", carrier="H2")
    n.add("Link", "electrolyser", bus0="mv", bus1="h2", p_nom=5.0, efficiency=0.7)
    n.links_t.p0["electrolyser"] = 4.0
    n.links_t.p1["electrolyser"] = -2.8
    campus = draft_campus(n).spec
    hourly, _ = campus_hourly(n, campus)
    assert (_unit(hourly, campus, "electrolyser")["p_mw"] == -4.0).all()


def test_an_unsolved_project_is_refused():
    n = solved_hub()
    campus = draft_campus(n).spec
    for ts, attr in (("generators_t", "p"), ("storage_units_t", "p"), ("loads_t", "p"), ("links_t", "p0")):
        setattr(getattr(n, ts), attr, pd.DataFrame(index=n.snapshots))
    with pytest.raises(ContractError, match="solved"):
        campus_hourly(n, campus)


def test_an_asset_that_never_left_zero_has_no_saved_series_and_reads_as_zero(tmp_path):
    """A saved network drops all-default time series: an idle battery comes
    back from NetCDF with no column, and must read as 0 MW, not as unsolved."""
    n = solved_hub()
    n.storage_units_t.p["bess"] = 0.0
    path = tmp_path / "n.nc"
    n.export_to_netcdf(str(path))
    m = pypsa.Network(str(path))
    assert "bess" not in m.storage_units_t.p.columns
    campus = draft_campus(m).spec
    hourly, _ = campus_hourly(m, campus)
    assert (_unit(hourly, campus, "bess")["p_mw"] == 0.0).all()


def test_a_unit_with_no_project_name_is_refused():
    n = solved_hub()
    campus = draft_campus(n).spec
    uid = next(iter(campus["campus"]["units"]))
    campus["campus"]["units"][uid].pop("pypsa_name")
    with pytest.raises(ContractError, match=f"{uid} has no pypsa_name"):
        campus_hourly(n, campus)


def test_a_unit_whose_project_name_is_not_in_the_project_is_refused():
    n = solved_hub()
    campus = draft_campus(n).spec
    uid = next(iter(campus["campus"]["units"]))
    campus["campus"]["units"][uid]["pypsa_name"] = "ghost"
    with pytest.raises(ContractError, match="ghost"):
        campus_hourly(n, campus)


def _bad_status(h):
    h.loc[h.index[0], "status"] = 2
    return h


@pytest.mark.parametrize("mutate, match", [
    (_bad_status, "status"),
    (lambda h: pd.concat([h, h.iloc[[0]]]), "duplicate"),
    (lambda h: h.drop(columns=["period"]), "period"),
    (lambda h: h.assign(p_mw=float("nan")), "finite"),
])
def test_the_hourly_table_contract(mutate, match):
    n = solved_hub()
    hourly, _ = campus_hourly(n, draft_campus(n).spec)
    with pytest.raises(ContractError, match=match):
        validate_hourly(mutate(hourly.copy()))


def test_an_off_unit_with_power_breaks_the_contract():
    n = solved_hub()
    hourly, _ = campus_hourly(n, draft_campus(n).spec)
    bad = hourly.copy()
    bad.loc[bad.index[0], ["status", "p_mw"]] = [0, 5.0]
    with pytest.raises(ContractError, match="status 0"):
        validate_hourly(bad)
