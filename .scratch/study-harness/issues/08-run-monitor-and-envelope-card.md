<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08 — Run monitor and envelope approval in the companion

Status: ready-for-agent
Type: task
Blocked by: 02, 03
Gated: yes (UX reviewer for placement; GK B for the backend payload)
Model tier: opus
Owner decisions: UX-flow (one entry point), SH-ENVELOPE-1, COP-1, DENSITY-1
Absorbs: LC-23 slice 3 (plan card UI)
Overlaps: site-scene SC-07 (`.scratch/site-scene-generation/issues/08-ai-guided-project-flow.md`, needs-triage); not folded without an owner decision
Design: [design.md](../design.md) §10

## What to build

1. Plan-review Choice card: an **Envelope** section (edits, runs, budgets, expiry, email opt-in) with "Approve and run unattended" / "Approve, step by step" / "Change"; narrowing allowed.
2. Plan card (`AssistantResultCard`): stage rows, the current stage expanded (gate rules pass/fail, loop round n/N, budgets used), "Waiting for you" items (cards, research confirmations), Pause and Stop. No new panel, no second progress line, the card-workspace Next row unchanged.
3. The attention notice opens the companion on the run.
4. The last row links to the report in the Reports panel.

## Acceptance criteria

- [ ] UX OK on placement before build (UX reviewer's verdict recorded in Comments).
- [ ] LC-23 SC-03 and SC-16 pass; Guided and Expert show the same plan card.
- [ ] A waiting card is issued when the user opens the companion, with its TTL starting then.
- [ ] Stop revokes the envelope and cancels the task; the card shows `cancelled`.

## Likely files

`pypsa-gui/frontend/src/components/AssistantResultCard.tsx`, `pypsa-gui/frontend/src/components/ChatPanel.tsx` (plan-review card), `pypsa-gui/backend/services/assistant_tasks.py` (view payload), frontend tests

## Comments
