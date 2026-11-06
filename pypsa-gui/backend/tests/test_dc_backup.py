"""DC grid readiness 03: battery-based backup in place of diesel."""
from __future__ import annotations

import math

import numpy as np
import pytest

from models.flex_archetypes import BackupSpec, BessSpec
from services.adequacy import dc_backup


def _bk(**kw) -> BackupSpec:
    bess = kw.pop("bess", {})
    base = {"power_mw": 1.0, "energy_mwh": 1.0, "round_trip_efficiency": 0.81,
            "depth_of_discharge": 0.9, "calendar_fade_per_year": 0.0,
            "cycle_fade_per_full_cycle": 0.0, "augmentation_policy": "restore_to_nameplate"}
    return BackupSpec(mode=kw.pop("mode", "bess"), bess=BessSpec(**{**base, **bess}), **kw)


# ── sizing ──────────────────────────────────────────────────────────────


def test_genset_mode_sizes_no_battery():
    s = dc_backup.size_bess([12.0], firm_genset_mw=30.0, backup=BackupSpec(),
                            redundancy="N", years=10)
    assert s["strings"] == 0 and s["p_mw"] == 0.0 and s["e_mwh"] == 0.0


@pytest.mark.parametrize("tier,strings", [("N", 3), ("N+1", 4), ("2N", 6)])
def test_redundancy_counts_strings(tier, strings):
    # 12 MW critical peak, 5 MW strings: N = ceil(12/5) = 3.
    s = dc_backup.size_bess(np.array([8.0, 12.0, 10.0]), firm_genset_mw=0.0,
                            backup=_bk(autonomy_h=4.0, ride_through_s=0.0),
                            redundancy=tier, years=10)
    assert s["shortfall_mw"] == pytest.approx(12.0)
    assert s["strings_n"] == 3
    assert s["strings"] == strings
    assert s["p_mw"] == pytest.approx(5.0 * strings)


def test_energy_covers_autonomy_and_ride_through_after_dod_and_losses():
    # η_dispatch = sqrt(0.81) = 0.9; DoD 0.9; 4 h + 600 s.
    s = dc_backup.size_bess([5.0], firm_genset_mw=0.0,
                            backup=_bk(autonomy_h=4.0, ride_through_s=600.0),
                            redundancy="N", years=10)
    per_string = 5.0 * (4.0 + 600.0 / 3600.0) / (0.9 * 0.9)
    assert s["e_mwh_per_string"] == pytest.approx(per_string)
    assert s["e_mwh"] == pytest.approx(per_string)
    assert s["retention"] == 1.0
    # Deliverable energy of the N strings covers the critical peak for the duration.
    assert s["e_mwh"] * 0.9 * 0.9 >= 5.0 * (4.0 + 1 / 6) - 1e-9


def test_no_augmentation_oversizes_for_end_of_life_fade():
    bk = _bk(autonomy_h=2.0, ride_through_s=0.0, test_cycles_per_year=10.0,
             bess={"calendar_fade_per_year": 0.02, "cycle_fade_per_full_cycle": 0.001,
                   "augmentation_policy": "none"})
    s = dc_backup.size_bess([5.0], firm_genset_mw=0.0, backup=bk, redundancy="N", years=10)
    retention = 0.98 ** 10 * (1 - 0.001 * 10 * 10)
    assert s["retention"] == pytest.approx(retention)
    assert s["e_mwh_per_string"] == pytest.approx(5.0 * 2.0 / (0.9 * 0.9 * retention))


def test_fade_beyond_nameplate_is_refused():
    bk = _bk(test_cycles_per_year=100.0,
             bess={"cycle_fade_per_full_cycle": 0.01, "augmentation_policy": "none"})
    with pytest.raises(dc_backup.DcBackupError, match="fade"):
        dc_backup.size_bess([5.0], firm_genset_mw=0.0, backup=bk, redundancy="N", years=10)


def test_hybrid_sizes_on_the_shortfall_the_firm_gensets_leave():
    s = dc_backup.size_bess([18.0, 26.0], firm_genset_mw=20.0,
                            backup=_bk(mode="hybrid", ride_through_s=0.0),
                            redundancy="N", years=5)
    assert s["shortfall_mw"] == pytest.approx(6.0)
    assert s["strings_n"] == 2
    covered = dc_backup.size_bess([18.0], firm_genset_mw=20.0,
                                  backup=_bk(mode="hybrid"), redundancy="N+1", years=5)
    assert covered["strings"] == 0


