# Rolling operational optimisation

Status: ready-for-agent
Model: Opus
Type: task
Blocked by: 03, 06
Gated: yes (solver)
Model tier: opus
Size: L

## What to build

- Rolling-horizon dispatch of BESS, flexible/curtailable load and import against prices and grid limits, on fixed capacities from the plan.
- Reuse the staged/operational solve. Report the plan-vs-operation gap.

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage`. Files: staged/operational solve (rolling horizon), plan-vs-operation gap report. Blockers: 03, 06. Reviewer: GK A. Size L: the implementer should propose a split in its plan.

### 2026-11-06: plan verdict (DC-07, session_01G7qCukWKZgPtG5Y6g3bGVV)

GK A (16): GO with conditions. Slices (PR-SIZE-1), each one PR of about 1,500 lines or fewer, all GK A:
- S1, operational rolling.
- S2, curtailable load. It is independent of S1 and can land in either order.
- S3, gap service and read route. It lands after S1.

Answers:
- Q1 (a): the comparison is a full-horizon dispatch on the same frozen sizes.
- Q2 (a): curtailable only. Shifting (`workload_shift_window_h`) is its own follow-up issue.
- Q3: the UI (S4) is not gated by GK A; it went to the coordinator.

S1:
- C1 Cyclic flags are off for StorageUnits and Stores, both the plain and the `*_per_period` flags, with a typed note. Window 1 starts from the configured initial SoC/e. The flags and initials are restored afterwards.
- C2 Status is checked per window. On a failure the run stops, and a typed error names the window. A failed window's dispatch reads not-solved, never 0, and never seeds the next window. Test: an infeasible mid-horizon window.
- C3 A table classifies every `build_lp_chain` row as per-snapshot or horizon-coupled. Each coupled row is either refused (typed) or windowed by a stated rule with carry-in, and tested. The table goes in the PR body.
- C4 Opex comes from the stitched dispatch × snapshot weightings over the full horizon, never from summed window objectives. Tested with O>0.
- C5 Tests:
  - (i) H ≥ len equals the full-horizon fixed-size dispatch;
  - (ii) SoC is continuous at every boundary, and the stitched results equal the last-written windows;
  - (iii) the reserve floor holds in every window;
  - (iv) plain rolling is byte-equal to master (LP and network.nc).
- C6 The run is refused if anything is still extendable after the freeze. Rolling + fixed sizes + multi-investment periods is refused, typed, with no silent full-horizon fallback.
- C7 The op run keys on the sizes source, H, O and the weightings. A plan change makes it stale. Myopic + fixed stays refused.

S2:
- C8 critical_share + curtailable_share ≤ 1. The cap is p ≤ curtailable_share·p_set(t).
- C9 The proxy for curtail_max_duration_h is stated as a claim limit and tested to bind. Under rolling it carries in, or it is listed in the C3 table.
- C10 The curtailment Generator has its own carrier and is never counted as generation. Every consumer that sums generators is checked, each with a test. Curtailment is off unless a price is set. A share > 0 with no price stays off and gets a typed note.
- C11 Off is byte-equal: LP, network.nc and fingerprint.

S3:
- C12 The comparison uses the same sizes, initial SoC and non-cyclic settings. It is labelled "full-horizon dispatch on the same sizes". It is persisted with the staleness rule. Missing or stale reads None + flag.
- C13 The route policy tests are run at the trial merge: chat disposition, project ACL and write-surface lock.
- C14 Each metric is defined in the response. Failed windows are counted.

### 2026-11-07: S2 design answers (DC-07, session_01G7qCukWKZgPtG5Y6g3bGVV)

GK A (16): Q1 (a), Q2 (a), Q3 (a), with conditions C15–C21 added to S2. S1 is PR #1218; it records the plan verdict above this entry on its own branch.

- Q1 (a): `DataCentreLoadSpec.curtailment_price_eur_per_mwh: float | None = None`, read at bind time through `dc_backup.project_spec` / `solve_target_context` (the DC-06 `project_backup` pattern).
  - C15: None, or curtailable_share = 0, leaves the network, LP, network.nc and fingerprint byte-equal (tested). The price is ≥ 0 and finite. 0 is allowed only when set explicitly, never as a default.
- Q2 (a): a transient Generator `__dccurt_<load>`, carrier `dc_curtailment`, on dc_mv (the cooling load's bus). It follows the DSR lifecycle (`services/solver/assumptions.py` ~1108: mark_transient, capture, remove). It goes in slack.py's SLACK_CARRIERS / prefixes, but not in the involuntary tier.
  - C16: the cap is p ≤ min(curtailable_share·F, cooling)(t), where F = it_load + cooling. Tested on the built network: p_max(t) ≤ the cooling p_set(t) at every t, including with the PUE temperature slope and the AI workload on. The generator never feeds the IT bus.
  - C17: a PR-body table lists every LP row builder that iterates generators or bus supply while the transient exists: commercial_bindings (group cap, group-net, PPA matching), sizing_cuts, DC-06 reserve headroom and floors, DC-03 envelope/backup, reserve_margin, island and curtailment_cost. Each one excludes `dc_curtailment`, as DSR is excluded today; the table says whether DSR relies on slack_generator_mask there. There is one test per row family a curtailment MW could relax (e.g. a group-net import cap is not loosened).
  - C18: curtailed energy is reported apart from unserved energy, never summed with it, and never counted as ENS for DtC or adequacy. A project with no dc_mv cooling load is refused, or carries a typed note; the generator is never placed elsewhere.
- Q3 (a): the energy proxy. Over every run of consecutive snapshots spanning D + one snapshot of weighted hours, Σ w·p_curt ≤ D·share·max p_set over the run.
  - C19: the claim-limit text ("energy proxy: curtailment can still be spread; consecutive hours are not enforced") appears both in the result (n.meta) and in the docs. The run is measured in weighted hours. Tested with 2-hourly snapshots.
  - C20 (carry-in under the operational run): only snapshots before the window start are constants. With O > 0 they are final. Tested at a boundary with O > 0 that the carry-in binds, and that a failed window is never a carry-in source.
  - C21: rows ≈ snapshots × run length. The row and nonzero counts are logged. A typed refusal applies when D exceeds the horizon (full run) or the window (operational run).

Also from the S2 mapping: DataCentreLoadSpec `_shares` already refuses critical + curtailable > 1 (flex_archetypes.py:175), so C8 needs a pin test only. The template splits it_load = critical·F on it_bus and cooling = (1−critical)·F on dc_mv (eh_templates.py:619; also dc_architecture.py:786).

### 2026-11-07: S2 built (DC-07 S2, session_01G7qCuizo1mH5kACP1VKbiV)

`services/solver/dc_curtailment.py`: `DataCentreLoadSpec.curtailment_price_eur_per_mwh` (None default, finite, ≥ 0), a transient `__dccurt_cooling` on dc_mv (carrier `dc_curtailment`, a slack but never involuntary), cap min(share·F, cooling)(t), energy-proxy duration rows over weighted-hour runs, typed refusals `curtailment_refused: no_cooling_load | duration_exceeds_horizon`, the run's record in `n.meta["dc_curtailment"]` with the claim limit (also in `pypsa-gui/CONTEXT.md`). C15–C19 and C21 tested in `tests/test_dc_curtailment.py`. C20 (carry-in under the operational run) waits for #1218's `services/solver/operational.py`.
