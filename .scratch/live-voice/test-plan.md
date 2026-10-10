<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Test-first implementation and release plan

Status: required; future-phase runtime tests are not yet implemented or passed.
The accepted scope authorizes public harness/provider, authenticated API,
conversation/job service, browser UI and actual exported-artifact seams. Mock only
external transport/inference, time or interrupted I/O. Define all phases here
before implementation, as the user requests. Before each production slice, create
its executable unit/integration/comprehensive tests, observe a relevant failure,
implement and regress. Do not invent all future APIs, permanently skip red tests
or label this test plan an executed suite. Merge only green required tests.

## Phase A — capability audit and typed navigation (08)

- A-U1: generated inventory preserves every live schema, safety tier, required
  argument, route, toolset and eligibility. UI inventory reveals gaps; it cannot
  claim behavioral/parameter coverage by counting declarations alone.
- A-U2: all actual result-tab IDs, including adequacy/FMEA, are shared navigation
  inputs; unknown IDs fail without side effects.
- A-I1: exact capability queries discover every eligible tool; bounded provider
  offers include discovered tools and retain controls/allowlists. Test unbound,
  network and GridSpine eligibility through normal catalogue selection.
- A-I2: streamed calls use the real navigation handler and frontend state on all
  supported provider paths, including argument fragmentation and continuation.
- A-C1: exhaustive schema/dispatcher/route/discovery/UI contracts, all 14 result
  tabs/17 panels and per-family fixture assignments. Unmapped editors/AR remain
  visible full-release blockers. Use backend coverage/navigation tests, frontend
  chatStore/Results requests and existing provider/workflow parity suites.

## Phase B — durable thread/owner (09)

- B-U1: preserve exact 7.5 MW goal, units, current versus discussed run/source/
  window/asset and task/evidence references through bounded compaction.
- B-U2: parent reservations/final charges/cached usage count once across episodes,
  restart/profile switches; existing child session caps remain enforced.
- B-I1: authorized checkpoint/resume APIs recheck ACL, reject wrong actor/org/
  project/worker and stale generations/approvals without clearing another owner.
- B-I2: compatible profile changes/new episodes and rebind refresh credentials,
  preserve parent facts and reconcile uncertain effects without tool replay.
- B-C1: prolonged mixed-domain conversation spanning rotation/compaction/rebind/
  provider change/restart, with exact references, outcomes and cumulative budgets.

## Phase C — shared bridge/UI/evidence (02, 03, 06, 07)

- C-U1: stable utterance admitted once, one turn lease, bounded pending correction,
  obsolete generations/duplicate IDs cannot repeat effects.
- C-U2: historical evidence retains run/settings/source/freshness; unavailable is
  not zero. LP active-flow proxies are distinct from AC thermal compliance.
- C-I1: typed/transcript admission uses identical ACL/confirmation/tools/provider
  continuation; malformed/fragmented arguments, denial, 401/403/429 and abort.
- C-I2: UI acknowledges exact eligible tab/asset/window/run; navigation preserves
  conversation, cards/downloads/dictation and rejects stale updates.
- C-C1: per-family current/historical follow-ups and real artifacts through mocked
  Claude/OpenAI HTTP streams and ordinary handlers. Newer failed/running jobs must
  not inherit an older successful result. Load flow is one example fixture.

## Phase D — nonblocking jobs/monitoring (11)

- D-U1: every worker adapter exposes stable identity/status/progress/cursor/
  revisions/cancellation; started never means completed.
- D-I1: start slow foreground solve/AC-PF/study, answer another read before
  completion, reject conflicting mutation and verify terminal outcome.
- D-I2: authenticated snapshot/cursor recovery handles gaps/duplicates/reordering,
  unknown/wrong-owner jobs and cancelled waits without changing job truth.
- D-C1: monitor multiple families while navigating/conversing; refresh matching
  result revisions only. Newer user navigation wins; Stop speaking/monitoring and
  Cancel task/job remain distinct. No inference calls just for polling.

## Phase E — typed experiments (10)

- E-U1: scalar/bool/enum/list/nested/unit/file/topology changes follow actual domain
  schemas; invalid values/paths fail and previews have input fingerprints.
- E-I1: preview -> approve -> isolated cases -> run -> monitor -> compatible
  comparison -> export. Test stale preview/denial/seeds/baseline/partial failures.
- E-I2: task steps use ordinary admission and reconcile uncertain effects before
  retry. Completed steps cannot replay; bounds/stop conditions remain enforced.
- E-C1: real small network, commercial/settings, reliability and GridSpine cases,
  then every other mapped domain. Verify numerical outcomes/inputs/provenance and
  export contents, not merely fluent model output.

## Phase F — hosted speech/media (01)

- F-U1: transcript/audio chunk/cancellation contracts preserve final utterance,
  language/numbers/units; reject empty/malformed/oversize output.
- F-I1: short-lived owner/thread/project/generation credentials, HTTPS/gesture,
  denied/stale microphone grants, echo/autoplay/cancellation and complete cleanup.
  Browser never receives a main API key or raw tool dispatch access.
- F-I2: captions -> stable input -> shared harness text -> sentence TTS; no second
  planner or independently paraphrased engineering outcome.
- F-C1: bounded real hosted probe after exact API/language/price/deprecation checks;
  English/German/Mandarin/code switching where supported, technical names,
  "7.5, not 75 MW", noise/pauses/corrections and representative devices.
  A mock or recorded dictation does not pass live-conversation acceptance.

## Phase G — recovery/budgets/performance (04)

- G-U1: actual media/STT/TTS/LLM units, startup minimums, reservations, cached usage,
  interrupted generated speech and unknown final usage counted once.
- G-I1: 401/403/429/quota/loss/permission/background/reconnect/idle failures show
  typed fallback; no approval/write replay. Parent caps cannot reset on rotation.
- G-I2: Build quotas including test use, cold starts, hosted resources/concurrency,
  shutdown and low-budget refusal measured; no automatic paid subscription.
- G-C1: p95 endpointing/STT/model/tools/TTS grounded-answer latency, interruption,
  navigation, accuracy and cost sampled by family/language/device/network and long
  monitored conversation. Acknowledgment timing is reported separately.

## Phase H — comprehensive release (05)

- H-C1: every coverage family has a real journey; every eligible tool/control/
  parameter/view has a passing contract. Include import/create/edit/run/refine/
  compare/report/export, audit/undo, Library/investment, studies/model switching.
- H-C2: prolonged multi-project experiments/monitoring, interruption/restart and
  results updates; check exact evidence, state and delivered artifacts.
- H-C3: relevant backend/frontend/provider/frame/layout regressions and production
  build, then separately bounded live fixtures with sanitized evidence. Cache
  stable prefixes/fixtures, reuse usage ledger and avoid redundant paid calls.
  This plan does not enlarge the existing dollar budget or grant paid upgrades.

## Evidence and handoff

For every case record executable file/node ID, fixture/source revision, observed
red result, green command/result, regression checks, coverage limits and actual
live usage. Update [session handoff](session-handoff.md) after each coherent slice.
Record pending/blocked cases accurately. Use existing pytest/Vitest/browser suites,
isolated project/users and small real solves; preserve user projects and the
previous failed environment. Fresh sessions must recreate dependency setup rather
than treating ephemeral caches as a repository requirement.
