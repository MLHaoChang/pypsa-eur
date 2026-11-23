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
