<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# "Use wait for job" wording

Status: needs-triage
Type: wording
Found by: UX (9) review items; owner list DOCS-1.

## Problem

A refusal names an action in tool-argument terms ("use wait for job"). A refusal names only actions its real caller can take (review-ux.md, #951). Details: ask the coordinator.

## Where to look

Chat/outcome text naming the `wait for job` action; start at `pypsa-gui/frontend/src/components/ChatPanel.toolOutcome.test.tsx`.
