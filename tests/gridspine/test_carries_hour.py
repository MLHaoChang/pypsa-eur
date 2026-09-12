"""Follow-ups F7: ONE stage-order guard.

`static/contingency.py` and `handoff/bundle.py` each carried a private copy of
"the net must already carry the hour" — the inverse check of `apply_snapshot`,
which is why it now lives next to it in `static/loadflow.py`. Two copies of a
guard drift: a tolerance loosened in one would let a screen accept a net the
bundle refuses, and no test would say which was right.
"""
import copy

import pytest

from gridspine.handoff import bundle as bundle_mod
from gridspine.ingest.pandapower_source import load_case39_res, registry_from_net
from gridspine.schema.contracts import ContractError
from gridspine.static import contingency as contingency_mod
from gridspine.static.loadflow import P_TOL_MW, apply_snapshot, check_net_carries_hour

from tests.gridspine.test_shortcircuit import HOUR, _hour_tables


@pytest.fixture(scope="module")
def tables():
    net = load_case39_res()
    reg = registry_from_net(net)
    dispatch, loads = _hour_tables(net, reg, curtailed=("G_BUS_34",))
    return net, reg, dispatch, loads


def test_both_call_sites_use_the_one_guard():
    assert contingency_mod.check_net_carries_hour is check_net_carries_hour
    assert bundle_mod.check_net_carries_hour is check_net_carries_hour
    assert not hasattr(contingency_mod, "_check_net_carries_hour")
    assert not hasattr(bundle_mod, "_check_net_carries_hour")


def test_a_snapshotted_net_passes_and_a_fresh_one_is_refused(tables):
    net, reg, dispatch, loads = tables
    fresh = copy.deepcopy(net)
    with pytest.raises(ContractError, match="does not carry hour"):
        check_net_carries_hour(fresh, dispatch, loads, HOUR, reg)
    apply_snapshot(fresh, dispatch, loads, hour=HOUR, registry=reg)
    check_net_carries_hour(fresh, dispatch, loads, HOUR, reg)


def test_a_unit_moved_after_the_snapshot_is_caught_even_when_the_load_total_agrees(tables):
    net, reg, dispatch, loads = tables
    work = copy.deepcopy(net)
    apply_snapshot(work, dispatch, loads, hour=HOUR, registry=reg)
    i = work.gen.index[work.gen["name"] == "G_BUS_32"][0]
    work.gen.at[i, "p_mw"] += 10 * P_TOL_MW
    with pytest.raises(ContractError, match="G_BUS_32"):
        check_net_carries_hour(work, dispatch, loads, HOUR, reg)


def test_a_load_moved_after_the_snapshot_is_caught(tables):
    net, reg, dispatch, loads = tables
    work = copy.deepcopy(net)
    apply_snapshot(work, dispatch, loads, hour=HOUR, registry=reg)
    work.load.at[work.load.index[0], "p_mw"] += 10 * P_TOL_MW
    with pytest.raises(ContractError, match="net.load total"):
        check_net_carries_hour(work, dispatch, loads, HOUR, reg)


def test_a_missing_hour_is_named(tables):
    net, reg, dispatch, loads = tables
    with pytest.raises(ContractError, match="no rows for hour 5"):
        check_net_carries_hour(net, dispatch, loads, 5, reg)
