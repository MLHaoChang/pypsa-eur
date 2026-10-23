"""
DC grid readiness 02: the grid-readiness scorecard.

Generic (D2): every EH template gets a scorecard through what it writes into
the project, never by its id. A missing input reads ``unknown``, never
``pass``; a dynamic requirement is always ``unknown``; every limit cites its
source or says "assumed". The peak row and the create-time 422 of DC 01 are
one check (``site_peak.peak_check``).
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

import numpy as np
import pandas as pd
import pypsa
import pytest

from models.flex_archetypes import DataCentreLoadSpec
from services.adequacy import grid_readiness as G
from services.adequacy import site_peak as SP
from services.dc_load_profile import DcLoadError

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

TIDS = ("eh_datacenter", "eh_datacenter_staged", "eh_h2_hub", "eh_h2_hub_staged",
        "eh_microgrid", "eh_microgrid_staged")
DC_IDS = ("eh_datacenter", "eh_datacenter_staged")
ENVELOPE_ROWS = ("ramp_rate", "oscillation", "rapid_voltage_change")
PROFILE = "eu_rfg_dcc_ce"
FLOW_SECTIONS = {"project", "carriers", "connection", "loads", "assets", "profiles", "commercial",
                 "scenarios", "readiness", "results"}


def _project(tmp_path, tid, dc_load=None):
    """A project directory as the create-from-template route writes it, and its network."""
    d = tmp_path / tid
    d.mkdir()
    spec = T.dc_load_spec(tid, dc_load) if dc_load is not None else None
    n = T.BUILDERS[tid](load_spec=spec) if spec is not None else T.BUILDERS[tid]()
    T.write_sidecars(d, tid)
    if spec is not None:
        meta = json.loads((d / "eh_template.json").read_text())
        meta["dc_load"] = T.dc_load_meta(tid, spec)
        (d / "eh_template.json").write_text(json.dumps(meta))
    return d, n


def _rows(out) -> dict:
    return {r["id"]: r for r in out["rows"]}


# ── the template markings ───────────────────────────────────────────────────


@pytest.mark.parametrize("tid", TIDS)
def test_the_markings_match_the_built_network(tid):
    link, mw, eff, bus, sk, _lole = T._GRID_READINESS_SITE[tid]
    n = T.BUILDERS[tid]()
    assert SP.import_links(n) == [link]
    assert n.links.at[link, "p_nom"] == mw
    assert n.links.at[link, "efficiency"] == eff
    assert bool(n.buses.at[bus, "eh_poc"])
    if sk is None:
        assert not math.isfinite(float(n.buses.at[bus, "eh_sk_mva"]))
    else:
        assert n.buses.at[bus, "eh_sk_mva"] == sk


@pytest.mark.parametrize("tid", TIDS)
def test_each_marking_is_an_assumed_ledger_row_with_its_flow_target(tid):
    rows = {r["item"]: r for r in T.TEMPLATE_META[tid]["ledger"]}
    for item in ("connection_ramp", "oscillation", "import_efficiency"):
        assert item in rows, item
    for item in ("connection_ramp", "oscillation", "import_efficiency", "fault_level", "lole_goal"):
        r = rows.get(item)
        if r is None:
            continue
        assert r["source"] == "assumed" and r["note"].startswith("assumed")
        assert r["label"] and r["label"][0].isupper() and "_" not in r["label"]
        assert isinstance(r["value"], float) and r["unit"]
        assert r["section"] in FLOW_SECTIONS
        assert set(r) >= {"panel", "component", "entity", "field"}
    assert rows["connection_ramp"]["field"] == "connection_ramp"     # not Link.ramp_limit_up (p.u./h)
    if "lole_goal" in rows:
        assert (rows["lole_goal"]["section"], rows["lole_goal"]["panel"],
                rows["lole_goal"]["field"]) == ("scenarios", "simparams", "target_lole_h")


def test_the_ramp_marking_is_ten_percent_of_the_connection_per_minute():
    ramp = {r["item"]: r for r in T.TEMPLATE_META["eh_datacenter"]["ledger"]}["connection_ramp"]
    assert ramp["value"] == pytest.approx(4.0)


# ── every template produces a scorecard ─────────────────────────────────────


@pytest.mark.parametrize("tid", TIDS)
def test_every_template_produces_a_scorecard_whose_limits_cite_a_source(tmp_path, tid):
    d, n = _project(tmp_path, tid)
    out = G.grid_readiness(d, n, PROFILE)
    assert out["schema"] == G.SCHEMA and out["template_id"] == tid
    assert out["profile"] == {"id": PROFILE, "title": out["profile"]["title"], "error": None}
    for r in out["rows"]:
        assert r["verdict"] in G.VERDICTS
        assert isinstance(r["source"], str) and r["source"].strip(), r["id"]
        # a limit either reads "assumed" or cites a source that is not an assumption
        assert r["assumed"] or "assumed" not in r["source"], r["id"]
        assert r["section"] in FLOW_SECTIONS and r["target"]["section"] == r["section"]
        assert set(r["target"]) == {"section", "panel", "component", "entity", "field"}
    s = out["summary"]
    assert sum(s[v] for v in G.VERDICTS) == len(out["rows"])
    assert s["red_flags"] == s["fail"] + s["warn"]


@pytest.mark.parametrize("tid", TIDS)
def test_without_the_ai_profile_the_envelope_rows_are_unknown(tmp_path, tid):
    d, n = _project(tmp_path, tid)
    rows = _rows(G.grid_readiness(d, n, PROFILE))
    for rid in ENVELOPE_ROWS:
        assert rows[rid]["verdict"] == "unknown", rid
        if rid != "rapid_voltage_change" or rows[rid]["limit"] is not None:
            assert "AI-training load profile" in rows[rid]["note"] \
                or "short-circuit level" in rows[rid]["note"]


@pytest.mark.parametrize("tid", DC_IDS)
def test_with_the_ai_profile_the_envelope_rows_are_red_flags_before_mitigation(tmp_path, tid):
    d, n = _project(tmp_path, tid, dc_load={})
    out = G.grid_readiness(d, n, PROFILE)
    rows = _rows(out)
    env = json.loads((d / "eh_template.json").read_text())["dc_load"]["envelope"]
    ramp = rows["ramp_rate"]
    assert ramp["project_value"] == pytest.approx(env["max"]["max_dp_dt_mw_per_s"] * 60.0, rel=1e-4)
    assert ramp["verdict"] == "fail" and "before mitigation" in ramp["note"]
    assert rows["oscillation"]["verdict"] == "fail"
    rvc = rows["rapid_voltage_change"]
    assert rvc["project_value"] == pytest.approx(env["max"]["swing_mw"] / 250.0 * 100.0, rel=1e-4)
    assert rvc["verdict"] == "warn" and "upper bound" in rvc["note"] and "X/R" in rvc["note"]
    assert out["envelope"] is True


def test_the_dynamic_requirements_are_always_unknown(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter", dc_load={})
    out = G.grid_readiness(d, n, PROFILE)
    dyn = [r for r in out["rows"] if r["id"].startswith("dynamic:")]
    assert {r["id"] for r in dyn} == {"dynamic:frequency_ranges", "dynamic:rocof", "dynamic:frt",
                                       "dynamic:lfsm_o"}
    assert all(r["verdict"] == "unknown" and "EMT" in r["note"] for r in dyn)
    gen = G.grid_readiness(d, n, "generic_assumed")
    assert not [r for r in gen["rows"] if r["id"].startswith("dynamic:")]


def test_no_row_passes_on_a_missing_input(tmp_path):
    """No network, no sidecar, no envelope, no study: nothing may pass."""
    d = tmp_path / "bare"
    d.mkdir()
    out = G.grid_readiness(d, None, PROFILE)
    assert out["rows"] and all(r["verdict"] == "unknown" for r in out["rows"]
                               if not r["id"].startswith("dynamic:"))
    assert not [r for r in out["rows"] if r["verdict"] == "pass"]


def test_a_sidecar_removed_makes_its_rows_unknown(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter")
    (d / "island_config.json").unlink()
    rows = _rows(G.grid_readiness(d, n, PROFILE))
    for rid in ("reactive_range", "frequency_support", "grid_former"):
        assert rows[rid]["verdict"] == "unknown" and "island requirements" in rows[rid]["note"], rid


def test_an_unknown_profile_is_reported_and_its_rows_unknown(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter", dc_load={})
    out = G.grid_readiness(d, n, "no_such_code")
    assert "unknown grid-code profile" in out["profile"]["error"]
    rows = _rows(out)
    assert rows["rapid_voltage_change"]["verdict"] == "unknown"
    assert rows["reactive_range"]["verdict"] == "unknown"


def test_a_user_built_project_gets_the_generic_placeholders_marked_assumed(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter", dc_load={})
    meta = json.loads((d / "eh_template.json").read_text())
    meta["ledger"] = []
    (d / "eh_template.json").write_text(json.dumps(meta))
    rows = _rows(G.grid_readiness(d, n, PROFILE))
    assert rows["ramp_rate"]["limit"] == pytest.approx(G.GENERIC_RAMP_SHARE_PER_MIN * 40.0)
    assert "generic placeholder" in rows["ramp_rate"]["source"] and rows["ramp_rate"]["assumed"]
    assert rows["oscillation"]["limit"] == G.GENERIC_OSCILLATION_PCT
    assert rows["lole"]["verdict"] == "unknown" and rows["lole"]["limit"] is None


# ── units and edges ─────────────────────────────────────────────────────────


def test_one_mw_per_second_is_sixty_mw_per_minute():
    assert G.mw_per_s_to_mw_per_min(1.0) == 60.0


def _with_envelope(tmp_path, *, dp_dt=None, swing=None, amp=None, period=None):
    d, n = _project(tmp_path, "eh_datacenter", dc_load={})
    meta = json.loads((d / "eh_template.json").read_text())
    m = meta["dc_load"]["envelope"]["max"]
    for key, v in (("max_dp_dt_mw_per_s", dp_dt), ("swing_mw", swing),
                   ("oscillation_amplitude_mw", amp), ("oscillation_period_s", period)):
        if v is not None:
            m[key] = v
    (d / "eh_template.json").write_text(json.dumps(meta))
    return d, n


@pytest.mark.parametrize("dp_dt, verdict", [(4.0 / 60.0, "pass"), (4.0 / 60.0 * 1.001, "fail")])
def test_the_ramp_row_at_its_limit(tmp_path, dp_dt, verdict):
    d, n = _with_envelope(tmp_path, dp_dt=dp_dt)
    assert _rows(G.grid_readiness(d, n, PROFILE))["ramp_rate"]["verdict"] == verdict


@pytest.mark.parametrize("swing, verdict", [(7.5, "pass"), (7.51, "warn")])
def test_the_voltage_change_row_at_the_profiles_limit(tmp_path, swing, verdict):
    d, n = _with_envelope(tmp_path, swing=swing)             # 3 % of 250 MVA = 7.5 MW
    assert _rows(G.grid_readiness(d, n, PROFILE))["rapid_voltage_change"]["verdict"] == verdict


@pytest.mark.parametrize("amp, period, verdict", [
    (0.2, 10.0, "pass"),            # 0.4 MW p-p = 1 % of 40 MW, at 0.1 Hz
    (0.21, 10.0, "fail"),
    (5.0, 20.0, "pass"),            # 0.05 Hz: outside the band of concern
])
def test_the_oscillation_row(tmp_path, amp, period, verdict):
    d, n = _with_envelope(tmp_path, amp=amp, period=period)
    assert _rows(G.grid_readiness(d, n, PROFILE))["oscillation"]["verdict"] == verdict


@pytest.mark.parametrize("sk, verdict", [(90.0, "pass"), (87.0, "warn")])
def test_the_scr_row_at_three(tmp_path, sk, verdict):
    d, n = _project(tmp_path, "eh_datacenter")
    n.buses.at["grid", "eh_sk_mva"] = sk                         # IBR 30 MVA: SCR 3.0 / 2.9
    row = _rows(G.grid_readiness(d, n, PROFILE))["scr"]
    assert row["verdict"] == verdict and row["assumed"]


def test_scr_without_a_fault_level_is_unknown(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter")
    n.buses.at["grid", "eh_sk_mva"] = float("nan")
    assert _rows(G.grid_readiness(d, n, PROFILE))["scr"]["verdict"] == "unknown"


# ── the peak check: one function for the 422 and the row ───────────────────


@pytest.mark.parametrize("tid", TIDS)
def test_the_peak_row_reads_the_same_check_as_the_create_time_refusal(tmp_path, tid):
    d, n = _project(tmp_path, tid)
    rows = [r for r in G.grid_readiness(d, n, PROFILE)["rows"] if r["id"].startswith("energization_peak")]
    checked = SP.peak_check(n, T.ISLAND_CONFIG.get(tid))
    if all(c["energized_mw"] <= 0 for c in checked):
        assert [r["verdict"] for r in rows] == ["unknown"] and "normally open" in rows[0]["note"]
        return
    assert [r["id"] for r in rows] == [f"energization_peak:{c['period']}" for c in checked]
    for r, c in zip(rows, checked):
        assert r["limit"] == pytest.approx(c["cap_mw"], abs=1e-3)
        assert r["project_value"] == pytest.approx(c["load_mw"], abs=1e-3)
        assert (r["verdict"] == "fail") == (c["headroom_mw"] < -1e-9)


def _one_period(import_mw=40.0, gen_pu=None, schedule=None, load=None):
    """A bare 48-hour site: one import Link, one load, no gensets."""
    snaps = pd.date_range("2030-01-01", periods=48, freq="h")
    n = pypsa.Network()
    n.set_snapshots(snaps)
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.buses["eh_poc"] = [True, False]
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=import_mw, efficiency=1.0)
    n.links["eh_role"] = "grid_import"
    n.add("Load", "demand", bus="site")
    n.loads_t.p_set = pd.DataFrame({"demand": load}, index=snaps)
    if gen_pu is not None:
        n.links_t.p_max_pu = pd.DataFrame({"grid_import": gen_pu}, index=snaps)
    if schedule is not None:
        n.links_t["eh_energization_mw"] = pd.DataFrame({"grid_import": schedule}, index=snaps)
    return n


def test_an_energization_step_inside_the_period_is_checked_hour_by_hour():
    """10 MW for the first day, 40 MW after; the 30 MW peak falls after the
    step. The period minimum (10 MW) would refuse it; hour by hour it fits."""
    load = np.r_[np.full(24, 8.0), np.full(24, 30.0)]
    n = _one_period(schedule=np.r_[np.full(24, 10.0), np.full(24, 40.0)], load=load)
    (row,) = SP.peak_check(n, None)
    assert row["headroom_mw"] == pytest.approx(2.0)                  # hour 0-23: 10 - 8
    assert row["load_mw"] == 8.0 and row["cap_mw"] == 10.0
    assert row["peak_mw"] == 30.0 and row["energized_max_mw"] == 40.0


def test_a_p_max_pu_dip_at_the_peak_hour_fails():
    """The Link is derated to half at the peak hour: the period's highest
    p_max_pu would pass it; hour by hour it does not fit."""
    load = np.full(48, 15.0)
    load[30] = 30.0
    pu = np.ones(48)
    pu[30] = 0.5
    n = _one_period(gen_pu=pu, load=load)
    (row,) = SP.peak_check(n, None)
    assert row["headroom_mw"] == pytest.approx(-10.0)
    assert row["binding_snapshot"].startswith("2030-01-02 06:00")
    assert row["load_mw"] == 30.0 and row["cap_mw"] == 20.0


def test_the_microgrids_open_tie_supplies_nothing():
    n = T.BUILDERS["eh_microgrid"]()
    assert all(r["energized_mw"] == 0.0 for r in SP.peak_check(n, T.ISLAND_CONFIG["eh_microgrid"]))


def test_the_peak_cap_takes_the_poc_links_losses():
    n = T.BUILDERS["eh_datacenter"]()
    (row,) = SP.peak_check(n, T.ISLAND_CONFIG["eh_datacenter"])
    assert row["delivered_mw"] == pytest.approx(40.0 * 0.995)
    assert row["cap_mw"] == pytest.approx(40.0 * 0.995 + 30.0)       # 4 × 10 MW gensets, N+1


# ── ride-through batteries in the peak check (DC 03) ────────────────────────


def _rt_site(load, import_mw=40.0, p_nom=20.0, max_hours=1.0, extendable=False, sustained_h=1.0,
             rts=("rt",), critical=None, eta=1.0):
    """A bare site with one ride-through battery; ``critical`` is the critical
    load's MW (a second Load), absent by default."""
    n = _one_period(import_mw=import_mw, load=np.asarray(load, dtype=float))
    n.add("StorageUnit", "rt", bus="site", p_nom=p_nom, max_hours=max_hours,
          efficiency_dispatch=eta, efficiency_store=eta, p_nom_extendable=extendable)
    units = {"rt": {"kind": "bess"}}
    if critical is not None:
        n.add("Load", "critical", bus="site", p_set=critical)
        units["critical"] = {"kind": "load", "critical": True}
    island = {"units": units,
              "requirements": {"sustained_h": {"value": sustained_h, "source": "assumed"}}}
    if rts is not None:
        island["requirements"]["ride_through_storage"] = list(rts)
    return n, island


