# B investment case: a re-solve counterfactual for a product Load

Status: ready-for-agent (triage 2026-10-28: still blocked by 10; gated IC, plan to GK A first)
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
- **Shortfall** is lost revenue in both the project and the baseline, never an expense twice.
  The lost-production value is a ledgered input, never VoLL (#312).

## Acceptance

- The case computes with no blocker.
- With no candidates, NPV = 0 and the lost-tonne delta = 0.

## Triage (2026-10-28)

**Verified on master (41ade3be):**
- The blocker is still there. `services/results/finance_case.py` appends `counterfactual_not_established:owner_conversion_load` when an owner conversion Link feeds a non-electric load with no `reference_purchases` entry. The line numbers have moved since the design was written.
- The "mechanism #302 gave the microgrid" is the site-today baseline re-solve in `services/results/baseline.py` (`plan_baseline`, owner decision 2026-10-15). It already removes the plan's new builds and solves again.

### Agent brief

**Category:** enhancement (gated IC: send GK A the plan before code)
**Summary:** a product Load served by the owner's process takes the site-today baseline re-solve as its counterfactual, instead of blocking the case.

**Desired behaviour:**
- When an owner conversion feeds a **product** Load (a bus that carries a `process_role` = `shortfall` Generator, the predicate `product_role_generators` that tickets 03, 04 and 08 share), the case does not block. Its counterfactual is the existing baseline re-solve: same process, existing connection, no candidates.
- A non-product load (heat from the owner's heat pump) keeps today's rules (block, or `reference_purchases`).
- The case reports the delta in lost tonnes and the delta in lost-production EUR as its own line, separate from the energy bill.
  - The shortfall Generator's marginal cost *is* the lost-production value, so lost tonnes are already inside each solve's operating cost.
  - The line is therefore **split out** of the opex delta, never added beside it (GK A, 2026-10-28).
- Shortfall is lost revenue on both sides and is counted once. The lost-production value is the ledgered bus input, never VoLL (#312).

**Acceptance:**
- [ ] On `eh_concentrator_staged`, the case computes with no blocker.
- [ ] With no candidates, NPV = 0 and the lost-tonne delta = 0.
- [ ] The microgrid, ammonia and H2-hub staged cases keep their NPV to the unit.
- [ ] A heat-load case without `reference_purchases` still blocks with `owner_conversion_load`.
- [ ] Reconciliation: the case's line items (energy-bill delta + lost-production delta + the rest) sum exactly to the NPV's cash flows, per year. One test changes only the shortfall (same energy bill) and sees the change in the lost-production line alone.

**Out of scope:** new baseline mechanics; buy-in prices for products.

**Likely files:** `pypsa-gui/backend/services/results/finance_case.py`, `pypsa-gui/backend/services/results/baseline.py`, tests under `pypsa-gui/backend/tests/`.
**Blocked by:** 10.
**Model:** Opus (IC engine, gated).
