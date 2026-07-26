# Multi-User Org Tenancy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add organization-scoped login, ACL-filtered projects (including scenario trees), advisory locks, and admin tooling to `pypsa-gui` while keeping the existing workbench and scenario flows working.

**Architecture:** Hybrid multi-tenant design — Postgres for users/orgs/sessions/project registry/membership/locks; filesystem bundles under `projects/{org_id}/{project_id}/`. FastAPI dependencies enforce auth + tree-aware ACL; React adds `/login`, `/projects`, `/admin/*`, and moves the workbench to `/app`. Feature flag `PYPSA_GUI_AUTH_ENABLED` allows stepwise integration.

**Tech Stack:** FastAPI, SQLAlchemy 2.x + Alembic, PostgreSQL, passlib/bcrypt (or argon2), server-side sessions, SMTP email, React 19 + react-router-dom 7, Vitest/pytest, Docker Compose for Postgres + Mailpit.

**Spec:** `docs/superpowers/specs/2026-07-26-multi-user-org-tenancy-design.md`

## Global Constraints

- Organization workspaces; each user belongs to exactly one org in v1.
- Email + password; admin-created accounts only; set-password + forgot-password email links.
- Org roles: Admin + Member; project assignment by creator or org admin.
- Scenario trees must keep working; ACL inherits from **tree root**; locks are **per node**.
- Hybrid storage: Postgres metadata/ACL; filesystem bundles (path resolver seam for later object storage).
- Prefer stable project UUIDs in APIs; display names unique per org; keep `parent_project` name on `ProjectInfo` for existing UI.
- Unauthorized project access returns **404**.
- Implement behind `PYPSA_GUI_AUTH_ENABLED`; when false, preserve today’s single-user behavior.
- Keep auth/ACL/email/storage behind narrow modules for post-v1 iteration (SSO, per-node ACL, S3).
- Do not redesign Scenarios panel / Compare UX beyond ID/ACL wiring.
- TDD: failing test → implement → pass → commit per task.

---

## File structure (create/modify)

**Create**
- `pypsa-gui/docker-compose.yml` — Postgres + Mailpit
- `pypsa-gui/backend/db/{session.py,models.py,base.py}`
- `pypsa-gui/backend/alembic/` + `alembic.ini`
- `pypsa-gui/backend/services/{auth_service.py,tenancy_service.py,project_acl.py,project_locks.py,email_service.py,storage_paths.py}`
- `pypsa-gui/backend/routers/{auth.py,admin.py}`
- `pypsa-gui/backend/deps.py` — FastAPI dependencies (`get_db`, `require_user`, `require_project_access`)
- `pypsa-gui/backend/settings.py` — env config
- `pypsa-gui/backend/tests/test_{auth,tenancy,project_acl,project_locks,legacy_migrate}.py`
- `pypsa-gui/frontend/src/api/auth.ts`, `admin.ts`
- `pypsa-gui/frontend/src/auth/{AuthProvider.tsx,RequireAuth.tsx,RequireAdmin.tsx}`
- `pypsa-gui/frontend/src/pages/{LoginPage.tsx,SetPasswordPage.tsx,ResetPasswordPage.tsx,ProjectsHomePage.tsx}`
- `pypsa-gui/frontend/src/pages/admin/{AdminLayout.tsx,UsersPage.tsx,OrgsPage.tsx,LegacyMigratePage.tsx,EmailSettingsPage.tsx}`
- `pypsa-gui/frontend/src/components/{LockBanner.tsx,AssignMembersDialog.tsx,UserMenu.tsx}`
- `pypsa-gui/frontend/src/routes.tsx`

**Modify**
- `pypsa-gui/backend/requirements.txt`, root `pixi.toml` (deps + compose helpers)
- `pypsa-gui/backend/main.py` — mount routers, auth middleware when enabled
- `pypsa-gui/backend/routers/projects.py` — ACL gates, UUID resolution, persist `parent_project_id`
- `pypsa-gui/backend/models/schemas.py` — add `id`, keep `parent_project` name
- `pypsa-gui/backend/tests/conftest.py` — DB fixtures, auth helpers
- `pypsa-gui/frontend/src/main.tsx` — route tree
- `pypsa-gui/frontend/src/App.tsx` — workbench-only under `/app`
- `pypsa-gui/frontend/src/api/client.ts` — credentials + 401 → `/login`
- `pypsa-gui/frontend/src/api/projects.ts` / types — `id` fields
- `pypsa-gui/frontend/src/layout/*` — user menu, back to projects

