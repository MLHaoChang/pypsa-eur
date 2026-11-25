<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 A signed-in user cannot change their password or see their account

Status: done (merged #1972; skipped: editable display name needs a users.name migration, owner call)
Owner: AO03-PAGE-1.
Blocked by: none
Source: gap review 2026-11-21, walk W4.
Model: sonnet. Review: GK B + UX.

## Problem

The avatar menu holds only Memory and Sign out. `/settings` and `/account` fall through to `/projects`. There is no change-password, no display name and no list of the workspaces a user belongs to. The only way to change a password is the reset flow (which 01 makes reachable).

## Question for the owner

1. **(Recommended) A small Account page**: name, email (read only), change password (current + new), and the workspaces with the user's role in each. Reached from the avatar menu.
2. Change password only, as a dialog from the avatar menu.
3. Not now: the reset flow from 01 is enough.

## Acceptance criteria (for option 1)

- [ ] `/account` renders for any signed-in user. Changing the password needs the current one, ends the user's other sessions, and is tested for a wrong current password and a weak new one.
- [ ] End to end in the real app: change the password, sign out, and sign in with the new one. The old one is refused.

## Likely files

new `pypsa-gui/frontend/src/pages/AccountPage.tsx`, the avatar menu in the app shell, `pypsa-gui/backend/routers/auth.py`.

## Comments
