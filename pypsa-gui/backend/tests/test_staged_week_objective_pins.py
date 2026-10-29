"""The week-based staged templates are unchanged by the `year` time base (grid-to-process 09).

Every staged template's objective, solved as `test_eh_ammonia_staged._lin_plan` does (energization applied, HiGHS,
linearised UC), pinned to the unit from master 610f1c78, before `stage_network` took a time base.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_site as S  # noqa: E402
import eh_templates as T  # noqa: E402

#: EUR, master 610f1c78; each a 3-period plan of one 168 h week per period.
PINS = {
    "eh_datacenter_staged": 455703702.708,
    "eh_h2_hub_staged": 160095471.207,
    "eh_microgrid_staged": 50409867.987,
    "eh_ammonia_staged": 171928188.451,
    "eh_site": 87313774.84,
}


def _build(tid):
    return S.build_eh_site() if tid == "eh_site" else T.BUILDERS[tid]()


def test_every_staged_template_is_pinned():
    assert {t for t in T.BUILDERS if t.endswith("_staged")} | {"eh_site"} == set(PINS)


@pytest.mark.live_solve
@pytest.mark.parametrize("tid", sorted(PINS))
def test_week_template_objective_is_unchanged(tid):
    from services.adequacy.energization import apply_energization

    n = _build(tid)
    assert len(n.snapshots) == 3 * T.HOURS
    n.snapshots.name = "snapshot"
    apply_energization(n)
    status, cond = n.optimize(solver_name="highs", multi_investment_periods=True,
                              linearized_unit_commitment=True)
    assert (status, cond) == ("ok", "optimal")
    assert n.objective == pytest.approx(PINS[tid], abs=1.0)