---

### Task 1: Settings, deps, and local infra

**Files:**
- Create: `pypsa-gui/backend/settings.py`
- Create: `pypsa-gui/docker-compose.yml`
- Modify: `pypsa-gui/backend/requirements.txt`
- Modify: `pixi.toml` (optional tasks `gui-db-up` / `gui-db-down`)

**Interfaces:**
- Produces: `Settings` with `auth_enabled`, `database_url`, `session_cookie_name`, `smtp_*`, `projects_root`, `legacy_root`, `secret_key`

- [ ] **Step 1: Add Python dependencies**

Append to `pypsa-gui/backend/requirements.txt`:

```text
sqlalchemy>=2.0
alembic>=1.13
psycopg[binary]>=3.1
pydantic-settings>=2.0
pwdlib[argon2]>=0.2
emails>=0.6
httpx>=0.27
```

Also add the same packages to the appropriate pixi feature used by `gui-backend` / `gui-tests` in `pixi.toml` (follow existing fastapi pins style).

- [ ] **Step 2: Create settings module**

```python
# pypsa-gui/backend/settings.py
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND = Path(__file__).resolve().parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_BACKEND / ".env"), extra="ignore")

    pypsa_gui_auth_enabled: bool = False
    database_url: str = "postgresql+psycopg://pypsa:pypsa@localhost:5432/pypsa_gui"
    secret_key: str = "dev-only-change-me"
    session_cookie_name: str = "pypsa_gui_session"
    session_ttl_hours: int = 72
    password_token_ttl_hours: int = 24
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@localhost"
    public_base_url: str = "http://localhost:5173"
    projects_root: Path = _BACKEND / "projects"
    legacy_root: Path = _BACKEND / "legacy_unclaimed"

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 3: Docker Compose for Postgres + Mailpit**

```yaml
# pypsa-gui/docker-compose.yml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: pypsa
      POSTGRES_PASSWORD: pypsa
      POSTGRES_DB: pypsa_gui
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
  mailpit:
    image: axllent/mailpit:latest
    ports: ["1025:1025", "8025:8025"]
volumes:
  pgdata:
```

- [ ] **Step 4: Smoke the stack**

Run:

```bash
cd pypsa-gui && docker compose up -d
docker compose ps
```

Expected: `db` and `mailpit` healthy/up.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/settings.py pypsa-gui/backend/requirements.txt pypsa-gui/docker-compose.yml pixi.toml
git commit -m "chore(gui): add auth settings and local Postgres/Mailpit compose"
```

---

### Task 2: Database models + Alembic

**Files:**
- Create: `pypsa-gui/backend/db/base.py`, `models.py`, `session.py`
- Create: `pypsa-gui/backend/alembic.ini`, `alembic/env.py`, `alembic/versions/0001_tenancy.py`
- Modify: `pypsa-gui/backend/tests/conftest.py`

**Interfaces:**
- Produces: SQLAlchemy models `User`, `Organization`, `OrgMembership`, `Project`, `ProjectMembership`, `ProjectLock`, `AuthToken`, `Session`
- Produces: `get_engine()`, `SessionLocal`, `get_db()` generator

- [ ] **Step 1: Write failing test that models import and create tables**

```python
# pypsa-gui/backend/tests/test_db_models.py
from db.models import User, Organization, Project

def test_project_has_parent_fk():
    assert "parent_project_id" in Project.__table__.columns
    assert "org_id" in Project.__table__.columns
```

- [ ] **Step 2: Run test — expect fail (module missing)**

```bash
cd pypsa-gui/backend && python -m pytest tests/test_db_models.py -v
```

Expected: `ModuleNotFoundError: db`

- [ ] **Step 3: Implement models**

