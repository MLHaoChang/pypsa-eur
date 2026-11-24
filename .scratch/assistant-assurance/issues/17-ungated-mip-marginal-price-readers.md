<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Readers outside checked answers still show a MIP's 0.0 bus prices

Status: ready-for-agent
Type: bug
Found by: GK B (65) on #1680 (issue 10, S2).

## Problem

On an integer (MIP) solve, HiGHS returns no duals, and PyPSA writes 0.0 to
`buses_t.marginal_price`. S2 (#1680) marks those prices unavailable
(`mip_duals_not_marginal`) in checked answers and in the checked bus-price
chart. Every other reader still shows the 0.0 as a price: the Results price
views, Compare, reports, headline KPIs, LCOH and `prices_digest`.

## Acceptance

- Each reader of `buses_t.marginal_price` either reads the solve kind through
  `harness/solve_validity.classify` (or a shared service equivalent) and
  shows "not available on an integer solve", or is listed with a reason why it
  is safe.
- A committable-unit test per reader shows that no 0.0 is presented as a price.

## Comments

## Triage (2026-11-20, master 87cf52a7)

Checked by code reading only. Still true: only `checked_charts.py` and `harness/solve_validity.py` handle `mip_duals_not_marginal`. Slice by reader (Results price views, Compare, reports, headline KPIs and LCOH, `prices_digest`), PR-SIZE-1. Acceptance is the section above. Gated: GK B. Blocked by: none.

## Readers on master 84857588 (2026-11-24)

Gate: `harness/solve_validity.mip_price_limit(n, config)` (classify's MIP test). `solver_service` records it in `last_solve_settings["price_limit"]` (only when set), and `solved_price_limit(state, n)` reads that record. A saved project in Compare is judged by `routers/compare._price_limit`, which fails closed with `solver_config_unreadable`.

Slice 1 (backend), gated:
- `services/results/prices_digest.py` (live and retained digest): unavailable with reason `mip_duals_not_marginal`.
- `services/compare/prices.py`: `PricesComparison.unavailable_reason`, against the project's own saved config.
- `services/results/lcoh.py` and the EH TEA LCOH (`services/adequacy/eh_report.py`).
- The decision-study report (`services/study/report.py`, via `runner._read_option`): LCOS incl. charging and market revenue at duals.

Slice 2 (Results UI and the rest), still open:
- `services/compare/economics.py` and `results/economics_by_carrier.py`: revenue and storage charge cost become nullable on a MIP, and `per_asset_lcoh` is withheld, together with the Compare and Results economics UI (GK B2 A3: no numeric placeholder before the UI renders null)
- `services/results/prices.py` (prices, price_drivers)
- `carrier_kpis.py`
- `asset_economics.py`
- `asset_results/compute.py price_at_bus`
- `results_workbook/views_network.py`
- `study/workbook_view.py` market revenue
- the Compare UI rendering of the two flags
- headline KPIs (after #1948)

Safe:
- `ac_pf_service.py` snapshots the frame for the AC power flow; it shows no price.
- `dispatch_status.py` clears the frame or hashes it.
- `solver/objective.py` rescales the duals (a write).
- `solver_service.py` logs the frame's shape.
- `checked_charts.py` renders `price_calculations`, which is already gated.
- `user_timeseries.py`, `economics.py`, `routers/projects.py` and `models/schemas.py` hold comments or docstrings only.
- `smoke/qa_e2e.py` checks only that the import is callable.

## GK B2 plan GO conditions (2026-11-24)

- **A1:** Gate on the solve that made the prices, not on today's config. The limit is recorded at solve time in `last_solve_settings`.
- **A2:** Fail closed when a saved config cannot be read (rulings #1079 and #1009).
- **A3:** No numeric 0 for an unjudged value. Compare economics moved to slice 2, with its UI.
- **A4:** An LP report and its detail stay byte-equal to master's. Reports stored without `price_limit` keep working.
- **A5:** Each gate is killed by a test. The killing test is named in the PR body.
- **A6:** If a new reason reaches a chat tool result, add a kind fragment. Run the manifest, ChatPanel and inventory checks at the trial merge.
- **A7:** Keep the reasons the "safe" readers are safe.

Slice 2 condition (GK B2 A3): on a MIP, Compare and Results economics show revenue and storage charge cost as null and "not available", never 0.

Known edge (GK B2 A1): a MIP solved before the `last_solve_settings["price_limit"]` record existed, whose config has since been changed to make it an LP (`process_uc` on a process-unit network), is judged under today's config. Every other record-less case errs toward withholding.

## GK B2 slice 2 plan GO (2026-11-24)

Slice 2 is two PRs (PR-SIZE-1): 2a economics, then 2b prices. Each opens after #1957 merges. 2b's results_workbook part waits for template-e2e 30 (coordinator ruling).

Rulings:
- (a) The 2a/2b split is accepted.
- (b) On a MIP, the prices fallback (generator marginal cost shown as a price) is withheld. On an LP it stays byte-for-byte.
- (c) `CashFlowYear.market_revenue_at_duals` is rendered: it is a study-workbook cash-flow column. So it is `float | None`: None with the reason flag on a MIP, and 0.0 only in a year when nothing operates (year 0, the build year, for every case).

Conditions:
- **S1, no silent partial sums.** A total over a withheld part reads not available, or is labelled with what it excludes (UX words). This covers CompareView, Economics.tsx, the Dispatch sums and the backend aggregates, including the carrier totals. Test it with a mixed case.
- **S2, one reason on every surface.** The reason is worded through sectionWords, and no surface shows 0, "—" or NaN. Each touched component has a vitest.
- **S3, the right solve, fail closed.** Each reader judges by the recorded limit of the solve that produced the values. Compare judges each project by its own record, then its config, and fails closed. A workbook scenario is judged by its own state.
- **S4, LP unchanged.** Compare economics, asset_economics, carrier_kpis, prices, price_drivers and any touched workbook view are byte-equal to master on an LP.
- **S5, mutants.** Each gate call and each None substitution, including the `_safe_finite` skip on the MIP path, has a named killing test.
- **S6, chat and parity at the trial merge.** Run the tool-error-kind manifest, ChatPanel.manifest, `assistant_inventory --check`, plus route_parity and control_matrix if routes, tools or the frontend change. A chat tool that returns these payloads is tested on a MIP.

2a refinements (GK, 2026-11-24):
1. The storage charge cost is inside `opex_meur`. So a carrier that booked one has opex and LCOE None on a MIP, while other carriers keep theirs, and the Σ-carriers total reads None. Mutate the condition both ways.
2. The year-0 0.0 rests on "no operation that year". Year 0 is the build year for every case (`engine_adapter`: no bill and no saving counted), and a test pins that.
3. On a MIP, only the reading notes that rest on the LP's duals are dropped (the zero-profit note). A price-independent note survives, and a test pins it.

UX 28 placement GO: one cell wording ("Not available (integer solve)", from sectionWords `short`, pinned by a parity test). A total that skips a withheld part says so beside the figure. Expert shows no less than Guided. No toast. Before/after shots in Guided and Expert, light and dark, on each PR.

Follow-up: `27-study-report-lp-flags-untested.md`. These are the LP-path flags in the study report that survived slice 1's mutant run; GK ruled they don't block #1957.
