"""
The user time-series store and everything that reads or writes it.

Extracted verbatim from `routers/network.py` — the largest cluster in that
file. `routers.network` re-exports every name, which matters more here than
anywhere else in this decomposition: `services/chat_tools.py`,
`routers/projects.py`, `routers/snapshots.py`, `routers/io.py`, `main.py`,
`services/solver_service.py` and the tests all import these by name, and
chat_tools guards its import with a comment reading "only fails if
routers/network refactor breaks paths".

`_user_ts` is a module-level dict and `_user_ts_lock` the RLock guarding it.
Both are shared MUTABLE state: importers take them by value and mutate in
place, so the re-export is only sound while nothing ever rebinds them.
`tests/test_network_facade_surface.py::test_the_user_ts_store_is_never_rebound`
enforces that statically, here and in the router.

Depends on `services/snapshot_index.py` and `PyPSAService` — both services.
Nothing here imports a router.
"""
from __future__ import annotations

import io
import math
import threading as _ts_threading
import pandas as pd
from fastapi import HTTPException
from services.pypsa_service import PyPSAService
from services.snapshot_index import _build_period_multiindex


# ── User-uploaded time series store ───────────────────────────────────────────
# Each column is stored as an independent pd.Series keyed by
# (component, attribute, column_name).  Using a per-column key avoids every
# pandas index-alignment pitfall: uploading a column with 2024 timestamps will
# never corrupt another column that was uploaded with 2026 timestamps, and a
# re-upload of any column simply overwrites its own entry.
_user_ts: dict[tuple[str, str, str], pd.Series] = {}


_user_ts_lock = _ts_threading.RLock()


def _user_ts_rename_asset(component_attr: str, old_name: str, new_name: str) -> int:
    """
    Re-key `_user_ts` entries when a component is renamed via PUT.

    Without this, a `PUT /generators/Solar` with `{"name": "Solar_new"}`
    renames in PyPSA + drops the vintage_bounds entry but leaves
    `_user_ts[("generators", "p_max_pu", "Solar")]` orphaned. Next save
    persists it; next load, `_reapply_user_ts_to_network` skips it
    (because `col not in n.generators.index`) and the profile is silently
    lost. Move every matching key to the new name so the profile follows
    the rename.

    Returns the number of entries re-keyed. No-op when no entries match.
    """
    if old_name == new_name:
        return 0
    with _user_ts_lock:
        keys_to_move = [
            (comp, attr, col) for (comp, attr, col) in _user_ts
            if comp == component_attr and col == old_name
        ]
        for key in keys_to_move:
            comp, attr, _ = key
            _user_ts[(comp, attr, new_name)] = _user_ts.pop(key)
    return len(keys_to_move)


def _user_ts_delete_asset(component_attr: str, name: str) -> int:
    """
    Drop `_user_ts` entries for a deleted component so they don't
    accumulate forever in saved projects (each save would serialise the
    orphan; each load would silently drop it during reapply because the
    component is gone). Also prevents a future component that happens to
    reuse the same name from inheriting the deleted asset's profile.

    Returns the number of entries dropped.
    """
    with _user_ts_lock:
        keys_to_drop = [
            (comp, attr, col) for (comp, attr, col) in _user_ts
            if comp == component_attr and col == name
        ]
        for key in keys_to_drop:
            del _user_ts[key]
    return len(keys_to_drop)


def _user_ts_extent() -> tuple[str | None, str | None]:
    """
    Start / end datetime of the *longest* flat (DatetimeIndex) series in
    _user_ts — the main uploaded profile. Returns (None, None) when nothing
    flat has been uploaded.

    Uses the longest series rather than the union min/max across all series
    on purpose: _user_ts also holds short series backed up from the network's
    own _t tables (see _backup_network_ts_to_user_ts), and a stale backed-up
    range (e.g. a template's 2024 default) would otherwise drag the reported
    start backwards even though the user's actual upload starts elsewhere.
    The longest series is exactly the reference _ensure_snapshots_cover_user_ts
    keys on to realign n.snapshots, so this keeps the Model Horizon default
    consistent with the snapshot index the upload actually produced.

    Per-period (MultiIndex) series are skipped — their range is period-scoped
    and doesn't describe a single operational window.
    """
    with _user_ts_lock:
        flat = [
            s for s in _user_ts.values()
            if not isinstance(s.index, pd.MultiIndex) and len(s.index) > 0
        ]
    if not flat:
        return None, None
    ref = max(flat, key=lambda s: len(s.index))
    try:
        lo, hi = ref.index.min(), ref.index.max()
    except Exception:  # noqa: BLE001 — defensive over arbitrary uploads
        return None, None
    return (
        lo.isoformat() if hasattr(lo, "isoformat") else str(lo),
        hi.isoformat() if hasattr(hi, "isoformat") else str(hi),
    )


