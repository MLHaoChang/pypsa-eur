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
| D14 | **LCOS charging from on-site PV surplus** is priced at the **export revenue forgone** (the committed export price net of export tariff items), **floored at 0** (a site curtails rather than export at a loss); grid charging at the committed import price. Disclosed in the LCOS basis. |

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

## 6. Review record

**Part A — defaults pack (a) and flat export series (e).**
- **Round 1 (c1d405a): PASS WITH CONDITIONS.** Transcription, tariffs, hand bills, hash pin and copy
  independence verified. B1 the export helper refused a multi-period axis (repeated weather year); B2
  `load_profile_series(annual_mwh=…)` ignored the step and the weightings (a 15-min index gave 250 MWh, 12
  weighted weeks 4,345 MWh); B3 the battery energy part's FOM was a bare None (C12); B4 the pack hash
  depended on the pydantic models' defaults. All fixed (b285bc6), plus pristine `pack_tariff` copies,
  `tariff_is_unchanged()`, the manifest in `check_bundle`'s ROOTED and a docstring-only package `__init__`
  (the IC tripwire).
- **Round 2 (b285bc6): one new finding.** B5 per-period scaling: on a multi-period axis one year's energy
  was spread over all periods. Fixed (f9f0f1d): each period carries `annual_mwh`; a stamp of another pack
  version reads customised; a non-fixed index freq raises clearly.
- **Round 3 (f9f0f1d): PASS.** Taken after it: a plain index that steps back is refused unless it repeats a
  year exactly (out-of-order weeks doubled the energy).

**Part B — finance additions ((d), D6, D7, D11, D12, D14, (g)).**
- **Round 1 (a5c1e09): PASS WITH CONDITIONS.** B1 a negative midday export price made PV-surplus charging
  income (LCOS 66.75 instead of 97.91 €/MWh); decided D14, the forgone export revenue floored at 0 per
  interval (`lcos_surplus_price_floored`). B2 an extendable, costed meter Link the LP sized was skipped as a
  meter link; it is now an asset (D11 skips only uncosted ones). C1 the facade test did not pin the pack,
  the export helper, `bind_commercial_on_context` or the `FinanceResult` fields. Also taken: the surplus
  flag's MWh annualised; the real-LCOS reason in `lcos.reasons`. Fixed in 6bdaefc.
- **Round 2 (6bdaefc): PASS.** 1,150 backend tests, `qa_investment_case.py` 245/245, investment vitest
  142, tsc clean.

**Part C — site connection (b), preflight port (f), D13.**
- **Round 1 (02d2ede): PASS WITH CONDITIONS.** The raw cycling check matches GS's exactly (1,500 random
  networks, every warning tuple identical; GS's cycling tests pass on the port). B1 the form's PUT skipped
  the site-connection check the chat tool runs (a reversed meter saved); B2 storage behind a converting
  Link (a heat tank, H2 with no fuel cell) counted as an arbitrage loop; B3 a grid-like bus name overruled
  an explicit `eh_role` tag; B4 the raw check made every LOPF preflight ~10× slower (per-pair table
  copies). Also taken: drop the efficiency-blind gate (an export fee with a negative import price hid a
  loop), the import efficiency at the import snapshot, the D13 signature pin, one time-zone default
  (None), the error-kind texts, clearing `export_link` / `timezone` in chat, refreshing preflight on save.
- **Round 2 (b22d22f): PASS.** B1–B4 re-probed fixed; GS parity again 0 mismatches on the 1,500 random
  networks (and 400 with NaN marginal costs); `validate_for_run` on the 40-node network 0.50 → 0.11–0.20 s.
  Taken after it: the PUT re-checks only the meter Link that changed (an edit of a pre-existing config
  whose untagged PoC runs into a grid-named bus was refused for the unchanged PoC), and the raw check
  works in chunks of pairs (one snapshots × pairs array peaked at 1.7 GB at 6,172 pairs). Known and
  documented: a Link two-way only through `links_t.p_min_pu` is not walked for returning storage.
- **U2 checklist:** once GS merges master both copies emit `tariff_export_exceeds_import`; GS removes its
  copy in U2.

## 7. Status

| Item | State |
|---|---|
| U1 PR | #81 merged to master (aae746e, 2026-10-06) |
| U1 follow-up PR | #85 open against master (retargeted after #81 merged); gate on af444ac green after df9ad88 |
| (a) defaults pack, (e) export helper | done (part A, PASS round 3) |
| (b) site connection, (f) preflight port, D13 | done (part C, PASS round 2) |
| (d), LCOS, D11, D12, Q12b / Q14 tests, Q4 facade test | done (part B, PASS round 2) |
| (c) this facade section | done |
| (g) | done with part B (rule in §5) |
| S0b, D9, D10 | waiting for PR #78 |
