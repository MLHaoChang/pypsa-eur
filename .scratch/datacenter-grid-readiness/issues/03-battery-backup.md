# Battery-based backup in place of diesel

Status: needs-triage
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
