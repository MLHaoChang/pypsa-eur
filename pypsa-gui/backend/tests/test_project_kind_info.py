"""`project_kind` reaches the project DTO (follow-up B).

The column landed with migration 0006 and the gridspine service reads it; the
project LIST did not carry it, so a client could not tell a study from a
network project without calling a gridspine endpoint and reading the 409. The
DTO now carries the raw column: NULL for every ordinary project (the client
resolves the default), `planning_dynamics` for a study.
"""
CONFIG = {"hours": 24, "k": 1, "window": 24, "overlap": 0, "screen": False}


def _rows(client):
    resp = client.get("/api/projects/")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return {r["name"]: r for r in (body if isinstance(body, list) else body.get("projects", body))}


def test_a_study_and_a_network_project_carry_their_kinds(client, api_project):
    plain = api_project("plain network")
    resp = client.post("/api/gridspine/projects", json={"name": "A Study", "config": CONFIG})
    assert resp.status_code == 200, resp.text

    rows = _rows(client)
    assert rows["A Study"]["project_kind"] == "planning_dynamics"
    assert rows[plain]["project_kind"] is None          # NULL means capacity_expansion


def test_an_empty_kind_string_is_normalised_to_none():
    from models.schemas import ProjectInfo

    info = ProjectInfo(name="x", created_at="2026-01-01T00:00:00", has_solver_config=False,
                       bus_count=0, snapshot_count=0, project_kind="  ")
    assert info.project_kind is None
