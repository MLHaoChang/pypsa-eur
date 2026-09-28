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
"""
from __future__ import annotations

import io
from typing import Any

# Reference palette instance (dataviz skill, references/palette.md):
_SERIES_BLUE = "#2a78d6"     # categorical slot 1 / sequential hue
_INK = "#0b0b0b"
_INK_MUTED = "#52514e"
_GRID = "#e6e5e1"
_SURFACE = "#fcfcfb"

MAX_BARS = 50  # the engine's own cap, MAX_EH_FMEA_TOP_N

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
    return f if f == f and f not in (float("inf"), float("-inf")) else None


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
        ax.set_xlabel("Criticality (€/yr)")
        # Same grouping the tables use, so a bar and its row read alike.
        ax.xaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.set_title("Residual failure modes by criticality", loc="left",
                     fontsize=10)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        ax.xaxis.grid(True)
        ax.yaxis.grid(False)
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
