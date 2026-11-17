<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — First-stage capex is paid unescalated; later stages are escalated

Status: needs-triage
Found by: IB-C (WALK-IMPROVE-1 b), while hand-checking the data-centre template's case. Triage: GK B2 with GK B.

## What

`services/finance/cashflow.py` (~lines 298-314): the first stage's capex is `total × (1 + contingency)` spread over `capex_phasing` in the construction years, with no escalation. A later stage's capex is `overnight × (1 + contingency) × (1 + r_capex) ** (year − base_year)`.

## Hand example (eh_datacenter as shipped, PR "finance defaults on every template")

- Overnight cost of the one candidate the plan builds (`genset_new`): 21 317 813 EUR in 2025 money (`currency_year` 2025, the pack's money year).
- Financial close 2029-01-01, capex phasing [1.0], escalation capex 2 %/yr.
- Expected if the overnight cost is in base-year money and is paid at close: 21 317 813 × 1.02^4 = 23 075 086 EUR.
- Current: 21 317 813 EUR (`op.capex.sum()`).
- Gap: 1 757 273 EUR, which also changes the AfA basis (1 121 990 → 1 214 478 EUR a year).

## Question

Is first-stage capex meant to be quoted in the money of the close (then the `currency_year` of an overnight cost from a pack is ignored for stage 1), or should it escalate from `base_year` like a later stage?
