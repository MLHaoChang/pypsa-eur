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

import local_mode


def decision_studies_enabled() -> bool:
    """
    The one predicate: local (single-user) mode.

    Until 2026-10-13 a flag (`PYPSAGUI_DECISION_STUDIES`) also had to be set;
    U3 dropped it once OPEN-ITEMS 1 (the process-global user-timeseries store,
    PR #71) was closed. Multi-user mode still refuses: a study builds, forks
    and solves its own projects in the background, and that path has not been
    reviewed for several tenants sharing one server (U3; review v2 BC-6 named
    OPEN-ITEMS 1 as the reason, and that reason is gone, not the review).
    """
    return local_mode.is_local_mode()
