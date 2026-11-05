<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 21d — Deliver the L1 data request's notice through `runner._wait_for_user`

Status: ready-for-agent
Type: task
Blocked by: #1127 (holds `harness/runner.py`; issue 21 has merged, #1100)
Gated: yes (GK B)
Model tier: opus
Owner decisions: SH21-SPLIT-1, SH21-ACCEPT-1, CHAT-DATA-SPEC-1, FILL-11-6, SH21D-NOTICE-1
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

- [x] The data stop's `run.waiting` is written through `_wait_for_user`'s writer, `runner.set_waiting` (GK B plan step 1; test).
- [x] The attention record and email carry only what GK B's ruling allows (test).
- [x] One notice per state change, at most 10 per run, unchanged (test).
- [x] `data_request`'s "nothing asked twice" rule holds: a repeated wake on
      the same open request writes no second `run.waiting`, attention record
      or email (test).
- [x] The lock order is unchanged: the data stop is written inside the
      existing task-lock update in `_advance_run`, never by a nested
      `_update` (test or code-review note).
- [x] "At most one email" counts `_notify`'s run email only; `loop_notice`'s
      own email for the same L1 ending (issue 25) is unchanged and is
      asserted separately (test).

## Comments

- 2026-11-05, triage (owner decision FILL-11-6). Status needs-info: code
  starts after the owner answers the design question (sent through the
  coordinator) and GK B gives a plan GO.

  **What is on master (6e1d5f963).** `run_loops.data_request`
  (`harness/run_loops.py:210`) writes `run.waiting` itself, under the task
  lock of `_advance_run`, with an ISO `since`; `Runner._wait_for_user`
  (`harness/runner.py:654`) writes `{kind, ref, since}` with the runner's
  float clock and sets `status, reason = needs_user, waiting_<kind>`.
  `_notify` (`runner.py:233`) sends `attention.run_notice` (id and state
  only; the attention record has no text field, `question=None`) and
  `_email`, whose body is fixed words, project name, state and link.
  `loop_notice.send_if_due` (issue 25) runs first in `_notify` and, for an
  opted-in run, already emails the failing rules with their facts and the
  ledger rows (component, name, attr, old -> new) for the same L1 ending.
  The data items' labels are `"<component name> <attr>"` from the envelope's
  gaps or the profile's param ids (`run_answers.data_items`), so a label can
  carry a name that came from imported project data.

  **Plan.**
  1. Split `_wait_for_user` into a pure mutator, `_set_waiting(run, kind,
     ref, since, **fields)` (status, reason, waiting dict, `items`/`notice`
     for `data`), and the method that wraps it in `_update`. `data_request`
     calls the mutator inside its existing locked update, so the lock order
     stays (`assistant_tasks._LOCK` last) and nothing nests an `_update`.
     The "same ref already waiting" check moves into the mutator, so a
     repeated wake is a no-op for both callers. `since` keeps one format;
     `assistant_tasks.py:807` passes it through to the view, so the builder
     checks the frontend reader before choosing the float clock.
  2. `_notify` reads `run.waiting` when the new status is `needs_user` and
     `waiting.kind == "data"`, and delivers what the owner's ruling allows
     (options below). Every string that leaves the app goes through
     `runner._plain` with a length bound, and the item list is capped
     (for example, 10 lines, then "and N more").
  3. Tests in `tests/test_harness_run_loops.py` (or a new
     `test_harness_data_notice.py` if #1127 still holds that file): L1
     exhaustion with two gaps gives one `needs_user` change, one attention
     record and at most one `_notify` email (fake sender) carrying exactly
     the allowed content; a second wake writes nothing; a label holding
     markup or a newline is flattened by `_plain`; the existing 10-per-run
     cap still holds.

  **Open design question (to the owner via the coordinator).** May the
  attention record and the email carry the notice text, or only the run id,
  state and item count?
  - (a) Count only, both channels (recommended): "waits for data (N items)".
    It keeps `run_notice`'s and `_email`'s "fixed fields only" contract, and
    no attention-schema change. No imported name reaches a mail client from
    this path. UX: the user opens the link to see what to give; for an
    opted-in run, issue 25's loop email already names the failing rules.
  - (b) Email carries the item lines (label, param, unit, accepted sources;
    `_plain`-bounded, capped); the attention record stays count-only. The
    user sees what to give without opening the app. Project component names
    leave the app by mail, as issue 25's email already does for this ending,
    and `_email`'s docstring and the 21 D4 note need rewording.
  - (c) Both carry the text. The attention record grows a text field
    (`attention._validate`, projection, frontend render), so the change is
    bigger and needs UX review; it duplicates the task view.
- 2026-11-05 (~07:35 CET), owner (SH21D-NOTICE-1), via the coordinator:
  "Count only (Recommended)". Both the attention record and the opt-in email
  say "waits for data (N items)"; the notice text stays in the task view;
  `run_notice` and `_email` keep the fixed-fields-only contract. Plan step 2
  sends exactly that line and the item count; the acceptance test asserts
  that no label, param or notice line appears in the record or the email.
- 2026-11-05, GK B (23), plan verdict: GO WITH CHANGES. Code starts after
  #1127 merges.
  - Count in the attention record too (SH21D-NOTICE-1 covers both).
    `native_status` stays `needs_user`. One optional integer field on
    `family: 'run'` records (for example `data_items`), set only when
    `waiting.kind == "data"` and absent otherwise, so other records stay
    byte-equal. `_validate` refuses a bool, a non-int, a value < 1, or the
    field on another family. `project_records` shows the fixed line "The
    harness run waits for data (N items); open it to see them." Test: an old
    record without the field reads exactly as on master.
  - D1. One `since` format: list every reader of `run.waiting.since` (the
    view at `assistant_tasks.py:807`, the frontend, any TTL or age
    arithmetic), pick the format they all handle, and test the served view
    for both waiting kinds, typed from the served dict (standing rule 16).
  - D2. One dedupe rule: the mutator's same-ref no-op and `data_request`'s
    "nothing asked twice" use the same `ref`. A mutant that drops the no-op
    is killed by the repeated-wake test.
  - D3. Count only: the count is `len(items)` as the harness computed it. A
    mutant that puts the notice or a label in the record or email is killed
    by the no-label test.
  - D4. Lock order: the mutator runs inside `_advance_run`'s existing update,
    with no nested `_update`. The wrapper is its only other caller. A test
    shows the data stop writes `run.waiting` through the mutator.
  - The 10-email cap and `loop_notice`'s separate assertion stay as planned.
    Send GK B the PR head when ready.
- 2026-11-05, build (branch `claude/sh-21d-data-notice`, after #1127).
  `runner.set_waiting` is the one `run.waiting` writer; `data_request` calls
  it inside `_advance_run`'s locked update with `reason=False`, so the data
  stop stays a failed stage with no `run.reason` and the run monitor reads it
  as on master. `since` is epoch seconds for every kind (no frontend reader).
  Run attention records gain `data_items` (needs_user only); `_notify` and
  `_email` send the count only. Tests: `tests/test_harness_data_notice.py`;
  mutants for D2 (drop the same-ref no-op) and D3 (notice in the email) fail
  it. `loop_notice`'s own email is unchanged (`test_harness_loop_notice.py`).
  Self-review (mp-code-review): the "at most one email" box stays open. No
  test drives a real L1 ending where `loop_notice` and `_notify` both send.
  `test_harness_loop_notice.py` is unchanged and still passes. D2's no-op is
  killed at unit level only: the data path dedupes by item key in
  `run_answers.request` before `set_waiting`, so a repeated data wake never
  reaches the no-op. Old run files keep an ISO `since`; the view passes it
  through, and nothing reads it.
- 2026-11-05, GK B (23) NOT OK @1053607 asked for the combined-ending test.
  It is now `test_harness_loop_notice.py::test_an_exhausted_l1_with_gaps_sends_one_count_email_and_the_loop_email_unchanged`:
  a real L1 `exhausted` ending (issue 25's fixture) with two data gaps, one
  with a hostile label, sends exactly one `_notify` email with the count line
  and exactly one `loop_notice` email. Neither names a label, a param or a
  notice line. The loop email equals the body `loop_notice` builds from the
  same record without the request. A mutant that puts the notice in the
  email fails it.

