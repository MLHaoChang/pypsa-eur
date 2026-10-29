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
from tests.assurance_acceptance.test_p1_aggregate import L1, L2, MEAN, PEAK, loads  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("loads")


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


def test_complete_sum_is_unchanged(read):
    out = read(section="electricity")
    assert out["complete"] is True
    assert out["gaps"] == []
    assert out["values"] == [6.0, 8.0, 6.0]
    assert out["peak"] == PEAK
    assert math.isclose(out["mean"], MEAN)
    assert out["unknown_names"] == []
    assert out["loads_without_profile"] == []


def test_complete_sum_matches_checked_source():
    ctx = PyPSAService.get_active_context()
    with records.network_guard():
        _digest, gap, totals, _targets = records.aggregate_source(
            ctx, {"names": "L1,L2"}
        )
    assert gap is None
    out = aggregate_load_profile(names="L1,L2")
    assert out["values"] == [float(v) for v in totals]
    assert out["peak"] == max(totals)


def test_missing_step_is_null_not_zero(read):
    network().loads_t.p_set.loc[network().snapshots[1], "L2"] = np.nan
    out = read(names="L1,L2")
    assert out["values"] == [6.0, None, 6.0]
    assert_blocked(out)
    assert any("1 missing or non-finite step" in g for g in out["gaps"])


def test_infinite_step_is_null(read):
    network().loads_t.p_set.loc[network().snapshots[0], "L1"] = np.inf
    out = read(names="L1,L2")
    assert out["values"][0] is None
    assert_blocked(out)


def test_repeated_name_counts_once(read):
    out = read(names="L1,L1,L2")
    assert out["values"] == [6.0, 8.0, 6.0]
    assert out["total_loads"] == 2
    assert out["peak"] == PEAK
    assert out["complete"] is True


def test_unknown_name_is_named_and_blocks(read):
    out = read(names="L1,L2,ghost,ghost")
    assert out["unknown_names"] == ["ghost"]
    assert out["total_loads"] == 2
    assert any("ghost" in g for g in out["gaps"])
    assert_blocked(out)


def test_only_unknown_names_is_null_not_zero(read):
    out = read(names="ghost")
    assert out["unknown_names"] == ["ghost"]
    assert out["total_loads"] == 0
    assert out["index"] == [] and out["values"] == []
    assert_blocked(out)


def test_load_without_profile_is_named_and_blocks(read):
    network().add("Load", "S1", bus="B1", p_set=3.0)
    out = read(names="L1,L2,S1")
    assert out["loads_without_profile"] == ["S1"]
    assert out["loads_with_profile"] == 2
    assert any("1 of 3 loads have no profile" in g for g in out["gaps"])
    assert_blocked(out)


def test_no_profile_at_all_is_null_not_zero(read):
    network().add("Load", "S1", bus="B1", p_set=3.0)
    out = read(names="S1")
    assert out["values"] == []
    assert_blocked(out)


def test_empty_selection_is_null_not_zero(read):
    out = read(section="heat")
    assert out["total_loads"] == 0
    assert out["index"] == [] and out["values"] == []
    assert_blocked(out)
    assert out["gaps"] == ["no loads match"]


def test_misaligned_upload_is_a_gap(read, monkeypatch):
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


def test_boolean_series_is_not_a_number(read, monkeypatch):
    from routers import network_profiles

    flags = pd.Series([True, False, True], index=network().snapshots)
    monkeypatch.setitem(network_profiles._user_ts, ("loads", "p_set", "L2"), flags)
    out = read(names="L1,L2")
    assert "a step that is not a number" in out["gaps"]
    assert_blocked(out)


def test_shared_index_off_the_snapshots_is_a_gap(read, monkeypatch):
    from routers import network_profiles

    other = pd.date_range("2030-01-01", periods=3, freq="h")
    for name, values in (("L1", L1), ("L2", L2)):
        monkeypatch.setitem(
            network_profiles._user_ts,
            ("loads", "p_set", name),
            pd.Series(values, index=other),
        )
    out = read(names="L1,L2")
    assert out["gaps"] == ["the loads do not share the network's snapshots"]
    assert_blocked(out)


