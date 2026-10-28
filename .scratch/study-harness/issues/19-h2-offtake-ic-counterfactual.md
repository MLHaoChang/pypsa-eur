<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 19 — H2-offtake IC counterfactual: single-year hub and the investment gate fact

Status: ready-for-agent
Type: task
Blocked by: none (11 keeps its investment rules `not_evaluated` until this lands)
Gated: yes (GK A reviews the plan and the PR)
Model tier: sonnet
Owner decisions: SH-1, owner decision on #312 option 1 (grey-H2 reference purchase), H2e (a) of 2026-10-15
Design: [design.md](../design.md) §9.3 "eh_h2_hub", §11; `.scratch/generic-site/design.md` §2 and slice 5 (Q3)

## Why

Design §9.3 and generic-site Q3 say the investment case cannot build a counterfactual when the site has an H2 offtake. That was true when generic-site was written; the engine change has since landed. What is missing is the single-year template's data and the fact the harness gates on.

## What the code gives today (read before planning)

- `services/results/finance_case.py` (docstring "H2e on this basis", near `:1467`): the counterfactual is the baseline re-solve of the site as it stands today (`services/results/baseline.py`). A non-electric load the owner's conversion feeds (the H2 offtake without the new electrolyser) is bought at its `CommercialConfig.reference_purchases` price through a `reference_supply:<load>` Generator, priced with the period's `co2_price`. The case is flagged `counterfactual_basis:site_today` and `counterfactual_buys:<load>`.
- Without a reference purchase that load blocks the case: `counterfactual_not_established:owner_conversion_load` (`finance_case.py:1132`), plus `counterfactual_reference_purchase_missing:<load>` when other purchases exist.
- `eh_h2_hub_staged` ships `GREY_H2` and a CO2 price (`project_templates/eh_staged_hubs.py:220`, `_h2_commercial_and_finance` at `:262`); `pypsa-gui/backend/tests/test_eh_staged_hubs.py:194` asserts the flags, the bought volumes and the NPV on a live solve.
- The single-year `eh_h2_hub` ships no commercial or finance block: `eh_templates._SOLVER_CONFIG_BUILT` has no entry for it and `project_templates/eh_h2_hub/solver_config.json` has none. Its investment case therefore cannot run at all.
- The investment case runs as a job (`chat_tools.run_investment_case`, `POST /api/results/investment_case`), read by `get_investment_case` (status `done`/`refused`/`failed`, headlines, flags).

## What to build

1. **Template data, single-year hub.** `eh_h2_hub` gets a commercial and finance block built like the staged hub's: the same `GREY_H2` reference purchase on `h2_offtake`, the CO2 price for its single year, the synthetic tariff, single-owner value flows, finance inputs for a single period. Register it in `_SOLVER_CONFIG_BUILT`. Demo values only, marked as such in the ledger. No engine change: if the plan finds the engine refuses a single-period case with a reference purchase, that is a finding for GK A before any engine edit.
2. **Gate fact** `ic.counterfactual` for the harness (read from the finished investment case, never by re-running it): `established` when the case is `done`, carries `counterfactual_basis:site_today` (or the analytic basis) and no `counterfactual_not_established:*` flag; otherwise `not_established` with the flag(s) as the reason; `not_run` when no case exists or it is stale. Issue 11's investment rules gate on it (`warn`), so they leave `not_evaluated` once it reads `established`.
3. **Report wording.** The case states that the counterfactual buys grey H2 at the reference price (the flag text already exists in `services/finance/baseline_text.py`); the harness report quotes it, never restates the price.
4. **Issue 11.** Its item 3 already points here; the PR that lands this updates 11's acceptance line so the investment rules are evaluated once the fact reads `established`.

## Acceptance criteria

- [ ] A project from `eh_h2_hub` (single-year) runs the investment case to `done` with `counterfactual_basis:site_today` and `counterfactual_buys:h2_offtake`, and no `counterfactual_not_established:*` (live-solve test).
- [ ] Removing the reference purchase from that project's commercial block makes the case report `counterfactual_not_established:owner_conversion_load`, and the fact reads `not_established` with that reason.
- [ ] The fact reads `not_run` with no case and after the network is edited (stale).
- [ ] `eh_h2_hub_staged`'s numbers in `test_eh_staged_hubs.py` are unchanged.
- [ ] The offtake stays demand: no template or rule makes it tunable (design §6.0).

## Test plan

- `pypsa-gui/backend/tests/test_eh_staged_hubs.py` or a sibling: the single-year block validates; a `live_solve` test for the single-year case and its flags, NPV pinned with a tolerance and a dated comment as the staged test does.
- A unit test of the fact reader over stored case results: `done` with the basis flag, `done` with a not-established flag, `refused`, absent, stale.
- `test_the_single_year_templates_are_unchanged` is updated deliberately for the new block, with the reason in the commit.

## Likely files

`pypsa-gui/backend/project_templates/eh_templates.py`, `pypsa-gui/backend/project_templates/eh_staged_hubs.py` (reuse `GREY_H2`, `_co2_price`), the harness fact reader beside issue 11's profile, the tests above, a release-note fragment.

## Comments
