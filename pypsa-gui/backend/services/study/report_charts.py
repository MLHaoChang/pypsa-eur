"""
The decision report's charts as PNG bytes (plan S7, review v1 N5).

Rendered server-side from matplotlib ``Agg`` figures — no SVG rasterising —
from the SAME data the report's tables carry (``DecisionReport.sections``
payloads), so a chart never shows a number the tables do not:

* :func:`waterfall` — the value streams (``drivers`` payload), each bar a
  stream's annual value, ending at their sum (the annual saving);
* :func:`tornado` — the fixed-size tornado's battery NPV at each driver's
  low and high value around the centre;
* :func:`cash_flow` — the named option's net cash flow per year, with the
  cumulative discounted cash flow on the SAME axis (one currency scale).

Each returns ``None`` when there is nothing to draw; the renderers then show
the table alone. Colours follow the reference palette of the dataviz method:
polarity blue/red for signed bars, a fixed categorical order for the two
tornado bounds, recessive grid and axes.
"""
from __future__ import annotations

import io
import textwrap
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["cash_flow", "render_all", "tornado", "waterfall"]

_POS = "#2a78d6"      # polarity: gain
_NEG = "#e34948"      # polarity: loss
_TOTAL = "#52514e"    # the sum bar (text-secondary ink, not a series hue)
_LOW = "#2a78d6"      # categorical slot 1
_HIGH = "#eb6834"     # categorical slot 2
_INK = "#0b0b0b"
_MUTED = "#52514e"
_GRID = "#e4e3df"
_SURFACE = "#fcfcfb"


def _figure(width: float = 7.0, height: float = 3.4):
    import matplotlib

    matplotlib.use("Agg", force=True)
    from matplotlib.figure import Figure

    fig = Figure(figsize=(width, height), dpi=110, facecolor=_SURFACE)
    ax = fig.add_subplot(1, 1, 1)
    ax.set_facecolor(_SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(_GRID)
    ax.tick_params(colors=_MUTED, labelsize=8)
    ax.grid(axis="y", color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax


def _wrap(label: str, width: int) -> str:
    return "\n".join(textwrap.wrap(str(label), width)) or str(label)


def _title(fig, title: str) -> None:
    fig.suptitle(title, x=0.01, ha="left", fontsize=10, color=_INK)


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor=_SURFACE)
    return buf.getvalue()


def _money_axis(ax, unit: str) -> None:
    from matplotlib.ticker import FuncFormatter

    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:,.0f}"))
    ax.set_ylabel(unit, color=_MUTED, fontsize=8)


def waterfall(streams: Sequence[Mapping[str, Any]], *, title: str, unit: str = "EUR/yr") -> bytes | None:
    """Stream bars stepping from zero to their sum; None without a value."""
    rows = [(s.get("label") or s.get("key"), s.get("annual_value")) for s in streams]
    rows = [(label, float(v)) for label, v in rows if v is not None]
    if not rows:
        return None
    fig, ax = _figure()
    start = 0.0
    for i, (_label, v) in enumerate(rows):
        ax.bar(i, v, bottom=start, width=0.6, color=_POS if v >= 0 else _NEG,
               edgecolor=_SURFACE, linewidth=2)
        start += v
    ax.bar(len(rows), start, width=0.6, color=_TOTAL, edgecolor=_SURFACE, linewidth=2)
    ax.axhline(0.0, color=_MUTED, linewidth=0.8)
    ax.set_xticks(range(len(rows) + 1))
    ax.set_xticklabels([_wrap(label, 14) for label, _v in rows] + ["Total"], fontsize=8,
                       color=_INK)
    _money_axis(ax, unit)
    _title(fig, title)
    return _png(fig)


