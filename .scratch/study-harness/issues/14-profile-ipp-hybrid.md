<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 14 — Harness profile: eh_ipp_hybrid

Status: ready-for-agent
Type: task
Blocked by: 07
Gated: yes (GK B, investment/finance)
Model tier: sonnet
Owner decisions: SH-PILOT-1, AI-558
Design: [design.md](../design.md) §9.3 "eh_ipp_hybrid"

## What to build

1. Profile `eh_ipp_hybrid@1`: scope `_IPP`; inputs fetch the market series; solve then `run_investment_case`.
2. Result rules: export ≤ 70 MW, `ic.min_dscr` ≥ profile value (`warn`), `ic.project_pre_tax_irr` present.
3. Explore runs the shipped `cases.json` (≤4 cases) and `solve_ppa_price` for a target IRR.

## Acceptance criteria

- [ ] A run reaches hand-off with the investment chapter; the shipped cases run as explore cases (capped at 4).
- [ ] `solve_ppa_price` result is a KPI read back at hand-off.
- [ ] Grid, reliability and control stages that the preset turns off are `skipped` with the reason.

## Likely files

`pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/harness/workflows/profiles/eh_ipp_hybrid/`, `pypsa-gui/backend/tests/test_run_profile_ipp.py` (new)

## Comments
