"""
A net cost energy item on a multi-member group with an export Link, priced once
on the group's net import (Edge Investment Case P3 WP3.3b; oracle V6).

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.3b. P1
refused it: per-member adders charge gross member import the group meter nets
out. Now `ic_group_net_import[t] − ic_group_net_export[t] = Σ members p[t] −
p_export[t]` (both ≥ 0) carries the item at rates ≥ 0, removed from the member
and export adders; the record and the `energy_net_group` row recompute
max(0, Σ members − export) from the dispatch, so the LP row equals the bill to
the cent and the objective gap is 0. The bill (`bill_site`) is unchanged.
"""
from __future__ import annotations

import queue
import threading

import numpy as np
import pandas as pd
import pytest

from services.commercial import lp_bindings as L
from tests.fixtures.investment_case.edge_15min import build_edge_15min

NET_TOU = {"id": "net_energy", "kind": "energy", "unit": "per_kwh", "measured_on": "net",
           "periods": [{"name": "night", "rate": 0.05, "start_hour": 0, "end_hour": 6},
                       {"name": "peak", "rate": 0.40, "start_hour": 17, "end_hour": 21},
                       {"name": "day", "rate": 0.20}]}
LEVY = {"id": "levy", "kind": "tax_levy", "unit": "per_kwh",
        "periods": [{"name": "all", "rate": 0.01}]}
NET_REVENUE = {"id": "net_credit", "kind": "energy", "unit": "per_kwh", "measured_on": "net",
               "direction": "revenue", "periods": [{"name": "all", "rate": 0.03}]}


def _tariff(*items):
    return {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
            "items": list(items)}


def v6_network():
    """V6: three members behind one group connection and a group export Link —
    `import` (the edge site: PV, BESS, load), `import_b` (a load), `import_c`
    (PV only: it exports through `export` while the others import)."""
    n = build_edge_15min()
    n.generators.loc["grid_supply", ["marginal_cost", "p_min_pu"]] = [0.0, -1.0]
    for tag, load in (("b", 0.8), ("c", None)):
        n.add("Bus", f"poc_{tag}", carrier="AC")
        n.add("Bus", f"site_{tag}", carrier="AC")
        n.add("Link", f"import_{tag}", bus0="grid", bus1=f"poc_{tag}", p_nom=80.0, carrier="AC")
        n.add("Link", f"poc_site_{tag}", bus0=f"poc_{tag}", bus1=f"site_{tag}", p_nom=200.0,
              bus2="", carrier="AC", p_min_pu=-1.0)
        if load is not None:
            n.add("Load", f"site_{tag}_load", bus=f"site_{tag}",
                  p_set=n.loads_t.p_set["site_load"] * load)
    # Must-run: the group net-EXPORTS at midday, so the meter's max(0, ·) clips.
    n.add("Generator", "pv_c", bus="site_c", carrier="solar", p_nom=60.0,
          p_max_pu=n.generators_t.p_max_pu["pv"], p_min_pu=n.generators_t.p_max_pu["pv"])
    n.add("Link", "export", bus0="poc_c", bus1="grid", p_nom=80.0, carrier="AC")
    return n


def v6_commercial(*items, **extra):
    return {"poc_link": "import", "export_link": "export", "group_contract": "hub",
            "group_members": ["import", "import_b", "import_c"], "group_cap_mw": 150.0,
            "import_tariff": _tariff(*(items or (NET_TOU, LEVY))), **extra}


def _solve(n, commercial, **cfg_kw):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    cfg = SolverConfig(commercial=commercial, **cfg_kw)
    status, condition = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                       queue.SimpleQueue(), state_update=lambda **kw: None)
    assert status in ("ok", "optimal"), (status, condition)
    return cfg


def _net_mwh(n):
    p0 = n.links_t.p0
    return (p0["import"] + p0["import_b"] + p0["import_c"] - p0["export"]).to_numpy()


# ── binding: the item leaves the adders, the term is set ───────────────────


def test_the_net_item_leaves_the_member_and_export_adders():
    n = v6_network()
    applied = L.materialise_poc_prices(n, v6_commercial())
    mc = n.links_t.marginal_cost
    levy = 0.01 * 1000.0                                   # only the levy rides the members
    for member in ("import", "import_b", "import_c"):
        assert np.allclose(mc[member], levy), member
    assert "export" not in mc.columns                      # nothing priced on export
    spec = getattr(n, L.GROUP_NET_SPEC_ATTR)
    assert spec["items"] == ["net_energy"] and spec["export"] == "export"
    assert applied.facts["group_net_items"] == ["net_energy"]
    assert "net_energy" not in applied.facts["energy_items"]
    applied.undo()
    assert not hasattr(n, L.GROUP_NET_SPEC_ATTR)


def test_a_negative_net_rate_is_refused_naming_the_item():
    bad = {**NET_TOU, "periods": [{"name": "all", "rate": -0.01}]}
    with pytest.raises(L.CommercialBindingError, match="net_energy"):
        L.materialise_poc_prices(v6_network(), v6_commercial(bad))


@pytest.mark.parametrize("items", [(NET_REVENUE,), (NET_TOU, LEVY, NET_REVENUE)],
                         ids=["alone", "beside_the_net_cost_item"])
def test_a_net_revenue_item_on_the_group_stays_refused(items):
    """Review #1: priced on gross export beside a circulation-neutral net cost
    item, it pays the LP to import through a member and export at once."""
    with pytest.raises(L.CommercialBindingError, match="net_credit"):
        L.validate_for_network(v6_network(), v6_commercial(*items))