def tornado(rows: Sequence[Mapping[str, Any]], centre: float | None, *, title: str,
            unit: str = "EUR") -> bytes | None:
    """Horizontal bars from the centre to each bound's battery NPV."""
    bars = [(r.get("label") or r.get("key"), r.get("npv_low"), r.get("npv_high")) for r in rows]
    bars = [b for b in bars if b[1] is not None or b[2] is not None]
    if not bars or centre is None:
        return None
    fig, ax = _figure(height=0.6 * len(bars) + 1.4)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=_GRID, linewidth=0.8)
    for i, (_label, lo, hi) in enumerate(reversed(bars)):
        if lo is not None:
            ax.barh(i, float(lo) - centre, left=centre, height=0.55, color=_LOW,
                    edgecolor=_SURFACE, linewidth=2, label="Low value" if i == 0 else None)
        if hi is not None:
            ax.barh(i, float(hi) - centre, left=centre, height=0.55, color=_HIGH,
                    edgecolor=_SURFACE, linewidth=2, label="High value" if i == 0 else None)
    ax.axvline(centre, color=_INK, linewidth=1.0)
    ax.axvline(0.0, color=_NEG, linewidth=0.8, linestyle="--")
    ax.set_yticks(range(len(bars)))
    ax.set_yticklabels([_wrap(label, 30) for label, _l, _h in reversed(bars)], fontsize=8,
                       color=_INK)
    from matplotlib.ticker import FuncFormatter

    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:,.0f}"))
    ax.set_xlabel(f"Battery NPV ({unit})", color=_MUTED, fontsize=8)
    ax.legend(fontsize=8, frameon=False, loc="best")
    _title(fig, title)
    return _png(fig)


def cash_flow(years: Sequence[Mapping[str, Any]], *, title: str, unit: str = "EUR") -> bytes | None:
    """Net cash flow bars per year and the cumulative discounted line, one axis."""
    pts = [(y.get("year"), y.get("net_cash_flow"), y.get("cumulative_discounted")) for y in years]
    pts = [p for p in pts if p[0] is not None and p[1] is not None]
    if not pts:
        return None
    fig, ax = _figure()
    xs = [int(p[0]) for p in pts]
    net = [float(p[1]) for p in pts]
    ax.bar(xs, net, width=0.7, color=[_POS if v >= 0 else _NEG for v in net],
           edgecolor=_SURFACE, linewidth=1)
    cum = [(x, float(c)) for x, (_y, _n, c) in zip(xs, pts, strict=True) if c is not None]
    if cum:
        ax.plot([c[0] for c in cum], [c[1] for c in cum], color=_INK, linewidth=2)
    ax.axhline(0.0, color=_MUTED, linewidth=0.8)
    ax.set_xlabel("Year", color=_MUTED, fontsize=8)
    _money_axis(ax, unit)
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    handles = [Patch(color=_POS, label="Net cash flow, inflow"),
               Patch(color=_NEG, label="Net cash flow, outflow")]
    if cum:
        handles.append(Line2D([0], [0], color=_INK, linewidth=2, label="Cumulative discounted"))
    ax.legend(handles=handles, fontsize=8, frameon=False)
    _title(fig, title)
    return _png(fig)


def render_all(report) -> dict[str, bytes]:
    """Every chart the report's sections reference (``figures``), by id."""
    out: dict[str, bytes] = {}
    sections = report.sections
    drivers = sections.get("drivers")
    if drivers is not None and drivers.payload:
        p = drivers.payload
        from services.study.report import money_unit

        png = waterfall(p.get("value_streams") or [], title=p.get("value_streams_label") or "",
                        unit=money_unit(report, per_year=True))
        if png is not None:
            out["value_stream_waterfall"] = png
        png = tornado(p.get("tornado") or [], p.get("tornado_centre"),
                      title="Battery NPV at each driver's low and high value, sizes fixed",
                      unit=money_unit(report))
        if png is not None:
            out["tornado"] = png
    econ = sections.get("economics")
    if econ is not None and econ.payload:
        from services.study.report import money_unit

        png = cash_flow(econ.payload.get("cash_flow") or [],
                        title="Net cash flow per year and cumulative discounted cash flow",
                        unit=money_unit(report))
        if png is not None:
            out["cash_flow"] = png
    return out
