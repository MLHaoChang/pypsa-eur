# Model Horizon Guided Steps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the Model Horizon page from one 1,476-line scroll into a summary-first guided-step flow across focused files, without changing what any control does.

**Architecture:** A safety net first: Task 1 builds render coverage against the CURRENT page, pinning the three defect fixes most exposed by a JSX rewrite. Only then does the restructure proceed — pure predicates, then a routing shell, then the steps move file by file, with Task 1's tests green at every commit. `ModelHorizon.tsx` ends as a ~200-line shell owning queries and mutations; each step is a presentational file under `pages/modelHorizon/`.

**Tech Stack:** React 18, TanStack Query, Zustand, Tailwind, vitest + @testing-library/react (jsdom).

## Global Constraints

- **Repo root:** `~/Desktop/Code Test/pypsa-eur`. Paths below are relative to it.
- **Branch:** work happens on a branch off `feature/local-app-impl`. **This checkout is shared** — another session commits to it continuously. Re-run `git branch --show-current` before EVERY commit; commit path-limited, never `git add -A`.
- **Every task is TDD.** Test first, run it, capture the real failing output, then implement. Each report carries **TDD Evidence** with the RED command and real output, then GREEN.
- **RED must be behavioural.** Every task here modifies or moves existing rendered behaviour, so an import error is not acceptable evidence — the failing assertion must show a wrong rendered value or a wrong call argument.
- **Frontend tests:** `cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run <file>`
- **Type check:** same prefix, `npx tsc --noEmit -p tsconfig.json`
- **Backend suite** (only if a task touches backend, which none should): `pixi run python -m pytest pypsa-gui/backend/tests -p no:warnings` — **no extra `-q`**; `pytest.ini` already sets `addopts = -q` and a second one suppresses the summary line.
- **Frontend baseline: 105 files, 878 tests passing.** Any failure is yours.
- **This plan must not change any control's behaviour, any API call, or any backend file.** A diff touching `pypsa-gui/backend/` is out of scope.
- Never hardcode an interpreter path.
- End every commit message with: `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## A note on how this plan specifies work

Tasks 3–6 largely **move existing JSX** from `ModelHorizon.tsx` into step files. This plan deliberately does **not** restate that JSX: the source is in the repo, restating it would be duplication that drifts, and a transcription error is caught by Task 1's render tests plus `tsc`. What this plan pins instead is the boundaries, the props interfaces, and the tests. Task 1 and Task 2 — which create genuinely new code — carry full code.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `pypsa-gui/frontend/src/pages/ModelHorizon.render.test.tsx` | **New.** Render coverage: the safety net. | 1 |
| `pypsa-gui/frontend/src/pages/modelHorizonModel.ts` | Existing pure derivations + new summary/visibility predicates | 2 |
| `pypsa-gui/frontend/src/pages/modelHorizonModel.test.ts` | Existing 27 unit tests + new predicate tests | 2 |
| `pypsa-gui/frontend/src/pages/modelHorizon/StepShell.tsx` | **New.** Numbered rail, step chrome, Advanced disclosure | 3 |
| `pypsa-gui/frontend/src/pages/modelHorizon/HorizonSummary.tsx` | **New.** Summary view | 3 |
| `pypsa-gui/frontend/src/pages/modelHorizon/StepWindow.tsx` `StepSampling.tsx` `StepWeights.tsx` | **New.** Timestep-level steps | 4 |
| `pypsa-gui/frontend/src/pages/modelHorizon/StepMode.tsx` `StepYears.tsx` `StepPeriodEconomics.tsx` | **New.** Period-level steps | 5 |
| `pypsa-gui/frontend/src/pages/ModelHorizon.tsx` | Shrinks to a shell: queries, mutations, routing | 3,4,5,6 |

---

### Task 1: The safety net — render coverage of the current page

**Files:**
- Create: `pypsa-gui/frontend/src/pages/ModelHorizon.render.test.tsx`

**Interfaces:**
- Consumes: nothing.
- Produces: a reusable render harness other tasks extend. Export nothing from the test file; later tasks add cases to it.

**Why this is first.** Six defect fixes landed on this page's JSX across two branches and that JSX has zero render coverage — the preceding branch's final review said so explicitly. Tasks 3–6 rewrite it. This task builds the net before the trapeze.

**The risk you are being asked to retire.** Rendering this page needs a `QueryClient`, the `uiStore` Zustand store with a current project, and mocks for `networkApi.getSnapshots`, `networkApi.getInvestmentPeriods`, `networkApi.getLoads`, and `simulationApi.getSolverConfig`. The preceding branch avoided render tests for exactly this reason. **If after a genuine attempt this proves impractical, STOP and report BLOCKED with what you found** — do not write a shallow test that renders nothing meaningful. A vacuous safety net is worse than an admitted absence, because Tasks 3–6 would then proceed believing they are covered.

Look at `pypsa-gui/frontend/src/components/ChatPanelSurfaces.test.tsx` and `src/layout/PropertiesPanel.rescale.test.tsx` for the harness patterns already used in this codebase — follow them rather than inventing one.

- [ ] **Step 1: Write the three render tests**

Create `ModelHorizon.render.test.tsx` covering exactly these three behaviours, each named for the defect it pins:

1. **Multi-period weight edit sends a period-qualified key.** Render with a mocked multi-period `getSnapshots` payload whose `weightings` rows carry `period` and `timestep` (as `df_to_json` emits for a MultiIndex). Change an objective input in the weightings table and blur it. Assert `networkApi.updateSnapshotWeightings` was called with an `updates` key of the form `"2030|2024-01-01T00:00:00"` — **not** a bare ISO. This pins defect B1, whose failure mode was silently writing to the wrong investment period.

2. **Resolution comes from the network.** Render with `getSnapshots` returning `freq: "3h"`. Assert the rendered resolution reads "3-hourly". Then render with `freq: null` and assert it reads "Irregular" and never "Hourly". This pins defect B3, whose failure mode was rendering a form field that was never seeded from the network.

3. **PV preview greys when auto-discount is inert.** Render multi-period with `auto_discount_periods: true` and assert the PV column is in the active style; render with it false and assert the muted style. This pins the Task-3 preview and its later gating fix.

Assert on user-visible text and on mock call arguments — not on class names where a text or role assertion will do, and never on component internals.

- [ ] **Step 2: Run them and confirm they PASS against the current page**

Run: `cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/pages/ModelHorizon.render.test.tsx`

Expected: **all three PASS.** This task is characterisation, not defect-fixing — the behaviours are already correct, and the tests exist to keep them correct through the restructure.

- [ ] **Step 3: Prove each test can fail (this is the RED for this task)**

Because the tests pass immediately, their value rests entirely on their ability to detect a regression. Prove it, one at a time, restoring after each:

- Test 1: in `modelHorizonModel.ts`, make `snapshotWeightKey` return the bare ISO on multi-period. Confirm test 1 fails and the others do not.
- Test 2: in `ModelHorizon.tsx`, make the resolution read a hardcoded `'h'`. Confirm test 2 fails.
- Test 3: make `autoDiscountOn` unconditionally `true`. Confirm test 3 fails.

Record each failure's real output. **After each, restore and verify with `git diff` that the file is clean.** An implementer earlier in this workstream was killed mid-mutation and left an inverted production line in the tree — revert, verify, then move on.

- [ ] **Step 4: Full suite and commit**

Run the full frontend suite (baseline 105 files / 878 tests) and `tsc --noEmit`. Then commit the new test file path-limited.

---

### Task 2: Pure predicates for summary and step visibility

**Files:**
- Modify: `pypsa-gui/frontend/src/pages/modelHorizonModel.ts`, `modelHorizonModel.test.ts`

**Interfaces:**
- Consumes: existing exports (`resolutionLabel`, `horizonRangeLabel`, `pvFactor`, …).
- Produces:
  - `type HorizonStepId = 'mode' | 'years' | 'economics' | 'window' | 'sampling' | 'weights'`
  - `visibleSteps(isMultiPeriod: boolean): HorizonStepId[]` — all six when multi, `['mode','window','sampling','weights']` when single
  - `isHorizonUnset(snapshotCount: number | undefined): boolean` — true when count is undefined or ≤ 1
  - `stepSummary(step: HorizonStepId, ctx: HorizonSummaryContext): string` — one sentence of current state
  - `interface HorizonSummaryContext` carrying only plain data the page already has: `isMultiPeriod`, `periods`, `snapshotCount`, `freq`, `rangeLabel`, `canSampleWeeks`, `weightsAreDefault`

All pure — no React, no network. The module's existing header states that rule; keep it true.

- [ ] **Step 1: Write the failing tests**

Cover: `visibleSteps` in both modes (and that period steps are absent in single); `isHorizonUnset` at the boundary (0, 1, 2, undefined — 1 is unset because it is PyPSA's default "now" snapshot); `stepSummary` for each of the six ids in multi-period and each of the four in single, asserting the sentence names the actual configured value rather than a placeholder.

- [ ] **Step 2: Run to verify they fail** — missing exports is acceptable RED *here only*, because these symbols are genuinely new. Every later task modifies existing behaviour and needs a behavioural RED.

- [ ] **Step 3: Implement the predicates** in `modelHorizonModel.ts`.

- [ ] **Step 4: GREEN, then a mutation check.** Make `visibleSteps` return all six regardless of mode; confirm the single-period test fails. Restore and verify with `git diff`.

- [ ] **Step 5: Full suite, tsc, commit.**

---

### Task 3: The shell — routing between summary and steps

**Files:**
- Create: `pages/modelHorizon/StepShell.tsx`, `pages/modelHorizon/HorizonSummary.tsx`
- Modify: `pages/ModelHorizon.tsx`, `ModelHorizon.render.test.tsx`

**Interfaces:**
- Consumes: Task 2's `visibleSteps`, `isHorizonUnset`, `stepSummary`, `HorizonStepId`.
- Produces:
  - `StepShell({ steps, current, onSelect, title, children, advanced })` — numbered rail, step chrome, and an `advanced` slot rendered inside a collapsed `<details>`-style disclosure
  - `HorizonSummary({ steps, ctx, onOpen })` — one clickable line per step

**No step content moves in this task.** The shell renders the summary or a step frame; the existing sections stay where they are, rendered inside the frame for whichever step is current. This keeps the diff reviewable and keeps Task 1's tests meaningful throughout.

- [ ] **Step 1: Extend the render test file** with: a project whose `snapshots.count` is 1 opens on step 1, not the summary; a configured project opens on the summary; clicking a summary line opens that step; single-period mode shows four rail entries, multi shows six.

- [ ] **Step 2: Run to verify they fail behaviourally** — the assertions must fail because the summary/rail is absent, not because an import is missing.

- [ ] **Step 3: Build `StepShell` and `HorizonSummary`, wire routing in `ModelHorizon.tsx`.** Existing section JSX moves inside the step frame unchanged.

- [ ] **Step 4: GREEN — including all three Task 1 tests, which must still pass.** They are the whole point. Then full suite, tsc, commit.

---

### Task 4: Move the timestep steps

**Files:**
- Create: `pages/modelHorizon/StepWindow.tsx`, `StepSampling.tsx`, `StepWeights.tsx`
- Modify: `pages/ModelHorizon.tsx`

**Interfaces:**
- Each step is presentational and receives what it needs as props: its data slice, and the mutation objects it triggers. Mutations stay owned by the shell. Define each step's props interface in its own file and export it.

Move the operational-window section (both single- and multi-period constructor forms), the representative-weeks section, and the snapshot-weightings section into their files. Put the multi-period constructor's per-period range table behind the `advanced` slot; put the weightings CSV controls and paginated per-row table behind theirs.

- [ ] **Step 1: RED.** Before moving, add a render assertion that the weights step's Advanced disclosure is collapsed by default and its per-row table is not in the document until opened. This fails against the current always-visible table — a genuine behavioural RED.

- [ ] **Step 2–3: Move the JSX, implement the disclosures, GREEN.**

- [ ] **Step 4: All Task 1 tests must still pass.** Test 1 exercises the weightings table, which this task moves — if it breaks, the move was not faithful. Full suite, tsc, commit.

---

### Task 5: Move the period steps

**Files:**
- Create: `pages/modelHorizon/StepMode.tsx`, `StepYears.tsx`, `StepPeriodEconomics.tsx`
- Modify: `pages/ModelHorizon.tsx`

Same shape as Task 4. Put the per-carrier load-scaler columns and the CAPEX budget column behind `StepPeriodEconomics`'s `advanced` slot; years, objective and the PV preview stay in the default view.

- [ ] **Step 1: RED.** Assert the economics step's default view shows the PV column but not the CAPEX budget column until Advanced is opened. Fails against the current single wide table.

- [ ] **Step 2–3: Move, implement, GREEN.**

- [ ] **Step 4: Task 1 test 3 (PV greying) must still pass** — it exercises exactly this table. Full suite, tsc, commit.

---

### Task 6: Retire the old scroll

**Files:**
- Modify: `pages/ModelHorizon.tsx`

By now every section renders from a step file. This task removes what remains of the old top-to-bottom layout, deletes the page's obsolete header comment describing that flow, replaces it with one describing the step model, and confirms the file has reached roughly 200 lines.

- [ ] **Step 1:** Remove the residual layout, update the header comment.
- [ ] **Step 2:** Full frontend suite, `tsc`, and a report of `wc -l pages/ModelHorizon.tsx` against the 1,476-line starting point.
- [ ] **Step 3:** Commit.

---

## Verification: the whole set

- [ ] Full frontend suite green, and **all Task 1 render tests passing** — they are the evidence the six defect fixes survived the restructure.
- [ ] `tsc --noEmit` clean.
- [ ] `git diff --stat` shows **zero** files under `pypsa-gui/backend/`.
- [ ] State plainly that this is source-only: the `.app` and any DMG stay stale until `bash pypsa-gui/build-macos.sh` runs.

## Self-Review

**Spec coverage:** page shape → Tasks 3 and 6; the six steps and their Advanced splits → Tasks 4 and 5; code structure table → Tasks 3–6; the mandatory render tests → Task 1; pure predicates and their unit tests → Task 2. The spec's out-of-scope list is honoured — no backend file appears in any task.

**Deliberate deviation from usual plan style, stated so it is not mistaken for an omission:** Tasks 3–6 do not restate the JSX they move. For a move refactor the source is authoritative and in the repo; restating it would duplicate ~900 lines into a document that then drifts. Transcription errors are caught by Task 1's render tests and `tsc`. Tasks 1 and 2, which create new code, are fully specified.

**Risk concentrated deliberately in Task 1.** If the page cannot be render-tested at acceptable cost, that is discovered before any restructuring, and the plan should be re-scoped rather than continued blind. Task 1 explicitly authorises BLOCKED for that case and forbids a shallow substitute.

**Type consistency:** `HorizonStepId` and `HorizonSummaryContext` are defined in Task 2 and consumed by name in Tasks 3–5. `StepShell`'s and `HorizonSummary`'s prop names are fixed in Task 3 and used unchanged afterwards.
