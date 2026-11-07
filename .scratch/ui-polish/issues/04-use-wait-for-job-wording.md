<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# "Use wait for job" wording

Status: ready-for-agent
Type: wording
Found by: UX (9) review items; owner list DOCS-1.

## Problem

A refusal names an action in tool-argument terms ("use wait for job"). A refusal names only actions its real caller can take (review-ux.md, #951). Details: ask the coordinator.

## Where to look

Chat/outcome text naming the `wait for job` action; start at `pypsa-gui/frontend/src/components/ChatPanel.toolOutcome.test.tsx`.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `ChatPanel.toolOutcome.test.tsx` and the outcome text naming `wait_for_job`.
- Blocked by: none.
- Model tier: sonnet.
- Gated: UX (12).
- Size: S.
- Name only actions the real caller can take (review-ux.md, #951).
