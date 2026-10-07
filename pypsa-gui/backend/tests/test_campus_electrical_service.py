"""Campus electrical study, the service (plan C6).

A capacity-expansion (hub) project, once solved and saved, can be taken to
its electrical design. The service lets the user:
* draft a campus description from the project's own network;
* edit and save that description;
* run the study (prepare, rank and size the campus, then buy the electrical
  assets from the library);
* keep a per-project copy of the asset library (plan C9);
* read the results back, with the hub's own system cost beside the
  electrical cost.

The service refuses:
* a project of another kind (409);
* a project not yet saved, or saved but not solved (422);
* a description that does not build (422, naming the field);
* a draft that would overwrite the user's edits without being asked (409);
* a library that does not load (422, naming the entry and field) or is over
  the size cap (413).
"""
import uuid

import numpy as np
import pandas as pd
import pypsa
import pytest
import yaml
from fastapi import HTTPException

from db.models import Project, User
from services import campus_electrical_service as ce
from services import gridspine_service as gs
from services import project_registry

H = 12


@pytest.fixture
def user_and_db(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        yield db, db.get(User, seeded_identity["user_id"])


def hub_network(solved=True, priced=False):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-07-01", periods=H, freq="h"))
    n.add("Bus", "grid", v_nom=110.0)
    n.add("Bus", "mv", v_nom=20.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=500.0)
    n.add("Link", "grid_import", bus0="grid", bus1="mv", p_nom=60.0, efficiency=0.99)
    n.add("Load", "dc_load", bus="mv", p_set=40.0)
    n.add("Generator", "pv", bus="mv", carrier="solar", p_nom=20.0)
    n.add("StorageUnit", "bess", bus="mv", carrier="battery", p_nom=10.0, max_hours=2.0)
    if priced:
        n.generators.loc["grid_supply", "marginal_cost"] = 50.0
    n.buses["eh_poc"] = [True, False]
    n.buses["eh_sk_mva"] = [2000.0, float("nan")]
    if solved:
        k = np.arange(H)
        pv = np.clip(np.sin(np.pi * k / H), 0, None) * 20.0
        bess = np.where(k % 2 == 0, -5.0, 5.0)
        load = np.full(H, 40.0)
        n.generators_t.p = pd.DataFrame({"pv": pv, "grid_supply": load - pv - bess}, index=n.snapshots)
        n.storage_units_t.p = pd.DataFrame({"bess": bess}, index=n.snapshots)
        n.loads_t.p = pd.DataFrame({"dc_load": load}, index=n.snapshots)
        n.links_t.p0 = pd.DataFrame({"grid_import": (load - pv - bess) / 0.99}, index=n.snapshots)
        n.links_t.p1 = -0.99 * n.links_t.p0
    return n


@pytest.fixture
def hub(user_and_db):
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Campus Hub")
    hub_network(priced=True).export_to_netcdf(str(project_registry.ensure_project_dir(row) / "network.nc"))
    return row


@pytest.fixture
def study(user_and_db):
    db, user = user_and_db
    created = gs.create_study(db, user, "Planning Study", config={"hours": 24, "k": 1, "window": 24, "overlap": 0})
    return db.get(Project, uuid.UUID(created["id"]))


@pytest.mark.parametrize("action", [
    lambda p: ce.get_state(p),
    lambda p: ce.draft(p),
    lambda p: ce.save_campus(p, "campus: {}"),
    lambda p: ce.run(p, {}),
    lambda p: ce.get_library(p),
    lambda p: ce.save_library(p, "x: 1"),
    lambda p: ce.reset_library(p),
    lambda p: ce.get_investment(p),
])
def test_every_action_refuses_a_project_of_another_kind(study, action):
    with pytest.raises(HTTPException) as exc:
        action(study)
    assert exc.value.status_code == 409 and "capacity-expansion" in exc.value.detail


def test_a_project_never_saved_cannot_be_drafted(user_and_db):
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Never Saved")
    with pytest.raises(HTTPException) as exc:
        ce.draft(row)
    assert exc.value.status_code == 422 and "save" in exc.value.detail


def test_a_project_saved_but_not_solved_cannot_be_drafted(user_and_db):
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Unsolved")
    hub_network(solved=False).export_to_netcdf(str(project_registry.ensure_project_dir(row) / "network.nc"))
    with pytest.raises(HTTPException) as exc:
        ce.draft(row)
    assert exc.value.status_code == 422 and "solve" in exc.value.detail


def test_a_draft_comes_from_the_project_and_is_kept_as_the_campus_file(hub):
    out = ce.draft(hub)
    spec = yaml.safe_load(out["campus_yaml"])
    assert spec["campus"]["pcc"]["pypsa_name"] == "grid"
    state = ce.get_state(hub)
    assert state["campus_yaml"] == out["campus_yaml"] and state["results"] is None
    assert "eu_rfg_dcc_ce" in state["profiles"]


def test_drafting_again_does_not_overwrite_edits_unless_asked(hub):
    ce.draft(hub)
    with pytest.raises(HTTPException) as exc:
        ce.draft(hub)
    assert exc.value.status_code == 409 and "overwrite" in exc.value.detail
    assert ce.draft(hub, overwrite=True)["campus_yaml"]


def test_a_saved_campus_is_validated_and_a_bad_one_is_refused_with_the_field(hub):
    spec = yaml.safe_load(ce.draft(hub)["campus_yaml"])
    spec["campus"]["pcc"]["sk_min_mva"]["value"] = 1e9
    with pytest.raises(HTTPException) as exc:
        ce.save_campus(hub, yaml.safe_dump(spec))
    assert exc.value.status_code == 422 and "sk_min_mva" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        ce.save_campus(hub, "campus: [unclosed")
    assert exc.value.status_code == 422 and "YAML" in exc.value.detail
    spec["campus"]["pcc"]["sk_min_mva"]["value"] = 1500.0
    ce.save_campus(hub, yaml.safe_dump(spec))
    assert yaml.safe_load(ce.get_state(hub)["campus_yaml"]) == spec


def test_an_oversized_campus_file_is_refused(hub):
    with pytest.raises(HTTPException) as exc:
        ce.save_campus(hub, "x" * (ce.MAX_CAMPUS_BYTES + 1))
    assert exc.value.status_code == 413


def test_running_without_a_campus_asks_for_one(hub):
    with pytest.raises(HTTPException) as exc:
        ce.run(hub, {})
    assert exc.value.status_code == 422 and "campus" in exc.value.detail


def test_a_run_sizes_the_campus_and_its_results_read_back(hub):
    ce.draft(hub)
    out = ce.run(hub, {"k": 1, "pf": 0.95, "margin": 0.1, "n_minus_1": True})
    res = out["results"]
    assert [r["check"] for r in res["compliance"]] == [
        "pcc_reactive", "pcc_voltage", "campus_voltage", "transformer_loading", "switchgear"]
    assert res["transformers"][0]["margin"] == 0.1
    assert res["requirement"]["pf"] == 0.95 and res["requirement"]["source"] == "assumed"
    assert res["selection"] and {"period", "hour", "reasons"} <= set(res["selection"][0])
    assert res["short_circuit"] and res["compensation"]
    state = ce.get_state(hub)
    assert state["results"] == res and state["settings"]["pf"] == 0.95 and not state["stale"]


def test_results_turn_stale_when_the_campus_changes(hub):
    spec = yaml.safe_load(ce.draft(hub)["campus_yaml"])
    ce.run(hub, {"k": 1})
    spec["campus"]["pcc"]["sk_max_mva"]["value"] = 2500.0
    ce.save_campus(hub, yaml.safe_dump(spec))
    assert ce.get_state(hub)["stale"]


@pytest.mark.parametrize("settings, match", [
    ({"k": 0}, "k"),
    ({"pf": 1.5}, "pf"),
    ({"margin": -0.1}, "margin"),
    ({"profile": "nowhere"}, "nowhere"),
])
def test_bad_settings_are_refused(hub, settings, match):
    ce.draft(hub)
    with pytest.raises(HTTPException) as exc:
        ce.run(hub, settings)
    assert exc.value.status_code == 422 and match in exc.value.detail


def test_the_defaults_match_the_engines(hub):
    from gridspine.drivers import campus_study
    assert ce.DEFAULTS["k"] == campus_study.DEFAULT_K
    assert ce.DEFAULTS["profile"] == campus_study.DEFAULT_PROFILE


def test_a_build_without_the_engine_answers_503(hub, monkeypatch):
    monkeypatch.setattr(ce, "CAMPUS_AVAILABLE", False)
    monkeypatch.setattr(ce, "CAMPUS_IMPORT_ERROR", "no gridspine")
    with pytest.raises(HTTPException) as exc:
        ce.get_state(hub)
    assert exc.value.status_code == 503 and "no gridspine" in exc.value.detail


# --------------------------------------------------------------------------
# the asset library and the investment step (plan C9)
# --------------------------------------------------------------------------

def default_library_text() -> str:
    from gridspine.drivers import campus_study
    return campus_study.ASSET_LIBRARY_PATH.read_text()


def library_with(change) -> str:
    data = yaml.safe_load(default_library_text())
    change(data)
    return yaml.safe_dump(data, sort_keys=False)


def scaled_capex(factor):
    def change(data):
        for kind in ("transformers", "capacitor_banks", "shunt_reactors", "statcoms", "switchgear"):
            for e in data[kind]:
                e["capex_eur"]["value"] *= factor
        for e in data["cables"]:
            e["capex_eur_per_km"]["value"] *= factor
    return change


def test_the_library_is_the_shipped_default_until_the_user_saves_a_copy(hub):
    out = ce.get_library(hub)
    assert out["is_default"] is True and out["yaml"] == default_library_text()
    assert not (ce.campus_dir(hub) / ce.LIBRARY_FILE).exists()


def test_a_saved_library_is_kept_in_the_project_and_read_back(hub):
    text = library_with(scaled_capex(2))
    out = ce.save_library(hub, text)
    assert out == {"yaml": text, "is_default": False}
    assert (ce.campus_dir(hub) / ce.LIBRARY_FILE).read_text() == text
    assert ce.get_library(hub) == {"yaml": text, "is_default": False}


def test_a_library_that_does_not_load_is_refused_with_the_field_and_nothing_is_kept(hub):
    def break_it(data):
        data["transformers"][0]["capex_eur"]["value"] = -5
    with pytest.raises(HTTPException) as exc:
        ce.save_library(hub, library_with(break_it))
    assert exc.value.status_code == 422
    assert "capex_eur" in exc.value.detail and data_id(0) in exc.value.detail
    assert ce.get_library(hub)["is_default"] is True


def data_id(i):
    return yaml.safe_load(default_library_text())["transformers"][i]["id"]


def test_a_missing_field_is_named(hub):
    def drop(data):
        del data["cables"][0]["max_i_ka"]
    with pytest.raises(HTTPException) as exc:
        ce.save_library(hub, library_with(drop))
    assert exc.value.status_code == 422 and "max_i_ka" in exc.value.detail


@pytest.mark.parametrize("text, match", [("transformers: [unclosed", "YAML"), ("- just\n- a list\n", "mapping")])
def test_a_library_that_is_not_a_yaml_mapping_is_refused(hub, text, match):
    with pytest.raises(HTTPException) as exc:
        ce.save_library(hub, text)
    assert exc.value.status_code == 422 and match in exc.value.detail
    assert ce.get_library(hub)["is_default"] is True


def test_an_oversized_library_is_refused_and_a_bad_save_keeps_the_previous_copy(hub):
    good = library_with(scaled_capex(2))
    ce.save_library(hub, good)
    with pytest.raises(HTTPException) as exc:
        ce.save_library(hub, "x" * (ce.MAX_LIBRARY_BYTES + 1))
    assert exc.value.status_code == 413
    with pytest.raises(HTTPException):
        ce.save_library(hub, "not: a library")
    assert ce.get_library(hub)["yaml"] == good
    assert sorted(p.name for p in ce.campus_dir(hub).iterdir() if p.is_file()) == [ce.LIBRARY_FILE]


def test_a_library_exactly_at_the_size_cap_is_not_refused_for_size(hub):
    text = default_library_text()
    text += "\n" + "#" * (ce.MAX_LIBRARY_BYTES - len(text.encode()) - 1)
    assert len(text.encode()) == ce.MAX_LIBRARY_BYTES
    assert ce.save_library(hub, text)["is_default"] is False


def test_reset_deletes_the_project_copy(hub):
    ce.save_library(hub, library_with(scaled_capex(2)))
    out = ce.reset_library(hub)
    assert out == {"yaml": default_library_text(), "is_default": True}
    assert not (ce.campus_dir(hub) / ce.LIBRARY_FILE).exists()
    assert ce.reset_library(hub)["is_default"] is True          # nothing to delete is not an error


INVESTMENT_KEYS = {"need", "library_id", "kind", "units", "invest_period", "capex_eur", "annualised_eur_per_a",
                   "existing", "status", "reason"}


def test_a_run_buys_the_assets_and_the_state_reads_them_back(hub):
    ce.draft(hub)
    out = ce.run(hub, {"k": 1, "pf": 0.95})
    res = out["results"]
    assert res["investment"] and INVESTMENT_KEYS <= set(res["investment"][0])
    chosen = [r for r in res["investment"] if r["status"] == "chosen"]
    assert chosen and all(r["capex_eur"] > 0 and r["annualised_eur_per_a"] > 0 for r in chosen)
    assert res["cost"][0]["period"] == 2030
    assert res["cost"][0]["capex_eur"] == pytest.approx(sum(r["capex_eur"] for r in chosen))
    assert res["cost"][0]["annualised_eur_per_a"] == pytest.approx(sum(r["annualised_eur_per_a"] for r in chosen))
    assert "value_with_measures" in res["compliance_invested"][0]
    assert {r["check"] for r in res["compliance_invested"]} >= {"pcc_reactive", "transformer_loading", "cable_loading"}
    assert res["history"] == [] and res["unresolved"] == []
    assert ce.get_state(hub)["results"] == res and out["settings"]["invest"] is True


def test_a_run_without_invest_has_no_investment_and_clears_the_previous_one(hub):
    ce.draft(hub)
    first = ce.run(hub, {"k": 1, "invest": True})["results"]
    assert first["investment"]
    run_dir = ce.campus_dir(hub) / "run"
    assert (run_dir / ce.cs.INVESTMENT_CSV).is_file()
    out = ce.run(hub, {"k": 1, "invest": False})
    res = out["results"]
    for key in ("investment", "cost", "compliance_invested", "history", "unresolved"):
        assert res[key] is None, key
    assert res["compliance"] and out["settings"]["invest"] is False
    for name in (ce.cs.INVESTMENT_CSV, ce.cs.COST_CSV, ce.cs.INVESTED_YAML, ce.cs.COMPLIANCE_INVESTED_CSV,
                 ce.cs.INVEST_HISTORY_CSV, ce.cs.INVEST_DISPATCH_CSV, ce.USED_LIBRARY_FILE):
        assert not (run_dir / name).exists(), name
    assert not out["stale"]


def test_invest_defaults_to_on_and_the_operator_owns_no_switchgear_by_default():
    assert ce.DEFAULTS["invest"] is True
    assert ce.DEFAULTS["pcc_switchgear_by_operator"] is False


def pcc_switchgear_rows(res):
    return [r for r in res["investment"] if r["need"] == "switchgear GRID"]


def test_the_pcc_switchgear_is_costed_to_the_campus_unless_the_operator_owns_it(hub):
    ce.draft(hub)
    mine = ce.run(hub, {"k": 1, "pf": 0.95})
    assert mine["results"]["scope"] == {"pcc_switchgear": "campus"}
    assert mine["settings"]["pcc_switchgear_by_operator"] is False
    assert [r["status"] for r in pcc_switchgear_rows(mine["results"])] == ["chosen"]
    theirs = ce.run(hub, {"k": 1, "pf": 0.95, "pcc_switchgear_by_operator": True})
    assert theirs["results"]["scope"] == {"pcc_switchgear": "grid_operator"}
    assert theirs["settings"]["pcc_switchgear_by_operator"] is True
    assert not [r for r in pcc_switchgear_rows(theirs["results"]) if r["status"] == "chosen"]
    assert theirs["results"]["cost"][0]["capex_eur"] < mine["results"]["cost"][0]["capex_eur"]


def test_the_engine_is_told_who_buys_the_pcc_switchgear(hub, monkeypatch):
    ce.draft(hub)
    seen = []
    real = ce.cs.invest_campus
    monkeypatch.setattr(ce.cs, "invest_campus",
                        lambda run_dir, library=None, **kw: seen.append(kw["pcc_switchgear"]) or real(run_dir, library, **kw))
    ce.run(hub, {"k": 1})
    ce.run(hub, {"k": 1, "pcc_switchgear_by_operator": True})
    assert seen == [True, False]


def test_a_run_without_invest_has_no_scope(hub):
    ce.draft(hub)
    ce.run(hub, {"k": 1})
    out = ce.run(hub, {"k": 1, "invest": False})
    assert out["results"]["scope"] is None
    assert not (ce.campus_dir(hub) / "run" / ce.cs.INVEST_SCOPE_JSON).exists()


def test_a_run_buys_from_the_project_library_and_the_shipped_one_without_a_copy(hub):
    ce.draft(hub)
    base = ce.run(hub, {"k": 1})["results"]["cost"][0]["capex_eur"]
    ce.save_library(hub, library_with(scaled_capex(2)))
    doubled = ce.run(hub, {"k": 1})["results"]["cost"][0]["capex_eur"]
    assert doubled == pytest.approx(2 * base)
    ce.reset_library(hub)
    assert ce.run(hub, {"k": 1})["results"]["cost"][0]["capex_eur"] == pytest.approx(base)


def test_the_engine_gets_the_project_library_or_none(hub, monkeypatch):
    ce.draft(hub)
    seen = []
    real = ce.cs.invest_campus

    def spy(run_dir, library=None, **kw):
        seen.append((library, None if library is None else open(library).read(), kw))
        return real(run_dir, library, **kw)
    monkeypatch.setattr(ce.cs, "invest_campus", spy)
    ce.run(hub, {"k": 1, "pf": 0.95, "profile": "eu_rfg_dcc_ce", "margin": 0.1, "n_minus_1": False})
    library, _text, kw = seen[0]
    assert library is None
    assert kw["pf"] == 0.95 and kw["profile"] == "eu_rfg_dcc_ce"
    assert kw["criteria"].margin == 0.1 and kw["criteria"].n_minus_1 is False
    assert tuple(kw["profile_dirs"]) == (ce.grid_codes_dir(hub),)
    text = library_with(scaled_capex(2))
    ce.save_library(hub, text)
    ce.run(hub, {"k": 1})
    library, content, _kw = seen[1]
    assert library is not None and content == text


def test_a_library_that_loads_no_more_is_a_422_at_run_not_a_500(hub):
    ce.draft(hub)
    (ce.campus_dir(hub) / ce.LIBRARY_FILE).write_text("transformers: []\n")          # hand-edited on disk
    with pytest.raises(HTTPException) as exc:
        ce.run(hub, {"k": 1})
    assert exc.value.status_code == 422


def test_results_turn_stale_when_the_library_changes_and_not_otherwise(hub):
    ce.draft(hub)
    ce.run(hub, {"k": 1})
    assert not ce.get_state(hub)["stale"]
    ce.save_library(hub, library_with(scaled_capex(2)))
    assert ce.get_state(hub)["stale"]
    ce.run(hub, {"k": 1})
    assert not ce.get_state(hub)["stale"]
    ce.reset_library(hub)                                    # back to the default, which the run did not use
    assert ce.get_state(hub)["stale"]
    ce.run(hub, {"k": 1})
    assert not ce.get_state(hub)["stale"]


def test_a_run_without_invest_does_not_go_stale_with_the_library(hub):
    ce.draft(hub)
    ce.run(hub, {"k": 1, "invest": False})
    ce.save_library(hub, library_with(scaled_capex(2)))
    assert not ce.get_state(hub)["stale"]


def test_a_changed_default_library_makes_a_default_run_stale(hub, monkeypatch, tmp_path):
    ce.draft(hub)
    ce.run(hub, {"k": 1})
    other = tmp_path / "default.yaml"
    other.write_text(library_with(scaled_capex(3)))
    monkeypatch.setattr(ce.cs, "ASSET_LIBRARY_PATH", other)
    assert ce.get_state(hub)["stale"]


def test_the_run_keeps_the_library_it_used(hub):
    ce.draft(hub)
    text = library_with(scaled_capex(2))
    ce.save_library(hub, text)
    ce.run(hub, {"k": 1})
    assert (ce.campus_dir(hub) / "run" / ce.USED_LIBRARY_FILE).read_text() == text


# the hub's own system cost, beside the electrical one

def test_the_state_carries_the_hubs_system_cost_once_a_study_has_run(hub):
    assert ce.get_state(hub)["hub_cost"] is None
    ce.draft(hub)
    out = ce.run(hub, {"k": 1})
    cost = out["hub_cost"]
    k = np.arange(H)
    pv = np.clip(np.sin(np.pi * k / H), 0, None) * 20.0
    bess = np.where(k % 2 == 0, -5.0, 5.0)
    expected = float(((40.0 - pv - bess) * 50.0).sum())       # grid_supply is the only priced term
    assert cost["basis"] == "single_period" and cost["per_period"] is None
    assert cost["total"] == pytest.approx(expected)
    assert out["hub_cost_reason"] is None


def test_hub_cost_is_null_with_a_reason_when_it_cannot_be_computed_and_the_state_still_answers(hub, monkeypatch):
    ce.draft(hub)
    ce.run(hub, {"k": 1})

    def boom(*a, **k):
        raise RuntimeError("statistics blew up")
    monkeypatch.setattr(ce, "_HUB_COST_CACHE", {})
    monkeypatch.setattr("services.results.cost_breakdown.compute_cost_breakdown", boom)
    state = ce.get_state(hub)
    assert state["hub_cost"] is None and "statistics blew up" in state["hub_cost_reason"]
    assert state["results"]["investment"]


def test_hub_cost_is_null_with_a_reason_when_the_project_prices_nothing(hub, monkeypatch):
    ce.draft(hub)
    ce.run(hub, {"k": 1})
    monkeypatch.setattr(ce, "_HUB_COST_CACHE", {})
    monkeypatch.setattr("services.results.cost_breakdown.compute_cost_breakdown", lambda n, cfg: None)
    state = ce.get_state(hub)
    assert state["hub_cost"] is None and "no cost statistics" in state["hub_cost_reason"]


def test_hub_cost_follows_the_network_and_is_not_served_stale_from_a_cache(hub):
    ce.draft(hub)
    first = ce.run(hub, {"k": 1})["hub_cost"]["total"]
    again = ce.get_state(hub)["hub_cost"]["total"]
    assert again == first
    n = hub_network(priced=True)
    n.generators.loc["grid_supply", "marginal_cost"] = 100.0
    n.export_to_netcdf(str(ce.project_registry.project_dir(hub) / "network.nc"))
    assert ce.get_state(hub)["hub_cost"]["total"] == pytest.approx(2 * first)


def multi_period_network():
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], pd.date_range("2030-01-01", periods=2, freq="h")]))
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings["years"] = [10.0, 5.0]
    n.add("Bus", "b")
    n.add("Load", "l", bus="b", p_set=1.0)
    n.add("Generator", "g", bus="b", p_nom=10.0, marginal_cost=10.0)
    n.generators_t.p = pd.DataFrame({"g": [2.0, 2.0, 4.0, 4.0]}, index=n.snapshots)
    return n