```python
# pypsa-gui/backend/db/models.py (core fields)
import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, ForeignKey, UniqueConstraint, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from db.base import Base

class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="invited")  # invited|active|disabled
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class OrgMembership(Base):
    __tablename__ = "org_memberships"
    __table_args__ = (UniqueConstraint("user_id"),)  # one org per user v1
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32))  # admin|member

class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("org_id", "name"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    storage_path: Mapped[str] = mapped_column(Text)
    parent_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scenario_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class ProjectMembership(Base):
    __tablename__ = "project_memberships"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    assigned_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class ProjectLock(Base):
    __tablename__ = "project_locks"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    holder_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

class AuthToken(Base):
    __tablename__ = "auth_tokens"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    purpose: Mapped[str] = mapped_column(String(32))  # set_password|reset_password
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Implement `db/base.py` (`DeclarativeBase`) and `db/session.py` (`create_engine`, `sessionmaker`, `get_db`).

- [ ] **Step 4: Alembic initial migration**

```bash
cd pypsa-gui/backend && alembic revision --autogenerate -m "tenancy_v1"
alembic upgrade head
```

Expected: tables created in local Postgres.

- [ ] **Step 5: Re-run test — pass; add conftest DB fixture using transaction rollback**

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(gui): add SQLAlchemy tenancy models and Alembic migration"
```

---

### Task 3: Auth service (passwords, sessions, tokens)

**Files:**
- Create: `pypsa-gui/backend/services/auth_service.py`
- Create: `pypsa-gui/backend/tests/test_auth_service.py`

**Interfaces:**
- Produces:
  - `hash_password(password: str) -> str`
  - `verify_password(password: str, password_hash: str) -> bool`
  - `create_session(db, user_id) -> tuple[str, Session]`  # raw token for cookie
  - `resolve_session(db, raw_token) -> User | None`
  - `revoke_session(db, raw_token) -> None`
  - `revoke_all_sessions_for_user(db, user_id) -> None`
  - `issue_password_token(db, user_id, purpose) -> str`
  - `consume_password_token(db, raw_token, purpose) -> User`

- [ ] **Step 1: Failing tests**

```python
def test_password_hash_roundtrip():
    h = hash_password("secret-pass")
    assert h != "secret-pass"
    assert verify_password("secret-pass", h)
    assert not verify_password("wrong", h)

def test_session_resolve_and_revoke(db):
    user = _make_user(db)
    raw, _ = create_session(db, user.id)
    assert resolve_session(db, raw).id == user.id
    revoke_session(db, raw)
    assert resolve_session(db, raw) is None

def test_set_password_token_one_time(db):
    user = _make_user(db, status="invited")
    raw = issue_password_token(db, user.id, "set_password")
    u = consume_password_token(db, raw, "set_password")
    assert u.id == user.id
    assert consume_password_token(db, raw, "set_password") is None
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement `auth_service.py` using argon2 via pwdlib; store only sha256 hashes of random tokens**

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(gui): auth service for passwords, sessions, and tokens"
```

---

### Task 4: Auth HTTP API + dependencies

**Files:**
- Create: `pypsa-gui/backend/deps.py`
- Create: `pypsa-gui/backend/routers/auth.py`
- Create: `pypsa-gui/backend/tests/test_auth_api.py`
- Modify: `pypsa-gui/backend/main.py`

**Interfaces:**
- Produces FastAPI deps: `require_user(request, db) -> User`, `optional_user(...) -> User | None`
- Routes: login/logout/me/forgot/reset/set-password

- [ ] **Step 1: Failing API tests with TestClient + auth enabled**

```python
def test_login_sets_cookie(auth_client, user_with_password):
    r = auth_client.post("/api/auth/login", json={"email": user_with_password.email, "password": "secret-pass"})
    assert r.status_code == 200
    assert "pypsa_gui_session" in r.cookies

def test_me_requires_auth(auth_client):
    assert auth_client.get("/api/auth/me").status_code == 401

def test_forgot_password_generic(auth_client, user_with_password):
    r = auth_client.post("/api/auth/forgot-password", json={"email": "missing@example.com"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
```

- [ ] **Step 2: Implement router + cookie handling (`httponly`, `samesite=lax`, `secure` when not local)**

- [ ] **Step 3: Mount router in `main.py`; when `pypsa_gui_auth_enabled`, protect `/api/*` except auth public paths + health**

