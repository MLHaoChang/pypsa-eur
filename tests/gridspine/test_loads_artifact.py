"""Task 5: the loads artifact and hour-consistent load flow.

The defect this file exists to kill: increment 1 never rescaled `net.load`, so
the pandapower demand sat at case39's native (hour-19 peak) level while the
dispatch came from whichever hour was asked for. Every non-peak hour still
CONVERGED — the slack silently imported the difference — which is why the
increment-1 driver had to refuse them outright. The loads artifact makes the
demand track the hour, and the slack bound below is the assertion that says so.
"""
import json

import numpy as np
import pandas as pd
import pytest

from gridspine.drivers.planning import run_39bus_slice
from gridspine.ingest.pandapower_source import load_case39, load_case39_res, registry_from_net
from gridspine.producers.pypsa_nodal import LOAD_SHAPE, to_loads_table, to_pypsa
from gridspine.ranking.metrics import _checked_loads
from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import validate_dispatch, validate_loads
from gridspine.static.loadflow import apply_snapshot, run_lf

TEST_HOUR = 8


def _good_loads():
    return pd.DataFrame(
        {
            "bus": ["BUS_01", "BUS_01", "BUS_02", "BUS_02"],
            "hour": [0, 1, 0, 1],
            "p_mw": [10.0, 12.0, 0.0, 5.5],
            "q_mvar": [3.0, -3.5, 0.0, 1.25],
        }
    )


# ---------------------------------------------------------------------------
# validate_loads
# ---------------------------------------------------------------------------

def test_validate_loads_accepts_and_coerces():
    out = validate_loads(_good_loads())
    assert list(out.columns) == ["bus", "hour", "p_mw", "q_mvar"]
    assert out["hour"].dtype == "int64"
    assert out["p_mw"].dtype == "float64" and out["q_mvar"].dtype == "float64"
    assert len(out) == 4


def test_validate_loads_rejects_missing_columns():
    with pytest.raises(ContractError, match="missing columns"):
        validate_loads(_good_loads().drop(columns=["q_mvar"]))


def test_validate_loads_rejects_duplicate_bus_hour():
    df = _good_loads()
    df.loc[len(df)] = ["BUS_01", 0, 4.0, 1.0]
    with pytest.raises(ContractError, match="duplicate"):
        validate_loads(df)


def test_validate_loads_rejects_negative_p():
    df = _good_loads()
    df.loc[0, "p_mw"] = -1.0
    with pytest.raises(ContractError, match="negative p_mw"):
        validate_loads(df)


@pytest.mark.parametrize("bad", [np.inf, -np.inf, np.nan])
def test_validate_loads_rejects_non_finite_q(bad):
    df = _good_loads()
    df.loc[1, "q_mvar"] = bad
    with pytest.raises(ContractError):
        validate_loads(df)


@pytest.mark.parametrize("bad", [np.inf, np.nan])
def test_validate_loads_rejects_non_finite_p(bad):
    df = _good_loads()
    df.loc[1, "p_mw"] = bad
    with pytest.raises(ContractError):
        validate_loads(df)


def test_validate_loads_rejects_null_bus():
    df = _good_loads()
    df.loc[0, "bus"] = None
    with pytest.raises(ContractError, match="null bus"):
        validate_loads(df)


def test_validate_loads_sees_fractional_hour_before_coercion():
    """`astype('int64')` would turn 1.5 into 1 in silence; the guard runs first."""
    df = _good_loads()
    df["hour"] = df["hour"].astype(float)
    df.loc[1, "hour"] = 1.5
    with pytest.raises(ContractError, match="integral"):
        validate_loads(df)


def test_everything_validate_loads_accepts_is_accepted_by_ranking():
    """The producer-side contract must not out-accept the consumer-side check.

    `ranking._checked_loads` deliberately re-checks a minimum so a client can
    recompute metrics from the CSV alone. If `validate_loads` let a frame
    through that ranking rejects, the artifact would be written and the study
    would fail one stage later, on the consumer's machine.
    """
    accepted = validate_loads(_good_loads())
    _checked_loads(accepted)  # must not raise
    _checked_loads(to_loads_table(*_case39_pair()))


def _case39_pair():
    net = load_case39()
    return to_pypsa(net), net


# ---------------------------------------------------------------------------
# to_loads_table
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def case39_loads():
    n, net = _case39_pair()
    return to_loads_table(n, net), n, net


