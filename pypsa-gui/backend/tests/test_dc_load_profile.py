"""
DC grid readiness 01: the AI-training load profile in the data-center
templates, and its dynamic envelope.

The profile is opt-in (a create body's ``dc_load``); without it both data
center templates keep today's load bit for bit.
"""
from __future__ import annotations

import hashlib
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
#: sha256 of each template's default p_set on master 67a90b5e (before 01).
DEFAULT_P_SET_SHA = {
    "eh_datacenter": "f2909d51355732c26e6690db80ffb0b1358ff8cb7ec5e226f9a8f78a67964262",
    "eh_datacenter_staged": "e0a7c8caf5686cecdadce47bc64375e97736c5830bfc003f42c56fa6fd31dce5",
}


def _spec(**kw) -> DataCentreLoadSpec:
    base = dict(phases=[DcPhase(cod=date(2028, 1, 1), it_mw=40.0, rack_kw=130.0)],
                utilisation="ai_training", pue_at_design=1.25, redundancy="N+1",
                critical_share=0.8, curtailable_share=0.1, ai_workload=AiWorkloadSpec())
    return DataCentreLoadSpec(**{**base, **kw})


def _sha(df: pd.DataFrame) -> str:
    return hashlib.sha256(np.ascontiguousarray(df.to_numpy(dtype=float)).tobytes()).hexdigest()


# ── the default build is unchanged ──────────────────────────────────────────


@pytest.mark.parametrize("tid", DC_IDS)
def test_without_the_opt_in_the_load_is_bit_for_bit_unchanged(tid):
    assert _sha(T.BUILDERS[tid]().loads_t.p_set) == DEFAULT_P_SET_SHA[tid]


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
    assert _sha(n.loads_t.p_set) != DEFAULT_P_SET_SHA[tid]
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
    ({"utilisation": "inference"}, "dc_load.utilisation"),
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
