"""The island frequency check and the PSS/E handoff through the campus driver
(plan 2026-10-07-campus-island-operation, I6 at the I3b hours, I8's hours).

A run directory is written by hand (campus.yaml, the hourly and PCC tables,
the island tables, the selection), so the driver is tested without a
solve. Oracles:
* the island hours are the selected hours an island criterion picked;
* E2-E6 run at those hours only, E1 at every hour, and the summary names
  the hours the every-hour E1 fails that the ranking did not select;
* what is missing (the requirements, the island tables) is refused;
* one PSS/E bundle per island hour, named by period and hour.
"""
import json
import math

import pandas as pd
import pytest
import yaml

from gridspine.drivers import campus_study as D
from gridspine.schema.contracts import ContractError
from gridspine.schema.island import ISLAND_HOURLY_CSV, ISLAND_UNIT_HOURLY_CSV
from tests.gridspine.test_island_flow import rows, spec
from tests.gridspine.test_island_schema import limits, requirements, t, ups

HOURS = (0, 1, 2)


def campus_spec(scenarios=("seamless",), island=True):
    s = spec()
    s["campus"]["units"]["UPS1"] = {"kind": "ups", "bus": "MV2", "p_mw": t(8.0), "e_mwh": t(2.0), "s_mva": t(9.0),
                                    "island": {**ups(), "it_load": ["DC_LOAD"]}}
    if island:
        req = requirements()
        lim = limits()
        lim["transition"]["rocof_hz_per_s"] = t(100.0)              # the nadir decides, not RoCoF
        req.update(scenarios=list(scenarios), genset_redundancy_n=t(0), pickup_blocks=t([2.0, 2.0]),
                   frequency_limits=lim)
        s["campus"]["island"] = req
    return s


def hour_rows(load=25.0):
    return pd.concat([rows(load=load), pd.DataFrame({"unit_id": ["UPS1"], "p_mw": [0.0], "status": [1]})],
                     ignore_index=True)


def write_run(run, s, dp_up=(2.0, 2.0, 40.0), selected=((0, "max_island_dp_up"), (1, "max_import")),
              island_tables=True, load=25.0, genset_online=1.0):
    run.mkdir(parents=True, exist_ok=True)
    (run / D.CAMPUS_YAML).write_text(yaml.safe_dump(s, sort_keys=False))
    hourly = pd.concat([hour_rows(load).assign(period=2030, hour=h) for h in HOURS], ignore_index=True)
    hourly[["unit_id", "period", "hour", "p_mw", "status"]].to_csv(run / "campus_hourly.csv", index=False)
    pd.DataFrame({"period": 2030, "hour": list(HOURS), "weight": 1.0, "import_mw": 10.0}).to_csv(
        run / "campus_pcc.csv", index=False)
    if island_tables:
        pd.DataFrame({"period": 2030, "hour": list(HOURS), "scenario": "seamless", "dp_up_mw": list(dp_up),
                      "dp_down_mw": -1.0, "inertia_mws": 1.0, "k_mw_per_hz": 1.0, "critical_load_mw": 1.0}).to_csv(
            run / ISLAND_HOURLY_CSV, index=False)
        unit = [{"period": 2030, "hour": h, "unit_id": u, "soc_start_mwh": soc, "online": on,
                 "headroom_up_mw": hr, "headroom_down_mw": hr, "p_ups_it_mw": it}
                for h in HOURS for u, soc, on, hr, it in (("GEN1", math.nan, genset_online, 5.0, math.nan),
                                                         ("UPS1", 1.0, math.nan, math.nan, 20.0))]
        pd.DataFrame(unit).to_csv(run / ISLAND_UNIT_HOURLY_CSV, index=False)
    pd.DataFrame([{"period": 2030, "hour": h, "reasons": r} for h, r in selected]).to_csv(
        run / D.SELECTED_CSV, index=False)
    return run


def test_the_island_hours_are_those_an_island_criterion_picked(tmp_path):
    run = write_run(tmp_path / "run", campus_spec(),
                    selected=((0, "max_import"), (1, "max_import;min_island_inertia"), (2, "max_island_dp_down")))
    assert D.island_hours(run) == [(2030, 1), (2030, 2)]


def test_the_frequency_check_runs_e1_every_hour_and_the_rest_at_the_island_hours(tmp_path):
    run = write_run(tmp_path / "run", campus_spec())
    summary = D.island_frequency_campus(run)
    sweep = pd.read_csv(run / D.FREQ_E1_HOURLY_CSV)
    assert sorted(sweep["hour"]) == [0, 1, 2] and set(sweep["dp_source"]) == {"I3a lossless"}
    events = pd.read_csv(run / D.FREQ_EVENTS_CSV)
    assert set(events["hour"]) == {0}                                 # hour 1 was picked by a base criterion only
    assert {"E1", "E2", "E3", "E5"} <= set(events["event"])
    assert pd.read_csv(run / D.FREQ_LOSSES_CSV)["hour"].tolist() == [0]
    # hour 2's 40 MW step fails the nadir at every-hour E1, and the ranking did not select it
    assert summary["e1_failing_hours"] == [[2030, 2]] and summary["e1_failing_not_selected"] == [[2030, 2]]
    assert summary["island_hours"] == [[2030, 0]] and summary["every_hour_e1"]
    assert json.loads((run / D.FREQ_SUMMARY_JSON).read_text()) == summary
    assert summary["ledger"] and summary["label"].startswith("frequency screening")


