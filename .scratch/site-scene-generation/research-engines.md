<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Engine research: Blender-class and photo reconstruction (Q-SS-1)

Accessed 2026-11-13 unless noted. Primary sources only (upstream docs, repos, PyPI metadata). Anything not seen is marked UNVERIFIED. Licence consequences are engineering inference, not legal advice.

## 1. Question and requirements

- Which one Blender-class engine serves issue 05 (editable GLB+manifest snapshot, round-trip IDs/transforms, output-only `.blend`) and 06 (bounded isolated job, reviewed return, cancel/timeout, no hosted paid worker)?
- Which one photo engine serves the photo half of issue 07 (multi-view aerial/ground photos to a reference mesh; ODM and COLMAP are the named candidates; GPU and texturing path must be shown)?
- D09: pinned tested version/OS, trusted scripts only, no arbitrary `.blend` execution, worker sandbox/resource/cancel evidence. D10: one supported stack, licence/hardware/packaging, real texturing, scale/control residuals.
- Native generation and the neutral GLB bundle must work with every optional engine absent.
- Do not ship several photo engines; sparse SfM is not a textured campus.

## 2. Comparison tables

### 2a. Blender-class engines

| | Blender binary (`--background --python`) | `bpy` wheel | trimesh | pygltflib | Open3D |
|---|---|---|---|---|---|
| Licence | GPL-3.0 (bpy PyPI: GPL-3.0; Blender manual has a GPL page) | GPL-3.0 | MIT | MIT | MIT |
| Server consequence | Run as a separate subprocess/container: arm's-length, no linking into the app. Distributing Blender needs GPL source offer. | Importing into the app process links GPL code into it: avoid | None | None | None |
| Headless | Yes, `--background`, `--python`, `--python-exit-code`, `--factory-startup`, `--offline-mode`, `--disable-autoexec` (default) | Yes, but "isn't provided on Blender's official download page"; startup scene (cube/camera/light) is loaded | Pure Python | Pure Python | Python/C++ |
| Python API | `bpy` inside Blender | `import bpy` | Yes | Yes (glTF JSON/binary model) | Yes |
| GPU | Not needed for scene/GLB/`.blend` export | Same | No | No | No (mesh ops) |
| Cost | Free | Free | Free | Free | Free |
| Use-case quality | Full `.blend` writer; glTF exporter can write custom properties as `extras` | Same | GLB write/read, nodes/transforms/metadata; no `.blend` | Direct control of nodes/extras; no `.blend` | Mesh processing; not a scene/GLB-with-manifest tool (UNVERIFIED depth of glTF scene support) |
| Maintenance | 5.3 released 2026-11-10; 5.2 LTS released 2026-07-14, supported to 2028-07 | 5.3.0 uploaded 2026-11-10 | 5.2.1 uploaded 2026-11-12 | 1.16.5, 2025-07-24 | 0.20.0, 2026-09-16 |
| Isolation | OS-level only (container, no network, resource limits); Blender has no built-in sandbox | Hard to isolate (in-process) | In-process, no external exec | In-process | In-process |

### 2b. Photo reconstruction engines

| | OpenDroneMap (+NodeODM) | COLMAP (+OpenMVS) | Meshroom/AliceVision | nerfstudio (brief) |
|---|---|---|---|---|
| Licence | ODM AGPL-3.0; NodeODM AGPL-3.0 | COLMAP BSD (new BSD, own text only; deps separate); OpenMVS AGPL-3.0 | MPL-2.0 (some MIT files) | Apache-2.0 (PyPI) |
| Server consequence | AGPL network clause: unmodified separate service is the safe pattern; any modification must be offered to network users. Keep it out-of-process. | COLMAP is permissive. Adding OpenMVS brings AGPL back. | File-level copyleft; easiest of the three | Permissive, but output is a splat/NeRF, not a mesh by default (UNVERIFIED mesh export quality) |
| Headless/server | Yes: NodeODM REST API, Docker image | Yes: CLI, Docker image; `pycolmap` 4.2.1 Python bindings | Local/distributed execution; headless CLI not confirmed on README (UNVERIFIED) | CLI; GPU training |
| GPU | Not needed. ODM docs say GPU "currently has no impact on performance" (2020-era page, v3.6.0 docs); README: GPU only speeds SIFT features (NVIDIA only) | CUDA for dense `patch_match_stereo` (CUDA "at least 11.X" per install doc; HIP/ROCm build option, v4.2.0) | CUDA NVIDIA required for depth maps; only Draft Meshing is CPU (search summary of manual; version-dependent compute capability) | NVIDIA GPU (UNVERIFIED exact needs) |
| Texturing | Built in: `odm_textured_model.obj`, `odm_textured_model_geo.obj` (georeferenced), `odm_mesh.ply`, orthophoto GeoTIFF, DSM | COLMAP gives sparse/dense points; meshing/texturing needs OpenMVS (mesh texturing listed) or other tools | Built in textured mesh via node graph | None as a campus mesh |
| Georeferencing/scale | GPS/EXIF and GCP support (UNVERIFIED in detail here); georeferenced outputs listed | Scale needs external control (UNVERIFIED tooling) | Needs control by other means (UNVERIFIED) | Not metric |
| Fit | Aerial/drone sets best fit; ground-level sets UNVERIFIED | General SfM/MVS, both aerial and ground | General photogrammetry | Visualisation only |
| Maintenance | ODM 3,655 commits; NodeODM 671; no GitHub release entries seen; Docker tag `3.6.1-gpu` named in README; PyODM 1.5.13 (2026-04-13) | 4.2.1 released 2026-09-29 (v4.x active) | Meshroom develop branch 6,484 commits; AliceVision 12,438 commits; no release entries seen on pages | 1.1.5, 2024-11-11 (stale) |
| Isolation | Container; NodeODM supports cancel/remove tasks via API (UNVERIFIED details; see docs/index.adoc) | Container/subprocess | Container/subprocess | Container |

