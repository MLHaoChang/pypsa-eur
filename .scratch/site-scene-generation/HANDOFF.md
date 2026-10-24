# Continuation handoff

Status: proposed design complete enough for review; implementation not started
Date: 2026-10-24
Branch/worktree: `claude/site-scene-generation-spec`, `/workspace/site-scene-spec`
Baseline: `aba252d2384f55116bbd15516a5b025951ba265e`

## Owner intent

Generate a visually appealing 3D site first; associate buildings and complexes with multi-energy loads and process units; place real model equipment; play native hourly results. Provide native generation and explore direct/background Blender integration, export/refinement and reconstruction from suitable photos or layouts. This is authorization to develop the detailed design/exploration, not a claim the proposed runtime features exist.

## Read first

1. AGENTS.md and current owner/outsourced register; fetch current master and rebaseline open PR overlaps.
2. spec.md — requirements, semantics and integrity constraints.
3. implementation-plan.md — records, candidate interfaces, build sequence and touch points.
4. validation-matrix.md — 52 proposed acceptance cases plus domain fixtures.
5. issues/00–06 — independent prototype/build slices and dependencies.

## Current evidence and state

Existing PRs #93/#139 are merged; Site 3D, layout rules, results, thumbnails/report images and optional derived lengths already exist. A prior fresh assessment ran 45 files / 544 tests successfully on master-equivalent 3D source. This does not exercise any proposed new implementation. Initial research/assessment files remain under `/workspace/scratch/site3d-assessment-20261024/`.

This work adds planning documents only. No new application code, runtime dependencies, imports, jobs, endpoints, Blender installation, photo reconstruction, paid calls, reviewer contact, deployment or independent review occurred. No PR has been opened for these documents in this task.

## Next concrete task

Review SC-00 and freeze the public scene/proposal interfaces, target identity lifecycle, exact input formats and measurable resource gates. Validate a representative native building scene, Blender round trip and photo engine readiness in separate bounded prototypes. The native path does not wait for a photo GPU. Then proceed SC-01 -> SC-02 -> SC-03; SC-04 can follow scene stability; SC-05/06 require their own prototype evidence.

Open items: exact external runtime/packaging choice, supported photo engine/hardware, frozen file/resource limits, metric registration criteria, agreed real-site fixtures and native model-target lifecycle hooks. These are named prototype outcomes, not permission to guess silently. Existing engine/UX review requirements apply to future code. Owner/coordinator arranges review; do not contact reviewers from this session.

Preserve the separate G-a/G-b/K task package and its handoff at `/workspace/scratch/gb-k-readiness-20261024/NEXT-STEPS.md`. This design must not rewrite or merge their branches. Coordinator retains merge authority.
