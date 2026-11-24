<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 Guided section 10's headline shows no product cost

Status: needs-triage
Blocked by: template-e2e 21 (#1927) merging (it owns `headline.ts` and `hubDesign/cards/ResultsCard.tsx`). Related: 09, 10.

## Problem

The 09 acceptance criterion "Guided section 10's headline for these templates shows the product cost with its unit" was not done in 09: the headline strip has no per-product card (a backend card in `headline_kpis.py` plus a `HEADLINE_WORDS` entry), and the files it touches belong to template-e2e 21.

## Acceptance criteria

- [ ] For a hydrogen project the headline shows the hydrogen cost in EUR/kg, read from the lcoh result (same figure as the Products tab and the report). Ammonia follows issue 10.

## Comments

- 2026-11-24 (owner decision AC3-FOLD-1): also show the H2 hub's hydrogen output in kg/h and its cost in EUR/kg here. This was guided-results 09 AC3, which could not hold in #1939 because the H2 hub template has no product bus.
