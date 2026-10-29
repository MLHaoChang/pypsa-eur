# LC-06 — whole-application behavior coverage

Status: in progress (LC-06 session; S0 manifest PR, then S1–S3 with GK B; S4–S9 reviewer named by the coordinator)
Type: task
Baseline: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Dependencies: LC-01–05 as applicable

Read the [specification](../spec.md), [assessment](../gap-assessment.md),
[acceptance matrix](../test-plan.md) and
[implementation plan](../../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md).
Applicable contracts/cases: LC-C1/C3/C4/C5/C10; LC-T16/17 and
[LC-D01–24](../domain-coverage.md).

Use all actual capability-matrix families as the backlog. Map every eligible tool/control to its read/action/result/refusal fixture and independent oracle. Deliver small domain/operation adapters, preserve current Guided labels and add incoming profile/process capabilities only after actual source integration.

One journey per broad family is insufficient. Cover each named engine/control
variant in the domain annex: capacity and multi-period planning, static load
flow/security, COPT/LP/standard and zonal MC/common-mode, Energy Hub sub-engines,
island control schemas, E1–E6 aggregate frequency events and GFM apply/re-study.
Keep analytical correctness independent of native re-run consistency. Maintain
the operation/schema/control execution manifest and explicit unavailable
dispositions; inventory counts and legacy pending journey IDs close no scope.

Expected ownership: capability matrix, native family adapters, result/parameter UI bindings and public-seam journeys.

Rebaseline current master and incoming interfaces before implementation. Write
one meaningful public-seam RED case, then implement that slice and regress its
neighbors. Record actual source/commands/results and remaining gates here.
No new capability is enabled by this issue. Paid calls, reviewer contact,
deployment and full-coverage/domain-approval claims remain outside this package.


## Rebaseline (2026-10-28, master `a6d73f57`)

Branch `claude/lc06-family-manifest`. Denominators have moved since `c75ca558`:
367 tools (was 255), 21 result tabs (15), 23 panels (19), 187 creation
controls (170, 178 with a tool and 9 with an explicit gap). The capability matrix
(`smoke/assistant_control_matrix.py`) still maps the 14 families to
`pending:issue-05-*` journeys. Every inventory tool reads
`behavioral_fixture: pending`, and `full_coverage=not_established`. No
tool→family map or per-variant execution manifest exists yet.

LC-03 kept six result families with their run: load flow, summary, capacity,
dispatch, prices and emissions (`services/retained_results.py`). The other
result views (overview, flows, loadshape, grid_code, electrical_design,
island, grid_forming, economics, curtailment, lostload, adequacy, storage,
fmea, investment, asset) answer the current run only.

Labels: **PS+O** = public seam (chat tool, checked stream or authenticated API)
plus a hand oracle; **SEED** = engine/service oracle with no seam; **weak** =
seam that checks only shape or refusals, or that stubs the engine.

| Suite | Already PS+O | Missing (the backlog) |
|---|---|---|
| D04 | fixed/extendable build to bound, MVA/MWh units (`test_capacity_cycles_assurance.py`) | 10/6/4 unique-LP objective and cap perturbation; `get_vintage_results` untested |
| D05 | — (SEED: `test_myopic_horizon_cost.py` full/myopic/limited) | any seam; two-period discount/retire |
| D06 | full LP (310, `test_assistant_capabilities.py`); infeasible (`test_lc19_run_lifecycle_journeys.py`) | rolling, myopic, SCLOPF and AC stage numbers through the seam; unbounded, time limit, MIP gap, unavailable solver |
| D07 | active proxy, AC apparent, convergence mask, weighting, `s_max_pu`, missing Q (`test_loadflow_summary.py`, `test_branch_loading_assurance.py`) | exact P=60/Q=80→100 MVA; independently solved static PF; N-1/voltage disposition |
| D08 | lost load, curtailment, energy balance | — |
| D09 | StorageUnit trajectory, 51 EUR cost, invalidation, Store sign/cyclic | native inflow/spill oracle |
| D10 | prices, binding-cap dual, emissions | — |
| D11 | COPT two-unit (`test_copt_assurance.py`) | FMEA numbers and health through the seam (weak only) |
| D12 | — (SEED: standard MC vs exact COPT, storage, period reset; zonal; common-mode) | standard, zonal and common-mode MC through the seam |
| D13 | COPT EUE/LOLE screening | margin_loop (engine stubbed), eh_redundancy, eh_levers, eh_dtc, eh_dtc_planning, energization, H₂/heat reporting through the seam |
| D14 | — (SEED: `tests/gridspine/test_campus_ranking.py`) | static campus through the seam (chat tests stub it) |
| D15 | — (SEED: `test_island_schema.py` for all but gfl_pq) | all six control models and droop/isochronous governors through the native tools; gfl_pq specific case |
| D16 | — (SEED: `test_frequency*.py` inertia/settled/one-governor, E1/E3/E4/E6, collapse, UPS) | `campus_run_island_study` unstubbed per event E1–E6; E2/E5 oracles |
| D17 | apply digest/read-back (equality) | recommend→apply→re-study chain through the seam |
| D18/D19 | hand cash flow; one experiment case (200) | two-case solve compare |
| D01/02/20–23 | seam-only (no oracle) | independent oracles per the annex |
| D24 | frontend e2e, not re-audited here | — |

## Comments

### GK A (3), GO conditions for S4–S9 (restated 2026-10-28, after rotation)

GK A checks each condition at its slice's PR.

All slices:
- Show one mutation per slice (a perturbed fixture input, e.g. a doubled FOR,
  halved H or link removed) under which the oracle assert fails.
- State and justify each tolerance; no bare rel=0.1.
- Take f0 from the network or profile (60 Hz on SAGC fixtures); test_sixty_hz stays green.
- Use SAGC-1 generic demo figures in campus fixtures.
- An engine bug found by a RED gets its own plan to GK A before any engine edit.

S4, standard MC:
- Oracle LOLE = Σ_h P(cap < L_h), EUE = Σ_h E[max(L_h − cap, 0)] from the two-unit COPT.
- Fixed seed, with enough draws that the CI half-width is ≤ 10 % of the oracle LOLE.
  Assert both the containment and the width.
