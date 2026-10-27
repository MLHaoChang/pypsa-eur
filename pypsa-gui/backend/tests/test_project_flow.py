"""The project flow endpoint (project-workflow B1, design §3).

`GET /api/projects/{name}/flow` files the existing checks into the ten flow
sections; `POST …/flow/ack` acknowledges a warning. Covered here:

  * per section: empty → incomplete, filled → complete, broken (an error item)
    → incomplete, a warning → warning, acknowledged → complete, and re-raised
    once the value changes;
  * a deferred preflight → every input section `unknown`, `run_ready` false;
  * the templates at create, against the instantiation table (design §2.3);
  * an islanded microgrid → connection `not_needed`;
  * `run_ready` agrees with `/simulation/preflight`'s `ok` when no presence
    rule fails.
"""
from __future__ import annotations

import contextlib
import json
import time
from dataclasses import replace

import pandas as pd
import pypsa
import pytest

from routers import simulation as sim_router  # noqa: F401 — monkeypatched
from services.pypsa_service import PyPSAService

INPUTS = ("project", "carriers", "connection", "loads", "assets", "profiles",
          "commercial", "scenarios")


# ── helpers ─────────────────────────────────────────────────────────────────

def _site_network() -> pypsa.Network:
    """A small placed site behind a tagged grid connection: every presence rule
    holds and the validator finds nothing."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=24, freq="h"))
    n.snapshot_weightings.loc[:, :] = 8760 / 24         # one modelled year
    n.add("Carrier", "AC")
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "grid")
    n.add("Bus", "grid", x=5.0, y=52.0, carrier="AC")
    n.add("Bus", "site", x=5.01, y=52.01, carrier="AC")
    n.add("Generator", "market", bus="grid", carrier="grid", p_nom=100.0, marginal_cost=60.0)
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=50.0, carrier="grid")
    n.links.loc["grid_import", "eh_role"] = "grid_import"
    n.add("Load", "it", bus="site", p_set=20.0)
    n.add("Generator", "gas", bus="site", carrier="gas", p_nom=30.0, marginal_cost=80.0)
    return n


@pytest.fixture
def site(client, install_network, tmp_projects_dir):
    """Factory: install a network as a saved, open project; returns its name."""
    def _make(n: pypsa.Network | None = None, name: str = "site") -> str:
        install_network(n if n is not None else _site_network(), name=name)
        r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
        assert r.status_code == 200, r.text
        return name
    return _make


def _flow(client, name) -> dict:
    r = client.get(f"/api/projects/{name}/flow")
    assert r.status_code == 200, r.text
    return r.json()


def _states(body) -> dict[str, str | None]:
    return {s["section"]: s["state"] for s in body["sections"]}


def _codes(body, section) -> list[str]:
    row = next(s for s in body["sections"] if s["section"] == section)
    return [i["code"] for i in row["items"]]


@pytest.fixture
def edit(client, bound_ctx):
    """Yield the session's own network under its lock, as a context manager."""
    @contextlib.contextmanager
    def _edit():
        with bound_ctx(client), PyPSAService.get_lock():
            yield PyPSAService.get_network()
    return _edit


@pytest.fixture
def set_cfg(client, bound_ctx):
    """Replace fields of the session's solver config."""
    def _set(**kw):
        with bound_ctx(client):
            state = PyPSAService.get_solver_state()
            state["solver_config"] = replace(state["solver_config"], **kw)
    return _set


def _create(client, tid, name, **kw) -> str:
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": name}, **kw)
    assert r.status_code == 200, r.text
    return r.json().get("name", name)


# ── the payload ─────────────────────────────────────────────────────────────

def test_a_filled_site_is_complete_and_ready(client, site):
    body = _flow(client, site())
    assert [s["section"] for s in body["sections"]] == [*INPUTS, "readiness", "results"]
    assert {s: _states(body)[s] for s in INPUTS} == {s: "complete" for s in INPUTS}
    assert body["run_ready"] is True and body["run_blocker"] is None
    assert body["notes"] == [] and body["deferred"] is False
    assert body["run_request"] == {"optimisation": True, "study": False, "loadflow": False}
    assert body["verdicts"] == {"optimisation": {"ready": True, "blocker": None},
                                "study": None, "loadflow": None}
    rows = {s["section"]: s for s in body["sections"]}
    assert rows["readiness"]["state"] is None and rows["readiness"]["summary"] == "0 to do, 0 to check"
    assert rows["results"]["state"] is None and rows["results"]["freshness"] == "not_run"
    assert rows["connection"]["summary"] == "grid_import, 50 MW"
    assert rows["project"]["required"] and not rows["commercial"]["required"]


def test_an_item_carries_its_target(client, site, edit):
    name = site()
    with edit() as n:
        n.loads.loc["it", "p_set"] = float("nan")
    body = _flow(client, name)
    row = next(s for s in body["sections"] if s["section"] == "loads")
    item = next(i for i in row["items"] if i["severity"] == "error")
    assert set(item) == {"code", "severity", "message", "source", "target", "blocking",
                         "acknowledged", "title", "card", "in_scope"}
    assert item["title"] and "_" not in item["title"]
    assert item["source"] == "validation" and item["blocking"] is True
    assert item["card"] is None and item["in_scope"] is True
    assert item["target"] == {"section": "loads", "panel": None, "component": "Load",
                              "entity": "it", "field": None}
    assert body["run_blocker"]["section"] == "loads"
    # The blocker reads in the plain title and the entity, not the validator's
    # detail text ("p_set must be finite ...").
    assert body["run_blocker"]["message"] == f"Loads & demand: {item['title']} (it)"
    # The detail the user fixes from stays reachable, unchanged, on the pointer
    # and on the item.
    assert body["run_blocker"]["detail"] == item["message"]
    assert item["message"] != item["title"]
    assert body["run_blocker"]["target"] == item["target"]


