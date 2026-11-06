"""The campus study's dip and restart screens, ``campus_dip_trips.csv`` and ``campus_restart.csv``, and the process
chain they read rate and mass pull from (grid-to-process issue 12b-2)."""
import math

import pandas as pd
import pypsa
import pytest

from gridspine.drivers import process_chain as pc
from gridspine.drivers.campus_study import DIP_TRIPS_CSV, RESTART_CSV, restart_campus
from gridspine.schema.contracts import ContractError
from gridspine.static import restart_sequence as rs
from tests.gridspine.test_harmonics_campus import _hub, _run


def _restart_hub(curve=("20:0.5", None), restart=(4.0, 2.0)):
    """The harmonic hub with tonnes on nh3 and a shortfall Generator on it: compressor and pump each make
    5 MW × 2 t/MWh = 10 t/h of the shortfall product (mass pull 1)."""
    n = _hub()
    n.buses.loc["nh3", "unit"] = "t"
    n.links.loc[["compressor", "pump"], "efficiency"] = 2.0
    n.add("Generator", "__lost_nh3", bus="nh3", p_nom=100.0)
    n.generators["process_role"] = ""
    n.generators.loc["__lost_nh3", "process_role"] = "shortfall"
    n.links["ride_through_curve"] = ["", *(c or "" for c in curve)]
    n.links["restart_time_h"] = [math.nan, *restart]
    n.links["criticality_tier"] = ["", "process_critical", "safety"]
    n.links["motor_kw"] = [math.nan, 2000.0, 500.0]
    n.links["start_method"] = ["", "vfd", "dol"]
    return n


def test_both_files_hold_their_levels_and_the_sum(tmp_path):
    run, _ = _run(tmp_path, _restart_hub())
    dip, restart = restart_campus(run)
    on_disk = pd.read_csv(run / DIP_TRIPS_CSV)
    assert set(on_disk["level"]) == {"class", "unit", "total"}
    assert set(pd.read_csv(run / RESTART_CSV)["level"]) == {"unit", "event", "total"}
    units = dip[dip["level"] == "unit"].set_index("unit")
    # The pump has no curve: unknown, never "rides through"; the total is incomplete.
    assert units.at["pump", "verdict"] == "unknown" and units.at["compressor", "verdict"] == "known"
    assert (dip["dip_stats_source"].dropna() == "demo").all()
    total = dip[dip["level"] == "total"].iloc[0]
    assert bool(total["incomplete"]) is True
    assert units.at["compressor", "rate_basis"].startswith("p_nom 5 MW × efficiency 2 t/MWh")

    trip = restart[(restart["transition"] == "trip") & (restart["level"] == "unit")].set_index("unit")
    # Both restart after a grid dip: the compressor trips the worst class, the pump is assumed to trip.
    assert list(trip.index) == ["pump", "compressor"]               # safety first
    assert "assumed to trip" in trip.at["pump", "note"]
    expected = 4.0 * 10.0 * 1.0 + 2.0 * 10.0 * 1.0                   # Σ restart time × rate × mass pull
    tot = restart[(restart["transition"] == "trip") & (restart["level"] == "total")].iloc[0]
    assert tot["lost_restart_t"] == pytest.approx(expected, abs=1e-12)
    assert trip["lost_restart_t"].sum() == pytest.approx(expected, abs=1e-12)


def test_a_unit_that_rides_every_class_does_not_restart_after_a_dip(tmp_path):
    run, _ = _run(tmp_path, _restart_hub(curve=("100:60", "100:60")))
    _, restart = restart_campus(run)
    assert restart[(restart["transition"] == "trip") & (restart["level"] == "unit")].empty
    island = restart[(restart["transition"] == "island") & (restart["level"] == "unit")]
    assert set(island["unit"]) == {"compressor", "pump"}             # an island transition trips every unit


def test_an_island_without_gensets_screens_unknown_never_on_the_grid(tmp_path):
    run, _ = _run(tmp_path, _restart_hub())
    _, restart = restart_campus(run)
    island = restart[(restart["transition"] == "island") & (restart["level"] == "unit")]
    assert (island["start_verdict"] == "unknown").all() and (island["sk_mva_source"] == "none").all()
    tot = restart[(restart["transition"] == "island") & (restart["level"] == "total")].iloc[0]
    assert bool(tot["lower_bound"]) is True


def test_a_changed_project_is_refused(tmp_path):
    run, path = _run(tmp_path, _restart_hub())
    n = pypsa.Network(str(path))
    n.links.loc["pump", "restart_time_h"] = 9.0
    n.export_to_netcdf(str(path))
    with pytest.raises(ContractError, match="changed since"):
        restart_campus(run)


