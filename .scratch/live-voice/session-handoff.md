<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Assistant implementation session handoff

Status: design published to master; phase A navigation/inventory slice implemented
and locally verified. Whole-application/live-voice implementation still pending.
Date: 2026-10-10.

Read [spec](spec.md), [coverage](application-coverage.md), [test plan](test-plan.md),
[implementation plan](../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md)
and backend/harness/README.md. Design approval is not production certification.

## Starting state

- Publication branch docs/whole-application-assistant starts from master 3fd3c32a.
  It contains reviewed documentation only.
- Existing dictation was, at publication, a separate open draft
  [PR 106](https://github.com/MLHaoChang/pypsa-eur/pull/106). Preserve it; integrate
  this prerequisite later without claiming its local baseline is already on master.
  Superseded 2026-10-10: PR 106 merged as `d43809aed`. It is reviewed dictation,
  not continuous live voice. See the companion amendment below.
- Inventory: 220 tools, 14 result tabs, 17 panels. Behavioral/editor mapping is
  pending. Known first fix: adequacy/FMEA navigation tool enum gaps.
- First phase: issue 08; write/run failing navigation tests and exhaustive
  discovery/offering/inventory contracts, then minimal provider-neutral changes.
- Remaining order: 09/02 durable shared path; 03 and 06/07 UI/evidence; 11 jobs;
  10 experiments; 01 speech; 04 recovery; 05 comprehensive release.
- Initial deployment: one authoritative app worker, owner-pinned hosted bridge,
  LiveKit Build, one user/up to ten conversation hours/month. Hosted credentials,
  model/API deprecation, resources/device/accuracy/latency remain release gates.
- No paid calls/subscription started for design/publication. Preserve existing
  usage ledger/budget and never print credentials or replay completed paid cases.
- Previous failed environment and existing worktrees remain untouched.

## Resume procedure

Start a fresh implementation branch/worktree from master after publication.
Current backend venv /workspace/chat-compat-venv is ephemeral; reproduce setup
with repository guidance in a fresh environment. Frontend uses npm/Vitest/TypeScript.
Append publication PR/commit, implementation branch, red/green commands/results,
covered cases, next runnable slice and remaining blockers below after each phase.

## 2026-10-10 session — first typed coverage slice

Publication completed: [PR 107](https://github.com/MLHaoChang/pypsa-eur/pull/107)
merged into master as `19e6e5fdfedff6b706212d8bf23f7c0f937c0879`.
Implementation branch: `feat/assistant-capability-coverage`, fresh worktree at
`/workspace/assistant-implementation`; the dictation branch and earlier environment
were not modified. No paid APIs, subscription changes or test credentials needed.
Implementation publication: [PR 108](https://github.com/MLHaoChang/pypsa-eur/pull/108),
with code/test commit `2e4c919c830786a5cf57e40ee2824a4fa92067df`.
Local test evidence is recorded below; check the PR for remote merge/check status.

TDD evidence:

| Cases | Executable seam/tests | Observed red | Verified green |
| --- | --- | --- | --- |
| A-U2 | `backend/tests/test_assistant_navigation_coverage.py` | 12 failures: missing two tabs/five panels and malformed navigation accepted; three later type cases also failed before fixing | 43 cases: every actual tab/panel, legacy aliases and invalid inputs |
| A-I2 | `backend/tests/test_assistant_navigation_provider_parity.py` | All 8 transport cases failed at unavailable schema targets | Anthropic, official OpenAI, compatible remote/local adapters; 124 actual navigation calls with fragmented arguments, IDs, continuation and UI events |
| A-I1 | `backend/tests/test_assistant_tool_reachability.py` | Existing reachability passed after correcting fixture activation/tool name; no provider selection change needed | 7 cases; 1,320 synthetic model requests cover every tool across network/study contexts and three profiles, plus unbound study ineligibility; offering is not handler certification |
| A-U1/A-C1 source portion | `backend/tests/test_assistant_inventory.py` | CLI absent; later dependency-free/multiline/unknown-target cases failed before correction | 12 cases; standard-library CLI, stale schema/route/toolset/target/missing-tool rejection, mixed quotes/multiline targets and refusal to silently omit unrecognized IDs |
| A-I2 UI | `frontend/src/components/ChatPanel.applicationNavigation.test.tsx` | 10 failures for five missing panels in Expert/Guided modes | 62 cases covering panel state, every requested tab and compare-rail route |

Broad regression command: `/workspace/chat-compat-venv/bin/python -m pytest -q`
with the four new backend modules plus schema-panels/schema-match/endpoint-map/
dispatch/OpenAI-compatibility/profile-binding/stream-ownership/workflow-tools/
workflow-provider-parity/layout/frame-contract modules. Result: **745 passed,
2 opt-in skips** (paid OpenAI probe and deliberate frame recording), zero failures.
After strengthening the reader,
its final 12-case suite passed separately; three added reader cases make the
combined unique tested set 748 passed plus the same two skips. JUnit evidence:
`/tmp/assistant-phase-a-backend.xml`, `/tmp/assistant-phase-a-inventory.xml`.

Full frontend: `npm test -- --maxWorkers=2 --reporter=default --reporter=junit
--outputFile=/tmp/assistant-phase-a-frontend.xml` — **3,496 passed in 295 files**.
Production `npm run build` passed; existing large-bundle/mixed-import warnings
remain. Backend emitted existing scientific/FastAPI warnings; frontend emitted
existing incomplete-query fixture warnings, with no test failures.

`python -S pypsa-gui/backend/smoke/assistant_inventory.py --check` passes without
site packages. A separate runtime parity check matched all 220 imported schemas,
required inputs/routes/safety/toolsets against the dependency-free source reader.
Inventory CI watches both tool/UI definitions and imported schema-cap sources.

Next runnable slice: finish issue 08's actual GUI control/parameter and per-family
behavioral-fixture matrix, then implement issue 09's durable parent-thread/owner
contract with B-U/B-I/B-C tests written first. Shared bridge extraction can proceed
alongside those prerequisites; integrated acceptance depends on completing them.
All runtime tool fixtures, acknowledged view/run/source/window mapping, durable
conversation, nonblocking adapters, experiments, speech and device/latency gates
remain pending. Do not mark issue 08 or the entire assistant complete.

## 2026-10-10 design amendment — floating companion

PRs 107 and 108 stay the hosted-voice architecture and the navigation/inventory
slice. They do not ship a live voice agent. The closed assistant in the product
is still a reserved column, and its microphone button only expands that column.

The companion decision is now in [spec.md](spec.md) under Companion launcher,
[issue 12](issues/12-companion-launcher.md), and
[the companion plan](../../docs/superpowers/plans/2026-10-10-assistant-companion.md).

- Closed state: a floating character over the canvas, with Compose and Speak.
  It does not reserve the 40px column and it is not a slide-panel tab.
- Compose opens the existing dock and focuses the typed composer.
- Speak opens that dock and the reviewed dictation panel from PR 106. It does
  not start the microphone, a recording, or a LiveKit session. A transcript is
  inserted only after the user accepts it.
- Live conversation stays an explicit control inside the open dock (issue 03).
  Idle motion must not look like listening.
- The profile chip opens the existing model select. Another agent is another
  profile on the shared harness, not a second planner.
- Kimi is already the `moonshot` preset on the OpenAI-compatible wire. Grok
  joins that wire as the `xai` preset. Neither needs a new provider class.
  Cursor's agent APIs are not a chat provider for this dock.

Issue 12 can land before media. The next coverage slice is unchanged: finish
issue 08's GUI control/parameter matrix, then issue 09's durable thread.
