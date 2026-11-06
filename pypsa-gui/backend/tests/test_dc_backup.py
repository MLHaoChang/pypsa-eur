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
    base = {
        "power_mw": 1.0,
        "energy_mwh": 1.0,
        "round_trip_efficiency": 0.81,
        "depth_of_discharge": 0.9,
        "calendar_fade_per_year": 0.0,
        "cycle_fade_per_full_cycle": 0.0,
        "augmentation_policy": "restore_to_nameplate",
    }
    return BackupSpec(
        mode=kw.pop("mode", "bess"), bess=BessSpec(**{**base, **bess}), **kw
    )


# ── sizing ──────────────────────────────────────────────────────────────


def test_genset_mode_sizes_no_battery():
    s = dc_backup.size_bess(
        [12.0], firm_genset_mw=30.0, backup=BackupSpec(), redundancy="N", years=10
    )
    assert s["strings"] == 0 and s["p_mw"] == 0.0 and s["e_mwh"] == 0.0


@pytest.mark.parametrize("tier,strings", [("N", 3), ("N+1", 4), ("2N", 6)])
def test_redundancy_counts_strings(tier, strings):
    # 12 MW critical peak, 5 MW strings: N = ceil(12/5) = 3.
    s = dc_backup.size_bess(
        np.array([8.0, 12.0, 10.0]),
        firm_genset_mw=0.0,
        backup=_bk(autonomy_h=4.0, ride_through_s=0.0),
        redundancy=tier,
        years=10,
    )
    assert s["shortfall_mw"] == pytest.approx(12.0)
    assert s["strings_n"] == 3
    assert s["strings"] == strings
    assert s["p_mw"] == pytest.approx(5.0 * strings)


def test_energy_covers_autonomy_and_ride_through_after_dod_and_losses():
    # η_dispatch = sqrt(0.81) = 0.9; DoD 0.9; 4 h + 600 s.
    s = dc_backup.size_bess(
        [5.0],
        firm_genset_mw=0.0,
        backup=_bk(autonomy_h=4.0, ride_through_s=600.0),
        redundancy="N",
        years=10,
    )
    per_string = 5.0 * (4.0 + 600.0 / 3600.0) / (0.9 * 0.9)
    assert s["e_mwh_per_string"] == pytest.approx(per_string)
    assert s["e_mwh"] == pytest.approx(per_string)
    assert s["retention"] == 1.0
    # Deliverable energy of the N strings covers the critical peak for the duration.
    assert s["e_mwh"] * 0.9 * 0.9 >= 5.0 * (4.0 + 1 / 6) - 1e-9


def test_no_augmentation_oversizes_for_end_of_life_fade():
    bk = _bk(
        autonomy_h=2.0,
        ride_through_s=0.0,
        test_cycles_per_year=10.0,
        bess={
            "calendar_fade_per_year": 0.02,
            "cycle_fade_per_full_cycle": 0.001,
            "augmentation_policy": "none",
        },
    )
    s = dc_backup.size_bess(
        [5.0], firm_genset_mw=0.0, backup=bk, redundancy="N", years=10
    )
    retention = 0.98**10 * (1 - 0.001 * 10 * 10)
    assert s["retention"] == pytest.approx(retention)
    assert s["e_mwh_per_string"] == pytest.approx(5.0 * 2.0 / (0.9 * 0.9 * retention))


def test_fade_beyond_nameplate_is_refused():
    bk = _bk(
        test_cycles_per_year=100.0,
        bess={"cycle_fade_per_full_cycle": 0.01, "augmentation_policy": "none"},
    )
    with pytest.raises(dc_backup.DcBackupError, match="fade"):
        dc_backup.size_bess(
            [5.0], firm_genset_mw=0.0, backup=bk, redundancy="N", years=10
        )


def test_hybrid_sizes_on_the_shortfall_the_firm_gensets_leave():
    s = dc_backup.size_bess(
        [18.0, 26.0],
        firm_genset_mw=20.0,
        backup=_bk(mode="hybrid", ride_through_s=0.0),
        redundancy="N",
        years=5,
    )
    assert s["shortfall_mw"] == pytest.approx(6.0)
    assert s["strings_n"] == 2
    covered = dc_backup.size_bess(
        [18.0],
        firm_genset_mw=20.0,
        backup=_bk(mode="hybrid"),
        redundancy="N+1",
        years=5,
    )
    assert covered["strings"] == 0


def test_bess_mode_ignores_gensets():
    s = dc_backup.size_bess(
        [12.0], firm_genset_mw=40.0, backup=_bk(), redundancy="N", years=5
    )
    assert s["shortfall_mw"] == pytest.approx(12.0)


def test_sizing_rows_are_labelled_assumed_with_sources():
    s = dc_backup.size_bess(
        [12.0], firm_genset_mw=0.0, backup=_bk(), redundancy="N+1", years=5
    )
    keys = {r["key"] for r in s["inputs"]}
    assert {
        "autonomy_h",
        "ride_through_s",
        "string_mw",
        "round_trip_efficiency",
        "depth_of_discharge",
        "redundancy",
    } <= keys
    assert all("assumed" in r and r["source"] for r in s["inputs"])
    assert math.isfinite(s["e_mwh"])