def test_loads_table_is_valid_and_covers_every_bus_hour(case39_loads):
    loads, n, net = case39_loads
    validate_loads(loads)
    assert len(loads) == len(net.load) * len(n.snapshots)
    assert set(loads["hour"]) == set(range(len(n.snapshots)))


def test_loads_table_hour0_sums_match_p_set(case39_loads):
    loads, n, _net = case39_loads
    expected = float(n.loads_t.p_set.iloc[0].sum())
    got = float(loads.loc[loads["hour"] == 0, "p_mw"].sum())
    assert got == pytest.approx(expected, rel=1e-9)


def test_loads_table_per_bus_matches_p_set_column_by_column(case39_loads):
    loads, n, _net = case39_loads
    bus_of = n.loads["bus"]
    for hour in (0, TEST_HOUR, 19, 23):
        row = loads[loads["hour"] == hour].set_index("bus")["p_mw"]
        for load_name, p in n.loads_t.p_set.iloc[hour].items():
            assert row.at[bus_of.at[load_name]] == pytest.approx(float(p), rel=1e-9)


def test_loads_table_q_follows_the_native_power_factor(case39_loads):
    """q_mvar is a constant-power-factor scaling of the native pandapower Q."""
    loads, _n, net = case39_loads
    name_of = net.bus["name"]
    native = net.load.groupby("bus")[["p_mw", "q_mvar"]].sum()
    at_hour = loads[loads["hour"] == TEST_HOUR].set_index("bus")
    for bus_idx, rec in native.iterrows():
        bus = name_of.at[bus_idx]
        ratio = float(rec["q_mvar"]) / float(rec["p_mw"])
        assert at_hour.at[bus, "q_mvar"] == pytest.approx(
            at_hour.at[bus, "p_mw"] * ratio, rel=1e-9
        )


# ---------------------------------------------------------------------------
# apply_snapshot
# ---------------------------------------------------------------------------

def _flat_dispatch(net, registry, hours):
    """A validated dispatch table: every gen at its native p, RES at full p."""
    rows = []
    for hour in hours:
        for unit_id, rec in registry.iterrows():
            if rec["kind"] == "gen":
                i = net.gen.index[net.gen["name"] == unit_id][0]
                p = float(net.gen.at[i, "p_mw"])
            elif rec["kind"] == "res":
                i = net.sgen.index[net.sgen["name"] == unit_id][0]
                p = float(net.sgen.at[i, "p_mw"])
            else:
                p = 0.0
            rows.append({"unit_id": unit_id, "hour": hour, "p_mw": p,
                         "q_mvar": 0.0, "status": 1})
    return validate_dispatch(pd.DataFrame(rows))


def test_apply_snapshot_moves_the_load_to_the_hour(case39_loads):
    loads, _n, _net = case39_loads
    net = load_case39()
    reg = registry_from_net(net)
    native_total = float(net.load["p_mw"].sum())

    apply_snapshot(net, _flat_dispatch(net, reg, [TEST_HOUR]), loads,
                   hour=TEST_HOUR, registry=reg)

    expected = LOAD_SHAPE[TEST_HOUR] * native_total
    assert float(net.load["p_mw"].sum()) == pytest.approx(expected, rel=1e-3)
    # ... and it is genuinely a different number from the native level.
    assert abs(float(net.load["p_mw"].sum()) - native_total) > 0.05 * native_total


def test_apply_snapshot_rejects_a_bus_the_loads_table_does_not_cover():
    """Fail closed: an unset net.load keeps its native value, which IS the
    increment-1 defect. Silently leaving one behind must not be possible."""
    net = load_case39()
    reg = registry_from_net(net)
    n = to_pypsa(net)
    loads = to_loads_table(n, net)
    victim = loads["bus"].iloc[0]
    thinned = loads[loads["bus"] != victim]
    with pytest.raises(ContractError, match="loads table"):
        apply_snapshot(net, _flat_dispatch(net, reg, [TEST_HOUR]), thinned,
                       hour=TEST_HOUR, registry=reg)


def test_apply_snapshot_rejects_an_hour_with_no_load_rows(case39_loads):
    loads, _n, _net = case39_loads
    net = load_case39()
    reg = registry_from_net(net)
    with pytest.raises(ContractError, match="hour"):
        apply_snapshot(net, _flat_dispatch(net, reg, [999]), loads,
                       hour=999, registry=reg)


