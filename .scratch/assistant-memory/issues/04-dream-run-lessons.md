<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Slice 3: run lessons via a dream job

Status: done
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
- [x] A durable harness task runs one analyzer per past transcript and run, then one organizer; a fake provider drives it in tests.
- [x] It writes only proposed lessons; nothing is stored until the user confirms each (COPILOT-1).
- [x] A token/cost budget cap stops it with a typed reason and keeps the lessons proposed so far.
- [x] The same code path works with any provider profile (no provider name in the task).
- [x] A confirmed lesson is a memory item with its source chats and runs.

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

### 2026-11-11: GK B (47) rulings on #1389

All four 04a departures are accepted:
- No tool-error-kinds fragment for the dream kinds.
- Dream spend is capped on its own: per user, against `PYPSA_GUI_CHAT_DAILY_TOKEN_CAP`, not added to chat's per-project tally.
- The budget is tokens only; there is no cost cap, since the app holds no per-profile prices.
- The D4 race test runs on SQLite only.

Minors left as they are: `spent_today` keys on the job's `created_at`, so a job that runs past midnight counts to the day it was queued. A name-keyed chat project is re-resolved by name in the user's current org.

### 2026-11-11: GK B (48) plan conditions on 04b

- T1 Two racing scans make one job: a per-user unique `trigger_key` on the job, `nightly:<Berlin date>` or `sessions:<latest job id | opt-in time>`, not only a read before `start`.
- T2 Berlin time through zoneinfo, with tests on both DST days. A worker down for the whole 03:00 hour misses that night and catches nothing up; the settings help text says so.
- T3 A trigger runs as the toggle's owner. At fire time it re-checks that the user is active and still a member of the org the job binds to, and never falls back to another org.
- T4 The sessions scan counts by an index, not by re-reading every header. A scan error skips that user only and is logged. Tested with a malformed header.
- T5 "Latest job" is any job of the user's. A manual job resets the count too. A failed triggered job waits for the next 10 chats.
- T6 PUT accepts only the two booleans. GET with no row returns both false. Another user's row is never read or written: tested both ways.
- T7 An unknown role is a 422 and a non-super-admin cannot set one. A marked profile that cannot run fails the job by name; only "unset" or "deleted" falls back to the active profile. The job records its model.
- T8 0015 is the only head, and downgrade drops what it added.

### 2026-11-11: 04b build notes

- UI: `components/memory/DreamReview.tsx` heads the Proposals tab:
  - a "Review my recent work" button with a `role="status"` line;
  - polling every 3 s while a job is live, and every 30 s otherwise so that automatic jobs show up;
  - the proposals list refreshes when a job ends;
  - under it, "Automatic reviews (your own setting)": two checkboxes, both off, a line that names the model reviews go to, and the skipped-night note.
- Opt-in: table `memory_dream_settings` (migration 0015), routes `GET/PUT /api/memory/dream/settings`, the caller's own row only. No row means off. The row records the caller's org at opt-in (T3); `after_sessions_since` keeps turning it on from firing on old chats.
- Triggers: `dream.fire_triggers` runs in the worker's scan (`Worker.run_once`).
  - Nightly fires on the first scan in [03:00, 04:00) Europe/Berlin wall time (`nightly_key`), once per Berlin date.
  - Sessions fires at 10 of the user's own chat logs created after their latest job of any trigger or outcome, or after the opt-in.
  - Both go through `dream.start(org_id=, trigger_key=)`. A live job refuses them (`dream_job_live`, D4); a used key refuses them (`dream_trigger_taken`, T1).
- T3: `org_memberships.user_id` is unique, so a user has one org, and the trigger binds to the one recorded at opt-in.
  - A user moved to another org is skipped.
  - A job whose `org_id` is no longer the user's fails `memory_no_org` before any read.
  - The change log (#1286) is untouched: the dream job writes `proposed` items through `memory_store`, which binds them to `org_of(user)`, the same org.
- T4: `new_sessions` makes one `stat` per log. A log last written before the mark was created before it and is not opened, so only logs written since the mark have their header read. A per-user stored count was not needed at this cost.
- `memory_analyzer` role:
  - It is a top-level `roles` map in `llm-profiles.json`, so a built-in profile can carry it too. It is set by `POST /api/chat/settings/llm/roles` (super admin; unknown role 422, unknown profile 404) and picked in Assistant model settings ("Memory reviews use", default "The active model (<name>)").
  - `llm_config.resolve_role` falls back to the active profile only when the role is unset or its profile was deleted. A marked profile on disk that does not load fails the job with `dream_analyzer_unavailable`; one without a key fails with the wiring's own kind.
  - The job records `analyzer_model`.
- Route-parity fragment `claude-am-04b-dream-ui-triggers.json` (ui-pointer, MEMORY-DREAM-1; the role route as assistant-self-config).
