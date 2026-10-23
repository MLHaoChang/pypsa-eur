# The Investment tab shows the campus equipment the case took

Status: done (#647, f3de776c)
Type: task
Blocked by: 01

Read [the spec](../spec.md) first. IC-owned frontend: `pypsa-gui/frontend/src/pages/results/investment/`
(`InvestmentCaseView.tsx`) and `pypsa-gui/frontend/src/api/finance.ts`. Findings Known limit 8: the extra assets
are in the report payload (`sections.project.payload.extra_assets`, built by
`services/finance/report.py::_extra_assets_block`) and the xlsx, not yet in the tab.

## What to build

- A block "Campus electrical equipment" in the Investment case view, shown only when the report's `extra_assets`
  is non-empty: one row per asset with name, kind, basis and quantity, overnight cost (the report's, so a capex
  scaling shows), build year and COD text, and the money line the report gives.
- Say **which campus result** the case took: the study method (`milp` → "joint optimisation (MILP)",
  `least_cost` → "least cost"), from the route's `campus` summary if issue 01 added it, else from the report if it
  carries it. If neither does, add `study_method` to the summary in issue 01 rather than guessing here.
- Say the costs are **illustrative placeholders** when the campus library tagged them `assumed` (every shipped
  cost is), in the tab's existing wording for illustrative defaults ("Generic default", "illustrative"; see
  "Investment language" in `pypsa-gui/CONTEXT.md`).
- When `GET /results/investment_case` reports the case stale with `"campus"` in `changed`, say the campus study
  changed since the case was built.
- When the run is refused with `campus_study_stale` or `campus_money_mismatch`, show the message and point to the
  campus electrical panel (it has its own navigation entry, `CampusElectrical`).

## Acceptance tests (vitest, write first)

1. A report with two extra assets renders both rows with their overnight cost and build year; none → no block.
2. The method line reads "joint optimisation (MILP)" or "least cost" from the payload.
3. The illustrative note appears for `assumed` costs.
4. A stale report with `"campus"` in `changed` shows the campus line.
5. Each refusal code shows its message.

## Files

- IC-owned, edited here: `src/pages/results/investment/*`, `src/api/finance.ts`, their tests.
- Campus-owned, untouched: `src/pages/Campus*`, `src/api/campusElectrical.ts`.

## Comments
