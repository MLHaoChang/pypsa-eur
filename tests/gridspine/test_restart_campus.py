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


def test_a_product_output_with_no_tonne_factor_reads_unknown_not_dropped():
    n = _chain()
    n.add("Bus", "tails", unit="MWh")
    n.links.loc[["flot_a", "flot_b"], "bus3"] = "tails"
    n.links.loc[["flot_a", "flot_b"], "efficiency3"] = 1.0
    mp, basis = pc.mass_pull(n, "mill")
    assert mp is None and "2 product outputs" in basis


# --- the island source (GK A on 12b-2: c_min, N+k, never a partial sum) ------

def _island():
    from gridspine.ingest.campus import build_campus
    from tests.gridspine.test_campus_island import island_campus

    return build_campus(island_campus())          # GEN1, GEN2: 12.5 MVA, xd_pp 0.15, on 20 kV; N+1


def test_the_island_source_is_the_fleet_less_its_k_largest_at_c_min():
    from gridspine.drivers.campus_study import _island_source
    from gridspine.static import motor_start as ms

    src, note, blocks = _island_source(_island())
    assert src.sk_min_mva == pytest.approx(12.5 / 0.15) and src.vn_kv == 20.0 and src.sk_mva is None
    assert "1 of 2 gensets (N+1)" in note and blocks == [25.0, 25.0]
    s_sc, _, case, c, _ = ms.short_circuit_power(src)
    assert case == "min" and c == 1.0 and s_sc == pytest.approx(12.5 / 0.15)


def test_n_minus_k_changes_the_island_verdict():
    from gridspine.drivers.campus_study import _island_source
    from gridspine.static import motor_start as ms

    campus = _island()
    # Unequal gensets: GEN2 at 25 MVA (166.7 MVA S_k''), GEN1 at 12.5 (83.3). N+1 drops the LARGEST, keeping 83.3.
    campus.units.loc["GEN2", "s_mva"] = 25.0
    unit = pd.DataFrame([{"unit": "mill", "tier": "process_critical", "rated_mw": 1.0, "restart_time_h": 1.0,
                          "rate_tph": 10.0, "mass_pull": 1.0, "motor_kw": 1200.0, "start_method": "soft"}])
    n_plus_1, _, _ = _island_source(campus)
    campus.island["requirements"]["values"]["genset_redundancy_n"] = 0
    whole, _, _ = _island_source(campus)
    assert n_plus_1.sk_min_mva == pytest.approx(12.5 / 0.15) and whole.sk_min_mva == pytest.approx(37.5 / 0.15)
    # S_start 3 × 1.2 / 0.85 MVA at pf 0.35: ΔU 4.76 % on 83.3 MVA (fail), 1.59 % on the whole 250 (pass).
    # Keeping the larger genset instead (166.7 MVA) would read 2.38 %, a pass: the verdict pins which one is dropped.
    verdict = {k: rs.restart_sequence(unit, "island", island_source=src, limit_pct=3.0)[0].iloc[0]
               for k, src in (("n+1", n_plus_1), ("whole", whole))}
    assert verdict["n+1"]["start_verdict"] == "fail" and verdict["whole"]["start_verdict"] == "pass"
    q = 3 * 1.2 / 0.85 * (1 - ms.START_PF["soft"] ** 2) ** 0.5
    assert verdict["n+1"]["du_pct"] == pytest.approx(q / (12.5 / 0.15) * 100)


def test_a_genset_missing_xd_pp_makes_the_island_source_unknown_never_partial():
    from gridspine.drivers.campus_study import _island_source

    campus = _island()
    p = campus.params
    campus.params = p[~((p["element"] == "GEN2") & (p["param"] == "xd_pp"))]
    src, note, _ = _island_source(campus)
    assert src is None and "GEN2" in note


def test_assumed_to_trip_shows_on_the_unit_row_and_the_timeline(tmp_path):
    run, _ = _run(tmp_path, _restart_hub())
    _, restart = restart_campus(run)
    trip = restart[restart["transition"] == "trip"]
    unit = trip[(trip["level"] == "unit") & (trip["unit"] == "pump")].iloc[0]
    event = trip[(trip["level"] == "event") & (trip["unit"] == "pump") & (trip["event"] == "trip")].iloc[0]
    assert "assumed to trip" in unit["note"] and "assumed: no ride-through curve" in event["note"]
    other = trip[(trip["level"] == "event") & (trip["unit"] == "compressor") & (trip["event"] == "trip")].iloc[0]
    assert "assumed" not in other["note"]


