<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Slice 3: run lessons via a dream job

Status: ready-for-agent (blocked by 02, whose UI part 02b is in progress; Opus; GK B plan first)
Blocked by: 02
Type: feature
Model: Opus. Reviewer: GK B.

A durable harness task, modelled on Anthropic's "Dreaming with a dynamic workflow" cookbook (MEMORY-DREAM-1):
- It reviews past chats and runs: an analyzer per transcript, then one organizer.
- It writes proposed lessons, which the user confirms.
- It is budget-capped and provider-agnostic.

## Comments

### 2026-11-11: triage

Checked on master: `services/memory_store.py` exists (02a, #1309); no dream job anywhere in `services` or `harness`. Verdict: ready-for-agent, blocked by 02 (02b merge) and by the durable-task seam named in the plan.

- Blocked by: 02.
- Likely files: a new `harness/` task module for the dream job, `services/memory_store.py`, `services/memory_tools.py`, tests beside them. Reviewer: GK B (plan first).

Acceptance:
- [ ] A durable harness task runs one analyzer per past transcript and run, then one organizer; a fake provider drives it in tests.
- [ ] It writes only proposed lessons; nothing is stored until the user confirms each (COPILOT-1).
- [ ] A token/cost budget cap stops it with a typed reason and keeps the lessons proposed so far.
- [ ] The same code path works with any provider profile (no provider name in the task).
- [ ] A confirmed lesson is a memory item with its source chats and runs.

### 2026-11-11: plan GO (GK B), slices 04a backend and 04b UI + triggers

Seam: its own `memory_dream_jobs` table (migration 0014) and a lease worker in `harness/dream.py`, not an `assistant_tasks` step (no tool calls, no project binding); plan.md §6 and M7. GK B's conditions for 04a:

- D1 Lease ownership on every write: a `lease_owner` token; take-over is a CAS setting owner and lease_until together (rowcount 1). Every later write (renewal, cursor, spent, status, proposals) is conditioned on `id, lease_owner, lease_until > now`; anything else stops the worker without writing. Test: two SQLite connections, A's lease lapses, B takes over, nothing from A lands.
- D2 No duplicate on resume: a batch's proposals, cursor advance and spent commit in one transaction (or proposals are idempotent on job, source, body hash). Test: kill between proposals and cursor, resume, no duplicates.
- D3 Settlement without a next touch: a lapsed job with no live worker is settled at boot and by the worker's timer (re-queued, or failed after 3 restarts in an hour); the boot sweep honours a live lease. Tests: stale running row at boot; restart counter at 3.
- D4 One live job per user, race-safe at the DB (partial unique index or CAS insert). Test: two concurrent POSTs give one 201 and one 409.
- D5 Budget fails closed: per-job and daily remainders checked before every provider call; spend recorded from reported usage in D2's transaction; the most one call can overshoot is stated. Unknown or truncated stop reasons drop that output (counted), never parsed. Dream spend counts to the user's daily cap only. Tests: cap mid-batch, truncated stop, unknown stop.
- D6 Read scope: the user's own sessions and solve jobs only; project access and org membership re-checked before each read; revoked projects skipped and counted; evidence names only items the user can still open. Tests: access revoked between batches; org removed.
- D7 Transcripts are untrusted: fenced; model output only ever creates `proposed` items; nothing is recalled before `transition("confirm")`. Test: a transcript asking to confirm yields only a proposed item and recall returns nothing.
- D8 GET returns only the caller's own latest job; ACL, chat-disposition, write-surface and golden-coverage tests and the three smoke `--check`s pass at the trial merge; each new error kind in its own `tool-error-kinds.d` fragment.
- D9 0014 is the only migration head at the trial merge; downgrade drops the table cleanly.
- D10 Shutdown stops the worker and releases its lease (owner-checked); tests drive the loop entry directly.

04b goes to UX, and to GK B for the triggers (nightly and after-10-sessions both default off, tested; a trigger never starts a second live job).

### 2026-11-11: 04a build notes

- Deferred to 04b: the `memory_analyzer` profile role (04a uses the active profile), the route-parity declarations for `/api/memory/dream` (the check refuses them until the UI calls the routes), and the triggers.
- Budget is in tokens only. The plan names token and cost caps; a cost cap needs per-profile prices, which the app does not hold. Dream spend is tallied per user against `PYPSA_GUI_CHAT_DAILY_TOKEN_CAP`, apart from chat's per-project tally (no per-user chat ledger exists). Both are open for GK B.
- The concurrent-start test (D4) runs on SQLite; CI has no Postgres.