def test_hub_cost_of_a_multi_period_network_is_the_annual_cost_per_period(hub):
    out = ce.hub_cost_of(multi_period_network(), None)
    # period cost (years-weighted) / years: 2 snapshots at 2 MW x 10 EUR = 40 a year, then 80
    assert out["basis"] == "per_period"
    assert out["per_period"] == {"2030": pytest.approx(40.0), "2040": pytest.approx(80.0)}
    assert out["total"] == pytest.approx(40.0 * 10 + 80.0 * 5)


def test_the_investment_summary_for_the_copilot_carries_the_caveats_with_the_numbers(hub):
    ce.draft(hub)
    before = ce.get_investment(hub)
    assert before["investment"] is None and "run the study" in before["reason"]
    ce.run(hub, {"k": 1})
    out = ce.get_investment(hub)
    assert out["investment"] and out["cost"] and out["unresolved"] == [] and out["history"] == []
    assert out["compliance_invested"] and out["hub_cost"]["total"] > 0
    assert out["library_is_default"] is True and out["stale"] is False
    assert out["scope"] == {"pcc_switchgear": "campus"}
    text = " ".join(out["notes"])
    assert "placeholder" in text and "assumed" in text and "tap" in text
    ce.save_library(hub, library_with(scaled_capex(2)))
    out = ce.get_investment(hub)
    assert out["library_is_default"] is False and out["stale"] is True


