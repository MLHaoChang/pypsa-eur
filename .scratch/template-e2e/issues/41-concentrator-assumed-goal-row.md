<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 41 — The concentrator template's rail does not show its assumed goal

Status: done
Source: issue 21, goal-parity re-check (#1927); GK A (35) chose a follow-up
Severity: misleads
Journey fit: inputs → results. The goal the study certifies against is not named where the other templates name it.

## What

On `eh_concentrator_staged` (weak_flexible) with no stored goal, the Goal card and the study use the pack's 3 h/yr LOLE (`reliability_goal.effective_goal` → `assumed`; `pack_target_lole_h`). The rail shows no "Reliability goal (LOLE): 3 h/yr (assumed)" row. `flow_service._assumed_items` reads template ledger rows with `source == "assumed"`, and `project_templates/eh_templates.py` adds the LOLE row only for ids in `_GRID_READINESS_SITE`. The concentrator is registered separately, so it gets no row. Every other shipped template agrees across rail, card and study (checked 2026-11-23 by calling the real functions on master c898a83c).

## Acceptance

- `eh_concentrator_staged` shows the assumed LOLE row in the rail with the value the Goal card and the study use.
- A test checks, for every shipped template, that the ledger's assumed LOLE, the Goal card's value (`effective_goal`) and the study's pack target are the same number (or all absent).

Gated: GK A. Blocked by: none.