Public allowlist: `/api/auth/login`, `/api/auth/forgot-password`, `/api/auth/reset-password`, `/api/auth/set-password`, `/api/health` (add health if missing).

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(gui): auth HTTP API and require_user dependency"
```

---

### Task 5: Tenancy + admin user/org APIs

**Files:**
- Create: `pypsa-gui/backend/services/tenancy_service.py`
- Create: `pypsa-gui/backend/routers/admin.py`
- Create: `pypsa-gui/backend/tests/test_tenancy_api.py`
- Create: bootstrap script `pypsa-gui/backend/tools/bootstrap_super_admin.py`

**Interfaces:**
- `create_organization(db, name, actor) -> Organization` (super-admin only)
- `create_user(db, email, org_id, role, actor) -> tuple[User, set_password_raw_token]`
- Admin routes under `/api/admin/...`

- [ ] **Step 1: Failing tests — member cannot create users; admin can; super-admin creates org**

```python
def test_member_cannot_create_user(member_client):
    r = member_client.post("/api/admin/users", json={"email": "x@ex.com", "role": "member"})
    assert r.status_code == 403

def test_admin_creates_user_and_issues_token(admin_client, mail_outbox):
    r = admin_client.post("/api/admin/users", json={"email": "new@ex.com", "role": "member"})
    assert r.status_code == 201
    assert len(mail_outbox) == 1
```

- [ ] **Step 2: Implement tenancy service + admin router; wire email stub in tests**

- [ ] **Step 3: Bootstrap tool creates first super-admin from CLI env vars**

```bash
python tools/bootstrap_super_admin.py --email admin@example.com --password '...'
```

- [ ] **Step 4: Tests pass; commit**

```bash
git commit -m "feat(gui): org/user admin APIs and super-admin bootstrap"
```

---

### Task 6: Storage paths + project ACL (tree-aware)

**Files:**
- Create: `pypsa-gui/backend/services/storage_paths.py`
- Create: `pypsa-gui/backend/services/project_acl.py`
- Create: `pypsa-gui/backend/tests/test_project_acl.py`

**Interfaces:**
- `storage_path_for(org_id: UUID, project_id: UUID) -> Path`
- `resolve_tree_root(db, project: Project) -> Project`
- `can_access_project(db, user: User, project: Project) -> bool`
- `can_manage_membership(db, user, project) -> bool`  # root creator or org admin
- `can_delete_project(db, user, project) -> bool`  # per spec root vs scenario rules
- `list_accessible_projects(db, user, roots_only: bool=False) -> list[Project]`
- `ensure_project_access(db, user, project) -> Project`  # raises HTTPException 404

- [ ] **Step 1: Failing ACL matrix tests**

```python
def test_member_assigned_to_root_can_open_nested_scenario(db, org, admin, member):
    root = create_project(db, org, admin, name="Root")
    child = create_project(db, org, admin, name="Child", parent=root)
    assign(db, root, member)
    assert can_access_project(db, member, child)

def test_other_org_user_gets_no_access(db, org_a, org_b, user_b, root_a):
    assert not can_access_project(db, user_b, root_a)

def test_list_roots_only_hides_scenarios(db, user, root, child):
    assign(db, root, user)
    names = {p.name for p in list_accessible_projects(db, user, roots_only=True)}
    assert names == {"Root"}
```

- [ ] **Step 2: Implement ACL using root walk; membership rows attach to root project id only in v1 writers**

- [ ] **Step 3: Tests pass; commit**

```bash
git commit -m "feat(gui): tree-aware project ACL and storage path helper"
```

---

### Task 7: Wire projects router to registry + ACL + scenarios

**Files:**
- Modify: `pypsa-gui/backend/routers/projects.py`
- Modify: `pypsa-gui/backend/models/schemas.py` (`ProjectInfo.id: str`)
- Create: `pypsa-gui/backend/tests/test_projects_tenancy.py`
- Modify: `PROJECTS_DIR` usage via `settings.projects_root` / per-project `storage_path`

**Interfaces:**
- Consumes: `project_acl.*`, `storage_paths.storage_path_for`
- Produces: list/create/load/save/scenario/rename/delete honor auth + org paths
- `POST /api/projects/{id_or_name}/scenarios` sets DB `parent_project_id` and disk metadata name pointer for compatibility

- [ ] **Step 1: Failing integration tests with two users**

```python
def test_user_b_cannot_list_user_a_project(client_a, client_b, project_a):
    assert project_a["name"] in {p["name"] for p in client_a.get("/api/projects/").json()}
    assert project_a["name"] not in {p["name"] for p in client_b.get("/api/projects/").json()}