- If outages are chronological with MTTR and start all-available, make that
  bias ≪ the CI or start stationary, and say which.

S5:
- Zonal: per hour, min(available Link capacity, max(surplus, 0) × delivery
  ratio), on hours where each bound binds.
- Common-mode: equal per-draw unit states across the two runs (no RNG shift),
  then per draw EUE_cm ≥ EUE_base, and strictly greater in total where the Link
  matters.

S7:
- Refusals name the field and the model.
- One wrong-base case per class (H unit vs system base, droop % vs pu).
- Exact read-back.

S8:
- RoCoF = −ΔP·f0/(2·H·S) at the first step, with D = 0.
- Settled Δf = −ΔP/(ΣK + D), with no isochronous unit in that fixture; the
  isochronous case asserts Δf → 0.
- UPS L·T/2/3600 MWh.
- dt-halving error ratio ≥ ~1.8 (first order).
- The EMT/PLL/voltage unavailable row stays.

S9:
- Stale = a network edit between preview and apply, refused with the reason.
- The re-study runs on the applied values.

S6:
- min(schedule, capacity) survives capacity edits up and down.
- eh_dtc: bus VoLL and per-Load VoLL each change the result.

### GK A (4), GO conditions for S6 (2026-10-29)

Option A: the pack override (`import_p_nom_mw`) is the capacity edit. A direct
Link edit would be overwritten by the pack on the run.
1. At each of the three cap steps, assert the solved import Link's p_nom equals
   the override, and ENS equals the oracle at that step.
2. Non-uniform snapshot weights (w_h ≠ 1 on at least two hours); one Link.
3. In the cap-binding step, at least one short hour with E_h > cap and one
   with E_h < cap.
4. The schedule-drop mutation asserts the exact hand delta.
5. DtC: 140 and 40 MWh on the same inputs; assert bus ≠ per_load and priority_exact.
6. Land these here with the S6 entry.

### GK A (4), GO conditions for S7 (2026-10-29)

Option A: the model goes into `where` in gridspine/schema/island.py. Wrong base
means oracle discrimination; no new refusal or warning.
1. Put the model in `where` for converter units and governors, e.g.
   "units.g1 (governor droop): missing ['droop_pct']".
2. The missing-'control' refusal stays as it is; assert that it lists the allowed controls.
3. A field from another model: the refusal names that field and the unit's own model.
4. Grep every consumer of the old text before changing it. Update only what
   breaks. Run tests/gridspine and the campus/island backend tests at the head,
   root tests/ and backend tests in separate pytest runs.
5. Read-back is exact for every {value, source}, including preset-expanded
   fields and the f0 source on the 60 Hz case.
6. Mutations: S_n scale; droop read as pu, which must fail the droop assert; the
   model removed from `where`, which must fail the refusal tests. Each
   wrong-base case asserts |correct − wrong| ≫ tol.
7. Land these here under S7.

### GK A (4), GO conditions for S8 (2026-10-29)

Split A: S8a now (E1, E2, E3, isochronous, dt halving, 60 Hz); S8b (E4–E6) with its
own plan (genset, PV and UPS figures; UPS L·T/2/3600 and E6 ramp oracles).
1. dt halving: assert the order, ratio ≥ 0.75·2⁴ = 12 for RK4, error = max over time
   points spanning several M/K, coarse error > 1e-9; report the measured ratio.
2. Assert the oracle against the reported metric: 0⁺ RoCoF, or the windowed closed form.
3. D = 0 and no isochronous unit in the Δf_qss case, asserted from the read-back.
4. E1: ΔP between 40 MW and 40 MW plus a stated loss bound.
5. Isochronous: |Δf(t_end)| below the band with t_end ≥ 5·T_i; the nadir nonzero.
6. E3: the collapse row names the tripped unit and the reason.
7. Mutations: H_v halved → twice the RoCoF; droop 8 % → twice Δf_qss; each fails its assert.
8. The EMT/PLL/voltage unavailable row stays; E4–E6 stay pending naming S8b.
9. Land these here under S8.

### GK A (4), GO conditions for S8b (2026-10-29)

GO on the S8b plan, with conditions:
1. E4 has no grid-forming BESS, asserted from the read-back sidecar.
2. E4 block 2: name where the engine resets between blocks; assert block 2's own
   RoCoF, Δf_qss (reset −0.6667 or cumulative −1.3333, as the engine does) and t_block_s.
3. E6: import_mw between the lossless hand value and it plus the stated loss bound.
4. E6 infeasible: one change from the base; p0 explicit; every shortfall term named.
5. UPS window mutant: drops ≥ 1, energy above L·T/2 by more than the tolerance, the drop's t reported.
6. E4 at N+1: two gensets, the largest dropped and named, needed_s on the rest.
7. The S8 conditions carry over: the oracle on the reported column (0⁺ or windowed,
   stated), D = 0 from the read-back, the EMT row explicit.
8. Mutations: PV, T halved, e 0.05 → ok false, droop 8 %, ramp halved; each turns red.
9. Land these here under S8b.

## Slice log

### S0, execution manifest (2026-10-28)

Branch `claude/lc06-family-manifest`.

- `smoke/family_manifest.py` gives each of the 367 tools one primary family
  (0 unmapped, none in two). A variant's `tools` show where a tool is shared;
  there is no `shared` family. Campus/GridSpine tools go by prefix, except the
  four campus library/investment tools, which go to economics. It lists 72
  variants: 19 covered, 51 pending, 2 unavailable. The unavailable rows point at
  `loadflow_summary.py LIMITS_CLAIM` and `gridspine/static/frequency.py LABEL`.
  Tools covered: 17 / 367. Checked-stream rows list no tools, so they do not
  raise the tool count.
- GK B plan GO, option 1 with conditions. A new tool prints as `unmapped`,
  stays in the denominator and never fails `--check`. `covered` node ids must
  collect (`pytest --collect-only`). An oracle line names the asserted hand
  value. An `unavailable` row needs a pointer that resolves (file + symbol).
  Nothing generated is committed.
