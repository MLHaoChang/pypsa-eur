"""
Single-user local mode.

The desktop build has no login. Rather than delete the tenancy layer — 68
require_user/optional_user sites, org-scoped storage, a large test suite —
local mode seeds ONE org + user + membership and injects that user on every
request. Every downstream check then passes for the reason it was written to
pass, and the web deployment keeps working unchanged.

The IDs are fixed constants, not generated: `projects_root/<org_id>/<project_id>/`
embeds the org id, so a regenerated id would orphan every project directory on
reinstall.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from db.models import OrgMembership, Organization, User

LOCAL_ORG_ID = uuid.UUID("00000000-0000-4000-8000-00000000da7a")
LOCAL_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000005e1f")
LOCAL_USER_EMAIL = "local@pypsa-gui.localhost"
LOCAL_ORG_NAME = "Local"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def is_local_mode() -> bool:
    """
    True when the launcher set ``PYPSAGUI_LOCAL_MODE``.

    Read from ``os.environ`` on EVERY call, never cached. That is what lets one
    app object serve both modes, and what lets a test flip modes by
    monkeypatching the environment without re-importing anything — re-importing
    is unsafe here, because ``del sys.modules["db.session"]`` is a no-op for
    ``from db import session`` and leaves the module graph half-swapped.

    The prefix is ``PYPSAGUI_``, not ``PYPSA_GUI_``: PyPSA's option system
    claims the whole ``PYPSA_*`` namespace and warns about unknown options on
    every boot.
    """
    return os.environ.get("PYPSAGUI_LOCAL_MODE", "").strip().lower() in _TRUTHY


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ensure_local_identity(db: DBSession) -> User:
    """
    Idempotently seed the local org, user and membership. Returns the user.

    Select-then-insert rather than merge: ``users.email`` is uniquely indexed
    and ``OrgMembership`` carries ``UniqueConstraint("user_id")``, so a blind
    insert on the second boot raises IntegrityError.

    ``status="active"`` is required — ``auth_service`` rejects anything else,
    and the column defaults to ``"invited"``. Both ``created_at`` columns are
    NOT NULL with no Python default, so they are set explicitly.
    ``password_hash`` stays NULL: there is no login to perform.

    ``is_super_admin`` AND ``role="admin"`` are both needed. The first gates
    ``/api/admin/*``; the second is the only see-everything short-circuit in
    ``project_acl.can_access_project``.
    """
    if db.get(Organization, LOCAL_ORG_ID) is None:
        db.add(Organization(id=LOCAL_ORG_ID, name=LOCAL_ORG_NAME, created_at=_now_utc()))
        db.flush()

    user = db.get(User, LOCAL_USER_ID)
    if user is None:
        user = User(
            id=LOCAL_USER_ID,
            email=LOCAL_USER_EMAIL,
            password_hash=None,
            status="active",
            is_super_admin=True,
            created_at=_now_utc(),
        )
        db.add(user)
        db.flush()

    if db.scalar(select(OrgMembership).where(OrgMembership.user_id == LOCAL_USER_ID)) is None:
        db.add(OrgMembership(org_id=LOCAL_ORG_ID, user_id=LOCAL_USER_ID, role="admin"))

    db.commit()
    db.refresh(user)
    return user


def remove_local_identity(db: DBSession) -> None:
    """
    Delete the seeded membership, user and org. Test teardown only.

    conftest's shared session-scoped database deliberately persists users and
    orgs between tests (`_reset_tenant_tables` truncates only the project
    tables), so a local-mode fixture that seeds without cleaning up leaks a
    super-admin into every subsequent test. Safe to call unseeded.
    """
    membership = db.scalar(
        select(OrgMembership).where(OrgMembership.user_id == LOCAL_USER_ID)
    )
    if membership is not None:
        db.delete(membership)
    for model, pk in ((User, LOCAL_USER_ID), (Organization, LOCAL_ORG_ID)):
        row = db.get(model, pk)
        if row is not None:
            db.delete(row)
    db.commit()


def get_local_user(db: DBSession) -> User | None:
    """
    Re-fetch the seeded user in the CALLER's session.

    Never cache the ORM object across requests: sessionmaker uses the default
    ``expire_on_commit=True``, so a cached instance is detached and reading
    ``user.id`` raises DetachedInstanceError inside ``project_registry`` /
    ``project_acl``.
    """
    return db.get(User, LOCAL_USER_ID)
