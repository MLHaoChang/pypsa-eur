<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 12 — Harness profile: eh_microgrid and eh_microgrid_staged

Status: ready-for-agent
Type: task
Blocked by: 07
Gated: yes (GK A for the island rules; GK B for the harness parts)
Model tier: opus
Owner decisions: SH-PILOT-1
Design: [design.md](../design.md) §9.3 "eh_microgrid"

## What to build

1. Profiles `eh_microgrid@1` and `eh_microgrid_staged@1`: scope `_MICROGRID`, archetype `off_grid`; `SINGLE_YEAR=True` handled at intake (end year after start year).
2. Island facts by the verdicts of the scorecard's island rows (`sustained_h` from `gridspine/schema/island.py`, frequency judge), critical feeder served; no SCR rule on an island.
3. Check the `campus_*` island tools on a microgrid project; where they do not fit, a native action over the island LP; record what was found in Comments.
4. Diesel-fleet IC baseline: the engine gives it (#302, owner M2e (a): `pypsa-gui/backend/services/results/baseline.py:4`, `finance_case.py:1376`; tested in `tests/test_eh_microgrid_case.py:97`), and [issue 20](20-microgrid-diesel-ic-baseline.md) landed its facts (`services/ic_counterfactual.py`). Two investment rules, both `warn`: `ic.counterfactual` `eq` `established`, and `ic.baseline_keeps` `nonempty` (the owned existing assets the case's baseline keeps; `not_established` naming each `MG_EXISTING` asset it drops). Both are evaluated once the case reads `established` (a baseline that sheds at VoLL or relaxes the island rows stays established, its caveats quoted by `counterfactual_text`), and `not_evaluated` with the fact's reasons otherwise; the run is never blocked by them (GK A).

## Acceptance criteria

- [ ] A run on each template reaches hand-off with island survival as a gate result.
- [ ] A seeded undersized battery fails survival; L2 resizes within bounds or ends `needs_user`.
- [ ] No SCR rule appears in the gate for an `off_grid` archetype.
- [ ] On `off_grid`, the rows built from the PoC's S_k'' (TDD, rapid voltage change, flicker, unbalance, motor start) are excluded with the reason (test); the gate is the island rows.
- [ ] The LOLE rule names its h/yr basis.
- [ ] The investment rules `ic.counterfactual` (`eq established`) and `ic.baseline_keeps` (`nonempty`), both `warn`, are evaluated on a finished case on each template, and `not_evaluated` with the fact's reasons while either reads `not_established` or `not_run` (issue 20).

## Likely files

`pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/services/run_actions.py`, `pypsa-gui/backend/harness/workflows/profiles/eh_microgrid/`, `pypsa-gui/backend/tests/test_run_profile_microgrid.py` (new)

## Comments
