"""
Drift-guard tests for the ProjectSolverState shape (multi-project Phase A1).

ProjectSolverState (services/project_context.py) is the single source of truth
for the solver lifecycle + result state. `routers.simulation._state` is built
from it and `routers.projects._RESULTS_STATE_KEYS` is aliased to its
RESULT_STATE_KEYS. These tests fail loudly if any of those drift apart — e.g. a
new `_state` key added to the dataclass but not classified, or the persistence
key list diverging from the state shape.
"""
from __future__ import annotations

from dataclasses import fields as dc_fields

from routers import simulation as sim_router
from routers.projects import _RESULTS_STATE_KEYS
from services.project_context import (
    LIFECYCLE_KEYS,
    RESULT_STATE_KEYS,
    ProjectSolverState,
)


def _field_names() -> set[str]:
    return {f.name for f in dc_fields(ProjectSolverState)}


def test_state_dict_keys_match_solver_state_fields():
    # The live `_state` dict must carry EXACTLY the ProjectSolverState fields:
    # no orphan key (the per-project dispatcher wouldn't reset it) and none
    # missing (an endpoint would KeyError). `_state` is built from
    # ProjectSolverState().as_dict() at import; this also catches a runtime
    # orphan written via `_state_update(orphan=…)` (conftest's reset only
    # re-sets known keys, it doesn't rebuild the dict, so a stray key persists).
    assert set(sim_router._state.keys()) == _field_names()


def test_every_field_is_classified_exactly_once():
    # Every field is exactly one of lifecycle / config / result-state — the
    # partition the dispatcher relies on (reset lifecycle, persist result-state).
    classified = set(LIFECYCLE_KEYS) | {"solver_config"} | set(RESULT_STATE_KEYS)
    assert classified == _field_names()
    # Disjoint groups: counts sum to the field total (no key in two groups).
    assert len(LIFECYCLE_KEYS) + 1 + len(RESULT_STATE_KEYS) == len(_field_names())


def test_results_state_keys_are_single_source():
    # projects._RESULTS_STATE_KEYS must BE the canonical RESULT_STATE_KEYS
    # (same content + order — the save/restore paths iterate it).
    assert tuple(_RESULTS_STATE_KEYS) == tuple(RESULT_STATE_KEYS)


def test_initial_values_match_legacy_literal():
    sentinel = object()
    d = ProjectSolverState(solver_config=sentinel).as_dict()
    assert d["status"] == "idle"
    assert d["solver_config"] is sentinel
    # Every other field initialises to None (matches the old inline `_state` literal).
    for key, val in d.items():
        if key in ("status", "solver_config"):
            continue
        assert val is None, f"{key} should default to None, got {val!r}"
