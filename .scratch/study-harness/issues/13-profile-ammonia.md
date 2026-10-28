<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 13 — Harness profile: eh_ammonia_staged

Status: ready-for-agent (the ammonia-specific result gate is pending g2p 10–13, per GK A; build the profile with those rules marked pending)
Type: task
Blocked by: 07
Gated: yes (GK A; g2p engine facts)
Model tier: opus
Owner decisions: SH-PILOT-1
Design: [design.md](../design.md) §9.3 "eh_ammonia_staged"; `.scratch/grid-to-process/` issues 10–13

## What to build

1. Profile `eh_ammonia_staged@1` (builds on `eh_h2_hub_staged`).
2. Facts: `binary_check` (`services/solver/process_uc.py:351`, from the solve result payload: `status`, `flagged`, `periods`), `nh3_delivery_tpd` against target, shortfall (grey NH3 or penalty).
3. Investment against the grey-NH3 counterfactual as a result rule.
4. The coupling report (g2p 13) as a profile chapter once that issue lands; until then a listed gap. It is not `run_coupling_loop`, the generic LOLE loop.
5. Process-unit form inputs (G2P-07) stay the user's: the run waits at stage 2 if they are missing.

## Acceptance criteria

- [ ] A run reaches hand-off; a flagged binary check fails `block` with its per-period gaps as facts.
- [ ] Delivery shortfall above the threshold fails; L2 changes only tunables (≤2 per round).
- [ ] Missing coupling report shows as a gap, not an error.

## Likely files

`pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/harness/workflows/profiles/eh_ammonia_staged/`, `pypsa-gui/backend/tests/test_run_profile_ammonia.py` (new)

## Comments
