"""DC grid readiness 03: battery-based backup in place of diesel."""
from __future__ import annotations

import math
import pathlib

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




def test_sizing_names_the_cycle_count_and_refuses_bad_inputs():
    bk = _bk(test_cycles_per_year=10.0, bess={"augmentation_policy": "none"})
    s = dc_backup.size_bess([5.0], firm_genset_mw=0.0, backup=bk, redundancy="N", years=10)
    row = next(r for r in s["inputs"] if r["key"] == "retention")
    assert "100 assumed test cycles" in row["source"]
    assert any("ride_through_seconds_in_energy_only" in x for x in s["notes"])
    with pytest.raises(dc_backup.DcBackupError, match="analysis_years"):
        dc_backup.size_bess([5.0], firm_genset_mw=0.0, backup=bk, redundancy="N", years=0)
    with pytest.raises(dc_backup.DcBackupError, match="finite"):
        dc_backup.size_bess([5.0, float("nan")], firm_genset_mw=0.0, backup=bk,
                            redundancy="N", years=5)
    with pytest.raises(dc_backup.DcBackupError, match="finite"):
        dc_backup.size_bess([5.0], firm_genset_mw=float("inf"), backup=bk,
                            redundancy="N", years=5)


def test_usable_energy_is_nameplate_times_dod_and_retention():
    bk = _bk(autonomy_h=2.0, ride_through_s=0.0, test_cycles_per_year=0.0,
             bess={"calendar_fade_per_year": 0.02, "augmentation_policy": "none"})
    s = dc_backup.size_bess([5.0], firm_genset_mw=0.0, backup=bk, redundancy="N", years=5)
    assert s["e_usable_mwh_per_string"] == pytest.approx(
        s["e_mwh_per_string"] * 0.9 * s["retention"])
    assert s["e_usable_mwh_per_string"] == pytest.approx(5.0 * 2.0 / 0.9)


# ── cost delta (GK B F1-F8) ─────────────────────────────────────────────

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


def _ann(r, life):
    return r / (1 - (1 + r) ** -life)


def _pv(r, years):
    return (1 - (1 + r) ** -years) / r


def _delta(sizing=None, backup=None, **kw):
    args = {"genset_mw": 0.0, "heat_rate": 2.8, "run_hours_per_year": 0.0, "years": 1,
            "prices": _PRICES, **kw}
    return dc_backup.backup_cost_delta(sizing or _sizing(), backup or _bk(), **args)


def test_fuel_is_hours_times_mw_times_heat_rate_times_price():
    out = _delta(backup=_bk(fuel_price_per_mwh_fuel=40.0), genset_mw=10.0,
                 run_hours_per_year=50.0, years=1)
    fuel = next(x for x in out["lines"] if x["line"] == "fuel")
    assert fuel["npv"] == pytest.approx(50 * 10 * 2.8 * 40.0 / 1.1)
    assert out["fuel_mwh_th_avoided_per_year"] == pytest.approx(50 * 10 * 2.8)
    units = {r["key"]: r["unit"] for r in out["inputs"]}
    assert units["heat_rate"] == "MWh_th/MWh_e" and units["fuel_price"] == "EUR/MWh_th"
    assert all(x["unit"] == "EUR" for x in out["lines"])
    labelled = {r["key"]: r["assumed"] for r in out["inputs"]}
    assert labelled["run_hours_per_year"] and labelled["discount_rate"] and not labelled["fuel_price"]
    assert _delta(genset_mw=1.0)["inputs"][3]["assumed"]  # fuel price left at its default


def test_one_year_delta_by_hand_with_the_remaining_life_credit():
    r = 0.1
    out = _delta(backup=_bk(fuel_price_per_mwh_fuel=40.0), genset_mw=10.0,
                 run_hours_per_year=50.0, years=1)
    g_res = 5000.0 * _ann(r, 25) * _pv(r, 24)
    genset = 5000.0 + (0.02 * 5000.0 + 10 * 50 * 2.8 * 40.0 - g_res) / 1.1
    # Power part (life 2): one year of its two remains; energy part (life 25): 24.
    b_res = 2000.0 * _ann(r, 2) * _pv(r, 1) + 2000.0 * _ann(r, 25) * _pv(r, 24)
    bess = 4000.0 + (0.01 * 2000.0 - b_res) / 1.1
    assert out["npv_genset"] == pytest.approx(genset)
    assert out["npv_chosen"] == pytest.approx(bess)
    assert out["delta"] == pytest.approx(bess - genset)
    assert out["run_hours_avoided_per_year"] == 50.0