def test_bess_mode_ignores_gensets():
    s = dc_backup.size_bess([12.0], firm_genset_mw=40.0, backup=_bk(),
                            redundancy="N", years=5)
    assert s["shortfall_mw"] == pytest.approx(12.0)


def test_sizing_rows_are_labelled_assumed_with_sources():
    s = dc_backup.size_bess([12.0], firm_genset_mw=0.0, backup=_bk(),
                            redundancy="N+1", years=5)
    keys = {r["key"] for r in s["inputs"]}
    assert {"autonomy_h", "ride_through_s", "string_mw", "round_trip_efficiency",
            "depth_of_discharge", "redundancy"} <= keys
    assert all("assumed" in r and r["source"] for r in s["inputs"])
    assert math.isfinite(s["e_mwh"])


# ── cost delta ──────────────────────────────────────────────────────────

_PRICES = {
    "gas-genset.investment.overnight": 500.0, "gas-genset.investment.fom_share": 0.02,
    "gas-genset.investment.lifetime": 25.0,
    "battery.power.overnight": 200.0, "battery.power.fom_share": 0.01,
    "battery.power.lifetime": 2.0,
    "battery.energy.overnight": 100.0, "battery.energy.fom_share": 0.0,
    "battery.energy.lifetime": 25.0,
    "discount_rate": 0.1, "currency": "EUR", "money_year": 2025, "source": "test",
}


def _sizing(mode="bess", p=10.0, e=20.0):
    return {"mode": mode, "p_mw": p, "e_mwh": e}


def test_one_year_delta_by_hand():
    bk = _bk(fuel_price_per_mwh_fuel=40.0)
    out = dc_backup.backup_cost_delta(_sizing(), bk, genset_mw=10.0, heat_rate=2.5,
                                      run_hours_per_year=50.0, years=1, prices=_PRICES)
    genset = 5000.0 + (0.02 * 5000.0 + 10 * 50 * 2.5 * 40.0) / 1.1
    bess = 2000.0 + 2000.0 + 0.01 * 2000.0 / 1.1
    assert out["npv_genset"] == pytest.approx(genset)
    assert out["npv_chosen"] == pytest.approx(bess)
    assert out["delta"] == pytest.approx(bess - genset)
    assert out["run_hours_avoided_per_year"] == 50.0
    assert out["fuel_mwh_avoided_per_year"] == pytest.approx(10 * 50 * 2.5)


def test_three_years_repurchases_the_power_part_at_its_lifetime():
    bk = _bk(fuel_price_per_mwh_fuel=0.0)
    out = dc_backup.backup_cost_delta(_sizing(e=0.0), bk, genset_mw=0.0, heat_rate=2.5,
                                      run_hours_per_year=0.0, years=3, prices=_PRICES)
    cash = [2000.0, 20.0, 20.0 + 2000.0, 20.0]
    assert out["npv_chosen"] == pytest.approx(sum(c / 1.1 ** i for i, c in enumerate(cash)))


def test_augmentation_restore_buys_each_years_fade():
    bk = _bk(test_cycles_per_year=10.0, bess={"calendar_fade_per_year": 0.02,
                                                "cycle_fade_per_full_cycle": 0.001})
    out = dc_backup.backup_cost_delta(_sizing(p=0.0, e=100.0), bk, genset_mw=0.0, heat_rate=2.5,
                                      run_hours_per_year=0.0, years=2, prices=_PRICES)
    # 3 % of 100 MWh a year at 100 /MWh.
    assert out["augmentation_mwh"] == pytest.approx(6.0)
    aug = next(x for x in out["lines"] if x["line"] == "augmentation")
    assert aug["npv"] == pytest.approx(300.0 / 1.1 + 300.0 / 1.21)


def test_no_augmentation_buys_nothing_and_fixed_years_buys_accumulated_fade():
    none = _bk(bess={"calendar_fade_per_year": 0.02, "augmentation_policy": "none"})
    assert dc_backup.backup_cost_delta(_sizing(e=100.0), none, genset_mw=0.0, heat_rate=2.5,
                                       run_hours_per_year=0.0, years=5,
                                       prices=_PRICES)["augmentation_mwh"] == 0.0
    fixed = _bk(bess={"calendar_fade_per_year": 0.02, "augmentation_policy": "fixed_years",
                      "augmentation_years": [3]})
    out = dc_backup.backup_cost_delta(_sizing(e=100.0), fixed, genset_mw=0.0, heat_rate=2.5,
                                      run_hours_per_year=0.0, years=5, prices=_PRICES)
    assert out["augmentation_mwh"] == pytest.approx(6.0)


