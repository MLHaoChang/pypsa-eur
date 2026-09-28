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
