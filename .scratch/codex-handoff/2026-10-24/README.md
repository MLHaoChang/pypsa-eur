<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Claude review and continuation handoff

Status: published handoff snapshot; official reviews/merges still pending
Prepared: 2026-10-24T18:39:25.445531+02:00 (Europe/Berlin)
Repository: MLHaoChang/pypsa-eur
Baseline master: aba252d2384f55116bbd15516a5b025951ba265e
Publication branch: claude/codex-review-handoff-20261024

Read [review-handoff.md](review-handoff.md), [next-steps.md](next-steps.md) and [evidence/README.md](evidence/README.md). This package is independently fetchable without merging a feature PR or accessing the Codex filesystem. It changes no product code and grants no new authority. PR descriptions remain the durable current implementation/evidence record; fresh GitHub state supersedes this snapshot.

## Implementation PRs

| Task | Source and review | Next action |
|---|---|---|
| G-a | [#694](https://github.com/MLHaoChang/pypsa-eur/pull/694), head101e8745ca44eb45b20a8ebb1a1a076cf5edb2b8 | Fresh GK review, then coordinator merge. All current reported checks and fresh premerge pass; prior GK OK is stale. |
| H | [#699](https://github.com/MLHaoChang/pypsa-eur/pull/699), head9418177a1f4e93c11948e6117e8731b9d05755c8 | Current GK OK and passing premerge; coordinator merge. |
| I | [#700](https://github.com/MLHaoChang/pypsa-eur/pull/700), head1d5cc8d0ae88547b0e97514fdf998c73479033fc | Fresh GK/UX re-review; premerge still blocks on earlier official GK NOT OK despite green technical checks. |
| L | [#704](https://github.com/MLHaoChang/pypsa-eur/pull/704), headd7dea035757ea8ba14c2861a0eb4f4f920a95f75 | Final implementation GK review; plan OK is not final approval. Local/remote evidence and master cap diagnosis disclosed. |
| J | [#706](https://github.com/MLHaoChang/pypsa-eur/pull/706), head6afc428d84320ff0d034d84e63e6c6eae5972e5a | Official UX review; committed Guided/Expert evidence is linked in the PR. |

Each feature PR already contains its code, tests and release fragment. Claude can inspect and, in an authorized implementation session, fix findings on the existing branch. The reviewer role remains read-only under its own rules. A fix push invalidates prior-head CI/review evidence: run the prescribed tests, regenerate inventories where needed, await exact-head CI and renewed official verdicts. Coordinator retains merge authority; do not override a gate or branch protection.

## Unimplemented work and prerequisites

G-b (profile tuning frontend) and K (native result plausibility) are owner-authorized but **must wait for verified #694 merge**. Start both from fresh master, never from the unmerged G-a branch. K opens one draft plan PR and waits for official GK plan OK before code/tests. See next-steps.md and current master briefs under .scratch/renewable-profiles/outsource/ and .scratch/live-copilot/outsource/.

A–F, M and N are merged; do not implement them again. H/I/J/L have implementations above and must not be restarted as new tasks merely because master register labels are stale.

## Separate 3D-site design package

[PR #711](https://github.com/MLHaoChang/pypsa-eur/pull/711) contains the 3D-site/Blender/photo/AI guidance specs, not runtime implementation. Read its branch version of .scratch/site-scene-generation/README.md, HANDOFF.md, contracts.md, implementation-plan.md, ai-guided-project-flow.md and validation-matrix.md. The optional coordinate-first workflow assessment is complete and uploaded at publication head9d90d5e55c8cc483bcef0aa8b150ccc2e2b8dc10 (eight tickets,90proposed cases). Both advisory review axes confirmed technical head eb2f8f64f9b7402764c559fafd1034228165c4a0 with zero unresolved findings. [Read the normative optional workflow](https://github.com/MLHaoChang/pypsa-eur/blob/claude/site-scene-generation-spec/.scratch/site-scene-generation/optional-site-workflow.md) and [source-based assessment](https://github.com/MLHaoChang/pypsa-eur/blob/claude/site-scene-generation-spec/.scratch/site-scene-generation/workflow-assessment.md): optional coordinate-gated entry, campus review/regenerate, buildings-only save/resume, later mapping of configured native2D/SLD assets/processes/connections, then actual-cadence result playback. Specs only; runtime remains unimplemented. Current publication head9d90d5e55c8cc483bcef0aa8b150ccc2e2b8dc10 has all14reported checks success/expected docs-only skips and fresh premerge exit0 against masteraba252d. See [saved premerge](evidence/proceed-premerge711.log) and [documentation integrity record](evidence/optional-workflow-validation.json). These checks do not execute the90proposed runtime cases. **Recheck latest PR head and CI; earlier-head checks are not current-head evidence**. G-b/K and #694 gates are independent of that design package.

## Access from another Claude session

Run normal authenticated GitHub/Git access in that session; this handoff does not confer account permissions:

```bash
git fetch origin claude/codex-review-handoff-20261024
git show origin/claude/codex-review-handoff-20261024:.scratch/codex-handoff/2026-10-24/README.md
git show origin/claude/codex-review-handoff-20261024:.scratch/codex-handoff/2026-10-24/review-handoff.md
```

Fetch each existing implementation branch or inspect its PR; read fresh AGENTS.md, docs/agents/outsourced.md, owner decisions and applicable brief before edits. No need to copy local `/workspace` paths, install a plugin, or re-request existing owner authorization. No direct Claude/coordinator message was sent; pass this GitHub handoff link through the owner's established channel. Do not assume a reviewer has received it automatically.
