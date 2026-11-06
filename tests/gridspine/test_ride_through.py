"""Dips to trips to lost tonnes (grid-to-process issue 12b): the EN 50160 class conversion, the curve's
boundaries and refusals, unknown never read as "rides", and the lost tonnes against hand values."""
import pandas as pd
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.static import ride_through as rt

# One class: residual 40..70 % (depth 60 % at its worst corner) for 0.2..0.5 s, 2 dips a year.
ONE = pd.DataFrame([{"u_min_pct": 40.0, "u_max_pct": 70.0, "t_min_s": 0.2, "t_max_s": 0.5, "count_per_year": 2.0}])


def _units(*rows):
    cols = ["unit", "ride_through_curve", "restart_time_h", "rate_tph", "mass_pull"]
    return pd.DataFrame([dict(zip(cols, r, strict=True)) for r in rows], columns=cols)


def _verdict(curve, stats=ONE):
    return rt.trip_table(_units(("Mill", curve, 4.0, 550.0, 0.02)), stats).iloc[0]["verdict"]


def test_classes_convert_residual_to_depth_at_the_worst_corner():
    classes, source = rt.dip_classes()
    assert source == "demo" and len(classes) == 25
    first = classes.iloc[0]                          # u 80..90 %, 0.01..0.2 s
    assert (first.depth_pct, first.duration_s) == (20.0, 0.2)
    deepest = classes[classes["u_min_pct"] == 0.0]   # the u < 5 % row is depth 100
    assert set(deepest["depth_pct"]) == {100.0}
    assert set(classes["duration_s"]) == {0.2, 0.5, 1.0, 5.0, 60.0}


def test_point_exactly_on_the_corner_rides_and_one_step_beyond_trips():
    assert _verdict("60:0.5") == "rides"
    assert _verdict("59.9:0.5") == "trips"   # one step shallower than the class: the class is deeper
    assert _verdict("60:0.49") == "trips"    # one step shorter than the class: the class is longer


def test_non_monotone_curve_rides_on_any_dominating_point():
    # The 30 % point lasts less than the 80 % point: the 80 % point covers the class.
    assert _verdict("30:0.1, 80:0.6") == "rides"
    assert _verdict("30:0.9, 50:0.6") == "trips"   # neither point is both deep and long enough


def test_dip_deeper_than_every_point_trips():
    deep = pd.DataFrame([{"u_min_pct": 0.0, "u_max_pct": 5.0, "t_min_s": 0.01, "t_max_s": 0.2,
                          "count_per_year": 1.0}])
    assert _verdict("90:10", deep) == "trips"
    assert _verdict("100:0.2", deep) == "rides"


@pytest.mark.parametrize("text, match", [
    ("0:0.5", "above 0 and at most 100"),
    ("100.1:0.5", "above 0 and at most 100"),
    ("-5:0.5", "above 0 and at most 100"),
    ("50:0", "must be above 0 s"),
    ("50:-1", "must be above 0 s"),
    ("50", "pairs of dip depth in % and duration in s"),
    ("50:1:2", "pairs of dip depth in % and duration in s"),
    ("a:1", "not a pair of numbers"),
    ("50:1, 50:2", "appears twice"),
    ("nan:1", "above 0 and at most 100"),
    ("inf:1", "above 0 and at most 100"),
    ("50:inf", "must be above 0 s"),
    ("50:nan", "must be above 0 s"),
])
def test_parser_refuses_bad_curves(text, match):
    with pytest.raises(ContractError, match=match):
        rt.parse_curve(text)


@pytest.mark.parametrize("text", ["50", "a:1", "0:0.5", "50:-1", "20-0.5"])
def test_refusals_read_as_words_not_code(text):
    """UX on #1168: no Python-repr quotes and no ``depth%:duration_s`` notation in what the user reads."""
    with pytest.raises(ContractError) as e:
        rt.parse_curve(text)
    msg = str(e.value)
    assert "'" not in msg and "depth%" not in msg and "duration_s" not in msg, msg


def test_parser_accepts_the_edges():
    assert rt.parse_curve("100:0.02, 0.5:3") == ((0.5, 3.0), (100.0, 0.02))
    assert rt.parse_curve("") == () and rt.parse_curve(None) == () and rt.parse_curve(float("nan")) == ()


def test_no_curve_reads_unknown_never_rides():
    rows = rt.trip_table(_units(("Mill", None, 4.0, 550.0, 0.02)))
    assert set(rows["verdict"]) == {"unknown"}
    lost = rt.lost_tonnes_table(_units(("Mill", "", 4.0, 550.0, 0.02))).iloc[0]
    assert lost.verdict == "unknown" and lost.lost_t_per_year is None and lost.trips_per_year is None


def test_lost_tonnes_hand_value():
    # Trips on the one class (2 a year): 2 × 4 h × 550 t/h × 0.0189 = 83.16 t/yr.
    row = rt.lost_tonnes_table(_units(("Mill", "60:0.3", 4.0, 550.0, 0.0189)), ONE).iloc[0]
    assert row.trips_per_year == 2.0
    assert row.lost_t_per_trip == pytest.approx(4.0 * 550.0 * 0.0189)
    assert row.lost_t_per_year == pytest.approx(83.16)
    assert row.dip_stats_source == "user"


@pytest.mark.parametrize("missing", ["ride_through_curve", "restart_time_h", "rate_tph", "mass_pull"])
def test_any_missing_input_is_unknown_and_the_total_incomplete(missing):
    full = {"unit": "A", "ride_through_curve": "60:0.3", "restart_time_h": 4.0, "rate_tph": 550.0, "mass_pull": 0.02}
    table = rt.lost_tonnes_table(pd.DataFrame([{**full, missing: None}, {**full, "unit": "B"}]), ONE)
    assert table.set_index("unit").at["A", "verdict"] == "unknown"
    total = rt.lost_tonnes_total(table)
    assert total["incomplete"] is True and total["unknown_count"] == 1 and total["known_count"] == 1
    assert "lower bound" in total["note"]


def test_demo_statistics_are_labelled_demo_on_every_row_and_the_total():
    table = rt.lost_tonnes_table(_units(("Mill", "20:0.01", 4.0, 550.0, 0.02)))
    assert set(table["dip_stats_source"]) == {"demo"}
    assert rt.DEMO_NOTE in table.iloc[0]["note"]
    total = rt.lost_tonnes_total(table)
    assert total["dip_stats_source"] == "demo" and rt.DEMO_NOTE in total["note"]
    assert set(rt.trip_table(_units(("Mill", "20:0.01", 4.0, 550.0, 0.02)))["dip_stats_source"]) == {"demo"}


def test_a_unit_riding_through_everything_loses_nothing():
    row = rt.lost_tonnes_table(_units(("Mill", "100:60", 4.0, 550.0, 0.02))).iloc[0]
    assert row.trips_per_year == 0.0 and row.lost_t_per_year == 0.0 and row.verdict == "known"


@pytest.mark.parametrize("bad, match", [
    ({"u_min_pct": 70.0, "u_max_pct": 40.0}, "residual voltage"),
    ({"t_min_s": 0.0}, "duration"),
    ({"count_per_year": -1.0}, "count"),
])
def test_bad_statistics_are_refused(bad, match):
    with pytest.raises(ContractError, match=match):
        rt.dip_classes(ONE.assign(**bad))


def test_negative_inputs_are_refused():
    with pytest.raises(ContractError, match="Restart time must be 0 or more"):
        rt.lost_tonnes_table(_units(("Mill", "60:0.3", -1.0, 550.0, 0.02)), ONE)
