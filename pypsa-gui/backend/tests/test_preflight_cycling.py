"""
Import-to-export cycling at preflight (IC U1 follow-up item f; owner decision 10).

Ported from the guided study's `validation_service._check_export_cycling`
(GS branch `claude/edge-tool-ux-research-n0n2l6`, decision study S3 / gate S3
[S2]) into `services/commercial/preflight.py`, on two paths:

  * WITHOUT a commercial config (`preflight.network_findings`): GS's check on
    the Links' own `marginal_cost`, for every pair of Links joining the same two
    buses in opposite directions, with GS's codes
    `tariff_export_exceeds_import` / `tariff_export_exceeds_import_via_storage`;
  * WITH one (`commercial_findings`): the MATERIALISED prices, i.e. the Links'
    base cost plus the tariff adders, the first convex tier and the export
    price, net of the import Link's efficiency: `commercial.arbitrage_loop`
    (same interval) and `commercial.arbitrage_loop_via_storage` (across
    intervals, through storage behind the meter).

Each case is worked by hand in its comment. A MWh bought through an import
Link of efficiency η delivers η MWh at the site; exporting it earns η × the
export credit. It pays when  η × credit − import cost > 0.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.commercial import lp_bindings as L
from services.commercial import preflight as P
from services.solver_service import SolverConfig
from services.validation_service import validate_for_run
from tests.fixtures.investment_case.edge_15min import build_edge_15min

SAME, CROSS = "tariff_export_exceeds_import", "tariff_export_exceeds_import_via_storage"
C_SAME, C_CROSS = "commercial.arbitrage_loop", "commercial.arbitrage_loop_via_storage"


def _codes(findings):
    return [f[1] for f in findings]


# ── without a commercial config: the Links' own marginal cost ─────────────


def _cycling_network(export_price: float, *, eff: float = 1.0) -> pypsa.Network:
    """GS's fixture: import 30 at 03:00 and 80 otherwise; a flat export credit."""
    idx = pd.date_range("2030-01-01", periods=24, freq="h")
    n = pypsa.Network()
    n.set_snapshots(idx)
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Generator", "grid_supply", bus="grid", p_nom=100.0, p_min_pu=-1.0)
    imp = pd.Series(np.where(idx.hour == 3, 30.0, 80.0), index=idx)
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=10.0, marginal_cost=imp,
          efficiency=eff)
    n.add("Link", "grid_export", bus0="site", bus1="grid", p_nom=10.0,
          marginal_cost=-export_price)
    n.add("Load", "l", bus="site", p_set=1.0)
    return n


def test_an_export_credit_above_the_import_price_flags_the_pair():
    # 03:00: 1.0 × 40 − 30 = +10 per MWh, in 1 snapshot; other hours 40 − 80 < 0.
    out = P.network_findings(_cycling_network(40.0))
    assert _codes(out) == [SAME]
    sev, _code, cls, name, msg = out[0]
    assert (sev, cls, name) == ("warning", "Link", "grid_import")
    assert "grid_import" in msg and "grid_export" in msg
    assert "1 snapshot(s)" in msg and "10.00" in msg


def test_a_credit_below_every_import_price_is_clean():
    # 25 − 30 < 0 at 03:00 and 25 − 80 < 0 elsewhere.
    assert P.network_findings(_cycling_network(25.0)) == []


def test_both_orientations_are_checked_so_a_loss_never_hides_a_pair():
    """GS's check does not know which Link of a raw pair imports, so it takes
    the better of both cycles (conservative: it may flag what the LP would not
    exploit, never the reverse). 03:00, grid_import at η = 0.7:
      grid-first:  0.7 × 40 − 30 = −2;
      site-first:  40 − 30 × η_export (1.0) = +10  → flagged, up to 10.00.
    The commercial path knows the roles and nets η_import alone (below)."""
    out = P.network_findings(_cycling_network(40.0, eff=0.7))
    assert _codes(out) == [SAME] and "10.00" in out[0][4]
    # Both cycles lose once the credit is below every import price.
    assert P.network_findings(_cycling_network(29.0, eff=0.7)) == []


