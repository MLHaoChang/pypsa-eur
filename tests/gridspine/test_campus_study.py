"""The campus study driver (plan C2): from a saved project network and a
campus description to a campus run directory.

``draft_from_project(network_path)`` is the "generate" button: a campus
draft from the saved, solved ``network.nc``.

``prepare_campus(run_dir, campus, network_path)`` writes everything the
later stages read:

* ``campus.yaml``, the campus exactly as used;
* ``campus_hourly.csv`` and ``campus_pcc.csv``;
* ``campus_manifest.json``, which records both inputs by content hash so a
  stale run can be recognised.

It validates everything before writing anything, so a refused campus leaves
the run directory as it found it.
"""
import json

import pytest
import yaml

from gridspine.drivers.campus_study import (
    CAMPUS_MANIFEST,
    CAMPUS_YAML,
    COMPLIANCE_INVESTED_CSV,
    COST_CSV,
    INVEST_DISPATCH_CSV,
    INVEST_HISTORY_CSV,
    INVESTED_YAML,
    INVESTMENT_CSV,
    campus_tables,
    draft_from_project,
    invest_campus,
    load_run_campus,
    prepare_campus,
    rank_campus,
    selected_hours,
    size_campus,
)
from gridspine.schema.campus import HOURLY_CSV, PCC_CSV
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_campus_hourly import solved_hub


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "network.nc"
    solved_hub(periods=[2030, 2040]).export_to_netcdf(str(path))
    return path


def test_a_draft_comes_from_the_saved_project(project):
    draft = draft_from_project(project)
    assert draft.spec["campus"]["pcc"]["pypsa_name"] == "grid"


def test_a_missing_project_is_refused(tmp_path):
    with pytest.raises(ContractError, match="not found"):
        draft_from_project(tmp_path / "nope.nc")


def test_prepare_writes_the_campus_the_tables_and_a_manifest(tmp_path, project):
    run = tmp_path / "run"
    summary = prepare_campus(run, draft_from_project(project).spec, project)
    for name in (CAMPUS_YAML, HOURLY_CSV, PCC_CSV, CAMPUS_MANIFEST):
        assert (run / name).is_file(), name
    hourly, pcc = campus_tables(run)
    assert sorted(hourly["period"].unique()) == [2030, 2040]
    assert len(pcc) == 12
    manifest = json.loads((run / CAMPUS_MANIFEST).read_text())
    assert manifest["periods"] == [2030, 2040]
    assert len(manifest["campus_sha256"]) == 64 and len(manifest["network_sha256"]) == 64
    assert summary["periods"] == [2030, 2040] and summary["units"] == len(hourly["unit_id"].unique())
    # the campus is stored exactly as used, and builds again from the run
    assert yaml.safe_load((run / CAMPUS_YAML).read_text()) == draft_from_project(project).spec
    assert load_run_campus(run).net.bus["name"].tolist()


def test_the_manifest_hash_changes_when_the_campus_changes(tmp_path, project):
    spec = draft_from_project(project).spec
    prepare_campus(tmp_path / "a", spec, project)
    spec["campus"]["pcc"]["sk_max_mva"]["value"] = 1500.0
    spec["campus"]["pcc"]["sk_min_mva"]["value"] = 1500.0
    prepare_campus(tmp_path / "b", spec, project)
    a = json.loads((tmp_path / "a" / CAMPUS_MANIFEST).read_text())
    b = json.loads((tmp_path / "b" / CAMPUS_MANIFEST).read_text())
    assert a["campus_sha256"] != b["campus_sha256"] and a["network_sha256"] == b["network_sha256"]


def test_a_refused_campus_writes_nothing(tmp_path, project):
    spec = draft_from_project(project).spec
    spec["campus"]["pcc"]["sk_min_mva"]["value"] = 1e9                  # above sk_max
    run = tmp_path / "run"
    with pytest.raises(ContractError, match="sk_min_mva"):
        prepare_campus(run, spec, project)
    assert not run.exists() or not any(run.iterdir())


