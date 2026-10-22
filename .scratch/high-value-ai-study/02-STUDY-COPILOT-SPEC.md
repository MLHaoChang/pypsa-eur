<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# HV-S: goal-to-study copilot

Status: proposed; runtime integration not implemented by this PR.
Owning work: W9/W10, LC-05/LC-12/LC-19, S5/S6/S7 where applicable. Native support is decided by W0 and the catalogue, not this document.

## User need and experience

“Compare ways to serve this campus through 2035 while keeping critical load supplied” should become an inspectable study, not an opaque sequence of chat commands. The user reviews the objective, planning periods, loads, constraints, selected cards, assumptions, intended cases and expected deliverables before starting any work.

Use the existing card walkthrough/companion entry. A cross-card brief (objective, cards, cases and steps) is shown as the existing plan card under AI-567b; no second Start assistant button, separate progress surface or Manage area. Native readiness reasons appear before model prose. In Guided, show only relevant steps and an Advanced-for-this-step route; Expert edits the same fields and returns to the same brief. An imported profile or asset edited in one view updates the same native source in both.

The study summary shows: objective; current project; exact source/base case; chosen periods; selected supported cards; next required decision; work planned/running/completed/blocked; evidence still missing. The existing plan card and Activity view show progress; no additional progress line is created. An informational “ready” chip is never an execution permission.

## Functional requirements

1. **Draft brief, do not run.** Capture target quantity and units, horizon and investment periods, demand/service boundary, allowed assets, objectives versus hard constraints, commercial basis, selected card/sub-option scope, user ranges and exclusions. Missing essential targets are questions, not inferred defaults. Template assumptions retain their source and assumption status. Planning horizons do not imply millisecond dynamics support.
2. **Compile only supported work.** Re-read server support, project access, live bindings and native readiness. Describe unavailable control/dynamics/standalone-flow combinations precisely. Do not enable an unsupported card or promise grid-code certification. An admissible ordered plan uses existing ordinary tools, their exact schemas, supported prior-step references and native caps. The current 12-step cap remains authoritative until the LC-05 finite-bound increase lands. Merged LC-23 fixes the brief limit at 24 steps/32,000 serialized characters (plain plans stay 12/16,000), with explicit tests, rather than hiding extra actions, nested task controls or an internal execution loop.
3. **Review native edits.** User sees base case/compare case and declared changes with old/new values, units, provenance, impact on existing runs and intended downstream studies. Reuse existing field/edit and experiment preview. Preview has zero native writes, solver submissions or sends. Editing the brief invalidates the proposed plan and any confirmation it changes.
4. **Execute through the harness.** Every action uses `dispatch_tool_call`, fresh ACL/restriction/lock checks, normal confirmation rules and durable native effect receipts. For this copilot, every write and run requires its own card click (including Expert); reads need no extra stop. At every card boundary the existing plan card pauses with “Continue to <card>”. Accepting a study brief or a boundary cannot preapprove subsequent writes/runs. A material new assumption/constraint/scope requires review. Existing per-engine and session limits apply; estimated resource needs can be unknown and must be shown as such.
5. **Verify before advancing.** Apply only the inspected change, read it back natively, link exact input digest and downstream job/run origin. A worker's terminal event is not enough: the expected native evidence must match the job and source. Current versus retained results are explicit; historical discussion never restores live inputs. Unsupported/missing chapters remain not established.
6. **Handle failure honestly.** Native logs and typed reasons support diagnosis. Offer a draft correction, never an automatic changed-model retry. Failed, infeasible, partial, uncertain, cancelled or stale work cannot count as study success. A lost-effect receipt blocks dependent steps; use existing explicit reconciliation where supported, never a replacement run with the same inputs as proof.
7. **Resume without replay.** Reopen the existing durable conversation and task under current access. Preserve completed effect receipts and unresolved states; do not silently replay them or reuse old approvals. Task cancellation stops future work; a running native job is stopped only by its actual supported cancellation path and confirmation. Never label cancellation quiescent while a worker remains live.
8. **Close with bounded deliverables.** Show completed decisions, exact run sources, selected assumptions, constraints not assessed and next optional studies. Use existing report evidence/chapter availability. General network reporting needs LC-21; absent adapters yield a source-linked gap rather than a fabricated universal report.

## Native acceptance boundaries

`start_task` currently accepts 1–12 **ordered** ordinary tools. It is not a workflow graph engine or project-cloning scheduler. An arbitrary owner goal is not directly executable. Cross-project/Scenario work must use existing admitted scenario/experiment adapters; project-rebinding tools cannot be inserted into a task to work around admission.

Current experiment execution admits bounded supported network cases, including its established two-case path. Commercial, adequacy, AC, control and process work each need their own exact native adapter; schema domain names do not grant execution. Respect both plan caps and narrower execution caps. Never reinterpret the LC-17 max-four suggestion limit as execution authority.

The current `resolve_task_step` control accepts a caller-supplied result after explicit ordinary confirmation. Its completed state is not independent evidence that a solve or write succeeded. The copilot must re-read the matching native effect/job/evidence before using that state for a verified completion claim; it cannot promote a user/model assertion into a native fact. If no such proof exists, show Needs review.

No new task system, provider committee, approval service, background daemon or model-based success detector. Existing continuation handles one owned native origin at a time; aggregation requires an explicit native contract and tests before claiming combined completion.

## Slices and demonstration

- HV-S1 (W9/W10): read-only brief and support/readiness mapping. Demo unsupported goals and a normal supported setup; no writes or paid provider required.
- HV-S2 (LC-05/S7): reviewed brief → existing task/experiment tools; one supported study path first, approval/denial, readback, stale source and uncertain failure demonstrated.
- HV-S3 (LC-12/LC-19/LC-21): durable resume, failure explanation and source-bound deliverable. Broader card/run adapters remain disabled until separately validated.

Use deterministic fake providers for native/effect acceptance tests, then opt-in evaluated live language behavior in a later authorized calibration exercise. Tests S01–S16, as amended by [OWNER-DISPOSITION.md](OWNER-DISPOSITION.md), are normative in the implementation matrix. Do not treat a polished chat transcript as execution evidence.
