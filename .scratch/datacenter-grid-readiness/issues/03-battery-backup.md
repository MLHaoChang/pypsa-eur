# Battery-based backup in place of diesel

Status: ready-for-agent
Model: Opus
Type: task
Blocked by: 01
Gated: yes (adequacy, finance)
Model tier: opus
Size: M

## What to build

- A backup-mode setting on the DC load: `genset | bess | hybrid`.
- BESS sizing for ride-through plus N hours of autonomy for the critical share, honouring redundancy (N, N+1, 2N).
- The DtC stress re-dispatch proves critical load is served on loss of grid.
- Investment case: avoided genset capex/fuel/run-hours against BESS capex, augmentation and fade (`BessSpec` already has them).

## Done when

A DC project can switch to `bess` and see adequacy plus the cost delta against `genset`.

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage`. Files: DC load model and template parameters, `services/adequacy/` (DtC stress re-dispatch), `BessSpec` sizing and finance. Blockers: 01 is done (#421). Reviewers: GK A (adequacy) and GK B (finance). Unblocks 05, 06, 07.
