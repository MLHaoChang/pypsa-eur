"""
The generic time-series upload must not accept a column that names no asset.

`POST /timeseries/upload` validated the CSV's index, the `period` argument and
finiteness, then wrote EVERY column into `_user_ts` keyed
`(component, attribute, col)`. Nothing checked that `col` named a row on the
component's frame, and nothing checked that `attribute` was a time-varying
attribute of that component. The response reported `columns: len(df.columns)`
— a success count — so a typo in a header was indistinguishable from a
correct upload.

The stored entry is not inert. `_user_ts` is persisted in every project save
and re-injected by `_reapply_user_ts_to_network` on every solve, so a mistyped
column accumulates forever and, if an asset with that name is later created,
that asset silently inherits the ghost profile — the cascade-delete failure
mode reached by a different route.

The chat tool `upload_timeseries` is the caller that pays most: an agent that
mistypes an asset name used to get a success dict back.
"""
from __future__ import annotations

import io

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException, UploadFile

from routers.network import upload_timeseries
from routers.network_time_axis import _user_ts, _user_ts_lock


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "B")
    n.add("Load", "L1", bus="B", p_set=50.0)
    n.add("Load", "L2", bus="B", p_set=30.0)
    return n


def _csv(cols: list[str], snapshots) -> UploadFile:
    df = pd.DataFrame({c: [10.0] * len(snapshots) for c in cols}, index=snapshots)
    return UploadFile(
        filename="profiles.csv",
        file=io.BytesIO(df.to_csv(index=True, lineterminator="\n").encode("utf-8")),
    )


def _upload(component, attribute, cols, snapshots):
    import asyncio
    return asyncio.run(upload_timeseries(
        component=component, attribute=attribute, file=_csv(cols, snapshots),
    ))


@pytest.fixture
def live(install_network):
    n = install_network(_network())
    try:
        yield n
    finally:
        with _user_ts_lock:
            _user_ts.clear()


def test_a_column_that_names_no_asset_is_not_stored(live):
    _upload("loads", "p_set", ["L1", "Lx"], live.snapshots)
    with _user_ts_lock:
        stored = {k[2] for k in _user_ts if k[0] == "loads"}
    assert stored == {"L1"}, f"a column naming no load was stored: {stored}"


def test_the_response_names_the_columns_it_could_not_match(live):
    body = _upload("loads", "p_set", ["L1", "Lx", "Ly"], live.snapshots)
    assert body["unmatched_columns"] == ["Lx", "Ly"], body
    # `columns` is the count APPLIED, not the count submitted — a success
    # count that includes the failures is worse than no count at all.
    assert body["columns"] == 1, body


def test_a_fully_matched_upload_reports_nothing_unmatched(live):
    # The control: the ordinary case must be unchanged.
    body = _upload("loads", "p_set", ["L1", "L2"], live.snapshots)
    assert body["unmatched_columns"] == [], body
    assert body["columns"] == 2, body
    with _user_ts_lock:
        assert {k[2] for k in _user_ts if k[0] == "loads"} == {"L1", "L2"}


def test_an_unknown_attribute_is_refused(live):
    with pytest.raises(HTTPException) as exc:
        _upload("loads", "not_an_attribute", ["L1"], live.snapshots)
    assert exc.value.status_code == 400
    assert "not_an_attribute" in str(exc.value.detail)
    with _user_ts_lock:
        assert not _user_ts


def test_a_recreated_asset_does_not_inherit_a_ghost_profile(live):
    # End to end: the reason unmatched columns cannot simply be stored.
    _upload("loads", "p_set", ["L3"], live.snapshots)
    live.add("Load", "L3", bus="B", p_set=20.0)
    with _user_ts_lock:
        assert ("loads", "p_set", "L3") not in _user_ts, (
            "a load created after the upload inherited the ghost profile"
        )
