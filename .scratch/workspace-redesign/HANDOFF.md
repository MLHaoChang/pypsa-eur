# GridSpine workspace redesign — implementation handoff

Status: ready-for-agent

Owner requested publication of these design specifications on 2026-10-20. This is an implementation handoff, not an application release or scientific approval.

## Start here

1. Read the repository's current `AGENTS.md`, steward skill and chat harness README. Reassess fresh master; this documentation branch starts at `fed26ad438d082c410e42a5c25a567cd738bc62b`.
2. Open [GridSpine-modern.html](GridSpine-modern.html) in a browser. All assets are embedded. [Overview screenshot](previews/home-modern.png), [three-section builder](previews/builder-dark.png) and [staged inputs](previews/staged-inputs.png) show the key decisions.
3. Read [spec.md](spec.md) for the behavior, implementation sequence and acceptance matrix. Start native backend classification with [backend-separability-assessment.md](backend-separability-assessment.md) before enabling any composed execution.
4. Use [README.md](README.md) for regeneration and [verification-notes.md](verification-notes.md)/[browser-checks.json](browser-checks.json) for bounded preview evidence. Editable sources, original SVG artwork and licensed reference assets are included.

## Owner decisions to preserve

- GridSpine naming, cool graphite/navy default, cool light alternative, bright red actions, image-led industry cards and one bottom-right dimensional companion. Reference artwork is original; the supplied meeting screenshot is not included or copied.
- Direct guided entry and explicit Resume, with a generic/own-model option. No question-first intake or redundant sidebar Assistant.
- Three work sections with nine choices. Capacity includes dispatch/economics; grid-code assessment includes the load-flow work it actually needs. Process detail and reliability remain optional additions; targets belong inside the reliability stage.
- User-editable recommendations, per-section partial state, Select all, Clear and explicit Use example preset. Example changes preserve an edited scope; Resume preserves the saved composition. Expert and the same companion remain available throughout.
- One integrated-planning card each for data center, island microgrid and hydrogen hub. Click/right-click enters setup directly; the user chooses planning years, with optional advanced investment dates. The six native template variants are implementation details, not separate public choices. Use canonical native IDs and actual period-specific sources. Staged periods are 2027/2030/2035; data-center IT is 12/24/36 MW, distinct from the 50 MW single-period campus peak.
- Ammonia (#418) is owner-reported in development and unmerged; mining follows it. Local `concept_*` IDs are preview-only. Do not pass them to project creation or execution as native template identifiers.
- Shared stress-week inputs and synthetic office/two-shift profiles belong in Model & inputs. Synthetic profiles are made up, not measured; native annual-MWh normalization and provenance require implementation/tests. Legacy IEEE/Belgian/tutorial grids leave this focused gallery while native templates/imports/existing projects remain available.

## Companion update requested for application implementation

Add the three-mode hover/focus/tap menu and distinct greeting/live-text animations described in the spec. Reuse native compose/reviewed-dictation/in-page-chat handlers. Continuous voice remains unavailable; no audio/model calls are part of visual validation. This application change is a separate runtime PR from this design-publication package.

## Ordered implementation slices

| Order | Deliverable | Exit evidence |
| --- | --- | --- |
| 0 | Native separability/source/dependency matrix for all nine activities | Each entry is supported, inseparably bundled, unsupported or unknown, with native tests and evidence. Unknown entries remain non-runnable. |
| 1 | Workspace overview and single-companion consolidation | Native login/deep-link/Resume/role behavior; no duplicated assistant or accidental project/job creation. |
| 2 | Expert navigation regrouping | Every existing destination mapped, permissions/readiness retained, Guided/Expert state parity. |
| 3 | Versioned project-owned composition and guided shell | Explicit editable presets/bulk selection, per-project drafts, dependency validation, source/mode round-trip and selected-only screens. |
| 4 | Capacity-only vertical slice | Ordinary native approvals/locks and exact receipts; sizing, dispatch and economics from real evidence; no unselected network work or reliability claim. |
| 5 | Optional network/reliability adapters | Eligible existing-source branches, required internal operations, stale/partial/cancelled/denied/uncertain effects and compatible evidence reuse. |
| 6 | Staged/template/scenario integration and bundled detail parity | Native IDs, period costs/weights/horizon, variant isolation; stress/shape normalization and provenance; no invented ammonia/mining availability. |
| 7 | Supported controls/islanding/dynamic adapters | Supported native schemas/simulators only; screening distinguished from full time-domain execution or compliance certification. |
| 8 | Authenticated native owner journeys and release checks | Applicable frontend/backend/scientific oracles, accessibility and required source/UX reviews/current-head CI before production merge. |

The spec's implementation sequence and acceptance matrix govern these slices; the preview does not certify that every checkbox combination is independently executable. Current backend/source classification comes first. Do not silently add solves to satisfy a UI sequence, weaken approval rules or turn unavailable results into success.

## Scope and evidence limits

This PR contains design/specification documents, a local interactive prototype, original visuals, screenshots and their build/verification tools. It changes no application runtime, template data, prompts, harness policy or production activation. The separate dirty branding implementation is intentionally excluded.

The earlier preview checkpoint passed 14 rendered-browser journey groups with 16 screenshots; see the current browser-checks.json for the revised interaction/horizon verification and zero external asset/API/model requests. Publication is followed by a clean-package regeneration/browser check. These are design journeys, not native scientific E2E. Remote CI must be described from actual current-head results, never inferred from these local checks.

Owner-supplied scenario quantities and issue development status need reconciliation against current master before integration. Independent domain review and real-use calibration remain pending by owner choice. No paid model/media calls, reviewer contact, deployment or full-domain-coverage claim is part of this handoff publication.
