# Task 14 Report — Workbench chrome: locks, members, user menu

**Status:** Complete. `npm test` (60/60) and `tsc -b` both pass.
**Branch:** `cursor/multi-user-tenancy-design-e4a8`
**Commit:** `692281e5` — feat(gui): workbench lock banner, assign members, user menu (pushed)

## What was built

### Step 1 — Pure lock-state reducer + vitest (node env)
- `utils/lockState.ts` — dependency-free reducer `lockStateFromAcquire(outcome)`
  mapping an acquire/heartbeat outcome to `{ readOnly, holderEmail }`, plus the
  destructive-action guard `canMutate(state)` and a `WRITABLE` default.
- `utils/lockState.test.ts` — 8 tests covering success/refusal/missing-payload
  and the "a refusal never trusts a `yours:true` payload" defence, and the
  `canMutate` gate that blocks mutations while read-only.

### Step 2 — Banner + dialog + user menu
- `components/LockBanner.tsx` — amber read-only strip (auth mode only, hidden
  when writable), rendered in `App.tsx` under `CrashRecoveryBanner`.
- `layout/UserMenu.tsx` — header dropdown: identity, **Assign members…**,
  **Back to projects**, **Sign out**. Both exits release the active lock first.
- `layout/AssignMembersDialog.tsx` — reads/writes `/api/projects/{id}/members`;
  candidate users from the admin list filtered to the actor's org, current
  membership pre-checked; degrades to a read-only member list when the user
  can't enumerate org users (403).

### Wiring
- `api/projects.ts` — `acquireLock` / `heartbeatLock` / `releaseLock` (409 uses
  `skipErrorToast` → banner, not a toast) + `getMembers` / `setMembers`; types
  `ProjectLockInfo`, `ProjectMember`.
- `store/uiStore.ts` — `readOnly`, `lockHolderEmail`, `setLockState()`.
- `utils/projectActions.ts` — `acquireProjectLock` / `releaseProjectLock` /
  `stopLockHeartbeat` + a singleton heartbeat (45 s vs. 120 s backend TTL);
  `switchToProject` releases the outgoing lock and acquires the target's (a 409
  → read-only but still activated for viewing).
- `App.tsx` — mount effect acquires the lock for a reload-restored project and
  stops the heartbeat on unmount.
- `layout/AppHeader.tsx` — Save/Undo/Run disabled + Ctrl+S/Ctrl+Z/enqueue
  guarded when `readOnly`; `UserMenu` mounted (auth only).

### Step 3 — Scenarios panel
`ScenariosPanel` already lists/creates via project names (which the backend
resolves to ids/tree root) and displays names — verified unchanged and working
with the UUID-capable routes; no edit needed.

## Legacy safety
`authEnabled === false` ⇒ `readOnly` stays false, no lock calls, `UserMenu` /
`LockBanner` render nothing — legacy single-user workbench is untouched.

## Concerns / follow-ups
- Non-admin root creators *can* manage membership server-side but can't
  enumerate org users (no non-admin org-user endpoint), so the dialog shows the
  read-only degrade path for them. A dedicated org-members endpoint would close
  this.
- Step 4 (two-browser manual check) not run here — needs a live auth-enabled
  backend + two sessions; logic verified via the reducer unit tests and the
  409→read-only path.
- API calls key on project name (backend resolver accepts name or id); a full
  switch to id-only keys was out of scope and left as-is.

---

# Task 14 Review Fixes — org-member directory, broader read-only gating, id-keyed APIs

**Status:** Complete. Frontend `npm test` (63/63) + `tsc -b` (exit 0) pass;
backend `pytest` for tenancy/auth (27/27) passes.
**Branch:** `cursor/multi-user-tenancy-design-e4a8`

Addresses the three "must fix" findings from the Task 14 review.

## 1 — Member directory for project creators (non-admin)
- **Backend** `GET /api/auth/org-members` (`routers/auth.py`) — available to ANY
  authenticated org member; returns `{id, email, role}` scoped to the caller's
  own organization. Backed by a new `list_org_members(db, actor)` in
  `services/tenancy_service.py` (org-scoped, role-agnostic, no cross-tenant
  leakage; a super-admin without a membership sees an empty directory).
- **Frontend** `authApi.orgMembers()` (`api/auth.ts`, `OrgMember` type).
  `AssignMembersDialog` now sources candidates from this endpoint instead of the
  admin-only `/admin/users`, so a project **creator** (not just an org admin) can
  assign colleagues. Org admins can still use it. The list is already org-scoped
  server-side (dropped the client-side org filter). Save still returns 403 for a
  non-owner/non-admin and surfaces as a toast.

## 2 — Broader read-only gating
- Extracted a shared, dependency-free guard `utils/mutationGuard.ts`
  (`evaluateMutation(readOnly) → {allowed, blockedMessage}`, wrapping the existing
  `canMutate` rule + one canonical `READ_ONLY_MUTATION_MESSAGE`), with unit tests
  in `utils/mutationGuard.test.ts`.
- **AppHeader** — project rename is now gated: the inline editor won't open for a
  locked active project (title reflects read-only), and `commitName` refuses +
  rolls back with the shared toast. (Save/Undo/Run were already gated.)
- **ScenariosPanel** — scenario **branch (create)** and **delete** are gated: the
  Plus/Trash buttons disable with a read-only hint, and the handlers funnel
  through the shared `guardMutation()` so a keyboard/edge path still toasts rather
  than silently failing.

## 3 — ScenariosPanel API ids
- Built a name→id map from the projects list; `apiIdFor(name) = id || name`.
- `createScenario` (base), `delete`, and the `switchToProject` (activate) calls
  now address projects by their UUID when known (falling back to name for legacy
  single-user mode) so UUID-backed routes resolve unambiguously. The UI keeps
  displaying human-friendly names throughout (delete keeps the name for the
  cascade re-prompt; create keeps the base name in the dialog header).

## Tests
- Backend: `test_member_lists_org_members` (member lists own-org members incl.
  role; excludes other-org users) + `test_org_members_requires_authentication`
  (401) in `tests/test_tenancy_api.py`.
- Frontend: `utils/mutationGuard.test.ts` covers the allow/block verdict + shared
  message.
- Commands run: `npm test` → 63 passed; `npx tsc -b` → exit 0;
  `pytest test_tenancy_api.py test_auth_api.py test_auth_service.py` → 27 passed.
