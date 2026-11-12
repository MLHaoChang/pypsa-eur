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

- 2026-11-12, GK A (24) plan verdict: GO. g2p 06 and 10–13 have landed, so the ammonia result rules are live, not pending (Q1 = A). Corrections: the check's `status` is only `ok` or `unverified`, and `flagged` is a separate bool. The payload has no solve id; the binding is the wipe at solve start plus `_own_solve`. Shortfall and buy-in generators are found by `process_role`, not by name. Conditions:
  - Q2 = A: `unverified` is no evidence, with its reason. The run goes to needs_user and L2 changes nothing.
  - C1: `nh3.binary_check` passes only when `status == "ok"` and `flagged is False` exactly. A truthy non-bool `flagged` does not pass. A flagged check fails with the payload's per-period rows.
  - C2: a check payload of None reads as a pass ("exact MILP, nothing to check") when the solve's resolved `process_uc` mode is binary. Any other None is no evidence.
  - C3: shortfall and buy-in are read by `process_role` on the product bus. Each is in t/d per period: Σw·p / mwh_per_t ÷ (Σw / 24), the target the same way from the delivery load. Tested with uneven weights and two periods, only one of which fails.
  - C4: buy-in alone passes and is shown. Only the shortfall counts against the threshold.
  - C5: the levers are the per-year candidates and the two tanks, each bounded at 2× its template cap and pinned by an over-bound refusal. Never levers: the process units, the process and delivery loads, the role generators, grid_import, the built electrolyser, wind_farm/solar_park.
  - C6: after an L2 re-solve the gate reads the new check, never round 1's.
  - C7: mutants on each guard (the flagged compare, unverified, the None split, role selection, weighting, the threshold compare, the lever bound). Survivors are listed in the PR body.
  - Q3: the threshold is a 1 % delivery tolerance, shown on the card. Q4: the NH3 tank is a lever.
