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
