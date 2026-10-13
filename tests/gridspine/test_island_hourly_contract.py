"""The two I3a island hourly contracts (plan 2026-10-07, I3a; owner option A).

gridspine owns the column sets and their validators
(``gridspine/schema/island.py``); pypsa-gui produces the values from its
solved hub LP. Each table has fixed columns: a missing or an extra column is
refused, as is a duplicate key or a non-finite value where one is required.
"""
import math

import pandas as pd
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.schema.island import (
    ISLAND_HOURLY_COLUMNS,
    ISLAND_HOURLY_CSV,
    ISLAND_UNIT_HOURLY_COLUMNS,
    ISLAND_UNIT_HOURLY_CSV,
    validate_island_hourly,
    validate_island_unit_hourly,
)

NAN = math.nan


def _unit_rows():
    return pd.DataFrame({
        "period": [2030, 2030, 2030, 2030],
        "hour": [0, 1, 0, 1],
        "unit_id": ["bess", "bess", "g1", "g1"],
        "soc_start_mwh": [4.0, 3.0, NAN, NAN],
        "online": [1.0, 1.0, 0.5, 0.0],
        "headroom_up_mw": [6.0, 5.0, 2.1, 0.0],
        "headroom_down_mw": [4.0, 5.0, 0.0, 0.0],
        "p_ups_it_mw": [NAN, NAN, NAN, NAN],
    })


def _hour_rows():
    return pd.DataFrame({
        "period": [2030, 2030],
        "hour": [0, 1],
        "scenario": ["seamless", "seamless"],
        "dp_up_mw": [10.0, -2.0],
        "dp_down_mw": [-10.0, 2.0],
        "inertia_mws": [25.0, 25.0],
        "k_mw_per_hz": [5.0, 5.0],
        "critical_load_mw": [8.0, 8.0],
    })


def test_the_column_sets_are_the_plans():
    assert ISLAND_UNIT_HOURLY_COLUMNS == ("period", "hour", "unit_id", "soc_start_mwh", "online",
                                          "headroom_up_mw", "headroom_down_mw", "p_ups_it_mw")
    assert ISLAND_HOURLY_COLUMNS == ("period", "hour", "scenario", "dp_up_mw", "dp_down_mw",
                                     "inertia_mws", "k_mw_per_hz", "critical_load_mw")
    assert ISLAND_UNIT_HOURLY_CSV == "island_unit_hourly.csv"
    assert ISLAND_HOURLY_CSV == "island_hourly.csv"


def test_valid_tables_pass_in_column_order():
    unit = validate_island_unit_hourly(_unit_rows()[list(reversed(ISLAND_UNIT_HOURLY_COLUMNS))])
    assert tuple(unit.columns) == ISLAND_UNIT_HOURLY_COLUMNS
    hour = validate_island_hourly(_hour_rows())
    assert tuple(hour.columns) == ISLAND_HOURLY_COLUMNS


@pytest.mark.parametrize("validate,rows,cols", [
    (validate_island_unit_hourly, _unit_rows, ISLAND_UNIT_HOURLY_COLUMNS),
    (validate_island_hourly, _hour_rows, ISLAND_HOURLY_COLUMNS),
])
def test_a_missing_column_is_refused(validate, rows, cols):
    for col in cols:
        with pytest.raises(ContractError, match=col):
            validate(rows().drop(columns=[col]))


@pytest.mark.parametrize("validate,rows", [
    (validate_island_unit_hourly, _unit_rows),
    (validate_island_hourly, _hour_rows),
])
def test_an_extra_column_is_refused(validate, rows):
    df = rows()
    df["status"] = 1
    with pytest.raises(ContractError, match="status"):
        validate(df)


def test_a_duplicate_key_is_refused():
    df = pd.concat([_unit_rows(), _unit_rows().iloc[[0]]], ignore_index=True)
    with pytest.raises(ContractError, match="duplicate"):
        validate_island_unit_hourly(df)
    df = pd.concat([_hour_rows(), _hour_rows().iloc[[1]]], ignore_index=True)
    with pytest.raises(ContractError, match="duplicate"):
        validate_island_hourly(df)


def test_an_unknown_scenario_is_refused():
    df = _hour_rows()
    df.loc[0, "scenario"] = "blackout"
    with pytest.raises(ContractError, match="scenario"):
        validate_island_hourly(df)


@pytest.mark.parametrize("col", ["headroom_up_mw", "headroom_down_mw"])
def test_unit_headroom_may_be_empty_only_on_a_ups(col):
    df = _unit_rows()
    df.loc[0, col] = NAN
    with pytest.raises(ContractError, match=col):
        validate_island_unit_hourly(df)


@pytest.mark.parametrize("col", ["dp_up_mw", "dp_down_mw", "inertia_mws", "k_mw_per_hz",
                                 "critical_load_mw"])
def test_every_hourly_value_is_finite(col):
    df = _hour_rows()
    df.loc[1, col] = NAN
    with pytest.raises(ContractError, match=col):
        validate_island_hourly(df)


def test_period_and_hour_are_non_negative_integers():
    df = _unit_rows()
    df["hour"] = df["hour"].astype(float)
    df.loc[0, "hour"] = 0.5
    with pytest.raises(ContractError, match="hour"):
        validate_island_unit_hourly(df)
    df = _hour_rows()
    df.loc[0, "hour"] = -1
    with pytest.raises(ContractError, match="hour"):
        validate_island_hourly(df)


def test_online_is_a_share_of_the_unit():
    df = _unit_rows()
    df.loc[2, "online"] = 1.5
    with pytest.raises(ContractError, match="online"):
        validate_island_unit_hourly(df)


def test_a_ups_row_carries_its_it_load_and_no_headroom():
    df = pd.DataFrame({"period": [2030], "hour": [0], "unit_id": ["ups"], "soc_start_mwh": [2.0],
                       "online": [NAN], "headroom_up_mw": [NAN], "headroom_down_mw": [NAN],
                       "p_ups_it_mw": [6.0]})
    out = validate_island_unit_hourly(df)
    assert out.loc[0, "p_ups_it_mw"] == 6.0