def _cross_hour_network(*, storage: str | None = "su", eta_rt: float = 0.9) -> pypsa.Network:
    """GS's fixture: import 50 before 06:00 and 120 after; export credit 100
    from 17:00 to 20:00, else 0. No hour pays on its own (100 < 120)."""
    idx = pd.date_range("2030-01-01", periods=48, freq="h")
    n = pypsa.Network()
    n.set_snapshots(idx)
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Generator", "grid_supply", bus="grid", p_nom=100.0, p_min_pu=-1.0)
    imp = pd.Series(np.where(idx.hour < 6, 50.0, 120.0), index=idx)
    credit = pd.Series(np.where((idx.hour >= 17) & (idx.hour <= 20), 100.0, 0.0), index=idx)
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=10.0, marginal_cost=imp)
    n.add("Link", "grid_export", bus0="site", bus1="grid", p_nom=10.0, marginal_cost=-credit)
    n.add("Load", "l", bus="site", p_set=1.0)
    if storage == "su":
        n.add("StorageUnit", "bess", bus="site", p_nom_extendable=True, max_hours=2.0,
              efficiency_store=eta_rt ** 0.5, efficiency_dispatch=eta_rt ** 0.5)
    elif storage == "store":
        n.add("Store", "bess_store", bus="site", e_nom_extendable=True)
    return n


def test_storage_at_the_site_carries_a_cheap_hour_to_a_dear_one():
    # 100 × 1.0 × 0.9 − 50 = +40 per MWh.
    out = P.network_findings(_cross_hour_network())
    assert _codes(out) == [CROSS]
    for word in ("grid_import", "grid_export", "bess", "40.00"):
        assert word in out[0][4]
    # A Store shifts energy as well (round trip 1.0: 100 − 50 = +50).
    assert _codes(P.network_findings(_cross_hour_network(storage="store"))) == [CROSS]


def test_cross_hour_needs_storage_and_a_gain_after_losses():
    assert P.network_findings(_cross_hour_network(storage=None)) == []
    # 100 × 0.4 − 50 = −10.
    assert P.network_findings(_cross_hour_network(eta_rt=0.4)) == []


def test_a_pair_flagged_in_the_same_hour_is_not_flagged_twice():
    n = _cycling_network(40.0)
    n.add("StorageUnit", "bess", bus="site", p_nom_extendable=True, max_hours=2.0)
    assert _codes(P.network_findings(n)) == [SAME]


def test_no_reverse_pair_densifies_nothing(monkeypatch):
    idx = pd.date_range("2030-01-01", periods=48, freq="h")
    n = pypsa.Network()
    n.set_snapshots(idx)
    for i in range(31):
        n.add("Bus", f"b{i}")
    for i in range(30):
        n.add("Link", f"l{i}", bus0=f"b{i}", bus1=f"b{i + 1}", p_nom=1.0,
              marginal_cost=pd.Series(-5.0, index=idx))
    calls = []
    real = pypsa.Network.get_switchable_as_dense

    def spy(self, component, attr, snapshots=None, inds=None):
        calls.append((component, attr))
        return real(self, component, attr, snapshots=snapshots, inds=inds)

    monkeypatch.setattr(pypsa.Network, "get_switchable_as_dense", spy)
    assert P.network_findings(n) == []
    assert calls == []


def test_validate_for_run_runs_it_without_a_commercial_config():
    issues = validate_for_run(_cycling_network(40.0), SolverConfig())
    hit = [i for i in issues if i.code == SAME]
    assert len(hit) == 1 and hit[0].severity == "warning"
    cross = validate_for_run(_cross_hour_network(), SolverConfig())
    assert P.cycling_flags(cross) == [CROSS]


# ── with a commercial config: the materialised prices ─────────────────────

ENERGY = {"id": "e", "kind": "energy", "unit": "per_kwh",
          "periods": [{"name": "all", "rate": 0.10}]}
TOU = {"id": "tou", "kind": "energy", "unit": "per_kwh",
       "periods": [{"name": "night", "rate": 0.05, "start_hour": 0, "end_hour": 6},
                   {"name": "day", "rate": 0.20}]}
REF = {"id": "px", "version": 1, "hash": "a" * 64, "source": "t"}


def _tariff(*items):
    return {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2030-01-01",
            "items": list(items)}


