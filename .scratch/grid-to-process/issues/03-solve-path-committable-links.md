# Solve path: committable Links

Status: needs-triage
Type: task
Blocked by: 02
Gated: **yes** (solver engine: send the gatekeeper the plan first)
Model tier: opus
Size: M

Read [the design](../design.md) §2.3 first.

## What to build

1. **MIP settings.** `solver_service.py:873-876` applies the MIP gap and time limit only when a
   Generator is committable. Widen the check to committable Links, and name both counts in the
   phase line.
2. **Linearised UC for process units.** When any `eh_process` Link is committable, a planning
   solve passes `linearized_unit_commitment=True`.
   - It is a config switch, `process_uc: linearised | binary`, defaulting to `linearised`.
   - It composes with the island path's `linearised_uc` (`solver/island.py:391-419`) without
     double-setting.
3. **Refusals.** Refuse, with a reason naming the unit, when:
   - `start_up_cost` or `shut_down_cost` is above 0 on a network whose objective weighting is
     not 1. PyPSA 1.1.2 leaves both unweighted (`pypsa/optimization/optimize.py:301-316`).
   - an `eh_process` Link is both committable and extendable (the big-M rule).
4. **Operational check.** A one-week-per-period binary-UC re-solve of the planned design (fixed
   capacities). It reports the objective gap and the lost-tonne gap against the linearised plan.

## Tests (write first)

- A committable Link with `p_min_pu` 0.4 never runs below 0.4·p_nom·u, and every stop lasts at
  least `min_down_time` snapshots.
- The `start_up_cost` refusal fires on a weighted network and passes on an unweighted one.
- The MIP settings are applied when only a Link is committable.
