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

import pytest

from services.adequacy import grid_readiness as G
from services.adequacy import site_peak as SP
from models.flex_archetypes import DataCentreLoadSpec
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
            assert "dc_load" in rows[rid]["note"] or "eh_sk_mva" in rows[rid]["note"]


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
        assert rows[rid]["verdict"] == "unknown" and "island sidecar" in rows[rid]["note"], rid


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
        assert r["project_value"] == pytest.approx(c["peak_mw"], abs=1e-3)
        assert (r["verdict"] == "fail") == (c["headroom_mw"] < -1e-9)


def test_the_microgrids_open_tie_supplies_nothing():
    n = T.BUILDERS["eh_microgrid"]()
    assert all(r["energized_mw"] == 0.0 for r in SP.peak_check(n, T.ISLAND_CONFIG["eh_microgrid"]))


def test_the_peak_cap_takes_the_poc_links_losses():
    n = T.BUILDERS["eh_datacenter"]()
    (row,) = SP.peak_check(n, T.ISLAND_CONFIG["eh_datacenter"])
    assert row["delivered_mw"] == pytest.approx(40.0 * 0.995)
    assert row["cap_mw"] == pytest.approx(40.0 * 0.995 + 30.0)       # 4 × 10 MW gensets, N+1


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
    slope = (hi["peak_mw"] - lo["peak_mw"]) / 24.0
    at_cap = 36.0 + (lo["cap_mw"] - lo["peak_mw"]) / slope
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
    assert row["verdict"] == "unknown" and "not assessed" in row["note"]


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
        assert "stale" in row["note"]


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
