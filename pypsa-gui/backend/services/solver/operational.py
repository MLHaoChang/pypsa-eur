"""Operational rolling solve on the plan's sizes (DC 07, slice S1).

A rolling-horizon run with sizing off (``solve_strategy="rolling"`` and
``fixed_sizes`` set, F1) dispatches the frozen design window by window, with
no look-ahead past each window. It is the operation that the plan (a full-horizon
solve with perfect foresight) is compared against.

This module owns the parts a plain rolling run does not have:

* the refusals: :func:`issues` refuses the horizon-coupled rows no other
  guard refuses under rolling (they would be applied per window), and
  :func:`fallback_refusal` raises where the solver would otherwise fall back
  from rolling to one full-horizon solve (multi-period, SCLOPF; preflight
  already refuses both as ``rolling_with_*``), so the label never lies;
* the completeness check: nothing may still be sizeable once the freeze is
  in place, because sizing per window is the decoupling that F1 used to refuse;
* :func:`non_cyclic`: the storage cyclic flags are turned off for the run and
  restored afterwards, with the initial state of charge (PyPSA's loop writes
  it into the static table);
* :func:`run_windows`: PyPSA's rolling loop, with each window's status checked.
  A failed window stops the run, its dispatch and every later window's reads
  NaN (not solved), and it never seeds a later window;
* :func:`stitched_opex`: the operating cost of the stitched dispatch, priced by
  the snapshot weightings over the full horizon. It never sums the window
  objectives, which would count the overlap twice.

A plain rolling run (sizing on) takes none of these paths.
"""

from __future__ import annotations

import contextlib
import contextvars
import math
from collections.abc import Iterator

import pandas as pd

#: ``n.meta`` key of the last operational run's report (windows, opex, notes).
META_KEY = "operational_run"

#: Cyclic flags turned off for an operational run, per component.
CYCLIC_FLAGS = {
    "StorageUnit": ("cyclic_state_of_charge", "cyclic_state_of_charge_per_period"),
    "Store": ("e_cyclic", "e_cyclic_per_period"),
}
#: The initial state PyPSA's rolling loop overwrites in the static table.
INITIAL_ATTR = {"StorageUnit": "state_of_charge_initial", "Store": "e_initial"}
#: The solved state each window seeds the next one from.
STATE_ATTR = {"StorageUnit": "state_of_charge", "Store": "e"}

#: A size counts as pinned when its bounds are at most this far apart (MW or
#: MWh). The freeze pins ``max = min + 1e-6``; 1e-5 allows for float noise.
PIN_TOL = 1e-5

#: The capacity columns that make a component sizeable.
_EXTENDABLE = (
    ("Generator", "p_nom"),
    ("StorageUnit", "p_nom"),
    ("Store", "e_nom"),
    ("Link", "p_nom"),
    ("Line", "s_nom"),
    ("Transformer", "s_nom"),
)


#: The window whose LP ``run_windows`` is building, set around each ``n.optimize`` and reset in
#: a ``finally`` (DC-07 C20b). Only inside it may a row read the dispatch before the window start.
_WINDOW: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "operational_window", default=None
)


def current_window(n) -> dict | None:
    """The operational window being solved on ``n`` (``index``, ``start``, ``stop``, and
    ``written``: the position below which this run's earlier windows wrote the dispatch), or
    None outside an operational run's ``n.optimize`` (C20b)."""
    window = _WINDOW.get()
    return window if window is not None and window["network"] is n else None


class OperationalWindowFailed(RuntimeError):
    """A window of an operational run did not solve. ``kind`` is the typed
    refusal; ``window`` is the window record (index, first and last
    snapshot, status, condition)."""

    kind = "operational_window_failed"

    def __init__(self, window: dict):
        self.window = window
        super().__init__(
            f"Operational run: window {window['index'] + 1} "
            f"({window['first']} to {window['last']}) did not solve "
            f"({window['status']}/{window['condition']}). Its dispatch and every "
            "later window's are not solved."
        )


def _get(cfg, key: str, default):
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def is_operational(cfg) -> bool:
    """True for a rolling run with sizing off: the operational run. Takes the
    solver config as a model or as its dict."""
    return (
        _get(cfg, "solve_strategy", "full") == "rolling"
        and (_get(cfg, "fixed_sizes", "off") or "off") != "off"
    )


def sizes_digest(n, cfg) -> str | None:
    """What the operational run's sizes come from (C7): the capacity run's
    recorded size vector digest, or None for the nameplate (its ``*_nom``
    columns are inputs, already in the input fingerprint) and for any other
    run."""
    if not is_operational(cfg) or _get(cfg, "fixed_sizes", "off") != "capacity_run":
        return None
    from services.solver.fixed_sizes import META_RECORD

    rec = n.meta.get(META_RECORD) if isinstance(n.meta, dict) else None
    return str(rec.get("sha256")) if isinstance(rec, dict) else "missing"


