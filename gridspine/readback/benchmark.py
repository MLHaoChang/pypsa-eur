"""
Windowed error benchmark between two channel frames (GC-07 part 4).

One engine serves two TRGC studies: RMS model against EMT model (TRGC 08) and
model against field measurement (TRGC 24/25). It follows the validation method
of IEC 61400-27-2 in part: both frames are resampled to
one uniform time grid, the record is split into windows at the event times
(two events give the pre-fault, fault and post-fault windows), and in each
window three errors of the test channel against the reference channel are
reported:

``me``
    mean error, ``mean(test - reference)``;
``mae``
    mean absolute error, ``mean(|test - reference|)``;
``mxe``
    maximum error: the signed error of largest magnitude, with the time it
    occurred.

A sample exactly at an event time belongs to the window that starts there.
A metric passes when its magnitude is at or below its tolerance; the margin is
``tolerance - |value|``. A metric with no tolerance, or a channel missing from
either frame, is ``unknown``, never ``pass``.

Tolerances come from the grid-code profile or the user, per reference
channel: ``{metric: limit}`` for every window, and ``{"<window>.<metric>":
limit}`` for one window, which wins over the plain form.

Not done here (follow-up): the standard's low-pass pre-filter and its split of
each window into transient and quasi-steady-state parts. Every row says so in
its ``method`` field (``METHOD``), so no row reads as a full IEC 61400-27-2
validation.

There is no aggregate verdict: each row stands on its own. A caller that
wants one passes only when every row passes; any unknown row, or no row at
all, makes it unknown.

numpy only.
"""

from __future__ import annotations

import math

import numpy as np

from gridspine.readback.channels import ChannelFrame
from gridspine.schema.contracts import ContractError

METRICS = ("me", "mae", "mxe")
#: Window names when exactly two events are given.
FAULT_WINDOWS = ("pre_fault", "fault", "post_fault")
PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"
#: What every row was computed with, stated so it is never read as more.
METHOD = "whole-window metrics; no IEC pre-filter or sub-window split"


def window_names(n_events: int) -> tuple:
    if n_events == 2:
        return FAULT_WINDOWS
    return tuple(f"w{i}" for i in range(n_events + 1))


def _median_step(frame: ChannelFrame) -> float:
    return float(np.median(np.diff(frame.time)))


def common_grid(reference: ChannelFrame, test: ChannelFrame, dt=None) -> np.ndarray:
    """
    A uniform time grid over the span both frames cover, step ``dt``
    (default: the coarser of the two frames' median steps).
    """
    t0 = max(reference.time[0], test.time[0])
    t1 = min(reference.time[-1], test.time[-1])
    if not t1 > t0:
        raise ContractError(
            f"{reference.source or 'reference'} ({reference.time[0]!r}..{reference.time[-1]!r} s) and "
            f"{test.source or 'test'} ({test.time[0]!r}..{test.time[-1]!r} s) share no time span"
        )
    if dt is None:
        dt = max(_median_step(reference), _median_step(test))
    if (
        isinstance(dt, bool)
        or not isinstance(dt, (int, float))
        or not math.isfinite(dt)
        or dt <= 0
    ):
        raise ContractError(
            f"benchmark step dt must be a number above zero, got {dt!r}"
        )
    n = int(math.floor((t1 - t0) / dt * (1 + 1e-12))) + 1
    if n < 2:
        raise ContractError(
            f"benchmark step {dt!r} s leaves fewer than two samples in {t0!r}..{t1!r} s"
        )
    return np.minimum(t0 + dt * np.arange(n), t1)


def _pairs(channels, reference, test):
    if isinstance(channels, dict):
        pairs = list(channels.items())
    else:
        pairs = [(c, c) for c in channels]
    if not pairs:
        raise ContractError("benchmark needs at least one channel")
    for ref_name, test_name in pairs:
        if not isinstance(ref_name, str) or not isinstance(test_name, str):
            raise ContractError(
                f"benchmark channel names must be text, got {ref_name!r} -> {test_name!r}"
            )
        if (
            ref_name in reference
            and test_name in test
            and reference.units[ref_name] != test.units[test_name]
        ):
            raise ContractError(
                f"channel {ref_name!r} is in {reference.units[ref_name]!r} but {test_name!r} in "
                f"{test.units[test_name]!r}; convert one before benchmarking"
            )
    return pairs