def test_a_project_that_is_not_open_answers_409(client, site):
    first = site(name="first")
    site(name="second")
    r = client.get(f"/api/projects/{first}/flow")
    assert r.status_code == 409
    assert r.json()["detail"]["error_kind"] == "flow_project_not_open"


# ── per section: empty, filled (above), broken, warning ─────────────────────

def test_project_section(client, site, edit):
    name = site()
    with edit() as n:
        n.snapshot_weightings.loc[:, "objective"] = float("nan")
    body = _flow(client, name)
    assert _states(body)["project"] == "incomplete"            # broken
    assert "snapshot_weighting_nan" in _codes(body, "project")
    with edit() as n:
        n.snapshot_weightings.loc[:, "objective"] = 8760 / 24
        n.buses[["x", "y"]] = 0.0
    body = _flow(client, name)
    assert _states(body)["project"] == "warning"               # no site location
    assert _codes(body, "project") == ["site_location_missing"]
    assert body["run_ready"] is True                           # W3: warnings never block


def _unplaced_wind_site() -> pypsa.Network:
    n = _site_network()
    n.add("Carrier", "wind")
    n.add("Bus", "B1", x=0.0, y=0.0, carrier="AC")           # never placed
    n.add("Generator", "g1", bus="B1", carrier="wind", p_nom=10.0)
    n.add("Generator", "g2", bus="B1", carrier="wind", p_nom=5.0)
    return n


def test_profiles_section_missing_renewable_location(client, site, edit):
    """One display-only item per unplaced bus that carries wind or solar."""
    name = site(_unplaced_wind_site())
    body = _flow(client, name)
    verdicts = (body["run_ready"], body["verdicts"])
    items = [i for s in body["sections"] if s["section"] == "profiles" for i in s["items"]]
    assert [i["code"] for i in items] == ["profile_location_missing"]
    it = items[0]
    assert it["message"] == ("Bus B1 has no location: a weather profile for g1, g2 "
                             "needs to know where it is.")
    assert it["target"]["component"] == "Bus" and it["target"]["entity"] == "B1"
    assert it["target"]["field"] == "x" and it["blocking"] is False
    assert body["run_ready"] is True and body["verdicts"]["optimisation"]["ready"] is True
    assert it["title"]
    # Filled: the bus gets a place and the item goes.
    with edit() as n:
        n.buses.loc["B1", ["x", "y"]] = (4.0, 51.0)
    body = _flow(client, name)
    assert "profile_location_missing" not in _codes(body, "profiles")
    assert (body["run_ready"], body["verdicts"]) == verdicts


def test_profile_location_many_generators_and_buses(client, site):
    n = _unplaced_wind_site()
    for k in range(3):
        n.add("Generator", f"g{k + 3}", bus="B1", carrier="wind", p_nom=1.0)
    n.add("Bus", "B2", x=0.0, y=0.0, carrier="AC")
    n.add("Generator", "s1", bus="B2", carrier="solar", p_nom=1.0)
    body = _flow(client, site(n))
    items = [i for s in body["sections"] if s["section"] == "profiles" for i in s["items"]]
    by_bus = {i["target"]["entity"]: i["message"] for i in items}
    assert set(by_bus) == {"B1", "B2"}
    assert by_bus["B1"].endswith("a weather profile for g1, g2, g3 and 2 more needs "
                                 "to know where it is.")
    assert "for s1 needs" in by_bus["B2"]


def test_profile_location_skips_profiles_not_generated(client, site):
    """A series the network carries (opening seeds the store from it) needs no
    weather point, so no note."""
    n = _unplaced_wind_site()
    n.generators_t.p_max_pu["g1"] = 0.5
    n.generators_t.p_max_pu["g2"] = 0.3
    body = _flow(client, site(n))
    assert "profile_location_missing" not in _codes(body, "profiles")


def test_unplaced_renewables_by_profile_state():
    from types import SimpleNamespace

    from services.profile_gen.readiness import supplied_profiles, unplaced_renewables

    n = _unplaced_wind_site()
    n.add("Generator", "g3", bus="B1", carrier="wind", p_nom=1.0)
    n.add("Generator", "g4", bus="B1", carrier="wind", p_nom=1.0)
    n.generators_t.p_max_pu["g4"] = 0.4                      # native
    key = lambda g: ("generators", "p_max_pu", g)            # noqa: E731
    ctx = SimpleNamespace(
        network=n, user_ts={key("g1"): 1, key("g2"): 1},
        user_ts_meta={key("g2"): {"provenance": "generated: era5"}})
    names = [str(g) for g in n.generators.index]
    # g1 uploaded (no record), g4 native: supplied. g2 generated, g3 none: need a point.
    assert supplied_profiles(ctx, names) == {"g1", "g4"}
    assert unplaced_renewables(n) == [(g, "B1") for g in ("g1", "g2", "g3", "g4")]
    assert unplaced_renewables(n, supplied_profiles(ctx, names)) == [("g2", "B1"), ("g3", "B1")]


def test_profile_location_ignores_non_renewables(client, site):
    n = _site_network()
    n.add("Bus", "B1", x=0.0, y=0.0, carrier="AC")
    n.add("Generator", "gas2", bus="B1", carrier="gas", p_nom=1.0)
    body = _flow(client, site(n))
    assert "profile_location_missing" not in _codes(body, "profiles")


def test_profile_location_defers_to_site_location_missing(client, site, edit):
    name = site(_unplaced_wind_site())
    with edit() as n:
        n.buses[["x", "y"]] = 0.0                              # no bus placed at all
    body = _flow(client, name)
    assert _codes(body, "project") == ["site_location_missing"]
    assert "profile_location_missing" not in _codes(body, "profiles")


