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
