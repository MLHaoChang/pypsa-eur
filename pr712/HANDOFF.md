<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# PR #712 continuation / review handoff

Source PR: https://github.com/MLHaoChang/pypsa-eur/pull/712
Branch: claude/asset-footprint-parameters
Exact implementation head: c117493f494a74d7d19d9a053286cb7f347730e9
Fresh actual master: aba252d2384f55116bbd15516a5b025951ba265e

## Implemented

Optional project equipment envelopes (width/depth/optional height), shared Properties/Site editor, Guided/Expert parity, explicit user/estimated provenance, simple 3D proxies and consistent packing/fit/land values, fixed dimensions under sizing changes, server/client validation and existing saved-project guards. No solver inputs, new route/tool or dependency. Plain/cascade deletion, native/frontend rename, dirty dimension/reset preservation and malformed optional metadata have regression coverage. Campus/building/AI/Blender/photo work remains separate PR #711.

## Evidence

- Local: 884 frontend tests pass (89 files); 357 backend tests pass plus one existing empty-parameter skip; TypeScript passes.
- Shared-component browser: Guided/Expert save/reload/reset/read-only checks pass, zero page errors, synthetic sites API. Screenshots and browser.json here. No full-application E2E or real-3D packaged-device performance claim.
- Independent advisory Spec and Standards axes: zero remaining bounded blockers at exact implementation head. These are not official reviewer OKs.
- Remote Test run40120389775 succeeds. All four actual backend shard Run backend tests steps, actual pilot evaluation and QA drivers, aggregate GUI backend, Unit and actual frontend type-check/tests complete successfully. See remote-test-jobs.json.
- CodeQL40120389740 and release guard40120389725 succeed. Validator Bot40120389718 is an expected skip. Gridspine/integration no-change paths do not certify engine execution.
- Fresh premerge exits0: exact head/base above, clean, no overlaps, 17 check runs success/skipped. Semantic/template checks correctly not required by changed paths; the three semantic suites also passed locally. See premerge.log.

## Next action

Owner/coordinator arranges official UX review and any applicable GK review of the exact head. No official GK/UX verdict is posted at last inspection. Coordinator retains merge authority; do not merge from this handoff. Refresh metadata/master/checks and run fresh premerge immediately before reporting/merging. A push requires current-head CI and renewed applicable review. Do not rebase, amend or force-push.

No paid/model/media/weather calls, engine installation, reviewer contact, deployment or merge occurred. Do not start unrelated implementation from this continuation. Existing last-writer sidecar behavior is not a CAS/generation-identity guarantee. Native undo does not restore removed footprint metadata. New building polygon editing, native shunt/STATCOM 3D rendering, sourced default expansion and broader 3D workflow remain unimplemented here.
