"""Shared fixtures and helpers for the S4 decision-study tests."""
from __future__ import annotations

import hashlib
import pathlib
import time

import main
from routers import studies as studies_router

INTAKE = {
    "site": {"zone": "DE", "connection_mw": 2.0},
    "tariff": {"tariff_id": "de_industrial_illustrative"},
    "load": {"source": "sector_profile", "profile": "commercial_office",
             "annual_mwh": 4000.0},
    "pv": {"enabled": True, "kind": "rooftop"},
}
OPTIONS = ["none", "bess_1h", "bess_2h", "bess_4h", "bess_pv_2h"]


def enable_studies(monkeypatch):
    """Generator body of each test module's `studies_on` fixture."""
    monkeypatch.setenv("PYPSAGUI_DECISION_STUDIES", "1")
    dep = studies_router.require_decision_studies_enabled
    main.app.dependency_overrides[dep] = lambda: None
    monkeypatch.setattr(studies_router, "require_decision_studies_enabled", lambda: None)
    try:
        yield
    finally:
        main.app.dependency_overrides.pop(dep, None)


def dir_hash(path: pathlib.Path) -> str:
    """Every file's relative path and bytes, in order."""
    h = hashlib.sha256()
    for f in sorted(p for p in pathlib.Path(path).rglob("*") if p.is_file()):
        h.update(str(f.relative_to(path)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def all_project_dirs(session_local) -> dict[str, pathlib.Path]:
    from sqlalchemy import select

    from db.models import Project
    from services import project_registry

    with session_local() as db:
        return {str(p.id): project_registry.project_dir(p)
                for p in db.scalars(select(Project)).all()}


def create_pack_study(client, path_project: str, base: str, intake=None,
                      name="Site battery"):
    r = client.post(f"/api/projects/{path_project}/studies/", json={
        "question_id": "bess_at_site", "name": name, "project_name": base,
        "intake": INTAKE if intake is None else intake})
    return r


def wait_run(client, base: str, study_id: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/projects/{base}/studies/{study_id}/run")
        if r.status_code == 200 and r.json().get("status") != "running":
            return r.json()
        time.sleep(0.1)
    raise AssertionError("the study run did not finish")
