<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Independent specification review and GO / NO-GO record

Date: 2026-10-24
Reviewed technical head: `7cb8daac79ea86725cab184be9b8b246194eec60`
Master baseline: `aba252d2384f55116bbd15516a5b025951ba265e`
Scope: planning documents, contracts, seven implementation/prototype tickets and 60 proposed validation cases. There is no new runtime implementation to certify.

The owner requested independent review against existing gaps, corrections and publication to Git. Two read-only review agents independently checked the requirements/feasibility and repository standards axes using the current source. Neither authored the specifications. This is an advisory review, separate from the repository's official engine gatekeeper and UX approval process.

## Decision by scope

| Scope | Spec axis | Standards axis | Required next evidence |
|---|---|---|---|
| Publish these reviewed specs and handoff | GO | GO | Documentation integrity checks and ordinary PR checks. |
| Begin SC-00 Native prototype / contract validation | GO | GO | Execute N1–N3, freeze native public contracts and measured limits; optional engines may be absent. |
| Subsequent native implementation | Conditional GO | Conditional on normal gates | Accepted Native milestone, test-first slices and applicable engine/UX plan review. |
| Enable or release new native runtime features now | NO-GO | NO-GO | Actual implementation, numerical/geometry/access/recovery tests, current-head checks and device evidence. |
| Enable or release Blender/photo capability now | NO-GO | NO-GO | Independent genuine-engine milestones, supported hardware/resources, exchange/reconstruction/device evidence and normal approvals. |

GO here means proceed with the specified next step. It does not mean a passing reconstruction, construction-grade accuracy, completed application integration, full domain coverage or official GK/UX approval.

## Spec

Initial review at `8f49c179ad9a8a24f327b3676d34536705ba0d3a` found four issues; delta review at `f6623a1b3ddca5cf65f260f7b443ab8d54bbb026` added one export clarification. All five are closed in the reviewed technical head:

| Finding | Correction | Planned validation |
|---|---|---|
| New workflow still implicitly depended on Bus/branch-first generation | Explicit coordinate/boundary-only Site with empty network/buses=[], no dummy model components; new path replaces the old client readiness gate | V53, V56 |
| Legacy placements and new scene transforms could independently overwrite one another | One canonical immutable scene generation/pointer; compatibility GET projection; one CAS/locked writer; reject old debounce/unload/fallback writes | V54, V59 |
| Private photos could enter recursive project copies/exports | Private original store outside project directories, keyed by native owner/Project identity; explicit scenario/snapshot/export/restore/delete/expiry rules | V45, V55 |
| Optional engine evidence could block all native work | Independently accepted Native/Blender/Photo milestones; native and manual-layout use do not require Blender/CUDA | V58 |
| Existing recursive sites/ copy did not provide pointed-generation-only export | Required manifest-closure builder, hash/ownership/terms checks, destination reference rewriting, marked excluded artifacts and missing-required failure | V59, V60 |

Final Spec review: all original findings and the export clarification closed, no new blocking inconsistency. Native-model authority, process units/units, explicit result bindings and independent optional milestones remain intact.

**Final unresolved Spec findings: 0.** GO publication and Native prototype; subsequent native implementation conditional on accepted evidence/review; runtime enablement remains NO-GO until actual validation.

## Standards

Initial review found three documented contract/convention gaps, all closed:

| Finding | Correction | Planned validation |
|---|---|---|
| Missing explicit server /flow authority for action availability | Server-only scene verdicts; no frontend worker/platform capability table; fresh submit/Apply rechecks; electrical run_ready unchanged | V56 |
| Implementation ticket filenames began at 00 | Numbered 01–07 per local tracker conventions; internal SC-00 scope ID retained | Documentation path/sequence checks |
| Missing exact serialization for unavailable numbers | value=null, available=false and reason versus resolved zero/true; ADR-0001 contract | V57 |

Final Standards review: no hard violations unresolved and no blocking heuristic judgments. Tool choices separate reuse, optional integration and exploration. Proposed tests and baseline evidence are not represented as completed runtime verification.

**Final unresolved Standards findings: 0.** GO publication and bounded Native prototype; NO-GO runtime release before actual implementation and its gates.

## Evidence and limits

- The reviewed technical diff is `git diff aba252d2384f55116bbd15516a5b025951ba265e...7cb8daac79ea86725cab184be9b8b246194eec60`.
- Publication-record changes after this technical head only add this verdict, documentation validation evidence and handoff/index status. They are not a new runtime implementation or a claim of a new official exact-head approval.
- Local checks: all relative Markdown links resolve, all seven ticket filenames follow 01–07, V01–V60 are unique, explicit documentation SPDX headers exist, and git diff --check passes. No executable test was added by this docs-only package.
- The preceding assessment's 45 files / 544 passing existing 3D tests are baseline evidence only; they are not tests of these new capabilities. The 60 cases here remain proposed.
- No Blender/photo executable was installed or run, no photos/layouts reconstructed, no paid model/media calls, external reviewer contact, deployment or merge occurred.

