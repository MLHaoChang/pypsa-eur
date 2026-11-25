"""
DC grid readiness 01: the AI-training load profile in the data-center
templates, and its dynamic envelope.

The profile is opt-in (a create body's ``dc_load``); without it both data
center templates keep today's load bit for bit.
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

from models.flex_archetypes import AiWorkloadSpec, DataCentreLoadSpec, DcPhase
from services import dc_load_profile as P

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

DC_IDS = ("eh_datacenter", "eh_datacenter_staged")


def _master_p_set(staged: bool) -> pd.DataFrame:
    """The data center's load as master 67a90b5e builds it (before 01),
    re-derived here in the running environment: a pinned hash is not
    portable, since exp/normal differ in the last bit across numpy builds."""
    rng = np.random.default_rng(11)
    flat = pd.DataFrame({
        "it_load": 36.0 + rng.normal(0.0, 0.6, T.HOURS),
        "cooling": T._day_shape(8.0, 14.0),
        "offices": T._day_shape(0.4, 1.6, peak_hour=12.0, width=3.0),
    }, index=T.SNAPSHOTS)
    if not staged:
        return flat
    years = [y for y, _it, _pue in T.DC_PHASES]
    out = pd.DataFrame(np.tile(flat.to_numpy(), (len(years), 1)), columns=flat.columns,
                       index=pd.MultiIndex.from_product([years, T.SNAPSHOTS]))
    for y, it_mw, _pue in T.DC_PHASES:
        for load in ("it_load", "cooling"):
            out.loc[y, load] = (out.loc[y, load] * (it_mw / 36.0)).to_numpy()
    return out


def _spec(**kw) -> DataCentreLoadSpec:
    base = dict(phases=[DcPhase(cod=date(2028, 1, 1), it_mw=40.0, rack_kw=130.0)],
                utilisation="ai_training", pue_at_design=1.25, redundancy="N+1",
                critical_share=0.8, curtailable_share=0.1, ai_workload=AiWorkloadSpec())
    return DataCentreLoadSpec(**{**base, **kw})




# ── the default build is unchanged ──────────────────────────────────────────


@pytest.mark.parametrize("tid", DC_IDS)
def test_without_the_opt_in_the_load_is_bit_for_bit_unchanged(tid):
    got = T.BUILDERS[tid]().loads_t.p_set
    want = _master_p_set(tid == "eh_datacenter_staged")
    assert list(got.columns) == list(want.columns)
    assert np.array_equal(got.to_numpy(dtype=float), want.to_numpy(dtype=float))   # bit for bit


def test_existing_spec_json_still_round_trips():
    old = {"phases": [{"cod": "2028-01-01", "it_mw": 40.0}], "pue_at_design": 1.2,
           "redundancy": "N+1", "critical_share": 0.7, "curtailable_share": 0.3}
    m = DataCentreLoadSpec.model_validate(old)
    assert m.ai_workload is None and m.phases[0].rack_kw is None
    assert DataCentreLoadSpec.model_validate_json(m.model_dump_json()) == m


# ── the hourly profile ──────────────────────────────────────────────────────


def test_with_no_slope_facility_energy_is_it_times_utilisation_times_pue():
    spec = _spec(pue_temperature_slope_per_k=0.0)
    u = P.utilisation(spec, T.SNAPSHOTS)
    facility = P.hourly_profile(spec, T.SNAPSHOTS, 40.0)
    assert facility.sum() == pytest.approx(40.0 * u.sum() * 1.25, rel=1e-12)


def test_utilisation_is_the_checkpoint_duty_mean_and_the_idle_hours():
    w = AiWorkloadSpec(full_share=0.9, idle_share=0.3, checkpoint_interval_s=1000.0,
                       checkpoint_duration_s=100.0, idle_hours_per_day=2, idle_start_hour=23)
    u = P.utilisation(_spec(ai_workload=w), T.SNAPSHOTS)
    train = 0.9 - 0.6 * 0.1
    hod = T.SNAPSHOTS.hour
    assert np.allclose(u[(hod == 23) | (hod == 0)], 0.3)
    assert np.allclose(u[(hod != 23) & (hod != 0)], train)


def test_pue_rises_only_above_the_design_temperature():
    spec = _spec(pue_temperature_slope_per_k=0.02, pue_design_temp_c=20.0)
    assert P.pue(spec, np.array([5.0, 20.0, 25.0])) == pytest.approx([1.25, 1.25, 1.35])


def test_ambient_follows_the_snapshot_dates_not_a_fixed_month():
    spec = _spec()
    jan = pd.date_range("2030-01-15", periods=24, freq="h")
    jul = pd.date_range("2030-07-19", periods=24, freq="h")
    assert P.ambient_c(spec, jan).mean() < P.ambient_c(spec, jul).mean() - 10.0


def test_a_commercial_timezone_maps_the_diurnal_terms_on_the_local_clock():
    spec = _spec()
    snaps = pd.date_range("2030-07-19", periods=24, freq="h")       # naive UTC
    utc = P.ambient_c(spec, snaps)
    oslo = P.ambient_c(spec, snaps, timezone="Europe/Oslo")           # UTC+2 in July
    assert int(np.argmax(utc)) == 15 and int(np.argmax(oslo)) == 13
    assert P.time_basis(None) == "site clock (assumed)"
    assert "Europe/Oslo" in P.time_basis("Europe/Oslo")


# ── the dynamic envelope ────────────────────────────────────────────────────


def test_envelope_units_and_conventions():
    w = AiWorkloadSpec(full_share=0.95, idle_share=0.30, ramp_s=2.0,
                       oscillation_amplitude_share=0.1, oscillation_period_s=4.0)
    env = P.dynamic_envelope(_spec(ai_workload=w))
    ph = env["phases"][0]
    assert ph["swing_mw"] == pytest.approx(40.0 * 0.65)
    assert ph["max_dp_dt_mw_per_s"] == pytest.approx(13.0)
    assert ph["max_dp_dt_mw_per_min"] == pytest.approx(780.0)
    assert ph["checkpoint"]["depth_mw"] == pytest.approx(26.0)
    assert ph["checkpoint"]["dp_dt_mw_per_s"] == pytest.approx(13.0)
    osc = ph["oscillation"]
    assert osc["amplitude_mw"] == pytest.approx(4.0)                 # peak
    assert osc["peak_to_peak_mw"] == pytest.approx(8.0)
    assert osc["frequency_hz"] == pytest.approx(0.25)
    assert osc["max_dp_dt_mw_per_s"] == pytest.approx(2 * np.pi * 4.0 / 4.0)
    assert ph["rack_count"] == 308                                   # ceil(40 000 / 130)
    assert env["source"] == "assumed" and "before any UPS/BESS" in env["basis"]
    assert env["units"]["amplitude_mw"] == "MW (peak)"


def test_max_dp_dt_is_the_oscillation_slope_when_that_is_steeper():
    w = AiWorkloadSpec(ramp_s=10.0, oscillation_amplitude_share=0.2, oscillation_period_s=1.0)
    ph = P.dynamic_envelope(_spec(ai_workload=w))["phases"][0]
    assert ph["max_dp_dt_mw_per_s"] == pytest.approx(2 * np.pi * 8.0)   # > 26 MW / 10 s
    assert ph["checkpoint"]["dp_dt_mw_per_s"] == pytest.approx(2.6)


def test_envelope_per_phase_and_its_maximum():
    spec = _spec(phases=[DcPhase(cod=date(2027, 1, 1), it_mw=12.0, rack_kw=40.0),
                         DcPhase(cod=date(2030, 1, 1), it_mw=36.0, rack_kw=130.0)])
    env = P.dynamic_envelope(spec)
    assert [p["it_mw"] for p in env["phases"]] == [12.0, 36.0]
    assert env["max"]["swing_mw"] == env["phases"][1]["swing_mw"]


def test_every_ledger_row_is_assumed_with_a_source():
    rows = P.ledger(_spec(), None)
    assert rows and all(r["source"] == "assumed" and r["note"] for r in rows)
    assert any("site clock (assumed)" in r["value"] for r in rows)


# ── the templates with the opt-in ───────────────────────────────────────────


def _solve(n):
    probe = n.copy()
    multi = isinstance(n.snapshots, pd.MultiIndex)
    if multi:
        probe.snapshots.name = "snapshot"
    from services.adequacy.energization import apply_energization

    apply_energization(probe)
    return probe.optimize(solver_name="highs", multi_investment_periods=multi)


@pytest.mark.parametrize("tid", DC_IDS)
def test_the_template_solves_with_the_profile(tid):
    n = T.BUILDERS[tid](load_spec=T.dc_load_spec(tid, {}))
    assert not np.array_equal(n.loads_t.p_set.to_numpy(dtype=float),
                              _master_p_set(tid == "eh_datacenter_staged").to_numpy(dtype=float))
    assert _solve(n) == ("ok", "optimal")


@pytest.mark.parametrize("tid", DC_IDS)
def test_the_critical_share_holds_in_every_hour(tid):
    spec = T.dc_load_spec(tid, {"critical_share": 0.6})
    p = T.BUILDERS[tid](load_spec=spec).loads_t.p_set
    dc = p["it_load"] + p["cooling"]
    assert np.allclose(p["it_load"] / dc, 0.6)
    assert p["it_load"].max() == pytest.approx(0.6 * dc.max())
    assert p["offices"].equals(T.BUILDERS[tid]().loads_t.p_set["offices"])


def test_staged_periods_carry_each_phase_it_mw():
    spec = T.dc_load_spec("eh_datacenter_staged", {"pue_temperature_slope_per_k": 0.0})
    p = T.build_eh_datacenter_staged(load_spec=spec).loads_t.p_set
    dc = (p["it_load"] + p["cooling"]).groupby(level=0).sum()
    u = P.utilisation(spec, T.SNAPSHOTS, timezone="UTC").sum()
    for y, it, _pue in T.DC_PHASES:
        assert dc.loc[y] == pytest.approx(it * u * 1.3)


def test_readiness_runs_on_the_profile():
    from services.adequacy.eh_readiness import eh_readiness
    from services.adequacy.eh_study_runner import _PACK_FACTORY, apply_pack_overrides

    tid = "eh_datacenter"
    meta = T.TEMPLATE_META[tid]
    pack = apply_pack_overrides(_PACK_FACTORY[meta["recommended_archetype"]](),
                                meta["pack_overrides"])
    ready = eh_readiness(T.BUILDERS[tid](load_spec=T.dc_load_spec(tid, {})), pack,
                         budget_solves=30, voll=T.SOLVER_CONFIG["voll"],
                         dtc_attribution=meta["dtc_attribution"])
    assert ready["warnings"] == []


@pytest.mark.parametrize("raw, field", [
    ({"phases": [{"cod": "2027-01-01", "it_mw": 12.0}]}, "dc_load.phases"),
    ({"utilisation": "bogus"}, "dc_load.utilisation"),
    ({"critical_share": 0.0}, "dc_load.critical_share"),
    ({"pue_at_design": 0.9}, "dc_load.pue_at_design"),
    ({"ai_workload": {"idle_share": 0.99}}, "dc_load.ai_workload"),
])
def test_staged_refusals_name_the_field(raw, field):
    with pytest.raises(P.DcLoadError) as exc:
        T.dc_load_spec("eh_datacenter_staged", raw)
    assert exc.value.field == field


def test_a_peak_above_import_plus_fleet_is_refused_naming_the_period():
    raw = {"phases": [{"cod": "2027-01-01", "it_mw": 60.0}, {"cod": "2030-01-01", "it_mw": 60.0},
                      {"cod": "2035-01-01", "it_mw": 60.0}]}
    with pytest.raises(P.DcLoadError, match=r"the 2027 peak is .* above the 15 MW energized "
                                             r"import plus 30 MW of firm gensets \(genset N\+1"):
        T.dc_load_spec("eh_datacenter_staged", raw)


def test_firm_gensets_read_the_fleet_and_its_redundancy(monkeypatch):
    n = T.build_eh_datacenter()
    assert T.firm_genset_mw(n, "eh_datacenter")[0] == 30.0           # 4 × 10 MW, N+1
    n.generators.at["genset_1", "p_nom"] = 16.0                      # an edited fleet
    assert T.firm_genset_mw(n, "eh_datacenter")[0] == 30.0           # less the 16 MW unit
    cfg = json.loads(json.dumps(T.ISLAND_CONFIG["eh_datacenter"]))
    cfg["requirements"]["genset_redundancy_n"]["value"] = 0
    monkeypatch.setitem(T.ISLAND_CONFIG, "eh_datacenter", cfg)
    firm, rule = T.firm_genset_mw(n, "eh_datacenter")
    assert firm == 46.0 and "whole genset fleet" in rule


def test_single_year_needs_a_phase_in_service():
    with pytest.raises(P.DcLoadError, match="no phase is in service"):
        T.dc_load_spec("eh_datacenter", {"phases": [{"cod": "2031-01-01", "it_mw": 30.0}]})


# ── create from the template ────────────────────────────────────────────────


def _stage(tmp_path, monkeypatch, tid):
    from routers import projects as projects_router

    src = tmp_path / tid
    src.mkdir()
    T.BUILDERS[tid]().export_to_netcdf(str(src / "network.nc"))
    T.write_sidecars(src, tid)
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    (tmp_path / "eh_templates.py").write_text((BACKEND / "project_templates"
                                              / "eh_templates.py").read_text())


def test_create_with_dc_load_stores_the_envelope(client, tmp_path, monkeypatch, tmp_projects_dir):
    tid = "eh_datacenter_staged"
    _stage(tmp_path, monkeypatch, tid)
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": "dc_ai"},
                    json={"dc_load": {"phases": [
                        {"cod": "2027-01-01", "it_mw": 12.0, "rack_kw": 40.0},
                        {"cod": "2030-01-01", "it_mw": 24.0, "rack_kw": 130.0},
                        {"cod": "2035-01-01", "it_mw": 36.0, "rack_kw": 250.0}]}})
    assert r.status_code == 200, r.text
    name = r.json().get("name", "dc_ai")
    meta = client.get(f"/api/projects/{name}/eh_template").json()
    assert meta["recommended_archetype"] == "weak_flexible"          # the template's meta kept
    block = meta["dc_load"]
    assert [p["rack_kw"] for p in block["envelope"]["phases"]] == [40.0, 130.0, 250.0]
    assert "UTC" in block["time_basis"]
    (tmp_path / "read_back").mkdir()
    (tmp_path / "read_back" / "eh_template.json").write_text(json.dumps(meta))
    assert P.read_envelope(tmp_path / "read_back") == block["envelope"]
    assert P.read_envelope(tmp_path) is None                         # no file
    loads = {row["name"]: row for row in client.get("/api/network/loads").json()}
    assert set(loads) == {"it_load", "cooling", "offices"}


def test_create_without_a_body_has_no_envelope(client, tmp_path, monkeypatch, tmp_projects_dir):
    _stage(tmp_path, monkeypatch, "eh_datacenter")
    r = client.post("/api/projects/from_template/eh_datacenter", params={"name": "dc_plain"})
    assert r.status_code == 200, r.text
    name = r.json().get("name", "dc_plain")
    assert "dc_load" not in client.get(f"/api/projects/{name}/eh_template").json()


def test_create_refuses_wrong_staged_years_with_a_typed_422(
        client, tmp_path, monkeypatch, tmp_projects_dir):
    tid = "eh_datacenter_staged"
    _stage(tmp_path, monkeypatch, tid)
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": "dc_bad"},
                    json={"dc_load": {"phases": [{"cod": "2028-01-01", "it_mw": 12.0}]}})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "dc_load_invalid" and detail["field"] == "dc_load.phases"
    assert "[2027, 2030, 2035]" in detail["message"]
    r2 = client.post(f"/api/projects/from_template/{tid}", params={"name": "dc_bad2"},
                     json={"site_type": "hospital"})
    assert r2.status_code == 422 and r2.json()["detail"]["field"] == "site_type"
    # nothing was created
    assert client.get("/api/projects/dc_bad/eh_template").status_code in (403, 404)


# ── DC template review 04: the ambient follows the site's location ──────────

def _year_hours() -> pd.DatetimeIndex:
    return pd.date_range("2025-01-01", periods=8760, freq="h")


def test_hot_site_peak_pue_hand_checked():
    # Hot preset (|lat| 15-25): mean 27, seasonal 10, diurnal 8 -> peak 45 degC
    # (day 200, 15:00). PUE = 1.3 + 0.02 x (45 - 20) = 1.80.
    spec = T.dc_load_spec("eh_datacenter", {"site_latitude": 24.7})
    assert (spec.ambient_annual_mean_c, spec.ambient_seasonal_amplitude_k,
            spec.ambient_diurnal_amplitude_k) == (27.0, 10.0, 8.0)
    temp = P.ambient_c(spec, _year_hours())
    assert temp.max() == pytest.approx(45.0, abs=1e-9)
    assert P.pue(spec, temp).max() == pytest.approx(1.80)


def test_cool_site_peak_pue_hand_checked():
    # Cold preset (|lat| > 60): mean 3, seasonal 11, diurnal 4 -> peak 18 degC,
    # below the 20 degC design temperature, so PUE stays 1.30 all year.
    spec = T.dc_load_spec("eh_datacenter", {"site_latitude": 65.0})
    temp = P.ambient_c(spec, _year_hours())
    assert temp.max() == pytest.approx(18.0, abs=1e-9)
    assert P.pue(spec, temp).max() == pytest.approx(1.30)


def test_southern_site_peaks_in_january():
    spec = T.dc_load_spec("eh_datacenter", {"site_latitude": -23.0})
    temp = P.ambient_c(spec, _year_hours())
    assert pd.Timestamp(_year_hours()[int(temp.argmax())]).month in (1, 2)


def test_hot_site_loads_more_than_default():
    snaps = _year_hours()
    hot = T.dc_load_spec("eh_datacenter", {"site_latitude": 24.7})
    default = T.dc_load_spec("eh_datacenter", {})
    p_hot = P.hourly_profile(hot, snaps, 100.0)
    p_def = P.hourly_profile(default, snaps, 100.0)
    assert p_hot.max() > p_def.max() and p_hot.sum() > p_def.sum()
    # Same utilisation, so the peak ratio is the PUE ratio: 1.80 / 1.38.
    assert p_hot.max() / p_def.max() == pytest.approx(1.80 / 1.38)


def test_ambient_source_says_where_the_temperature_came_from():
    assert T.dc_load_spec("eh_datacenter", {}).ambient_source == "default"
    assert T.dc_load_spec("eh_datacenter", {"site_latitude": 24.7}).ambient_source == "site_location"
    stated = T.dc_load_spec("eh_datacenter", {"site_latitude": 24.7, "ambient_annual_mean_c": 31.0})
    assert stated.ambient_source == "site_location" and stated.ambient_annual_mean_c == 31.0
    assert stated.ambient_entered == ["ambient_annual_mean_c"]


def test_basis_block_reports_mean_peak_pue_and_facility_mw():
    spec = T.dc_load_spec("eh_datacenter", {"site_latitude": 24.7,
                                            "phases": [{"cod": "2030-01-01", "it_mw": 20.0}]})
    b = P.ambient_basis(spec, _year_hours(), None)
    assert b["source"] == "site_location" and b["mean_c"] == 27.0
    assert b["design_day_peak_c"] == pytest.approx(45.0) and b["peak_pue"] == pytest.approx(1.80)
    assert 20.0 * 1.3 < b["peak_facility_mw"] <= 20.0 * 1.80


# Hand-derived (hot preset 27/10/8 against the default 10/9/5, slope 0.02, 20 MW IT):
# the annual energy difference above, and the peak 20 x 0.92833 (training mean) x 1.80.
HOT_MINUS_DEFAULT_MWH = 25832.878462
HOT_PEAK_MW = 33.42


# ── GK A review of DC 04 ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["24.7", True, 91, -90.5, float("nan"), float("inf"), [24.7]])
def test_a_bad_site_latitude_is_refused_not_coerced(bad):
    with pytest.raises(P.DcLoadError) as exc:
        T.dc_load_spec("eh_datacenter", {"site_latitude": bad})
    assert exc.value.field == "dc_load.site_latitude"


def test_an_integer_latitude_applies_its_preset():
    spec = T.dc_load_spec("eh_datacenter", {"site_latitude": 25})
    assert spec.site_latitude == 25.0 and spec.ambient_annual_mean_c == 27.0  # hot, |lat| <= 25


def test_a_partial_entry_names_the_entered_fields_and_keeps_the_preset_for_the_rest():
    spec = T.dc_load_spec("eh_datacenter", {"site_latitude": 24.7, "ambient_diurnal_amplitude_k": 2.0})
    assert spec.ambient_entered == ["ambient_diurnal_amplitude_k"]
    assert spec.ambient_source == "site_location"
    assert (spec.ambient_annual_mean_c, spec.ambient_seasonal_amplitude_k,
            spec.ambient_diurnal_amplitude_k) == (27.0, 10.0, 2.0)
    block = P.meta_block(spec, None)  # spec -> meta_block -> the card's data
    assert block["ambient"]["entered"] == ["ambient_diurnal_amplitude_k"]
    assert block["ambient"]["source"] == "site_location"
    assert block["spec"]["ambient_entered"] == ["ambient_diurnal_amplitude_k"]


def test_all_three_entered_is_source_entered_and_no_latitude_is_default():
    full = T.dc_load_spec("eh_datacenter", {"ambient_annual_mean_c": 30.0,
                                            "ambient_seasonal_amplitude_k": 8.0,
                                            "ambient_diurnal_amplitude_k": 6.0})
    assert full.ambient_source == "entered" and len(full.ambient_entered) == 3
    none = T.dc_load_spec("eh_datacenter", {})
    assert none.ambient_source == "default" and none.ambient_entered == []


@pytest.mark.parametrize("lat,zone_mean", [
    (0.0, 27.0), (14.99, 27.0), (15.0, 27.0), (15.01, 27.0),   # 15 closes the tropical zone
    (24.99, 27.0), (25.0, 27.0), (25.01, 21.0),
    (34.99, 21.0), (35.0, 21.0), (35.01, 15.0),
    (44.99, 15.0), (45.0, 15.0), (45.01, 10.0),
    (59.99, 10.0), (60.0, 10.0), (60.01, 3.0),
    (90.0, 3.0), (-90.0, 3.0), (-25.0, 27.0), (-25.01, 21.0), (-60.01, 3.0),
])
def test_climate_preset_zone_edges(lat, zone_mean):
    assert P.climate_preset(lat)["ambient_annual_mean_c"] == zone_mean


def test_climate_preset_whole_table():
    expect = {  # |lat| bound: (mean, seasonal, diurnal)
        10.0: (27.0, 1.5, 5.0), 20.0: (27.0, 10.0, 8.0), 30.0: (21.0, 9.0, 6.0),
        40.0: (15.0, 9.0, 5.0), 50.0: (10.0, 9.0, 5.0), 75.0: (3.0, 11.0, 4.0),
    }
    for lat, (m, s, d) in expect.items():
        for sign, day in ((1, 200), (-1, 17)):
            p = P.climate_preset(sign * lat)
            assert (p["ambient_annual_mean_c"], p["ambient_seasonal_amplitude_k"],
                    p["ambient_diurnal_amplitude_k"], p["ambient_peak_day"]) == (m, s, d, day)
    assert P.climate_preset(0.0)["ambient_peak_day"] == 200  # the equator is not southern


def test_hot_site_energy_and_peak_are_pinned():
    spec_hot = T.dc_load_spec("eh_datacenter", {"site_latitude": 24.7,
                                                "phases": [{"cod": "2030-01-01", "it_mw": 20.0}]})
    spec_def = T.dc_load_spec("eh_datacenter", {"phases": [{"cod": "2030-01-01", "it_mw": 20.0}]})
    snaps = _year_hours()
    u = P.utilisation(spec_hot, snaps)
    # Independent of P.ambient_c: the cosines written out.
    doy, hod = snaps.dayofyear.to_numpy(), snaps.hour.to_numpy()

    def temp(mean, seas, diur):
        return (mean + seas * np.cos(2 * np.pi * (doy - 200) / 365.0)
                + diur * np.cos(2 * np.pi * (hod - 15.0) / 24.0))

    excess = lambda t: np.maximum(0.0, t - 20.0)  # noqa: E731
    d_excess = excess(temp(27.0, 10.0, 8.0)) - excess(temp(10.0, 9.0, 5.0))
    want = float(np.sum(u * 20.0 * 0.02 * d_excess))
    got = (P.hourly_profile(spec_hot, snaps, 20.0).sum() - P.hourly_profile(spec_def, snaps, 20.0).sum())
    assert got == pytest.approx(want, rel=1e-9)
    assert got == pytest.approx(HOT_MINUS_DEFAULT_MWH, rel=1e-6)
    assert P.ambient_basis(spec_hot, snaps)["peak_facility_mw"] == pytest.approx(HOT_PEAK_MW, rel=1e-9)


def test_read_ambient_carries_the_peak_day(monkeypatch):
    import services.template_marker as M
    spec = T.dc_load_spec("eh_datacenter", {"site_latitude": -23.0})
    monkeypatch.setattr(M, "read_marker", lambda d: {P.META_KEY: {"spec": spec.model_dump(mode="json")}})
    amb = P.read_ambient("anywhere")
    assert amb["ambient_peak_day"] == 17 and amb["ambient_annual_mean_c"] == 27.0


def test_southern_peak_day_reaches_the_cop_and_availability_generators():
    from services.profile_gen import availability, series
    south = {"ambient_annual_mean_c": 20.0, "ambient_seasonal_amplitude_k": 10.0,
             "ambient_diurnal_amplitude_k": 0.0, "ambient_peak_day": 17}
    north = {**south, "ambient_peak_day": 200}
    # derating: the hottest hour has the lowest availability.
    der = {"threshold_c": 15.0, "per_k": 0.02, "floor": 0.5}
    a_s, _ = availability.generate_frame(["g"], "derating", {**der, **south}, 2025)
    a_n, _ = availability.generate_frame(["g"], "derating", {**der, **north}, 2025)
    assert a_s["g"].idxmin().month == 1 and a_n["g"].idxmin().month == 7
    # air COP: the coldest source hour (lowest COP) is in July in the south.
    c_s, _ = series.generate_frame(["hp"], "cop", south, 2025)
    c_n, _ = series.generate_frame(["hp"], "cop", north, 2025)
    assert c_s["hp"].idxmin().month == 7 and c_n["hp"].idxmin().month == 1


# ── mutant survivors of the GK A review ─────────────────────────────────────

@pytest.mark.parametrize("lat,triple", [
    (14.5, (27.0, 1.5, 5.0)), (15.0, (27.0, 1.5, 5.0)), (15.5, (27.0, 10.0, 8.0)),
    (24.5, (27.0, 10.0, 8.0)), (25.0, (27.0, 10.0, 8.0)), (25.5, (21.0, 9.0, 6.0)),
    (34.5, (21.0, 9.0, 6.0)), (35.0, (21.0, 9.0, 6.0)), (35.5, (15.0, 9.0, 5.0)),
    (44.5, (15.0, 9.0, 5.0)), (45.0, (15.0, 9.0, 5.0)), (45.5, (10.0, 9.0, 5.0)),
    (59.5, (10.0, 9.0, 5.0)), (60.0, (10.0, 9.0, 5.0)), (60.5, (3.0, 11.0, 4.0)),
])
def test_climate_zone_edges_on_the_whole_triple(lat, triple):
    p = P.climate_preset(lat)
    assert (p["ambient_annual_mean_c"], p["ambient_seasonal_amplitude_k"],
            p["ambient_diurnal_amplitude_k"]) == triple


@pytest.mark.parametrize("lat", [90, -90, 89.9, 0])
def test_the_latitude_range_is_inclusive(lat):
    assert T.dc_load_spec("eh_datacenter", {"site_latitude": lat}).site_latitude == float(lat)


def test_the_basis_reads_the_largest_phase_the_first_phases_year_and_a_full_year():
    spec = T.dc_load_spec("eh_datacenter", {"site_latitude": 24.7, "phases": [
        {"cod": "2023-01-01", "it_mw": 20.0}, {"cod": "2024-01-01", "it_mw": 40.0}]})
    snaps = P._basis_snapshots(spec)
    assert (len(snaps), snaps[0], snaps[-1]) == (
        8760, pd.Timestamp("2023-01-01 00:00"), pd.Timestamp("2023-12-31 23:00"))
    b = P.ambient_basis(spec, snaps)
    assert b["site_latitude"] == 24.7
    assert b["peak_facility_mw"] == pytest.approx(2 * 33.42)  # the 40 MW phase, not the first
    block = P.meta_block(spec, None)["ambient"]
    assert block["design_day_peak_c"] == pytest.approx(45.0) and block["peak_pue"] == pytest.approx(1.80)


def test_read_ambient_is_none_without_a_complete_spec(monkeypatch):
    import services.template_marker as M
    spec = T.dc_load_spec("eh_datacenter", {}).model_dump(mode="json")
    partial = {k: v for k, v in spec.items() if k != "ambient_diurnal_amplitude_k"}
    for marker in (None, {}, {P.META_KEY: None}, {P.META_KEY: {"spec": None}},
                   {P.META_KEY: {"spec": partial}}, {P.META_KEY: "x"}, "x"):
        monkeypatch.setattr(M, "read_marker", lambda d, m=marker: m)
        assert P.read_ambient("anywhere") is None


def test_the_zone_is_named():
    assert P.climate_preset(24.7)["zone"].startswith("hot") and P.climate_preset(70)["zone"] == "cold"
