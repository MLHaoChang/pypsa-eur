<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — First-stage capex is paid unescalated; later stages are escalated

Status: ready-for-agent
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

## Comments

**GK B 65** (relayed by GK B2, 2026-11-17):

GK B 65 ruling: defect. Evidence:
- cashflow.py:298-303 puts the initial capex at overnight × (1+contingency) × phasing, with no escalation.
- Later stages (:304-313) and replacements (:329) escalate by `capex` from tl.base_year.
- Operating lines escalate from their money year, so the model is nominal.
- A base_year before the construction years (which test_f4 allows) therefore pays base-year money in the construction years, inconsistent with stage 2 in the same case. With base 2025, close 2029 and 3% capex escalation, first-stage capex is understated by about 12.6%.

Fix conditions:
- F1: escalate each construction-year share by (1+r_capex)**(y−base_year). Require r_capex when any construction year ≠ base_year (escalation_missing:capex, as for stages). When y == base_year, keep the result byte-identical.
- F2: a pin test that cases whose construction years equal base_year are unchanged; tests with base < close at 0%, 3% and a missing rate.
- F3: a release note that IC capex and LCOE/NPV move for cases with base_year before close.
- F4 (corrected): if any stored or cached IC or finance result is keyed by input digest only, the fix would leave old numbers served as current. Bump the finance engine or method version that stored results carry (or invalidate those caches), so a result computed before the fix reads stale or recomputes. Pin that with a test.

No owner question unless someone argues overnight cost is stated in close-year money; if so, send it to GK B with the source.

Note: it stays out of PR1 (IB-C); the coordinator assigns the fix. eh_datacenter has base 2025 and close 2029, so PR1's D4 pin moves when the fix lands; whichever PR lands second updates the pin.
