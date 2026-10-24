<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# CP-01 — truthful companion mode and conversation state

Status: done (#235, 3175f4f9)
Type: task
Implementation baseline: master `f2b15c5b2caae8fc1259396158355007a670f531`
Branch: `claude/chat-companion-truthful-state`
Scope: [CP-01](../../../docs/superpowers/plans/2026-10-14-chat-companion-implementation.md), CC-01/16 and the applicable CT-01–03/28 cases

## Implemented locally

- The former typed Live action is labelled Chat here; it stays on the current page, focuses the typed input and retains the existing shared utterance-ID/generation admission path. Its existing internal Live event/test IDs remain compatible.
- Conversation response, pending approval/input, queued message and interruption state come from the existing chat store. The navigation walk has its own factual label. Streaming or opening/closing the composer never synthesizes listening or speaking.
- Continuous companion media remains off and Start voice is disabled with a visible explanation; reviewed Dictate and existing browser speech behavior remain separate. This slice adds no capture/playback/session credential endpoint.
- Closing in-page text preserves the stream, pending approvals and actual jobs. Old durable-bind failures cannot change the caption after close/reopen.
- Companion controls remain discoverable without hover; the input/card fits the viewport and reduced-motion behavior remains available. Full job Activity/status and readable ordinary outcomes are later CP-04 and CP-02 scopes.
- The production TypeScript configuration now explicitly includes the JSON fixture imported by the test setup. The omitted-file TS6307 defect reproduced on unchanged implementation baseline before the fix; no type/test requirement was loosened.

## Evidence and remaining gates

Two new public UI-path cases first failed for the missing Chat here action while the four existing admission cases passed. After implementation, the focused dock/launcher/admission/no-key/eviction selection passed 45 cases. This selection includes the real mounted dock and ChatPanel, with only external API/transport seams mocked.

`npm run build` passed after installing the locked dependencies and repairing the fixture file list. The initial shared cache lacked current 3D packages, so its interrupted broad run is not validation evidence.

`CHROMIUM_BIN=/usr/bin/chromium node scripts/smoke-companion-state.mjs` passed native Chromium keyboard, 320px touch/narrow and reduced-motion scenarios using mocked HTTP/SSE. Each scenario exercised two stream requests, zero capture/playback/RTC calls and no abort/confirm/cancel request on closing in-page chat. Artifacts for this local run: `/tmp/companion-browser-3hlNBC`. This is browser behavior evidence, not numerical/backend authority or hosted media proof.

Full locked frontend suite: **5,140 passed across 441 files**, no skips reported, 400.50 seconds. The focused selection overlaps this total and is not added to it. Script syntax, documentation link/coverage/dependency and diff checks pass.

Current master was rechecked at `d2b9f57b24aa755c71486512ab106c6543dc67c5`: incoming GridSpine frequency-cut changes do not overlap the slice. Exact-head remote CI and the final current-master premerge remain pending at publication. Applicable live verification stays pending under [ADR-0002](../../../pypsa-gui/docs/adr/0002-chat-changes-need-a-live-api-probe.md); no fresh paid probe is authorized/executed here. No deployment or production activation occurred. The implementation remains a reviewable draft until applicable verification gates pass.

## Next scope

CP-02: show the actual canonical answer beside the closed companion, with declared checking scope, exact source/receipt identity and authenticated evidence/result/download actions. Preserve strict withholding and avoid inheriting receipts onto unvalidated paraphrases. Do not start continuous media or unattended work to implement that slice.
