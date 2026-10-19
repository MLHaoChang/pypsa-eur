<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Feasibility and source-reviewed gaps

Status: ready-for-agent
Date: 2026-10-18, Europe/Berlin
Reviewed application: master `c75ca5584b873418f10f1bca42f868255d445b7b`

## Verdict and scope

Feasible on this architecture. Reuse the existing provider-neutral harness,
native services, guarded tool dispatch, owned conversations, job/effect receipts
and React companion. The owner describes a live engineering copilot spanning
the whole authorized application. A generic interface discovers capabilities;
domain-specific adapters establish what each result, edit and effect means.
Neither universal browser clicking nor a second conversational model is needed.

The usable typed foundation is implemented. The broad copilot is not complete:
source-verified walkthrough coverage is narrow, generic ordered workflows are
not yet established across families, and continuous hosted voice is disconnected.
The term “Dots-like” describes the requested interaction, not an assertion of
parity with an external product or its private architecture.

## Existing capabilities and precise gaps

| Area | Current source / implemented behavior | Gap and consequence | Priority |
| --- | --- | --- | --- |
| In-page interaction | `AssistantDock.tsx`, `CompanionLauncher.tsx`, mounted `ChatPanel.tsx`: typed Chat here, outcomes, ordinary tool turns, confirmations, queued corrections | Continuous microphone/STT/TTS/WebRTC lifecycle is absent; decorative presence must not imply voice readiness | P1 after typed core |
| Intent and scope | Ordinary requests reach the shared model/harness; `liveWalk.ts` has local tour/project-picker shortcuts | Tour recognition uses word tests and then reads current UI scope; it does not resolve a sensitivity named in the sentence. “Open project X” matches the project-picker shortcut and does not select X. Explicit target resolution must precede these shortcuts | P0 |
| Tool reachability | Catalogue/dispatcher/route/toolset parity; eligible discovery and bounded provider offers; inventory 255 tools / 15 tabs / 19 panels | Offering a tool does not establish every handler, editor or parameter journey. Need one eligibility-aware schema/discovery contract and executed family coverage, including new features | P0 |
| Model/parameter access | Native component/service schemas, asset schema and ordinary read/write tools exist; CP-06 binds arguments and permissions | Entire-model exploration needs bounded schema/value queries, exact entity resolution, units/basis/editability and explicit unsupported fields. Not every application control has proven round-trip coverage | P0 |
| Numerical answers | Native metric services plus bounded checked profiles, receipts, publication and evidence downloads | Current checked support is not all-result/all-domain certification. Generic summaries need per-claim source and check disposition; unavailable slots must not become invented values or zero | P0 |
| Current result navigation | `harness/navigation.py` issues v2 source bindings for current load flow; `liveWalk.ts` currently selects only Expert branch loading for current tours | Other current tabs and networkless study views lack equivalent source-attested completion adapters. A mounted page is not proof that the requested data is displayed | P0 |
| Historical/sensitivity results | `run_evidence.py`, run listing/discussion/export and retained views bind exact owned run IDs; no live-input restoration | Foreground/queue publishers retain bounded summaries, not complete historical load-flow data. `get_loadflow_summary(run_id=…)` refuses without a recorded loadflow section. A sensitivity name needs an explicit project/case/run resolver | P0 |
| View/entity controls | `ui_open_panel`, `ui_select_component`, `ui_open_asset_detail`, snapshot and discussed-run events exist | Reuse them, then add source-bound row/chart/filter selection acknowledgements. Generic snapshot navigation currently opens Results rather than proving the precise selected time | P1 |
| Parameter edits | Native validation, ACL/locks, fixed confirmation arguments, existing old/new/unit preview, restrictive personal rules | Expand adapters for storage cost bases and all supported parameter families. General edit/read-back/postcondition receipts and cross-step stale-source handling need explicit coverage | P0 |
| Ordered commands | Network case execution, two-case budgets/comparison/reconciliation, native asynchronous job adapters and observations exist | They do not certify arbitrary “change these parameters, then run, then explain/export” graphs across all families. A solver must not start after a refused/uncertain/partial edit | P0 |
| Conversation/recovery | Durable parent refs, owner/replica pins, generation/utterance deduplication, budget reserve/settle primitives exist | Wire final speech/media episodes and resource usage into those mechanisms; verify restart/late callback behavior in actual integrated journeys. Do not rebuild completed work by replay | P1 |
| Permissions UX | CP-06a–d: access view, revisioned personal restrictions, fixed approvals and companion summary | Actual Chromium found the pointer center of Check answers covered by the upload controls at default desktop composer height. Keyboard fallback is separate evidence, not a passing pointer case | P0 UX fix |
| Narrow UI | Companion is scroll-bounded and has narrow-view component tests | Actual 320px page overflows during companion review. Background-versus-companion attribution needs a geometry comparison; a source mock test is not actual whole-page evidence | P0 UX investigation |
| Application integration | The current master includes CP-01–06 changes and incoming earlier UX work | Current master has no commits after the reviewed baseline. Seventeen open PRs are pending, not merged capabilities; watch future schema/catalogue/result changes | Continuous |

The control matrix records 170 controls / 14 families and
`full_coverage=not_established`. Counts describe inventory, not correctness.

