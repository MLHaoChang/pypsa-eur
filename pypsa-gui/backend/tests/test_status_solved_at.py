"""Control-strategy walkthrough issue 06 / improvement 2: the source-run line names when the dispatch solve ran.

The time is recorded beside the solve's input fingerprint in ``n.meta`` (so it is saved with the project and
survives a reload), and /simulation/status reports it only while the dispatch exists.
"""

import calendar
import re
import time

import pypsa
import pytest

from routers import simulation
from services import workflow_tools


@pytest.fixture
def n(monkeypatch):
    net = pypsa.Network()
    net.set_snapshots(range(2))
    monkeypatch.setattr(workflow_tools, "_owning_context", lambda _n: None)
    monkeypatch.setattr(
        workflow_tools, "_network_input_fingerprint", lambda ctx, exact=False: "fp"
    )
    return net


def test_recording_a_solve_stamps_its_time_in_utc(n):
    workflow_tools._record_solve_inputs(n, {})
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", n.meta["assistant_solve_at"]
    )


def test_a_new_solve_moves_the_stamp(n, monkeypatch):
    n.meta["assistant_solve_at"] = "2000-01-01T00:00:00Z"
    workflow_tools._record_solve_inputs(n, {})
    assert n.meta["assistant_solve_at"] != "2000-01-01T00:00:00Z"


@pytest.mark.parametrize(
    "dispatch, expected",
    [
        ("fresh", "2026-11-17T15:42:00Z"),
        ("stale", "2026-11-17T15:42:00Z"),
        ("none", None),
    ],
)
def test_the_status_reports_the_solve_time_while_its_dispatch_exists(
    n, monkeypatch, dispatch, expected
):
    n.meta["assistant_solve_at"] = "2026-11-17T15:42:00Z"
    monkeypatch.setattr(simulation.PyPSAService, "get_network", staticmethod(lambda: n))
    monkeypatch.setattr(simulation, "_dispatch_status", lambda _n: dispatch)
    assert simulation.get_status()["solved_at"] == expected


def test_a_reopened_project_without_a_stamp_reads_none_not_a_failure(n, monkeypatch):
    # A project saved before this field: the line then says "From the dispatch solve" with no time (GK B3 C1).
    monkeypatch.setattr(simulation.PyPSAService, "get_network", staticmethod(lambda: n))
    monkeypatch.setattr(simulation, "_dispatch_status", lambda _n: "fresh")
    assert simulation.get_status()["solved_at"] is None


def test_a_network_with_no_meta_reads_none(monkeypatch):
    # A network whose meta is None (not a dict): the status still answers, with no time.
    bare = type("Bare", (), {"meta": None})()
    monkeypatch.setattr(simulation.PyPSAService, "get_network", staticmethod(lambda: bare))
    monkeypatch.setattr(simulation, "_dispatch_status", lambda _n: "fresh")
    assert simulation.get_status()["solved_at"] is None


def test_a_stamp_that_is_not_a_string_reads_none(n, monkeypatch):
    # A hand-edited or corrupted project: a number in the stamp is not passed on as a time.
    n.meta["assistant_solve_at"] = 1763394120
    monkeypatch.setattr(simulation.PyPSAService, "get_network", staticmethod(lambda: n))
    monkeypatch.setattr(simulation, "_dispatch_status", lambda _n: "fresh")
    assert simulation.get_status()["solved_at"] is None


@pytest.fixture
def tokyo_clock(monkeypatch):
    # A server whose local zone is not UTC (UTC+9 all year, no daylight saving).
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_the_stamp_is_utc_on_a_server_in_another_zone(n, tokyo_clock):
    workflow_tools._record_solve_inputs(n, {})
    stamped = calendar.timegm(time.strptime(n.meta["assistant_solve_at"], "%Y-%m-%dT%H:%M:%SZ"))
    assert abs(stamped - time.time()) < 60
