<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# G-b and K continuation instructions

Status: approved owner authorization persists; do not implement before the briefs' prerequisites. Fresh master briefs and current repository rules outrank this saved preparation.

## G-b — after #694 is verified merged

Read the fresh master version of `.scratch/renewable-profiles/outsource/G-b-profile-tuning-frontend.md`, AGENTS, owner decisions, steward, workflow design, UX rules, glossary and dataviz guidance. Create `outsource/profile-tuning-frontend` from actual current master, never from G-a's feature branch. Follow the existing GK-approved plan; no backend changes.

Core test-first slices at the brief's specified UI/API seams:

1. F5: split `generatePending` from the existing generation/apply helper. Confirm the unchanged `generateAndApply` tests keep the current request and batch apply behavior. Tuning generates and waits without applying.
2. F1: add typed schema/compare API methods and the Expert Tune dialog. Schema supplies units, bounds, options and provenance; only changed fields plus validated Advanced overrides are submitted. Preset changes reset stale edits. ERA5-only controls show disabled reasons for synthetic weather. Keep project/source/preset query keys scoped, and avoid stale job/compare responses becoming an apply target.
3. F2: show server-computed peak-week overlay, duration curves, FLH/CF/quantiles/peak/peak-hour and deltas. Use existing style tokens and accessible caption/text alternatives. Display refusals and warnings; never calculate substitute statistics in the browser. Apply uses the existing single apply route once; Close makes no writes.
4. F3: integrate Tune only for Expert rows with coordinates, through the existing editor host. Guided, Generate, defaults, upload and existing Profile Studio behavior remain unchanged.
5. F4: render UTC sampled-window dates or sampled-period count and weather year in the weather bar; full year adds no line. Show unsupported-window explanations honestly.

Review input to settle against the actual backend before code: the G-b brief permits omitting the current side when no current series exists, but `routers/profiles.py::compare_profiles` currently requires both string query parameters a and b. No missing-side response exists: a missing current series is a typed refusal. Document this contract difference in G-b's PR as the brief requires; do not silently edit G-a/backend or fabricate current values/deltas. A possible frontend-only design is a candidate self-comparison used solely to obtain native b statistics, with the entire current side and all deltas hidden and the explicit no-current message; this is a proposal for review, not an approved backend contract change.

Also preserve GK's nonblocking G-b note on #694: the fixed-tilt atlite preset can expose tracking `preset_value: null` and misleading synthetic-only provenance. Do not print that provenance as a factual statement about the atlite fixed-tilt preset. Report and resolve presentation without modifying backend scope.

Run F1–F5, the requested neighboring series/page tests, full frontend suite, TypeScript build, and G-a tuning/compare backend regressions. Capture each prescribed UI state at 1440x900 using synthetic data, including Guided/Expert, untouched/changed/disabled dialog, compare/no-current, Apply receipt and sampled window. Commit the g-prefixed evidence and release fragment. Use two-axis internal review before official review. One PR, body starts `Outsourced task G-b`; state exact red-on-master locations and optional S2/S3/S4 status. No paid calls or ERA5/CDS downloads.

## K — after #694 is verified merged; plan approval before code/tests

Create `outsource/results-plausibility` from new master and open one draft plan PR whose body starts `Outsourced task K`. No implementation or tests until official GK plan OK. Do not contact reviewers; owner/coordinator arranges review. Read the fresh brief, harness contract, LC-20 issue, owner journeys and review-gk first.

Proposed plan details to substantiate in that draft:

- Tool `check_result_plausibility`, Safety: read, no arguments for the first bounded slice; only the current authenticated project's active/loaded network. No implicit run selection or loading another run. `PyPSAService.get_network()` supplies the bound network. Reuse the native dispatch readiness contract (`is_solved` and `dispatch_status == fresh`) rather than process-global status alone, so correctly loaded solved runs work. Unsolved/unavailable results refuse by typed error; topology-stale dispatch does not pass as solved. This is not full fingerprint staleness analysis, which the brief defers.
- Pure rule functions consume native network frames and explicitly injected capture data when needed; no router imports/state access inside arithmetic. The handler obtains the current context's capture using the same seam as existing results endpoints. Never infer absent lost-load capture as zero.
- `lost_load`: reuse `compute_lost_load`'s capture semantics and snapshot weights, bus/carrier identity and stated VoLL. Positive lost load flags; price-at-VoLL needs an actual recorded nonzero VoLL and finite matching price frame. Missing capture/price facts produce named not-evaluated reasons.
- `branch_overload`: line/transformer terminal active power against the relevant fixed or optimized MVA rating; label utilisation as an active-power proxy. Proposed warning threshold is strictly above 100%, a nameable rating limit. Missing/zero/non-finite ratings or frames are not evaluated, never a division-by-zero or silently passed case.
- `curtailment_share`: call `curtailment_frame` unchanged, preserving its carrier/subsidy admission. Weight native curtailment and dispatch by generator snapshot weights, aggregate per admitted carrier and compare curtailed energy / available energy. Proposed screening threshold: 20%, explicitly an advisory screening constant, not grid-code or reliability certification. Empty/unknown excluded carriers and zero availability are disclosed, not invented as clean checks.
- `storage_simultaneous`: inspect actual StorageUnit `p_store` and `p_dispatch` at identical snapshots; both positive flags. No reconstruction of charge/discharge from net power. Missing side is not evaluated.
- `build_at_bound`: extendable generator/StorageUnit p_nom_opt at finite p_nom_max. Propose explicit numerical equality tolerances as named constants with basis, avoiding flags on unbounded maxima or a non-extendable asset. State the exact proposed tolerance for GK before tests/code.
- Each finding carries rule ID, severity, verdict, raw facts and units, threshold/basis, deterministic one-sentence explanation; ordering is deterministic by severity then identity. Partial evaluation is never a blanket “results correct” claim.
- Typed refusals must be registered with surface/why in tool-error-kinds; new catalogue declaration, dispatcher and SERVICE_CALL mapping stay in parity. Regenerate inventory (and matrix only if needed); do not hand-merge JSON. Update only the permitted family note and obsolete prompt sentence/tests.

Tests to put in the plan: independent flagging fixture for each rule; clean and every named not-evaluated case; malformed/finite/weight handling; missing/unsolved and loaded-solved networks; seeded oj-grid-bad with exactly the five defects; real native handler OJ18/OJ19 scripted-provider parity; LC-T19 extra claim withheld through the existing publication gate (confirm the admissible path rather than promising a new checked adapter outside the allowed files); S2.5 removed-name guard, unchanged schema/endpoint/manifest/inventory/matrix/route/layout suites and the three/six current premerge semantic suites. No loop/provider/assurance changes are authorized by K's file list; raise any required additional adapter scope in the plan before code.

K intentionally leaves six further rules, show-tab navigation and sensitivities for follow-up. It claims neither engineering certification nor independent domain approval. Update the issue Progress line, add the exact release fragment, run complete backend validation, merge fresh master as its brief requires and regenerate inventory before review. One draft remains the single PR through implementation and review.
