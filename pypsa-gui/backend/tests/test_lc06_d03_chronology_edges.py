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

Upload guards (ticket 33, ``LC-D03.upload_guards``), on a four-snapshot network:
- a repeated timestamp is refused as ``timeseries_duplicate_timestamp`` and the stored
  series is unchanged;
- a CSV missing 02:00 is stored and the gap is forward-filled (1, 2, 2, 4), named in
  ``filled_snapshots``;
- a CSV wholly outside the horizon is refused as ``timeseries_outside_horizon`` and the
  snapshots are unchanged, unless ``replace_horizon=True`` confirms the replacement;
- the first upload to a blank network (PyPSA's placeholder ``now`` snapshot) is accepted
  and the snapshots become the CSV's.
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


def _four(alpha):
    chat_tools.set_snapshots("2026-01-01 00:00", "2026-01-01 03:00", "h")
    ok = "snapshot,L1\n" + "".join(f"2026-01-01 0{h}:00,{h + 1}\n" for h in range(4))
    chat_tools.upload_timeseries("loads", "L1", "p_set", ok)
    return list(PyPSAService.get_network().loads_t.p_set["L1"])


def _kind(result):
    detail = result.get("detail", result) if isinstance(result, dict) else result
    return detail.get("error_kind") if isinstance(detail, dict) else None


def _refused(csv):
    from fastapi import HTTPException
    try:
        chat_tools.upload_timeseries("loads", "L1", "p_set", csv)
    except HTTPException as exc:
        return exc.detail
    pytest.fail("the upload was accepted")


def test_a_repeated_timestamp_is_refused_and_stores_nothing(alpha):
    before = _four(alpha)
    csv = "snapshot,L1\n2026-01-01 00:00,9\n2026-01-01 01:00,2\n2026-01-01 01:00,7\n2026-01-01 03:00,4\n"
    detail = _refused(csv)
    assert detail["error_kind"] == "timeseries_duplicate_timestamp"
    assert "2026-01-01 01:00" in detail["message"]
    assert list(PyPSAService.get_network().loads_t.p_set["L1"]) == before == [1.0, 2.0, 3.0, 4.0]


def test_a_missing_snapshot_is_forward_filled_and_named(alpha):
    _four(alpha)
    csv = "snapshot,L1\n2026-01-01 00:00,1\n2026-01-01 01:00,2\n2026-01-01 03:00,4\n"
    result = chat_tools.upload_timeseries("loads", "L1", "p_set", csv)
    assert result["rows"] == 3
    assert result["filled_snapshots"] == ["2026-01-01 02:00:00"]
    assert list(PyPSAService.get_network().loads_t.p_set["L1"]) == [1.0, 2.0, 2.0, 4.0]
    assert _objective() == pytest.approx(10 * (1 + 2 + 2 + 4), rel=REL)


def test_an_off_horizon_csv_is_refused_and_keeps_the_snapshots(alpha):
    before = _four(alpha)
    snaps = list(PyPSAService.get_network().snapshots)
    csv = "snapshot,L1\n2026-06-01 00:00,8\n2026-06-01 01:00,9\n"
    assert _refused(csv)["error_kind"] == "timeseries_outside_horizon"
    n = PyPSAService.get_network()
    assert list(n.snapshots) == snaps and list(n.loads_t.p_set["L1"]) == before


def test_an_off_horizon_csv_replaces_the_snapshots_once_confirmed(alpha):
    import io

    from fastapi import UploadFile

    from routers.network import upload_timeseries as route
    _four(alpha)
    csv = "snapshot,L1\n2026-06-01 00:00,8\n2026-06-01 01:00,9\n"
    upload = UploadFile(filename="L1.csv", file=io.BytesIO(csv.encode()))
    result = chat_tools._sync(route(component="loads", attribute="p_set", file=upload,
                                    replace_horizon=True))
    assert result["rows"] == 2
    assert len(PyPSAService.get_network().snapshots) == 2


def test_the_first_upload_to_a_blank_network_adopts_the_csv_range(alpha, install_network):
    n = pypsa.Network()
    n.add("Bus", "B1", v_nom=110)
    n.add("Load", "L1", bus="B1", p_set=5)
    assert not isinstance(n.snapshots, pd.DatetimeIndex)
    install_network(n)
    csv = "snapshot,L1\n2026-06-01 00:00,8\n2026-06-01 01:00,9\n"
    assert chat_tools.upload_timeseries("loads", "L1", "p_set", csv)["rows"] == 2
    assert [str(t) for t in PyPSAService.get_network().snapshots] == [
        "2026-06-01 00:00:00", "2026-06-01 01:00:00"]


@pytest.mark.parametrize("csv", [
    "snapshot,L1\n2026-01-01 00:00+01:00,9\n2026-01-01 01:00+01:00,9\n",
    "snapshot,L1\n2026-01-01T00:00:00+00:00,9\n2026-01-01T01:00:00+00:00,9\n"
    "2026-01-01T02:00:00+00:00,9\n2026-01-01T03:00:00+00:00,9\n",
    "snapshot,L1\n2026-01-01 00:00+01:00,9\n2026-01-01 01:00+02:00,9\n",
], ids=["offset", "utc", "mixed_offsets"])
def test_a_timezone_aware_csv_is_refused_typed_and_stores_nothing(alpha, csv):
    before = _four(alpha)
    snaps = list(PyPSAService.get_network().snapshots)
    assert _refused(csv)["error_kind"] == "timeseries_timezone_aware"
    n = PyPSAService.get_network()
    assert list(n.snapshots) == snaps and list(n.loads_t.p_set["L1"]) == before


@pytest.fixture
def two_periods(alpha):
    n = PyPSAService.get_network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=2, freq="h"))
    n.set_investment_periods([2030, 2040])
    assert isinstance(n.snapshots, pd.MultiIndex)
    return n


@pytest.mark.parametrize("kwargs", [{}, {"period": 2030}], ids=["broadcast", "per_period"])
def test_an_off_horizon_csv_on_a_period_network_is_refused_and_stores_nothing(two_periods, kwargs):
    import io

    from fastapi import HTTPException, UploadFile

    from routers.network import upload_timeseries as route
    from services.user_timeseries import _user_ts
    snaps = list(two_periods.snapshots)
    csv = "snapshot,L1\n2026-06-01 00:00,8\n2026-06-01 01:00,9\n"
    upload = UploadFile(filename="L1.csv", file=io.BytesIO(csv.encode()))
    with pytest.raises(HTTPException) as exc:
        chat_tools._sync(route(component="loads", attribute="p_set", file=upload, **kwargs))
    assert exc.value.detail["error_kind"] == "timeseries_outside_horizon"
    assert list(PyPSAService.get_network().snapshots) == snaps
    assert ("loads", "p_set", "L1") not in _user_ts


def test_an_integer_indexed_csv_on_a_range_snapshot_network_is_not_called_timezone_aware(alpha):
    n = PyPSAService.get_network()
    n.set_snapshots(pd.RangeIndex(4))
    result = chat_tools.upload_timeseries("loads", "L1", "p_set", "snapshot,L1\n0,1\n1,2\n2,3\n3,4\n")
    assert result["rows"] == 4
