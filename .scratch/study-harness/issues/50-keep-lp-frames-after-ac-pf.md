<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 50 — Keep the LP's dispatch frames after the AC power flow

Status: needs-triage
Gated: GK A
Type: task
Parent: SH 15 (grid_only@1) hand-off follow-up; owner decision SH15-PF-1

## Problem

With Load flow on (`run_ac_pf_after_lopf`), the AC power flow after the LOPF rewrites the live `generators_t`, `storage_units_t` and `links_t` frames (the slack generator's dispatch above all). Cost and CO2 read from the live network after the flow are then PF-contaminated: on `3bus` the read cost is 61734.29 against the LP objective 61733.46. So `grid_only@1` reads no headline KPIs and no hand-off KPI rule (`kpis: []`, `missing_tools`), and the retained-results and hand-off readers stay on the LP-only guard (GK A1).

## Ask

Keep the LP's `generators_t`, `storage_units_t` and `links_t` frames (or a labelled copy of them) after the AC power flow, so KPIs are read from the LP dispatch while the PF rules read the stored AC PF result. Then give `grid_only@1` its headline KPIs and hand-off KPI rule, with a test that the KPIs after a PF equal the LP's.

Two more parts of the same root cause:

- **Saved dispatch.** A Load-flow solve also saves the PF-rewritten slack dispatch to `network.nc`, not the LP's. In the SH-15 run tests, the saved `generators_t.p` differs from `lopf_results` by 0.0028 MW on 3bus and by 0.077 MW on ieee14. This issue covers persisting the LP dispatch too.
- **Report stage.** `grid_only@1` skips the report stage until this issue lands (GK B (61)).

## Done criteria

- KPIs after a PF equal the LP's.
- The saved dispatch is the LP's.
- `grid_only@1`'s report stage is re-enabled, with a test that its figures are the LP's.

## GK B (61) and GK A (28), 2026-11-17 (SH-15 known limitation)

1. **Measured repro.** On the slack, against `lopf_results`: 3bus 0.0028 MW, ieee14 0.077 MW. The gap grows with network losses. On a large lossy grid, the persisted slack energy, cost and CO2 can differ materially from the LP objective.
2. **Readers that disagree after a reload.** Once persisted, `generators_t.p` disagrees with the solve record for: energy flows, emissions, the cost breakdown, and #851's lost-load and branch-overload screens.
3. **Fix (engine side).** Keep the LP frames and persist the PF results separately, for example under a `pf_` namespace.
   - Done criterion: a test that the persisted `generators_t.p` equals `lopf_results` on a lossy template.

## Comments

### Owner decision PF-SAVE-1 (2026-11-17)

A saved project keeps the LP dispatch in the normal result frames. The AC power flow's results are kept under their own names (`ac_pf_results` and the `ac_pf_*` state keys). Only a screen that asks for load flow reads them.

### GK A (29) plan GO, with conditions (2026-11-17)

The approach is to restore the LP values into the live frames, keep the PF values only in `ac_pf_results`, and add links to the snapshot. Each condition is a property plus the test that shows it.

- **F1 Exact.** Test (a) compares with `==` (bit-equal), never a tolerance.
- **F2 Every outcome.** The restore runs in `finally`. Success, non-convergence, a raise inside the PF and a raise after the PF snapshot all leave the live frames equal to `lopf_snapshot`. Test it with an injected raise inside `pf()`, and kill the mutant that moves the restore out of `finally`.
- **F3 No stale PF values.** After the restore, the live q, `v_mag_pu`, `v_ang` and the PF-only columns hold what the LP snapshot holds, never a value from an earlier PF.
  - Test: LOPF→PF, edit a load, LOPF→PF again, save and reload. The live q/v equal the second LP snapshot.
  - If the LP snapshot itself carries an older PF's q/v, clear those columns instead of restoring them.
- **F4 Multi-port links.** Restore every `pN` that `links_t` has (p2, p3, …), not only p0/p1. Test it with a 3-port link.
- **F5 Projects saved before this change.** Their `network.nc` carries PF values.
  - On reload, when `results_state` has `lopf_results` that differ from the frames, the LP values win (restore on load).
  - When there is no `lopf_results`, show "dispatch saved from load flow" instead of silently showing PF values as LP.
  - Build the fixture with master's writer, never a hand-made shape.
  - A missing links key in an old `ac_pf_results` reads as no data, never a KeyError.
- **F6 One lock hold.** The PF write and the restore happen in a single mutation-lock hold, so no reader sees PF-written live frames in between. Show that a reader hooked between the write and the restore waits, or say why no reader can get there.
- **F7 Not an edit.** The restore moves no network revision, no `_network_input_fingerprint` and no undo entry. Pin the fingerprint and the revision before and after the PF stage.
- **F8 Reader audit.** The PR body carries a table: each reader, its file:line, LP or PF, and the test that pins it.
  - Build the table from a grep over backend and frontend for `q0|q1|v_mag_pu|v_ang|\.q\b|source=ac_pf`.
  - Every PF screen gets one test: PF values after a reload.
  - Every default screen gets one test: LP values after a PF.
- **F9 Ruling 15 holds.** `run_grid_pf` and N-1 read the stored PF snapshot. The existing verdict tests stay unchanged and exact.
- **F10 Mutants.** Run each targeted mutant on its single test with `-x` and a timeout: drop the restore, restore before the snapshot, drop links from `_RESULT_DYNAMIC_ATTRS`, drop the legacy reload restore. List the survivors in the PR body.

The SH 50 profile parts and SH 51 go to GK B and UX.
