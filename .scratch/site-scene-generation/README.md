# Site-scene generation design package

Status: concrete proposal; no new runtime implementation or external review approval

Read [spec.md](spec.md), [implementation-plan.md](implementation-plan.md), [validation-matrix.md](validation-matrix.md) and [HANDOFF.md](HANDOFF.md).

The design covers native campus generation, buildings associated with multi-energy Loads and process-unit complexes, equipment placement, native result playback, optional integrated Blender workers, Blender export/refinement, and reconstruction proposals from suitable layouts/photos. It reuses the existing Site 3D implementation; no new top-level workflow card is proposed.

Seven independently assignable tickets live in [issues/](issues/), SC-00 through SC-06. SC-00 is the first task. The native path remains independent of Blender/photogrammetry availability. Input formats, target identity hooks and worker/resource limits must be frozen with evidence before implementation claims.

This package is local on branch claude/site-scene-generation-spec and has not been uploaded or opened as a PR in this task. No worker or model calls were made.
