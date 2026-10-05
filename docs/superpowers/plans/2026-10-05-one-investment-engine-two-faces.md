# Plan: one investment engine, two faces (expert workbench and guided study)

**Date:** 2026-10-05. **Status:** v1.3. Owner decisions in §8 taken 2026-10-05; independent review PASS WITH CONDITIONS, all conditions applied (§9); C1 corrected after the asset-parameterisation assessment (§9 R12, §10).
**Replaces nothing yet.** It sequences two existing efforts so they converge instead of colliding:

- **Edge Investment Case (IC)**: spec `docs/superpowers/specs/2026-09-26-edge-investment-case-design.md`.
  P0–P2 and P3 WP3.0/3.1/3.5 are on master (`services/commercial`, `services/library`,
  `services/finance/packs`). P3 rest and P4 (finance engine) are on
  `claude/energy-tool-features-research-fdixs0`. Session: "Energy tool feature research and benchmarking".
- **Guided investment study (GS, "decision study")**: spec
  `docs/superpowers/specs/2026-09-28-guided-investment-study-design.md`, MVP-1 done plus follow-up F1, on
  `claude/edge-tool-ux-research-n0n2l6` (never merged, branched from master at `67c4c77`, before IC P0–P2
  and before Guided mode landed). Session: "Edge tool competitive analysis and UX design".

## 1. What the owner wants

One investment case implementation. **One** tariff model, **one** bill, **one** cash-flow and metrics
engine, **one** library, **one** report of assumptions. Served by two faces:

