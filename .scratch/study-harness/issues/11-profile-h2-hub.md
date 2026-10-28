<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 — Harness profile: eh_h2_hub and eh_h2_hub_staged

Status: ready-for-agent
Type: task
Blocked by: 07
Gated: yes (GK B; GK A for the IC counterfactual rule's engine dependency)
Model tier: opus
Owner decisions: SH-PILOT-1
Design: [design.md](../design.md) §9.3 "eh_h2_hub"

## What to build

1. Profiles `eh_h2_hub@1` and `eh_h2_hub_staged@1`: scope `_H2_HUB`, archetype `strong_grid`.
2. Facts: H2 offtake served per hour, electrolyser utilisation (a new reader over the solved network; none exists today), LOLE with the fuel cell as firm backup.
3. Investment rules `not_evaluated` with the reason "IC counterfactual with H2 offtake not available": an IC engine change that needs its own issue (GK A, generic-site Q3); the report lists it as a gap. Offtake is demand and never tunable.
4. Tunables: wind/PV/BESS candidate max, electrolyser and H2 storage size within bounds.

## Acceptance criteria

- [ ] A run on each template reaches hand-off; offtake and LOLE rules pass on the fixture.
- [ ] A seeded undersized electrolyser fails the offtake rule; L2 sizes it within bounds in ≤2 rounds.
- [ ] Investment rules show `not_evaluated` with the reason, and the run is not blocked by them (`warn`).

## Likely files

`pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/harness/workflows/profiles/eh_h2_hub/`, `pypsa-gui/backend/tests/test_run_profile_h2_hub.py` (new)

## Comments
