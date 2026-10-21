<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Proposed records, lifecycle and end-to-end journeys

These are design records, not existing API endpoints or catalogue tool declarations. Names must be reconciled with native contracts before any implementation. Extend existing owners only when needed; do not create a parallel planner/approval/evidence store.

## Shared source and action contract

A proposed record carries a schema version, owner/project identifiers, source references (owned ID, full content/input digest, relevant run/job ID, parser/calculation version), selected card/sub-options, request identity and inspected revision. A model cannot supply authoritative ownership, access, readiness or verification status. Native code derives those on every read/admission.

Every proposed operation identifies an existing catalogue tool/native adapter and exact typed arguments, a declared source and target, source/target digests, expected native validation/readback and effect category. Tool names in free text are never executable. Unknown adapter, undeclared change, schema mismatch or obsolete preview fails closed. A source-link click does not grant cross-project access.

The UI proposal lifecycle is **draft → needs clarification / ready for review → inspected → submitted / rejected / invalidated**. This is presentation of a proposal, not a new task state machine. Native task/job/experiment states remain authoritative, including uncertain, infeasible, failed, interrupted and partial outcomes. A proposal marked submitted can still have zero or partial effects; success requires exact native receipts and evidence. No UI lifecycle revives an old approval.

## Capability-specific records

| Record | Required information | Explicitly excluded authority |
|---|---|---|
| Study brief | Objective + units; hard constraints versus preferences; source base case; horizon/stages; selected supported cards; known assumptions/gaps; proposed ordered phases and evidence deliverables | No blanket approval, implicit scope activation, project binding switch or unlimited solve budget |
| Decision view | Decision criterion; admitted base/compare sources; declared changes; feasibility; basis compatibility; native metric/fact IDs; sensitivity method/ranges; existing inline source/status limits; qualified conclusion or no preference | No LLM-computed ranking/probability/NPV, invented causal proof or compliance approval |
| Input mapping | Owned full-hash source; sheet/cell/row or admitted page-region; original value/unit; native target; converted value/basis; repair notes; unresolved ambiguities; exact proposed operations and readback | No executable file instructions, guessed missing values, rights clearance or generic rollback guarantee |

Bound payloads by existing native limits. Result explanation admits at most 12 facts. Suggested scenarios stay within LC-17's four-case/two-change proposal limit. A study task stays within the current 12-step task bound until the bounded LC-05 increase lands, existing experiment admission and narrower engine-specific caps. Every write/run has its own card click, and each card boundary pauses on the plan card (COP-1); reads continue normally. Use file/source references rather than dumping an upload into task arguments. None of these limits expands authorization.

## Example journeys and negative controls

| User group / task | Natural workflow | Expected insight / benefit | Boundary to demonstrate |
|---|---|---|---|
| C&I: choose storage for peak cost | Import measured profile → Capacity/dispatch → Commercial bill/investment → supported native study comparison | Less data preparation; incremental battery value and sensitivity rather than broad savings claims | Unbillable tariff component remains incomplete; no grid-service revenue invented |
| Data centre: plan growth behind weak connection | Source demand and stage assumptions → Capacity → selected Grid/Reliability → optional supported Control screen → Hand-off | One reviewed study, visible cost/service trade-offs and missing dynamic validation | Millisecond load response belongs to its native control/smoothing assessment; hourly dispatch or adequacy alone cannot prove it |
| IPP: compare contract tenor and merchant exposure | Capacity/dispatch → Commercial contracts/investment → scenario evidence | Which supported contract case changes value under stated price assumptions | No automatic Control selection, guessed PPA price, financial-swap engine or hedge execution |
| H2: serve process and offtake | Explicit carrier/unit basis → Capacity/process if supported → selected Reliability/Commercial | Show cost versus unmet product/service and source assumptions | Hydrogen mass/energy conversion needs declared LHV/HHV; unsupported process/dynamic detail remains unavailable |
| Ammonia: staged production | Admitted staged template → Capacity/process → optional production-aware Reliability → Commercial | Trace build timing to product target and constraints | Template status and production-aware reliability adapter checked fresh; no assumption that a pending template is merged |
| Mining: critical loads and restart | Import asset/demand sources → Capacity → Reliability → optional supported Control | Identify service boundary, energy cost and restart evidence gap | Restart transients cannot be inferred from annual ENS; no new mining template claimed here |
| Island microgrid: meet critical service | Native island assumptions → Capacity → Reliability → selected Grid/Control → Hand-off | Compare feasible asset choices and identify missing frequency/dynamics proof | No optimizer dispatch treated as validated real-time island control; no hardware activation |

The same user can omit cards. An unselected domain is Outside scope and cannot block except for dependencies actually read by an admitted run. Unknown combinations are refused by native support; the assistant cannot override them. Examples are acceptance journeys, not assertions that every combination/template/tool is currently implemented.

## Overview and interaction requirements

Keep the cross-card brief on the existing plan card: goal, source, selected cards, current phase, next decision and missing evidence. Preserve the single companion and current conversation; the action fills the composer and the user sends it. Use the existing Activity/task controls for status, review and cancellation. Source-navigation success requires the actual native acknowledgment where that adapter exists; a model saying “opened” is not UI completion. Keep keyboard focus, screen-reader labels, escape/close behavior, narrow-screen usability and reduced-motion support. Facts and gates cannot depend on an animation or color alone.

Follow one Save/header and one Export/chooser per SAVE-1. A Hand-off packet contains source IDs, assumptions, admitted comparison/calculation evidence, outside-scope and not-established sections. Existing report availability controls what can be exported. A new packet cannot silently restore inputs or certify stale historical evidence.
