# Multi-User Organization Tenancy Design

**Date:** 2026-07-26  
**Status:** Draft for review  
**Product:** pypsa-gui (FastAPI + React workbench inside PyPSA-Eur)

## 1. Problem

Today `pypsa-gui` is a single-user localhost workbench. Projects live as directories under `backend/projects/` with no authentication. Anyone with access to the deployment sees every project. We need organization-scoped accounts so that after login, users only see and manage projects they are allowed to access, with a clear landing → projects → workbench flow.

## 2. Goals (v1)

- Unauthenticated users see only a branded login landing page.
- Platform super-admin creates organizations; each user belongs to exactly one org.
- Org admins (and super-admin) create user accounts manually (no public self-registration).
- Email + password auth with set-password and forgot-password email links.
- Org roles: **Admin** and **Member**.
- Members collaborate on projects they create or are assigned to.
- Project creator and org admin can assign/unassign members.
- Advisory edit lock: first editor gets write access; others open read-only.
- Projects home is resume-first; otherwise list accessible projects.
- Hybrid storage: Postgres for identity/ACL/metadata/locks; filesystem for large project bundles.
- Existing on-disk projects become a legacy unclaimed pool, claimable via Admin → Legacy migrate.

## 3. Non-goals (v1)

- OAuth / SSO / magic-link login
- Public self-registration or invite links
- Multi-organization membership per user
- Real-time co-editing
- Storing `network.nc` / large blobs primarily in the database
- Billing, quotas, or usage metering
- Redesigning the existing network workbench canvas beyond auth/lock/assignment chrome

## 4. Chosen approach

**Hybrid multi-tenant architecture**

- Keep FastAPI backend and React frontend workbench.
- Add auth, tenancy, ACL, and locks in Postgres.
- Keep project bundles on disk under org-scoped paths (object storage later without changing the product model).
- Put login, projects home, and admin console in front of the existing workbench.

## 5. Architecture

### 5.1 High-level flow

```text
Browser
  ├─ /login | /set-password | /reset-password   (public)
  ├─ /projects                                 (auth)
  ├─ /admin/*                                  (admin / super-admin)
  └─ /app (workbench)                          (auth + project access)

FastAPI
  ├─ auth / session
  ├─ tenancy (orgs, memberships)
  ├─ project_acl + project_locks
  ├─ existing project/network/solver routers (ACL-gated)
  └─ email sender (SMTP)

Postgres          Filesystem
  users             projects/{org_id}/{project_id}/...
  organizations     legacy_unclaimed/{old_name}/...
  memberships
  projects
  project_memberships
  project_locks
  auth_tokens
```

### 5.2 Data model

| Entity | Purpose |
|---|---|
| `User` | email (unique), password hash, status (`invited` \| `active` \| `disabled`), flags |
| `Organization` | name, created_by super-admin, timestamps |
| `OrgMembership` | user_id, org_id, role (`admin` \| `member`); one org per user in v1 |
| `Project` | id (UUID), org_id, name, created_by, storage_path, timestamps, soft metadata |
| `ProjectMembership` | project_id, user_id, assigned_by, assigned_at |
| `ProjectLock` | project_id, holder_user_id, acquired_at, expires_at / heartbeat |
| `AuthToken` | type (`set_password` \| `reset_password`), user_id, hash, expires_at, used_at |
| Platform super-admin | Bootstrap principal that can create orgs and org admins; not an org role |

**Access rule for any project resource**

Allow if authenticated user is in the project’s org AND one of:

1. org role is `admin`, or
2. user is project `created_by`, or
3. user has a `ProjectMembership` row

Otherwise return **404** (do not reveal existence).

**Delete / rename policy (v1):** project creator or org admin only.

### 5.3 Storage layout

```text
projects/
  {org_id}/
    {project_id}/
      network.nc
      user_ts.json
      solver_config.json
      metadata.json
      layout.json
      results_state.pkl
      uploads/
      chat.jsonl
      snapshots/
legacy_unclaimed/
  {legacy_folder_name}/
    ...same bundle shape...
```

