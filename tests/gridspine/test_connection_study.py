"""Increment 10, stage B: the connection-point assessment inside a real run.

The driver assesses one facility at every selected hour. It rebuilds each
hour's network from the run's own dispatch and loads, as the handoff pass does,
and takes the minimum fault level at the POC from that hour's bundle. The rows
are stored, keyed by a stable id for the facility, in the run directory and in
each bundle, so a downloaded bundle carries them.
"""
import pandas as pd
import pytest

from gridspine.drivers.connection import assess_facility, connection_table
from gridspine.drivers.study import StudyConfig, run_study
from gridspine.ingest.pandapower_source import load_case39_res, registry_from_net
from gridspine.schema.connection import CHECKS
from gridspine.schema.contracts import ContractError

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
    out = tmp_path_factory.mktemp("connection-study")
    result = run_study(StudyConfig(outdir=out, from_external=src / "d.csv",
                                   from_external_loads=src / "l.csv", k=1, screen=True))
    return out, sorted(int(h) for h in result.selected["hour"])


FACILITY = {"bus": "BUS_16", "load_mw": 300.0, "load_pf": 0.98, "onsite_mw": 100.0, "onsite_converter": True}


def test_every_check_at_every_selected_hour_with_the_facility_on_each_row(run):
    out, hours = run
    rows = assess_facility(out, FACILITY)
    assert sorted(rows["hour"].unique()) == hours
    for hour in hours:
        assert sorted(rows.loc[rows["hour"] == hour, "check"]) == sorted(CHECKS)
    assert (rows["bus"] == "BUS_16").all() and (rows["load_mw"] == 300.0).all()
    assert rows["assessment_id"].nunique() == 1
    assert (rows["profile"] == "eu_rfg_dcc_ce").all()


def test_the_scr_uses_the_hours_own_minimum_fault_level_at_the_poc(run):
    out, hours = run
    rows = assess_facility(out, FACILITY)
    for hour in hours:
        fl = pd.read_csv(out / f"bundle_h{hour}" / "fault_levels.csv")
        sk = float(fl[(fl["bus"] == "BUS_16") & (fl["case"] == "min")]["sk_mva"].iloc[0])
        scr = rows[(rows["hour"] == hour) & (rows["check"] == "scr_onsite")]["value"].iloc[0]
        assert scr == pytest.approx(sk / 100.0)


def test_assessments_are_stored_in_the_run_and_the_bundles_and_repeat_idempotently(run):
    out, hours = run
    first = assess_facility(out, FACILITY)
    assess_facility(out, FACILITY)                                   # same facility again
    other = assess_facility(out, {**FACILITY, "bus": "BUS_03", "onsite_mw": 0.0})
    table = connection_table(out)
    assert set(table["assessment_id"]) == {first["assessment_id"].iloc[0], other["assessment_id"].iloc[0]}
    assert not table.duplicated(subset=["assessment_id", "hour", "check"]).any()
    for hour in hours:
        b = pd.read_csv(out / f"bundle_h{hour}" / "connection.csv")
        assert set(b["hour"]) == {hour} and b["assessment_id"].nunique() == 2


def test_a_run_with_no_assessment_yet_has_an_empty_table(tmp_path):
    (tmp_path / "selected.csv").write_text("hour\n")
    assert connection_table(tmp_path).empty


@pytest.mark.parametrize("facility, match", [
    ({**FACILITY, "bus": "BUS_99"}, "BUS_99"),
    ({**FACILITY, "load_mw": -5.0}, "load_mw"),
    ({**FACILITY, "profile": "atlantis"}, "atlantis"),
])
def test_a_bad_facility_is_refused_with_the_reason(run, facility, match):
    out, _hours = run
    with pytest.raises(ContractError, match=match):
        assess_facility(out, facility)


def test_a_run_that_was_never_screened_is_refused(tmp_path):
    with pytest.raises(ContractError, match="screened run"):
        assess_facility(tmp_path, FACILITY)
