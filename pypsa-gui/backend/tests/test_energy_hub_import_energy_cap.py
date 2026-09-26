"""
P17 — energy import cap (spec §6 amendment 2026-09-26; plan B13 / R2).

Per investment period P:  Σ_{t∈P} w_t · η · p0_t  ≤  E × Σ_{t∈P} w_t / 8760,
w = the `generators` snapshot weighting WITHOUT the years multiplier; metered
Links are the import Links oriented grid → hub (hub side from the P11
boundary rule).
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from models.energy_hub import OptimizationLevers, default_weak_flexible_pack
from services.adequacy import archetypes as A
from services.solver_service import SolverConfig
from tests.test_energy_hub_frontier_fmea import _feeder_hub

HOURS = 12


def _pack(energy=None, **lever_kw):
    base = default_weak_flexible_pack()
    upd = {"import_overlay": base.import_overlay.model_copy(
        update={"import_energy_mwh_per_year": energy})}
    if lever_kw:
        upd["levers"] = base.levers.model_copy(update=lever_kw)
    return base.model_copy(update=upd)


def _budget(e_mwh_per_year, hours=HOURS):
    return e_mwh_per_year * hours / 8760.0


def _solve(n, cfg):
    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation
    PyPSAService.set_network(n)
    return run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                          queue.SimpleQueue())


def _hub_import_mwh(n, link="import"):
    eff = float(n.links.at[link, "efficiency"])
    w = n.snapshot_weightings["generators"]
    return float((n.links_t.p0[link] * eff * w).sum())


def _cost(n):
    return float(n.objective)


# ── orientation, refusals, scope ────────────────────────────────────────────


def test_patch_meters_grid_to_hub_links():
    patch, notes = A.solver_config_patch_with_preflight(
        _pack(100_000.0), network=_feeder_hub())
    assert patch["import_energy_cap_mwh_per_year"] == 100_000.0
    assert patch["import_energy_links"] == ["import"]
    assert not any("reserved" in w for w in notes)


def test_no_energy_field_means_no_cap_fields():
    patch, _ = A.solver_config_patch_with_preflight(_pack(None),
                                                    network=_feeder_hub())
    assert "import_energy_cap_mwh_per_year" not in patch


def test_a_hub_to_grid_one_way_link_is_left_out_with_a_note():
    n = _feeder_hub()
    n.add("Link", "export", bus0="hub", bus1="grid", p_nom=20.0,
          efficiency=1.0, carrier="AC")
    n.links.at["export", "eh_role"] = "grid_import"
    patch, notes = A.solver_config_patch_with_preflight(_pack(1e5), network=n)
    assert patch["import_energy_links"] == ["import"]
    assert any("export" in w and "hub → grid" in w for w in notes)


def test_only_a_hub_to_grid_link_is_refused():
    n = _feeder_hub()
    n.links.loc["import", ["bus0", "bus1"]] = ["hub", "grid"]
    with pytest.raises(A.ArchetypePackError, match="grid → hub"):
        A.solver_config_patch_with_preflight(_pack(1e5), network=n)


def test_a_bidirectional_link_is_refused():
    n = _feeder_hub()
    n.links.at["import", "p_min_pu"] = -1.0
    with pytest.raises(A.ArchetypePackError, match="bidirectional"):
        A.solver_config_patch_with_preflight(_pack(1e5), network=n)


def test_an_unorientable_boundary_is_refused():
    n = _feeder_hub()
    n.links["eh_role"] = ""                       # carrier-rule selection
    with pytest.raises(A.ArchetypePackError, match="hub side"):
        A.solver_config_patch_with_preflight(_pack(1e5), network=n)


@pytest.mark.parametrize("factory", ["default_strong_grid_pack",
                                     "default_off_grid_pack"])
def test_other_archetypes_refuse_the_energy_field(factory):
    import models.energy_hub as M
    base = getattr(M, factory)()
    pack = base.model_copy(update={"import_overlay": base.import_overlay.model_copy(
        update={"import_energy_mwh_per_year": 1e5})})
    with pytest.raises(A.ArchetypePackError, match="weak_flexible"):
        A.solver_config_patch_with_preflight(pack, network=_feeder_hub())


def test_apply_no_longer_warns_reserved():
    res = A.apply_archetype_pack_detailed(_feeder_hub(), _pack(1e5))
    assert not any("reserved" in w for w in res.warnings)


def test_cap_fields_are_pack_only():
    from models.schemas import SolverConfigSchema
    from routers.projects import _solver_config_from_dict
    for f in ("import_energy_cap_mwh_per_year", "import_energy_links"):
        assert f not in SolverConfigSchema.model_fields
    cfg = _solver_config_from_dict({"voll": 10.0,
                                    "import_energy_cap_mwh_per_year": 5.0,
                                    "import_energy_links": ["x"]})
    assert cfg.import_energy_cap_mwh_per_year is None
    assert cfg.import_energy_links == []


def test_rolling_and_bidirectional_are_preflight_errors():
    from services.validation_service import validate_for_run
    n = _feeder_hub()
    cfg = SolverConfig(voll=3000.0, solve_strategy="rolling",
                       import_energy_cap_mwh_per_year=1e5,
                       import_energy_links=["import"])
    codes = {i.code for i in validate_for_run(n, cfg) if i.severity == "error"}
    assert "import_energy_cap_unsupported_strategy" in codes
    n.links.at["import", "p_min_pu"] = -0.5
    cfg.solve_strategy = "full"
    codes = {i.code for i in validate_for_run(n, cfg) if i.severity == "error"}
    assert "import_energy_link_bidirectional" in codes


# ── the constraint, solved ──────────────────────────────────────────────────


def _capped(cap, eff=1.0):
    n = _feeder_hub()
    n.links.at["import", "efficiency"] = eff
    A.apply_archetype_pack_detailed(n, _pack(None))
    cfg = SolverConfig(voll=3000.0)
    if cap is not None:
        cfg.import_energy_cap_mwh_per_year = cap
        cfg.import_energy_links = ["import"]
    status, cond = _solve(n, cfg)
    assert status in ("ok", "optimal"), cond
    return n


@pytest.mark.live_solve
def test_a_binding_cap_raises_cost_monotonically():
    runs = [_capped(c) for c in (None, 300_000.0, 150_000.0, 0.0)]
    costs = [_cost(n) for n in runs]
    assert costs == sorted(costs) and costs[-1] > costs[0] + 1.0
    for n, cap in zip(runs[1:], (300_000.0, 150_000.0, 0.0)):
        assert _hub_import_mwh(n) <= _budget(cap) + 1e-3


@pytest.mark.live_solve
def test_import_is_metered_at_the_hub_as_p0_times_efficiency():
    cap = 150_000.0
    n = _capped(cap, eff=0.8)
    delivered = _hub_import_mwh(n)                  # η·p0
    drawn = float((n.links_t.p0["import"]).sum())
    assert delivered == pytest.approx(_budget(cap), rel=1e-4)  # binding
    assert drawn == pytest.approx(delivered / 0.8, rel=1e-6)


def _two_period() -> pypsa.Network:
    n = pypsa.Network()
    periods, per = [2030, 2040], 4
    snaps = pd.MultiIndex.from_product(
        [periods, pd.date_range("2030-01-01", periods=per, freq="h")])
    n.set_snapshots(snaps)
    n.investment_periods = periods
    n.investment_period_weightings.loc[:, "years"] = 10.0
    n.snapshot_weightings.loc[:, :] = 1.0
    for c in ("gas", "AC"):
        n.add("Carrier", c)
    n.add("Bus", "hub", carrier="AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Load", "l", bus="hub", p_set=50.0)
    n.add("Generator", "local", bus="hub", carrier="gas", p_nom=60.0,
          marginal_cost=100.0)
    n.add("Generator", "remote", bus="grid", carrier="gas", p_nom=200.0,
          marginal_cost=10.0)
    n.add("Link", "import", bus0="grid", bus1="hub", p_nom=100.0,
          efficiency=1.0, carrier="AC")
    n.links["eh_role"] = ""
    n.links.at["import", "eh_role"] = "grid_import"
    return n


@pytest.mark.live_solve
def test_the_cap_is_per_year_of_each_period_not_times_years():
    n = _two_period()
    cap = 100_000.0
    cfg = SolverConfig(voll=3000.0, multi_investment_periods=True,
                       import_energy_cap_mwh_per_year=cap,
                       import_energy_links=["import"])
    status, cond = _solve(n, cfg)
    assert status in ("ok", "optimal"), cond
    per_period = n.links_t.p0["import"].groupby(level=0).sum()
    budget = cap * 4 / 8760.0                      # 45.7 MWh per period
    for p in (2030, 2040):
        assert per_period[p] == pytest.approx(budget, rel=1e-4)


# ── the lever ────────────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_the_import_energy_lever_gives_differentiated_options():
    from services.adequacy import levers as L
    from services.pypsa_service import PyPSAService

    n = _feeder_hub()
    A.apply_archetype_pack_detailed(n, _pack(None))
    PyPSAService.set_network(n)
    cfg = SolverConfig(voll=3000.0, ens_cap_permyriad=10.0,
                       import_energy_links=["import"])
    table = L.compare_lever_scenarios(
        n, cfg, lock=PyPSAService.get_lock(), stop_event=threading.Event(),
        kind="import_energy")
    ok = [o for o in table["options"] if o["status"] in ("ok", "optimal")]
    assert len(ok) >= 2 and table["distinct_costs"] >= 2
    assert all(o["unit"] == "MWh/yr" for o in ok)
    assert L.levers_section_status(table)[0] == "ok"


def test_the_lever_needs_metered_links():
    from services.adequacy import levers as L
    with pytest.raises(L.LeverScenarioError, match="metered import Links"):
        L.apply_lever_scenario(_feeder_hub(), "import_energy", value=1e5,
                               cfg=SolverConfig())


def test_the_pack_lever_flag_exists():
    assert OptimizationLevers().import_energy is False


# ── the request path ─────────────────────────────────────────────────────────


def test_overrides_accept_the_energy_cap_only_for_weak_flexible():
    from fastapi import HTTPException

    from models.energy_hub import default_strong_grid_pack
    from services.adequacy.eh_study_runner import apply_pack_overrides
    out = apply_pack_overrides(default_weak_flexible_pack(),
                               {"import_energy_mwh_per_year": 1e5},
                               raise_http=True)
    assert out.import_overlay.import_energy_mwh_per_year == 1e5
    with pytest.raises(HTTPException) as exc:
        apply_pack_overrides(default_strong_grid_pack(),
                             {"import_energy_mwh_per_year": 1e5},
                             raise_http=True)
    assert "weak_flexible" in str(exc.value.detail)


def test_a_bidirectional_import_is_a_422_before_the_study_starts(
        client, install_network):
    n = _feeder_hub()
    n.links.at["import", "p_min_pu"] = -1.0
    install_network(n)
    r = client.post("/api/results/eh_study", json={
        "archetype": "weak_flexible",
        "pack_overrides": {"import_energy_mwh_per_year": 1e5}})
    assert r.status_code == 422, r.text
    assert "bidirectional" in r.text