- RED: `tests/test_family_manifest.py` errors on master (no module). Green
  after, together with `tests/test_assistant_control_matrix.py` (98 tests).
  `assistant_inventory.py --check` passes at the master merge (2d8779ca).
- Covered rows are the rebaseline's PS+O tests. Their oracles were read from
  the tests and were not re-derived here. `full_coverage=not_established`.

### Reviewers and standing conditions (2026-10-28)

The coordinator named GK A to lead S4–S9 (adequacy/EH MC, common-mode, island
control/GFM, frequency oracles). GK B keeps S0–S3 and the D11/D14/D19/D01–23
rows; a UI slice also needs UX. GK A's plan GO came with these per-slice conditions:

- Every slice: one mutation per oracle (a perturbed input fails the assert);
  each tolerance stated and justified; f0 from the network/profile, never a
  literal (`test_sixty_hz` stays green); generic demo figures in campus fixtures (SAGC-1).
- S4 standard MC: exact LOLE = Σ_h P(cap < L_h) and EUE = Σ_h E[max(L_h − cap, 0)]
  from the two-unit COPT. Fixed seed. CI half-width ≤ 10 % of the oracle LOLE,
  with both that and oracle-in-CI asserted. Handle the bias of all units starting
  available (horizon ≫ MTTR, or a stationary start), and say which.
- S5: zonal per-hour min(available Link, max(surplus, 0) × delivery ratio) on hours
  where both bounds bind. Common-mode: equal per-draw unit states across runs, so
  removing a Link does not shift the RNG stream. EUE_cm ≥ EUE_base per draw,
  and strictly greater in total where the Link matters.
- S7: each refusal names the field and model; one case per wrong-base class;
  exact read-back.
- S8: RoCoF at 0+ with D = 0. Settled Δf = −ΔP/(ΣK + D), with no isochronous
  unit in that case; an isochronous case of its own (Δf → 0). UPS L·T/2/3600 MWh.
  Halving dt must cut the error by the scheme's order (≥ ~1.8). EMT row stays explicit.
- S9: stale = a network edit between preview and apply; the re-study is
  required and runs on the applied values.
- S6: energization survives capacity edits up and down; bus and per-Load VoLL
  each change the result.

GK B on S1: fill `tools` for LC-D04.bound_build/units. The perturbation delta is
computed in the test, not read from the result. S2's unavailable-solver refusal
goes through the real dispatcher, not a monkeypatched `_probe`.

### S1, LC-D04 unique LP (2026-10-28)

Branch `claude/lc06-d04-capacity-lp`, stacked on S0. GK B reviews.

- The RED first run found two app rules the annex fixture ignored. The
  validator refuses an unbounded extendable generator
  (`generator_p_nom_bounds`), and capital cost is periodised by nyears = Σ
  weight × hours / 8760. So the fixture weights two snapshots 4380 h each
  (nyears = 1) and bounds Backup at 100 MW. The oracle computes nyears itself.
- `tests/test_lc06_d04_capacity_lp.py` (3 tests, real `run_simulation` and
  HiGHS) checks:
  - objective 2,717,600 = 6 × 147,600 + 4 × 458,000, read through
    `get_results('objective_decomposition')`;
  - per-carrier capex/opex/GWh;
  - `explain_investment`: Cheap existing 0 / optimised 6 / added 6,
    at_upper_bound; Backup 4, interior;
  - the cap edit through `PUT /api/network/generators/Cheap`: 6 → 7 lowers
    the objective by 458,000 − 147,600 (computed in the test), Cheap 7 /
    Backup 3, and the older run keeps 0.36 M EUR capex (historical scope);
  - single period: `get_vintage_results` answers no vintages.
- Manifest: LC-D04.unique_lp covered. LC-D04.vintage_results stays pending
  for the LC-D05 multi-period fixture. A covered row names its tools or the
  checked-stream seam (GK B's S0 note: those rows call no catalogue tool, so
  their tools stay uncovered rather than invented). 19 / 367 tools covered.
- Limit: with nyears = 1 the periodisation factor is not discriminated; a
  partial-year case belongs with the LC-D05 horizon-vs-annual work.
- Reads `get_study_evidence` 409 `unsaved_results` before a save, by design,
  so the objective is read from the unsaved current solve instead.

### S2, LC-D05 foresight and LC-D06 solver outcomes (2026-10-28)

Branch `claude/lc06-d05-foresight`, stacked on S1. GK B reviews. Successor
session session_01G7qCun3kha23YVZyoEFDuW.

- `tests/test_lc06_d05_foresight.py`: every solve goes through `run_simulation`,
  with the strategy set by `update_solver_config` and the outcome read through
  `get_simulation_status`. Hand oracles:
  - perfect, myopic and limited foresight on the `test_myopic_horizon_cost.py`
    network: 33,000, never the per-period LP sum of 13,000;
  - two periods at r = 0.05 with years 10, discounted as the app's
    auto-discount defines it, and Old retiring before 2040: G@2030 = 6,
    G@2040 = 4, objective = the hand PV;
  - a half-year horizon charges 0.5 x the annual capex;
  - rolling windows of 2 give 200;
  - an unbounded LP and an unavailable solver are refused by the real
    validator before the solver runs;
  - a MIP gap of 0.25 bounds the brute-forced UC optimum of 640;
  - a real HiGHS time limit with no incumbent (1 ms, stopped in presolve) is a
    failed run. The other limit stops cannot be produced deterministically:
    no incumbent at the SCLOPF and myopic call sites, and a stop with an
    incumbent. For those, only PyPSA's optimize return is stubbed; the app's
    solve path runs as is.
- The RED run found two engine defects and one misleading field:
  - D-a: a rolling solve reported only the last window's LP (140 for 200).
  - D-b: a MIP stopped by its time limit with no incumbent read
    `completed` / `time_limit` with objective inf (linopy returns status ok).
    Fix: `solver/objective._no_solution_is_a_failure` (a `math.isfinite`
    test) at the full-horizon, SCLOPF and myopic per-period call sites.
    It returns status "warning" with the condition kept, so the run fails
    with the `time_limit` card. A stop with an incumbent stays completed,
    and its run record keeps condition `time_limit`.
  - D-a fix: `_compute_run_objective` prices a rolling run on the
    statistics basis (`horizon_system_cost`), as it already does for myopic.
    The status, the run record and the solve queue's reader all read 200,
    with overlap 1.
  - `objective_decomposition.myopic_horizon_total` was the per-period LP sum.
    It is renamed `myopic_lp_sum`, and `lp_total_scope` (horizon, last_period
    or last_window) says what `lp_total` covers.
