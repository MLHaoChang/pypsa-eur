# Solve path: committable Links

Status: done
Type: task
Blocked by: 02
Gated: **yes** (solver engine: send the gatekeeper the plan first)
Model tier: opus
Size: M–L

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
3. **Weighted networks and start-up costs.** "Weighted" means any
   `snapshot_weightings.objective` ≠ 1 or any `investment_period_weightings.objective` ≠ 1.
   PyPSA 1.1.2 leaves `start_up_cost` and `shut_down_cost` unweighted
   (`pypsa/optimization/optimize.py:301-316`; the gatekeeper confirmed it).
   - **The template validator refuses** `start_up_cost` or `shut_down_cost` above 0 on a
     weighted network. The reason names the unit and points to `stand_by_cost` or the shortfall
     slack.
   - **The solver warns** in the same case on a user network, and does not refuse: that is
     existing behaviour.
4. **More template validator rules:**
   - refuse an `eh_process` Link that is both committable and extendable (the big-M rule);
   - refuse `__lost_<p>` priced at or below `__buy_<p>`, or the LP buys by shedding;
   - refuse a lost-production value missing or defaulted from VoLL (#312).
5. **The binary operational check is mandatory** whenever a process Link is committable.
   - It re-solves one representative week per period with binary UC on the planned design
     (fixed capacities).
   - It reports per period, in the results and the ledger: binary minus linearised cost, and
     the extra shortfall tonnes.
   - Flag threshold: > 2 % cost, or any extra shortfall. Above it, the plan says "linearised UC
     understates restarts".
6. **Upstream note.** Add a note in the design or the code on the upstream PyPSA weighting gap,
   for an upstream report.

## Tests (write first)

- A committable Link with `p_min_pu` 0.4 never runs below 0.4·p_nom·u, and every stop lasts at
  least `min_down_time` snapshots.
- The `start_up_cost` refusal fires on a weighted template network, the warning fires on a
  weighted user network, and an unweighted network passes silently.
- The lost ≤ buy-in refusal fires.
- The binary check's flag fires on a case where a unit sits below its minimum load in the
  linearised plan.
- The MIP settings are applied when only a Link is committable.
