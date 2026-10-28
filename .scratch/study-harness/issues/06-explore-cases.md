<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 — Explore stage: ≤4 cases through existing case engines

Status: ready-for-agent
Type: task
Blocked by: 05
Gated: yes (GK B)
Model tier: opus
Owner decisions: AI-558 (≤4 cases, ≤2 changes each, sourced ranges), AI-567
Design: [design.md](../design.md) §3 stage 6, §6.7

## What to build

1. Agent proposes ≤4 cases with ≤2 changes each and a sourced range per change (AI-558 Suggest-cases); harness admits them into `run.cases`.
2. Each case runs through `run_sensitivity_sweep` (network), experiments (two runnable cases today), or the decision-study option runner for investment questions; then its own result gate.
3. A case that needs an absent domain adapter is `not_runnable` with the reason (live-voice 10 adapters, not built here).

## Acceptance criteria

- [ ] A fifth case or a third change is refused at proposal.
- [ ] eh_site: connection vs BESS vs genset, 3 cases, each with its own gate result and settled status.
- [ ] A failing case does not fail the run; the base case does.
- [ ] An investment case runs through the decision-study runner on study-owned forks; the base project is unchanged.

## Likely files

`pypsa-gui/backend/harness/runner.py`, `pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/study/runner.py` (read only unless a seam is needed), `pypsa-gui/backend/tests/test_harness_run_explore.py` (new)

## Comments
