"""
The staged templates on the ``year`` time base, for the tests that run over every template.

A year-base template plans 8,760 h per period, so its full three-period plan takes about 25 min (its own
opt-in budget test). The tests over every template solve it on its first period only: the same
network and the same checks, on one year.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "project_templates"))
import eh_concentrator as C  # noqa: E402

#: Staged templates on the year time base; their week build is refused.
YEAR_BASE = {"eh_concentrator_staged"}


def build_for_solve(T, tid: str):
    """``T.BUILDERS[tid]()``, or a year-base template on its first period."""
    if tid == "eh_concentrator_staged":
        return C.build_eh_concentrator_staged(T, years=C.YEARS[:1])
    return T.BUILDERS[tid]()
