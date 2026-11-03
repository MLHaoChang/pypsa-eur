<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-03 — hourly native results on buildings and process groups

Status: needs-triage — specification proposed; not implemented
Type: task
Dependencies: SC-02
Baseline: master 9988f9899a3bf8e80f51272529f18ea70599b90c

## Implementation assignment

Follow [execution-guide.md](../execution-guide.md) and the matching units in [execution-manifest.yaml](../execution-manifest.yaml). Freeze this assignment's actual-master file allowlist and public contracts; satisfy [decision gates](../decision-register.md) before dependent code, and record test-first evidence/unfinished work using [implementation-record-template.md](../implementation-record-template.md). Prototype N/B/P units close their own decisions through evidence; they do not require accepted experimental outcomes before starting. Primary V ownership does not remove cross-cutting obligations or authorize changes outside the frozen slice.

## Scope and acceptance

Write a known-hour numerical oracle before projection code. Reuse the shared timeline and native generator/Load/storage/branch/Link/Store series, freshness gates and selected project/run. Hourly values, weighted energy/material totals and process summary data are distinct. Do not repeat process running-hours or horizon production as hourly throughput.

Use reviewed native adapters for multi-port process quantities and group boundary accounting. Preserve MWh LHV versus tonnes and native factors. Unknown conversions and invalid/missing mappings serialize value=null, available=false and a reason; genuine zero is available=true. Pin those exact JSON shapes per ADR-0001. Explicit allocation stays labelled allocated, conserves the native aggregate and does not become measured building demand. Avoid counting internal transfers twice.

A geometry edit keeps an otherwise valid native run; an electrical input mutation invalidates it normally. Test cancelled/failed/stale runs, multi-period/representative snapshots, absent source and wrong project. Animate only the rendering; smoothing does not add higher-time-resolution simulation.

Done: V22–28, V57 and applicable domain fixtures, with native numerical evidence at exact hours/periods. No solver, dynamic simulation, live telemetry or Blender-per-hour job is added.

## Validation

V22–28, V15–16, V38, V48, V51, V57; definitions in ../validation-matrix.md. Tests before implementation at the frozen public seams; no passing evidence is claimed by this ticket.

## Coordination

One bounded implementation PR after applicable plan review; current master and open-PR overlap check first. Follow AGENTS and steward, add a release fragment for shipped behavior, never change compiled release notes or another task branch. Record exact tests, genuine worker evidence and unimplemented parts in the PR and handoff.

## Cross-cutting AI guidance

Deliver this slice's in-place explanation, typed proposed action or truthful UI fallback, native read-back and resume context under [ai-guided-project-flow.md](../ai-guided-project-flow.md), alongside SC-07. Server flow state and current approvals remain authoritative. Include the relevant V61–V72 assertions; optional engine absence cannot block the native/manual flow.

## Optional coordinate-first workflow clarification

Playback uses the actual native snapshot cadence, including quarter-hourly, hourly, representative and multi-period data; the title's hourly case is an example, not a restriction. Project native edge/port quantities and units onto valid physical mappings. Keep stale/missing quantities unavailable and interpolation purely visual. Cover V87–V88 and prior freshness/numerical cases. Read [optional-site-workflow.md](../optional-site-workflow.md) and [workflow-assessment.md](../workflow-assessment.md).

## Comments

### 2026-11-03: D07 plan verdict, GK A (11): GO @a0fdf73f with conditions

Draft: [d07/plan.md](../d07/plan.md). Oracle re-derived by GK A at t3. Conditions binding on Step 2:

1. Units: read the raw `buses.unit` beside `process_products.bus_unit`. `""`, `"None"`, NaN, `"MW"` and `"MWh"` mean MW, `t` means t/h, and anything else (kg, GJ) is `unit_unknown`. Tested.
2. A StorageUnit's rate and SoC take the bus unit, as a Store's do (t/h and t on a tonne bus). Tested on a t bus.
3. The Link port role from the efficiency sign is nominal. Add `reversed: true` when the value's sign disagrees with it (p_min_pu < 0). Tested. A link with two ports on one bus: the group sums both. Tested.
4. `snapshot` accepts the native integer label of a RangeIndex network. A tz-aware query against a naive index, or a missing `period` on a multi-period network, gives `invalid_input`, never a 500. Each is tested.
5. The run id is `services.dispatch_status.dispatch_digest` (str), not `services.results.dispatch_digest` (dict). `run_changed` is tested.
6. Freshness has one source: availability is gated by `solve_matches_inputs(ctx)` itself, and the finer reasons only label it. One test per reason, including after an AC PF. A mutant that drops the gate must fail.
7. Run `test_route_chat_disposition`, `test_project_route_acl_policy`, `test_write_surface_lock_policy` and `assistant_route_parity --check` at the trial merge. The GET leaves the network revision, `n.meta`, solver_state and the D06 uids unchanged. Tested.
8. Store `e` and SoC are never window-summed. Test with generators ≠ stores weightings. A window across two periods applies years per period, with a test, or is refused.
9. Internal bus: enumerate every class with a port on the bus (Line, Transformer, ShuntImpedance, and bus2..4 of non-member Links). A non-member Link's bus3 on a would-be-internal bus makes it a boundary bus. Tested.
10. Shares of one target summing above 1 (beyond tolerance) are refused. Tested.
11. The closed reason-code list is pinned by a test.
12. The oracle values go into the tests as hand values.

GK A leanings on the owner choices: A backend route, B vintage rows unavailable in SC-03, C unavailable after an AC PF.