def _limits(tolerances, pairs, windows):
    tolerances = tolerances or {}
    if not isinstance(tolerances, dict):
        raise ContractError(
            f"tolerances must be a mapping, got {type(tolerances).__name__}"
        )
    known = {r for r, _ in pairs}
    allowed = set(METRICS) | {f"{w}.{m}" for w in windows for m in METRICS}
    for ch, lims in tolerances.items():
        if ch not in known:
            raise ContractError(
                f"tolerance for {ch!r}, which is not a benchmarked channel ({sorted(known)})"
            )
        if not isinstance(lims, dict):
            raise ContractError(
                f"tolerances[{ch!r}] must be a mapping, got {type(lims).__name__}"
            )
        for key, lim in lims.items():
            if key not in allowed:
                raise ContractError(
                    f"tolerances[{ch!r}] has {key!r}; allowed {sorted(allowed)}"
                )
            if (
                isinstance(lim, bool)
                or not isinstance(lim, (int, float))
                or not math.isfinite(lim)
                or lim < 0
            ):
                raise ContractError(
                    f"tolerances[{ch!r}][{key!r}] must be a number from 0, got {lim!r}"
                )

    def limit(ch, window, metric):
        lims = tolerances.get(ch, {})
        return lims.get(f"{window}.{metric}", lims.get(metric))

    return limit


def _status(value, limit):
    if value is None or limit is None:
        return UNKNOWN, None
    return (PASS if abs(value) <= limit else FAIL), limit - abs(value)


def benchmark(
    reference: ChannelFrame,
    test: ChannelFrame,
    channels,
    events,
    tolerances=None,
    dt=None,
) -> list:
    """
    One row per channel, window and metric:
    ``{channel, test_channel, window, t_start, t_end, samples, metric, value,
    at_s, unit, limit, margin, status, method, detail}``. ``channels`` is a list of
    names found in both frames or a ``{reference name: test name}`` mapping;
    ``events`` the times (s) the windows split at, strictly increasing and
    inside the shared span.
    """
    grid = common_grid(reference, test, dt)
    ref_on_grid, test_on_grid = reference.resample(grid), test.resample(grid)
    events = [float(e) for e in events]
    for e in events:
        if not math.isfinite(e) or not grid[0] < e < grid[-1]:
            raise ContractError(
                f"event time {e!r} s is not inside the shared span {grid[0]!r}..{grid[-1]!r} s"
            )
    if any(b <= a for a, b in zip(events, events[1:])):
        raise ContractError(f"event times must be strictly increasing, got {events}")
    names = window_names(len(events))
    which = np.searchsorted(np.asarray(events), grid, side="right")
    bounds = [grid[0], *events, grid[-1]]
    for w, name in enumerate(names):
        if not np.any(which == w):
            raise ContractError(
                f"window {name!r} ({bounds[w]!r}..{bounds[w + 1]!r} s) holds no sample at this step"
            )
    pairs = _pairs(channels, reference, test)
    limit = _limits(tolerances, pairs, names)
    rows = []
    for ref_name, test_name in pairs:
        missing = [
            f"{f.source or role} has no channel {n!r}"
            for f, n, role in (
                (reference, ref_name, "reference"),
                (test, test_name, "test"),
            )
            if n not in f
        ]
        err = None
        if not missing:
            err = test_on_grid.channels[test_name] - ref_on_grid.channels[ref_name]
        unit = reference.units.get(ref_name, test.units.get(test_name, ""))
        for w, name in enumerate(names):
            sel = which == w
            t_w = grid[sel]
            values = {m: (None, None) for m in METRICS}
            if err is not None:
                e = err[sel]
                k = int(np.argmax(np.abs(e)))
                values = {
                    "me": (float(np.mean(e)), None),
                    "mae": (float(np.mean(np.abs(e))), None),
                    "mxe": (float(e[k]), float(t_w[k])),
                }
            for metric in METRICS:
                value, at = values[metric]
                lim = limit(ref_name, name, metric)
                status, margin = _status(value, lim)
                detail = (
                    "; ".join(missing)
                    if missing
                    else ("" if lim is not None else "no tolerance given")
                )
                rows.append(
                    {
                        "channel": ref_name,
                        "test_channel": test_name,
                        "window": name,
                        "t_start": float(bounds[w]),
                        "t_end": float(bounds[w + 1]),
                        "samples": int(sel.sum()),
                        "metric": metric,
                        "value": value,
                        "at_s": at,
                        "unit": unit,
                        "limit": lim,
                        "margin": margin,
                        "status": status,
                        "method": METHOD,
                        "detail": detail,
                    }
                )
    return rows
