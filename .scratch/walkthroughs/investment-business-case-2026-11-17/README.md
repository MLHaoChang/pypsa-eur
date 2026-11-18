<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Walkthrough: Investment and business case, 17 Nov 2026 (WALK-E2E-1)

Master `9491878d`. Scope: finance and business-case results (NPV, IRR, LCOE, lease-up, cost units, money year, workbook export) for the campus (the "Data Center Energy Hub", a 50 MW hyperscale campus), the data-centre staged variant and the renewables IPP with battery, from inputs to the investment decision.
Owner decisions applied: WALK-E2E-1, HOLISTIC-1, EXPERT-GUIDED-1, RAW-NAMES-1, DECISION-GAPS-1, KEY-LATER-1, DENSITY-1, THEME-LIGHT-1. The rows are read from the brief; they land on master with the coordinator's docs PR.
Method: Playwright (Chromium 1360×860, light and dark) and the HTTP API against the running app as the dev admin. Every figure below was recomputed by hand from the report's own inputs unless it says otherwise.

**Not walked.** The chat dock (no `ANTHROPIC_API_KEY`, KEY-LATER-1). That includes the Guided question "Is this plant worth building?" (`plant_business_case`), which the business-case spec §7 puts in chat only. The campus library assets and the Industrial hydrogen, ammonia, concentrator and microgrid business-case presets (other groups' templates). Light/dark: the business-case view was read in dark; every other screen in light.

**Already filed by others, cited not re-filed.**
- Guided "Certified" against Expert "not run yet" after a study: `.scratch/ux-walkthrough-2026-11/issues/01-guided-expert-result-mismatch.md`. Seen again here: Expert › Results › Reliability says "No reliability result yet" after the Guided study ran.
- IPP has no Guided run or results (dead end): ux-walkthrough 02 and `.scratch/template-e2e/issues/27-ipp-no-guided-run-or-results.md`. In flight; not repeated.
- Investment and Improve have no manual path in Guided: `.scratch/template-e2e/issues/22-improve-and-investment-no-manual-path.md`, on branch `claude/template-e2e-decide-report`. Issue 02 here extends it with the walls a manual path meets.
- Raw names in the Expert header and chat cards: ux-walkthrough 06. Issue 06 here lists different strings.

## Journey map

| Goal | Guided | Expert | What the walk found |
|---|---|---|---|
| G1 Get a project to price | Home › New project › From template › Data Center Energy Hub › form (IT MW, PUE, backup, architecture) › Create | same | Form works; its IT spec never reaches the business case (issue 01). |
| G2 State finance inputs | Section 7 › "State the finance inputs" (a form with no values, "not stated") | Commercial & parties › Economics | DC ships none; the IPP ships a full set. Section 7 reads "complete" while empty (issue 02). |
| G3 Set the commercial connection | no control | Connection & grid › Site connection › "Save site connection" | Guided shows a green "Connection point" that is a different thing (issue 02). |
| G4 Run the dispatch | Section 9 › Run study (reliability study) | Run LOPF | Two different runs; Guided cost €39.8 M = Expert total system cost €39.8 M (agree). |
| G5 Investment case (NPV, IRR, LCOE) | Section 10 › Investment tile › Open the detail | Results › Commercial & finance › Investment case | DC: raw refusal then a chain of walls (issue 02). IPP: completes, numbers correct. |
| G6 Business case (project on its own) | chat only (not walkable) | Results › Commercial & finance › Business case › preset › prices › Run | DC preset cannot run (01); KPIs "not established" with the wrong or raw cause (03). |
| G7 Export | Section 10 › Download report (.docx) | Results › Hand-off; `/results/workbook.xlsx` | Workbook is right where it carries the case; default export omits it (Improvements 4). |

## What meets the requirements

Hand calculations that matched the app.

| Check | Hand result | App |
|---|---|---|
| IPP project NPV at 7 % WACC: net cash lines per year (2028 close, 2028–2054), discounted | 29 333 318 EUR | `npv_at_wacc` 29 333 318 (to the euro) |
| IPP IRR against WACC | post-tax 9.19 % > 7 % WACC, NPV > 0: consistent | `project_irr_post_tax` 0.0919 |
| IPP land lease 2030: (60 + 41.667) MW × 3000 | 305 000 | 305 000 |
| IPP capacity payment 2030: 32.2405 MW × 1000 × 40 | 1 289 622 | 1 289 622 (but see issue 04) |
| IPP insurance 2030: 0.5 % × 130 M | 650 000 | 650 000 |
| DC lease 2027: 432 000 kW-month × 170 × 1.02 | 74 908 800 | 74 908 800 |
| DC pass-through 2027: 250 000 MWh × 95 × 1.02 | 24 225 000 | 24 225 000 |
| DC staff 2027: 432 000 × 12 × 1.02 | 5 287 680 | 5 287 680 |
| DC maintenance 2027: 1.5 % × capex (36 MW × 9.469 M EUR, money year 2025, escalated 2 % for 2 years to the 2027 close) | 5 319 836 | 5 319 836 (money-year handling correct) |
| DC EBITDA margin gross 2027: 36.039 M / 99.181 M | 36.34 % | 36.34 % |
| DC EBITDA margin net of pass-through: (36.039 − 24.225) M over (99.181 − 24.225) M | 15.76 % | 15.76 % |
| DC "Who pays whom": site paid 34 700 616 = market 26 996 556 + operator 5 874 767 + capex supplier 1 829 293 | 34 700 616 | equals the solve objective 34 700 616 |

Behaviour that meets the spec: business case on its own hash and basis label ("the project on its own … never added to the investment case"); locked rows "from the energy model"; revenue prices start empty with a public range beside them, never filled in (BC rule); not-established never shows 0 (KPIs, workbook, Economics sheet); DSCR reads "n/a: there is no debt"; refusals carry a line and column in the API (G9); stale detection after a point-of-connection change ("no current solve"); the workbook adds a "Business case" sheet with its hash and the investment-case key when the investment view is selected; light and dark are both legible.

## What does not

Six bugs, each with an issue file (all `Status: ready-for-agent`).

| # | Severity | Title |
|---|---|---|
| 01 | high | Data-centre business case: the IT-capacity output is never offered (the preset cannot run) |
| 02 | high | Data-centre investment case: no path from the template, and the screens contradict each other |
| 03 | high | "Not established" names the wrong cause, or a raw code |
| 04 | medium | IPP preset: capacity payment has no de-rating row; price hints are in another currency |
| 05 | medium | Preset leaves required fields empty; the refusals are raw strings |
| 06 | low–medium | Raw and mangled names on the finance screens and in the workbook; the line table clips |

## Improvements

1. **One "price this site" step that ships assumed finance defaults.** The IPP template carries a finance block, tariff and participants and its case just runs; the data-centre template carries none and needs about ten inputs in a fixed order. Ship the same defaults on every template (flagged "assumed", source and year, as the cost packs already do): asset dates, a tax rule, degradation rates, a WACC. Reason: HOLISTIC-1 asks for no dead ends, and a first result in one click lets the user judge before they tune.
2. **A readiness checklist on the Investment and Business case screens**, in flow order, each line with its control: "Point of connection · Participants · Asset dates · Tax rule · Degradation · Terminal value". Reason: the engine already knows the first blocker (`row_reason`); the user sees only the last code.
3. **Label the two break-even prices on the IPP.** The case shows "LCOE (finance-consistent) 84.48 EUR/MWh" and "price for target IRR 36.35 EUR/MWh" beside PPAs at 65 and 50 and a positive NPV. They answer different questions (all generation at one price, against the PPA price alone) but nothing says which; they read as a contradiction. Reason: DECISION-GAPS-1, a number the user cannot place is a gap. (Not filed as a bug: the NPV ties; LCOE was not recomputed by hand.)
4. **Put the investment and business case in the default workbook when they exist.** "Cards included" lists "Commercial & finance" but no such sheet appears until the Investment view is ticked. Reason: the export is the hand-off, and the About sheet should not promise what is missing.
5. **Show the money year on the price, in words.** "9 469 000 EUR/MW in 2025 money; escalated 2 % a year to the 2027 close = 9.85 M" beside each capex and cost line. The engine does this correctly (verified); the screen does not show it. Reason: the owner can then trace a revenue or cost to its input.
6. **Reconcile the P&L rows to the streams.** DC 2027 revenue row 99 181 329 against the two revenue streams 99 133 800; the 47 529 EUR difference (0.05 %) is not named. Ask the owner whether a small positive item in "vom" belongs on the revenue row; either way show it.
7. **Say which capacity prices the land lease.** The IPP card says "50 MWp PV"; the model builds 41.67 MW (AC) and the land lease prices 41.67. State "per MW AC" on the row. Reason: a developer pays land per MWp.
8. **Trace button on each KPI** ("how this was computed": the cash lines that sum to it, as the hand check does). Reason: HOLISTIC-1, results traceable to inputs; the data is already in `cashflow_lines`.

## Hand-off

Bugs 01–06 go to builders via the coordinator. UX 2 reviews this text. Coordinator list follows in the PR message.