def test_carriers_section(client, site, edit):
    name = site()
    with edit() as n:
        n.carriers.loc["gas", "co2_emissions"] = 0.0
    body = _flow(client, name)
    assert _states(body)["carriers"] == "warning"
    assert "carrier_zero_co2" in _codes(body, "carriers")


def test_connection_section(client, site, edit):
    name = site()
    with edit() as n:
        n.links.loc["grid_import", "p_nom_extendable"] = True
        n.links.loc["grid_import", "p_nom_min"] = 80.0
        n.links.loc["grid_import", "p_nom_max"] = 10.0
    body = _flow(client, name)
    assert _states(body)["connection"] == "incomplete"         # broken, on the import Link
    assert "link_p_nom_bounds" in _codes(body, "connection")
    assert "link_p_nom_bounds" not in _codes(body, "assets")
    with edit() as n:
        n.remove("Link", "grid_import")
    body = _flow(client, name)
    assert _states(body)["connection"] == "incomplete"         # empty
    assert "presence_connection" in _codes(body, "connection")
    assert body["run_ready"] is True                           # display only (R2 kind c)
    assert body["notes"][0]["section"] == "connection"


def test_loads_section(client, site, edit):
    name = site()
    with edit() as n:
        n.loads.loc["it", "p_set"] = float("nan")
    assert _states(_flow(client, name))["loads"] == "incomplete"   # broken
    with edit() as n:
        n.remove("Load", "it")
    body = _flow(client, name)
    assert _states(body)["loads"] == "incomplete"              # empty
    assert "presence_loads" in _codes(body, "loads")


def test_assets_section(client, site, edit):
    name = site()
    with edit() as n:
        n.generators.loc["gas", "p_nom_extendable"] = True
        n.generators.loc["gas", "p_nom_min"] = 50.0
        n.generators.loc["gas", "p_nom_max"] = 5.0
    body = _flow(client, name)
    assert _states(body)["assets"] == "incomplete"             # broken
    assert "generator_p_nom_bounds" in _codes(body, "assets")
    with edit() as n:
        n.remove("Generator", "gas")
    body = _flow(client, name)
    assert _states(body)["assets"] == "incomplete"             # empty: the import is not an asset
    assert "presence_assets" in _codes(body, "assets")


def test_profiles_section(client, site, edit):
    name = site()
    with edit() as n:
        n.add("Generator", "pv", bus="site", carrier="gas", p_nom=5.0,
              p_max_pu=pd.Series(0.0, index=n.snapshots))
    body = _flow(client, name)
    assert _states(body)["profiles"] == "warning"
    assert "timeseries_all_zero" in _codes(body, "profiles")


def test_commercial_section(client, site, set_cfg):
    name = site()
    set_cfg(finance={"discount_rate": "not a number"})
    body = _flow(client, name)
    rows = {s["section"]: s for s in body["sections"]}
    assert rows["commercial"]["state"] == "incomplete"
    assert rows["commercial"]["required"] is True
    assert "finance_inputs_invalid" in _codes(body, "commercial")
    # A bind issue is display-only (R1): /run's preflight does not refuse it.
    assert body["run_ready"] is True and body["run_blocker"] is None
    assert [n["section"] for n in body["notes"]] == ["commercial"]


def test_scenarios_section(client, site, set_cfg):
    name = site()
    set_cfg(solve_strategy="rolling", rolling_horizon=0)
    body = _flow(client, name)
    assert _states(body)["scenarios"] == "incomplete"          # broken
    assert "rolling_horizon_invalid" in _codes(body, "scenarios")
    # A reliability goal without VOLL: the validator's own check, filed here.
    set_cfg(solve_strategy="full", ens_cap_permyriad=1.0, voll=0.0)
    assert "ens_cap_without_voll" in _codes(_flow(client, name), "scenarios")
    set_cfg(voll=5000.0)
    body = _flow(client, name)
    assert "ens_cap_without_voll" not in _codes(body, "scenarios")    # filled
    assert "presence_voll" not in _codes(body, "scenarios")     # no study: not asked


# ── acknowledgements ────────────────────────────────────────────────────────

def test_a_warning_acknowledged_is_complete_until_its_value_changes(
        client, site, project_storage_dir, edit):
    name = site()
    with edit() as n:
        n.snapshot_weightings.loc[:, ["objective", "generators", "stores"]] = 3.0
    body = _flow(client, name)
    assert _states(body)["project"] == "warning"
    assert "snapshot_weights_nyears_off" in _codes(body, "project")

    r = client.post(f"/api/projects/{name}/flow/ack",
                    json={"code": "snapshot_weights_nyears_off", "entity": None})
    assert r.status_code == 200, r.text
    ack = r.json()["ack"]
    assert set(ack) == {"section", "code", "entity", "value_hash", "at"}
    assert ack["section"] == "project"
    stored = json.loads((project_storage_dir(name) / "flow_acks.json").read_text())
    assert stored["acks"] == [ack]
    body = _flow(client, name)
    assert _states(body)["project"] == "complete"
    row = next(s for s in body["sections"] if s["section"] == "project")
    assert row["items"][0]["acknowledged"] is True               # still listed, as checked
    # The title is added beside the message; the message (the acknowledged
    # value) is the validator's own text, not the title.
    assert row["items"][0]["title"] and row["items"][0]["message"] != row["items"][0]["title"]

    with edit() as n:
        n.snapshot_weightings.loc[:, ["objective", "generators", "stores"]] = 5.0
    assert _states(_flow(client, name))["project"] == "warning"  # re-raised


