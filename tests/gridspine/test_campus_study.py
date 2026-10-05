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
