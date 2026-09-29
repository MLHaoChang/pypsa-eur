"""Increment 9, stage B: connection capacity inside a real study run.

The study writes a DC estimate for every bus and both kinds at each selected
hour, into ``capacity.csv`` in the run directory and into each hour's bundle.
The AC answer is computed on demand for one bus (``drivers.capacity``). The
hour's network is rebuilt from the run's own dispatch and loads, and the
result is cached back into both places, so a downloaded bundle carries it.

The run is the increment-7 slice's shape: a client's two flat hours on the
real 39-bus grid, with screening on (the pass that writes bundles).
"""
import json

import pandas as pd
import pytest

from gridspine.drivers.capacity import capacity_table, compute_capacity_ac
from gridspine.drivers.study import StudyConfig, run_study
from gridspine.ingest.pandapower_source import load_case39_res, registry_from_net
from gridspine.schema.capacity import validate_capacity
from gridspine.schema.contracts import ContractError
from gridspine.static.capacity import CAPACITY_LEDGER

HOURS = 2


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    net = load_case39_res()
    registry = registry_from_net(net)
    src = tmp_path_factory.mktemp("client")
    load_buses = list(net.bus.loc[net.load.bus, "name"].astype(str))
    total_p = float(net.load["p_mw"].sum())
    units = list(registry.index.astype(str))
    pd.DataFrame([
        {"unit_id": u, "hour": h, "p_mw": round(total_p / len(units), 3), "q_mvar": 0.0, "status": 1}
        for h in range(HOURS) for u in units
    ]).to_csv(src / "d.csv", index=False)
    pd.DataFrame([
        {"bus": b, "hour": h, "p_mw": p, "q_mvar": q}
        for h in range(HOURS)
        for b, p, q in zip(load_buses, net.load["p_mw"], net.load["q_mvar"])
    ]).to_csv(src / "l.csv", index=False)
    out = tmp_path_factory.mktemp("capacity-study")
    result = run_study(StudyConfig(outdir=out, from_external=src / "d.csv",
                                   from_external_loads=src / "l.csv", k=1, screen=True))
    return out, result, list(net.bus["name"])


def _hours(result):
    return sorted(int(h) for h in result.selected["hour"])


def test_the_run_carries_a_dc_capacity_row_for_every_bus_kind_and_selected_hour(run):
    out, result, buses = run
    table = validate_capacity(pd.read_csv(out / "capacity.csv"))
    hours = _hours(result)
    assert len(table) == len(hours) * len(buses) * 2
    assert (table["method"] == "dc").all()
    assert set(table["bus"]) == set(buses)
    assert set(table["hour"]) == set(hours)
    assert table["capacity_mw"].isna().all()           # DC fills the estimate only
    assert (table["dc_estimate_mw"] >= 0).all()


def test_each_bundle_carries_its_own_hours_capacity_and_lists_it(run):
    out, result, buses = run
    for hour in _hours(result):
        bundle = out / f"bundle_h{hour}"
        rows = validate_capacity(pd.read_csv(bundle / "capacity.csv"))
        assert set(rows["hour"]) == {hour}
        assert len(rows) == len(buses) * 2
        assert "capacity.csv" in json.loads((bundle / "manifest.json").read_text())["files"]


def test_the_ledger_states_the_capacity_rule(run):
    out, result, _buses = run
    hour = _hours(result)[0]
    entries = json.loads((out / f"bundle_h{hour}" / "ledger.json").read_text())["entries"]
    for line in CAPACITY_LEDGER:
        assert line in entries


def test_ac_on_demand_fills_every_selected_hour_and_is_cached_in_run_and_bundles(run):
    out, result, buses = run
    hours = _hours(result)
    got = compute_capacity_ac(out, "BUS_16", "load")
    assert sorted(got["hour"]) == hours
    assert (got["method"] == "ac").all() and got["capacity_mw"].notna().all()
    assert got["dc_estimate_mw"].notna().all()            # the DC figure travels with it

    table = capacity_table(out)
    assert len(table) == len(hours) * len(buses) * 2      # replaced, not appended
    mine = table[(table["bus"] == "BUS_16") & (table["kind"] == "load")]
    assert (mine["method"] == "ac").all()
    for hour in hours:
        rows = validate_capacity(pd.read_csv(out / f"bundle_h{hour}" / "capacity.csv"))
        row = rows[(rows["bus"] == "BUS_16") & (rows["kind"] == "load")]
        assert len(row) == 1 and row["method"].iloc[0] == "ac"


def test_computing_twice_leaves_one_row_per_key(run):
    out, result, buses = run
    compute_capacity_ac(out, "BUS_03", "generation")
    compute_capacity_ac(out, "BUS_03", "generation")
    table = capacity_table(out)
    assert not table.duplicated(subset=["bus", "hour", "kind"]).any()
    assert len(table) == len(_hours(result)) * len(buses) * 2


@pytest.mark.parametrize("bus, kind, match", [
    ("BUS_99", "load", "BUS_99"),
    ("BUS_16", "storage", "storage"),
])
def test_a_bad_request_is_refused_with_the_reason(run, bus, kind, match):
    out, _result, _buses = run
    with pytest.raises(ContractError, match=match):
        compute_capacity_ac(out, bus, kind)


def test_a_run_without_a_capacity_table_is_refused(tmp_path):
    with pytest.raises(ContractError, match="capacity table"):
        capacity_table(tmp_path)