def test_an_error_cannot_be_acknowledged(client, site, edit):
    name = site()
    with edit() as n:
        n.loads.loc["it", "p_set"] = float("nan")
    r = client.post(f"/api/projects/{name}/flow/ack",
                    json={"code": "load_p_set_invalid", "entity": "it"})
    assert r.status_code == 422
    assert r.json()["detail"]["error_kind"] == "flow_ack_not_warning"
    r = client.post(f"/api/projects/{name}/flow/ack", json={"code": "no_such_code"})
    assert r.status_code == 404
    assert r.json()["detail"]["error_kind"] == "flow_item_unknown"


# ── deferred ────────────────────────────────────────────────────────────────

def test_a_deferred_preflight_is_unknown_everywhere(client, site, monkeypatch):
    name = site()
    monkeypatch.setattr(sim_router, "_solver_in_flight", lambda: True)
    body = _flow(client, name)
    assert body["deferred"] is True and body["run_ready"] is False
    assert {s: _states(body)[s] for s in INPUTS} == {s: "unknown" for s in INPUTS}
    assert body["run_blocker"]["section"] == "readiness"
    assert body["deferred_reason"]
    rows = {s["section"]: s for s in body["sections"]}
    assert rows["results"]["freshness"] == "running"
    r = client.post(f"/api/projects/{name}/flow/ack", json={"code": "x"})
    assert r.status_code == 409


# ── R4: run_ready is exactly the server's verdict ───────────────────────────
#
# The verdict is asked of the server itself, after the flow is read: the
# preflight (what /run validates with), and, for a project whose Run includes
# the reliability study, `POST /results/eh_study` with the Goal card's body (the
# study's worker is faked, so an accepted start returns at once).

def _server_verdict(client, monkeypatch, name) -> bool:
    import time

    from services import flow_service as S

    pre = client.post("/api/simulation/preflight").json()
    if pre.get("deferred"):
        return False
    ok = bool(pre["ok"])
    meta = client.get(f"/api/projects/{name}/eh_template")
    if meta.status_code == 200:
        monkeypatch.setattr("services.adequacy.eh_study.run_eh_study",
                            lambda *a, **k: {})
        r = client.post("/api/results/eh_study", json=S._request_for(meta.json()))
        assert r.status_code in (200, 202, 422), r.text
        ok = ok and r.status_code != 422
        for _ in range(100):                     # let the fake worker finish
            rec = client.get("/api/results/eh_study")
            if rec.status_code != 200 or rec.json().get("status") != "running":
                break
            time.sleep(0.05)
    return ok


def _assert_equivalent(client, monkeypatch, name) -> dict:
    body = _flow(client, name)
    assert body["run_ready"] == _server_verdict(client, monkeypatch, name), body["run_blocker"]
    assert (body["run_blocker"] is None) == body["run_ready"]
    return body


R4_TEMPLATES = ["eh_datacenter", "eh_datacenter_staged", "eh_h2_hub", "eh_h2_hub_staged",
                "eh_microgrid", "eh_microgrid_staged", "eh_site", "eh_ammonia_staged",
                "eh_ipp_hybrid"]


@pytest.mark.parametrize("tid", R4_TEMPLATES)
def test_r4_templates(client, tmp_projects_dir, monkeypatch, tid):
    kw = {"json": {"site_type": "industrial"}} if tid == "eh_site" else {}
    body = _assert_equivalent(client, monkeypatch, _create(client, tid, "r4", **kw))
    assert body["run_ready"] is True


def test_r4_ieee14(client, site, monkeypatch):
    import project_templates._build as classic

    body = _assert_equivalent(client, monkeypatch, site(classic.build_ieee14(), name="ieee14"))
    assert body["run_ready"] is True


@pytest.mark.parametrize("breakage", [
    lambda n: n.loads.loc.__setitem__(("it", "p_set"), float("nan")),
    lambda n: n.carriers.loc.__setitem__(("gas", "co2_emissions"), 0.0),
    lambda n: n.generators.loc.__setitem__(("gas", "efficiency"), -1.0),
    lambda n: n.remove("Load", "it"),
])
def test_r4_broken_sites(client, site, edit, monkeypatch, breakage):
    name = site()
    with edit() as n:
        breakage(n)
    _assert_equivalent(client, monkeypatch, name)


def test_r4_no_import_link_undeclared(client, site, edit, monkeypatch):
    """No import Link, not declared islanded: the server accepts the run, so
    Run is ready; connection reads incomplete, display only."""
    name = site()
    with edit() as n:
        n.remove("Link", "grid_import")
        n.remove("Generator", "market")
        n.remove("Bus", "grid")
    body = _assert_equivalent(client, monkeypatch, name)
    assert body["run_ready"] is True
    assert _states(body)["connection"] == "incomplete"
    assert [x["section"] for x in body["notes"]] == ["connection"]


def test_r4_off_grid_template_without_import_link(client, tmp_projects_dir, monkeypatch):
    """Declared islanded (the microgrid's off_grid archetype) and its tie
    removed: connection `not_needed`, Run as the server says."""
    name = _create(client, "eh_microgrid", "island_cut")
    r = client.delete("/api/network/links/subsea_tie")
    assert r.status_code in (200, 204), r.text
    body = _assert_equivalent(client, monkeypatch, name)
    assert _states(body)["connection"] == "not_needed"
    assert body["run_ready"] is False                   # the commercial config still names the tie
    assert body["run_blocker"]["section"] == "commercial", body["run_blocker"]


def test_r4_snapshots_empty(client, site, monkeypatch):
    """PyPSA refuses an empty snapshot index on a live network, so the
    validator's own `snapshots_empty` is injected where both /preflight and the
    flow read it (`routers.simulation.validate_for_run`)."""
    from services.validation_service import Issue

    name = site()
    monkeypatch.setattr(sim_router, "validate_for_run", lambda n, cfg: [
        Issue("error", "snapshots_empty", "", "", "Network has no snapshots.")])
    body = _assert_equivalent(client, monkeypatch, name)
    assert body["run_ready"] is False
    assert body["run_blocker"]["section"] == "project"
    assert _codes(body, "project") == ["snapshots_empty"]