def test_a_repurchase_in_the_final_year_is_credited_whole():
    # Power part (life 2) bought in year 0 and again in year 2 = the horizon:
    # the second purchase is fully credited, so it neither penalises nor flatters.
    out = _delta(_sizing(e=0.0), years=2)
    r = 0.1
    cash = [2000.0, 20.0, 20.0 + 2000.0 - 2000.0 * _ann(r, 2) * _pv(r, 2)]
    assert out["npv_chosen"] == pytest.approx(sum(c / 1.1 ** i for i, c in enumerate(cash)))
    assert out["npv_chosen"] == pytest.approx(2000.0 + 20.0 / 1.1 + 20.0 / 1.21)


def test_a_finance_rate_with_inflation_is_made_real():
    out = _delta(rate=0.09, inflation=0.02, years=2)
    assert out["discount_rate"] == pytest.approx(1.09 / 1.02 - 1)
    assert "made real" in out["discount_rate_source"]
    nominal = _delta(rate=0.09, inflation=0.0, years=2)
    assert out["npv_chosen"] != pytest.approx(nominal["npv_chosen"])
    pack = _delta(years=2)
    assert pack["discount_rate"] == 0.1 and "real, assumed" in pack["discount_rate_source"]


def test_augmentation_policies_on_one_fade_curve():
    fade = {"calendar_fade_per_year": 0.02, "cycle_fade_per_full_cycle": 0.001}
    restore = _delta(_sizing(p=0.0, e=100.0), _bk(test_cycles_per_year=10.0, bess=fade), years=3)
    fixed = _delta(_sizing(p=0.0, e=100.0),
                   _bk(test_cycles_per_year=10.0, bess={**fade, "augmentation_policy": "fixed_years",
                                                         "augmentation_years": [3]}), years=3)
    none = _delta(_sizing(p=0.0, e=100.0),
                  _bk(test_cycles_per_year=10.0, bess={**fade, "augmentation_policy": "none"}), years=3)
    # 3 % of 100 MWh a year: restore buys 3 MWh each year, fixed_years 9 MWh in year 3.
    assert restore["augmentation_mwh"] == pytest.approx(9.0)
    assert fixed["augmentation_mwh"] == pytest.approx(9.0)
    aug = lambda o: next(x for x in o["lines"] if x["line"] == "augmentation")["npv"]  # noqa: E731
    assert aug(restore) == pytest.approx(300 / 1.1 + 300 / 1.21 + 300 / 1.331)
    assert aug(fixed) == pytest.approx(900 / 1.331)
    assert none["augmentation_mwh"] == 0.0 and aug(none) == 0.0


def test_fixed_years_sizes_for_the_longest_gap_not_the_horizon():
    """Each augmentation restores the fade before it, so the nameplate covers
    the longest stretch between augmentations: sizing for the whole horizon
    as well would pay the fade twice (review finding on F7)."""
    fade = {"calendar_fade_per_year": 0.02, "cycle_fade_per_full_cycle": 0.001}
    fixed = _bk(test_cycles_per_year=10.0, bess={**fade, "augmentation_policy": "fixed_years",
                                                  "augmentation_years": [4, 7]})
    none = _bk(test_cycles_per_year=10.0, bess={**fade, "augmentation_policy": "none"})
    # Gaps 0-4, 4-7, 7-10: the longest is 4 years.
    assert dc_backup.retention(fixed, 10) == pytest.approx(dc_backup.retention(none, 4))
    assert dc_backup.retention(fixed, 10) > dc_backup.retention(none, 10)


def test_no_augmentation_puts_the_up_sized_energy_in_capex():
    bk = _bk(autonomy_h=2.0, ride_through_s=0.0, test_cycles_per_year=0.0,
             bess={"calendar_fade_per_year": 0.02, "augmentation_policy": "none"})
    s = dc_backup.size_bess([5.0], firm_genset_mw=0.0, backup=bk, redundancy="N", years=5)
    out = _delta({**s}, bk, years=5)
    cap = next(x for x in out["lines"] if x["line"] == "energy_capex_and_repurchase")
    assert cap["npv"] == pytest.approx(s["e_mwh"] * 100.0)
    assert s["e_mwh"] == pytest.approx(5.0 * 2.0 / 0.9 / 0.9 / 0.98 ** 5)


