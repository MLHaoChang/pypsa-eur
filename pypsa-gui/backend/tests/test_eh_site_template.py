"""The generic site template ``eh_site`` (generic-site E2E, slice G1).

One parametrized site for anything that is not a data center: a site type
preset (industrial, commercial, hospital, campus, other) with every parameter
overridable. Each preset builds a multi-period plan that follows the staged
data center's rules, ships commercial and finance blocks that bind, and is
physically plausible: every stage's modelled peak is at or below the
connection energized in it, so the investment case's grid-only comparison
runs under its own connection (owner, Q1).
"""
from __future__ import annotations

import json
import pathlib
import queue
import sys
import threading
from datetime import date

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_site as S  # noqa: E402

TID = "eh_site"


@pytest.fixture(scope="module", params=S.SITE_TYPES)
def site(request):
    p = S.site_params({"site_type": request.param})
    return p, S.build_eh_site(p)


def _years(p):
    return [s["year"] for s in p["stages"]]


# ── 1. the build ────────────────────────────────────────────────────────────

def test_every_stage_peak_is_the_preset_peak_and_fits_the_energized_connection(site):
    p, n = site
    total = n.loads_t.p_set.sum(axis=1)
    cap = n.links_t["eh_energization_mw"]["grid_import"]
    for s in p["stages"]:
        y = s["year"]
        assert total.loc[y].max() == pytest.approx(s["peak_mw"], rel=1e-9)
        assert total.loc[y].max() <= cap.loc[y].min() + 1e-9
        assert cap.loc[y].max() <= n.links.at["grid_import", "p_nom"] + 1e-9


def test_periods_weigh_a_year_each_and_their_own_length(site):
    p, n = site
    years = _years(p)
    assert list(n.investment_periods) == years
    w = n.investment_period_weightings
    want = S.period_years(years, p["horizon_end"])
    assert w["years"].tolist() == [float(want[y]) for y in years]
    assert w["objective"].tolist() == [float(want[y]) for y in years]
    assert sum(want.values()) == p["horizon_end"] - years[0]
    for y in years:
        assert n.snapshot_weightings.objective.loc[y].sum() == pytest.approx(8760.0)
    assert (n.snapshot_weightings.stores == 1.0).all()


def test_one_candidate_per_period_that_outlives_the_horizon(site):
    p, n = site
    for name in S.candidate_names(p):
        y = int(name.rsplit("_", 1)[1])
        df = n.storage_units if name.startswith("bess") else n.generators
        assert df.at[name, "build_year"] == y and bool(df.at[name, "p_nom_extendable"])
        assert y + float(df.at[name, "lifetime"]) >= p["horizon_end"], name


def test_the_existing_site_is_sunk_and_the_poc_chain_is_loss_free(site):
    p, n = site
    for a in S.existing_assets(p):
        for frame in ("generators", "storage_units", "links"):
            df = getattr(n, frame)
            if a in df.index:
                assert df.at[a, "overnight_cost"] == 0.0, a
    assert n.links.at["grid_import", "efficiency"] == 1.0
    assert n.links.at["critical_feeder", "efficiency"] == 1.0
    # the PoC meter link stays untyped (not an investment)
    assert n.links.at["grid_import", "overnight_cost"] != n.links.at["grid_import",
                                                                     "overnight_cost"]


def test_the_tags_make_it_an_energy_hub(site):
    p, n = site
    assert n.buses.index[n.buses.eh_poc].tolist() == ["grid"]
    assert n.buses.index[n.buses.eh_critical].tolist() == ["critical"]
    assert n.links.at["grid_import", "eh_role"] == "grid_import"
    assert n.loads.at["critical_load", "bus"] == "critical"
    # SCR at the PoC passes (the connection is not SCR-weak)
    assert n.buses.at["grid", "eh_sk_mva"] / n.buses.at["grid", "eh_ibr_mva"] > 3.0


def test_the_island_sidecar_is_valid_and_its_blocks_cover_the_critical_peak(site):
    from services.adequacy.island_config import validate

    p, _n = site
    isl = S.island_config(p)
    assert isl is not None                             # every preset has a UPS
    checked = validate(isl)
    from gridspine.schema.island import check_island

    check_island(checked, p["critical_share"] * p["stages"][-1]["peak_mw"])
    assert isl["units"]["ups_battery"]["it_load"] == ["critical_load"]
    for name in S.candidate_names(p):
        assert name in isl["units"], name


