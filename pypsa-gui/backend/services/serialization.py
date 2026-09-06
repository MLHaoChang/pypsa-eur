"""
Single home for the JSON serialisation boundary.

Every DataFrame / float that reaches a Starlette ``JSONResponse`` must pass
through here first. Starlette renders with ``json.dumps(..., allow_nan=False)``
— a single NaN / Inf anywhere in the payload raises ``ValueError`` which uvicorn
turns into a bare 500 with a 21-byte plain-text body (NOT a JSON ``{detail}``),
and the endpoint's own ``try/except`` can't catch it because the failure is in
the response-render phase AFTER the handler returns. Multi-period networks hit
this constantly: PyPSA's ``set_snapshots`` reindex fills new rows with each
attribute's default (``state_of_charge`` → NaN, ``p`` → 0.0), so some
``/results/*`` endpoints 500 and others don't on the same network.

This module is a LEAF — it imports only stdlib + pandas. It must never import
``routers.*`` or other ``services.*`` so anything can depend on it without a
cycle. Previously these helpers were re-implemented in ``routers/network.py``
(``_clean`` / ``df_to_json``) and ``routers/simulation.py`` (``_safe_values`` /
``_ts_payload`` / ``_safe_float``) and ``routers/projects.py`` (``_safe_float``);
collapsing them here keeps the "scrub before JSON" invariant greppable and
enforceable in one place.

NOTE: ``routers/network.py`` keeps a separate *vectorised* time-series
serialiser (numpy ``strftime`` + ``~np.isfinite`` masking) for the perf-critical
8760×100 ``/timeseries`` path. It is a single self-contained call site tuned for
that case; unifying it with ``ts_payload`` here (per-cell, ``isoformat``) is a
deliberate follow-up because the two differ in timestamp formatting (``strftime``
vs ``isoformat``) and merging changes the wire format — out of scope for the
behaviour-preserving extraction.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd


def clean_scalar(v: Any) -> Any:
    """Per-value scrub: non-finite float → None, Timestamp/date → ISO string."""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, bool):
        return v
    return v


def safe_float(v: Any, default: float | None = 0.0) -> float | None:
    """
    ``float(v)`` with non-finite (NaN/Inf/-Inf), None, and parse failures
    collapsed to ``default``.

    ``default`` is ``0.0`` for the cost/statistics accumulators (a missing
    number means "no contribution") and ``None`` for the LCOH/asset-economics
    paths (a missing number means "not computable" → renders as ``—``).
    """
    try:
        f = float(v) if v is not None else default
    except (TypeError, ValueError):
        return default
    if f is None:
        return default
    return f if math.isfinite(f) else default


def safe_values(df: pd.DataFrame) -> list[list]:
    """``df.values.tolist()`` with non-finite floats → None (NaN-safe for JSON)."""
    return [
        [(None if isinstance(v, float) and not math.isfinite(v) else v) for v in row]
        for row in df.values.tolist()
    ]


# ~10 bytes per JSON float was measured on a frame of the exact shape
# `ts_payload` emits: 5,256,000 values serialised to 52.6 MB. So this cap is
# roughly a 20 MB response — large enough that no legitimate UI request hits
# it, small enough that a hand-crafted one cannot ask the server to build
# something nobody can use.
MAX_RESPONSE_VALUES = 2_000_000


def slice_ts(
    df: pd.DataFrame,
    from_: int | None,
    to_: int | None,
    *,
    max_values: int = MAX_RESPONSE_VALUES,
) -> tuple[pd.DataFrame, dict]:
    """
    Positionally slice a time-series frame. Returns ``(sliced, range_meta)``.

    Bounds are INCLUSIVE and POSITIONAL — indices into the snapshot axis, not
    timestamps. Positional addressing is what lets one implementation serve
    both shapes: ``df.iloc[a:b]`` does not care whether the index is a flat
    DatetimeIndex or a ``(period, timestep)`` MultiIndex. A timestamp would be
    ambiguous on multi-period networks, which replicate ONE operational year
    under every investment period — the same trap CLAUDE.md documents against
    the Horizon filter.

    Clamping mirrors the frontend's own ``Math.max(0, Math.min(...))`` in
    ``shared.tsx::weightedSum``, so client and server agree by construction
    rather than by coincidence.

    ``from_ > to_`` yields an EMPTY frame rather than raising. The client
    already treats an inverted range as empty (``if (rFrom > rTo) return 0``),
    and a 400 would turn a benign transient UI state into an error toast. The
    returned meta states exactly what was served, so nothing is silent.

    ``complete`` is computed from what was SERVED, not what was ASKED: a
    request for row 99,999 of a 168-row series returns ``complete: True``,
    because the caller does hold every row. Reporting False there would make a
    consumer refuse to total data that is in fact whole.
    """
    total = len(df.index)
    if total == 0:
        return df, {"from": 0, "to": -1, "total": 0, "complete": True, "capped": False}

    lo = 0 if from_ is None else max(0, int(from_))
    hi = total - 1 if to_ is None else min(total - 1, int(to_))

    if lo > hi:
        return df.iloc[0:0], {
            "from": lo, "to": hi, "total": total, "complete": False, "capped": False,
        }

    capped = False
    width = max(1, len(df.columns))
    if (hi - lo + 1) * width > max_values:
        hi = min(lo + max(1, max_values // width) - 1, total - 1)
        capped = True

    return df.iloc[lo : hi + 1], {
        "from": lo,
        "to": hi,
        "total": total,
        "complete": lo == 0 and hi == total - 1,
        "capped": capped,
    }


def wants_slice(from_: int | None, to_: int | None) -> bool:
    """
    True when the caller actually asked for a `from`/`to` window.

    Deliberately `isinstance(x, int)`, not `x is None` / `x is not None`:
    when a route function decorated with a `Query(None, ...)` default is
    called directly as plain Python — bypassing FastAPI's request handling,
    which is the only place that actually resolves the query string into an
    int-or-None — the unset parameter's value is the `fastapi.params.Query`
    sentinel object itself, not `None`. `x is not None` is then True for a
    bound the caller never supplied, and `_slice_ts` chokes on a non-int
    bound. `tests/test_compare_cross_surface.py` calls
    `get_prices`/`get_curtailment` exactly this way; nothing calls one of
    the `_serve_ts`-backed endpoints directly today, but the guard is kept
    identical across every call site on purpose — a reader who "fixes" one
    back to `is not None` because it looks simpler must not have a second,
    correct form to copy from.

    When both bounds are absent, the caller should leave `range_meta` as
    `None` so the payload stays byte-identical to the pre-range response —
    that is what keeps every consumer that has not been converted to ask
    for a slice working unchanged.
    """
    return isinstance(from_, int) or isinstance(to_, int)


def df_to_json(df: pd.DataFrame) -> list[dict]:
    """Static component / statistics DataFrame → list of NaN-safe row dicts."""
    out = df.reset_index()
    # PyPSA's runtime state can lose a static DataFrame's `index.name`
    # during n.add / n.remove cycles (observed on Links after a vintage
    # expansion + restore round). `df.reset_index()` then produces an
    # unnamed column called "index" instead of the expected "name",
    # which makes every frontend that keys off `row.name` (canvas edge
    # IDs, query-cache lookups, asset tables) silently break — duplicate
    # `undefined` IDs across rows, React Flow only renders one. Normalise
    # at the API boundary so a transient PyPSA state quirk never leaks
    # into the JSON contract.
    if "index" in out.columns and "name" not in out.columns:
        out = out.rename(columns={"index": "name"})
    # Multi-period `n.statistics()` returns columns as a MultiIndex of
    # `(metric, period)` tuples (e.g. `('Operational Expenditure', 2026)`).
    # `to_dict(orient="records")` produces tuple-keyed dicts, which FastAPI's
    # `jsonable_encoder` then converts tuples → lists for JSON output. The
    # list-keyed dict is unhashable → 500 ("unhashable type: 'list'").
    # Flatten the MultiIndex to ' | '-joined strings here so callers receive
    # JSON-friendly keys. Plain-column DataFrames (component tables,
    # weightings, etc.) are unaffected — `isinstance` check is False there.
    if isinstance(out.columns, pd.MultiIndex):
        out = out.copy()
        out.columns = [
            ' | '.join(str(x) for x in col if str(x))
            for col in out.columns
        ]
    records = out.to_dict(orient="records")
    return [{k: clean_scalar(vv) for k, vv in row.items()} for row in records]


def ts_payload(
    df: pd.DataFrame,
    *,
    extra: dict | None = None,
    range_meta: dict | None = None,
) -> dict:
    """
    Standard `{index, columns, data}` time-series payload, NaN-safe.

    Multi-period: when df.index is a MultiIndex (period, timestep) we emit:
      • index    — timesteps only, as clean ISO strings
      • periods  — parallel array of period integers (same length as index)
      • columns, data — unchanged

    Rationale: pre-fix `str(tuple)` produced `"(2026, Timestamp('...'))"` which
    no chart tickFormatter / horizon string-comparator handled correctly,
    breaking every X-axis label on multi-period runs.

    ``range`` appears only when ``range_meta`` is supplied — the no-range call
    shape stays byte-identical to what existing consumers already depend on.
    """
    if isinstance(df.index, pd.MultiIndex):
        # Period level — coerce to int when possible (PyPSA's convention is
        # year-as-int), fall back to str for safety.
        period_vals = df.index.get_level_values(0)
        try:
            periods = [int(p) for p in period_vals]
        except (TypeError, ValueError):
            periods = [str(p) for p in period_vals]
        timesteps = df.index.get_level_values(1)
        idx = [
            ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            for ts in timesteps
        ]
        payload = {
            "index": idx,
            "periods": periods,
            "columns": df.columns.tolist(),
            "data": safe_values(df),
        }
    else:
        idx = [s.isoformat() if hasattr(s, "isoformat") else str(s) for s in df.index]
        payload = {
            "index": idx,
            "columns": df.columns.tolist(),
            "data": safe_values(df),
        }
    if extra:
        payload.update(extra)
    # AFTER `extra`, deliberately: `range` describes what was served and must
    # not be shadowed by an endpoint's own extra dict.
    if range_meta is not None:
        payload["range"] = range_meta
    return payload