def test_hybrid_parts_sum_to_the_whole_and_genset_delta_is_zero():
    hy = _delta(_sizing("hybrid"), _bk(mode="hybrid"), genset_mw=10.0,
                run_hours_per_year=50.0, years=3)
    assert hy["npv_chosen"] == pytest.approx(hy["npv_genset"] + hy["npv_bess"])
    assert hy["delta"] == pytest.approx(hy["npv_bess"])
    gs = _delta(_sizing("genset", 0.0, 0.0), BackupSpec(), genset_mw=10.0,
                run_hours_per_year=50.0, years=3)
    assert gs["delta"] == 0.0 and gs["npv_bess"] == 0.0


def test_genset_fleet_and_strings_read_the_same_redundancy_rule():
    for tier in ("N", "N+1", "2N"):
        s = dc_backup.size_bess([26.0], firm_genset_mw=0.0, backup=_bk(string_mw=10.0),
                                redundancy=tier, years=5)
        assert dc_backup.genset_fleet_mw(26.0, 10.0, tier) == s["p_mw"]
    assert dc_backup.genset_fleet_mw(26.0, 10.0, "2N") == 60.0


@pytest.mark.parametrize("bad", ["missing_key", "nan_price", "years_0", "nan_input", "life_0"])
def test_cost_delta_fails_closed(bad):
    prices = dict(_PRICES)
    kw = {"years": 3}
    if bad == "missing_key":
        prices.pop("battery.energy.overnight")
    elif bad == "nan_price":
        prices["gas-genset.investment.overnight"] = float("nan")
    elif bad == "years_0":
        kw["years"] = 0
    elif bad == "life_0":
        prices["battery.power.lifetime"] = 0.0
    sizing = _sizing(e=float("nan")) if bad == "nan_input" else _sizing()
    with pytest.raises(dc_backup.DcBackupError):
        _delta(sizing, prices=prices, **kw)


def test_pack_prices_reads_the_template_pack():
    from project_templates.eh_templates import TEMPLATE_PACK_VERSION

    p = dc_backup.pack_prices(TEMPLATE_PACK_VERSION)
    assert all(p[k] > 0 for k in dc_backup.PACK_KEYS if not k.endswith("fom_share"))
    assert p["blank_as_zero"] == ["battery.energy.fom_share"]
    assert p["currency"] and p["money_year"] and 0 < p["discount_rate"] < 1


# ── windowed islanding (GK A 1, 2) ──────────────────────────────────────


def _dc(mode="bess", scale=0.6, **backup):
    from models.flex_archetypes import DataCentreLoadSpec
    from project_templates import eh_templates as t

    pre = t.dc_load_preset("eh_datacenter")
    raw = {**pre, "phases": [{**p, "it_mw": p["it_mw"] * scale} for p in pre["phases"]],
           "ai_workload": {}, "backup": {"mode": mode, **backup}}
    return DataCentreLoadSpec.model_validate(raw)


def test_dtc_config_window_needs_both_fields_and_is_not_on_the_study_request():
    from models.energy_hub import DtcConfig
    from services.adequacy.eh_study_runner import DtcConfigRequest

    with pytest.raises(ValueError, match="outage_start and outage_snapshots"):
        DtcConfig(critical_load_ids=["a"], islanding_contingencies=["l"], outage_start=3)
    assert DtcConfig(critical_load_ids=["a"], islanding_contingencies=["l"]).outage_snapshots is None
    # Assess-only: the EH study's request refuses the window.
    with pytest.raises(ValueError):
        DtcConfigRequest(critical_load_ids=["a"], islanding_contingencies=["l"], outage_start=1,
                         outage_snapshots=2)