def test_a_battery_does_not_cover_a_deficit_longer_than_its_energy():
    """20 MW / 20 MWh against a 15 MW shortfall for 3 h: it runs out after
    1.33 h, so the third hour falls short by the full 15 MW."""
    load = np.full(48, 30.0)
    load[10:13] = 55.0
    n, island = _rt_site(load)
    (row,) = SP.peak_check(n, island)
    assert row["headroom_mw"] == pytest.approx(-15.0)                # 15 + 5 MWh, then empty
    assert row["binding_snapshot"].startswith("2030-01-01 12:00")
    assert row["firm"][1]["mw"] == 0.0
    assert "20 MWh above the" in row["firm"][1]["rule"]


def test_the_outage_reserve_is_not_spent_on_the_peak():
    """A 20 MW critical load, no gensets, 1 h ride-through: R = 20 MWh = E,
    so nothing is free and the 15 MW shortfall stands."""
    load = np.full(48, 30.0)
    load[10] = 55.0
    n, island = _rt_site(load - 20.0, critical=20.0)
    (row,) = SP.peak_check(n, island)
    assert row["firm"][1]["mw"] == 0.0
    assert row["headroom_mw"] == pytest.approx(-15.0)
    assert "0 MWh above the 20 MWh outage reserve" in row["firm"][1]["rule"]