def test_a_hospital_starts_its_gensets_within_ten_seconds():
    isl = S.island_config(S.site_params({"site_type": "hospital"}))
    gens = [u for u in isl["units"].values() if u["kind"] == "genset"]
    assert gens and all(u["start_s"]["value"] + u["sync_s"]["value"] <= 10.0 for u in gens)


# ── 2. the parameters ───────────────────────────────────────────────────────

def test_a_stage_above_its_energized_connection_is_refused_naming_the_shortfall():
    with pytest.raises(S.SiteParamsError) as exc:
        S.site_params({"site_type": "hospital",
                       "energization": [{"year": 2027, "mw": 8.0}, {"year": 2030, "mw": 4.5},
                                        {"year": 2035, "mw": 8.0}]})
    msg = str(exc.value)
    assert "stage 2030" in msg and "short by 0.5 MW" in msg


def test_a_peak_above_the_connection_is_refused():
    with pytest.raises(S.SiteParamsError, match="stage 2035.*short by 2 MW"):
        S.site_params({"site_type": "industrial", "connection_mw": 13.0,
                       "stages": [{"year": 2027, "peak_mw": 8.0},
                                  {"year": 2035, "peak_mw": 15.0}]})


@pytest.mark.parametrize("raw, field", [
    ({"site_type": "spaceport"}, "site_type"),
    ({"peak": 3}, "peak"),
    ({"stages": []}, "stages"),
    ({"stages": [{"year": 2030, "peak_mw": 5}, {"year": 2027, "peak_mw": 6}]}, "stages"),
    ({"existing": {"gensets": {"count": 20}}}, "existing.gensets.count"),
    ({"existing": {"ups": {"mw": 1.0}}}, "existing.ups.mw"),
    ({"existing": {"ups": {"minutes": 5.0}}}, "existing.ups.minutes"),
    ({"critical_share": 0}, "critical_share"),
    ({"load_profile": "brewery"}, "load_profile"),
])
def test_bad_parameters_are_refused_naming_the_field(raw, field):
    with pytest.raises(S.SiteParamsError, match=field.replace(".", r"\.")) as exc:
        S.site_params(raw)
    assert exc.value.field == field


@pytest.mark.parametrize("raw, field", [
    ({"stages": [{"year": 2027, "peak_mw": float("nan")}]}, "stages[0].peak_mw"),
    ({"critical_share": True}, "critical_share"),
    ({"fault_level_mva": float("inf")}, "fault_level_mva"),
])
def test_a_non_number_is_refused_rather_than_coerced(raw, field):
    # NaN fails every bound check, True is an int, and fault level has no
    # ceiling: without the finite-number guard each of these would be stored.
    with pytest.raises(S.SiteParamsError, match="expected a number") as exc:
        S.site_params(raw)
    assert exc.value.field == field


@pytest.mark.parametrize("battery", [
    {"mw": 2.0, "mwh": 0.0},
    {"mw": 0.0, "mwh": 4.0},
])
def test_a_battery_needs_both_power_and_energy_or_neither(battery):
    with pytest.raises(S.SiteParamsError, match="give both mw and mwh") as exc:
        S.site_params({"existing": {"battery": battery}})
    assert exc.value.field == "existing.battery"


def test_the_horizon_must_outlast_the_last_stage():
    with pytest.raises(S.SiteParamsError, match=r"after the last stage \(2035\)") as exc:
        S.site_params({"horizon_end": 2035})
    assert exc.value.field == "horizon_end"


def test_a_horizon_one_year_past_the_last_stage_weighs_that_period_as_one_year():
    p = S.site_params({"horizon_end": 2036})
    n = S.build_eh_site(p)
    # 2027–2030 is 3 years, 2030–2035 is 5, 2035–2036 is 1.
    assert list(n.investment_periods) == [2027, 2030, 2035]
    assert n.investment_period_weightings["years"].tolist() == [3.0, 5.0, 1.0]
    assert n.investment_period_weightings["objective"].tolist() == [3.0, 5.0, 1.0]


def test_a_site_without_a_ups_ships_no_island_rows(tmp_path):
    p = S.site_params({"site_type": "hospital", "existing": {"ups": {"mw": 0.0}}})
    assert p["existing"]["ups"]["mw"] == 0.0
    assert S.island_config(p) is None
    n = S.build_eh_site(p)
    assert "ups_battery" not in n.storage_units.index
    assert "ups_battery" not in S.existing_assets(p)
    assert "island_constraints" not in S.solver_config(p, n)
    S.write_sidecars(tmp_path, p, n)
    assert not (tmp_path / "island_config.json").exists()
    meta = json.loads((tmp_path / "eh_template.json").read_text())
    assert any("No UPS" in note for note in meta["study_notes"])