def test_r4_study_with_voll_zero(client, tmp_projects_dir, monkeypatch):
    """The study starts with VOLL 0 (only its frontier and stress stages need
    it), so Run is ready and the gap is a scenarios note."""
    name = _create(client, "eh_h2_hub", "voll0")
    cfg = client.get("/api/simulation/solver_config").json()
    assert client.put("/api/simulation/solver_config", json={**cfg, "voll": 0.0}).status_code == 200
    body = _assert_equivalent(client, monkeypatch, name)
    assert body["run_ready"] is True
    assert body["verdicts"]["optimisation"]["ready"] is True
    assert body["verdicts"]["study"]["ready"] is True       # the study starts without VOLL
    assert "presence_voll" in _codes(body, "scenarios")
    # The template's buses are unplaced: the location is a note too.
    assert {x["section"] for x in body["notes"]} == {"project", "scenarios"}


def test_r4_a_study_start_refusal_blocks_run(client, tmp_projects_dir, monkeypatch,
                                             project_storage_dir):
    """The study's own refusal (here: an energy-import cap on a two-way import
    Link, `check_eh_study_start`) blocks Run in section 8."""
    name = _create(client, "eh_datacenter", "refused")
    path = project_storage_dir(name) / "eh_template.json"
    meta = json.loads(path.read_text())
    meta["pack_overrides"] = {**meta["pack_overrides"], "import_energy_mwh_per_year": 1000.0}
    path.write_text(json.dumps(meta))
    link = client.get("/api/network/links").json()
    grid = next(r for r in link if r["name"] == "grid_import")
    assert client.put("/api/network/links/grid_import",
                      json={**grid, "p_min_pu": -1.0}).status_code == 200
    body = _assert_equivalent(client, monkeypatch, name)
    assert body["run_ready"] is False
    assert body["run_blocker"]["section"] == "scenarios"
    assert "eh_study_refused" in _codes(body, "scenarios")
    # Per run kind: the optimisation is the preflight's verdict alone, so a
    # client driving Run LOPF is not blocked by the study's refusal.
    pre = client.post("/api/simulation/preflight").json()
    assert body["run_request"] == {"optimisation": True, "study": True, "loadflow": False}
    assert body["verdicts"]["optimisation"]["ready"] is pre["ok"] is True
    assert body["verdicts"]["study"]["ready"] is False
    assert body["verdicts"]["study"]["blocker"] == body["run_blocker"]
    assert body["run_verdict"] == body["verdicts"]["optimisation"]   # /run has no study


def test_r4_in_flight(client, site, monkeypatch):
    name = site()
    monkeypatch.setattr(sim_router, "_solver_in_flight", lambda: True)
    body = _assert_equivalent(client, monkeypatch, name)
    assert body["run_ready"] is False
    assert body["verdicts"]["optimisation"]["ready"] is False
    assert {s: _states(body)[s] for s in INPUTS} == {s: "unknown" for s in INPUTS}


# ── the templates at create (design §2.3) ───────────────────────────────────
#
# The state of each section right after create. Where it differs from the
# instantiation table, the comment says how (recorded in the PR, not papered
# over): the table's **R** inputs (location, the data centre's limits, ramp
# and LOLE goal) say "not yet confirmed" through the template's `assumed`
# ledger rows (DC grid readiness 02 adds connection_ramp, oscillation,
# import_efficiency, fault_level and lole_goal), and the site location is a
# warning by the brief's rule (the validator does not require it).
TEMPLATE_STATES = {
    # R location → warning (site_location_missing). R connection limits and
    # ramp, R LOLE goal: DC 02's assumed ledger rows → connection and
    # scenarios warning ("to check").
    "eh_datacenter": {"project": "warning", "carriers": "complete",
                      "connection": "warning", "loads": "complete",
                      "assets": "complete", "profiles": "complete",
                      "commercial": "complete", "scenarios": "warning"},
    # Islanded: connection `not_needed`. Assumed island data → assets warning.
    # DC 02's assumed LOLE goal → scenarios warning.
    "eh_microgrid": {"project": "warning", "carriers": "complete",
                     "connection": "not_needed", "loads": "complete",
                     "assets": "warning", "profiles": "complete",
                     "commercial": "warning", "scenarios": "warning"},
    # Ammonia (#418): electricity, H2 and NH3 pre-filled. Its assumed rows
    # (energization; tanks, electrolyser, UC; finance and the NH3 value) read warning.
    "eh_ammonia_staged": {"project": "warning", "carriers": "complete",
                          "connection": "warning", "loads": "complete",
                          "assets": "warning", "profiles": "complete",
                          "commercial": "warning", "scenarios": "complete"},
    # DC 02's assumed connection limits → connection warning; no LOLE goal
    # (the strong-grid pack sets an ENS cap only) → scenarios complete.
    "eh_h2_hub": {"project": "warning", "carriers": "complete",
                  "connection": "warning", "loads": "complete",
                  "assets": "warning", "profiles": "complete",
                  "commercial": "complete", "scenarios": "complete"},
    # Generation-only (IPP ticket 03): no Load, so loads `not_needed` and no study.
    # Assumed limits (connection) and finance, tax and charges (commercial) read warning.
    "eh_ipp_hybrid": {"project": "warning", "carriers": "complete",
                      "connection": "warning", "loads": "not_needed",
                      "assets": "complete", "profiles": "complete",
                      "commercial": "warning", "scenarios": "complete"},
}


