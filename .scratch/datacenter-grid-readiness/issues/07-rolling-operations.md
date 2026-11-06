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
