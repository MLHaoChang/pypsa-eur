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

## Owner clarification extension — AI guidance (pending delta review)

The owner requires the complete process to be part of the overall project flow with AI guidance. New ai-guided-project-flow.md, SC-07, V61–V72 and cross-cutting requirements in all slices specify that integration. This is a technical specification change beyond the reviewed head above; its independent delta verdict is pending. No previous exact-head review or CI result certifies the extension. Runtime release remains NO-GO pending implementation and evidence.