@pytest.mark.parametrize("tid", sorted(TEMPLATE_STATES))
def test_template_states_at_create(client, tmp_projects_dir, tid):
    name = _create(client, tid, "tpl")
    body = _flow(client, name)
    assert {s: _states(body)[s] for s in INPUTS} == TEMPLATE_STATES[tid], json.dumps(
        {s["section"]: [i["code"] for i in s["items"]] for s in body["sections"]}, indent=1)
    assert body["run_ready"] is True
    assert body["run_ready"] == client.post("/api/simulation/preflight").json()["ok"]


def test_generic_site_states_at_create(client, tmp_projects_dir):
    name = _create(client, "eh_site", "plant", json={"site_type": "industrial"})
    body = _flow(client, name)
    states = _states(body)
    # Assumed: discount rate and finance (commercial), PoC losses, fault level
    # and the energization schedule (connection), the critical share (loads),
    # the existing site (assets), the money year (commercial).
    assert {s: states[s] for s in INPUTS} == {
        "project": "warning", "carriers": "complete", "connection": "warning",
        "loads": "warning", "assets": "warning", "profiles": "complete",
        "commercial": "warning", "scenarios": "complete"}
    assert "assumed_default" in _codes(body, "connection")
    assert body["run_ready"] is True


def test_an_assumed_default_acknowledged_is_re_raised_when_the_template_value_changes(
        client, tmp_projects_dir, project_storage_dir):
    name = _create(client, "eh_site", "plant2", json={"site_type": "industrial"})
    body = _flow(client, name)
    item = next(i for s in body["sections"] for i in s["items"]
                if i["code"] == "assumed_default" and i["target"]["entity"] == "fault level at the PoC")
    r = client.post(f"/api/projects/{name}/flow/ack",
                    json={"code": "assumed_default", "entity": item["target"]["entity"]})
    assert r.status_code == 200, r.text
    body = _flow(client, name)
    acked = next(i for s in body["sections"] for i in s["items"]
                 if i["target"]["entity"] == "fault level at the PoC")
    assert acked["acknowledged"] is True
    path = project_storage_dir(name) / "eh_template.json"
    meta = json.loads(path.read_text())
    for row in meta["ledger"]:
        if row["item"] == "fault level at the PoC":
            row["value"] = "999 MVA"
    path.write_text(json.dumps(meta))
    again = next(i for s in _flow(client, name)["sections"] for i in s["items"]
                 if i["target"]["entity"] == "fault level at the PoC")
    assert again["acknowledged"] is False


def test_an_islanded_microgrid_needs_no_connection(client, tmp_projects_dir):
    name = _create(client, "eh_microgrid", "island")
    rows = {s["section"]: s for s in _flow(client, name)["sections"]}
    assert rows["connection"]["state"] == "not_needed"
    assert rows["connection"]["required"] is False
    assert rows["connection"]["summary"] == "Off-grid: no grid connection needed"


@pytest.mark.parametrize("tid", R4_TEMPLATES)
def test_flow_answers_within_the_preflights_time(client, tmp_projects_dir, tid, capsys):
    """Done-when: GET /flow answers in about the preflight's time (measured, and
    printed for the PR). The cold call runs eh_readiness on a copy; the next
    one reads its cache (per network revision)."""
    kw = {"json": {"site_type": "industrial"}} if tid == "eh_site" else {}
    name = _create(client, tid, f"t_{tid}", **kw)
    t0 = time.perf_counter()
    client.post("/api/simulation/preflight")
    pre = time.perf_counter() - t0
    t0 = time.perf_counter()
    _flow(client, name)
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    _flow(client, name)
    warm = time.perf_counter() - t0
    with capsys.disabled():
        print(f"\n[flow timing] {tid}: preflight {pre * 1000:.0f} ms, flow cold "
              f"{cold * 1000:.0f} ms, warm {warm * 1000:.0f} ms")
    # The budget: generous absolute bounds, so a slow CI runner does not flake.
    # Measured locally: preflight 45-120 ms, cold 100-570 ms, warm 50-100 ms.
    assert cold < 10.0 and warm < 3.0


def test_the_acks_sidecar_travels_with_the_project():
    from routers.projects import _BUNDLE_FILES
    from services.flow_service import ACKS_FILE

    assert ACKS_FILE in _BUNDLE_FILES


def test_the_assistant_tool_returns_the_endpoints_payload(client, site, bound_ctx):
    from services import chat_tools
    from services.chat_tools_schema import TOOL_ROUTES, safety_tier_for

    name = site()
    assert TOOL_ROUTES["get_project_flow"] == [("GET", "/api/projects/{name}/flow")]
    assert safety_tier_for("get_project_flow") == "read"
    with bound_ctx(client):
        out = chat_tools.DISPATCHERS["get_project_flow"](name=name)
    assert out == _flow(client, name)


def test_a_missing_location_is_a_note_beside_an_enabled_run(client, site, edit):
    """Design §3.3: "Run is enabled with a note when only display-only gaps
    remain ('Project & site: no location')"."""
    name = site()
    with edit() as n:
        n.buses[["x", "y"]] = 0.0
    body = _flow(client, name)
    assert body["run_ready"] is True
    assert [x["section"] for x in body["notes"]] == ["project"]
    assert body["notes"][0]["message"].startswith("Project & site: ")