def test_preparing_again_replaces_the_previous_run(tmp_path, project):
    run = tmp_path / "run"
    spec = draft_from_project(project).spec
    prepare_campus(run, spec, project)
    spec["campus"]["units"] = {k: v for k, v in spec["campus"]["units"].items() if v["kind"] != "pv"}
    prepare_campus(run, spec, project)
    hourly, _ = campus_tables(run)
    kinds = {v["kind"] for v in yaml.safe_load((run / CAMPUS_YAML).read_text())["campus"]["units"].values()}
    assert "pv" not in kinds
    assert len(hourly["unit_id"].unique()) == len(spec["campus"]["units"])


def test_tables_of_a_run_that_was_never_prepared_are_refused(tmp_path):
    with pytest.raises(ContractError, match="prepare"):
        campus_tables(tmp_path)


def test_ranking_a_prepared_run_stores_the_selection_with_its_reasons(tmp_path, project):
    run = tmp_path / "run"
    prepare_campus(run, draft_from_project(project).spec, project)
    sel = rank_campus(run, k=1)
    stored = selected_hours(run)
    assert stored[["period", "hour"]].values.tolist() == sel[["period", "hour"]].values.tolist()
    assert list(stored["reasons"]) == list(sel["reasons"])
    assert set(stored["period"]) == {2030, 2040}
    assert all(r.startswith("max_") for rs in stored["reasons"] for r in rs)


def test_ranking_before_preparing_is_refused(tmp_path):
    with pytest.raises(ContractError, match="prepare"):
        rank_campus(tmp_path)


def test_sizing_a_ranked_run_solves_every_selected_hour_and_sizes_each_transformer(tmp_path, project):
    import pandas as pd
    run = tmp_path / "run"
    prepare_campus(run, draft_from_project(project).spec, project)
    sel = rank_campus(run, k=1)
    out = size_campus(run)
    sizing = out["transformers"]
    pcc = pd.read_csv(run / "campus_lf_pcc.csv")
    intact = pcc[pcc["case"] == "intact"]
    assert sorted(map(tuple, intact[["period", "hour"]].values.tolist())) == sorted(map(tuple, sel[["period", "hour"]].values.tolist()))
    assert intact["converged"].all()
    assert len(sizing) == 1 and sizing.iloc[0]["group"] == "GRID_IMPORT"
    assert sizing.iloc[0]["recommended_unit_mva"] >= sizing.iloc[0]["required_unit_mva"]
    assert (run / "campus_sizing_trafo.csv").is_file() and (run / "campus_lf_bus.csv").is_file()


def test_sizing_corrects_each_selected_hour_into_the_pcc_band_and_records_the_rule(tmp_path, project):
    import pandas as pd
    run = tmp_path / "run"
    spec = draft_from_project(project).spec
    spec["campus"]["units"] = {k: dict(v, pf={"value": 0.7, "source": "assumed"}) if v["kind"] == "load" else v
                               for k, v in spec["campus"]["units"].items()}
    spec["campus"]["pcc"]["p_connection_mw"] = {"value": 60.0, "source": "measured"}
    prepare_campus(run, spec, project)
    rank_campus(run, k=1)
    out = size_campus(run, pf=0.98)
    req = out["requirement"]
    assert req["p_ref_mw"] == 60.0 and req["p_ref_from"] == "campus file p_connection_mw"
    assert req["source"] == "assumed" and "0.98" in req["clause"]
    q = pd.read_csv(run / "campus_reactive.csv")
    assert q["converged"].all()
    assert (q["q_final_mvar"].abs() <= req["q_limit_mvar"] + 0.011).all()
    comp = out["compensation"].set_index("direction")
    assert comp.at["capacitive", "required_mvar"] > 0      # pf 0.7 loads need support
    stored = json.loads((run / "campus_requirement.json").read_text())
    assert stored == req


def test_without_a_connection_capacity_the_peak_import_is_used_and_said_so(tmp_path, project):
    run = tmp_path / "run"
    prepare_campus(run, draft_from_project(project).spec, project)
    rank_campus(run, k=1)
    req = size_campus(run)["requirement"]
    assert req["p_ref_from"].startswith("peak |import|") and req["source"] == "code"