def test_create_scenario_inherits_tree_access(client_member, root_assigned):
    r = client_member.post(f"/api/projects/{root_assigned['id']}/scenarios", json={"name": "S1", "description": "[scenario] x"})
    assert r.status_code == 201
    assert r.json()["parent_project"] == root_assigned["name"]
    assert client_member.get(f"/api/projects/{r.json()['id']}/bundle").status_code in (200, 404)  # whatever export path exists — at least activate/load works
```

Use real endpoints that exist today (`GET /`, `POST /{base}/scenarios`, load/activate). Prefer asserting activate/load 200 for member on child.

- [ ] **Step 2: Add resolver `get_project_for_request(db, org, id_or_name) -> Project`**

- [ ] **Step 3: On create root — insert DB row + create dir at `storage_path_for`**

- [ ] **Step 4: On create scenario — copy bundle as today, insert child row with `parent_project_id`, keep `metadata.json parent_project` name in sync**

- [ ] **Step 5: Gate every mutating/read project route with `ensure_project_access`; delete/rename use `can_delete_project` / manage rules**

- [ ] **Step 6: When auth disabled, keep legacy flat `PROJECTS_DIR / name` behavior (no DB) for backward compat**

- [ ] **Step 7: Tests pass; commit**

```bash
git commit -m "feat(gui): ACL-gate projects router and persist scenario parent ids"
```

---

### Task 8: Advisory project locks

**Files:**
- Create: `pypsa-gui/backend/services/project_locks.py`
- Create: `pypsa-gui/backend/tests/test_project_locks.py`
- Modify: projects router (lock endpoints + activate/load integration)

**Interfaces:**
- `acquire_lock(db, project_id, user_id, ttl_seconds=120) -> ProjectLock | None`
- `heartbeat_lock(db, project_id, user_id) -> bool`
- `release_lock(db, project_id, user_id) -> None`
- `get_lock(db, project_id) -> ProjectLock | None`

- [ ] **Step 1: Failing tests for acquire conflict + heartbeat expiry**

```python
def test_second_user_cannot_acquire(db, project, user_a, user_b):
    assert acquire_lock(db, project.id, user_a.id) is not None
    assert acquire_lock(db, project.id, user_b.id) is None

def test_expired_lock_can_be_stolen(db, project, user_a, user_b, monkeypatch):
    lock = acquire_lock(db, project.id, user_a.id, ttl_seconds=1)
    lock.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert acquire_lock(db, project.id, user_b.id) is not None
```

- [ ] **Step 2: Implement + endpoints**

`POST /api/projects/{id}/lock`, `POST .../lock/heartbeat`, `DELETE .../lock`

Activate/open response includes `{ lock: { holder_email, yours: bool } }` or parallel GET.

- [ ] **Step 3: Logout releases locks held by that session’s user (best-effort all their locks)**

- [ ] **Step 4: Tests pass; commit**

```bash
git commit -m "feat(gui): advisory per-project edit locks"
```

---

### Task 9: Email service + admin email/legacy endpoints

**Files:**
- Create: `pypsa-gui/backend/services/email_service.py`
- Modify: `pypsa-gui/backend/routers/admin.py`
- Create: `pypsa-gui/backend/services/legacy_migrate.py`
- Create: `pypsa-gui/backend/tests/test_legacy_migrate.py`

**Interfaces:**
- `send_email(to, subject, body_text, body_html=None)`
- `claim_legacy_project(db, legacy_name, org_id, owner_id, member_ids, include_descendants=True)`

- [ ] **Step 1: Failing test — claim two legacy folders linked by parent_project reconnects FK**

```python
def test_claim_tree_relinks_parent(db, org, admin, legacy_root_dir):
    # write Root + Child under legacy_unclaimed with metadata parent_project
    result = claim_legacy_project(db, "Root", org.id, admin.id, [], include_descendants=True)
    child = db.query(Project).filter_by(name="Child").one()
    assert child.parent_project_id == result.root.id
