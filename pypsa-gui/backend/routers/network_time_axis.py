"""
The network's TIME AXIS: snapshots, investment periods, and per-component
time series.

Split out of `routers/network.py` in Phase 5. Phase 4 took the pure helper
clusters out and deliberately left the ~80 CRUD routes, on the grounds that they
are individually short. Measuring what remained showed the depth was never in
the CRUD:

    these 15 routes          1,028 lines  (45% of the file's function content)
    storage_units / stores / shunt_impedances
                             4 routes each, in 8 lines each

So this moves the fifteen rather than the eighty. Splitting two-line factory
calls into per-component modules would have added files without removing
complexity.

The cut is clean because these routes reference only three module-level names
from the router they came from: `router` (this module has its own, included
back), `_ATTR_TO_CLASS` (used by nothing else, so it came along), and
`filter_transient_names` (shared, so it moved to `services/transient_rows.py`
where both can import it without a cycle).

`routers/network.py` re-exports every handler here, because
`services/chat_tools.py` imports fourteen of them BY NAME and calls them
in-process as plain functions rather than over HTTP. `tests/test_network_time_axis_surface.py`
pins that.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from models.schemas import (
    InvestmentPeriods,
    SampleWeeksConfig,
    SnapshotConfig,
)
from services import change_log_service
from services.transient_rows import filter_transient_names
from services.pypsa_service import PyPSAService
from services.serialization import df_to_json
from services.upload_guard import read_capped
from services.network_geometry import (  # noqa: F401
    _EARTH_KM,
    _IMPEDANCE_FIELDS,
    _RecomputeResult,
    _bus_coord,
    _haversine_km,
    _impedance_preview,
    _line_haversine_km,
    _recompute_lengths_for_bus,
)
from services.transformer_rules import (  # noqa: F401
    _VNOM_TOL_KV,
    _enrich_transformer_voltage,
    _sanitise_transformer_type,
    _validate_transformer_voltage,
)
from services.profile_shapes import (  # noqa: F401
    _CONVENTIONAL_KW,
    _DR_KW,
    _ELEC_CARRIERS,
    _H2_CARRIERS,
    _H2_CARRIERS_LOAD,
    _HEAT_CARRIERS,
    _RENEWABLE_KW,
    _double_peak_profile,
    _flat_cf_profile,
    _gen_category,
    _h2_load_profile,
    _heat_load_profile,
    _link_category,
    _load_section,
    _profile_meta_for,
    _shape_for_section,
    _solar_cf_profile,
    _template_snapshots,
    _wind_cf_profile,
)
from services.snapshot_index import (  # noqa: F401
    _build_period_multiindex,
)
from services.user_timeseries import (  # noqa: F401
    _TS_COMPONENTS,
    _annual_hourly_reference,
    _backup_network_ts_to_user_ts,
    _capture_snapshot_weights_per_timestep,
    _ensure_snapshots_cover_user_ts,
    _flatten_snapshot_state,
    _parse_upload,
    _reapply_snapshot_weights,
    _reapply_user_ts_to_network,
    _rebase_flat_user_ts,
    _restore_user_ts,
    _serialize_user_ts,
    _user_ts,
    _user_ts_delete_asset,
    _user_ts_extent,
    _user_ts_lock,
    _user_ts_rename_asset,
    _reject_nonfinite_timeseries,
)

from services.transient_rows import filter_transient_names

# This module's own router; `routers/network.py` includes it, so every path
# below is served exactly where it was before the split.
router = APIRouter()

# `filter_transient_names` under the name the moved bodies already used.
_filter_transient_names = filter_transient_names


# Map PyPSA's lowercase-plural DataFrame attribute names to the singular
# PascalCase class names used as keys in the transient-row registry.
# Kept as a module-level constant so endpoints that only know the attr
# form (e.g. /timeseries iterates "generators", "loads", …) can resolve
# to the registry's class key with a single lookup.
_ATTR_TO_CLASS: dict[str, str] = {
    "buses":         "Bus",
    "carriers":      "Carrier",
    "generators":    "Generator",
    "loads":         "Load",
    "lines":         "Line",
    "links":         "Link",
    "storage_units": "StorageUnit",
    "stores":        "Store",
    "transformers":  "Transformer",
}
@router.get("/snapshots")
def get_snapshots():
    n = PyPSAService.get_network()
    sns = n.snapshots
    # Multi-period: emit ISO timestep strings + a parallel `periods` array,
    # same convention as /results/* TS payloads. Pre-fix the fallback
    # str(tuple) produced "(2026, Timestamp('...'))" which broke every
    # consumer doing indexOf / string-compare on the array.
    # Extent of uploaded time series — lets the Model Horizon page default its
    # snapshot range to the data the user actually uploaded. `can_sample_weeks`
    # gates the representative-week sampler (needs a full-year hourly profile).
    ts_start, ts_end = _user_ts_extent()
    can_sample_weeks = _annual_hourly_reference()[0] is not None
    if isinstance(sns, pd.MultiIndex):
        try:
            periods = [int(p) for p in sns.get_level_values(0)]
        except (TypeError, ValueError):
            periods = [str(p) for p in sns.get_level_values(0)]
        timesteps = sns.get_level_values(1)
        snaps = [
            ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            for ts in timesteps
        ]
        weightings = df_to_json(n.snapshot_weightings) if not n.snapshot_weightings.empty else []
        return {
            "count": len(sns),
            "snapshots": snaps,
            "periods": periods,
            "weightings": weightings,
            "ts_start": ts_start,
            "ts_end": ts_end,
            "can_sample_weeks": can_sample_weeks,
        }
    snaps = [s.isoformat() if hasattr(s, "isoformat") else str(s) for s in sns]
    weightings = df_to_json(n.snapshot_weightings) if not n.snapshot_weightings.empty else []
    return {
        "count": len(sns), "snapshots": snaps, "weightings": weightings,
        "ts_start": ts_start, "ts_end": ts_end,
        "can_sample_weeks": can_sample_weeks,
    }
@router.get("/snapshots/weightings.csv")
def download_snapshot_weightings_csv():
    """
    Stream `n.snapshot_weightings` as a CSV file.

    Format — one row per snapshot, columns:
      • ``snapshot`` — ISO timestamp for flat networks; ``period|iso`` (e.g.
        ``2030|2024-01-01T00:00:00``) for MultiIndex networks. The pipe
        separator avoids datetime-parsing ambiguity inside Excel.
      • ``objective``, ``generators``, ``stores`` — float weights.

    The same shape is accepted by ``POST /snapshots/weightings.csv``.
    """
    import csv
    import io

    import pandas as pd
    from fastapi.responses import StreamingResponse

    n = PyPSAService.get_network()
    df = n.snapshot_weightings
    if df.empty:
        raise HTTPException(400, "Network has no snapshots yet.")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["snapshot", *df.columns])
    is_multi = isinstance(df.index, pd.MultiIndex)
    for idx, row in df.iterrows():
        if is_multi:
            period, ts = idx
            iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            key = f"{int(period)}|{iso}"
        else:
            key = idx.isoformat() if hasattr(idx, "isoformat") else str(idx)
        w.writerow([key, *[float(row[c]) for c in df.columns]])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="snapshot_weightings.csv"'},
    )
@router.post("/snapshots/weightings.csv")
async def upload_snapshot_weightings_csv(file: UploadFile = File(...)):
    """
    Replace `n.snapshot_weightings` from an uploaded CSV.

    CSV must have a ``snapshot`` column plus at least one of
    ``objective`` / ``generators`` / ``stores``. Other columns are ignored.
    Rows whose ``snapshot`` key doesn't match an existing snapshot are
    skipped (not an error — partial uploads are common while debugging).
    Returns the number of rows applied so the UI can show a count.
    """
    import csv
    import io

    import pandas as pd

    content = await read_capped(file)
    try:
        text = content.decode("utf-8-sig")  # handles Excel-saved UTF-8 BOM
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "snapshot" not in (reader.fieldnames or []):
        raise HTTPException(
            400,
            "CSV must have a `snapshot` column (and one or more of "
            "`objective`, `generators`, `stores`).",
        )

    n = PyPSAService.get_network()
    df = n.snapshot_weightings
    if df.empty:
        raise HTTPException(
            400,
            "Network has no snapshots. Set the snapshot index first.",
        )
    is_multi = isinstance(df.index, pd.MultiIndex)

    # Build an index lookup so we can match either pipe-separated multi
    # keys ("2030|2024-01-01T00:00:00") or plain ISO timestamps.
    iso_to_idx: dict[str, object] = {}
    for idx in df.index:
        if is_multi:
            period, ts = idx
            iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            iso_to_idx[f"{int(period)}|{iso}"] = idx
            iso_to_idx[iso] = idx  # tolerant: accept ISO-only too
        else:
            iso = idx.isoformat() if hasattr(idx, "isoformat") else str(idx)
            iso_to_idx[iso] = idx

    cols = [c for c in ("objective", "generators", "stores") if c in (reader.fieldnames or [])]
    if not cols:
        raise HTTPException(
            400,
            "CSV has no weight columns. Include at least one of `objective`, "
            "`generators`, `stores`.",
        )

    # Two-pass validation + apply: parse + validate every cell BEFORE any
    # write, so a malformed row N doesn't leave rows 1..N-1 already
    # applied with no rollback. The previous single-pass loop raised
    # HTTPException mid-iteration after partially mutating
    # n.snapshot_weightings — symptom: a CSV with one bad cell would
    # leave the file partially applied and the user with no clean way
    # to retry without manually undoing the partial state.
    pending: list[tuple[object, str, float]] = []
    skipped = 0
    for row in reader:
        key = (row.get("snapshot") or "").strip()
        if not key or key not in iso_to_idx:
            skipped += 1
            continue
        idx = iso_to_idx[key]
        for c in cols:
            v = row.get(c, "").strip()
            if v == "":
                continue
            try:
                pending.append((idx, c, float(v)))
            except (TypeError, ValueError):
                raise HTTPException(
                    400,
                    f"Bad {c} value for {key}: {v!r} — no rows applied "
                    "(transaction rolled back). Fix the CSV and re-upload."
                )
    applied = 0
    with PyPSAService.get_lock():
        for idx, c, val in pending:
            df.at[idx, c] = val
            applied += 1
        change_log_service.log(
            "update", "Network", "snapshot_weightings",
            f"Uploaded snapshot_weightings.csv: {applied} cell(s) applied, "
            f"{skipped} row(s) skipped (no match).",
        )
    return {
        "applied": applied,
        "skipped": skipped,
        "columns": cols,
    }
@router.patch("/snapshots/weightings")
def update_snapshot_weightings(body: dict):
    """
    Update per-snapshot weights in `n.snapshot_weightings`.

    Body shape options (in priority order):

      • `{"all": <float>}` — set every snapshot weight (objective + generators +
        stores) to the same value. Canonical for the "representative day"
        workflow: 24 hourly snapshots representing 1 typical day in a 30-day
        month → set `{"all": 30}`.

      • `{"updates": {iso_or_idx: {objective?, generators?, stores?}, ...}}` —
        per-row override map. Keys may be ISO strings (e.g.
        `"2026-05-11T00:00:00"`) or integer indices into `n.snapshots`. Any
        column omitted is left unchanged.

    Returns the post-update weighting DataFrame so callers can verify.
    """
    import pandas as pd
    n = PyPSAService.get_network()
    if n.snapshot_weightings.empty:
        raise HTTPException(400, "Network has no snapshots. Set the snapshot index first via POST /snapshots.")
    with PyPSAService.get_lock():
        df = n.snapshot_weightings
        # Two-pass validate-then-apply: resolve + parse EVERYTHING first
        # (mutating nothing), raise on the first bad value, then write. The old
        # code wrote `df.at[idx,col]=float(raw)` mid-loop and raised 400 on a
        # bad cell at row N, leaving rows 0..N-1 already mutated with no
        # rollback — the user retries and the table is half-applied. Same
        # pattern as upload_snapshot_weightings_csv.
        all_val = body.get("all")
        all_float: float | None = None
        if all_val is not None:
            try:
                all_float = float(all_val)
            except (TypeError, ValueError):
                raise HTTPException(400, f"`all` must be a number, got {all_val!r}")
        updates = body.get("updates") or {}
        if not isinstance(updates, dict):
            raise HTTPException(400, "`updates` must be a dict keyed by snapshot.")
        # Build an iso → index lookup once so per-row updates are fast.
        # On multi-period networks, `df.index` is a MultiIndex of
        # `(period, ts)` tuples — neither has a top-level `.isoformat`
        # method, so the legacy `hasattr(s, 'isoformat')` branch fell
        # through to `str(tuple)` like "(2026, Timestamp('2026-05-11 …'))",
        # which a frontend ISO key never matches → every multi-period
        # weight PATCH 400'd with "Unknown snapshot key". Build two index
        # styles for multi-period: the bare-ts ISO and a `period|ts`
        # composite key. Flat networks keep the single-ISO behaviour.
        iso_to_idx: dict[str, object] = {}
        is_multi = isinstance(df.index, pd.MultiIndex)
        for s in df.index:
            if is_multi and isinstance(s, tuple) and len(s) == 2:
                period, ts = s
                ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
                # Period-qualified key: frontend can disambiguate identical
                # operational hours across periods (`"2026|2026-05-11T00:00:00"`).
                iso_to_idx[f"{period}|{ts_iso}"] = s
                # Bare ISO key — kept for the flat→multi migration case
                # where the frontend hasn't been updated yet. Last write
                # wins (later periods overwrite earlier in the bare-ISO
                # map), so flat-style PATCHes target the latest period's
                # row. Documented quirk; multi-period clients should use
                # the period-qualified form.
                iso_to_idx[ts_iso] = s
            else:
                iso_to_idx[s.isoformat() if hasattr(s, "isoformat") else str(s)] = s
        # Pass 1 — resolve + parse every cell into `pending`, raising before
        # any write.
        pending: list[tuple[object, str, float]] = []
        for key, vals in updates.items():
            if not isinstance(vals, dict):
                continue
            if key in iso_to_idx:
                idx = iso_to_idx[key]
            else:
                try:
                    pos = int(key)
                    idx = df.index[pos]
                except (TypeError, ValueError, IndexError):
                    raise HTTPException(400, f"Unknown snapshot key {key!r}")
            for col, raw in vals.items():
                if col not in df.columns:
                    continue
                try:
                    pending.append((idx, col, float(raw)))
                except (TypeError, ValueError):
                    raise HTTPException(400, f"Bad weight value for {key}/{col}: {raw!r}")
        # Pass 2 — everything validated; apply atomically (the `all` broadcast
        # first, then per-row overrides on top).
        if all_float is not None:
            for col in df.columns:
                df[col] = all_float
        for idx, col, val in pending:
            df.at[idx, col] = val
        applied = len(pending)
        change_log_service.log(
            "update", "Network", "snapshot_weightings",
            f"Updated snapshot weightings: all={all_val}, per-row updates={applied}",
        )
    return {
        "count": len(n.snapshot_weightings),
        "weightings": df_to_json(n.snapshot_weightings),
    }
@router.post("/snapshots")
def set_snapshots(config: SnapshotConfig):
    import pandas as pd
    n = PyPSAService.get_network()
    # Preserve existing time series BEFORE PyPSA reindexes them to the new snapshots.
    _backup_network_ts_to_user_ts(n)
    with PyPSAService.get_lock():
        sns = pd.date_range(config.start, config.end, freq=config.freq)
        kw: dict = {}
        if config.weightings is not None:
            kw["default_snapshot_weightings"] = config.weightings
        # Demote any lingering MultiIndex (multi-period toggled off without
        # rebuilding n.snapshots, or a stale _t / weightings frame) to flat
        # FIRST. A direct set_snapshots(flat DatetimeIndex) on MultiIndex state
        # trips pandas' "cannot include dtype 'M' in a buffer" reindex bug.
        # No-op when the network is already flat.
        _flatten_snapshot_state(n)
        n.set_snapshots(sns, **kw)
        # Re-apply full profiles (from _user_ts) aligned to the new snapshot range.
        _reapply_user_ts_to_network(n)
    change_log_service.log(
        "update", "Network", "snapshots",
        f"Updated snapshots: {config.start} → {config.end} at {config.freq} ({len(n.snapshots)} steps)",
    )
    return {"count": len(n.snapshots)}
@router.post("/snapshots/multi_period")
def set_multi_period_snapshots(body: dict):
    """
    Build a 2-level MultiIndex (period, timestep) snapshot index for
    multi-investment-period planning.

    Body shapes:

      • Same operational year per period (canonical):
        `{"periods": [2025, 2036, 2046], "start": "2025-01-01T00:00",
          "end": "2025-12-31T23:00", "freq": "h"}`
        — same (start,end,freq) DatetimeIndex replicated under each period.

      • Different operational range per period:
        `{"periods": [2025, 2036, 2046],
          "per_period": [{"start":..., "end":..., "freq":...}, ...]}`
        — one (start,end,freq) per period. List length must equal periods.

    Side effects:
      • Sets `n.investment_periods = periods`.
      • Backs up _t tables to _user_ts BEFORE reindex; re-applies after.
      • PyPSA initialises `investment_period_weightings` rows (years=1.0,
        objective=1.0) — the user can then tune via /investment_period_weightings.
    """
    import pandas as pd
    n = PyPSAService.get_network()

    periods = body.get("periods")
    if not isinstance(periods, list) or not periods:
        raise HTTPException(400, "`periods` must be a non-empty list of years.")
    try:
        periods_int = [int(p) for p in periods]
    except (TypeError, ValueError):
        raise HTTPException(400, "`periods` entries must be integers.")
    if len(set(periods_int)) != len(periods_int):
        raise HTTPException(400, "`periods` must be unique.")
    periods_sorted = sorted(periods_int)

    per_period = body.get("per_period")
    if per_period is not None:
        if not isinstance(per_period, list) or len(per_period) != len(periods_sorted):
            raise HTTPException(
                400, "`per_period` length must equal `periods` length.",
            )
        timestep_blocks = []
        for i, spec in enumerate(per_period):
            if not isinstance(spec, dict):
                raise HTTPException(400, f"per_period[{i}] must be an object.")
            try:
                idx = pd.date_range(
                    spec.get("start"), spec.get("end"),
                    freq=spec.get("freq", "h"),
                )
            except Exception as exc:
                raise HTTPException(
                    400, f"per_period[{i}] bad date range: {exc}",
                ) from exc
            if len(idx) == 0:
                raise HTTPException(400, f"per_period[{i}] produced an empty index.")
            timestep_blocks.append(idx)
    else:
        start = body.get("start")
        end = body.get("end")
        freq = body.get("freq", "h")
        if not start or not end:
            raise HTTPException(400, "Provide `start`+`end` (or `per_period`).")
        try:
            base_idx = pd.date_range(start, end, freq=freq)
        except Exception as exc:
            raise HTTPException(400, f"Bad date range: {exc}") from exc
        if len(base_idx) == 0:
            raise HTTPException(400, "Date range produced an empty index.")
        timestep_blocks = [base_idx for _ in periods_sorted]

    mi = _build_period_multiindex(periods_sorted, timestep_blocks)

    # Preserve existing time series BEFORE reindex.
    _backup_network_ts_to_user_ts(n)
    # Capture snapshot_weightings BEFORE set_snapshots resets them to 1.0
    # (PyPSA fills with default_snapshot_weightings on reindex). Without
    # this, the LP's n.nyears collapses to n_timesteps/8760 — undervaluing
    # CAPEX 50× on representative-week setups and producing renewable
    # over-build.
    captured_weights = _capture_snapshot_weights_per_timestep(n)
    with PyPSAService.get_lock():
        # n.set_snapshots is order-sensitive vs n.investment_periods: PyPSA's
        # multi-period machinery expects investment_periods to mirror the
        # MultiIndex's level-0 values. Set snapshots first, then sync periods.
        n.set_snapshots(mi)
        n.investment_periods = periods_sorted
        # Re-broadcast the captured weights under each new period.
        _reapply_snapshot_weights(n, captured_weights)
        # Re-apply user time series (handles MultiIndex via the level-1 path
        # added in _reapply_user_ts_to_network).
        _reapply_user_ts_to_network(n)

    change_log_service.log(
        "update", "Network", "snapshots",
        f"Built MultiIndex snapshots: {len(periods_sorted)} periods × "
        f"{[len(blk) for blk in timestep_blocks]} steps = {len(mi)} total",
    )
    return {
        "count": len(n.snapshots),
        "periods": periods_sorted,
        "rows_per_period": [len(blk) for blk in timestep_blocks],
    }
@router.post("/snapshots/sample_weeks")
def sample_representative_weeks(config: SampleWeeksConfig):
    """
    Build a representative-week snapshot index from an uploaded annual
    hourly profile.

    For each calendar month, ``n_weeks`` random ISO calendar weeks (Mon–Sun)
    are sampled; their 168 hourly timesteps form the new snapshot index, and
    ``snapshot_weightings`` is set so each sampled hour represents
    ``days_in_month / (weeks_sampled_for_that_month × 7)`` hours — the
    weighted total reconstructs the full year (Σ ≈ 8760 h).

    Requires a flat hourly series in ``_user_ts`` spanning all 12 calendar
    months of one year (validated via ``_annual_hourly_reference``). Works for
    flat AND multi-period networks: for multi-period the sampled timestep
    index is replicated under every investment period (same representative
    weeks per period — the canonical multi-period workflow).
    """
    import calendar as _calendar
    import datetime as _datetime

    import numpy as _np

    if config.n_weeks < 1 or config.n_weeks > 5:
        raise HTTPException(400, "n_weeks must be between 1 and 5.")

    idx, reason = _annual_hourly_reference()
    if idx is None:
        raise HTTPException(400, reason)

    ref_year = int(idx.year[0])
    idx_set = set(idx)

    # ── Candidate ISO weeks per calendar month ────────────────────────────
    # An ISO week qualifies only if its full Mon 00:00 … Sun 23:00 (168 h)
    # span is present in the uploaded index — this naturally drops the partial
    # weeks at the Jan / Dec year edges. Each qualifying week is assigned to
    # the month of its Thursday (the ISO-standard rule for which month/year a
    # week belongs to).
    iso = idx.isocalendar()
    unique_weeks = sorted(set(zip(
        iso["year"].astype(int).tolist(),
        iso["week"].astype(int).tolist(),
    )))
    month_candidates: dict[int, list] = {m: [] for m in range(1, 13)}
    for iy, iw in unique_weeks:
        try:
            monday = pd.Timestamp(_datetime.date.fromisocalendar(iy, iw, 1))
        except ValueError:
            continue
        span = pd.date_range(monday, periods=168, freq="h")
        if not set(span).issubset(idx_set):
            continue
        owning_month = (monday + pd.Timedelta(days=3)).month  # Thursday's month
        month_candidates[int(owning_month)].append((iy, iw, monday))

    empty_months = [m for m, c in month_candidates.items() if not c]
    if empty_months:
        raise HTTPException(
            400,
            f"Month(s) {empty_months} have no fully-contained ISO week in the "
            "uploaded profile — cannot sample. The profile must cover complete "
            "Mon–Sun weeks in every month.",
        )

    # ── Sample n_weeks per month ──────────────────────────────────────────
    rng = _np.random.default_rng(config.seed)
    chosen: list = []                       # (month, iso_year, iso_week, monday)
    weeks_per_month: dict[int, int] = {}
    for month in range(1, 13):
        cands = month_candidates[month]
        take = min(config.n_weeks, len(cands))
        weeks_per_month[month] = take
        for pi in sorted(rng.choice(len(cands), size=take, replace=False)):
            iy, iw, monday = cands[int(pi)]
            chosen.append((month, iy, iw, monday))

    # ── Assemble the sampled timestep index + per-snapshot weights ────────
    chosen.sort(key=lambda t: t[3])         # chronological by Monday
    sampled_blocks: list = []
    weight_blocks: list = []
    week_meta: list = []
    for month, iy, iw, monday in chosen:
        span = pd.date_range(monday, periods=168, freq="h")
        days_in_month = _calendar.monthrange(ref_year, month)[1]
        w = days_in_month / (weeks_per_month[month] * 7.0)
        sampled_blocks.append(span)
        weight_blocks.append(_np.full(168, w))
        week_meta.append({
            "month": month,
            "iso_year": iy,
            "iso_week": iw,
            "start": span[0].isoformat(),
            "end": span[-1].isoformat(),
            "weight": round(w, 4),
        })
    sampled_idx = pd.DatetimeIndex(_np.concatenate([b.values for b in sampled_blocks]))
    weights = _np.concatenate(weight_blocks)

    # ── Apply to the network ──────────────────────────────────────────────
    n = PyPSAService.get_network()
    is_multi = isinstance(n.snapshots, pd.MultiIndex)
    _backup_network_ts_to_user_ts(n)
    # Detect whether the user had non-default snapshot_weightings configured
    # BEFORE sampling. Representative-week sampling replaces the snapshot
    # index entirely — the prior weights have no meaningful mapping onto
    # the new sparse index, so they're necessarily overwritten with the
    # sampler-derived rep-week scaling. We can't preserve them safely, but
    # we CAN warn the user that their custom scaling is being discarded so
    # the silent-loss footgun documented for `set_snapshots(MultiIndex)` in
    # CLAUDE.md doesn't bite here. Compare every weight column to the PyPSA
    # default (1.0); anything else counts as "custom".
    _had_custom_weights = False
    try:
        sw_pre = n.snapshot_weightings
        if not sw_pre.empty:
            for _col in sw_pre.columns:
                if not (sw_pre[_col].astype(float) == 1.0).all():
                    _had_custom_weights = True
                    break
    except Exception:
        _had_custom_weights = False
    with PyPSAService.get_lock():
        if is_multi:
            periods = sorted(n.snapshots.get_level_values(0).unique().tolist())
            if not periods:
                raise HTTPException(
                    400, "Multi-period network has no investment periods.",
                )
            mi = _build_period_multiindex(periods, [sampled_idx] * len(periods))
            n.set_snapshots(mi)
            n.investment_periods = periods
            full_weights = _np.concatenate([weights for _ in periods])
        else:
            n.set_snapshots(sampled_idx)
            full_weights = weights
        # Re-apply uploaded profiles — sampled timesteps ⊂ uploaded index so
        # the reindex is exact (no all-NaN columns to skip).
        _reapply_user_ts_to_network(n)
        # Each sampled hour stands for days_in_month / (weeks × 7) hours. Set
        # all three weight columns so the LP objective, generator energy
        # balance and storage SoC equations scale consistently.
        for col in n.snapshot_weightings.columns:
            n.snapshot_weightings[col] = full_weights

    change_log_service.log(
        "update", "Network", "snapshots",
        f"Sampled {config.n_weeks} representative ISO week(s)/month → "
        f"{len(sampled_idx)} timestep(s)"
        + (f" × {len(periods)} period(s) = {len(n.snapshots)} snapshots"
           if is_multi else f" = {len(n.snapshots)} snapshots")
        + (" — NOTE: prior custom snapshot_weightings overwritten with "
           "rep-week scaling (each sampled hour now represents N hours)"
           if _had_custom_weights else ""),
    )
    return {
        "count": len(n.snapshots),
        "n_weeks": config.n_weeks,
        "seed": config.seed,
        "multi_period": is_multi,
        "timesteps_per_period": len(sampled_idx),
        "weeks": week_meta,
    }
@router.get("/investment_periods")
def get_investment_periods():
    n = PyPSAService.get_network()
    if n.investment_periods.empty:
        return {"periods": [], "weightings": []}
    return {
        "periods": n.investment_periods.tolist(),
        "weightings": df_to_json(n.investment_period_weightings),
    }
@router.post("/investment_periods")
def set_investment_periods(body: InvestmentPeriods):
    """
    Set the list of investment periods, rebuilding ``n.snapshots`` to match.

    PyPSA's ``n.investment_periods = […]`` setter doesn't auto-extend the
    snapshot MultiIndex when periods are added — it raises if the new
    periods aren't already present as level-0 values. To make the GUI's
    "add year" interaction work without forcing the user to manually rebuild
    snapshots, this endpoint handles the three transitions:

      1) Flat snapshots → MultiIndex: promote by replicating the existing
         operational DatetimeIndex under each requested period.
      2) MultiIndex → different MultiIndex (period added / removed): rebuild
         by replicating the FIRST existing period's operational range under
         every new period. User uploads survive via _user_ts.
      3) Empty `periods` on a MultiIndex: demote back to flat using the first
         period's operational range.
    """
    import pandas as pd
    n = PyPSAService.get_network()

    new_periods = sorted({int(p) for p in body.periods})

    with PyPSAService.get_lock():
        is_multi = isinstance(n.snapshots, pd.MultiIndex)

        if not new_periods:
            # Demote to flat snapshots using period-0's timesteps.
            if is_multi:
                # Capture profiles, collapse the MultiIndex → flat (handles the
                # pandas "cannot include dtype 'M' in a buffer" reindex bug),
                # then re-apply profiles aligned to the flat index.
                _backup_network_ts_to_user_ts(n)
                _flatten_snapshot_state(n)
                _reapply_user_ts_to_network(n)
            else:
                # Already flat. PyPSA accepts an empty pd.Index for this.
                n.investment_periods = pd.Index([], dtype="int64")
            return {"count": 0}

        # Determine base operational DatetimeIndex.
        if is_multi:
            existing_periods = sorted(n.snapshots.get_level_values(0).unique().tolist())
            first_p = existing_periods[0]
            base_idx = pd.DatetimeIndex(
                n.snapshots[n.snapshots.get_level_values(0) == first_p]
                .get_level_values(1),
            )
        else:
            existing_periods = []
            base_idx = pd.DatetimeIndex(n.snapshots)

        # Only rebuild snapshots if the period set actually changed.
        if existing_periods != new_periods:
            _backup_network_ts_to_user_ts(n)
            captured_weights = _capture_snapshot_weights_per_timestep(n)
            mi = _build_period_multiindex(new_periods, [base_idx] * len(new_periods))
            n.set_snapshots(mi)
            _reapply_snapshot_weights(n, captured_weights)
            _reapply_user_ts_to_network(n)

        # Set / re-set the periods list. PyPSA validates it matches level-0.
        n.investment_periods = new_periods

        if body.objective_weightings:
            n.investment_period_weightings["objective"] = body.objective_weightings
        if body.years_weightings:
            n.investment_period_weightings["years"] = body.years_weightings

    change_log_service.log(
        "update", "Network", "investment_periods",
        f"Set investment periods: {new_periods} "
        f"(operational range × {len(base_idx)} steps)",
    )
    return {"count": len(new_periods)}
@router.patch("/investment_period_weightings")
def update_investment_period_weightings(body: dict):
    """
    Update per-period weights in `n.investment_period_weightings`.

    Body shape options (combinable in one call):

      • `{"all_years": <float>}` — set every period's `years` column.
      • `{"all_objective": <float>}` — set every period's `objective` column.
      • `{"updates": {<period>: {"years"?, "objective"?}, ...}}` — per-period
        overrides. Keys are integer years (matching `n.investment_periods`);
        string keys are coerced.

    `years` represents the number of calendar years a period stands in for
    (PyPSA's discounting uses this as the integration window). `objective`
    is the discount/weight applied to that period's operational + capital
    contribution in the LP objective. Defaults are both 1.0 — set them
    explicitly when running multi-period.
    """
    n = PyPSAService.get_network()
    if n.investment_periods.empty:
        raise HTTPException(
            400,
            "Network has no investment periods. Configure them first via "
            "POST /network/investment_periods.",
        )
    df = n.investment_period_weightings
    with PyPSAService.get_lock():
        all_years = body.get("all_years")
        all_obj = body.get("all_objective")
        if all_years is not None:
            try:
                df["years"] = float(all_years)
            except (TypeError, ValueError):
                raise HTTPException(400, f"`all_years` must be a number, got {all_years!r}")
        if all_obj is not None:
            try:
                df["objective"] = float(all_obj)
            except (TypeError, ValueError):
                raise HTTPException(400, f"`all_objective` must be a number, got {all_obj!r}")
        updates = body.get("updates") or {}
        if not isinstance(updates, dict):
            raise HTTPException(400, "`updates` must be a dict keyed by period (year).")
        applied = 0
        for key, vals in updates.items():
            if not isinstance(vals, dict):
                continue
            try:
                period = int(key)
            except (TypeError, ValueError):
                raise HTTPException(400, f"Bad period key {key!r}")
            if period not in df.index:
                raise HTTPException(400, f"Unknown period {period}")
            for col in ("years", "objective"):
                if col in vals:
                    try:
                        df.at[period, col] = float(vals[col])
                        applied += 1
                    except (TypeError, ValueError):
                        raise HTTPException(
                            400, f"Bad {col} value for period {period}: {vals[col]!r}",
                        )
        change_log_service.log(
            "update", "Network", "investment_period_weightings",
            f"Updated period weightings: all_years={all_years}, "
            f"all_objective={all_obj}, per-row updates={applied}",
        )
    return {
        "periods": n.investment_periods.tolist(),
        "weightings": df_to_json(n.investment_period_weightings),
    }
@router.get("/timeseries")
def list_timeseries():
    n = PyPSAService.get_network()
    result = []
    for component in ["generators", "loads", "storage_units", "stores", "lines", "links"]:
        ts_store = getattr(n, f"{component}_t", None)
        if ts_store is None:
            continue
        comp_class = _ATTR_TO_CLASS.get(component, component)
        for attr in ts_store:
            df = ts_store[attr]
            if not df.empty:
                # Filter transient column names (vintage clones'
                # cloned-from-parent profiles, VOLL slack columns) so the
                # Time-Series tab list doesn't show LP scaffolding mid-solve.
                cols = _filter_transient_names(comp_class, df.columns.tolist())
                if not cols:
                    continue
                result.append({
                    "component": component,
                    "attribute": attr,
                    "column_count": len(cols),
                    "columns": cols,
                })
    return result
@router.get("/timeseries/{component}/{attribute}")
def get_timeseries(component: str, attribute: str, columns: str | None = None):
    """
    Return a time-series DataFrame as JSON.

    Optional ``columns`` query param: comma-separated list of column names to
    return (e.g. ``?columns=Wind+BE,Solar+BE``). Prefer user-uploaded data
    (from _user_ts) for any requested column that was uploaded by the user.
    """
    n = PyPSAService.get_network()
    ts_store = getattr(n, f"{component}_t", None)
    if ts_store is None:
        raise HTTPException(404, f"Component '{component}' not found")

    net_df = ts_store.get(attribute)
    wanted = [c.strip() for c in columns.split(",")] if columns else None

    if wanted:
        # For each requested column: prefer user-uploaded Series, fall back to network data
        series_list: list[pd.Series] = []
        for col in wanted:
            user_series = _user_ts.get((component, attribute, col))
            if user_series is not None:
                series_list.append(user_series.rename(col))
            elif net_df is not None and not net_df.empty and col in net_df.columns:
                series_list.append(net_df[col])
        df = pd.concat(series_list, axis=1) if series_list else pd.DataFrame()
    else:
        # No column filter — build from all user-uploaded columns for this
        # (component, attribute), then fall back to network data for the rest.
        prefix = (component, attribute)
        user_series_list = [
            s.rename(k[2]) for k, s in _user_ts.items() if k[:2] == prefix
        ]
        if user_series_list:
            df = pd.concat(user_series_list, axis=1)
        elif net_df is not None and not net_df.empty:
            df = net_df
        else:
            df = pd.DataFrame()

    if df is None or df.empty:
        return {"index": [], "columns": [], "data": []}

    # Drop transient columns (vintage clones' cloned profiles, VOLL slack
    # columns) so the Time-Series viewer doesn't render LP scaffolding
    # mid-solve. Apply AFTER user-supplied `wanted` resolution so an
    # explicit ?columns=foo@2026 request gets a clean empty payload
    # rather than a confusing partial frame.
    comp_class = _ATTR_TO_CLASS.get(component, component)
    keep_cols = _filter_transient_names(comp_class, list(df.columns))
    if len(keep_cols) != len(df.columns):
        df = df[keep_cols]
    if df.empty or len(df.columns) == 0:
        return {"index": [], "columns": [], "data": []}

    # Vectorised conversion. The previous nested Python loop with per-cell
    # isinstance/math.isfinite calls took 30+ s for tables in the 8760 × 100
    # range — long enough to block the event loop and time out the periodic
    # /network/meta poll. NumPy + DatetimeIndex.strftime do the same work in
    # well under a second.
    # MultiIndex on multi-period: emit ISO timesteps + parallel periods array,
    # same convention as `_ts_payload` and `/network/snapshots`. Without this,
    # `str(s)` on the tuple yields garbage like "(2026, Timestamp('...'))".
    periods: list | None = None
    if isinstance(df.index, pd.MultiIndex):
        try:
            periods = [int(p) for p in df.index.get_level_values(0)]
        except (TypeError, ValueError):
            periods = [str(p) for p in df.index.get_level_values(0)]
        timesteps = df.index.get_level_values(1)
        if isinstance(timesteps, pd.DatetimeIndex):
            idx = timesteps.strftime("%Y-%m-%dT%H:%M:%S").tolist()
        else:
            idx = [str(s) for s in timesteps]
    elif isinstance(df.index, pd.DatetimeIndex):
        idx = df.index.strftime("%Y-%m-%dT%H:%M:%S").tolist()
    else:
        idx = [str(s) for s in df.index]

    arr = df.to_numpy(dtype=float, copy=False)
    arr_obj = arr.astype(object)
    arr_obj[~np.isfinite(arr)] = None
    data = arr_obj.tolist()

    payload = {"index": idx, "columns": df.columns.tolist(), "data": data}
    if periods is not None:
        payload["periods"] = periods
    return payload
@router.put("/timeseries/{component}/{attribute}")
def set_timeseries(component: str, attribute: str, body: dict):
    import pandas as pd
    n = PyPSAService.get_network()
    ts_store = getattr(n, f"{component}_t", None)
    if ts_store is None:
        raise HTTPException(404)
    with PyPSAService.get_lock():
        idx = pd.DatetimeIndex(body.get("index", []))
        cols = body.get("columns", [])
        data = body.get("data", [])
        df = pd.DataFrame(data, index=idx, columns=cols)
        # Phase 12f: refuse before the store is touched, so a rejected write
        # leaves nothing behind.
        _reject_nonfinite_timeseries(df, component, attribute)
        ts_store[attribute] = df
        # Persist edits in _user_ts so they survive project reload.
        # Hold _user_ts_lock for the mutation — autosave's
        # _serialize_user_ts iterates the dict concurrently and would
        # raise RuntimeError("dictionary changed size during iteration")
        # without serialization. Inner lock; outer is the PyPSA lock.
        with _user_ts_lock:
            for col in df.columns:
                _user_ts[(component, attribute, col)] = df[col].copy()
    cols_preview = ", ".join(list(df.columns)[:3]) + ("…" if len(df.columns) > 3 else "")
    change_log_service.log(
        "timeseries", component.capitalize(), cols_preview,
        f"Edited {component}/{attribute} time series: "
        f"{len(df.columns)} column(s), {len(df)} rows",
    )
    return {"rows": len(df), "columns": len(df.columns)}
@router.post("/timeseries/upload")
async def upload_timeseries(
    component: str,
    attribute: str,
    file: UploadFile = File(...),
    period: int | None = None,
):
    """
    Upload a CSV of time-series data for one (component, attribute) pair.

    Behaviour by network snapshot type:

    - Flat DatetimeIndex snapshots → period is ignored; CSV's DatetimeIndex is
      stored as-is (single-period workflow).
    - MultiIndex (period, timestep) snapshots, period=None → CSV stored with
      its DatetimeIndex. At apply time, the values are broadcast under every
      period via level-1 lookup (canonical "same operational year per period").
    - MultiIndex snapshots, period=<int> → CSV's DatetimeIndex is promoted to
      a MultiIndex by prepending `period`. Stored in _user_ts with that
      MultiIndex; subsequent uploads with a different `period` for the same
      column stitch (replace that period's rows, keep the others). Required
      for "different weather year per period" workflows.
    """
    import io

    import numpy as _np
    import pandas as pd
    content = await read_capped(file)
    df = pd.read_csv(io.BytesIO(content), index_col=0, parse_dates=True)
    n = PyPSAService.get_network()
    ts_store = getattr(n, f"{component}_t", None)
    if ts_store is None:
        raise HTTPException(404, f"Component '{component}' not found")

    is_multi = isinstance(n.snapshots, pd.MultiIndex)
    if period is not None:
        if not is_multi:
            raise HTTPException(
                400, "?period=… requires MultiIndex snapshots. "
                "Build them first via /snapshots/multi_period.",
            )
        try:
            period_int = int(period)
        except (TypeError, ValueError):
            raise HTTPException(400, f"period must be an integer, got {period!r}")
        if period_int not in n.investment_periods:
            raise HTTPException(
                400, f"period={period_int} is not in n.investment_periods "
                f"({list(n.investment_periods)})",
            )
        # Promote the CSV's DatetimeIndex to MultiIndex(period, timestep).
        new_mi = pd.MultiIndex.from_arrays(
            [_np.full(len(df), period_int), df.index],
            names=["period", "timestep"],
        )
        df.index = new_mi

    # Phase 12f: refuse before the lock is taken and before `_user_ts` is
    # written. A CSV's blank cell is a NaN by the time `read_csv` is done, and
    # an entry that reaches `_user_ts` is re-injected on every solve.
    _reject_nonfinite_timeseries(df, component, attribute)

    with PyPSAService.get_lock():
        with _user_ts_lock:
            for col in df.columns:
                new_s = df[col]
                if period is not None:
                    existing = _user_ts.get((component, attribute, col))
                    if existing is not None and isinstance(existing.index, pd.MultiIndex):
                        # Drop existing rows for this period, then concat.
                        keep = existing[existing.index.get_level_values(0) != int(period)]
                        merged = pd.concat([keep, new_s]).sort_index()
                        _user_ts[(component, attribute, col)] = merged
                    else:
                        # First per-period upload for this column — replaces any
                        # earlier broadcast (DatetimeIndex) entry.
                        _user_ts[(component, attribute, col)] = new_s.copy()
                else:
                    _user_ts[(component, attribute, col)] = new_s.copy()
        # Grow n.snapshots if the upload covers more rows than the current
        # operational range. Matches the behaviour of the component-specific
        # upload routes (/generators/upload_profile, /loads/upload_profile)
        # so the user doesn't have to know which endpoint the GUI chose.
        # MultiIndex networks grow per-period; flat networks grow directly.
        # No-op (returns False) when the upload is per-period (`period=` set)
        # because that path stores a MultiIndex series, which the helper
        # already filters out.
        _ensure_snapshots_cover_user_ts(n)
        # Apply to the live network's _t table immediately (so the next GET
        # /timeseries reflects the upload without requiring a snapshot rebuild).
        # _reapply handles all three cases (DatetimeIndex+flat, DatetimeIndex+
        # MultiIndex broadcast, MultiIndex+MultiIndex direct).
        _reapply_user_ts_to_network(n)

    cols_preview = ", ".join(list(df.columns)[:3]) + ("…" if len(df.columns) > 3 else "")
    change_log_service.log(
        "timeseries", component.capitalize(), file.filename or cols_preview,
        f"Uploaded {component}/{attribute} time series '{file.filename}': "
        f"{len(df.columns)} column(s), {len(df)} rows"
        + (f", period={period}" if period is not None else ""),
    )
    return {
        "rows": len(df),
        "columns": len(df.columns),
        "period": period,
        "mode": "per_period" if period is not None else ("broadcast" if is_multi else "flat"),
    }
# ── User-uploaded time-series delete ─────────────────────────────────────────
# Single endpoint that drops `(component, attribute, name)` entries from
# `_user_ts`. `component` is the PyPSA `_t` attr name ('loads' / 'generators'
# / 'links' / 'stores' / 'storage_units'). When `name` is omitted, EVERY
# uploaded profile under `(component, attribute, *)` is dropped — useful for
# a future "Clear all profiles on this tab" UX. The matching `_t` slot is
# reset to PyPSA's default (column dropped from `_t.<attribute>`) via the
# explicit column-drop below, so the LP falls back to whatever the static
# attribute holds.
@router.delete("/timeseries")
def delete_timeseries(
    component: str,
    attribute: str,
    name: str | None = None,
):
    """
    Delete one (or all matching) user-uploaded time-series entries.

    Query params:
      - component: 'loads' | 'generators' | 'links' | 'stores' | 'storage_units'
      - attribute: 'p_set' / 'p_max_pu' / 'p_min_pu' / 'marginal_cost' / ...
      - name: optional component name. When omitted, drops every profile
        matching (component, attribute, *).
    """
    if component not in {"loads", "generators", "links", "stores", "storage_units"}:
        raise HTTPException(400, f"Unsupported component '{component}'")
    n = PyPSAService.get_network()

    with _user_ts_lock:
        keys_to_drop = [
            (c, a, col) for (c, a, col) in _user_ts
            if c == component and a == attribute and (name is None or col == name)
        ]
        for key in keys_to_drop:
            del _user_ts[key]
    dropped_names = [col for (_, _, col) in keys_to_drop]
    if not dropped_names:
        # 404 over silent-success — the frontend uses this to surface
        # "Profile not found" vs a successful drop. Same `_user_ts` key
        # may have already been cleared by a concurrent autosave / reload,
        # so this is informational, not destructive.
        raise HTTPException(404, f"No uploaded profile found for {component}/{attribute}/{name or '*'}")

    # Drop the matching columns from n.<component>_t.<attribute>. Without
    # this the `_t` table keeps the stale column until next reset — the LP
    # would still see the old profile and the next save would re-serialise
    # it. `_reapply_user_ts_to_network` only writes; it doesn't drop.
    with PyPSAService.get_lock():
        t_obj = getattr(n, f"{component}_t", None)
        if t_obj is not None:
            attr_df = getattr(t_obj, attribute, None)
            if attr_df is not None and hasattr(attr_df, "columns"):
                cols_to_drop = [c for c in dropped_names if c in attr_df.columns]
                if cols_to_drop:
                    attr_df.drop(columns=cols_to_drop, inplace=True)
        _reapply_user_ts_to_network(n)

    names_preview = ", ".join(dropped_names[:3]) + ("…" if len(dropped_names) > 3 else "")
    # Component → display class: 'loads' → 'Load', 'generators' → 'Generator',
    # 'links' → 'Link'. Plural-strip the trailing 's' for the changelog.
    display_class = component[:-1].capitalize() if component.endswith("s") else component.capitalize()
    change_log_service.log(
        "timeseries", display_class, names_preview,
        f"Deleted {component} {attribute} profile(s): {len(dropped_names)} entry(ies)",
    )
    return {
        "deleted": dropped_names,
        "component": component,
        "attribute": attribute,
        "snapshot_count": len(n.snapshots),
    }