## 3. Per-candidate notes

**Blender binary.** `--background` plus `--python <file>`; `--python-exit-code` makes a script exception a nonzero exit; `--factory-startup` skips user startup file; `--offline-mode` disallows internet; `--python-use-system-env`/`--python-use-user-env` are off by default and weaken isolation if enabled. Source: https://docs.blender.org/manual/en/latest/advanced/command_line/arguments.html. Auto-execution of Python in `.blend` files (drivers, registered text blocks) is off by default on the command line (`--disable-autoexec`), but Blender's own page warns that loading untrusted files can run malicious code when enabled and lists manual-execution paths; it describes no sandbox. Source: https://docs.blender.org/manual/en/latest/advanced/scripting/security.html. Consequence: never open an inbound `.blend`; accept only our own normalized GLB+manifest as input, and write `.blend` as output only. glTF exporter: `.glb` is single-file; custom properties are exported as `extras` only when the Custom Properties option is enabled (`export_extras`), and imported always. Sources: https://docs.blender.org/manual/en/latest/addons/scene_gltf2.html (GLB text confirmed); the `extras` detail is from a search summary of the manual, re-check the exporter option name against the pinned version (UNVERIFIED). Versions: 5.3 released 2026-11-10 (not LTS), 5.2 LTS released 2026-07-14, supported until July 2028, 4.5 LTS until July 2027 (from search summary of https://developer.blender.org/docs/release_notes/ and https://www.blender.org/download/releases/; direct fetch of blender.org was blocked). Pin 5.2 LTS.