The owner-requested independent agent review found LC-T01–19 and one journey
per family insufficient for comprehensive domain acceptance. The added
[24 domain suites](domain-coverage.md) separate capacity expansion/multi-period
planning, AC/proxy/security results, probabilistic/chronological/zonal adequacy,
Energy Hub planning, control models and actual time-domain frequency screening.
They require independent physical/numerical oracles and explicit operation
traceability. The application already has single-bus aggregate RK4 frequency
simulation; general voltage/EMT/PLL certification is not established. Standard
MC and zonal/common-mode EH MC are separate existing engines. This source review
adds requirements, not executed domain tests or independent release approval.

The [real-browser/backend acceptance report](../../docs/superpowers/qa/2026-10-18-chat-companion-browser-e2e.md)
records 6 passing and 2 failing scenarios with actual native numerical
read-back. It uses a local scripted model wire, not a hosted model or speech.

## Review of incoming work

The open-PR file sweep found no direct file overlap with CP-06a–d. This is an
integration review, not a full code-quality or domain-validation review of every
pending PR. Source-pinned file/patch records are retained in the workspace at
`/workspace/scratch/github-actions/chat-permissions-closeout-evidence/incoming-pr-review.json`.

| Pending work | Review finding / integration obligation |
| --- | --- |
| [359](https://github.com/MLHaoChang/pypsa-eur/pull/359) repository guide | Documentation/architecture refresh; retain the current live-copilot pointer when older handoffs are retired |
| [360](https://github.com/MLHaoChang/pypsa-eur/pull/360), [362](https://github.com/MLHaoChang/pypsa-eur/pull/362)–[365](https://github.com/MLHaoChang/pypsa-eur/pull/365) renewable profile generation | Current code PRs add engine/synthetic source, cache/downloader, atlite source and provenance presets; they form a stacked branch chain. Router/chat generate/apply integration is planned in Task 8, not released. Future apply needs exact artifact/entity binding, ordinary confirmation and verified write; weather download needs explicit configuration/resource handling. Synthetic provenance must remain visible |
| [338](https://github.com/MLHaoChang/pypsa-eur/pull/338) process-unit schemas | Future schema/unit/port discovery must expose actual new fields and editability; generic access does not add checked process-domain approval |
| [341](https://github.com/MLHaoChang/pypsa-eur/pull/341), [344](https://github.com/MLHaoChang/pypsa-eur/pull/344), [345](https://github.com/MLHaoChang/pypsa-eur/pull/345), [347](https://github.com/MLHaoChang/pypsa-eur/pull/347)–[350](https://github.com/MLHaoChang/pypsa-eur/pull/350) Guided UX | Preserve not-modelled/stale/source labels, whole-plan versus yearly cost basis, ordinary grid-check admission and authenticated report delivery in narration/navigation adapters |
| Draft test PRs [301](https://github.com/MLHaoChang/pypsa-eur/pull/301), [331](https://github.com/MLHaoChang/pypsa-eur/pull/331), [355](https://github.com/MLHaoChang/pypsa-eur/pull/355) | Additional tests remain unmerged and do not certify the reviewed source; do not duplicate or weaken them during later integration |

Re-fetch master before each slice. Integrate incoming source only when actually
merged or separately authorized, with fresh overlap/semantic review and tests.

## Architecture decision

One model chooses among existing authorized tools. The harness remains the sole
admission and execution path for typed, dictated and eventual continuous-voice
commands. Native adapters compute facts and receipts. The UI renders the same
answer/facts used for speech, with exact scope and checking labels.

Preserve the [approved hosted speech architecture](../../docs/superpowers/assessments/2026-10-10-hosted-assistant-architecture-decision.md):
LiveKit Cloud media, Deepgram recognition, hosted OpenAI synthesis, selected
existing reasoning provider. Concrete supported vendor/model contracts, scoped
credentials, provisioning and device measurements remain implementation gates.
Generic HTTP STT/TTS adapter contracts already exist; real media sessions and
vendor integration must be completed rather than counted as done from mocks.

An initial whole-application typed interface is independently useful. Each
released domain still needs its own adapters and journeys. Continuous speech
should follow the same commands; it must not become a separate executor.

## Addendum — 2026-10-19 review against the owner's examples

Rechecked at master `d006f4beaa302b97432c65757762ae180222731a`. Added gaps,
with evidence, are in the [vision review](vision-review-2026-10-19.md):

| Area | Gap | Priority |
| --- | --- | --- |
| Owner journeys | No test plays an owner example end to end; browser runners are local; no live-model evaluation | P0 ([LC-13](issues/13-owner-journey-suite.md)) |
| Deictic targets | No order between utterance, screen focus, recency and asking; no rule for “latest” | P0 ([LC-10](issues/10-focus-and-recency-resolution.md)) |
| Long jobs | `wait_for_job` ≤25 s and no completion turn into chat; edit→run→walk stops mid-way | P0 ([LC-12](issues/12-job-completion-continuation.md)) |
| Stale results | Most reads mark stale only on component-shape change; parameter edits leave old results unlabelled | P0 ([LC-12](issues/12-job-completion-continuation.md)) |
| Walk-through | Local tour narrates fixed blurbs; prompt gives no read per tab | P0 ([LC-11](issues/11-narrated-walkthrough.md)) |
| Navigation reach | Snapshot index not set; selection hidden behind slide panels; no edit card, Guided step or campus sub-section target | P1 ([LC-14](issues/14-navigation-reach.md)) |
| Ordinary-turn ACK | Source-bound ACK issued and answered only in strict turns | P1 ([LC-09](issues/09-vertical-slice-loadflow.md)) |
| Expert edits | Chat writes apply without an old→new card in Expert | P0, owner decided Q3: card in both modes (LC-04) |
