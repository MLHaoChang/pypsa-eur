"""``island_sc.genset_machine`` lifted out of ``machine_net`` (GC-13, GK A G2):
the gen, trafo and sgen tables ``machine_net`` builds for a genset campus
equal those pinned from master d0d98072, before the lift
(``fixtures/gc13_master_pins.json``)."""

import io
import json
from pathlib import Path

import pandas as pd
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.static.island_sc import machine_net
from tests.gridspine.test_island_sc import a_genset_island

PINS = json.loads(
    (Path(__file__).parent / "fixtures" / "gc13_master_pins.json").read_text()
)["machine_net"]
CASES = {
    "on_pcc": {},
    "unit_trafo": {"with_unit_trafo": True},
    "unit_trafo_oltc": {"with_unit_trafo": True, "oltc": True},
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_machine_net_builds_the_same_tables_as_before_the_lift(case):
    c = build_campus(a_genset_island(**CASES[case]))
    net = machine_net(c, set(c.units.index), case="max")
    for table in ("gen", "trafo", "sgen"):
        got = json.loads(
            getattr(net, table).to_json(orient="split", double_precision=15)
        )
        assert got == json.loads(PINS[case][table]), table


def test_the_pins_hold_a_genset_machine():
    gen = pd.read_json(io.StringIO(PINS["unit_trafo"]["gen"]), orient="split")
    assert list(gen["name"]) == ["G"] and gen["power_station_trafo"].notna().all()
