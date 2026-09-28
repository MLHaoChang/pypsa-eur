"""
The `update_solver_config` chat tool must reach the user-code gate with REAL
dependencies, not `Depends` sentinels.

`services/chat_tools.update_solver_config` called
`routers.simulation.update_solver_config(body)` directly, around `_route`. When
the per-route authorization audit gave that handler `db` and `actor`
dependencies, the tool started handing `fastapi.params.Depends` objects to
`is_org_admin`, and the call died with

    AttributeError: 'Depends' object has no attribute 'is_super_admin'

It failed CLOSED — nothing was stored, which is why it took a review to notice —
but the failure mode was wrong in both directions:

  * a member got an opaque `AttributeError` instead of the authored
    `user_code_forbidden` 403, so the model could not tell the user what to do
    about it, and
  * an ADMIN, who is allowed to set the field, could not set it through chat at
    all. "Fails closed" hid a broken feature behind a security guard.

`_route` exists precisely to resolve a handler's declared dependencies, and its
tripwire (`RuntimeError: cannot satisfy dependency`) is what should have caught
this — except the tool never called `_route`. The second half of the fix is that
`_route` knows the name `actor`: the codebase uses `actor` and `user` for the
same thing (14 sites vs 62), and a helper that only understands one of the two
spellings is a trap for the next handler.

OPEN-ITEMS item 5. Found by an independent QA review, 2026-09-12.
"""
import uuid as _uuid
from dataclasses import asdict
from datetime import datetime, timezone

import pytest
from fastapi import Depends, HTTPException

from services import chat_tools


_CODE = "def extra_functionality(n, sns):\n    pass\n"


def _live_code() -> str:
    from routers.simulation import _state

    return (asdict(_state["solver_config"]).get("extra_functionality_code") or "").strip()


@pytest.fixture
def acting_member(_auth_db, seeded_identity):
    """Bind a plain MEMBER of the seeded org as the chat tools' acting user."""
    from db.models import OrgMembership, User
    from services.auth_service import hash_password

    _engine, session_local = _auth_db
    with session_local() as db:
        u = User(
            id=_uuid.uuid4(),
            email=f"m-{_uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("irrelevant"),
            status="active",
            is_super_admin=False,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(u)
        db.flush()
        db.add(OrgMembership(id=_uuid.uuid4(), user_id=u.id,
                             org_id=seeded_identity["org_id"], role="member"))
        db.commit()
        uid = str(u.id)
    previous = chat_tools.acting_user_id()
    chat_tools.set_acting_user(uid)
    yield uid
    chat_tools.set_acting_user(previous)


@pytest.fixture
def clean_solver_code():
    """Leave `_state` as we found it — it is a process global."""
    from routers.simulation import _state

    before = _state["solver_config"]
    yield
    _state["solver_config"] = before


def test_an_admin_can_set_user_code_through_the_chat_tool(
    clean_solver_code, monkeypatch,
):
    """
    The half of the bug that was a broken feature, not a leak.

    The acting identity here is the seeded org admin and the operator flag is
    on, so this is the SUPPORTED path — and it raised `AttributeError` from
    inside `is_org_admin` before the fix.
    """
    monkeypatch.setenv("PYPSA_GUI_ALLOW_USER_CODE", "1")
    out = chat_tools.update_solver_config({"extra_functionality_code": _CODE})
    assert _CODE.strip() in (out.get("extra_functionality_code") or ""), out
    assert _live_code() == _CODE.strip()


def test_a_member_gets_the_authored_refusal_not_an_attributeerror(
    acting_member, clean_solver_code, monkeypatch,
):
    """
    The other half: refused, but refused legibly.

    `HTTPException(403, user_code_forbidden)` is what the chat error surface
    knows how to render; an `AttributeError` is what it renders as an internal
    error. Both keep the code out of `_state`, so the store assertion is here to
    prove the fix did not trade the message for the protection.
    """
    monkeypatch.setenv("PYPSA_GUI_ALLOW_USER_CODE", "1")
    with pytest.raises(HTTPException) as excinfo:
        chat_tools.update_solver_config({"extra_functionality_code": _CODE})
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["error_kind"] == "user_code_forbidden"
    assert not _live_code(), "refused and stored it anyway"


def test_the_operator_flag_still_refuses_through_the_chat_tool(
    clean_solver_code, monkeypatch,
):
    """
    Flag off refuses even the admin. This passed before the fix — the flag check
    runs before the handler touches `actor` — and is kept as the guard that
    routing through `_route` did not reorder the two conditions.
    """
    monkeypatch.delenv("PYPSA_GUI_ALLOW_USER_CODE", raising=False)
    with pytest.raises(HTTPException) as excinfo:
        chat_tools.update_solver_config({"extra_functionality_code": _CODE})
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["error_kind"] == "user_code_disabled"
    assert not _live_code()


def test_an_ordinary_partial_still_merges_through_the_chat_tool(clean_solver_code):
    """
    The common path, which has no user code in it at all: routing the tool
    through `_route` must not have cost it the partial-PUT semantics.
    """
    from routers.simulation import _state

    before = asdict(_state["solver_config"])
    out = chat_tools.update_solver_config({"transmission_losses": True})
    assert out["transmission_losses"] is True
    assert out["solver_name"] == before["solver_name"], "a partial reset a sibling field"


def test_route_resolves_a_dependency_named_actor():
    """
    The general half of the fix, pinned on `_route` itself rather than on the one
    handler that exposed it.

    `_route`'s contract is "resolve whatever the target declares", and it knew
    only `db`/`user`/`session`. `actor` is the same value under the spelling
    `routers/admin.py`, `routers/chat.py` and `routers/simulation.py` use, so a
    handler naming it that way tripped the RuntimeError meant for genuinely
    unsatisfiable dependencies.
    """
    from db.models import User

    def handler(db=Depends(lambda: None), actor=Depends(lambda: None)):
        return db, actor

    db, actor = chat_tools._route(handler)
    assert db is not None and not isinstance(db, type(Depends(lambda: None)))
    assert isinstance(actor, User), f"actor arrived as {actor!r}"
