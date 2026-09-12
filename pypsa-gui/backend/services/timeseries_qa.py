"""
Data-quality checks on the time series a user uploads.

`validation_service` asks "will PyPSA fail on this?". This module asks the
question that comes before it: **is this series what the user thinks it is?**
Everything here passes the LP cleanly and produces a confident, wrong answer —
a solar profile that is 8760 zeros builds no solar and reports the plan as
optimal; a demand column pasted in kW makes a country look like a village;
one decimal-place typo sets the peak, and the peak sizes the fleet.

Every finding is a WARNING by the severity policy in `validation_service`:
"error = PyPSA will fail on this; warning = the solve will run but the result
is probably nonsense". None of these makes PyPSA fail, so none may block a
run — the user is told, and decides.

Scope is deliberately the three profile kinds the upload routes write
(`PROFILE_KIND_ENUM` — loads / generators / links), because a check's message
has to be TRUE of the attribute it fires on. "All zero" is a broken solar
profile and a perfectly ordinary `p_min_pu`; a module that scanned every
time-varying column would have to hedge every sentence into uselessness.
"""
from __future__ import annotations

import math

import numpy as np

# (component class, PyPSA _t attribute, column, what a zero series means)
_PROFILE_TARGETS: tuple[tuple[str, str, str, str], ...] = (
    ("Load", "loads_t", "p_set",
     "this load contributes no demand at all — the LP serves nothing for it"),
    ("Generator", "generators_t", "p_max_pu",
     "this generator can never dispatch — the LP will build none of it and "
     "report the plan as optimal"),
    ("Link", "links_t", "p_max_pu",
     "this link can never carry flow — whatever it connects is islanded for "
     "the whole horizon"),
)

# A series shorter than this is not evidence of anything: a 4-snapshot test
# network is legitimately flat.
MIN_SNAPSHOTS_FOR_SHAPE_CHECKS = 24

# One value this many times the series' own median is a decimal-place typo,
# not a peak. Deliberately blunt — a factor of 50 is far outside any real
# load or availability shape, so the check almost never fires on real data.
SPIKE_MULTIPLE = 50.0

# Order-of-magnitude population outlier: kW pasted into an MW column is 1000x,
# and so is GW. Anything smaller is a modelling choice, not a unit error.
SCALE_MULTIPLE = 1000.0

# The scale check compares a load against its PEERS, so it needs a population
# to be a peer of.
MIN_LOADS_FOR_SCALE_CHECK = 3


def _finite_values(frame, column) -> np.ndarray | None:
    """The column's finite values as float64, or None when unusable."""
    try:
        values = frame[column].to_numpy(dtype=float)
    except (TypeError, ValueError, KeyError):
        return None
    finite = values[np.isfinite(values)]
    return finite if finite.size else None


def _check_column(component_class: str, name: str, attribute: str,
                  values: np.ndarray, zero_meaning: str) -> list:
    """The shape checks for one series. Order matters: most specific first."""
    from services.validation_service import Issue

    out: list[Issue] = []
    n_values = int(values.size)
    long_enough = n_values >= MIN_SNAPSHOTS_FOR_SHAPE_CHECKS

    if long_enough and bool(np.all(values == 0.0)):
        out.append(Issue(
            "warning", "timeseries_all_zero", component_class, name,
            f"time-varying {attribute} is zero in all {n_values} snapshots: "
            f"{zero_meaning}. The solve will succeed and the result will be "
            f"silently meaningless — check the column that was uploaded."))
        # A zero series is constant too; saying so twice adds nothing.
        return out

    if long_enough and float(np.ptp(values)) == 0.0:
        out.append(Issue(
            "warning", "timeseries_frozen", component_class, name,
            f"time-varying {attribute} never changes ({values[0]:g} in all "
            f"{n_values} snapshots). A flat time series is usually one value "
            f"pasted down a column, or a sensor that stopped reporting. If it "
            f"is intentional, set the static {attribute} instead and drop the "
            f"profile."))

    negatives = int((values < 0).sum())
    if negatives:
        if attribute == "p_set":
            out.append(Issue(
                "warning", "timeseries_negative", component_class, name,
                f"time-varying p_set is negative in {negatives} of {n_values} "
                f"snapshots (min {values.min():g}). Negative demand is an "
                f"INJECTION — legal in PyPSA, and almost always a sign error "
                f"in an imported profile."))
        else:
            out.append(Issue(
                "warning", "timeseries_negative", component_class, name,
                f"time-varying {attribute} is negative in {negatives} of "
                f"{n_values} snapshots (min {values.min():g}). Availability is "
                f"a fraction of nameplate and cannot be below zero."))

    median = float(np.median(values))
    peak = float(values.max())
    if median > 0 and peak > median * SPIKE_MULTIPLE:
        out.append(Issue(
            "warning", "timeseries_spike", component_class, name,
            f"time-varying {attribute} peaks at {peak:g}, {peak / median:.0f}x "
            f"its own median of {median:g}. A single outlier that far out is "
            f"usually a misplaced decimal point — and the peak is what sizes "
            f"the fleet, so one bad cell propagates into every capacity "
            f"number."))
    return out


