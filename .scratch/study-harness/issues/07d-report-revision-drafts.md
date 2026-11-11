<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07d — Model-drafted report revisions as a runner stage (HH-08A, part 2)

Status: ready-for-agent (Opus; GK B plan first)
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
