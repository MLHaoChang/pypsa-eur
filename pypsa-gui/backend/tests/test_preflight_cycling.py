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


# ── round 1 review: B2 (storage only where electricity can come back) ─────


def _behind_meter(kind: str):
    """The evening-export / night-import case (85.38 €/MWh with the BESS),
    the BESS replaced by storage of `kind` behind the meter."""
    n = _edge(_evening, storage=False)
    if kind == "heat_tank":
        n.add("Bus", "heat", carrier="heat")
        n.add("Link", "heat_pump", bus0="site", bus1="heat", p_nom=10.0, efficiency=3.0)
        n.add("Store", "tank", bus="heat", e_nom_extendable=True)
    elif kind in ("h2_no_fc", "h2_fc"):
        n.add("Bus", "h2", carrier="H2")
        n.add("Link", "electrolyser", bus0="site", bus1="h2", p_nom=10.0, efficiency=0.7)
        n.add("Store", "h2_store", bus="h2", e_nom_extendable=True)
        if kind == "h2_fc":
            n.add("Link", "fuel_cell", bus0="h2", bus1="site", p_nom=10.0, efficiency=0.5)
    elif kind == "battery_bus":
        n.add("Bus", "battery", carrier="battery")
        n.add("Link", "charger", bus0="site", bus1="battery", p_nom=10.0, efficiency=0.95)
        n.add("Link", "discharger", bus0="battery", bus1="site", p_nom=10.0, efficiency=0.95)
        n.add("Store", "battery_store", bus="battery", e_nom_extendable=True)
    elif kind == "ac_battery":
        n.add("StorageUnit", "bess2", bus="site", p_nom=5.0, max_hours=2.0,
              efficiency_store=0.95, efficiency_dispatch=0.95)
    return n


@pytest.mark.parametrize("kind", ["heat_tank", "h2_no_fc"])
def test_storage_that_cannot_return_electricity_is_not_a_loop(kind):
    out = P.commercial_findings(_behind_meter(kind), _commercial(TOU))
    assert C_CROSS not in _codes(out)


@pytest.mark.parametrize("kind", ["ac_battery", "battery_bus", "h2_fc"])
def test_storage_that_can_return_electricity_still_warns(kind):
    out = P.commercial_findings(_behind_meter(kind), _commercial(TOU))
    assert C_CROSS in _codes(out)


# ── round 1 review: non-binding 1 and 2 ────────────────────────────────────


def test_a_loop_the_efficiency_blind_count_misses_is_found():
    """Import paid −7 €/MWh, an export FEE of 10, η_import = 0.5: blind,
    −7 + 10 = +3 (no loop); netted, 0.5 × (−10) − (−7) = +2 per MWh: a loop
    in all 672 quarter-hours."""
    n = build_edge_15min()
    n.links.loc["import", ["marginal_cost", "efficiency"]] = [-7.0, 0.5]
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC",
          marginal_cost=10.0)
    out = P.commercial_findings(n, {"poc_link": "import", "export_link": "export"})
    hit = [f for f in out if f[1] == C_SAME]
    assert len(hit) == 1 and "672" in hit[0][4]


def test_the_storage_variant_reads_the_efficiency_at_the_import_snapshot():
    """η_import 0.3 at night (the cheap import) and 1.0 otherwise. At the
    import snapshot: 150 × 0.3 × 0.9025 − 50 = −9.39; by day 150 × 0.9025 −
    200 < 0. Read at the export snapshot (η 1.0) it would wrongly be +85.38."""
    n = _edge(_evening)
    h = np.asarray(n.snapshots.hour)
    n.links_t.efficiency["import"] = np.where(h < 6, 0.3, 1.0)
    out = P.commercial_findings(n, _commercial(TOU))
    assert C_CROSS not in _codes(out) and C_SAME not in _codes(out)


# ── round 1 review: B4 (one storage map per call, numpy gains) ─────────────


