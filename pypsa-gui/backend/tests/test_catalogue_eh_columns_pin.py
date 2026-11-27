"""
`EH_CUSTOM_COLUMNS` reads its kinds from the catalogue's settings rows (spec expert-catalogue §3.1,
issue 01a-2, GK B (63) C5, GK B2 (7) E2).

The registry is compared with a hand-written literal of master's keys, order and kinds, and the input
fingerprint of a network that sets every eh_* column (one at a non-default value each) is pinned to the
digests master gave before the move (recorded in a fresh master worktree under this same warm-up).
"""

from __future__ import annotations

import types

import pandas as pd
import pypsa

from models.energy_hub import EH_CUSTOM_COLUMNS

#: Master's literal (models/energy_hub.py before 01a-2), written out by hand.
MASTER_EH_CUSTOM_COLUMNS = {
    "Bus": {
        "eh_poc": "bool",
        "eh_critical": "bool",
        "eh_sk_mva": "float_nonneg",
        "eh_ibr_mva": "float_nonneg",
    },
    "Link": {"eh_role": "role"},
}

#: `(plain, exact)` input fingerprints of `_network()` on master d0d98072, before the move.
MASTER_FP_EH_COLUMNS = (
    "90dc604ebe8d8cef26d3218f2393ab6d2803d3e061c11b32bd1c452baacc820f",
    "e0b2e6b4057696feb6554c74618f25bd3bc1d144aa64613e88a56cae1305dde4",
)


def test_registry_is_masters_literal_in_order():
    assert EH_CUSTOM_COLUMNS == MASTER_EH_CUSTOM_COLUMNS
    assert list(EH_CUSTOM_COLUMNS) == ["Bus", "Link"]
    for cls, cols in MASTER_EH_CUSTOM_COLUMNS.items():
        assert list(EH_CUSTOM_COLUMNS[cls].items()) == list(cols.items())


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Bus", "grid", carrier="AC", v_nom=20.0)
    n.add("Bus", "site", carrier="AC", v_nom=0.4)
    n.add("Generator", "supply", bus="grid", p_nom=10.0, marginal_cost=50.0)
    n.add("Load", "demand", bus="site", p_set=[1.0, 2.0, 1.5])
    n.add("Link", "feeder", bus0="grid", bus1="site", p_nom=5.0, efficiency=0.98)
    n.add("Link", "spare", bus0="grid", bus1="site", p_nom=1.0)
    n.buses["eh_poc"] = [True, False]
    n.buses["eh_critical"] = [False, True]
    n.buses["eh_sk_mva"] = [250.0, float("nan")]
    n.buses["eh_ibr_mva"] = [float("nan"), 12.5]
    n.links["eh_role"] = ["grid_import", ""]
    return n


def _fps(n):
    from services.workflow_tools import _network_input_fingerprint

    ctx = types.SimpleNamespace(
        network=n, solver_state={"solver_config": {}}, user_ts={}, storage_dir=None
    )
    return _network_input_fingerprint(ctx), _network_input_fingerprint(ctx, exact=True)


def test_fingerprint_is_masters_with_every_eh_column_set():
    _fps(_network())  # warm-up: the first walk builds the shared caches
    assert _fps(_network()) == MASTER_FP_EH_COLUMNS


def _ic_digest():
    """The investment-case assumptions digest of a solver config that sets the catalogued settings."""
    from services.finance.investment_case_runner import assumptions_digest
    from services.solver_service import SolverConfig

    cfg = SolverConfig(
        multi_investment_periods=True,
        investment_periods=[2030, 2035],
        target_lole_h=0.5,
        dsr_share_of_load=0.1,
        dsr_price_eur_per_mwh=300.0,
        load_growth_pct_per_year={"electrical": 2.0},
        load_growth_base_year=2030,
        load_scalers={"2035": 1.2},
    )
    return assumptions_digest(
        finance=None, commercial=None, dispatch=None, packs={}, solver=cfg
    )[0]


#: `_ic_digest()` on master d0d98072, before the move (C5).
MASTER_IC_DIGEST = "1ddbd1322e4bc2001b77cd0214ca810e"


def test_investment_case_digest_is_masters():
    assert _ic_digest() == MASTER_IC_DIGEST
