<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 20 The investment case has no sensitivities, and its "Uncertainty" chip shows phase codes

Status: needs-triage
Blocked by: none
Design: `.scratch/project-workflow/integration-sensitivities.md` §5. Related: `project-workflow/issues/S5-sensitivity-case-banner.md` (scenario cases).
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W2.
Model: opus (finance). Review: GK B + UX.

## Problem

The investment case's status chip reads "Uncertainty: skipped", with the tooltip "P6: uncertainty is not modelled in P4": internal phase codes (RAW-NAMES-1). There is no sensitivity view: `pages/results/TornadoChart.tsx` is used only by `decision/Robust.tsx` and tests.

## User-visible impact

A lender-style question ("what if the PPA price is 10 % lower, or capex 15 % higher?") cannot be answered in the investment case.

## Question for the owner

1. **(Recommended) A finance-only tornado**: ± steps on PPA price, capex, opex, discount rate and degradation, re-pricing the cash flows without a re-solve. Shown as a tornado of NPV and IRR, labelled "dispatch held fixed".
2. A tornado with a re-solve per step (true sensitivities; minutes per case, through the run queue).
3. Not now; replace the chip text with plain words only.

## Acceptance criteria (option 1)

- [ ] The chip says in plain words what is and is not modelled; no phase codes (a RAW-NAMES-1 guard test).
- [ ] The investment case shows a tornado for the five drivers, with the step sizes stated. The base equals the case's NPV, and each bar equals a hand-oracle re-pricing in a test.
- [ ] End to end in the real app: IPP template › run › Run the investment case › Sensitivities shows the tornado, and PPA price −10 % moves NPV by the tested amount.

## Likely files

`pypsa-gui/frontend/src/pages/results/investment/InvestmentCaseView.tsx`, `pypsa-gui/frontend/src/pages/results/TornadoChart.tsx`, the finance service's uncertainty stage in `pypsa-gui/backend/services/`.

## Comments
