> **SUPERSEDED by `2026-09-28-guided-investment-study-mvp1-v2.md`.** Adversarial review v1 returned **NO-GO**
> (8 blockers, 11 should-fixes, 13 nits); the full review is `docs/superpowers/notes/2026-09-28-mvp1-plan-review-v1.md`.
> Kept for the revision history. Do not execute this version.

# Guided investment study — MVP-1 plan (the BESS question, end to end)

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development or superpowers:executing-plans. Implement phase by phase. Extend `pypsa-gui/backend/services/adequacy/` runners, `services/results/` surfaces and `services/solver/objective.py` over any new parallel stack. Every number this plan adds is the tenth economic surface and must reconcile with the nine that exist.
>
> **Status (2026-09-28):** drafted from the spec; **not yet adversarially reviewed**. Do not start S1 until the review is recorded under § Review deltas and the plan-level gate reads `GO` or `GO WITH BINDING CONDITIONS`.

**Goal.** A user with a load file and a tariff answers "Do I need a BESS at my site, and what is it worth?" inside the product: a verdict relative to the grid-only baseline, recommended MW and MWh, NPV, IRR and payback on a stated basis, a value-stream waterfall, a tornado over the five key drivers, and a DOCX report plus an XLSX pro forma whose every figure is bound to the model. No canvas required, and the canvas is one click away.

**Spec.** `docs/superpowers/specs/2026-09-28-guided-investment-study-design.md` (decisions 1 to 20, contracts §4, template §6 row 1, report §7, rules §8). Owner decisions confirmed 2026-09-28 are recorded in the spec's §11.

**Assessment.** `docs/superpowers/assessments/2026-09-28-investment-study-gap-analysis.md` (gaps G1, G2, G3, G4, G8, G10, G11 are the ones MVP-1 closes for one question).

**Already shipped (do not rebuild).**
- Solve path with extendability, vintages, representative weeks: `services/solver_service.py::run_simulation`, `services/solve_queue.py::SolveQueue`.
- Annuity, upfront cost, PV factor: `services/solver/periodized_costs.py` (`_annuity`, `upfront_cost_series`, `_pv_factor_series`, `periodized_capital_costs`).
- Reconciled cost surfaces under golden tests: `services/results/cost_breakdown.py::compute_cost_breakdown`, `services/results/asset_economics.py::compute_asset_economics`, `tests/golden/{fixture,oracle}.py`.
- Constraint injection through `extra_functionality`: `services/solver/objective.py::_wrap_with_capex_budget` is the template.
- Import-link selection: `services/adequacy/archetypes.py::select_import_links` (`eh_role`, `eh_poc`, carrier fallback).
- Campaign budget, abort, study mesh: `services/adequacy/campaign.py` (`start`, `check`, `record`, `end`), `services/study_state.py`, `routers/results.py::_abort_study`.
- Worker-thread study pattern with status polling and a results-state store: `services/adequacy/eh_study_runner.py::start_eh_study`.
- Scenario fork: `routers/projects.py::create_scenario` (`_create_scenario_db`).
- Report contract: `services/adequacy/study_report.py::build_study_report` (`_disclosures`, `_not_established`, `_evidence_gaps`), `services/adequacy/eh_report.py::assemble_reference_design_report`.
- `explain_investment` binding-constraint reasons and the zero-profit reading note: `services/chat_tools.py` (around lines 4250 and 4295).
- Guided-step chrome: `frontend/src/pages/modelHorizon/StepShell.tsx`, `HorizonSummary.tsx`, `modelHorizonModel.ts`; stage list: `frontend/src/pages/GridspinePanel.tsx`; chips and shared caveats: `frontend/src/pages/results/adequacy.tsx`.
- Document libraries already in the environment: `python-docx 1.2.0`, `openpyxl 3.1.5`, `Jinja2 3.1.6`. No PDF renderer is installed.

**Honest scope (MVP-1).** One question template (BESS at a grid-connected site). Perspective `site_owner`. Basis real, pre-tax, no subsidy. Tariff with energy bands, one billing-period demand charge, fixed charge, export price. Baseline is grid supply with existing assets. Options: best with BESS, best with BESS + PV (PV optional at intake), without. Tornado at quick fidelity over five ledger rows. Report to DOCX and HTML (printable to PDF from the browser); native PDF, PPTX, share link, scenario sets, ancillary markets, multi-party and post-tax are MVP-2/3. The copilot is not required for any deliverable.

