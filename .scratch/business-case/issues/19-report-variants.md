<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 19 Report variants: top N by NPV, greatest IRR, one per participant (K9 / C4)

Status: ready-for-agent
Owner: BC19-NPV-1.
Blocked by: `site-commercial-ux/04` (party board, for the per-participant variant)
Design: `.scratch/project-workflow/commercial-finance.md` §7 row K9; design.md:317.
Source: gap review 2026-11-21, design map.
Model: sonnet. Review: UX + GK B.

## Problem

Reports come in one form per project. The design promises variants: the top N options by NPV, the option with the greatest IRR, and one report per participant. None is filed.

## Question for the owner

1. **(Recommended) Top N by NPV and greatest IRR now**, reading the existing option comparison. Per participant after the party board.
2. All three after the party board.
3. Not on the roadmap.

## Acceptance criteria (option 1)

- [ ] Hand-off › Reports offers "Top 3 by NPV" and "Greatest IRR" when the project has compared options. Each report names its selection rule and the figures, which equal the compare view.
- [ ] End to end in the real app: on the IPP hybrid template with two options run, generate "Top 3 by NPV", open the .docx, and find the options in NPV order.

## Likely files

`pypsa-gui/backend/services/report*` (variant selection), `pypsa-gui/frontend/src/pages/ReportsPanel.tsx`.

## Comments
