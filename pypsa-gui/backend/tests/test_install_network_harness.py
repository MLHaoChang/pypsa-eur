"""
The test / QA-driver install helper must start the installed network clean, as
the real "New Project" route (`POST /network/reset`) does.

`services.user_timeseries._user_ts` is process-global: left over from a
previous install, the next project save writes that project's profiles into
`user_ts.json` and the reload re-applies them onto the new network (a 384-
snapshot network came back with 1,344 snapshots in the Phase 1 gate probe).
"""
from __future__ import annotations

import pandas as pd

from services.user_timeseries import _user_ts, _user_ts_lock
from tests.conftest import install_network_into_backend
from tests.fixtures.investment_case.edge_15min import build_edge_15min


def test_installing_a_network_drops_the_previous_user_timeseries():
    install_network_into_backend(build_edge_15min(), name="harness_a")
    with _user_ts_lock:
        _user_ts[("Load", "site_load", "p_set")] = pd.Series([1.0, 2.0])
    install_network_into_backend(build_edge_15min(), name="harness_b")
    with _user_ts_lock:
        assert not _user_ts
