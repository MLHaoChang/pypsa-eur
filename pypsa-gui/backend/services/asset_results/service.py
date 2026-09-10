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
    CATEGORY_LABELS,
    ALL_CLASSES,
    headline_ids,
    metric_for,
    metrics_for,
)

VIEW_MODES = ("chronological", "duration", "monthly")


def list_assets(n) -> list[dict]:
    """
    Every selectable asset, transient rows removed — same filter as every
    other asset list, so `__voll_*` (see services/adequacy/slack.py) and
    `<name>@<year>` never appear.
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
            # Positional indexing, NOT MultiIndex.from_tuples: an unmatched
            # period must yield an EMPTY index, not the full horizon. Falling
            # back to the unfiltered set would silently report every snapshot
            # as belonging to a period the network does not have.
            keep = [i for i, s in enumerate(sns) if str(s[0]) == str(period)]
            sns = sns[keep]
        stamps = [pd.Timestamp(s[1]).isoformat() for s in sns]
    else:
        stamps = [pd.Timestamp(s).isoformat() for s in sns]

    if from_iso or to_iso:
        keep_idx = [
            i for i, st in enumerate(stamps)
            if (not from_iso or st >= from_iso) and (not to_iso or st <= to_iso)
        ]
        sns = sns[keep_idx]
    # Positional indexing drops a MultiIndex's OVERALL `.name` (distinct from
    # its per-level `.names`). This repo has a documented failure class where
    # that loss surfaces much later as an xarray `dim_0` error, so restore it
    # unconditionally rather than only on the period branch.
    if isinstance(sns, pd.MultiIndex):
        sns.name = "snapshot"
    return sns


def _stamps_and_periods(n, sns) -> tuple[list[str], list | None]:
    import pandas as pd

    if isinstance(sns, pd.MultiIndex):
        return ([pd.Timestamp(s[1]).isoformat() for s in sns],
                [s[0] for s in sns])
    return ([pd.Timestamp(s).isoformat() for s in sns], None)


def apply_view_mode(
    stamps, periods, series_map: dict, metrics: dict, mode: str,
    weights: list[float] | None = None,
) -> dict:
    """
    Reshape the chronological series into the requested view.

    Returns index / periods / pct_of_hours / columns / series. `columns`
    describes every emitted column (id, label, unit, metric_id, agg) so the
    frontend never has to infer a naming convention — the registry stays the
    only place that knows what a metric is called.

    `weights` is the snapshot-weighting vector aligned to `stamps` (same basis
    as `Ctx.weights`, i.e. the `generators` column — matching every series
    metric this shapes, none of which are cost-weighted). Only the monthly
    view's "energy" aggregate uses it; the other modes ignore it.
    """
    import math

    # Power units only — appending "h" to `pu`, `EUR/MWh`, `t/h` etc. produces
    # nonsense units like "puh" / "EUR/MWhh". MW·h = MWh is the one case where
    # the concatenation is actually correct.
    _POWER_UNITS = {"MW", "MVAr"}

    def col(mid: str, agg: str | None) -> dict:
        m = metrics[mid]
        suffix = {"mean": " (mean)", "max": " (max)", "energy": " (energy)"}
        if agg == "energy" and m.unit in _POWER_UNITS:
            unit = f"{m.unit}h"
        else:
            unit = m.unit
        return {
            "id": mid if agg is None else f"{mid}__{agg}",
            "label": m.label + ("" if agg is None else suffix[agg]),
            "unit": unit,
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
        # PyPSA replicates ONE operational year under every investment period,
        # so a bare `st[:7]` bucket key (e.g. "2025-01") collects rows from
        # EVERY period into one bucket on a multi-period network — January
        # 2026 and January 2031 both carry the timestep-year prefix "2025".
        # Qualify the bucket key with the period (when there is one) so
        # periods never merge; `bucket_periods` carries the period each
        # bucket actually belongs to through to the response instead of
        # nulling it out. Flat-network behaviour (no periods) is unchanged:
        # bucket on `st[:7]` alone, `periods: None`.
        keys: list[str] = []
        months: list[str] = []
        bucket_periods: list | None = [] if periods is not None else None
        buckets: dict[str, list[int]] = {}
        for i, st in enumerate(stamps):
            mth = st[:7]
            key = f"{periods[i]}|{mth}" if periods is not None else mth
            if key not in buckets:
                buckets[key] = []
                keys.append(key)
                months.append(mth)
                if periods is not None:
                    bucket_periods.append(periods[i])
            buckets[key].append(i)
        columns: list[dict] = []
        out_series = {}
        for mid, vals in series_map.items():
            for agg in ("mean", "max", "energy"):
                c = col(mid, agg)
                columns.append(c)
                acc = []
                for key in keys:
                    idxs = buckets[key]
                    picked = [vals[i] for i in idxs
                              if vals[i] is not None and math.isfinite(vals[i])]
                    if not picked:
                        acc.append(None)
                    elif agg == "mean":
                        acc.append(sum(picked) / len(picked))
                    elif agg == "max":
                        acc.append(max(picked))
                    else:
                        # "energy" must be the snapshot-weighted sum, not a
                        # raw sum of raw series values, to match the "MWh"
                        # unit the column is labelled with. `weights` is
                        # None only when the caller has no context to supply
                        # one (defensive default) — fall back to an
                        # unweighted sum rather than raising, since a raw
                        # sum was the pre-existing (if wrong) behaviour.
                        if weights is None:
                            acc.append(sum(picked))
                        else:
                            acc.append(sum(
                                vals[i] * weights[i] for i in idxs
                                if vals[i] is not None and math.isfinite(vals[i])
                            ))
                out_series[c["id"]] = acc
        return {"index": months, "periods": bucket_periods, "pct_of_hours": None,
                "columns": columns, "series": out_series}

    return {
        "index": stamps,
        "periods": periods,
        "pct_of_hours": None,
        "columns": [col(mid, None) for mid in series_map],
        "series": series_map,
    }


def _compute_one(n, component_class: str, name: str, metric, *, source: str, sns):
    """
    Run a single metric and return a JSON-ready value, or None.

    Shares `build_response`'s contract: the metric's own `source_override`
    beats the panel's lopf/ac_pf toggle, a raising compute is a None rather
    than a 500, and a Series comes back as a plain list.
    """
    from services.serialization import clean_scalar

    ctx = C.build_ctx(
        n, component_class, name,
        source=(metric.source_override or source), sns=sns,
    )
    try:
        value = metric.compute(ctx)
    except Exception:
        return None
    if value is None:
        return None
    if metric.kind == "series":
        return [clean_scalar(v) for v in list(value.values)]
    if isinstance(value, dict):
        return {k: clean_scalar(v) for k, v in value.items()}
    return clean_scalar(value)


def build_headline(n, component_class: str, name: str, *, precond: dict,
                   source: str, sns) -> list[dict]:
    """
    The Summary tab's aggregated KPIs, lifted from the OTHER categories.

    Ids come from `registry.HEADLINE`; everything else — label, unit,
    formula, preconditions — is read back off the same registry entry the
    owning category uses, so a headline can never drift from the metric it
    mirrors. Blocked and n/a headlines are returned WITH their reason rather
    than dropped: "Capture price — needs a solve" is information, and a
    summary that silently omits half its rows on an unsolved network reads as
    if those results do not exist.

    Scalar-only. A headline is a single number a user reads at a glance; the
    series live one click away in their own category.
    """
    out: list[dict] = []
    for mid in headline_ids(component_class):
        m = metric_for(component_class, mid)
        if m is None or m.kind != "scalar":
            continue
        st = resolve_metric(m, component_class, precond)
        row = {
            "id": m.id,
            "label": m.label,
            "unit": m.unit,
            "category": m.category,
            "category_label": CATEGORY_LABELS.get(m.category, m.category),
            "origin": m.origin,
            **st.as_dict(),
        }
        if m.formula:
            row["formula"] = m.formula
        if st.status == "ok":
            value = _compute_one(n, component_class, name, m,
                                 source=source, sns=sns)
            if value is None:
                # Same downgrade the per-category path applies: `ok` means
                # every precondition passed, which does not guarantee the
                # solver actually wrote the column.
                row["status"] = "blocked"
                row["reason"] = "not produced by this solve"
                row.pop("remedy", None)
            else:
                row["value"] = value
        out.append(row)
    return out


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
    rows_by_id: dict[str, dict] = {}
    for m in members:
        st = resolve_metric(m, component_class, precond)
        resolved[m.id] = st
        row = {"id": m.id, "label": m.label, "unit": m.unit, "kind": m.kind,
               "origin": m.origin, **st.as_dict()}
        if m.formula:
            row["formula"] = m.formula
        metric_rows.append(row)
        rows_by_id[m.id] = row

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
            # A metric can resolve `ok` (every PRECONDITION is met) and still
            # compute to nothing — e.g. mu_upper/mu_lower on a non-extendable,
            # non-committable generator: PyPSA enforces that bound as a
            # variable bound rather than a linear constraint, so no dual
            # column exists at all, and `_duals_status` only checks
            # `buses_t.marginal_price`. Without this, the row stays `ok`,
            # never appears in series/scalars, and the frontend shows a
            # ticked checkbox with an empty chart and no explanation — a
            # fourth, unnamed state on top of ok/blocked/na. Downgrade the
            # row in place so the checklist tells the truth.
            row = rows_by_id[mid]
            row["status"] = "blocked"
            row["reason"] = "not produced by this solve"
            row.pop("remedy", None)
            continue
        if m.kind == "series":
            series_map[mid] = [clean_scalar(v) for v in list(value.values)]
        else:
            scalars[mid] = clean_scalar(value) if not isinstance(value, dict) \
                else {k: clean_scalar(v) for k, v in value.items()}

    # Monthly "energy" needs a weighting vector — the same `generators` basis
    # every series metric here already uses via `Ctx.weights` (cost-weighted
    # scalars like revenue/VOM are never `kind="series"` for Generator, so a
    # single weights vector is correct for every series this shapes). Build
    # one `Ctx` for the un-overridden `source` to get it; cheap (no compute
    # call), and reused below for the response's `asset` field too.
    ctx0 = C.build_ctx(n, component_class, name, source=source, sns=sns)
    weights = [float(w) for w in ctx0.weights.values]
    shaped = apply_view_mode(stamps, periods, series_map, by_id, mode, weights)
    state = _state_snapshot()

    # Headline KPIs are computed only for the Summary tab. They reach across
    # every other category, so building them on all eight requests would run
    # the same ~8 metrics seven extra times for a payload nobody reads.
    headline = (
        build_headline(n, component_class, name, precond=precond,
                       source=source, sns=sns)
        if category == "summary" else []
    )

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
        "headline": headline,
        **shaped,
    }
