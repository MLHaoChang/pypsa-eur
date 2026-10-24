<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Site-scene generation design package

Status: independently reviewed specification — GO to publish and begin the Native prototype; no runtime release approval

Read [spec.md](spec.md), [implementation-plan.md](implementation-plan.md), [validation-matrix.md](validation-matrix.md) [contracts.md](contracts.md), [ai-guided-project-flow.md](ai-guided-project-flow.md), [optional-site-workflow.md](optional-site-workflow.md), [workflow-assessment.md](workflow-assessment.md), [tools-and-experiments.md](tools-and-experiments.md) and [HANDOFF.md](HANDOFF.md).

The design covers native campus generation, buildings associated with multi-energy Loads and process-unit complexes, equipment placement, native result playback, optional integrated Blender workers, Blender export/refinement, and reconstruction proposals from suitable layouts/photos. It reuses the existing Site 3D implementation; no new top-level workflow card is proposed.

Eight independently assignable tickets live in [issues/](issues/), SC-00 through SC-07. SC-00 is the first task. The native path remains independent of Blender/photogrammetry availability. Input formats, target identity hooks and worker/resource limits must be frozen with evidence before implementation claims.

The owner authorized independent review, corrections and draft-PR publication. Both review agents closed all findings at technical head 7cb8daac79ea86725cab184be9b8b246194eec60: five Spec and three Standards corrections, zero unresolved. See [review-verdict.md](review-verdict.md) for the scoped GO/NO-GO record. No runtime worker or model calls were made. Advisory review is separate from official GK/UX approval and real engine validation.

Publication branch: `claude/site-scene-generation-spec`, repository MLHaoChang/pypsa-eur, base master. The draft PR is found by this unique branch. The next task is issues/01-prototype-and-contracts.md, Native milestone only; optional workers stay independently gated.

Owner clarification: the complete process must live within the existing project flow with contextual AI guidance. ai-guided-project-flow.md and SC-07 make this a cross-cutting native release requirement; validation-matrix.md now contains 72 proposed cases. Both independent agents confirmed the extension at technical head 5aa25514696274390c597e425bec21d1f8f79f96 with zero unresolved findings. The advisory GO covers publication and the bounded Native prototype; runtime release remains gated. See review-verdict.md for the separate historical and extension records.

Latest owner clarification:3D is optional and coordinate-gated, followed by campus mapping/review, buildings-only save/resume, existing native2D/SLD target/connectivity mapping and actual-cadence result playback. optional-site-workflow.md is normative; workflow-assessment.md maps gaps to the existing slices. Validation now contains90proposed cases; the earlier72-case review remains historical. Both agents confirmed the clarification at technical head eb2f8f64f9b7402764c559fafd1034228165c4a0, with zero unresolved findings per axis and scoped GO for publication/Native prototype. Runtime release is still gated; see review-verdict.md.

The latest owner clarification makes interactive copilot configuration explicit: ai-guided-project-flow.md now defines scene/model/display intent examples, exact Load/facility and process/connection mapping, supported parameter adjustment and required V91–V98. Both independent agents confirmed this98-case extension at technical head533616f015b09383d625f2f956d5da14667c4851 with zero unresolved findings; scoped GO covers publication/Native prototype, and runtime release remains gated. See review-verdict.md.