def _gs_reference(n):
    """The GS branch's `_check_export_cycling` + `_cross_hour_cycling` +
    `_site_storage`, verbatim but for the tuple output: the parity oracle."""
    links = n.links
    if len(links) < 2:
        return []
    by_buses: dict = {}
    for name, b0, b1 in zip(links.index, links["bus0"].astype(str), links["bus1"].astype(str)):
        by_buses.setdefault((b0, b1), []).append(name)
    pairs, seen = [], set()
    for (b0, b1), forward in by_buses.items():
        for a in forward:
            for b in by_buses.get((b1, b0), []):
                key = frozenset((a, b))
                if a == b or key in seen:
                    continue
                seen.add(key)
                pairs.append((a, b, b0, b1))
    if not pairs:
        return []
    paired = pd.Index(sorted({x for a, b, _, _ in pairs for x in (a, b)}))
    mc = n.get_switchable_as_dense("Link", "marginal_cost", inds=paired)
    eff = links["efficiency"].astype(float).fillna(1.0)

    def num(row, col, default):
        try:
            v = float(row.get(col, default))
        except (TypeError, ValueError):
            return default
        return default if v != v else v

    def site_storage(bus):
        best = None
        for df, is_su in ((n.storage_units, True), (n.stores, False)):
            rows = df[df["bus"].astype(str) == bus]
            if "active" in rows.columns:
                rows = rows[rows["active"].astype(bool)]
            for name, row in rows.iterrows():
                if is_su:
                    can = bool(row.get("p_nom_extendable", False)) or num(row, "p_nom", 0.0) > 0
                    if not can or num(row, "max_hours", 0.0) <= 0:
                        continue
                    eta = num(row, "efficiency_store", 1.0) * num(row, "efficiency_dispatch", 1.0)
                    if best is None or eta > best[1]:
                        best = (str(name), eta)
                elif bool(row.get("e_nom_extendable", False)) or num(row, "e_nom", 0.0) > 0:
                    if best is None or 1.0 > best[1]:
                        best = (str(name), 1.0)
        return best

    out = []
    for a, b, b0, b1 in pairs:
        gain = np.maximum((-mc[b] * float(eff[a]) - mc[a]).to_numpy(),
                          (-mc[a] * float(eff[b]) - mc[b]).to_numpy())
        hit = gain > 1e-9
        if not hit.any():
            for imp, exp, site in ((a, b, b1), (b, a, b0)):
                store = site_storage(site)
                if store is None:
                    continue
                name, eta = store
                credit = -mc[exp]
                g = float(credit.max()) * float(eff[imp]) * eta - float(mc[imp].min())
                if g > 1e-9:
                    out.append((SAME + "_via_storage", imp, name, round(g, 9),
                                credit.idxmax(), mc[imp].idxmin()))
                    break
            continue
        out.append((SAME, a, b, int(hit.sum()), mc.index[hit][0], round(float(gain.max()), 9)))
    return out


