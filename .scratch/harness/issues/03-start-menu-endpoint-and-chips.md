# 03 — `GET /api/chat/workflows` and chips read from it

Status: ready-for-agent
Type: task
Blocked by: 01

Backend: a router endpoint that calls `harness.workflows.menu(context)` where
`context` is `unbound | expert | guided` plus the bound project kind, and
returns `[{id, title, intent, opening_request}]`. Frontend: `ChatPanel`
fetches it with React Query under a `['chat','workflows',ctx]` key and renders
`ChatStarterChips` from the response; a chip sends `opening_request` through
`chatStore.sendRequest` with `label: title`. Delete the three
`CHAT_STARTER_PROMPTS*` arrays and update their tests
(`ChatPanel.hubDesignPanel.test.tsx`, `ProjectsHomePage.assistant.test.tsx`).

Done when: the chips shown in Expert, Guided and unbound match the
registry; a provider switch does not change them; vitest and tsc are clean.
