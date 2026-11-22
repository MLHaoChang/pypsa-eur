<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Slice 3: Finish/fail notifications

Status: ready-for-agent
Type: task
Blocked by: 02b (stacked on PR #1630)

Spec: [../spec.md](../spec.md) §3; UX plan GO conditions 3, 6, 7, 10.

## What

- Backend: each queue listing row carries `started_by_me`, a per-caller
  capability like `can_dismiss`; the owner id never leaves the server.
- `hooks/useRunNotifications.ts`: a notice per run of mine that ends, in the
  tab that claims it first (`localStorage` `run-notified:<job id>`); the first
  listing after mount feeds only the "N runs finished while you were away"
  summary (`run-last-seen:<user id>`, 24 h at most).
- `components/runStatus/RunNotices.tsx`: the notices, as one toast in the app's
  toaster: three at most, the rest behind "(N more) Show all".
  - finished: "Campus study finished: Project X · 6 min" + Open results; goes
    after 8 s or once opened;
  - failed: "Campus study failed: Project X" + See why (Guided in place,
    Expert the Solve queue); stays;
  - interrupted: "Run stopped when the app restarted: Project X" + "Run again:
    Campus study" (requeue, double-click guard, "Starting…", refusals in plain
    words); stays;
  - aborted: none.
  Failed and interrupted notices go when their project runs again.
- Browser notification while the tab is hidden, permission granted and the
  setting on: the toast's words only. Asked only from the running line's
  "Notify me when it finishes" link; the PREFERENCES footer's "Browser
  notifications" toggle shares its state.

## Comments

GK B3 plan GO (2026-11-18 00:53 CET), conditions:

- N1 One `_started_by(owner, user)` helper behind `can_dismiss` and
  `started_by_me`, read from the popped `owners` map; NULL owner is false.
- N2 Tests: owner true / colleague false (live and persisted), NULL owner
  false, foreign redacted row false, no `_owner` or user id in any row; a
  mutant without the owner check is killed.
- N3 "Run again" names the route's refusal in plain words (project_locked names
  the holder; not requeueable; 404); never silent or read as success. Check
  the viewer refusal once #1622 lands, else hide the button for viewers.
- N4 `run-last-seen` per user; the away summary claims what it counts; last
  seen is written after the summary. First poll summary once, second nothing.
- N5 Jobs already ended before mount get no per-job notice.
- N6 The OS notification carries only the project name and outcome.
- N7 Mutants on the started_by_me filter, claim set-if-absent, the
  hidden+granted+setting gate, ask-only-on-click, the 24 h cap and the
  double-click guard; kill list in the PR body.
- N8 Release-note fragment; route parity / `test_route_chat_disposition` checked.

UX 2 (3) plan GO (2026-11-18 00:52 CET), conditions:

1. Link and footer toggle share one state; the link goes once the user chose
   either way; the footer is then the one place to change it.
2. "Shows the project name in the notification" only while off; when on, the
   muted line says what it shows; after a denial the footer says "Blocked in
   this browser: allow notifications in the browser's site settings", the
   toggle is disabled and the link hidden.
3. Toasts `role="status"`, no focus moves, reduced motion honoured; failed and
   interrupted stay with a named close control and clear when their condition
   ends; a finished toast that opened results goes.
4. Three at most, the rest behind "(N more) Show all" (▸/▾, no red); the away
   summary stays until shown or dismissed and counts only runs that ended
   while no tab watched.
5. "Run again: <name>" shows "Starting…" while it starts; Open results and See
   why go through `withReturnFrom` / `openSectionInGuided` (Guided) and
   `openFlowTarget` (Expert); nothing jumps by itself.
6. No run ids, engine names or raw error text in a toast or the OS
   notification; "Campus study failed: <project>"; "6 min" durations.
