# Guided investment study — U2 plan: rewire the study onto the Investment Case engine

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development or superpowers:executing-plans. Implement work package by work package, red first. The study layer keeps its questions, ledger, forks, runner, tornado, verdict and report; every bill, LP price, demand term, cash flow and KPI it shows comes from the IC engine through ONE seam (`services/study/engine_adapter.py`, fed by `services/study/compile.py`). After this plan no GS number is computed by a second engine; a figure the engine cannot give is `null` plus a flag (ADR-0001), never a GS re-implementation.

**Date:** 2026-10-05. **Status:** draft v3. v1 was written against the parent plan v1.1; v2 against **parent v1.2** (`1d6ea4a`, independent review PASS WITH CONDITIONS, §9); **v3 against parent v1.3** (`d101660`), whose corrected C1 replaces this plan's post-solve `overnight_cost` workaround with the two upfront parts read through `upfront_parts` (§10 S0), so **C1 and WP7 are blocked on S0**. It must be re-checked against the U1 PRs (facade, generic defaults pack, flat export series helper, basis note, preflight port) before WP1 starts.

**Parent plan.** `docs/superpowers/plans/2026-10-05-one-investment-engine-two-faces.md` at `d101660` on `origin/claude/determined-tesla-np09ww`: §4 (call sites, compile rules C1–C6, C1 corrected in v1.3), §5 U2, §6 (facade), §8 (owner decisions), §9 (review), §10 (one asset parameter schema, S0/S0b). Assessment behind v1.3: `docs/superpowers/assessments/2026-10-05-uniform-asset-parameterisation.md` on the same branch. This file is the GS session's progress record for **U2** and outlines **U3**; the parent plan is not edited.

**Sources read** (repository root `/home/user/pypsa-eur`):
- GS, this branch at `10f8abc` (F1 merged, incl. F1-B4 `c7d3ae0` and F1-B6 `7c54cc4`): `pypsa-gui/backend/services/study/{tariff,packs,ledger,library,proforma,proforma_xlsx,report_xlsx,findings,runner,tornado_runner,report,run_hashes}.py`, `models/study.py`, `routers/studies.py`, `study_library/`, `services/solver/objective.py::_wrap_with_demand_charge`, `services/solver_service.py` (`SolverConfig.demand_charge`, `record_solved_demand_charge` call), `services/results/objective_decomposition.py::_bridge`, `services/validation_service.py::_check_export_cycling` / `_site_storage` / `_cross_hour_cycling`, `tests/qa_decision_study.py`, the MVP-1 plan and findings note; PyPSA 1.1.2 `pypsa/costs.py::periodized_cost`.
- IC, read-only from `origin/claude/energy-tool-features-research-fdixs0` at `8470eb8`: `services/commercial/{tariff_engine,billing,binding,lp_bindings,value_flow_templates,participants,gap,cost_rows,preflight}.py`, `services/library/{items,series_store}.py`, `services/finance/{engine,metrics,case,cashflow,timeline,export_xlsx,report}.py`, `services/finance/packs/{base,eu_de}.py`, `services/results/{finance_case,billing,value_flows}.py`, `routers/simulation.py::_bind_commercial`, `models/{commercial,finance}.py`, the IC spec and P0–P4 plans.
- Master at `855bbac`: Guided mode (U3 outline) and a dry `git merge-tree HEAD origin/master` (§8).

Anything marked **(unverified)** was read in code but not exercised; WP1 turns each one into a test.

**Goal.** One tariff, one bill, one cash-flow path. After U2 the decision study's LP prices and demand charge are IC's `lp_bindings`, its bills are `tariff_engine.rate` through `billing.bill_site` / `rate_meter` (export credit from the value-flow ledger), and its cash flow and KPIs are `finance.engine.run_case` on a `FinanceCase` from `finance_case.build_finance_case`. GS's `InvestmentCase` / `CaseKpis` / `Bill` remain the guided face's **view shapes**, filled by the adapter, so findings, report, charts and frontend keep working. `qa_decision_study.py` keeps its verdict class on its fixture (`marginal`, driver `demand_charge_price`, best option `bess_1h`, 2 h and 4 h skipped by size) and records every numeric delta with its cause.

**Honest scope (U2).** No new question, no new key parameter, no frontend redesign beyond the vocabulary rename (§5.4). Basis unchanged (decision 2: pre-tax, real, excluding subsidies). GS's refusals stay refusals (annual-peak and ratchet demand bases, measured capacity charge, network-charge bases other than per MWh / period / year), except the export cap, which C3 maps to `ConnectionAgreement.export_cap_mw`. Guided-mode integration is U3.

**Dependency order.**

```
U1 PR (P3+P4) and U1 follow-up PR (a defaults pack, b commercial root, c facade, d basis note,
   e flat export series helper, f preflight port of GS checks, g weighted-week note) merged
  └─ WP0 merge master into GS (13 conflicts, §8); freeze the pre-U2 numbers
       └─ WP1 facade spike on the site pack, incl. C1 and C6 checks (tests only)
            └─ WP2 TEST PORT: the five GS engine test files re-expressed on engine_adapter (red)
                 ├─ WP3 defaults pack → ledger (engine_path on rows)
                 │    └─ WP4 compile.commercial_from_ledger (+ C3 export series, cap)
                 │         ├─ WP5 engine_adapter.bill / bill_meter (Bill mapping)
                 │         └─ WP6 LP switch (solver config carries commercial; demand on IC)
                 │              └─ WP7 compile.finance_from_ledger + engine_adapter.case (C1, C2, C4, C5)
                 │                   └─ WP8 findings, tornado, runner, routes; vocabulary rename
                 │                        └─ WP9 workbooks
                 └──────────────────────────────── WP10 DELETE (only now) + driver, packaging, findings note
```

---

## Phase QA gate + TDD protocol (mandatory, unchanged from MVP-1)

1. **Red:** failing tests that encode the package's acceptance; show the red error.
2. **Green:** the minimum code to pass; no drive-by refactors.
3. **Mutation** on the property the package exists for; only the intended tests go red. Mutations run on a copy of the tree, never the main tree.
4. **Independent assessor gate:** `GO` | `GO WITH BINDING CONDITIONS` | `NO-GO`, against acceptance and modelling honesty (one engine, null-not-zero, basis labelled, no user project mutated, no IC-owned file edited).
5. **Proceed rule** as MVP-1.

**Global constraints.**
- **File ownership (parent §5).** GS edits `services/study/*`, `routers/studies.py`, `models/study.py`, `frontend/src/pages/decision/*`, `api/decisionStudies.ts`, `utils/decisionVocabulary.ts`, its own tests and driver. The removals in `services/solver_service.py`, `services/solver/objective.py`, `models/schemas.py`, `services/results/objective_decomposition.py`, `services/validation_service.py`, `smoke/check_bundle.py`, `pypsa-gui.spec` and `tests/test_packaging_requirements.py` are edits to **shared hot files**: GS removes only its own additions, merged right before the PR. Nothing under `services/commercial/*`, `services/library/*`, `services/finance/*`, `services/results/{finance_case,billing,value_flows}.py`, `models/{commercial,finance}.py` is edited; engine changes GS would like are **asks through the owner** (§7).
- **Only the frozen facade** (parent §6) is imported from IC services, plus the model classes. Names GS needs outside it are listed in Q4 and not used until IC freezes them.
- **Nothing is deleted before its tests are ported** (parent U2, review R6): WP10 is the only deleting package and its gate checks that every ported test is green.
- No existing non-study number changes: golden tests, `test_fom_reconciliation.py`, `qa_asset_economics.py`, `qa_cost_decomp_overnight.py`, `qa_eh_reference_design.py`, `qa_investment_case.py`, `qa_value_flows.py` pass unchanged.
- No user project is mutated by a study. Compiled `commercial` / `finance` and minted export series live only in study-owned base projects and forks.
- `PYPSAGUI_DECISION_STUDIES` and the auth-mode refusal (OPEN-ITEMS 1, BC-6) unchanged.
- New honesty codes go into BOTH `report.help_for` and the frontend HELP mirror in `utils/decisionVocabulary.ts` (the S9 full suite caught a missing mirror once).
- Cite function names in commits and findings, not line numbers.

---

## 1. The ledger compile table

Each `LedgerRow` gains `engine_path: str | None` (the field it compiles to; `None` for rows that only shape the network the pack builds). `compile.py` is the only reader that turns rows into engine inputs. "KP" = one of the owner's decision-4 key parameters.

Units: GS states money per MW / MWh; IC tariff items per kW / kWh (`TariffUnit`: `per_kwh`, `per_kw_month`, `per_kw_year`, `per_month`, `per_day`); `FinanceInputs.replacement_capex` and `TerminalValueRule.value` are absolute currency. `p` = `p_nom_opt` (MW), `h` = the option's `max_hours`, `y_m` = the modelled (snapshot) year, `y0 = y_m − 1`, `H` = horizon.

### 1.1 The 20 rows GS seeds today (`library.seed_ledger`, BESS question)

