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
4. Diesel-fleet IC baseline: an IC engine change that needs its own issue (GK A); the rule is `warn`, `not_evaluated` with the reason until then.

## Acceptance criteria

- [ ] A run on each template reaches hand-off with island survival as a gate result.
- [ ] A seeded undersized battery fails survival; L2 resizes within bounds or ends `needs_user`.
- [ ] No SCR rule appears in the gate for an `off_grid` archetype.

## Likely files

`pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/services/run_actions.py`, `pypsa-gui/backend/harness/workflows/profiles/eh_microgrid/`, `pypsa-gui/backend/tests/test_run_profile_microgrid.py` (new)

## Comments
