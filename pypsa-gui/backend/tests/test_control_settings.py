"""AS-07: the control-settings sheet the forward pass writes next to the PSS/E dyr.

Every number is checked by hand against the formulas of `services.control_settings`; the limits are generic
engineering, "assumed" (SAGC-1).
"""
from __future__ import annotations

import csv
import json
import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pypsa
import pytest

from services import control_settings as CS
from services.solver import envelope_floors as EF
from services.solver.split_storage import _wrap_with_split_storage, add_split_storage

#: Swing 30 MW, oscillation 10 MW at 2 s, checkpoint dip 20 MW for 30 s (the prototype envelope).
PROTOTYPE = {"phases": [{"swing_mw": 30.0, "checkpoint": {"depth_mw": 20.0, "duration_s": 30.0},
                         "oscillation": {"amplitude_mw": 10.0, "period_s": 2.0}}]}
RAMP = 10.0                                                     # MW/min
#: 2·(step + checkpoint) + oscillation, the sum of the events, by hand (the formula; RESULTS.md rounds to 1.82).
E_SM = 2 * (30**2 / (2 * 10 * 60) + min(20 * 30 / 3600, 20**2 / (2 * 10 * 60))) + 10 * 2 / (math.pi * 3600)


def _recommendation(status="recommended"):
    return SimpleNamespace(status=status, achievable=True, changed=True, margin=0.1, notes=[],
                           settings={"bess1": {"model": "gfm_droop", "current": {"droop_pct": 5.0},
                                               "recommended": {"droop_pct": 3.0, "H_equivalent_s": 4.0, "tau_f_s": 4.0,
                                                               "i_max_pu": 1.2}}})


def _tier(**kw) -> dict:
    return {"name": "fast", "p_mw": 40.0, "e_mwh": 10.0, "p_sm_mw": 40.0, "e_sm_mwh": E_SM, "e_min_pu": 0.0,
            "e_max_pu": 1.0, "eta_dis": 0.96, "control": "gfm_droop", **kw}


def _sheet(tiers=None, **kw) -> dict:
    kw.setdefault("gfm", _recommendation())
    return CS.build_sheet(envelope=PROTOTYPE, ramp_mw_per_min=RAMP, tiers=tiers or [_tier()], **kw)


def _row(sheet: dict, row_id: str) -> dict:
    (row,) = [r for r in sheet["rows"] if r["id"] == row_id]
    return row


# ── SOC band ─────────────────────────────────────────────────────────────────


def test_a_soc_band_is_half_the_smoothing_energy_in_from_each_end():
    b = CS.soc_band(10.0, 1.82)                                  # the issue's figure
    assert (b["smoothing_lower_mwh"], b["smoothing_upper_mwh"]) == (pytest.approx(0.91), pytest.approx(9.09))


def test_the_formula_energy_gives_0_9175_and_9_0825_not_the_rounded_issue_figures():
    b = CS.soc_band(10.0, E_SM)
    assert E_SM == pytest.approx(1.835, abs=5e-4)
    assert b["smoothing_lower_mwh"] == pytest.approx(E_SM / 2) and b["smoothing_lower_mwh"] == pytest.approx(0.9175, abs=3e-4)
    assert b["smoothing_upper_mwh"] == pytest.approx(10.0 - E_SM / 2)


def test_the_band_sits_in_the_stores_usable_window_and_above_the_backup_slice():
    b = CS.soc_band(10.0, 2.0, e_min_pu=0.1, e_max_pu=0.9, backup_stored_mwh=3.0)
    assert b["backup_floor_mwh"] == pytest.approx(1.0 + 3.0)                       # e_min x E + stored backup
    assert b["smoothing_lower_mwh"] == pytest.approx(1.0 + 3.0 + 1.0)
    assert b["smoothing_upper_mwh"] == pytest.approx(9.0 - 1.0)
    assert b["fits"] is True


def test_a_band_that_does_not_fit_the_store_is_flagged():
    b = CS.soc_band(10.0, 9.0, backup_stored_mwh=3.0)
    assert b["fits"] is False


# ── the sheet traces to the sized capacity ───────────────────────────────────


