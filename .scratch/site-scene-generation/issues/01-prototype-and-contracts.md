<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-00 — demonstrate the geometry and exchange contracts

Status: ready-for-agent — N milestone (owner decision SITE3D-START-1); B and P stay pending until a supported Blender and photo engine are available
Type: prototype
Dependencies: none
Baseline: master 9988f9899a3bf8e80f51272529f18ea70599b90c

## Implementation assignment

Follow [execution-guide.md](../execution-guide.md) and the matching units in [execution-manifest.yaml](../execution-manifest.yaml). Freeze this assignment's actual-master file allowlist and public contracts; satisfy [decision gates](../decision-register.md) before dependent code, and record test-first evidence/unfinished work using [implementation-record-template.md](../implementation-record-template.md). Prototype N/B/P units close their own decisions through evidence; they do not require accepted experimental outcomes before starting. Primary V ownership does not remove cross-cutting obligations or authorize changes outside the frozen slice.

## Scope and acceptance

Read spec.md, implementation-plan.md and validation-matrix.md. Do not modify the solver or install mandatory base-app dependencies. Complete independent N (Native), B (Blender) and P (Photo) milestones, each with its own evidence/status. N covers model-free native generation, canonical writes, private-media policy and server /flow. B covers a genuine GLB/manifest round trip on a supported pinned Blender. P covers a small genuine reconstruction on supported hardware. Completion of N alone unblocks native implementation; the parent issue need not wait for B/P to enable SC-01.

Each item below is tagged with the milestone that settles it. An SC-00 N assignment settles only the [N] items and does no B or P work: it installs no Blender or photo engine, runs no worker and leaves every [B]/[P] item as a named pending result.

- [N] Metric frames/origin and dimensional labels.
- [N] Exact public scene/proposal records and method/path contracts (D02).
- [N] Existing equipment geometry: name #712's sites.json `asset_footprints` record (`pypsa-gui/backend/services/site_service.py`; `pypsa-gui/frontend/src/site3d/sitesStore.ts`, `footprints.ts`, `layout.ts`) as the existing equipment geometry, and freeze how an adopted Site's canonical generation takes it over: the legacy projection, stale-save refusal, copy/bundle closure, and rename/delete rekeying (contracts.md §2, D02/D03). Native equipment proxies reuse its `width_m`/`depth_m`/`height_m`; no second equipment-footprint store. Its `<Class>:<name>` key is the D06 identity gap SC-02 must close.
- [N] Source/asset redistribution posture for native sources and fixtures (D08).
- [N] Native target rename/delete/recreate identity hooks: trace them and record the gap; D06 itself closes before SC-02, not in N.
- [N] Measured native file/pixel/mesh/job budgets. [B] Blender export/worker budgets. [P] Photo texture/mesh/job budgets.
- [N] Background generation is separate from hourly playback.
- [N] Inspect the existing solve queue before choosing any job adapter; no unknown kind may fall through to an electrical solve (D04).
- [N] The reference 10 × 20 × 8 m building: report expected versus observed geometry and identity; a visually attractive screenshot is not a scale/binding test.
- [N] Native/proxy generation stays unblocked by optional GPU availability.
- [B] Supported pinned Blender version and the GLB/manifest round trip (D09).
- [P] The two known camera/control/scale fixtures and the small genuine reconstruction (D10).
- [B] [P] A worker not available is a named missing result, not a successful stub.

Done for each milestone separately: exact reproducible evidence and its agreed downstream contracts, with each unsupported input/engine explicitly disabled. Record accepted N independently even while B/P remain unavailable or pending. Before SC-02, lifecycle identity must be demonstrably enforceable or its required scope amendment is recorded. No production worker or per-project arbitrary-library override is enabled here; "disabled from production" is defined in execution-guide.md "Contract freeze".

[N] The owner also requires end-to-end AI guidance: freeze the bounded per-turn context, proposed scene tool tiers/DTOs, approval/receipt/recovery and per-section handoff in ../ai-guided-project-flow.md. Prototype a native journey with the existing scripted provider and real native records; no paid call. This is part of N and must not wait for optional engines. Freezing the tool DTOs adds no chat tool: scene tools enter only in SC-07.

## Validation

Definitions in ../validation-matrix.md. Each list matches that unit's `additional_applicable_cases` in ../execution-manifest.yaml exactly:

- N (SC-00-N): V05, V06, V07, V08, V49, V53, V54, V55, V56, V57, V58, V59, V60, V64, V65, V67, V73, V74, V79, V91.
- B (SC-00-B): V06, V29, V30, V32, V33, V34, V35, V36, V37, V38, V45, V48, V49, V55, V56, V58, V90.
- P (SC-00-P): V39, V40, V41, V42, V43, V44, V45, V48, V49, V55, V56, V58, V90.