**Tech stack.** Unchanged: FastAPI / PyPSA / linopy; React + TS; `pixi run gui-tests`, `pixi run gui-qa-drivers`.

**Dependency order.**

```
S0 pre-fixes (independent, small; may merge first)
S1 contracts + persistence
 ├─ S2 assumptions library + ledger
 ├─ S3 tariff + demand-charge constraint + bill calculator
 └─ S4 question pack + option runner  (needs S2, S3)
      ├─ S5 pro forma (tenth surface)  ─┐
      └─ S6 findings (verdict, streams, tornado, explain) ─┼─→ S7 report (DOCX/HTML/XLSX)
S8 frontend guided flow (starts after S1 on mocked payloads; wires to S4–S7 as they land)
S9 QA driver + findings note (last)
```

---

## Phase QA gate + TDD protocol (mandatory)

Every phase follows this loop. **Do not start phase N+1 until phase N's gate is cleared.**

1. **TDD, red:** write failing tests that encode the phase acceptance first and show the red error (for a live-solve test the environment cannot run, show a unit-level red and document the skip).
2. **TDD, green:** the minimum code to pass. No drive-by refactors; the router lifts already set the shape.
3. **Mutation check** on the property the phase exists for (demand-charge constraint binds; pro forma reconciles; a bare number in report prose is rejected): show that the intended tests, and only they, go red.
4. **Independent assessor gate:** `GO` | `GO WITH BINDING CONDITIONS` | `NO-GO` against acceptance and modelling honesty (basis labelled, null-not-zero, completeness set, no second engine).
5. **Proceed rule:** `GO` commit; `GO WITH BINDING CONDITIONS` satisfy in the same phase, re-verify, then proceed; `NO-GO` stop and re-gate.

**Global constraints.**
- No existing number changes. Golden tests, `test_fom_reconciliation.py`, `qa_asset_economics.py` and `qa_cost_decomp_overnight.py` must pass unchanged at every phase.
- A project with no attached `Tariff` solves byte-for-byte as before (S3 pins this with a test on the golden fixture).
- Every payload: `status: ok | not_established | skipped` per section, `basis` and `engine` on every figure, `honesty_notes` tuple, `null` plus a flag for an unresolvable figure (ADR-0001).
- Cite function names in commits and findings, not line numbers.
- Path-limited commits; one phase per pull request; `pixi run gui-tests` and `pixi run gui-qa-drivers` before each push.

---

## S0. Pre-fixes that mislead an investment reading (small, independent)

**Why first.** Both are visible in the expert view MVP-1 leaves one click away, and both are cheap.

**Files.**
- `frontend/src/layout/PropertiesPanel.tsx`: the `Capital cost` unit badge on Generator, StorageUnit and Link forms and read-outs (`unit="€/MW"` on the annuity field) becomes `€/MW/yr`; `Overnight cost` keeps `€/MW`. Store stays `€/MWh/yr` for capital and `€/MWh` for overnight.
- `frontend/src/layout/CreationForm.tsx`: `$/MWh`, `$/MW` become `€/MWh`, `€/MW`.
- New `backend/services/results/economics_caveats.py`: `ZERO_PROFIT_BY_CONSTRUCTION` as the one string, lifted from the reading note in `services/chat_tools.py` (the note there imports it; no second copy).
- `backend/services/results/asset_economics.py`: `compute_asset_economics` output gains `reading_notes: [ZERO_PROFIT_BY_CONSTRUCTION]` when any extendable asset sits at an interior optimum (reuse the interior test `explain_investment` already performs).
- `frontend/src/pages/results/Economics.tsx` and `pages/results/asset/AssetDetail.tsx`: render `reading_notes` beside Net profit, once, as a caveat chip with the shared string.
- `frontend/src/pages/results/MarginLoopPanel.tsx`: the literal `max_solves` placeholder renders the number.

**Acceptance.** Badge and currency tests in the existing panel test files; a backend test that an interior-optimum golden asset carries the note and a bound-hitting one does not; MarginLoopPanel test asserts a digit where the placeholder was.

**TDD evidence to record.** The red for the badge test (`expected "€/MW/yr"`), the red for `reading_notes` (`KeyError`), the red for the placeholder (`found "max_solves"`).

- [ ] Gate S0

## S1. Contracts and persistence