def test_a_held_network_lock_defers_the_flow_instead_of_waiting(client, tmp_projects_dir,
                                                                monkeypatch):
    """Gatekeeper: GET /flow is polled, so it never blocks on the lock. The
    study check runs after the preflight's span; a lock taken in between makes
    the answer `deferred`, within the budget, with run_ready false."""
    import threading

    from services.adequacy import eh_study_runner as R

    name = _create(client, "eh_h2_hub", "busy")
    real = R.check_eh_study_start
    held, release = threading.Event(), threading.Event()

    def holder(lock):
        with lock:
            held.set()
            release.wait(10)

    def check(*a, **k):
        # The request's own lock is taken just after the preflight released it.
        t = threading.Thread(target=holder, args=(PyPSAService.get_lock(),), daemon=True)
        t.start()
        held.wait(5)
        assert k.get("lock_blocking") is False
        return real(*a, **k)

    monkeypatch.setattr(R, "check_eh_study_start", check)
    t0 = time.perf_counter()
    try:
        body = _flow(client, name)
    finally:
        release.set()
    assert time.perf_counter() - t0 < 3.0
    assert body["deferred"] is True and body["run_ready"] is False
    assert {s: _states(body)[s] for s in INPUTS} == {s: "unknown" for s in INPUTS}
    assert body["verdicts"]["study"]["ready"] is False


def test_section_for_component_uses_the_flows_connection(client, site, bound_ctx):
    """LC-04's edit card names the section with the flow's own rule."""
    from services.flow_service import section_for_component

    site()
    with bound_ctx(client):
        n = PyPSAService.get_network()
        cfg = PyPSAService.get_solver_state()["solver_config"]
        assert section_for_component(n, cfg, None, "Link", "grid_import")["id"] == "connection"
        assert section_for_component(n, cfg, None, "Generator", "gas") == {
            "id": "assets", "label": "Assets & sizing", "panel": None}


def test_a_generation_only_site_with_a_load_is_held_to_the_study(client, tmp_projects_dir,
                                                                  edit, bound_ctx):
    """The generation-only rule is keyed on the network too: a Load added to an IPP project
    brings the loads rules back, and the VOLL rule and the study once the scope selects
    Reliability (W4a: the IPP preset leaves it off, so the scope decides). The flag is the
    PROJECT's stored template meta (`read_template`), so a created project carries it."""
    from services import flow_service

    name = _create(client, "eh_ipp_hybrid", "ipp")
    from services import network_lengths

    with bound_ctx(client):
        project_dir = network_lengths.active_project_dir()
    assert flow_service.read_template(project_dir)["generation_only"] is True
    fresh = _flow(client, name)
    assert _states(fresh)["loads"] == "not_needed"
    assert fresh["run_request"]["study"] is False
    with edit() as n:
        n.add("Load", "aux", bus="poi", p_set=1.0)
    body = _flow(client, name)
    assert _states(body)["loads"] in ("incomplete", "warning", "complete")
    assert "presence_loads" not in _codes(body, "loads")
    assert body["run_request"]["study"] is False             # Reliability off (its preset)
    scope = client.get(f"/api/projects/{name}/flow/scope").json()["scope"]
    r = client.put(f"/api/projects/{name}/flow/scope", json={
        "version": 1, "groups": [*scope["groups"], "reliability"],
        "options": {**scope["options"], "reliability": ["goal_study"]}})
    assert r.status_code == 200, r.text
    body = _flow(client, name)
    assert body["run_request"]["study"] is True
    assert "presence_voll" in _codes(body, "scenarios")      # the template ships VoLL 0



# ── Load flow, a run kind of its own (W4b) ──────────────────────────────────
#
# The Run requests the load flow exactly when What the Run computes › Load
# flow is on (`run_ac_pf_after_lopf`). Stage 2's own refusals are its items
# (`source: "loadflow"`), so they block the load flow's verdict, not the
# optimisation's; `run_ready` stays the AND over the requested kinds and agrees
# with /preflight, which validates /run with Stage 2 included.

def _pf_network(*, r: float = 0.05, x: float = 0.5) -> pypsa.Network:
    from tests.test_run_separability import _line_network

    n = _line_network()
    n.lines.loc["ab", ["r", "x"]] = [r, x]
    return n


def _every_item(body) -> list[dict]:
    return [i for s in body["sections"] for i in s["items"]]