def test_the_battery_recharges_between_runs():
    """Two 1 h, 15 MW shortfalls a day apart, 20 MWh: the headroom between
    refills it (η = 0.9 both ways), so both are covered."""
    load = np.full(48, 30.0)
    load[[5, 29]] = 55.0
    n, island = _rt_site(load, eta=0.9)
    (row,) = SP.peak_check(n, island)
    assert row["headroom_mw"] == pytest.approx(0.0, abs=1e-9)
    assert row["firm"][1]["mw"] == pytest.approx(15.0)


def test_without_ride_through_storage_named_every_bess_is_ride_through():
    load = np.full(48, 30.0)
    load[10] = 50.0
    n, island = _rt_site(load, rts=None)
    assert SP.peak_check(n, island)[0]["headroom_mw"] == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("change, why", [
    ({"extendable": True}, "no committed ride-through battery"),
    ({"rts": ()}, "no committed ride-through battery"),
    ({"sustained_h": 0}, "no sustained ride-through time"),
])
def test_no_battery_shaves_without_a_committed_unit_or_a_ride_through_time(change, why):
    load = np.full(48, 30.0)
    load[10] = 50.0
    n, island = _rt_site(load, **change)
    (row,) = SP.peak_check(n, island)
    assert row["firm"][1]["mw"] == 0.0 and why in row["firm"][1]["rule"]
    assert row["headroom_mw"] == pytest.approx(-10.0)
    assert "batteries" not in SP.describe(row)
    (row,) = SP.peak_check(n, None)
    assert row["firm"][1]["rule"] == "no island sidecar: no battery is counted as firm"


