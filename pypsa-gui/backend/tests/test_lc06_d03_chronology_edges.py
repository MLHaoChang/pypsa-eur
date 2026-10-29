"""LC-06 S11, LC-D03: leap day, a DST date and out-of-order timestamps.

The seam is the copilot's ``set_snapshots`` and ``upload_timeseries`` tools. The
oracles are counted by hand and read from the network the tools built, solved on a
copy with ``pypsa`` alone.

Hand oracle, one bus, G1 20 MW at 10 EUR/MWh, L1 5 MW, unit weights:
- 2028-02-28 00:00 to 2028-03-01 23:00 hourly is three days of 24 h = 72 snapshots,
  and 2027's same dates are two days = 48: the leap day is there only in 2028. The
  solved objective is 10 · 5 · 72 = 3600 EUR (2400 for 2027).
- Snapshots are wall-clock and zone-free: 2026-03-28 to 2026-03-30 (the EU spring
  change falls on 29 March) has 72 hourly snapshots, no hour is dropped or doubled.
- A CSV whose rows are out of order is aligned by its timestamps: 03:00 -> 4, 01:00 -> 2,
  00:00 -> 1, 02:00 -> 3 puts 1, 2, 3, 4 on 00:00 to 03:00, an objective of 10 · (1+2+3+4) = 100.

A repeated timestamp is not pinned here: today it fails with an untyped pandas error
(ticket 33, ``LC-D03.upload_guards`` in the manifest).
"""
# ruff: noqa: F811 -- shared public harness fixtures
import pandas as pd
import pypsa
import pytest

from services import chat_tools
from services.pypsa_service import PyPSAService
from tests.test_workflow_tools import helpers_state  # noqa: F401

REL = 1e-9


@pytest.fixture
def alpha(client, session_ctx, install_network):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=2, freq="h"))
    n.add("Bus", "B1", v_nom=110)
    n.add("Generator", "G1", bus="B1", p_nom=20, marginal_cost=10)
    n.add("Load", "L1", bus="B1", p_set=5)
    install_network(n)
    response = client.post("/api/projects/Alpha")
    assert response.status_code == 200, response.text
    binding = PyPSAService.bind_request_context(session_ctx(client))
    cell = PyPSAService.bind_turn_cell()
    try:
        yield
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def _objective():
    n = PyPSAService.get_network().copy()
    status, _ = n.optimize(solver_name="highs")
    assert status == "ok"
    return n.objective


@pytest.mark.parametrize("start, end, hours, objective", [
    ("2028-02-28 00:00", "2028-03-01 23:00", 72, 3600.0),
    ("2027-02-28 00:00", "2027-03-01 23:00", 48, 2400.0),
    ("2026-03-28 00:00", "2026-03-30 23:00", 72, 3600.0),
])
def test_the_horizon_has_the_hand_counted_hours_and_cost(alpha, start, end, hours, objective):
    assert chat_tools.set_snapshots(start, end, "h") == {"count": hours}
    snaps = PyPSAService.get_network().snapshots
    assert len(snaps) == hours and snaps.is_unique and snaps.is_monotonic_increasing
    assert (snaps[1:] - snaps[:-1] == pd.Timedelta("1h")).all()
    assert _objective() == pytest.approx(objective, rel=REL)


def test_an_out_of_order_csv_is_aligned_by_its_timestamps(alpha):
    chat_tools.set_snapshots("2026-01-01 00:00", "2026-01-01 03:00", "h")
    csv = "snapshot,L1\n2026-01-01 03:00,4\n2026-01-01 01:00,2\n2026-01-01 00:00,1\n2026-01-01 02:00,3\n"
    assert chat_tools.upload_timeseries("loads", "L1", "p_set", csv)["rows"] == 4
    series = PyPSAService.get_network().loads_t.p_set["L1"]
    assert {str(t): v for t, v in series.items()} == {
        "2026-01-01 00:00:00": 1.0, "2026-01-01 01:00:00": 2.0,
        "2026-01-01 02:00:00": 3.0, "2026-01-01 03:00:00": 4.0}
    assert _objective() == pytest.approx(100.0, rel=REL)
