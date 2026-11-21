<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 35 Opening a project sometimes never activates it and leaves "Unnamed Network"

Status: ready-for-agent
Blocked by: none (21, now done, sped the open up; this race remains)
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W4 (shot `member_app`).
Model: sonnet (diagnose first: `diagnosing-bugs` skill). Review: UX.

## Problem

Opening a project from Projects (`/app?project=<id>`) failed 3 times out of about 9 for a workspace member. No `POST …/activate` fired, the URL kept `?project=`, and the screen stayed on "Unnamed Network / No project open" with no error. The other opens worked. A likely cause is the `?project=` resume effect in `frontend/src/App.tsx` (~393–428: the `resumeProjectRef` / `currentProject` check) racing `switchToProject` (`frontend/src/utils/projectActions.ts` ~446). Other walkers were using the same backend at the time, so the trigger may be load-dependent.

## User-visible impact

The user clicks a project and sees an empty app with no message. The only remedy is a reload.

## Acceptance criteria

- [ ] A reproduction that fails on demand (for example a vitest that delays `/api/auth/me` or the projects list), written first and seen failing.
- [ ] The fix makes the resume effect start `activate` exactly once per `?project=` value, whatever order the auth, project-list and store updates arrive in. A failed activate shows the error with a Retry, never "Unnamed Network".
- [ ] End to end in the real app: a Playwright loop opens a shared project 20 times as a member (fresh page each time, with 200 ms latency injected into `/api/auth/me` through route interception). It reaches the project name every time.

## Likely files

`pypsa-gui/frontend/src/App.tsx`, `pypsa-gui/frontend/src/utils/projectActions.ts`, their tests.

## Comments
