# B investment case: a re-solve counterfactual for a product Load

Status: needs-triage
Type: task
Blocked by: 10
Gated: **yes** (IC)
Model tier: opus
Size: M

Read [the design](../design.md) §3.B (the IC paragraph) first.

## What to build

- **The blocker.** The concentrate Load is non-electric and served by the owner's conversion,
  so `finance_case.py:1074` blocks the case (`owner_conversion_load`). There is nothing to buy
  in.
  - Accept a product Load when the counterfactual is the baseline re-solve. The baseline is the
    site as it is today: same process, existing connection, no candidates.
  - Use the mechanism #302 gave the microgrid.
  - The lost-production slack carries the difference in tonnes.
- **Report** the delta in lost tonnes and EUR as its own line.

## Acceptance

- The case computes with no blocker.
- With no candidates, NPV = 0 and the lost-tonne delta = 0.
