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
