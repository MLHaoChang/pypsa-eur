<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Control strategy walkthrough (2026-11-17)

This is an end-to-end walk of the **Control strategy** group: dispatch and reserve, run-time (island) control, the grid-to-process chain and the time-series generators. It ran on master `9491878d` with the real API and Vite, signed in as the dev admin, through Playwright, in Guided and Expert, light and dark. Owner decision WALK-E2E-1.

Templates walked: Island Microgrid, Data Center Energy Hub, Green Ammonia Hub (staged growth). The shots in `shots/` are downscaled.

**Result: 12 issues** (`issues/01`–`12`, all `ready-for-agent`): 3 high, 5 medium, 4 low. The engine numbers checked by hand are right. The gaps are in routes, words and what each mode shows.

## Scope and what was not walked

- **The Control strategy group, as specified.** `workspace-reconciliation.md:303` defines it:
  - inputs: section 5, the Controls block of each converter-coupled asset; section 8, island requirements;
  - run: `control_checks`;
  - results: Controls (island report, frequency cuts, grid-forming recommendation).

  Dispatch and the operating reserve sit with Capacity expansion, under the Operation results.
- **Not walked:**
  - The chat dock: no `ANTHROPIC_API_KEY` (KEY-LATER-1). This leaves out:
    - the time-series chat refinement (timeseries-generators 04);
    - `explain_metric`;
    - the study-harness profiles: the composer and plan-review card, `study-harness/design.md:853-882`.
  - Study-harness #1586, #1590, #1594, #1616 and #1624: in flight.
  - The campus steady state, short circuit and frequency checks: they need the campus file (issue 05).
  - The Mining Concentrator: a 15-minute run, already filed as template-e2e 09 and 12.
- **Not re-filed (already open elsewhere):**
  - Raw names in the Grid study and Control tabs (`gfl_pq`, `ups_bridged`, `droop_pct`, "island_config.json"): template-e2e 19.
  - The DC set-up cannot be edited after creation: template-e2e 20.
  - A Guided study leaves Expert empty: #1618 issue 01 (issue 06 here is its mirror).

## Journey map

| # | User goal | Guided route | Expert route | Verdict |
|---|---|---|---|---|
| C1 | See and confirm the island control set-up (droop, grid-forming, current limit) | 5 › Assumptions › "Island unit settings … Edit ›" | 8 › Grid study › Island (control mode per unit, raw JSON) | **Fails**: the Guided link opens Expert Capacity bounds (01); there is no Controls block in the asset form (02) |
| C2 | Set the operating (spinning) reserve | none | Solver settings › **Network** › Operating reserve | **Fails** in Guided (03); Expert works but is hard to find |
| C3 | Run dispatch with the reserve and read whether it bound | 9 › Run (EH study) | Run LOPF › Results › Capacity expansion › Build › Operating reserve | **Meets** in Expert: matches the hand check (below) |
| C4 | Run the island study and read the cost of island capability | none | Grid study › Island › Run cost-of-islanding study › Results › Control | Numbers right; **no units, constraint names** (04); the prerequisite is a dead end (05) |
| C5 | Read the control and dispatch outcome in Guided | 10 › Results | n/a | **Fails**: "Not run yet" after an Expert solve; no Control card (06) |
| C6 | Generate a load profile (archetype, peak) | 4 › load › Edit › Profile | Properties › Profile | Preview works; **Apply refused on the DC template**, the reason hidden (08); 6 › Edit opens the clipped library (11). Not tried on a non-spec load |
| C7 | Read a load's demand | 4 card "Peak load 34.49 MW" | Properties | **Disagrees**: the drawer says "Demand 0 MW, Sign -1" (09) |
| C8 | Run a process template and read tonnes made, delivered and lost | 10 › Production tiles | Results › Summary › Production | Mass balance right; **buy-in missing from the tiles** (10) |
| C9 | Grid events → lost tonnes (grid-to-process 13) | 10 › tiles | Results › Grid risk | Lines say "missing: campus study run", with a target. It cannot be fully walked without the campus file (05) |
| C10 | See the solve's objective at a glance | status bar | status bar | **Fails**: always "Obj: —" (07) |

## Hand checks (independent of the app)

- **Operating reserve, Island Microgrid** (load 10 %, solar 20 %, wind 20 %; 168 h):
  - Required = 0.1·load + 0.2·PV output + 0.2·wind output. From `/api/results/loads` and `/api/results/generators`, the largest deviation from `/api/results/operating_reserve` is **0 MW** over all hours.
  - Held: hour 4 = 9 MW (three idle 3 MW diesels; batteries energy-limited). The smallest margin, held − required, is 3.97 MW, so "The reserve never bound in this run" is right.