def test_hybrid_delta_is_the_strings_and_genset_delta_is_zero():
    bk = _bk(mode="hybrid")
    hy = dc_backup.backup_cost_delta(_sizing("hybrid"), bk, genset_mw=10.0, heat_rate=2.5,
                                     run_hours_per_year=50.0, years=3, prices=_PRICES)
    bess = dc_backup.backup_cost_delta(_sizing("bess"), bk, genset_mw=0.0, heat_rate=2.5,
                                       run_hours_per_year=0.0, years=3, prices=_PRICES)
    assert hy["delta"] == pytest.approx(bess["npv_chosen"])
    gs = dc_backup.backup_cost_delta(_sizing("genset", 0.0, 0.0), BackupSpec(), genset_mw=10.0,
                                     heat_rate=2.5, run_hours_per_year=50.0, years=3,
                                     prices=_PRICES)
    assert gs["delta"] == 0.0


def test_rate_from_the_finance_case_overrides_the_pack():
    out = dc_backup.backup_cost_delta(_sizing(), _bk(), genset_mw=1.0, heat_rate=2.5,
                                      run_hours_per_year=1.0, years=2, prices=_PRICES, rate=0.05)
    assert out["discount_rate"] == 0.05 and out["discount_rate_source"] == "finance case"


def test_pack_prices_reads_the_template_pack():
    from project_templates.eh_templates import TEMPLATE_PACK_VERSION

    p = dc_backup.pack_prices(TEMPLATE_PACK_VERSION)
    assert all(p[k] > 0 for k in dc_backup.PACK_KEYS if not k.endswith("fom_share"))
    assert p["currency"] and p["money_year"] and 0 < p["discount_rate"] < 1


def test_genset_fleet_follows_redundancy():
    assert dc_backup.genset_fleet_mw(26.0, 10.0, "N") == 30.0
    assert dc_backup.genset_fleet_mw(26.0, 10.0, "N+1") == 40.0
    assert dc_backup.genset_fleet_mw(26.0, 10.0, "2N") == 60.0


# ── windowed islanding (DtC) ────────────────────────────────────────────


def _dc(mode="bess", scale=0.6, **backup):
    from models.flex_archetypes import DataCentreLoadSpec
    from project_templates import eh_templates as t

    pre = t.dc_load_preset("eh_datacenter")
    raw = {**pre, "phases": [{**p, "it_mw": p["it_mw"] * scale} for p in pre["phases"]],
           "ai_workload": {}, "backup": {"mode": mode, **backup}}
    return DataCentreLoadSpec.model_validate(raw)


def test_dtc_config_window_needs_both_fields():
    from models.energy_hub import DtcConfig

    with pytest.raises(ValueError, match="outage_start and outage_hours"):
        DtcConfig(critical_load_ids=["a"], islanding_contingencies=["l"], outage_start=3)
    assert DtcConfig(critical_load_ids=["a"], islanding_contingencies=["l"]).outage_hours is None


def test_window_islanding_zeroes_only_the_window_and_undoes():
    from project_templates import eh_templates as t
    from services.adequacy.dtc import apply_islanding_contingency

    n = t.build_eh_datacenter()
    before = n.links_t.p_max_pu.copy()
    undo, mut = apply_islanding_contingency(n, "grid_import", (10, 4))
    col = n.links_t.p_max_pu["grid_import"].to_numpy()
    assert (col[10:14] == 0.0).all() and (np.delete(col, range(10, 14)) == 1.0).all()
    assert float(n.links.at["grid_import", "p_max_pu"]) == 1.0
    assert mut["window"]["hours"] == 4
    undo()
    assert n.links_t.p_max_pu.equals(before)


def test_window_past_the_horizon_is_refused():
    from project_templates import eh_templates as t
    from services.adequacy.dtc import DtcStressError, apply_islanding_contingency

    n = t.build_eh_datacenter()
    with pytest.raises(DtcStressError, match="past"):
        apply_islanding_contingency(n, "grid_import", (len(n.snapshots) - 2, 4))


def test_outage_window_starts_at_the_critical_peak_and_fits():
    assert dc_backup.outage_window(np.array([1.0, 5.0, 2.0, 1.0]), 2.0) == (1, 2)
    assert dc_backup.outage_window(np.array([1.0, 2.0, 2.0, 9.0]), 2.5) == (1, 3)


# ── the template in bess / hybrid mode ──────────────────────────────────