| | Expert face | Guided face |
|---|---|---|
| Who | Seasoned engineers and modellers | Non-specialists, decision makers, consultants in a hurry |
| What they touch | Every parameter: tariff items and periods, contracts, participants, debt, tax, escalation, network | A handful of **key parameters** that move the decision (site, load, connection, the question's drivers, discount rate) |
| Everything else | Set by hand | Filled from **generic defaults** in the Library, each with source and year, all listed in the report |
| How | Workbench, Investment tab editors, chat as a power tool | Question-first walk-through, the **chat assistant guides each step**, a parameter list they can tweak, a verdict, a report |

Both faces read and write **the same project state**. A guided study opened in Expert mode shows the
full case with every default visible and editable; nothing is a second copy.

## 2. The duplication today

| Concept | GS branch (to retire) | IC engine (to keep) |
|---|---|---|
| Tariff model | `models/study.py::Tariff` (bands, demand charge, capacity, fixed, network, export) | `models/commercial.py::Tariff` / `TariffItem` / `TariffPeriod` / `Ratchet` |
| Prices into the LP | `study/tariff.py::write_tariff_prices` (marginal cost on `grid_import`/`grid_export`) | `commercial/lp_bindings.py::materialise_poc_prices` |
| Demand charge in the LP | `services/solver/objective.py::_wrap_with_demand_charge`, `SolverConfig.demand_charge` | `lp_bindings._wrap_with_commercial_bindings` → `add_demand_terms` (+ ratchets, tiers, capacity, group) |
| Bill | `study/tariff.py::BillCalculator` (six components) | `commercial/tariff_engine.rate`, `billing.bill_site` / `rate_meter`, `results/billing.compute_billing(_preview)`, `gap.billing_vs_lp_gap` |
| Library | `study/library.py` + vendored `study_library/` CSV/YAML (technology-data v0.14.0) | `services/library/` (DB items: tariff, contract, connection agreement; series store; URDB import; bundle pins) |
| Cash flow, NPV, IRR, payback, salvage | `study/proforma.py::build_investment_case` → `InvestmentCase` / `CaseKpis` | `services/finance/engine.run_case` (timeline, operating, debt, incentives, tax) via `results/finance_case.build_finance_case`; `metrics`, `solve_ppa`, `wacc_gate` |
| XLSX | `study/proforma_xlsx.py` | `services/finance/export_xlsx.build_workbook` |

Two of everything means two answers on the same project, and two places to fix every bug.

## 3. Target architecture

```
              Expert face                                   Guided face
  Workbench · Investment tab editors           Question cards · Intake · Key-parameter list
  (TariffBuilder, LibraryBrowser,              · Verdict · Robustness · Report
   ContractsEditor, ParticipantsDesigner,      Chat assistant drives each step (Guided mode)
   FinanceInputsEditor, InvestmentCaseView)            │
                 │                                     ▼
                 │                       Study layer (GS, kept): questions, ledger,
                 │                       forks/runner, tornado, verdict, DecisionReport
                 │                                     │  compile(ledger) → engine inputs
                 ▼                                     ▼
   ┌──────────────── ONE engine (IC) ─────────────────────────────────────────┐
   │ CommercialConfig + FinanceInputs in SolverConfig (solver_config.json)     │
   │ Library (items + series + GENERIC DEFAULTS pack)                           │
   │ lp_bindings → PyPSA solve → tariff_engine / bill_site → finance engine    │
   │ InvestmentCaseReport, export_xlsx                                          │
   └───────────────────────────────────────────────────────────────────────────┘
```

Rules:

1. **The engine is IC.** It is deeper (15-min billing, ratchets, tiers, contracts, participants, debt,
   tax packs, incentives, SAM-oracle parity) and already half on master.
2. **The study layer is GS minus its engine.** Questions, intake, the assumptions ledger, forks and
   runner, tornado, verdict, attribution, DecisionReport and all of `pages/decision` stay.
3. **The bridge is a compiler, not a copy.** `services/study/compile.py` turns intake + ledger into a
   `CommercialConfig` and `FinanceInputs` written to the study's projects. The engine stays strict
   (ADR-0001: missing is `None` plus a flag, never 0). Guided completeness comes from the ledger
   supplying every value explicitly, each with provenance, not from engine defaults.
4. **Generic defaults are Library content.** A versioned, hashed **generic defaults pack** in
   `services/library` holds illustrative tariffs, technology costs and finance defaults (seeded from
   GS's `study_library/`: technology-data v0.14.0, `finance_defaults.yaml`, the two illustrative
   tariffs). Each row: value, unit, range, source, year, `illustrative` flag. The ledger seeds from it;
   the report's assumptions appendix lists every row the case used and whether the user changed it.
5. **Expert edits are visible to the guided face.** An engine field changed in the workbench marks the
   matching ledger row `customised` with `changed_by: expert` and makes findings stale (GS's
   `run_hashes` already does staleness).
6. **Guided mode on master is the guided face's shell.** `ui_mode`, the Start card, `delegate()` →
   `sendRequest` queue, Guided confirmation tiers and the per-turn addendum are reused; the decision
   study becomes the investment path inside it, beside the Energy Hub reliability path.

## 4. Mapping the GS call sites onto the engine

All engine calls go through one module, `services/study/engine_adapter.py`, so the swap is one seam.

| GS call site today | Becomes |
|---|---|
| `packs.effective_tariff` → `tariff.tariff_from_ledger` | `compile.commercial_from_ledger(intake, ledger)` → `CommercialConfig` with `poc_link` = the pack's import link, `import_tariff` from the ledger's tariff (a Library item ref), demand-charge and energy-level ledger rows applied as item rate scaling |
| `tariff.write_tariff_prices` in `packs.build_site_network` | Nothing in the pack; `lp_bindings.materialise_poc_prices` does it at solve |
| `tariff.demand_charge_config` → `SolverConfig.demand_charge` | `CommercialConfig` demand item; `add_demand_terms`. Delete `_wrap_with_demand_charge` and `SolverConfig.demand_charge` |
| `BillCalculator().bill` (runner, preview, case, findings) | `engine_adapter.bill(n, commercial)` over `billing.bill_site` / `rate_meter`; preview over `compute_billing_preview` |
| `Bill.by_component` six keys (`findings.STREAMS` asserts them) | `energy`, `demand`, `capacity`, `fixed` from `RatingResult.per_item`, keyed by item ids that `compile` assigns. `network` is not an IC item kind (`TariffItemKind = energy\|demand\|capacity\|fixed\|certificate\|tax_levy`): compile emits network charges as items with ids `network:*` (kind `energy`, `fixed` or `tax_levy` by basis) and the adapter sums them by id. `export_credit` is not in `bill_site`: it comes from the value-flow ledger's export line (`results/value_flows.py::_export_revenue`). The adapter owns the mapping and the assertion |
| `proforma.build_investment_case` (case route, centre, PV reference, every tornado bound) | `engine_adapter.case(n, cfg, ledger, owner)` → `build_finance_case` + `run_case` with `value_flow_templates.build("single_owner")`; returns GS's `InvestmentCase` / `CaseKpis` view shape so findings, report, charts and frontend keep working |
| `proforma_xlsx.write_proforma_xlsx`, `report_xlsx` sheet writers | `export_xlsx.build_workbook`; `report_xlsx` reuses its sheets |
| `Engine` literal `bill_calculator`, `cash_flow_expander`; `CaseSources.bill_refs="bill_calculator:*"`; `CaseKpis.lcos`, `salvage_eur` | `tariff_engine`, `finance_engine`; LCOS shown from IC's LCOE fields with a label; salvage from the terminal value (rule C2). This is a frontend contract change inside GS's own files (`utils/decisionVocabulary.ts`, `vocabularySource.test.ts`) |
| `results/objective_decomposition.py` imports `study.tariff.demand_charge_eur_from_network` for a `demand_charge_eur` term | Remove that term; commercial terms already reach `cost_breakdown` through `commercial/cost_rows.commercial_cost_terms` ("Commercial" component) |
| `validation_service._check_export_cycling` and the tariff sanity checks read `links_t.marginal_cost` written by `write_tariff_prices` | After U2 prices are materialised at solve, so these read nothing: port them into `commercial/preflight.py` (IC, U1 f) and remove GS's copies in U2 |

**What the guided ledger must supply for a minimal single-owner case** (from IC's refusal codes,
`FinanceRefused` in `services/finance/case.py`): `financial_close`, `analysis_years` (from the storage
lifetime, as GS already does), `cod_by_asset` for every owner asset (one COD), `escalation` per class
with cashflows, `wacc_nominal` consistent with the LP `discount_rate` (the WACC gate), plus
`value_flows` from the `single_owner` template. Tax: `tax_pack_id` unset in the guided basis (pre-tax),
so post-tax figures read `not_established`, which the report already discloses.

**Compile rules (the contract of `study/compile.py`; review conditions R1, R2, R7, R9):**

- **C1 Battery capex (corrected in v1.3).** The battery has two investment parts: a power part
  (inverter, EUR/kW, inverter lifetime) and an energy part (storage, EUR/kWh x max_hours, storage
  lifetime). Compile writes **both parts** as upfront values; it does **not** set PyPSA's
  `overnight_cost` on the StorageUnit, because PyPSA 1.1.2 then annuitises that one value over one
  lifetime and **ignores `capital_cost`** (`pypsa/costs.py::periodized_cost`), which would under-cost
  the battery in the LP by about 12.7 % on the library's numbers. The LP keeps the two-annuity
  `capital_cost` it has today. The finance engine reads the parts through one accessor
  (`upfront_parts`, §10 S0/S0b) instead of the typed `overnight_cost` alone. Until S0 lands, C1 is
  blocked: U2 must not ship a blended `overnight_cost`.
- **C2 Terminal value.** IC's `TerminalValueRule` has `none | book_value | multiple_of_ebitda | fixed`;
  GS's `annuity_pv` salvage has no equivalent. Compile uses `fixed`, computed from the remaining
  annuities exactly as GS does today, and the report discloses it. GS's `BY_CONSTRUCTION` finding codes
  ("NPV = LP saving x annuity factor") are re-derived or dropped in `qa_decision_study.py`.
- **C3 Export.** IC prices export only through `export_price_ref`, a Library series (per org).
  Compile mints a flat export series per study (`series_store.put_series`, using the
  helper from U1 e) and binds it through `PUT /solver_config` → `_bind_commercial` (local "project org"
  path). GS's `cap_mw` maps to `ConnectionAgreement.export_cap_mw`.
- **C4 Real basis.** `FinanceInputs` is nominal and has no currency year. For the guided pre-tax real
  basis compile writes `escalation = 0` for all six classes, `inflation = None` and
  `wacc_nominal = the real rate`; the report states "real basis, 2020 EUR" through the basis note from
  U1 d.
- **C5 Tornado cost.** The adapter builds one `FinanceCase` per option and derives every CAPEX and RATE
  bound with `dataclasses.replace` on `assets` / `inputs` (as `engine._scaled` does), so no bound
  re-runs the 8,760-hour value-flow ledger. Rate bounds make `wacc_gate` read `differs`; the adapter
  accepts that flag for rate rows only (GS used to refuse `discount_rate_differs_from_lp`).
- **C6 Study forks.** U2 verifies that `_bind_commercial` (series pins, FCA registry) works on a
  study-owned fork context (`pypsa_service._study_owned`), since OPEN-ITEMS 1 bites there before U3.

**Known semantic differences to settle in tests, not to paper over:** IRR (GS bisection vs IC's
`metrics.irr`, a grid scan plus `brentq`), salvage (C2), LCOS vs IC's LCOE, real vs nominal (C4). The GS
QA driver `qa_decision_study.py` (57 checks) is the acceptance test: same verdict class on its fixture;
numeric deltas recorded with their cause.

## 5. Phases and owners

Ownership is by files, so the sessions never edit the same module in parallel.

- **IC session owns:** `services/commercial/*`, `services/library/*`, `services/finance/*`,
  `models/commercial.py`, `models/finance.py`, `models/flex_archetypes.py`, `services/results/finance_case.py`,
  `results/billing.py`, `results/value_flows.py`, library/finance/commercial routes,
  `frontend/src/pages/results/investment/*`, `InvestmentTab.tsx`, `api/finance.ts`, `api/commercial.ts`.
- **GS session owns:** `services/study/*`, `routers/studies.py`, `models/study.py`,
  `frontend/src/pages/decision/*`, `api/decisionStudies.ts`, `utils/decisionVocabulary.ts`.
- **Shared hot files** (additive edits only, merge master right before each PR, the second lander
  resolves): `services/solver_service.py` (`SolverConfig`), `services/solver/objective.py`,
  `services/chat_tools.py` (GS moved about 300 lines into `study/explain.py` while IC changed about
  1,600: expect a manual merge in U2), `chat_tools_schema.py`, `tool-error-kinds.json`,
  `services/project_context.py`, `models/schemas.py`, `main.py`, `routers/projects.py`,
  `services/adequacy/campaign.py`, `services/validation_service.py`, `services/pypsa_service.py`,
  `services/results/objective_decomposition.py`, `smoke/check_bundle.py`, `pypsa-gui.spec`, `App.tsx`,
  `layout/*`, `store/uiStore.ts`, `tests/test_packaging_requirements.py`,
  `tests/test_qa_support_sandbox.py`, `tests/fixtures/tool_schema_audit_phase1.csv`,
  `tests/test_hourly_assumption_audit.py`. (A dry `git merge-tree` of master + GS shows 13 conflicts,
  all in this list.)
- **Master changes only through PRs the owner merges.** Merge order: U1 before U2 before U3.

| Phase | Owner | Work | Done when |
|---|---|---|---|
| **U0 Freeze** (now) | both | IC finishes the P4 gate; GS finishes F1. No new features, no master merges, no PRs | Both branch heads pushed and reported |
| **U1 Land the engine** | IC | Merge master into IC (two known conflicts: `tool-error-kinds.json`, `tests/test_hourly_assumption_audit.py`; IC lacks 89 master commits); close the P4 gate; PR P3 + P4 to master. Then, in a follow-up PR: (a) a **generic defaults pack** kind in `services/library` (versioned, hashed, `illustrative` flag) with a loader, seeded from GS's `study_library/` files; (b) a **chat tool and frontend affordance that set the commercial root** (`poc_link`, `export_link`, `timezone`). The backend already accepts it through `PUT /solver_config` (409 `no_commercial_config` otherwise), but no chat tool sets `poc_link` and the frontend's `NoCommercialConfigError` has no way out; (c) the §6 facade frozen in the IC plan; (d) a basis / money-year note on `FinanceInputs` and the report (C4); (e) a constant export price, or a helper that mints a flat export series (C3); (f) GS's export-cycling and tariff-sanity checks ported into `commercial/preflight.py`, read from the GS ref; (g) documented behaviour of a weighted week with monthly-billed items (see U4) | U1 PR merged; follow-up PR merged before U2's PR |
| **U2 Rewire the guided study** | GS | Merge master (with U1) into the GS branch. Add `study/compile.py` (rules C1 to C6) and `study/engine_adapter.py`; move every §4 call site onto them. **Before deleting anything**, port the expectations of `test_tariff_bill` (32), `test_tariff_demand_charge` (11), `test_demand_charge_absent_is_noop`, `test_proforma_golden` (28) and `test_proforma_xlsx` (8) onto `engine_adapter` tests on the same fixtures, with recorded deltas, and adapt `test_study_pack`, `test_study_findings`, `test_study_runner`, `test_study_tornado_lp`, `test_study_library`. Then delete `study/tariff.py` engine parts, `study/proforma.py`, `study/proforma_xlsx.py`, `_wrap_with_demand_charge`, `SolverConfig.demand_charge`, the `demand_charge_eur` term in `objective_decomposition.py` and GS's `validation_service` tariff checks; replace `study_library/` with the generic defaults pack; rename the vocabulary (§4 last rows); ledger rows carry the engine field path they compile to | `qa_decision_study.py` passes; ported golden tests pass; deltas recorded; PR merged; one tariff, one bill, one cash-flow path on master |
| **U3 Guided face in Guided mode** | GS (IC for expert-side hooks) | Decision study entry in Guided mode's Start card (question picker beside the hub templates); study chat tools (`start_decision_study`, `set_study_parameter`, `run_decision_study`, `get_study_findings`, `explain_verdict`, `generate_study_report`) with Guided confirmation tiers and `delegate()` steps; the key-parameter list view; "Open in Expert" lands on the Investment tab of the study's project with defaults flagged; expert edits mark ledger rows (rule 5). Drop the `PYPSAGUI_DECISION_STUDIES` flag once OPEN-ITEMS 1 (PR #71, per-context user time-series store) is merged | A novice can go from question to report by chatting; an expert sees and edits the same case |
| **U4 Data-centre question** | IC then GS | IC P5 WP5.1: `DataCentreLoadSpec` builder (phases, PUE, redundancy, critical/curtailable share, gensets, waste heat) and the CFE score, as engine content. GS: the guided question "How should the data centre be powered before and after grid connection?" on top, starting from the `eh_datacenter` template. That template is a 168 h week weighted 8760/168, which IC's annual check accepts; but a monthly-billed item (demand charge) sees one month and `RatingResult.annual` reads NaN, so the question needs an annual snapshot set (or IC confirms in U1 g how `annualise` covers it) | Data-centre question passes its QA driver in both faces |
| **Later** | IC engine, GS question | Remaining P5 archetypes, P6 realistic dispatch, P7 tax equity / scenarios / full report assembler. Each new engine capability reaches the guided face only as a key parameter when it moves the decision | |

## 6. Engine facade frozen for U2 (IC keeps these stable or notes changes here)

`commercial.billing.bill_site`, `commercial.billing.rate_meter`, `commercial.tariff_engine.rate`,
`results.billing.compute_billing`, `results.billing.compute_billing_preview`,
`commercial.lp_bindings.materialise_poc_prices`, `commercial.value_flow_templates.build`,
`results.finance_case.build_finance_case`, `finance.case.FinanceRefused`, `finance.engine.run_case`,
`finance.engine.solve_ppa`, `finance.export_xlsx.build_workbook`, `finance.packs.base.load_pack`,
`library.items.resolve` / `put_item`, `library.series_store.put_series`, the generic defaults pack
loader and the flat export series helper (new in U1), `commercial.cost_rows.commercial_cost_terms`, and the
`CommercialConfig` / `FinanceInputs` field names the ledger compiles to.

## 7. Coordination protocol

- Each session reads this file from `origin/claude/determined-tesla-np09ww` (later from master) and
  records its progress in its own plan file, not here.
- A session that must touch a file the other owns asks through the owner (or a cross-session message)
  first.
- Before opening a PR: merge the latest master, run the full backend suite, vitest and tsc, and the QA
  drivers of both efforts (`qa_investment_case.py`, `qa_value_flows.py`, `qa_decision_study.py`).
- No session merges to master; the owner does.

## 8. Owner decisions (taken 2026-10-05)

| # | Decision | Answer |
|---|---|---|
| 1 | Ship generic defaults in the Library | **Yes.** A versioned, sourced, `illustrative`-flagged generic defaults pack ships in-tree and is always listed in the report. This amends IC spec decision 20 for this pack only; tariffs and market data the user brings still go through the import schemas. U1 item (a) is unblocked. |
| 2 | Guided financial basis | **Pre-tax, real, excluding subsidies** as the guided default. "Include German taxes" (`eu_de` pack) is added later as one optional key parameter. |
| 3 | Guided question order | **Battery at site** first (exists), **data-centre power** before and after grid connection second (U4), then waste heat, hydrogen, off-grid. |
| 4 | Key parameters, battery question | **All four groups:** site zone, connection MW, load (upload or sector profile); tariff and PV on/off; battery storage cost and inverter cost; demand-charge price, energy price level, discount rate. Everything else from the defaults pack. |
| 5 | Guided start screen | **Both paths** side by side: "Is my site reliable?" (Energy Hub) and "Is this investment worth it?" (decision study). |

## 9. Independent review (2026-10-05)

An independent reviewer checked v1.1 against the code on all three refs and ran dry merges.
**Verdict: PASS WITH CONDITIONS; GO** once §4 carries the compile rules, §5 the full shared-file list,
and U2 the test port. All applied in v1.2:

| Finding | Severity | Applied in |
|---|---|---|
| R1 battery has no `overnight_cost`; IC ignores `capital_cost`; salvage rule has no IC equivalent | blocking | §4 C1, C2 |
| R2 no `network` or `export` item kind; export needs a Library series | blocking | §4 bill row, C3; U1 e |
| R3 168 h template passes the annual check; the real issue is monthly items | should-fix | U4; U1 g |
| R4 conflict and shared-file inventory incomplete (2 conflicts master+IC, 13 master+GS) | should-fix | §5 shared list; U1 |
| R5 missed duplication: `objective_decomposition` demand-charge term, `validation_service` tariff checks, `chat_tools` move | should-fix | §4 rows; §5; U1 f; U2 |
| R6 deleting GS engine modules loses about 80 golden tests | should-fix | U2 work and done-when |
| R7 tornado cost per bound and the WACC gate on rate bounds | should-fix | §4 C5 |
| R8 naming: `FinanceRefused` location, `NoCommercialConfigError` is frontend-only, IRR method | should-fix | §4, §6, U1 b |
| R9 FinanceInputs is nominal, no currency year | should-fix | §4 C4; U1 d |
| R10 vocabulary rename is a frontend contract change | nit | §4 last row; U2 |
| R11 forks and `_bind_commercial`, flag and auth | nit | §4 C6 |
| R12 (asset-parameterisation assessment) C1 as written sets `overnight_cost`, so PyPSA ignores `capital_cost` and under-costs the battery about 12.7 % | blocking | §4 C1 rewritten; §10 |

**Split of work after the review:** owners unchanged for every phase. The IC session's U1 grows by
items (d) to (g); the GS session's U2 grows by the compile rules, the test port, the removals in
`objective_decomposition.py` and `validation_service.py`, and the vocabulary rename. U0, U3 and the
order of U4 are unchanged.

## 10. One asset parameter schema (proposed, owner decision pending)

Source: `docs/superpowers/assessments/2026-10-05-uniform-asset-parameterisation.md`. The owner asked that
every asset (battery, line, electrolyser, generator, ...) is parameterised the same way in both modes,
with only the asset-specific variables differing. Today an asset can be costed on two incompatible bases
(an upfront `overnight_cost` or an annualised `capital_cost`), PyPSA silently prefers the first, and
which one applies depends on the entry path.

**Proposal.** One declarative schema per asset class, with fixed group order on every screen: Identity,
Size, Investment, Fixed O&M, Variable cost, Performance, Replacement/degradation, Provenance.
Investment is typed only as **overnight parts** (basis, overnight cost, lifetime, FOM share per part).
`capital_cost` is **derived in one function** and shown read-only. One accessor, `upfront_parts`, serves
the LP cost code, the finance engine and the Assumptions ledger. Composite assets (battery, later HVDC)
keep their parts in custom columns and leave PyPSA's `overnight_cost` empty.

| Step | What | Owner | When |
|---|---|---|---|
| S0 Schema core | `services/asset_schema/{schema,derive,access}.py`, custom part columns, a derive hook in component create/update, `upfront_parts` in `periodized_costs`, capex-budget fallback fixed, two-part battery in the golden fixture (single-part numbers unchanged) | **to decide** (no owner today; touches shared hot files) | Own owner-merged PR, **before U2's PR** |
| S0b Finance read | `finance_case._assets` reads `upfront_parts`; `cod_by_asset` defaults from `build_year` | IC session | In the U1 follow-up PR |
| S1 Expert mode | Asset editors, quick-add, columns and tooltips rendered from the schema; labels fixed | to decide | After S0, parallel to U2/U3 |
| S2 Guided mode | Assumptions ledger rows as a view of the schema's provenance | GS session | Inside U3 |
| S3 Seeds | Templates and Energy Hub placeholders derived from the generic defaults pack | to decide | After U1 a, before U4 |
| S4 Later | Store+Link batteries, HVDC, degradation in the LP | later | After U4 |