def test_each_sized_setting_names_the_capacity_it_traces_to():
    sheet = _sheet(backup_mwh=0.0)
    soc = _row(sheet, "soc_band.fast")
    assert soc["traces_to"] == "Store fast: e_nom_opt 10 MWh, e_smooth 1.835 MWh"
    assert soc["value"] == {"smoothing_lower_mwh": pytest.approx(E_SM / 2), "smoothing_upper_mwh": pytest.approx(10 - E_SM / 2),
                            "backup_floor_mwh": 0.0, "fits": True}
    split = _row(sheet, "tier_split.fast")
    assert split["traces_to"].startswith("Store fast")
    assert split["value"] == {"power_share": 1.0, "energy_share": 1.0}
    assert all(r["source"] in ("sized", "assumed") and r["traces_to"] for r in sheet["rows"] if r["status"] != "needs EMT")


def test_the_ramp_limiter_is_the_ramp_the_floors_were_sized_with():
    r = _row(_sheet(), "ramp_limiter")
    assert (r["value"], r["unit"]) == (RAMP, "MW/min")


def test_the_filter_cut_off_passes_a_full_step_no_faster_than_the_ramp_and_sits_below_the_oscillation():
    r = _row(_sheet(), "smoothing_filter_fc")
    f_ramp = (RAMP / 60.0) / (2 * math.pi * 30.0)                # first-order low-pass: initial slope S x 2π f_c
    assert r["value"] == pytest.approx(f_ramp)
    assert r["value"] < 1.0 / 2.0                                # the 2 s oscillation (0.5 Hz)


def test_the_tier_split_follows_each_stores_share_of_the_smoothing_power_and_energy():
    a, b = _tier(name="a", p_sm_mw=30.0, e_sm_mwh=1.0), _tier(name="b", p_sm_mw=10.0, e_sm_mwh=0.5)
    sheet = _sheet([a, b])
    assert _row(sheet, "tier_split.a")["value"] == {"power_share": pytest.approx(0.75), "energy_share": pytest.approx(1 / 1.5)}
    assert _row(sheet, "tier_split.b")["value"]["power_share"] == pytest.approx(0.25)


def test_the_backup_energy_is_shared_over_the_backup_stores_by_size():
    big = _tier(name="big", e_mwh=30.0, backup=True)
    small = _tier(name="small", e_mwh=10.0, backup=True)
    sheet = _sheet([big, small], backup_mwh=96.0)
    # 96 MWh AC through eta 0.96 is 100 MWh stored, 75 / 25 by energy rating
    assert _row(sheet, "soc_band.big")["value"]["backup_floor_mwh"] == pytest.approx(75.0)
    assert _row(sheet, "soc_band.small")["value"]["backup_floor_mwh"] == pytest.approx(25.0)


# ── latency ──────────────────────────────────────────────────────────────────


def test_the_band_limit_is_one_over_two_pi_f_of_the_oscillation():
    assert CS.band_limit_s(0.5) == pytest.approx(1 / math.pi)
    assert _sheet()["latency_limit_s"] == pytest.approx(1 / math.pi)       # 2 s period -> 0.5 Hz -> 0.318 s


def test_a_tier_slower_than_the_band_limit_fails_the_latency_check_and_is_flagged():
    slow = _tier(name="slow", control="gfl_pq", latency_s=0.4)                # 0.4 s > 0.318 s
    sheet = _sheet([_tier(), slow])
    ok, bad = _row(sheet, "latency.fast"), _row(sheet, "latency.slow")
    assert ok["status"] == "ok" and ok["value"] == pytest.approx(0.02)
    assert bad["status"] == "flagged" and "0.4" in bad["note"]
    assert sheet["ok"] is False and any("slow" in f for f in sheet["flags"])


def test_a_grid_following_tier_at_the_assumed_0_2_s_passes_a_2_s_oscillation():
    sheet = _sheet([_tier(control="gfl_pq")])
    assert _row(sheet, "latency.fast")["status"] == "ok" and sheet["ok"] is True


def test_a_tier_exactly_at_the_limit_fails_because_the_rule_is_strict():
    limit = CS.band_limit_s(0.5)
    assert _row(_sheet([_tier(latency_s=limit)]), "latency.fast")["status"] == "flagged"


# ── GFM, UPS, preset ─────────────────────────────────────────────────────────


def test_the_gfm_rows_come_from_the_recommendation():
    sheet = _sheet(gfm=_recommendation())
    droop, h = _row(sheet, "gfm_droop.bess1"), _row(sheet, "gfm_h.bess1")
    assert (droop["value"], droop["unit"]) == (3.0, "%")
    assert (h["value"], h["unit"]) == (4.0, "s")
    assert "recommend_gfm_settings" in droop["traces_to"]


def test_a_gfm_search_that_could_not_achieve_the_limits_is_flagged_not_hidden():
    rec = _recommendation("not_achievable")
    rec.achievable = False
    sheet = _sheet(gfm=rec)
    assert _row(sheet, "gfm_droop.bess1")["status"] == "flagged"
    assert sheet["ok"] is False


