"""LC-06 S11, LC-D22: undo and project checkpoints return the exact inputs, never results.

The seam is the assistant's ordinary dispatch (``_dispatch_real_tool_call``, one
undo step per turn) of ``update_component``, ``undo_last``, ``create_project_snapshot``
and ``restore_project_snapshot``, and a real queued solve. The oracle is a
fingerprint of the network's input tables taken before and after, and the checkpoint
file read with ``pypsa`` alone.

Hand oracle, the baseline project (G1 20 MW at 10 EUR/MWh, L1 5 MW, two snapshots):
- an edit to G1's marginal cost (10 -> 30) changes the fingerprint and makes the
  solved results stale; ``undo_last`` returns the fingerprint to the exact original,
  and because the inputs are again the solved ones the results are current again;
  a second undo is refused ("Nothing to undo");
- a checkpoint taken before any solve holds 10 EUR/MWh and no results (read from
  its ``network.nc``). Restoring it after a solve and an edit returns the original
  fingerprint but not the results: the network has none and they are not current;
- restoring a checkpoint id that does not exist is refused and changes nothing.
"""
# ruff: noqa: F811 -- shared public harness fixtures
import hashlib

import pypsa
import pytest
from fastapi import HTTPException

from services import chat_tools, undo_service
from services.pypsa_service import PyPSAService
from services.workflow_tools import solve_freshness
from tests.test_assistant_capabilities import dispatch, output, session, solve_to_completion  # noqa: F401
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

EDIT = {"component_class": "Generator", "name": "G1", "attrs": {"marginal_cost": 30}}


def fingerprint(n=None):
    n = n or PyPSAService.get_network()
    parts = [getattr(n, k).sort_index().sort_index(axis=1).to_json() for k in ("buses", "generators", "loads")]
    parts.append(n.snapshots.astype(str).str.cat(sep=","))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def cost():
    return float(PyPSAService.get_network().generators.at["G1", "marginal_cost"])


def test_undo_returns_the_exact_inputs_and_the_results_that_matched_them(baseline, session):
    undo_service.clear()
    solve_to_completion(session)
    original = fingerprint()
    assert solve_freshness()["results_current"] is True
    assert PyPSAService.get_network().objective == pytest.approx(100.0, rel=1e-9)   # 10 x 5 MW x 2 h

    session._undo_snapshotted = []
    dispatch(session, "update_component", EDIT)
    assert cost() == 30.0 and fingerprint() != original
    assert solve_freshness()["results_current"] is False

    assert output(dispatch(session, "undo_last", {}))["_edit_receipt"]["rows"][0]["readback"] == 10.0
    assert fingerprint() == original and cost() == 10.0
    assert solve_freshness()["results_current"] is True
    assert undo_service.depth() == 0
    with pytest.raises(HTTPException) as refused:
        chat_tools.undo_last()
    assert refused.value.detail == "Nothing to undo"
    assert fingerprint() == original


def test_a_checkpoint_restores_the_inputs_it_held_and_not_the_results(baseline, session):
    ctx, _project, root = baseline
    original = fingerprint()
    checkpoint = chat_tools.create_project_snapshot("Tool Baseline", "S0")
    saved = pypsa.Network(str(root / "snapshots" / checkpoint.id / "network.nc"))
    assert fingerprint(saved) == original and float(saved.generators.at["G1", "marginal_cost"]) == 10.0
    assert [s.has_results for s in chat_tools.list_project_snapshots("Tool Baseline")] == [False]

    solve_to_completion(session)
    session._undo_snapshotted = []
    dispatch(session, "update_component", EDIT)
    assert fingerprint() != original and cost() == 30.0

    restored = output(dispatch(session, "restore_project_snapshot",
                               {"name": "Tool Baseline", "snapshot_id": checkpoint.id}))
    assert restored["restored"] == checkpoint.id
    assert fingerprint() == original and cost() == 10.0
    assert solve_freshness()["results_current"] is False
    assert PyPSAService.get_network().generators_t.p.empty

    with pytest.raises(HTTPException):
        chat_tools.restore_project_snapshot("Tool Baseline", "no-such-checkpoint")
    assert fingerprint() == original