def test_the_investment_summary_says_so_when_the_run_did_not_invest(hub):
    ce.draft(hub)
    ce.run(hub, {"k": 1, "invest": False})
    out = ce.get_investment(hub)
    assert out["investment"] is None and "invest" in out["reason"]


def test_a_failed_investment_leaves_no_half_purchase_and_is_a_422(hub, monkeypatch):
    from gridspine.schema.contracts import ContractError
    ce.draft(hub)
    ce.run(hub, {"k": 1})

    def refuse(*a, **k):
        raise ContractError("the library has no 20/0.4 kV transformer")
    monkeypatch.setattr(ce.cs, "invest_campus", refuse)
    with pytest.raises(HTTPException) as exc:
        ce.run(hub, {"k": 1})
    assert exc.value.status_code == 422 and "20/0.4" in exc.value.detail
    run_dir = ce.campus_dir(hub) / "run"
    assert not (run_dir / ce.USED_LIBRARY_FILE).exists() and not (run_dir / ce.cs.INVESTMENT_CSV).exists()
    assert not ce.get_state(hub)["stale"]


def test_the_unresolved_needs_are_the_unresolved_rows_with_their_reasons():
    rows = [
        {"need": "transformer T", "status": "chosen", "reason": None},
        {"need": "reactive", "status": "unresolved", "reason": "campus_voltage: 0.88 pu; needs a tap change"},
        {"need": "switchgear MV", "status": "kept", "reason": None},
    ]
    assert ce._unresolved(rows) == [{"need": "reactive", "reason": "campus_voltage: 0.88 pu; needs a tap change"}]
    assert ce._unresolved([]) == [] and ce._unresolved(None) is None