def test_sizing_names_the_cycle_count_and_refuses_bad_inputs():
    bk = _bk(test_cycles_per_year=10.0, bess={"augmentation_policy": "none"})
    s = dc_backup.size_bess(
        [5.0], firm_genset_mw=0.0, backup=bk, redundancy="N", years=10
    )
    row = next(r for r in s["inputs"] if r["key"] == "retention")
    assert "100 assumed test cycles" in row["source"]
    assert any("ride_through_seconds_in_energy_only" in x for x in s["notes"])
    with pytest.raises(dc_backup.DcBackupError, match="analysis_years"):
        dc_backup.size_bess(
            [5.0], firm_genset_mw=0.0, backup=bk, redundancy="N", years=0
        )
    with pytest.raises(dc_backup.DcBackupError, match="finite"):
        dc_backup.size_bess(
            [5.0, float("nan")], firm_genset_mw=0.0, backup=bk, redundancy="N", years=5
        )
    with pytest.raises(dc_backup.DcBackupError, match="finite"):
        dc_backup.size_bess(
            [5.0], firm_genset_mw=float("inf"), backup=bk, redundancy="N", years=5
        )


def test_usable_energy_is_nameplate_times_dod_and_retention():
    bk = _bk(
        autonomy_h=2.0,
        ride_through_s=0.0,
        test_cycles_per_year=0.0,
        bess={"calendar_fade_per_year": 0.02, "augmentation_policy": "none"},
    )
    s = dc_backup.size_bess(
        [5.0], firm_genset_mw=0.0, backup=bk, redundancy="N", years=5
    )
    assert s["e_usable_mwh_per_string"] == pytest.approx(
        s["e_mwh_per_string"] * 0.9 * s["retention"]
    )
    assert s["e_usable_mwh_per_string"] == pytest.approx(5.0 * 2.0 / 0.9)


# ── cost delta (GK B F1-F8) ─────────────────────────────────────────────

_PRICES = {
    "gas-genset.investment.overnight": 500.0,
    "gas-genset.investment.fom_share": 0.02,
    "gas-genset.investment.lifetime": 25.0,
    "battery.power.overnight": 200.0,
    "battery.power.fom_share": 0.01,
    "battery.power.lifetime": 2.0,
    "battery.energy.overnight": 100.0,
    "battery.energy.fom_share": 0.0,
    "battery.energy.lifetime": 25.0,
    "discount_rate": 0.1,
    "currency": "EUR",
    "money_year": 2025,
    "source": "test",
}


def _sizing(mode="bess", p=10.0, e=20.0):
    return {"mode": mode, "p_mw": p, "e_mwh": e}


def _ann(r, life):
    return r / (1 - (1 + r) ** -life)


def _pv(r, years):
    return (1 - (1 + r) ** -years) / r


def _delta(sizing=None, backup=None, **kw):
    args = {
        "genset_mw": 0.0,
        "heat_rate": 2.8,
        "run_hours_per_year": 0.0,
        "years": 1,
        "prices": _PRICES,
        **kw,
    }
    return dc_backup.backup_cost_delta(sizing or _sizing(), backup or _bk(), **args)


def test_fuel_is_hours_times_mw_times_heat_rate_times_price():
    out = _delta(
        backup=_bk(fuel_price_per_mwh_fuel=40.0),
        genset_mw=10.0,
        run_hours_per_year=50.0,
        years=1,
    )
    fuel = next(x for x in out["lines"] if x["line"] == "fuel")
    assert fuel["npv"] == pytest.approx(50 * 10 * 2.8 * 40.0 / 1.1)
    assert out["fuel_mwh_th_avoided_per_year"] == pytest.approx(50 * 10 * 2.8)
    units = {r["key"]: r["unit"] for r in out["inputs"]}
    assert units["heat_rate"] == "MWh_th/MWh_e" and units["fuel_price"] == "EUR/MWh_th"
    assert all(x["unit"] == "EUR" for x in out["lines"])
    labelled = {r["key"]: r["assumed"] for r in out["inputs"]}
    assert (
        labelled["run_hours_per_year"]
        and labelled["discount_rate"]
        and not labelled["fuel_price"]
    )
    assert _delta(genset_mw=1.0)["inputs"][3][
        "assumed"
    ]  # fuel price left at its default


def test_one_year_delta_by_hand_with_the_remaining_life_credit():
    r = 0.1
    out = _delta(
        backup=_bk(fuel_price_per_mwh_fuel=40.0),
        genset_mw=10.0,
        run_hours_per_year=50.0,
        years=1,
    )
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
    assert out["npv_chosen"] == pytest.approx(
        sum(c / 1.1**i for i, c in enumerate(cash))
    )
    assert out["npv_chosen"] == pytest.approx(2000.0 + 20.0 / 1.1 + 20.0 / 1.21)


def test_a_finance_rate_with_inflation_is_made_real():
    out = _delta(rate=0.09, inflation=0.02, years=2)
    assert out["discount_rate"] == pytest.approx(1.09 / 1.02 - 1)
    assert "made real" in out["discount_rate_source"]
    nominal = _delta(rate=0.09, inflation=0.0, years=2)
    assert out["npv_chosen"] != pytest.approx(nominal["npv_chosen"])
    pack = _delta(years=2)
    assert (
        pack["discount_rate"] == 0.1 and "real, assumed" in pack["discount_rate_source"]
    )


