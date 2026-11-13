# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""ui-modern 08: a case with a long horizon compares with its base.

`basis_digests` hashed the snapshot index through the experiment plan's
`_digest`, which refuses JSON over 64 KiB, so a three-period 504-snapshot
network (77,790 bytes) could never be compared.
"""
import hashlib
import json
from types import SimpleNamespace

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from harness import experiment_comparison as ec
from services import experiments as ex, workflow_tools as wt
from services.solver_service import SolverConfig


def _saved(snapshots, periods=None):
    n = pypsa.Network()
    if periods:
        n.set_snapshots(pd.MultiIndex.from_product([periods, snapshots]))
        n.set_investment_periods(periods)
    else:
        n.set_snapshots(snapshots)
    return SimpleNamespace(network=n, solver_state={'solver_config': SolverConfig()})


def _hours(count):
    return pd.date_range('2030-01-01', periods=count, freq='h')


def test_a_504_snapshot_horizon_has_basis_digests():
    saved = _saved(_hours(168), periods=[2030, 2040, 2050])
    blob = json.dumps(wt._exact_frame_json(saved.network.snapshots.to_series().to_frame()),
                      sort_keys=True, allow_nan=False).encode()
    assert len(blob) > ex.MAX_RECORD_BYTES // 2, 'the fixture must be past the plan limit'
    digests = ec.basis_digests(saved)
    assert digests['snapshots_sha256'] == hashlib.sha256(blob).hexdigest()
    assert set(digests) == {'snapshots_sha256', 'weights_sha256', 'solver_config_sha256'}


def test_a_short_horizon_keeps_the_digest_saved_records_hold():
    """Every index that passed before digests to the same value, so records
    saved before the fix stay comparable."""
    n = _saved(_hours(168)).network
    for obj in (wt._exact_frame_json(n.snapshots.to_series().to_frame()),
                [wt._exact_frame_json(n.snapshot_weightings),
                 wt._exact_frame_json(n.investment_period_weightings)]):
        assert ec._basis_digest(obj) == ex._digest(obj)
    saved = _saved(_hours(168))
    digests = ec.basis_digests(saved)
    assert digests['snapshots_sha256'] == ex._digest(
        wt._exact_frame_json(saved.network.snapshots.to_series().to_frame()))


def test_the_plan_limit_still_holds_for_plans():
    with pytest.raises(HTTPException) as err:
        ex._digest({'blob': 'x' * ex.MAX_RECORD_BYTES})
    assert err.value.status_code == 422


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), object()])
def test_a_basis_that_is_not_finite_json_is_still_refused(bad):
    with pytest.raises(HTTPException) as err:
        ec._basis_digest([bad])
    assert err.value.status_code == 422
