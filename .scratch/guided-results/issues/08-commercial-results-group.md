<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08 Results › Commercial & finance holds only the investment case: add Bill and Contracts & revenue (R5)

Status: needs-triage
Blocked by: none (#533 merged); the Parties tab waits on `site-commercial-ux/04` (party board)
Design: `.scratch/project-workflow/card-workspaces.md` §3.5 and §8 row R5. Owner decisions: RES-CARDS, CARD-WS.
Source: gap review 2026-11-21, design map.
Model: sonnet. Review: UX.

## Problem

`CARD_GROUPS.commercial.tabs` (`pages/results/groups.ts`) is only `investment_case`. The design's Bill (the site's energy bill by tariff component), Contracts & revenue (the CFE score's UI, PPA settlement) and Parties tabs do not exist. Their data partly exists in the value-flow and finance services.

## User-visible impact

A user who set tariffs and PPAs in section 7 cannot see the bill or the contract revenue those inputs produce, only the investment case.

## Question for the owner

1. **(Recommended) Bill and Contracts & revenue now, Parties after the party board.** Two Sonnet PRs that read existing services and add no engine change.
2. All four tabs together after `site-commercial-ux/04`, so one package does the whole Commercial group.
3. Defer: the investment case is enough until a commercial template user asks.

## Acceptance criteria (option 1)

- [ ] Bill: per tariff component, per year, in the project currency, with units. It equals the figure the investment case uses for the electricity cost (a test with a hand oracle).
- [ ] Contracts & revenue: per contract (PPA, export), the volume, price and revenue, and the CFE score where the run has one.
- [ ] End to end in the real app: on an IPP or C&I template after a run, Results › Commercial & finance shows Bill and Contracts & revenue, and their totals match the investment case's lines.

## Likely files

`pypsa-gui/frontend/src/pages/results/groups.ts`, new `pypsa-gui/frontend/src/pages/results/BillTab.tsx` and `ContractsRevenueTab.tsx`, the value-flow read routes in `pypsa-gui/backend/routers/results.py` (read only).

## Comments