def test_overrides_lay_over_the_preset():
    p = S.site_params({"site_type": "hospital", "connection_mw": 10.0,
                       "stages": [{"year": 2028, "peak_mw": 7.0}],
                       "existing": {"pv_mw": 0.0}, "candidates": {"pv": {"enabled": False}}})
    assert p["existing"]["gensets"]["count"] == 3                    # from the preset
    # the UPS follows the critical load it protects unless it is set
    assert p["existing"]["ups"]["mw"] == pytest.approx(1.1 * 0.5 * 7.0)
    assert p["energization"] == [{"year": 2028, "mw": 10.0}]
    n = S.build_eh_site(p)
    assert list(n.investment_periods) == [2028]
    assert "site_pv" not in n.generators.index
    assert not any(g.startswith("pv_new") for g in n.generators.index)
    assert S.site_params(p) == p                                     # idempotent


# ── 3. the commercial and finance blocks ───────────────────────────────────

def _no_ref(ref):
    raise AssertionError(f"the template's commercial config names a Library ref: {ref!r}")


def test_the_commercial_block_binds_unchanged_and_the_finance_inputs_validate(site):
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs
    from services.commercial import binding

    p, n = site
    cfg = S.solver_config(p, n)
    parsed = CommercialConfig.model_validate(cfg["commercial"])
    assert parsed.poc_link == "grid_import" and parsed.import_tariff is not None
    assert binding.bind_commercial(n, parsed, project_dir=None, resolve_ref=_no_ref,
                                   resolve_item=_no_ref) == cfg["commercial"]
    owned = {o["asset_id"] for o in cfg["commercial"]["value_flows"]["asset_owners"]}
    assert set(S.existing_assets(p)) | set(S.candidate_names(p)) <= owned
    fin = FinanceInputs.model_validate(cfg["finance"])
    assert cfg["finance"] == fin.model_dump(mode="json")
    first = _years(p)[0]
    assert fin.cod_by_asset == {a: date(first, 1, 1) for a in S.existing_assets(p)}
    assert first + fin.analysis_years == p["horizon_end"]
    assert fin.wacc_nominal == cfg["discount_rate"]
    assert cfg["multi_investment_periods"] and cfg["island_constraints"]


def test_the_ledger_states_the_assumptions():
    meta = S.template_meta(S.site_params({"site_type": "industrial"}))
    items = {r["item"]: r for r in meta["ledger"]}
    assert "industrial_two_shift" in items["load shape"]["note"]
    assert "PF 0.98" in items["power factor and load damping"]["value"]
    assert "not modelled" in items["PoC and feeder losses"]["note"]
    assert items["import tariff"]["source"] == "synthetic"
    assert meta["site_type"] == "industrial" and meta["recommended_archetype"] == "weak_flexible"
    assert [r["peak_mw"] for r in meta["projected_load"]] == [8.0, 12.0, 15.0]


# ── 4. the route ────────────────────────────────────────────────────────────