- **Cost build-up, Island Microgrid:** capital €724.5 k/yr + operating €2.75 M = €3.47 M, which equals the LOPF objective (3,473,806). The island study's baseline row equals that objective, and its cost of island capability is 3,657,931 − 3,473,806 = 184,125.
- **Green Ammonia staged:**
  - Delivered: 50/100/150 t/d × 365 × 3/5/10 years = 784,750 t (the tile shows 785 kt).
  - Made + bought in: 672,348 + 112,402 = 784,750 t. Mass balance holds; the tile hides the 112 kt bought in (10).
  - `t_per_unit` 0.19357 = 1/5.166 MWh/t (`grid-to-process/design.md:323-347`).
- **Load generator, DC:** peak 36 MW with the data-centre archetype gives 289,617 MWh/yr, a mean of 33.06 MW (92 % load factor). That is plausible for a data centre.

## What meets the requirements

- The operating reserve engine and its Results card: exact numbers, plain words, a "Reserve inputs ›" link back, and "never bound" stated.
- The island study numbers reconcile with the LOPF.
- The process view's mass balance and per-period delivery match the template's contract.
- The load generator's preview gives peak, year and in-model energy, flags the time-zone assumption, and holds Apply when the model cannot take the series.
- The grid-risk lines name what is missing and carry a navigation target.
- Dark theme: Guided Results and Expert Results render legibly (`shots/dark-guided-results.png`). Nothing theme-specific was found.

## Improvements (recommendations, not bugs)

1. **Give the Control strategy group one home per input, as designed.** Build the section-5 Controls block (02) and the section-8 island requirements card. Until they exist, link every island assumption to the Grid study panel. *Reason:* today the group's inputs live in one Expert panel (`CampusIslandSection.tsx`) that Guided cannot reach, so a Guided user can neither see nor confirm how the island will be controlled.
2. **One rule for which run each mode's headline reads.** Show the source run ("from the dispatch solve, 17 Nov 15:42" or "from the reliability study") on the Results headline in both modes. *Reason:* #1618-01 and issue 06 are the same defect from two sides. A shared "source run" line removes both and makes results traceable.
3. **Name the reserve where users look for it.** Move Operating reserve to Solver settings › Dispatch, add it to the section-8 Assumptions card as "Operating reserve: off (assumed)", and default it on for off-grid templates (for example 10 % of load, 25 % of PV and wind). *Reason:* an island without operating reserve is optimistic by construction. The engine supports it, but no template sets it (`reserve_share_*` is unset in every `project_templates/` entry).
4. **Say what a "Not yet available" line would need.** Turn every "Not yet available" line into "Needs: X ›" with a link. *Reason:* Control › Island, Solver settings › General › Control and Grid study › Study settings each stop the user with no next step (05).
5. **Explain dispatch per hour without the chat.** Add a click-an-hour explanation to the Dispatch chart: which unit was marginal, whether the reserve or a state-of-charge limit bound, and the price. *Reason:* "Why did the battery charge at 03:00?" is the core control-strategy question. Today only the chat (`explain_metric`) answers it, and the chat is off without a key (KEY-LATER-1).
6. **Spec-driven loads should say so up front.** Mark loads that come from a site set-up (DC IT and cooling) in sections 4 and 6, and route their edits to the set-up (08). *Reason:* the user should not build a profile only to learn it cannot be applied.
7. **Units on every money cell in study tables.** Add a shared table cell formatter for € (per year, or horizon). *Reason:* the island study (04) and other study tables print bare numbers. The Summary already formats €M, so reusing its formatter is cheap.

## Files

- `issues/01`–`12`: one file per bug, with repro, actual, expected and the line each breaks.
- `shots/`: downscaled evidence, named by issue.

## Skipped, and the risk it leaves

- The chat-driven journeys and the study-harness profile runs were not walked (KEY-LATER-1; in flight). A failure there would surface only when a key is set.
- The campus steady-state and frequency checks and the grid-forming recommendation were not run, because the campus file was never drafted (05). Issue 05 may hide further defects behind that prerequisite.
- The availability editor (maintenance window, must-run) and the bulk upload receipt were not walked: TSG 02, 07 and 10 are marked done. Untested regressions there stay possible.
- Only one LOPF per template. No multi-period reserve or what-if comparison was run.
