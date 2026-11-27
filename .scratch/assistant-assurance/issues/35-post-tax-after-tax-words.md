<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# One word for "post-tax" and "after tax" through figure_words.json

Status: needs-triage
Type: task
From: UX (32)'s review of #2077 (AA 28), follow-up, not blocking.

## Problem

The investment figures name the same tax basis two ways. `services/figure_words.json` rules
"Project NPV at the WACC, after tax (unlevered)" and "…, before tax (unlevered)", while the
Investment tab's other headlines (`frontend/src/pages/results/investment/financeModel.ts`
`HEADLINES`) say "Unlevered project IRR, post-tax", "Equity IRR, post-tax" and
"Equity NPV, post-tax (at cost of equity)". Since #2077 the chat's checked `investment-case/2`
answer prints both sets, so one card reads "after tax" and "post-tax" side by side.

## Scope

- Pick one word (owner or UX call) and move the IRR and equity NPV headlines into
  `figure_words.json` beside `project_npv`, read through `figureName(...)` on the tab.
- The checked-answer labels follow through `services.figure_words.name(...)`. A label is part of
  a metric definition, so under AA-V1-CHANGE-1 a changed `investment-case/2` label needs a
  `/3` beside it, unless /2 has not shipped.
- `financeModel.contractLabels.test.ts` and `test_figure_words.py` keep the parity pinned.
