"""
Findings from the PR #55 review pass that the automatic fixer skipped as
"behaviour changes" — fixed deliberately, each pinned here.

1. Bad grid-side data must refuse, like bad hub-side data. A grid unit with
   an outage rate outside [0, 1) used to fall back silently to an unbounded
   grid (v1), which is optimistic.
2. A hub with no sampled unit of its own still gets its Link ranked. The
   fmea_top stage returned before the Class-B Link sweep whenever the COPT
   had nothing to screen.
3. A sampled Link plus a common-mode event, with no sampled grid, is
   labelled with both.
"""
from __future__ import annotations

import queue
import threading

import numpy as np
import pytest

from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
from services.adequacy import eh_stages as ST
from services.adequacy import eh_study as S
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import VOLL, certifiable_weak_network

OVERLAY = default_strong_grid_pack().import_overlay


def _freeze(n, **kw):
    return ST.freeze_fixed_plan(n, SolverConfig(voll=VOLL), threading.Lock(),
                                overlay=OVERLAY, **kw)


def test_a_bad_grid_side_rate_refuses_the_snapshot():
    n = certifiable_weak_network()
    n.generators.at["grid_supply", "outage_rate_value"] = 1.5
    frozen = _freeze(n)
    assert frozen.mc_inputs is None and frozen.zonal_inputs is None
    assert "grid_supply" in (frozen.mc_error or "")
    assert "[0, 1)" in frozen.mc_error


def test_a_generic_grid_snapshot_failure_still_falls_back(monkeypatch):
    """
    Only bad DATA refuses; any other snapshot failure keeps the documented
    fallback (the area is unsampled, with the reason).
    """
    from services.adequacy import mc as MCmod

    real = MCmod.snapshot_inputs

    def refuse_grid(n, **kw):
        if "grid" in n.buses.index and "hub" not in n.buses.index:
            raise RuntimeError("synthetic failure")
        return real(n, **kw)

    monkeypatch.setattr(MCmod, "snapshot_inputs", refuse_grid)
    frozen = _freeze(certifiable_weak_network())
    assert frozen.mc_inputs is not None
    assert "synthetic failure" in frozen.scope["note"]


def test_sampled_link_plus_common_mode_without_a_grid_is_labelled_with_both():
    n = certifiable_weak_network()
    n.add("Carrier", "grid_mix")
    n.generators.at["grid_supply", "carrier"] = "grid_mix"
    for c in ("outage_rate_value", "mttr_hours"):
        n.generators.at["grid_supply", c] = np.nan
    n.links["common_mode_rate"] = np.nan
    n.links["common_mode_mttr_hours"] = np.nan
    n.links.at["import_poc", "common_mode_rate"] = 0.05
    n.links.at["import_poc", "common_mode_mttr_hours"] = 24.0
    frozen = _freeze(n)
    assert frozen.scope["import_model"] == "sampled_unit"
    assert frozen.scope["import_firmness"] == "outage_and_common_mode_sampled"


@pytest.mark.live_solve
def test_fmea_top_ranks_the_link_when_the_hub_has_no_sampled_unit():
    """
    Firm Link that still has a Class-B contingency (rate, no MTTR), hub
    units without outage data, sampled grid: the MC certifies through the
    grid area, the COPT has nothing to screen, and the Link must still be
    ranked by its Class-B row.
    """
    from services.pypsa_service import PyPSAService

    n = certifiable_weak_network()
    n.add("Carrier", "hub_fuel")
    for g in ("base", "peaker"):
        n.generators.at[g, "carrier"] = "hub_fuel"
        n.generators.at[g, "outage_rate_value"] = np.nan
        n.generators.at[g, "mttr_hours"] = np.nan
    n.links.at["import_poc", "mttr_hours"] = np.nan
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=10.0)})
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=VOLL), lock=PyPSAService.get_lock(),
        stop_event=threading.Event(), log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "fmea_top", "assemble"),
        budget_solves=30)
    sec = report.sections["fmea_top"]
    assert report.completeness["fmea_top"] == "ok", sec.note
    top = sec.payload["top"]
    assert [m["failure_class"] for m in top] == ["B"]
    assert top[0]["name"] == "import_poc"
    assert "COPT screening skipped" in (sec.payload.get("copt_error") or "")