def test_an_unresolved_row_reaches_the_state_and_the_copilot_summary(hub):
    ce.draft(hub)
    ce.run(hub, {"k": 1})
    path = ce.campus_dir(hub) / "run" / ce.cs.INVESTMENT_CSV
    df = pd.read_csv(path)
    df.loc[1, ["status", "reason"]] = ["unresolved", "campus_voltage: needs a tap change"]
    df.to_csv(path, index=False)
    want = [{"need": df.loc[1, "need"], "reason": "campus_voltage: needs a tap change"}]
    assert ce.get_state(hub)["results"]["unresolved"] == want
    assert ce.get_investment(hub)["unresolved"] == want


# --------------------------------------------------------------------------
# part three, D1a: the discount rate and the price year come from the project
# --------------------------------------------------------------------------
#
# The library's own 0.07 and price year are stand-alone defaults. A project
# that has saved a solver config annualises at that config's discount rate,
# and one that has finance inputs states its money year there. The engine
# is handed a copy of the library with the project's rate, and that copy is
# the one kept as run/campus_assets_used.yaml.

def save_solver_config(project, **fields):
    import json
    path = project_registry.project_dir(project) / ce.SOLVER_CONFIG_FILE
    path.write_text(json.dumps(fields))
    return path


def finance_with(currency_year):
    return {"currency_year": currency_year}


