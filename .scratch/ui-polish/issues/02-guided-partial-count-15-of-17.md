<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Guided partial count reads "15 of 17 rows"

Status: done
Type: wording
Found by: AA-04 (`assistant-assurance/issues/04-action-and-job-outcomes.md`) review; owner list DOCS-1.

## Problem

A partial result reads "15 of 17 rows", which does not say what happened to the other two. Say what the two were (not changed, refused, already present) in words. Details: ask the coordinator.

## Where to look

Guided chat/outcome line that counts changed rows against attempted rows; start at `pypsa-gui/frontend/src/components/ChatPanel.toolOutcome.test.tsx` and the component it tests.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `ChatPanel.toolOutcome.test.tsx` and the outcome-line component it tests.
- Blocked by: none.
- Model tier: sonnet.
- Gated: UX (12).
- Size: S.
- Say in words what happened to the rows not changed (refused / already present / unchanged), from the tool result fields.