**Files.**
- New `backend/models/study.py`: `Study`, `DecisionQuestion`, `LedgerRow`, `AssumptionsLedger`, `Tariff`, `OptionSpec`, `OptionResult`, `InvestmentCase`, `Findings`, `DecisionReport`, `SectionState` (import from `models/energy_hub.py`; do not redefine), `StudyMaturity`, enums `Perspective`, `Basis`, `VerdictClass`, `Fidelity`. Field lists per spec §4; pydantic v2 like `models/energy_hub.py`.
- New `backend/services/study/__init__.py`, `services/study/store.py`: a study is a sidecar `study.json` in the base project's storage directory (same discipline as `adequacy_worksheet.json`: listed in `_BUNDLE_FILES` in `routers/projects.py` so bundles and snapshots carry it; atomic write via `services/atomic_io.py`).
- New `backend/routers/studies.py`: `POST /api/studies` (base project, question_id, intake answers), `GET /api/studies/{id}`, `PATCH /api/studies/{id}` (intake and settings, per-step apply), `DELETE`. Mounted under `/api/projects`-style access: use `ProjectAccessDep` from `routers/deps.py` and the project lock check the worksheet router uses (`_check_project_lock`). Register in `main.py`.
- `backend/services/chat_tools_schema.py`: no new tools in MVP-1.

**Steps.** Models → store round-trip → router → bundle inclusion.

**Acceptance.** Round-trip test for every model with `null` figures preserved; router test for create, read, patch, delete, 403 for a non-member, 409 for a foreign lock; bundle export includes `study.json`; a snapshot restore brings it back.

**TDD evidence.** `ModuleNotFoundError: models.study` → green; `test_bundle_includes_study` red on the missing file name.

- [ ] Gate S1

## S2. Assumptions library and ledger

