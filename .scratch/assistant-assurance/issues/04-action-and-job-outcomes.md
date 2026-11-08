<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P3: verified action outcomes and real progress

Status: ready-for-agent
Progress: S1 (blocking foreground-job outcomes, harness/outcomes.py) merged ee141c66; S1b (chat tool-line marks) in review; S2–S5 planned; update_component static inputs done (effects.py)
Type: task
Blocked by: 01, 02; live-voice issue 11 for nonblocking jobs

Read the [spec](../spec.md), [test matrix](../test-plan.md),
[plan](../../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md)
and harness README. Design status/review is in the plan; readiness is subject to
the listed dependencies and reviewed contract, not implementation certification.

## Scope

Map handler results to submitted/running/completed/partial/denied/cancel_requested/cancelled/failed/uncertain. Capture requested versus actual postconditions; unknown effects require reconciliation. Remove generic completion checkmarks for queued/failed payloads. Publish only actual task/job/check transitions and retain ordinary confirmations; no model polls.

## Test-first acceptance

Required case groups: **A3.1–A3.4** in the shared test matrix. Start with a failing
public-seam tracer, implement minimally, run green and relevant regressions.
Use independent numerical fixtures and isolated authorized projects; preserve
provider-neutral handlers, ordinary safety gates and cumulative budgets.
Record every required case as executable/passed/red/pending with a reason.
Opt-in skip or schema reachability is not behavior coverage. No paid APIs are
needed to start these deterministic slices.

## Completion and handoff

Done only when the required behavioral cases and dependencies pass, every
affected public channel uses the same approved contract, and regression/style/
schema/UI checks pass. Append commands, exact red/green counts, policy version,
remaining blockers and next runnable tracer to [handoff](../session-handoff.md).

## Comments

### Proposed reliability revision

P3 also requires A3.5–A3.6: required guards separate from courtesy checks, exact confirmed scope, real effect read-back and ambiguous-operation reconciliation. Coordinate ticket 11; no completion from model-supplied result JSON alone.

See the [reliability contract](../reliability-contract.md). Original approval
does not cover these additions; review/freeze them before implementation.

### 2026-10-11 — shared harness implementation

The shared effect adapter now reserves exact confirmed static-input update contracts before execution, compares actual complete input read-back against a private candidate, checkpoints completion and refuses live/uncertain replay. Read-only reconciliation can settle verified after/before states without executing again. This is only `checked-input-update/1`; queue jobs, other mutations, partial-output maps and distributed project runtime remain pending.

See [implementation status](../implementation-status.md), [contracts](../harness-contracts.md) and [coverage ledger](../coverage-ledger.md). The whole ticket remains open; no independent review verdict is inherited by this extension.

### 2026-11-05 — audit against master ad12e8c79 and the slice plan (GK B (24))

Audit: 35 existing P3/effect tests pass. Tool adapters: write_unvalidated 100,
destructive_unvalidated 50, job_unvalidated 26, checked_input_effect 1
(update_component), navigation_ack 1. In a checked turn every other tool reads
"uncertain"; the backend emitted no running/partial/failed/denied/
cancel_requested/cancelled outcome before S1.

| Case | State at audit | Owner slice |
|------|----------------|-------------|
| A3.1 submitted/running not completed | navigation passed; UI passed for 6 statuses; backend job mapping red | S1 |
| A3.1 successful return over failed/partial not success | durable-task layer passed; tool outcome red | S1 (jobs), S2 (writes) |
| A3.1 unavailable effects uncertain | passed for update_component; red elsewhere | S1, S2 |
| A3.2 denied/expired, lock refusal, checkpoint failure | red (ordinary refusals only) | S4 |
| A3.2 timeout after possible effect, no replay | passed for update_component | S5 (jobs) |
| A3.3 cancel_requested not cancelled; own project/run; unrelated/newer job | red on the backend (UI display only) | S1 |
| A3.4 requested vs actual | passed for update_component; bulk/create/delete/undo/import/export red | S2, S3 |
| A3.5 guards, schema, paths, altered args, stale revision, courtesy, no-progress | guard_revision/args_changed passed for update_component; rest red | S4 (needs owner freeze) |
| A3.6 wrong read-back, extra edits, late worker, model completion | passed (update_component; p3 forged action) | — |
| A3.6 partial writes; duplicate/conflicting job ops | red | S2, S5 (needs owner freeze) |
| A3.6 mismatched artifacts | pending: issue 05 (P4 manifests) | 05 |
| Nonblocking job cases | pending: live-voice issue 11 | live-voice 11 |

