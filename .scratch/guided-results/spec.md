<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Guided results legibility

Status: agreed (UX 23 plan GO with conditions 1-10; GK B 62 plan GO with G1-G5; expert-catalogue design lead GO). Conditions in `issues/01-headline-card.md`.

Owner decisions: UX-NEXT-1 (commissions this package), HOLISTIC-1, HOLISTIC-REC-1, EXPERT-GUIDED-1, RAW-NAMES-1,
VISUALS-1, DASHBOARD-1, DENSITY-1. Master `c78a115e3`.

Evidence: `.scratch/ui-modern/audit.md` findings 1, 4 and 7 (9 Nov). Since then ui-modern 05 (#1340, #1353) fixed part
of 4 and 7, so this spec starts from what master shows today, not from the audit's shots.

**Journey fit.** Steps touched: *read results* (the headline card), *decide/compare* (why this number, the
assumptions behind it) and *set up site and assets* (sections 2, 4, 5, 6). Hand-in: the run-progress sibling's
running state ends inside the Results card's own running slot, and the next-step flow's "Next"/"Open results" lands on
the headline card, top of section 10. Hand-out: "Show the engineering detail" and each figure's "Open in Expert" lead
to the Expert Results tab that holds the same figure (the HeadlineStrip, the Investment tab, the Reliability tab), so
Guided and Expert show one number from one read.

## What master shows today

| Area | Today | Where |
|---|---|---|
| Guided Results (section 10) | Verdict sentence, one cost tile, ranked risks, gaps list, then four chapter tiles (NPV, grid checks, expected shortfall, operation). No chart. No "why". | `pages/hubDesign/cards/ResultsCard.tsx:52-217`, `ChaptersSection.tsx`, `chapters.ts` |
| Expert Results headline | `HeadlineStrip` per project type (LCOE, system cost, new-build capital, curtailed share, unserved energy, time to power) from `/results/headline_kpis` | `pages/results/HeadlineStrip.tsx`, `backend/services/results/headline_kpis.py` |
| Assumed rows | One Assumptions card per section, plain words (`_LEDGER_PLAIN`), one "Edit ›" per row, one "Keep all defaults". Always open. Seven rows in a microgrid section still push the section's own content below the fold. | `pages/hubDesign/AssumptionsCard.tsx`, `backend/services/flow_service.py:846-903` |
| Sections 2, 4, 5, 6 | A list of what the section holds (one row per carrier, load, asset or series, its kind, one figure, "Edit ›") and "Open … in Expert ›". No totals, no picture, so the section answers "what is there" but not "is it about right". | `SectionView.tsx:105-190`, `SectionHoldings.tsx` |
| Why this number | Graph routes exist: asset field (`/asset_schema/{c}/provenance/{n}/why/{f}`), Guided ledger row (`/studies/{id}/ledger/{key}/why`), checked metric (`/chat/evidence/{id}/why/{metric}`). No frontend caller. No target for a study's headline figures. | `backend/services/provenance_graph/explain.py` |

## 1. The headline card (top of section 10)

One card replaces the verdict block and the cost tile. Top to bottom:

1. **Verdict**, one sentence, unchanged (`headline(review, report)`), with the existing edited / unchecked / stale line
   above it. One "Out of date" mark per card (audit 10).
2. **Three figures**, the shared `Kpi` tile, in this order:
   - **Reliability**: expected shortfall in hours per year against the goal ("0.4 h/yr, goal 1 h/yr"). Source: the
     study (`mc_lole_h`, `target_lole_h`), as the chapter tile reads it today.
   - **Net present value**: from the investment case (`chapters.investment`, same read, same basis words).
   - **Cost of energy (LCOE)**: the `lcoe` card of `/results/headline_kpis`, the read Expert's strip uses.
   A figure that is not there says its `ResultState` and the one action that produces it ("Run the investment case"),
   never 0 and never blank; a project type whose Expert strip has no LCOE card reads "Not applicable for this project
   type" under the same label. The study's cost at target (today's tile) stays as its own tile in the row, unchanged
   (P24-FE gate B1 wording, period basis). Each figure carries its own state; the card's study banner ("These results
   are from an earlier study") is the study figure's one mark, so that tile only dims under it. When some figures are
   current and others out of date, one line says which (GK B G1). Tiles use the shared `Kpi` notation, the one
   Expert's strip uses, so the two modes print a figure alike.
3. **One chart: reliability against the goal.** A horizontal bullet chart: the expected shortfall as the bar, the
   goal as a marker, the pass/fail band behind it; the number in the title ("Expected shortfall 0.4 h/yr, goal
   1 h/yr"). Built from `components/charts/chartTheme.tsx` (`ChartFrame`, `ChartSourceLine`, tokens), with the
   source line naming the study run and its out-of-date mark (VISUALS-1). The axis says "Expected shortfall (h/yr) ·
   lower is better", the goal has a text label, and "Within goal" / "Above goal" is printed in words (UX 23, 7). With
   no goal set, the chart shows the bar alone and the existing "Set a goal" button.
4. **"Why this number"**, a disclosure under each figure (closed by default), in plain words:
   - what produced it (the study run, the investment case, the solve) and whether it is current;
   - the inputs it rests on: the Guided ledger rows the study used, each with its value, its source stamp
     (default / customised / measured) and, on demand, the provenance walk from `/studies/{id}/ledger/{key}/why`;
   - the defaults among those inputs that nobody has confirmed (section 2 below), with "Edit ›".
   No new backend target in slice 1: the disclosure lists the run and the ledger rows the frontend already reads,
   and calls the existing ledger-row route. A graph target for the study's own figures is a follow-up (see Open
   questions, Q2).
5. Then, unchanged: biggest risks, what the study could not establish, report buttons, and the chapter tiles. The
   tiles no longer repeat a figure the headline shows: "What the design is worth" and "Expected shortfall" say
   "in the headline above" and drop their number and their "Open" link, since the headline figure's link goes to the
   same Expert tab (one number, one place; one entry point per destination, UX 23, 5).

Guided and Expert agree: the LCOE figure and its state come from the same `/results/headline_kpis` read as Expert's
strip; NPV from the same investment report as Expert's Investment tab; each figure's "Open in Expert" opens that tab.

## 2. Assumptions as one plain-language group

- **Per section** (3, 5 and the others that raise rows): the Assumptions card opens **collapsed** to one line,
  "5 defaults not yet confirmed · Keep all defaults · Show ›". Open, the rows are grouped under plain headings by what
  they are about (grid connection, units and sizing, reliability goal, costs), one "Edit ›" each, as today.
- **On Results**: one collapsed group, "Assumptions behind this result (N)", listing every unconfirmed default across
  sections, each with its section name and "Edit ›" (it returns to section 10 through the existing "Back to"). This is
  the same list the "Why this number" disclosures filter.
- **Words**: every row stays in `_LEDGER_PLAIN` words; the test that already pins "no engineering keys in the row
  text" extends to every shipped template (RAW-NAMES-1). Unit names such as `diesel_1` stay, as ui-modern 05 decided:
  they are the names the lists and editors show.
- Nothing changes in what is acknowledged or how (`ackFlowItems`, the ledger `value`, the fingerprint).

## 3. Sections 2, 4, 5 and 6: from a list to an answer

Each keeps its list and its "Open … in Expert ›" link, and gains **one summary line and at most one small chart**
above the list, read from the flow's `holds` (display only; GK B H2 holds: it feeds no state, count or verdict).

| Section | Summary line | Small chart |
|---|---|---|
| 2 Carriers & processes | "3 carriers: electricity, heat, hydrogen · 1 process unit" | none: a list of names is the answer |
| 4 Loads & demand | "2 loads · largest peak 40 MW" (never a sum of peaks: peaks do not coincide) | none in slice 3; a load-shape sparkline needs a series read (Q3) |
| 5 Assets & sizing | "9 assets · 52 MW fixed, 3 sized by the study" | one bar per carrier of fixed capacity (MW), carrier tokens; extendable assets named, not drawn |
| 6 Time series & profiles | "5 series · 2 loads, 3 availability" | none in slice 3 (Q3) |

A sum only adds figures with the same field and unit; mixed units are listed, not added. The helper generators
(`__lost_*`, `__buy_*`, ui-modern 05 follow-up) are left out of the sums and the chart.

## Slices

| # | Slice | Area | Size |
|---|---|---|---|
| 1 | Headline card: verdict, three figures, the reliability chart, chapter tiles without repeated numbers | UI | ~500 lines |
| 2 | "Why this number" disclosures + Results assumptions group | UI (+ GK B for the ledger-why caller) | ~500 lines |
| 3 | Collapsed, grouped Assumptions card per section; summary lines and the assets chart in sections 2, 4, 5, 6 | UI | ~400 lines |

Each slice: vitest for the states (ready, not run, running, stale, refused) and the one-number rule; shots dark and
light, `dc-` and `mg-`, at 1280×800, through `scripts/coord/shots.sh`.

## Open questions

- **Q1. Which chart heads the card?** Recommended: reliability against the goal (it backs the verdict, and the study
  always has it). Alternative: the cost split of the design (needs the project's own solve, so it is often "From the
  study" or missing in Guided).
- **Q2. "Why this number" depth.** Recommended for now: run + ledger rows + the existing ledger-row walk (no backend).
  Later: a provenance-graph target for the study's figures (`study_figure:<id>/<metric>`), GK B-gated.
- **Q3. Series sparklines in sections 4 and 6.** Recommended: not in this package; it needs a series read per row.

## Not in this package

Colour and typography (THEME-LIGHT, ui-cohesion), the run progress itself (run progress sibling), the "Next" flow
(next-step sibling), the Expert Results layout (expert-catalogue), OperatingReserve pages (#1576).
