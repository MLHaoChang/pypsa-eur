# A investment case: grey NH3 counterfactual

Status: needs-triage
Type: task
Blocked by: 05
Gated: **yes** (IC)
Model tier: opus
Size: S–M

Read [the design](../design.md) §3.A (the IC row and decision question 4) first.

## What to build

- **Commercial config.** `ReferencePurchase(load="nh3_delivery", price_eur_per_kg=0.501,
  lhv_kwh_per_kg=5.166, co2_t_per_kg=<demo>, source=...)`.
  - The price comes from `config.default.yaml:982`: 97 EUR/MWh_NH3 × 5.166 MWh/t ≈ 501 EUR/t.
    The source text says it is an upstream PyPSA-Eur default import price.
  - The CO2 price follows the staged H2 hub.
- **Check the counterfactual.** Confirm that `finance_case.py:1074` raises no
  `counterfactual_not_established:owner_conversion_load`.
- **Lost production in the case.** It enters as a cost line separate from the energy bill,
  through the engine (ADR-0004). Nothing is computed in the study layer.
- **Engine change.** If the case needs one (for example, a product slack Generator mistaken for
  an owner asset), describe it to the gatekeeper first.

## Acceptance

- The case computes with no blocker.
- With the electrolysers removed, the NPV against the counterfactual is about 0: the site buys
  the same grey NH3 either way.
