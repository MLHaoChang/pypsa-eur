<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 21d — Deliver the L1 data request's notice through `runner._wait_for_user`

Status: needs-triage
Type: task
Blocked by: 21
Gated: yes (GK B)
Model tier: opus
Owner decisions: SH21-SPLIT-1, SH21-ACCEPT-1, CHAT-DATA-SPEC-1
Design: issue 21 §3 ("the notice lists every gap at once ... through `_wait_for_user`") and its scope list (`harness/runner.py`)

## Why

Issue 21 (PR #1100) opens one data request when L1 ends with data gaps:
`run_loops.data_request`, under the task lock in `assistant_tasks._advance_run`,
writes `run.waiting = {kind: "data", ref, items, notice, since}`, and the task
view shows the notice. The spec routes that stop through
`runner._wait_for_user`, but `harness/runner.py` was held by #1088 while #1100
was built, so the wiring was left out (owner decision on 2026-11-04, option 1
under SH21-SPLIT-1). Today the run's `needs_user` change gets the ordinary run
notice (`runner._notify` → `attention.run_notice`), which names the run and its
state only.

## Design question (GK B rules on it in this issue's plan)

`attention.run_notice` writes "the run's id and state only, never its values".
The data request's notice is harness text: one line per item with its label,
unit and the accepted sources, built by `run_intake.request_lines`, never from
model text. It holds no values, but it does name the project's components and
attributes (for example `G1 p_nom`).

May the attention record (and the opt-in email that `runner._email` sends)
carry that notice text, or only a fixed line such as "waits for data (N items)"
with the text left to the task view?

## What to build

1. `runner._wait_for_user` takes the data stop's fields (`items`, `notice`)
   and is the one writer of `run.waiting` for `kind: "data"`, keeping
   `data_request`'s "nothing asked twice" rule and its lock order
   (`assistant_tasks._LOCK` is taken last).
2. `runner._notify` delivers the notice as GK B's ruling allows: either the
   notice text or a fixed line with the item count.
3. Tests: L1 exhaustion with two gaps gives one `needs_user` change, one
   attention record and at most one email, carrying exactly what the ruling
   allows; a repeated wake writes no second record.

## Acceptance criteria

- [ ] The data stop's `run.waiting` is written through `_wait_for_user` (test).
- [ ] The attention record and email carry only what GK B's ruling allows (test).
- [ ] One notice per state change, at most 10 per run, unchanged (test).

## Comments