def test_augmentation_policies_on_one_fade_curve():
    fade = {"calendar_fade_per_year": 0.02, "cycle_fade_per_full_cycle": 0.001}
    restore = _delta(
        _sizing(p=0.0, e=100.0), _bk(test_cycles_per_year=10.0, bess=fade), years=3
    )
    fixed = _delta(
        _sizing(p=0.0, e=100.0),
        _bk(
            test_cycles_per_year=10.0,
            bess={
                **fade,
                "augmentation_policy": "fixed_years",
                "augmentation_years": [3],
            },
        ),
        years=3,
    )
    none = _delta(
        _sizing(p=0.0, e=100.0),
        _bk(test_cycles_per_year=10.0, bess={**fade, "augmentation_policy": "none"}),
        years=3,
    )
    # One compound curve (GK B F7): restore buys 1 − 0.98 × 0.99 = 2.98 % a
    # year; fixed_years buys 1 − 0.98³ × (1 − 0.03) of 100 MWh in year 3.
    yearly, gap3 = 100 * (1 - 0.98 * 0.99), 100 * (1 - 0.98**3 * 0.97)
    assert restore["augmentation_mwh"] == pytest.approx(3 * yearly)
    assert fixed["augmentation_mwh"] == pytest.approx(gap3)
    aug = lambda o: next(x for x in o["lines"] if x["line"] == "augmentation")["npv"]  # noqa: E731
    price = 100.0 * yearly
    assert aug(restore) == pytest.approx(price / 1.1 + price / 1.21 + price / 1.331)
    assert aug(fixed) == pytest.approx(100.0 * gap3 / 1.331)
    assert none["augmentation_mwh"] == 0.0 and aug(none) == 0.0


def test_fixed_years_sizes_for_the_longest_gap_not_the_horizon():
    """Each augmentation restores the fade before it, so the nameplate covers
    the longest stretch between augmentations: sizing for the whole horizon
    as well would pay the fade twice (review finding on F7)."""
    fade = {"calendar_fade_per_year": 0.02, "cycle_fade_per_full_cycle": 0.001}
    fixed = _bk(
        test_cycles_per_year=10.0,
        bess={
            **fade,
            "augmentation_policy": "fixed_years",
            "augmentation_years": [4, 7],
        },
    )
    none = _bk(test_cycles_per_year=10.0, bess={**fade, "augmentation_policy": "none"})
    # Gaps 0-4, 4-7, 7-10: the longest is 4 years.
    assert dc_backup.retention(fixed, 10) == pytest.approx(dc_backup.retention(none, 4))
    assert dc_backup.retention(fixed, 10) > dc_backup.retention(none, 10)


def test_no_augmentation_puts_the_up_sized_energy_in_capex():
    bk = _bk(
        autonomy_h=2.0,
        ride_through_s=0.0,
        test_cycles_per_year=0.0,
        bess={"calendar_fade_per_year": 0.02, "augmentation_policy": "none"},
    )
    s = dc_backup.size_bess(
        [5.0], firm_genset_mw=0.0, backup=bk, redundancy="N", years=5
    )
    out = _delta({**s}, bk, years=5)
    cap = next(x for x in out["lines"] if x["line"] == "energy_capex_and_repurchase")
    assert cap["npv"] == pytest.approx(s["e_mwh"] * 100.0)
    assert s["e_mwh"] == pytest.approx(5.0 * 2.0 / 0.9 / 0.9 / 0.98**5)


def test_hybrid_parts_sum_to_the_whole_and_genset_delta_is_zero():
    hy = _delta(
        _sizing("hybrid"),
        _bk(mode="hybrid"),
        genset_mw=10.0,
        run_hours_per_year=50.0,
        years=3,
    )
    assert hy["npv_chosen"] == pytest.approx(hy["npv_genset"] + hy["npv_bess"])
    assert hy["delta"] == pytest.approx(hy["npv_bess"])
    gs = _delta(
        _sizing("genset", 0.0, 0.0),
        BackupSpec(),
        genset_mw=10.0,
        run_hours_per_year=50.0,
        years=3,
    )
    assert gs["delta"] == 0.0 and gs["npv_bess"] == 0.0


def test_hybrid_prices_only_the_fleet_it_keeps_by_hand():
    """GK B K1: hybrid's genset leg is the kept fleet (40 MW), the comparison
    the all-genset fleet (50 MW); the fleet legs are linear in MW."""
    hy = _delta(
        _sizing("hybrid"),
        _bk(mode="hybrid"),
        genset_mw=50.0,
        kept_genset_mw=40.0,
        run_hours_per_year=50.0,
        years=3,
    )
    per_mw = hy["npv_genset"] / 50.0
    assert hy["npv_chosen"] == pytest.approx(40.0 * per_mw + hy["npv_bess"])
    assert hy["delta"] == pytest.approx(hy["npv_bess"] - 10.0 * per_mw)
    assert hy["hybrid_genset_mw"] == 40.0 and hy["genset_mw"] == 50.0
    kept = {r["key"]: r["value"] for r in hy["inputs"]}
    assert kept["hybrid_genset_mw"] == 40.0 and kept["genset_mw"] == 50.0
    assert {r["leg"] for r in hy["lines"]} == {"genset", "hybrid_genset", "bess"}


def test_genset_fleets_price_and_prove_one_fleet_or_say_unsized():
    """GK A 2: unsolved, the all-genset fleet is sized on the critical peak
    and its excess over the committed fleet goes on the candidate; hybrid
    keeps the committed fleet; no candidate is ``not_shown``, never fail."""
    from project_templates import eh_templates as t

    n = t.build_eh_datacenter()
    island = t.island_config("eh_datacenter")
    f = dc_backup.genset_fleets(n, island, 51.1, 10.0, "N+1")
    assert f["committed_mw"] == 40.0 and f["hybrid"]["mw"] == 40.0
    assert f["all_genset"]["mw"] == 70.0 and f["unsized"] is None
    assert f["all_genset"]["candidate_mw"] == {"genset_new": 30.0}
    assert set(f["hybrid"]["candidate_mw"].values()) == {0.0}
    n.remove("Generator", "genset_new")
    bare = dc_backup.genset_fleets(n, island, 51.1, 10.0, "N+1")
    assert bare["unsized"] == "the genset candidate is unsized"
    sizing = dc_backup.size_bess(
        [51.1],
        firm_genset_mw=30.0,
        backup=_bk(mode="genset"),
        redundancy="N+1",
        years=5,
    )
    win = {"start": 0, "snapshots": 4, "hours": 4.0}
    out = dc_backup._proof(
        n,
        island,
        sizing,
        _bk(mode="genset"),
        win,
        cfg=None,
        lock=None,
        stop_event=None,
        log_queue=None,
        unsized=bare["unsized"],
    )
    assert out["verdict"] == "not_shown" and out["reason"] == bare["unsized"]


