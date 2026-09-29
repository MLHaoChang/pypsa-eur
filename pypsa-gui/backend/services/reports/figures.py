"""
Report figures — matplotlib PNGs rendered on the backend (Agg).

Design rules (the ``dataviz`` skill, applied to a static print figure):
one axis per chart (a Pareto's cumulative share is annotated in ink, not put
on a second y-scale); magnitude is one sequential hue; text wears ink
colours, never the series colour; recessive grid and spines; the unit on
the axis label. Empty input returns ``None`` so the writer states the
absence in a sentence instead of embedding a blank axis.

Determinism: identical input → identical bytes (fixed rcParams, no
timestamp metadata), so re-exporting an unchanged report does not churn the
project directory.

The three figures share ``_style_axis`` so they read as one system: a
left-aligned title, top/right spines hidden, the grid only along the
magnitude axis, and thousands-grouped tick labels (the same grouping the
tables use, so a bar and its row read alike). Captions are the writer's
job — a figure never restates the ``period_basis`` or the shed-cost caveat.
"""
from __future__ import annotations

import io
import math
from typing import Any

# Reference palette instance (dataviz skill, references/palette.md):
_SERIES_BLUE = "#2a78d6"     # categorical slot 1 / sequential hue
_INK = "#0b0b0b"
_INK_MUTED = "#52514e"
_GRID = "#e6e5e1"
_SURFACE = "#fcfcfb"

MAX_BARS = 50  # the engine's own cap, MAX_EH_FMEA_TOP_N
MAX_CARRIERS = 30  # capacity mix: beyond this the tail folds into "other"

_RC = {
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.edgecolor": _GRID,
    "axes.labelcolor": _INK_MUTED,
    "axes.titlecolor": _INK,
    "xtick.color": _INK_MUTED,
    "ytick.color": _INK_MUTED,
    "axes.grid": True,
    "grid.color": _GRID,
    "grid.linewidth": 0.6,
    "axes.axisbelow": True,
    "figure.facecolor": _SURFACE,
    "axes.facecolor": _SURFACE,
    "savefig.facecolor": _SURFACE,
    "svg.hashsalt": "pypsa-studio-report",
    # Layout-affecting parameters pinned so two renders of one input agree
    # regardless of the caller's style sheet or a user matplotlibrc.
    "figure.dpi": 100,
    "savefig.dpi": 150,
    "figure.autolayout": False,
    "figure.constrained_layout.use": False,
    "axes.titlelocation": "left",
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "lines.linewidth": 1.5,
    "lines.markersize": 6,
    "text.hinting": "default",
    "text.hinting_factor": 8,
    "path.simplify": True,
    "path.simplify_threshold": 0.111111111111,
    "agg.path.chunksize": 0,
}


def _matplotlib():
    import matplotlib
    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt
    return matplotlib, plt


def _png(fig) -> bytes:
    _, plt = _matplotlib()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                metadata={"Software": None})
    plt.close(fig)
    return buf.getvalue()


def _finite(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _grouped(matplotlib):
    """Tick formatter with thousands grouping (``1,234,568``)."""
    return matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}")


def _style_axis(ax, matplotlib, *, title: str, xlabel: str, ylabel: str | None = None,
                grid: str = "x") -> None:
    """
    The shared look: left-aligned title, top/right spines hidden, the grid
    only along the magnitude axis (``grid`` is ``"x"`` or ``"y"``) with its
    tick labels thousands-grouped, the unit carried by the axis label.
    """
    ax.set_title(title, loc="left", fontsize=10)
    ax.set_xlabel(xlabel)
    if ylabel is not None:
        ax.set_ylabel(ylabel)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.xaxis.grid(grid == "x")
    ax.yaxis.grid(grid == "y")
    axis = ax.xaxis if grid == "x" else ax.yaxis
    axis.set_major_formatter(_grouped(matplotlib))


def fmea_pareto_png(top: list[dict] | None) -> bytes | None:
    """
    Criticality (€/yr) per ranked failure mode, most critical first, with
    the cumulative share of total criticality written above each bar.
    Rows without a finite criticality are dropped; none left → ``None``.
    """
    rows = []
    for r in (top or [])[:MAX_BARS]:
        crit = _finite((r or {}).get("criticality_eur_per_year"))
        if crit is None:
            continue
        label = str(r.get("name") or r.get("mode_id") or "?")
        cls = r.get("failure_class")
        rows.append((f"{label} ({cls})" if cls else label, crit))
    if not rows:
        return None
    total = sum(c for _, c in rows) or 1.0

    matplotlib, plt = _matplotlib()
    with matplotlib.rc_context(_RC):
        height = max(2.2, 0.32 * len(rows) + 1.2)
        fig, ax = plt.subplots(figsize=(6.5, height))
        labels = [l for l, _ in rows][::-1]
        values = [c for _, c in rows][::-1]
        ax.barh(labels, values, color=_SERIES_BLUE, height=0.62)
        _style_axis(ax, matplotlib, title="Residual failure modes by criticality",
                    xlabel="Criticality (€/yr)", grid="x")
        cumulative = 0.0
        shares = []
        for _, c in rows:
            cumulative += c
            shares.append(cumulative / total)
        xmax = max(values) if values else 1.0
        for y, (v, share) in enumerate(zip(values, shares[::-1])):
            ax.annotate(f"{share:.0%} cum.", xy=(v, y),
                        xytext=(4, 0), textcoords="offset points",
                        va="center", ha="left", fontsize=7.5, color=_INK_MUTED)
        ax.set_xlim(0, xmax * 1.22)
        return _png(fig)


