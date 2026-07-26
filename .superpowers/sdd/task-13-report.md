# Task 13 Report — Admin console pages

## Status

Implemented the admin console shell and `/admin/*` pages for users, organizations, legacy migration, and email settings, plus the supporting frontend admin API client and the auth-shape update needed for org-admin access.

## Changes

- Added `frontend/src/api/admin.ts` with typed bindings for admin users, organizations, legacy-claim, and email endpoints.
- Replaced the placeholder `/admin` shell with `frontend/src/pages/admin/AdminLayout.tsx` and four routed pages:
  - `UsersPage.tsx`
  - `OrgsPage.tsx`
  - `LegacyMigratePage.tsx`
  - `EmailSettingsPage.tsx`
- Added `frontend/src/pages/admin/helpers.ts` plus `helpers.test.ts` for admin-access and user-filter/sort helper coverage.
- Updated frontend auth typing and gating so org admins can enter the admin console:
  - `frontend/src/api/auth.ts`
  - `frontend/src/auth/AuthProvider.tsx`
  - `frontend/src/pages/ProjectsHomePage.tsx`
  - `frontend/src/routes.tsx`
- Extended backend auth serialization so `/api/auth/login` and `/api/auth/me` include `org_id` and `role`, then added a regression test in `pypsa-gui/backend/tests/test_auth_api.py`.

## Verification

- `cd /workspace/pypsa-gui/frontend && npm test` ✅ (52 tests passed)
- `cd /workspace/pypsa-gui/frontend && npx tsc -b` ✅
- `cd /workspace && pixi run gui-tests tests/test_auth_api.py -q` ✅

## Concerns

- The admin pages are functional and typed, but they currently use straightforward inline tables/forms rather than a richer reusable admin component library; that keeps scope tight for Task 13 but leaves room for future UX polish.
