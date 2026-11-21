<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 Invite and password links reach their pages, and the invite result is honest

Status: ready-for-agent
Blocked by: none
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W4 (shots `invite`, `setpw`).
Design: `.scratch/project-workflow/workspace-reconciliation.md:364` (`/login`, `/set-password`, `/reset-password` are public).
Model: sonnet. Review: GK B (auth gate) + UX.

## Problem

1. Without a session, `/set-password?token=…` and `/reset-password?token=…` serve the static login page ("Welcome back"), not `SetPasswordPage`. `backend/static_gate.py` `decide_route` returns `LOGIN` for every unauthenticated path except `/login.html`, and `frontend/vite.auth-gate.ts` (`decideGateRoute`) does the same. The token itself is valid: `POST /api/auth/set-password` accepts it.
2. The static login page (`frontend/index.html` / `login.html`) has no "Forgot password" link. Only the React `LoginPage` has one, and it is never served to a signed-out user.
3. Admin › Users › Invite user returns 201 with `email_sent: false` and a warning when no SMTP is set. `pages/admin/UsersPage.tsx` (~84–96) ignores both and shows the toast "Invitation sent to …".

## User-visible impact

An invited colleague clicks the link and lands on a login page for an account that has no password. A user who forgot their password has no way to reset it. The admin is told an invitation was sent when it was not, so onboarding fails silently.

## Acceptance criteria

- [ ] Signed out, `/set-password` and `/reset-password` (with or without a token) serve the SPA page that handles them, in both the backend static gate and the Vite gate. `/projects`, `/app` and the other app routes still serve the login page (tests on `decide_route` and `decideGateRoute`, kept in step).
- [ ] An expired or used token shows a plain message with a way back to sign in, never a blank page.
- [ ] The signed-out login page offers "Forgot password?", which leads to the reset request.
- [ ] Invite with `email_sent: false` shows a warning toast that says the email was not sent and why (the backend's warning text), not "Invitation sent". Invite with `email_sent: true` keeps today's toast.
- [ ] End to end in the real app with no SMTP: an admin invites `x@example.com` and sees the not-sent warning. The token, taken from the test fixture or the DB as the test harness does, opens `/set-password` signed out. The invitee sets a password, signs in, and sees Projects. Then sign out › Forgot password reaches the reset request form.

## Likely files

`pypsa-gui/backend/static_gate.py`, `pypsa-gui/frontend/vite.auth-gate.ts`, `pypsa-gui/frontend/index.html` / `login.html`, `pypsa-gui/frontend/src/pages/admin/UsersPage.tsx`, their tests (`backend/tests/test_static_gate*.py`, the vite gate test).

## Comments
