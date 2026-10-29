<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-33 — Behaviour coverage for the nine unassigned LC-D06/LC-D13 variants

Status: ready-for-agent
Type: task

Owner decision LC-VARIANTS-1: one package covers the nine variants LC-08's hand-off left
unowned (`OWNERS` maps them to `UNASSIGNED` in `smoke/release_evidence.py`; `_pending` in
`smoke/family_manifest.py`). Same bar as the `_covered` rows (LC-D13.energization,
LC-D13.eh_dtc, LC-D12.zonal_mc): a test drives the real engine through the chat tools
(`seam` from `test_lc06_d05_foresight.py`, `chat_service._dispatch_real_tool_call`), pins a
hand-computed number, and a mutation moves that number by a hand delta. No engine stubs.

All numbers below are the oracle's formula; the fixture's concrete values are fixed when the
test is written and land in the manifest row, as in LC-06.

| Variant | Hand number (oracle) | Test file | Mutation |
|---|---|---|---|
| `LC-D06.sclopf` | Two identical parallel lines A–B (s_nom 60 each), 10 EUR/MWh gen at A, 50 EUR/MWh gen at B, 100 MW load at B. `update_solver_config(sclopf, include all lines)` + `run_simulation`: objective per hour = 60 x 10 + 40 x 50 = 2600 (plain LOPF 1000); base flows 30/30, so each line's post-outage flow 60 = s_nom. Read via `get_simulation_status`, `get_results('lines')`, `get_run_evidence` | `test_lc06_d06_sclopf_ac.py` | s_nom 80 -> 1800/h (delta -800); `sclopf` off -> 1000/h |
| `LC-D06.ac_stage` | The chained stage (`run_ac_pf_after_lopf` through `run_simulation`; LC-D07.static_pf covers only the standalone `run_ac_pf_stage`). LP dispatch by hand (cheap gen meets the load), then slack P = LP dispatch + losses, losses and bus voltages from an independent Ybus/NR solve (the LC-D07 oracle, reused). `get_results(source='ac_pf')` matches the oracle; `source='lopf'` still reads the LP baseline; converged count = snapshot count | same file | line r doubled -> losses move by the oracle delta; LP objective unchanged |
| `LC-D13.margin_loop` | `run_margin_loop` on a q=0 fleet (MC LOLE exactly 0 once firm >= load, so the MC draw is not in the oracle): probe m_tight = min_P(firm_P/peak_P) - 1, x0 = max(1.05 x m_tight, 1e-4), status `met` at x0, `cost_eur` of each iterate = annuity x built MW + energy cost by hand, `lever_star` = cheapest met iterate | `test_lc33_d13_margin_elcc.py` | peak +10 MW -> x0 and cost move by the hand deltas |
| `LC-D13.reserve_margin_coupling` | `update_solver_config(reserve_margin=m)` + `run_simulation`: required = (1+m) x unweighted peak; existing firm F (q=0); extendable unit derated (1-q) builds (required - F)/(1-q) MW; objective by hand; `get_adequacy_results('reserve_margin')` required/achieved/binding | same file | m 0.2 -> 0.3: build +0.1 x peak/(1-q) MW, objective by the hand delta; m 0 builds nothing |
| `LC-D13.elcc_frontier_campaign` | Inside `start_campaign`: `run_frontier_study` with two or three targets, each point's `total_system_cost_eur` (objective - VoLL x ENS) and `achieved_ens_mwh` (= cap x demand energy) by hand, knee by hand; `run_mc_study(elcc_assets=[q=0 unit])` ELCC = nameplate within the bisection tolerance; `campaign_status` solves charged = targets + 1 | same file | VoLL halved -> knee moves by hand; capex +x -> each point's cost moves by x x built MW |
| `LC-D13.eh_redundancy` | `run_eh_study` stages `apply_pack, ens_solve, redundancy` on a tiny hub: `cost_at_target_eur` of `base` and of the N-1 scenario(s) by hand (N-1 needs the largest unit's MW again as built capacity x annuity); `selected_id` = cheapest feasible; read `get_adequacy_results('eh_redundancy')` | `test_lc33_d13_eh_planning.py` | capex of the backup unit +x -> the N-1 cost moves by x x MW, base unchanged |
| `LC-D13.eh_levers` | stage `levers`, `import_cap` values {a, b}: `cost_at_target_eur(cap)` = import energy x price + local build for the rest, by hand per value; `get_adequacy_results('eh_levers')` | same file | import price +x -> each option's cost moves by x x imported MWh |
| `LC-D13.eh_dtc_planning` | stage `dtc_planning`: islanded critical Load must be met locally, so the plan builds critical peak - local firm MW; `critical_unserved_mwh` 0 and the planning cost by hand; against the covered stress mode on the same fixed plan (non-zero) | same file | critical Load +10 MW -> build +10 MW, cost by the hand delta |
| `LC-D13.multi_energy` | `ens_solve` on a shared bus with per-Load capture: an H2 Load starved, `ens_by_carrier_mwh` {H2: x, AC: y} and `ens_by_load_mwh` by hand; the dedicated-bus roll-up case gives the same carrier split from bus ENS. (The manifest reason "no H2/heat attribution test found" is stale: `test_energy_hub_multi_energy*.py` exist but assert only > 0.) | `test_lc33_d13_multi_energy.py` | H2 Load +5 MW -> H2 ENS +5 x hours, AC unchanged |

## PRs

1. D06 pair (`sclopf`, `ac_stage`).
2. D13 adequacy loop: `margin_loop`, `reserve_margin_coupling`, `elcc_frontier_campaign`.
3. D13 EH planning: `eh_redundancy`, `eh_levers`, `eh_dtc_planning`.
4. `multi_energy`.

Each PR adds tests only (plus a release-note fragment). The manifest flips (`_pending` ->
`_covered` with node ids) and `OWNERS` (-> LC-33) land after LC-32's #918, #920, #922 merge.
A variant that needs engine work to be testable goes to the coordinator with options instead.

## Comments

### GK A (6), 2026-10-29: plan @33f6119e is a GO with conditions

- General: each mutation runs in the test (parametrised case or a second solve); each file finishes
  under 590 s alone with `-x`. A hand number the engine gets wrong is not pinned at the engine's value:
  assert what is right, add a `_pending` row naming a new ticket, tell the coordinator (no xfail, no
  skip). A release-note fragment per PR; manifest/OWNERS flips wait for #918/#920/#922.
- D06.sclopf: gens at A and B fixed (non-extendable, p_nom >= 100), snapshot weight 1. Assert the base
  flows are 30/30 under SCLOPF and 50/50 with it off, and the post-outage flow equals s_nom.
- D06.ac_stage: the oracle is the independent Ybus/NR from LC-D07, never pypsa's pf. The slack
  generator is named; non-slack generators keep their LP setpoints exactly; slack = LP + losses; the LP
  objective is unchanged under the r-doubling mutation to 1e-9 relative.
- D13.margin_loop: the code's formula (`margin_loop_runner.py` x0: base = max(m_tight, 0),
  m_start = base x (1 + STEP_OVERSHOOT) or STEP_OVERSHOOT at base 0, clamped to
  [PROBE_MARGIN, ceiling]), constants imported. Both branches pinned (m_tight > 0 and <= 0). One q > 0
  step pinned on a fixed seed, LC-D12.standard_mc style: each iterate's MC LOLE inside the seed CI
  around a hand/independent COPT LOLE; met/not-met asserted only where the COPT LOLE clears the target
  by more than the CI half-width; the step direction follows not-met -> raise; `lever_star` asserted.
- D13.reserve_margin_coupling: the extendable unit has q != 0 (e.g. 0.1); weighted and unweighted
  peaks differ in the fixture.
- D13.elcc_frontier_campaign: the ELCC tolerance is the engine's named bisection tolerance, imported;
  the identity total_system_cost = objective - VoLL x ENS is asserted separately from the hand numbers.
- EH trio and multi_energy: GO as written; multi_energy asserts AC ENS under the mutation exactly.
