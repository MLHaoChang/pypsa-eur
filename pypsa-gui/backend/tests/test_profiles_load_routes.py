"""Load profiles over HTTP (time-series generators ticket 01, slice B2).

* `GET  /api/profiles/load-archetypes`: the archetypes, their parameter
  defaults and the presets;
* `POST /api/projects/{name}/profiles/loads/generate`: generate a load
  archetype for named loads, synchronously, into the SAME pending store as
  the renewable jobs (`job.write_pending`);
* `POST /api/projects/{name}/profiles/apply`: the one apply route, now told
  (or inferring from the artifact's family) which target to write.
"""
from __future__ import annotations

import json

import pandas as pd
import pypsa
import pytest

SNAPS = pd.date_range("2025-01-01", periods=8760, freq="h")


def _load_network() -> pypsa.Network:
    n = pypsa.Network()
    idx = SNAPS.copy()
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1", x=8.0, y=50.0)
    n.add("Load", "office", bus="B1", p_set=1.0)
    n.add("Load", "plant", bus="B1", p_set=1.0)
    n.add("Carrier", "wind")
    n.add("Generator", "g_wind", bus="B1", carrier="wind", p_nom=10.0)
    return n


@pytest.fixture
def load_project(client, install_network):
    install_network(_load_network())
    resp = client.post("/api/projects/Load Site",
                       params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text
    return "Load Site"


def _generate(client, project, **over):
    body = {"archetype": "industrial", "loads": ["plant"],
            "params": {"shifts": 2, "annual_mwh": 5000.0}, "year": 2025}
    body.update(over)
    return client.post(f"/api/projects/{project}/profiles/loads/generate",
                       json=body)


def test_the_catalogue_lists_archetypes_and_presets(client):
    resp = client.get("/api/profiles/load-archetypes")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {a["name"] for a in body["archetypes"]}
    assert {"industrial", "commercial", "residential", "ev_charging", "heat",
            "data_centre", "library"} <= names
    industrial = next(a for a in body["archetypes"] if a["name"] == "industrial")
    assert industrial["params"]["shifts"] == 2
    assert "peak_mw" in industrial["params"]
    presets = {p["name"] for p in body["presets"]}
    assert "commercial_office" in presets


def test_generate_writes_a_pending_load_artifact(client, load_project):
    resp = _generate(client, load_project)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["family"] == "load"
    assert body["archetype"] == "industrial"
    assert body["points"] == ["plant"]
    assert body["stats"]["plant"]["annual_mwh"] == pytest.approx(5000.0)
    assert body["stats"]["plant"]["peak_mw"] > 0

    pending = client.get(f"/api/projects/{load_project}/profiles/pending").json()
    assert any(p["job_id"] == body["job_id"] for p in pending["pending"])


def test_generate_refuses_a_load_not_on_the_network(client, load_project):
    resp = _generate(client, load_project, loads=["nope"])
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "unknown_load"


def test_generate_refuses_an_unknown_archetype(client, load_project):
    resp = _generate(client, load_project, archetype="bakery")
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "unknown_archetype"


def test_generate_refuses_bad_parameters(client, load_project):
    resp = _generate(client, load_project, params={"shfits": 3, "peak_mw": 1.0})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "invalid_archetype_params"


def test_a_generated_load_applies_to_p_set_by_its_family(client, load_project,
                                                       session_ctx):
    job = _generate(client, load_project).json()
    resp = client.post(f"/api/projects/{load_project}/profiles/apply",
                       json={"job_id": job["job_id"], "component": "plant",
                             "time_basis": "UTC"})
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["component_type"] == "loads"
    assert summary["attribute"] == "p_set"
    assert summary["profile_annual_mwh"] == pytest.approx(5000.0)

    ctx = session_ctx(client)
    assert ("loads", "p_set", "plant") in ctx.user_ts
    assert float(ctx.network.loads_t.p_set["plant"].sum()) == pytest.approx(5000.0)


def test_apply_of_a_load_profile_can_be_undone(client, load_project):
    job = _generate(client, load_project).json()
    client.post(f"/api/projects/{load_project}/profiles/apply",
                json={"job_id": job["job_id"], "component": "plant",
                      "time_basis": "UTC"})
    assert client.post("/api/network/undo").status_code == 200
    payload = client.get("/api/network/timeseries/loads/p_set").json()
    assert "plant" not in payload["columns"]


def test_a_load_profile_cannot_be_applied_to_a_generator(client, load_project):
    job = _generate(client, load_project).json()
    resp = client.post(f"/api/projects/{load_project}/profiles/apply",
                       json={"job_id": job["job_id"], "component": "g_wind",
                             "point_id": "plant",
                             "component_type": "generators",
                             "attribute": "p_max_pu"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "family_target_mismatch"


def test_a_dc_archetype_on_a_dc_load_project_is_refused(client, load_project,
                                                      session_ctx):
    from services.dc_load_profile import META_KEY

    ctx = session_ctx(client)
    meta_path = ctx.storage_dir and (__import__("pathlib").Path(ctx.storage_dir)
                                     / "eh_template.json")
    meta_path.write_text(json.dumps({META_KEY: {"envelope": {"schema": 1}}}))
    job = _generate(client, load_project, archetype="data_centre",
                    params={"peak_mw": 5.0}).json()
    resp = client.post(f"/api/projects/{load_project}/profiles/apply",
                       json={"job_id": job["job_id"], "component": "plant",
                             "time_basis": "UTC"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "dc_load_owned"


def test_generate_on_another_orgs_project_is_a_404(other_org_client):
    resp = other_org_client.post(
        "/api/projects/Load Site/profiles/loads/generate",
        json={"archetype": "industrial", "loads": ["plant"],
              "params": {"peak_mw": 1.0}},
    )
    assert resp.status_code == 404


def test_generate_maps_working_hours_on_the_projects_commercial_timezone(
    client, load_project, monkeypatch,
):
    import services.profile_gen.apply as apply_mod

    monkeypatch.setattr(apply_mod, "commercial_timezone", lambda ctx: "Europe/Berlin")
    body = _generate(client, load_project).json()
    assert body["params"]["timezone"] == "Europe/Berlin"
    explicit = _generate(client, load_project,
                         params={"annual_mwh": 1.0, "timezone": "UTC"}).json()
    assert explicit["params"]["timezone"] == "UTC"


def _meter_csv(columns: dict, unit_suffix: str = "") -> bytes:
    idx = pd.date_range("2025-01-01", "2026-01-01", freq="h", inclusive="left")
    frame = pd.DataFrame({f"{k}{unit_suffix}": v for k, v in columns.items()},
                         index=idx)
    frame.index.name = "timestamp"
    return frame.to_csv().encode()


def test_a_measured_upload_lands_in_pending_and_applies(client, load_project,
                                                       session_ctx):
    resp = client.post(
        f"/api/projects/{load_project}/profiles/loads/upload",
        files={"file": ("meter.csv", _meter_csv({"plant": 2000.0, "shed": 1.0}),
                        "text/csv")},
        data={"unit": "kW"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provenance"] == "measured"
    assert body["points"] == ["plant"]
    assert body["unmatched"] == ["shed"]
    assert body["stats"]["plant"]["peak_mw"] == pytest.approx(2.0)

    applied = client.post(f"/api/projects/{load_project}/profiles/apply",
                          json={"job_id": body["job_id"], "component": "plant",
                                "time_basis": "UTC"})
    assert applied.status_code == 200, applied.text
    ctx = session_ctx(client)
    assert ctx.user_ts_meta[("loads", "p_set", "plant")]["provenance"] == "measured"
    assert float(ctx.network.loads_t.p_set["plant"].iloc[0]) == pytest.approx(2.0)


def test_an_upload_without_a_unit_is_a_typed_refusal(client, load_project):
    resp = client.post(
        f"/api/projects/{load_project}/profiles/loads/upload",
        files={"file": ("meter.csv", _meter_csv({"plant": 1.0}), "text/csv")},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "load_upload_unit"


def test_an_upload_naming_no_load_is_refused(client, load_project):
    resp = client.post(
        f"/api/projects/{load_project}/profiles/loads/upload",
        files={"file": ("meter.csv", _meter_csv({"shed": 1.0}), "text/csv")},
        data={"unit": "MW"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "unknown_load"
def test_generate_previews_the_modelled_energy_and_the_time_basis(client,
                                                                load_project):
    body = _generate(client, load_project).json()
    assert body["stats"]["plant"]["modelled_mwh"] == {"total": pytest.approx(5000.0)}
    assert any(w.startswith("time_basis_assumed_utc") for w in body["warnings"])


def _first_on_hour(series: pd.Series, day: str) -> int:
    hours = series.loc[day]
    return int(hours.index[hours.to_numpy() > hours.min() + 1e-9][0].hour)


@pytest.mark.parametrize("zone, winter, summer, warned", [
    ("Europe/Oslo", 5, 4, False),   # 06:00 local is 05:00 UTC (CET), 04:00 (CEST)
    (None, 6, 6, True),             # no zone: the snapshot clock, with a warning
])
def test_an_archetype_lands_once_converted_on_utc_snapshots(
    client, load_project, session_ctx, monkeypatch, zone, winter, summer, warned,
):
    import services.profile_gen.apply as apply_mod

    monkeypatch.setattr(apply_mod, "commercial_timezone", lambda ctx: zone)
    job = _generate(client, load_project,
                    params={"shifts": 1, "peak_mw": 1.0}).json()
    resp = client.post(f"/api/projects/{load_project}/profiles/apply",
                       json={"job_id": job["job_id"], "component": "plant"})
    assert resp.status_code == 200, resp.text
    assert any(w.startswith("time_basis_assumed_utc")
               for w in resp.json()["warnings"]) is warned
    s = session_ctx(client).network.loads_t.p_set["plant"]
    assert _first_on_hour(s, "2025-01-08") == winter   # a Wednesday
    assert _first_on_hour(s, "2025-07-09") == summer


# ---------------------------------------------------------------------------
# availability (ticket 02)
# ---------------------------------------------------------------------------


@pytest.fixture
def plant_project(client, install_network):
    n = _load_network()
    n.add("Carrier", "gas")
    n.add("Generator", "chp", bus="B1", carrier="gas", p_nom=10.0)
    install_network(n)
    resp = client.post("/api/projects/CHP Site",
                       params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text
    return "CHP Site"


def _avail(client, project, **over):
    body = {"kind": "maintenance", "generators": ["chp"],
            "params": {"windows": [{"from": "07-03", "to": "07-16"}]}}
    body.update(over)
    return client.post(f"/api/projects/{project}/profiles/availability/generate",
                       json=body)


def test_availability_generates_on_the_networks_year_and_applies(
    client, plant_project, session_ctx,
):
    gen = _avail(client, plant_project)
    assert gen.status_code == 200, gen.text
    body = gen.json()
    assert body["weather_year"] == 2025          # the network's own year
    assert body["target"] == ["generators", "p_max_pu"]
    assert body["stats"]["chp"]["hours_reduced"] == 14 * 24
    applied = client.post(f"/api/projects/{plant_project}/profiles/apply",
                          json={"job_id": body["job_id"], "component": "chp",
                                "time_basis": "UTC"})
    assert applied.status_code == 200, applied.text
    p = session_ctx(client).network.generators_t.p_max_pu["chp"]
    assert float(p.loc["2025-07-04T12:00"]) == 0.0


def test_must_run_applies_to_p_min_pu_by_its_stated_target(client, plant_project,
                                                          session_ctx):
    body = _avail(client, plant_project, kind="must_run",
                  params={"share": 0.2}).json()
    applied = client.post(f"/api/projects/{plant_project}/profiles/apply",
                          json={"job_id": body["job_id"], "component": "chp",
                                "time_basis": "UTC"})
    assert applied.status_code == 200, applied.text
    assert applied.json()["attribute"] == "p_min_pu"


@pytest.mark.parametrize("over, kind", [
    ({"kind": "forced_outages", "params": {}}, "forced_outages_not_written"),
    ({"kind": "strike", "params": {}}, "unknown_availability_kind"),
    ({"params": {"windows": "07-03"}}, "invalid_availability_params"),
    ({"params": {"windows": [{"from": "2031-07-03", "to": "2031-07-04"}]}},
     "maintenance_year"),
    ({"generators": ["nope"]}, "unknown_generator"),
    ({"generators": ["g_wind"]}, "carrier_mismatch"),
])
def test_availability_refusals_are_typed(client, plant_project, over, kind):
    resp = _avail(client, plant_project, **over)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == kind


def test_a_dated_window_on_a_multi_period_network_is_refused_before_pending(
    client, install_network,
):
    from services.snapshot_index import _build_period_multiindex

    n = pypsa.Network()
    idx = [pd.date_range(f"{y}-01-01", f"{y}-12-31T23:00", freq="h") for y in (2030, 2031)]
    n.set_snapshots(_build_period_multiindex([2030, 2031], idx))
    n.add("Bus", "B1")
    n.add("Carrier", "gas")
    n.add("Generator", "chp", bus="B1", carrier="gas", p_nom=10.0)
    install_network(n)
    assert client.post("/api/projects/Two Periods",
                       params={"force": True, "rebind": True}).status_code == 200
    resp = _avail(client, "Two Periods",
                  params={"windows": [{"from": "2030-07-03", "to": "2030-07-04"}]})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "maintenance_year"
    pending = client.get("/api/projects/Two Periods/profiles/pending").json()
    assert pending["pending"] == []


def test_must_run_stats_count_the_floored_hours(client, plant_project):
    body = _avail(client, plant_project, kind="must_run",
                  params={"share": 0.2}).json()
    assert body["stats"]["chp"]["hours_floored"] == 8760
    assert "hours_reduced" not in body["stats"]["chp"]


def test_availability_refusals_say_their_sentence_and_nothing_foreign(
    client, plant_project, monkeypatch, bound_ctx,
):
    # The sanitiser's rule (#508) on ticket 02's route, zone and chat tool.
    from fastapi import HTTPException

    from services import chat_tools
    from services.profile_gen import availability

    resp = _avail(client, plant_project, kind="must_run",
                  params={"share": 0.4, "timezone": "Europe"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["message"] == "timezone 'Europe' is not an IANA zone"

    foreign = "CDSAPI_KEY=abc123 at /srv/secret/path"

    def boom(*a, **k):
        raise ValueError(foreign)

    monkeypatch.setattr(availability, "generate_frame", boom)
    with bound_ctx(client), pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS["generate_availability_profile"](
            project_id=plant_project, kind="maintenance", generators=["chp"],
            params={"windows": [{"from": "07-03", "to": "07-16"}]})
    assert err.value.detail == "unexpected error (ValueError); see the server log"


# ---------------------------------------------------------------------------
# other series + link bounds (ticket 03)
# ---------------------------------------------------------------------------


@pytest.fixture
def heat_project(client, install_network):
    n = _load_network()
    n.buses.loc["B1", "carrier"] = "AC"
    n.add("Bus", "H", carrier="heat")
    n.add("Link", "hp", bus0="B1", bus1="H", p_nom=5.0)
    n.add("StorageUnit", "dam", bus="B1", p_nom=5.0, max_hours=100.0)
    install_network(n)
    resp = client.post("/api/projects/Heat Site",
                       params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text
    return "Heat Site"


def _series(client, project, **body):
    return client.post(f"/api/projects/{project}/profiles/series/generate", json=body)


def test_a_cop_series_generates_and_applies_to_the_links_efficiency(
    client, heat_project, session_ctx,
):
    gen = _series(client, heat_project, kind="cop", components=["hp"],
                  params={"source": "ground", "source_c": 5.0, "sink_c": 35.0})
    assert gen.status_code == 200, gen.text
    body = gen.json()
    assert body["target"] == ["links", "efficiency"]
    applied = client.post(f"/api/projects/{heat_project}/profiles/apply",
                          json={"job_id": body["job_id"], "component": "hp",
                                "time_basis": "UTC"})
    assert applied.status_code == 200, applied.text
    eff = session_ctx(client).network.links_t.efficiency["hp"]
    assert float(eff.iloc[0]) == pytest.approx(0.45 * 308.15 / 30.0)


def test_an_inflow_series_reports_its_energy(client, heat_project):
    body = _series(client, heat_project, kind="inflow", components=["dam"],
                   params={"annual_mwh": 1000.0, "shape": "flat"}).json()
    assert body["stats"]["dam"]["annual_mwh"] == pytest.approx(1000.0)


@pytest.mark.parametrize("over, kind", [
    ({"kind": "part_load", "components": ["hp"], "params": {}}, "part_load_not_a_series"),
    ({"kind": "tides", "components": ["hp"], "params": {}}, "unknown_series_kind"),
    ({"kind": "market", "components": ["hp"], "params": {}}, "market_bundles_unavailable"),
    ({"kind": "cop", "components": ["hp"], "params": {"source": "water"}},
     "invalid_series_params"),
    ({"kind": "cop", "components": ["nope"], "params": {}}, "unknown_component"),
])
def test_series_refusals_are_typed(client, heat_project, over, kind):
    resp = _series(client, heat_project, **over)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == kind


def test_availability_on_a_link_through_the_route(client, heat_project, session_ctx):
    gen = _avail(client, heat_project, generators=None, components=["hp"],
                 component_type="links")
    assert gen.status_code == 200, gen.text
    body = gen.json()
    applied = client.post(f"/api/projects/{heat_project}/profiles/apply",
                          json={"job_id": body["job_id"], "component": "hp",
                                "component_type": "links", "time_basis": "UTC"})
    assert applied.status_code == 200, applied.text
    assert applied.json()["component_type"] == "links"


def test_a_price_on_the_poc_link_is_refused_at_generation(client, heat_project,
                                                         session_ctx):
    from types import SimpleNamespace

    ctx = session_ctx(client)
    state = ctx.solver_state.setdefault("solver_config", SimpleNamespace())
    state.commercial = {"poc_link": "hp"}
    resp = _series(client, heat_project, kind="tou", components=["hp"],
                   params={"component_type": "links", "currency": "EUR",
                           "price_basis": "nominal", "currency_year": 2025,
                           "periods": [{"name": "flat", "rate": 50.0}]})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "poc_tariff"


# ---------------------------------------------------------------------------
# apply's dry run at generation: the editor holds Apply before a refusal
# ---------------------------------------------------------------------------


def test_availability_shows_what_apply_would_warn_before_apply(client, plant_project):
    # The bus's loads take 2 MW; a 50 % floor on the 10 MW plant forces 5 MW.
    body = _avail(client, plant_project, kind="must_run", params={"share": 0.5}).json()
    assert any(w.startswith("must_run_exceeds_sink") for w in body["warnings"]), body["warnings"]
    assert not any(w.startswith("not_applicable") for w in body["warnings"])


def test_a_series_apply_would_refuse_says_so_at_generation(client, install_network):
    n = _load_network()
    n.set_snapshots(pd.date_range("2025-06-02T00:30", periods=168, freq="h"))  # off the hour
    n.add("Bus", "H", carrier="heat")
    n.add("Link", "hp", bus0="B1", bus1="H", p_nom=5.0)
    n.add("Carrier", "gas")
    n.add("Generator", "chp", bus="B1", carrier="gas", p_nom=10.0)
    install_network(n)
    assert client.post("/api/projects/Week Site",
                       params={"force": True, "rebind": True}).status_code == 200
    for resp in (
        _series(client, "Week Site", kind="cop", components=["hp"],
                params={"source": "ground", "source_c": 5.0, "sink_c": 35.0}),
        _avail(client, "Week Site", kind="must_run", params={"share": 0.1}, year=2025),
    ):
        assert resp.status_code == 200, resp.text
        warnings = resp.json()["warnings"]
        assert any(w.startswith("not_applicable:") and "not on the hour" in w
                   for w in warnings), warnings
        assert resp.json()["weeks"] == {}


def test_a_load_preview_carries_its_peak_week_as_apply_would_write_it(client, load_project):
    body = _generate(client, load_project).json()
    week = body["weeks"]["plant"]
    assert week["label"] == "peak week"
    stamps = pd.DatetimeIndex(week["snapshots"])
    # One calendar week from Monday 00:00, cut where the snapshots end (the
    # year's first week starts on Wednesday 1 January 2025).
    assert len(stamps) <= 168 and stamps[0].hour == 0
    assert stamps[0].weekday() == 0 or stamps[0] == pd.Timestamp("2025-01-01")
    assert (stamps[-1] - stamps[0]) < pd.Timedelta(days=7)
    assert max(week["new"]) == pytest.approx(body["stats"]["plant"]["peak_mw"], rel=1e-3)
    assert week["current"] is None          # a static p_set has no series to show


def test_an_outage_preview_shows_its_lowest_week_beside_the_current_series(
    client, plant_project, session_ctx,
):
    first = _avail(client, plant_project, kind="derating", params={"floor": 0.8}).json()
    client.post(f"/api/projects/{plant_project}/profiles/apply",
                json={"job_id": first["job_id"], "component": "chp", "time_basis": "UTC"})
    body = _avail(client, plant_project).json()       # maintenance 07-03 – 07-16
    week = body["weeks"]["chp"]
    assert week["label"] == "lowest week"
    assert min(week["new"]) == 0.0
    assert week["snapshots"][0] == "2025-06-30T00:00:00"   # the Monday before 07-03
    assert week["current"] is not None and len(week["current"]) == len(week["new"])
    assert any(w.startswith("replaces_existing_series") for w in body["warnings"])


def test_the_current_series_refines_and_applies_through_the_routes(client, load_project,
                                                                   session_ctx):
    gen = _generate(client, load_project).json()
    client.post(f"/api/projects/{load_project}/profiles/apply",
                json={"job_id": gen["job_id"], "component": "plant", "time_basis": "UTC"})
    cur = client.get(f"/api/projects/{load_project}/profiles/current",
                     params={"component_type": "loads", "attribute": "p_set",
                             "component": "plant"}).json()
    assert cur["exists"] and cur["family"] == "load" and cur["rows"] == 8760
    body = client.post(f"/api/projects/{load_project}/profiles/from-current",
                       json={"component_type": "loads", "attribute": "p_set",
                             "component": "plant"})
    assert body.status_code == 200, body.text
    pending = body.json()
    assert pending["source"] == "current" and pending["weeks"]["plant"]["label"] == "peak week"
    refined = client.post(f"/api/projects/{load_project}/profiles/refine",
                          json={"job_id": pending["job_id"],
                                "ops": [{"op": "scale_peak_to", "mw": 2.0}]}).json()
    applied = client.post(f"/api/projects/{load_project}/profiles/apply",
                          json={"job_id": refined["job_id"], "component": "plant"})
    assert applied.status_code == 200, applied.text
    assert float(session_ctx(client).network.loads_t.p_set["plant"].max()) == pytest.approx(2.0)


def test_from_current_without_a_series_is_a_typed_refusal(client, load_project):
    resp = client.post(f"/api/projects/{load_project}/profiles/from-current",
                       json={"component_type": "loads", "attribute": "p_set",
                             "component": "office"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_kind"] == "no_current_series"


def test_series_refusals_say_their_sentence_and_nothing_foreign(
    client, heat_project, monkeypatch, bound_ctx,
):
    # The sanitiser's rule (#508) on ticket 03's series route, zone and chat tool.
    from fastapi import HTTPException

    from services import chat_tools
    from services.profile_gen import series

    resp = _series(client, heat_project, kind="cop", components=["hp"],
                   params={"source": "ground", "source_c": 5.0, "sink_c": 35.0,
                           "timezone": "Europe"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["message"] == "timezone 'Europe' is not an IANA zone"

    foreign = "CDSAPI_KEY=abc123 at /srv/secret/path"

    def boom(*a, **k):
        raise ValueError(foreign)

    monkeypatch.setattr(series, "generate_frame", boom)
    with bound_ctx(client), pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS["generate_series_profile"](
            project_id=heat_project, kind="inflow", components=["dam"],
            params={"annual_mwh": 1000.0, "shape": "flat"})
    assert err.value.detail == "unexpected error (ValueError); see the server log"