def test_a_battery_built_after_the_period_shaves_nothing_in_it():
    load = np.full(48, 30.0)
    load[10] = 50.0
    n, island = _rt_site(load)
    n.storage_units.at["rt", "build_year"] = 2035
    (row,) = SP.peak_check(n, island)                               # the 2030 period
    assert row["firm"][1]["mw"] == 0.0 and row["headroom_mw"] == pytest.approx(-10.0)


@pytest.mark.parametrize("tid, product", [("eh_h2_hub", "h2_offtake"), ("eh_h2_hub_staged", "h2_offtake"),
                                          ("eh_ammonia_staged", "nh3_delivery")])
def test_a_product_offtake_is_not_electric_peak_load(tid, product):
    """The H2 and NH3 offtakes sit on product buses: the electric peak check
    counts the AC process load only."""
    n = T.BUILDERS[tid]()
    assert product in n.loads.index and SP.electric_loads(n) == ["process_load"]
    for row in SP.peak_check(n, T.ISLAND_CONFIG.get(tid)):
        mask = [str(s).startswith(row["period"]) or row["period"] in str(s) for s in n.snapshots]
        assert row["peak_mw"] == pytest.approx(float(n.loads_t.p_set["process_load"][mask].max()))


def test_a_load_on_an_unknown_electric_carrier_counts_and_an_offtake_does_not():
    """A Load on a "low voltage" bus is electric (an unknown carrier counts);
    one on an H2 bus is an offtake, left out and named in the row."""
    n = _one_period(load=np.full(48, 10.0))
    n.add("Bus", "lv", carrier="low voltage")
    n.add("Bus", "h2", carrier="H2")
    n.add("Load", "homes", bus="lv", p_set=5.0)
    n.add("Load", "h2_offtake", bus="h2", p_set=25.0)
    assert SP.electric_loads(n) == ["demand", "homes"]
    (row,) = SP.peak_check(n, None)
    assert row["peak_mw"] == pytest.approx(15.0)
    assert row["not_electric"] == ["h2_offtake on h2 (H2)"]


def test_the_h2_hubs_site_battery_and_the_data_centres_cap():
    """The H2 hub's 20 MW / 40 MWh site battery delivers 0.95 × 40 = 38 MWh;
    its critical process load, with no genset, needs its peak for the 1 h
    ride-through (43.65 MWh), so nothing is free and the cap is the import.
    The data-centre campus names only its extendable bess_new, so its cap
    stays the import plus the gensets."""
    n = T.BUILDERS["eh_h2_hub"]()
    (row,) = SP.peak_check(n, T.ISLAND_CONFIG["eh_h2_hub"])
    reserve = float(n.loads_t.p_set["process_load"].max()) * 1.0
    assert reserve > 38.0
    assert row["firm"][1]["mw"] == 0.0
    assert row["cap_mw"] == pytest.approx(row["delivered_mw"])
    assert f"20 MW / 38 MWh, 0 MWh above the {reserve:.4g} MWh outage reserve" in row["firm"][1]["rule"]
    n = T.BUILDERS["eh_datacenter"]()
    (row,) = SP.peak_check(n, T.ISLAND_CONFIG["eh_datacenter"])
    assert row["firm"][1]["mw"] == 0.0
    assert row["cap_mw"] == pytest.approx(40.0 * 0.995 + 30.0)


