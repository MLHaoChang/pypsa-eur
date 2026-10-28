# LC-06 — whole-application behavior coverage

Status: ready-for-agent
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

- `smoke/family_manifest.py` assigns all 367 tools to the 14 families: 0
  unmapped, none in two families. Campus/GridSpine tools go by prefix, except
  the four campus library/investment tools, which go to economics. It lists
  72 variants: 19 covered, 51 pending, 2 unavailable (N-1/voltage compliance,
  EMT/PLL/voltage dynamics). `--check` fails on a stale tool, a duplicate, a
  covered node id with no test function, or a missing suite. A new tool is
  printed as `unmapped` and does not fail (pending GK B's choice).
- RED: `tests/test_family_manifest.py` errors on master (no module). Green
  after, together with `tests/test_assistant_control_matrix.py` (96 tests).
- Covered rows are the rebaseline's PS+O tests. Their oracles were read from
  the tests and were not re-derived here. `full_coverage=not_established`.