def test_without_a_recommendation_the_gfm_row_says_it_was_not_run():
    r = _row(_sheet(gfm=None), "gfm_tuning")
    assert r["status"] == "flagged" and "not run" in r["note"]


UPS = {"ups1": {"kind": "ups", "values": {"walk_in_s": 15.0, "f_window_hz": 0.5, "transfers_on_islanding": True}}}


def test_the_ups_rows_carry_walk_in_and_transfer_and_count_no_disturbance():
    sheet = _sheet(ups=UPS)
    assert _row(sheet, "ups_walk_in.ups1")["value"] == 15.0
    assert _row(sheet, "ups_transfer.ups1")["value"] == {"f_window_hz": 0.5, "transfers_on_islanding": True}
    dc = _row(sheet, "ups_disturbance_counting.ups1")
    assert dc["value"] == "none" and "NOGRR 282" in dc["note"]


def test_the_ups_walk_in_must_keep_the_grid_ramp_within_the_limit_when_its_rating_is_known():
    # 60 MW x 60 / 15 s = 240 MW/min through the grid vs a 10 MW/min limit
    ups = {"ups1": {**UPS["ups1"], "p_mw": 60.0}}
    r = _row(_sheet(ups=ups), "ups_walk_in.ups1")
    assert r["status"] == "flagged" and r["min_walk_in_s"] == pytest.approx(60 * 60 / RAMP)
    assert _row(_sheet(ups={"ups1": {**UPS["ups1"], "p_mw": 1.0}}), "ups_walk_in.ups1")["status"] == "set"


def test_the_preset_is_listed_as_selected_or_none():
    assert _row(_sheet(preset="fast_frequency_response"), "preset")["value"] == "fast_frequency_response"
    assert _row(_sheet(preset=None), "preset")["value"] is None


# ── sub-cycle items ──────────────────────────────────────────────────────────


def test_sub_cycle_items_are_listed_needs_emt_with_a_reason():
    emt = [r for r in _sheet()["rows"] if r["status"] == "needs EMT"]
    assert {r["id"] for r in emt} >= {"emt.current_loop", "emt.pll", "emt.ups_transfer_switching"}
    assert all(r["note"] and r["source"] == "assumed" for r in emt)
    assert all(r["status"] != "needs EMT" for r in _sheet()["rows"] if r["id"].startswith("latency."))


def test_needs_emt_rows_never_count_as_a_failure():
    assert _sheet()["ok"] is True


# ── export ───────────────────────────────────────────────────────────────────


def test_the_export_lands_next_to_the_dyr_and_round_trips(tmp_path):
    dyr = tmp_path / "campus.dyr"
    dyr.write_text("")
    paths = CS.write_sheet(_sheet(ups=UPS), dyr)
    assert {p.name for p in paths} == {"campus.control_settings.json", "campus.control_settings.csv"}
    assert all(p.parent == tmp_path for p in paths)
    doc = json.loads((tmp_path / "campus.control_settings.json").read_text())
    assert doc["format"] == CS.FORMAT and doc["rows"]
    rows = list(csv.DictReader((tmp_path / "campus.control_settings.csv").open()))
    assert [r["id"] for r in rows] == [r["id"] for r in doc["rows"]]
    assert set(rows[0]) == {"id", "group", "setting", "value", "unit", "source", "status", "traces_to", "note"}


def test_the_format_text_names_every_column_and_status():
    for word in ("id", "traces_to", "needs EMT", "flagged", "assumed", "sized"):
        assert word in CS.FORMAT


# ── from a solved network ────────────────────────────────────────────────────


def _solved() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(24))
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "load", bus="ac", p_set=50.0)
    n.add("Generator", "import", bus="ac", p_nom=1e4, marginal_cost=pd.Series(np.full(24, 50.0), n.snapshots))
    add_split_storage(n, "fast", "ac", "high_c_bess", power_capital_cost=50.0, energy_capital_cost=20.0)
    EF.tag_store(n, "fast", smoothing_tier=True)
    floors = EF.floors_from_envelope(PROTOTYPE, RAMP)
    fn = EF._wrap_with_envelope_floors(n, _wrap_with_split_storage(n, None), SimpleNamespace(envelope_floors=floors))
    status, cond = n.optimize(solver_name="highs", extra_functionality=fn)
    assert status == "ok", cond
    return n


