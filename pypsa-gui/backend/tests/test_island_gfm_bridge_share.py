"""Each grid-forming battery holds its own share of the seamless bridge
energy B(t) (study harness 40, owner decision SH40-BRIDGE, GK A P5/P6).

gridspine's frequency screen judges the bridge per battery (the energy each
GFM gives from the islanding until the cold gensets join, against its own
e(t−1)). The aggregate row (``Island-gfm-bridge``) only holds the sum, so one
battery may sit empty while the other holds it all. The per-battery row

    η_s·(e_s(t−1) − e_min_pu·e_nom,s) ≥ share_s·B(t)

closes that: share_s = K_s/ΣK (K = p_nom/(R·f0)) when every grid-forming
battery is fixed, else 1. Hand networks, a closed tie: ΔP⁺ = 3 MW given, a
cold genset joining at 10 + 5 s, so B = 3·15/3600 MWh at every hour.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.solver import island as I
from tests.test_island_rows import _BESS, _a, _genset, _island

DP, JOIN = 3.0, 15.0
B = DP * JOIN / 3600.0
TOL = 1e-7


def _units(droop_a=4.0, droop_b=2.0):
    return {
        "crit": {"kind": "load", "critical": True},
        "a": {**_BESS, "droop_pct": _a(droop_a)},
        "b": {**_BESS, "droop_pct": _a(droop_b)},
        "g": _genset(start_s=10.0, sync_s=5.0),
    }


def _net(*, a_ext=True, b_mw=4.0, cost_b=1000.0, hours=6, eta=1.0, b_set=None):
    """One bus, a grid supply, a cold genset that never runs, and two
    grid-forming batteries: ``a`` (extendable, or fixed at 6 MW) and ``b``
    (fixed, ``b_mw``). Holding energy in ``b`` costs ``cost_b`` per MWh·h, so
    a solve with only the summed row leaves ``b`` empty."""
    n = pypsa.Network()
    sns = pd.date_range("2030-01-01", periods=hours, freq="h")
    n.set_snapshots(sns)
    n.add("Bus", "site")
    n.add("Load", "crit", bus="site", p_set=0.0)  # no ride-through window: B alone
    n.add("Generator", "grid", bus="site", p_nom=100.0, marginal_cost=10.0)
    n.add("Generator", "g", bus="site", p_nom=0.0, marginal_cost=1000.0)
    kw_a = dict(p_nom_extendable=True, capital_cost=100.0) if a_ext else dict(p_nom=6.0)
    n.add(
        "StorageUnit",
        "a",
        bus="site",
        max_hours=2.0,
        efficiency_dispatch=eta,
        cyclic_state_of_charge=True,
        **kw_a,
    )
    n.add(
        "StorageUnit",
        "b",
        bus="site",
        p_nom=b_mw,
        max_hours=2.0,
        efficiency_dispatch=eta,
        cyclic_state_of_charge=True,
        marginal_cost_storage=cost_b,
    )
    if b_set is not None:
        n.storage_units_t.state_of_charge_set = pd.DataFrame({"b": b_set}, index=sns)
    return n


def _sidecar(**kw):
    return _island(
        units=_units(**kw),
        k=0,
        scenarios=("seamless",),
        rocof=1e6,
        qss=1e6,
        sustained_h=1.0,
        bridge_s=1.0,
    )


def _solve(n, island, check=False):
    status, cond = n.optimize(
        solver_name="highs",
        extra_functionality=I.island_extra_functionality(
            island, delta_p_plus=DP, check=check
        ),
    )
    return status, cond


def _soc_before(n, name):
    soc = n.storage_units_t.state_of_charge[name]
    return np.roll(soc.to_numpy(), 1)  # cyclic: e(t−1), the first from the last


def test_a_summed_row_alone_leaves_one_battery_empty(monkeypatch):
    """The red case: without the per-battery rows the cheaper solve holds
    all of B in ``a`` and leaves ``b`` empty, which the screen fails."""
    monkeypatch.setattr(
        I, "_gfm_share_rows", lambda *a, **k: {"rule": "off", "shares": {}}
    )
    n = _net()
    assert _solve(n, _sidecar()) == ("ok", "optimal")
    assert _soc_before(n, "b").max() == pytest.approx(0.0, abs=TOL)
    assert (_soc_before(n, "a") >= B - TOL).all()


def test_with_an_extendable_battery_each_holds_all_of_b():
    n = _net()
    assert _solve(n, _sidecar()) == ("ok", "optimal")
    assert _soc_before(n, "b").min() == pytest.approx(B, abs=1e-6)  # binds: share 1
    assert (_soc_before(n, "a") >= B - TOL).all()
    facts = n._island_facts["gfm_bridge_share"]["None"]
    assert facts == {"rule": I.GFM_SHARE_ONE, "shares": {"a": 1.0, "b": 1.0}}


def test_every_battery_fixed_holds_its_droop_share():
    """K_a = 6/0.04 = 150, K_b = 4/0.02 = 200 (per f0): b holds 200/350 of B,
    not its 4/10 share of the MW."""
    n = _net(a_ext=False)
    status, _ = _solve(n, _sidecar(), check=True)
    assert status == "ok"
    share = 200.0 / 350.0
    assert _soc_before(n, "b").min() == pytest.approx(share * B, abs=1e-6)
    facts = n._island_facts["gfm_bridge_share"]["None"]
    assert facts["rule"] == I.GFM_SHARE_DROOP
    assert facts["shares"] == pytest.approx({"a": 150.0 / 350.0, "b": share}, rel=1e-12)


def test_the_row_reads_the_dispatch_efficiency():
    n = _net(eta=0.8)
    assert _solve(n, _sidecar()) == ("ok", "optimal")
    assert _soc_before(n, "b").min() == pytest.approx(B / 0.8, abs=1e-6)


def test_the_row_reads_the_energy_above_the_floor():
    n = _net()
    n.storage_units["e_min_pu"] = 0.0
    n.storage_units.at["b", "e_min_pu"] = 0.25  # 0.25·4 MW·2 h = 2 MWh held back
    assert _solve(n, _sidecar()) == ("ok", "optimal")
    assert _soc_before(n, "b").min() == pytest.approx(2.0 + B, abs=1e-6)


def test_a_floor_outside_zero_to_one_is_refused():
    n = _net()
    n.storage_units["e_min_pu"] = 1.5
    with pytest.raises(I.IslandRefusal, match="e_min_pu"):
        _solve(n, _sidecar())


def test_no_step_no_row():
    """B = 0 (the tie open: no import step): nothing to hold."""
    n = _net()
    status, _ = n.optimize(
        solver_name="highs",
        extra_functionality=I.island_extra_functionality(_sidecar(), delta_p_plus=0.0),
    )
    assert status == "ok"
    assert n._island_facts["gfm_bridge_share"] == {}
    assert _soc_before(n, "b").max() == pytest.approx(0.0, abs=TOL)


# ── P5: the LP and the screen agree at the share boundary ──────────────────


def _screen_bridge_ok(e_b):
    """Gridspine's screen on the same island: the two batteries' droop and
    τ_f, the cold genset joining at 15 s, E1 = ΔP⁺; b's own e(t−1) = e_b."""
    from gridspine.static import frequency as F

    units = [
        F.Unit("a", "gfm_droop", 0.0, 6.0, s_n=6.0 / 0.95, r=0.04, tau_f=0.1),
        F.Unit("b", "gfm_droop", 0.0, 4.0, s_n=4.0 / 0.95, r=0.02, tau_f=0.1),
        F.Unit(
            "g",
            "genset",
            0.0,
            2.0,
            s_n=2.5,
            r=0.04,
            h=1.5,
            join_s=JOIN,
            start_s=10.0,
            sync_s=5.0,
        ),
    ]
    tr = F.e1_islanding(units, DP, t_end=20.0, dt=1e-3)
    rows = {
        r["unit"]: r
        for r in F._gfm_bridge(
            {"period": 0, "hour": 0}, units, tr, {"a": 10.0, "b": e_b}
        )
    }
    return rows["b"]["ok"]


@pytest.mark.parametrize("scale, holds", [(1.0 + 1e-6, True), (1.0 - 1e-3, False)])
def test_the_lp_and_the_screen_agree_at_the_droop_share(scale, holds):
    share = 200.0 / 350.0
    e_b = share * B * scale
    assert _screen_bridge_ok(e_b) is holds
    n = _net(
        a_ext=False, cost_b=0.0, b_set=[np.nan] * 5 + [e_b]
    )  # e_b(t−1) at the first hour
    status, cond = _solve(n, _sidecar(), check=True)
    assert (status == "ok") is holds, (status, cond)