def _edge(price, *, import_eff=1.0, storage=True):
    """The edge fixture (import grid → poc; BESS behind poc → site at
    0.95 × 0.95 = 0.9025) with an export Link poc → grid and an export price."""
    n = build_edge_15min()
    n.links.loc["import", "efficiency"] = import_eff
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC")
    if not storage:
        n.remove("StorageUnit", "bess")
    values = price(n.snapshots) if callable(price) else np.full(len(n.snapshots), price)
    n.links_t[L.EXPORT_PRICE_ATTR] = pd.DataFrame({"export": values}, index=n.snapshots)
    return n


def _commercial(*items):
    return {"poc_link": "import", "export_link": "export", "export_price_ref": REF,
            "import_tariff": _tariff(*(items or (ENERGY,)))}


def test_an_export_price_above_the_tariff_flags_every_interval():
    # 500 × 1.0 − 100 = +400 in all 672 quarter-hours.
    out = P.commercial_findings(_edge(500.0), _commercial())
    hit = [f for f in out if f[1] == C_SAME]
    assert len(hit) == 1 and "672" in hit[0][4]
    assert C_CROSS not in _codes(out)


def test_the_import_links_efficiency_removes_a_loop_that_does_not_pay():
    # Same hour: 500 × 0.15 − 100 = −25. Across hours with the BESS:
    # 500 × 0.15 × 0.9025 − 100 = −32.3. Neither pays.
    out = P.commercial_findings(_edge(500.0, import_eff=0.15), _commercial())
    assert C_SAME not in _codes(out) and C_CROSS not in _codes(out)
    # 0.25: 500 × 0.25 − 100 = +25 per MWh still pays.
    out = P.commercial_findings(_edge(500.0, import_eff=0.25), _commercial())
    assert C_SAME in _codes(out)


def _evening(idx):
    h = np.asarray(idx.hour)
    return np.where((h >= 17) & (h < 21), 150.0, 0.0)


def test_storage_behind_the_meter_carries_night_import_to_evening_export():
    # Night import 0.05 €/kWh = 50 €/MWh, day 200; evening export price 150.
    # Same hour: 150 − 200 < 0 in the evening, 0 − 50 < 0 at night.
    # Across hours via the BESS (behind poc → site): 150 × 1.0 × 0.9025 − 50 = +85.38.
    out = P.commercial_findings(_edge(_evening), _commercial(TOU))
    assert C_SAME not in _codes(out)
    hit = [f for f in out if f[1] == C_CROSS]
    assert len(hit) == 1
    assert "bess" in hit[0][4] and "85.38" in hit[0][4]


def test_no_storage_behind_the_meter_no_cross_interval_loop():
    out = P.commercial_findings(_edge(_evening, storage=False), _commercial(TOU))
    assert C_SAME not in _codes(out) and C_CROSS not in _codes(out)


def test_the_raw_pair_check_does_not_run_with_a_commercial_config():
    """With a commercial config the raw marginal costs are not what the solve
    prices: only the materialised check speaks."""
    n = _edge(500.0)
    n.links.loc["import", "marginal_cost"] = 30.0
    n.links.loc["export", "marginal_cost"] = -40.0   # a raw pair that would flag
    issues = validate_for_run(n, SolverConfig(commercial=_commercial()))
    codes = {i.code for i in issues}
    assert SAME not in codes and CROSS not in codes
    assert C_SAME in codes


def test_cycling_flags_lists_every_cycling_code_once_in_a_stable_order():
    class I:  # noqa: E742 — a stand-in Issue
        def __init__(self, code):
            self.code = code

    issues = [I(C_CROSS), I("other"), I(SAME), I(C_CROSS)]
    assert P.cycling_flags(issues) == [SAME, C_CROSS]
    assert set(P.CYCLING_CODES) == {SAME, CROSS, C_SAME, C_CROSS}


def test_a_crash_in_the_raw_check_is_a_warning_not_a_500(monkeypatch):
    monkeypatch.setattr(P, "_reverse_pairs", lambda links: 1 / 0)
    out = P.network_findings(_cycling_network(40.0))
    assert _codes(out) == ["commercial.preflight_incomplete"]
    assert out[0][0] == "warning"
