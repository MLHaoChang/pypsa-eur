"""Networkless studies must bind authenticated sessions and chat tool selection."""
from services import chat_tools, gridspine_service, project_registry
from services.pypsa_service import PyPSAService
from harness.providers import wiring


def test_study_activation_survives_cold_session_resolution(client, session_ctx):
    study = chat_tools.gridspine_create_study("Networkless Study", {"hours": 24, "k": 1})
    response = client.post(f"/api/projects/{study['id']}/activate")
    assert response.status_code == 200, response.text
    ctx = session_ctx(client)
    assert ctx.loaded_project == study["name"] and ctx.project_uuid == study["id"]
    assert len(ctx.network.buses) == 0
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, study["id"])
        key = project_registry.registry_key(project)
        assert not (project_registry.project_dir(project) / "network.nc").exists()
        assert gridspine_service.get_config(project)["hours"] == 24
    PyPSAService._contexts.pop(key)
    cold = session_ctx(client)
    assert cold.loaded_project == study["name"] and cold.project_uuid == study["id"]
    binding = PyPSAService.bind_request_context(cold)
    try:
        assert wiring._bound_project_kind(None) == "planning_dynamics"
        assert "gridspine_run_pipeline" in {t["name"] for t in wiring._tools_payload()}
    finally:
        PyPSAService.reset_request_context(binding)


def test_networkless_study_activation_preserves_tenant_access(client, other_org_client):
    study = chat_tools.gridspine_create_study("Private Study", {"hours": 24})
    denied = other_org_client.post(f"/api/projects/{study['id']}/activate")
    assert denied.status_code == 404
    assert client.post(f"/api/projects/{study['id']}/activate").status_code == 200


def test_ordinary_project_without_network_still_cannot_activate(client):
    with chat_tools._acting() as (db, user):
        project = project_registry.create_root(db, user, "Missing Network")
        project_id = str(project.id)
    assert client.post(f"/api/projects/{project_id}/activate").status_code == 404


def test_deleted_study_config_cannot_activate(client):
    study = chat_tools.gridspine_create_study("Missing Study Config", {"hours": 24})
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, study["id"])
        (gridspine_service.gridspine_dir(project) / "config.json").unlink()
    assert client.post(f"/api/projects/{study['id']}/activate").status_code == 404