def issues(n, cfg) -> list[tuple[str, str, str, str]]:
    """``(severity, code, name, message)`` for an operational run (C3, C6).
    Empty for any other run, so a plain rolling run keeps today's checks.

    The rows of ``build_lp_chain`` that couple snapshots and are not already
    refused under rolling elsewhere (ENS cap, import energy cap, reserve
    margin, island and the windowed commercial terms are) are refused here:
    a run-hour cap would be prorated per window, and the PCS/STATCOM rows
    would size the converter again in every window. User code cannot be
    classified, so it gets a warning."""
    if not is_operational(cfg):
        return []
    out = []
    from services.solver.envelope_floors import _capped

    for g in sorted(_capped(n)):
        out.append(
            (
                "error",
                "operational_rolling_run_hours",
                g,
                f"{g} has a run-hour cap, which is an annual budget: a rolling "
                "run would prorate it per window with nothing carried between "
                "windows. Remove the cap or use the full strategy.",
            )
        )
    from services.solver.pcs_statcom import pcs_statcom_tags

    stores, links = pcs_statcom_tags(n)
    for name in [
        *(stores.index if stores is not None else []),
        *(links.index if links is not None else []),
    ]:
        out.append(
            (
                "error",
                "operational_rolling_pcs_sizing",
                str(name),
                f"{name} carries a PCS/STATCOM tag: its converter is sized in the "
                "LP, and a rolling run would size it again in every window. Use "
                "the full strategy.",
            )
        )
    if (_get(cfg, "extra_functionality_code", None) or "").strip():
        out.append(
            (
                "warning",
                "operational_rolling_user_code",
                "",
                "Your extra_functionality code runs in every window of the "
                "operational run and sees only that window's snapshots. A row "
                "that sums over the year is applied per window.",
            )
        )
    return out


class OperationalRefused(ValueError):
    """An operational run the solver cannot run as asked. ``kind`` is typed."""

    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


def fallback_refusal(cfg, *, sclopf: bool) -> None:
    """Raise :class:`OperationalRefused` where a plain rolling run falls back
    to the full horizon (C6): for an operational run that fallback would
    publish a full-horizon dispatch under the rolling label."""
    if not is_operational(cfg):
        return
    if _get(cfg, "multi_investment_periods", False):
        raise OperationalRefused(
            "operational_rolling_multi_period",
            "An operational (rolling) run on fixed sizes cannot span several investment "
            "periods. Solve one period, or use the full strategy.",
        )
    if sclopf:
        raise OperationalRefused(
            "operational_rolling_sclopf",
            "An operational (rolling) run on fixed sizes cannot run N-1 (SCLOPF). Turn "
            "N-1 off, or use the full strategy.",
        )


def refuse_sizeable(n) -> None:
    """Raise :class:`OperationalRefused` (``operational_still_sizeable``) when
    anything is still sizeable once the freeze is in place (C6)."""
    left = still_sizeable(n)
    if left:
        more = " …" if len(left) > 5 else ""
        raise OperationalRefused(
            "operational_still_sizeable",
            f"An operational run needs every size fixed, but {', '.join(left[:5])}{more} "
            "can still be sized.",
        )


def still_sizeable(n) -> list[str]:
    """``"<Component>/<name>"`` of every component the LP could still size:
    extendable without bounds pinned within :data:`PIN_TOL`. Empty once the
    freeze covers everything."""
    out: list[str] = []
    for comp, nom in _EXTENDABLE:
        df = n.components[comp].static
        ext = f"{nom}_extendable"
        if df.empty or ext not in df.columns:
            continue
        mask = df[ext].astype(bool)
        if not mask.any():
            continue
        lo = (
            pd.to_numeric(df.get(f"{nom}_min"), errors="coerce")
            if f"{nom}_min" in df
            else None
        )
        hi = (
            pd.to_numeric(df.get(f"{nom}_max"), errors="coerce")
            if f"{nom}_max" in df
            else None
        )
        for name in df.index[mask]:
            a = None if lo is None else lo.get(name)
            b = None if hi is None else hi.get(name)
            pinned = (
                a is not None
                and b is not None
                and math.isfinite(a)
                and math.isfinite(b)
                and b - a <= PIN_TOL
            )
            if not pinned:
                out.append(f"{comp}/{name}")
    return out


@contextlib.contextmanager
def non_cyclic(n) -> Iterator[list[str]]:
    """Turn every storage cyclic flag off for the run (C1) and restore the
    flags, and the initial state PyPSA's rolling loop overwrites, on exit.
    Yields ``"<Component>/<name>"`` of each storage whose flags were on."""
    saved: list[tuple[str, str, pd.Series]] = []
    turned_off: list[str] = []
    try:
        for comp, flags in CYCLIC_FLAGS.items():
            df = n.components[comp].static
            if df.empty:
                continue
            on = pd.Series(False, index=df.index)
            for flag in flags:
                if flag in df.columns:
                    saved.append((comp, flag, df[flag].copy()))
                    on |= df[flag].astype(bool)
            init = INITIAL_ATTR[comp]
            if init in df.columns:
                saved.append((comp, init, df[init].copy()))
            turned_off += [f"{comp}/{name}" for name in df.index[on]]
            for flag in flags:
                if flag in df.columns:
                    df[flag] = False
        yield turned_off
    finally:
        for comp, col, values in saved:
            live = n.components[comp].static
            keep = values.index.intersection(live.index)
            live.loc[keep, col] = values.loc[keep]