def test_tiers_read_off_the_solved_split_storage():
    (t,) = CS.tiers_from_solved(_solved())
    assert t["name"] == "fast"
    assert t["p_sm_mw"] == pytest.approx(40.0, rel=1e-6)
    assert t["e_sm_mwh"] == pytest.approx(E_SM, rel=1e-6)
    assert t["e_mwh"] == pytest.approx(10.0)                     # high_c_bess minimum duration 0.25 h x 40 MW
    assert t["e_mwh"] >= t["e_sm_mwh"]
    assert t["p_mw"] >= t["p_sm_mw"] - 1e-6


def test_the_sheet_from_a_solved_network_has_a_band_that_fits():
    n = _solved()
    sheet = CS.sheet_from_solved(n, PROTOTYPE, RAMP)
    soc = _row(sheet, "soc_band.fast")["value"]
    assert soc["fits"] is True
    assert soc["smoothing_upper_mwh"] - soc["smoothing_lower_mwh"] == pytest.approx(
        float(n.stores.at["fast", "e_nom_opt"]) - E_SM, rel=1e-6)


def test_a_solved_network_without_its_lp_solution_is_refused_not_read_as_zero_slices():
    n = _solved()
    n._model = None
    with pytest.raises(CS.ControlSettingsError, match="fast"):
        CS.tiers_from_solved(n)


def test_a_model_without_the_envelope_variables_is_refused():
    n = _solved()
    n.model.variables.remove("EnvelopeFloor-p_smooth")
    with pytest.raises(CS.ControlSettingsError, match="EnvelopeFloor-p_smooth"):
        CS.tiers_from_solved(n)


def test_an_envelope_without_oscillation_gets_a_not_checked_latency_row():
    env = {"phases": [{"swing_mw": 30.0}]}
    sheet = CS.build_sheet(envelope=env, ramp_mw_per_min=RAMP, tiers=[_tier()], gfm=_recommendation())
    r = _row(sheet, "latency")
    assert r["status"] == "not checked" and "oscillation" in r["note"]
    assert sheet["latency_limit_s"] is None and sheet["ok"] is True
    assert not [x for x in sheet["rows"] if x["id"].startswith("latency.")]


def test_the_latency_note_carries_the_basis_of_the_assumed_latency():
    assert "grid forming" in _row(_sheet(), "latency.fast")["note"]
    assert "grid following" in _row(_sheet([_tier(control="gfl_pq")]), "latency.fast")["note"]
    assert "given" in _row(_sheet([_tier(latency_s=0.1)]), "latency.fast")["note"]


# ── the shared store latency (AS-13) ─────────────────────────────────────────


def test_the_sheet_and_the_tier_split_read_one_latency_for_a_store_with_no_control_column():
    from services.adequacy import spectrum as S

    n = _solved()
    assert "control" not in n.stores.columns
    (t,) = CS.tiers_from_solved(n)
    assert t["latency_s"] == S.store_latency(n, "fast")[0] == S.tier_split(n, 2.0)["fast"]["latency_s"]
    assert t["latency_basis"] == S.store_latency(n, "fast")[1]
    assert "assumed for high_c_bess" in _row(CS.sheet_from_solved(n, PROTOTYPE, RAMP), "latency.fast")["note"]


def test_a_given_tier_latency_is_read_before_the_shared_one():
    assert "given" in _row(_sheet([_tier(latency_s=0.1, latency_basis="given")]), "latency.fast")["note"]


def test_a_trace_sourced_oscillation_sets_the_latency_band_at_the_traces_f_c():
    osc = {"amplitude_mw": 10.0, "period_s": 2.0, "source": "trace", "f_c_hz": 5.0}
    env = {"phases": [{**PROTOTYPE["phases"][0], "oscillation": osc}]}
    sheet = CS.build_sheet(envelope=env, ramp_mw_per_min=RAMP, tiers=[_tier()], gfm=_recommendation())
    assert sheet["latency_limit_s"] == pytest.approx(CS.band_limit_s(5.0))


def test_a_trace_f_c_below_one_over_t_does_not_loosen_the_latency_band():
    osc = {"amplitude_mw": 10.0, "period_s": 6.6, "source": "trace", "f_c_hz": 0.150}     # 1/T = 0.1515 Hz
    env = {"phases": [{**PROTOTYPE["phases"][0], "oscillation": osc}]}
    sheet = CS.build_sheet(envelope=env, ramp_mw_per_min=RAMP, tiers=[_tier()], gfm=_recommendation())
    assert sheet["latency_limit_s"] == pytest.approx(CS.band_limit_s(1 / 6.6))