def test_an_unrated_group_net_item_is_refused_at_config_time():
    """Review #2: the config route and preflight see what the solve refuses."""
    night_only = {**NET_TOU, "periods": [NET_TOU["periods"][0]]}
    with pytest.raises(L.CommercialBindingError, match="no period covering"):
        L.validate_for_network(v6_network(), v6_commercial(night_only))


def test_one_member_or_no_export_keeps_the_p1_pricing():
    n = v6_network()
    one = v6_commercial(group_members=["import"], export_link="export")
    assert L.group_net_items(L._parse(one)) == []
    none = v6_commercial()
    none.pop("export_link")
    assert L.group_net_items(L._parse(none)) == []


# ── V6: live ───────────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_v6_the_lp_row_is_the_bill_to_the_cent_and_the_gap_is_zero():
    from models.commercial import Tariff
    from services.commercial.billing import bill_site
    from services.commercial.gap import billing_vs_lp_gap
    from services.commercial.tariff_engine import rate
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    n = v6_network()
    commercial = v6_commercial()
    cfg = _solve(n, commercial)
    net = _net_mwh(n)
    p0 = n.links_t.p0
    both = (p0["export"] > 1e-6) & ((p0["import"] + p0["import_b"]) > 1e-6)
    assert both.any()                                     # it nets: export and import at once
    assert (net < -1e-6).any() and (net > 1e-6).any()     # and net-exports at times
    rec = n.meta[L.META_GROUP_NET]
    assert rec["items"] == ["net_energy"] and rec["lp_recipe"] == L.GROUP_NET_RECIPE
    cb = compute_cost_breakdown(n, cfg)
    row = cb["commercial"]["energy_net_group"]
    bill = bill_site(n, commercial).per_period[None]
    assert abs(row - bill.per_item["net_energy"]) < 0.005
    # No billing change: the bill still rates Σ members − export as the engine does.
    direct = rate(pd.DataFrame({"import_mw": (p0["import"] + p0["import_b"] + p0["import_c"])
                                .to_numpy(), "export_mw": p0["export"].to_numpy()},
                               index=n.snapshots),
                  Tariff.model_validate(commercial["import_tariff"]), step_hours=0.25,
                  timezone=None)
    assert bill.per_item == pytest.approx(direct.per_item, rel=1e-12)
    assert abs(compute_objective_decomposition(n, cb)["gap_pct"]) < 1e-6
    # Members importing while another exports is metering here, not a
    # mispricing: no `simultaneous_import_export` (review #3).
    assert "simultaneous_import_export" not in cb["commercial"]["flags"]
    gap = billing_vs_lp_gap(n, commercial, bill_site(n, commercial))
    energy = gap["periods"][None]["energy"]
    assert abs(energy["unattributed"]) < 0.01, energy
    assert not [c for c in energy["causes"] if c["cause"] == "net_split_by_direction"]
    assert energy["items"]["net_energy"]["lp"] == pytest.approx(row, rel=1e-9)
    assert gap["gates"] == []


@pytest.mark.live_solve
def test_the_net_term_is_priced_once():
    """Priced on the adders too, the objective would pay the item twice and the
    rows (priced once) would miss the objective."""
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    n = v6_network()
    cfg = _solve(n, v6_commercial(NET_TOU))
    cb = compute_cost_breakdown(n, cfg)
    assert cb["commercial"].get("energy_import") in (None, 0.0)
    assert abs(compute_objective_decomposition(n, cb)["gap_pct"]) < 1e-6


@pytest.mark.live_solve
def test_an_edited_net_item_after_the_solve_is_drift():
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.solver_service import SolverConfig

    n = v6_network()
    _solve(n, v6_commercial())
    edited = {**NET_TOU, "periods": [{"name": "all", "rate": 0.30}]}
    cb = compute_cost_breakdown(n, SolverConfig(commercial=v6_commercial(edited, LEVY)))
    assert "config_changed_since_solve" in cb["commercial"]["flags"]
    cb = compute_cost_breakdown(n, SolverConfig(commercial=v6_commercial()))
    assert "config_changed_since_solve" not in cb["commercial"]["flags"]


@pytest.mark.live_solve
@pytest.mark.parametrize("strategy", ["rolling", "myopic"])
def test_rolling_and_myopic_carry_the_term_per_window(strategy):
    """The same coverage as the group cap: a per-snapshot term, rebuilt in
    every window (rolling) or period (myopic); no strategy refuses it."""
    from models.commercial import Tariff
    from services.commercial.billing import bill_site
    from services.results.cost_breakdown import compute_cost_breakdown

    n = v6_network()
    kw = {"solve_strategy": strategy}
    if strategy == "rolling":
        kw.update(rolling_horizon=96, rolling_overlap=0)
    else:
        n.set_investment_periods([2030, 2040])
        kw.update(multi_investment_periods=True)
    commercial = v6_commercial()
    cfg = _solve(n, commercial, **kw)
    assert n.meta.get(L.META_GROUP_NET)
    cb = compute_cost_breakdown(n, cfg)
    bill = bill_site(n, commercial)
    billed = sum(r.per_item_sampled["net_energy"] for r in bill.per_period.values())
    from services.commercial.cost_rows import commercial_cost_terms

    items = commercial_cost_terms(n, commercial)["items"]
    rows = sum(cx + ox for lab, _p, cx, ox in items if lab == "energy_net_group")
    assert abs(rows - billed) < 0.005, (strategy, rows, billed)
    assert cb["commercial"]["energy_net_group"] is not None
