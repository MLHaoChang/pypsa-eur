"""
Native Excel charts drawn from a sheet's own cells (owner decision RW-CHARTS-1).

Every chart here references a cell range on the sheet it is placed on, so it
stays an editable Excel chart that follows the cells. A range is a box
``(min_col, min_row, max_col, max_row)``, 1-based like openpyxl.

``data`` holds the series WITH their titles: by default one series per column
and the title in the box's first row; with ``from_rows=True`` one series per
row and the title in the box's first column. ``cats`` is the category box
(one row or one column, no title cell).

A chart type Excel lacks natively becomes the nearest native form and says so
in its title (a waterfall is a sorted bar): the caller writes that title.
"""
from __future__ import annotations

from openpyxl.chart import BarChart, LineChart, Reference, ScatterChart, Series

Box = tuple[int, int, int, int]

__all__ = ["Box", "bar", "bar_line", "line", "scatter"]

_WIDTH, _HEIGHT = 18.0, 9.0


def _ref(ws, box: Box) -> Reference:
    c0, r0, c1, r1 = box
    return Reference(ws, min_col=c0, min_row=r0, max_col=c1, max_row=r1)


def _frame(chart, title: str, x_title: str | None, y_title: str | None) -> None:
    chart.title = title
    chart.width, chart.height = _WIDTH, _HEIGHT
    if x_title:
        chart.x_axis.title = x_title
    if y_title:
        chart.y_axis.title = y_title
    # openpyxl ≥ 3.1 hides axes unless told otherwise; Excel then draws none.
    chart.x_axis.delete = False
    chart.y_axis.delete = False


def bar(ws, *, title: str, data: Box, cats: Box, anchor: str, from_rows: bool = False,
        stacked: bool = False, horizontal: bool = False, x_title: str | None = None,
        y_title: str | None = None) -> BarChart:
    """A clustered (or stacked) column chart; ``horizontal`` turns it into bars."""
    chart = BarChart()
    chart.type = "bar" if horizontal else "col"
    if stacked:
        chart.grouping = "stacked"
        chart.overlap = 100
    chart.add_data(_ref(ws, data), from_rows=from_rows, titles_from_data=True)
    chart.set_categories(_ref(ws, cats))
    _frame(chart, title, x_title, y_title)
    ws.add_chart(chart, anchor)
    return chart


def line(ws, *, title: str, data: Box, cats: Box, anchor: str, from_rows: bool = False,
         x_title: str | None = None, y_title: str | None = None) -> LineChart:
    """One line per series over the categories."""
    chart = LineChart()
    chart.add_data(_ref(ws, data), from_rows=from_rows, titles_from_data=True)
    chart.set_categories(_ref(ws, cats))
    _frame(chart, title, x_title, y_title)
    ws.add_chart(chart, anchor)
    return chart


def scatter(ws, *, title: str, x: Box, ys: list[Box], anchor: str,
            x_title: str | None = None, y_title: str | None = None) -> ScatterChart:
    """An x–y chart (e.g. a duration curve): ``x`` has no title cell, each of
    ``ys`` is one column whose first cell is the series title."""
    chart = ScatterChart()
    chart.style = 13
    xref = _ref(ws, x)
    for y in ys:
        series = Series(_ref(ws, y), xref, title_from_data=True)
        series.marker.symbol = "none"
        series.smooth = False
        chart.series.append(series)
    _frame(chart, title, x_title, y_title)
    ws.add_chart(chart, anchor)
    return chart


def bar_line(ws, *, title: str, bars: Box, line_data: Box, cats: Box, anchor: str,
             x_title: str | None = None, y_title: str | None = None,
             line_title: str | None = None) -> BarChart:
    """Columns with a line over them on a secondary axis (a yearly cash flow
    with its cumulative discounted sum). Both boxes are column series."""
    chart = BarChart()
    chart.type = "col"
    chart.add_data(_ref(ws, bars), titles_from_data=True)
    chart.set_categories(_ref(ws, cats))
    _frame(chart, title, x_title, y_title)
    over = LineChart()
    over.add_data(_ref(ws, line_data), titles_from_data=True)
    over.set_categories(_ref(ws, cats))
    over.y_axis.axId = 200
    over.y_axis.crosses = "max"
    over.y_axis.delete = False
    if line_title:
        over.y_axis.title = line_title
    chart += over
    ws.add_chart(chart, anchor)
    return chart
