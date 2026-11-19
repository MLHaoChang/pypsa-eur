# Guided Results headline card

Status: in-progress
Type: task
Spec: `.scratch/guided-results/spec.md` §1

Gated: GK B (62), UX (23).

## What to build

Spec §1: verdict, three figures (reliability against the goal, NPV, LCOE) with their own source and state, one
reliability-against-goal bullet chart, and chapter tiles that no longer repeat those figures.

## Acceptance

- One vitest per ResultState of each figure (ready, not run, running, stale, unavailable); a missing figure is never 0 or blank.
- The one-number rule: a figure shown in the headline is not repeated on a chapter tile.
- GK B G1, G2, G5 below.

## Comments

### Plan verdicts (2026-11-17)

GK B (62): plan GO, with these conditions:
- G1. One solve, said once. LCOE (headline_kpis), the study's LOLE and the IC NPV may come from different solves or revisions. Each figure shows its own source and state; when they do not describe the same network revision the headline says so, rather than reading as one consistent result. Vitest with mismatched revisions. A missing figure reads unavailable with its reason, never 0, never a blank that looks like 0.
- G2. Types come from the server's own return shape (#1060), for headline_kpis and ledger/why. Fixtures are built from a captured real response, not hand-typed. LCOE's unit and money year as the server gives them.
- G3. ledger/why is a project-scoped read: project_id equals the active project; 404/409 read as "no data" in words, never an error toast. Read only, no write on open.
- G4. The holds sums: the bus/asset's real unit (#1347/#1353), never the carrier's catalogue unit; same-field, same-unit only, each excluded row named; the helper/slack exclusion from a server flag if one exists, else a copied pattern pinned against the backend's `is_slack_name` cases; display only, a test asserts nothing reads the sum.
- G5. HOLISTIC-1: Expert and the report use the same names and units for LCOE, LOLE and NPV. State any place they differ and file it.
- Q2: a provenance-graph target for the study's own figures is a separate ticket, not this package.

Expert-catalogue design lead: GO. No Results tab is renamed or moved; no Expert reliability chart planned.
- Input names in the headline (reliability goal, discount rate, lifetime) read their label and unit from the catalogue entry once its slice 01a lands; until then today's goal-editor word. No new local word map.
- "Open in Expert" switches mode only on the user's click, through withReturn, so Back returns to the Guided Results card.

Next-step flow: takes no files; its optional "Open results" header link (layout/AppHeader.tsx) would target hub-card-results.

UX (23): plan GO with conditions (Q1-Q3 as recommended):
1. Each figure carries its own ResultState; a card-level mark only where all share it. Built: per-figure state; the study banner is the study figure's own mark (its text names the study), NPV and LCOE carry their own.
2. LCOE "Not applicable for this project type" as text, same label as Expert's strip. `money()` notation for NPV and cost: answered in the PR (the shared Kpi tile has its own notation, which Expert's strip uses too).
3. Expert never shows less than Guided: say how Expert gets "Why this number" (slice 2).
4. The "why" walk passes the plain-words test (no ledger keys, route names, engine ids); "Assumed" is the word for unconfirmed defaults (slice 2/3).
5. One entry point per destination; the figure link goes through withReturn on a user click. Built.
6. Collapsed Assumptions line uses ▸/▾, shows a blocking state in words, only "Keep all defaults" as bulk action (slice 3).
7. Bullet chart: axis with unit, goal text label, "lower is better", "Within goal"/"Above goal" as text, text alternative, source line. Built.
8. Summary lines use "·"; section 5 chart unit-labelled axis, direct carrier labels, tonne buses never on one axis with MW, extendable assets named in a visible line, demo-data label kept (slice 3).
9. HOLISTIC: the PR lists where the three figures appear (Summary, Compare, reports, chat) and that they read the same source.
10. Shots dark and light, dc- and mg-, all states; one Expert shot of the Results headline.
