<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 Section 7 does not yet hold every commercial input: the participants designer still edits from Results (K1)

Status: needs-triage
Blocked by: none (#533, W3b-1, moved the investment inputs out of Results)
Design: `.scratch/project-workflow/commercial-finance.md` §2, §4 and §7 row K1; design.md §2.1 row 7 ("Results edits none"). Owner decisions: D5 (Package C order).
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), design map.
Model: opus (the shell carries the `lp_input` / `commercial` tags). Review: UX + GK B.

## Problem

The design puts every commercial input in section 7, in five blocks with Guided key-parameter cards and the `commercial` scope flag. On master, `pages/results/investment/ParticipantsDesigner.tsx` still edits participants from under Results. Section 7 has no five-block shell, and no K-slice has an issue file. The slice plan exists only as the table in commercial-finance.md §7.

## User-visible impact

A user edits the commercial set-up in two places, one of them a results page. This is the jump away and back the design removes.

## Question for the owner

Which commercial slice opens next?
1. **(Recommended) K1 now**: the section-7 shell, the participants designer moved in, and the field tags declared. One Opus package, and it unblocks K2–K9.
2. K4 party board first, built in today's places, with K1 later. Faster to a visible feature, but it moves twice.
3. Hold all K-slices until template-e2e and guided-results are done.

## Acceptance criteria (option 1)

- [ ] Section 7 shows the five blocks of commercial-finance.md §2. The participants designer renders there, and Results shows it read only with "Edit in section 7 ›".
- [ ] Every commercial field carries its `lp_input` / `commercial` tag, and a test fails on an untagged field.
- [ ] Guided section 7 cards show the key parameters (§4), and Guided and Expert agree on each.
- [ ] End to end in the real app: on the IPP template, edit a participant's share in section 7 (Guided), run, read it in Results › Investment case, and find no edit control under Results.

## Likely files

`pypsa-gui/frontend/src/pages/results/investment/ParticipantsDesigner.tsx`, the section-7 view under `pypsa-gui/frontend/src/pages/hubDesign/`, `pypsa-gui/frontend/src/layout/flowSections.ts`, `pypsa-gui/backend/services/flow_scope.py`.

## Comments
