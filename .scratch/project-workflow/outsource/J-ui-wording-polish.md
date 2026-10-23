<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Status: ready-for-agent — written 2026-10-23; no gatekeeper review (frontend wording, no engine change); UX reviewer gives the OK

# Outsourced task J: Nine wording and label fixes in the frontend

Branch: `outsource/ui-wording-polish` · one PR against `master` · PR body starts with `Outsourced task J`

## Read first

1. `AGENTS.md` (repo root). Release notes and PR rules are there.
2. `docs/agents/review-ux.md`. The UX reviewer applies it to this PR: "›" only for links that go somewhere, "·" between data, and screenshots of each state in Guided and Expert.
3. `.scratch/frontend-wording-fixes/screenshots/README.md`. PR #653 did a batch like this one. Its README says how to run the screenshot harness (`harness.tsx`, `harness.vite.config.ts`, `shoot.mjs`). Reuse it; delete your copies afterwards.
4. The code for each item, listed under "The nine items". Every path below was opened on master `abefdbd49`.

## Goal in plain words

Nine places show a machine word or an awkward string. Each becomes plain text. Nothing else changes: no new panel, button, setting or colour. The app's 9-11px text and the Inter font are deliberate (owner decision DENSITY-1); do not restyle anything.

Make one small commit per item, in the order below. Where a test file exists next to the code, add a vitest case in it. Write the case first and see it fail.

## The nine items

### 1. The finance model shows raw flag codes

`pages/results/investment/financeModel.ts:735-751`, `projectFlagWords`. It words the campus life-end flags and passes every other flag through unchanged. The list is shown by `Counterfactual` in `pages/results/investment/InvestmentCaseView.tsx:190-208`, so `load_shed_excluded:12.5` reaches the user as written.

- The codes come from `flags.append(...)` in `pypsa-gui/backend/services/results/finance_case.py` (lines 472-2016). Read them; do not guess.
- Add a table of words keyed by the part before the first `:`. The sentence uses the parts after it (for example the MWh in `load_shed_excluded`).
- Skip the flags `BASELINE_CAVEATS` already words (`financeModel.ts:331-358`: `counterfactual_exceeds_connection`, `counterfactual_shed_at_voll`, `counterfactual_buys`, `co2_price_period`, `counterfactual_exceeds_supply`). Check whether `Counterfactual` shows both; if it does, remove the raw copy.
- A code with no entry still passes through unchanged. No flag is ever hidden because it has no words.
- Test in `pages/results/investment/financeModel.test.ts`, next to the `projectFlagWords` case at l.310-321.

### 2. A campus asset shows an internal id (located with doubt)

I could not find one place that says "kind". The campus equipment table on the Investment tab already uses `assetLabel` (`financeModel.ts:696-699`), which prefers `display_name`. Two places show a library id as written:

- `pages/CampusInvestmentSection.tsx:453` (Asset column: `x.library_id`);
- `pages/CampusElectricalPanel.tsx:600` (`bought.library_id`).

`InvestmentRow` (`api/campusElectrical.ts:118-145`) has no display name. Use `assetLabel(library_id)` for both (underscores to spaces, no `campus:` prefix). Keep the raw id in a `title` attribute for Expert, as `components/AssetName.tsx` does. If you find the real spot is somewhere else, fix that and say so in the PR body. If you find nothing more, say that too.

### 3. The cuts table shows "E1" and "p2030 h3"

`pages/CampusIslandCutsSection.tsx`:
- l.149 (trials): `${c.kind} p${c.period} h${c.hour} ≥ …`;
- l.166-168 (applied cuts): `c.kind`, `p{c.period} h{c.hour}`;
- l.205-209 (proposed cuts): `p{period} h{hour}`, `c.event` (raw `E1`), `c.kind`.