- Limit: a rolling window that stops on a limit without an incumbent is
  not caught, because PyPSA only logs it.

### S3, LC-D07 independent static PF (2026-10-28)

Branch `claude/lc06-d07-static-pf`, stacked on S2. GK B reviews. Session LC-06 (3).

- `tests/test_lc06_d07_static_pf.py`: a meshed 3-bus 110 kV network (one line
  with shunt susceptance) goes through `run_simulation`, then `run_ac_pf_stage`
  with `ac_pf_x_tol = 1e-10` set by `update_solver_config`. The test builds the
  Ybus from the line ohms and siemens and solves the power balance with
  `scipy.optimize.root`, independently of PyPSA's NR. The residual must be
  below 1e-9 MW/Mvar.
  - Through `get_results(..., source='ac_pf')`: bus voltages (1e-9 p.u.), bus0 P/Q,
    per-line losses (p0 + p1) and the total, and the slack = load + losses
    (1e-6 MW).
  - bus1 P/Q come from the solved network, since no tool serves them. At the load
    bus C, the lines deliver 60 + j80 = 100 MVA (KCL).
  - `get_loadflow_summary(source='ac_pf')`: peak apparent power and utilisation
    equal the independent max-terminal |S| (rounded to 2 decimals).
  - On the LC-T07 weighted fixture, P=60/Q=80 at both terminals gives exactly
    100.0 MVA, util 100.0 %, peak_at snapshot 0, h_at_limit 1.0 and
    h_over_limit 0.0.
- Measured agreement: 2e-16 p.u. and 3e-13 MW. Mutations, each red on its own test:
  1. r × 1.01 in the oracle's Ybus: the voltages fail.
  2. Full shunt instead of b/2 at each end: the bus0 flows fail.
  3. Load q × 0.999 in the app network: the bus1 terminals and KCL fail.
  4. r × 1.01 in the app network: the losses and slack fail.
  5. s_nom 201: the summary fails.
  6. Q 79 on the fixture: the exact 100 MVA test fails.
  7. The oracle's KCL off by 1e-5: its self-check fails.
  8. x_tol 1e-3: the voltages and the bus0 flows fail.
- No engine defect was found. `LC-D07.n1_voltage` stays unavailable (LIMITS_CLAIM).

### S4, LC-D12 standard MC (2026-10-28)

Branch `claude/lc06-d12-standard-mc`, stacked on S3. GK A reviews. Session LC-06 (3).

- `tests/test_lc06_d12_standard_mc.py`: the study runs through `run_mc_study`
  (seed 1, 500 draws, cov_target 0.04) and is read back through
  `get_adequacy_results('mc')`.
  - The fleet is G60 (q 0.1, MTTR 50 h) and G40 (q 0.2, MTTR 40 h); the load
    cycles 50/70/90 MW for 8760 h.
  - The oracle is computed from the network's inputs over the four COPT
    states: LOLE = 2920 x 0.66 = 1927.2 h, EUE = 2920 x 18.6 = 54,312 MWh.
  - Asserted: both oracles lie inside the reported 95 % CI; the LOLE half-width
    is at most 10 % of the oracle (seed 1 gives 25 h, 1.3 %); the time basis is
    per year with horizon_years 1.0; the run converged; the rates come from the
    assets.
- The start bias: the engine starts every chain from the stationary
  distribution (`mc.sample_capacity`). Also, horizon ≫ MTTR: an all-available
  start would miss about sum(q x MTTR) = 13 unit-down hours, under 1 %.
- Calibration on 40 seeds: coverage 36/40 (LOLE) and 37/40 (EUE); mean 1928.1 h
  against the oracle's 1927.2 h; the sd across seeds of 15.0 h matches the
  reported SEM of 13.2 h.
