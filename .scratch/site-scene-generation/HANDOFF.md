<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Continuation handoff

Status: SC-00 N native prototype and proposed contract closures in review ([sc-00-n/record.md](sc-00-n/record.md)); SC-01 onward, B and P not started
Date: 2026-10-24
Branch/worktree: `claude/site-scene-generation-spec`, `/workspace/site-scene-spec`
Baseline: `9988f9899a3bf8e80f51272529f18ea70599b90c`

## Implementer entry and current boundaries

Start at [execution-guide.md](execution-guide.md); the first assignment is SC-00 N only. [execution-manifest.yaml](execution-manifest.yaml) divides the eight tickets into dependency-checked delivery units and assigns every V01–V98 row one primary evidence owner. [decision-register.md](decision-register.md) names the still-unresolved contracts and their downstream blockers. [fixtures-and-oracles.md](fixtures-and-oracles.md) supplies analytic/negative fixtures. Copy [implementation-record-template.md](implementation-record-template.md) into each bounded assignment's PR and successor handoff.

No unit is accepted or implemented. Prototype decisions remain unresolved; all 98 acceptance rows remain planned. The new execution-support documents were not covered by the historical exact-head independent reviews below. No paid/provider/worker calls, deployment, reviewer contact or merge are authorized by this extension. Resolve actual source from PR #711, not this worktree path; until merged, another session can fetch `claude/site-scene-generation-spec` and read the docs without basing its implementation branch on it.

## Owner intent

Generate a visually appealing 3D site first; associate buildings and complexes with multi-energy loads and process units; place real model equipment; play native hourly results. Provide native generation and explore direct/background Blender integration, export/refinement and reconstruction from suitable photos or layouts. This is authorization to develop the detailed design/exploration, not a claim the proposed runtime features exist.

## Read first

1. AGENTS.md and current owner/outsourced register; fetch current master and rebaseline open PR overlaps.
2. spec.md and contracts.md — requirements, canonical persistence/privacy/flow and integrity constraints.
3. implementation-plan.md and ai-guided-project-flow.md — build sequence and the required whole-process companion/context/approval/recovery integration.
4. validation-matrix.md — 98 proposed acceptance cases plus domain fixtures.
5. tools-and-experiments.md and issues/01–08 — selected tools, independent prototype/build slices and dependencies.
6. review-verdict.md — reviewed technical SHA, all findings/resolutions and scoped GO/NO-GO.

## Current evidence and state

Existing PRs #93/#139 are merged; Site 3D, layout rules, results, thumbnails/report images and optional derived lengths already exist. A prior fresh assessment ran 45 files / 544 tests successfully on master-equivalent 3D source. This does not exercise any proposed new implementation. Initial research/assessment files remain under `/workspace/scratch/site3d-assessment-20261024/`.

This work adds planning documents only. No new application code, runtime dependencies, jobs, endpoints, Blender installation, photo reconstruction, paid calls, official reviewer contact or deployment occurred. Two independent advisory review agents found four Spec issues and three Standards issues on initial head 8f49c179ad9a8a24f327b3676d34536705ba0d3a. Corrections are implemented in the documents. At technical head 7cb8daac79ea86725cab184be9b8b246194eec60, both axes report zero unresolved findings (five Spec and three Standards corrections). GO to publish and start the bounded Native prototype; normal review/evidence gates remain for subsequent implementation and runtime release. See review-verdict.md. No official GK/UX approval is claimed. The owner authorized uploading as a draft PR; resolve it by this unique branch in MLHaoChang/pypsa-eur.

## Next concrete task

Review SC-00 Native milestone in issues/01-prototype-and-contracts.md and freeze the public scene/proposal interfaces, target identity lifecycle, exact input formats and measurable resource gates. Validate a representative native building scene, Blender round trip and photo engine readiness in independently accepted N/B/P milestones. Native completion does not wait for Blender/photo. The native path does not wait for a photo GPU. Then proceed SC-01 -> SC-02 -> SC-03; SC-04 artifact work can follow native scene stability; advertising tested Blender compatibility requires B. SC-05 requires B; SC-06 layout/manual tracing needs N while its photo capability separately needs P.

Open items: exact external runtime/packaging choice, supported photo engine/hardware, frozen file/resource limits, metric registration criteria, agreed real-site fixtures and native model-target lifecycle hooks. Canonical authority, original-media privacy, geometry-first admission and /flow authority are now specified in contracts.md; runtime proof remains a prototype outcome. These are named prototype outcomes, not permission to guess silently. Existing engine/UX review requirements apply to future code. Owner/coordinator arranges review; do not contact reviewers from this session.

Preserve the separate G-a/G-b/K task package and its handoff at `/workspace/scratch/gb-k-readiness-20261024/NEXT-STEPS.md`. This design must not rewrite or merge their branches. Coordinator retains merge authority.

## Owner clarification: integrated guidance

The entire process is one project flow with contextual AI guidance, not a separate 3D wizard. ai-guided-project-flow.md, SC-07 (issues/08-ai-guided-project-flow.md) and V61–V72 specify it. Freeze contracts in SC-00 N; deliver shared guidance alongside SC-01–SC-03, not after optional engines. Scope includes existing section mapping, bounded fresh context, approved scene/model actions, native receipt/read-back, truthful result explanations, resume and manual fallback. No new voice stack, paid evaluation or autonomous execution is implied. Both independent agents confirmed technical head 5aa25514696274390c597e425bec21d1f8f79f96, zero unresolved findings on each axis. Advisory GO covers publication/Native prototype; subsequent implementation and runtime release retain normal evidence/review gates. See the separate extension record in review-verdict.md. Draft PR #711 carries this package; the coordinator retains merge authority.

## Latest optional-site clarification

Owner requires optional Create/Open/Resume at native site coordinates, disabled before valid location; mapping and reviewing campus precedes model association. Buildings-only save/exit/resume is a valid stop. Existing configured2D/SLD targets and ports/carriers are mapped later, with logical/physical route availability kept distinct; native results play at actual cadence. Read optional-site-workflow.md and workflow-assessment.md, then V73–V90. No new top-level card or parallel model is introduced. The clarification was independently reviewed at exact technical head eb2f8f64f9b7402764c559fafd1034228165c4a0: Spec0unresolved, Standards0unresolved; advisory GO for publication/Native prototype only, normal implementation/release gates remain. No runtime code or engine integration implemented.

Portable Claude implementation-PR handoff (separate package): https://github.com/MLHaoChang/pypsa-eur/blob/claude/codex-review-handoff-20261024/.scratch/codex-handoff/2026-10-24/README.md . Existing G-a/H/I/J/L official review/merge gates and G-b/K prerequisites remain on the coordinator side.

## Interactive configuration clarification

Owner explicitly requires conversational initial configuration and iterative adjustment of geometry, existing load/facility/asset/process associations, supported native connections/parameters and result channels. Read ai-guided-project-flow.md's example intent table and V91–V98, assigned to existing SC-00/01/02/03/07. Preserve separate scene/model/display effect paths and ordinary approvals; unsupported actions disclose a native editor fallback. Both advisory axes confirmed this98-case technical delta at533616f015b09383d625f2f956d5da14667c4851, zero unresolved findings; scoped GO is for publication/Native prototype. No runtime configuration capability has been implemented by this docs update.