@pytest.mark.parametrize("pre_existing", [False, True])
def test_window_islanding_keeps_the_rest_and_undoes_exactly(pre_existing):
    import copy

    from project_templates import eh_templates as t
    from services.adequacy.dtc import apply_islanding_contingency

    n = t.build_eh_datacenter()
    if pre_existing:
        n.links_t.p_max_pu["grid_import"] = np.linspace(0.5, 1.0, len(n.snapshots))
    static = n.links.copy(deep=True)
    ts = {a: copy.deepcopy(n.links_t[a]) for a in ("p_max_pu", "p_min_pu")}
    undo, mut = apply_islanding_contingency(n, "grid_import", (10, 4))
    col = n.links_t.p_max_pu["grid_import"].to_numpy()
    own = ts["p_max_pu"]["grid_import"].to_numpy() if pre_existing else np.ones(len(col))
    assert (col[10:14] == 0.0).all()
    assert np.array_equal(np.delete(col, range(10, 14)), np.delete(own, range(10, 14)))
    assert mut["window"]["hours"] if "hours" in mut["window"] else mut["window"]["snapshots"] == 4
    undo()
    assert n.links.equals(static)
    for a, frame in ts.items():
        assert n.links_t[a].equals(frame)


def test_window_past_the_horizon_is_refused():
    from project_templates import eh_templates as t
    from services.adequacy.dtc import DtcStressError, apply_islanding_contingency

    n = t.build_eh_datacenter()
    with pytest.raises(DtcStressError, match="past"):
        apply_islanding_contingency(n, "grid_import", (len(n.snapshots) - 2, 4))


def test_window_takes_the_largest_energy_span_not_the_peak_hour():
    crit = np.array([0.0, 10.0, 0.0, 0.0, 6.0, 6.0, 0.0])
    w = dc_backup.outage_window(crit, np.ones(7), 2.0)
    assert (w["start"], w["snapshots"], w["energy_mwh"]) == (4, 2, 12.0)


def test_window_counts_hours_by_weighting_and_stays_in_its_period():
    w = dc_backup.outage_window(np.array([1.0, 1.0, 9.0, 9.0]), np.array([2.0, 2.0, 2.0, 2.0]), 3.0)
    assert (w["start"], w["snapshots"], w["hours"]) == (2, 2, 4.0)
    # The best span would straddle two periods: it is not a candidate.
    w = dc_backup.outage_window(np.array([1.0, 5.0, 5.0, 1.0]), np.ones(4), 2.0,
                                np.array([1, 1, 2, 2]))
    assert w["start"] in (0, 2) and w["energy_mwh"] == 6.0


@pytest.mark.parametrize("tid", ["eh_datacenter", "eh_datacenter_staged"])
def test_template_window_counts_stores_hours_not_annualised_ones(tid):
    from project_templates import eh_templates as t
    from services.adequacy.site_peak import named_load

    spec, n, island = _built(tid, "genset", 1.0)
    w = n.snapshot_weightings
    # The template annualises with "generators"; a window counted with it
    # would be one snapshot long.
    assert (w["generators"] != w["stores"]).any()
    step = float(w["stores"].iloc[0])
    critical = named_load(n, dc_backup.critical_loads(island))
    window = dc_backup.network_window(n, critical, spec.backup.autonomy_h)
    assert window["snapshots"] == math.ceil(spec.backup.autonomy_h / step)
    assert window["hours"] == pytest.approx(window["snapshots"] * step)


def test_window_longer_than_the_horizon_is_refused():
    with pytest.raises(dc_backup.DcBackupError, match="longer than the modelled horizon"):
        dc_backup.outage_window(np.ones(3), np.ones(3), 4.0)


# ── the template in bess / hybrid mode (GK A 4, 6) ──────────────────────


def test_genset_mode_leaves_the_template_bit_for_bit():
    from project_templates import eh_templates as t

    spec = _dc("genset")
    n = t.build_eh_datacenter(load_spec=spec)
    assert not any(s.startswith(dc_backup.STRING_PREFIX) for s in n.storage_units.index)
    assert t.island_config("eh_datacenter", None, load_spec=spec) == t.island_config("eh_datacenter")


def _built(tid, mode, scale):
    from models.flex_archetypes import DataCentreLoadSpec
    from project_templates import eh_templates as t

    pre = t.dc_load_preset(tid)
    spec = DataCentreLoadSpec.model_validate(
        {**pre, "phases": [{**p, "it_mw": p["it_mw"] * scale} for p in pre["phases"]],
         "ai_workload": {}, "backup": {"mode": mode}})
    return spec, t.BUILDERS[tid](load_spec=spec), t.island_config(tid, None, load_spec=spec)


