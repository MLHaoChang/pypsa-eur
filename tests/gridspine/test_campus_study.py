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
    campus_tables,
    draft_from_project,
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