def frontier_png(points: list[dict] | None, knee_index: int | None) -> bytes | None:
    """
    Total system cost (€) against the ENS target (‱ of demand) for every
    ε-constraint point. Points with ``status == "ok"`` and a finite cost are
    filled markers joined in x order; any other point is a hollow marker on
    the baseline at its target, annotated with its status word, so an
    infeasible target is visible rather than silently dropped. The knee
    (``knee_index`` into the original list) wears a ring and the label
    "knee". No plottable point → ``None``.
    """
    ok: list[tuple[int, float, float]] = []      # (original index, x, y)
    bad: list[tuple[float, str]] = []            # (x, status word)
    for i, p in enumerate(points or []):
        p = p or {}
        x = _finite(p.get("target_permyriad"))
        if x is None:
            continue
        status = str(p.get("status") or "unknown")
        cost = _finite((p.get("point") or {}).get("total_system_cost_eur"))
        if status == "ok" and cost is not None:
            ok.append((i, x, cost))
        else:
            bad.append((x, status))
    if not ok:
        return None
    ok.sort(key=lambda t: t[1])
    xs = [x for _, x, _ in ok]
    ys = [y for _, _, y in ok]
    knee = next(((x, y) for i, x, y in ok if i == knee_index), None)

    matplotlib, plt = _matplotlib()
    with matplotlib.rc_context(_RC):
        fig, ax = plt.subplots(figsize=(6.5, 3.6))
        ax.plot(xs, ys, color=_SERIES_BLUE, linewidth=2, marker="o",
                markersize=6, markerfacecolor=_SERIES_BLUE,
                markeredgecolor=_SURFACE, markeredgewidth=1, zorder=3)
        ymin, ymax = min(ys), max(ys)
        span = (ymax - ymin) or abs(ymax) or 1.0
        ax.set_ylim(ymin - 0.12 * span, ymax + 0.18 * span)
        all_x = xs + [x for x, _ in bad]
        xspan = (max(all_x) - min(all_x)) or max(all_x) or 1.0
        ax.set_xlim(min(all_x) - 0.08 * xspan, max(all_x) + 0.08 * xspan)
        baseline = ax.get_ylim()[0]
        for x, status in bad:
            ax.plot([x], [baseline], marker="o", markersize=6, linestyle="none",
                    markerfacecolor=_SURFACE, markeredgecolor=_INK_MUTED,
                    markeredgewidth=1.2, clip_on=False, zorder=4)
            ax.annotate(status, xy=(x, baseline), xytext=(0, 7),
                        textcoords="offset points", ha="center", va="bottom",
                        fontsize=7.5, color=_INK_MUTED)
        if knee is not None:
            ax.plot([knee[0]], [knee[1]], marker="o", markersize=13,
                    linestyle="none", markerfacecolor="none",
                    markeredgecolor=_INK, markeredgewidth=1.4, zorder=5)
            ax.annotate("knee", xy=knee, xytext=(9, 9), textcoords="offset points",
                        ha="left", va="bottom", fontsize=8, color=_INK)
        _style_axis(ax, matplotlib,
                    title="Cost of reliability (ε-constraint frontier)",
                    xlabel="ENS target (‱ of demand)",
                    ylabel="Total system cost (€)", grid="y")
        return _png(fig)


def _capacity_rows(by_carrier: dict[str, Any] | None) -> list[tuple[str, float]]:
    """
    Carriers with a positive, finite capacity, largest first; beyond
    ``MAX_CARRIERS`` the tail folds into one trailing ``"other"`` row.
    """
    rows = []
    for name, value in (by_carrier or {}).items():
        mw = _finite(value)
        if mw is None or mw <= 0:
            continue
        rows.append((str(name), mw))
    rows.sort(key=lambda t: (-t[1], t[0]))
    if len(rows) > MAX_CARRIERS:
        head, tail = rows[:MAX_CARRIERS], rows[MAX_CARRIERS:]
        rows = head + [("other", sum(mw for _, mw in tail))]
    return rows


def capacity_mix_png(by_carrier: dict[str, Any] | None) -> bytes | None:
    """
    Installed capacity (MW) per carrier as horizontal bars, largest on top.
    Zero, negative and non-finite entries are dropped; none left → ``None``.
    """
    rows = _capacity_rows(by_carrier)
    if not rows:
        return None
    matplotlib, plt = _matplotlib()
    with matplotlib.rc_context(_RC):
        height = max(2.0, 0.32 * len(rows) + 1.2)
        fig, ax = plt.subplots(figsize=(6.5, height))
        labels = [name for name, _ in rows][::-1]
        values = [mw for _, mw in rows][::-1]
        ax.barh(labels, values, color=_SERIES_BLUE, height=0.62)
        _style_axis(ax, matplotlib, title="Capacity by carrier",
                    xlabel="Installed capacity (MW)", grid="x")
        xmax = max(values)
        for y, v in enumerate(values):
            ax.annotate(f"{v:,.0f}", xy=(v, y), xytext=(4, 0),
                        textcoords="offset points", va="center", ha="left",
                        fontsize=7.5, color=_INK_MUTED)
        ax.set_xlim(0, xmax * 1.18)
        return _png(fig)