@pytest.mark.parametrize("tid", ["eh_datacenter", "eh_datacenter_staged"])
@pytest.mark.parametrize("mode,scale", [("bess", 0.6), ("hybrid", 1.0)])
def test_backup_mode_network_and_sidecar_agree_and_validate(tid, mode, scale):
    from services.adequacy.island_config import validate
    from services.adequacy.site_peak import outage_reserve_mwh, ride_through_batteries

    spec, n, island = _built(tid, mode, scale)
    strings = [s for s in n.storage_units.index if s.startswith(dc_backup.STRING_PREFIX)]
    assert strings
    validate(island)          # the gridspine island contract
    assert set(strings) <= set(island["requirements"]["ride_through_storage"])
    assert all(island["units"][s]["kind"] == "bess" for s in strings)
    has_gensets = any(str(g).startswith("genset") for g in n.generators.index)
    assert has_gensets == (mode == "hybrid")
    if mode == "bess":
        assert not any(d.get("kind") == "genset" for d in island["units"].values())
        assert "genset_redundancy_n" not in island["requirements"]
    assert island["requirements"]["sustained_h"]["value"] == 4.0
    # Held charge: discharge is off at every snapshot in a normal solve.
    assert (n.storage_units_t.p_max_pu[strings].to_numpy() == 0.0).all()
    # site_peak reads the raw sidecar ({value, source} per requirement), as
    # the screen does; gridspine's validated form flattens them to "values".
    rt = ride_through_batteries(n, island)
    assert set(strings) <= set(rt["names"])
    reserve, words = outage_reserve_mwh(n, island)
    assert reserve > 0 and "outage reserve" in words


@pytest.mark.parametrize("tid", ["eh_datacenter", "eh_datacenter_staged"])
def test_site_peak_shaves_the_same_with_or_without_the_backup_strings(tid):
    """GK A ruling 3: the strings hold their charge for an outage; site_peak's
    shaving never counts them."""
    import copy

    from services.adequacy.site_peak import import_links, peak_check, without_backup

    _spec, n, island = _built(tid, "bess", 0.6)
    # An import just under the peak, so the peak binds for a few hours and a
    # battery free above its reserve would shave it.
    top = max(peak_check(n, island), key=lambda r: r["peak_mw"])
    links = import_links(n)
    n.links.loc[links, "p_nom"] *= 0.97 * top["peak_mw"] / top["delivered_mw"]
    strings = [s for s in n.storage_units.index if s.startswith(dc_backup.STRING_PREFIX)]
    bare, bare_island = n.copy(), copy.deepcopy(island)
    bare.remove("StorageUnit", strings)
    for s in strings:
        bare_island["units"].pop(s)
    bare_island["requirements"]["ride_through_storage"] = [
        u for u in bare_island["requirements"]["ride_through_storage"] if u not in strings]

    def shaving(rows):
        return [(r["period"], r["binding_snapshot"], r["firm"][1]["mw"], r["cap_mw"])
                for r in rows]

    with_strings, without = peak_check(n, island), peak_check(bare, bare_island)
    assert any(r["headroom_mw"] < 0 for r in with_strings)        # the check binds
    assert shaving(with_strings) == shaving(without)
    assert not set(strings) & set(without_backup(island)["requirements"]["ride_through_storage"])
    assert set(strings) <= set(island["requirements"]["ride_through_storage"])   # untouched


def test_bess_mode_above_the_import_is_refused_at_create():
    from project_templates import eh_templates as t
    from services.dc_load_profile import DcLoadError

    # The full-size campus covers its peak above the 40 MW import with
    # gensets; without them that peak cannot be met on grid.
    with pytest.raises(DcLoadError, match="peak"):
        t.dc_load_spec("eh_datacenter", {"backup": {"mode": "bess"}})


# ── the proof (live solves; GK A 3, 4, 8) ───────────────────────────────


def _run():
    import queue
    import threading

    from services.solver_service import SolverConfig

    return {"cfg": SolverConfig(voll=500.0), "lock": threading.Lock(),
            "stop_event": threading.Event(), "log_queue": queue.SimpleQueue()}


