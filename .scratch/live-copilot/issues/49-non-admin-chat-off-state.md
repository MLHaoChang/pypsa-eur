<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 49 With no API key, a non-admin's chat panel hangs on "Loading models…" and is offered setup they cannot do

Status: done

Done in #1822 (a68541e6bc).
Blocked by: none
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W4 (shots `member_setup`, `member_app`).
Model: sonnet. Review: UX.

## Problem

With no `ANTHROPIC_API_KEY`, an admin sees an honest off state ("Add an Anthropic API key to talk to me", key field, Send disabled). A workspace member who is not super-admin sees the right text ("Ask an administrator… only a super-admin can set it"). However:
- `GET /api/chat/settings/llm` and `/api/chat/settings/api-key` return 403 for that user (`backend/routers/chat.py` ~253–283, `_require_super_admin`) on every page. The panel then shows "Loading models…" and "Loading answer-check availability…" indefinitely (`components/AssistantModelSettings.tsx`, `components/ChatPanel.tsx`).
- The Guided banner (`pages/hubDesign/shared/AssistantSetupCard.tsx`, "set it up once with an API key") still offers "Set up the assistant", which a member cannot complete.

## User-visible impact

A non-admin sees spinners that never end and a call to action they are not allowed to finish, on every page.

## Acceptance criteria

- [ ] A 403 from the settings routes ends the loading state. The panel shows the "ask an administrator" message, with no model or answer-check rows, and stops retrying.
- [ ] The Guided assistant banner shows the admin-only setup button only to a user who may set the key (the same rule as `_require_super_admin`, read from the session's user, not guessed). Other users get one line: "An administrator can turn the assistant on".
- [ ] Vitests for the admin and member states; no new console errors in either.
- [ ] End to end in the real app, with no key: sign in as a member, open Projects, then a project in Guided and in Expert. No "Loading…" remains after 5 s, and no setup button shows. Sign in as admin: the setup card and key field are unchanged.

## Likely files

`pypsa-gui/frontend/src/components/AssistantModelSettings.tsx`, `pypsa-gui/frontend/src/components/ChatPanel.tsx`, `pypsa-gui/frontend/src/pages/hubDesign/shared/AssistantSetupCard.tsx`.

## Comments