def library_rate(text):
    return yaml.safe_load(text)["discount_rate"]["value"]


def library_entry(library_id, text=None):
    data = yaml.safe_load(text or default_library_text())
    return next(e for kind in data if isinstance(data[kind], list) for e in data[kind] if e["id"] == library_id)


def annualised(row, rate):
    from gridspine.templates.campus_assets import annuity
    life = library_entry(row["library_id"])["lifetime_a"]["value"]
    return row["capex_eur"] * annuity(rate, life) + row["opex_eur_per_a"]


def test_without_a_saved_solver_config_the_library_keeps_its_rate_and_year(hub):
    ce.draft(hub)
    out = ce.run(hub, {"k": 1})
    basis = out["results"]["cost_basis"]
    assert basis["discount_rate"] == 0.07 and basis["discount_rate_from"] == "asset library"
    assert basis["price_year"] == 2026 and basis["price_year_from"] == "asset library"
    assert basis["library_price_year"] == 2026 and basis["price_year_mismatch"] is False
    assert basis["currency"] == "EUR"
    assert (ce.campus_dir(hub) / "run" / ce.USED_LIBRARY_FILE).read_text() == default_library_text()


def test_a_solver_config_that_states_no_discount_rate_leaves_the_librarys(hub):
    """The loader's 0.07 default is not a rate the project stated: a saved
    config with only finance inputs keeps the library's rate, labelled as
    the library's, and hands the library over byte for byte."""
    ce.draft(hub)
    save_solver_config(hub, finance=finance_with(2024))
    basis = ce.run(hub, {"k": 1})["results"]["cost_basis"]
    assert basis["discount_rate_from"] == "asset library"
    assert basis["price_year"] == 2024 and basis["price_year_from"] == "project finance inputs"
    assert (ce.campus_dir(hub) / "run" / ce.USED_LIBRARY_FILE).read_text() == default_library_text()


