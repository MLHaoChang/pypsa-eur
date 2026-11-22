<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07d — Model-drafted report revisions as a runner stage (HH-08A, part 2)

Status: done (#1746)
Landed in #1746.
Type: task
Blocked by: 07c
Gated: yes (GK B)
Model tier: opus
Owner decisions: HARNESS-970-1, SH07C-DRAFT-1
Split from: [07c](07c-report-comment-revisions.md), its fifth acceptance criterion (owner decision SH07C-DRAFT-1)

## What to build

07c keeps report comments and addresses them with edits the user writes. This issue lets the model draft that edit.

- The harness drafts the edit as a runner stage it launches itself, on the run's own lease. A route or chat tool never infers a run from a report or charges a run's budget (SH-07, GK B (37) Q1).
- The draft's model call is the run's own send and is reserved through issue 24's `send_reservation`.
- A draft is a proposal bound to the digest of the version it was drafted from. It is never applied on its own. The user confirms it, and confirming goes through 07c's address path: the closed edit set, the invariants and the stale-base refusal.

## Acceptance criteria

- [ ] A model call for a revision draft is reserved through issue 24's `send_reservation`, under the run's live lease, by a stage the harness launched.
- [ ] A refused reservation makes no provider call and leaves the comment open with its reason.
- [ ] A draft is applied only on the user's confirmation, against the digest it was drafted from; a stale one is refused `revision_stale_base`.
- [ ] A confirmed draft passes 07c's invariants, or the comment stays open with their reasons.

## Likely files

`pypsa-gui/backend/harness/` (a draft stage), `pypsa-gui/backend/services/reports/revisions.py` (07c), tests beside them

## Comments

### 2026-11-11: triage

Checked on master: 07c is done (#1300) and `send_reservation` exists (`services/run_monitor.py`, `durable_conversation.py`, `reports/report_job.py`). `reports/revisions.py` has no draft stage. Verdict: ready-for-agent.

- Blocked by: none.
- Reviewer: GK B (plan first). Likely files and acceptance are in the body.

### 2026-11-17: GK B (64) plan verdict, GO with conditions D1–D10

Q1: a request for a run the user names, made under the run's own lease and charged to the run's budget, fits SH-07 and SH07C-DRAFT-1. Q2: drafts live on the run block (`run.revisions.requests`); comments.json is unchanged. Two PRs: PR A is the backend (GK B), PR B is the RunMonitor drafts list (UX).

- D1 Rescan: `scan()` also returns completed runs with a request due. Test: the woken tick gets `leased_elsewhere`, the lease lapses, and the next scan drafts the request.
- D2 Status never changes: the branch sits before tick's idle return, off `_act` and the takeover recovery. Test: a takeover at MAX_RESTARTS drafts and the status stays `completed`; a spent budget refuses with `draft_budget`.
- D3 No project hold. Test: no holder while a draft is in flight.
- D4 One meter: revision rows count toward `budget.cost_eur` and are never also charged by `runner.charge`. Test: spend rises by the settled EUR, exactly.
- D5 Request shape: comment and block fenced as untrusted; no tools, `max_tokens` 4096, a finite input bound, the run's model, a `ReservingProvider` of scope `revision`.
- D6 Confirm and discard: routes only ("out of chat"); drafted → applying under the lock before `address`; 07c's ACL; a non-drafted row gets a typed refusal; a fault never leaves `applying`.
- D7 Limits: requested or drafting blocks a second request; 10 rows in every status.
- D8 Run state: only `completed`; another or unreadable report is `revision_draft_not_run_report`.
- D9 Route checklist, new kinds in a fragment, REASON_TEXT pinned; `lost` only on a lapsed lease or this instance's. The new kinds are route-only and out of chat, so they never reach a tool_error frame; `test_tool_error_kind_manifest` calls a `tool-error-kinds.d/` entry for them stale, so PR A adds none.
- D10 One mutant per guard, listed in the PR body.