| # | Ledger key (unit) | Compiles to | Unit conversion | Basis handling | KP |
|---|---|---|---|---|---|
| 1 | `battery_inverter_eur_per_kw` (EUR/kW) | (a) network `StorageUnit.capital_cost` inverter annuity term (`packs.battery_capital_cost_eur_per_mw`, unchanged — the LP's cost); (b) **C1** the battery's **power part** (inverter EUR/kW × 1000, inverter lifetime) written as an upfront part read through `upfront_parts` (§4.5; blocked on S0); (c) `FinanceInputs.replacement_capex[(y0 + k·L_inv, "battery", inverter × p)]`; (d) inverter share of the C2 terminal value | × 1000 → EUR/MW; × `p` → EUR | real, ledger `currency_year` (2020 EUR), overnight, pre-tax; `contingency_share = 0` (row 23) | **yes** |
| 2 | `battery_inverter_fom_pct_per_year` (%/year) | network `StorageUnit.fom_cost` (`packs.battery_fom_eur_per_mw`, unchanged) → P3 `fom:StorageUnit:battery` cash line → `TemplateLine(stream="fom", esc_class="opex")` | ÷ 100 × inverter × 1000 → EUR/MW/yr | `escalation["opex"] = 0` | no |
| 3 | `battery_inverter_efficiency` (per unit) | none (input of row 13, `library.DERIVED`) | — | — | no |
| 4 | `battery_inverter_lifetime_years` | `replacement_capex` years (`library.horizon_and_replacements`); terminal remaining life | whole years | — | no |
| 5 | `battery_storage_eur_per_kwh` (EUR/kWh) | (a) `StorageUnit.capital_cost` storage term (unchanged); (b) **C1** the battery's **energy part** (storage EUR/kWh × `h` × 1000, storage lifetime) through `upfront_parts` (blocked on S0) | × `h` × 1000 → EUR/MW | as row 1 | **yes** |
| 6 | `battery_storage_lifetime_years` | `FinanceInputs.analysis_years = H`; the energy part's lifetime in `upfront_parts` (C1) → the finance asset's lifetime; `SolverConfig.default_lifetime` | whole years | — | no |
| 7–9 | `pv_utility_eur_per_kw`, `_fom_pct_per_year`, `_lifetime_years` | network `Generator.overnight_cost` / `fom_cost` / `lifetime` (unchanged; PV has ONE lifetime, so its typed `overnight_cost` is already what the LP annualises and what `finance_case._assets` reads); PV share of the C2 terminal value | EUR/kW × 1000; % ÷ 100 | real; PV outlives H (40 > 25) | no (PV on/off is a KP at intake) |
| 10–12 | `pv_rooftop_*` (same three) | as 7–9, by `intake.pv.kind` | as 7–9 | as 7–9 | no |
| 13 | `battery_round_trip_efficiency` | network `efficiency_store = efficiency_dispatch = √value` (unchanged) | — | — | no |
| 14–15 | `battery_storage_degradation_{calendar,cycling}` (`not_used_in_mvp1`) | not compiled (`FinanceInputs.degradation_by_asset` covers generators only; `no_degradation` note kept) | — | — | no |
| 16 | `tariff` (descriptor) | `CommercialConfig.import_tariff` (inline IC `Tariff`, compiled from the pack tariff or the user's form and rows 17–18), `import_tariff_id`, `import_tariff_ref` (the pack tariff's `LibraryItemRef`; `None` for a user tariff) | §3 | tariff money year in `CompiledCommercial.tariff_meta`; the report states it through the U1 (d) basis note (C4) | **yes** (tariff choice) |
| 17 | `demand_charge_price` (EUR/MW/month) | `import_tariff.items[id="demand"].periods[0].rate`, `kind="demand"`, `per_kw_month`, `measured_on="import"`, `settlement="h"`; a year-billed GS tariff → `kind="capacity"`, `measured_on="peak_import"`, `per_kw_year` (§3.1) | ÷ 1000 | pre-tax | **yes** |
| 18 | `energy_price_level` (multiplier; null on one band) | every `items[id="energy"].periods[*].rate` rewritten `m + level·(p − m)`, `m` the time-weighted mean over the study snapshots (`tariff.tariff_from_ledger`'s rule, moved verbatim) | dimensionless | `network:*`, demand and export untouched | **yes** |
| 19 | `sizing_limit_connection_multiple` | network `p_nom_max` (unchanged) | — | — | no |
| 20 | `discount_rate` (real) | `SolverConfig.discount_rate`; assets' network `discount_rate`; **C4** `FinanceInputs.wacc_nominal` = this real rate; `cost_of_equity` = same (row 26); `LpBasis` via `finance_case` | — | C4: escalation 0, `inflation = None`, disclosed | **yes** |

Intake answers that are decision-4 KPs (not ledger rows): **site zone** → bus `country`; **connection MW** → `grid_import.p_nom` / `grid_export.p_nom` (the PoC size IC's capacity items rate on, `billing._poc_capacity_mw`), `p_nom_max`, and `ConnectionAgreement.import_cap_mw` (C3); **load** → `loads_t.p_set`; **tariff** → row 16; **PV on/off** → the option set.

### 1.2 Engine inputs a minimal single-owner case requires that GS has no row for

Seeded from the **generic defaults pack** (§6) with `provenance=library`, the pack rule as `source`, `status=default`, an `engine_path`. Non-numeric rules are descriptor rows (value `null`, `unavailable={"value": "rule_descriptor"}`, refused by `ledger.apply_user_row` like the `tariff` row) so the report's assumptions appendix still lists them.

| # | New key | Engine field | Value and rule | Provenance |
|---|---|---|---|---|
| 21 | `financial_close_year` | `FinanceInputs.financial_close = date(value, 1, 1)`; `capex_phasing = [1.0]` | `y_m − 1`: one construction year, so engine index 0 = GS year 0 (capex) and operating years 1…H sit at indices 1…H (`timeline.build_timeline`) | pack rule `guided.financial_close.one_year_before_model_year` |
| 22 | `cod_year` (derived) | `FinanceInputs.cod_by_asset = {a: date(y_m, 1, 1) for every owner asset}` | one COD (else `cod_missing` / `cod_mismatch`, `finance_case._cod`) | pack rule `guided.cod.model_year` |
| 23 | `contingency_share` | `FinanceInputs.contingency_share` | `0.0`; `None` makes capex `not_established` (`contingency_share_missing`) | pack finance default |
| 24 | `escalation_{tariff,export,opex,capex,fuel,ppa}`, six rows | `FinanceInputs.escalation[class]` | **C4** `0.0` each; a class with cash and no rate is `escalation_missing:<class>`; `fuel` / `ppa` seeded so an Expert edit has a row to mark (rule 5) | pack rule `guided.basis.real` |
| 25 | `inflation` (descriptor) | `FinanceInputs.inflation = None` | **C4**: `None`, not 0. The WACC gate's `inflation` leg reads `n/a` because `auto_discount_periods` is off; `lcoe_real` reads `not_established:inflation_missing` (not shown pre-tax) | pack rule `guided.basis.real` |
| 26 | `cost_of_equity_rule` (descriptor) | `FinanceInputs.cost_of_equity = discount_rate` | all-equity single owner: equity returns = project returns | pack rule `guided.finance.all_equity` |
| 27 | `analysis_years_rule` (descriptor) | `FinanceInputs.analysis_years = round(battery_storage_lifetime_years)` | MVP-1 horizon rule | pack |
| 28 | `value_flows_template` (descriptor) | `CommercialConfig.value_flows = value_flow_templates.build("single_owner", n, cfg).config` | owner = `site_party` `"site"`; built per option network after `packs.build_site_network` (owner asset list checked in WP1, Q5) | pack rule `guided.value_flows.single_owner` |
| 29 | `salvage_rule` (descriptor) | **C2** `FinanceInputs.terminal_value = TerminalValueRule(method="fixed", value=V)` | `V` = GS's annuity-PV salvage (PV array + last inverter), computed by `compile.resolve_post_solve` from rows 1, 4, 7–12, 20 and the solved sizes; disclosed `salvage_fixed_from_remaining_annuities` | pack rule `guided.salvage.annuity_pv_remaining_life` |
| 30 | `pv_degradation_pct_per_year` | `FinanceInputs.degradation_by_asset["pv"] = value / 100` (options with PV) | `0.0`; without it `cashflow.build_operating` marks the operating section `degradation_missing:pv` (unverified that the bess_pv case fails; WP1) | pack row, `note=mvp_basis_no_degradation` |
| 31 | `tax_pack` (descriptor, `off`) | `FinanceInputs.tax_pack_id = None` | pre-tax (decision 2): tax `not_established`; the compiler already accepts `eu_de` (U3 outline) | pack rule `guided.basis.pre_tax` |
| 32 | `incentives_rule` (descriptor) | `FinanceInputs.incentives = []` | excluding subsidies | pack rule `guided.basis.excl_subsidy` |
| 33 | `export_series` (descriptor, per study) | **C3** `CommercialConfig.export_price_ref` (a flat `PriceSeriesRef` minted by the U1 (e) helper over `series_store.put_series`), `export_link = "grid_export"` | the tariff's flat export price on every snapshot; `None` when the tariff has no export price (export link unpriced) | the tariff's own source; the ref's `version`/`hash` recorded in the compiled digest |
| 34 | `export_cap_mw` (only when the tariff states `cap_mw`) | **C3** `CommercialConfig.connection = ConnectionAgreement(kind="firm", import_cap_mw=connection_mw, export_cap_mw=cap_mw, available_from=date(y_m,1,1))` | lifts GS's `export_cap_unsupported` refusal; with no cap the compiler writes no `connection` (unchanged LP) | the tariff |

Fixed compile-time constants (stated in the compiled provenance): `poc_link = "grid_import"`, `export_link = "grid_export"`, `timezone = None` (GS reads snapshot timestamps as the local clock; IC's rule for `None` is the same, `lp_bindings._local_clock`), `site_party = "site"`; `FinanceInputs.currency` = the tariff's currency, `annualise = False` (8,760 h of a non-leap year pass `finance_case`'s C3 check), `debt = []`.

---

## 2. The call-site table

Callers from a grep on `10f8abc`. The adapter is the only GS module that imports IC services; `compile.py` imports IC models plus the defaults-pack loader and the export-series helper.

| # | Today (function) | Callers | Becomes (GS seam, signature) | Engine facade called |
|---|---|---|---|---|
| C1 | `packs.effective_tariff(intake, ledger, library, snapshots=None) -> Tariff` → `tariff.tariff_from_ledger(tariff, ledger, snapshots, snapshot_weightings=None)` | `packs.build_site_network`; `findings._price_bound`, `findings.load_inputs`; `runner.start_study_run`, `runner._worker`; `routers/studies.py::preview_intake`, `_create_pack_study`, `_option_case`; driver `_reconcile` | `compile.commercial_from_ledger(intake, ledger, defaults, snapshots, *, snapshot_weightings=None, export_series: PriceSeriesRef \| None) -> CompiledCommercial` (frozen: `config: CommercialConfig`, `item_component: Mapping[str, BillComponentKey]`, `tariff_meta` (currency, currency_year, source, illustrative, honesty codes/help), `notes`, `digest`). `compile.mint_export_series(study, tariff_form, snapshots) -> PriceSeriesRef \| None` calls the U1 (e) helper once per study (idempotent on content). `ledger_tariff_stale` refusal moves here | defaults-pack loader; U1 (e) flat export series helper (`series_store.put_series` underneath); `library.items.resolve` only for a pinned pack tariff |
| C2 | `tariff.write_tariff_prices(n, tariff, import_link, export_link, *, series=None)` | `packs.build_site_network`; `findings._price_bound` | **removed.** Link `marginal_cost` stays 0; IC prices transiently at solve. The export price column `links_t["ic_export_price"]` is written by binding (C6) | `lp_bindings.materialise_poc_prices` (in IC's `run_simulation`) |
| C3 | `tariff.demand_charge_config` → `SolverConfig.demand_charge` → `objective._wrap_with_demand_charge`; `run_simulation` → `record_solved_demand_charge` (F1-B4); `objective_decomposition._bridge` → `demand_charge_eur_from_network` / `solved_demand_charge_config` (F1-B4) | `packs.option_solver_config` (callers: `runner._worker`; `findings._price_bound`, `_capex_bound`, `_rate_bound`, `_centre`; `routers/studies.py::_create_pack_study`, `_option_case`; driver `_reconcile`) | `compile.solver_config(ledger, compiled, finance) -> SolverConfig` (lopf, flat, full strategy, no SCLOPF, no user code, `discount_rate`, `default_lifetime`, `commercial=compiled.config.model_dump(mode="json")`, `finance=finance.model_dump(mode="json")`). `runner._read_option`'s `demand_charge_eur` becomes `engine_adapter.demand_charge_eur(n, compiled)`: the demand items' amount from `commercial_cost_terms` (what the solve COMMITTED, recorded in `ic_demand_peaks` — the property F1-B4 added, now IC's). See §5.2 for the full removal | `lp_bindings._wrap_with_commercial_bindings` → `add_demand_terms`; `cost_rows.commercial_cost_terms` |
| C4 | `BillCalculator().bill(import_mw, export_mw, tariff, snapshot_weightings, *, fidelity=None) -> Bill` | `runner._read_option`; `findings._bill_of` (from `_price_bound`, option and baseline-at-perturbed-tariff); `proforma._bill_of`; `routers/studies.py::preview_intake` | `engine_adapter.bill(n, compiled, *, fidelity) -> Bill` (solved PoC meter, `bill_site`, plus the export line, §3.2) and `engine_adapter.bill_meter(n, compiled, import_mw, export_mw, *, fidelity) -> Bill` (`rate_meter`: the baseline at a perturbed tariff without a solve — BC-7 — and the intake preview on the unsolved baseline pack with `import = load`, `export = 0`, so `export_credit = 0.0`). **Parent §4 says "preview over `compute_billing_preview`"**; that needs a solved network already carrying a commercial config, which the preview does not have — WP1 tests `rate_meter` on the unsolved pack and the deviation is recorded | `billing.bill_site`, `billing.rate_meter`; export line: `results/value_flows.py::_export_revenue` (Q4) |
| C5 | `Bill.by_component` six keys; `proforma.BILL_COMPONENTS`; `findings.STREAMS` assertion; `findings.value_streams` | `proforma.build_investment_case`, `findings.value_streams`, `report.py`, frontend | `engine_adapter.BILL_COMPONENTS` (moved) and `engine_adapter._components(...)` (§3.2) own the mapping and the assertion; `findings` imports from the adapter; `STREAMS` unchanged | `tariff_engine.RatingResult` |
| C6 | `proforma.build_investment_case(n_option, cfg_option, n_baseline, ledger, bills, option_id, *, study_id, tariff, fidelity, asset_economics, study_currency_year, question, project_ref, model_hash) -> InvestmentCase` | `routers/studies.py::_option_case` (case and case.xlsx routes); `findings._case` (from `_centre`, `_price_bound`, `_capex_bound`, `_rate_bound`) | `engine_adapter.option_case(n_option, cfg_option, ledger, *, compiled, option_id, study_id, fidelity, asset_economics, question, project_ref=None, model_hash=None) -> CaseBundle` (`view: InvestmentCase`, `case: FinanceCase`, `result: FinanceResult`) — **C5: one `FinanceCase` per option**: (C1: the finance engine reads the battery's two upfront parts through `upfront_parts`, §4.5, blocked on S0/S0b) → `build_finance_case(n, cfg, fin, result_df=_live_result_df)` → `run_case(case, pack=None)` → `_view`. Bounds: `engine_adapter.bound_case(bundle, ledger_variant, *, kind) -> CaseBundle` derives CAPEX and RATE bounds with `dataclasses.replace` on `case.assets` / `case.inputs` (and the battery `fom` template line, which follows the inverter cost) — no 8,760-h re-read; PRICE bounds re-dispatch and call `option_case` on the variant network. `n_baseline` is dropped: the engine counterfactual (`rate_meter` on the served load, export 0) **is** the grid-only baseline bill at the case's tariff. `ProformaError` codes kept where meaningful; `FinanceRefused(code)` → `engine_refused:<code>` | `results.finance_case.build_finance_case`, `finance.case.FinanceRefused`, `finance.engine.run_case`, `value_flow_templates.build` |
| C7 | `proforma.irr`, `npv`, `payback`, `is_zero_size`, `EPSILON_MW` | `findings.attribute` (`proforma.payback` on option − reference net flows), `findings.is_zero_size` / `EPSILON_MW` | `engine_adapter.payback`, `engine_adapter.irr` (re-exports), `EPSILON_MW` / `is_zero_size` moved to the adapter | `finance.engine.payback`, `finance.metrics.irr` / `npv` (Q4) |
| C8 | `proforma_xlsx.write_proforma_xlsx(case, ledger)`; `report_xlsx.write_report_xlsx(...)` (uses `proforma_xlsx` helpers) | `routers/studies.py::get_option_case_xlsx`, `get_report_xlsx` | `engine_adapter.case_workbook(bundle, ledger) -> bytes`: `build_workbook(assemble_finance_sections(result, case, ...))`, then GS sheets appended (§WP9); `report_xlsx` builds on it per option | `finance.export_xlsx.build_workbook`; `finance.report.assemble_finance_sections` (Q4) |
| C9 | `Engine` literal `bill_calculator` / `cash_flow_expander`; `Bill.engine`; `CaseSources.bill_refs = ["bill_calculator:none", "bill_calculator:<option>"]`; `CaseKpis.lcos`, `salvage_eur`; `proforma.ENGINES`; `report._RUN_ENGINES` and `engines` list | `models/study.py`, `services/study/{proforma,report,findings}.py`, `api/decisionStudies.ts`, `utils/decisionVocabulary.ts`, `pages/decision/Verdict.tsx`, `pages/decision/__fixtures__/*.json`, `vocabularySource.test.ts` | §5.4 (vocabulary rename) | — |
| C10 | `run_hashes.ledger_matches` / `intake_matches` | `_option_case`, `findings.load_inputs`, `report.stale_reasons` | unchanged + `FindingsHashes.compiled_hash` (compiled commercial + resolved finance + export ref) and `run_hashes.compiled_matches`: an Expert edit of a fork's `solver_config.json` → case 409 `engine_inputs_changed_since_run`, report `stale` (prepares rule 5) | — |
| C11 | `library.load_library()`, `library.seed_ledger`, `library.horizon_and_replacements`, `ledger.reseed_ledger` / `reset_rows` | `routers/studies.py` (create, patch, reseed, reset, preview, `_library_or_500`), `runner._worker`, `findings.load_inputs`, `packs.*` | `library.load_defaults() -> GuidedDefaults` (GS view over the U1 loader); `seed_ledger(question, intake, defaults)` (§6.2) | defaults-pack loader |
| C12 | fork binding: forks get their network with prices written into it | `runner._worker` → `forks.create_option_fork`; `tornado_runner` variant forks | **C6**: `compile.bind_on_fork(fork_ctx_or_network, compiled, *, project_dir, org)` applies the commercial block to a **study-owned fork** the way `PUT /solver_config` does (`routers/simulation.py::_bind_commercial` → `services/commercial/binding.bind_commercial`: series resolution, `ic_export_price` column, FCA registry). `_bind_commercial` binds the **ACTIVE** context (`PyPSAService.get_active_context()`, `get_network()`), which a runner fork off the foreground is not; WP1 tests whether the route can be pointed at the fork, else Q15 | `binding.bind_commercial` (not in the §6 list: Q15) |

`packs.battery_capital_cost_eur_per_mw`, `battery_fom_eur_per_mw`, `battery_upfront_eur_per_mw` stay (network data for the LP; `battery_upfront_eur_per_mw` becomes the cross-check of the sum of C1's two parts). `packs.build_site_network` loses only its `write_tariff_prices` call; `packs.option_solver_config` becomes a call of `compile.solver_config`.

---

## 3. The bill mapping

### 3.1 GS tariff → IC items (compiler)

Fixed item ids, so the adapter maps by id first and by kind second (`item_component`).

| GS tariff part | IC representation | Rate conversion | Component |
|---|---|---|---|
| `energy_bands` (first match) | item `energy`: `kind="energy"`, `per_kwh`, `import`, `cost`; one `TariffPeriod` per band and contiguous hour run (`hours=[0..7, 20..23]` → two periods, same name and rate), band order kept, so IC's first-match (`tariff_engine._rates`) equals GS's | €/MWh ÷ 1000 after the level rescale | `energy` |
| `network_charges`, basis `per_mwh` | item `network:energy:<i>`: `kind="energy"`, `per_kwh`, `import`, `cost`, catch-all | ÷ 1000 | `network` (id prefix `network:`) |
| `network_charges`, basis `per_period` | item `network:fixed:<i>`: `kind="fixed"`, `per_month` | as is (÷ 12 on a year-billed tariff) | `network` |
| `network_charges`, basis `per_year` | item `network:fixed:<i>`: `kind="fixed"`, `per_month` | ÷ 12 (IC `fixed` takes `per_month` / `per_day` only; note `network_per_year_billed_monthly`) | `network` |
| `demand_charge`, month, `billing_period_peak` | item `demand`: `kind="demand"`, `per_kw_month`, `import`, `cost`, `settlement="h"` | ÷ 1000 | `demand` |
| `demand_charge`, year | item `demand`: `kind="capacity"`, `per_kw_year`, `peak_import`, `settlement="h"` (IC's annual measured peak, `lp_bindings._capacity_spec`) | ÷ 1000 | `demand` (by id) |
| `capacity_charge`, `contracted` | item `capacity`: `kind="capacity"`, `per_kw_year`, `import` (rated on the PoC `p_nom` = connection MW) | ÷ 1000 | `capacity` |
| `fixed_charge_per_period` | item `fixed`: `kind="fixed"`, `per_month` | as is (÷ 12 year-billed) | `fixed` |
| `export.price_per_mwh` | **C3**: not an item. A flat Library series minted per study (U1 e), `export_price_ref` + `export_link`; bound on the fork (C6) into `links_t["ic_export_price"]` | the price as is, EUR/MWh on every snapshot | `export_credit` from the value-flow export line (§3.2) |
| `export.series_ref` | the referenced series through the same path (resolved by GS's existing `series` mapping, then minted) | — | `export_credit` |
| `export.cap_mw` | **C3** `ConnectionAgreement.export_cap_mw` (row 34) | MW | — |
| `connection_limit_mw` (tariff field) | not an item: IC rates capacity on the PoC size. A tariff whose `connection_limit_mw` differs from the intake's connection is refused (`capacity_basis_mismatch`) rather than billed on another MW | — | — |
| refused today (`annual_peak`, `ratchet`, `measured` capacity, other network bases) | still refused, typed, same codes, raised by `compile` | — | — |

`settlement="h"` on demand items: GS bills hourly peaks; IC's default `15min` makes preflight warn `commercial.demand_resolution` on an hourly axis. The case keeps the note `demand_peak_hourly_resolution`, beside the seed tariff's own `tariff_demand_charge_monthly_peak_not_annual`.

IC's mandatory `Tariff.jurisdiction` / `valid_from` for illustrative seeds come from the pack (`DE` / `generic`, `valid_from = date(currency_year, 1, 1)`, `valid_to = None`, so `commercial.tariff_out_of_validity` does not fire) (Q9).

### 3.2 `RatingResult` + export line → `Bill` (adapter)

`bill_site` returns `SiteBill(per_period={None: RatingResult | None}, flags, provenance)` on a flat network. `engine_adapter._bill_from_rating` builds GS's `Bill`:

| `Bill` field | Source |
|---|---|
| `energy`, `demand`, `capacity`, `fixed` | Σ `RatingResult.per_item[i]` over item ids mapped to the key. IC `amount` is + cost / − revenue; an item with `per_item = None` makes its component `None` (`item_not_rated:<id>`); a component with no item is `0.0` (GS: no demand charge → `demand = 0.0`) |
| `network` | Σ `per_item[i]` for every id starting `network:` (kind `energy`, `fixed`, or an Expert-added `tax_levy` named `network:*`), summed by the adapter |
| `export_credit` | **not from `bill_site`** (export is not a tariff item): `−` the value-flow ledger's export revenue for the period, `results/value_flows.py::_export_revenue` = Σ w · `p0[export_link]` · `ic_export_price` (Q4: reached through a facade name). `None` when the export column is not established; `0.0` when nothing is exported or the tariff has no export price; `bill_meter` (preview, counterfactual) has export 0 → `0.0`. Cross-checked against the export row of `commercial_cost_terms` to 1e-9 (unverified that the row exists separately; WP1) |
| `total` | `RatingResult.total + export_credit`; assert `|total − Σ six components| ≤ 1e-9·max(1, |total|)`, else `None` + `bill_components_do_not_sum` |
| `annual_bill` | `total` when Σ weights is 8,760 / 8,784 h, else `None` + `horizon_not_one_year` (kept) |
| `peak_mw_by_billing_period` | `demand_lines` grouped by `month`: `max(peak_kw) / 1000` |
| `billing_periods` / `partial_billing_periods` | `monthly.index` / months with `fixed_lines.hours_covered < hours_in_month` |
| `horizon_hours`, `currency`, `currency_year` | Σ objective weightings; `compiled.tariff_meta` |
| `honesty_notes` | digit-free codes only (S7 prose guard): `capacity_prorated_by_represented_hours` → `capacity_charge_prorated_by_hours`; `resolution:dispatch_<x>h_settlement_<y>h` → `bill_resolution_differs_from_settlement`; `peak_from_partial_year`, `fixed_prorated_on_partial_coverage` kept; `SiteBill.flags` (`config_changed_since_solve`, `solve_provenance_unknown`, `negative_flow:*`, `period_not_billed:*`) → `unavailable["total"]` codes |
| `engine` | `"tariff_engine"` |

**No clean mapping.**
1. `certificate` / `tax_levy` items not named `network:*` (German per-kWh levies are `tax_levy`). The compiler never emits them; an Expert can add one to a fork (U3). Until the owner decides (Q10) the bill is `not_established` with `bill_item_kind_unmapped:<id>` — never silently into `energy`.
2. An Expert-added item with an unknown id: by kind and side — import cost energy → `energy`; demand (ratchet or tiers included) → `demand`; capacity → `capacity`; fixed → `fixed`; an export revenue *tariff item* (allowed by IC) → `export_credit`, summed with the value-flow export line.
3. `RatingResult.unsupported_items` → component `None`, `item_unsupported:<id>`.
4. A year-billed GS demand charge is an IC `capacity` item but a GS `demand` component: mapped by id, and `STREAMS["demand_charge_reduction"] = (demand, capacity)` is unaffected either way.

**Why `findings.STREAMS` and `value_streams` stay correct.** The six keys and their order are unchanged (`engine_adapter.BILL_COMPONENTS` is the moved tuple); `findings` keeps its import-time assertion; the adapter adds two: every compiled id is in `item_component` (or matches `network:`), and `item_component` values ⊆ the six keys. `value_streams` reads `by_component` as today, so streams sum to `bill_baseline − bill_option`. New guard in `option_case`: the engine's incremental operating cash in year 1 equals the view's `savings − fom − vom` to 1e-6 relative (`case_streams_reconcile_with_engine`), so waterfall and cash flow cannot drift.

---

## 4. Semantic differences (settled in tests, not papered over)

**Judging rule for `qa_decision_study.py`** (WP10): the verdict class, its driver list and the option it names must equal the WP0 record (`marginal`, `["demand_charge_price"]`, `bess_1h`; `bess_2h`, `bess_4h` `skipped`). Every printed figure is compared with WP0 and written to `EVIDENCE["u2_deltas"]` as `{figure, pre, post, abs, rel, cause}`. Inside tolerance passes silently; outside passes only with a cause code below, and the assessor gates it. The driver had 57 checks at `b02b117` and 60 after the S9 gate fixes (findings note); the parent's "57" refers to the former.

### 4.1 IRR — bisection vs `metrics.irr`
- GS `proforma.irr`: bisection of NPV on [−0.99, 10]; `None` when the ends share a sign. IC `metrics.irr`: grid scan on [−0.99, 10], each sign change refined by `brentq`, the root closest to 0, flags `irr_multiple_sign_changes` / `irr_not_established:*`.
- **Expected delta:** none in value with one root in range. On the S5 golden numbers `bess_2h` cash changes sign **5 times** (inverter replacements of 165,143 EUR in years 10 and 20 exceed the year's net 80,560 EUR) with one root (15.56 %); `bess_pv_2h` once. IC returns the same IRR and adds the flag. Tolerance |ΔIRR| ≤ 1e-9; cause if exceeded `irr_root_choice`.
- The case gains `irr_cash_changes_sign_more_than_once` (digit-free, HELP mirrored).

### 4.2 Salvage — C2
- GS: PV at H of the annuities the LP charged for the remaining life; IC: `TerminalValueRule` `none | book_value | multiple_of_ebitda | fixed`, booked in EBITDA at the last index.
- **C2:** `fixed`, value computed exactly as GS (`compile.resolve_post_solve`). **Expected delta 0.** With `none` the NPV would fall by `V / 1.07^25` (≈ −17,800 EUR on golden `bess_2h`, −4.8 %; ≈ −271,700 EUR on `bess_pv_2h`, −17 %); `book_value` is `not_established` pre-tax. Disclosed `salvage_fixed_from_remaining_annuities`; `salvage_eur` reads the terminal value (§5.4).
- **`BY_CONSTRUCTION` codes — re-derived, not dropped.** `findings.BY_CONSTRUCTION` (`npv_nonnegative_at_optimum_by_construction`, `irr_and_discounted_payback_bounded_at_optimum_by_construction`) stay only because §4.6 is proved on a fixture by a test (WP7) and checked in the driver (WP10). If either test fails, the codes are dropped from the case, from `findings.BY_CONSTRUCTION`, from the report and the HELP mirror in the same PR (never kept on a false premise). They hold at the centre only; a rate bound's case (C5) carries `wacc_gate_differs_on_rate_bound` instead.

### 4.3 LCOS vs IC's LCOE
- GS `lcos = NPV(battery capex + replacements + FOM + VOM − inverter salvage) / (discharge MWh × AF(r, H))`, `lcos_excludes_charging_energy_cost`; `lcoe = None`.
- IC `lcoe_{nominal,real}_per_mwh`: `(PV value − PV post-tax equity cash) / PV generator energy` at `cost_of_equity`; storage has no `energy_mwh` ("never storage"); post-tax equity cash is `None` pre-tax. So for every guided case **IC's LCOE is `None`** (battery-only: no generation; any case: pre-tax).
- **Parent §4 says "LCOS shown from IC's LCOE fields with a label".** Following it, U2 shows `levelised_cost = None` with the engine's reason (`lcoe_not_established:pre_tax_no_equity_cash`, plus `storage_has_no_generation` on battery-only options) and withdraws the report's LCOS paragraphs (`lcos_excludes_charging_energy_cost`, `lcos_two_definitions`, `_lcos_including_charging`). **Delta:** `lcos` from a number to `null`, cause `lcos_not_in_engine`. The S7 "two LCOS figures" disclosure goes with it; asset economics' own LCOS (charging included) is untouched and still shown where it was. Q8 asks the owner whether to accept losing the guided LCOS or have IC add a storage LCOS metric.

### 4.4 Real vs nominal — C4
- **C4:** escalation 0 for all six classes, `inflation = None`, `wacc_nominal` = the real rate; **expected delta 0** (every factor `(1+0)^k = 1`). Gate: `discount_rate` leg `ok`, `asset_rates` `ok`, `inflation` `n/a` (`auto_discount_periods` off) → consistent.
- **Disclosure:** IC's fields say "nominal" and its money year is the template's (`y_m`) while values are 2020 EUR. The report states "real basis, 2020 EUR" through the U1 (d) basis note; the GS view keeps `basis.terms = real`, `currency_year` from the ledger, and adds `wacc_field_holds_the_real_rate`. Mutation (WP7): `escalation_tariff = 0.02` → NPV rises, the real-basis test goes red.

### 4.5 Battery CAPEX — C1, two lifetimes, `battery_upfront_eur_per_mw`
- GS: StorageUnit `capital_cost` = sum of two annuities (inverter 10 y + storage 25 y); `overnight_cost` unset; pro forma books `battery_upfront_eur_per_mw(ledger, h) × p` in year 0 and the inverter at 10 and 20.
- IC: one `AssetFinance(overnight_cost, lifetime_years)` per owner asset from the **typed** `overnight_cost` × capacity (`finance_case._assets`, never back-calculated); replacements via `FinanceInputs.replacement_capex`; `asset_lifetime_short` without one. **No inverter/storage split inside an asset**: the split exists only as one asset (lifetime 25) plus the replacement lines.
- **C1 (parent v1.3, corrected).** Compile writes the battery's **two upfront parts**: power (inverter EUR/kW × 1000 per MW, inverter lifetime) and energy (storage EUR/kWh × `h` × 1000 per MW, storage lifetime). It leaves PyPSA's `overnight_cost` **empty** on the StorageUnit, so the LP keeps today's two-annuity `capital_cost` exactly; the finance engine reads the parts through the one accessor `upfront_parts` (parent §10 S0 schema core, S0b finance read). The inverter part's replacements at years 10 and 20 follow from its lifetime inside the horizon. **Expected delta 0** on CAPEX (the driver's figure) and replacements, and 0 on the LP.
- **Why not a blended `overnight_cost`.** In PyPSA 1.1.2 `pypsa/costs.py::periodized_cost` annualises `overnight_cost` whenever it is not NaN and then ignores `capital_cost`; one value over one lifetime under-costs the battery (−12.7 % on the library's numbers per the parent's assessment; −17 % to −26 % at the quotes this plan's v2 checked). v2's workaround (write it post-solve on an in-memory copy) is **withdrawn**: it left the saved fork without the parts, so IC's own route disagreed with the guided case.
- **Blocked on S0.** Until the schema core lands (its own owner-merged PR, before U2's), WP7's capex path cannot be built. Tests (WP7, once unblocked): `upfront_parts` on the study fork returns exactly the two ledger parts; the solved objective and `p_nom_opt` are byte-identical with and without the parts written; the sum of parts equals `packs.battery_upfront_eur_per_mw`. **Mutation:** setting `overnight_cost` on the StorageUnit → the LP-identity test goes red.
- **U3 consequence:** "Open in Expert" reads the same parts through the same accessor, so the expert case and the guided case agree on the saved fork (v2's Q1 is answered by S0).

### 4.6 The identity NPV = LP objective saving × AF(r, H)
Rests on: (1) capex + replacements − salvage discount to the LP's annuities — holds iff C1 (ledger upfront, replacements at 10 and 20, LP untouched) and C2 (`fixed` annuity-PV salvage); (2) bill savings = the LP's grid-cost saving — IC prices the LP from the same compiled tariff (`materialise_poc_prices` energy and network items, export through `ic_export_price`, `add_demand_terms`); fixed items and a fixed-PoC capacity charge are not LP terms but identical in both bills; asserted with `gap.billing_vs_lp_gap` `unattributed = 0` for `energy` and `demand`; (3) FOM identical in LP and cash (network `fom_cost`; P3 `fom` cash line).
**It survives**, reading the LP objective as `n.objective` (which includes IC's transient commercial terms). Test (WP7): on the site golden fixture `|NPV − (obj_none − obj_option) × AF(0.07, 25)| ≤ 1e-6·|NPV|` for `bess_2h` and `bess_pv_2h`; mutation `salvage_rule → none` red. Driver (WP10): the same check on `bess_1h`.

### 4.7 The demand charge — same LP formulation?
- GS `_wrap_with_demand_charge`: `peak_import[p]` per `YYYY-MM`; `Σ_{import links} Link-p[l,t] − peak_import[p(t)] ≤ 0` ∀t; `objective += Σ_p price × peak_import[p]`, unweighted; refused under myopic / rolling, SCLOPF, multi-period, non-flat snapshots.
- IC `_demand_spec` + `add_demand_terms`: one key per (item, window, period, local month); `ic_peak_import[key] ≥` import (interval mean over `settlement` groups; one snapshot per interval at hourly settlement on an hourly axis); `ic_billed_demand ≥ ic_peak_import` (and ratchet terms); `objective += Σ w_obj · rate × 1000 · ic_billed_demand` (`w_obj = 1` flat); zero-rate months skipped; windowed strategies refused.
- **For GS's case** (one catch-all window, no ratchet or tiers, hourly, `settlement="h"`, flat, `timezone=None`): billed = peak at the optimum, so feasible sets and objective coincide — **the same formulation**. Expected Δobjective 0 to solver tolerance; dispatch may differ on degenerate ties. Test (WP6): a 3-month toy network solved with the GS wrapper (WP0 record) and with IC: objectives ≤ 1e-7 relative, equal monthly peaks. A year-billed GS demand charge maps to IC's annual `peak_import` capacity term (`_capacity_spec`), tested separately on a toy year (unverified equality). IC's wrapper position in `run_simulation` relative to the objective scale is covered by the objective test (unverified by reading).

### 4.8 Payback and the baseline bill
- IC reports payback on post-tax equity cash only (`None` pre-tax). The adapter computes `payback_simple` = `engine.payback(cash["project_pre_tax"])` and `payback_discounted` on its discounted series. Expected delta 0 (GS computed paybacks only with capex > 0, where IC's `0.0`-for-never-negative rule cannot apply).
- GS billed the solved `none` fork; IC's counterfactual rates the served load with export 0. On a grid-only network import = load: equal to solver tolerance. The `none` option is still solved (its bill feeds findings and the report).

### 4.9 Tornado bounds — C5
- CAPEX bounds: `dataclasses.replace` on `case.assets[battery].overnight_cost`, `case.inputs.replacement_capex`, `case.inputs.terminal_value`, and the battery `fom` template line (the pack ties FOM to the inverter investment, `packs.battery_fom_eur_per_mw`); dispatch unchanged at fixed sizes (as GS). Expected delta 0 vs GS's `_capex_bound`.
- RATE bounds: `replace` on `inputs.wacc_nominal`, `inputs.cost_of_equity` and the terminal value (its annuities use the rate); `case.lp_basis` keeps the centre rate, so `wacc_gate` reads `differs`. **Accepted for rate rows only**: GS's `discount_rate_differs_from_lp` refusal is removed for rate bounds and kept everywhere else; the bound's case carries `wacc_gate_differs_on_rate_bound`. GS's `_rate_bound` also rewrote the battery `capital_cost` and the assets' `discount_rate` on a network copy (which moved asset economics' FOM-free numbers only); under C5 nothing re-reads the network. Expected delta: 0 on NPV (same cash, same rate); recorded if not, cause `rate_bound_network_not_rewritten`.
- PRICE bounds: re-dispatch as today, a new `option_case` on the variant network (2 builds of the value-flow ledger per driver run, measured in WP1).

---

## 5. What GS deletes, keeps and renames (parent §3 rules, §4 rows, §5 U2)

### 5.1 Rule 1 (the engine is IC) → delete in WP10, after WP2's port is green
- `services/study/tariff.py`: `BillCalculator`, `write_tariff_prices`, `band_prices`, `_network_per_mwh`, `_export_price`, `billing_period_labels`, `DemandChargeSpec`, `parse_demand_charge_config`, `demand_charge_config`, `record_solved_demand_charge`, `solved_demand_charge_config`, `demand_charge_eur_from_network`, `SOLVE_DEMAND_CHARGE_META`, the typed error family (→ `compile.CompileError(code)`, same codes). `validate_tariff_intake` and the `energy_price_level` rule move to `compile.py`; the file goes.
- `services/study/proforma.py`; `services/study/proforma_xlsx.py`.
- `services/solver/objective.py::_wrap_with_demand_charge`, `DemandChargeRefused`; `services/solver_service.py`: `SolverConfig.demand_charge`, its wrapper call, the F1-B4 `record_solved_demand_charge` call; `models/schemas.py`'s `demand_charge` field.

### 5.2 The `demand_charge_eur` term and F1-B4
Remove from `services/results/objective_decomposition.py::_bridge` the `demand_charge_eur` term, its addition to `lp_basis_total`, and the imports of `demand_charge_eur_from_network` / `solved_demand_charge_config`; remove the F1-B4 meta record (`n.meta["pypsa_gui_solve_demand_charge"]`, written by `record_solved_demand_charge` in `run_simulation`, read by `solved_demand_charge_config`). **What replaces it:** commercial terms reach `cost_breakdown` as the "Commercial" component through `cost_rows.commercial_cost_terms` (facade), computed from what the solve COMMITTED (`n.meta["ic_poc_links"]`, `ic_demand_peaks`, `links_t["ic_energy_price"]`). That is the property F1-B4 added for GS ("price the charge the solve carried, not the current config"), now IC's; a changed config after the solve is flagged by `billing._drift_flags` (`config_changed_since_solve`). `runner._read_option`'s `demand_charge_eur` comes from `engine_adapter.demand_charge_eur` over `commercial_cost_terms` (C3). Old networks still carrying the F1-B4 key: ignored on read (test: such a network loads and bridges without it). F1-B4's three tests (`test_decomposition_closes_with_the_demand_charge_term`, `test_the_bridge_reads_the_demand_charge_the_solve_used_not_the_current_one`, `test_a_network_solved_before_the_record_falls_back_to_the_given_config`) are ported as: decomposition closes on a commercial fork with the Commercial component (residual gap ≈ 0, unverified on master's `_bridge`), and a fork whose config changed after the solve reads the committed demand amount and the bill flags drift.

### 5.3 GS's `validation_service` tariff checks and F1-B6 → IC port (U1 f), removed by GS in U2
Removed from `services/validation_service.py`: `_check_export_cycling`, `_site_storage`, `_cross_hour_cycling` (F1-B6) and the call in the LOPF branch (the merge with master puts it beside master's `_check_commercial`, §8). Kept: the `gen_zero_costs` exemption for `eh_role = grid_supply` (not a tariff check). **What the port into `commercial/preflight.py` must carry** (GS ref `7c54cc4` and S3):
1. **Same-hour cycling**, code `tariff_export_exceeds_import` semantics: warn when `export credit × efficiency(import link) − import price > 1e-9` in any snapshot; message names both Links, the count of snapshots, the first one and the maximum gain per MWh. IC's `commercial.arbitrage_loop` already covers the same-hour case on the commercial adders; the port confirms it includes the forward link's efficiency and the minimum-gain threshold.
2. **Cross-hour cycling through storage** (F1-B6, `tariff_export_exceeds_import_via_storage`): with storage at the site end, warn when `max_t(export credit) × efficiency(import link) × η_round_trip − min_t(import price) > 1e-9`; storage = a StorageUnit that has or may build power with `max_hours > 0` (η = `efficiency_store × efficiency_dispatch`) or a Store that has or may build energy (η = 1), active rows only, the most efficient first; hour order deliberately ignored (never misses what the LP could exploit); a pair already flagged in the same hour is not flagged twice; a warning, never an error; message names the Links, the storage, the best credit and cheapest price with their first timestamps and the gain.
3. **Prices read from the commercial layer**, not `links_t.marginal_cost`: the import price = the PoC adders (`_adders` import side, energy and `network:*` items and non-convex tier adders) and the export credit = `ic_export_price` (C3) plus export revenue items; pairs = (`poc_link` or each `group_members` Link, `export_link`).
4. **Performance guards** from S3 BC-S3-1: nothing densified when there is no export link or no price; only the paired Links' prices built.
5. **The seed-tariff property**: neither illustrative seed flags cycling with a battery at the site (`test_the_seed_tariffs_do_not_flag_cycling_with_a_battery_at_the_site`).
**Open (owner):** GS's check also warns on NON-commercial networks with reverse Link pairs priced through `marginal_cost`. Removing it returns those networks to their pre-S3 state (no warning). Either IC's port keeps a `marginal_cost` variant, or GS keeps that half in `validation_service` (Q16).

### 5.4 Vocabulary rename (frontend contract change, GS files only)
| Name today | After U2 |
|---|---|
| `Engine` `"bill_calculator"` | `"tariff_engine"` |
| `Engine` `"cash_flow_expander"` | `"finance_engine"` |
| `Bill.engine: Literal["bill_calculator"]` | `Literal["tariff_engine"]` |
| `CaseSources.bill_refs = ["bill_calculator:none", "bill_calculator:<option>"]` | `["tariff_engine:counterfactual", "tariff_engine:<option>"]` (the baseline is the engine's counterfactual) |
| `CaseKpis.lcos` | `CaseKpis.levelised_cost` from IC's LCOE fields with `levelised_cost_basis` (`nominal` / `real`) and a label "levelised cost (finance engine)"; `null` with the engine reason in the guided basis (§4.3) |
| `CaseKpis.salvage_eur` | `CaseKpis.terminal_value_eur` (C2), with `salvage_basis = "fixed_from_remaining_annuities"` |

Files: `models/study.py` (`Engine`, `_RUN_ENGINES`, `Bill`, `CaseSources`, `CaseKpis`, `InvestmentCase._salvage_has_a_basis`), `services/study/{report,findings,report_charts,render_*}.py`, `api/decisionStudies.ts` (the `Engine` union, `Bill.engine`, `CaseKpis` fields), `utils/decisionVocabulary.ts` (engine labels, `KPI_LABELS`, HELP codes: drop `lcos_excludes_charging_energy_cost`, `lcos_two_definitions`, `salvage_annuity_pv_remaining_life`; add `salvage_fixed_from_remaining_annuities`, `irr_cash_changes_sign_more_than_once`, `wacc_field_holds_the_real_rate`, `wacc_gate_differs_on_rate_bound`, `lcos_not_in_engine`, `demand_peak_hourly_resolution`), `pages/decision/vocabularySource.test.ts` (its label maps), `pages/decision/Verdict.tsx` (`fig.engine === 'cash_flow_expander'`), `pages/decision/__fixtures__/*.json`. **Stored studies** written before U2 hold the old strings and `_Model` forbids unknown values: a `mode="before"` validator on `Figure`, `ValueStream`, `Bill`, `CaseSources`, `CaseKpis`, `InvestmentCase` maps old → new on read (never written back in the old form).

### 5.5 Rules 2–5
- **Keep (rule 2):** `questions.py`, `ledger.py` (+ `engine_path`), `forks.py`, `runner.py`, `tornado_runner.py`, `findings.py`, `report.py`, `report_charts.py`, `render_docx.py`, `render_html.py`, `report_xlsx.py` (rebuilt), `run_hashes.py`, `store.py`, `explain.py`, `body_limit.py`, `packs.py` (network building, two-annuity battery, load upload), `models/study.py` view shapes, all of `pages/decision/*`.
- **Add (rule 3):** `services/study/compile.py` (rules C1–C6), `services/study/engine_adapter.py`.
- **Change (rule 4):** `library.py` reads the pack; `study_library/` removed in WP10 (load profiles per Q7).
- **Prepare (rule 5):** `LedgerRow.engine_path`, `FindingsHashes.compiled_hash`, `run_hashes.compiled_matches`.
- **`models/study.py::Tariff`** (owner, Q11): proposal — keep it renamed `TariffForm` as the guided intake form only, with no pricing code in GS; `compile.tariff_to_engine(form)` its only consumer; `compile.form_from_tariff(tariff) -> TariffForm | None` to show a pack or Expert tariff in the guided step ("Open in Expert to edit" otherwise).

---

## 6. The generic defaults pack (U1 a) as GS needs it

### 6.1 Fields
Pack level: `pack_id`, `version`, `hash` (→ `AssumptionsLedger.ledger_version = "generic-defaults <version> <hash[:12]>"`, `CaseProvenance.library_version`), `basis` (`real / pre / excl` + source), `perspective` (`site_owner`), `currency`, `currency_year` (2020) + source.

| Per value row | Why GS needs it |
|---|---|
| `key` (GS's 20 keys + rows 21–34) | ledger key; tornado and report read by name |
| `engine_path` | `LedgerRow.engine_path`; rule 5 |
| `value` (nullable) + `note` (`not_used_in_mvp1`, `mvp_basis_no_degradation`, `rule_descriptor`) | ADR-0001; non-editable rows |
| `unit`, `basis` | `apply_user_row` unit check; `LedgerRow.basis` |
| `currency_year` (null on non-money rows) | BC-S2-4 one-currency-year refusal (moves to `compile`) |
| `projection_year` | BC-S2-5 "(2030 projection)" label and note |
| `range {low, high, source: assumed\|source}` | tornado bounds (`findings.bounds_for`), tested range in report and XLSX |
| `domain` | BC-S2-3 |
| `source`, `source_year`, `source_url` | provenance everywhere |
| `illustrative: bool` | maturity badge (replaces the `source == "illustrative"` string test) and the report appendix |
| `derived {inputs, formula_id}` | `ledger.refresh_derived` (round trip = inverter efficiency squared) |

Per tariff: the IC `Tariff` **plus** `currency`, `currency_year`, `source`, `source_year`, `illustrative`, `billing_period`, the export price (C3 mints its series from it), `cap_mw`, honesty codes with their help sentences, and the `default` flag. IC `Tariff` has none of these fields, so they are pack metadata beside it (Q7). Rules: `horizon_rule`, `replacement_rules`, `sizing_limit` and rows 21–34. Load profiles: not named in parent rule 4; pack series or GS keeps `study_library/load_profiles/` (Q7).

### 6.2 `ledger.seed_ledger` changes
- `seed_ledger(question, intake, defaults: GuidedDefaults)`; `reseed_ledger` / `reset_rows` take `defaults`. `Library`, `load_library`, `LIBRARY_VERSION`, `LIBRARY_DIR` go; `UnknownLibraryVersion` → "this build carries pack version X only".
- Rows come from pack rows; `_TECH_KEYS` keeps only GS's labels, technical names and help per row id (a pack row without a GS label is refused at load).
- Every row carries `engine_path` and, for pack rows, `illustrative`; rows 21–34 seeded (descriptors non-editable).
- `_tariff_rows` reads the pack tariff's metadata and the IC demand item's rate × 1000; the single-band test reads the IC energy item's distinct rates.
- `maturity_from_ledger`: the tariff row holds screening when `illustrative` or `provenance=library` (BC-S2-1 on the flag).
- `ledger_hash` unchanged in shape; `engine_path` not hashed.

---

## 7. Work packages (test first)

### WP0 — Merge master (with U1) and freeze the pre-U2 numbers
**Do.** Merge `origin/master` after both U1 PRs (conflict rules in §8). Before any U2 change, run `qa_decision_study.py` and the site golden tests on the merge and write `tests/fixtures/u2_pre_numbers.json`: driver EVIDENCE (sizes, verdict, NPV centre and bounds, CAPEX, FOM, IRR, paybacks, LCOS, every option's bill by component), the S5 golden table (`bess_2h`, `bess_pv_2h`), both seeds' bills on the golden dispatch, and the 3-month toy LP's GS-wrapper objective and peaks.
**Acceptance.** `test_u2_pre_numbers_recorded.py`: every key the later comparisons read exists; the commit predates WP1. Full backend suite, vitest, tsc and all three QA drivers pass on the merge.
**Mutation.** Alter one frozen NPV by 1 EUR: WP7's parity test goes red (recorded when WP7 lands).

### WP1 — Facade spike on the site pack (tests only)
Turn each (unverified) item into a fact: `value_flow_templates.build("single_owner")` owner assets (meter Links owned? → capex `not_established` from `overnight_cost=None`, Q5); `rate_meter` on the unsolved baseline pack; preflight on the compiled config (no error; no `demand_resolution` with `settlement="h"`; no `tariff_out_of_validity`); `build_finance_case` refusals until rows 21–34 exist and its flags after; **C1**: on the IC branch as read, `finance_case._assets` reads only the typed `overnight_cost` (so a battery without it reads `overnight_cost_missing`), which confirms C1 depends on S0b's `upfront_parts` read; **C3/C6**: mint a flat export series for a study in local mode (which org? `binding.BindingRefusal("library_org_unknown")` if none — studies run in local mode only) and bind it on a **study-owned fork** off the foreground (`pypsa_service._study_owned`), via `_bind_commercial` if it can address the fork, else `binding.bind_commercial` directly (Q15); the export line from the value-flow ledger and its cross-check with `commercial_cost_terms`; run time of `build_finance_case` on 8,760 h.
**Acceptance.** Each test names the plan item it settles; the plan's (unverified) markers are updated in the WP1 commit. **Mutation:** none (spike); the assessor checks every marker has a test.

### WP2 — Test port (red against the adapter, before anything is deleted)
Re-express the expectations of the five GS engine test files on `engine_adapter` / `compile`, same fixtures, deltas recorded in `tests/fixtures/u2_deltas.json` (`{test, figure, pre, post, cause}`). Old files stay green and untouched until WP10.

| Source (count) | Target | Mapping |
|---|---|---|
| `test_tariff_bill.py` (32) | `test_engine_adapter_bill.py`, `test_study_compile_commercial.py`, `test_study_preflight_ported.py` | band pricing, first match, unpriced hours → compile + `rate`; monthly and yearly demand, no-demand zero, network charges, weightings scale energy not peaks, contracted capacity (with and without limit), annual-bill rule, honesty codes digit-free, partial periods → `bill`; export credit sign and zero, series ref, price-and-series refused, export-without-price → C3 export line and compile refusals; absent import/export series → `bill_meter` flags; `write_tariff_prices` tests → "prices are not in the network; `materialise_poc_prices` prices at solve" (WP6); refusals (annual peak, ratchet, measured capacity) → compile; the 7 export-cycling tests (incl. F1-B6's 4) → `validate_for_run` on a compiled fork, asserting IC's ported codes (§5.3) |
| `test_tariff_demand_charge.py` (11) | `test_engine_adapter_demand.py` | queue snapshot / partial PUT carry → `SolverConfig.commercial` through the queue; peak falls with the charge, peak = monthly max → `ic_peak_import` / `ic_billed_demand`; objective delta = charge + cost delta; objective scale leaves sizes and charge unchanged; wrapper order → IC chain (objective equality); refused modes and bases → IC `refuse_windowed_terms` codes + compile refusals; the three F1-B4 tests → §5.2 |
| `test_demand_charge_absent_is_noop.py` (1) | `test_no_commercial_is_noop.py` | a project without `commercial` solves byte-identically through `run_simulation` |
| `test_proforma_golden.py` (28) | `test_engine_adapter_case_golden.py` | interior optimum; CAPEX from the ledger (C1) and not the back-calculation; replacements at 10 and 20; FOM = asset economics = cost breakdown; VOM own assets only; flat path; NPV/IRR/payback = oracle; NPV = LP saving × AF (§4.6); LCOS → `levelised_cost` null with reason (delta recorded, §4.3); market revenue at duals excluded; salvage = remaining annuities (C2) and the uncomputed-salvage / basis rules; value streams sum to savings; null bill → not established; one currency year; provenance; tariff notes as codes; whole-year lifetimes; "a rate other than the LP's is refused" → refused at the centre, accepted with `differs` on rate bounds (C5); baseline has no case |
| `test_proforma_xlsx.py` (8) | `test_engine_adapter_workbook.py` | IC sheets present; the GS formula sheet's NPV = CF0 + NPV(CF1:CFn) evaluates to the case; every year row; assumptions = ledger with `engine_path`; formula-looking text stays text; not-established case writes no NPV; the in-repo evaluator tests unchanged |

Adapt in place (WP3–WP8 as their code moves): `test_study_pack.py` (36: no prices in the network; `option_solver_config` carries `commercial`/`finance`; F1-B6's pack assertion moves to the preflight port), `test_study_findings.py` (18), `test_study_runner.py` (22: `demand_charge_eur` from the adapter; fork binding C6), `test_study_tornado_lp.py` (6: C5 bounds, the marginal construction), `test_study_library.py` (53: pack-backed seeding, §6).
**Acceptance.** Every source test has a row in the port map (`tests/fixtures/u2_port_map.json`: source test → target test or "dropped: <reason>"); `test_u2_port_map_complete.py` fails if a source test name is missing. **Mutation:** delete one map row → red.

### WP3 — Defaults pack → ledger
**Tests.** `test_study_ledger_from_pack.py`: seeded ledger = WP0 ledger row for row on (key, value, unit, basis, currency_year, range, domain, source, source_year, sensitivity_flag); rows 21–34 with `engine_path`; descriptors refused by `apply_user_row`; `illustrative` drives maturity; re-seed / reset as before (F1-B5's three branches kept); `ledger_version` names pack version and hash. **Mutation:** drop `illustrative` from the DE seed → maturity red.

### WP4 — `compile.commercial_from_ledger` (+ C3)
**Tests.** `test_study_compile_commercial.py`: unit conversions; band → periods reproduce GS prices on 8,760 h of both seeds (rated through `tariff_engine.rate` on a 1 MW constant dispatch); level rescale; every GS refusal code kept + `capacity_basis_mismatch`; `network:*` ids; `item_component` complete; export series minted once per study and idempotent; `cap_mw` → `ConnectionAgreement.export_cap_mw` and an LP test that export ≤ cap; digest stable. **Mutation:** forget ÷ 1000 on the demand item → red; reverse band order → red on the TOU seed.

### WP5 — `engine_adapter.bill` / `bill_meter`
**Tests.** WP2's bill targets go green: every component of both seeds = WP0 to 1e-9 relative; `export_credit` from the value-flow line; `network` = Σ `network:*`; unmapped kinds → `not_established`; `STREAMS` still imports; `value_streams` sums to savings; preview unchanged. **Mutation:** map `network:energy:*` to `energy` → per-component parity red, totals green.

### WP6 — LP switch
**Do.** `option_solver_config` → `compile.solver_config`; `build_site_network` writes no prices; forks bound per C6; `runner._read_option` reads `demand_charge_eur` via `commercial_cost_terms`. (The GS wrapper is still present but unused; deleted in WP10.)
**Tests.** WP2's demand targets green; 3-month toy objective ≤ 1e-7 vs WP0; year-billed toy (or refuse the year basis with `year_billed_demand_formulation` until settled); no-commercial no-op; site golden `bess_2h` through the queue: `p_nom_opt` within 1e-4 MW and objective within 1e-6 relative of WP0; `billing_vs_lp_gap` unattributed 0 for energy and demand; `validate_for_run` on a fork raises no error. **Mutation:** `settlement="15min"` → the demand-resolution warning test red; drop the demand item → golden objective red.

### WP7 — `compile.finance_from_ledger` + `engine_adapter.option_case` (C1, C2, C4, C5)
**Tests.** WP2's case targets green: S5 table = WP0 to 1e-9 (IRR 1e-9 absolute); §4.6 identity ≤ 1e-6; WACC gate consistent at the centre and `differs` (accepted) on rate bounds only; post-tax KPIs `None` with reasons; no `contingency_share` → `contingency_share_missing`; `escalation_tariff=None` → `escalation_missing:tariff`; C1 (once S0 lands): the two upfront parts through `upfront_parts` equal the ledger's and leave the objective and sizes byte-identical; C5 bounds equal GS's `_capex_bound` / `_rate_bound` NPVs (WP0) and re-read no network; `case_streams_reconcile_with_engine`. **Mutations:** `salvage_rule → none` → identity red; set `overnight_cost` on the StorageUnit → LP-identity red; `escalation_tariff = 0.02` → real-basis red.

### WP8 — Findings, tornado, runner, routes; vocabulary rename
**Do.** `findings._case` / `_bill_of` / `_price_bound` / `_capex_bound` / `_rate_bound` / `_centre` / `load_inputs` on the adapter; `runner._worker` compiles, mints, binds and writes `commercial` + `finance` into each fork; routes; §5.4 rename with read-compat; `FindingsHashes.compiled_hash`.
**Tests.** Ported `test_study_findings`, `test_study_tornado_lp`, `test_study_runner`, `test_study_decision_report`, `test_study_report_live_lp` with WP0 values or recorded deltas; `test_study_engine_literal_compat.py` (a stored pre-U2 study loads; new writes use the new names); `test_study_compiled_hash.py` (editing a fork's commercial → 409 `engine_inputs_changed_since_run`, report stale); vitest: `vocabularySource.test.ts`, `Verdict.render.test.tsx`, `Findings.render.test.tsx` on renamed fixtures; HELP mirror test. **Mutation:** remove the legacy mapping → stored-study red; skip `compiled_hash` → 409 test red.

### WP9 — Workbooks
**Do.** `engine_adapter.case_workbook` and `report_xlsx` on `build_workbook`; GS sheets appended: `Assumptions` (ledger + `engine_path` + tested range), `Provenance` (pack version and hash, compiled digest, export series ref, engines, basis, currency year), **`Cash flows (formulas)`** (a view over the engine's cash series with live discount-factor, discounted and cumulative formulas and NPV pinned as `=CF0 + NPV(rate, CF1:CFn)`; owner may drop it, Q13). **Tests:** WP2's workbook targets. **Mutation:** start the NPV range at CF0 → red.

### WP10 — Delete, then driver, golden matrix, packaging, findings note
**Do (in this order).** (1) Confirm every WP2 target green and the port map complete. (2) Delete §5.1, §5.2, §5.3 code and the five old test files. (3) `qa_decision_study.py`: `_reconcile` uses compile; the §4 judging rule and `EVIDENCE["u2_deltas"]`; new checks — forks carry `commercial` and `finance`; the case's engines are `tariff_engine` / `finance_engine`; the WACC gate consistent; the §4.6 identity on `bess_1h` (else the `BY_CONSTRUCTION` codes are dropped, §4.2); the export series ref is the study's. (4) `tests/golden/site_fixture.py`, `coverage.py`, `test_golden_coverage.py::ROUTE_FILES`. (5) `study_library/` removed (or `load_profiles/` kept per Q7); `pypsa-gui.spec` `datas`, `smoke/check_bundle.py::EXPECTED`, `tests/test_packaging_requirements.py`. (6) Findings note `docs/superpowers/findings/2026-10-05-guided-study-u2.md` (deltas, port map, mutations).
**Acceptance.** Driver passes with the same verdict class, drivers, best and skipped options; `test_study_one_engine.py`: GS imports IC only from `engine_adapter.py` and `compile.py`, only facade names, and nothing imports `BillCalculator`, `proforma`, `_wrap_with_demand_charge` or `SolverConfig.demand_charge`. **Mutation:** re-add a `BillCalculator` import in `findings.py` on the copy → red.

### Pre-PR checklist (parent §7)
- [ ] Merge the latest `origin/master` right before the PR; resolve shared hot files by the §8 rules.
- [ ] Full backend suite (`pixi run gui-tests`), vitest, `tsc`.
- [ ] QA drivers of both efforts: `qa_investment_case.py`, `qa_value_flows.py`, `qa_decision_study.py` (`pixi run gui-qa-drivers`).
- [ ] Ported golden tests green; `u2_deltas.json` complete with causes; port map complete.
- [ ] No IC-owned file in the diff.
- [ ] Progress recorded here, not in the parent plan; the owner merges.

- [ ] Gate U2

---

## 8. The merge with master (WP0)

A dry `git merge-tree --write-tree HEAD origin/master` at `10f8abc` / `855bbac` gives exactly **13 conflicted files**, all in parent §5's shared list. Rule for all: keep both sides (additive), GS's U2 deletions happen later in WP10, never in the merge commit. Per file:

| File | Conflict | Resolution rule |
|---|---|---|
| `backend/main.py` | GS `studies.router` include vs master's study-reports router include (`/{name}/reports…`) | keep both includes; both before `projects.router`; GS's `_SOLVER_BLOCKING_EXEMPT_PATTERNS` entry kept |
| `backend/routers/projects.py` | `_BUNDLE_DIRS = ("uploads", "studies")` (+ `_STUDY_FORK_META_KEYS`) vs `("uploads", "reports")` | `_BUNDLE_DIRS = ("uploads", "reports", "studies")`; keep `_STUDY_FORK_META_KEYS`; both comments; `tests/test_study_forks.py` pin re-run |
| `backend/services/solver_service.py` | F1-B4 `record_solved_demand_charge` block vs master's commercial price-frame persistence after a successful solve | keep both under the same `status in ("ok", "optimal")` guard (GS's goes in WP10) |
| `backend/services/validation_service.py` | GS `_check_export_cycling(n)` call vs master's `_check_commercial(n, solver_config)` | call both (GS's removed in WP10 once IC's U1 (f) port is in) |
| `backend/smoke/check_bundle.py` | GS `EXPECTED` (decision report template, `study_library` files, `default.docx`) vs master's `synth_dunkelflaute.json` | union of entries (WP10 drops the `study_library` names) |
| `backend/tests/fixtures/tool_schema_audit_phase1.csv` | GS `list_scenarios` `study_owned` / `owner_study_*` rows vs master's Library tool rows | union of rows, sorted as the file is; re-run the schema audit test |
| `backend/tests/test_packaging_requirements.py` | GS `_spec_backend_datas` block vs master's `_guarded_gridspine_modules` | keep both blocks and their tests |
| `backend/tests/test_qa_support_sandbox.py` | two hunks: comment wording, and master's `together.wait()` | take master's code (`together.wait()`, all four sessions held open) and master's comment; it is the stricter version of the same fix |
| `frontend/src/App.tsx` | 4 hunks: `DecisionPanel` vs `ReportsPanel` + `recoveryFor` imports; `decision` vs `reports` panel titles; `FULL_SCREEN_TABS`; render cases | keep both everywhere; `FULL_SCREEN_TABS` = master's set + `'decision'` |
| `frontend/src/layout/Sidebar.tsx` | icon imports (`Scale` vs `FileText`, `Compass`); GS "Decision study" `SItem` vs master's "Reports" `SItem` | union of imports; both items, master's order with "Decision study" after "Reports" |
| `frontend/src/store/uiStore.ts` | `SlidePanel` union (`'decision'` vs `'hubDesign' \| 'reports'`) | union of the three |
| `pypsa-gui.spec` | GS `collect_data_files("docx")` vs master's `collect_data_files("docx", includes=["templates/*"])` | GS's (it also collects `docx/parts` templates read as `parts/../templates`, S9 [S3]), plus master's comment; `study_library` datas kept until WP10 |
| `tool-error-kinds.json` | 5 hunks: GS study kinds (`fork_changed_since_run`, `fork_has_children`, `fork_solving`, `intake_changed_since_run`, `ledger_changed_since_run`, `report_never_assembled`, …) interleaved with master's (`eh_report_not_found`, `inline_tariff_would_be_replaced`, `library_ref_stale`, …) | union, alphabetical as the file is; U2 adds `engine_inputs_changed_since_run` and `engine_refused` |

**`services/chat_tools.py` (manual merge).** It auto-merges against today's master, but GS moved about 300 lines into `services/study/explain.py` while IC changed about 1,600 lines; once U1 is on master the merge is manual. Rule: take master's (IC's) file as the base, re-apply GS's move as a pure delegation to `study/explain.py` (no behaviour change), re-apply F3's `ProjectInfo` study-owned field descriptions (`ac9d6dc`), then run the chat tool tests, `tests/fixtures/tool_schema_audit_phase1.csv` audit and `tests/test_hourly_assumption_audit.py`. Other shared files that auto-merge today (`models/schemas.py`, `services/adequacy/campaign.py`, `services/chat_tools_schema.py`, `services/project_context.py`, `services/pypsa_service.py`, `services/results/objective_decomposition.py`, `tests/test_golden_coverage.py`, `api/simulation.ts`) are re-checked after the U1 merges, which add IC lines to several of them.

**F1 mapped onto U2.** F1-B1 (boot reconciliation), B2 (body limit), B3 (single-band skip code), B5 (ledger branches), F1–F5 (frontend): untouched, their tests kept (B3 and B5 re-run on the pack-backed ledger). **F1-B4** (`c7d3ae0`): removed with the GS demand charge (§5.2); its property survives in IC's committed-term records; its tests ported. **F1-B6** (`7c54cc4`): removed from `validation_service` (§5.3) after IC's U1 (f) port carries it; its four tests ported in WP2 onto the compiled fork's preflight.

---

## 9. Risks and open questions for the IC session (asks go through the owner)

**Risks.**
- **R1 — C1 is blocked on S0** (§4.5, parent v1.3 §10): a blended `overnight_cost` would change the LP, so the battery's two upfront parts go through `upfront_parts`, which does not exist until the schema core (S0) and the finance read (S0b) land. S0 has no owner yet (owner decision pending); U2's WP7 cannot start before it.
- **R2 — Export via a Library series in local mode (C3, C6).** Studies run in local mode only (BC-6) while Library series resolve in the project's org (`_bind_commercial` refuses `library_org_unknown` without one), and `_bind_commercial` binds the ACTIVE context, not a runner fork. If WP1 confirms either, U2 needs `binding.bind_commercial` in the facade and an org for study-owned projects (Q15).
- **R3 — Owner assets** (`single_owner` and the meter Links, Q5).
- **R4 — LP degeneracy**: equal objectives with different dispatches can move bills and re-dispatched NPVs more than the objective; tolerances in §4 / WP6, larger moves need a cause.
- **R5 — LCOS disappears** from the guided case under parent §4's rule (§4.3, Q8).
- **R6 — Stored studies** with old literals fail to load without the read-compat (§5.4).
- **R7 — Losing the non-commercial cycling warning** (§5.3, Q16).
- **R8 — Facade drift**: U2 needs names outside parent §6 (Q4, Q15).
- **R9 — Run time**: price bounds rebuild the value-flow ledger on 8,760 h (C5 keeps CAPEX and RATE bounds cheap); measured in WP1.

**Open questions.**
- **Q1** *Answered by parent v1.3 §10:* the two-lifetime battery's parts live in the schema core's custom part columns and are read through `upfront_parts` (S0, S0b). Open only: who owns S0, and when.
- **Q2** `replacement_capex` amounts per MW (or as a share of the asset's overnight cost), so a persisted `FinanceInputs` survives a re-solve.
- **Q3** A `TerminalValueRule` method for the remaining-life annuity value, so the Expert face shows the rule rather than a resolved number (C2).
- **Q4** Facade additions: `finance.report.assemble_finance_sections`, `finance.engine.payback`, `finance.metrics.irr` / `npv`, the `tariff_engine.RatingResult` type, and the value-flow export line (`value_flows._export_revenue` or a public accessor).
- **Q5** Should `single_owner` assign the PoC meter Links to the owner as assets? If yes, how do non-investment assets avoid `overnight_cost_missing`?
- **Q6** A currency-year field (C4 / U1 d) machine-readable on `FinanceInputs` or the report, not only a note.
- **Q7** Pack schema: the §6.1 metadata beside each IC `Tariff`; `derived` formula ids; load profiles as pack content or not.
- **Q8** (owner) Accept `levelised_cost = null` for the guided battery case, or ask IC for a storage LCOS metric?
- **Q9** `jurisdiction` / `valid_from` for illustrative tariffs.
- **Q10** (owner) `tax_levy` / `certificate` items in the guided bill: a seventh component, or under `network` / `energy`?
- **Q11** (owner) Keep `models/study.py::Tariff` as a guided intake form (`TariffForm`) that only compiles, or move the guided tariff step onto IC items?
- **Q12** Confirm `commercial_cost_terms` exposes the demand amount per item for `demand_charge_eur` and that master's `objective_decomposition._bridge` closes with the "Commercial" component.
- **Q13** (owner) Keep the formula cash-flow sheet as a view over the engine, or rely on IC's static `build_workbook` only?
- **Q14** Is the export revenue in the P3 ledger streamed as `energy_export` (export escalation class) in both the actual and the counterfactual templates?
- **Q15** `binding.bind_commercial` in the facade, and the org a study-owned project's Library series live in (C6, R2).
- **Q16** (owner) Port the `marginal_cost` reverse-pair cycling check for non-commercial networks into IC's preflight, keep it in `validation_service`, or drop it?

---

## U3 outline (Guided face inside Guided mode)

**Start card.** Master's Guided shell: `frontend/src/pages/hubDesign/HubDesignPanel.tsx` (cards `start`, `site`, `goal`, `results`, `improve`), `cards/StartCard.tsx` (today example sites from `layout/NewProjectWizard.tsx::TEMPLATES` filtered to `eh_*`, created through `hooks/useCreateFromTemplate.ts`), opened as the `hubDesign` slide panel (eyebrow `GUIDED`) in `App.tsx`. U3 adds decision 5's second path beside it: "Is my site reliable?" (the Energy Hub cards) and "Is this investment worth it?" — a question picker in decision 3's order (battery at site; data-centre power after U4; waste heat, hydrogen, off-grid shown as coming). Picking the battery question opens the decision intake (`pages/decision/DecisionPanel.tsx`, `Intake.tsx`) inside the Guided shell with the key-parameter list (decision 4) as one view over `sensitivity_flag` rows plus the intake answers; everything else collapsed as "from the generic defaults (source, year)".

**Study chat tools** (`services/chat_tools.py`, `chat_tools_schema.py`, shared hot files, additive), each with its `Safety: <tier>` marker: `start_decision_study(question_id, site, load, tariff_id, pv)` — `write`; `set_study_parameter(study_id, key, value, unit)` — `write` (through `ledger.apply_user_row`, unit required); `run_decision_study(study_id, fidelity)` — `execution_long_running`; `get_study_findings(study_id)` — `read`; `explain_verdict(study_id)` — `read` (`findings.explain`); `generate_study_report(study_id, format)` — `write`.

**Confirmation tiers.** `services/chat_service.py::GUIDED_CONFIRM_TIERS = DESTRUCTIVE_TIERS | {"write"}` via `_confirm_tiers(guided)` and `_is_guided(ui_context)` (`ui_context["ui_mode"] == "guided"`): in Guided every study tool except the two reads shows a card; in Expert only `run_decision_study`. `_guided_mode_addendum(step)` gains the study steps. Study cards hand work to the assistant through `pages/hubDesign/delegate.ts::delegate(text, {label, group})` → `store/chatStore.ts::sendRequest` (queued; `components/ChatPanel.tsx` sends when no turn streams and no card waits), and `ask(text)` for "explain this".

**"Open in Expert".** `store/uiStore.ts::setUiMode('expert')`, switch to the option fork (or base project), open Results → Investment (`frontend/src/pages/results/InvestmentTab.tsx`, IC-owned) with every pack-sourced field badged "default (source, year)" by matching `LedgerRow.engine_path` (needs an IC hook in the Investment editors). Requires Q1–Q3 answered (R1).

**Rule 5.** On a study fork, a `solver_config` whose `commercial` / `finance` differ from the last compile (`compiled_hash`) is diffed by `engine_path`: matching rows become `status=customised`, `provenance=user`, `changed_by=expert`, `changed_at`; an engine field with no row is noted `expert_edit_without_ledger_row:<path>`; findings go stale through `run_hashes`. The diff runs in GS's study routes on read, never inside IC's routes.

**"Include German taxes" (decision 2, later).** Future key parameter `tax_pack` (row 31): `off` / `eu_de`. The U2 compiler already accepts it: `eu_de` → `FinanceInputs.tax_pack_id = "eu_de"` and requires rows the pack supplies or the user states — `hebesatz_pct`, `tax_losses`, `financing_fee_tax`, `depreciation_class_by_asset["battery"]` (the BMF AfA tables list no battery class, so `eu_de` leaves it `not_established` unless stated); the basis label flips to `tax=post` only when the tax section is `ok`; the fixed terminal value (C2) is then taxed as EBITDA. WP4/WP7 include a test that `tax_pack=eu_de` with those rows missing compiles, runs, and reports post-tax `not_established` with the pack's reasons (no refusal, no 0 tax).

**Flag.** Drop `PYPSAGUI_DECISION_STUDIES` once OPEN-ITEMS 1 (PR #71) is merged (parent §5).

---

## Definition of done (U2)

One tariff (IC `Tariff` compiled from the guided form or a pack tariff), one bill (`tariff_engine` through `bill_site` / `rate_meter`, export from the value-flow ledger), one cash-flow and KPI path (`run_case`); `tariff.py`, `proforma.py`, `proforma_xlsx.py`, `_wrap_with_demand_charge`, `SolverConfig.demand_charge`, the `demand_charge_eur` bridge term (F1-B4) and GS's export-cycling checks (F1-B6) gone, each after its tests were ported; every ledger row names its `engine_path`; vocabulary renamed with read-compat; `qa_decision_study.py` passes with the same verdict class and its deltas recorded; the pre-PR checklist ticked; the PR merged by the owner.