def _at_cap_spec(tid, it_mw):
    return {"phases": [{"cod": "2030-01-01", "it_mw": it_mw}]}


def test_at_the_cap_the_refusal_and_the_row_agree(tmp_path):
    """Size the single-year campus's IT load so its peak sits just under and
    just over the cap: the 422 and the scorecard flip together."""
    tid = "eh_datacenter"

    def spec(it):
        return DataCentreLoadSpec.model_validate({**T.dc_load_preset(tid), **_at_cap_spec(tid, it),
                                                  "ai_workload": {}})

    def peak(it):
        (row,) = SP.peak_check(T.BUILDERS[tid](load_spec=spec(it)), T.ISLAND_CONFIG[tid])
        return row

    lo, hi = peak(36.0), peak(60.0)                    # the peak is affine in IT MW
    slope = (hi["load_mw"] - lo["load_mw"]) / 24.0
    at_cap = 36.0 + (lo["cap_mw"] - lo["load_mw"]) / slope
    for it, refused in ((at_cap - 0.01, False), (at_cap + 0.01, True)):
        raw = _at_cap_spec(tid, it)
        if refused:
            with pytest.raises(DcLoadError, match=r"above the 40 MW energized import \(× 0.995 efficiency\)"):
                T.dc_load_spec(tid, raw)
        else:
            T.dc_load_spec(tid, raw)
        d = tmp_path / f"cap_{refused}"
        d.mkdir()
        T.write_sidecars(d, tid)
        out = G.grid_readiness(d, T.BUILDERS[tid](load_spec=spec(it)), PROFILE)
        (row,) = [r for r in out["rows"] if r["id"].startswith("energization_peak")]
        assert (row["verdict"] == "fail") is refused


@pytest.mark.parametrize("tid, raw, refused_period", [
    ("eh_datacenter_staged", {"phases": [{"cod": f"{y}-01-01", "it_mw": 60.0} for y in (2027, 2030, 2035)]},
     "2027"),
    ("eh_datacenter_staged", {}, None),
    ("eh_datacenter", {"phases": [{"cod": "2030-01-01", "it_mw": 80.0}]}, "2030"),
    ("eh_datacenter", {}, None),
])
def test_the_refusal_and_the_row_agree_on_both_data_center_templates(tmp_path, tid, raw, refused_period):
    if refused_period:
        with pytest.raises(DcLoadError, match=f"the {refused_period} peak is"):
            T.dc_load_spec(tid, raw)
    else:
        T.dc_load_spec(tid, raw)
    spec = DataCentreLoadSpec.model_validate({**T.dc_load_preset(tid), **raw, "ai_workload": {}})
    d = tmp_path / "p"
    d.mkdir()
    T.write_sidecars(d, tid)
    rows = [r for r in G.grid_readiness(d, T.BUILDERS[tid](load_spec=spec), PROFILE)["rows"]
            if r["id"].startswith("energization_peak")]
    failed = [r["id"].split(":")[1] for r in rows if r["verdict"] == "fail"]
    assert (failed[:1] or [None])[0] == refused_period