def test_apply_snapshot_sets_res_sgen_and_takes_curtailed_units_out_of_service():
    net = load_case39_res()
    reg = registry_from_net(net)
    n = to_pypsa(net, res_cf={name: np.full(24, 0.5) for name in net.sgen["name"]})
    loads = to_loads_table(n, net)
    table = _flat_dispatch(net, reg, [TEST_HOUR])

    curtailed = net.sgen["name"].iloc[0]
    derated = net.sgen["name"].iloc[1]
    mask = table["hour"] == TEST_HOUR
    table.loc[mask & (table["unit_id"] == curtailed), ["p_mw", "status"]] = [0.0, 0]
    table.loc[mask & (table["unit_id"] == derated), "p_mw"] = 123.0
    table = validate_dispatch(table)

    apply_snapshot(net, table, loads, hour=TEST_HOUR, registry=reg)

    idx = {net.sgen.at[i, "name"]: i for i in net.sgen.index}
    assert not bool(net.sgen.at[idx[curtailed], "in_service"])
    assert float(net.sgen.at[idx[derated], "p_mw"]) == pytest.approx(123.0)
    assert bool(net.sgen.at[idx[derated], "in_service"])


def test_apply_snapshot_still_commits_the_thermal_units(case39_loads):
    loads, _n, _net = case39_loads
    net = load_case39()
    reg = registry_from_net(net)
    table = _flat_dispatch(net, reg, [TEST_HOUR])
    victim = table.loc[table["unit_id"].str.startswith("G_"), "unit_id"].iloc[0]
    table.loc[table["unit_id"] == victim, ["p_mw", "status"]] = [0.0, 0]
    apply_snapshot(net, validate_dispatch(table), loads, hour=TEST_HOUR, registry=reg)
    i = net.gen.index[net.gen["name"] == victim][0]
    assert not bool(net.gen.at[i, "in_service"])


# ---------------------------------------------------------------------------
# Driver: any hour is valid now, and the slack no longer papers over the gap
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def slice_at_hour8(tmp_path_factory):
    out = tmp_path_factory.mktemp("slice_h8")
    return run_39bus_slice(out, hour=TEST_HOUR), out


def test_driver_runs_a_non_peak_hour_end_to_end(slice_at_hour8):
    res, _out = slice_at_hour8
    assert res.converged
    for key in ("dispatch", "loads", "lf_bus", "lf_branch", "raw", "manifest"):
        assert res.artifacts[key].exists(), key


def test_driver_manifest_records_the_loads_artifact(slice_at_hour8):
    res, _out = slice_at_hour8
    manifest = json.loads(res.artifacts["manifest"].read_text())
    assert manifest["hour"] == TEST_HOUR
    assert manifest["load_consistency"] == "per-snapshot loads artifact (increment 2)"
    assert "power factor" in " ".join(manifest["ledger"]).lower()


def test_driver_loads_artifact_validates_and_is_hour_consistent(slice_at_hour8):
    res, _out = slice_at_hour8
    loads = validate_loads(pd.read_csv(res.artifacts["loads"]))
    net = load_case39()
    at_hour = loads[loads["hour"] == TEST_HOUR]
    assert len(at_hour) == len(net.load)
    assert float(at_hour["p_mw"].sum()) == pytest.approx(
        LOAD_SHAPE[TEST_HOUR] * float(net.load["p_mw"].sum()), rel=1e-3
    )


def test_driver_slack_no_longer_imports_the_load_residual(slice_at_hour8):
    """THE assertion. Increment 1's hour-8 flow converged by importing ~933 MW
    of phantom residual through the slack; with hour-consistent loads the slack
    carries losses only. Skip the load-setting in `apply_snapshot` and this
    fails while everything else stays green.
    """
    res, _out = slice_at_hour8
    served = LOAD_SHAPE[TEST_HOUR] * float(load_case39()["load"]["p_mw"].sum())
    assert abs(res.lf.slack_p_mw) < 0.05 * served


def test_driver_raw_carries_the_requested_hour(slice_at_hour8):
    """Stage-order guard, adapted from the increment-1 hour-19 version."""
    res, _out = slice_at_hour8
    table = pd.read_csv(res.artifacts["dispatch"])
    at_hour = table[(table["hour"] == TEST_HOUR) & (~table["unit_id"].str.startswith("SLK_"))]
    expected = sorted(round(v, 3) for v in at_hour["p_mw"])

    lines = res.artifacts["raw"].read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if "BEGIN GENERATOR DATA" in ln) + 1
    end = next(i for i, ln in enumerate(lines) if "END OF GENERATOR DATA" in ln)
    pg = [float(ln.split(",")[2]) for ln in lines[start:end]]

    assert len(expected) > 0
    assert sorted(round(v, 3) for v in pg[: len(expected)]) == expected