```

- [ ] **Step 2: Implement move-from-legacy into `storage_path_for`, insert rows, relink parents by legacy name map**

- [ ] **Step 3: Admin routes for legacy list/claim + email status/test**

- [ ] **Step 4: Tests pass; commit**

```bash
git commit -m "feat(gui): email helper and legacy project claim with tree relink"
```

---

### Task 10: Frontend auth shell + routing

**Files:**
- Create: `pypsa-gui/frontend/src/api/auth.ts`
- Create: `pypsa-gui/frontend/src/auth/AuthProvider.tsx`, `RequireAuth.tsx`, `RequireAdmin.tsx`
- Create: `pypsa-gui/frontend/src/routes.tsx`
- Modify: `pypsa-gui/frontend/src/main.tsx`, `src/api/client.ts`, `src/App.tsx`
- Create: `pypsa-gui/frontend/src/api/auth.test.ts` (or vitest for redirect helper)

**Interfaces:**
- `authApi.login/logout/me/forgot/setPassword/resetPassword`
- axios `withCredentials: true`; on 401 navigate to `/login`

- [ ] **Step 1: Failing vitest for `getPostLoginPath(user, lastProjectId)` resume helper**

```ts
import { describe, expect, it } from 'vitest'
import { getPostLoginPath } from '../auth/resume'

describe('getPostLoginPath', () => {
  it('goes to projects home by default', () => {
    expect(getPostLoginPath(null)).toBe('/projects')
  })
  it('resumes last project when provided', () => {
    expect(getPostLoginPath('uuid-1')).toBe('/app?project=uuid-1')
  })
})
```

- [ ] **Step 2: Implement auth API + provider; wrap routes**

Route map:

```tsx
<Routes>
  <Route path="/login" element={<LoginPage />} />
  <Route path="/set-password" element={<SetPasswordPage />} />
  <Route path="/reset-password" element={<ResetPasswordPage />} />
  <Route element={<RequireAuth />}>
    <Route path="/projects" element={<ProjectsHomePage />} />
    <Route path="/admin/*" element={<RequireAdmin><AdminLayout /></RequireAdmin>} />
    <Route path="/app" element={<App />} />
  </Route>
  <Route path="*" element={<Navigate to="/projects" replace />} />
</Routes>
```

When auth disabled (dev probe via `/api/auth/me` special or vite env `VITE_AUTH_ENABLED`), keep rendering `App` at `/` as today.

- [ ] **Step 3: `client.ts` — `withCredentials: true`; 401 interceptor**

- [ ] **Step 4: Tests pass; commit**

```bash
git commit -m "feat(gui): frontend auth provider and route shell"
```

---

### Task 11: Login + password pages (split brand layout)

**Files:**
- Create: `LoginPage.tsx`, `SetPasswordPage.tsx`, `ResetPasswordPage.tsx`
- Create: minimal CSS module or Tailwind classes matching forest-green split layout from spec

- [ ] **Step 1: Manual/visual check via Story-less page render; add vitest that Login form calls `authApi.login`**

```tsx
// LoginPage.tsx structure
<div className="min-h-screen grid md:grid-cols-2">
  <section className="bg-gradient-to-br from-[#1f3d2e] to-[#3d6b4f] text-white p-10 flex flex-col justify-end">
    <h1 className="text-4xl font-bold">PyPSA Gui</h1>
    <p className="opacity-85 mt-2">Model, solve, and compare energy networks with your team.</p>
  </section>
  <section className="flex items-center justify-center p-8 bg-[#f3f7f2]">
    {/* email, password, Sign in, Forgot password */}
  </section>
</div>
```

- [ ] **Step 2: Wire forgot → API; set/reset password token from query string**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(gui): split-brand login and password token pages"
```

---

### Task 12: Resume-first Projects home

**Files:**
- Create: `ProjectsHomePage.tsx`
- Modify: `projectsApi.list` to support `roots_only=true`
- Modify: `uiStore` last project key to store project UUID

- [ ] **Step 1: Vitest for resume card visibility logic**

```ts
expect(shouldShowResume({ lastId: 'a', accessibleIds: ['a', 'b'] })).toBe(true)
expect(shouldShowResume({ lastId: 'z', accessibleIds: ['a'] })).toBe(false)
```

- [ ] **Step 2: Build page — hero Resume + roots list + New project**

