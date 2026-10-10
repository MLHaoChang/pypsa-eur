<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Assistant implementation session handoff

Status: design approved; implementation starting with phase A.
Date: 2026-10-10.

Read [spec](spec.md), [coverage](application-coverage.md), [test plan](test-plan.md),
[implementation plan](../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md)
and backend/harness/README.md. Design approval is not production certification.

## Starting state

- Publication branch docs/whole-application-assistant starts from master 3fd3c32a.
  It contains reviewed documentation only.
- Existing dictation remains in separate open draft
  [PR 106](https://github.com/MLHaoChang/pypsa-eur/pull/106). Preserve it; integrate
  this prerequisite later without claiming its local baseline is already on master.
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