def test_sizing_checks_short_circuit_per_period_with_that_periods_units(tmp_path, project):
    run = tmp_path / "run"
    spec = draft_from_project(project).spec
    mv = next(k for k, v in spec["campus"]["buses"].items() if v["pypsa_name"] == "mv")
    spec["campus"]["buses"][mv]["ik_rated_ka"] = {"value": 25.0, "source": "datasheet"}
    prepare_campus(run, spec, project)
    rank_campus(run, k=1)
    fl = size_campus(run)["short_circuit"]
    assert set(fl["period"]) == {2030, 2040}
    mv_rows = fl[fl["bus"] == mv].set_index("period")
    # the battery is built in 2040, so the MV fault level rises
    assert mv_rows.at[2040, "ikss_max_ka"] > mv_rows.at[2030, "ikss_max_ka"]
    assert mv_rows.at[2040, "rated_ka"] == 25.0 and bool(mv_rows.at[2040, "adequate"])
    assert (run / "campus_short_circuit.csv").is_file()


def test_sizing_ends_with_the_compliance_report(tmp_path, project):
    import pandas as pd
    from gridspine.static.campus_compliance import CHECKS
    run = tmp_path / "run"
    prepare_campus(run, draft_from_project(project).spec, project)
    rank_campus(run, k=1)
    out = size_campus(run)
    stored = pd.read_csv(run / "campus_compliance.csv")
    assert list(stored["check"]) == list(CHECKS) == list(out["compliance"]["check"])
    assert set(stored["status_as_is"]) <= {"pass", "fail", "not_rated"}


def test_an_unsolved_project_cannot_be_drafted(tmp_path):
    n = solved_hub()
    for ts, attr in (("generators_t", "p"), ("storage_units_t", "p"), ("loads_t", "p"), ("links_t", "p0")):
        setattr(getattr(n, ts), attr, __import__("pandas").DataFrame(index=n.snapshots))
    path = tmp_path / "unsolved.nc"
    n.export_to_netcdf(str(path))
    with pytest.raises(ContractError, match="not solved"):
        draft_from_project(path)


def test_investing_a_sized_run_buys_from_the_library_and_re_solves_the_compliance(tmp_path, project):
    """The hub end to end with the shipped library: prepare, rank, size,
    invest. Every check with measures is an AC result that passes (or has
    no rating to judge), and the invested campus file loads."""
    import pandas as pd
    from gridspine.ingest.campus import load_campus
    from gridspine.static.campus_invest import INVEST_CHECKS
    run = tmp_path / "run"
    prepare_campus(run, draft_from_project(project).spec, project)
    rank_campus(run, k=1)
    size_campus(run)
    out = invest_campus(run)
    for name in (INVESTMENT_CSV, COST_CSV, INVESTED_YAML, COMPLIANCE_INVESTED_CSV, INVEST_HISTORY_CSV,
                 INVEST_DISPATCH_CSV):
        assert (run / name).is_file(), name
    comp = pd.read_csv(run / COMPLIANCE_INVESTED_CSV)
    assert list(comp["check"]) == list(INVEST_CHECKS)
    assert set(comp["status_with_measures"]) <= {"pass", "not_rated"}
    inv = pd.read_csv(run / INVESTMENT_CSV)
    assert set(inv["status"]) <= {"chosen", "kept", "not_needed"}
    assert inv["library_id"].dropna().str.len().gt(0).all()
    camp = load_campus(run / INVESTED_YAML)
    assert set(camp.net.trafo["name"]) == set(yaml.safe_load((run / INVESTED_YAML).read_text())["campus"]["transformers"])
    cost = pd.read_csv(run / COST_CSV)
    assert list(cost["period"]) == [2030, 2040] and (cost["annualised_eur_per_a"] > 0).all()
    assert set(out) >= {"investment", "cost", "compliance", "history", "dispatch", "spec", "unresolved"}
    assert out["unresolved"] == []


