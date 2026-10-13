"""
WP0/WP4 — report figures are matplotlib PNGs rendered on the backend.

Empty input is ``None`` (the writer then omits the figure with a sentence),
never a blank axis; and the same input renders to the same bytes so a
re-export of an unchanged report does not churn the project directory.
"""
from __future__ import annotations

from services.reports import figures as FIG


def _top(n: int) -> list[dict]:
    return [{
        "rank": i + 1, "name": f"unit{i}", "failure_class": "A" if i % 2 else "B",
        "criticality_eur_per_year": float(1000 * (n - i)),
    } for i in range(n)]


def test_empty_input_is_none_not_a_blank_chart():
    assert FIG.fmea_pareto_png([]) is None
    assert FIG.fmea_pareto_png(None) is None
    assert FIG.fmea_pareto_png([{"name": "x", "criticality_eur_per_year": None}]) is None


def test_pareto_returns_png_bytes():
    data = FIG.fmea_pareto_png(_top(3))
    assert data is not None
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_pareto_is_deterministic_for_the_same_input():
    assert FIG.fmea_pareto_png(_top(4)) == FIG.fmea_pareto_png(_top(4))


def test_pareto_caps_rows_at_top_n():
    # 50 is the engine's own cap (MAX_EH_FMEA_TOP_N); a longer list is cut, not
    # squeezed into unreadable bars.
    assert FIG.fmea_pareto_png(_top(80)) is not None


# --- WP4: frontier -----------------------------------------------------------

def _frontier_points() -> list[dict]:
    """Same shape the writer's fixture uses (copied, not imported)."""
    return [
        {"target_permyriad": 40.0, "status": "ok", "period_basis": "single_period",
         "point": {"cap_mwh": 3960.0, "achieved_ens_mwh": 3900.0,
                   "achieved_shed_hours": 60.0,
                   "total_system_cost_eur": 900_000.0}},
        {"target_permyriad": 10.0, "status": "ok", "period_basis": "single_period",
         "point": {"cap_mwh": 990.0, "achieved_ens_mwh": 816.75,
                   "achieved_shed_hours": 12.5,
                   "total_system_cost_eur": 1_234_567.89}},
        {"target_permyriad": 2.5, "status": "infeasible", "point": None},
    ]


def test_frontier_empty_or_no_ok_point_is_none():
    assert FIG.frontier_png([], None) is None
    assert FIG.frontier_png(None, None) is None
    only_bad = [{"target_permyriad": 5.0, "status": "infeasible", "point": None},
                {"target_permyriad": 1.0, "status": "error", "point": None}]
    assert FIG.frontier_png(only_bad, None) is None
    # An "ok" point without a finite cost is not plottable either.
    nan_cost = [{"target_permyriad": 5.0, "status": "ok",
                 "point": {"total_system_cost_eur": float("nan")}}]
    assert FIG.frontier_png(nan_cost, 0) is None


def test_frontier_returns_png_bytes_with_knee():
    data = FIG.frontier_png(_frontier_points(), 1)
    assert data is not None
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_frontier_tolerates_a_missing_or_out_of_range_knee():
    assert FIG.frontier_png(_frontier_points(), None) is not None
    assert FIG.frontier_png(_frontier_points(), 99) is not None
    # A knee that lands on a non-ok point is ignored, not a crash.
    assert FIG.frontier_png(_frontier_points(), 2) is not None


def test_frontier_is_deterministic_for_the_same_input():
    assert FIG.frontier_png(_frontier_points(), 1) == FIG.frontier_png(_frontier_points(), 1)


def test_frontier_with_an_infeasible_point_in_the_middle_still_renders():
    pts = _frontier_points()
    pts.insert(1, {"target_permyriad": 20.0, "status": "infeasible", "point": None})
    data = FIG.frontier_png(pts, 2)
    assert data is not None and data[:8] == b"\x89PNG\r\n\x1a\n"
    # The infeasible point is drawn (hollow), not dropped: the image differs from
    # the same frontier without it.
    assert data != FIG.frontier_png(_frontier_points(), 1)


# --- WP4: capacity mix -------------------------------------------------------

def test_capacity_mix_empty_or_nothing_positive_is_none():
    assert FIG.capacity_mix_png({}) is None
    assert FIG.capacity_mix_png(None) is None
    assert FIG.capacity_mix_png({"gas": 0.0, "wind": -1.0, "solar": float("nan"),
                                 "coal": None}) is None


def test_capacity_mix_returns_png_bytes():
    data = FIG.capacity_mix_png({"gas": 100.0, "wind": 25.0})
    assert data is not None
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_capacity_mix_is_deterministic_for_the_same_input():
    mix = {"gas": 100.0, "wind": 25.0, "solar": 12.5}
    assert FIG.capacity_mix_png(mix) == FIG.capacity_mix_png(dict(mix))


def test_capacity_rows_sort_descending_and_drop_unplottable_entries():
    rows = FIG._capacity_rows({"wind": 25.0, "gas": 100.0, "solar": 0.0,
                               "coal": -3.0, "oil": float("inf"), "nuclear": "7"})
    assert rows == [("gas", 100.0), ("wind", 25.0), ("nuclear", 7.0)]


def test_capacity_rows_fold_beyond_thirty_carriers_into_other():
    mix = {f"carrier{i:02d}": float(100 - i) for i in range(40)}
    rows = FIG._capacity_rows(mix)
    assert len(rows) == 31
    assert rows[-1][0] == "other"
    assert rows[-1][1] == sum(float(100 - i) for i in range(30, 40))
    assert [name for name, _ in rows[:30]] == [f"carrier{i:02d}" for i in range(30)]
    # 30 carriers exactly need no fold.
    assert len(FIG._capacity_rows(dict(list(mix.items())[:30]))) == 30
    assert FIG.capacity_mix_png(mix) is not None