- Mutations, each red on its own test:
  1. COPT state probabilities swapped: the LOLE test fails.
  2. EUE shortfall less 1 MW: the EUE test fails.
  3. 5 draws at cov_target 0.2: the half-width is 371 h, over the 193 h bound.
  4. The hand total off by 0.01 per cycle: the hand-oracle test fails.
  5. Fixture input (GK A's form): G60's FOR doubled in the network the app
     runs, after the oracle is taken. LOLE 2702.8 h, CI (2673.9, 2731.7), excludes
     1927.2; EUE 85,970 excludes 54,312. Both tests fail.

### S5, LC-D12 zonal MC and common-mode (2026-10-29)

Branch `claude/lc06-d12-zonal-mc`, stacked on S4. GK A reviews. Session LC-06 (3).

- The seam: only `run_eh_study` reaches the zonal engine. It runs when a live
  import Link's grid side carries outage data (eh_study.py:480). The weak_flexible
  pack sets the import cap itself (`import_overlay.import_p_nom_mw`, default 50 MW)
  and overrides the Link's p_nom. The test passes it through `pack_overrides`.
  The project needs a VOLL (`update_solver_config(voll=…)`), or ens_solve is
  infeasible.
- `tests/test_lc06_d12_zonal_mc.py`, all through the chat tools:
  - Deterministic zonal case (every q = 0): EUE = 1095 x 2 x (15 + 42 + 60)
    = 256,230 MWh exactly, LOLE 6570 h, CI width 0. The oracle is
    `zonal_oracle(n, cap)` = sum_h max(L − own − min(cap·eff,
    max(surplus, 0)·eff), 0). The fixture has hours bound by the Link, by the
    surplus and by zero surplus, plus hours with no deficit.
  - Common-mode (base q 0.1, cm 0.05 / 30 h, seed 3, 200 draws), Link useless
    (zero surplus): EUE and LOLE are bitwise equal with and without the common
    mode, so the hub draws do not shift.
  - Common-mode, Link useful: EUE_cm > EUE_base.
- Fixture-input mutations, each red on its own test:
  1. Z1: cap 25: EUE 305,505, only the Link hours move (+2·1095·22.5).
  2. Z2: grid load 190 on the surplus hours: 275,940 (+2·1095·9).
  3. Z3: one hour of surplus in the useless case: 410,241.75 ≠ 407,285.25.
  4. Z4: no surplus in the useful case: equal, so the strict > fails.
- GK A ruled option A (NOT OK @96b7f680 asked for it). LC-06 (4) added, on
  `simulate_zonal_blocks` with the `freeze_fixed_plan` inputs of the same
  networks (weak_flexible overlay, seed 3, 200 draws):
  - deterministic case: each hour's import equals the hand
    min(cap x p_max_pu x eff, max(surplus, 0) x eff) on every draw, and EUE is
    256,230 on every draw;
  - common mode: EUE_cm >= EUE_base per draw and strictly greater in total;
    the hub's unit availability (the first `sample_capacity` call) is bitwise
    equal across the two runs; per-hour import = min(offered, max(surplus, 0) x ratio);
  - an `_AreaState.offer` hook that calls through: results bitwise equal with
    and without it.
  Z1/Z2 are now a test at the chat seam, asserting the hand delta exactly.
  Planted faults: a hook that halves the import, and an engine that ignores
  the surplus, each turn the engine tests red. The manifest rows name the layer
  of each check.
- `import_p_nom_mw`: the override is the pack's intent (spec §6), but it is not
  disclosed when it differs from the Link's rating. Ticket: `26-weak-flexible-import-cap-disclosure.md`.

### S6, LC-D13 energization and eh_dtc (2026-10-29)

Branch `claude/lc06-d13-energization-dtc`, stacked on S5. GK A reviews. Session LC-06 (4).
Test only; no engine edit.

- `tests/test_lc06_d13_energization_dtc.py`, all through `run_eh_study`:
  - Energization: E = (50, 50, 20, 20, 50, 50, 20, 20) MW, L = (150, 120, 95,
    90, 100, 60, 70, 60), G = 60, weights (1500, 1000, 1200, 900, 1100, 1000,
    1060, 1000). Cap steps 80 → 30 → 80 give ENS 97,000 / 158,000 / 97,000 MWh.
    That is the hand value at each step, and the third equals the first bitwise.
    A pass-through spy on `run_simulation` shows p_nom = the cap at each solve.
    At 30, the cap binds in short hours 0, 1 and 4, and E binds in short hours 2 and 3.
  - Schedule dropped at cap 80: ENS 15,000, a delta of −82,000 by hand.
  - DtC, the P16 shared bus (2 h, w 1): bus aggregate 140 MWh critical against
    per_load 40 MWh, priority_exact. The mutation (local 40 MW) gives 80 against 0.
- Planted fault: the schedule applied as a fraction of a fixed 100 MW rating
  (rescaled by the new p_nom) gives 130,400 at the first step, so the test is red.

### S7, LC-D15 control models (2026-10-29)

Branch `claude/lc06-d15-control-models`, stacked on S6. GK A reviews. Session LC-06 (5).

- Engine edit (option A, TDD: the refusal tests were red first): `gridspine/schema/island.py`
  `validate_unit_island` puts the model in `where`, `units.bess (control gfm_droop)` once
  the control enum passes and `units.g (governor droop)` when the governor is a valid one.
  The missing-'control' and bad-enum refusals keep their text. No consumer parses these
  messages (grep of services, harness, frontend and tests); test_island_schema.py matches
  field names only.
- `pypsa-gui/backend/tests/test_lc06_d15_control_models.py`, all through
  `campus_set_island_config` / `campus_island_config` / `campus_run_island_study`:
  - Refusals: a missing field per model, a field from another model, gfl_pf's one-of,
    k_gfm_min > i_max_pu, both governors; each names the field and the unit's own model.
  - Read-back: the six models x both governors read back equal to the written config,
    every {value, source}; the grid_forming_support preset reads back as written and
    validates to its fields, each `assumed`.
  - Oracles on the seamless objective (400 import energy + 100 x p_nom). VSM RoCoF row:
    47.5 MW, 4750 at 50 Hz, 5700 at 60 Hz (f0 from the campus file, read back as
    facts.instant.f0_hz); H on P gives 5000/6000. Preset H_v 4 s: 5937.5.
    gfm_droop QSS row with an isochronous genset: 40 MW, 4000; droop read as pu 400,000.
    A 4 % droop genset adds K = 10 MW/Hz: 20 MW, 2000. A gfl BESS enters no GFM row: the
    genset carries 10 MW x 1000 x 4 h = 40,000, only Island-headroom-up binds.
  - The predecessor's 4000 for the droop case assumed `cost_of_seamless`; with the QSS
    row live, ups_bridged also builds BESS (its objective 800), so the tests read the
    seamless variant's objective instead.
- Mutations (each run, each red): H_v on p_nom (solver island.py RoCoF coef) fails the
  VSM and preset tests; droop_pct as pu in the GFM K (island.py ΣK) fails both droop
  tests; the `where` edit reverted fails all 13 model-naming refusal tests.
- Manifest: LC-D15.gfl_pq/qu/pf/fw, gfm_droop, gfm_vsm, governors covered.
- Runs at the head, separate pytest runs: root tests/gridspine island files (test_island_schema,
  test_campus_island, test_island_presets, test_campus_draft_island, test_sixty_hz): 118 passed;
  backend test_island_config, test_island_presets, test_chat_tools_campus_island,
  test_campus_island_service, test_family_manifest, test_edit_card_campus, test_island_f0: pass;
  the new file: 39 passed. The whole tests/gridspine exceeds the 580 s local cap (46 % clean
  when cut off); CI runs it. Consumers checked per GK A (4)'s list: `_unprocessable` passes
  str(exc) through; no tool-error kind or harness parser reads these messages.
- f0 provenance: the study reports `facts.instant.f0_hz` only. The 60 Hz case shows f0
  comes from the campus file (no file → 50); a separate source tag would be an engine field.

### S8a, LC-D16 E1–E3 (2026-10-29)

Branch `claude/lc06-d16-frequency-events`, stacked on S7. GK A reviews. Session LC-06 (5).
Test only; no engine edit. The plan, and the A/B/C split question, went to GK A (4).

- `pypsa-gui/backend/tests/test_lc06_d16_frequency_events.py`. The seam is
  `campus_set_island_config` → `campus_draft_campus` → `campus_run_study` (the queued
  Run, waited on) → `GET …/island/report`, table `freq_events`. The hub has a 40 MW
  load on a 40 MW import, a 100 MW gfm_vsm BESS (H_v 5, pf 0.95, droop 4 %), no PV
  and D = 0.
  - E2, a 40 MW step: RoCoF₀ −1.900 Hz/s and Δf_qss −0.800 Hz, both exact to 1e-6.
    At 60 Hz (campus file): −2.280 and −0.960.
  - E1: the same formulas on the row's own I4 ΔP (40 < ΔP < 40/0.99).
  - E3: tripping the BESS collapses the island at t = 0, named "no grid-forming headroom".
  - Isochronous: a spinning 200 MW genset adds H·S to RoCoF₀ (−1.1095) and brings Δf
    back to 0 within 1e-3. At 60 MW the I4 plan loads it to 40, so it saturates at
    +20 MW and Δf settles at −0.40.
  - dt halving: engine `simulate` against the closed form at t = 1 s, dt 0.1 → 0.05.
    The integrator is RK4, so the ratio is about 16 (asserted ≥ 1.8).
  - Mutations: H_v halved and droop 8 % (in-file tests). The default fixture with both
    halved/doubled fails the E2 and E1 oracles (run, red).
- Found: the chat tool `campus_island_report` trims the 21 tables to the first 2 under
  the result cap, so the events never reach the copilot. Ticket 27. S8a reads the route.
- Manifest: LC-D16.E1, E2 and E3 covered; E4–E6 pending (S8b).
- GK A (4)'s S8 conditions: (1) dt halving takes the max error over the trace to 2 s
  (4.75 τ), dt 0.05 → 0.025: ratio 16.8, coarse 5.4e-7 Hz, asserted ≥ 12 and > 1e-9.
  (2) `rocof0_hz_per_s` is dΔf/dt at 0⁺ (`Trace.rocof0`); the 500 ms windowed column is
  also asserted on the closed form (−1.11200). (3) The ledger's "load damping D = 0" and
  the read-back sidecar's kinds {bess, load}. (4) 40 < ΔP < 40/0.99 (the Link's 1 %).
  (5) t_end 20 s ≥ 5·T_i, |Δf| < 1e-3, nadir < −0.1 (−0.345). (6) E3 names BESS and
  "no grid-forming headroom". (7) In-file tests, plus the default fixture mutated red.
  (8) The EMT row stays; E4–E6 pending, "S8b".

