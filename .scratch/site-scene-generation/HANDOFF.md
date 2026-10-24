<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Continuation handoff

Status: revised after independent review; delta verdict pending; implementation not started
Date: 2026-10-24
Branch/worktree: `claude/site-scene-generation-spec`, `/workspace/site-scene-spec`
Baseline: `aba252d2384f55116bbd15516a5b025951ba265e`

## Owner intent

Generate a visually appealing 3D site first; associate buildings and complexes with multi-energy loads and process units; place real model equipment; play native hourly results. Provide native generation and explore direct/background Blender integration, export/refinement and reconstruction from suitable photos or layouts. This is authorization to develop the detailed design/exploration, not a claim the proposed runtime features exist.

## Read first

1. AGENTS.md and current owner/outsourced register; fetch current master and rebaseline open PR overlaps.
2. spec.md — requirements, semantics and integrity constraints.
3. implementation-plan.md — records, candidate interfaces, build sequence and touch points.
4. validation-matrix.md — 59 proposed acceptance cases plus domain fixtures.
5. issues/01–07 — independent prototype/build slices and dependencies.

## Current evidence and state

Existing PRs #93/#139 are merged; Site 3D, layout rules, results, thumbnails/report images and optional derived lengths already exist. A prior fresh assessment ran 45 files / 544 tests successfully on master-equivalent 3D source. This does not exercise any proposed new implementation. Initial research/assessment files remain under `/workspace/scratch/site3d-assessment-20261024/`.

This work adds planning documents only. No new application code, runtime dependencies, jobs, endpoints, Blender installation, photo reconstruction, paid calls, official reviewer contact or deployment occurred. Two independent advisory review agents found four Spec issues and three Standards issues on initial head 8f49c179ad9a8a24f327b3676d34536705ba0d3a. Corrections are implemented in the documents; the delta verdict is pending. No official GK/UX approval is claimed. The owner has now authorized uploading the revised specs as a draft PR; publication is pending this review.

## Next concrete task

Review SC-00 Native milestone in issues/01-prototype-and-contracts.md and freeze the public scene/proposal interfaces, target identity lifecycle, exact input formats and measurable resource gates. Validate a representative native building scene, Blender round trip and photo engine readiness in independently accepted N/B/P milestones. Native completion does not wait for Blender/photo. The native path does not wait for a photo GPU. Then proceed SC-01 -> SC-02 -> SC-03; SC-04 artifact work can follow native scene stability; advertising tested Blender compatibility requires B. SC-05 requires B; SC-06 layout/manual tracing needs N while its photo capability separately needs P.

Open items: exact external runtime/packaging choice, supported photo engine/hardware, frozen file/resource limits, metric registration criteria, agreed real-site fixtures and native model-target lifecycle hooks. Canonical authority, original-media privacy, geometry-first admission and /flow authority are now specified in contracts.md; runtime proof remains a prototype outcome. These are named prototype outcomes, not permission to guess silently. Existing engine/UX review requirements apply to future code. Owner/coordinator arranges review; do not contact reviewers from this session.

Preserve the separate G-a/G-b/K task package and its handoff at `/workspace/scratch/gb-k-readiness-20261024/NEXT-STEPS.md`. This design must not rewrite or merge their branches. Coordinator retains merge authority.
