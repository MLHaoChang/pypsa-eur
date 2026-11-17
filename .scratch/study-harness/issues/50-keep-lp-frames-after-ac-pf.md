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
