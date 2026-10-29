<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08 — Run monitor and envelope approval in the companion

Status: ready-for-agent
Type: task
Blocked by: 02, 03
Gated: yes (UX reviewer for placement; GK B for the backend payload)
Model tier: opus
Owner decisions: UX-flow (one entry point), SH-ENVELOPE-1, COP-1, DENSITY-1
Absorbs: LC-23 slice 3 (plan card UI)
Overlaps: site-scene SC-07 (`.scratch/site-scene-generation/issues/08-ai-guided-project-flow.md`, needs-triage); not folded without an owner decision
Design: [design.md](../design.md) §10
UX placement: GO, 28 Oct, with the conditions under "UX conditions" below

## What to build

1. Plan-review Choice card: an **Envelope** section (edits, runs, budgets, expiry, email opt-in) with "Approve and run unattended" / "Approve, step by step" / "Change"; narrowing allowed.
2. Plan card (`AssistantResultCard`): stage rows, the current stage expanded (gate rules pass/fail, loop round n/N, budgets used), "Waiting for you" items (cards, research confirmations), Pause and Stop. No new panel, no second progress line, the card-workspace Next row unchanged.
3. The attention notice opens the companion on the run.
4. The last row links to the report in the Reports panel.

## UX conditions (UX reviewer, 28 Oct)

1. Both modes: say how each of the six placement points looks in Guided and in Expert, and where the companion differs. The PR has a screenshot of every state in both modes: proposal, envelope approval, running (gate pass and fail, loop n/N), "Waiting for you", paused, stopped, finished, over budget or expired.
2. The Envelope section is in plain words: components and fields named by their panel's word maps (`FIELD_WORDS` and the like, with a parity test), never raw keys or a {} dump. Budgets carry units and use KPI formatting ("Solves 3 of 10" · "Cost cap €2.00" · "Expires 28 Oct 18:00"). An unset limit is said in words ("no cost cap").
3. The verdict row has exactly three choices. "Approve and run unattended" is disabled, with the reason as visible text, when it cannot work: no email or attention surface, chat off with no API key, or a multi-user limit.
4. Pause and Stop: say what Stop keeps (partial results, the edits applied so far) and whether it can be undone. If Stop reverts edits, it gets the same confirmation card that destructive actions use. A paused or stopped card names the stage it stopped at and how to resume.
5. Off-envelope and destructive calls keep today's Confirmation card. Its Details are closed by default and show no raw tool name or {}. (This is a carried follow-up; don't extend it.)
6. Background run: the app never switches panels on its own when a stage or the run ends. The plan card offers "›" links through `withReturnFrom`. The notification opens the companion on the plan card through `withReturn`, so "Back to …" works.
7. Wording: "›" for links only; "·" between data; a colon (not an em dash) between clauses; freshness is "out of date". Stage names: Brief · Set up · Inputs · Check inputs · Solve · Check results · Explore options · Report · Hand-off.
8. Failure states are in words with a "›" fix: gate failed, budget exhausted, envelope expired, solver errored. No raw exception text on the card.

## Acceptance criteria

- [ ] The UX reviewer checks the build plan against conditions 1–8, the stage names included.
- [ ] Screenshots of every state in Guided and Expert are in the PR.
- [ ] LC-23 SC-03 and SC-16 pass; Guided and Expert show the same plan card.
- [ ] A waiting card is issued when the user opens the companion, with its TTL starting then.
- [ ] Stop revokes the envelope and cancels the task; the card says "Stopped at <stage>" and what was kept, never an internal status such as `cancelled` (the same for every task status).

## Likely files

`pypsa-gui/frontend/src/components/AssistantResultCard.tsx`, `pypsa-gui/frontend/src/components/ChatPanel.tsx` (plan-review card), `pypsa-gui/backend/services/assistant_tasks.py` (view payload), frontend tests

## Comments

### GK B (9) plan verdict, 29 Oct: GO with conditions

1. No oracle: unknown, foreign owner, owner removed from the project (a real membership-row delete) and deleted project each give a byte-identical 404 `task_not_found` on GET and on every POST (present, pause, resume, stop, confirm).
2. Every action rechecks at call time, with nothing cached from view: resume, present and confirm re-run owner, `resolve_project`, `_may_write`, envelope expiry and budget. Tests:
   - revoke after pause, then resume → 404, and nothing dispatches;
   - pause past `expires_at`, then resume → no unattended step. The PR says whether the run falls back to step by step or to needs_user, and tests it.
3. Stop and pause kill the live card. After stop, a confirm with the old token is refused and nothing runs. After pause, the PR says and tests the same or "stays answerable". The captured card is dropped on resolve, stop, pause and expiry. Mutation: not clearing it on stop must fail a test.
4. present refuses a paused or stopped run, and issues at most one live card per run: a second present re-issues and invalidates the first token. Test both.
5. `solves.used` counts only this run's own audit rows: test with a second run in the same project.
6. brief_stale: the red test asserts the dispatch count over N ticks, then the needs_user stop. Mutation: removing the stop must fail.
7. At the trial merge: test_route_chat_disposition, test_project_route_acl_policy, test_write_surface_lock_policy, and assistant_inventory, route_parity and control_matrix `--check`. New error kinds go in this branch's tool-error-kinds.d fragment. Keep the word-map parity test.
8. No envelope lists run_sensitivity_sweep or run_eh_study. The #875 ruling holds: refuse any project argument that is not the run's own.
9. Ruling (A): pause keeps the envelope granted. Resume re-checks item 2, and the monitor shows the expiry still running while paused.

### UX (4) plan verdict, 29 Oct: GO with conditions

- **Stop is (B):** an inline second step, "Stop the run? Stop · Keep running", with "The edits applied so far and the results so far are kept. A stopped run cannot be restarted."

1. Links use "›" (U+203A).
2. Every "›" that opens a panel goes through the mode-aware opener (openFlowTargetInPlace in Guided, openFlowTarget in Expert) with withReturnFrom. A block deep in a panel is reached by request-then-scroll. One screenshot shows a Guided link landing in its Guided home.
3. Reuse the panels' own FIELD_WORDS and component word maps rather than a separate envelope map, and parity-test against them and the backend profiles. Gate facts get plain names with units.
4. Budget used up: the fix is "Change the budget ›" (the Envelope or Change path), not Resume. The PR says what each failure fix does.
5. Paused: also "Unattended approval expires <date time>".
6. Status in words, never by colour alone. No internal status anywhere, Expert Details included. Expert Details may show the task and stage ids; Guided shows neither.
7. Envelope section:
   - the disabled email and unattended choices give their reason as visible text;
   - the steppers have accessible names ("Solves, at most");
   - an unticked edit stays visible as "removed".
8. Screenshots, in Guided and Expert, of every issue state, plus:
   - a disabled unattended reason;
   - the email-disabled reason;
   - a narrowed envelope;
   - the Stop second step;
   - the Activity row and where "Open the run ›" lands.
9. "Open the run ›" is the only run link in Activity.
