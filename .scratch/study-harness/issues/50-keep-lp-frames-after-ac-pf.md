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
