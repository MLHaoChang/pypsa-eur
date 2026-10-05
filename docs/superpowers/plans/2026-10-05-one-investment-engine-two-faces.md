# Plan: one investment engine, two faces (expert workbench and guided study)

**Date:** 2026-10-05. **Status:** v1.1, written at the owner's request; owner decisions in §8 taken on 2026-10-05.
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
| `Bill.by_component` six keys (`findings.STREAMS` asserts them) | Mapped from `RatingResult` item kinds: energy, demand, capacity, fixed, network, export credit. The adapter owns the mapping and the assertion |
| `proforma.build_investment_case` (case route, centre, PV reference, every tornado bound) | `engine_adapter.case(n, cfg, ledger, owner)` → `build_finance_case` + `run_case` with `value_flow_templates.build("single_owner")`; returns GS's `InvestmentCase` / `CaseKpis` view shape so findings, report, charts and frontend keep working |
| `proforma_xlsx.write_proforma_xlsx`, `report_xlsx` sheet writers | `export_xlsx.build_workbook`; `report_xlsx` reuses its sheets |
| `Engine` literal `bill_calculator`, `cash_flow_expander` | `tariff_engine`, `finance_engine` |

**What the guided ledger must supply for a minimal single-owner case** (from IC's refusal codes):
`financial_close`, `analysis_years` (from the storage lifetime, as GS already does),
`cod_by_asset` for every owner asset (one COD), `escalation` per class with cashflows (0 in a real
basis), `wacc_nominal` consistent with the LP `discount_rate` (the WACC gate) and `inflation`, plus
`value_flows` from the `single_owner` template. Tax: `tax_pack_id` unset in the MVP-1 basis (pre-tax),
so post-tax figures read `not_established`, which the report already discloses.

**Known semantic differences to settle in tests, not to paper over:** IRR (bisection vs IC's
`metrics.irr`), salvage (GS: PV of remaining annuities; IC: `TerminalValueRule`), LCOS vs IC's LCOE,
real vs nominal handling. The GS QA driver `qa_decision_study.py` (57 checks) is the acceptance test:
same verdict class on its fixture; numeric deltas recorded with their cause.

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
  `services/chat_tools.py`, `chat_tools_schema.py`, `tool-error-kinds.json`, `services/project_context.py`,
  `models/schemas.py`, `main.py`, `layout/*`, `store/uiStore.ts`.
- **Master changes only through PRs the owner merges.** Merge order: U1 before U2 before U3.

| Phase | Owner | Work | Done when |
|---|---|---|---|
| **U0 Freeze** (now) | both | IC finishes the P4 gate; GS finishes F1. No new features, no master merges, no PRs | Both branch heads pushed and reported |
| **U1 Land the engine** | IC | Merge master into IC (one known conflict: `tool-error-kinds.json`); close the P4 gate; PR P3 + P4 to master. Add, in the same or a follow-up PR: (a) a **generic defaults pack** kind in `services/library` (versioned, hashed, `illustrative` flag) with a loader, seeded from GS's `study_library/` files; (b) a route + chat tool that **creates the commercial root** (`poc_link`, `export_link`, `timezone`) for a project, closing the `NoCommercialConfigError` gap in the expert UI; (c) a stable engine facade list (§6) frozen for U2 | PR merged; facade documented in the IC plan |
| **U2 Rewire the guided study** | GS | Merge master (with U1) into the GS branch. Add `study/compile.py` and `study/engine_adapter.py`; move every §4 call site onto them; delete `study/tariff.py` engine parts, `study/proforma.py`, `study/proforma_xlsx.py`, `_wrap_with_demand_charge`, `SolverConfig.demand_charge`; replace `study_library/` with the generic defaults pack; ledger rows carry the engine field path they compile to. Golden: `qa_decision_study.py` passes; the deltas of §4 recorded | PR merged; one tariff, one bill, one cash-flow path on master |
| **U3 Guided face in Guided mode** | GS (IC for expert-side hooks) | Decision study entry in Guided mode's Start card (question picker beside the hub templates); study chat tools (`start_decision_study`, `set_study_parameter`, `run_decision_study`, `get_study_findings`, `explain_verdict`, `generate_study_report`) with Guided confirmation tiers and `delegate()` steps; the key-parameter list view; "Open in Expert" lands on the Investment tab of the study's project with defaults flagged; expert edits mark ledger rows (rule 5). Drop the `PYPSAGUI_DECISION_STUDIES` flag once OPEN-ITEMS 1 (PR #71, per-context user time-series store) is merged | A novice can go from question to report by chatting; an expert sees and edits the same case |
| **U4 Data-centre question** | IC then GS | IC P5 WP5.1: `DataCentreLoadSpec` builder (phases, PUE, redundancy, critical/curtailable share, gensets, waste heat) and the CFE score, as engine content. GS: the guided question "How should the data centre be powered before and after grid connection?" on top, starting from the `eh_datacenter` template (needs an annual snapshot set or `annualise`, since IC refuses the 168 h week with `template_not_annual`) | Data-centre question passes its QA driver in both faces |
| **Later** | IC engine, GS question | Remaining P5 archetypes, P6 realistic dispatch, P7 tax equity / scenarios / full report assembler. Each new engine capability reaches the guided face only as a key parameter when it moves the decision | |

## 6. Engine facade frozen for U2 (IC keeps these stable or notes changes here)

`commercial.billing.bill_site`, `commercial.billing.rate_meter`, `commercial.tariff_engine.rate`,
`results.billing.compute_billing`, `results.billing.compute_billing_preview`,
`commercial.lp_bindings.materialise_poc_prices`, `commercial.value_flow_templates.build`,
`results.finance_case.build_finance_case` / `FinanceRefused`, `finance.engine.run_case`,
`finance.engine.solve_ppa`, `finance.export_xlsx.build_workbook`, `finance.packs.base.load_pack`,
`library.items.resolve` / `put_item`, the generic defaults pack loader (new in U1), and the
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