New project calls existing create/save wizard entry (navigate to `/app?mode=new` or open wizard).

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(gui): resume-first projects home"
```

---

### Task 13: Admin console pages

**Files:**
- Create: `AdminLayout.tsx`, `UsersPage.tsx`, `OrgsPage.tsx`, `LegacyMigratePage.tsx`, `EmailSettingsPage.tsx`
- Create: `frontend/src/api/admin.ts`

- [ ] **Step 1: Admin layout left nav routes under `/admin`**

- [ ] **Step 2: Users page — create user (email, role), status, resend set-password**

- [ ] **Step 3: Orgs page — super-admin create/list; org admin read-only own org**

- [ ] **Step 4: Legacy migrate — list unclaimed, claim with owner + include descendants**

- [ ] **Step 5: Email settings — status + test send**

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(gui): admin console for users, orgs, legacy, email"
```

---

### Task 14: Workbench chrome — locks, members, user menu

**Files:**
- Create: `LockBanner.tsx`, `AssignMembersDialog.tsx`, `UserMenu.tsx`
- Modify: `AppHeader.tsx`, `App.tsx`, `ScenariosPanel.tsx` (use `id` when calling APIs; keep name labels)
- Modify: `projectActions.ts` — acquire lock on switch; read-only mode flag in uiStore

**Interfaces:**
- `uiStore.readOnly: boolean`
- On `switchToProject`: `POST lock`; if not acquired, set readOnly true and still activate for viewing
- Heartbeat interval while holding lock
- Assign dialog reads/writes `/api/projects/{id}/members` (server resolves to root)

- [ ] **Step 1: Vitest — readOnly blocks a pure helper used by destructive actions if one exists; else test lock state reducer**

- [ ] **Step 2: Implement banner + dialog + user menu (Back to projects, Logout)**

- [ ] **Step 3: Ensure Scenarios panel create/list still works with UUID routes + name display**

- [ ] **Step 4: Manual check: two browsers, same scenario tree — second opens read-only**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(gui): workbench lock banner, assign members, user menu"
```

---

### Task 15: End-to-end hardening + docs

**Files:**
- Modify: `pypsa-gui/README.md` or `pypsa-gui/docs/` with auth setup
- Modify: `.env.example` under backend
- Add integration test marking auth smoke

- [ ] **Step 1: Write `.env.example`**

```text
PYPSA_GUI_AUTH_ENABLED=true
DATABASE_URL=postgresql+psycopg://pypsa:pypsa@localhost:5432/pypsa_gui
SECRET_KEY=change-me
SMTP_HOST=localhost
SMTP_PORT=1025
PUBLIC_BASE_URL=http://localhost:5173
```

- [ ] **Step 2: Run full backend auth/acl/lock/legacy pytest files + frontend vitest**

```bash
cd pypsa-gui/backend && python -m pytest tests/test_auth_service.py tests/test_auth_api.py tests/test_tenancy_api.py tests/test_project_acl.py tests/test_projects_tenancy.py tests/test_project_locks.py tests/test_legacy_migrate.py -v
cd ../frontend && npm test
```

Expected: all pass.

- [ ] **Step 3: Document bootstrap + compose + Mailpit URL (`http://localhost:8025`)**

- [ ] **Step 4: Commit**

```bash
git commit -m "docs(gui): auth setup guide and env example for multi-user v1"
```

---

## Flexibility checklist (do not regress)

While implementing, keep these seams intact:

1. Routers never parse cookies directly — only `deps.require_user`.
2. All authorization goes through `project_acl` (future viewer/editor / per-node ACL).
3. Membership writes target tree root only, but table stays generic (`project_id`).
4. Paths only via `storage_paths.storage_path_for`.
5. Password login lives only in `auth_service` / `routers/auth.py` (SSO later).
6. UX pages are replaceable without changing ACL contracts.

---

## Self-review vs spec

| Spec requirement | Task |
|---|---|
| Login landing split brand | Task 11 |
| Admin-created users + set-password email | Tasks 5, 9, 11 |
| Forgot password | Tasks 4, 9, 11 |
| Org admin/member roles | Task 5 |
| Project assignment creator/admin | Tasks 6, 14 |
| Whole-tree scenario ACL | Tasks 6, 7 |
| Scenario create/compare/cascade keep working | Task 7, 14 |
| Advisory per-node locks | Task 8, 14 |
| Resume-first projects home | Task 12 |
| Full admin console | Task 13 |
| Legacy unclaimed + tree relink | Task 9 |
| Hybrid Postgres + filesystem | Tasks 1–2, 6–7 |
| `PYPSA_GUI_AUTH_ENABLED` | Tasks 1, 4, 7, 10 |
| Iteration flexibility seams | Global constraints + Flexibility checklist |
