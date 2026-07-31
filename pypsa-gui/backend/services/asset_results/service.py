"""
Orchestration: pick the asset, slice the horizon, run the requested metrics,
reshape for the view mode. `compute.py` stays metric functions; this module
is the only place that knows about requests.
"""
from __future__ import annotations

from typing import Any

from services.pypsa_service import PyPSAService

from . import compute as C
from .applicability import resolve_category, resolve_metric
from .registry import (
    CATEGORIES,
    ALL_CLASSES,
    metrics_for,
)

VIEW_MODES = ("chronological", "duration", "monthly")


def list_assets(n) -> list[dict]:
    """
    Every selectable asset, transient rows removed — same filter as every
    other asset list, so `__voll_*` and `<name>@<year>` never appear.
    """
    out: list[dict] = []
    for cls in ALL_CLASSES:
        attr = C.attr_for(cls)
        df = getattr(n, attr, None)
        if df is None or df.empty:
            continue
        transient: set[str] = set()
        if PyPSAService.has_any_transient_rows():
            transient = PyPSAService.get_transient_rows(cls)
        for name in df.index:
            if name in transient:
                continue
            row = df.loc[name]
            out.append({
                "class": cls,
                "name": str(name),
                "carrier": str(row.get("carrier", "") or ""),
                "bus": str(row.get("bus", row.get("bus0", "")) or ""),
            })
    return out


def slice_snapshots(n, from_iso: str | None, to_iso: str | None, period):
    """Apply the Results shell's horizon filter + period strip to n.snapshots."""
    import pandas as pd

    sns = n.snapshots
    if isinstance(sns, pd.MultiIndex):
        if period is not None:
            keep = [s for s in sns if str(s[0]) == str(period)]
            sns = pd.MultiIndex.from_tuples(keep, names=sns.names) if keep else sns
            sns.name = "snapshot"
        stamps = [pd.Timestamp(s[1]).isoformat() for s in sns]
    else:
        stamps = [pd.Timestamp(s).isoformat() for s in sns]

    if from_iso or to_iso:
        keep_idx = [
            i for i, st in enumerate(stamps)
            if (not from_iso or st >= from_iso) and (not to_iso or st <= to_iso)
        ]
        sns = sns[keep_idx]
    return sns


def _stamps_and_periods(n, sns) -> tuple[list[str], list | None]:
    import pandas as pd

    if isinstance(sns, pd.MultiIndex):
        return ([pd.Timestamp(s[1]).isoformat() for s in sns],
                [s[0] for s in sns])
    return ([pd.Timestamp(s).isoformat() for s in sns], None)


def apply_view_mode(stamps, periods, series_map: dict, metrics: dict, mode: str) -> dict:
    """
    Reshape the chronological series into the requested view.

    Returns index / periods / pct_of_hours / columns / series. `columns`
    describes every emitted column (id, label, unit, metric_id, agg) so the
    frontend never has to infer a naming convention — the registry stays the
    only place that knows what a metric is called.
    """
    import math

    def col(mid: str, agg: str | None) -> dict:
        m = metrics[mid]
        suffix = {"mean": " (mean)", "max": " (max)", "energy": " (energy)"}
        return {
            "id": mid if agg is None else f"{mid}__{agg}",
            "label": m.label + ("" if agg is None else suffix[agg]),
            "unit": m.unit if agg != "energy" else f"{m.unit}h",
            "metric_id": mid,
            "agg": agg,
        }

    if mode == "duration":
        out_series: dict[str, list] = {}
        n_rows = 0
        for mid, vals in series_map.items():
            finite = sorted(
                (v for v in vals if v is not None and math.isfinite(v)),
                reverse=True,
            )
            out_series[mid] = finite
            n_rows = max(n_rows, len(finite))
        # Pad every series to the longest so the table stays rectangular.
        for mid in out_series:
            out_series[mid] += [None] * (n_rows - len(out_series[mid]))
        return {
            "index": [str(i + 1) for i in range(n_rows)],
            "periods": None,
            "pct_of_hours": [(i + 1) / n_rows for i in range(n_rows)] if n_rows else [],
            "columns": [col(mid, None) for mid in series_map],
            "series": out_series,
        }

    if mode == "monthly":
        months: list[str] = []
        buckets: dict[str, list[int]] = {}
        for i, st in enumerate(stamps):
            key = st[:7]
            if key not in buckets:
                buckets[key] = []
                months.append(key)
            buckets[key].append(i)
        columns: list[dict] = []
        out_series = {}
        for mid, vals in series_map.items():
            for agg in ("mean", "max", "energy"):
                c = col(mid, agg)
                columns.append(c)
                acc = []
                for mth in months:
                    picked = [vals[i] for i in buckets[mth]
                              if vals[i] is not None and math.isfinite(vals[i])]
                    if not picked:
                        acc.append(None)
                    elif agg == "mean":
                        acc.append(sum(picked) / len(picked))
                    elif agg == "max":
                        acc.append(max(picked))
                    else:
                        acc.append(sum(picked))
                out_series[c["id"]] = acc
        return {"index": months, "periods": None, "pct_of_hours": None,
                "columns": columns, "series": out_series}

    return {
        "index": stamps,
        "periods": periods,
        "pct_of_hours": None,
        "columns": [col(mid, None) for mid in series_map],
        "series": series_map,
    }


