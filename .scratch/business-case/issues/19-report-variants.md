<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 19 Report variants: top N by NPV, greatest IRR, one per participant (K9 / C4)

Status: ready-for-agent; option 1 (top N by NPV, greatest IRR) built on branch claude/report-variants-npv-irr (PR open); left: per-participant variant (waits for site-commercial-ux/04), and the chat report tool taking a `variant` (route `GET /api/projects/{name}/report-variants` has no chat tool yet)
Owner: BC19-NPV-1.
Blocked by: none for option 1 (BC19-NPV-1); the per-participant variant waits for `site-commercial-ux/04` (party board, still ready-for-agent and unbuilt)
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

## Audit (2026-11-25)

- 2026-11-25 audit: no 'Top 3 by NPV' or 'Greatest IRR' variant in backend or frontend/src; no PR named.

## Audit (2026-11-28)

- 2026-11-28 audit: grep for 'Top 3 by NPV' and 'Greatest IRR' in backend and frontend/src finds nothing; no PR named. The owner decision BC19-NPV-1 picked option 1, so the first slice reads the existing option comparison and needs nothing else. Left: all of option 1's acceptance criteria. Split the per-participant report off as its own issue when `site-commercial-ux/04` starts. Model: sonnet, review UX + GK B.
