<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 21 The IPP business-case preset cannot run as applied: indexation, life and terminal value are missing

Status: done

Done in #1893 (babc7da8).
Blocked by: none (12, presets, is done with its PR pending; this is what a user meets after applying it)
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W2 (shots `s24-bc-run`, `s28-bc-result`).
Model: sonnet. Review: GK B (finance).

## Problem

On the IPP template, Business case › Apply preset (IPP) adds 6 lines with empty prices, as designed. After prices are typed, three refusals remain: "Indexation needed" ×2 and "Life needed" for capex. After those are filled, NPV, IRR and payback all read "Not established: needs a terminal-value method that can price Plant capex", and EBITDA margin shows 100 %.

## User-visible impact

The preset promises a business case, and a user who follows it ends with no NPV or IRR, and no hint which setting would give one.

## Acceptance criteria

- [ ] Applying the IPP preset sets the indexation, the capex life and a terminal-value method that can price plant capex. Each default carries its source, or reads "assumed", per the preset rules in 12.
- [ ] After a user fills only the prices, Run gives NPV, IRR and payback (a test on the preset with prices filled).
- [ ] When a terminal-value method is missing, the refusal names the setting and links to it.
- [ ] EBITDA margin is not shown as 100 % when opex lines are zero because they are unset; it reads "not established" with why.
- [ ] End to end in the real app: IPP template › run › Investment case › Business case › Apply preset › type the PPA and capex prices › Run. NPV, IRR and payback show.

## Likely files

`pypsa-gui/backend/project_templates/business_case_presets/eh_ipp_hybrid.json`, `pypsa-gui/backend/services/business_case/presets.py`, `pypsa-gui/frontend/src/pages/results/investment/BusinessCaseLines.tsx`.

## Comments
