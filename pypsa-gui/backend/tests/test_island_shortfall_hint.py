"""UX review N8 (decision C, landing 1): the island rows' missing-MW hint reaches a Guided user.

`_diagnose_infeasibility` already says "the largest load step is 10.00 MW … 3.00 MW is missing" on
an infeasible island-rows solve, but only in the solver log. The hub study's failed ens_solve reason
(which Goal prints as "The last study failed: …") did not carry it. One pure helper,
`island_shortfalls(facts)`, is the single source for both the log line and the reason.
"""
from __future__ import annotations

import queue
import threading

import pytest

from services.adequacy import eh_study as S
from services.solver import island as isl
from services.solver.diagnostics import _diagnose_infeasibility
from services.solver_service import SolverConfig
from tests.test_island_rows import _a, _genset, _hub, _seamless, _units


def _short_hub():
    """10 MW load, the BESS capped at 7 MW, a cold 6 MW genset: E2 (the 10 MW step) is 3 MW short."""
    n = _hub(load=10.0, gensets=[("g", 6.0)])
    n.storage_units.at["bess", "p_nom_max"] = 7.0
    return n


def _island():
    return _seamless(_units(gensets={"g": _genset(p_min_pu=_a(0.3))}))


def test_island_shortfalls_is_pure_and_lists_only_the_rows_that_are_short():
    facts = {"events": [
        {"row": "Island-e2-up", "step_mw": 10.0, "gfm_mw": 7.0, "genset_mw": 0.0},
        {"row": "Island-n1-up", "step_mw": 4.0, "gfm_mw": 7.0, "genset_mw": 0.0}]}
    assert isl.island_shortfalls(facts) == [
        {"row": "Island-e2-up", "step_mw": 10.0, "reach_mw": 7.0, "missing_mw": 3.0,
         "gfm_mw": 7.0, "genset_mw": 0.0}]
    assert isl.island_shortfalls({"events": [{"row": "r", "step_mw": 1.0, "gfm_mw": 5.0, "genset_mw": 0.0}]}) == []
    assert isl.island_shortfalls({}) == [] and isl.island_shortfalls(None) == []


def test_the_log_line_is_unchanged_and_comes_from_the_helper():
    """Byte-identical text, so the existing diagnostics tests stay as they are."""
    n = _short_hub()
    status, cond = n.optimize(solver_name="highs",
                              extra_functionality=isl.island_extra_functionality(_island()))
    assert "infeasible" in str(cond).lower()
    log: queue.SimpleQueue = queue.SimpleQueue()
    _diagnose_infeasibility(n, None, log)
    text = " ".join(log.get() for _ in range(log.qsize()))
    assert ("[INFEASIBLE]   • Island-e2-up: the largest load step is 10.00 MW, but the grid formers "
            "can pick up at most 7.00 MW at the instant (GFM storage at its rating 7.00 MW, the online "
            "gensets' load step 0.00 MW): 3.00 MW is missing. Raise a grid-forming battery's p_nom_max, "
            "or commit or add a genset.") in text


def test_a_failed_ens_solve_names_the_missing_mw_in_its_reason(monkeypatch):
    """The study path: `_stage_ens_solve` -> `run_simulation` solves `st.network` with the island rows,
    and the failure reason says which row is short and by how much (appended, the ENS wording stays)."""
    monkeypatch.setattr(isl, "load_project_island", lambda *a, **k: _island())
    n = _short_hub()
    st = S._Study(cfg=SolverConfig(island_constraints=True), network=n, lock=threading.RLock(),
                  stop_event=threading.Event(), log_queue=queue.SimpleQueue(), solves=0, records=[],
                  sections={}, failed_reason=None)
    S._stage_ens_solve(st)
    reason = st.failed_reason
    assert reason and "infeasible" in reason
    assert "the pack's ENS target cannot be met" in reason                       # the old wording stays
    assert "Island-e2-up: the largest load step is 10.00 MW" in reason
    assert "at most 7.00 MW" in reason and "3.00 MW is missing" in reason


def test_without_island_facts_the_reason_is_unchanged(monkeypatch):
    """A plain infeasible study (no island rows): the reason is the old one, byte for byte."""
    n = _hub(load=10.0)
    n.generators.at["supply", "p_nom"] = 1.0          # not enough supply at all: infeasible without rows
    n.links.at["imp", "p_nom"] = 1.0
    st = S._Study(cfg=SolverConfig(), network=n, lock=threading.RLock(), stop_event=threading.Event(),
                  log_queue=queue.SimpleQueue(), solves=0, records=[], sections={}, failed_reason=None)
    S._stage_ens_solve(st)
    if st.failed_reason is None:
        pytest.skip("the plain case solved; no failure to compare")
    assert st.failed_reason.endswith(
        "the pack's ENS target cannot be met by this network under the pack overlay") \
        or "infeasible" not in st.failed_reason
    assert "Island-" not in st.failed_reason