def test_hybrid_window_is_on_the_shortfall_not_the_gross_load():
    """GK A 4: the largest critical-energy span and the largest (P_c − g)⁺
    span differ here; hybrid's strings face the second."""
    crit = np.array([10.0, 10.0, 10.0, 0.0, 0.0, 25.0, 0.0, 0.0])
    hours = np.ones(crit.size)
    gross = dc_backup.outage_window(crit, hours, 3.0)
    short = dc_backup.outage_window(np.clip(crit - 20.0, 0.0, None), hours, 3.0)
    assert gross["start"] == 0 and gross["energy_mwh"] == 30.0
    assert short["start"] == 3 and short["energy_mwh"] == 5.0


def test_a_given_bess_without_its_fade_is_refused_and_set_values_cite_the_spec():
    """GK B K5: BessSpec's 0 default never reads as no fade; a set policy's
    source is the spec, not the assumed default's text."""
    with pytest.raises(ValueError, match="calendar_fade_per_year"):
        BackupSpec(
            mode="bess",
            bess={
                "power_mw": 1.0,
                "energy_mwh": 1.0,
                "round_trip_efficiency": 0.88,
                "augmentation_policy": "none",
            },
        )
    BackupSpec(mode="bess")  # assumed defaults
    bk = BackupSpec(
        mode="bess",
        bess={
            "power_mw": 1.0,
            "energy_mwh": 1.0,
            "round_trip_efficiency": 0.88,
            "calendar_fade_per_year": 0.02,
            "cycle_fade_per_full_cycle": 0.0,
            "augmentation_policy": "none",
        },
    )
    rows = {r["key"]: r for r in dc_backup._inputs(bk, "N+1", 5)}
    assert rows["augmentation_policy"]["value"] == "none"
    assert rows["augmentation_policy"]["source"] == dc_backup.SET_SOURCE
    assumed = {
        r["key"]: r for r in dc_backup._inputs(BackupSpec(mode="bess"), "N+1", 5)
    }
    assert "restore_to_nameplate" in assumed["augmentation_policy"]["source"]


def test_augmentation_is_the_sizing_curve_over_each_gap():
    """GK B F7: one compound curve. restore_to_nameplate buys 1 − retention
    over one year each year; fixed_years with one gap equal to the horizon
    buys 1 − retention over the horizon, the curve sizing reads."""
    fade = {"calendar_fade_per_year": 0.03, "cycle_fade_per_full_cycle": 0.002}

    def bk(**b):
        return _bk(test_cycles_per_year=12.0, bess={**fade, **b})

    none = bk(augmentation_policy="none")
    restore = dc_backup._augmentation(bk(), 100.0, 6)
    assert restore[1:] == pytest.approx(
        [100.0 * (1 - dc_backup.retention(none, 1))] * 6
    )
    fixed = dc_backup._augmentation(
        bk(augmentation_policy="fixed_years", augmentation_years=[6]), 100.0, 6
    )
    assert fixed[6] == pytest.approx(100.0 * (1 - dc_backup.retention(none, 6)))
    assert fixed[:6].sum() == 0.0


def test_riders_repurchase_rounding_restore_linear_and_scrapped_augmentation():
    # Half-years round up, never banker's (2.5 → 3, not 2).
    assert dc_backup._repurchase_years(2.5, 5) == [3, 5]
    # restore_to_nameplate: no up-sizing, and each year buys one year's fade
    # of nameplate (the compound and linear fades agree over one year).
    bk = _bk(
        test_cycles_per_year=10.0,
        bess={"calendar_fade_per_year": 0.02, "cycle_fade_per_full_cycle": 0.001},
    )
    assert dc_backup.retention(bk, 7) == 1.0
    aug = dc_backup._augmentation(bk, 100.0, 7)
    one_year = 1.0 - 0.98 * (1.0 - 0.001 * 10)  # compound, one curve
    assert aug[0] == 0.0 and aug[1:] == pytest.approx([100.0 * one_year] * 7)
    # An energy repurchase scraps the augmentation cells bought before it:
    # they get no remaining-life credit; those bought after it do.
    part = dc_backup._part(100.0, 0.0, 4.0, 6, 0.05, extra=[(2, 10.0), (5, 10.0)])
    alone = dc_backup._part(100.0, 0.0, 4.0, 6, 0.05, extra=[(5, 10.0)])
    assert part["residual"][6] == pytest.approx(alone["residual"][6])
    assert (
        dc_backup._part(100.0, 0.0, 4.0, 6, 0.05)["residual"][6] > alone["residual"][6]
    )


def test_pack_prices_refuses_rows_in_different_money_years(monkeypatch):
    from types import SimpleNamespace

    from project_templates.eh_templates import TEMPLATE_PACK_VERSION
    from services.library.defaults_pack import loader

    real = loader.load_defaults_pack

    def mixed(version, region=None):
        pack = real(version, region=region)
        rows = [
            SimpleNamespace(**{**r.__dict__, "money_year": 1999})
            if r.key == "battery.power.overnight"
            else r
            for r in pack.cost_values
        ]
        return SimpleNamespace(**{**pack.__dict__, "cost_values": rows})

    monkeypatch.setattr(loader, "load_defaults_pack", mixed)
    with pytest.raises(dc_backup.DcBackupError, match="money years"):
        dc_backup.pack_prices(TEMPLATE_PACK_VERSION)