GK B (24) verdicts:
- Q1 (a): use representative real handlers per effect class. Every tool left
  write/destructive/job_unvalidated gets a named `_pending` row with its ticket;
  a class reads "covered" only for what its tests check (#918/#920).
- Q2 (a): classify the outcome of every tool_result in ordinary and checked
  turns. The visible change (checkmarks gone in ordinary turns) is UI and needs
  the UX reviewer's OK on the PR that changes the display; tell the UX
  reviewer where it sits before building it.
- Q3 (a): build the A3.5 guards in S4 and record progress in issue 11.
- Q4: the A3.5–A3.6 freeze is the owner's (the original approval was the
  owner's); GK B finds the wording sound and testable, plus reliability-contract
  §4. Owner answer AA04-FREEZE-1 (2026-11-05, ~14:4x CET): "Freeze as worded
  (Recommended)"; A3.5–A3.6 are frozen as worded in test-plan.md rows A3.5/A3.6
  and reliability-contract.md §4. S4/S5 build after S1–S3, each with a plan first.
- S1 GO WITH CHANGES, conditions J1–J8: inputs are the independent read only;
  fail closed per input; identity (id, project, run id, started_at) per
  mismatch; abort cancel_requested→cancelled; failed/partial for run_simulation
  and wait_for_job; update_component outcomes unchanged; pure classifier with a
  table test and a killed mutant per branch; shared files untouched.
- S2–S5: send GK B a short plan before each.

### 2026-11-05 — S1 build (branch `claude/aa04-s1-job-outcomes`)

`harness/outcomes.py`: `note_submission` records the call's own identity when
the loop mints an `fg:` handle (job id, project, context, worker thread, run id
and the run marker's started_at). `read_live` reads the live solver record and
the run's evidence marker; any exception is a read error. `classify` is pure.
The loop adds `_outcome` to run_simulation, run_ac_pf_stage, wait_for_job and
abort_simulation results in ordinary turns. Checked turns cannot run these
tools (CHECKED_TOOLS), so their projection is unchanged. `_outcome`, not
`_assurance`, because `_assurance` on an ordinary result hides task cards and
download links in the frontend. The display stays as on master: the checkmark
change is a separate UI slice (S1b) with the UX reviewer.

AC PF "completed" with fewer converged snapshots than total reads partial;
missing or invalid counts read uncertain. An unsaved scratch network has no run
marker, so its solve never reads completed (no_run_record). wait_for_job on
`ad:`, `rpt:` and queue jobs reads uncertain, reason adapter_pending.

GK B (24) notes for later slice plans (GK B (25) now holds this review):
- S2: a revision that moved during read-back gives uncertain; a requested
  value equal to the old value counts as "changed as requested", not a short
  write (test it).
- S4: the required-guard deny happens before any reservation or checkpoint
  write (test nothing is written); the bounded stop counts identical failing
  calls per turn and per run and refuses with its own kind from a
  tool-error-kinds.d fragment; PRE_DISPATCH_VALIDATORS untouched.
- S5: the conflict check compares normalized args (S4's confirmation
  fingerprint), not raw JSON.

UX (9) GO for S1b (chat tool-line marks), rules:
1. Words from `components/activityWords.ts` ACTIVITY_STATUS_WORDS, lowercased:
   add submitted → "queued", cancel_requested → "stopping", uncertain →
   "outcome unknown" to that map; a test fails when an outcome has no word.
2. "…" only for in-flight (submitted, running, cancel_requested); ✓ only for
   completed; failed "✕" in text-warn; cancelled/partial/uncertain "–" muted;
   glyphs aria-hidden.
3. A stale "(running)" must not stay: update the line from the job's live
   state (preferred) or word it as of that moment ("was running"); say which.
4. Shots of every mark state in Guided and Expert (component-level harness OK, #1053).

### 2026-11-05 — S1b build (branch `claude/aa04-s1b-tool-line-marks`)

`components/toolOutcome.ts` (pure): `_outcome.outcome` → glyph, word, tone
and in-flight flag; an unknown string reads as uncertain. Words come from
ACTIVITY_STATUS_WORDS (submitted → Queued, cancel_requested → Stopping,
uncertain → Outcome unknown, denied → Denied added). ChatPanel writes the
transcript line `<glyph> <tool> (<word>)` for a result with `_outcome` and
renders the glyph aria-hidden in Guided and Expert; the checked-turn
`_assurance` line and every tool without `_outcome` are unchanged.

UX rule 3, both parts: an in-flight line takes the newest later `_outcome`
of the same job id in the transcript (wait_for_job, abort_simulation); with
no later read it reads "running as of 14:02" (Guided: "Running: start a solve,
as of 14:02"). The global /simulation/status is not used: it carries no job
identity. A reloaded transcript has no tool rows (history replays tool uses
as `[tool: name]`), so no stale line comes back. Shots:
`frontend/scripts/tool-outcome-shots.mjs`.

GK B (25) plan conditions U1–U5 (no GK gate on the frontend-only PR) and UX (9)
GO, applied: a job tool's returned result with a missing, malformed or
non-version-1 `_outcome` reads "outcome unknown: the result could not be
read", and the handler's `completed`/`status` are never read (U1); a cut
result's `_outcome` is read strictly from the start of its preview, else
unknown (U2); supersede only forward, same non-null job id, same project
(U3); `adapter_pending` and `no_submission` read unknown with their own words
(U4); the outcome table names all nine (U5). UX: the time gets a date
("4 Nov 14:02") when it is from an earlier day; Guided keeps the glyph.

UX (9) NOT OK @1d2a57e6 fixed: the reason follows a colon (Expert) or sits in
brackets (Guided), with no em dash; both modes say "as of 14:02"; shots in
`.scratch/assistant-assurance/shots/aa04-s1b-tool-outcome-{guided,expert}.png`.

### 2026-11-05 — S2 plan verdicts and build (branch `claude/aa04-s2-write-outcomes`)

GK B (25) S2 plan: GO WITH CHANGES, conditions W1–W7:
- W1 pre-existence: created only if absent before and present after with its
  requested static attrs; deleted only if present before and absent after.
- W2 comparison basis: the bulk path's own coercion (`_coerce_bulk_value`); a
  refused value, an unknown attr or a series gives uncertain, never completed.
- W3 no unseen interleaving: the edit counter noted after the seam's own bump
  must equal the counter at the start and the end of the read-back, else
  uncertain (revision_moved).
- W4/W5 undo: the hash covers what undo restores; the popped entry must be the
  one peeked; otherwise uncertain.
- W6 partial output bounded: counts plus at most 5 paths, no requested values.
- W7 a mutant per classify_write branch; a handler-supplied `_outcome` is dropped.

UX (9) S2 GO, asks: the count on a partly done line ("partly done: 3 of 5
updated"; Guided "Partly done: update components (3 of 5)"); plain Guided
phrases for the five write tools; shots with a partly done line.

Build: `harness/outcomes.py` `note_write` / `read_write` / pure
`classify_write` for bulk_update_components, batch_create_components,
delete_component and batch_delete_components in ordinary turns; loop.py notes
the request after the edit counter's bump and projects `_outcome` beside S1's
job outcome (a `None` result from delete_component gets one too). undo_last
moves to S3 with import/export: all three are snapshot read-backs that share
W4/W5. Shots: `.scratch/assistant-assurance/shots/aa04-s2-tool-outcome-{guided,expert}.png`.

### 2026-11-06 — S2 OKs; S3 plan verdicts and undo_last build (branch `claude/aa04-s3-snapshot-outcomes`)

#1161 (S2) has GK OK and UX OK @fc9732f83a129ee2710840b7ad76d577fdb14f66.

Named open rows (GK B (26) R1):

| Case | Owner | Note |
|------|-------|------|
| A3.6 extra edits by a row write (rows the call did not name) | S4 | S2's read-back covers only the named rows; W3 catches other writers only |
| A3.4 saved/unsaved values | S3 (import/export part) done; saved project files: issue 11 | S3 reads the live network: an import lands as an unsaved draft with no saved copy; an export's file is compared with the live network, which is what it writes. A read-back of the project's saved files (save_project, autosave) is not an issue 04 tool; named for issue 11 (X7) |
| AA04-P8 csv_bundle export: float text precision | `_pending` (X5) | a csv bundle stores floats as text, so the export check allows `CSV_RTOL` = 1e-12 relative; a change smaller than that reads completed (test_a_csv_export_sees_no_change_below_its_float_tolerance) |
| AA04-P1 import_excel | `_pending` (X6) | no independent Excel reader; reads adapter_pending |
| AA04-P2 import_matpower | `_pending` (X6) | no independent MATPOWER reader; reads adapter_pending |
| AA04-P3 import_uploaded_network with format excel or matpower | `_pending` (X6) | as the two rows above |
| AA04-P4 export_excel | `_pending` (X6) | no independent Excel reader; reads adapter_pending |
| AA04-P5 export_matpower | `_pending` (X6) | no independent MATPOWER reader; reads adapter_pending |
| AA04-P6 export whose bytes equal a non-export upload (e.g. a user upload) | `_pending` (X5) | `add_upload` dedups onto that file, which is not agent_export, so an ordinary export reads `export_file_count` (fails safe) |
| AA04-P7 csv export: EH columns refilled on read | `_pending` (X5) | the read-back applies `normalise_eh_columns`, as the import route does; an EH column the export dropped is refilled with defaults and can match the live values |
| AA04-P9 job holds across sessions (N1): every job tool but solve_queue_enqueue | `_pending` (ticket 16) | issue 16 S1 records each hold on a saved project (`assistant_job_holds`) and a new chat or tab of the same process reads it (`tests/assurance_acceptance/test_p3_job_hold_records.py`). Still open (S2): a second replica or a restart, which need the owner-liveness lease; and an unsaved project, whose hold stays on its chat |
| AA04-P10 enqueue_unique drops a different config | `_pending` (ticket 15) | GK B (30) Q3 (a): outside S5. A chat re-enqueue is denied while the store has the project's solve; the HTTP route and the panel still meet the silent drop |
| R3: an outcome on the raise path for the four S2 tools and undo_last | S4 | GK B (26) chose (a): S3 keeps no `_outcome` on raise/timeout. S4 must keep: `failed` only when verified unchanged, never `failed` on a timeout. |

GK B (26) S3 plan: GO WITH CHANGES, conditions X1–X8:
- X1: load the scratch Network with the loader that apply_undo uses, and test that an undo of a real template network (custom columns, time series) reads completed.
- X2: match peek and pop per call, never through a context-global last-popped sha. Test that another session's pop between note and read-back gives uncertain.
- X3: a raise may give `failed` only when nothing changed. A tool_timeout never gives failed; it gives uncertain or no outcome. Test both.
- X4: import extras use a closed allow-list in code (the classes and names the router adds), with a test. Any other extra row reads partial, and counts name the allowed extras.
- X5: an export needs exactly one new agent_export upload, else uncertain. Show completed for nc and csv_bundle on a realistic template. A csv round-trip limit becomes a named `_pending` row, never a silent always-uncertain.
- X6: excel and matpower read adapter_pending, each with a named `_pending` row.
- X7: say whether S3 covers saved/unsaved, or name its owner.
- X8: hold the PyPSA lock only for the read. State the digest's cost in the PR. A frame that cannot be hashed reads not_comparable, with a mutant.

Riders: R2, keep the ctx object and compare with `is`; R3, the raise-path rule covers the S2 write tools too.

UX (9) GO for the undo_last lines: entry_moved → "a different change was undone"; not_restored → "the network does not match its state before that change". Done, failed and no count are fine.

Built so far (undo_last):
- `undo_service` counts pops. `watch_pops` records, in this call's context only, the sha256 of each payload this call pops (X2).
- `harness/outcomes.py` `note_undo` / `read_undo` / pure `classify_undo`. The note holds the peeked entry's sha (W4: payload = netcdf + user_ts + template_meta) and its network digest, loaded with `PyPSAService.import_network_from_netcdf`, the loader apply_undo uses (X1).
- The read-back compares the live `network_digest`, user series and template meta, between two reads of the edit counter.
- A pop count that differs from this call's own pops reads entry_moved.
- The undo re-imports into a new context object, so undo compares the project binding. S2's row writes now keep the ctx and compare with `is` (R2).
- `network_digest` costs about 20 ms on ieee39, eh_datacenter, eh_datacenter_staged and eh_ipp_hybrid (8760 snapshots) (X8).
- Raise and timeout (X3, R3): the loop's error path carries no result dict, so a raising or timed-out write (S2 tools and undo_last) is a tool error with no `_outcome`, never completed or failed. It is tested for bulk_update_components and undo_last, including an orphan that lands after the timeout. Putting an outcome on the error path would change loop.py (held by #1154) and the frontend's error display; GK B (26) answered (a): no outcome on raise/timeout in S3, and R3 moves to S4 (row above). No loop.py error-branch change in S3.
- Tests: `tests/assurance_acceptance/test_p3_snapshot_outcomes.py`, 37 passed. 14 mutants of classify_undo were killed (before X2; the X2 rows were added to the table after).

Next: import/export (X4, X5, X6, X7). Then a PR with a release note and shots.

Import/export build (same branch):
- Tool sets: `IMPORT_TOOLS` (the four imports and import_uploaded_network) and `EXPORT_TOOLS` (the four exports) join `SNAPSHOT_TOOLS`. The loop dumps a pydantic result (ImportSummary) to a dict for a write tool, so the projection has a home; no other loop.py change.
- X4: `note_import` reads the call's own bytes (b64 args, or the upload by file_id with its sha256 re-checked) into a scratch Network with the route's reader steps (netcdf: `import_network_from_netcdf`; csv: safe_extract, `_unescape_formula`, `import_from_csv_folder`, `normalise_eh_columns`). `read_import` reads, under the PyPSA lock between two edit-counter reads, the live rows per class, the extras, the values on the file's rows (`_match`: `pd.testing.assert_frame_equal`, order ignored, exact), and the user series (an import clears them). `classify_import`: completed; partial `rows_missing` (count "n of m imported") or `extra_rows`; uncertain `not_as_file`, `not_comparable`, `revision_moved`, `readback_failed`, `no_submission`. `IMPORT_EXTRAS` is a closed frozenset in code, measured empty: the routes add columns but no rows on ieee39 and the EH templates. A test shows an extra row reads partial, and reads completed with the row named in `counts.allowed_extras` once allow-listed.
- X5: uploads are content-addressed, so a second export of an unchanged network lands on the first file (a dedup hit) and "one NEW upload" read uncertain on an ordinary repeat. `upload_service.watch_uploads` records, per call context as X2's pop watch does, the file_id of each upload this call adds, dedup hits included. The read-back needs exactly one, still listed as agent_export in the project's store, re-checks its sha256, reads it with the same reader, and compares it with the live network (exact for nc, `CSV_RTOL` for csv). Tests: completed for nc and csv on eh_datacenter; a repeat export completed; two files by one call, none, and another session's export into the same store all read `export_file_count`; a file unlike the network reads `not_as_network`.
- X6: import_excel, import_matpower, import_uploaded_network with excel/matpower, export_excel and export_matpower read `uncertain adapter_pending` (tests named `..._pending`). The tool line words it "this file format cannot be checked yet"; a job keeps "this kind of job cannot be checked yet".
- X7: see the A3.4 row above.
- X8: `_match` returns UNCOMPARABLE on a TypeError/ValueError and the outcome reads not_comparable (a mutant test). The PyPSA lock is held only for the live read; the scratch read happens outside it.
- R3: a refused import (an `ic:` bus) and an export with no project are tool errors with no `_outcome`. A watch set by a note whose handler then raises is never unwatched; it is a per-context list, so it is harmless, and S4 owns the raise path.
- Review (mp-code-review): the digest and `_match` now compare snapshot and investment-period weightings and `n.meta` (asset provenance), with mutant tests for import and export; `note_undo` reads the user series under the lock; a parity test pins the frontend FILE_TOOLS and import verbs to the backend sets.
- X8 cost: `_match` 40–55 ms on the four EH templates and 43 ms on eh_ipp_hybrid (8760 snapshots); `network_digest` 20–24 ms.
- Tests: `tests/assurance_acceptance/test_p3_file_outcomes.py` 63 passed; p3 file + snapshot + write 154 passed. Frontend: 2093 passed (src/components), ChatPanel.toolOutcome + toolOutcome 94 passed.

### 2026-11-06 — S3 GK NOT OK @ce6007dd (GK B (26)) and fixes

GK B (26) accepted the X5 reading (a per-call upload record, dedup hits included), X4 with an empty `IMPORT_EXTRAS`, and X7 owned by issue 11. Fixes for E1–E5:
- E1: `_match` compares what a file format carries (`_canon_col` / `_eq_rows`). Dtypes and index names are ignored. Numbers and flags compare as float. None, NaN and, in text columns, "" are one missing value; netcdf stores a missing string as "". A column one side lacks reads as all missing. Tests: export after a `batch_create_components` edit, and export of an in-memory template (the export fixture is parametrised loaded / in_memory), both complete for nc and csv.
- E2 and contract D5: `note_import` records, under the lock, the live digest, the revision, and the file's rows the live network already holds with equal static values. `requested` and `done` count only the rows the import had to change. A live state left unchanged reads completed when it already was the file, else failed; never partial. Tests: a handler that does nothing reads failed; re-importing the live file reads completed with requested 0.
- E3: the read-back's revision must equal the note's at both reads (an import keeps the counter; measured). Test: a bump between handler and read-back reads revision_moved.
- E4: `DERIVED_CLASSES = {"SubNetwork"}` is left out of the digest, rows and match. Tests: export after `determine_network_topology` completes; the digest is equal across two loads after topology.
- E5: M12 (a dedup onto a user upload of the same bytes reads export_file_count), M14 (the digest sees `meta`) and M15 (a tampered stored blob reads readback_failed) each have a killing test, run as mutants.
- Riders: `release_note` stops the note's watch in the loop's error branch (test: a raising export leaves no upload watch); the X3 timeout test is parametrised over bulk_update_components, undo_last and import_network_nc; the `_pending` rows carry ids AA04-P1..P8; the csv tolerance test stands as row AA04-P8. The X2 HTTP-route test is not added; the route pops through the same `undo_service.pop`, so it would test the same seam.


### 2026-11-06 — S3 GK NOT OK @94baa63c (GK B (27)) and fixes

GK B (27) met E1 (it probed: "" vs "x", 2 vs 2.5, None vs 0.0 and "1" vs 1.0 all still differ), E4 and E5, and accepted skipping the X2 HTTP-route rider. Two asks are fixed:
- E3: `note_import` records `_binding()`. A landed import publishes an unbound context (`routers.io._unbind_session`), so `read_import` accepts only the note's binding (the import did not land) or unbound; any other binding reads uncertain `other_context`, whatever the revision. Test: a project switch between the handler and the read-back, with an equal revision.
- E2: `done` counts the needed rows the live network holds equal to the file at the read-back (`_rows_already_equal` ∩ `need`, less the missing rows), not every needed row that is present. Tests: pure need {a, b, l} → done 1; route drops one Load and spoils one Generator → done = requested − 2.
- The format-only `loop.py` edits are reverted. Master's loop.py is already unformatted, so the ruff guard exempts it.

### 2026-11-06 — S4 plan: GK B (27) GO WITH CHANGES (G1–G8)

Plan: a required-guard path (`harness/guards.py`, REQUIRED_GUARDS) separate from PRE_DISPATCH_VALIDATORS; the confirmation binds a normalized-args fingerprint; a `denied` outcome for pre-effect refusals; R3 on the raise path; `extra_edits` partial; a bounded no-progress stop. Answers: (a) 3 per turn and 5 per run, as named constants in `harness/budget.py`; (b) `denied` is its own outcome kind, with UX (10) words, the frontend type and a backend/frontend kind parity test; (c) a fail-closed default, a schema guard for every effectful tool, and path/scope guards where S2/S3 read back.

- G1 Inventory: a test walks the effect-class registry, and every write/destructive tool has a REQUIRED_GUARDS entry. A fake unmapped write tool is denied `required_guard_failed` with no handler call. At the trial merge, run `smoke/assistant_inventory.py --check`, `assistant_control_matrix.py --check` and `assistant_route_parity.py --check`.
- G2 The schema guard breaks no working call: validate against the offered input_schema with only the dispatcher's existing coercion. Before turning it on, run the chat-tool suites plus test_route_chat_disposition, test_project_route_acl_policy and test_write_surface_lock_policy; name every newly refused call in the PR (a real defect or a schema fix). A raising guard is denied, with a test.
- G3 Fingerprint: taken over the args the handler actually receives, after every pre-dispatch rewrite, and compared at that point after approval. Key order and 1 vs 1.0 pass; "" vs "x", 1 vs 1.5, true vs 1, and a missing key vs null with no schema default are denied. Fold "" and None only for schema strings (the `_canon_col` rule).
- G4 `denied` means nothing ran: each deny kind's test spies on reserve, the undo snapshot, note_write, the dirty mark and bump_revision, and checks that the project file bytes are unchanged. A lock refusal is denied only before the dirty mark (an order test).
- G5 R3: `failed` only when a note's read-back compared before and after and found them equal. No note, an uncomparable read or a read error gives uncertain. tool_timeout and tool_not_started are always uncertain (mutant test).
- G6 Extra edits: the committed-path set is a whole-state diff (every class's static rows, the series, n.meta, the weightings, user_ts). Test: a handler that also edits another class's row, or a user series, reads partial `extra_edits`.
- G7 No-progress stop: "failing" means a tool_error or the outcome failed/denied, keyed on the tool plus the G3 fingerprint. The per-run count lives on the session across turns, and a success on the key resets it. The kind goes only in its own tool-error-kinds.d fragment. Run test_tool_error_kind_manifest.py and ChatPanel.manifest.test.tsx at the trial merge.
- G8: one killed mutant per new branch, each test targeting its own guard.
Scope: stay out of harness/runner.py and services/assistant_tasks.py (#1163); PRE_DISPATCH_VALIDATORS untouched.

### 2026-11-06 — S4b plan verdicts and build (branch `claude/aa04-s4b-raise-extra`)

GK B (28) plan verdict, verbatim:

> GK B (28) plan verdict, AA-04 S4b (G4 lock order, G5, G6): GO WITH CONDITIONS H1–H9.
>
> I checked against #1193 c09c2b63: _check_foreign_lock is check-only; note_write runs after the loop's own bump_revision, so "revision equals the note's" is the right test; reserve() and begin_step() run before mark_dirty.
>
> - H1 Lock pre-check placement. Put it right after the G3 fingerprint compare, BEFORE reserve() and assistant_tasks.begin_step(), not merely before mark_dirty. G4 says denied means nothing ran, and the spies fixture must include reserve and begin_step for this case.
> - H2 Lock pre-check identity. _check_foreign_lock returns silently when _ACTING_USER_ID is unset or no project is bound. The pre-check must run under the same acting identity and active context the handler gets. Test it with a real foreign lock (not a monkeypatched _check_foreign_lock): denied, handler not called. Cover the harness-run hold (harness_locks.refuses_active) too: whatever kind it raises is denied with that kind, and the kind is classified in the fragment and the denied-words parity test. A 503 project_lock_unavailable is denied the same way.
> - H3 Order test as planned: a lock that appears only after mark_dirty is never denied, and reads through G5. Keep the handler's own check.
> - H4 G5 row writes as planned (ctx `is` the note's, revision equal at both reads, state map equal → failed; else uncertain with the named reasons). Add one test: a handler that raises having changed nothing reads failed, which shows the loop's own bump does not make it uncertain. Add another: a concurrent bump (another writer) between note and read reads uncertain.
> - H5 Imports on raise: not a plain error. An import mutates the network, so it uses the same whole-state compare: failed only when the state map equals the note's, else uncertain. Exports keep the plain error line as _pending AA04-P9 with its ticket, since an export changes no project state.
> - H6 Timeouts: uncertain tool_timeout / tool_not_started for every write- or destructive-tier call in an ordinary turn, not only WRITE_TOOLS. Name in the PR any effect tool that gets no _outcome on these paths, and why. Never read back. Keep the "forced through the classifier" mutant.
> - H7 State map.
>   - Canonicalise before hashing (the #1175 E1 rule): int vs float, -0.0, dtype, index names, NaN/None/"" to one sentinel. Test on an edited network and on an in-memory one, never only a fixture loaded by the same reader.
>   - A series may be hashed per column (path /cls/name/attr@series) rather than per cell, if the per-cell cost is too high. Report the cost on the EH templates and eh_ipp_hybrid at 8760 either way.
>   - List DERIVED_CLASSES in the PR.
> - H8 One rule, both ways.
>   - extras > 0 with completed/partial → partial extra_edits (done and requested kept).
>   - failed with ANY changed path (named or extra) → uncertain not_as_requested, never partial; your two statements conflict, and this resolves it.
>   - Uncertain stays uncertain.
>   - Include exports in the extras check (same map), so an export that edits the live network reads partial extra_edits.
>   - Table test, and a killed mutant per branch.
> - H9 New kinds and words: each in its own tool-error-kinds.d fragment. Run test_tool_error_kind_manifest and ChatPanel.manifest at the trial merge. If the activity words render something new, UX (11) session_01G7qCumdnJRMrYcDA7MJudB takes a look.
>
> S5: send the plan once S4b merges.

UX (11): GO with changes, all applied but one. extra_edits reads "2 extra changes it was not asked to make", or "3 of 5 updated · 2 extra changes"; lock and timeout words as UX proposed; details on a failed or uncertain tool_error line show in Expert, repr quotes stripped. Not applied: tool_not_started stays "outcome unknown: no answer in time", not "not run", because the dirty mark, revision bump and undo snapshot run before the cancel, so nothing proves the project unchanged (UX told by AA-04 (8)).

Build (AA-04 (7), finished by AA-04 (8)):
- H1/H2: `guards.lock_refusal` runs right after the G3 fingerprint, before reserve and begin_step, under the handler's own identity; tests use a real foreign-user DB lock, the harness-run hold and a 503.
- H4/H5: `outcomes.classify_raised`; imports use the state compare on a raise, exports keep `_pending` AA04-P9.
- H6: every write/destructive-tier call in an ordinary turn reads uncertain on timeout and not_started; no effect tool is left without `_outcome` on those paths. Checked turns get none from the loop (their own projection), guarded by a test.
- H7: canonical whole-state map, series per column (`@series`); DERIVED_CLASSES = {SubNetwork}. Cost 30–37 ms per map on the EH templates and on eh_ipp_hybrid at 8760.
- H8: failed with any changed path reads uncertain not_as_requested; exports are in the extras check.
- H9: no new error_kind (project_locked and project_lock_unavailable were already in tool-error-kinds.json), so no fragment.
- Mutants, 16/16 killed against test_p3_raise_and_extra.py: classify_raised 6, classify_write extras 4, classify_export extras 2, loop lock pre-check and timeout tier 3, guards.lock_refusal 1. The two that first survived (export with an uncomparable map; timeout outcome leaking into checked turns) got their own tests.
- The red EH-flag test was a test bug: Generator has no bool EH column; it now uses Bus (eh_poc/eh_critical).
- Trial merge with master: assistant inventory, control-matrix and route-parity `--check`, test_tool_error_kind_manifest and ChatPanel.manifest all pass.

### 2026-11-06 — S4b merged (#1204); S5 plan: GK B (30) GO with conditions N1–N9

S4b merged as #1204 (0b7677bd). It was reviewed by GK B (29), who killed 10 of 10 mutants including the H7 negative-zero and SubNetwork gaps, and by UX (11). UX's non-blocking follow-up is still open: the lock refusals' Details still name the tool ("so bulk_update_components was not run"); that text is in services/chat_tools._check_foreign_lock.

S5 plan (AA-04 (8)): a job call that ends uncertain in an ordinary, untasked turn opens a hold keyed by project binding + tool, carrying the G3 fingerprint, tool_use_id, any job_id and the worker future.
- The same fingerprint is then denied `operation_pending`; a different one is denied `operation_conflict`.
- The hold clears only on independent evidence that the operation is terminal.
- A repeat the user asks for runs as a new operation once the user approves a card that names the hold.

GK B (30) answers: Q1 (a) key per project binding + tool; Q2 (a) a confirmation card that names the hold; Q3 (a) solve_queue.enqueue_unique silently dropping a different config is out of S5, to be filed as a ticket and cited here.

Conditions, as GK B (30) sent them:
- N1 Cross-session gap. A hold on ChatSession is in-process and per session; a new chat, another tab, a second replica or a restart replays freely (#1070: fail closed means a cross-process mechanism). Either, per job tool, name the persisted record P2 also reads when the session has no hold (the solve-queue store's non-terminal job with the same project and fingerprint; the alive solve thread is per process) and test the new-session case for queue jobs; or, where no persisted record exists (sweeps?), add a `_pending` row in issue 04 for the cross-session replay, with a ticket. Do not claim A3.2/A3.6 covered beyond what this holds.
- N2 abort_simulation is exempt from holds: re-aborting is idempotent, a timed-out abort must never block the next abort, and a hold on a run must never block abort_simulation. Test both.
- N3 Every hold kind lists its clearing evidence. A hold with no job_id and no future (tool_not_started, or a raise read uncertain) clears only on the solve thread or late_receipt evidence, a P4 card, or the end of the session; never on time alone. If tool_not_started really means not started, open no hold for it, and say which with the code path.
- N4 A store or thread check that cannot read counts as "not terminal" (deny), never as free (#1079). Test it with the store read raising.
- N5 An operation_conflict outcome names the held operation and points to wait_for_job, the job status, or abort. Whether the P4 card also covers a different-args call is the builder's choice; say which. If it does, the approval is bound to the held hold id and the new call's fingerprint, both fixed when the card is issued (#1072); test that a card approved for fingerprint F leaves a call with F′ still denied.
- N6 The repeat card runs exactly once; a second identical call after the approved repeat is held by the new hold. Test it.
- N7 Order: the deny comes after the G3 compare and the lock pre-check, before reserve, begin_step and the handler. The handler, the queue enqueue and the solve thread start are spied with 0 calls. A tasked call gets only begin_step's refusal and is not double-denied.
- N8 Drop the DERIVED_CLASSES item: that mutant (now outcomes.py:582) is already killed on master by test_p3_file_outcomes::test_the_digest_is_stable_across_loads_after_topology, checked during #1208.
- N9 Each new error kind goes in its own tool-error-kinds.d fragment. Run test_tool_error_kind_manifest, ChatPanel.manifest.test.tsx and the denied-words parity at the trial merge. Kill one mutant per new branch (pending, conflict, each clearing source, the N4 read-fail, the card binding) and name the killing test for each. Note in issue 04 that run_simulation and run_ac_pf_stage open holds on an uncertain raise only, since they run on the SSE thread and never time out.

GK B: "Send the PR when CI is green."

### 2026-11-06 — S5 build (branch `claude/aa04-s5-conflict`), against GK B (30) N1–N9

`harness/job_holds.py`, called from `loop._dispatch_real_tool_call`. A job call
is a tool of the `execution` or `execution_long_running` tier (28 tools);
abort_simulation (destructive) and wait_for_job (read) are not (N2).

- **Open** (ordinary, untasked turn only; a call `assistant_tasks.governs` is
  left to `begin_step`, N7: the next step with the same args, or a repeat of
  a completed step; a task that cannot be read governs nothing, so the hold
  check still runs): `tool_timeout` on the shared pool, carrying the
  worker future; run_simulation / run_ac_pf_stage on an uncertain raise only
  (a new solve thread alive after it, or the solver state unreadable) since
  they run on the SSE thread and never time out (N9); a returned solve whose
  `_outcome` reads uncertain, carrying its job id. `tool_not_started` opens
  none: `Future.cancel()` succeeded, so the handler can never run (N3,
  loop.py the `started.wait(...) and future.cancel()` branch). A raise of a
  pool tool opens none: the worker returned.
- **Deny** after the G3 compare and the lock check, before reserve,
  begin_step and the handler (N7): `operation_pending` (same fingerprint) or
  `operation_conflict` (other fingerprint), both through `guards.denied`, so
  the result reads denied. The message names the tool and points to
  wait_for_job with the job id, the job status, or the stop tool (N5).
- **Repeat card** (P4): after a same-fingerprint call was told
  `operation_pending`, the next one shows the card with `repeat_card`
  (hold id, fingerprint, opened, opened_at, job id), always the user's own
  card (confirm_only, as a requirement card). Its approval admits the call
  only while that hold is the open one and the fingerprint is the one bound;
  the hold is then dropped and the call runs as a new operation (N5/N6). An
  other-args call never gets a card (builder's choice under N5).
- **Clear** only on evidence (N3): future done (a late result naming a job id
  moves the hold onto that job), the job's status terminal
  (`job_adapters` for fg:/ad:/rpt:, the queue route's read for its UUIDs),
  the solve thread ended, or with no thread known the live solver record
  shows none alive; an approved repeat; the end of the session. Never time.
  Any read that fails keeps the hold (N4).
- **N1**: solve_queue_enqueue with no session hold reads the solve-queue
  store's queued/running rows for the resolved project
  (`solve_job_store.load_by_status(..., strict=True)`; a failed read denies).
  Its args are only the project (`rerun_current` is ignored by its handler),
  so a matching row is the same operation: `operation_pending`, worded as
  "the solve queue already has this project's solve", no card (the queue
  cannot run the project twice). The project lookup in front of it fails
  closed too; only the handler's own refusal (an HTTPException) passes. This
  read also runs inside the session that queued the solve, so a chat
  re-enqueue while the project's solve is queued or running is refused
  instead of being answered with the existing job.
  Every other tool's cross-session replay: AA04-P9 (ticket 16). A3.2 "timeout
  after possible effect, no replay" and A3.6 "duplicate/conflicting job ops"
  are covered for jobs within one session, and across sessions for
  solve_queue_enqueue only.
  GK B (30) U1–U4: an admitted task step of solve_queue_enqueue (the task's
  next step, same tool and args, which begin_step admits:
  `assistant_tasks.admits_next_step`) skips this store read. It relies on the
  handler's own duplicate refusal and begin_step's step record, not the store
  hold. The handler refuses a run's capture with `solve_already_queued`
  (`pypsa-gui/backend/routers/solve_queue.py:203`), and `enqueue_unique` answers a
  plain repeat with the active job (`already_queued`). Session holds still
  apply to that step (T1). The killing tests: widening the skip to "task_id set" is killed by
  `test_a_tasked_session_whose_next_step_is_another_call_still_reads_the_store`;
  dropping it is killed by `test_the_tasks_admitted_next_step_is_left_to_the_handler`
  and master's `test_harness_immutable_solve::test_a_run_solve_is_refused_while_another_solve_of_the_project_is_active`.
- **N8**: the prep commit's `test_the_digest_ignores_sub_network_rows` is
  dropped; master's `test_p3_file_outcomes::test_the_digest_is_stable_across_loads_after_topology`
  already kills that mutant.
- **Q3**: ticket 15 (`enqueue_unique` drops a different config).

Mutants (N9), each run against `tests/assurance_acceptance/test_p3_job_holds.py`, all killed:

| Branch | Mutant | Killing test |
|---|---|---|
| pending | same fingerprint never denied | test_a_timed_out_job_call_denies_the_same_args_as_pending |
| conflict | other fingerprint admitted | test_a_timed_out_job_call_denies_other_args_as_conflict |
| clear: future | a running future reads terminal | test_a_timed_out_job_call_denies_the_same_args_as_pending |
| clear: future raised | a raised worker keeps the hold | test_the_hold_clears_when_the_worker_raises |
| clear: future, no job | a jobless late result keeps the hold | test_the_hold_clears_when_the_worker_ends_with_no_job |
| clear: job status | the job reads terminal unread | test_a_late_result_moves_the_hold_onto_its_job |
| clear: solve thread | the raise's thread reads ended | test_a_solve_raise_that_left_a_new_thread_alive_holds_until_it_ends |
| clear: live solver | no thread known reads idle | test_a_solve_raise_with_an_unreadable_state_holds_until_the_solver_is_idle |
| N4 read fails | a failed read clears | test_a_job_status_read_that_fails_keeps_the_hold |
| N4 store read fails | an unreadable store reads empty | test_a_store_read_that_raises_denies |
| N1 store | no store read | test_a_new_session_is_denied_while_the_store_has_the_projects_solve |
| card: hold id | binding ignores the hold id | test_a_repeat_approval_is_bound_to_its_hold_and_fingerprint |
| card: fingerprint | binding ignores the fingerprint | test_a_repeat_approval_is_bound_to_its_hold_and_fingerprint |
| card: after pending | card on the first repeat | test_a_timed_out_job_call_denies_the_same_args_as_pending |
| raise: new thread | every raise holds | test_a_solve_raise_that_left_a_new_thread_alive_holds_until_it_ends |
| raise: prior thread | the earlier solve's thread counts as new | test_a_solve_raise_with_no_new_thread_opens_no_hold |
| loop: timeout opens | no hold on tool_timeout | test_a_timed_out_job_call_denies_other_args_as_conflict |
| loop: tasked skip (N7) | a governed call also checked | test_a_tasked_call_gets_only_begin_steps_refusal |
| loop: card binding | the approved card not passed to the check | test_after_the_pending_deny_a_repeat_card_names_the_hold_and_runs_once |
| loop: raise opens | no hold on an uncertain solve raise | test_a_solve_raise_through_the_loop_holds_the_next_call |
| confirm: forced card | the repeat follows the tier's policy | test_the_repeat_card_is_forced_even_when_the_tier_auto_approves |