def test_the_projects_discount_rate_replaces_the_librarys_for_the_engine_and_the_snapshot(hub, monkeypatch):
    ce.draft(hub)
    save_solver_config(hub, discount_rate=0.03)
    seen = []
    real = ce.cs.invest_campus

    def spy(run_dir, library=None, **kw):
        seen.append(open(library).read() if library is not None else None)
        return real(run_dir, library, **kw)
    monkeypatch.setattr(ce.cs, "invest_campus", spy)
    out = ce.run(hub, {"k": 1})
    assert seen and seen[0] is not None and library_rate(seen[0]) == 0.03
    assert library_rate((ce.campus_dir(hub) / "run" / ce.USED_LIBRARY_FILE).read_text()) == 0.03
    basis = out["results"]["cost_basis"]
    assert basis["discount_rate"] == 0.03 and basis["discount_rate_from"] == "project solver config"
    chosen = [r for r in out["results"]["investment"] if r["status"] == "chosen"]
    assert chosen
    for r in chosen:
        assert r["annualised_eur_per_a"] == pytest.approx(annualised(r, 0.03))
        assert r["annualised_eur_per_a"] != pytest.approx(annualised(r, 0.07))


def test_the_rate_override_also_applies_to_the_projects_own_library_copy(hub):
    ce.draft(hub)
    ce.save_library(hub, library_with(scaled_capex(2)))
    save_solver_config(hub, discount_rate=0.10)
    out = ce.run(hub, {"k": 1})
    used = yaml.safe_load((ce.campus_dir(hub) / "run" / ce.USED_LIBRARY_FILE).read_text())
    assert used["discount_rate"]["value"] == 0.10
    assert used["transformers"][0]["capex_eur"]["value"] == 2 * yaml.safe_load(default_library_text())[
        "transformers"][0]["capex_eur"]["value"]                       # the rest of the copy is as saved
    assert out["results"]["cost_basis"]["discount_rate_from"] == "project solver config"
    assert yaml.safe_load(ce.get_library(hub)["yaml"])["discount_rate"]["value"] == 0.07   # the saved copy is not rewritten


def test_the_price_year_is_the_projects_when_it_has_finance_inputs(hub):
    ce.draft(hub)
    save_solver_config(hub, discount_rate=0.07, finance=finance_with(2024))
    basis = ce.run(hub, {"k": 1})["results"]["cost_basis"]
    assert basis["price_year"] == 2024 and basis["price_year_from"] == "project finance inputs"
    assert basis["library_price_year"] == 2026
    assert basis["price_year_mismatch"] is True                       # flagged; no money is converted


def test_a_project_money_year_equal_to_the_librarys_is_not_a_mismatch(hub):
    ce.draft(hub)
    save_solver_config(hub, discount_rate=0.07, finance=finance_with(2026))
    basis = ce.run(hub, {"k": 1})["results"]["cost_basis"]
    assert basis["price_year"] == 2026 and basis["price_year_from"] == "project finance inputs"
    assert basis["price_year_mismatch"] is False


@pytest.mark.parametrize("finance", [None, {}, {"currency_year": None}, {"currency_year": "2024"}])
def test_finance_inputs_without_a_money_year_leave_the_librarys(hub, finance):
    ce.draft(hub)
    save_solver_config(hub, discount_rate=0.05, finance=finance)
    basis = ce.run(hub, {"k": 1})["results"]["cost_basis"]
    assert basis["price_year"] == 2026 and basis["price_year_from"] == "asset library"
    assert basis["price_year_mismatch"] is False
    assert basis["discount_rate"] == 0.05 and basis["discount_rate_from"] == "project solver config"


def test_a_mismatched_year_does_not_change_a_single_cost(hub):
    ce.draft(hub)
    plain = ce.run(hub, {"k": 1})["results"]["cost"]
    save_solver_config(hub, discount_rate=0.07, finance=finance_with(2019))
    assert ce.run(hub, {"k": 1})["results"]["cost"] == plain


def test_a_project_rate_outside_the_librarys_range_is_a_422_naming_the_rate(hub):
    ce.draft(hub)
    save_solver_config(hub, discount_rate=1.5)
    with pytest.raises(HTTPException) as exc:
        ce.run(hub, {"k": 1})
    assert exc.value.status_code == 422 and "1.5" in exc.value.detail and "solver config" in exc.value.detail
    assert not (ce.campus_dir(hub) / "run" / ce.USED_LIBRARY_FILE).exists()


def test_a_run_without_invest_has_no_cost_basis(hub):
    ce.draft(hub)
    save_solver_config(hub, discount_rate=0.03)
    ce.run(hub, {"k": 1})
    out = ce.run(hub, {"k": 1, "invest": False})
    assert out["results"]["cost_basis"] is None
    assert not (ce.campus_dir(hub) / "run" / ce.COST_BASIS_FILE).exists()


def test_a_change_of_the_projects_discount_rate_makes_the_results_stale(hub):
    ce.draft(hub)
    save_solver_config(hub, discount_rate=0.05)
    ce.run(hub, {"k": 1})
    assert not ce.get_state(hub)["stale"]
    save_solver_config(hub, discount_rate=0.06)
    assert ce.get_state(hub)["stale"]
    ce.run(hub, {"k": 1})
    assert not ce.get_state(hub)["stale"]