def _tsx_keys(const: str) -> set[str]:
    import re

    src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend/src/pages/DcBackupSection.tsx"
    ).read_text()
    body = re.search(
        rf"const {const}: Record<string, string> = \{{(.*?)\n\}}", src, re.S
    )
    assert body, const
    return set(re.findall(r"(\w+):\s*'", body.group(1)))


def test_card_word_maps_cover_every_input_and_line_key():
    """UX 10: every input the backend can mark assumed, and every cost line,
    has a word in the card (no raw key ever shows)."""
    hy = _delta(
        _sizing("hybrid"),
        _bk(mode="hybrid"),
        genset_mw=50.0,
        kept_genset_mw=40.0,
        years=3,
    )
    inputs = {r["key"] for r in hy["inputs"]} - set(dc_backup.PACK_KEYS)
    rows = {r["key"] for r in dc_backup._inputs(_bk(), "N+1", 5)}
    assert inputs | rows <= _tsx_keys("INPUT_LABEL")
    assert {r["line"] for r in hy["lines"]} <= _tsx_keys("LINE_LABEL")
    assert {r["leg"] for r in hy["lines"]} <= _tsx_keys("LEG_LABEL")


def test_genset_fleet_and_strings_read_the_same_redundancy_rule():
    for tier in ("N", "N+1", "2N"):
        s = dc_backup.size_bess(
            [26.0],
            firm_genset_mw=0.0,
            backup=_bk(string_mw=10.0),
            redundancy=tier,
            years=5,
        )
        assert dc_backup.genset_fleet_mw(26.0, 10.0, tier) == s["p_mw"]
    assert dc_backup.genset_fleet_mw(26.0, 10.0, "2N") == 60.0


@pytest.mark.parametrize(
    "bad", ["missing_key", "nan_price", "years_0", "nan_input", "life_0"]
)
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
    raw = {
        **pre,
        "phases": [{**p, "it_mw": p["it_mw"] * scale} for p in pre["phases"]],
        "ai_workload": {},
        "backup": {"mode": mode, **backup},
    }
    return DataCentreLoadSpec.model_validate(raw)


def test_dtc_config_window_needs_both_fields_and_is_not_on_the_study_request():
    from models.energy_hub import DtcConfig
    from services.adequacy.eh_study_runner import DtcConfigRequest

    with pytest.raises(ValueError, match="outage_start and outage_snapshots"):
        DtcConfig(
            critical_load_ids=["a"], islanding_contingencies=["l"], outage_start=3
        )
    assert (
        DtcConfig(
            critical_load_ids=["a"], islanding_contingencies=["l"]
        ).outage_snapshots
        is None
    )
    # Assess-only: the EH study's request refuses the window.
    with pytest.raises(ValueError):
        DtcConfigRequest(
            critical_load_ids=["a"],
            islanding_contingencies=["l"],
            outage_start=1,
            outage_snapshots=2,
        )


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
    own = (
        ts["p_max_pu"]["grid_import"].to_numpy() if pre_existing else np.ones(len(col))
    )
    assert (col[10:14] == 0.0).all()
    assert np.array_equal(np.delete(col, range(10, 14)), np.delete(own, range(10, 14)))
    assert (
        mut["window"]["hours"]
        if "hours" in mut["window"]
        else mut["window"]["snapshots"] == 4
    )
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
    w = dc_backup.outage_window(
        np.array([1.0, 1.0, 9.0, 9.0]), np.array([2.0, 2.0, 2.0, 2.0]), 3.0
    )
    assert (w["start"], w["snapshots"], w["hours"]) == (2, 2, 4.0)
    # The best span would straddle two periods: it is not a candidate.
    w = dc_backup.outage_window(
        np.array([1.0, 5.0, 5.0, 1.0]), np.ones(4), 2.0, np.array([1, 1, 2, 2])
    )
    assert w["start"] in (0, 2) and w["energy_mwh"] == 6.0


@pytest.mark.parametrize("tid", ["eh_datacenter", "eh_datacenter_staged"])
def test_template_window_counts_stores_hours_not_annualised_ones(tid):
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
    with pytest.raises(
        dc_backup.DcBackupError, match="longer than the modelled horizon"
    ):
        dc_backup.outage_window(np.ones(3), np.ones(3), 4.0)


# ── the template in bess / hybrid mode (GK A 4, 6) ──────────────────────


def test_genset_mode_leaves_the_template_bit_for_bit():
    from project_templates import eh_templates as t

    spec = _dc("genset")
    n = t.build_eh_datacenter(load_spec=spec)
    assert not any(s.startswith(dc_backup.STRING_PREFIX) for s in n.storage_units.index)
    assert t.island_config("eh_datacenter", None, load_spec=spec) == t.island_config(
        "eh_datacenter"
    )


def _built(tid, mode, scale):
    from models.flex_archetypes import DataCentreLoadSpec
    from project_templates import eh_templates as t

    pre = t.dc_load_preset(tid)
    spec = DataCentreLoadSpec.model_validate(
        {
            **pre,
            "phases": [{**p, "it_mw": p["it_mw"] * scale} for p in pre["phases"]],
            "ai_workload": {},
            "backup": {"mode": mode},
        }
    )
    return (
        spec,
        t.BUILDERS[tid](load_spec=spec),
        t.island_config(tid, None, load_spec=spec),
    )


