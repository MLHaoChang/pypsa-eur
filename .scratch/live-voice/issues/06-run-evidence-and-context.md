<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Immutable run evidence and conversational references

Status: ready-for-agent
Type: task

This task is independent of a voice provider and can be implemented before the
voice transport. Read the [spec](../spec.md), harness README and the run-evidence
contract in the [research report](../../../docs/superpowers/assessments/2026-10-10-voice-options-and-tool-workflows.md).

## Scope

- Persist immutable manifests and authorized input/result artifact references
  with each supported solve/study completion path, integrated with the existing
  persistent job records. Capture actual foreground inputs, including unsaved
  changes, and retain failure/interruption status. Atomic publication, bounded
  storage/retention, no arbitrary model-supplied paths; preserve latest-result APIs.
- Add shared `list_project_runs`, `get_run_evidence`, `compare_project_runs`
  tools/services and catalogue/dispatcher/route parity. Bounded pagination and
  software-computed metrics/deltas with units, fingerprints, modes and warnings.
  Old runs without artifacts return typed unavailable, never newest-run detail.
- Carry discussed project/run/baseline/section references in conversation state
  for follow-ups. UI context contains identifiers, not copied numerical truth.
  Revalidate owner/ACL and binding generation at every read/action.
- Make result UI/navigation and exports select the discussed run. Add a typed
  run-result UI event/view if the existing tab selection cannot represent it.
  Do not activate/restore historical inputs merely to read them. New workspace
  tabs are a separate UI extension, not arbitrary browser URL execution.

## Acceptance

Three runs in one project: completed, newer failed and newest running. List,
summarize, drill into one section, compare valid runs and export the selected
run. Assert numerical values, timestamps, units, compatible/incomparable modes,
artifact unavailability and exact UI references. Cover ACL revocation, restart,
duplicate completion, unsaved foreground solve, stale binding, pagination,
retention cleanup and existing latest/scenario regression behavior. These tools
must work from ordinary Claude/OpenAI chat and other shared-harness agents.

## Comments

2026-10-11: First slice — `services/run_evidence.py` stores one immutable
manifest per run under `<project>/run_evidence/<run_id>/` (sections first,
manifest last, SHA-256 re-verified on read, bounded retention) with
`list_project_runs` and `get_run_evidence`; old or pruned runs return typed
`evidence_unavailable`, never newest-run detail. Tests:
`tests/test_run_evidence_list_get.py` (38 passed).

2026-10-11: `compare_project_runs(run_id, baseline_run_id, metrics?,
allow_input_changes?)` — catalogue entry, `DISPATCHERS`, route and control
toolset. Deltas are `run - baseline` computed in software from recorded metrics
with units and `delta_pct` (null for a zero baseline). Comparable only when both
runs completed, share a mode and share an input fingerprint; otherwise
`comparable: false` with `incomparable_reasons` (`run_not_completed`,
`mode_mismatch`/`mode_unknown`, `fingerprint_mismatch`/`fingerprint_unknown`)
and no deltas. `allow_input_changes=true` waives only the fingerprint rule (the
result carries `inputs_differ`); a mode mismatch is never waived. Metrics absent
on one side or recorded in different units land in `unmatched_metrics`, never
estimated. Pruned/corrupt artifacts raise `evidence_unavailable` for that run;
unknown ids raise `run_not_found`. Tests: `tests/test_run_evidence_compare.py`
(23 passed).

2026-10-11: Completion wiring (foreground only) — the `run_simulation` /
`run_ac_pf_stage` handoff in `harness/loop.py` calls
`run_evidence.start_foreground_run` (running marker, `run_id = fg-<hex>` from the
`fg:` job id, `unsaved_inputs` from `dirty_state`), and the detached solver
monitor's new `on_terminal` hook calls `publish_foreground_run` once solver state
is terminal: completed (objective in EUR, solve seconds, AC-PF snapshot counts,
input fingerprint, `summary` section), failed (typed error, no sections) or
aborted (`interrupted`). Best-effort: no project authority means no record and
the solve is unaffected. Tests: `tests/test_run_evidence_publish_foreground.py`
(7 passed). Still open: solve-queue/GridSpine, study and report completion
paths; a monitor cancelled by a session abort before the solve ends leaves a
`running` marker; objective unit is assumed EUR (no per-project currency yet);
fingerprint is computed at completion; conversational run references, result UI
selection/export of the discussed run, restart and stale-binding cases.