**Files.**
- New `backend/study_library/technology_costs.csv` with columns `technology, parameter, value, unit, basis, source, source_year, source_url, range_low, range_high`. Seed: a curated subset (battery inverter EUR/kW, battery storage EUR/kWh, utility and rooftop PV EUR/kW, FOM shares, lifetimes, round-trip efficiency, degradation placeholder rows marked `not_used_in_mvp1`) transcribed from the PyPSA technology-data release the repo's `rules/retrieve.smk::retrieve_cost_data` pins, each row citing that release and year. The runtime does not download; the seed is vendored and versioned (`library_version`).
- New `backend/study_library/tariffs.csv`: two seed tariffs (a German-style industrial tariff with a capacity-based network charge, and a flat time-of-use reference), both marked `illustrative` in `source`.
- New `backend/study_library/finance_defaults.yaml`: discount rate (real), lifetime, horizon years, basis, perspective, with sources.
- New `backend/services/study/library.py`: `load_library(version) -> Library`, `seed_ledger(question, intake, library) -> AssumptionsLedger` (every row `provenance=library`, `status=default`, `sensitivity_flag` from the question template's `key_drivers`).
- New `backend/services/study/ledger.py`: `apply_user_row`, `diff_against_defaults`, `key_driver_rows`, `maturity_from_ledger` (screening when any key driver is `default` and load is `synthetic`; feasibility when all key drivers are `customised` or `measured`).
- `routers/studies.py`: `GET/PUT /api/studies/{id}/ledger`, `GET /api/studies/{id}/ledger.csv`, `POST /api/studies/{id}/ledger/import`.

**Acceptance.** A seeded BESS ledger has every key-driver row with a source and a year; a user edit flips `provenance` and `status` and is preserved across a re-seed; maturity moves from `screening` to `feasibility` when the five key rows are customised; CSV round-trip is lossless; an import row with no unit is refused with the row named.

**TDD evidence.** Red: `seed_ledger` missing; red: maturity stays `screening` after edits (before the predicate exists).

- [ ] Gate S2

## S3. Tariff, demand-charge constraint, bill calculator

**Files.**
- `backend/models/study.py::Tariff` (from S1).
- New `backend/services/study/tariff.py`:
  - `apply_tariff(n, tariff, import_links) -> revert` : writes the energy-band price as a `marginal_cost` series on the selected import Link(s) (selection via `services/adequacy/archetypes.py::select_import_links`), and the export price as a negative-cost series on the export direction (a second Link, created by the pack in S4 with `eh_role=grid_export`); returns a revert closure like `with_periodized_cost_defaults`.
  - `BillCalculator.bill(import_mw: Series, export_mw: Series, tariff, weightings) -> Bill` with `annual_bill`, `by_component` (energy, demand, fixed, export credit, network), `by_period`, `peak_kw_by_billing_period`, `basis`, `engine="bill_calculator"`.
- `backend/services/solver/objective.py`: new `_wrap_with_demand_charge(network, user_fn, cfg, log_queue)` composed after `_wrap_with_capex_budget`: for each billing period `p` adds a linopy variable `peak_import[p] ≥ 0`, constraints `Link-p[import, t] ≤ peak_import[p]` for `t ∈ p`, and objective term `price_per_mw_per_period × peak_import[p]`. Active only when `cfg.tariff` is set (a new optional `SolverConfig` field carrying the serialised `Tariff` and the import link names). Emits one `[TARIFF]` log line naming the links and the billing periods.
- `backend/services/solver_service.py`: `SolverConfig.tariff: dict | None = None`; `run_simulation` applies `apply_tariff` inside the same revert discipline as periodized defaults.
- `backend/services/results/objective_decomposition.py`: the demand-charge term is reported as its own line so the objective still bridges to `cost_breakdown.total` plus `demand_charge_eur`.

**Acceptance.**
- `test_tariff_demand_charge.py`: on a two-day toy network with a BESS, the solved peak import is lower with the demand charge than without, `peak_import[p]` equals the max of the import series in each billing period (within solver tolerance), and the objective difference equals `price × Σ peak` plus the energy-cost delta.
- `test_tariff_absent_is_noop.py`: the golden fixture solved with `cfg.tariff=None` produces identical `n.objective`, `generators_t.p` and `links_t.p0` to a solve on the commit before this phase (pin by hash of the frames).
- `BillCalculator` unit tests: a flat band, a two-band time-of-use, a demand charge on monthly peaks, export credit; each component nulls with a flag when the series is absent.
- Objective decomposition test: `residual_gap_pct` stays within its existing tolerance with the tariff term present.

**Mutation.** Drop the `≤ peak_import` constraint: the "peak equals max import" test must go red and nothing else.

**TDD evidence.** Red: `AttributeError: SolverConfig has no attribute tariff`; red on the binding test before the wrapper exists (peak unchanged).

- [ ] Gate S3

## S4. Question pack and option runner

**Files.**
- New `backend/services/study/questions.py`: `BESS_AT_SITE: DecisionQuestion` per spec §6 row 1 (mandatory inputs: site and zone, tariff, load, connection limit; options `bess`, `bess_pv`, `none`; headline metrics `npv, payback, sizes`; value streams `demand_charge_reduction, energy_shift, export, resilience(skipped in MVP-1)`; key drivers `battery_capex_per_kwh, demand_charge_price, energy_spread, discount_rate, degradation(not_used)`).
- New `backend/services/study/packs.py`: `build_site_network(intake, ledger) -> pypsa.Network`: one AC bus `site`, a grid bus, an import Link `grid_import` (`eh_role=grid_import`, `p_nom` = connection limit) and an export Link `grid_export`, the Load from the upload or a sector profile (through the existing `profile_shapes.py` and `timeseries_qa.py`), optional PV Generator with `p_max_pu` from the library or upload, and the BESS as the pair the vocabulary map describes: a `Store` (energy, `e_nom_extendable`, `capital_cost` from EUR/kWh) with a charge/discharge `Link` pair (power, `p_nom_extendable`, `capital_cost` from EUR/kW, `efficiency` split as `sqrt(rte)`). Snapshots: one representative year at hourly resolution, or representative weeks for `quick_screen` through `services/time_aggregation_service.py`. Every cost written from a ledger row, never a literal.
- New `backend/services/study/runner.py`: `start_study_run(study_id, fidelity)`: a `campaign.start(objective=question.title, budget_solves=...)`; forks one Project per option via the scenario service (`routers/projects.py::_create_scenario_db` lifted to `services/study/forks.py` if it is router-bound); sets extendability per option (`none`: BESS `e_nom_extendable=False`, `e_nom=0`; `bess`: free; `bess_pv`: PV free too); enqueues on `SolveQueue` with `enqueue_unique`; on completion reads `compute_cost_breakdown`, `compute_asset_economics`, `BillCalculator` per option; records `OptionResult`; abort at the next queue boundary; restore is by construction (the base project is never mutated). Status in the results-state store like `eh_study`; mesh through `study_state`.
- `routers/studies.py`: `POST /api/studies/{id}/run {fidelity}`, `GET /api/studies/{id}/run`, `POST /api/studies/{id}/run/abort`; 409 through `_refuse_if_mesh_busy`.

**Acceptance.**
- Pack test: every cost on the built network equals its ledger row; the import Link is found by `select_import_links`; the BESS pair's round-trip efficiency reproduces the ledger's RTE.
- Runner test with the fake solver used by `test_adequacy_campaign.py`: three forks, three solves charged to the campaign, `none` has zero storage, `bess` has `e_nom_opt > 0` on the toy where the demand charge makes it worthwhile, abort after the first solve leaves the base project untouched and `completeness.options = not_established` for the rest.
- Budget test: `budget_solves=2` with three options refuses before the first solve with `CampaignBudgetError`, naming the shortfall.

**TDD evidence.** Red: `BESS_AT_SITE` missing; red: `start_study_run` charges zero solves (before `campaign.record` is wired).

- [ ] Gate S4

## S5. The pro forma (tenth economic surface)

**Files.**
- New `backend/services/study/proforma.py`: `build_investment_case(n_option, n_baseline, cfg, ledger, bills, option) -> InvestmentCase`:
  - CAPEX in the build year from `upfront_cost_series` (or `capital_cost` un-annuitised with `_annuity` and labelled `derived_from_annuity`);
  - fixed O&M and variable OPEX from `compute_cost_breakdown` and `compute_asset_economics` per period expanded to calendar years with `active_period_years`;
  - `bill_baseline`, `bill_option`, `savings` from `BillCalculator`;
  - `market_revenue_at_duals` from `asset_economics.revenue_eur`, labelled `engine=lp_duals`, excluded from `net_cash_flow` when a bill is present (decision 10: the bill already prices the same energy);
  - discount at the ledger's real rate; `npv`, `irr` by bisection (null with `irr_undefined` when no sign change), simple and discounted payback by first crossing, `lcos` on discounted energy, `capex_total`, salvage as un-depreciated straight-line share at horizon end, reported in `years[-1]` and in `kpis.salvage_eur`;
  - `completeness` per block, `honesty_notes` including `basis_real_pre_tax_no_subsidy`, `single_year_extrapolated` when the solve covered one representative year, `perfect_foresight_dispatch`.
- `backend/tests/golden/oracle.py`: `npv(cash_flows, rate)`, `irr(cash_flows)`, `payback(cash_flows)`; `tests/golden/fixture.py`: a flat tariff attached to the golden network (a second install, the original stays untouched).
- New `backend/tests/test_proforma_golden.py`: the case built from the golden fixture reconciles: `Σ years.capex` equals the oracle's upfront total; `Σ years.opex_fixed` over the horizon equals `cost_breakdown.fom + capex` on the annual basis times years; NPV equals the oracle's NPV of the same vector.
- `backend/services/study/proforma_xlsx.py`: `write_proforma_xlsx(case, ledger) -> bytes` with openpyxl: sheet `Cash flows` with year rows and Excel formulas for discount factor, discounted cash flow, cumulative and NPV (`=NPV(...)`), sheet `KPIs`, sheet `Assumptions` (the ledger), sheet `Provenance` (engines, basis, fidelity, hashes).
- `routers/studies.py`: `GET /api/studies/{id}/options/{option_id}/case`, `.../case.xlsx`.

**Acceptance.** Golden reconciliation as above; XLSX opens with openpyxl and the `NPV` formula cell recomputed by a pure-Python evaluation equals `kpis.npv` within 1 EUR; an option with no bill carries `savings: null` with `savings_available=false`; `irr` null case tested; the basis toggle absent in MVP-1 still writes `basis` on every figure.

**Mutation.** Multiply upfront CAPEX by the annuity twice: the golden reconciliation goes red.

**TDD evidence.** Red: `test_proforma_golden` on `ImportError`; red: NPV mismatch before discounting is wired.

- [ ] Gate S5

## S6. Findings: verdict, value streams, tornado, explain

**Files.**
- New `backend/services/study/findings.py`:
  - `value_streams(case_option, case_baseline, bills)`: demand-charge reduction, energy time-shift and export credit from `Bill.by_component` deltas; each `engine=bill_calculator`; the sum equals `savings` (test).
  - `verdict(cases, tornado)`: `recommended` when the best option's NPV > 0 and stays > 0 across the tornado's low/high bounds; `marginal` when the sign flips inside the bounds; `not_recommended` otherwise. One sentence built from a fixed template with fact IDs (`{{npv}}`, `{{payback}}`, `{{sizes}}`, `{{top_driver}}`).
  - `tornado(study, rows, fidelity=quick_screen)`: for each `sensitivity_flag` row, two re-solves of the best option at `range_low` and `range_high` (through the runner, charged to the campaign; budget check first), NPV at each, sorted by swing. MVP-1 does re-solves, not re-dispatch; the record says `method=resolve`.
  - `explain(option)`: `explain_investment` lifted into `services/study/explain.py` as a pure function over `(n, component_class, name)` that `chat_tools.explain_investment` now calls (one implementation).
  - `assemble_findings(study) -> Findings` with completeness per block and honesty notes.
- `routers/studies.py`: `POST /api/studies/{id}/findings/tornado` (charged), `GET /api/studies/{id}/findings`.

**Acceptance.** Streams sum to savings on the toy; verdict classes on three constructed cases (always positive, sign flip inside range, always negative); tornado on two rows charges four solves and sorts by swing; abort mid-tornado leaves computed bars and `tornado.status=not_established` with the remaining rows named; `chat_tools.explain_investment` output is byte-identical before and after the lift (pin with a recorded fixture).

**TDD evidence.** Red: streams do not sum (before the export credit sign is fixed, expected); red: `marginal` never emitted.

- [ ] Gate S6

## S7. The report: DOCX, HTML, XLSX

**Files.**
- New `backend/services/study/report.py`: `assemble_decision_report(study) -> DecisionReport`. Reuses `study_report._disclosures`, `_not_established` and `_evidence_gaps` by lifting them to accept a generic section list (small refactor, `build_study_report` keeps its output byte-identical, pinned). Sections per spec §7. `facts` map: every number in the report is a `fact_id` with value, unit, basis, engine, fidelity. `prose` paragraphs are template strings with `{{fact_id}}` references only; no AI paragraphs in MVP-1 (the model has the field, the assembler leaves it empty).
- New `backend/services/study/render_html.py` (Jinja2 template under `backend/templates/decision_report.html.j2`, printable stylesheet, charts as inline SVG generated server-side with matplotlib `Agg` from the same data the frontend charts use) and `render_docx.py` (python-docx: headings, tables, the SVG charts rasterised to PNG, an "Assumptions" table from the ledger, a "Provenance" appendix with engines, fidelity, library version, model hash).
- Renderer guard: `validate_prose(report)` rejects any paragraph containing a digit outside a `{{fact_id}}` reference or an unresolved reference, naming the section and paragraph. Stale flag: `report.stale=true` when the study's ledger hash or any option project's network hash differs from the ones recorded at findings time.
- `routers/studies.py`: `POST /api/studies/{id}/report` (assemble), `GET /api/studies/{id}/report` (JSON), `.../report.html`, `.../report.docx`, `.../report.xlsx` (pro forma plus ledger), with the safe-filename helpers from `services/http_filenames.py`.

**Acceptance.** A report from the S4 toy renders to HTML and DOCX with every KPI on the verdict page equal to the findings payload; `validate_prose` rejects a paragraph with a hand-typed "4.1 M"; disclosures render before the first number; a ledger edit after assembly sets `stale`; DOCX opens with python-docx and contains the Assumptions table with the edited row marked.

**Mutation.** Remove the digit check: the hand-typed-number test goes red.

**TDD evidence.** Red on `validate_prose` accepting a bare number; red on `stale` never set.

- [ ] Gate S7

## S8. Frontend: the guided flow

**Files.**
- `frontend/src/api/studies.ts`: client and types mirroring `models/study.py` (same discipline as the EH types in `api/simulation.ts`).
- `frontend/src/pages/modelHorizon/StepShell.tsx`: generalise the step id from `HorizonStepId` to a type parameter with `labels` passed in; Model Horizon passes `STEP_LABELS`; its tests stay green unchanged.
- `frontend/src/layout/NewProjectWizard.tsx`: the first screen becomes question cards (BESS question, custom question, blank model, template, import, clone, planning → dynamics study). The existing tabs stay reachable.
- New `frontend/src/pages/study/`:
  - `StudyHub.tsx` (task list with section status chips and the maturity badge; entry state from the study record: intake incomplete opens the intake, else the hub, the Model Horizon rule),
  - `Intake.tsx` (StepShell: site and zone → what exists → consumption upload or sector profile → goal → horizon and perspective → check your answers; per-step PATCH),
  - `Options.tsx`, `TariffStep.tsx` (with the baseline bill preview from `BillCalculator` on the load alone), `FinanceStep.tsx`, `LedgerReview.tsx` (key rows first, defaults visually distinct),
  - `RunStep.tsx` (fidelity choice, budget sentence, stage list from `GridspinePanel`'s pattern, abort),
  - `Verdict.tsx` (chip, sentence, three KPIs vs baseline, top drivers, maturity, main caveat), `WhyHow.tsx` (waterfall, cumulative cash-flow with payback marker, categorized option table, typical week from the existing Dispatch chart component), `Robust.tsx` (tornado, run button charged), `ReportStep.tsx` (assemble, stale banner, download DOCX/HTML/XLSX).
  - `studyModel.ts`: pure predicates (section status, entry state, maturity label, verdict tone), unit-tested without rendering.
- `frontend/src/App.tsx`: a `study` SlidePanel in `FULL_SCREEN_TABS` with `PANEL_META { eyebrow: 'STUDY', title }`; the Expert view button opens the option project in the workbench.
- `frontend/src/layout/AppHeader.tsx`: when the active project belongs to a study and a study run completes, open the Verdict and toast the verdict class (today nothing opens).
- Vocabulary map `frontend/src/utils/studyVocabulary.ts` per spec §8.1, the only source of novice labels; every study field renders `label`, unit, and `technical_name` as subtitle.

**Acceptance (render tests, mandatory as in the Model Horizon design).** Intake opens on an incomplete study and the hub on a complete one; a ledger row edited in the UI shows `customised`; the verdict page renders the three KPIs from a fixture payload with basis labels; the tornado bars sort by swing; the report step shows the stale banner from `report.stale`; the Expert view link targets the option project; the Model Horizon tests pass unchanged after the StepShell generalisation.

**TDD evidence.** Red: `StepShell` rejects a non-horizon id; red: verdict page renders `0` for a null KPI (must render "not established").

- [ ] Gate S8

## S9. QA driver, integration, findings note

**Files.**
- New `backend/tests/qa_decision_study.py` (discovered by `tests/run_qa_drivers.py`): over HTTP, create a study from a load CSV and the seed tariff, seed the ledger, edit two rows, run at `quick_screen`, assert three options solved and charged, the case reconciles to `cost_breakdown` for the best option, findings carry a verdict, the tornado runs on two rows, the report renders to DOCX and XLSX, `stale` flips after a ledger edit, and abort mid-run restores nothing because nothing was mutated.
- `docs/superpowers/findings/2026-09-28-guided-investment-study-mvp1.md`: status line, what changed, before/after counts (`gui-tests`, `gui-qa-drivers`), the golden reconciliation numbers, "Still open / deliberately not done".

**Acceptance.** Driver passes on the pinned PyPSA (1.1.2) and on the container's; failing set of the full suite unchanged versus master; `qa_asset_economics.py` and `qa_cost_decomp_overnight.py` unchanged.

- [ ] Gate S9 (plan-level definition of done)

---

## Review deltas

*(to be filled by the adversarial plan review before S1 starts; tag findings `[B*]` blocker, `[S*]` should-fix, `[N*]` nit, and reproduce each before applying)*

## Deferred (explicitly, to MVP-2/3)

Native PDF (needs a renderer the environment does not have; HTML print is the MVP-1 path), PPTX, share link, AI-drafted paragraphs with labels and review, post-tax and nominal bases, replacements and degradation rows in the cash flow, price and weather scenario sets and the dotplot, ancillary and capacity revenue, resilience value from the sequential MC, multi-party perspective, the data-centre, hydrogen, co-located and off-grid templates, break-even bisection and the option map, scenario input diffs, tariff library import from OpenEI, the container-tenancy fix for `services/user_timeseries.py` (its own plan; required before a multi-user deployment of studies).

## Definition of done

MVP-1 is done when a QA driver takes a load file and a seed tariff to a DOCX report and an XLSX pro forma over HTTP with no canvas interaction, the pro forma reconciles to the golden oracle, a project without a tariff solves byte-identically to master, the nine existing economic surfaces and their tests are unchanged, every phase gate reads `GO`, and the findings note records the counts.