@pytest.mark.parametrize("tid", ["eh_datacenter", "eh_datacenter_staged"])
@pytest.mark.parametrize("mode,scale", [("bess", 0.6), ("hybrid", 1.0)])
def test_backup_mode_network_and_sidecar_agree_and_validate(tid, mode, scale):
    from services.adequacy.island_config import validate
    from services.adequacy.site_peak import outage_reserve_mwh, ride_through_batteries

    spec, n, island = _built(tid, mode, scale)
    strings = [
        s for s in n.storage_units.index if s.startswith(dc_backup.STRING_PREFIX)
    ]
    assert strings
    validate(island)  # the gridspine island contract
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
def test_strings_covering_the_reserve_demand_none_of_the_other_batteries(tid):
    """GK A 3: the strings count toward the outage reserve (not dropped from
    it); the reserve the shaving batteries keep is net of the in-service
    strings' deliverable energy, and the strings never shave."""
    from services.adequacy.site_peak import (
        backup_strings_mwh,
        named_load,
        outage_reserve_mwh,
        ride_through_batteries,
        shaving_reserve_mwh,
        without_backup,
    )

    _spec, n, island = _built(tid, "bess", 0.6)
    strings = [
        s for s in n.storage_units.index if s.startswith(dc_backup.STRING_PREFIX)
    ]
    gross, _ = outage_reserve_mwh(n, island)
    held = backup_strings_mwh(n, island)
    assert gross > 0 and held >= gross  # sized to cover the reserve
    net, words = shaving_reserve_mwh(n, island)
    assert net == 0.0 and "held in the backup strings" in words
    assert not set(strings) & set(
        ride_through_batteries(n, without_backup(island))["names"]
    )
    assert set(strings) <= set(
        island["requirements"]["ride_through_storage"]
    )  # untouched
    # GK A Q2: the N strings (not the spares) × string_mw × autonomy_h, and
    # the other batteries shave as they do in genset mode.
    from services.adequacy.site_peak import peak_check

    spec, _n, _i = _built(tid, "bess", 0.6)
    sizing = dc_backup.size_bess(
        named_load(n, dc_backup.critical_loads(island)),
        firm_genset_mw=0.0,
        backup=spec.backup,
        redundancy=spec.redundancy,
        years=1,
    )
    b = spec.backup
    assert held == pytest.approx(sizing["strings_n"] * b.string_mw * b.autonomy_h)
    spares = [s for s in strings if s.startswith(dc_backup.SPARE_PREFIX)]
    assert len(spares) == sizing["strings"] - sizing["strings_n"]
    _g, gn, gisland = _built(tid, "genset", 0.6)

    def shaving(rows):
        return [(r["period"], r["firm"][1]["mw"]) for r in rows]

    assert shaving(peak_check(n, island)) == shaving(peak_check(gn, gisland))


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

    return {
        "cfg": SolverConfig(voll=500.0),
        "lock": threading.Lock(),
        "stop_event": threading.Event(),
        "log_queue": queue.SimpleQueue(),
    }


@pytest.mark.live_solve
def test_normal_solve_never_discharges_the_backup():
    _spec, n, _island = _built("eh_datacenter", "bess", 0.6)
    n.optimize(solver_name="highs")
    strings = [
        s for s in n.storage_units.index if s.startswith(dc_backup.STRING_PREFIX)
    ]
    assert float(
        n.storage_units_t.p_dispatch[strings].to_numpy().sum()
    ) == pytest.approx(0.0, abs=1e-6)


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
    island = {
        "units": {"g1": {"kind": "genset"}, "crit": {"kind": "load", "critical": True}},
        "requirements": {},
    }
    return n, island


@pytest.mark.live_solve
@pytest.mark.parametrize("scale,verdict", [(1.0, "pass"), (0.95, "fail")])
def test_proof_serves_at_exactly_the_sized_energy_and_fails_below(scale, verdict):
    n, island = _flat_site()
    bk = _bk(autonomy_h=4.0, ride_through_s=0.0, bess={"depth_of_discharge": 0.8})
    sizing = dc_backup.size_bess(
        np.full(24, 10.0), firm_genset_mw=0.0, backup=bk, redundancy="N+1", years=10
    )
    assert sizing["strings_n"] == 2 and sizing["strings"] == 3
    window = dc_backup.outage_window(np.full(24, 10.0), np.ones(24), 4.0)
    proof = dc_backup._proof(
        n, island, sizing, bk, window, energy_scale=scale, **_run()
    )
    assert proof["verdict"] == verdict
    assert proof["strings_in_service"] == 2
    if verdict == "fail":
        assert proof["window_unserved_mwh"] == pytest.approx(0.05 * 40.0, rel=1e-3)


@pytest.mark.live_solve
def test_an_unreadable_unmet_is_not_shown(monkeypatch):
    from services.adequacy import dtc

    n, island = _flat_site()
    bk = _bk(autonomy_h=2.0, ride_through_s=0.0)
    sizing = dc_backup.size_bess(
        np.full(24, 10.0), firm_genset_mw=0.0, backup=bk, redundancy="N", years=5
    )
    window = dc_backup.outage_window(np.full(24, 10.0), np.ones(24), 2.0)
    monkeypatch.setattr(
        dtc,
        "_window_unserved",
        lambda *a, **k: {
            "critical_unserved_window_mwh": None,
            "critical_unserved_outside_window_mwh": None,
        },
    )
    proof = dc_backup._proof(n, island, sizing, bk, window, **_run())
    assert proof["verdict"] == "not_shown"


