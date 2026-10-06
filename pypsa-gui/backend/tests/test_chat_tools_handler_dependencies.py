"""
Chat tools that call a router handler must hand it every dependency it
declares (merge review 2026-09-29, B1 and N6).

A handler reached directly from ``services/chat_tools.py`` does not go
through FastAPI's dependency injection. A ``db`` / ``user`` / ``actor``
parameter left at its ``Depends(...)`` default arrives as the raw sentinel,
and the handler dies with ``AttributeError`` deep inside a lock or tenancy
check. Master's sidecar lock gate (68e5f62c3) added ``db`` + ``user`` to
``put_stress_scenarios`` after the branch's P22 chat tool was written; the
unit test's ``uuid="u-1"`` project double skipped the lock branch and hid it.

The fix is ``_route``, which injects the acting identity and fails loudly on
any dependency it cannot satisfy. These tests pin:

* a static guard: no chat tool calls a router handler directly while leaving
  a ``Depends`` parameter unsupplied;
* ``put_stress_scenarios`` through chat on a REAL project uuid, in server
  mode: the lock holder writes, a non-holder gets 409 ``project_locked``;
* ``update_solver_config`` through chat carries the acting user to the
  user-code admin gate: an admin may set ``extra_functionality_code``, a
  plain member gets 403 ``user_code_forbidden`` (never an AttributeError).
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
import uuid as _uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi import params as fastapi_params

from services import chat_tools
from tests.test_worksheet_foreign_lock import same_org_other_user  # noqa: F401

BACKEND = pathlib.Path(__file__).resolve().parent.parent
_CODE = "def extra_functionality(n, snapshots):\n    pass\n"


# ── static guard ─────────────────────────────────────────────────────────────


def _router_aliases(fn: ast.FunctionDef) -> tuple[dict, dict]:
    """``{local_name: (module, attr)}`` for ``from routers.x import y [as z]``
    and ``{local_name: module}`` for ``import routers.x as m``."""
    names, modules = {}, {}
    for node in ast.walk(fn):
        if isinstance(node, ast.ImportFrom) and node.module \
                and node.module.startswith("routers"):
            for a in node.names:
                names[a.asname or a.name] = (node.module, a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("routers"):
                    modules[a.asname or a.name] = a.name
    return names, modules


def _unsupplied(handler, call: ast.Call) -> list[str]:
    try:
        ps = inspect.signature(handler).parameters
    except (TypeError, ValueError):
        return []
    given = {k.arg for k in call.keywords if k.arg}
    given |= set(list(ps)[:len(call.args)])
    if any(k.arg is None for k in call.keywords):
        return []                                   # **kwargs: cannot tell
    return [p for p, v in ps.items()
            if isinstance(v.default, fastapi_params.Depends) and p not in given]


def direct_calls_missing_dependencies() -> list[str]:
    tree = ast.parse((BACKEND / "services" / "chat_tools.py").read_text())
    out = []
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        names, modules = _router_aliases(fn)
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            target = None
            if isinstance(node.func, ast.Name) and node.func.id in names:
                target = names[node.func.id]
            elif isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id in modules:
                target = (modules[node.func.value.id], node.func.attr)
            if target is None:
                continue
            try:
                h = getattr(importlib.import_module(target[0]), target[1])
            except (ImportError, AttributeError):
                continue
            if not callable(h) or isinstance(h, type):
                continue
            missing = _unsupplied(h, node)
            if missing:
                out.append(f"{fn.name} -> {target[0]}.{target[1]}: {missing}")
    return out


def test_no_chat_tool_leaves_a_handler_dependency_unsupplied():
    assert direct_calls_missing_dependencies() == [], (
        "call these through _route(...) or pass the dependency explicitly")


def test_the_guard_sees_a_direct_call_that_misses_a_dependency():
    """Self-test: the scan flags the exact shape of the B1 bug."""
    from routers.adequacy_worksheet import put_stress_scenarios as h
    call = ast.parse("_h(body=b, project=p)").body[0].value
    assert set(_unsupplied(h, call)) == {"db", "user"}
    ok = ast.parse("_h(body=b, project=p, db=d, user=u)").body[0].value
    assert _unsupplied(h, ok) == []


# ── B1: put_stress_scenarios through chat on a real project ────────────────


def test_chat_resolves_a_real_project_uuid(client, api_project):
    proj = chat_tools._authorized_project(api_project("chat-ss-uuid"))
    _uuid.UUID(proj.uuid)       # a real uuid, unlike the "u-1" unit double


def test_the_lock_holder_can_put_stress_scenarios_via_chat(client, api_project):
    name = api_project("chat-ss-holder")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200
    # HTTP control: the holder may write.
    assert client.put(f"/api/projects/{name}/stress_scenarios",
                      json={"scenarios": []}).status_code == 200
    # The chat tool, acting as the same (holder) user.
    assert chat_tools.put_stress_scenarios(name, []) == {"scenarios": []}


def test_a_non_holder_chat_write_is_refused_with_409(
        client, api_project, same_org_other_user):
    name = api_project("chat-ss-foreign")
    client.delete(f"/api/projects/{name}/lock")
    assert same_org_other_user.post(
        f"/api/projects/{name}/lock").status_code == 200
    with pytest.raises(HTTPException) as exc:
        chat_tools.put_stress_scenarios(name, [])
    assert exc.value.status_code == 409
    detail = exc.value.detail
    kind = detail.get("error_kind") if isinstance(detail, dict) else str(detail)
    assert "project_locked" in str(kind)


# ── N6: update_solver_config through chat reaches the admin gate ────────────


@pytest.fixture
def _member_id(_auth_db, seeded_identity):
    """A plain MEMBER of the seeded org (the seeded user is an admin)."""
    from db.models import OrgMembership, User
    from services.auth_service import hash_password

    _engine, session_local = _auth_db
    with session_local() as db:
        user = User(
            id=_uuid.uuid4(),
            email=f"member-{_uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("irrelevant"),
            status="active", is_super_admin=False,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(user)
        db.flush()
        db.add(OrgMembership(id=_uuid.uuid4(), user_id=user.id,
                             org_id=seeded_identity["org_id"], role="member"))
        db.commit()
        return str(user.id)


def test_an_admin_can_set_user_code_via_chat(client, monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_ALLOW_USER_CODE", "1")
    try:
        out = chat_tools.update_solver_config({"extra_functionality_code": _CODE})
        assert out["extra_functionality_code"] == _CODE
    finally:
        chat_tools.update_solver_config({"extra_functionality_code": ""})


def test_a_member_cannot_set_user_code_via_chat(client, monkeypatch, _member_id):
    monkeypatch.setenv("PYPSA_GUI_ALLOW_USER_CODE", "1")
    chat_tools.set_acting_user(_member_id)
    with pytest.raises(HTTPException) as exc:
        chat_tools.update_solver_config({"extra_functionality_code": _CODE})
    assert exc.value.status_code == 403
    assert exc.value.detail["error_kind"] == "user_code_forbidden"


def test_an_ordinary_solver_knob_via_chat_still_works(client):
    out = chat_tools.update_solver_config({"voll": 4321.0})
    assert out["voll"] == 4321.0


def test_no_acting_user_fails_closed_on_user_code(client, monkeypatch):
    """Without a bound identity the gate sees no actor → 403, never a crash."""
    monkeypatch.setenv("PYPSA_GUI_ALLOW_USER_CODE", "1")
    chat_tools.set_acting_user(None)
    with pytest.raises(HTTPException) as exc:
        chat_tools.update_solver_config({"extra_functionality_code": _CODE})
    assert exc.value.status_code == 403
