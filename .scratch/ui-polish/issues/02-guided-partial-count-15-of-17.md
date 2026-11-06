<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Guided partial count reads "15 of 17 rows"

Status: needs-triage
Type: wording
Found by: AA-04 (`assistant-assurance/issues/04-action-and-job-outcomes.md`) review; owner list DOCS-1.

## Problem

A partial result reads "15 of 17 rows", which does not say what happened to the other two. Say what the two were (not changed, refused, already present) in words. Details: ask the coordinator.

## Where to look

Guided chat/outcome line that counts changed rows against attempted rows; start at `pypsa-gui/frontend/src/components/ChatPanel.toolOutcome.test.tsx` and the component it tests.