Make three small helpers in that file, next to `scenarioWords` (l.39):
- Hour: "2030, hour 3".
- Event: a word, with the id kept in `title`. From `gridspine/static/frequency.py:654-677, 696, 861`: E1 grid opens (islanding), E2 load step, E3 unit trip, E4 start from a dead bus, E5 PV loss, E6 planned islanding. Read those lines to confirm; an unknown id shows as written.
- Kind (`CutKind`, `api/campusElectrical.ts:544`): inertia, up headroom, down headroom, droop gain.

Test in `pages/CampusIslandCutsSection.test.tsx` (the fixture at l.29 has `event: 'E1'`, period 2030, hour 3).

### 4. The "Edit in Connection & grid ›" link wraps badly

`layout/properties/cardKit.tsx:1061-1067`, inside the two-column `eh-bus-fields` grid (l.1056). The button class is `self-start text-[10px] text-accent underline`, so in a narrow cell it breaks mid-phrase. Add `whitespace-nowrap`, or let the cell span both columns. Check the same link text in `components/series/WeatherBar.tsx:58` and `pages/economics/SiteConnectionLink.tsx:22` at narrow width; fix only the ones that wrap. Keep the text and the "›". Existing tests click it by name (`layout/PropertiesPanel.energyHub.test.tsx:126`), so the name must not change. Add a class check to that test file.

### 5. "the template" under Fault level reads oddly

`components/gridReadiness/ConnectionLimits.tsx:57-64`, `resetTarget`. The Fault level line's reset button reads "Reset to the template's 25, assumed" (the unit is empty for this row). The `GridLimitSetting` payload (`api/campusElectrical.ts:712-716`) has no template name, and the backend that builds it (`services/adequacy/grid_readiness.py`) is another session's file. So:

