<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 Onboarding a colleague when the server cannot send email

Status: needs-triage
Blocked by: 01
Source: gap review 2026-11-21, walk W4.
Model: sonnet. Review: GK B (security) + UX.

## Problem

With no SMTP configured (the default for a desktop or self-hosted install), an invite creates the user and a set-password token. Nothing delivers the link, and the admin cannot see it. After 01 the admin is at least told the email failed, but the invitee still cannot get in.

## User-visible impact

Sharing a project needs a second account, so on an install without SMTP no project can be shared with anyone new.

## Question for the owner

How should an admin hand over the link when no email goes out?
1. **(Recommended) Show a one-time copyable link to the super-admin who sent the invite**, only when `email_sent` is false. The link expires as the token does, is shown once, and is never logged. `routers/admin.py` returns it only in that response. The admin pastes it into their own channel.
2. The admin sets a temporary password, which the user must change at first sign-in. Simple, but the admin knows the password.
3. No change: SMTP is required for multi-user installs, and Email settings says so before an invite is sent.

## Acceptance criteria (for option 1)

- [ ] `POST /api/admin/users` with `email_sent: false` returns `set_password_url` to a super-admin caller only. Tests: another caller, a sent email and the logs never carry it.
- [ ] Users page shows the link once with a Copy button and its expiry.
- [ ] End to end in the real app with no SMTP: invite, copy the link, open it in a fresh browser context, set a password, sign in, and open a project shared with the new user.

## Likely files

`pypsa-gui/backend/routers/admin.py` (~180–243), `pypsa-gui/backend/services/auth_service.py`, `pypsa-gui/frontend/src/pages/admin/UsersPage.tsx`.

## Comments
