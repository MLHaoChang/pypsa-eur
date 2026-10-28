"""A1.6/A1.7: validity of what get_aggregate_load shows on any turn.

The checked path (test_p1_aggregate) refuses a partial sum before the handler
runs. These cases hold the handler itself, which the route and the chat tool
share, to the same contract: a missing or non-finite step is null, not 0; a
repeated name counts once; an unknown name or a load with no profile is named,
not dropped; and the peak and mean are null, not a number, whenever the sum is
not complete.
"""

import math

import numpy as np
import pandas as pd
import pytest

from routers.network_profiles import aggregate_load_profile
from services import assurance_records as records
from services import chat_tools
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.test_p1_aggregate import L1, L2, MEAN, PEAK, loads  # noqa: F401


def network():
    return PyPSAService.get_active_context().network


@pytest.fixture(params=["route", "chat"])
def read(request):
    """Both public channels of the one handler."""
    if request.param == "route":
        return lambda **kw: aggregate_load_profile(**kw)
    return lambda **kw: chat_tools.get_aggregate_load(**kw)


def assert_blocked(out):
    assert out["complete"] is False
    assert out["gaps"]
    assert out["peak"] is None
    assert out["mean"] is None


def test_complete_sum_is_unchanged(loads, read):
    out = read(section="electricity")
    assert out["complete"] is True
    assert out["gaps"] == []
    assert out["values"] == [6.0, 8.0, 6.0]
    assert out["peak"] == PEAK
    assert math.isclose(out["mean"], MEAN)
    assert out["unknown_names"] == []
    assert out["loads_without_profile"] == []


def test_complete_sum_matches_checked_source(loads):
    ctx = PyPSAService.get_active_context()
    with records.network_guard():
        _digest, gap, totals, _targets = records.aggregate_source(
            ctx, {"names": "L1,L2"}
        )
    assert gap is None
    out = aggregate_load_profile(names="L1,L2")
    assert out["values"] == [float(v) for v in totals]
    assert out["peak"] == max(totals)


def test_missing_step_is_null_not_zero(loads, read):
    network().loads_t.p_set.loc[network().snapshots[1], "L2"] = np.nan
    out = read(names="L1,L2")
    assert out["values"] == [6.0, None, 6.0]
    assert_blocked(out)
    assert any("1 missing or non-finite step" in g for g in out["gaps"])


def test_infinite_step_is_null(loads, read):
    network().loads_t.p_set.loc[network().snapshots[0], "L1"] = np.inf
    out = read(names="L1,L2")
    assert out["values"][0] is None
    assert_blocked(out)


def test_repeated_name_counts_once(loads, read):
    out = read(names="L1,L1,L2")
    assert out["values"] == [6.0, 8.0, 6.0]
    assert out["total_loads"] == 2
    assert out["peak"] == PEAK
    assert out["complete"] is True


def test_unknown_name_is_named_and_blocks(loads, read):
    out = read(names="L1,L2,ghost")
    assert out["unknown_names"] == ["ghost"]
    assert any("ghost" in g for g in out["gaps"])
    assert_blocked(out)


def test_only_unknown_names_is_null_not_zero(loads, read):
    out = read(names="ghost")
    assert out["unknown_names"] == ["ghost"]
    assert_blocked(out)


def test_load_without_profile_is_named_and_blocks(loads, read):
    network().add("Load", "S1", bus="B1", p_set=3.0)
    out = read(names="L1,L2,S1")
    assert out["loads_without_profile"] == ["S1"]
    assert out["loads_with_profile"] == 2
    assert any("1 of 3 loads have no profile" in g for g in out["gaps"])
    assert_blocked(out)


def test_no_profile_at_all_is_null_not_zero(loads, read):
    network().add("Load", "S1", bus="B1", p_set=3.0)
    out = read(names="S1")
    assert out["values"] == []
    assert_blocked(out)


def test_empty_selection_is_null_not_zero(loads, read):
    out = read(section="heat")
    assert out["total_loads"] == 0
    assert_blocked(out)
    assert "no loads match" in out["gaps"]


def test_misaligned_upload_is_a_gap(loads, read, monkeypatch):
    from routers import network_profiles

    other = pd.Series(
        [1.0, 1.0, 1.0],
        index=pd.date_range("2030-01-01", periods=3, freq="h"),
    )
    monkeypatch.setitem(network_profiles._user_ts, ("loads", "p_set", "L2"), other)
    out = read(names="L1,L2")
    assert None in out["values"]
    assert any("snapshots" in g for g in out["gaps"])
    assert_blocked(out)


def test_boolean_series_is_not_a_number(loads, read, monkeypatch):
    from routers import network_profiles

    flags = pd.Series([True, False, True], index=network().snapshots)
    monkeypatch.setitem(network_profiles._user_ts, ("loads", "p_set", "L2"), flags)
    out = read(names="L1,L2")
    assert "a step that is not a number" in out["gaps"]
    assert_blocked(out)