def windows(
    snapshots: pd.Index, horizon: int, overlap: int
) -> list[tuple[int, int, int]]:
    """``(index, start, stop)`` of each window, as PyPSA's rolling loop cuts
    them: a window starts every ``horizon - overlap`` snapshots and runs for
    ``horizon`` snapshots, the last one clipped to the horizon's end."""
    if horizon < 1 or not 0 <= overlap < horizon:
        raise ValueError(
            f"rolling_overlap ({overlap}) must be < rolling_horizon ({horizon})."
        )
    step = horizon - overlap
    count = len(snapshots)
    return [
        (i, start, min(count, start + horizon))
        for i, start in enumerate(range(0, count, step))
    ]


def _blank_from(n, snapshots: pd.Index) -> None:
    """Set every varying solver output at ``snapshots`` to NaN: not solved,
    never 0 (C2)."""
    for component in n.components:
        if component.static.empty and not component.dynamic:
            continue
        defaults = component.defaults
        outputs = [
            a
            for a in defaults.index
            if str(defaults.at[a, "status"]).startswith("Output")
            and bool(defaults.at[a, "varying"])
        ]
        for attr in outputs:
            frame = component.dynamic.get(attr)
            if frame is None or frame.empty:
                continue
            rows = frame.index.intersection(snapshots)
            if len(rows):
                frame.loc[rows, :] = math.nan


def run_windows(n, *, horizon: int, overlap: int, **optimize_kwargs) -> list[dict]:
    """PyPSA's ``optimize_with_rolling_horizon`` loop, with each window's
    status checked (C2). Window ``i > 0`` starts from the state solved at the
    snapshot before it. The first failure blanks that window's snapshots and
    every later one and raises :class:`OperationalWindowFailed`. Returns one
    record per window."""
    snapshots = n.snapshots
    records: list[dict] = []
    written = 0
    for index, start, stop in windows(snapshots, horizon, overlap):
        sns = snapshots[start:stop]
        if index:
            for comp, state in STATE_ATTR.items():
                static = n.components[comp].static
                if not static.empty:
                    static[INITIAL_ATTR[comp]] = (
                        n.components[comp].dynamic[state].loc[snapshots[start - 1]]
                    )
        token = _WINDOW.set(
            {
                "network": n,
                "index": index,
                "start": start,
                "stop": stop,
                "written": written,
            }
        )
        try:
            status, condition = n.optimize(sns, **optimize_kwargs)
        finally:
            _WINDOW.reset(token)
        record = {
            "index": index,
            "first": str(sns[0]),
            "last": str(sns[-1]),
            "snapshots": len(sns),
            "status": str(status),
            "condition": str(condition),
        }
        records.append(record)
        if status != "ok":
            _blank_from(n, snapshots[start:])
            raise OperationalWindowFailed(record)
        written = stop
    return records


def stitched_opex(n) -> float:
    """Operating cost of the stitched dispatch over the full horizon (C4):
    PyPSA's per-snapshot opex (marginal and storage costs) weighted by the
    objective snapshot weightings, summed. Window objectives are never
    summed: the overlap would count twice."""
    opex = n.statistics.opex(aggregate_time="sum")
    total = float(opex.sum()) if len(opex) else 0.0
    return total


def report(
    n,
    cfg,
    *,
    horizon: int,
    overlap: int,
    window_records: list[dict],
    cyclic_off: list[str],
) -> dict:
    """The ``n.meta[META_KEY]`` record of a solved operational run: what it
    ran on (sizes source and digest, the horizon and overlap the windows were
    cut with), each window, the storages whose cyclic flags were off, and the
    stitched opex (C4)."""
    return {
        "version": 1,
        "label": f"operational (rolling {horizon}/{overlap}) on plan sizes",
        "sizes_source": _get(cfg, "fixed_sizes", "off"),
        "sizes_digest": sizes_digest(n, cfg),
        "horizon": horizon,
        "overlap": overlap,
        "windows": window_records,
        "cyclic_off": list(cyclic_off),
        "opex": stitched_opex(n),
        "notes": [
            "The envelope smoothing slice (P^sm, E^sm) is allocated per window "
            "within the fixed store; its floors hold in every window."
        ],
        "opex_basis": (
            "stitched dispatch x objective snapshot weightings over the full "
            "horizon (marginal and storage costs); window objectives are not "
            "summed"
        ),
    }
