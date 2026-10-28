<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 20 — Microgrid diesel-fleet IC baseline: the investment gate fact for eh_microgrid

Status: needs-triage
Type: task
Blocked by: 19 (the `ic.counterfactual` fact reader)
Gated: yes (GK A reviews the plan and the PR)
Model tier: sonnet
Owner decisions: SH-1, M2e (a) (the island as it stands today is the baseline)
Design: [design.md](../design.md) §9.3 "eh_microgrid", §11; `.scratch/generic-site/design.md` §3 and slice 5 (Q4)

## Why

Design §9.3 and generic-site Q4 say the microgrid's investment baseline, the existing diesel fleet, needs an IC engine change. It has landed: #302's baseline re-solve gives it with no engine change (owner M2e (a)). What is missing is the harness reading it, so issue 12's investment rule can leave `not_evaluated`.

## What the code gives today (read before planning)

- `services/results/baseline.py`: the counterfactual of a case whose owner keeps existing assets is a second solve of the same network with the plan's new builds removed (extendable increments and later-period candidates go; sunk rows at overnight cost 0 stay).
- `eh_microgrid` and `eh_microgrid_staged` both ship a commercial block with the open subsea tie as the PoC and no tariff (`eh_templates._microgrid_commercial_and_finance`, `eh_staged_hubs._mg_commercial_and_finance`), and `MG_EXISTING` (`diesel_1..3`, PV, wind, the island battery, the hospital feeder) sunk and in service.
- Tests already pin it: `pypsa-gui/backend/tests/test_eh_microgrid_case.py:97` (single-year) and `tests/test_eh_staged_hubs.py:258` (staged) assert `counterfactual_basis:site_today`, no `counterfactual_not_established:*`, no `counterfactual:shed_at_voll`, the existing diesels' opex lines, and the NPV.

## What to build

1. **Profile rule.** Issue 12's investment rule (`warn`) gates on 19's `ic.counterfactual` fact, unchanged: `established` evaluates the rule, anything else stays `not_evaluated` with the fact's reason.
2. **Baseline guard fact.** `ic.baseline_keeps` lists the owned existing assets the baseline kept (from the case's baseline plan, `BaselinePlan`), so the report can say "compared with the island as it stands today: diesels 1–3, PV, wind, battery". The rule reads `not_established` if any `diesel_*` in `MG_EXISTING` was removed from the baseline (a template edit that priced a diesel would do that), with that as the reason.
3. **Shed in the baseline.** When the baseline sheds load (an undersized fleet), the case already prices it at VoLL (`counterfactual:shed_at_voll`); the report names it as such and the rule stays evaluated, since that is the case's own stated basis, not a gap.
4. **Correct the design.** In the PR that lands this, issue 12's item 4 points at the fact instead of "an IC engine change".

## Acceptance criteria

- [ ] On `eh_microgrid` and `eh_microgrid_staged` a finished case makes the investment rule evaluated, and the report lists the kept existing assets.
- [ ] A test project whose `diesel_1` carries a non-zero overnight cost reads `not_established` with the reason naming `diesel_1`.
- [ ] A baseline that sheds is reported with `counterfactual:shed_at_voll` named, and the rule is still evaluated.
- [ ] The NPVs pinned in `test_eh_microgrid_case.py` and `test_eh_staged_hubs.py` are unchanged (no engine change).

## Test plan

- Unit tests of `ic.baseline_keeps` and the diesel guard over a stored case and baseline plan (no solve).
- One `live_solve` test on `eh_microgrid` through the profile's rule, reusing the existing case fixture.

## Likely files

The harness fact reader beside 19's, issue 12's profile rules, the tests above, a release-note fragment.

## Comments