def build_response(
    n, component_class: str, name: str, *, category: str,
    metric_ids: list[str], source: str, from_iso: str | None,
    to_iso: str | None, period, mode: str,
) -> dict:
    from services.serialization import clean_scalar

    from routers.simulation import _state_snapshot

    precond = C.preconditions(n, component_class, name)
    sns = slice_snapshots(n, from_iso, to_iso, period)
    stamps, periods = _stamps_and_periods(n, sns)

    categories = []
    for cid, label in CATEGORIES:
        st = resolve_category(cid, component_class, precond)
        categories.append({"id": cid, "label": label, **st.as_dict()})

    members = metrics_for(component_class, category)
    metric_rows, resolved = [], {}
    for m in members:
        st = resolve_metric(m, component_class, precond)
        resolved[m.id] = st
        row = {"id": m.id, "label": m.label, "unit": m.unit, "kind": m.kind,
               "origin": m.origin, **st.as_dict()}
        if m.formula:
            row["formula"] = m.formula
        metric_rows.append(row)

    # An explicit `metrics=` list narrows to that subset (e.g. a chart that
    # only wants "p"). With no explicit list, the feature's own contract —
    # "every applicable result shown" — means default to every `ok` member
    # of the requested category, not nothing. Without this fallback,
    # `GET .../gas?category=summary` (no `metrics=`) would return empty
    # `scalars`/`series` even though "params"/"identity" are always
    # computable, unconditional-precondition metrics of that category.
    wanted = (
        [mid for mid in metric_ids if mid in resolved and resolved[mid].status == "ok"]
        if metric_ids
        else [m.id for m in members if resolved[m.id].status == "ok"]
    )
    series_map: dict[str, list] = {}
    scalars: dict[str, Any] = {}
    by_id = {m.id: m for m in members}

    for mid in wanted:
        m = by_id[mid]
        ctx = C.build_ctx(
            n, component_class, name,
            source=(m.source_override or source), sns=sns,
        )
        try:
            value = m.compute(ctx)
        except Exception:
            value = None
        if value is None:
            continue
        if m.kind == "series":
            series_map[mid] = [clean_scalar(v) for v in list(value.values)]
        else:
            scalars[mid] = clean_scalar(value) if not isinstance(value, dict) \
                else {k: clean_scalar(v) for k, v in value.items()}

    shaped = apply_view_mode(stamps, periods, series_map, by_id, mode)
    state = _state_snapshot()
    ctx0 = C.build_ctx(n, component_class, name, source=source, sns=sns)

    return {
        "asset": {**C.summary_identity(ctx0), "params": C.summary_params(ctx0)},
        "solve": {
            "source": source,
            "objective": clean_scalar(state.get("objective")),
            "solve_time": clean_scalar(state.get("solve_time")),
            "condition": state.get("condition"),
        },
        "category": category,
        "mode": mode,
        "categories": categories,
        "metrics": metric_rows,
        "scalars": scalars,
        **shaped,
    }
