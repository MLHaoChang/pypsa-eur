<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Status: ready-for-agent once PRs #827 and #838 have merged (both touch files below). Written 2026-10-28. No gatekeeper review (wording only, no engine change); the UX reviewer gives the OK.

# Outsourced task P: Five wording fixes users meet in refusals, notes, toasts and confirmation cards

Branch: `outsource/ui-wording-pack-2` · one PR against `master` · PR body starts with `Outsourced task P`

## Read first

1. `AGENTS.md` (repo root). Release notes and PR rules are there: add one fragment under `doc/release-notes.d/`, never edit `doc/release_notes.md`.
2. `docs/agents/review-ux.md`. The UX reviewer applies it to this PR. It says "·" goes between data, "›" only on links that go somewhere, and it asks for screenshots of each changed state in Guided and Expert.
3. `.scratch/frontend-wording-fixes/screenshots/README.md` explains the screenshot harness (`harness.tsx`, `harness.vite.config.ts`, `shoot.mjs`). Reuse it, and delete your copies afterwards.
4. `.scratch/project-workflow/outsource/J-ui-wording-polish.md`, task J, a batch like this one that merged as #706. Follow its shape.

Start from current `master` after #827 and #838 have merged. Every path below was opened on master `700539f9`. Re-find each line before editing: line numbers move.

## Goal in plain words

Five places show text written for developers: a config key, "got 2030", a Python exception, an em dash between data, or a bare "Confirm". Each one becomes plain text. Nothing else changes: no new panel, button, setting or colour, and no restyling (owner decision DENSITY-1: the 9–11px text and the Inter font are deliberate).

Make one small commit per item, in the order below. Write each test first and see it fail.

## The five items

### 1. The end-year refusal reads like a stack trace

`pypsa-gui/backend/project_templates/eh_templates.py`, `_single_year_planning` (about l.336–350). Three `PlanningError` messages start with a config key and end with "got N", for example:

> planning.end_year: the investment case runs from the start year to the end year, so it must be after the start year (2030), got 2030

The UX reviewer's plain form for the first message:

> End year: the investment case runs from the start year to the end year, so the end year must be after 2030.

- Word the other two the same way. The `planning.dates` one becomes "Dates: a single-year example has one period, its start year. Use its staged example to set investment dates." The `end < start` one becomes "End year: it must be 2030 or later."
- Keep the second argument, the field key (`"planning.end_year"`, `"planning.dates"`), unchanged. Code and tests use it to point at the field.
- Search the rest of `eh_templates.py` and `pypsa-gui/backend/project_templates/` for other `PlanningError(f"planning.` messages, and word them the same way.
- Tests pin the text. Find them with `grep -rn "must be after the start year\|got " pypsa-gui/backend/tests | grep -i planning` and update them. Do not loosen an assertion into a substring match on the key alone.

### 2. Screen notes show a Python exception

`pypsa-gui/backend/services/adequacy/grid_readiness.py`, at about l.532, 645, 768 and 848:

```python
note = f"The harmonic screen needs gridspine ({exc})"
```

The same pattern appears for the current-distortion, unbalance and flicker screens. `services/adequacy/island_config.py:83` has a similar line ("the island data needs gridspine, which this build does not carry (…)").

- The user-facing sentence becomes plain: "The harmonic screen is not available in this build." Keep the screen name, and drop the exception text from the sentence.
- Keep the exception text available to Expert users and to logs. Read how `note` reaches the scorecard (the readiness row's payload and its frontend renderer under `pypsa-gui/frontend/src/components/gridReadiness/`).
  - If the row already has a detail or `title` field, put the exception there.
  - If it has none, log the exception with the module's logger and leave it out of the payload. Say which you did in the PR body.
- Do not change the gridspine import logic, row status or severity. Only the words change.
- Tests: `pypsa-gui/backend/tests/` has readiness tests that may pin "needs gridspine". Update them, and add a case asserting the note no longer contains `(`.

### 3. The motor-start row shows too many digits

PR #827 (G2P-18) added a motor-start voltage-dip (ΔU) row to grid readiness. The UX reviewer asked for its value to show 3 significant figures. Find the row's formatter after #827 merges (search `motor` under `pypsa-gui/frontend/src/components/gridReadiness/` and `pypsa-gui/backend/services/adequacy/grid_readiness.py`).

- Format the value in the frontend if the row renders a number there. If the backend sends a preformatted string, round it there instead.
- Reuse the codebase's existing significant-figure helper if there is one (`grep -rn "toPrecision\|sigFig\|significant" pypsa-gui/frontend/src/lib pypsa-gui/frontend/src/components | head`).
- Add one test with a value like 0.0412345 → "0.0412".

### 4. Upload toasts use an em dash between data

`pypsa-gui/frontend/src/pages/TimeSeriesManager.tsx`, at about l.1413, 1446 and 1485:

```
Load profile uploaded: 8760 rows for 3 loads (1 unmatched ignored) — model horizon: 8760 snapshots
```

`review-ux.md` puts "·" between data. Replace ` — model horizon: ` with ` · model horizon: ` in all three. Keep the rest of the text as it is: tests click and assert on it. Update the tests that pin the em dash (`grep -rn "model horizon" pypsa-gui/frontend/src --include=*.test.tsx`).

### 5. A Guided confirmation card for a destructive or execution call says only "Confirm"

`pypsa-gui/frontend/src/components/ConfirmationCard.tsx`, `guidedConfirmWording` (about l.62–67). For any tier other than `write` it returns `{ header: 'Confirm', note: null }`, so a Guided card that deletes or runs something opens with a bare "Confirm".

- Give `destructive` and `execution` tiers a header that says what happens, the way `GUIDED_WRITE_PURPOSE` (about l.42–60) does for writes:
  - destructive: "Confirm: delete or replace something". Where the card already names its target (the "P26 gate B2" block, about l.131), use it: "Confirm: delete Load L_hot".
  - execution: "Confirm: run this".
- Keep the "Details" disclosure open by default on destructive cards (about l.413–416). Gate P26 B2 deliberately shows the raw call there, so it must not change.
- Expert never reads this wording. Do not change Expert.
- Test in the card's existing test file (`ConfirmationCard*.test.tsx`): one destructive and one execution case on the header text, and one asserting Details is still open on a destructive card.

## Done

- One PR from `outsource/ui-wording-pack-2`, body starting `Outsourced task P`, listing each item with before/after text and a screenshot of each changed state in Guided and Expert.
- `pixi run gui-tests` (or the frontend vitest and backend pytest for the touched files) green. The repo's CI is green.
- A release-note fragment under `doc/release-notes.d/`.
- Stay out of every file not named above. If an item turns out to need another file, say so in the PR body instead of widening the change.
