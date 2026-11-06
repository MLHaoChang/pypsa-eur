"""The restart-sequence event model (grid-to-process issue 12b): pickup order and blocks, each restart's motor
start on the right source, and the lost tonnes against hand values."""
import pandas as pd
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.static import motor_start as ms
from gridspine.static import restart_sequence as rs

GRID = ms.Source(sk_mva=150.0)
LIMIT = 3.0
COLS = ["unit", "tier", "rated_mw", "restart_time_h", "rate_tph", "mass_pull", "motor_kw", "start_method"]


def _units(*rows):
    return pd.DataFrame([dict(zip(COLS, r, strict=True)) for r in rows], columns=COLS)


def _vfd(name, tier="process_critical", mw=5.0, restart=4.0, rate=550.0, mp=0.0189):
    return (name, tier, mw, restart, rate, mp, 2000.0, "vfd")


def _seq(units, transition="trip", **kw):
    kw.setdefault("poc_source", GRID)
    kw.setdefault("limit_pct", LIMIT)
    return rs.restart_sequence(units, transition, **kw)


def test_lost_restart_tonnes_equal_the_sum_over_tripped_units():
    units = _units(_vfd("Mill1"), _vfd("Mill2"), _vfd("Flot", tier="process_flexible", restart=0.5, rate=20.0,
                                                    mp=1.0))
    table, timeline, total = _seq(units)
    expected = 4.0 * 550.0 * 0.0189 * 2 + 0.5 * 20.0 * 1.0
    assert total["lost_restart_t"] == pytest.approx(expected, abs=1e-12)
    # The wait column holds the seconds before each pickup: 1 s, 61 s, 121 s.
    assert list(table["pickup_s"]) == [1.0, 61.0, 121.0]
    waits = (1.0 * 550 * 0.0189 + 61.0 * 550 * 0.0189 + 121.0 * 20.0) / 3600.0
    assert total["lost_wait_t"] == pytest.approx(waits)
    assert total["lost_t"] == pytest.approx(expected + waits)
    assert total["lower_bound"] is False
    assert list(timeline["event"]).count("full_rate") == 3


def test_tier_order_then_first_fit_into_blocks():
    units = _units(_vfd("Defer", tier="deferrable", mw=2.0), _vfd("Mill", mw=6.0), _vfd("Safe", tier="safety",
                                                                                       mw=3.0))
    table, _, _ = _seq(units, pickup_blocks=[8.0, 6.0])
    by = table.set_index("unit")
    assert list(table["unit"]) == ["Safe", "Mill", "Defer"]
    # Safe (3) into block 1; Mill (6) does not fit the 5 left, so block 2; Defer (2) back into block 1.
    assert (by.at["Safe", "block"], by.at["Mill", "block"], by.at["Defer", "block"]) == (1, 2, 1)
    assert by.at["Mill", "pickup_s"] == 61.0 and by.at["Defer", "pickup_s"] == 1.0


def test_a_unit_bigger_than_every_block_fails_no_block_fits():
    table, _, total = _seq(_units(_vfd("Mill", mw=9.0), _vfd("Pump", mw=1.0)), pickup_blocks=[5.0, 5.0])
    mill = table.set_index("unit").loc["Mill"]
    assert mill.verdict == "fail" and rs.NO_BLOCK in mill.note and pd.isna(mill.lost_t)
    assert total["fail_count"] == 1 and total["lower_bound"] is True


def test_no_tier_goes_last_with_a_note():
    table, _, _ = _seq(_units(_vfd("Loose", tier=None), _vfd("Mill", tier="deferrable")))
    assert list(table["unit"]) == ["Mill", "Loose"]
    assert "no criticality tier" in table.iloc[1]["note"] and table.iloc[1]["tier"] is None


def test_island_restart_screens_on_the_island_source_not_the_grid():
    # A 500 kW soft start (3 × rated, pf 0.35): S_start = 3 × 0.5 / 0.85 = 1.764706 MVA,
    # Q = 1.764706 × sqrt(1 - 0.35²) = 1.653088 Mvar.
    unit = _units(("Mill", "process_critical", 1.0, 1.0, 100.0, 1.0, 500.0, "soft"))
    grid, _, _ = _seq(unit, "trip", island_source=ms.Source(sk_mva=20.0))
    island, timeline, total = _seq(unit, "island", island_source=ms.Source(sk_mva=20.0))
    # Grid S_sc 150 / 1.1 = 136.3636 MVA: dU 1.2123 % (pass); island 20 / 1.1 = 18.1818 MVA: 9.0920 % (fail).
    assert grid.iloc[0]["start_verdict"] == "pass" and grid.iloc[0]["du_pct"] == pytest.approx(1.2123, abs=1e-3)
    assert island.iloc[0]["start_verdict"] == "fail" and island.iloc[0]["du_pct"] == pytest.approx(9.0920,
                                                                                                   abs=1e-3)
    assert "the island" in timeline[timeline["event"] == "pickup"].iloc[0]["note"]
    assert total["lower_bound"] is True and total["fail_count"] == 1
    # The failed start keeps its place on the timeline and its tonnes.
    assert island.iloc[0]["lost_restart_t"] == pytest.approx(100.0)