def test_genset_mode_leaves_the_template_bit_for_bit():
    from project_templates import eh_templates as t

    spec = _dc("genset")
    n = t.build_eh_datacenter(load_spec=spec)
    assert not any(s.startswith(dc_backup.STRING_PREFIX) for s in n.storage_units.index)
    assert t.island_config("eh_datacenter", None, load_spec=spec) == t.island_config("eh_datacenter")


@pytest.mark.parametrize("tid", ["eh_datacenter", "eh_datacenter_staged"])
def test_bess_mode_replaces_the_gensets_with_strings_in_network_and_sidecar(tid):
    from project_templates import eh_templates as t

    spec = t.dc_load_spec(tid, {"phases": [{**p, "it_mw": p["it_mw"] * 0.6}
                                           for p in t.dc_load_preset(tid)["phases"]],
                                "backup": {"mode": "bess"}})
    n = t.BUILDERS[tid](load_spec=spec)
    island = t.island_config(tid, None, load_spec=spec)
    strings = [s for s in n.storage_units.index if s.startswith(dc_backup.STRING_PREFIX)]
    assert strings and not any(str(g).startswith("genset") for g in n.generators.index)
    assert set(strings) <= set(island["requirements"]["ride_through_storage"])
    assert all(island["units"][s]["kind"] == "bess" for s in strings)
    assert not any(d.get("kind") == "genset" for d in island["units"].values())
    assert "genset_redundancy_n" not in island["requirements"]
    assert island["requirements"]["sustained_h"]["value"] == 4.0
    # N+1 over 5 MW strings.
    assert len(strings) == math.ceil(
        max(dc_backup.size_bess(
            __import__("services.adequacy.site_peak", fromlist=["named_load"]).named_load(
                n, dc_backup.critical_loads(island)), firm_genset_mw=0.0, backup=spec.backup,
            redundancy="N", years=20)["shortfall_mw"], 1e-9) / 5.0) + 1


def test_bess_mode_above_the_import_is_refused_at_create():
    from project_templates import eh_templates as t
    from services.dc_load_profile import DcLoadError

    # The full-size campus covers its peak above the 40 MW import with
    # gensets; without them that peak cannot be met on grid.
    with pytest.raises(DcLoadError, match="peak"):
        t.dc_load_spec("eh_datacenter", {"backup": {"mode": "bess"}})


# ── the proof (live solves) ─────────────────────────────────────────────


def _assess(spec, **kw):
    from project_templates import eh_templates as t
    from services.solver_service import SolverConfig

    genset = spec.model_copy(update={"backup": spec.backup.model_copy(update={"mode": "genset"})})
    n = t.build_eh_datacenter(load_spec=genset)
    return dc_backup.assess(n, t.island_config("eh_datacenter"), spec, years=20,
                            prices=dc_backup.pack_prices(t.TEMPLATE_PACK_VERSION),
                            cfg=SolverConfig(voll=500.0), **kw), n


@pytest.mark.live_solve
def test_bess_serves_the_critical_load_through_the_window_with_n_strings():
    out, _n = _assess(_dc("bess"))
    assert out["window"]["hours"] == 4
    assert out["proofs"]["genset"]["verdict"] == "pass"
    assert out["proofs"]["bess"]["verdict"] == "pass"
    assert out["proofs"]["bess"]["strings_in_service"] == out["sizing"]["strings_n"]
    assert out["verdict"] == "pass"
    assert out["cost"]["delta"] == pytest.approx(out["cost"]["npv_chosen"] - out["cost"]["npv_genset"])
    assert out["cost"]["run_hours_avoided_per_year"] == 50.0


@pytest.mark.live_solve
def test_undersized_strings_fail_the_window():
    from project_templates import eh_templates as t
    from services.solver_service import SolverConfig

    spec = _dc("bess")
    genset = spec.model_copy(update={"backup": spec.backup.model_copy(update={"mode": "genset"})})
    n = t.build_eh_datacenter(load_spec=genset)
    island = t.island_config("eh_datacenter")
    crit = __import__("services.adequacy.site_peak", fromlist=["named_load"]).named_load(
        n, dc_backup.critical_loads(island))
    sizing = dc_backup.size_bess(crit, firm_genset_mw=0.0, backup=spec.backup,
                                 redundancy="N", years=20)
    small = {**sizing, "strings_n": 2, "strings": 2}
    proof = dc_backup._proof(n, island, small, spec.backup,
                             dc_backup.outage_window(crit, 4.0), cfg=SolverConfig(voll=500.0),
                             lock=__import__("threading").Lock(),
                             stop_event=__import__("threading").Event(),
                             log_queue=__import__("queue").SimpleQueue())
    assert proof["verdict"] == "fail" and proof["window_unserved_mwh"] > 1.0
