<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — Server-side runner: advance without a panel, restart, notify

Status: ready-for-agent
Type: task
Blocked by: 01
Gated: yes (GK B)
Model tier: opus
Owner decisions: SH-1, SH-FOLD-1, AI-567b
Absorbs: live-voice 10's resumable task advancement (`.scratch/live-voice/issues/10-cross-domain-experiments.md`, plan step 6; its typed cross-domain adapters stay there)
Design: [design.md](../design.md) §6.6
Plan verdict: GK B GO, 28 Oct; B1 = option b (explicit base context)

## What to build

`harness/runner.py`, started from the `main.py` lifespan after the solve queue:

1. One worker thread, a lease per run (`runner.lease_until`, 30 s heartbeat), one action per tick, woken by job settle (`continuation.follow_job`), card decision, user resume and a 60 s timer.
2. **Explicit context**: resolve the run's project like `services/study/runner.py` (`hydrate_or_adopt`, `mutation_lock` → `solver_state_lock`, contextvars-bound request context). `assistant_tasks._authority` accepts that context with the owner's ACL, re-checked each tick.
3. Deterministic steps through `admit` → `begin_step` → confirmation gate → `DISPATCHERS` → `finish_step`, on the runner-owned session `run:<task_id>` rebuilt from the event log. Without an envelope (03) every card waits for the user: the run records `waiting`, goes `needs_user`, and the card is issued when the user opens the companion (TTL starts at display).
4. Restart recovery (design §6.6): reconcile waiting steps from solve-queue rows (`settle_from_record`), lost jobs `uncertain` (no requeue, SC-09), `uncertain` → `needs_user`; 3 restarts in 1 h → `paused`.
5. Notices: an attention record per state change (`needs_user`, `completed`, `failed`, `paused`); email through `email_service.send_email` when configured and opted in, ≤1 per state change, ≤10 per run, no values or secrets.

## Acceptance criteria

- [ ] With no chat session open, an eh_site run whose cards were pre-clicked (test hook) advances setup → solve → hand-off; the eventlog of `run:<task_id>` holds every call.
- [ ] Kill-and-restart mid-solve: a queue job that finished settles `completed` from its row; a lost job settles `uncertain` and the run is `needs_user`; nothing is requeued.
- [ ] Two runner instances: only the lease holder acts; the other takes over after the lease lapses.
- [ ] The foreground active project is never touched by a run on another project (assert on `get_active_context`).
- [ ] ACL revoked mid-run → the next tick stops with `task_not_found`/refusal and no effect (SC-07, SC-16).
- [ ] An attention record is written on `needs_user` and `completed`; email is sent once when configured (outbox test hook), never when not.
- [ ] `goals.py` behaviour is unchanged (its tests pass).
- [ ] Foreground switch mid-stage: the runner's writes land only in its project, the foreground's only in the foreground (`_user_ts`, dirty state, change log, undo checked).
- [ ] A foreground PUT during a mutating stage gets 409 `project_locked` ("a harness run is editing this project") and no write is lost.
- [ ] `chat_tools._acting()` resolves the run owner inside the runner; each dispatch re-resolves the project by uuid and refuses on a mismatch.
- [ ] Notification text is server-built; no researched value appears in an email.

## Likely files

`pypsa-gui/backend/harness/runner.py` (new), `pypsa-gui/backend/main.py` (lifespan), `pypsa-gui/backend/services/assistant_tasks.py`, `pypsa-gui/backend/harness/continuation.py`, `pypsa-gui/backend/harness/attention.py`, `pypsa-gui/backend/services/email_service.py`, `pypsa-gui/backend/tests/test_harness_runner.py` (new)

## Not in scope

Agent turns (04), envelope admission (03), the UI (08).

## Comments