Postgres `projects.storage_path` points at the org-scoped directory. Legacy folders are invisible to normal list APIs until claimed.

### 5.4 Concurrency (advisory locks)

- Opening a project for edit attempts lock acquire.
- Success → full edit capabilities in workbench.
- Failure → open read-only; UI shows holder identity and option to retry later.
- Active editor sends heartbeats; lock expires if heartbeat stops (tab crash / network loss).
- Logout, explicit close, or navigating away releases the lock when held by the session user.

### 5.5 Database choice

- **Default:** PostgreSQL for shared/cloud and local-via-Docker.
- Keep the data-access layer swappable so a SQLite local mode can be added later if needed; not required for v1 if Docker Postgres is acceptable.

## 6. Auth & account flows

### 6.1 Login landing

- Split layout: brand panel (left) + sign-in form (right).
- Fields: email, password, Sign in, Forgot password.
- No self-registration CTA.

### 6.2 Admin-created user

1. Super-admin creates organization (if needed).
2. Org admin or super-admin creates user (email, org, role).
3. System emails one-time **set-password** link.
4. User sets password → status `active` → Projects home.

### 6.3 Forgot password

- Always return a generic success message.
- One-time reset link; on success invalidate existing sessions for that user.

### 6.4 Session

- Server-side revocable session stored in Postgres, referenced by an HTTP-only secure cookie. Admin disable immediately invalidates sessions.
- Public endpoints only: login, set-password, reset-password, health.
- Logout clears session and releases locks held by that user/session.

### 6.5 Email

- SMTP required for set-password and forgot-password.
- Local/dev uses a mail catcher (Mailpit/Mailhog).
- Admin console Email settings shows configuration status and test-send.

## 7. UX / UI

### 7.1 Screens

| Screen | Audience | Purpose |
|---|---|---|
| Login landing | Public | Brand + authenticate |
| Set / reset password | Token holder | Establish credentials |
| Projects home | All authenticated users | Resume last project + list accessible projects + New project |
| Workbench | Project-authorized users | Existing modeling UI + lock/assignment chrome |
| Admin console | Org admin / super-admin | Users, Organizations, Legacy migrate, Email settings |

### 7.2 Projects home (resume-first)

- Header: brand, org name, user menu, Admin link (if authorized).
- Hero: “Continue where you left off” when a last project exists and is still accessible; primary Resume CTA.
- Below: searchable list of projects the user can access (created, assigned, or all org projects if admin).
- New project CTA.
- If no resume candidate, hero collapses to the list.

### 7.3 Workbench additions

- User menu (account, back to projects, logout).
- Lock status banner (editing / read-only with holder).
- Assign members dialog for creator and org admin (pick from org members).

### 7.4 Admin console

Left nav:

- **Users** — create user, role, status, resend set-password
- **Organizations** — super-admin creates/lists orgs; org admins see their org
- **Legacy migrate** — list `legacy_unclaimed/`, claim into current org with owner + optional members
- **Email settings** — SMTP status / test

### 7.5 Visual direction (v1 baseline)

- Login: forest green / soft mint brand panel; form on light panel.
- Projects home: darker resume-first composition.
- Workbench: preserve current look to limit scope.
- Avoid purple-gradient and cream-serif AI default aesthetics.

## 8. API surface (v1)

### Auth

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `POST /api/auth/forgot-password`
- `POST /api/auth/reset-password`
- `POST /api/auth/set-password`

### Tenancy / admin

- `POST /api/admin/organizations`
- `GET /api/admin/organizations`
- `POST /api/admin/users`
- `GET /api/admin/users`
- `POST /api/admin/users/{id}/resend-set-password`
- `GET /api/admin/legacy-projects`
- `POST /api/admin/legacy-projects/{name}/claim`
- `GET /api/admin/email/status`
- `POST /api/admin/email/test`

### Projects (ACL-aware replacements/extensions)

