<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-05 — optional integrated Blender generation and reviewed return

Status: ready-for-agent (slice (a) #1561; slice (b), the backend reviewed return, on `claude/site-scene-06b`; slice (c), the review UI, still to build; see the 2026-11-17 comments)
Type: task
Dependencies: SC-04; SC-00 worker/protocol/resource evidence
Baseline: master 9988f9899a3bf8e80f51272529f18ea70599b90c

## Implementation assignment

Follow [execution-guide.md](../execution-guide.md) and the matching units in [execution-manifest.yaml](../execution-manifest.yaml). Freeze this assignment's actual-master file allowlist and public contracts; satisfy [decision gates](../decision-register.md) before dependent code, and record test-first evidence/unfinished work using [implementation-record-template.md](../implementation-record-template.md). Prototype N/B/P units close their own decisions through evidence; they do not require accepted experimental outcomes before starting. Primary V ownership does not remove cross-cutting obligations or authorize changes outside the frozen slice.

## Scope and acceptance

Write readiness/job/apply tests before implementation. A configured pinned Blender executable/worker receives only normalized owned input and a trusted script in a bounded isolated job. Native generation remains available when it is absent. Reuse the agreed job persistence/cancellation contract; do not infer safe resource limits or add a kind that can execute the solve dispatcher accidentally.

A worker's completion produces a proposal. Geometry/placement returns must reference the exact export base and current scene/native target identity. Validate units/transforms/content limits and all IDs, show a diff and named unknowns, then require explicit Apply. Missing metadata yields reference-only geometry, not an automatic rebind. Extras cannot change numerical model inputs or results. Preserve ordinary project locks and recheck access at Apply.

Test cancellation, timeout, worker death, late/stale output, concurrent edit, duplicate Apply, unknown apply outcome/read-back and revoked access. Never execute untrusted uploaded .blend scripts. Optional Open in Blender is a separate desktop convenience using a configured executable; it is not a web capability claim.

Done: V32–38, genuine worker evidence and no change to the model from imported visual data. Only the validated adapter is exposed; no hosted paid worker is enabled.

## Validation

V32–38, V45–50, V52; definitions in ../validation-matrix.md. Tests before implementation at the frozen public seams; no passing evidence is claimed by this ticket.

## Coordination

One bounded implementation PR after applicable plan review; current master and open-PR overlap check first. Follow AGENTS and steward, add a release fragment for shipped behavior, never change compiled release notes or another task branch. Record exact tests, genuine worker evidence and unimplemented parts in the PR and handoff.

## Cross-cutting AI guidance

Deliver this slice's in-place explanation, typed proposed action or truthful UI fallback, native read-back and resume context under [ai-guided-project-flow.md](../ai-guided-project-flow.md), alongside SC-07. Server flow state and current approvals remain authoritative. Include the relevant V61–V72 assertions; optional engine absence cannot block the native/manual flow.

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role needs-info

Was: `needs-triage — specification proposed; not implemented`. Owner question Q-SS-1 (see issue 05): the worker needs the SC-00 B worker, protocol and resource evidence, which waits for a supported Blender engine. Blockers when answered: 04 and 05. Opus, reviewers GK B (worker isolation, security) and UX.

2026-11-05, owner answer Q-SS-1 (coordinator card): "Park until an engine is chosen (Recommended)". This issue waits for an engine choice.

### 2026-11-14: engine chosen (Q-SS-2)

2026-11-14, owner answer Q-SS-2 (coordinator card): "option 1 (Recommended)". Engines chosen, see [research-engines.md](../research-engines.md) (#1490): Blender-free GLB+manifest by default (trimesh/pygltflib, MIT); Blender 5.2 LTS only as a separate pinned no-network container for optional `.blend` output; photos via OpenDroneMap/NodeODM, out-of-process and unmodified (AGPL). Change: the worker is one container job running the pinned Blender 5.2 LTS binary with `--background --python-exit-code --factory-startup --offline-mode`, no network, resource caps and a process-group kill for cancel/timeout. Not the `bpy` wheel (exact Python 3.13, GPL linked in-process, no cancel). Never open inbound `.blend` files: Blender has no sandbox, isolation is OS-level. Sandbox and resource numbers come from the SC-00 B measured run.

### 2026-11-17: SC-00 B moved to CI; plan GO; split into two slices

- SC-00 B evidence: the agent container may not run the Blender binary (the auto-mode classifier refuses it). Owner decision SC06-CI-1: a manual CI job (`.github/workflows/site-scene-blender-sc00b.yaml`, #1557) measures the round trip. Facts recorded without a run:
  - Pin: Blender 5.2.4 LTS (2026-11-10), URL https://download.blender.org/release/Blender5.2/blender-5.2.4-linux-x64.tar.xz.
  - Tarball sha256: 2fd14c50987426897ba60237ee996edf2b4151f4b1a72a460ca20be58c1d2d86 (matches blender.org's).
  - The unpacked `blender` executable's sha256: 9c12ad1b09401dcd3a8a5336279bca91455f15023aa0e5e4cb5cdb7b116a09f1.
  - glTF custom-properties options: exporter `export_extras` (default False), importer `import_scene_extras` (default True).
  - The measured CPU, peak RSS, wall time and output still come from that job.
- GK B plan GO, with conditions B1–B7 for slice (a) and R1–R2 for slice (b):
  - Q1: always a container wrapper (`--network none`, read-only root, non-root user, memory cap). There is no in-host mode. An in-host mode for desktop installs is an owner question, not built here.
  - Q2: split.
- Slice (a): worker, `blender_scene` job kind, readiness, and `POST .../scene/blender-jobs` + `GET .../scene/artifacts/{id}`. There is no path that applies returned geometry. The caps were placeholders until the CI numbers landed; see the SC-00 B measurement below.
  - The app builds the whole `docker`/`podman run` command itself. The operator sets only:
    - the unpacked tree, `PYPSA_GUI_BLENDER_DIR`, whose whole-tree sha256 is pinned (`9c3072a9…a3be`), bind-mounted read-only;
    - an absolute runtime path, `PYPSA_GUI_BLENDER_RUNTIME`;
    - a digest-pinned library image, `PYPSA_GUI_BLENDER_IMAGE`.
  - There is no free-form wrapper. An early free-form wrapper draft failed review: its flags could be overridden, and the limits and kill reached only the CLI.
  - Cancel and timeout run `<runtime> kill <name>`.
- Slice (b): the reviewed return (proposal plus Apply), with GK B's R1 (the returned GLB is untrusted: size caps, embedded buffers only) and R2. It also fixes the contract `lock: False` mismatch on the proposal create and cancel routes.

### 2026-11-17: SC-00 B measured (run 44013964974, master 6b559a5e)

The manual job ran on `ubuntu-24.04` (4 vCPU, 16 GiB, 3 GiB swap). Artifact `sc00b-blender-roundtrip`, sha256 `a76ac095…861b`.

- Blender 5.2.4 LTS (hash cd5dc428cce1). The round trip imported, read `feature_id` from the extras and exported: `MATCH 3 nodes`.
- Process wall time 2.77 s. Inside Blender: 0.68 s script wall, 2.46 s user plus 0.19 s system CPU, peak RSS 337,432 KiB (about 330 MiB).
- Output: `rt.glb` 5,028 B and `rt.blend` 511,551 B.
- glTF options (grep of the bundled add-on): `export_extras` at line 667 and `import_scene_extras` at line 2013.
- Missing libraries: `ldd` found none, both before and after the job installed `libxxf86vm1 libxfixes3 libxi6 libxkbcommon0 libsm6 libxrender1 libgl1 libegl1`. The runner image already carries them, so this run does not give a minimal image's list. The operator's runtime image must provide at least those eight X/GL libraries, plus glibc. A minimal image's list stays unmeasured until the image is built.
- Caps (B5) confirmed, not tightened. The fixture is a 3-node scene, so a real campus scene and container start-up need headroom:

  | cap | value | headroom over the run |
  |---|---|---|
  | wall | 180 s | about 65x |
  | CPU | 120 s | about 45x |
  | memory | 2 GiB, no swap | about 6x peak RSS |
  | output | 256 MiB each | about 500x the `.blend` |


### 2026-11-17: slice (b) plan GO, GK B (61), conditions G1–G9

- R2's text was lost with GK B (60)'s session. R1 plus G1–G3 are the complete untrusted-GLB contract.
- Q1: (b) is backend-only and route-only, and no chat path can create or apply it. Slice (c) is the review UI, under UX review.
- Q2: a height taken from the mesh's Y extent is accepted only when the mesh passes the rigid-prism check. It must be finite, above 0 and at most the height cap, and the diff shows it before and after.
- G1: the parser refuses:
  - duplicate JSON keys and NaN/Infinity, with nesting depth capped and a typed refusal;
  - sparse accessors, animations, skins, morph targets and any extension (`extensionsUsed` included);
  - a byteStride outside 4..252 or not a multiple of 4, and a wrong componentType for the accessor's type.
  - Total decoded data is capped before any decode.
- G2: every refusal is typed, with its own reason and test, and each parser guard has a killed mutant.
- G3: a feature_id on two nodes refuses the whole return.
- G4: world transforms compose. Rotation is yaw-only within a stated tolerance. The prism is matched within a stated tolerance. An unedited round trip is exactly zero, and a known move plus a 30° yaw reproduces exactly those values.
- G5: Apply runs the editor's validators, with one refusal through the return path.
- G6: access, project and site are rechecked at create and at Apply. Each part of the meta equality has its own red case.
- G7: the diff lists every bound value per building, and every unknown or reference-only node by name, never as a count.
- G8: `lock: True` passes the lock-policy, chat-disposition, ACL and golden-coverage tests, plus the inventory, route-parity and control-matrix checks at the trial merge.
- G9: the four (a) pins each have a mutant. Follow-up issue 10.

### 2026-11-17: slice (b) built (branch `claude/site-scene-06b`)

- R1 is `services/site_scene/glb_read.py`. The returned GLB is untrusted, and these limits apply:
  - Container: one JSON chunk and at most one BIN chunk, with the declared length exact.
  - Buffers: exactly one, embedded. Any `uri`, extension, animation, skin or morph target is refused, and so are duplicate JSON keys and NaN.
  - Every view and accessor is bounds-checked. Running totals of vertices, indices and primitives are capped, so aliased accessors cannot amplify.
  - The node graph is a forest of depth at most 16.
  - A node may not carry a matrix or a scale. Numbers must be finite.
  - Every `extras` is capped at 4 KiB, and only `extras.feature_id` is read.
- The `blender_return` proposal (`services/site_scene/blender_return.py`) is route-only and not a chat action.
  - The export base must be exact: revision, generation and generation sha256, and the rebuilt export GLB's sha256 must equal the meta's.
  - It proposes, for matched buildings only, rigid moves, yaw turns and a prism's new height, checked by `native.check_footprint` and `native._height`.
  - Everything else is a named unknown and is not applied: tilted, lifted, mesh_edited, no_mesh, missing_in_return, unknown_feature_id, equipment_not_applied, not_a_building.
  - Nodes with no id or an unknown id are listed by node index and name as reference-only.
  - A duplicated id refuses the whole return (`ambiguous_feature_id`).
  - The returned GLB's sha256 joins the input hash, so Apply re-reads the artifact and refuses changed bytes.
- Contract: proposal create and cancel are `lock: True`. A test drives every contract row under a foreign lock.
- GK B's four pins from #1561 have their own tests: the kill timeout, the kill except, the symlinked store, and META_CAP.
- Artifact streaming and retention are filed as issue 10. The review UI (diff panel plus Apply) is not in this slice.

### 2026-11-17: slice (b) review

- GK B (62) NOT OK @781b75c7. Three items:
  - an unhashable componentType gave a 500;
  - a non-finite composed transform was not refused, and the footprint check through the return was unpinned;
  - the revoked-owner case and the not-found parity case had no tests.
- All three were fixed at f200722c.
- GK B (62) OK @f200722c4060851cb5bf8f3d9cae07a736eed5d0, sent by message before the PR opened (#1561 had not merged). The OK carries to the PR head if that head is f200722c plus a clean master merge; a rebase or hand resolution gets a delta check.
- GK B (62) was archived afterwards. GK B (63) is session_01G7qCundFLhgXpNk7ehKZQT.
