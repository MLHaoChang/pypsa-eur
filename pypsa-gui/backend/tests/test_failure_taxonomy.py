"""
Unit tests for the solve-failure taxonomy (services/failure_taxonomy.py).

Pure mapping from a finished solve's ``(status, condition)`` to an actionable
failure card (or None on success/abort). No PyPSA / network needed.
"""
from __future__ import annotations

import pytest

from services.failure_taxonomy import classify_failure


@pytest.mark.parametrize(
    "status,condition",
    [
        ("ok", "optimal"),
        ("optimal", "optimal"),
        ("ok", "lopf+ac_pf_ok"),
        # LP succeeded, Stage-2 AC PF failed → status stays ok → not a failure.
        ("ok", "optimal; ac_pf_failed: singular matrix"),
    ],
)
def test_success_returns_none(status, condition):
    assert classify_failure(status, condition) is None


@pytest.mark.parametrize(
    "status,condition",
    [
        ("aborted", "user_aborted:native_solve"),
        ("aborted", "user_aborted:before LP solve"),
        ("warning", "user_aborted:whatever"),  # status non-success but aborted cond wins
    ],
)
def test_abort_returns_none(status, condition):
    assert classify_failure(status, condition) is None


def test_validation_failure():
    f = classify_failure("error", "validation_failed")
    assert f is not None
    assert f["category"] == "validation"
    assert "Issues" in f["hint"] or "error" in f["hint"].lower()
    assert f["detail"] == "validation_failed"


def test_infeasible():
    f = classify_failure("warning", "infeasible")
    assert f["category"] == "infeasible"
    assert f["title"]
    assert f["hint"]


def test_unbounded():
    f = classify_failure("warning", "unbounded")
    assert f["category"] == "unbounded"


def test_infeasible_or_unbounded_precedence():
    # Must classify as the combined category, NOT plain "infeasible"/"unbounded"
    # (the substring rule order is load-bearing here).
    f = classify_failure("warning", "infeasible_or_unbounded")
    assert f["category"] == "infeasible_or_unbounded"


def test_time_limit():
    f = classify_failure("warning", "time_limit")
    assert f["category"] == "time_limit"


@pytest.mark.parametrize(
    "condition",
    ["matrix is singular", "numerical difficulties", "ill-conditioned problem"],
)
def test_numerical(condition):
    f = classify_failure("error", condition)
    assert f["category"] == "numerical"


@pytest.mark.parametrize(
    "condition",
    [
        "'snapshot' is not a valid dimension or coordinate",
        "cannot include dtype 'M' in a buffer",
        "KeyError: DatetimeIndex(...) not in index",
        # str(exc) shapes that DON'T contain the class name — must still map to
        # setup_error via message-pattern needles (QA-flagged coverage gap).
        "'tuple' object has no attribute 'lower'",
        "DatetimeIndex([...]) are in the [columns], but not in index",
    ],
)
def test_setup_error(condition):
    f = classify_failure("error", condition)
    assert f["category"] == "setup_error"


def test_suboptimal():
    f = classify_failure("warning", "suboptimal")
    assert f["category"] == "suboptimal"


def test_unknown_falls_back_to_solver_error():
    f = classify_failure("warning", "some_unrecognised_condition_token")
    assert f["category"] == "solver_error"
    assert f["detail"] == "some_unrecognised_condition_token"


def test_none_and_empty_inputs():
    # Defensive: None/empty must not crash; treated as non-success unknown.
    assert classify_failure(None, None)["category"] == "solver_error"
    assert classify_failure("", "")["category"] == "solver_error"


def test_card_shape_is_complete():
    f = classify_failure("warning", "infeasible")
    assert set(f.keys()) == {"category", "title", "hint", "detail"}
    assert all(isinstance(v, str) for v in f.values())