@pytest.mark.live_solve
def test_assess_proves_both_legs_and_prices_the_mode():
    from project_templates import eh_templates as t

    spec = _dc("bess")
    genset = spec.model_copy(
        update={"backup": spec.backup.model_copy(update={"mode": "genset"})}
    )
    n = t.build_eh_datacenter(load_spec=genset)
    out = dc_backup.assess(
        n,
        t.island_config("eh_datacenter"),
        spec,
        years=20,
        prices=dc_backup.pack_prices(t.TEMPLATE_PACK_VERSION),
        **_run(),
    )
    assert out["window"]["hours"] == 4.0 and out["window"]["energy_mwh"] > 0
    assert out["proofs"]["genset"]["verdict"] == "pass"
    assert out["proofs"]["bess"]["verdict"] == "pass" and out["verdict"] == "pass"
    assert out["proofs"]["bess"]["strings_in_service"] == out["sizing"]["strings_n"]
    c = out["cost"]
    assert c["delta"] == pytest.approx(c["npv_chosen"] - c["npv_genset"])
    assert c["genset_mw"] == dc_backup.genset_fleet_mw(
        out["sizing"]["critical_peak_mw"], 10.0, "N+1"
    )


# ── the routes (GK A ruling 1 / GK B F8) ────────────────────────────────


def _stage_dc(tmp_path, monkeypatch, tid):
    from project_templates import eh_templates as T
    from routers import projects as projects_router

    src = tmp_path / tid
    src.mkdir()
    T.BUILDERS[tid]().export_to_netcdf(str(src / "network.nc"))
    T.write_sidecars(src, tid)
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    (tmp_path / "eh_templates.py").write_text((pathlib.Path(T.__file__)).read_text())


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "hybrid"},  # valid: stored
        {"mode": "bess"},  # refused by the peak check
        {"mode": "diesel"},  # refused by the schema
        {"mode": "bess", "autonomy_h": -1.0},  # refused by a validator
    ],
)
def test_put_backup_leaves_the_network_fingerprint_and_case_digest(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx, change
):
    from routers.results import _ic_current_assumptions
    from services import project_registry
    from services.workflow_tools import _network_input_fingerprint

    tid = "eh_datacenter"
    _stage_dc(tmp_path, monkeypatch, tid)
    r = client.post(
        f"/api/projects/from_template/{tid}",
        params={"name": "dc_bk"},
        json={"dc_load": {}},
    )
    assert r.status_code == 200, r.text
    name = r.json().get("name", "dc_bk")
    d = pathlib.Path(project_registry.project_dir(project_row(name)))
    ctx = session_ctx(client)
    nc, fp, (ic, why) = (
        (d / "network.nc").read_bytes(),
        _network_input_fingerprint(ctx),
        _ic_current_assumptions(),
    )
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
    client, tmp_path, monkeypatch, tmp_projects_dir
):
    _stage_dc(tmp_path, monkeypatch, "eh_datacenter")
    r = client.post(
        "/api/projects/from_template/eh_datacenter", params={"name": "dc_plain"}
    )
    name = r.json().get("name", "dc_plain")
    for resp in (
        client.put(f"/api/campus-electrical/{name}/dc-backup", json={"mode": "hybrid"}),
        client.post(f"/api/campus-electrical/{name}/dc-backup/assess", json={}),
    ):
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error_kind"] == "dc_backup_invalid"
    assert (
        client.post(
            f"/api/campus-electrical/{name}/dc-backup/assess", json={"mode": "diesel"}
        ).status_code
        == 422
    )


def _served(monkeypatch):
    """Stub the re-dispatch: every contingency solves and serves (the fleet
    and window wiring is under test, not the LP)."""
    from services.adequacy import dtc

    monkeypatch.setattr(
        dtc,
        "run_dtc_stress",
        lambda *a, **k: {
            "contingencies": [{"status": "ok", "critical_unserved_window_mwh": 0.0}]
        },
    )


def _dc_project(client, tmp_path, monkeypatch, project_row, name, finance=None):
    import json

    from services import project_registry

    _stage_dc(tmp_path, monkeypatch, "eh_datacenter")
    r = client.post(
        "/api/projects/from_template/eh_datacenter",
        params={"name": name},
        json={"dc_load": {}},
    )
    d = pathlib.Path(
        project_registry.project_dir(project_row(r.json().get("name", name)))
    )
    if finance is not None:
        cfg = d / "solver_config.json"
        raw = json.loads(cfg.read_text()) if cfg.exists() else {}
        cfg.write_text(json.dumps({**raw, "finance": finance}))
    return d