### S8b, LC-D16 E4–E6 (2026-10-29)

Branch `claude/lc06-d16b-frequency-events`, stacked on S8a. GK A reviews. Session LC-06 (6).
Test only; no engine edit. Same file and seam as S8a; tables freq_events, freq_ups,
freq_bridge, freq_planned.

- E5 (seamless): PV 20 MW gfl_pq; ΔP = 20, −0.9500 Hz/s and −0.4000 Hz to 1e-6. The
  mutation is PV 10 MW (halves both): PV 40 MW leaves no import, which the campus refuses.
- UPS walk-in on E1: L 40 MW, T 10 s, 2 Hz window; energy L·T/2/3600 = 0.055556 MWh to
  1e-9, drops 0, t_online = T. T 5 halves it; a 0.1 Hz window drops it (first_drop_s
  reported, energy above the hand value). The UPS is full at hour 0: e(t−1) is its
  `state_of_charge_initial` (`_solved_soc_before`, non-cyclic).
- E4 (ups_bridged): a cold 60 MW droop genset alone (the BESS is gfl_pq, read back),
  blocks 20 + 20. E4 rows carry only the 500 ms windowed RoCoF, so its oracle is
  Δf(0.5)/0.5 on the two-state matrix exponential (Δf, P_m with T_g); Δf_qss −0.6667 per
  block. `_e4_pickup` restores f0 at each block's settling instant (reset, not
  cumulative); block 2's t_block_s = 15 + the settling instant on the closed form
  (3.14 s), within one dt. Bridge: autonomy e·3600/L = 900 s; e 0.05 MWh → ok false.
  N+1: an 80 MW genset (start 20 s) is dropped and named; ready stays 15 s.
- E6 (planned): BESS 30 MW plus a spinning 60 MW genset at p_min 6 MW, ramp 6 MW/s;
  34 < import < 34/0.99; ramp_s = (import − 30)/6. Ramp halved doubles it. Infeasible:
  the genset alone changed to 5 MW (p0 0.5): I4 is not viable, so the import is the
  lossless 39.5 (`dp_e1`); shortfall = import − (30 + 4.5).
- Manifest: LC-D16.E4, E5 and E6 covered; the EMT/PLL/voltage unavailable row stays.

## Hand-off 6 (2026-10-29, LC-06 (6), early)

PR stack (each against master, stacked):
- S2 #877 merged as 9b6770a4.
- S3 #878 @dfcc445958102c3e725196737df8c104c29cc0a5 (master merged in at the coordinator's
  request): GK B (9) OK @d4cc3f00 carries. CI restarted on it; premerge ok not yet sent.
- S4 #880 @4356ec68603633441c9a79aa044511435272cd00: GK A OK @abd52b35 carries.
- S5 #881 @76b46f4e3831f567fe2ca016023d8d57d3c274a7: GK A (4) OK @bdba756a carries.
  "GUI backend shard 2/4" (run 40804860617) died to a runner shutdown; re-run the failed
  job once that run completes (it was still in progress at hand-off).
- S6 #884 @aa64ab5e2f51a58f78336e0cc8835880522a2cb4: GK A (4) OK.
- S7 #885 @7b72e519c7b75c16e9274cfc5a0fa52ceabeb0cd: GK A (4) OK.
- S8a #886 @45795d5173977cb493c14f7f3adde673a8e18e90: GK A (4) OK.
- S8b #887: review requested from GK A (4); the GO conditions are above.
The coordinator's order: after each merge, merge master into the next PR (a merge commit),
then send premerge ok for it once green, in stack order.