def test_headroom_below_the_in_hour_step_is_a_warning(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter", dc_load={})
    (row,) = SP.peak_check(n, T.ISLAND_CONFIG["eh_datacenter"])
    uplift = G._in_hour_uplift_mw(G._Inputs(d, n, None, None, None, None), row["period"])
    assert uplift and uplift > 0
    n.links.at["grid_import", "p_nom"] -= (row["headroom_mw"] - uplift / 2) / 0.995
    (peak,) = [r for r in G.grid_readiness(d, n, PROFILE)["rows"] if r["id"].startswith("energization_peak")]
    assert peak["verdict"] == "warn" and "in-hour" in peak["note"]


# ── reactive range, support, grid former, ride-through ──────────────────────


def _edit_island(d, fn):
    raw = json.loads((d / "island_config.json").read_text())
    fn(raw)
    (d / "island_config.json").write_text(json.dumps(raw))


def test_the_reactive_range_counts_gfm_and_gfl_qu_units_at_rated_p(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter")
    row = _rows(G.grid_readiness(d, n, PROFILE))["reactive_range"]
    # bess_new (gfm_droop) is extendable and unsized: only the 4 MW PV counts so far
    assert row["project_value"] == pytest.approx(4.0 * math.tan(math.acos(0.95)), rel=1e-4)
    assert row["limit"] == pytest.approx(0.48 * 40.0)
    assert row["verdict"] == "unknown" and "bess_new" in row["note"]
    n.storage_units.at["bess_new", "p_nom_extendable"] = False
    n.storage_units.at["bess_new", "p_nom"] = 60.0
    row = _rows(G.grid_readiness(d, n, PROFILE))["reactive_range"]
    assert row["project_value"] == pytest.approx(64.0 * math.tan(math.acos(0.95)), rel=1e-4)
    assert row["verdict"] == "pass"
    n.storage_units.at["bess_new", "p_nom"] = 10.0
    assert _rows(G.grid_readiness(d, n, PROFILE))["reactive_range"]["verdict"] == "warn"


def test_a_grid_former_on_pv_is_not_credited(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter")
    n.storage_units.at["bess_new", "p_nom_extendable"] = False
    n.storage_units.at["bess_new", "p_nom"] = 0.0
    _edit_island(d, lambda raw: raw["units"]["rooftop_pv"].pop("qu_points") and raw["units"]["rooftop_pv"].update(
        {"control": "gfm_droop", "droop_pct": {"value": 4.0, "source": "assumed"},
         "q_droop_pct": {"value": 4.0, "source": "assumed"}, "tau_f_s": {"value": 0.1, "source": "assumed"},
         "i_max_pu": {"value": 1.2, "source": "assumed"}, "k_gfm_min": {"value": 1.0, "source": "assumed"}}))
    row = _rows(G.grid_readiness(d, n, PROFILE))["reactive_range"]
    assert row["project_value"] == 0.0 and row["verdict"] == "warn"


def test_no_frequency_service_is_a_warning(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter")

    def gfl(raw):
        raw["units"]["bess_new"] = {"kind": "bess", "control": "gfl_pq",
                                    "pf_rated": {"value": 0.95, "source": "assumed"}}
    _edit_island(d, gfl)
    assert _rows(G.grid_readiness(d, n, PROFILE))["frequency_support"]["verdict"] == "warn"


def test_the_grid_former_applies_only_when_the_site_is_asked_to_island(tmp_path):
    d, n = _project(tmp_path, "eh_h2_hub")

    def no_former(raw):
        for name, u in raw["units"].items():
            if u["kind"] == "bess":
                raw["units"][name] = {"kind": "bess", "control": "gfl_pq",
                                      "pf_rated": {"value": 0.95, "source": "assumed"}}
    _edit_island(d, no_former)
    assert _rows(G.grid_readiness(d, n, PROFILE))["grid_former"]["verdict"] == "fail"
    (d / "island_config.json").unlink()
    row = _rows(G.grid_readiness(d, n, PROFILE))["grid_former"]
    assert row["verdict"] == "unknown" and "Not assessed" in row["note"]


@pytest.mark.parametrize("status, verdict", [(None, "unknown"), ("ok", "pass"),
                                             ("infeasible", "fail"), ("refused", "fail")])
def test_ride_through_reads_the_stored_island_study(tmp_path, status, verdict):
    d, n = _project(tmp_path, "eh_datacenter")
    if status is not None:
        (d / "campus_electrical").mkdir()
        (d / "campus_electrical" / "island_study.json").write_text(json.dumps(
            {"variants": {"baseline": {"status": "ok"}, "ups_bridged": {"status": status}}}))
    row = _rows(G.grid_readiness(d, n, PROFILE))["ride_through:ups_bridged"]
    assert row["verdict"] == verdict and row["assumed"]
    assert "600" in row["limit"]


# ── LOLE ────────────────────────────────────────────────────────────────────


def _study(lole_h_per_year, ci, years=1.0, target=None):
    payload = {"lole_h_per_year": lole_h_per_year, "lole_ci": ci, "horizon_years": years}
    if target is not None:
        payload["target_lole_h"] = target
    return {"status": "done", "report": {"sections": {"certification": {"status": "ok",
                                                                        "payload": payload}}}}


@pytest.mark.parametrize("study, edited, verdict", [
    (None, None, "unknown"),
    (_study(1.0, [0.0, 2.0]), False, "pass"),
    (_study(1.0, [0.0, 4.0]), False, "warn"),        # inconclusive: the CI straddles the goal
    (_study(4.0, [3.5, 5.0]), False, "fail"),        # the CI's lower bound is above the goal
    (_study(1.0, []), False, "warn"),                # no CI: never a pass
    (_study(1.0, [0.0, 2.0]), True, "unknown"),      # stale: edited since the study
    (_study(1.0, [0.0, 2.0]), None, "unknown"),      # revision unknown
    (_study(1.0, [0.0, 4.0], years=2.0), False, "pass"),  # CI 4 h over 2 years = 2 h/yr
    (_study(1.0, [0.0, 2.0], years=0.0), False, "unknown"),
])
def test_the_lole_row(tmp_path, study, edited, verdict):
    d, n = _project(tmp_path, "eh_datacenter")
    row = _rows(G.grid_readiness(d, n, PROFILE, eh_study=study, study_edited=edited))["lole"]
    assert row["verdict"] == verdict and row["limit"] == 3.0 and row["unit"] == "h/yr"
    if verdict != "unknown":
        assert row["project_value"]["horizon_years"] == study["report"]["sections"]["certification"][
            "payload"]["horizon_years"]
    if edited:
        assert "Stale" in row["note"]


def test_the_lole_goal_is_the_one_the_study_certified_against(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter")
    row = _rows(G.grid_readiness(d, n, PROFILE, eh_study=_study(1.0, [0.0, 2.0], target=1.5),
                                 study_edited=False))["lole"]
    assert row["limit"] == 1.5 and not row["assumed"] and row["verdict"] == "warn"
    assert "Scenarios" in row["source"]


def test_the_h2_hub_sets_no_lole_goal(tmp_path):
    d, n = _project(tmp_path, "eh_h2_hub")
    row = _rows(G.grid_readiness(d, n, PROFILE, eh_study=_study(1.0, [0.0, 2.0]),
                                 study_edited=False))["lole"]
    assert row["verdict"] == "unknown" and row["limit"] is None


# ── the route and the copilot tool ──────────────────────────────────────────


@pytest.fixture
def hub(_auth_db, seeded_identity):
    from db.models import User
    from services import project_registry

    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        project = project_registry.create_root(db, user, "Readiness Hub")
        d = project_registry.ensure_project_dir(project)
        T.BUILDERS["eh_datacenter"]().export_to_netcdf(str(d / "network.nc"))
        T.write_sidecars(d, "eh_datacenter")
        yield project


def test_the_route_serves_the_scorecard(client, hub):
    r = client.get("/api/campus-electrical/Readiness Hub/grid-readiness")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile"]["id"] == "eu_rfg_dcc_ce" and body["template_id"] == "eh_datacenter"
    assert {"scr", "ramp_rate", "lole"} <= {row["id"] for row in body["rows"]}
    r = client.get("/api/campus-electrical/Readiness Hub/grid-readiness",
                   params={"profile": "generic_assumed"})
    assert r.status_code == 200 and r.json()["profile"]["id"] == "generic_assumed"
    r = client.get("/api/campus-electrical/Readiness Hub/grid-readiness", params={"profile": "nope"})
    assert r.status_code == 422
    assert r.json()["detail"]["error_kind"] == "unknown_grid_code_profile"


def test_the_copilot_tool_is_a_read_service_call(hub, monkeypatch):
    from services import chat_tools
    from services.chat_tools_schema import TOOL_ROUTES, TOOLS, safety_tier_for

    assert "campus_grid_readiness" in {t["name"] for t in TOOLS}
    assert TOOL_ROUTES["campus_grid_readiness"] == ["_service_call_"]
    assert safety_tier_for("campus_grid_readiness") == "read"
    desc = next(t["description"] for t in TOOLS if t["name"] == "campus_grid_readiness")
    assert "never a pass" in desc and "assumed" in desc
    calls = []
    monkeypatch.setattr(G, "grid_readiness_for", lambda p, prof: calls.append((p.name, prof)) or {"ok": 1})
    assert chat_tools.DISPATCHERS["campus_grid_readiness"](project_id="Readiness Hub",
                                                    profile="generic_assumed") == {"ok": 1}
    assert calls == [("Readiness Hub", "generic_assumed")]


RAW_IDS = ("eh_", "/api/", "p_max_pu", "p_nom", "island_config", "sidecar", "dc_load", "ups_bridged",
           "IBR", "pf_rated", "offered_services", "gridspine", "rvc_limit_pct", "q_range_demand",
           "eu_rfg_dcc_ce", "generic_assumed", "DC 0", "has_grid_former", "_mva")


@pytest.mark.parametrize("tid, dc_load", [(t, None) for t in TIDS] + [(t, {}) for t in DC_IDS])
def test_the_panel_text_is_in_plain_words(tmp_path, tid, dc_load):
    """What the panel and the report show (requirement, limit, project value,
    source, note) carries no attribute ids, file names, API paths, profile or
    scenario ids (UX review, checklist 6.9). Asset names are the user's own and
    stay. An unknown row's note, shown first, starts as a sentence."""
    d, n = _project(tmp_path, tid, dc_load=dc_load)
    (d / "campus_electrical").mkdir()
    (d / "campus_electrical" / "island_study.json").write_text(json.dumps(
        {"variants": {"ups_bridged": {"status": "infeasible"}, "seamless": {"status": "ok"}}}))
    for r in G.grid_readiness(d, n, PROFILE)["rows"]:
        text = " ".join(str(r[k]) for k in ("requirement", "limit", "project_value", "source", "note")
                        if r[k] is not None)
        text += " " + str((r.get("after_battery") or {}).get("note", ""))     # DC 03
        for word in RAW_IDS:
            assert word not in text, (r["id"], word, text)
        if r["verdict"] == "unknown":
            assert r["note"][0].isupper(), (r["id"], r["note"])


# ── after mitigation (DC 03) ────────────────────────────────────────────────


def _envelope_rows(tmp_path, tid="eh_datacenter", dc_load=None, edit=None):
    d, n = _project(tmp_path, tid, dc_load=dc_load)
    if edit:
        edit(n)
    out = G.grid_readiness(d, n, PROFILE)
    return out, {k: v for k, v in _rows(out).items() if k in G._AFTER_IDS}


def test_the_campus_ups_walks_the_step_in_too_fast_for_the_ramp_limit(tmp_path):
    """The 40 MW / 10 MWh UPS keeps 34.5 MW × 600 s = 5.75 MWh for its bridge
    and carries the whole 23.4 MW step, but its rectifier walks it onto the
    grid over 15 s: 23.4·60/15 = 93.6 MW/min, above the 4 MW/min limit. The
    voltage change and the oscillation pass after it. The rows' own
    verdicts stay before mitigation."""
    out, rows = _envelope_rows(tmp_path, dc_load={})
    assert {k: r["verdict"] for k, r in rows.items()} == {
        "ramp_rate": "fail", "oscillation": "fail", "rapid_voltage_change": "warn"}
    ramp = rows["ramp_rate"]["after_battery"]
    assert ramp["value"] == pytest.approx(93.6) and ramp["verdict"] == "fail"
    assert "walks 23.4 MW in over 15 s" in ramp["note"]
    assert rows["oscillation"]["after_battery"]["value"] == 0.0
    assert rows["rapid_voltage_change"]["after_battery"]["value"] == 0.0
    assert rows["oscillation"]["after_battery"]["verdict"] == "pass"
    assert rows["rapid_voltage_change"]["after_battery"]["verdict"] == "pass"
    assert rows["ramp_rate"]["after_battery"]["units"] == ["ups_battery"]
    assert "before mitigation" in rows["ramp_rate"]["note"]
    assert out["summary"]["pass_after_mitigation"] == 2
    assert out["summary"]["red_flags"] == 3                         # the summary stays before mitigation


def test_a_small_ups_covers_part_of_the_step(tmp_path):
    """A 10 MW UPS walks 10 of the 23.4 MW in over 15 s (40 MW/min); the
    other 13.4 MW reach the grid at the envelope's rate, so the ramp fails and
    the voltage change warns after it."""
    def shrink(n):
        n.storage_units.at["ups_battery", "p_nom"] = 10.0
        n.storage_units.at["ups_battery", "max_hours"] = 2.0
    _, rows = _envelope_rows(tmp_path, dc_load={}, edit=shrink)
    rvc = rows["rapid_voltage_change"]["after_battery"]
    assert rvc["value"] == pytest.approx(round(13.4 / 250.0 * 100.0, 4))
    assert rvc["verdict"] == "warn"                                  # 5.36 % above the 3 % limit
    ramp = rows["ramp_rate"]["after_battery"]
    assert ramp["value"] == pytest.approx(40.0 + 13.4 / 23.4 * 1404.0, rel=1e-6)
    assert ramp["verdict"] == "fail"


def test_without_a_battery_or_ups_there_is_no_after_value(tmp_path):
    def drop(n):
        n.remove("StorageUnit", "ups_battery")
    out, rows = _envelope_rows(tmp_path, dc_load={}, edit=drop)
    assert not any("after_battery" in r for r in rows.values())
    assert out["summary"]["pass_after_mitigation"] == 0


@pytest.mark.parametrize("tid", TIDS)
def test_without_the_ai_profile_there_is_no_after_value(tmp_path, tid):
    _, rows = _envelope_rows(tmp_path, tid)
    assert not any("after_battery" in r for r in rows.values())


def test_the_after_value_names_each_unit_its_kind_latency_and_asset(tmp_path):
    _, rows = _envelope_rows(tmp_path, dc_load={})
    (u,) = rows["ramp_rate"]["after_battery"]["unit_detail"]
    assert u == {"unit": "ups_battery", "kind": "ups", "latency_s": None, "walk_in_s": 15.0,
                 "basis": "UPS, walks in over 15 s",
                 "target": {"section": "assets", "panel": None, "component": "StorageUnit",
                            "entity": "ups_battery", "field": None}}
    assert "'ups_battery' walks in over 15 s (UPS, assumed)" in rows["ramp_rate"]["after_battery"]["note"]


def test_the_counts_read_the_row_verdict_never_the_after_value(tmp_path):
    out, _ = _envelope_rows(tmp_path, dc_load={})
    counts = {v: sum(r["verdict"] == v for r in out["rows"]) for v in G.VERDICTS}
    assert {v: out["summary"][v] for v in G.VERDICTS} == counts
    assert out["summary"]["red_flags"] == counts["fail"] + counts["warn"]


def test_the_after_value_survives_every_consumer(tmp_path, monkeypatch):
    """The key round-trips through JSON (the route and the assistant tool
    return the dict as is), the assistant tool's dispatcher and the report
    chapter, which reads the rows it lists and ignores the rest."""
    from services import chat_tools
    from services.reports import chapters as C

    out, rows = _envelope_rows(tmp_path, dc_load={})
    back = json.loads(json.dumps(out))
    assert back["rows"] == out["rows"] and "after_battery" in _rows(back)["ramp_rate"]
    monkeypatch.setattr(G, "grid_readiness_for", lambda p, prof: out)
    monkeypatch.setattr(chat_tools, "_acting", lambda: __import__("contextlib").nullcontext((None, None)))
    monkeypatch.setattr(chat_tools, "_gridspine_project", lambda db, user, pid: pid)
    assert chat_tools.DISPATCHERS["campus_grid_readiness"](project_id="p") is out
    payload = C.grid_readiness_payload(out)
    assert [r["verdict"] for r in payload["rows"]] == [r["verdict"] for r in out["rows"]]
    C.grid_readiness_table(payload, "/sections/grid_readiness/payload")
    C.grid_readiness_disclosures(payload)


# ── two-tier rapid voltage change and RMS-study wording (DC 10a) ───────────


def _two_tier_profile(tmp_path, infrequent_pct):
    """A project profile: the EU one plus an infrequent RVC tier (generic value)."""
    from gridspine.templates.grid_codes import load_grid_code

    p = load_grid_code(PROFILE, raw=True)
    p["rvc_limit_infrequent_pct"] = {"value": infrequent_pct, "clause": "generic test clause", "source": "code"}
    d = tmp_path / "codes"
    d.mkdir()
    (d / "two_tier.yaml").write_text(json.dumps(p))
    return d


def test_without_an_infrequent_tier_its_row_is_unknown_with_no_limit_set(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter")
    row = _rows(G.grid_readiness(d, n, PROFILE))["rapid_voltage_change_infrequent"]
    assert row["verdict"] == "unknown" and row["limit"] is None and row["project_value"] is None
    assert "no limit set" in row["note"].lower()


@pytest.mark.parametrize("pct, verdict", [(100.0, "pass"), (5.0, "warn")])
def test_the_infrequent_tier_is_the_connection_over_the_fault_level(tmp_path, pct, verdict):
    d, n = _project(tmp_path, "eh_datacenter")
    out = G.grid_readiness(d, n, "two_tier", profile_dirs=(_two_tier_profile(tmp_path, pct),))
    rows = _rows(out)
    row = rows["rapid_voltage_change_infrequent"]
    conn = sum(float(n.links.at[link, "p_nom"]) for link in SP.import_links(n))
    assert conn / 250.0 * 100.0 > 5.0                                     # so the 5 % tier warns
    assert row["project_value"] == pytest.approx(conn / 250.0 * 100.0, rel=1e-4)    # Sk 250 MVA
    assert row["verdict"] == verdict and row["limit"] == pct
    assert "connection capacity / fault level × 100" in row["note"]
    # the frequent tier is the profile's rvc_limit_pct, unchanged
    assert rows["rapid_voltage_change"]["limit"] == 3.0


def test_the_dynamic_requirements_need_an_rms_study(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter")
    dyn = [r for r in G.grid_readiness(d, n, PROFILE)["rows"] if r["id"].startswith("dynamic:")]
    assert dyn and all(r["note"].startswith("Needs RMS study") and r["verdict"] == "unknown" for r in dyn)
