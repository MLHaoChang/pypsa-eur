# IC U1: landing the engine (P3 + P4 PR) and the U1 follow-up

**Date:** 2026-10-05. **Session:** "Energy tool feature research and benchmarking" (the IC session).
**Coordinating plan:** `docs/superpowers/plans/2026-10-05-one-investment-engine-two-faces.md` (on
`claude/determined-tesla-np09ww`, PR #78's branch): this file is the IC session's record for its phase U1
(§5) and the place where the IC session answers the guided study's (GS) engine questions (§9 of
`docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md` on `claude/edge-tool-ux-research-n0n2l6`).

## 1. Two pull requests

| PR | Branch | Content | Merge order |
|---|---|---|---|
| **U1** | `claude/energy-tool-features-research-fdixs0` | P3 rest + P4 (finance engine), master merged in, Q2 (an Investment-tab route check) and Q7 (plain chip labels) | first |
| **U1 follow-up** | `claude/energy-tool-features-research-fdixs0-u1-followup` (based on the U1 branch) | items (a)–(g) below, the facade freeze, the GS answers; S0b and what builds on it last, after S0 (PR #78) merges | second, before U2's PR |

The owner merges both; no session merges to master.

## 2. Owner decisions for the follow-up (2026-10-05)

| # | Decision |
|---|---|
| D1 | The follow-up is its own branch and PR, based on the U1 branch. The U1 PR is watched (CI, review) and never merged by the session. |
| D2 | **(a) The generic defaults pack is in-tree versioned files** under `services/library/defaults_pack/`, hash-pinned like the tax packs; every row carries a source, a year and an `illustrative` flag (true where the figure is not cited from a source — the seed tariffs, the synthetic profiles; false for the DEA / PyPSA-Eur catalogue rows). This amends IC spec decision 20 for this pack only (owner decision 1 of the coordinating plan). |
| D3 | Pack tariffs reach a project **copied inline** into the commercial config, stamped with the pack id, version and hash (`Tariff.pack_hash`); not resolved by a Library ref. |
| D4 | The two synthetic load profiles ship **as pack files**. |
| D5 | **(b)** The commercial root (PoC link, export link, timezone) is set through an **inline "Site connection" form** in the Investment tab where the dead-end error was, and a chat tool. |
| D6 | **(d)** `FinanceInputs` gains **`currency_year` and `price_basis`** (nominal / real), echoed in the report and the xlsx; a real basis with escalation is flagged. |
| D7 | **Storage LCOS** (coordinating decision 6): the charged energy is priced at **what the site actually paid for it** in the dispatch. |
| D8 | **S0b after S0 (PR #78) merges**: the finance adapter reads investment parts through `asset_schema.access.upfront_parts`, and the COD defaults from `build_year`. |
| D9 | **Replacement capex** gains an opt-in `replacement_rule="part_lifetimes"` (each part replaced at the end of its lifetime at its current upfront cost), built with S0b; the fixed (year, asset, amount) entries keep working. |
| D10 | **Terminal value** gains the method `remaining_life_annuity` (GS rule C2), computed by the engine, after S0b. |
| D11 | The finance adapter **skips meter links with no typed cost** (`meter_link_not_investment:<name>`). |
| D12 | `commercial_cost_terms` gains a **per-tariff-item block**. |
| D13 | `binding.bind_commercial_on_context(ctx, …)` becomes public and part of the facade. |

## 3. The engine facade, frozen (coordinating plan §6)

These are kept stable for U2. A change to any of them is recorded here first, and a test pins every
signature (`tests/test_engine_facade_frozen.py`): an accidental change fails loudly.

- **Commercial:** `commercial.billing.bill_site`, `commercial.billing.rate_meter`,
  `commercial.tariff_engine.rate` and its `RatingResult` (with `per_item` keyed by the tariff's item ids;
  `tax_levy` and `certificate` items stay distinguishable — coordinating decision 7),
  `commercial.lp_bindings.materialise_poc_prices`, `commercial.value_flow_templates.build`,
  `commercial.cost_rows.commercial_cost_terms`, `commercial.binding.bind_commercial_on_context` (D13).
- **Results:** `results.billing.compute_billing`, `results.billing.compute_billing_preview`,
  `results.finance_case.build_finance_case`, `results.value_flows.export_revenue` (a public wrapper of the
  export line).
- **Finance:** `finance.case.FinanceRefused` (its real home), `finance.engine.run_case`,
  `finance.engine.solve_ppa`, `finance.engine.payback`, `finance.metrics.irr` (returns `(value, flags)`,
  not a bare number) and `finance.metrics.npv`, `finance.report.assemble_finance_sections`,
  `finance.export_xlsx.build_workbook`, `finance.packs.base.load_pack`.
- **Library:** `library.items.resolve` / `put_item`, `library.series_store.put_series`, the defaults-pack
  loader and the flat export series helper (new, items a and e).
- **Field names** of `CommercialConfig` and `FinanceInputs` that the guided ledger compiles to.

## 4. Answers to the guided study's engine questions (U2 sub-plan §9)

Evidence (file:line on the U1 branch) is in the research note behind this section; the answers:

| Q | Answer | Code in the follow-up |
|---|---|---|
| Q1 | S0 accessors exist only on PR #78's branch; `finance_case._assets` reads only the typed `overnight_cost` today. S0b switches it to `upfront_parts` (D8). | yes, after #78 |
| Q2 | `replacement_capex` is an absolute amount at a calendar year, so it goes stale after a re-solve. Answer: D9 (`replacement_rule="part_lifetimes"`), nothing to persist. | yes, with S0b |
| Q3 | No such method today; GS would pass a `fixed` number. Answer: D10 (`remaining_life_annuity`), so the tornado's CAPEX / rate bounds need no hand recomputation. | yes, after S0b |
| Q4 | `assemble_finance_sections`, `engine.payback`, `metrics.irr` / `npv` and `RatingResult` exist and are public; they join the frozen list. New: `value_flows.export_revenue(...)` (public wrapper) and the signature-pin test. Note `irr` returns `(value, flags)`. | yes, small |
| Q5 | `single_owner` makes the site party own the meter links `grid_import` / `grid_export`; with no typed cost the case reads `overnight_cost_missing` and needs their COD. Answer: D11 — uncosted meter links are not investments (flagged). The template is unchanged. | yes, small |
| Q6 | No currency year today; FinanceInputs is nominal. Answer: D6 (`currency_year`, `price_basis`). The IC `Tariff` has no currency field: the pack's tariff metadata carries it. | yes, small |
| Q7 | The defaults pack (D2–D4): each tariff with a metadata model (currency, year, source, illustrative, billing period, export price / series / cap, honesty notes, default); value rows with range and source; `derived` values from a closed formula registry the pack exports (`derive(formula_id, values)`), e.g. round-trip = inverter efficiency²; load profiles as pack files. GS leaves `import_tariff_ref = None` and writes the tariff inline (a ref would fail `library_ref_stale`). | yes (item a) |
| Q9 | `jurisdiction` and `valid_from` are required fields of the `Tariff` itself; the pack supplies them (e.g. DE, 2020-01-01, open-ended). | no (pack data) |
| Q12 | (a) `commercial_cost_terms` has one `demand_charge` total and no item ids: D12 adds the per-item block. (b) Master's `_bridge` closes with the "Commercial" component for GS's terms (residual ≈ −3e-7 on a demand case); capex-like commercial terms (a connection fee, a contracted capacity on an extendable connection) land in the residual, which GS does not use. A residual test is added. | (a) yes, (b) test only |
| Q14 | Yes: export revenue is `energy_export` in the export escalation class on the actual side; the counterfactual has no export line (its export is zero by construction); tariff export items are `energy_export` on both sides. A pinning test is added. | test only |
| Q15 | `binding.bind_commercial` is pure and works on any network; the router's `_bind_commercial` only binds the active context. D13 moves it into `bind_commercial_on_context(ctx, …)`. Org: projects in local mode belong to the fixed `LOCAL_ORG_ID`, forks inherit it, the loaded context records it; `library_org_unknown` only fires for an unsaved network. | yes |
| Q8 | No LCOS today (`lcoe_*` counts generator energy). D7: `lcos_*_per_mwh` from storage capex, O&M and charging cost over discharged energy; the adapter carries discharge MWh and charging cost per storage asset. | yes |
| Q16 | `preflight.commercial_findings` returns nothing without a commercial config and ignores link efficiencies. Item (f): the ported checks, including a `marginal_cost` path for networks without a commercial setup (coordinating decision 10). | yes (item f) |

Q10, Q11 and Q13 are GS-side only: the engine already exposes `RatingResult.per_item` (by item id, with
the item's kind) and `FinanceResult.cash`.

## 5. Weighted weeks and monthly-billed items (item g)

Probed with real solves (a 45 MW evening-peak load, 40 MW PV, a 10 MW / 4 h battery; energy 0.06 / 0.18
€/kWh, demand 9 €/kW-month, fixed 150 €/month), each against a full hourly year:

| Template | Annual check | Demand charge in the case | vs the full year |
|---|---|---|---|
| One January week weighted 8760/168 (Σw = 8760) | passes (factor 1, no flag) | **one month** (319,500 €/yr), booked as annual, **no flag** | site bill −9.8 %; demand savings −92 % |
| The same week straddling two months | passes | two months | still 10/12 short |
| One week unweighted (Σw = 168), `annualise=True` | `template_annualised:52.14` | × 12 (`template_annualised_monthly:demand:12`) = 3,834,000 | demand +0.4 %; fixed −1.9 … +2.6 % (to +8.6 % for a February week) |
| 12 weeks, one per month, each weighted month-hours / 168 | passes | 12 months, no flags | every bill line within 0.3 % |

Facts: `RatingResult.annual` is not NaN for the weighted week (an item's annual sum is over its own
months); `per_item["demand"]`, `total` and `total_supported` are None and `compute_billing` shows the total
not established, but the value-flow ledger reads `per_item_sampled` (the one-month figure) and its
`demand_months_not_established` flag is not blocking — so the finance case carried one month as a year.
`annualise` scaled only when the factor ≠ 1, so it could not help a weighted week. The LP has the same
imbalance (a year of energy against one month of demand in the objective).

**Rule (owner, 2026-10-05):**
- **Engine (this follow-up):** when Σw represents a year but fewer than 12 billing months are present,
  monthly-billed items are **not established** (`monthly_item_months_missing:<item>:<months>`, both sides),
  unless `annualise=True`, which scales them by 12 / months (flagged). Items restricted to some months stay
  None.
- **LP: unchanged** (changing the objective weighting alters the solve recipe and its hashes); the case is
  avoided by the template rule below.
- **Templates (U4, the data-centre question):** a tariff with a monthly-billed item needs the full year or 12
  weeks (one per month, weighted month-hours / 168, storage weighted at the 1-hour step — weighting the
  storage state of charge by 52.14 wrecks the battery's dispatch); or one unweighted week with `annualise`
  (disclosed: the week's peak stands for every month's). A single week weighted to a year with fewer than 12
  months reads not established.

## 6. Status

| Item | State |
|---|---|
| U1 PR | master merged (two additive conflicts), Q2 / Q7 added; verification running |
| (a) defaults pack, (e) export helper | in progress |
| (b) site connection, (f) preflight port, D13 | in progress |
| (d), LCOS, D11, D12, Q12b / Q14 tests, Q4 facade test | in progress |
| (c) this facade section | done |
| (g) | probed; rule in §5; the engine change with B |
| S0b, D9, D10 | waiting for PR #78 |