# --- the process chain: rate and mass pull -----------------------------------

def _chain():
    """mill (bus1 slurry, t) → two parallel flotations → conc (shortfall). Each flotation draws slurry on bus2."""
    n = pypsa.Network()
    for b, unit in (("el", "MW"), ("ore", "t"), ("slurry", "t"), ("conc", "t")):
        n.add("Bus", b, unit=unit)
    n.add("Link", "mill", bus0="el", bus1="slurry", bus2="ore", efficiency=62.5, efficiency2=-62.5, p_nom=8.8)
    for name in ("flot_a", "flot_b"):
        n.add("Link", name, bus0="el", bus1="conc", bus2="slurry", efficiency=9.4, efficiency2=-500.0, p_nom=2.0)
    n.add("Generator", "__lost_conc", bus="conc", p_nom=10.0)
    n.generators["process_role"] = ["shortfall"]
    n.links["eh_process"] = True
    return n


def test_rate_and_mass_pull_on_a_parallel_chain():
    n = _chain()
    rate, basis = pc.rate_tph(n, "mill")
    assert rate == pytest.approx(550.0) and "p_nom 8.8 MW" in basis
    mp, basis = pc.mass_pull(n, "mill")
    assert mp == pytest.approx(9.4 / 500.0) and "flot_a / flot_b" in basis


def test_rate_through_mwh_per_t_states_its_basis():
    n = _chain()
    n.buses.loc["slurry", ["unit"]] = "MWh"
    n.buses["mwh_per_t"] = math.nan
    n.buses.loc["slurry", "mwh_per_t"] = 0.5
    rate, basis = pc.rate_tph(n, "mill")
    assert rate == pytest.approx(8.8 * 62.5 / 0.5) and "mwh_per_t 0.5" in basis


def test_a_throughput_unit_that_disagrees_with_the_bus_reads_unknown():
    # #1057-style: two statements of the same thing disagree, and the screen never picks one.
    n = _chain()
    n.links["throughput_unit"] = "MWh/h"
    rate, basis = pc.rate_tph(n, "mill")
    assert rate is None and "disagrees" in basis
    n.links["throughput_unit"] = "t/h"
    assert pc.rate_tph(n, "mill")[0] == pytest.approx(550.0)


def _other_factor(n):
    n.links.loc["flot_b", "efficiency"] = 9.0


def _other_bus(n):
    n.links.loc["flot_b", "bus1"] = "ore"


def _no_shortfall(n):
    n.remove("Generator", "__lost_conc")


def _no_product(n):
    n.links.loc[["flot_a", "flot_b"], "bus1"] = "el"


@pytest.mark.parametrize("edit, why", [(_other_factor, "branches"), (_other_bus, "branches"),
                                       (_no_shortfall, "no Generator"), (_no_product, "product outputs")])
def test_branching_and_dead_ends_read_unknown(edit, why):
    n = _chain()
    edit(n)
    mp, basis = pc.mass_pull(n, "mill")
    assert mp is None and why in basis


def test_a_cycle_reads_unknown():
    n = _chain()
    n.remove("Generator", "__lost_conc")
    n.remove("Link", ["flot_b"])
    n.add("Generator", "__lost_x", bus="ore", p_nom=1.0)
    n.generators["process_role"] = ["shortfall"]
    n.generators.loc["__lost_x", "bus"] = "nowhere"
    n.add("Link", "back", bus0="el", bus1="slurry", bus2="conc", efficiency=1.0, efficiency2=-1.0, p_nom=1.0)
    mp, basis = pc.mass_pull(n, "mill")
    assert mp is None and "cycle" in basis


def test_a_time_varying_efficiency_reads_unknown():
    n = _chain()
    n.set_snapshots(range(2))
    n.links_t.efficiency = pd.DataFrame({"flot_a": [9.4, 9.0]}, index=n.snapshots)
    assert "varies in time" in pc.mass_pull(n, "mill")[1]
    n.links_t.efficiency = pd.DataFrame({"mill": [62.5, 60.0]}, index=n.snapshots)
    assert "varies in time" in pc.rate_tph(n, "mill")[1]


def test_restart_units_keeps_a_unit_with_inputs_missing():
    units = pc.restart_units(_chain())
    assert list(units.columns) == list(pc.UNIT_COLUMNS) and len(units) == 3
    assert units.set_index("unit").at["mill", "ride_through_curve"] is None
    # The flotations' own product is the shortfall bus: mass pull 1.
    assert units.set_index("unit").at["flot_a", "mass_pull"] == pytest.approx(1.0)
    assert rs.restart_sequence(units, "trip")[2]["lower_bound"] is True