**`bpy` wheel.** PyPI 5.3.0, GPL-3.0, `requires_python ==3.13.*`, wheels for Linux x86_64, macOS arm64, Windows (https://pypi.org/pypi/bpy/json). The manual says it is not on the official download page, signal handlers are not initialized (so no Ctrl-C cancel of a render, no crash log), command-line options are unsupported, the default startup scene is loaded, and other modules can block GPU access (https://docs.blender.org/api/current/info_advanced_blender_as_bpy.html). Consequence: weaker cancellation story and in-process GPL linking; use only inside the worker if at all. Not recommended.

**trimesh / pygltflib.** trimesh 5.2.1, MIT, Python >=3.10 (https://pypi.org/pypi/trimesh/json); pygltflib 1.16.5, MIT (https://pypi.org/pypi/pygltflib/json); gltflib 1.0.13 is stale (2022-08-22). Both write GLB with no Blender install, which matches 05's requirement that the export works without Blender. Whether either already exists in the repo environment: not checked (see section 6).

**Open3D.** 0.20.0, MIT (https://pypi.org/pypi/open3d/json). Large dependency; only useful for point-cloud/mesh clean-up. Not needed for the GLB path.

**OpenDroneMap/NodeODM.** ODM is AGPL-3.0; README lists textured and georeferenced OBJ, PLY mesh, orthophoto, point cloud, DSM; GPU optional (NVIDIA, SIFT only) via the `:gpu` image (https://github.com/OpenDroneMap/ODM). Hardware page: 4 GB RAM/20 GB disk is the bare minimum for about 100-200 images; recommended 16 GB; RAM table 40 images 4, 250 images 16, 500 images 32, 1500 images 64 (https://docs.opendronemap.org/installation/). NodeODM is AGPL-3.0, a REST API in front of ODM, `docker run -p 3000:3000 opendronemap/nodeodm`; full API doc is `docs/index.adoc` in the repo (https://github.com/OpenDroneMap/NodeODM). PyODM 1.5.13 (2026-04-13, BSD) is the Python client (https://pypi.org/pypi/pyodm/json). Gap: output is OBJ/PLY, so a conversion to GLB (trimesh) is needed; georeferenced model carries its own origin, which must be reconciled with the Site origin.

**COLMAP (+OpenMVS).** COLMAP is new-BSD (https://raw.githubusercontent.com/colmap/colmap/main/LICENSE). 4.2.1 released 2026-09-29; 4.2.0 added CPU-Ceres fallback for BA and a ROCm/HIP build for `patch_match_stereo`; install doc: CUDA at least 11.x, prebuilt Docker image includes CUDA (https://github.com/colmap/colmap/releases, https://colmap.github.io/install.html). `pycolmap` 4.2.1, BSD-3 (https://pypi.org/pypi/pycolmap/json). OpenMVS: AGPL-3.0, input is camera poses plus sparse cloud, README lists mesh texturing; GPU need and a release tag not shown (https://github.com/cdcseacave/openMVS). A COLMAP-to-OpenMVS texturing path is therefore a two-tool pipeline we would own and package. Matches 07's warning ("requires a demonstrated compatible GPU and texturing path").

**Meshroom/AliceVision.** MPL-2.0 (https://github.com/alicevision/Meshroom, https://github.com/alicevision/AliceVision). Depth-map step needs CUDA NVIDIA GPU per the Meshroom manual/wiki (search result; minimum compute capability differs by release: manual says >=3.0 for MVS, GPU-recommendation wiki 2.0 for DepthMap and 5.2 for GPU feature extraction), so UNVERIFIED for the current release. Desktop-first tool; headless CLI not confirmed.

**nerfstudio.** 1.1.5, Apache-2.0, uploaded 2024-11-11 (https://pypi.org/pypi/nerfstudio/json). Not a metric reference-mesh tool; stale. Not pursued.

## 4. Recommendation

- **Blender-class: Blender 5.2 LTS binary, run as a separate pinned subprocess/container (not the `bpy` wheel), only for the optional `.blend` output job.** The default and always-available path is a Blender-free GLB+manifest writer using trimesh (or pygltflib for exact `extras` control). Why: licence boundary stays at a process edge; command-line flags give exit codes, offline mode and no auto-exec; LTS to 2028-07; kill-by-process-group gives real cancel/timeout; `bpy` has no signal handlers and needs exactly Python 3.13.
- **Photo: OpenDroneMap run through NodeODM as a separate unmodified service.** Why: built-in textured georeferenced mesh, CPU-capable (GPU optional), REST job API fits 06's job/cancel contract, documented RAM table gives a measurable resource gate, PyODM client is BSD. Cost: AGPL (keep unmodified and out-of-process); aerial-first.
- **Alternative A (photo): COLMAP 4.x + OpenMVS texturing** if ground-level sets are the priority or the owner wants BSD-only for the core. Needs CUDA for dense, two-tool packaging, and the AGPL OpenMVS bit anyway.
- **Alternative B (photo): Meshroom/AliceVision (MPL-2.0)** if file-level copyleft is preferred; needs NVIDIA CUDA for depth maps.
- **Alternative C (Blender-class): no Blender at all** (trimesh/pygltflib only): drop `.blend` output, keep GLB+manifest and "Export for Blender" as the neutral bundle. Cheapest, but fails D09's genuine pinned-Blender round-trip fixture.

## 5. What changes for issues 05, 06, 07

- **05:** Split into (a) the neutral GLB+manifest bundle, built with trimesh/pygltflib, no Blender needed, shippable on its own; (b) an optional worker step that runs a repo-owned script with Blender 5.2 LTS to produce an output-only `.blend`. D09 fixture becomes: pinned 5.2.x build, import our GLB, check transforms/IDs via `extras` (enable the custom-properties export option), independent metric oracle. Inbound `.blend` stays rejected.
- **06:** Worker contract: input is normalized GLB+manifest plus our script; command shape `blender --background --factory-startup --offline-mode --python-exit-code <n> --python <ours> -- <args>`; container with no network, read-only input, size/time/memory caps, process-group kill for cancel/timeout; output returns as a proposal for review. Resource limits are still measured, not assumed (UNVERIFIED for Blender; needs the SC-00 B run).
- **07 (photo half):** Select ODM/NodeODM as D10's stack; start with an aerial set, measure RAM per the ODM table, record residuals/GCP use, convert OBJ to GLB, label output reference-only until aligned. The ground-level set is not covered by ODM evidence here: either test it on ODM and record failure, or defer ground-level to alternative A. Private-original store and external-destination rules still apply (NodeODM runs on a configured, authorized host only).

## 6. Unverified/open items

- AGPL network-clause consequences for the product (legal review): is an unmodified NodeODM container acceptable without further steps?
- Blender: exact `export_extras` option name and default for 5.2 LTS; blender.org pages were blocked for direct fetch, release dates came via search summaries; confirm 5.2.x patch version to pin.
- Blender memory/time footprint and sandbox hardening (seccomp, rlimits) for the worker: no primary source; needs the measured SC-00 B run.
- ODM: latest tag/date and last-commit date (GitHub release pages empty in fetch; GitHub API blocked in this session); NodeODM cancel/remove endpoint and task-option details (read `docs/index.adoc`); ground-level photo suitability; GCP/scale and georeference handling; OBJ coordinate origin vs Site origin; whether the "GPU has no impact" statement (docs site "3.6.0", © 2020) still holds.
- COLMAP: scale/control-point workflow for metric anchoring; OpenMVS release tag, CUDA need, and the COLMAP-to-OpenMVS import path (README did not mention COLMAP).
- Meshroom/AliceVision: current release tag and CUDA minimum, headless CLI.
- nerfstudio mesh export and GPU needs.
- Open3D glTF scene support depth.
- Whether trimesh/pygltflib are already dependencies of `pypsa-gui` (not checked), and whether the host has any NVIDIA GPU.
