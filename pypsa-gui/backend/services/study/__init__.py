"""
Decision studies (guided investment study, MVP-1).

Spec: docs/superpowers/specs/2026-09-28-guided-investment-study-design.md
Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md

S1 ships the record and its persistence (`store`). The question pack, the
mutation boundary and the option runner (S4, including `forks.py`) build on
it; nothing here touches a network or the process-global user-timeseries
store (OPEN-ITEMS 1).
"""
from __future__ import annotations

import os

import local_mode

# The feature flag. It ENABLES the study routes in local (single-user) mode
# only; in auth (multi-user) mode they refuse whatever it says, until
# OPEN-ITEMS 1 is closed (review v2 BC-6). It is not an operator override.
FLAG_ENV = "PYPSAGUI_DECISION_STUDIES"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def flag_set() -> bool:
    """True when `PYPSAGUI_DECISION_STUDIES` is truthy. Read per call."""
    return os.environ.get(FLAG_ENV, "").strip().lower() in _TRUTHY


def decision_studies_enabled() -> bool:
    """
    The one predicate: local mode AND the flag.

    Auth mode is refused unconditionally because a study's Expert-view fork
    is saved by the ordinary foreground save, which reapplies the
    process-global `_user_ts` (`services/user_timeseries.py`) — the
    cross-tenant path OPEN-ITEMS 1 describes, which a study would load with a
    client's meter data.
    """
    return local_mode.is_local_mode() and flag_set()
