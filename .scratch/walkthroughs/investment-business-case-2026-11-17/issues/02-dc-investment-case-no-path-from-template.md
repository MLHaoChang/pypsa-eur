<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — Data-centre investment case: no path from the template, and the screens contradict each other

Status: ready-for-agent
Severity: high (no investment decision is reachable on the first template in the group)
Group: Investment and business case. Walk 2026-11-17, master 9491878d.
Breaks: owner decisions HOLISTIC-1 (one journey, no dead ends), EXPERT-GUIDED-1 (Guided and Expert agree), RAW-NAMES-1 (no tool or field names on screens), DECISION-GAPS-1. Extends `.scratch/template-e2e/issues/22-improve-and-investment-no-manual-path.md` (in flight on `claude/template-e2e-decide-report`): that one says the Guided path needs a manual action; this one lists the walls a manual path meets.

## Repro

1. Create "Data Center Energy Hub" from the template. Run the Guided study (section 9 › Run study; 30 s), or Expert › Run LOPF.
2. Guided section 10 › "The whole picture" › Investment tile.
3. Expert › Results › Commercial & finance › Investment case. Then try to run the investment case by hand (`POST /api/results/investment_case`) and fix each refusal as the screen tells you.

## Actual

- Guided section 7 (Commercial & parties) shows "complete" while its own body says "No finance inputs are stated for this project" and "Set up the commercial config (the point of connection) before adding contracts."
- Guided section 3 shows a green "Connection point: Grid import (40 MW)". Expert › Commercial & finance says "The investment case needs the site connection. Set it in Connection & grid ›". It is set in the reliability sense; the commercial point of connection is a different field (Expert › Connection & grid › Site connection, saved by "Save site connection"), and Guided has no control for it.
- Guided section 10 Investment tile reads: "The investment case did not complete: value_flows_not_configured: no commercial config, no value_flows, or no solve." A raw code in prose, no next step.
- Walls met in order, each a separate raw refusal: `value_flows_not_configured` (no point of connection, no participants) → `cod_missing: cod_by_asset names no date for ['genset_1', … 'site_transformer']` (nine asset ids) → with those set the case completes but `project`, `tax` read `not_established`, and the IC report has every KPI `null` (no tax pack, no tax-loss rule, no capex).
- The template ships none of these. The IPP template ships a full finance block, a tariff, PPAs and participants and its investment case completes (NPV 29.33 M EUR at 7 %); the data-centre template ships none. Neither Guided nor Expert says so.

## Expected

On any template in the group, either the investment case runs from the template's defaults (assumed values flagged, as the IPP does), or the screen names, in plain words and in order, what is missing and puts a control for each in the same step. Guided section 7 is not "complete" while the finance inputs are empty. The two "connection" notions are named apart ("Site connection for billing", "Connection for the reliability study") and each points to its own control.

## Acceptance

- Data-centre template: Results › Investment gives a number or a checklist (point of connection, asset dates, tax rule) with a button for each, in Guided and Expert.
- No `value_flows_not_configured`, `cod_missing`, asset-id lists or field paths in user-facing prose.
- Guided section 7 status reflects finance inputs.
- A Playwright check that the Guided and Expert investment screens say the same thing.

## Comments

- Seen again 2026-11-20 (template walk, master 1f6905e3): DC: Guided Results 'Investment case ›' and Expert Results > Commercial & finance still say 'The investment case needs the site connection. Set it in Connection & grid' although the connection point is set (Guided step 3 shows 'Grid import (40 MW)'). H2 does reach 'Run the investment case'.

- 2026-11-23 status sweep: landed in #1711 (56987f69); #1683 (c779c3d1): the DC template ships a point of connection, owners, dates and finance inputs; refusals name their need. Unverified: Guided section 7 status reflecting finance inputs, and the Playwright Guided/Expert agreement check.

- 2026-11-23 re-walk (IBA-02-QUEUE-1), master 443ec313, real app (Playwright, 1600×1000), "Data Center Energy Hub" from the template form with every default:
  - Passes. Guided section 7 reads "2 to check · Assumed · 2 not yet confirmed", not "complete". The template ships the site connection (Expert: "Site connection: imports through grid_import, no export link"), the asset dates and the finance inputs. The "needs the site connection" screen no longer shows.
  - Passes. Guided 9 › Run study (reliability) → 10 › Investment tile › "Run the investment case" lists "The investment case needs: A solved project — Run the solve", with no raw code. "Run the solve" solves, and the case then runs: Guided headline "What the design is worth −€18.2 M, Net present value at the WACC, after tax"; Expert › Results › Commercial & finance › Investment case "Unlevered NPV at WACC −18,220,995.22", IRR −5.16 % post-tax, LCOE 99.04/MWh real. Guided and Expert agree.
  - Still fails (acceptance 2, raw codes and asset-id lists). Expert › Investment case › Counterfactual lists unworded flags: `asset_lifetime_unknown:genset_1` … `:ups_battery` (seven asset ids), `baseline_island_rows_relaxed`, `counterfactual_basis:site_today`, `degradation_bill_first_order`, `degradation_bill_volume_items_only`, `island_critical_load_mw:32.161`, `lcoe_on_incremental_value`, `lcos_not_established:no_discharge:bess_new`, `meter_link_not_investment:grid_import`, `remaining_basis_written_off:gewst`/`:kst`. The LCOS rows read "not established (no_discharge:bess_new)". `projectFlagWords` (financeModel.ts) has no words for these keys, so they pass through as codes.
  - Still fails (acceptance 4). No Playwright check compares the Guided and Expert investment screens; the agreement above was checked by hand.
  - Not fixed here (noted): Guided has no in-section control for the billing site connection; the needs checklist's "connection" button jumps to the Expert editor. The Expert tax-layer detail table (Advanced) shows `eu_de`, `offset_other_income`, `not_deducted` as values. After the dispatch solve, Guided says "Expected shortfall is out of date".

- 2026-11-23 GK B2 (5), the old PR B plan conditions (from GK B2 (2)'s hand-off): B1, one shared section-7-needed predicate. B2, one refusal-words map whose keys a test compares to the backend refusal kinds as an exact set (the `detail.needs` codes included), with a generic fallback. B3, the connection fix reuses SiteConnectionForm and refetches after the save, with no optimistic update. B4, after #1626 (merged a8f35cc71, met). The billing control moves to section 3 (UX 2's call). After the re-walk above: the remaining work, finance-flag words and the Guided/Expert smoke, touches none of B1–B3. Refusals already go through `investmentCaseNeeds.json` and its parity test.

- 2026-11-23 GK B2 (5) plan GO, conditions G1–G8:
  - G1: one words map for both modes.
  - G2: the live flag set is checked as an exact set (`projectFlagKeys.json`, read by both the unit test and the smoke), and an unknown code shows as "Other check: <code>".
  - G3: skips match their exact prefix, with tests and killed mutants.
  - G4: every sentence states today's behaviour (timeline.py:69, cashflow.py:253).
  - G5: the smoke checks the prose against `/\b[a-z]+(?:_[a-z0-9]+)+\b/` and is shown red on 443ec313.
  - G6: Guided and Expert NPV match to 0.1 M with the sign; the needs match by text; an absent value fails.
  - G7: a mutant for each new word, the fold, each skip, the fallback and LCOS `no_discharge`.
  - G8: the out-of-scope findings are filed as 07, 08 and 09.

  Built: `island_critical_load_mw` and `baseline_island_rows_relaxed` are worded, not skipped: the 32.161 MW critical load appears in no other sentence. Only `counterfactual_basis:site_today` is skipped, and only when the report's counterfactual basis is `site_today`.
