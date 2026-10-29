"""Dev helper: dump aggregate_load_profile's JSON for every gap scenario.

GK B (9) asked for the S1 refactor (one aggregate gap helper) to leave the
handler's response byte-equal to master's. This file is self-contained so it
can be copied into a master worktree and run there too:

    AGG_DUMP=/path/out.jsonl python -m pytest -p no:cacheprovider tests/assurance_acceptance/aggregate_json_dump.py

then `cmp` the two files. SCENARIOS is also what test_p1_validity_aggregate's
parity cases run on both paths.
"""

import json
import os

import numpy as np
import pandas as pd
import pytest

from routers.network_profiles import aggregate_load_profile
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.test_p1_aggregate import loads  # noqa: F401  (fixture)


def network():
    return PyPSAService.get_active_context().network


def upload(monkeypatch, name, series):
    from routers import network_profiles

    monkeypatch.setitem(network_profiles._user_ts, ("loads", "p_set", name), series)


def set_step(name, step, value):
    network().loads_t.p_set.loc[network().snapshots[step], name] = value


def no_profile(mp):
    network().add("Load", "S1", bus="B1", p_set=3.0)


def other_index():
    return pd.date_range("2030-01-01", periods=3, freq="h")


def many_gaps(mp):
    # Every reachable gap at once, so their order is pinned.
    no_profile(mp)
    upload(mp, "L2", pd.Series([True, False, True], index=other_index()))


def empty_upload(mp):
    # An empty upload reaches "no steps", here next to every gap it can
    # appear with.
    no_profile(mp)
    upload(mp, "L2", pd.Series([], index=pd.DatetimeIndex([]), dtype=object))


# name -> (setup(monkeypatch), handler kwargs)
SCENARIOS = {
    "complete section": (lambda mp: None, {"section": "electricity"}),
    "complete names": (lambda mp: None, {"names": "L1,L2"}),
    "every load": (lambda mp: None, {}),
    "repeated name": (lambda mp: None, {"names": "L1,L1,L2"}),
    "unknown": (lambda mp: None, {"names": "ghost,L1"}),
    "only unknown": (lambda mp: None, {"names": "ghost"}),
    "no loads match": (lambda mp: None, {"section": "heat"}),
    "no profile": (no_profile, {"names": "L1,L2,S1"}),
    "no profile at all": (no_profile, {"names": "S1"}),
    "missing step": (lambda mp: set_step("L2", 1, np.nan), {"names": "L1,L2"}),
    "infinite step": (lambda mp: set_step("L1", 0, np.inf), {"names": "L1,L2"}),
    "other snapshots": (
        lambda mp: upload(mp, "L2", pd.Series([1.0, 1.0, 1.0], index=other_index())),
        {"names": "L1,L2"},
    ),
    "both off snapshots": (
        lambda mp: [
            upload(mp, name, pd.Series([1.0, 2.0, 3.0], index=other_index()))
            for name in ("L1", "L2")
        ],
        {"names": "L1,L2"},
    ),
    "bool": (
        lambda mp: upload(mp, "L2", pd.Series([True, False, True], index=network().snapshots)),
        {"names": "L1,L2"},
    ),
    "numeric string": (
        lambda mp: upload(
            mp, "L2", pd.Series(["1", "4", "5"], index=network().snapshots, dtype=object)
        ),
        {"names": "L1,L2"},
    ),
    "repeated timestamp": (
        lambda mp: upload(
            mp,
            "L2",
            pd.Series([2.0, 4.0, 1.0], index=network().snapshots[[0, 0, 2]]),
        ),
        {"names": "L1,L2"},
    ),
    "timezone-aware": (
        lambda mp: upload(
            mp, "L2", pd.Series([2.0, 4.0, 1.0], index=network().snapshots.tz_localize("UTC"))
        ),
        {"names": "L1,L2"},
    ),
    "many gaps": (many_gaps, {"names": "ghost,L1,L2,S1"}),
    "empty upload": (empty_upload, {"names": "ghost,L2,S1"}),
}


@pytest.mark.parametrize("case", list(SCENARIOS))
def test_dump(case, loads, monkeypatch):  # noqa: F811
    setup, kwargs = SCENARIOS[case]
    setup(monkeypatch)
    out = aggregate_load_profile(**kwargs)
    path = os.environ.get("AGG_DUMP")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps({"case": case, "out": out}, allow_nan=False) + "\n")
