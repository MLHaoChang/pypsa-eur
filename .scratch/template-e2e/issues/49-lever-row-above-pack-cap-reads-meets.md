<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 49 A lever row above the pack's cap reads only "meets"

Status: needs-triage
Source: template-e2e 22 S3 shots (PR #2095), UX 32 asked for it to be filed.
Severity: misleads

## What

On the data-centre template (weak_flexible, 40 MW import rating), the Expert Levers table shows import capacity 50 MW at € 31.8 m, the cheapest row, with Note "meets". The engine marks that row `exceeds_pack_cap: true` with `autonomy_note` "above the connection's current 40 MW rating — a plan the pack does not permit" (`levers.py`, compare_lever_scenarios), but `EhReferenceDesignPanel` shows only meets/miss in the Note column. A user reads the cheapest row as a valid option.

## Acceptance

- A row with `exceeds_pack_cap` says in plain words that the pack does not permit it, in the table's Note column and in its CSV.
- The cheapest-option readers (the review's `levers_best_option` finding, `cheaperDuration`) do not offer a row the pack does not permit, or say that it is not permitted.