Summary: Spec 0 unresolved (five findings corrected); Standards 0 unresolved (three findings corrected). First implementation task is the independent Native milestone in issues/01-prototype-and-contracts.md.

## Owner clarification extension — AI guidance (review complete)

Reviewed technical head: `5aa25514696274390c597e425bec21d1f8f79f96`.
Scope: delta after publication head a129323cbffc3691757bdcec1e9e98cafcc4a63d; complete package now has eight tickets and 72 proposed acceptance cases.

The owner requires the complete process to be part of the overall project flow with AI guidance. ai-guided-project-flow.md, SC-07, V61–V72 and cross-cutting requirements in all slices specify the existing ten-section mapping, six-card scope, bounded current context, confirmed actions, native receipts/read-back, interruption recovery, evidence-grounded explanation and manual fallback. Guidance develops alongside native slices rather than waiting for optional engines.

Both independent agents reviewed the technical delta and confirmed the exact committed head above. Spec: zero unresolved findings. Standards: zero unresolved findings. A nonblocking Standards clarification is resolved: preserve the sixth Hand-off card, its Reports/Data exports/Specialist packet outputs and the three-sub-option limit under owner G1. The design explicitly reuses existing start_task/study-brief execution, step approvals, card-phase pauses, limits and uncertain-effect handling; it does not introduce a second executor or widen runnable engineering cases.

**Advisory GO:** publish the revised specifications and begin the bounded SC-00 N prototype/contract validation, including the guidance contract. Subsequent implementation remains conditional on accepted prototype evidence and normal gates. **NO-GO:** runtime release or native/Blender/photo enablement until implementation and applicable validation pass. No official GK/UX approval is claimed. Scripted-provider tests do not prove conversational quality; a real-provider quality claim requires separately authorized evaluation.

Documentation checks pass at the reviewed technical head: all 17 package Markdown files have SPDX headers, relative links resolve, eight tickets follow 01–08, V01–V72 are unique and diff checks pass. The 72 cases remain proposed; no new runtime tests, model calls or worker reconstruction were performed. Later publication-record edits only record this verdict and update README/HANDOFF status, not the technical design. Earlier CI on a129323 does not certify the new head.

## Optional coordinate-first workflow clarification — review complete

Reviewed technical head: `eb2f8f64f9b7402764c559fafd1034228165c4a0`.
Scope: delta after e508bcd5506f9d89f79d728fd773ecec6bb27027;19package Markdown files, eight implementation/prototype tickets and90proposed acceptance cases.

The owner's detailed workflow requires optional entry at native site coordinates, campus mapping before generation, review/correction/explicit regeneration, buildings-only save/resume, later associations to configured native2D/SLD targets and energy connections, and playback at actual native cadence. optional-site-workflow.md is normative; workflow-assessment.md maps source-grounded gaps to the existing slices. V73–V90 pin the new acceptance conditions. No new card, mandatory3D study dependency, duplicate topology model, dummy Bus or default paid engine is introduced.

Both read-only advisory agents confirmed this exact technical head. **Spec:0unresolved findings. Standards:0unresolved findings.** One Spec finding was corrected: PoC is resolved from existing Bus.eh_poc, role/config import/export Links and separate Transformer terminals, preserving multiple/ambiguous relations; no standalone universal PoC class is invented. V82 and SC-02 cover it. Partial result playback retains substantiated layers while missing quantities remain unavailable.

The Standards review reports no hard violations and no actionable heuristic findings. The clarification retains server /flow authority, existing owning editors and return paths, six-card optional scope, native settings/ACL contract freeze, legacy origin compatibility and normal effect/privacy/recovery contracts.

**Advisory GO:** publish this design and begin the bounded SC-00 N prototype/contract validation. Subsequent implementation remains conditional on accepted prototype evidence and applicable engine/UX plan gates. **NO-GO:** release or enable new native/Blender/photo runtime features before actual implementation and required validation. No official GK/UX approval, construction accuracy, broad domain certification or completed conversational quality evaluation is claimed.

Local documentation checks pass: all19Markdown files have SPDX headers, relative links resolve, tickets retain01–08, V01–V90 are unique and diff checks pass. These90cases are planned, not executed tests. No runtime code, dependency, paid model/media call, Blender/photo worker, reconstruction or new runtime test was introduced/run. Later publication-record edits update this verdict and README/HANDOFF status only.

## Interactive copilot configuration clarification — delta review pending

New example intent/effect contracts and V91–V98 make guided creation and iterative site/model/display adjustment explicit. The98-case extension is technical specification work beyond the recorded90-case head; earlier verdicts do not certify it. Delta review pending; runtime release remains gated on implementation and evidence.
