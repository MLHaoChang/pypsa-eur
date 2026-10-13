# QA gate: P31 — Cosmetics (C2, C3, C4, C5)

**Date:** 2026-10-05. **Reviewer:** independent QA gate (did not write the code).
**Scope:** `git diff 4d37e92c5..d5d38f7b0` on `claude/epic-allen-k2t1c4` (8 commits). The backend has not changed since `1a2b91c75`, the commit the implementer's row-1 log ran on. `git diff 1a2b91c75 d5d38f7b0` touches only the plan.
**Contract:** deferred spec `2026-09-28-guided-mode-deferred.md`, §0.2 and §6 (P31), plus the plan's "P31 phase note" (4 deviations, probe list).
**Scratch:** `/tmp/claude-0/-home-user-pypsa-eur/93e65137-63f8-5e19-b114-b55788c10af8/scratchpad/qa31/` (written `qa31/` below).
- No repo source or test was edited.
- Mutations ran in a scratch worktree `qa31/wt` (HEAD, `node_modules` symlinked). It is removed now.
- My own live C2 probes (`qa31/probe-c2*.mjs`) started their own uvicorn and Vite, one at a time, between the smoke runs. No process is left running.

## Verdict: **GO** (no blocker; one should-fix, S-1)

Every gate row is green:
- Row 2: 784 passed, 17 skipped.
- Row 3: tsc clean.
- Row 4: 245 files, 2836 passed.
- Row 5: P31 PASS; P31 `--self-test` fails as expected (exit 1); P26 regression PASS.
- Row 7: 0 `uiMode` lines. Expert snapshots, prompt and schema are untouched.

Results by item:
- **C2** is correct live. Toast ∩ Send = ∅ on `/app` and `/projects` at 1440, 1280, 1024 and 800 px, with the dock open, open and widened, and closed. The C2 mutant (toaster ignores the dock) makes the smoke FAIL with 1285 px² overlap.
- **C3:** every `fmtEnergy` caller now renders a true unit. The ΔEUE header and its cells agree. Exports are unchanged.
- **C4:** the counter is real. My mutant (counter disabled) makes `--self-test` PASS, and the real script FAILs. However, the normal run's window is empty (0 requests), see N-3.
- **C5:** every 200 / 204 sentence of the docstring is true against `routers/results.py:1534-1557` and `eh_review.review_latest` / `eh_study_runner.start_eh_study`. The sentence the 204 case rests on ("a study clears the stored report when it starts") is **not pinned by any test**, although the docstring says "Pinned by `tests/test_eh_review_route.py`". A mutant that drops the clear survives all 45 EH test files. This is S-1.

## Gate rows