def _annual_hourly_reference():
    """
    Validate that an uploaded profile is sample-able into representative
    weeks. Returns ``(idx, None)`` when the longest flat _user_ts series is a
    deduped DatetimeIndex spanning all 12 calendar months of ONE year at
    hourly resolution; otherwise ``(None, reason)`` where ``reason`` explains
    the failed precondition.

    Drives both the ``can_sample_weeks`` flag on GET /network/snapshots and
    the precondition check inside POST /network/snapshots/sample_weeks.
    """
    with _user_ts_lock:
        flat = [
            s for s in _user_ts.values()
            if not isinstance(s.index, pd.MultiIndex) and len(s.index) > 1
        ]
    if not flat:
        return None, (
            "No uploaded time series found. Upload a full-year hourly profile "
            "(generation or load) first."
        )
    ref = max(flat, key=lambda s: len(s.index))
    idx = pd.DatetimeIndex(sorted(set(ref.index)))
    years = sorted(idx.year.unique().tolist())
    months = sorted(int(m) for m in idx.month.unique())
    if len(years) != 1 or months != list(range(1, 13)):
        return None, (
            "Representative-week sampling needs a profile spanning all 12 "
            f"months of a single calendar year — uploaded data covers "
            f"year(s) {years}, month(s) {months}."
        )
    try:
        med = idx.to_series().diff().median()
    except Exception:  # noqa: BLE001 — defensive over arbitrary uploads
        med = None
    if med != pd.Timedelta(hours=1):
        return None, (
            f"Representative-week sampling needs an hourly profile "
            f"({len(idx)} timesteps found; expected ~8760)."
        )
    return idx, None


def _serialize_user_ts() -> dict:
    """
    Return _user_ts as a JSON-serialisable nested dict.

    Format: ``{component: {attribute: {column: {index: [...], values: [...]}}}}``
    The index entries are:
      • DatetimeIndex series → list of ISO strings: ``["2025-01-01T00:00:00", …]``
      • MultiIndex(period, timestep) series → list of [int, ISO] pairs:
        ``[[2025, "2025-01-01T00:00:00"], …]``
    Restore auto-detects the shape from the first entry.

    Nested top-level structure avoids separator-collision bugs with component
    names that contain "|" or "/".
    """
    result: dict = {}
    # Hold _user_ts_lock for the iteration so concurrent uploads can't trip
    # `dictionary changed size during iteration`. Snapshot keys first so a
    # writer waiting on the lock isn't blocked for the JSON-serialisation
    # cost (just the dict-snapshot cost).
    with _user_ts_lock:
        items = list(_user_ts.items())
    for (comp, attr, col), series in items:
        if isinstance(series.index, pd.MultiIndex):
            idx = [
                [int(period), ts.isoformat() if hasattr(ts, "isoformat") else str(ts)]
                for period, ts in series.index
            ]
        else:
            idx = [ts.isoformat() if hasattr(ts, "isoformat") else str(ts) for ts in series.index]
        vals = [
            None if isinstance(v, float) and not math.isfinite(v) else v
            for v in series.tolist()
        ]
        result.setdefault(comp, {}).setdefault(attr, {})[col] = {"index": idx, "values": vals}
    return result