def test_an_e1_failure_at_a_selected_hour_is_not_listed_as_missed(tmp_path):
    run = write_run(tmp_path / "run", campus_spec(), selected=((2, "max_island_dp_up"),))
    summary = D.island_frequency_campus(run)
    assert summary["e1_failing_hours"] == [[2030, 2]] and summary["e1_failing_not_selected"] == []
    assert set(pd.read_csv(run / D.FREQ_EVENTS_CSV)["hour"]) == {2}


def test_the_bridge_and_the_planned_ramp_are_summarised(tmp_path):
    run = write_run(tmp_path / "run", campus_spec(scenarios=("ups_bridged", "planned")))
    summary = D.island_frequency_campus(run)
    bridge = pd.read_csv(run / D.FREQ_BRIDGE_CSV)
    assert bridge["unit"].tolist() == ["UPS1"] and bridge["it_mw"].tolist() == [20.0]     # I3a's, not the draw
    assert summary["bridge_failing_hours"] == [[2030, 0]]               # 1 MWh at 20 MW: 180 s against 600 s
    assert pd.read_csv(run / D.FREQ_PLANNED_CSV)["feasible"].tolist() == [True]
    assert summary["planned_infeasible_hours"] == []
    assert not summary["every_hour_e1"] and pd.read_csv(run / D.FREQ_E1_HOURLY_CSV).empty


def test_a_planned_islanding_the_units_cannot_cover_is_summarised(tmp_path):
    # 60 MW of load against 10 PV, 5 + 5 genset and 20 BESS: about 45 MW to take over, 25 MW of room
    run = write_run(tmp_path / "run", campus_spec(scenarios=("planned",)), load=60.0)
    summary = D.island_frequency_campus(run, every_hour=False)
    assert summary["planned_infeasible_hours"] == [[2030, 0]]


def test_a_gfm_bridge_row_without_its_stored_energy_is_not_a_failure(tmp_path):
    # the genset is cold: the BESS bridges until it joins, but I3a gives no e(t-1) for the BESS
    run = write_run(tmp_path / "run", campus_spec(), genset_online=0.0)
    summary = D.island_frequency_campus(run, every_hour=False)
    bridge = pd.read_csv(run / D.FREQ_BRIDGE_CSV)
    assert bridge["unit"].tolist() == ["BESS1"] and bridge["kind"].tolist() == ["gfm"] and bridge["ok"].isna().all()
    assert summary["bridge_failing_hours"] == []


@pytest.mark.parametrize("scenarios", [("seamless",), ("ups_bridged", "planned")])
def test_every_table_carries_its_fixed_columns_whatever_ran(tmp_path, scenarios):
    from gridspine.static import frequency as F

    run = write_run(tmp_path / "run", campus_spec(scenarios=scenarios))
    summary = D.island_frequency_campus(run)
    for name, cols in ((D.FREQ_E1_HOURLY_CSV, F.E1_HOURLY_COLUMNS), (D.FREQ_EVENTS_CSV, F.EVENT_COLUMNS),
                       (D.FREQ_LOSSES_CSV, F.LOSSES_COLUMNS), (D.FREQ_BRIDGE_CSV, F.BRIDGE_COLUMNS),
                       (D.FREQ_PLANNED_CSV, F.PLANNED_COLUMNS), (D.FREQ_UPS_CSV, F.UPS_COLUMNS)):
        assert tuple(pd.read_csv(run / name).columns) == cols, name
    assert tuple(summary) == D.FREQ_SUMMARY_KEYS


def test_the_every_hour_e1_can_be_skipped(tmp_path):
    run = write_run(tmp_path / "run", campus_spec())
    summary = D.island_frequency_campus(run, every_hour=False)
    assert not summary["every_hour_e1"] and summary["e1_failing_hours"] == []
    assert pd.read_csv(run / D.FREQ_E1_HOURLY_CSV).empty


def test_the_frequency_check_needs_the_requirements_and_the_island_tables(tmp_path):
    with pytest.raises(ContractError, match="no island requirements"):
        D.island_frequency_campus(write_run(tmp_path / "a", campus_spec(island=False)))
    with pytest.raises(ContractError, match="no island tables"):
        D.island_frequency_campus(write_run(tmp_path / "b", campus_spec(), island_tables=False))


def test_psse_writes_one_bundle_per_island_hour(tmp_path):
    run = write_run(tmp_path / "run", campus_spec(),
                    selected=((0, "max_island_dp_up"), (1, "max_import"), (2, "min_stored_energy")))
    bundles = D.psse_campus(run)
    name = D.load_run_campus(run).name
    assert [b.path.name for b in bundles] == [f"{name}_p2030_h0", f"{name}_p2030_h2"]
    assert all((b.path / f"{b.path.name}.raw").is_file() for b in bundles)
    assert all(b.path.parent == run / D.PSSE_DIR for b in bundles)
    readme = (bundles[0].path / "README.md").read_text()
    assert "frequency:" in readme                                     # the frequency ledger rides along


def test_psse_takes_given_hours_and_refuses_one_the_run_lacks(tmp_path):
    run = write_run(tmp_path / "run", campus_spec())
    name = D.load_run_campus(run).name
    assert [b.path.name for b in D.psse_campus(run, hours=[(2030, 1)])] == [f"{name}_p2030_h1"]
    with pytest.raises(ContractError, match="no hour 7"):
        D.psse_campus(run, hours=[(2030, 7)])