def _random_network(seed: int, buses: int = 12, hours: int = 48) -> pypsa.Network:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2030-01-01", periods=hours, freq="h")
    n = pypsa.Network()
    n.set_snapshots(idx)
    for i in range(buses):
        n.add("Bus", f"b{i}")
    for k in range(buses * 2):
        i, j = rng.choice(buses, size=2, replace=False)
        mc = rng.normal(40, 30, hours) if rng.random() < 0.6 else float(rng.normal(20, 30))
        n.add("Link", f"l{k}", bus0=f"b{i}", bus1=f"b{j}", p_nom=1.0,
              efficiency=float(rng.uniform(0.3, 1.0)),
              marginal_cost=pd.Series(mc, index=idx) if np.ndim(mc) else mc)
    # Anti-phase pairs: an import dear by day, an export credit high in the
    # evening, so some pairs pay only ACROSS hours (the storage branch).
    h = np.arange(hours) % 24
    for k in range(buses // 2):
        i, j = rng.choice(buses, size=2, replace=False)
        imp = rng.uniform(30, 60) + np.where(h < 6, 0.0, rng.uniform(40, 80))
        credit = np.where((h >= 17) & (h <= 20), rng.uniform(40, 120), 0.0)
        n.add("Link", f"p{k}_in", bus0=f"b{i}", bus1=f"b{j}", p_nom=1.0,
              efficiency=float(rng.uniform(0.6, 1.0)), marginal_cost=pd.Series(imp, index=idx))
        n.add("Link", f"p{k}_out", bus0=f"b{j}", bus1=f"b{i}", p_nom=1.0,
              marginal_cost=pd.Series(-credit, index=idx))
    for k in range(buses):
        b = f"b{rng.integers(buses)}"
        if rng.random() < 0.5:
            n.add("StorageUnit", f"su{k}", bus=b, p_nom=float(rng.choice([0.0, 1.0])),
                  p_nom_extendable=bool(rng.random() < 0.3), max_hours=float(rng.choice([0.0, 2.0])),
                  efficiency_store=float(rng.uniform(0.5, 1.0)),
                  efficiency_dispatch=float(rng.uniform(0.5, 1.0)))
        else:
            n.add("Store", f"st{k}", bus=b, e_nom=float(rng.choice([0.0, 1.0])))
    return n


def _as_reference(findings, n):
    """Our tuples projected onto the oracle's fields (parsed from the facts
    the message carries)."""
    import re

    out = []
    for _sev, code, _cls, name, msg in findings:
        if code == SAME:
            a, b = re.findall(r"'([^']+)'", msg)[:2]
            count = int(re.search(r"in (\d+) snapshot", msg).group(1))
            out.append((code, a, b, count))
        else:
            out.append((code, name, re.findall(r"storage '([^']+)'", msg)[0]))
    return out


@pytest.mark.parametrize("seed", range(8))
def test_the_vectorised_check_matches_the_gs_reference(seed):
    n = _random_network(seed)
    ref = _gs_reference(n)
    got = P.network_findings(n)
    assert _as_reference(got, n) == [r[:4] if r[0] == SAME else r[:3] for r in ref]
    # The figures in the messages agree too.
    for (_s, code, _c, _n, msg), r in zip(got, ref):
        if code == SAME:
            assert f"{r[5]:,.2f}" in msg and f"first {r[4]}" in msg
        else:
            assert f"{r[3]:,.2f}" in msg


def test_a_sector_style_network_at_8760_h_is_fast():
    """40 buses, 80 reverse-paired Links with hourly prices, storage on half
    the buses, a year of hours: well under 0.1 s on the reference machine
    (asserted at 0.3 s against CI noise)."""
    import time

    rng = np.random.default_rng(0)
    idx = pd.date_range("2030-01-01", periods=8760, freq="h")
    n = pypsa.Network()
    n.set_snapshots(idx)
    for i in range(40):
        n.add("Bus", f"b{i}")
    mc = pd.DataFrame(rng.uniform(20, 80, (8760, 80)), index=idx,
                      columns=[f"f{i}" for i in range(40)] + [f"r{i}" for i in range(40)])
    for i in range(40):
        j = (i + 1) % 40
        n.add("Link", f"f{i}", bus0=f"b{i}", bus1=f"b{j}", p_nom=1.0, efficiency=0.9)
        n.add("Link", f"r{i}", bus0=f"b{j}", bus1=f"b{i}", p_nom=1.0, efficiency=0.9)
        if i % 2 == 0:
            n.add("StorageUnit", f"su{i}", bus=f"b{i}", p_nom=1.0, max_hours=4.0,
                  efficiency_store=0.6, efficiency_dispatch=0.6)
    n.links_t.marginal_cost = mc
    P.network_findings(n)   # warm-up
    t0 = time.perf_counter()
    P.network_findings(n)
    assert time.perf_counter() - t0 < 0.3