def test_saving_or_removing_the_solver_config_changes_the_basis_and_so_the_staleness(hub):
    ce.draft(hub)
    ce.run(hub, {"k": 1})                                              # the library's 0.07, no config
    assert not ce.get_state(hub)["stale"]
    path = save_solver_config(hub, discount_rate=0.04)
    assert ce.get_state(hub)["stale"]
    ce.run(hub, {"k": 1})
    assert not ce.get_state(hub)["stale"]
    path.unlink()
    assert ce.get_state(hub)["stale"]


def test_a_change_to_something_else_in_the_solver_config_is_not_a_change_of_the_results(hub):
    ce.draft(hub)
    save_solver_config(hub, discount_rate=0.05, solver_name="highs")
    ce.run(hub, {"k": 1})
    save_solver_config(hub, discount_rate=0.05, solver_name="gurobi")
    assert not ce.get_state(hub)["stale"]


def test_a_change_of_the_money_year_makes_the_results_stale_since_the_basis_is_shown(hub):
    ce.draft(hub)
    save_solver_config(hub, discount_rate=0.05, finance=finance_with(2026))
    ce.run(hub, {"k": 1})
    assert not ce.get_state(hub)["stale"]
    save_solver_config(hub, discount_rate=0.05, finance=finance_with(2024))
    assert ce.get_state(hub)["stale"]


def test_a_run_that_fails_leaves_no_cost_basis_behind(hub, monkeypatch):
    from gridspine.schema.contracts import ContractError
    ce.draft(hub)
    ce.run(hub, {"k": 1})
    monkeypatch.setattr(ce.cs, "invest_campus", lambda *a, **k: (_ for _ in ()).throw(ContractError("no")))
    with pytest.raises(HTTPException):
        ce.run(hub, {"k": 1})
    assert not (ce.campus_dir(hub) / "run" / ce.COST_BASIS_FILE).exists()


def test_the_investment_summary_for_the_copilot_carries_the_cost_basis(hub):
    ce.draft(hub)
    save_solver_config(hub, discount_rate=0.05)
    ce.run(hub, {"k": 1})
    assert ce.get_investment(hub)["cost_basis"]["discount_rate"] == 0.05


# --------------------------------------------------------------------------
# part three, D2: the chosen equipment as extra owner assets
# --------------------------------------------------------------------------
#
# ``extra_owner_assets`` turns what the last investment run bought into the
# list the investment-case builder can take: one entry per purchased item that
# is neither existing nor unresolved, with the TOTAL overnight cost in EUR.

INVEST_COLUMNS = ["need", "library_id", "kind", "units", "length_km", "invest_period", "capex_eur",
                  "opex_eur_per_a", "annualised_eur_per_a", "existing", "status", "reason"]


def inv_row(need, library_id, kind, units, *, length_km=None, period=2030, capex=1.0, existing=False,
            status="chosen", reason=None):
    return {"need": need, "library_id": library_id, "kind": kind, "units": units, "length_km": length_km,
            "invest_period": period, "capex_eur": capex, "opex_eur_per_a": 0.0, "annualised_eur_per_a": 0.0,
            "existing": existing, "status": status, "reason": reason}


def fake_investment(project, rows, library_text=None, scope="campus"):
    """Write a run directory as an investment run leaves it, from hand-made rows."""
    import json
    run_dir = ce.campus_dir(project) / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=INVEST_COLUMNS).to_csv(run_dir / ce.cs.INVESTMENT_CSV, index=False)
    (run_dir / ce.cs.INVEST_SCOPE_JSON).write_text(json.dumps({"pcc_switchgear": scope}))
    (run_dir / ce.USED_LIBRARY_FILE).write_text(library_text or default_library_text())
    return run_dir


def purchase_rows():
    return [
        inv_row("transformer GRID_IMPORT", "TR_132_33_63", "transformer", 2, capex=5_000_000.0),
        inv_row("cable CBL_1", "CB_33_AL240", "cable", 2, length_km=1.5, period=2032, capex=420_000.0),
        inv_row("switchgear GRID", "SG_132_31p5", "switchgear", 3, capex=1_350_000.0),
        inv_row("reactive", "CAP_33_10M", "capacitor_bank", 2, period=2035, capex=300_000.0),
        inv_row("reactive", "SR_33_5M", "shunt_reactor", 1, period=2035, capex=150_000.0),
    ]


def test_each_purchased_item_is_one_owner_asset_with_the_total_overnight_cost(hub):
    fake_investment(hub, purchase_rows())
    assets = ce.extra_owner_assets(hub)
    assert [a["name"] for a in assets] == [
        "transformer GRID_IMPORT TR_132_33_63", "cable CBL_1 CB_33_AL240", "switchgear GRID SG_132_31p5",
        "reactive CAP_33_10M", "reactive SR_33_5M"]
    assert assets[0] == {
        "name": "transformer GRID_IMPORT TR_132_33_63", "need": "transformer GRID_IMPORT", "kind": "transformer",
        "library_id": "TR_132_33_63", "units": 2, "invest_period": 2030,
        "upfront_parts": [{"name": "investment", "upfront": 5_000_000.0, "lifetime": 40.0, "fom_share": 0.015}],
        "currency": "EUR", "price_year": 2026, "provenance": "assumed", "illustrative": True}


def test_the_upfront_cost_is_units_times_the_library_overnight_cost(hub):
    # the figure is the library's, multiplied out here: wrong CSV capex columns must not leak through
    rows = [inv_row("transformer T", "TR_132_33_63", "transformer", 3, capex=-1.0)]
    fake_investment(hub, rows)
    (asset,) = ce.extra_owner_assets(hub)
    assert asset["upfront_parts"][0]["upfront"] == 3 * 2_500_000.0


def test_a_cable_is_priced_per_km_times_km_times_the_cables(hub):
    fake_investment(hub, [inv_row("cable CBL_1", "CB_33_AL240", "cable", 2, length_km=1.5, capex=-1.0)])
    (asset,) = ce.extra_owner_assets(hub)
    assert asset["units"] == 2
    assert asset["upfront_parts"][0]["upfront"] == 2 * 140_000.0 * 1.5
    assert asset["upfront_parts"][0]["lifetime"] == 40.0 and asset["upfront_parts"][0]["fom_share"] == 0.005