def test_a_project_library_replaces_the_shipped_one(tmp_path, project):
    import pandas as pd
    from gridspine.templates.campus_assets import DEFAULT_PATH
    run = tmp_path / "run"
    prepare_campus(run, draft_from_project(project).spec, project)
    rank_campus(run, k=1)
    lib = yaml.safe_load(DEFAULT_PATH.read_text())
    lib["transformers"] = [e for e in lib["transformers"] if e["id"] != "TR_110_20_63"]
    path = tmp_path / "project_assets.yaml"
    path.write_text(yaml.safe_dump(lib))
    shipped = invest_campus(run)["investment"]
    edited = invest_campus(run, library=path)["investment"]
    assert "TR_110_20_63" not in set(edited["library_id"].dropna())
    assert len(shipped) and len(edited)
    with pytest.raises(ContractError, match="not found"):
        invest_campus(run, library=tmp_path / "nope.yaml")
    assert pd.read_csv(run / INVESTMENT_CSV)["library_id"].fillna("").tolist() == edited["library_id"].fillna("").tolist()


def test_investing_before_ranking_is_refused(tmp_path, project):
    run = tmp_path / "run"
    prepare_campus(run, draft_from_project(project).spec, project)
    with pytest.raises(ContractError, match="campus_selected"):
        invest_campus(run)


def test_the_pcc_switchgear_scope_is_a_study_setting_and_is_written(tmp_path, project):
    import json
    from gridspine.drivers.campus_study import INVEST_SCOPE_JSON
    run = tmp_path / "run"
    prepare_campus(run, draft_from_project(project).spec, project)
    rank_campus(run, k=1)
    pcc = draft_from_project(project).spec["campus"]["pcc"]["bus"]
    by_operator = invest_campus(run, pcc_switchgear=False)
    assert f"switchgear {pcc}" not in set(by_operator["investment"]["need"])
    assert json.loads((run / INVEST_SCOPE_JSON).read_text()) == {"pcc_switchgear": "grid_operator"}
    invest_campus(run)
    assert json.loads((run / INVEST_SCOPE_JSON).read_text()) == {"pcc_switchgear": "campus"}


def test_investing_by_milp_writes_c8s_files_and_the_milp_history(tmp_path, project):
    """The hub end to end with ``method="milp"``: the investment files are
    the same set as C8's, plus the iteration history and the comparison
    with C8. The default stays the least-cost pick."""
    import inspect
    import pandas as pd
    from gridspine.drivers.campus_study import MILP_COMPARISON_CSV, MILP_HISTORY_CSV
    from gridspine.static.campus_invest import INVEST_CHECKS
    assert inspect.signature(invest_campus).parameters["method"].default == "least_cost"
    run = tmp_path / "run"
    prepare_campus(run, draft_from_project(project).spec, project)
    rank_campus(run, k=1)
    out = invest_campus(run, method="milp")
    for name in (INVESTMENT_CSV, COST_CSV, INVESTED_YAML, COMPLIANCE_INVESTED_CSV, INVEST_HISTORY_CSV,
                 INVEST_DISPATCH_CSV, MILP_HISTORY_CSV, MILP_COMPARISON_CSV):
        assert (run / name).is_file(), name
    hist = pd.read_csv(run / MILP_HISTORY_CSV)
    assert list(hist.columns[:4]) == ["iteration", "cost", "feasible", "worst_violation"]
    assert hist["iteration"].iloc[0] == 0 and len(hist) >= 2
    comp = pd.read_csv(run / COMPLIANCE_INVESTED_CSV)
    assert list(comp["check"]) == list(INVEST_CHECKS)
    assert set(comp["status_with_measures"]) <= {"pass", "not_rated"}
    s = out["summary"]
    assert s["milp_cost"] <= s["c8_cost"] + 1e-6
    cost = pd.read_csv(run / COST_CSV)
    assert list(cost["period"]) == [2030, 2040]
    cmp_ = pd.read_csv(run / MILP_COMPARISON_CSV)
    assert set(cmp_.columns) >= {"need", "c8_choice", "milp_choice"}
    with pytest.raises(ContractError, match="method"):
        invest_campus(run, method="greedy")