@pytest.mark.live_solve
def test_normal_solve_never_discharges_the_backup():
    _spec, n, _island = _built("eh_datacenter", "bess", 0.6)
    n.optimize(solver_name="highs")
    strings = [s for s in n.storage_units.index if s.startswith(dc_backup.STRING_PREFIX)]
    assert float(n.storage_units_t.p_dispatch[strings].to_numpy().sum()) == pytest.approx(0.0, abs=1e-6)


def _flat_site():
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=24, freq="h"))
    n.add("Carrier", "AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "site", carrier="AC")
    n.add("Generator", "supply", bus="grid", p_nom=100.0, marginal_cost=50.0)
    n.add("Link", "imp", bus0="grid", bus1="site", p_nom=30.0, efficiency=1.0)
    n.links["eh_role"] = "grid_import"
    n.add("Generator", "g1", bus="site", p_nom=10.0, marginal_cost=200.0)
    n.add("Load", "crit", bus="site", p_set=10.0)
    island = {"units": {"g1": {"kind": "genset"}, "crit": {"kind": "load", "critical": True}},
              "requirements": {}}
    return n, island


@pytest.mark.live_solve
@pytest.mark.parametrize("scale,verdict", [(1.0, "pass"), (0.95, "fail")])
def test_proof_serves_at_exactly_the_sized_energy_and_fails_below(scale, verdict):
    n, island = _flat_site()
    bk = _bk(autonomy_h=4.0, ride_through_s=0.0, bess={"depth_of_discharge": 0.8})
    sizing = dc_backup.size_bess(np.full(24, 10.0), firm_genset_mw=0.0, backup=bk,
                                 redundancy="N+1", years=10)
    assert sizing["strings_n"] == 2 and sizing["strings"] == 3
    window = dc_backup.outage_window(np.full(24, 10.0), np.ones(24), 4.0)
    proof = dc_backup._proof(n, island, sizing, bk, window, energy_scale=scale, **_run())
    assert proof["verdict"] == verdict
    assert proof["strings_in_service"] == 2
    if verdict == "fail":
        assert proof["window_unserved_mwh"] == pytest.approx(0.05 * 40.0, rel=1e-3)


@pytest.mark.live_solve
def test_an_unreadable_unmet_is_not_shown(monkeypatch):
    from services.adequacy import dtc

    n, island = _flat_site()
    bk = _bk(autonomy_h=2.0, ride_through_s=0.0)
    sizing = dc_backup.size_bess(np.full(24, 10.0), firm_genset_mw=0.0, backup=bk,
                                 redundancy="N", years=5)
    window = dc_backup.outage_window(np.full(24, 10.0), np.ones(24), 2.0)
    monkeypatch.setattr(dtc, "_window_unserved", lambda *a, **k: {
        "critical_unserved_window_mwh": None, "critical_unserved_outside_window_mwh": None})
    proof = dc_backup._proof(n, island, sizing, bk, window, **_run())
    assert proof["verdict"] == "not_shown"


@pytest.mark.live_solve
def test_assess_proves_both_legs_and_prices_the_mode():
    from project_templates import eh_templates as t

    spec = _dc("bess")
    genset = spec.model_copy(update={"backup": spec.backup.model_copy(update={"mode": "genset"})})
    n = t.build_eh_datacenter(load_spec=genset)
    out = dc_backup.assess(n, t.island_config("eh_datacenter"), spec, years=20,
                           prices=dc_backup.pack_prices(t.TEMPLATE_PACK_VERSION), **_run())
    assert out["window"]["hours"] == 4.0 and out["window"]["energy_mwh"] > 0
    assert out["proofs"]["genset"]["verdict"] == "pass"
    assert out["proofs"]["bess"]["verdict"] == "pass" and out["verdict"] == "pass"
    assert out["proofs"]["bess"]["strings_in_service"] == out["sizing"]["strings_n"]
    c = out["cost"]
    assert c["delta"] == pytest.approx(c["npv_chosen"] - c["npv_genset"])
    assert c["genset_mw"] == dc_backup.genset_fleet_mw(out["sizing"]["critical_peak_mw"], 10.0, "N+1")


# ── the routes (GK A ruling 1 / GK B F8) ────────────────────────────────


def _stage_dc(tmp_path, monkeypatch, tid):
    from project_templates import eh_templates as T
    from routers import projects as projects_router

    src = tmp_path / tid
    src.mkdir()
    T.BUILDERS[tid]().export_to_netcdf(str(src / "network.nc"))
    T.write_sidecars(src, tid)
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    (tmp_path / "eh_templates.py").write_text(
        (pathlib.Path(T.__file__)).read_text())


@pytest.mark.parametrize("change", [
    {"mode": "hybrid"},                       # valid: stored
    {"mode": "bess"},                         # refused by the peak check
    {"mode": "diesel"},                       # refused by the schema
    {"mode": "bess", "autonomy_h": -1.0},     # refused by a validator
])
def test_put_backup_leaves_the_network_fingerprint_and_case_digest(
        client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx, change):
    from routers.results import _ic_current_assumptions
    from services import project_registry
    from services.workflow_tools import _network_input_fingerprint

    tid = "eh_datacenter"
    _stage_dc(tmp_path, monkeypatch, tid)
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": "dc_bk"},
                    json={"dc_load": {}})
    assert r.status_code == 200, r.text
    name = r.json().get("name", "dc_bk")
    d = pathlib.Path(project_registry.project_dir(project_row(name)))
    ctx = session_ctx(client)
    nc, fp, (ic, why) = (d / "network.nc").read_bytes(), _network_input_fingerprint(ctx), \
        _ic_current_assumptions()
    assert ic is not None, why

    r = client.put(f"/api/campus-electrical/{name}/dc-backup", json=change)
    if change == {"mode": "hybrid"}:
        assert r.status_code == 200, r.text
        assert r.json()["mode"] == "hybrid"
        stored = client.get(f"/api/projects/{name}/eh_template").json()
        assert stored["dc_load"]["spec"]["backup"]["mode"] == "hybrid"
    else:
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["error_kind"] == "dc_backup_invalid"
    assert (d / "network.nc").read_bytes() == nc
    assert _network_input_fingerprint(ctx) == fp
    assert _ic_current_assumptions()[0] == ic


