<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 23 Business-case indexation takes a fraction while the field beside it takes %, and 200 %/yr is accepted silently

Status: done
Blocked by: none
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W2.
Severity: wrong figures
Model: sonnet. Review: GK B + UX.

## Problem

In Business case lines, Indexation is a fraction (0.02 = 2 %/yr), but the only hint is in the code (`BusinessCaseLines.tsx` ~168), while the volume factor next to it is in %. Typing 2, meaning 2 %, gives 200 %/yr. It is accepted with no warning, and capacity revenue reached 3.6e17 EUR by 2054.

## Acceptance criteria

- [ ] The indexation field shows and takes % per year, like its neighbour, and stores the fraction. The unit is printed beside the field.
- [ ] The backend line model refuses an indexation outside a stated band (for example −20 to +30 %/yr) with a plain reason. A value outside a softer band (beyond ±10 %/yr) needs confirmation. Tests on both bounds.
- [ ] Existing saved lines keep their values: the stored fraction is unchanged, and only the display changes. A migration test covers this.
- [ ] End to end in the real app: type 2 in Indexation → it reads 2 %/yr and the revenue grows about 2 % a year. Type 200 → refused.

## Likely files

`pypsa-gui/frontend/src/pages/results/investment/BusinessCaseLines.tsx`, the business-case line model and validation in `pypsa-gui/backend/services/business_case/`.

## Comments
