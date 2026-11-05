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
