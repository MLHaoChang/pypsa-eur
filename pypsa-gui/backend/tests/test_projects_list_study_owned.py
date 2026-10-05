"""
Study-owned forks are marked in the project list (plan F1-F, F3; review v2
[S11], gate S8 [S9-c]).

A decision study's option forks are ordinary child projects named
`<base>-opt-<option>`; without a marker a user deletes one and the study's
next read says `option_not_solved`. `GET /api/projects/` now carries
`study_owned`, `owner_study_id` and `owner_study_name` on every row.

The flag uses the ownership rule of `services/study/forks.py::is_study_owned`:
the fork's metadata names a study and a base (`owner_study_id`,
`owner_base_project`) AND its database parent IS that base. The metadata
alone is copied by Save-As, bundles and scenarios of the fork, so a copy
elsewhere is not marked.
"""
from __future__ import annotations

import json

import pytest

import main
from routers import studies as studies_router

BODY = {"question_id": "bess_site", "name": "Site A battery",
        "intake": {"site": {"connection_mw": 5.0}}}


@pytest.fixture
def studies_on(monkeypatch):
    # As in test_studies_routes: the routes refuse in auth mode (BC-6); the
    # suite opens the "in tests" door through dependency_overrides.
    monkeypatch.setenv("PYPSAGUI_DECISION_STUDIES", "1")
    dep = studies_router.require_decision_studies_enabled
    main.app.dependency_overrides[dep] = lambda: None
    monkeypatch.setattr(studies_router, "require_decision_studies_enabled", lambda: None)
    try:
        yield
    finally:
        main.app.dependency_overrides.pop(dep, None)


def _rows(client):
    resp = client.get("/api/projects/")
    assert resp.status_code == 200, resp.text
    return {r["name"]: r for r in resp.json()}


def _child(client, base, name):
    r = client.post(f"/api/projects/{base}/scenarios", json={"name": name})
    assert r.status_code == 201, r.text
    return name


def _stamp(project_row, name, **owner):
    from services import project_registry

    d = project_registry.project_dir(project_row(name))
    meta = json.loads((d / "metadata.json").read_text())
    meta.update(owner)
    (d / "metadata.json").write_text(json.dumps(meta))


def test_a_study_owned_fork_is_marked_and_names_its_study(client, api_project, project_row, studies_on):
    base = api_project("Site")
    r = client.post(f"/api/projects/{base}/studies/", json=BODY)
    assert r.status_code == 201, r.text
    study_id = r.json()["study_id"]
    fork = _child(client, base, "Site-opt-bess_1h")
    mine = _child(client, base, "Site-mine")
    _stamp(project_row, fork, owner_study_id=study_id,
           owner_base_project=str(project_row(base).id), owner_option_id="bess_1h")

    rows = _rows(client)
    assert rows[fork]["study_owned"] is True
    assert rows[fork]["owner_study_id"] == study_id
    assert rows[fork]["owner_study_name"] == "Site A battery"
    # The user's own child of the same base, and the base itself: unmarked.
    for name in (mine, base):
        assert rows[name]["study_owned"] is False
        assert rows[name]["owner_study_id"] is None
        assert rows[name]["owner_study_name"] is None


def test_a_copy_of_the_owner_keys_under_another_parent_is_not_marked(client, api_project, project_row, studies_on):
    base = api_project("Site")
    other = api_project("Elsewhere")
    r = client.post(f"/api/projects/{base}/studies/", json=BODY)
    assert r.status_code == 201, r.text
    copy = _child(client, other, "Elsewhere-copy")
    # Save-As of a fork, or a bundle re-import: the keys came along, the parent did not.
    _stamp(project_row, copy, owner_study_id=r.json()["study_id"],
           owner_base_project=str(project_row(base).id), owner_option_id="none")

    row = _rows(client)[copy]
    assert row["study_owned"] is False
    assert row["owner_study_id"] is None


def test_a_fork_whose_study_is_gone_is_still_marked_without_a_name(client, api_project, project_row, studies_on):
    # The startup sweep removes it later (forks.sweep_leftover_forks); until
    # then the list still says whose it was, and that the study is gone.
    base = api_project("Site")
    fork = _child(client, base, "Site-opt-none")
    _stamp(project_row, fork, owner_study_id="0" * 32,
           owner_base_project=str(project_row(base).id), owner_option_id="none")

    row = _rows(client)[fork]
    assert row["study_owned"] is True
    assert row["owner_study_id"] == "0" * 32
    assert row["owner_study_name"] is None
