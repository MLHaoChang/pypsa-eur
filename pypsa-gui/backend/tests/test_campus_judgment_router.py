"""The pack read-back routes (grid-compliance GC-22) wrap ``services.campus_judgment``.

Each route calls its one service function with the project row; the POSTs are
writes (lock-checked, refused for a viewer) and the GET is a read.
"""

import pytest

from db.models import Project
from services import campus_judgment as cj
from services.project_write_gate import KIND
from tests.test_campus_electrical_router import hub  # noqa: F401
from tests.test_viewer_role_gate import as_user, baseline, world  # noqa: F401
from tests.test_worksheet_foreign_lock import _is_lock_refusal, same_org_other_user  # noqa: F401

BASE = "/api/campus-electrical/Router Hub"


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(cj, "add_results", lambda project, bundle, files: seen.append(
        ("add_results", project, bundle, files)) or {"ok": "add_results"})
    monkeypatch.setattr(cj, "judge", lambda project, bundle: seen.append(
        ("judge", project, bundle)) or {"ok": "judge"})
    monkeypatch.setattr(cj, "get_judgment", lambda project: seen.append(
        ("get_judgment", project)) or {"ok": "get_judgment"})
    return seen


def test_the_upload_passes_every_file_with_its_name_and_bytes(client, hub, calls):  # noqa: F811
    resp = client.post(f"{BASE}/psse/b_p2030_h1/emt/results",
                       files=[("files", ("a.csv", b"1")), ("files", ("a_01.out", b"22"))])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": "add_results"}
    (name, project, bundle, files), = calls
    assert name == "add_results" and isinstance(project, Project) and project.name == "Router Hub"
    assert bundle == "b_p2030_h1" and files == [("a.csv", b"1"), ("a_01.out", b"22")]


def test_judge_and_get_call_their_function_with_the_row(client, hub, calls):  # noqa: F811
    assert client.post(f"{BASE}/psse/b_p2030_h1/emt/judge").json() == {"ok": "judge"}
    assert client.get(f"{BASE}/judgment").json() == {"ok": "get_judgment"}
    assert [c[0] for c in calls] == ["judge", "get_judgment"]
    assert calls[0][2] == "b_p2030_h1" and all(c[1].name == "Router Hub" for c in calls)


def test_too_many_files_are_refused_before_any_is_read(client, hub, calls, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cj, "MAX_RESULT_FILES", 1)
    resp = client.post(f"{BASE}/psse/b/emt/results", files=[("files", ("a.csv", b"1")), ("files", ("b.csv", b"2"))])
    assert resp.status_code == 413 and resp.json()["detail"]["error_kind"] == "campus_results_too_many"
    assert calls == []


def test_a_total_over_the_cap_is_413(client, hub, calls, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cj, "MAX_RESULT_BYTES", 3)
    resp = client.post(f"{BASE}/psse/b/emt/results", files=[("files", ("a.csv", b"12")), ("files", ("b.csv", b"34"))])
    assert resp.status_code == 413 and calls == []


def test_a_foreign_lock_refuses_both_writes_but_not_the_read(
        client, hub, calls, same_org_other_user):  # noqa: F811
    assert client.post("/api/projects/Router Hub/lock").status_code == 200
    up = same_org_other_user.post(f"{BASE}/psse/b/emt/results", files=[("files", ("a.csv", b"1"))])
    again = same_org_other_user.post(f"{BASE}/psse/b/emt/judge")
    for resp in (up, again):
        assert resp.status_code == 409 and resp.json()["detail"]["error_kind"] == "project_locked", resp.text
    assert same_org_other_user.get(f"{BASE}/judgment").status_code == 200
    assert [c[0] for c in calls] == ["get_judgment"]


def test_another_org_gets_404_on_every_route(client, hub, calls, other_org_client):  # noqa: F811
    assert other_org_client.get(f"{BASE}/judgment").status_code == 404
    assert other_org_client.post(f"{BASE}/psse/b/emt/judge").status_code == 404
    assert other_org_client.post(f"{BASE}/psse/b/emt/results", files=[("files", ("a.csv", b"1"))]).status_code == 404
    assert calls == []


def test_a_viewer_reads_the_judgment_and_is_refused_both_writes(world, as_user, calls):  # noqa: F811
    viewer = as_user(world["viewer"])
    assert viewer.post(f"/api/projects/{world['project']}/activate").status_code == 200
    base = f"/api/campus-electrical/{world['project']}"
    assert viewer.get(f"{base}/judgment").status_code == 200
    for resp in (viewer.post(f"{base}/psse/b/emt/judge"),
                 viewer.post(f"{base}/psse/b/emt/results", files=[("files", ("a.csv", b"1"))])):
        assert resp.status_code == 403 and resp.json()["detail"]["error_kind"] == KIND, resp.text
    assert [c[0] for c in calls] == ["get_judgment"]