Next: S9 (D17 chain) and the GK B D-rows as in Hand-off 1, each with a plan to its GK first.

Follow-ups: ticket 26 (with #881), ticket 27 (the chat island report trims the frequency tables).

Env: as in Hand-off 2. The whole d16 file runs in about 5 min. premerge: `PREMERGE_PYTHON=/root/.venv-pypsa-gui/bin/python bash scripts/coord/premerge.sh <pr>`
(about 3 min with its semantic tests). gh GraphQL is refused here; use `gh api` REST. `send_later` is
refused (lineage depth 8), so there is no self check-in.
Last merged SHA: 9b6770a4 (#877).

## Hand-off 5 (2026-10-29, LC-06 (5) at 300k)

PR stack (each against master, stacked; all merges clean):
- S2 #877 @0122e16e57d0af8a6e51a7bf94b8647cf389567f: GK B OK @78f751d5 carries. CI was on its last shard.
- S3 #878 @d4cc3f00a3c5a43b50c4581d08ffbfc684b6fcbd: GK B (9) OK.
- S4 #880 @4356ec68603633441c9a79aa044511435272cd00: GK A OK @abd52b35 carries.
- S5 #881 @76b46f4e3831f567fe2ca016023d8d57d3c274a7: GK A (4) OK @bdba756a carries.
  "GUI backend shard 2/4" (run 40804860617) died to a runner shutdown at 52 % with no
  test failure. Re-run that failed job once the run completes (403 while it runs).
- S6 #884 @aa64ab5e2f51a58f78336e0cc8835880522a2cb4: GK A (4) OK.
- S7 #885 @7b72e519c7b75c16e9274cfc5a0fa52ceabeb0cd: GK A (4) review running.
- S8a #886 @cdda3bf58dbdd0ab4d7951d4f0a69affcf73047d: review requested from GK A (4).
No premerge ok has been sent yet; send them in stack order (#877 first) as CI goes green.

Next:
- S8b (E4–E6): send GK A (4) its own plan first, with the genset, PV and UPS figures
  and the hand oracles for UPS L·T/2/3600 and the E6 ramp. Reuse `make_hub`, `_network`
  and `_sidecar` in test_lc06_d16_frequency_events.py. The I4 plan pre-loads a spinning
  genset to the load, so size it with room for the step.
- Then S9 (D17 chain) and the GK B D-rows as in Hand-off 1.

Follow-ups: ticket 26 (with #881), and ticket 27 (the chat island report trims the
frequency tables; new in S8a).

Env: as in Hand-off 2. A whole tests/gridspine run is over 580 s; run island files.
Last merged SHA: afc2bee1 (#873).

## Hand-off 4 (2026-10-29, LC-06 (4) at 300k)

PR stack (each against master, stacked; all merges clean, no rebase):
- S2 #877 @0122e16e57d0af8a6e51a7bf94b8647cf389567f: GK B OK @78f751d5 carries (clean master merge, GK B confirmed). CI was running. Next: premerge ok when green.
- S3 #878 @d4cc3f00a3c5a43b50c4581d08ffbfc684b6fcbd: GK OK @d4cc3f00 (GK B (9)). CI pending. Next: premerge ok when green.
- S4 #880 @4356ec68603633441c9a79aa044511435272cd00: GK A OK @abd52b35 carries (clean merges only since). CI pending.
- S5 #881 @76b46f4e3831f567fe2ca016023d8d57d3c274a7: the GK A NOT OK @96b7f680 was answered at bdba756a (engine-level tests, Z1/Z2, manifest, ticket 26). Re-review by GK A (4) is requested; 76b46f4e is a clean merge of #878 onto it.
- S6 #884 @aa64ab5e2f51a58f78336e0cc8835880522a2cb4: built to GK A (4)'s S6 conditions (above). GK A (4) is reviewing it.
- S7: branch `claude/lc06-d15-control-models` (this commit), stacked on S6. GK A (4) gave GO (conditions above). Nothing is built yet.

S7 build notes (verified by probe):
- Seam: chat_tools.campus_set_island_config(project, config), campus_island_config
  (read-back: config/study/...) and campus_run_island_study, on a project made like
  tests/test_campus_island_service.py (`_hub(load=10, gensets=[("g", 20)])`, network.nc,
  with _auth_db + seeded_identity as in tests/test_campus_limit_dispatch_cards.py; `_config`, `_BESS` and
  `_GENSET` there). A refusal is HTTPException 422 with the message as detail, e.g.
  "island_config.units.bess: unknown field(s) ['H_v_s']; allowed [...]" (no model yet).
- Engine edit: island.py `validate_unit_island`, the converter branch after the `control`
  enum, and the genset branch once `governor` is known. Grep found no consumer
  parsing these messages; tests/gridspine/test_island_schema.py matches on field
  names only. TDD: refusal test first.
- Wrong-base oracles from the seamless sizing (cost_of_seamless = 100 x BESS p_nom; ΔP = 10 MW import):
  - RoCoF row 2M ≥ f0·ΔP/rocof_max, with M_vsm = H_v_s/pf_rated·p_nom. With rocof 1 Hz/s,
    H_v_s 5, pf 0.95 and f0 50: p_nom 47.5, cost 4750 (60 Hz: 57, cost 5700). A mutant reading
    H on P rather than S_n gives 50, cost 5000.
  - QSS row ΣK·qss_band ≥ ΔP, with K = p_nom/(droop/100·f0). gfm_droop 4 % with qss_band 0.5 Hz:
    p_nom 40, cost 4000. Droop read as pu gives 400,000.
  - Set the other limits slack (rocof 1e6 / qss 1e6 in the row you are not testing).
    Check the headroom, mva and energy rows don't bind (headroom ≥ 10 MW).
- f0: the campus file (SUBDIR/CAMPUS_FILE, campus.f_hz); the study shows it in
  variants.*.instant.f0_hz.
- gfl_* models and governors: read-back, refusals and study status (gfl does not
  enter the GFM rows). Example blocks are in tests/gridspine/test_island_schema.py:40-60.
- The release-note fragment, the manifest rows LC-D15.*, then mp-code-review, then the PR.

Also open: ticket 26 (weak_flexible import cap not disclosed). Then S8, S9 and the GK B
D-rows as in Hand-off 1. Env: as in Hand-off 2; one pytest at a time.

## Hand-off 3 (2026-10-29, LC-06 (3) near 230k)

State (all stacked: S1 #873 ← S2 #877 ← S3 #878 ← S4 #880 ← S5, each against master):
- S1 #873 @4d1218fe60aecc937fdf89112e2de936f40faa71: GK B OK, CI was running. Send premerge ok when green.
- S2 #877 @78f751d50fcd2273c9f7ddc1a0e9573f9a0b1317: GK B OK @78f751d5, CI pending. Send premerge ok when green.
- S3 #878 @9042eb2b: review requested from GK B through the coordinator.
- S4 #880 @abd52b35: review requested from GK A (told about the abd52b35 head directly).
- S5 (this commit): PR to open if not open. GK A's ruling on the S5 plan (A/B/C) is pending.
  On A: add the engine-level per-draw test.
- After #873 merges: merge master into #877, then each later PR in turn (no rebase).
  If #869 lands first, merge master too (solver_service.py).

Next: S6 (D13 energization and eh_dtc, GK A conditions under ## Comments), then
S7 (D15), S8 (D16), S9 (D17), then the GK B D-rows as in Hand-off 1.

Env: as in Hand-off 2. The mutation runner is an easy recreate: copy the test with
one replacement to tests/test_zz_mutant.py, run `-k`, delete. EH studies via chat
need `seam(n, voll=…)` and `pack_overrides` for the ENS cap and import cap.

## Hand-off (2026-10-28, context near 300k)

State:
- S0 #867 (manifest) has GK OK and premerge ok @9398fa61, waiting on the coordinator's merge.
- S1 is done on `claude/lc06-d04-capacity-lp` (stacked on S0) with all tests green. Its PR is not opened yet: after #867 merges, merge master in and open it against master for GK B. Run `smoke/family_manifest.py --check`, `tests/test_family_manifest.py` and `tests/test_lc06_d04_capacity_lp.py` (background, ~4 min) first.

Next slices, in order (each a separate PR; the conditions are above):
- S2 (GK B): LC-D05 myopic/limited/perfect foresight and LC-D06 rolling/myopic through the seam, against `test_myopic_horizon_cost.py` hand costs; unbounded, time limit, MIP gap; unavailable solver via the real dispatcher (no monkeypatched `_probe`); LC-D04.vintage_results on a two-period fixture; a partial-year nyears case.
- S3 (GK B): LC-D07 P=60/Q=80 → 100 MVA and an independently solved 3-bus AC PF.
- S4–S9 (GK A, GO with conditions): D12 standard MC, D12 zonal and common-mode, D15 control models, D16 E1–E6, D17 chain, then D13 EH variants.
- Then (GK B): D11 FMEA/health, D14 static campus, D19 two-case, D09 inflow/spill, and the oracle-less D01/02/20–23 rows. D24 needs UX.

Families left without any covered variant: Projects (D01), Network/topology (D02), Reliability beyond COPT (D11 FMEA/health, D12, D13 EH), GridSpine/campus (D14–17), Experiments beyond one case (D19), Import/export (D20), Reports (D21), Audit/undo (D22), Harness parity (D23), UI (D24).

Env notes: the `lp` fixture in `test_lc06_d04_capacity_lp.py` is the solve pattern to reuse. `get_study_evidence` needs a saved project, so read objectives through `get_results('objective_decomposition')`. A past run's `get_results(run_id=…)` wraps the payload in `{status, scope, data}`. The validator refuses unbounded extendables and periodises capex by nyears.

Reviewer ids at hand-off: coordinator session_01G7qCuj4ML7V5FccYz7NghF; GK B session_01G7qCumomaAxyuvAgo3oVss (S0–S3, D-rows); GK A session_01G7qCum3HtNvhuPbBnJhiuB (S4–S9); UX (4) session_01G7qCumtovuejQkxqdLJRZD for any UI slice.

## Hand-off 2 (2026-10-28, LC-06 (2) near 300k)

State:
- S1 #873 (head 4d1218fe60aecc937fdf89112e2de936f40faa71) is open against master. GK B (8)
  has the review request (relayed by the coordinator). CI was running at hand-off.
- S2 #877 (head 3104c2334479a0e71f5d9eccf19439b50137f54b) is open, stacked on S1. It went to
  GK B (8) with conditions 1–7 addressed in the body. It carries two engine fixes:
  the rolling objective and a limit stop with no incumbent.
- After #873 merges, merge master into #877 (it then shows only S2). If #869
  lands first, merge master too: both edit `solver_service.py` (GK B condition 7).

Next: S3 (GK B): LC-D07 P=60/Q=80 → 100 MVA and an independently solved
3-bus AC PF. Then S4–S9 (GK A conditions restated in GK A's 22:09 message: one
mutation per oracle, stated tolerances, f0 from the network, SAGC-1). Then the
GK B D-rows as in Hand-off 1.

Env notes:
- There is no `.pixi` here. Use `/root/.venv-pypsa-gui/bin/python` from `pypsa-gui/backend`.
  Test collection is slow (1–3 min), so run in the background with a log file
  and never two pytest runs at once: the container was OOM-restarted once.
- `test_lc06_d05_foresight.py`'s `seam` fixture is the reusable solve pattern.
  `get_simulation_status` gives status/condition/objective/failure, and
  `get_run_evidence(run_id, section='summary')` gives the run record.
- The mutation runner lives in the session scratchpad; it is easy to
  recreate: copy the test with one replacement and run `-k` on the target.

Ids: coordinator session_01G7qCuj4ML7V5FccYz7NghF; GK B (8)
session_01G7qCumQA1s15bnCz8WyJuA; GK A session_01G7qCum3HtNvhuPbBnJhiuB.
