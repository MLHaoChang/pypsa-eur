<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 10 — Harness profile: eh_datacenter and eh_datacenter_staged

Status: ready-for-agent
Type: task
Blocked by: 07
Gated: yes (GK A for the engine gates and the grid-readiness actions; GK B for the harness parts)
Model tier: opus
Owner decisions: SH-PILOT-1, SAGC-1, D3-KEEP, DC13-SCOPE, TOOLSEL-1
Design: [design.md](../design.md) §5, §6.0, §9.3 "eh_datacenter"
Plan verdict: GK A GO, 28 Oct (screening rows, verdict gating, never-tunable list); sources: public SAGC (May 2026) and generic engineering only

## What to build

1. Profiles `eh_datacenter@1` and `eh_datacenter_staged@1` (per-period rules over 2027/30/35 from `TEMPLATE_META`).
2. Native actions for the runner: the campus/gridspine run that produces the SCR/ESCR, PQ, contingency and P–V/Q–V rows, and the grid-readiness read (`grid_readiness_for`). Same admission, ACL, lock and audit path; not offered to the model.
3. `dc.readiness.*` and `dc.gridcode.*` fact readers. Input stage waits (`needs_user`, reason "publish the grid-code profile") until a published profile exists; limits are never tunable or researched into the profile.
4. Only if chat must start these studies: one `grid_readiness_study(kind=scr|pq|contingency|pv_qv)` catalogue tool, `execution` tier, with the TOOLSEL-1 selection test.

## Acceptance criteria

- [ ] A DC fixture with a published blank-schema profile (SAGC fields filled per project) runs to hand-off; each scorecard row is a gate result by verdict (only `pass` passes), with its `assumed` flag; screening rows read "within the screening limit", and a pass on assumed inputs is never "verified".
- [ ] P–V/Q–V gates through the scorecard rows `dc.readiness.voltage_stability_pv` and `dc.readiness.voltage_stability_qv` as `warn` (a margin is screening; issue 18), read through `grid_readiness.readiness_fact`, so a row excluded off-grid is not evaluated with its reason, never a pass.
- [ ] Grid-code publish is always the user's own card; the envelope never admits it.
- [ ] The profile has no `dc.readiness.*` wildcard: a table lists each row id or family with its severity (`block`/`warn`), a one-line basis and the run that produces its input (this also answers which run produces each SAGC study); a `warn` row that is `unknown` reads "not evaluated".
- [ ] On the shipped template (19 of 20 rows `unknown`), the run does not end `needs_user` on `warn` rows.
- [ ] Family rows (`energization_peak:*`, `dynamic:*`, `ride_through:*`, `motor_start:*`) evaluate all rows of the family.
- [ ] No published profile → the run waits at stage 2 and notifies; nothing is drafted or published by the run.
- [ ] A contingency failure: L2 changes only tunables; taps and shunts only in stage 2 after an outage (DC13-SCOPE); redispatch is not attempted.
- [ ] 60 Hz project: the frequency rule passes; a 50 Hz network under a SAGC profile fails `block`.
- [ ] Staged: rules evaluated per period; the investment case (NPV/IRR) appears in the result gate.
- [ ] Offered-tool count stays within the 128 limit with `validate_network` still selected (TOOLSEL-1 test).

## Likely files

`pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/services/run_actions.py` (new, native action registry), `pypsa-gui/backend/harness/workflows/profiles/eh_datacenter/`, `pypsa-gui/backend/tests/test_run_profile_datacenter.py` (new)

## Comments