def test_mass_pull_converts_an_mwh_bus_by_its_tonne_factor():
    # GK A on 12b-2: slurry counted in MWh at 0.5 MWh/t, so 1 MWh of slurry is 2 t. Each flotation draws 500 MWh
    # (1000 t) of slurry per unit of p0 and makes 9.4 t of concentrate: 9.4 / 1000 = 0.0094 t per t.
    n = _chain()
    n.buses["mwh_per_t"] = math.nan
    n.buses.loc["slurry", ["unit", "mwh_per_t"]] = ["MWh", 0.5]
    mp, _ = pc.mass_pull(n, "mill")
    assert mp == pytest.approx(0.0094, rel=1e-12)


def test_an_extendable_unit_rates_on_p_nom_opt_not_p_nom():
    n = _chain()
    n.links.loc["mill", ["p_nom_extendable", "p_nom", "p_nom_opt"]] = [True, 1.0, 8.8]
    rate, basis = pc.rate_tph(n, "mill")
    assert rate == pytest.approx(550.0) and basis.startswith("p_nom_opt 8.8 MW")
    n.links.loc["mill", "p_nom_opt"] = 0.0
    assert pc.rate_tph(n, "mill") == (None, "unknown: p_nom_opt is not above 0")


# The two files' columns before they carried the product (G2P #23, G23-1): adding it changes nothing else.
_DIP_COLUMNS_BEFORE = "level,unit,u_min_pct,u_max_pct,t_min_s,t_max_s,depth_pct,duration_s,count_per_year,verdict,dip_stats_source,note,trips_per_year,restart_time_h,rate_tph,mass_pull,lost_t_per_trip,lost_t_per_year,label,rate_basis,mass_pull_basis,incomplete,unknown_count,known_count,profile".split(",")
_RESTART_COLUMNS_BEFORE = "level,unit,tier,rated_mw,block,pickup_s,full_rate_s,restart_time_h,rate_tph,mass_pull,du_pct,start_verdict,lost_wait_t,lost_restart_t,lost_t,verdict,note,label,rate_basis,mass_pull_basis,transition,sk_mva_source,assumed,t_s,event,fail_count,unknown_count,lower_bound,end_s,limit_pct,limit_clause,profile".split(",")


def test_every_unit_row_names_the_shortfall_product_its_tonnes_are_of(tmp_path):
    run, _ = _run(tmp_path, _restart_hub())
    restart_campus(run)
    for name, before in ((DIP_TRIPS_CSV, _DIP_COLUMNS_BEFORE), (RESTART_CSV, _RESTART_COLUMNS_BEFORE)):
        df = pd.read_csv(run / name, keep_default_na=False)          # raw text: an empty cell stays ""
        assert [c for c in df.columns if c != "product"] == before
        unit = df["level"] == "unit"
        assert set(df.loc[unit, "product"]) == {"nh3"}
        assert set(df.loc[~unit, "product"]) == {""}                  # never "nan"


def test_the_product_is_where_the_mass_pull_walk_ends_not_bus1():
    n = _chain()
    assert pc.shortfall_product(n, "mill") == "conc"                 # mill's bus1 is slurry
    n.generators["process_role"] = [""]
    assert pc.shortfall_product(n, "mill") is None                   # mass pull unknown: no product


def test_a_two_stage_chain_writes_the_downstream_product_on_every_unit_row(tmp_path):
    """nh3 feeds a synthesis Link onto urea, where the shortfall sits: the tonnes, and so the product, are urea's."""
    n = _restart_hub()
    n.add("Carrier", "urea")
    n.add("Bus", "urea", carrier="urea", unit="t")
    n.add("Link", "synth", bus0="nh3", bus1="urea", efficiency=0.5, p_nom=100.0)
    n.generators.loc["__lost_nh3", "bus"] = "urea"
    run, _ = _run(tmp_path, n)
    dip, restart = restart_campus(run)
    for df in (dip, restart):
        unit = df[df["level"] == "unit"]
        assert set(unit["product"]) == {"urea"}
        assert (unit["mass_pull"] == 0.5).all()


def test_a_unit_whose_walk_reads_unknown_has_no_product_and_unknown_tonnes(tmp_path):
    n = _restart_hub()
    n.generators["process_role"] = ""                                 # no shortfall: the walk reads unknown
    run, _ = _run(tmp_path, n)
    restart_campus(run)
    df = pd.read_csv(run / DIP_TRIPS_CSV, keep_default_na=False)
    unit = df[df["level"] == "unit"]
    assert set(unit["product"]) == {""} and set(unit["lost_t_per_year"]) == {""}