def test_assess_route_sizes_on_the_raw_sidecar_and_proves_the_priced_fleets(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row
):
    """GK A 1: N+1 firm gensets (30 MW, not 40); GK A 2 / GK B K1: each
    proof dispatches the fleet its cost leg prices; GK A 4: hybrid's window
    is on the shortfall, both reported; GK B K4: the pack's gas heat rate."""
    from project_templates import eh_templates as t
    from services.adequacy.site_peak import named_load

    _served(monkeypatch)
    d = _dc_project(client, tmp_path, monkeypatch, project_row, "dc_fleet")
    out = dc_backup.assess_project(d, t, "hybrid")
    sz, c = out["sizing"], out["cost"]
    assert sz["firm_genset_mw"] == 30.0 and "N+1" in sz["firm_genset_rule"]
    assert out["proofs"]["genset"]["genset_fleet_mw"] == c["genset_mw"]
    assert out["proofs"]["hybrid"]["genset_fleet_mw"] == c["hybrid_genset_mw"] == 40.0
    assert c["genset_mw"] == dc_backup.genset_fleet_mw(
        sz["critical_peak_mw"], 10.0, "N+1"
    )
    w = out["windows"]
    assert out["window"] == w["shortfall"]
    assert out["proofs"]["hybrid"]["window_first"] == w["shortfall"]["first"]
    assert out["proofs"]["genset"]["window_first"] == w["critical"]["first"]
    import pypsa

    n = pypsa.Network(str(d / "network.nc"))
    crit = named_load(n, dc_backup.critical_loads(t.island_config("eh_datacenter")))
    assert w["shortfall"]["energy_mwh"] == pytest.approx(
        dc_backup.outage_window(
            np.clip(crit - 30.0, 0, None),
            np.ones(crit.size),
            4.0,
            dc_backup._periods(n),
        )["energy_mwh"]
    )
    heat = {r["key"]: r for r in c["inputs"]}["heat_rate"]
    assert heat["value"] == pytest.approx(1 / 0.41) and not heat["assumed"]
    assert dc_backup.EFFICIENCY_KEY in heat["source"]


@pytest.mark.parametrize(
    "finance,rate,assumed,words",
    [
        (None, 0.1, True, "real, assumed"),
        (
            {"wacc_nominal": 0.09, "inflation": 0.02, "financial_close": "2026-01-01"},
            1.09 / 1.02 - 1,
            False,
            "made real",
        ),
        (
            {
                "wacc_nominal": 0.09,
                "price_basis": "real",
                "financial_close": "2026-01-01",
            },
            0.09,
            False,
            "real price basis",
        ),
    ],
)
def test_assess_reads_the_finance_case_rate_on_its_basis(
    client,
    tmp_path,
    monkeypatch,
    tmp_projects_dir,
    project_row,
    finance,
    rate,
    assumed,
    words,
):
    """GK B K3: the finance case's wacc (nominal made real with its
    inflation, or real as given); unset, the pack's rate, real and assumed."""
    from project_templates import eh_templates as t

    _served(monkeypatch)
    pack = dc_backup.pack_prices(t.TEMPLATE_PACK_VERSION)["discount_rate"]
    d = _dc_project(client, tmp_path, monkeypatch, project_row, "dc_rate", finance)
    c = dc_backup.assess_project(d, t, "hybrid")["cost"]
    want = pack if assumed else rate
    assert c["discount_rate"] == pytest.approx(want)
    row = {r["key"]: r for r in c["inputs"]}["discount_rate"]
    assert row["assumed"] is assumed and words in row["source"]


def test_a_project_created_with_strings_is_refused_with_the_way_out(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row
):
    """GK A Q1: the saved network already has the strings; assess is a typed
    422 that says to switch with PUT /dc-backup on a genset-form project,
    and the meta marks it for the card."""
    import json

    from services import project_registry

    _served(monkeypatch)
    _stage_dc(tmp_path, monkeypatch, "eh_datacenter")
    r = client.post(
        "/api/projects/from_template/eh_datacenter",
        params={"name": "dc_hy"},
        json={"dc_load": {"backup": {"mode": "hybrid"}}},
    )
    assert r.status_code < 300, r.text
    d = pathlib.Path(
        project_registry.project_dir(project_row(r.json().get("name", "dc_hy")))
    )
    meta = json.loads((d / "eh_template.json").read_text())
    assert meta["dc_load"]["network_backup_mode"] == "hybrid"
    r = client.post(f"/api/campus-electrical/{d.name}/dc-backup/assess", json={})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error_kind"] == "dc_backup_invalid"
    assert "PUT /dc-backup" in str(detail) and "created with hybrid" in str(detail)


def test_a_nominal_finance_rate_without_inflation_is_refused(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row
):
    """GK B F3: a nominal wacc with no inflation is a typed 422, never read
    as real and never an assumed 0."""
    _served(monkeypatch)
    d = _dc_project(
        client,
        tmp_path,
        monkeypatch,
        project_row,
        "dc_nom",
        {"wacc_nominal": 0.09, "financial_close": "2026-01-01"},
    )
    r = client.post(
        f"/api/campus-electrical/{d.name}/dc-backup/assess", json={"mode": "hybrid"}
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error_kind"] == "dc_backup_invalid" and "inflation" in str(detail)


@pytest.mark.live_solve
def test_assess_route_returns_json_and_writes_nothing(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row
):
    from services import project_registry

    _stage_dc(tmp_path, monkeypatch, "eh_datacenter")
    r = client.post(
        "/api/projects/from_template/eh_datacenter",
        params={"name": "dc_as"},
        json={"dc_load": {}},
    )
    name = r.json().get("name", "dc_as")
    d = pathlib.Path(project_registry.project_dir(project_row(name)))
    before = {f: (d / f).read_bytes() for f in ("network.nc", "eh_template.json")}
    r = client.post(
        f"/api/campus-electrical/{name}/dc-backup/assess", json={"mode": "hybrid"}
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["mode"] == "hybrid" and out["verdict"] in ("pass", "fail", "not_shown")
    assert {"genset", "hybrid"} <= set(out["proofs"]) and "delta" in out["cost"]
    # GK A 7: which saved network was read, and what the re-dispatch does not reproduce.
    import hashlib

    basis = out["basis"]
    assert (
        basis["network_sha256"] == hashlib.sha256(before["network.nc"]).hexdigest()[:12]
    )
    assert basis["network_saved_at"] and basis["not_reproduced"] == list(
        dc_backup.NOT_REPRODUCED
    )
    assert {f: (d / f).read_bytes() for f in before} == before
