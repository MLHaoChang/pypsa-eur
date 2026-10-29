<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — Prices, curtailment and cost-breakdown services raise instead of returning None

Status: needs-triage
Type: task
Blocked by: 02 (R1, PR #896)
Gated: yes (results data; GK B)
Model tier: sonnet
Size: S

## What to build

`compute_prices` (`services/results/prices.py`), `compute_curtailment` (`services/results/curtailment.py`) and `compute_cost_breakdown` (`services/results/cost_breakdown.py`) catch their own exceptions and return None, the same value they return for "no results". Callers cannot tell a crash from an empty result, so the results workbook (02) lists a crashed view as "no results" instead of an error (02's condition 5).

Make each service raise on an unexpected exception and return None only when there is genuinely nothing to show. Their routes catch the exception and keep answering 204, byte-equal to today. The workbook's builders then let the exception reach About as a reported error.

## Acceptance

- [ ] Each service raises on an internal exception; a test forces one and sees it raised.
- [ ] Each route still answers 204 on that exception, and master's route tests pass unchanged.
- [ ] The workbook lists a crashing prices, curtailment or economics view in About as an error, not "no results"; one test.

## Comments

### 2026-10-29 — origin

GK B ruled option A on 02's condition 5: R1 leaves these services unchanged, and this ticket follows up.