def test_from_template_builds_the_site_from_the_body_and_binds_its_blocks(
        client, tmp_projects_dir):
    body = {"site_type": "hospital", "connection_mw": 10.0}
    r = client.post(f"/api/projects/from_template/{TID}", params={"name": "clinic"}, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == "clinic"
    assert r.json().get("solver_config_issues", []) == []
    p = S.site_params(body)
    cfg = client.get("/api/simulation/solver_config").json()
    want = S.solver_config(p)
    assert cfg["commercial"] == want["commercial"]
    assert client.get("/api/simulation/finance").json()["finance"] == want["finance"]
    meta = client.get("/api/projects/clinic/eh_template")
    assert meta.status_code == 200, meta.text
    assert meta.json()["site_type"] == "hospital"
    assert meta.json()["pack_overrides"] == {"import_p_nom_mw": 10.0}
    # the fields the user set, told from the preset's defaults
    assert meta.json()["entered"] == ["connection_mw", "site_type"]


def test_from_template_without_a_body_uses_the_default_preset(client, tmp_projects_dir):
    r = client.post(f"/api/projects/from_template/{TID}")
    assert r.status_code == 200, r.text
    assert r.json()["imported"].startswith("Industrial plant Energy Hub")


def test_a_refused_site_creates_no_project(client, tmp_projects_dir):
    before = {row["name"] for row in client.get("/api/projects/").json()}
    r = client.post(f"/api/projects/from_template/{TID}", params={"name": "too_big"},
                    json={"site_type": "commercial", "connection_mw": 3.0})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "site_params_invalid" and detail["field"] == "stages[2].peak_mw"
    assert "stage 2035" in detail["message"] and "short by 1 MW" in detail["message"]
    assert {row["name"] for row in client.get("/api/projects/").json()} == before


def test_other_templates_take_no_parameters(client, tmp_projects_dir):
    r = client.post("/api/projects/from_template/eh_microgrid", json={"site_type": "hospital"})
    assert r.status_code == 422


# ── 5. the plan solves and the investment case computes ────────────────────

@pytest.fixture
def solved_case(site, reset_backend, monkeypatch, tmp_path):
    """The site solved multi-period through run_simulation with its own
    solver settings (island rows from its sidecar), then the finance case."""
    import routers.results as R
    import routers.simulation as sim_router
    from models.finance import FinanceInputs
    from routers.projects import _solver_config_from_dict
    from services.adequacy.island_config import SIDECAR_NAME, load_island_config
    from services.finance.engine import run_case
    from services.pypsa_service import PyPSAService
    from services.results.finance_case import build_finance_case
    from services.solver import island as I
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    p, _n = site
    (tmp_path / SIDECAR_NAME).write_text(json.dumps(S.island_config(p)))
    isl = load_island_config(tmp_path / SIDECAR_NAME)
    isl["cuts"] = []
    monkeypatch.setattr(I, "load_project_island", lambda: isl)

    install_network_into_backend(S.build_eh_site(p))
    cfg = _solver_config_from_dict(S.solver_config(p))
    sim_router._state["solver_config"] = cfg
    n = PyPSAService.get_network()
    state: dict = {}
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: state.update(k))
    assert status in ("ok", "optimal"), (p["site_type"], status, cond)
    case = build_finance_case(n, cfg, FinanceInputs.model_validate(cfg.finance),
                              result_df=R._result_df, lost_load=state.get("last_lost_load"))
    return p, n, case, run_case(case)


@pytest.mark.live_solve
def test_every_preset_solves_to_an_investment_case_against_a_feasible_grid_only_site(solved_case):
    p, n, case, r = solved_case
    first = _years(p)[0]
    assets = {a.name: a for a in case.assets}
    for a in S.existing_assets(p):
        assert a in assets and assets[a].overnight_cost == 0.0, a
    assert case.cod == date(first, 1, 1)
    # The grid-only comparison serves the load under the connection.
    assert "counterfactual_exceeds_connection" not in r.flags, r.flags
    assert not [f for f in r.flags if f.startswith("counterfactual_not_established")], r.flags
    assert r.op.status == {"operating": "ok", "capex": "ok", "terminal": "ok"}, r.op.reasons
    for k in ("project_pre_tax_npv",):
        assert r.metrics[k] is not None, (k, r.reasons)
    # The import never exceeds the energized connection.
    cap = n.links_t["eh_energization_mw"]["grid_import"]
    assert (n.links_t.p0["grid_import"] <= cap + 1e-4).all()


def test_the_assistant_creates_a_site_and_still_creates_the_other_templates(
        client, tmp_projects_dir):
    """The chat tool calls the route handler as a plain function: `params`
    must reach it as a value (a raw `Body` default would refuse every
    template), and the site's parameters must reach eh_site."""
    from harness import catalogue
    from services import chat_tools

    tool = next(t for t in catalogue.TOOLS if t["name"] == "create_project_from_template")
    assert "eh_site" in tool["input_schema"]["properties"]["template_id"]["enum"]
    assert "params" in tool["input_schema"]["properties"]
    create = chat_tools.DISPATCHERS["create_project_from_template"]
    assert create(template_id="eh_microgrid", new_name="chat_island")["imported"] == "chat_island"
    out = create(template_id=TID, new_name="chat_clinic", params={"site_type": "hospital"})
    assert out["imported"] == "chat_clinic" and out.get("solver_config_issues", []) == []
    meta = client.get("/api/projects/chat_clinic/eh_template").json()
    assert meta["site_type"] == "hospital" and meta["entered"] == ["site_type"]