def test_island_without_an_island_source_reads_unknown():
    table, _, total = _seq(_units(_vfd("Mill")), "island")   # the grid source is given, the island's is not
    assert table.iloc[0]["start_verdict"] == "unknown" and table.iloc[0]["verdict"] == "unknown"
    assert total["unknown_count"] == 1 and total["lower_bound"] is True


def test_missing_motor_data_is_unknown_never_pass():
    table, _, total = _seq(_units(("Mill", "process_critical", 5.0, 4.0, 550.0, 0.02, None, None)))
    assert table.iloc[0]["start_verdict"] == "unknown" and total["lower_bound"] is True


def test_dol_fails_and_vfd_passes_on_150_mva():
    dol, _, _ = _seq(_units(("Mill", "process_critical", 5.0, 4.0, 550.0, 0.02, 2000.0, "dol")))
    vfd, _, _ = _seq(_units(_vfd("Mill")))
    assert dol.iloc[0]["start_verdict"] == "fail" and dol.iloc[0]["du_pct"] == pytest.approx(9.876076, abs=1e-5)
    assert vfd.iloc[0]["start_verdict"] == "pass" and vfd.iloc[0]["du_pct"] == pytest.approx(0.538784, abs=1e-5)


def test_tonnes_counted_past_the_window_with_a_note():
    table, _, _ = _seq(_units(_vfd("A"), _vfd("B")))
    b = table.set_index("unit").loc["B"]
    assert b.full_rate_s == pytest.approx(61.0 + 4 * 3600.0)
    assert rs.BEYOND in b.note and b.lost_restart_t == pytest.approx(4.0 * 550.0 * 0.0189)
    short, _, _ = _seq(_units(_vfd("A", restart=1.0)))
    assert rs.BEYOND not in short.iloc[0]["note"]


def test_missing_restart_inputs_add_no_tonnes():
    table, _, total = _seq(_units(_vfd("A", rate=None), _vfd("B")))
    assert pd.isna(table.set_index("unit").at["A", "lost_t"]) and total["unknown_count"] == 1
    assert total["lower_bound"] is True
    assert total["lost_restart_t"] == pytest.approx(4.0 * 550.0 * 0.0189)


@pytest.mark.parametrize("kw, match", [
    ({"pickup_blocks": [5.0, 0.0]}, "pickup_blocks"),
    ({"pickup_interval_s": 0.0}, "pickup interval"),
])
def test_bad_parameters_are_refused(kw, match):
    with pytest.raises(ContractError, match=match):
        _seq(_units(_vfd("A")), **kw)


def test_unknown_transition_and_tier_are_refused():
    with pytest.raises(ContractError, match="transition"):
        _seq(_units(_vfd("A")), "brownout")
    with pytest.raises(ContractError, match="tier"):
        _seq(_units(_vfd("A", tier="vital")))


def test_a_failed_start_on_a_unit_missing_inputs_counts_as_both():
    unit = _units(("Mill", "process_critical", 5.0, None, 550.0, 0.02, 2000.0, "dol"))
    table, _, total = _seq(unit)
    assert table.iloc[0]["start_verdict"] == "fail" and table.iloc[0]["verdict"] == "unknown"
    assert total["fail_count"] == 1 and total["unknown_count"] == 1 and total["lower_bound"] is True


def test_full_rate_exactly_at_four_hours_is_inside_the_window():
    # Picked up at 1 s, full rate 4 h - 1 s later: exactly 14 400 s.
    table, _, _ = _seq(_units(_vfd("A", restart=(rs.HORIZON_S - rs.T_FIRST_S) / 3600.0)))
    assert table.iloc[0]["full_rate_s"] == pytest.approx(rs.HORIZON_S) and rs.BEYOND not in table.iloc[0]["note"]
    later, _, _ = _seq(_units(_vfd("A", restart=rs.HORIZON_S / 3600.0)))
    assert rs.BEYOND in later.iloc[0]["note"]
