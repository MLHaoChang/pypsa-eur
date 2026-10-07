"""
The campus-electrical chat tools must respect another user's edit lock.

OPEN-ITEMS item 14. `routers/campus_electrical.py` runs `_check_lock` in each
write handler and only then calls the service. The four write tools called the
SERVICE directly, so a non-holder could redraft (with `overwrite=True`), run,
add a grid-code draft to, or overwrite the asset library of a project another
user was editing. Each one arrived with a later PR: #79 added two, #84 one and
#91 one. The write-surface policy test caught each on the day it landed.

These tools take a `project_id`, NOT the session's active project. So the chat
seam's `_check_foreign_lock`, which tests the ACTIVE project, is the wrong
check for them: it would test some other project's lock. The fix checks the
lock of the project the tool actually resolves, through
`_gridspine_project_for_write`.

Server only: `_check_project_lock` early-returns in local mode.
"""
import pytest
from fastapi import HTTPException

from services import campus_electrical_service as ce
from services import campus_grid_code_service as gc
from services import chat_tools
from tests.test_chat_tools_dispatch import (
    _bound_to, _hold_foreign_lock, _project_row_id, _save_and_bind_project,
)

# tool -> (service module, service function, tool kwargs)
WRITE_TOOLS = {
    "campus_draft_campus": (ce, "draft", {"overwrite": True}),
    "campus_run_study": (ce, "run", {}),
    "campus_extract_grid_code": (gc, "extract", {"document_id": "doc1"}),
    "campus_set_library": (ce, "save_library", {"yaml": "intruder: true\n"}),
}
READ_TOOLS = {
    "campus_get_study": (ce, "get_state", {}),
    "campus_get_library": (ce, "get_library", {}),
}


@pytest.fixture
def campus_project(client, install_network, _auth_db, session_ctx):
    """A saved, active project. The acting chat identity is the seeded user."""
    _engine, session_local = _auth_db
    name = "CampusLockTarget"
    _save_and_bind_project(client, install_network, name)
    return name, _project_row_id(session_local, name), session_ctx(client), session_local


def _record(monkeypatch, module, fn):
    calls = []
    monkeypatch.setattr(module, fn, lambda *a, **k: calls.append((fn, a[1:], k)) or {"stub": fn})
    return calls


def _is_lock_refusal(exc) -> bool:
    d = exc.detail
    return exc.status_code == 409 and isinstance(d, dict) and d.get("error_kind") == "project_locked"


def test_the_http_twins_really_are_lock_checked(client, campus_project, second_identity):
    """
    Control. If the HTTP routes ever stopped checking the lock, the tool tests
    below would be measuring nothing, so this is asserted rather than assumed.
    """
    name, pid, _ctx, sl = campus_project
    _hold_foreign_lock(sl, pid, second_identity["user_id"])
    for method, path, body in (
        ("post", f"/api/campus-electrical/{name}/draft", {"overwrite": True}),
        ("post", f"/api/campus-electrical/{name}/run", {}),
        ("post", f"/api/campus-electrical/{name}/grid-codes/documents/doc1/extract", {}),
        ("put", f"/api/campus-electrical/{name}/library", {"yaml": "x: 1\n"}),
    ):
        r = getattr(client, method)(path, json=body)
        assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "project_locked", (
            f"control {method.upper()} {path}: {r.status_code} {r.text[:160]}"
        )


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_a_write_tool_is_refused_under_a_foreign_lock(tool, campus_project, second_identity, monkeypatch):
    """The refusal AND the absence of the write: the service must not be reached."""
    name, pid, ctx, sl = campus_project
    module, fn, kw = WRITE_TOOLS[tool]
    calls = _record(monkeypatch, module, fn)
    _hold_foreign_lock(sl, pid, second_identity["user_id"])

    with _bound_to(ctx), pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS[tool](project_id=name, **kw)
    assert not calls, f"{tool} reached {fn} under a foreign lock: {calls}"
    assert _is_lock_refusal(exc.value), f"{tool}: {exc.value.status_code} {exc.value.detail}"


def test_the_holders_library_survives_a_refused_overwrite(client, campus_project, second_identity):
    """
    The property, with the real service rather than a recorder: the holder's
    edited asset library is byte-identical after a non-holder's refused
    `campus_set_library`.
    """
    name, pid, ctx, sl = campus_project
    default = client.get(f"/api/campus-electrical/{name}/library").json()["yaml"]
    holders = default + "\n# the holder's edit\n"
    saved = client.put(f"/api/campus-electrical/{name}/library", json={"yaml": holders})
    assert saved.status_code == 200, saved.text[:200]
    before = client.get(f"/api/campus-electrical/{name}/library").json()
    assert before["is_default"] is False and "the holder's edit" in before["yaml"]

    _hold_foreign_lock(sl, pid, second_identity["user_id"])
    with _bound_to(ctx), pytest.raises(HTTPException):
        chat_tools.DISPATCHERS["campus_set_library"](project_id=name, yaml=default + "\n# intruder\n")

    after = client.get(f"/api/campus-electrical/{name}/library").json()
    assert after == before, "a non-holder's chat tool overwrote the holder's asset library"


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_the_holder_can_still_use_each_write_tool(tool, campus_project, monkeypatch):
    """Check-only: the acting user's own lock must not refuse them."""
    name, _pid, ctx, _sl = campus_project  # the save made the acting user the holder
    module, fn, kw = WRITE_TOOLS[tool]
    calls = _record(monkeypatch, module, fn)
    with _bound_to(ctx):
        chat_tools.DISPATCHERS[tool](project_id=name, **kw)
    assert calls, f"the holder was refused {tool}"


@pytest.mark.parametrize("tool", sorted(READ_TOOLS))
def test_read_tools_stay_open_under_a_foreign_lock(tool, campus_project, second_identity, monkeypatch):
    """
    Why the check is not in `_gridspine_project` itself: reads go through it
    too, and a non-holder viewing a project must still be able to read it.
    """
    name, pid, ctx, sl = campus_project
    module, fn, kw = READ_TOOLS[tool]
    calls = _record(monkeypatch, module, fn)
    _hold_foreign_lock(sl, pid, second_identity["user_id"])
    with _bound_to(ctx):
        chat_tools.DISPATCHERS[tool](project_id=name, **kw)
    assert calls, f"{tool} was refused for a non-holder; reads must stay open"


def test_a_write_tool_on_a_free_project_does_not_take_the_lock(campus_project, monkeypatch):
    """Check-only, not acquire: a passive write must not leave a claim behind."""
    from db.models import ProjectLock
    from services import project_locks

    name, pid, ctx, sl = campus_project
    with sl() as db:  # the save acquired it; free the project first
        row = db.get(ProjectLock, pid)
        if row is not None:
            db.delete(row)
            db.commit()
    _record(monkeypatch, ce, "save_library")
    with _bound_to(ctx):
        chat_tools.DISPATCHERS["campus_set_library"](project_id=name, yaml="x: 1\n")
    with sl() as db:
        assert project_locks.get_lock(db, pid) is None, "the tool acquired the lock instead of checking it"
