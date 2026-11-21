<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 No party board: value flows between parties have no editor (K4–K7)

Status: needs-triage
Blocked by: 03
Design: `.scratch/project-workflow/commercial-finance.md` §7 rows K4–K7; design.md:318–322. Owner decisions: D5 (C5 + C6, then C7, C8, C9).
Source: gap review 2026-11-21, design map.
Model: opus (K4, gated finance), then sonnet (K5–K7). Review: GK B (finance) + UX.

## Problem

The design makes the party board the only `ValueFlowConfig` editor: party tags on contracts, per-stream splits, a per-party "my view" against BAU (K5), sector party templates (data centre, e-mobility, C&I, IPP, K6) and offsite PPAs on the board (K7). None is built or filed. `ipp-renewables-bess/01` covers only the PPA share.

## User-visible impact

A multi-party site (landlord, tenant, IPP, offtaker) cannot be set up, so the business case shows one party's view only.

## Question for the owner

1. **(Recommended) K4 then K5 as two packages after K1**, with K6 and K7 filed when K5 merges.
2. K4–K7 as one planned stack, opened together after K1.
3. Not on the roadmap now.

## Acceptance criteria (K4)

- [ ] A board in section 7 lists parties and the streams between them. Each stream's split is editable and saved into `ValueFlowConfig`, which no other screen edits.
- [ ] The investment case reads the board's splits, with a hand-oracle test on a two-party case.
- [ ] End to end in the real app: on the data-centre template, add a landlord and a tenant, split the grid bill 70/30, run, and see both parties' NPV in Results.

## Likely files

new `pypsa-gui/frontend/src/pages/hubDesign/PartyBoard.tsx`, `pypsa-gui/backend/services/value_flows*.py`, `pypsa-gui/backend/routers/results.py` (value-flow routes).

## Comments