V14 is not an SC-00 case: it belongs to SC-02 and the D06 gate only. Tests before implementation at the frozen public seams; no passing evidence is claimed by this ticket.

## Coordination

One bounded implementation PR after applicable plan review; current master and open-PR overlap check first. Follow AGENTS and steward, add a release fragment for shipped behavior, never change compiled release notes or another task branch. Record exact tests, genuine worker evidence and unimplemented parts in the PR and handoff.

## Optional coordinate-first workflow clarification

Freeze the confirmed native site-anchor settings DTO/persistence/ACL seam, open-versus-generate action verdicts, saved buildings-only state, native graph/terminal projection and native cadence contract from optional-site-workflow.md. Prototype both geometry-first and already-configured-model entry orders. Unknown anchor persistence is an explicit prototype outcome, never permission to create a dummy Bus. V73–V90 are design cases to freeze, not all completed prototype evidence. Read [optional-site-workflow.md](../optional-site-workflow.md) and [workflow-assessment.md](../workflow-assessment.md).

### SC-00 N UI contract

SC-00 N freezes these UI rules; SC-01 builds them. optional-site-workflow.md mirrors this list, and a change to one changes both.

1. **Current UI names.** The view is "Site 3D": the label of the `site` entry in `MapModeSwitcher` (`pypsa-gui/frontend/src/components/MapModeSwitcher.tsx`). The package uses "Site 3D" for it everywhere. A rename of that switch label is frozen in the same slice that makes it, never as a second name for the same view.
2. **One entry point, including the view switch.** "Create 3D site" in Project & site is the one creation action. Site 3D with no scene shows an empty state: the current action state from rule 3 (the disabled reason, or the enabled action's label) and a link to that one "Create 3D site" action. The empty state is not a second creation control. Today's `SiteEmptyState` (`components/SiteEmptyState.tsx`) has its own "Generate 3D site" button and points to "New site" on the Satellite map; SC-01 folds both into the one action rather than keeping them beside it.
3. **One visible action label per state.**

   | State | Visible action | Reason and link |
   |---|---|---|
   | No confirmed anchor | "Create 3D site", disabled | "Add site coordinates to create a 3D site" › the coordinate field |
   | Unsaved project | "Save project first" | Points to Save in the header; no second save button |
   | Anchor, no Site | "Create 3D site" | — |
   | Site with unfinished parts | "Resume site setup" | — |
   | Accepted scene | "Open 3D site" | — |

4. **Homes and return, in both modes.** Each link a part uses has one Guided home and one Expert home (section ids and panels from `layout/flowSections.ts`, `SECTION_OPEN` and `sectionForComponent`):

   | Link | Guided home | Expert home |
   |---|---|---|
   | Coordinate field | Project & site card (`project`), field drawer | `horizon` panel. D01 freezes the field; if it lands elsewhere, both homes are re-frozen in that slice |
   | Loads | Loads & demand card (`loads`), the Load's drawer | Loads table, the Load selected |
   | Generation, storage | Assets & sizing card (`assets`), the component's drawer | `capacityBounds` panel / the component's Properties |
   | Process | Carriers & processes card (`carriers`), the unit's drawer | Carriers table / the unit's Properties |
   | Connection/PoC, transformer | Connection & grid card (`connection`), the field's or Transformer's drawer | `campusElectrical` panel / the Transformer's Properties |
   | Native result selection | Results → reports (`results`) | `results` panel, the run tab via `showResultsTab` |

   Every jump that leaves Site 3D goes through `withReturn(...)` (`layout/flowNavigation.ts`, via `openFlowTarget` or `openFlowTargetInPlace`), shows "Back to Site 3D" and lands on the same Site, part and selection. Today's `FlowReturn` records mode, section, panel, drawer, selected component and scroll, but not the map mode, Site id or part; SC-00 N freezes that extension and SC-01 adds it. A deep link to a block uses request-then-scroll (as `requestGridReadiness` does), never a bare `setSlidePanel`. In Guided the jump opens in place (`openFlowTargetInPlace`) and never switches to Expert.
5. **The four parts are not a stepper.** Part progress shows inside the Site 3D context only. It adds no rail, card or section-completion signal to the flow sidebar.
6. **Wording.** A colon, not an em dash: "Buildings only: configure model elements to map them". Every unavailable layer or target shows a visible reason naming the missing input, with one "›" link to its home from rule 4. Never a tooltip alone, never one global "not ready".
7. **Evidence.** Any UI SC-00 ships, even a prototype panel, needs real-app screenshots of each action state in rule 3, in Guided and in Expert.
