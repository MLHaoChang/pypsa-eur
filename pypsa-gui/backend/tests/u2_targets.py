"""
Shared helpers of the U2 port targets (plan
docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md, WP2).

Not collected by pytest (no ``test_`` prefix). ``pending(wp)`` marks a target
whose engine seam is built by a LATER work package: it must fail now
(``strict=True``), so the day the seam lands the target flips to XPASS and the
marker has to go (the WP's acceptance is "the WP2 targets go green").

``WP0`` is the frozen pre-U2 record (``tests/fixtures/u2_pre_numbers.json``).
"""
from __future__ import annotations

import json
import math
import pathlib

import pandas as pd
import pytest

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
WP0 = json.loads((FIXTURES / "u2_pre_numbers.json").read_text(encoding="utf-8"))
DE, TOU = "de_industrial_illustrative", "tou_reference_illustrative"
SEEDS = (DE, TOU)
SIX = ("energy", "demand", "capacity", "fixed", "network", "export_credit")

# A study-shaped fake ref for in-memory binds (no Library, no org).
FAKE_REF = {"id": "decision-study:fake:" + "5" * 32 + ":export", "version": 1,
            "hash": "f" * 64, "source": "decision_study"}


def pending(wp: str, why: str = ""):
    """The target waits for ``wp`` (strict xfail: it must fail until then)."""
    return pytest.mark.xfail(strict=True, reason=f"U2 {wp}: {why or 'seam not built yet'}")


def close(got, want, *, rel: float = 1e-9, abs_: float = 1e-9) -> bool:
    if got is None or want is None:
        return got is want
    return math.isclose(float(got), float(want), rel_tol=rel, abs_tol=abs_)


def flat_resolver(price: float, snapshots):
    """`resolve_ref` for `binding.bind_commercial`: a flat series on the axis."""
    series = pd.Series(float(price), index=pd.DatetimeIndex(snapshots), name="price")
    return lambda _ref: series


def series_resolver(series: pd.Series):
    return lambda _ref: series


def golden_copy(option: str):
    """
    An independent copy of the S5 golden network of `option` (GS-solved,
    cached per process by `site_fixture`); the solver model is detached first
    (PyPSA refuses to copy a network with an attached solver model).
    """
    from tests.golden import site_fixture as SF

    n = SF.solve_site_option(option)[0]
    model = getattr(n, "_model", None)
    if model is not None and getattr(model, "solver_model", None) is not None:
        model.solver_model = None
    return n.copy()