def test_switchgear_is_priced_per_bay_times_the_bays(hub):
    fake_investment(hub, [inv_row("switchgear MV", "SG_20_25p0", "switchgear", 4, capex=-1.0)])
    (asset,) = ce.extra_owner_assets(hub)
    assert asset["units"] == 4 and asset["upfront_parts"][0]["upfront"] == 4 * 54_000.0
    assert asset["upfront_parts"][0]["fom_share"] == 0.01


def test_existing_and_unresolved_and_empty_needs_are_left_out(hub):
    rows = [
        inv_row("transformer T", "TR_132_33_63", "transformer", 1),
        inv_row("transformer OLD", "TR_132_33_40", "transformer", 1, existing=True),      # sunk, even if 'chosen'
        inv_row("transformer KEPT", None, "keep", 0, existing=True, status="kept"),
        inv_row("reactive", "CAP_33_5M", "capacitor_bank", 1, status="unresolved", reason="needs a tap change"),
        inv_row("cable NONE", None, "none", 0, status="not_needed"),
    ]
    fake_investment(hub, rows)
    assert [a["name"] for a in ce.extra_owner_assets(hub)] == ["transformer T TR_132_33_63"]


def test_there_are_no_owner_assets_before_a_run_or_after_a_run_that_did_not_invest(hub):
    assert ce.extra_owner_assets(hub) == []
    ce.draft(hub)
    assert ce.extra_owner_assets(hub) == []
    ce.run(hub, {"k": 1, "invest": False})
    assert ce.extra_owner_assets(hub) == []


def test_a_run_that_only_kept_what_exists_has_no_owner_assets(hub):
    fake_investment(hub, [inv_row("transformer KEPT", None, "keep", 0, existing=True, status="kept")])
    assert ce.extra_owner_assets(hub) == []


def test_a_project_of_another_kind_is_refused_like_every_other_action(study):
    with pytest.raises(HTTPException) as exc:
        ce.extra_owner_assets(study)
    assert exc.value.status_code == 409


def test_the_cost_tag_of_the_library_is_the_provenance_and_assumed_alone_is_illustrative(hub):
    def measured(data):
        for e in data["transformers"]:
            if e["id"] == "TR_132_33_63":
                e["capex_eur"]["source"] = "datasheet"
    fake_investment(hub, purchase_rows(), library_text=library_with(measured))
    by_id = {a["library_id"]: a for a in ce.extra_owner_assets(hub)}
    assert by_id["TR_132_33_63"]["provenance"] == "datasheet" and by_id["TR_132_33_63"]["illustrative"] is False
    assert by_id["CB_33_AL240"]["provenance"] == "assumed" and by_id["CB_33_AL240"]["illustrative"] is True


def test_a_cable_takes_the_provenance_of_its_per_km_cost(hub):
    def change(data):
        for e in data["cables"]:
            if e["id"] == "CB_33_AL240":
                e["capex_eur_per_km"]["source"] = "measured"
    fake_investment(hub, [inv_row("cable CBL_1", "CB_33_AL240", "cable", 1, length_km=2.0)], library_text=library_with(change))
    (asset,) = ce.extra_owner_assets(hub)
    assert asset["provenance"] == "measured" and asset["illustrative"] is False


def test_the_currency_and_the_price_year_are_the_libraries_of_the_run(hub):
    def change(data):
        data["currency"] = "USD"
        data["price_year"] = 2021
    fake_investment(hub, purchase_rows(), library_text=library_with(change))
    assert {(a["currency"], a["price_year"]) for a in ce.extra_owner_assets(hub)} == {("USD", 2021)}


def test_the_invest_period_is_the_rows(hub):
    fake_investment(hub, purchase_rows())
    assert [a["invest_period"] for a in ce.extra_owner_assets(hub)] == [2030, 2032, 2030, 2035, 2035]


def test_the_owner_assets_come_from_the_library_the_run_used_not_the_one_saved_since(hub):
    fake_investment(hub, [inv_row("transformer T", "TR_132_33_63", "transformer", 1)])
    ce.save_library(hub, library_with(scaled_capex(5)))                 # saved after the run
    (asset,) = ce.extra_owner_assets(hub)
    assert asset["upfront_parts"][0]["upfront"] == 2_500_000.0


def test_a_real_run_gives_owner_assets_that_add_up_to_its_cost_table(hub):
    ce.draft(hub)
    out = ce.run(hub, {"k": 1, "pf": 0.95})
    assets = ce.extra_owner_assets(hub)
    chosen = [r for r in out["results"]["investment"] if r["status"] == "chosen" and not r["existing"]]
    assert chosen and len(assets) == len(chosen)
    assert sum(a["upfront_parts"][0]["upfront"] for a in assets) == pytest.approx(sum(r["capex_eur"] for r in chosen))
    assert sum(a["upfront_parts"][0]["upfront"] for a in assets) == pytest.approx(out["results"]["cost"][0]["capex_eur"])
    assert out["owner_assets_count"] == len(assets)
    assert all(a["illustrative"] and a["provenance"] == "assumed" for a in assets)


def test_the_pcc_switchgear_the_operator_owns_is_not_an_owner_asset(hub):
    ce.draft(hub)
    mine = ce.run(hub, {"k": 1, "pf": 0.95})
    assert "switchgear GRID SG_110_31p5" in [a["name"] for a in ce.extra_owner_assets(hub)]
    theirs = ce.run(hub, {"k": 1, "pf": 0.95, "pcc_switchgear_by_operator": True})
    assert not [a for a in ce.extra_owner_assets(hub) if a["need"] == "switchgear GRID"]
    assert theirs["owner_assets_count"] == mine["owner_assets_count"] - 1


def test_the_state_counts_the_owner_assets(hub):
    assert ce.get_state(hub)["owner_assets_count"] == 0
    fake_investment(hub, purchase_rows())
    assert ce.extra_owner_assets(hub) and ce.get_state(hub)["owner_assets_count"] == 5