- `GET /api/projects` — only accessible projects for current user
- `POST /api/projects` — create in user’s org; creator becomes owner
- Existing load/save/activate/rename/delete/bundle endpoints — enforce ACL; delete/rename limited to creator or org admin
- `GET /api/projects/{id}/members`
- `PUT /api/projects/{id}/members`
- `POST /api/projects/{id}/lock` / `POST .../lock/heartbeat` / `DELETE .../lock`

All project identifiers in URLs should prefer stable UUIDs; display names remain unique per org.

## 9. Frontend routing

- `/login` — public
- `/set-password?token=...` — public
- `/reset-password?token=...` — public
- `/projects` — authenticated home
- `/admin/*` — admin / super-admin
- `/app` — workbench; requires selected/accessible project. The current root workbench mounts here after auth lands.

Unauthenticated access to protected routes redirects to `/login`.  
Authenticated access to `/login` redirects to `/projects`.

## 10. Legacy migration

- On upgrade/startup, detect existing `backend/projects/*` bundles that are not already registered in Postgres.
- Move or mark them under `legacy_unclaimed/` (implementation detail: move preferred to avoid dual listing).
- Admin Legacy migrate UI claims a folder into an org: creates `Project` row, sets storage path, assigns owner/members, removes from unclaimed pool.
- Normal users never see unclaimed legacy projects.

## 11. Components & responsibilities

| Unit | Responsibility |
|---|---|
| `auth` service/router | Credentials, sessions, tokens, password flows |
| `tenancy` service/router | Orgs, org membership, super-admin bootstrap |
| `project_acl` | Project registry, assignment, authorization helpers |
| `project_locks` | Advisory lock lifecycle |
| `email` | SMTP send + templates |
| Existing `projects` router | Bundle IO, activate/resident contexts — call ACL first |
| Frontend auth gate | Session bootstrap, route guards |
| Projects home | Resume + list + create |
| Admin console | User/org/legacy/email management |
| Workbench chrome | Lock banner, assign dialog, user menu |

## 12. Error handling

| Case | Behavior |
|---|---|
| Invalid login | Generic invalid credentials |
| Forgot password for unknown email | Generic success |
| Expired/used token | Clear error + request new link / contact admin |
| Unauthorized project access | 404 |
| Lock held by another user | Open read-only + banner |
| Email not configured | Admin create-user / forgot-password fails with actionable admin error |
| Disabled user | Reject login |

## 13. Testing strategy

- **Backend unit/integration:** password hashing; token lifecycle; org isolation; project ACL matrix; lock acquire/conflict/heartbeat/expiry; legacy claim.
- **Frontend:** route guards; resume-home visibility; admin nav gating; lock banner states.
- **Manual/dev:** Mailpit end-to-end for set-password and reset-password.

## 14. Rollout notes

- Design for shared server / cloud first; keep local viable via Dockerized Postgres + Mailpit.
- Feature can ship behind config flag `PYPSA_GUI_AUTH_ENABLED` during development; production shared deployments require auth on.
- Iterate UX after v1 baseline (login split, resume-first home, full admin console) without changing the tenancy model.

## 15. Open iteration items (post-v1 OK)

- Polish visual system across workbench + home
- SQLite local mode without Docker
- Object storage backend for bundles
- Finer per-project roles (viewer vs editor)
- Multi-org membership / org switcher
- SSO

## 16. Decisions log

| Topic | Decision |
|---|---|
| Tenancy model | Organization workspaces |
| Deployment target | Design for shared/cloud; keep local possible |
| Auth | Email + password |
| Account creation | Admin-created only |
| Org roles | Admin + Member; assigned members collaborate |
| Project assignment | Creator + org admin |
| Storage | Hybrid (Postgres + filesystem bundles) |
| Org creation | Super-admin creates orgs; one org per user |
| Concurrency | Advisory edit lock; others read-only |
| Existing projects | Legacy unclaimed pool + admin migrate |
| First password | Email set-password link |
| Post-login | Projects home; resume last project when possible |
| Forgot password | Self-serve email reset |
| DB preference | Postgres default; keep door open for later SQLite |
| Projects home layout | Resume-first |
| Login layout | Split brand + form |
| Admin UX | Full admin console |
| Approach | Hybrid multi-tenant |
