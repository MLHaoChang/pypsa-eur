<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Chat tool row shows tool_not_offered raw

Status: needs-triage
Type: task
Source: UX 35 review of #2151 (issue 05)

## Problem

Pre-existing: the chat tool row reads "update_component — tool_not_offered: …". It should show plain words through `TOOL_OUTCOME_DENIED_DETAIL_WORDS` (UX rule #1213).

## Acceptance criteria

- [ ] A not-offered tool row shows plain words, not the error kind.
- [ ] A shot in Guided and Expert.