def _restore_user_ts(data: dict) -> None:
    """
    Restore _user_ts from the format produced by _serialize_user_ts.
    Supports both the current nested format and the legacy pipe-separated format
    for backwards compatibility with old user_ts.json files.
    All-NaN series are silently skipped — they represent corrupt/empty data.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    new_store: dict[tuple[str, str, str], pd.Series] = {}

    # Detect format: any top-level key containing "|" → legacy pipe format.
    is_legacy = any("|" in k for k in data.keys())

    def _build_series(raw_idx, values, name: str) -> pd.Series:
        """Reconstruct a Series, detecting MultiIndex vs flat by index shape."""
        if raw_idx and isinstance(raw_idx[0], (list, tuple)) and len(raw_idx[0]) == 2:
            periods = [int(p) for p, _ in raw_idx]
            timesteps = pd.to_datetime([ts for _, ts in raw_idx])
            mi = pd.MultiIndex.from_arrays([periods, timesteps], names=["period", "timestep"])
            return pd.Series(values, index=mi, dtype=float, name=name)
        idx = pd.to_datetime(raw_idx)
        return pd.Series(values, index=idx, dtype=float, name=name)

    if is_legacy:
        for key, payload in data.items():
            parts = key.split("|", 2)
            if len(parts) != 3:
                continue
            comp, attr, col = parts
            try:
                series = _build_series(payload["index"], payload["values"], col)
                if series.isna().all():
                    continue  # skip corrupt all-NaN series
                new_store[(comp, attr, col)] = series
            except Exception as exc:
                _log.warning("_restore_user_ts (legacy): skipping '%s': %s", key, exc)
    else:
        for comp, attrs in data.items():
            if not isinstance(attrs, dict):
                continue
            for attr, cols in attrs.items():
                if not isinstance(cols, dict):
                    continue
                for col, payload in cols.items():
                    if not isinstance(payload, dict):
                        continue
                    try:
                        series = _build_series(payload["index"], payload["values"], col)
                        if series.isna().all():
                            continue  # skip corrupt all-NaN series
                        new_store[(comp, attr, col)] = series
                    except Exception as exc:
                        _log.warning(
                            "_restore_user_ts: skipping %s/%s/%s: %s", comp, attr, col, exc
                        )

    # Replace the store atomically under the lock — readers in
    # _serialize_user_ts / _user_ts.items() never see a half-populated state.
    with _user_ts_lock:
        _user_ts.clear()
        _user_ts.update(new_store)


# Components whose `_t` tables we walk for time-series backup. Every non-empty
# attribute on every component is captured — not just the historical 3 slots —
# so that q_set, p_set, p_min_pu, state_of_charge, marginal_cost, etc. all
# round-trip through save/load.
# Components whose _t tables can hold USER-uploaded profiles (loads, capacity
# factors, etc.). Used by _backup_network_ts_to_user_ts to decide what to
# preserve across a set_snapshots(). Deliberately omits Bus and GlobalConstraint:
# their _t frames (buses_t.marginal_price, global_constraints_t.mu, …) are
# solver OUTPUTS, not user inputs, so they must not be ingested into _user_ts.
# NOTE: _flatten_snapshot_state must NOT use this list — it has to walk EVERY
# component (n.all_components) since set_snapshots reindexes all of them.
_TS_COMPONENTS: list[str] = [
    "generators", "loads", "storage_units", "stores",
    "lines", "links", "transformers", "shunt_impedances",
]


def _backup_network_ts_to_user_ts(n=None) -> None:
    """
    Copy time series from the network's _t tables into _user_ts.

    Walks every non-empty (component, attribute) pair. Only copies a column when:
      - it is not already in _user_ts, OR
      - the existing _user_ts entry is all-NaN (corrupt from a previous bad save).
    All-NaN columns from the network are never ingested — they represent
    reindex artifacts, not real user data.
    Call this BEFORE n.set_snapshots() or n.export_to_netcdf() so that profiles
    from imported networks are captured without overwriting good user uploads.

    INPUT-attribute filter: PyPSA's component defaults flag each attribute as
    Input (user-supplied profile) or Output (solver result). We only ingest
    Inputs — capturing Outputs like ``generators_t.p`` / ``lines_t.p0`` /
    ``storage_units_t.state_of_charge`` causes the next call to
    ``_reapply_user_ts_to_network`` (triggered by every autosave) to write
    captured dispatch back to the live network, overwriting fresh solve
    results with stale snapshots and producing orphan columns after a
    topology mutation. Falls back to capturing every attribute if defaults
    aren't readable, preserving legacy behaviour for exotic component classes.
    """
    if n is None:
        n = PyPSAService.get_network()
    for comp in _TS_COMPONENTS:
        ts_store = getattr(n, f"{comp}_t", None)
        if ts_store is None:
            continue
        input_attrs: set[str] | None = None
        try:
            comp_defaults = getattr(n.components, comp).defaults
            mask = comp_defaults["status"].astype(str).str.startswith("Input", na=False)
            input_attrs = set(comp_defaults.index[mask])
        except Exception:
            input_attrs = None
        try:
            attrs = list(ts_store.keys()) if hasattr(ts_store, "keys") else []
        except Exception:
            attrs = []
        for attr in attrs:
            if input_attrs is not None and attr not in input_attrs:
                continue
            # Skip marginal_cost: the LP-build path (solver_service ~3736)
            # writes per-snapshot CO2 surcharges into n.<component>_t.marginal_cost
            # when co2_price_per_period is configured. Without this skip, the
            # next autosave ingests those solver-written columns into _user_ts,
            # they appear as "uploaded profiles" in the Time Series Manager,
            # and DELETE /api/network/timeseries is futile — the next solve
            # rewrites the columns and the autosave-after re-ingests them.
            # Legitimate user uploads bypass this path (the upload endpoint
            # writes _user_ts directly) and persist via user_ts.json on save.
            if attr == "marginal_cost":
                continue
            df = ts_store.get(attr) if hasattr(ts_store, "get") else getattr(ts_store, attr, None)
            if df is None or not hasattr(df, "empty") or df.empty:
                continue
            for col in df.columns:
                series = df[col]
                if series.isna().all():
                    continue
                key = (comp, attr, col)
                # Atomic read-and-write under _user_ts_lock — this function
                # is reachable from save_project's autosave path (projects.py
                # ~line 677) BEFORE that path takes the PyPSA lock, so a
                # foreground upload route in another thread can race the
                # iteration here without the lock. Holding _user_ts_lock
                # for the per-key get-or-insert keeps `_serialize_user_ts`'s
                # snapshot-then-iterate path safe.
                with _user_ts_lock:
                    existing = _user_ts.get(key)
                    if existing is None or existing.isna().all():
                        _user_ts[key] = series.copy()


def _rebase_flat_user_ts(new_idx: pd.DatetimeIndex) -> int:
    """
    Positionally re-base every flat _user_ts series of the SAME LENGTH as
    `new_idx` onto `new_idx`. Returns the count re-based.

    Prevents silent data loss when _user_ts holds profiles from different
    calendar years. Scenario: a project is loaded with 2024 profiles (so
    _user_ts carries a 2024 series for every profiled column), then the user
    uploads a 2026 profile for ONE column. `_ensure_snapshots_cover_user_ts`
    then realigns `n.snapshots` to 2026 — and without this, every still-2024
    column reindexes to all-NaN inside `_reapply_user_ts_to_network` and is
    silently skipped, leaving those loads at 0 demand and those renewables at
    a flat `p_max_pu = 1.0`. The result is a badly corrupted solve (massive
    curtailment + lost load) with no error surfaced.

    Re-basing by POSITION keeps every same-resolution profile on one common
    operational range — a series' value at hour i maps to hour i of the new
    index. Series whose length differs from `new_idx` (a genuine 24 h
    representative day, leap- vs non-leap-year data) are left untouched: a
    positional re-base there would shift the calendar and corrupt the data.
    """
    n_target = len(new_idx)
    rebased = 0
    with _user_ts_lock:
        for key, series in list(_user_ts.items()):
            if isinstance(series.index, pd.MultiIndex):
                continue
            if len(series.index) != n_target or series.index.equals(new_idx):
                continue
            _user_ts[key] = pd.Series(
                series.values, index=new_idx, name=series.name,
            )
            rebased += 1
    return rebased


def _ensure_snapshots_cover_user_ts(n=None) -> bool:
    """
    Align n.snapshots with the _user_ts profiles so an upload actually
    reaches the optimiser. Returns True when snapshots were updated.

    Two triggers — the longest stored series is adopted as the operational
    range when EITHER:

      • it is **longer** than the network currently models (growth — the
        original behaviour), OR
      • it has **zero date overlap** with the current snapshots — i.e. the
        upload is for a completely different time window (e.g. a May profile
        on a January network). This is the important case:
        ``_reapply_user_ts_to_network`` skips every column whose aligned
        series would be all-NaN, so without realigning here the upload is
        stored in ``_user_ts`` (and shown in the GUI) but NEVER reaches
        ``n.loads_t.p_set`` etc. — the model then optimises with no profile.

    Two paths depending on the current snapshot shape:

      • Flat ``DatetimeIndex`` → ``set_snapshots`` swaps to the uploaded range.
      • ``MultiIndex(period, timestep)`` → rebuild the MultiIndex with the new
        per-period range replicated under every existing period (canonical
        "Same year per period"). Always MultiIndex → MultiIndex here, never
        to flat, so the pandas ``cannot include dtype 'M' in a buffer``
        reindex bug doesn't fire.

      • Per-period uploads (series whose index is itself MultiIndex) are
        filtered out before scanning — ``sorted(set(MultiIndex))`` yields
        tuples and would explode ``pd.DatetimeIndex(...)``. They're already
        period-scoped and don't drive the range decision.

    Used after bundle load / per-component profile upload. Caller is
    responsible for calling _reapply_user_ts_to_network afterwards to
    populate the freshly sized _t tables.
    """
    if n is None:
        n = PyPSAService.get_network()
    if not _user_ts:
        return False
    flat_series = [s for s in _user_ts.values() if not isinstance(s.index, pd.MultiIndex)]
    if not flat_series:
        return False
    longest = max(flat_series, key=lambda s: len(s.index))
    # Dedup before comparing/applying so a series with duplicate timestamps
    # can't accidentally shrink n.snapshots below its current length.
    new_idx = pd.DatetimeIndex(sorted(set(longest.index)))

    if isinstance(n.snapshots, pd.MultiIndex):
        periods = sorted(n.snapshots.get_level_values(0).unique().tolist())
        if not periods:
            return False
        per_period_now = len(n.snapshots) // len(periods)
        # Per-period timestep range (deduped across periods) — used for the
        # zero-overlap check below.
        per_period_idx = pd.DatetimeIndex(
            sorted(set(n.snapshots.get_level_values(1)))
        )
        no_overlap = len(per_period_idx.intersection(new_idx)) == 0
        if len(new_idx) <= per_period_now and not no_overlap:
            return False
        # Grow / realign the per-period operational range to the uploaded
        # series' extent, keeping the same periods. _t tables are MultiIndex →
        # MultiIndex reindex, which PyPSA handles cleanly.
        _backup_network_ts_to_user_ts(n)
        # Re-base same-length _user_ts series onto the new timestep range so a
        # mixed-year _user_ts (old project profiles + a fresh upload) doesn't
        # leave the non-uploaded columns stranded in the old year (→ all-NaN
        # → silently dropped by _reapply). See _rebase_flat_user_ts.
        _rebase_flat_user_ts(new_idx)
        mi = _build_period_multiindex(periods, [new_idx] * len(periods))
        n.set_snapshots(mi)
        return True

    # Flat → flat.
    cur = n.snapshots
    grew = len(new_idx) > len(cur)
    # Zero overlap → the upload is for a different operational window than the
    # network's current snapshots; adopt the uploaded range so the data the
    # user gave actually drives the optimisation (see docstring).
    realign = len(cur.intersection(new_idx)) == 0
    if not grew and not realign:
        return False
    # Preserve network-side _t tables (e.g. from a freshly imported netcdf)
    # into _user_ts before set_snapshots reindexes them — otherwise rows that
    # fall outside the new index would be silently dropped. Backup is a no-op
    # for columns already present in _user_ts.
    _backup_network_ts_to_user_ts(n)
    # Re-base same-length _user_ts series onto new_idx so a mixed-year _user_ts
    # (old project profiles + a fresh upload in a different year) doesn't leave
    # the non-uploaded columns stranded in the old year — which would reindex
    # to all-NaN and be silently dropped by _reapply. See _rebase_flat_user_ts.
    _rebase_flat_user_ts(new_idx)
    n.set_snapshots(new_idx)
    return True


def _reapply_user_ts_to_network(n=None) -> None:
    """
    Re-apply _user_ts profiles to the network's _t tables, aligned to the
    current snapshot index.  Call this after n.set_snapshots() or after a
    project load so that the network uses the correct time series for simulation.

    If a stored series has zero overlap with the current snapshots (e.g. the
    user uploaded 2024 hourly data but the network still uses 2013 daily
    snapshots), the column is skipped rather than overwriting the network table
    with all-NaN.  Update the network's snapshots first, then call this again.

    Implementation note: aligned series are grouped by (component, attribute)
    and written back in a single concat per group. The previous per-column
    `existing.copy(); merged[col] = aligned; ts_store[attr] = merged` loop was
    O(R·N²) per group (each iteration re-copied the full DataFrame), which
    pushed bundle imports past axios' 30 s timeout for year-of-hourly-data
    projects. The grouped path is O(R·N).
    """
    import logging as _logging
    from collections import defaultdict

    import numpy as _np
    _log = _logging.getLogger(__name__)
    if n is None:
        n = PyPSAService.get_network()

    # Three cases for aligning a stored _user_ts series with n.snapshots:
    #   1) series.index is MultiIndex (per-period upload) AND n.snapshots is
    #      MultiIndex → direct reindex (PyPSA matches tuples).
    #   2) series.index is MultiIndex AND n.snapshots is flat → skip; the
    #      stored data is scoped to specific periods that no longer exist.
    #   3) series.index is DatetimeIndex AND n.snapshots is MultiIndex →
    #      broadcast via level-1 lookup (canonical "same operational year").
    #      reindex() rejects duplicate-target labels (level-1 has duplicates
    #      across periods), so we lookup positionally via get_indexer.
    #   4) series.index is DatetimeIndex AND n.snapshots is flat → plain
    #      reindex (the original code path).
    is_multi = isinstance(n.snapshots, pd.MultiIndex)
    target_lookup = n.snapshots.get_level_values(1) if is_multi else None

    # Defensive pre-pass: align any stale `_t` DataFrame whose index doesn't
    # match the current `n.snapshots`. Without this, a frame written under
    # a previous snapshot regime (e.g. flat DatetimeIndex from a single-period
    # solve) silently leaks into the next solve via PyPSA's `_t` access —
    # the LP either reindexes to all-NaN (broken) or the table grows in
    # subsequent concats (rows from BOTH old and new indexes coexist,
    # 35040-row mixed-index frames observed in live state).
    #
    # Strategy mirrors the per-column reapply logic below:
    #   • flat → multi: broadcast each column by level-1 timestep match
    #   • multi → flat: drop entirely (period scope no longer exists; if
    #     the data is preserved in `_user_ts`, the main loop re-injects)
    #   • multi → multi or flat → flat with different bounds: reindex
    #     (fills missing snapshots with NaN; PyPSA's default-fallback
    #     handles those at solve time)
    for comp in ("generators", "loads", "storage_units", "stores", "links",
                 "lines", "transformers"):
        ts_store = getattr(n, f"{comp}_t", None)
        if ts_store is None:
            continue
        for attr in list(ts_store.keys()):
            ts_df = ts_store[attr]
            if ts_df is None or ts_df.empty:
                continue
            if ts_df.index.equals(n.snapshots):
                continue  # already aligned, no-op
            df_is_multi = isinstance(ts_df.index, pd.MultiIndex)
            if df_is_multi and is_multi:
                ts_store[attr] = ts_df.reindex(n.snapshots)
            elif df_is_multi and not is_multi:
                ts_store[attr] = pd.DataFrame(index=n.snapshots)
            elif is_multi:
                # Flat existing → broadcast to MultiIndex by level-1 lookup.
                positions = ts_df.index.get_indexer(target_lookup)
                rebroadcast = pd.DataFrame(index=n.snapshots)
                mask = positions >= 0
                for c in ts_df.columns:
                    out = _np.full(len(target_lookup), _np.nan, dtype=float)
                    out[mask] = ts_df[c].values[positions[mask]]
                    rebroadcast[c] = out
                ts_store[attr] = rebroadcast
            else:
                ts_store[attr] = ts_df.reindex(n.snapshots)

    # Input-attribute filter, mirroring the gate in
    # _backup_network_ts_to_user_ts. Older user_ts.json files (saved before
    # the backup-side filter landed) may carry OUTPUT keys like
    # ("generators", "p", "Solar2"); writing those back to the network would
    # clobber freshly-solved dispatch with the captured snapshot. Cache the
    # per-component Input set so the .defaults lookup is paid once per call.
    input_attrs_by_comp: dict[str, set[str] | None] = {}

    def _is_input_attr(comp_name: str, attr_name: str) -> bool:
        if comp_name not in input_attrs_by_comp:
            try:
                comp_defaults = getattr(n.components, comp_name).defaults
                mask = comp_defaults["status"].astype(str).str.startswith("Input", na=False)
                input_attrs_by_comp[comp_name] = set(comp_defaults.index[mask])
            except Exception:
                input_attrs_by_comp[comp_name] = None
        cached = input_attrs_by_comp[comp_name]
        return cached is None or attr_name in cached

    grouped: dict[tuple[str, str], dict[str, pd.Series]] = defaultdict(dict)
    for (comp, attr, col), series in _user_ts.items():
        ts_store = getattr(n, f"{comp}_t", None)
        if ts_store is None:
            continue
        if not _is_input_attr(comp, attr):
            _log.debug("_reapply: skipping %s/%s/%s — output attribute", comp, attr, col)
            continue
        component_df = getattr(n, comp, None)
        if component_df is None or col not in component_df.index:
            _log.debug("_reapply: skipping %s/%s/%s — component not in network", comp, attr, col)
            continue
        series_is_multi = isinstance(series.index, pd.MultiIndex)
        if series_is_multi and is_multi:
            # Case 1 — both MultiIndex: direct tuple reindex.
            aligned = series.reindex(n.snapshots)
        elif series_is_multi and not is_multi:
            # Case 2 — stored per-period data on flat snapshots: skip rather
            # than guess which period to project. User must rebuild snapshots
            # to MultiIndex (or re-upload as DatetimeIndex) to use this data.
            _log.debug(
                "_reapply: skipping per-period %s/%s/%s — network is flat",
                comp, attr, col,
            )
            continue
        elif is_multi:
            # Case 3 — DatetimeIndex series + MultiIndex snapshots: broadcast.
            positions = series.index.get_indexer(target_lookup)
            out = _np.full(len(target_lookup), _np.nan, dtype=float)
            mask = positions >= 0
            out[mask] = series.values[positions[mask]]
            aligned = pd.Series(out, index=n.snapshots)
        else:
            # Case 4 — plain reindex.
            aligned = series.reindex(n.snapshots)
        if aligned.isna().all() and not series.isna().all():
            _log.warning(
                "_reapply: %s/%s/%s has no overlap with current snapshots "
                "(%s … %s) — skipping to avoid writing all-NaN. "
                "Set network snapshots to match the uploaded profile first.",
                comp, attr, col,
                n.snapshots[0] if len(n.snapshots) else "?",
                n.snapshots[-1] if len(n.snapshots) else "?",
            )
            continue
        grouped[(comp, attr)][col] = aligned

    for (comp, attr), col_dict in grouped.items():
        ts_store = getattr(n, f"{comp}_t", None)
        if ts_store is None:
            continue
        new_block = pd.DataFrame(col_dict, index=n.snapshots)
        existing = getattr(ts_store, attr, None)
        if existing is None or (hasattr(existing, "empty") and existing.empty):
            ts_store[attr] = new_block
        else:
            # Drop overlapping columns from `existing` first, then concat once.
            # Single O(R·(N+M)) pass instead of N copies of an N-column frame.
            overlap = [c for c in col_dict.keys() if c in existing.columns]
            base = existing.drop(columns=overlap) if overlap else existing
            # Re-align `base` to `n.snapshots` BEFORE the concat. Without this
            # guard, a flat-DatetimeIndex `base` from a previous single-period
            # solve unions with the MultiIndex `new_block` — producing a
            # mixed-index DataFrame whose flat rows don't match
            # `n.snapshots`, so PyPSA's LP silently falls back to the scalar
            # default for every column the base owns (e.g. `Solar2` p_max_pu).
            # Symptom: renewable dispatch flat at p_nom while vintages still
            # honour the profile, because vintage columns were written with
            # the correct MultiIndex.
            #
            # Case-by-case re-alignment mirrors the `_user_ts` path above:
            #   • flat base + multi snapshots → broadcast by level-1 timestep
            #   • multi base + flat snapshots → drop (period scope no longer
            #     exists)
            #   • same-shape index → no-op (reindex is identity)
            if not base.empty and not base.index.equals(n.snapshots):
                base_is_multi = isinstance(base.index, pd.MultiIndex)
                if base_is_multi and is_multi:
                    base = base.reindex(n.snapshots)
                elif base_is_multi and not is_multi:
                    # Multi-period base, flat target — period scope is gone,
                    # drop the entire base; user-uploaded data lives in
                    # `_user_ts` and will be re-broadcast by the loop above.
                    base = pd.DataFrame(index=n.snapshots)
                elif is_multi:
                    # Flat base, multi target — broadcast by level-1 timestep
                    # so each period gets the same operational profile.
                    base_target_lookup = n.snapshots.get_level_values(1)
                    positions = base.index.get_indexer(base_target_lookup)
                    rebroadcast = pd.DataFrame(index=n.snapshots)
                    for c in base.columns:
                        out = _np.full(len(base_target_lookup), _np.nan, dtype=float)
                        mask = positions >= 0
                        out[mask] = base[c].values[positions[mask]]
                        rebroadcast[c] = out
                    base = rebroadcast
                else:
                    base = base.reindex(n.snapshots)
            ts_store[attr] = pd.concat([base, new_block], axis=1)


def _capture_snapshot_weights_per_timestep(n) -> pd.DataFrame | None:
    """
    Snapshot ``n.snapshot_weightings`` keyed by timestep so it survives a
    subsequent ``n.set_snapshots(mi)`` reset.

    PyPSA's ``set_snapshots`` calls ``_snapshots_data.reindex(new_idx,
    fill_value=default_snapshot_weightings)`` — for a MultiIndex transition,
    the old DatetimeIndex tuples have no match in the new MultiIndex, so every
    cell falls back to 1.0. Any custom weights the user set on the flat
    snapshots (representative-week scaling factor 52.14, half-hour resolution
    0.5, etc.) are silently lost, and the LP's ``n.nyears`` collapses to
    ``n_timesteps / 8760`` — heavily undervaluing CAPEX and producing
    renewable over-build.

    Returned frame is indexed by *timestep only* (the timezone-naive datetime),
    not by snapshot — the reapply helper aligns it against the NEW
    ``n.snapshots``. Returns ``None`` when weights are at PyPSA defaults
    (all 1.0) since there's nothing meaningful to preserve.
    """
    sw = n.snapshot_weightings.copy()
    if sw.empty:
        return None
    if isinstance(sw.index, pd.MultiIndex):
        first_p = sw.index.get_level_values(0)[0]
        sw_per_ts = sw.loc[first_p].copy()
        # `.loc[first_p]` collapses the level — sw_per_ts has a flat
        # DatetimeIndex named "timestep". Normalise to "snapshot" so the
        # reapply path's `.reindex(timestep_idx)` works regardless of origin.
        sw_per_ts.index.name = "snapshot"
    else:
        sw_per_ts = sw.copy()
    # No point preserving the all-1.0 default — saves a needless write.
    if (sw_per_ts == 1.0).all().all():
        return None
    return sw_per_ts


def _reapply_snapshot_weights(n, captured) -> None:
    """
    Write the captured per-timestep weights back onto ``n.snapshot_weightings``
    after ``set_snapshots`` has rebuilt the index. Aligns by timestep value:

      • Flat target  → reindex by ``n.snapshots`` directly.
      • Multi target → for each period, reindex the timestep slice; replicate
        the same per-timestep weights under every period (the canonical
        multi-period workflow uses the same operational year per period, so
        broadcasting weights matches user intent).

    Missing timesteps (e.g. user changed operational range) fall back to 1.0.
    Must run AFTER ``n.set_snapshots(...)`` so ``n.snapshots`` reflects the
    new index. Holds no lock — caller responsibility.
    """
    if captured is None:
        return
    idx = n.snapshots
    if isinstance(idx, pd.MultiIndex):
        # Build the new frame period-by-period.
        chunks = []
        for p in idx.get_level_values(0).unique():
            mask = idx.get_level_values(0) == p
            ts_slice = idx[mask].get_level_values(1)
            aligned = captured.reindex(ts_slice).fillna(1.0)
            aligned.index = idx[mask]
            chunks.append(aligned)
        new_sw = pd.concat(chunks)
    else:
        new_sw = captured.reindex(idx).fillna(1.0)
        new_sw.index = idx
    # Setter validates df.index.equals(n.snapshots); we built new_sw against
    # n.snapshots so it should pass. If the columns mismatch (PyPSA added new
    # weight columns in a future version), assign per-column to be safe.
    for col in new_sw.columns:
        if col in n.snapshot_weightings.columns:
            try:
                n.snapshot_weightings[col] = new_sw[col].values
            except Exception:
                # Defensive — column-level assignment failure shouldn't break
                # the whole solve. Default 1.0 is acceptable as fallback.
                pass


def _flatten_snapshot_state(n=None) -> None:
    """
    Collapse any MultiIndex(period, timestep) snapshot structure on the
    network down to a flat DatetimeIndex, in place — so a subsequent
    ``n.set_snapshots(flat)`` is a safe flat→flat operation.

    A direct ``set_snapshots(flat)`` on a MultiIndexed network trips pandas'
    "cannot include dtype 'M' in a buffer" bug (``MultiIndex._wrap_reindex_result``
    → ``tuples_to_object_array``) for every non-empty MultiIndexed frame PyPSA
    reindexes, and a length mismatch for the empty ones. To pre-empt both we
    walk EXACTLY the frames ``set_snapshots`` touches — every component's
    ``dynamic`` dict plus ``_snapshots_data`` — and demote each MultiIndexed one
    to its first period's timesteps. (The earlier version walked a
    hand-maintained component list that omitted Bus / GlobalConstraint, so their
    solved ``_t`` frames survived as MultiIndex and still tripped the bug.)

    No-op when everything is already flat. Rows from non-first periods are
    discarded — the caller should ``_backup_network_ts_to_user_ts(n)`` before
    and ``_reapply_user_ts_to_network(n)`` after so uploaded profiles survive.
    Must be called under ``PyPSAService.get_lock()``.
    """
    if n is None:
        n = PyPSAService.get_network()
    snaps_multi = isinstance(n.snapshots, pd.MultiIndex)

    if snaps_multi:
        first_p = n.snapshots.get_level_values(0).unique()[0]
        base_idx = pd.DatetimeIndex(
            n.snapshots[n.snapshots.get_level_values(0) == first_p].get_level_values(1)
        )
    else:
        base_idx = pd.DatetimeIndex(n.snapshots)
    base_idx.name = "snapshot"

    # Collapse one MultiIndexed frame to flat by keeping its FIRST period's rows
    # and relabelling the index with that period's own timestep values. Safe for
    # empty frames too — a boolean-mask slice works on a 0-column frame — and the
    # result is always length-consistent, so set_snapshots() is then flat→flat.
    def _demote(df):
        lvl0 = df.index.get_level_values(0)
        mask = lvl0 == lvl0.unique()[0]
        out = df[mask].copy()
        out.index = pd.DatetimeIndex(df.index[mask].get_level_values(1))
        out.index.name = "snapshot"
        return out

    # Walk every component's dynamic dict — the exact set ``set_snapshots``
    # iterates (``n.c[component].dynamic`` for ``component in n.all_components``)
    # — so nothing PyPSA will later reindex is left MultiIndexed.
    for component in n.all_components:
        try:
            dynamic = n.c[component].dynamic
        except (KeyError, AttributeError, TypeError):
            continue
        for k in list(dynamic.keys()):
            df = dynamic[k]
            if df is None or not isinstance(df.index, pd.MultiIndex):
                continue
            dynamic[k] = _demote(df)

    if isinstance(n.snapshot_weightings.index, pd.MultiIndex):
        # Direct write to the private backing — PyPSA's public setter validates
        # df.index == self.snapshots, which is still MultiIndex at this point.
        n._snapshots_data = _demote(n.snapshot_weightings)

    if snaps_multi:
        n.set_snapshots(base_idx)


def _parse_upload(content: bytes, filename: str) -> pd.DataFrame:
    """
    Parse an uploaded Excel or CSV file, returning a DataFrame with a
    timezone-naive DatetimeIndex.  Raises HTTPException on failure.
    """
    fname = (filename or "").lower()
    try:
        if fname.endswith(".xlsx") or fname.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(content), index_col=0)
        else:
            df = pd.read_csv(io.BytesIO(content), index_col=0, parse_dates=False)
    except Exception as exc:
        raise HTTPException(400, f"Could not parse file: {exc}") from exc

    # Normalise index to timezone-naive datetime using a consistent parser.
    # Using format="mixed" (pandas ≥ 2) gracefully handles both
    # "2024-01-01 00:00" and "2024-01-01T00:00:00" strings.
    try:
        idx = pd.to_datetime(df.index, utc=False, format="mixed", dayfirst=False)
    except TypeError:
        # pandas < 2 doesn't have format="mixed"; fall back to inference
        idx = pd.to_datetime(df.index, utc=False, infer_datetime_format=True)
    df.index = idx.tz_localize(None) if idx.tz is not None else idx
    df = df[df.index.notna()]          # drop rows that didn't parse
    df.index.name = "timestamp"
    return df
