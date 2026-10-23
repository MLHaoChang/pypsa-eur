**Start from `master` after task G-a (`outsource/profile-tuning-backend`) has merged; this task needs its routes.**

<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Status: ready-for-agent once G-a has merged (split from brief G on 2026-10-23 by owner decision; written against `origin/master` `bcd81b268`, plan-reviewed by the gatekeeper)

# Outsourced task G-b: tune a wind or solar profile, and compare it with what the generator has now (the GUI)

Branch: `outsource/profile-tuning-frontend` · one PR against `master` · PR body starts with `Outsourced task G-b`

This is the GUI half of a feature whose backend (task G-a) is already on `master`. **Change no backend file.** The task has a **core** you must finish and a **stretch** you do only if time is left. Every item below is marked. If you run short, open the PR with the core done and list the unfinished stretch items in the PR body.

## Read first

1. `AGENTS.md` (repo root). Release notes and PR rules are there.
2. The tickets: `.scratch/renewable-profiles/issues/05-tuning-panel.md` (RP-05), `07-before-after-compare.md` (RP-07), `12-representative-weeks.md` (RP-12). Background: `04-timeseries-manager-integration.md`, `06-calibration-engine.md`, and the spec `.scratch/renewable-profiles/spec.md`, requirements R4, R6, R8, R9 and decisions RP-Q3 = A, RP-Q4 = A, RP-Q5 = A.
3. Placement rules: `.scratch/project-workflow/design.md` §4.3 (l.324-339: RP-05 is "Expert depth, collapsed by default; Guided never shows it"; RP-07 is "inside the Renewables tab, no new destination") and §6 (l.392-415, the UI review checklist). UX rules: `docs/agents/review-ux.md`. Owner decision DENSITY-1 in `docs/agents/owner-decisions.md` (l.58): the 9–11px text and Inter are intentional. Style tokens: `pypsa-gui/frontend/src/index.css`.
4. Domain words: `pypsa-gui/CONTEXT.md` l.155-198 (**Coordinate profile**, **Profile source**, **Profile preset**).
5. Frontend code you build on (`pypsa-gui/frontend/src/`):
   - `pages/TimeSeriesManager.tsx`: `GenItem` l.725 and its per-row Generate button l.840-849; the editor-dialog state l.981-986 and its `Dialog` host l.2265-2306 (`EDITOR_TITLE` l.36); readiness query l.1043-1048; `generateRow`/`canGenerateRow` l.1052-1067; the Expert-only Profile Studio link l.1649-1655; the Renewables list l.1866-1910;
   - `components/series/renewableGeneration.ts` (`readyRows`, `waitForJob`, `generateAndApply`);
   - `components/series/WeatherBar.tsx`, `components/series/RenewableReadiness.tsx`;
   - `components/series/SeriesKit.tsx` l.235-273 (`weekDates`, `WeekChart`: the recharts week overlay to copy);
   - `api/profiles.ts` l.143-185 (`profilesApi`);
   - `pages/ProfileStudioSection.tsx` l.165-177, 251-280 (today's raw JSON overrides).
6. Test style models: `pages/TimeSeriesManager.renewables.test.tsx`, `components/series/WeatherBar.test.tsx`, `components/series/renewableGeneration.test.ts`.
7. Task G-a's code and PR body: the next section.

## What task G-a built (on `master` now; read it, do not change it)

G-a merged the backend this task builds on. Read its code and its PR body, which has example payloads, before you start:

- `GET /api/profiles/schema?technology=wind|solar&preset=<name>` (`routers/profiles.py`, `services/profile_gen/tuning.py`): the technology's fields, each with `key`, `label`, `unit`, `type`, `min`/`max` or `options`, `sources` (which weather sources use it), `preset_value` and `provenance`.
  - wind: `hub_height`, `cut_out` (ERA5 only), `derate`, `losses` (both sources);
  - solar: `tilt`, `azimuth`, `tracking`, `inverter_efficiency` (ERA5 only), `losses` (both sources).
- Tuning travels as flat keys in the existing `params` of `/profiles/generate` and `/api/profiles/estimate`. An out-of-range or wrong-type key is 422 `invalid_params`, with a message that names the key, the value and the bounds. ERA5-only keys on the synthetic source are accepted and listed in the job meta as `tuning_not_applied`.
- `GET /api/projects/{name}/profiles/compare?a=…&b=…[&component=…]` (`services/profile_gen/compare.py`). A side is `current:<generator>`, `pending:<job>[:<point>]` or `shaped:<job>[:<point>]`. The payload has, per side, `cf`, `flh`, `p10`/`p50`/`p90`, `peak`, `peak_hour`, `monthly` and a 100-point `duration`; a `delta` (b − a) for every scalar and month; and a `week` block with its snapshots, its `period` on a multi-period model, and both sides' values. Every number is computed by the server; the GUI only displays them. Refusals: `no_current_series` (the generator has no series), `invalid_compare_side`, `not_a_shaped_profile`, and the apply refusals.
- `GET /profiles/weather` carries `window`: `null` for one full calendar year, `{first, last, hours, runs}` for a sampled model, or `{unsupported: <message>}` when the snapshots cannot take an hourly profile.
- The assistant's `compare_profiles` tool reads the same route.

If a payload is not what this brief expects, say so in the PR body; do not change the backend.

## What is already done (do not rebuild)

Checked against `origin/master` `bcd81b268`:

- **RP-04 is done apart from "Upload instead".** #616 (readiness badges, missing-location banner), #623 (weather bar, per-row Generate, wind/solar in "Generate defaults for all"), #624 (Profile Studio Expert-only).
  - "Set coordinates" exists as **"Set the bus coordinates ›"** (`RenewableReadiness.tsx` l.64-68). It opens the bus in Properties, in place in Guided (`openBusCoordinates` l.44-48). This replaced the ticket's inline x/y edit on purpose: the file header says fixing goes where the value lives. Do not add an inline editor.
  - `time_basis` is settled by #623: the weather bar shows the basis read-only with "Edit in Connection & grid ›" (`WeatherBar.tsx` l.54-60). Apply resolves it from the project's commercial timezone (`apply.py` l.222-252, the gatekeeper's option C). No "snapshots are local time in …" picker is to be built (see "Decisions already made").
  - Still missing: the banner's **"Upload instead"** action. It is stretch item S3.
- **RP-03** (#606) and **RP-09** (#605) are done. The month-day-hour slice handles any hourly window: `apply.py::_hourly_window` l.303-337, tested by `tests/test_profile_gen_snapshot_window.py` (a study week l.103, two weighted weeks l.128, per-period windows l.217) and `test_profile_gen_apply.py::test_a_template_week_takes_an_hourly_profile_and_refuses_a_daily_one` l.572. The daily/weekly refusal already names the fix (`apply.py` l.293-299).
- **RP-06** (#432) is done: `shape.py` and `POST /profiles/shape`. There is no GUI for shaping and no `shape_generated_profile` chat tool; both are RP-08, not this task.
- **What RP-12 still lacks:**
  - no test builds a real Energy Hub template and applies a generated profile to it. The tests above hand-build a week;
  - no GUI words tell the user that a sampled model takes those dates from the weather year.

  Two facts make the template test harder than the ticket says:
  - every Energy Hub template puts its buses at 0/0, which reads as "no location": `eh_templates.py` l.132-134, 662-664, 776-778;
  - their wind/solar generators carry the network's own `p_max_pu`, so readiness says `network_native`, and "Generate defaults for all" and per-row Generate leave them alone (`renewableGeneration.ts::readyRows` l.20-23).

  So the test must place the bus and apply explicitly; see "Decisions already made"; task G-a's T-R1 test applies a profile to a template through the routes.
- **Not done at all:** RP-05 (no schema route; the synthetic source ignores losses and derate; tuning is a raw JSON textarea in Profile Studio only) and RP-07 (no compare route, no `compare_profiles` tool, no compare view). The apply dry run already returns a one-week old → new preview (`_preview_week`), which you will not change.

This list was written before task G-a. G-a has since built the backend parts of RP-05, RP-07 and RP-12 (the previous section); what is left of them is the GUI, which is this task.

## Goal in plain words

An Expert user on Time Series › Renewables can open **Tune…** on a wind or solar generator. The panel lists that technology's settings, each with its preset value, unit, bounds and source. The user changes a few and generates. Before anything is written, a **compare** view shows the new profile against what the generator carries now: both lines over the same week, both duration curves, and full-load hours, capacity factor and quantiles with their differences, all computed by the server. Then the user applies it or closes the panel. The assistant gets the same comparison as a read-only tool, `compare_profiles`.

Separately, the RP-12 remainder: a test proves that an Energy Hub template's representative week takes the weather year's own dates, and the weather bar says so in words.

## User-facing outcome

- **Expert, Renewables tab:** each wind/solar row that has a location gets a **Tune…** link next to Generate (`TimeSeriesManager.tsx` l.840-849). It opens a dialog, "Tune the profile of <name>", hosted like the series editors (l.2265-2306). Guided never shows Tune (design §4.3).
- **The dialog:**
  - a preset select, listing the presets whose `source_type` matches the weather bar's source;
  - the technology's fields, each with its unit, preset value and provenance (a tooltip or a muted line). A changed field is marked, and a "Reset to preset" link restores every field;
  - fields that only the ERA5 source uses are shown disabled with the visible reason "ERA5 only" while the weather bar says Synthetic;
  - "Advanced" (collapsed) holds the JSON overrides, as Profile Studio has them today;
  - **Generate**. It queues one job with the weather bar's source and year and only the changed fields as `params`, waits for it as `waitForJob` does, and does **not** apply.
- **When the job is done, the dialog shows the compare view:**
  - two lines over the peak week: the current series faint, the new one in accent, as `WeekChart` draws them;
  - the two duration curves;
  - a small table of FLH, CF, peak, peak hour, P10/P50/P90, each with a Δ column;
  - the warnings the route returns (for example the assumed time basis);
  - **Apply** writes the profile with the single apply route (one undo step) and closes the dialog with the usual notice ("… applied. Undo (Ctrl+Z) reverts it."). **Close** writes nothing.
- When the generator has no series now (`profile: none`), the compare view shows the new profile alone and says "No current profile to compare with".
- **Weather bar:** when the model's snapshots are a sampled window rather than one full calendar year, the bar adds one line, e.g. "Your model covers 1–7 Jul (168 h): a generated profile takes these dates from 2025." For two sampled weeks it says "2 sampled periods" instead of a date range.
- Per-row Generate, "Generate defaults for all" and Profile Studio behave exactly as today.
- Copy follows the glossary: "profile", "preset", "weather year", "full-load hours". No ticket ids or code names in the UI. Text sizes 9–11px as the neighbours use (DENSITY-1).

## Files to touch

### Core

**Frontend** (`pypsa-gui/frontend/src/`)

- `api/profiles.ts`:
  - types `TuningField`, `TuningSchema`, `CompareSide`, `ProfileCompare`;
  - `profilesApi.schema(technology, preset)`, `profilesApi.compare(project, a, b, component?)`;
  - `apply` already exists (l.180-184). Add `time_basis` to nothing.
  - `WeatherChoice` gains `window: { first: string; last: string; hours: number; runs: number } | { unsupported: string } | null`.
- `components/series/renewableGeneration.ts`: split `generateAndApply` into `generatePending(project, technology, generators, opts?: { preset?: string; params?: Record<string, unknown> })` (generate + `waitForJob`, returns the job id) and the existing apply step. `generateAndApply` keeps its signature and behaviour; its test must pass unchanged.
- `components/series/RenewableTuner.tsx` (new): the dialog body.
  - Props: `project`, `row: ReadinessRow`, `onApplied`.
  - It reads the schema with react-query (key under `nk(project, 'profile_schema', tech, preset)`) and the weather choice with `weatherKey(project)`.
  - It holds the edited values, and the "changed" set is computed against `preset_value`.
  - Generate sends `params` with only the changed keys, plus the parsed Advanced JSON merged under them.
  - It then renders `ProfileCompare` for `a = current:<g>` (omitted when `row.profile === 'none'`) and `b = pending:<job>:<g>`, with Apply calling `profilesApi.apply(project, job, g)`.
  - It shows a refusal in place with `errorText`, as `SeriesKit.tsx`'s `PendingCard` does (l.357).
- `components/series/ProfileCompare.tsx` (new):
  - the week overlay, copied from `WeekChart` (two `Line`s; current faint `strokeOpacity 0.3`, new `var(--color-accent)`);
  - a duration-curve `LineChart` (x = share of hours 0–100 %, y = per-unit);
  - the stats table with Δ;
  - a `figcaption` and a `sr-only` text alternative as `WeekChart` has (`SeriesKit.tsx` l.246-272). Follow the dataviz skill for axis labels and the legend.

  No new colours: use the tokens in `index.css`.
- `pages/TimeSeriesManager.tsx`:
  - extend the `seriesEditor` union (l.981-986) with `{ family: 'tune'; component: string }`, `EDITOR_TITLE` with `tune: 'Tune the profile'`, and render `RenewableTuner` in the existing `Dialog` host;
  - add an optional `onTune` prop to `GenItem`, rendered next to Generate as "Tune…" (`data-testid="tune-<generator>"`) whenever it is passed. `GenItem` has no `uiMode`; the parent passes `onTune` only when `uiMode === 'expert'` (l.976) and the readiness row has a location (`coord_source !== 'missing'`);
  - change nothing else in the file.
- `components/series/WeatherBar.tsx`: the window line under the time-basis line (l.54-60), with `data-testid="weather-window"`. Reuse `weekDates`-style formatting from `SeriesKit.tsx` (UTC, `en-GB`).

**Docs**

- `doc/release-notes.d/outsource-profile-tuning-frontend.md`: the fragment below.

### Stretch (only after the core is green)

- **S2. Compare in Profile Studio.** Each `PendingCard` (`pages/ProfileStudioSection.tsx` l.61-120, its own local `PendingCard`, not `SeriesKit.tsx`'s) gets "Compare with current ›", which opens `ProfileCompare` for `current:<component>` vs `pending:<job>:<component>` in a `Dialog`.
- **S3. "Upload instead" in the missing-location banner** (RP-04's last item). Next to "Set the bus coordinates ›" (`RenewableReadiness.tsx` l.64-68), add "or upload a profile ›". It selects that generator's row and opens its existing per-row upload picker (`GenItem`'s `onUpload` input). The banner then needs a callback prop from `TimeSeriesManager.tsx`.
- **S4. A scripted Playwright walk** for the screenshots (Chromium at `/opt/pw-browsers`, synthetic source), kept under `.scratch/renewable-profiles/evidence/` with the PNGs. Not a committed test suite: the frontend has none.

## Access and lock checks that must hold

- **Generate and apply keep their checks unchanged:** the Tune dialog uses `/profiles/generate` and the single `/profiles/apply` route as they are (lock, undo snapshot, foreground check, moved-bus warning). Apply is one undo step.
- **Compare writes nothing**, so the dialog may call it as often as it needs.

## Tests

Every new test must fail on `master`; say in the PR body which line fails there.

### Frontend (vitest)

1. **F1** `components/series/RenewableTuner.test.tsx` (new). Mock `profilesApi` as `TimeSeriesManager.renewables.test.tsx` l.26-30 does:
   - the solar schema renders `tilt` with its unit and preset value;
   - typing a new tilt marks the field changed (`data-testid="tune-changed-tilt"`), and "Reset to preset" clears it;
   - Generate calls `profilesApi.generate` with `params` equal to `{ tilt: 20 }` only;
   - with the weather source `synthetic`, `inverter_efficiency` is disabled and shows "ERA5 only";
   - after `pending` returns the job, `compare` is called with `current:<g>` and `pending:<job>:<g>`, and Apply calls `profilesApi.apply(project, job, g)`.
2. **F2** `components/series/ProfileCompare.test.tsx` (new): from a fixed payload, the FLH cell shows the route's number and its Δ; the week and duration charts render (`data-testid`s); the text alternative names both FLH values; with no `a` side it says "No current profile to compare with".
3. **F3** `pages/TimeSeriesManager.renewables.test.tsx`, added cases:
   - in Expert, `tune-pv_ready` is present and `tune-pv_noloc` is absent;
   - in Guided, no `tune-*` link exists;
   - clicking Tune opens the dialog titled "Tune the profile of pv_ready".
4. **F4** `components/series/WeatherBar.test.tsx`, added cases: a one-run window renders "1–7 Jul" and "168 h"; a two-run window renders "2 sampled periods"; `window: null` renders no `weather-window`.
5. **F5** `components/series/renewableGeneration.test.ts`, added case: `generatePending(..., { params: { losses: 0.1 } })` posts them, and `generateAndApply` still posts no `params` (its existing cases pass unchanged).

### Commands

From the repo root; install once with `pixi install --frozen -e default -e test`:

```bash
cd pypsa-gui/frontend && npx vitest run src/components/series src/pages/TimeSeriesManager.renewables.test.tsx src/pages/TimeSeriesManager.weather.test.tsx src/pages/ProfileStudioSection.test.tsx && npx vitest run && npx tsc -b
pixi run gui-tests tests/test_profile_tuning.py tests/test_profile_compare.py   # G-a's tests: they must still pass, since you change no backend
```

For the screenshots, run the app against the synthetic source; never call ERA5 or CDS.

## Out of scope / don't touch

- **Every backend file** (`pypsa-gui/backend/`), `pypsa-gui/tool-error-kinds.json`, `.scratch/live-voice/capability-inventory.json` and `pypsa-gui/CONTEXT.md`. Task G-a owns them, and it has merged.
- **Owned by the parallel Codex tasks D, E and F:**
  - `services/profile_shapes.py` and its `_gen_category` (F);
  - `find_capabilities` in the harness (E), and any new `services/not_via_chat.py` (E);
  - `routers/network_library_reference.py` and every library_reference route (D);
- `harness/workflows/renewable-profile.md`, `shape_generated_profile`, batch-apply chat arguments, Guided offers (RP-08, RP-10).
- **Generation flows that stay as they are:** "Generate defaults for all", per-row Generate and `DefaultsReview` behave as today. Tuning is per run and not saved per generator.
- `capability-inventory.json` by hand, `doc/release_notes.md`, `docs/agents/*`, `scripts/coord/*`.
- Campus files: `routers/campus_electrical.py`, `services/campus_*.py`, `frontend/src/**/campusElectrical*`, `CampusElectricalPanel*`.
- Profile Studio, except stretch S2. Properties panels and `useSeriesFields.ts`.
- No GUI for shaping.
- No new colours, fonts or text sizes. The `ui-ux-pro-max` skill is for lookups only: never run `--design-system` or `--persist`.

## Repo rules

- Branch `outsource/profile-tuning-frontend` from current `master`, after G-a has merged. Open exactly one PR. Never force-push. Do not merge it; our coordinator merges.
- PR body:
  - starts with the line `Outsourced task G-b`, then follows `.github/pull_request_template.md`. Mark it as AI-generated;
  - says which stretch items are done;
  - says which line of each new test fails on `master`;
  - lists the commands you ran.
- **Screenshots for the UX review** (`docs/agents/review-ux.md`: "screenshots of each state, in Guided and Expert"), synthetic source, at 1440×900. Save them under `.scratch/renewable-profiles/evidence/` with a `g-` prefix, commit them, and link them in the PR body:
  1. the Renewables tab in Expert with the Tune… links;
  2. the same tab in Guided, with no Tune;
  3. the Tune dialog untouched;
  4. the Tune dialog with a changed, marked field;
  5. the "ERA5 only" disabled state;
  6. the compare view (overlay, duration curve, stats with Δ);
  7. the compare view with no current profile;
  8. the receipt after Apply;
  9. the weather bar on an Energy Hub template showing the window line.
- Add one release-note fragment, `doc/release-notes.d/outsource-profile-tuning-frontend.md`:

  ```
  * Feat(pypsa-gui): Time Series › Renewables gets a Tune… panel (Expert) for a wind or solar generator: the preset's settings with their units, bounds and sources, changed fields marked, and a compare view that shows the new profile against the current one (peak week, duration curve, full-load hours and quantiles with their differences) before Apply. The weather bar names the dates a sampled model (an Energy Hub week) takes from the weather year.
  ```

  Never edit `doc/release_notes.md`.
- Tests and docs go in the same PR. Do not skip, loosen or delete any existing test.
- Do not commit credentials or `.env` files. Make no paid model calls, and no ERA5/CDS downloads.

## Done when

Core:

- [ ] Expert rows have Tune…; Guided has none. The dialog marks changed fields, resets, generates with only the changed fields, shows the compare and applies in one undo step.
- [ ] The compare view shows the week overlay, the duration curves and the stats with Δ from the route's numbers, and says "No current profile to compare with" when there is none.
- [ ] The weather bar shows the window line on a sampled model and nothing new on a full year.
- [ ] F1–F5 pass, and each new test fails on `master`. `generateAndApply`'s existing tests pass unchanged.
- [ ] The full `npx vitest run` and `npx tsc -b` are green, or the PR lists each failure that also fails on `master`.
- [ ] The release-note fragment is present. The screenshots are committed and linked.
- [ ] The diff touches only the files listed under "Files to touch" (plus the evidence PNGs).
- [ ] One PR on `outsource/profile-tuning-frontend`, with a body starting `Outsourced task G-b`.

Stretch, each only if done: S2 Profile Studio compare, S3 "Upload instead", S4 Playwright walk.

## Decisions already made

Settled in the plan review; do not reopen them in the PR.

- **Where Tune lives:** an Expert-only per-row "Tune…" that opens a dialog in the Time Series Manager's existing editor host, not a panel at the top of the tab. Design §4.3: Guided never shows it. The dialog's "Advanced" JSON repeats Profile Studio's overrides box on purpose; keep both.
- **Tune does not auto-apply:** Tune → Generate → compare → Apply, so the before/after comes first. Per-row Generate keeps its one-click generate-and-apply.
- **Bounds and units come from the schema route**; never hard-code them in the frontend.
- **ERA5-only fields** are shown disabled with "ERA5 only" while the weather bar says Synthetic. The backend accepts them and records them as not applied, so nothing changes silently either way.
- **Tuned settings are not remembered per generator.** Regenerate and "Generate defaults for all" use the preset again.
- **Energy Hub templates:** their renewables carry the network's own series and sit on 0/0 buses, so they show "No location" and get no Tune link. Adding a per-row "Replace with a weather profile" for them is a separate follow-up; do not add it here.
- **RP-04's "Snapshots are local time in …" item is done as built:** the time basis is read-only in the weather bar and set in Connection & grid. Build no local-time picker. "Set coordinates" is done as "Set the bus coordinates ›", by design.
- **"Upload instead"** is RP-04's last missing item: stretch S3 here. If it is not done, say so in the PR body.