def test_numeric_string_upload_is_null_not_joined(read, monkeypatch):
    from routers import network_profiles

    text = pd.Series(["1", "4", "5"], index=network().snapshots, dtype=object)
    monkeypatch.setitem(network_profiles._user_ts, ("loads", "p_set", "L2"), text)
    out = read(names="L1,L2")
    assert out["values"] == [None, None, None]
    assert "a step that is not a number" in out["gaps"]
    assert_blocked(out)


def test_repeated_timestamp_is_a_gap_not_an_error(read, monkeypatch):
    from routers import network_profiles

    stamps = network().snapshots
    twice = pd.Series(L2, index=[stamps[0], stamps[0], stamps[2]])
    monkeypatch.setitem(network_profiles._user_ts, ("loads", "p_set", "L2"), twice)
    out = read(names="L1,L2")
    assert out["gaps"] == ["the loads do not share the network's snapshots"]
    assert out["values"] == []
    assert_blocked(out)


def test_timezone_aware_member_is_a_gap_not_an_error(read, monkeypatch):
    from routers import network_profiles

    aware = pd.Series(L2, index=network().snapshots.tz_localize("UTC"))
    monkeypatch.setitem(network_profiles._user_ts, ("loads", "p_set", "L2"), aware)
    out = read(names="L1,L2")
    assert out["gaps"] == ["the loads do not share the network's snapshots"]
    assert out["values"] == []
    assert_blocked(out)



# The full ordered gap list the handler reports for each SCENARIOS case
# (byte-equal to master's before the shared helper; see aggregate_json_dump).
SNAPSHOTS = "the loads do not share the network's snapshots"
EXPECTED_GAPS = {
    "unknown": ["no load named ghost"],
    "only unknown": ["no load named ghost"],
    "no loads match": ["no loads match"],
    "no profile": ["1 of 3 loads have no profile"],
    "no profile at all": ["1 of 1 loads have no profile"],
    "missing step": ["1 missing or non-finite step"],
    "infinite step": ["1 missing or non-finite step"],
    "other snapshots": [SNAPSHOTS, "6 missing or non-finite steps"],
    "both off snapshots": [SNAPSHOTS],
    "bool": ["a step that is not a number", "3 missing or non-finite steps"],
    "numeric string": ["a step that is not a number", "3 missing or non-finite steps"],
    "repeated timestamp": [SNAPSHOTS],
    "timezone-aware": [SNAPSHOTS],
    "many gaps": [
        "no load named ghost",
        "1 of 3 loads have no profile",
        SNAPSHOTS,
        "a step that is not a number",
        "9 missing or non-finite steps",
    ],
    "empty upload": [
        "no load named ghost",
        "1 of 2 loads have no profile",
        SNAPSHOTS,
        "a step that is not a number",
        "no steps",
    ],
}


@pytest.mark.parametrize("case", sorted(EXPECTED_GAPS))
def test_both_paths_name_the_same_first_gap(case, monkeypatch):
    """One helper names the gaps: the checked path refuses on the first,
    the handler reports them all, in order, and neither raises."""
    from tests.assurance_acceptance.aggregate_json_dump import SCENARIOS

    setup, kwargs = SCENARIOS[case]
    setup(monkeypatch)
    ctx = PyPSAService.get_active_context()
    with records.network_guard():
        _digest, gap, totals, _targets = records.aggregate_source(ctx, kwargs)
    out = aggregate_load_profile(**kwargs)
    assert out["gaps"] == EXPECTED_GAPS[case]
    assert gap == out["gaps"][0]
    assert totals is None
    assert_blocked(out)


def test_gap_helper_names_no_steps_alone():
    """An empty member on empty snapshots gives "no steps" and nothing else.

    PyPSA refuses empty snapshots, so only the helper reaches this case;
    "empty upload" reaches "no steps" through both paths.
    """
    empty = pd.DatetimeIndex([])
    gaps, frame, values = records.aggregate_gaps(empty, [], [pd.Series([], index=empty, dtype=float)])
    assert gaps == ["no steps"]
    assert frame is not None and frame.empty
    assert values.shape == (0, 1)