def _mean_demand(n, name: str) -> float | None:
    """A load's mean demand over the horizon — from its profile, else static."""
    frame = getattr(n.loads_t, "p_set", None)
    if frame is not None and name in getattr(frame, "columns", []):
        values = _finite_values(frame, name)
        if values is not None:
            return float(np.abs(values).mean())
    try:
        static = float(n.loads.at[name, "p_set"])
    except (KeyError, TypeError, ValueError):
        return None
    return abs(static) if math.isfinite(static) else None


def _check_load_scale(n) -> list:
    """
    One load three orders of magnitude away from its peers.

    Unit confusion is invisible to every other check: a demand column in kW is
    finite, positive, correctly shaped and covers the horizon. What gives it
    away is the COMPANY it keeps, which is why this is the one check that
    needs the rest of the network to say anything at all.
    """
    from services.validation_service import Issue

    if n.loads.empty or len(n.loads.index) < MIN_LOADS_FOR_SCALE_CHECK:
        return []
    means = {str(name): _mean_demand(n, str(name)) for name in n.loads.index}
    positive = {k: v for k, v in means.items() if v is not None and v > 0}
    if len(positive) < MIN_LOADS_FOR_SCALE_CHECK:
        return []

    reference = float(np.median(list(positive.values())))
    if not (reference > 0):
        return []

    out: list[Issue] = []
    for name, mean in sorted(positive.items()):
        # Inclusive on purpose. kW pasted into an MW column is EXACTLY 1000x,
        # and a strict `>` puts the check's blind spot precisely on the case
        # it exists to catch.
        if mean >= reference * SCALE_MULTIPLE:
            ratio, direction, unit = mean / reference, "larger", "MW where the others are in kW"
        elif mean * SCALE_MULTIPLE <= reference:
            ratio, direction, unit = reference / mean, "smaller", "kW where the others are in MW"
        else:
            continue
        out.append(Issue(
            "warning", "timeseries_scale_outlier", "Load", name,
            f"mean demand {mean:g} MW is {ratio:.0f}x {direction} than the "
            f"median load on this network ({reference:g} MW). A gap that wide "
            f"is usually a unit error — this series may be in {unit}."))
    return out


def check_timeseries_quality(n) -> list:
    """
    Every data-quality finding for the uploadable profile kinds.

    Cheap by construction: one vectorised pass per time-varying column, no
    solve, no copy of the frame. Called from `validate_for_run`, which runs on
    every preflight AND before every solve.
    """
    out: list = []
    snapshots = getattr(n, "snapshots", None)
    if snapshots is None or len(snapshots) == 0:
        return out

    for component_class, frame_attr, attribute, zero_meaning in _PROFILE_TARGETS:
        container = getattr(n, frame_attr, None)
        frame = getattr(container, attribute, None) if container is not None else None
        if frame is None or getattr(frame, "empty", True):
            continue
        for column in frame.columns:
            values = _finite_values(frame, column)
            if values is None:
                continue          # all-NaN — `_check_nonfinite_inputs` owns it
            out += _check_column(component_class, str(column), attribute,
                                 values, zero_meaning)

    out += _check_load_scale(n)
    return out