| # | Command (cwd) | Result |
|---|---|---|
| 1 | not rerun. The implementer's `scratchpad/p31/row1.log` ran on `1a2b91c75` and says `6738 passed, 31 skipped, 11 deselected`. `git diff 1a2b91c75 HEAD -- pypsa-gui/backend` is empty. `pytest tests/ -m "not slow" --collect-only` on HEAD (`pypsa-gui/backend`) gives `6769/6780 tests collected (11 deselected)`, and 6738 + 31 = 6769 | accepted (log usable, counts match) |
| 2 | `PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest <14-file set> tests/test_validation*.py tests/test_local_settings*.py tests/test_energy_hub_templates.py -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`, HEAD) | **784 passed, 17 skipped** (`qa31/row2.log`; P30 had 780, plus the 4 C5 tests). Includes `test_guided_mode_prompt.py` and `test_eh_review_route.py` |
| 3 | `npx tsc --noEmit -p .` (`pypsa-gui/frontend`) | **0 errors** (`qa31/row3.log`) |
| 4 | `npx vitest run` (`pypsa-gui/frontend`) | **245 files / 2836 passed** (`qa31/row4.log`) |
| 4s | not required: no store, polling or chat change. `ToasterHost` only reads the store | — |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P31 --out qa31/smoke-P31` (`pypsa-gui/frontend`) | **PASS, 43 screenshots, exit 0** (`qa31/smoke-P31.log`). Toast ∩ Send = ∅ over 141 / 142 / 151 shared frames (72 / 74 / 86 with the whole toast on screen). B4: "0 eh_study request(s) seen, 0 with status ≥ 500" |
| 5 | same, `--phase P31 --self-test --out qa31/smoke-P31-selftest` | **expected FAIL, exit 1**: `FAIL — assertion failed: no failing eh_study response after recovery (1 eh_study request(s) seen, 1 with status ≥ 500: 500)`. Every P26 and toast step before it passed |
| 5 | same, `--phase P26 --out qa31/smoke-P26` (whole-flow regression before the PR) | **PASS, 38 screenshots, exit 0** (`qa31/smoke-P26.log`) |
| 7 | `git diff 4d37e92c5 -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` → **0** | No `uiMode` branch was added. The behaviour changes that reach Expert follow from the items themselves, and the spec scopes none of them to Guided: the toast position, `fmtEnergy` below 1 MWh, and the ΔEUE column. **Signed.** |
| E | Expert unchanged | No `*.expertUnchanged*` test or snapshot is in the diff, and they pass in row 4. There is no change to `chat_tools_schema`, the prompt or tool descriptions, and `test_guided_mode_prompt.py` is green in row 2 |

**Scope check (`git diff 4d37e92c5..HEAD --stat`):** 11 files, all in P31's scope:
- `results.py` (docstring only);
- `test_eh_review_route.py`;
- `smoke-guided.mjs`;
- `ToasterHost.tsx` and its test;
- `main.tsx`;
- `shared.tsx` and its test;
- `EhReferenceDesignPanel.tsx` and its formatting test;
- the plan.

Nothing outside P31's scope.

## Spec contract, item by item

| Item | Contract | Code | Live / evidence |
|---|---|---|---|
| C2 | toast clears Send while the dock is open; follows the store | `components/ToasterHost.tsx:23-41`; `main.tsx:47` | smoke: 0 px² on 3 templates (`/projects` → `/app`). My probe (`qa31/probe/probe.log`, screenshots `toast-*.png`): 1440 / 1280 / 1024 / 800 × {open, widened +250 px, closed} → ∩ Send = 0 every time. Dock closed → `right: 16` (original corner, `toast-1440x900-closed.png`). `/projects` at 1440 and 800 → 0 (`qa31/probe2/probe2.log`) |
| C2 (dev. 1) | spec said `bottom: DOCK_HEIGHT + 16` | right inset = `assistantDockWidth + 16` on `/app`, `/projects` | **Accepted.** The dock is a right-hand column (`AssistantDock.tsx:73-78`, `shrink-0`, `width` from the store), so a bottom offset would not clear Send. The C2 mutant (`right = 16` always) fails the smoke with 1285 px² overlap |
| C3 | `< 1 MWh` → `toFixed(3) MWh`; `0` → `0 MWh`; ΔEUE header gets `(MWh)` | `shared.tsx:743-764`, `EhReferenceDesignPanel.tsx:1887,1920-1923` | all `fmtEnergy` callers checked (`AggregatedOverview`, `Dispatch`, `CapacityExpansion`, `Economics`, `EhReferenceDesignPanel`): KPI values, table cells, axis ticks and tooltips all carry their unit in the string, so each one is true. The local `fmtEnergy` copies (`Curtailment.tsx:216`, `LostLoadTab.tsx:364`, `TimeSeriesManager.tsx:156`) are separate and untouched. Hub cards and chat do not use `fmtEnergy` (chat tables are model-written text). No `kWh` is left in non-test `src` except comments and the H2 LHV note. No export path changed: the CSV / JSON builders take raw values (`EhReferenceDesignPanel.tsx:549` `m.delta_eue_mwh ?? ''`), and the diff touches no export code |
| C3 (dev. 2) | ΔEUE cells are bare MWh numbers (`fmtMwhNumber`) | `shared.tsx:755-764` | **Accepted.** A `(MWh)` header above `fmtEnergy`'s `315.61 GWh` would be false. The cell is now `315,605.35` |
| C4 | count request + response with status ≥ 500 after recovery; `--self-test` must FAIL / exit 1 | `smoke-guided.mjs` `b4StudyReadFailure` | real FAILs (row 5). **My mutant** (the response listener never pushes) with `--self-test` → **PASS, exit 0**, "1 eh_study request(s) seen, 0 with status ≥ 500" (`qa31/smoke-mutC4.log`). So the self-test separates a counting check from a vacuous one |
| C4 (dev. 3) | the self-test's failing read is a page `fetch` | — | **Accepted.** While no study runs the app sends no `eh_study` read after recovery (row 5 says 0 requests), so a route alone would PASS vacuously. See N-3 |
| C5 | 204 only with no stored report and no record with a report; worker raised → 204; aborted → 200 `ok` | `routers/results.py:1541-1551` | checked against the code: `review_latest` (`eh_review.py:440-468`): `running` → 200; otherwise the stored report, falling back to `record.report` (`stale: true`); `no_data` → 204. The runner's `except` sets `report=None` (`eh_study_runner.py:404-411`). `run_eh_study` pops `eh_reference_design_report` (`eh_study.py:1180-1182`) before any step that can raise, and stores the report last (`:1363-1364`). An aborted run or a stage marked `failed` keeps a report, so it returns 200. `mc_certify` runs before `fmea_top` and the later stages (`models/energy_hub.py:44-55`), so "verdict null unless MC finished" is true. **All sentences true.** |
| C5 (dev. 4) | spec text "aborted → verdict null" replaced | — | **Accepted.** The spec text is false for an abort after `mc_certify`, and `test_an_aborted_study_keeps_a_verdict_mc_already_reached` shows it |

### Probe: an exception before `run_eh_study` clears the stored report

The steps before the pop at `eh_study.py:1172-1179` are:
- `validate_stages(stages)`: the same list was already validated at request time (`eh_study_runner.py:282-287`);
- `default_stages_for(pack)`: the pack is built and validated at request time (`:299-312`);
- `int(budget_solves)`: already an int, checked against its 1…max range (`:268-279`).

None of these can raise for a request the route accepted, so the earlier-report case cannot be reached today. The other side is also safe. `store_eh_report` is the last statement, and the worker's later `model_dump` is the same call that already succeeded inside `store_eh_report`. So a report stored with the record left at `failed` / `report: None` cannot be reached either. Recorded as N-5. The docstring holds, but see S-1: nothing pins it.

## Findings

### Should-fix

**S-1: The C5 docstring's 204 premise is unpinned, but the docstring and the phase note say "pinned".**
- **Where:** `pypsa-gui/backend/routers/results.py:1547-1551` ("a study clears the stored report when it starts … Pinned by `tests/test_eh_review_route.py`"); the phase note says "Each sentence is pinned by a test".
- **Gap:** `test_a_study_whose_worker_raised_is_204` (`tests/test_eh_review_route.py:184-191`) monkeypatches `run_eh_study` away and starts with an empty store. So it never checks that a *real* study clears an *earlier* stored report.
- **Repro (mutant):** in `services/adequacy/eh_study.py:1181`, change `for key in SIBLING_STORE_KEYS + (report_mod.EH_REPORT_STORE_KEY,):` to `for key in SIBLING_STORE_KEYS:`. Then run every `tests/test_*.py` that mentions `eh_study|eh_review|eh_reference_design` (45 files, `-m "not slow"`): **851 passed, 17 skipped. The mutant survives.**
- **Effect of that regression:** after a successful study, a re-run whose worker raises would serve the *previous* study's report as `200 ok, stale: false`. That is the false "ok" the docstring rules out.
- **Fix:** add one route test. Store a report (or run a fake study that stores one). Then start a study with the real `run_eh_study`, made to raise *after* the pop (for example, monkeypatch `services.adequacy.redundancy._detach_solver_model` to raise). Assert `GET /api/results/eh_review` → 204.

### Notes

- **N-1 (C3): a tiny non-zero energy reads `0.000 MWh`, and a tiny negative one reads `-0.000 MWh`.**
  - Below 0.0005 MWh, `fmtEnergy` (`shared.tsx:752`) shows `0.000 MWh`; `fmtEnergy(-0.0004)` gives `-0.000 MWh`.
  - The zero threshold moved from 5e-6 MWh (old `0.00 kWh`) to 5e-4 MWh.
  - Example: `Dispatch.tsx:2556` shows lost load only when `> 1e-6`, so a value of 1e-5 MWh renders as `0.000 MWh` next to a real lost-load event.
  - This follows the spec (`toFixed(3)`). On the MW-scale templates such values are solver noise, so it is not misleading in practice.
  - Recommended follow-up (not required for this gate): `0 < |x| < 0.0005` → `< 0.001 MWh`, and treat `-0.000` as `0 MWh`.
- **N-2 (C2): the toast now covers part of the main area while it is shown.**
  - At 1440 it sits over the bottom ~60–80 px of the Properties panel (`qa31/probe4/form-1440x900.png`: an open "Add Generator" form's lower fields) and over the last data-table rows.
  - At 800 it sits over the sidebar's Zoom buttons.
  - Before P31 it covered the dock composer and Send. With the dock closed it still covers the Properties panel's bottom corner, as before.
  - It is transient (~2–4 s), and no form footer (Create / Cancel, `CreationForm.tsx:715`) was covered in the observed layouts, because that footer is inside the scroll area.
  - Acceptable.
- **N-3 (C4): the normal run's after-recovery window sees 0 `eh_study` requests.**
  - The counter is real (the self-test plus my mutant prove it), but in a normal run it has nothing to count: the app does not read `eh_study` after recovery while no study runs.
  - So the normal-run check can only fail if the app starts re-reading. That is acceptable: the spec asks for a real counter and a self-test, and both exist.
  - The implementer disclosed this.
- **N-4 (C2, edge): with a dock wider than the window can fit, the toast is squeezed.**
  - The dock has no max width, and `constrainDockWidth` (`uiStore.ts:782`) is unused, so the rendered dock can overflow the viewport. This is pre-existing.
  - Example: 800 px window with the dock dragged to 671 px. The dock is laid out from x=380, and Send is off-screen at x=989.
  - The toaster's right inset (687 px) then leaves a 97 px column, and the toast wraps into a 100×296 px strip (`qa31/probe/toast-800x700-wide.png`).
  - Also: on `/app/` (trailing slash), `DOCK_ROUTES.has(pathname)` misses, and the toast returns to the corner.
  - Both are edge cases. A possible follow-up is to clamp the inset to `min(dockWidth, innerWidth − 360)`.
- **N-5 (C5):** as in the probe section above, no exception path before the store clear is reachable for an accepted request.
- **N-6 (tests): two of my mutants survive.**
  - F2: `fmtMwhNumber`'s `< 1` branch ignores negatives. A negative ΔEUE does not occur.
  - F7: a missing ΔEUE renders `'0'` instead of `—`, which would break the "no 0 for a missing number" rule. Nothing pins the missing-value cell (`EhReferenceDesignPanel.tsx:1923`).
  - Cheap to pin; not required.

## Mutation table (mine)

| # | Mutant | Detector | Result |
|---|---|---|---|
| M-C4 | smoke: the response listener never records a ≥ 500 (counter disabled), run with `--self-test` | the self-test must not detect it | **PASS, exit 0** (the real script FAILs) → the counter is real |
| M-C2 | `ToasterHost`: `right = TOAST_GAP` always | smoke `--phase P31` | **KILLED**, FAIL with 1285 px² overlap on the first template |
| M-C5 | `run_eh_study` does not pop `eh_reference_design_report` at start | 45 EH test files (851 tests) | **SURVIVED** → S-1 |
| F2 | `fmtMwhNumber`: `< 1` branch only for positive values | `shared.test.ts`, `EhReferenceDesignPanel.formatting.test.tsx`, `ToasterHost.test.tsx` | SURVIVED (N-6) |
| F7 | missing ΔEUE cell renders `'0'` | same | SURVIVED (N-6) |
| F8 | `fmtMwhNumber` without grouping (`toFixed(2)`) | same | KILLED |
| F9 | toaster inset uses a fixed 420, not the store width | same | KILLED |
| F10 | `fmtEnergy` below 1 MWh drops the sign (`Math.abs`) | same | KILLED |

My mutants: 5 of 8 killed, plus the C4 control behaving as designed. The implementer's 13/13 FE and 5/5 BE mutants are not re-run. Their logs are in `scratchpad/p31/`.

## Deviations

| # | Deviation | Ruling |
|---|---|---|
| 1 | C2 uses a right offset (dock width + 16) instead of `bottom: DOCK_HEIGHT + 16`, and includes `/projects` | accepted: the dock is a column. Confirmed live and by M-C2 |
| 2 | C3 ΔEUE cells use `fmtMwhNumber` (bare MWh), not `fmtEnergy` | accepted: this is the only way the `(MWh)` header is true for every cell |
| 3 | C4 self-test drives its failing read with a page `fetch` | accepted: the app makes no read in the window (N-3) |
| 4 | C5 docstring wording differs from the spec's proposed text | accepted: the spec text is false for an abort after `mc_certify`, and for a stage-failed study. The new text is true (S-1 is about pinning, not truth) |
