"""An EH study on a multi-period network states its cost basis.

The adequacy report's CostBlock says ``npv_multi_period`` for a
multi-investment-period solve, and the EH report copies it. The EH model
used to accept only ``"multi_period"``, so every multi-period study failed
validation when it assembled its report (E2E demo plan 2026-10-14, slice 8).
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pytest

from models.energy_hub import ReferenceDesignReport, default_weak_flexible_pack
from services.adequacy import eh_study as S
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import VOLL, certifiable_weak_network

PERIODS = (2030, 2035)


def _two_period(n):
    """The same hub in two investment periods (one tile per period)."""
    hours = len(n.snapshots)
    frames = {(c, a): getattr(n, c)[a].copy()
              for c, a in (("loads_t", "p_set"),)}
    weights = n.snapshot_weightings.copy()
    n.set_snapshots(pd.MultiIndex.from_product([PERIODS, range(hours)]))
    n.investment_periods = list(PERIODS)
    n.investment_period_weightings["years"] = [5.0, 10.0]
    for col in weights.columns:
        n.snapshot_weightings[col] = list(weights[col]) * len(PERIODS)
    for (c, a), df in frames.items():
        getattr(n, c)[a] = pd.DataFrame(
            list(df.to_numpy()) * len(PERIODS), index=n.snapshots, columns=df.columns)
    # a storage unit: PyPSA's Network.copy() drops a multi-period snapshot
    # index's name, which breaks a storage unit's spillage variables
    n.add("StorageUnit", "bess", bus="hub", p_nom=5.0, max_hours=2.0)
    return n


def _run(n):
    from services.pypsa_service import PyPSAService

    PyPSAService.set_network(n)
    cfg = SolverConfig(voll=VOLL, multi_investment_periods=n.snapshots.nlevels > 1)
    return S.run_eh_study(
        n, default_weak_flexible_pack(), cfg,
        lock=PyPSAService.get_lock(), stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(), stages=["apply_pack", "ens_solve"],
        budget_solves=5,
    )


@pytest.mark.parametrize("multi, basis", [(True, "npv_multi_period"), (False, "single_period")])
def test_the_report_states_the_period_basis(multi, basis):
    n = certifiable_weak_network()
    rep = _run(_two_period(n) if multi else n)
    assert isinstance(rep, ReferenceDesignReport)
    assert rep.sections["target"].status == "ok", rep.sections["target"]
    assert rep.period_basis == basis
    assert ReferenceDesignReport.model_validate(rep.model_dump()).period_basis == basis
