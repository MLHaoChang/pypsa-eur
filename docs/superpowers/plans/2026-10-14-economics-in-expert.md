# Plan: Economics in Expert

**Date:** 2026-10-14. **Status:** file split agreed with IC (14 Oct); E0 is IC's, E1 is built. **Owner decision:** the owner picked this slice
on 14 Oct (relayed by the coordinator), after S1 (Expert asset cards from the asset schema) was finished.
**Source:** the umbrella plan `2026-10-05-one-investment-engine-two-faces.md` §11, row "Economics in Expert: M4
cost-mode control becomes the S1 asset cards of §10; S2 Economics section hosting IC's editors"; the UX
assessment's task-ordered sidebar (Build, Time, **Economics**, Run, Results, Reports).

## 1. What is wrong today

A modeller cannot see or set a project's economics in one place:

| Input | Where it is edited now | Stored in |
|---|---|---|
| Discount rate, inflation | Solver Settings panel (`SolverSettings.tsx:1450`) | `SolverConfig.discount_rate`, `inflation_rate` |
| Money year, price basis (nominal/real) | `FinanceInputsEditor`, Results → Investment tab | `/simulation/finance` (`FinanceInputs`) |
| Site connection (`poc_link`, `export_link`, `timezone`) | `SiteConnectionForm`, Results → Investment tab only | `SolverConfig.commercial` |
| Tariffs, contracts, participants, library | IC's editors, Results → Investment tab | `SolverConfig.commercial`, the library |
| Each asset's investment (overnight parts, lifetime, FOM) | One asset card at a time (S1) | the asset's columns, `upfront_parts` |
| Investment periods, period weights | Model horizon panel | `SolverConfig` |

So the inputs that decide an investment case are spread across the Results page (shown after a solve, though they are
inputs) and Solver Settings. The commercial root, which every finance route needs (`no_commercial_config` 409), is
reachable only from a results tab. No view lists what every asset costs.

## 2. Target

A sidebar step **Economics** between Time and Run, opening one panel `economics` with four tabs. Inputs only:
results (the investment case, value flows, the bill) stay on Results → Investment.

| Tab | Content | Built from |
|---|---|---|
| **Basis** | Discount rate and inflation (SolverConfig); money year and price basis (FinanceInputs); the site connection | a small form on `simulationApi` for the two rates; IC's `FinanceInputsEditor` and `SiteConnectionSection` (the wrapper of `SiteConnectionForm`), embedded |
| **Asset costs** | Every priced asset: class, name, each investment part (overnight, lifetime, FOM share, basis unit), the derived annual capital cost, provenance chip. Read-only: a row opens the asset's card in Properties to edit, so there is one editor per field | new route `GET /network/asset_costs` (all assets' `upfront_parts` + provenance; not under `/asset_schema/`, where `{component}` would capture it); new `AssetCostsTable` |
| **Tariffs & contracts** | Tariff, contracts, participants | IC's `TariffSection`, `ContractsEditor`, `ParticipantsDesigner`, embedded |
| **Library** | Tariff and series library | IC's `LibraryBrowser`, embedded |

Rules:
- **Embed, don't rewrite.** IC's editors already read the project from the store and take no props, except
  `TariffBuilder` and `SiteConnectionForm`, which `InvestmentTab`'s `TariffSection` and `SiteConnectionSection`
  wrap. They are mounted as they are.
- **One editor per field.** The discount rate keeps its Solver Settings field (it is also a solver input), with the
  same query key, so both show the same value. Asset investment is edited only on the asset card.
- **Guided mode** hides the step, as it hides the other Expert steps.
- Labels follow `pypsa-gui/CONTEXT.md` ("Overnight cost", "Capital cost (annualised)", never CAPEX).

## 3. File split (to agree with IC)

| Files | Owner | Change |
|---|---|---|
| `layout/sidebar/steps.tsx`, new `layout/sidebar/EconomicsSection.tsx` | coordinating session (me) | the step and its rows |
| `store/uiStore.ts` (`SlidePanel` union), `App.tsx` (`PANEL_META`, `fullPageContent`) | me (shared hot files, additive) | the `economics` panel id |
| new `pages/economics/EconomicsPanel.tsx`, `pages/economics/AssetCostsTable.tsx`, `pages/economics/RatesForm.tsx` | me | the panel shell, the asset table, the rates form |
| `backend/routers/network.py` (one route), `backend/services/asset_schema/access.py` (`investment_rows(n)`) | me | the network-wide parts listing |
| `pages/results/InvestmentTab.tsx` → new `pages/results/investment/TariffSection.tsx` and `SiteConnectionSection.tsx` | **IC** | move the two wrappers into their own exported files, no behaviour change (one small PR, first) |
| `pages/results/SiteConnectionForm.tsx`, `pages/results/investment/*` | **IC** | none in this slice; I import them |
| `pages/results/InvestmentTab.tsx` (inputs) | **IC**, later | optional follow-up: keep the outputs and link "Edit in Economics" for the inputs. Nothing removed in this slice |

## 4. Slices

- **E0 (IC, small):** `TariffSection` and `SiteConnectionSection` moved out of `InvestmentTab.tsx` and exported.
- **E1 (me, in parallel with E0):** the backend route and its tests; the `economics` panel with the Asset costs tab
  and the Basis tab's rates form and `FinanceInputsEditor`; the sidebar step.
- **E2 (me, after E0 lands):** the site connection on the Basis tab; the Tariffs & contracts and Library tabs.
- **E3 (IC, optional):** the Investment tab keeps outputs and links to Economics for inputs.

## 5. Tests (gates)

- Route: every priced asset in the golden network is listed once per part, with the numbers `upfront_parts` gives
  (a two-part battery, a per-km line, a single-part generator); an unpriced asset is listed with no parts; a legacy
  `capital_cost`-only asset is flagged `derived_from_capital_cost`.
- Panel: the step opens the panel; each tab renders its embedded editor (mocked APIs); a row of the asset table
  selects that component; the rates form writes `discount_rate` through the same query key Solver Settings reads.
- Guided mode: no Economics step.
- Control matrix and assistant inventory checks pass; the release-note fragment is present.

## 6. Open points

- Should the Investment tab keep its own copies of the input editors once Economics exists (E3)? IC decides. Two
  mounts of the same self-contained editor are harmless meanwhile: both read and write the same queries.
- The chat: `ui_open_panel` gains `economics` through `SAFETY_PANEL_ENUM` in E1 so the assistant can open it.

## 7. E1 as built (14 Oct)

- `GET /network/asset_costs` (`services/asset_schema/access.asset_cost_rows`): one row per asset of the six
  investable classes, Load excluded. Parts are `upfront_parts`', per unit of the sizing variable (`basis`: EUR/MW,
  EUR/MWh for a Store, EUR/MVA for branches). A bare `capital_cost` is back-calculated at the global rate and
  flagged. Each row carries the asset's provenance, so the table needs one request. No inf in the payload: a
  lifetime that never ends is null. Solver scaffolding (vintage clones, VOLL slacks) is filtered out.
- The panel shows money per kW or kWh, as the cards do; branch money stays per MVA. The capital cost is the stored
  annual column; the solve re-derives it at the rate in force.
- The rates form sends only `discount_rate` and `inflation_rate` (the route merges a partial config) and shares the
  `solverConfig` query with Solver Settings.
- The assistant: `ui_open_panel` takes `economics` / `Economics`.