- First look for a template name the frontend already has (the project's template metadata, the flow scope payload `components/flowScope/useFlowScope.ts`). If there is one, use it: "Reset to the Data Center Energy Hub's 25, assumed".
- If there is none, do not touch the backend. Say in the PR body that the name needs a backend field, and word it as "the starting template" meanwhile.

Existing tests pin the text: `components/gridReadiness/ConnectionLimits.test.tsx:39, 125`. Update them with the new text.

### 6. The Back pill reads "Back to Readiness → run"

The pill is `layout/FlowReturnButton.tsx:8-14`. It already has an `ArrowLeft` icon. The "→" comes from the section name: `layout/flowSections.ts:23` (`label: 'Readiness → run'`) and a copy in `components/gridReadiness/GridReadinessScorecard.tsx:39`. `layout/flowNavigation.ts:78-82` (`withReturn`) only stores the label; do not edit that file (task I edits it).

- Change the section name at its source to words ("Readiness and run"), in both maps. Run `grep -rn "Readiness → run" pypsa-gui/frontend/src` (15 hits at the time of writing) and update every test and comment that pins it, including `layout/flowReturn.test.tsx:21, 53`.
- Other pills read "Back to Results" (`components/BackToHubResults.tsx`) and "Back to Connection & grid". Keep that pattern.

### 7. `PAGE_RANGES_EXAMPLE` has no space after the comma

It is a backend constant: `pypsa-gui/backend/services/campus_grid_code_service.py:105`, `"1-40,120-180"`. Change it to `"1-40, 120-180"`, which is how the UI hint already writes it (`pages/CampusGridCodeSection.tsx:150`, and `pageRangesText` at l.37). `parse_page_ranges` (l.327-349) strips spaces, so input is unaffected. Update the docstring at l.36 too. The backend test (`tests/test_campus_grid_code_service.py:244`) reads the constant. Update the two mocked server messages in `pages/CampusGridCodeSection.test.tsx:137, 143`. Add a backend assertion that the example parses with `parse_page_ranges`.

### 8. A band with two negative edges reads "−a to -b"

`pages/CampusElectricalPanel.tsx:680-684`: `num(c.limit_min, 3).replace('-', '−')`. A string `replace` with a string argument changes the first match only, and `limit_max` is not changed at all. Swap the minus sign in both edges. Make one small local helper and use it for both. Also check `pages/CampusGridCodeSection.tsx:82` (`pu`), which has the same first-match shape; there each call is one number, so it is fine, but use the same helper if it reads better. Test in `pages/CampusElectricalPanel.test.tsx`: the case at l.321-327 has one negative edge; add `limit_min: -0.2, limit_max: -0.1` and expect `−0.2 to −0.1 Mvar`.

### 9. The hub "Grid plan" tile counts different rows from the table

`pages/hubDesign/chapters.ts:104-135` (`gridPlan`). It reads `campus?.results?.compliance` at l.106 and l.117. The table reads `r.compliance_invested ?? r.compliance` (`pages/CampusElectricalPanel.tsx:624-625`). When the run invested, the tile counts the as-is rows and the table counts the re-solved rows. Both lines get the same fallback: `campus?.results?.compliance_invested ?? campus?.results?.compliance`. The status column the tile uses (`status_with_measures`) exists on both row types (`api/campusElectrical.ts:162`). Test in `pages/hubDesign/chapters.test.ts`, next to the grid-plan case at l.108-134: a result with both arrays, the invested one passing more checks, and expect the tile to count the invested one. A result with `compliance_invested: null` keeps today's count.

## Files it may touch

Only the files named in the nine items, their tests, and `doc/release-notes.d/outsource-ui-wording-polish.md`.

## Files it must NOT touch

- `layout/flowNavigation.ts` (task I) and `layout/FlowReturnButton.tsx` beyond what item 6 needs (nothing is expected).
- `pypsa-gui/backend/services/adequacy/grid_readiness.py`, `gridspine/**`, `data/grid_codes.yaml`.
- Profiles, carrier KPIs and curtailment files (tasks G-a and H), the navigation files of task I, `harness/**`.
- `doc/release_notes.md`.

## Review

The UX reviewer gives the OK (UI change), against `docs/agents/review-ux.md`. The PR body shows screenshots of each changed state, in Guided and Expert. No gatekeeper review.

## Repo rules

- Branch `outsource/ui-wording-polish` from current `master`. One PR. Never force-push. Do not merge it; the coordinator merges.
- PR body starts with `Outsourced task J`, then follows `.github/pull_request_template.md`. Mark it AI-generated. List any item you could not find or chose to leave.
- One release-note fragment, `doc/release-notes.d/outsource-ui-wording-polish.md`, with one bullet: `* Fix(pypsa-gui): Plain wording for finance flags, campus assets, island cuts, the fault-level reset and the Back pill; a grid-code limit band with two negative edges; the Grid plan tile counts the rows its table shows.`
- Do not skip, loosen or delete any existing test; update the ones that pin the old text.
- Do not commit credentials.

Commands (from `pypsa-gui/frontend`; Node comes from `.pixi/envs/default`):

```bash
npx vitest run src/pages/results/investment/financeModel.test.ts src/pages/CampusIslandCutsSection.test.tsx src/layout/PropertiesPanel.energyHub.test.tsx src/components/gridReadiness/ConnectionLimits.test.tsx src/layout/flowReturn.test.tsx src/pages/CampusGridCodeSection.test.tsx src/pages/CampusElectricalPanel.test.tsx src/pages/hubDesign/chapters.test.ts
npx tsc -b
pixi run gui-tests tests/test_campus_grid_code_service.py
```

## Done when

- [ ] Each of the nine items is a separate commit, or the PR body says why not.
- [ ] Every changed string has a test that fails on master.
- [ ] The eight vitest files above, `tsc -b` and the backend test pass.
- [ ] The full frontend suite is green, or the PR lists each failure that also fails on `master`.
- [ ] Screenshots of each changed state, in Guided and Expert, are in the PR.
- [ ] The PR body names any item you could not find (item 2 is the likely one).