def _wait_idle(client, timeout: float = 60.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get("/api/simulation/status").json()
        if st.get("status") != "running":
            return st
        time.sleep(0.2)
    raise AssertionError("the run did not finish")


def test_load_flow_off_is_not_requested(client, site):
    body = _flow(client, site(_pf_network(), name="lf_off"))
    assert body["run_request"] == {"optimisation": True, "study": False, "loadflow": False}
    assert body["verdicts"]["loadflow"] is None
    assert body["run_ready"] is True
    assert not any(i["source"] == "loadflow" for i in _every_item(body))


def test_a_zero_impedance_line_blocks_each_kind_once(client, site, set_cfg, monkeypatch):
    """x = 0 is the optimisation's own refusal; r = x = 0 is also Stage 2's.
    Each kind has its item, each shown once (UX), and /preflight agrees."""
    name = site(_pf_network(r=0.0, x=0.0), name="lf_zero")
    set_cfg(run_ac_pf_after_lopf=True)
    body = _assert_equivalent(client, monkeypatch, name)
    assert body["run_request"]["loadflow"] is True
    items = _every_item(body)
    stage2 = [i for i in items if i["code"] == "stage2_zero_impedance"]
    assert len(stage2) == 1 and stage2[0]["source"] == "loadflow" and stage2[0]["blocking"]
    assert [i["source"] for i in items if i["code"] == "line_x_invalid"] == ["validation"]
    assert body["verdicts"]["optimisation"]["blocker"]["code"] == "line_x_invalid"
    assert body["verdicts"]["loadflow"]["blocker"]["code"] == "stage2_zero_impedance"
    assert body["run_ready"] is False
    # /run's own verdict (the header's Run) is the first of the two in flow order.
    assert body["run_verdict"] == {"ready": False, "blocker": body["run_blocker"]}


def test_a_stage_two_refusal_blocks_the_load_flow_alone(client, site, set_cfg, monkeypatch):
    name = site(_pf_network(), name="lf_roll")
    set_cfg(run_ac_pf_after_lopf=True, solve_strategy="rolling")
    body = _assert_equivalent(client, monkeypatch, name)
    hits = [i for i in _every_item(body) if i["code"] == "rolling_with_ac_pf"]
    assert len(hits) == 1, hits
    assert hits[0]["source"] == "loadflow" and hits[0]["blocking"] and hits[0]["in_scope"]
    assert body["run_ready"] is False
    assert body["verdicts"]["optimisation"] == {"ready": True, "blocker": None}
    assert body["verdicts"]["loadflow"]["ready"] is False
    assert body["verdicts"]["loadflow"]["blocker"] == body["run_blocker"]
    assert body["run_blocker"]["code"] == "rolling_with_ac_pf"
    assert body["run_verdict"] == body["verdicts"]["loadflow"]
    assert not any(n["code"] == "rolling_with_ac_pf" for n in body["notes"])
    # Switched off, the same project is ready and its Run requests no load
    # flow; readiness counts the load flow's item once.
    set_cfg(run_ac_pf_after_lopf=False)
    off = _assert_equivalent(client, monkeypatch, name)
    assert off["run_ready"] is True and off["verdicts"]["loadflow"] is None
    assert off["run_verdict"] == off["verdicts"]["optimisation"]

    def todo(b) -> int:
        row = next(s for s in b["sections"] if s["section"] == "readiness")
        return int(row["summary"].split(" ", 1)[0])
    assert todo(body) == todo(off) + 1


def test_a_deferred_flow_answers_the_load_flow_not_ready(client, site, set_cfg, monkeypatch):
    name = site(_pf_network(), name="lf_busy")
    set_cfg(run_ac_pf_after_lopf=True)
    monkeypatch.setattr(sim_router, "_solver_in_flight", lambda: True)
    body = _flow(client, name)
    assert body["deferred"] is True
    assert body["run_request"]["loadflow"] is True
    assert body["verdicts"]["loadflow"]["ready"] is False
    assert _kinds(body)["loadflow"] == "running"


def _put(client, component: str, name: str, **fields) -> None:
    """An edit through the HTTP seam, which bumps the edit counter."""
    row = next(r for r in client.get(f"/api/network/{component}").json() if r["name"] == name)
    r = client.put(f"/api/network/{component}/{name}", json={**row, **fields})
    assert r.status_code == 200, r.text


def _kinds(body) -> dict:
    return next(s for s in body["sections"] if s["section"] == "results")["freshness_by_kind"]


def _bump(client, bound_ctx, times: int = 1) -> None:
    """An edit seam crossed without touching the dispatch (the chat seam)."""
    from services import dirty_state

    with bound_ctx(client):
        for _ in range(times):
            dirty_state.bump_revision()


def test_load_flow_freshness_follows_its_inputs(client, site, set_cfg, edit, bound_ctx):
    name = site(_pf_network(), name="lf_fresh")
    set_cfg(run_ac_pf_after_lopf=True)
    body = _flow(client, name)
    assert _kinds(body) == {"optimisation": "not_run", "study": None, "loadflow": "not_run"}

    assert client.post("/api/simulation/run").status_code == 200
    assert _wait_idle(client)["condition"] == "lopf+ac_pf_ok"
    body = _flow(client, name)
    assert _kinds(body) == {"optimisation": "fresh", "study": None, "loadflow": "fresh"}
    assert next(s for s in body["sections"] if s["section"] == "results")["freshness"] == "fresh"

    # An edit that leaves the dispatch's shape fresh: the power flow read the
    # network before it, so the load flow alone is stale.
    _bump(client, bound_ctx)
    assert _kinds(_flow(client, name)) == {"optimisation": "fresh", "study": None,
                                           "loadflow": "stale"}
    # A topology change: the dispatch itself is stale.
    with edit() as n:
        n.add("Load", "l2", bus="a", p_set=1.0)
    assert _kinds(_flow(client, name)) == {"optimisation": "stale", "study": None,
                                           "loadflow": "stale"}
    # An HTTP edit clears the dispatch: neither has results any more.
    _put(client, "loads", "l", p_set=11.0)
    assert _kinds(_flow(client, name)) == {"optimisation": "not_run", "study": None,
                                           "loadflow": "not_run"}


def test_a_standalone_load_flow_is_fresh_until_an_edit(client, site, bound_ctx):
    name = site(_pf_network(), name="lf_alone")
    assert client.post("/api/simulation/run").status_code == 200
    _wait_idle(client)
    # Not requested: no load flow results, so not run.
    assert _kinds(_flow(client, name))["loadflow"] == "not_run"
    assert client.post("/api/simulation/run_ac_pf").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
    assert _kinds(_flow(client, name))["loadflow"] == "fresh"
    _bump(client, bound_ctx)
    assert _kinds(_flow(client, name))["loadflow"] == "stale"


def test_load_flow_freshness_survives_a_save_and_reload(client, site, set_cfg, bound_ctx):
    """Gatekeeper L3: the edit counter travels with the results, so after a
    reload the same number of edits never reads fresh again."""
    name = site(_pf_network(), name="lf_reload")
    set_cfg(run_ac_pf_after_lopf=True)
    _bump(client, bound_ctx, 3)                           # the PF runs at a counter above 0
    assert client.post("/api/simulation/run").status_code == 200
    _wait_idle(client)
    r = client.post(f"/api/projects/{name}", params={"force": True})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/projects/{name}").status_code == 200
    assert _kinds(_flow(client, name))["loadflow"] == "fresh"
    _bump(client, bound_ctx)
    assert _kinds(_flow(client, name))["loadflow"] == "stale"