def test_backup_routes_refuse_a_project_without_dc_load(
        client, tmp_path, monkeypatch, tmp_projects_dir):
    _stage_dc(tmp_path, monkeypatch, "eh_datacenter")
    r = client.post("/api/projects/from_template/eh_datacenter", params={"name": "dc_plain"})
    name = r.json().get("name", "dc_plain")
    for resp in (client.put(f"/api/campus-electrical/{name}/dc-backup", json={"mode": "hybrid"}),
                 client.post(f"/api/campus-electrical/{name}/dc-backup/assess", json={})):
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error_kind"] == "dc_backup_invalid"
    assert client.post(f"/api/campus-electrical/{name}/dc-backup/assess",
                       json={"mode": "diesel"}).status_code == 422


@pytest.mark.live_solve
def test_assess_route_returns_json_and_writes_nothing(
        client, tmp_path, monkeypatch, tmp_projects_dir, project_row):
    from services import project_registry

    _stage_dc(tmp_path, monkeypatch, "eh_datacenter")
    r = client.post("/api/projects/from_template/eh_datacenter", params={"name": "dc_as"},
                    json={"dc_load": {}})
    name = r.json().get("name", "dc_as")
    d = pathlib.Path(project_registry.project_dir(project_row(name)))
    before = {f: (d / f).read_bytes() for f in ("network.nc", "eh_template.json")}
    r = client.post(f"/api/campus-electrical/{name}/dc-backup/assess", json={"mode": "hybrid"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["mode"] == "hybrid" and out["verdict"] in ("pass", "fail", "not_shown")
    assert {"genset", "hybrid"} <= set(out["proofs"]) and "delta" in out["cost"]
    # GK A 7: which saved network was read, and what the re-dispatch does not reproduce.
    import hashlib
    basis = out["basis"]
    assert basis["network_sha256"] == hashlib.sha256(before["network.nc"]).hexdigest()[:12]
    assert basis["network_saved_at"] and basis["not_reproduced"] == list(dc_backup.NOT_REPRODUCED)
    assert {f: (d / f).read_bytes() for f in before} == before
