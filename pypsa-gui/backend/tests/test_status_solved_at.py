"""Control-strategy walkthrough issue 06 / improvement 2: the source-run line names when the dispatch solve ran.

The time is recorded beside the solve's input fingerprint in ``n.meta`` (so it is saved with the project and
survives a reload), and /simulation/status reports it only while the dispatch exists.
"""
import re

import pypsa
import pytest

from routers import simulation
from services import workflow_tools


@pytest.fixture
def n(monkeypatch):
    net = pypsa.Network()
    net.set_snapshots(range(2))
    monkeypatch.setattr(workflow_tools, "_owning_context", lambda _n: None)
    monkeypatch.setattr(workflow_tools, "_network_input_fingerprint", lambda ctx, exact=False: "fp")
    return net


def test_recording_a_solve_stamps_its_time_in_utc(n):
    workflow_tools._record_solve_inputs(n, {})
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", n.meta["assistant_solve_at"])


def test_a_new_solve_moves_the_stamp(n, monkeypatch):
    n.meta["assistant_solve_at"] = "2000-01-01T00:00:00Z"
    workflow_tools._record_solve_inputs(n, {})
    assert n.meta["assistant_solve_at"] != "2000-01-01T00:00:00Z"


@pytest.mark.parametrize("dispatch, expected", [("fresh", "2026-11-17T15:42:00Z"), ("stale", "2026-11-17T15:42:00Z"),
                                                ("none", None)])
def test_the_status_reports_the_solve_time_while_its_dispatch_exists(n, monkeypatch, dispatch, expected):
    n.meta["assistant_solve_at"] = "2026-11-17T15:42:00Z"
    monkeypatch.setattr(simulation.PyPSAService, "get_network", staticmethod(lambda: n))
    monkeypatch.setattr(simulation, "_dispatch_status", lambda _n: dispatch)
    assert simulation.get_status()["solved_at"] == expected


def test_a_reopened_project_without_a_stamp_reads_none_not_a_failure(n, monkeypatch):
    # A project saved before this field: the line then says "From the dispatch solve" with no time (GK B3 C1).
    monkeypatch.setattr(simulation.PyPSAService, "get_network", staticmethod(lambda: n))
    monkeypatch.setattr(simulation, "_dispatch_status", lambda _n: "fresh")
    assert simulation.get_status()["solved_at"] is None
