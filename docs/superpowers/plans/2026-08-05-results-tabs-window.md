# Results Tabs Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open the six windowing Results tabs on a bounded default view and fetch only that window, so a large model's default Results view stops costing ~470 MB and an unreadable 26,280-point chart.

**Architecture:** A pure `defaultWindow` function chooses the opening view from the snapshot index alone. `Results.tsx` applies it in the existing one-shot seeding effect and makes the window visible in the filter chip. The six windowing tabs resolve that window to positional bounds — no probe needed, since `/api/network/snapshots` is already fetched — and pass it to the getters widened in the previous plan. `AggregatedOverview` stays whole-horizon and gains the first real consumer of the `complete` flag.

**Tech Stack:** React 18 / TypeScript / TanStack Query / vitest.

**Spec:** `docs/superpowers/specs/2026-08-05-results-tabs-window-design.md`

## Global Constraints

- **Branch is `feature/local-app-impl`.** Re-run `git branch --show-current` before every commit. Another session commits to this branch concurrently — leave the tree clean when pausing.
- **Never write to or delete under `pypsa-gui/backend/projects/`, `~/Documents/PyPSA GUI/`, or `~/Documents/PyPSA Studio/`.**
- **Never write an API key value into a report, commit message or log.** `backend/.env` holds a live one.
- **`AggregatedOverview.tsx` must NOT be converted to ranged fetches.** It computes over `fullRange` at 13 sites because it IS the whole-horizon summary. Windowing it turns its headline energy and cost figures into window totals under horizon labels.
- **Do not remove the query-key separation between AggregatedOverview and the windowing tabs.** They differ today only by arity — `['proj','results','generators']` vs `['proj','results','generators',resultSource]`. Collapsing that "duplicate fetch" would feed a windowed payload into the summary tab.
- **No downsampling.** 85 call sites sum every row to produce MWh and €; averaging would make those figures depend on zoom level.
- **Client-side re-slicing stays.** Every `aggregateTS(ts, names, range)` and `weightedSum(ts, cols, ctx, range)` keeps its `range` argument. Once the payload is the window, that second slice is an identity operation.
- **Threshold is tested against the TOTAL horizon before any structural branch** — a 2-period × 24-snapshot network must keep opening whole.
- **node and npx are NOT on PATH by default:**
  ```bash
  cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
  export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
  ```
  A bare `npx` giving `command not found` is a PATH problem. Never `npm install`.
- **`npx tsc --noEmit -p tsconfig.json` is NOT the build's typecheck.** The build runs `tsc -b`, which also builds `tsconfig.node.json`. Run both; the `-b` form is the one that matches CI and the DMG build.
- **Never pipe a test run into `tail`/`head`** — a pipeline reports only its last stage's exit status. Redirect, echo `$?`, then read the file.
- **Path-limited `git commit <paths>`, never `git add -A`.**

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/pages/results/filterContext.tsx` | modify | add `defaultWindow`; owns `resolveRange` already |
| `src/pages/results/filterContext.test.ts` | create | `defaultWindow` unit tests |
| `src/pages/Results.tsx` | modify (`:193-200`, `:250-252`, `:341-345`) | apply the default; make the window visible |
| `src/pages/results/Dispatch.tsx` | modify | ranged fetches (9 series) |
| `src/pages/results/LoadFlow.tsx` | modify | ranged fetches |
| `src/pages/results/Prices.tsx` | modify | ranged fetches |
| `src/pages/results/Curtailment.tsx` | modify | ranged fetches |
| `src/pages/results/LostLoadTab.tsx` | modify | ranged fetches |
| `src/pages/results/StorageCycling.tsx` | modify | ranged fetches |
| `src/pages/results/AggregatedOverview.tsx` | modify | the `complete` guard |
| `src/pages/results/AggregatedOverview.test.tsx` | create | guard test |

---

### Task 1: `defaultWindow`

**Files:**
- Modify: `pypsa-gui/frontend/src/pages/results/filterContext.tsx`
- Create: `pypsa-gui/frontend/src/pages/results/filterContext.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces, for Task 2:
  ```ts
  export type DefaultWindow =
    | { kind: 'whole' }
    | { kind: 'period'; period: number | string }
    | { kind: 'iso'; fromIso: string; toIso: string }

  export const WINDOW_THRESHOLD: number      // 8760
  export const DEFAULT_FLAT_WINDOW: number   // 720

  export function defaultWindow(
    snapshots: string[],
    periods: Array<number | string> | undefined,
  ): DefaultWindow
  ```

- [ ] **Step 1: Write the failing tests**

Create `pypsa-gui/frontend/src/pages/results/filterContext.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { defaultWindow, DEFAULT_FLAT_WINDOW, WINDOW_THRESHOLD } from './filterContext'

/** N hourly ISO stamps starting 2030-01-01T00:00:00. */
function stamps(n: number): string[] {
  const out: string[] = []
  const start = Date.UTC(2030, 0, 1)
  for (let i = 0; i < n; i++) {
    out.push(new Date(start + i * 3_600_000).toISOString().slice(0, 19))
  }
  return out
}

describe('defaultWindow', () => {
  it('leaves a short flat network whole', () => {
    expect(defaultWindow(stamps(168), undefined)).toEqual({ kind: 'whole' })
  })

  it('leaves a network of exactly the threshold whole', () => {
    // 8760 is one hourly year — the largest horizon that renders as a chart
    // without windowing, so it must NOT be narrowed.
    expect(defaultWindow(stamps(WINDOW_THRESHOLD), undefined)).toEqual({ kind: 'whole' })
  })

  it('leaves a SHORT multi-period network whole', () => {
    // The threshold is tested against the TOTAL horizon before any structural
    // branch. Branching on multi-period first would window the golden test
    // fixture (2 periods x 24) down to 24 rows.
    const periods = [...Array(24).fill(2030), ...Array(24).fill(2035)]
    expect(defaultWindow(stamps(48), periods)).toEqual({ kind: 'whole' })
  })

  it('opens a long multi-period network on its first period', () => {
    const periods = [...Array(8760).fill(2030), ...Array(8760).fill(2035)]
    expect(defaultWindow(stamps(17520), periods)).toEqual({ kind: 'period', period: 2030 })
  })

  it('picks the numerically lowest period, not the first encountered', () => {
    const periods = [...Array(8760).fill(2040), ...Array(8760).fill(2030)]
    expect(defaultWindow(stamps(17520), periods)).toEqual({ kind: 'period', period: 2030 })
  })

  it('opens a long flat network on its first month', () => {
    const s = stamps(26280)
    expect(defaultWindow(s, undefined)).toEqual({
      kind: 'iso',
      fromIso: s[0],
      toIso: s[DEFAULT_FLAT_WINDOW - 1],
    })
  })

  it('treats an empty periods array as flat', () => {
    const s = stamps(26280)
    expect(defaultWindow(s, [])).toEqual({
      kind: 'iso', fromIso: s[0], toIso: s[DEFAULT_FLAT_WINDOW - 1],
    })
  })

  it('returns whole for an empty snapshot list', () => {
    expect(defaultWindow([], undefined)).toEqual({ kind: 'whole' })
  })

  it('never runs past the end when the horizon is barely over the threshold', () => {
    const s = stamps(WINDOW_THRESHOLD + 10)
    const w = defaultWindow(s, undefined)
    expect(w).toEqual({ kind: 'iso', fromIso: s[0], toIso: s[DEFAULT_FLAT_WINDOW - 1] })
  })
})
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
npx vitest run src/pages/results/filterContext.test.ts > /tmp/w1.log 2>&1; echo "EXIT=$?"
```

Expected: failure importing `defaultWindow` from `./filterContext`.

- [ ] **Step 3: Implement**

Add to `pypsa-gui/frontend/src/pages/results/filterContext.tsx`, beside `resolveRange`:

```ts
/**
 * The view a Results tab opens on.
 *
 * `whole` means "change nothing" — the pre-existing behaviour, and what every
 * network at or below the threshold keeps.
 */
export type DefaultWindow =
  | { kind: 'whole' }
  | { kind: 'period'; period: number | string }
  | { kind: 'iso'; fromIso: string; toIso: string }

/**
 * One hourly year. The largest horizon that renders as a chart without
 * windowing, and the natural unit of this domain — below it, nothing changes.
 */
export const WINDOW_THRESHOLD = 8760

/** One month of hourly snapshots. */
export const DEFAULT_FLAT_WINDOW = 720

/**
 * Choose the opening window from the snapshot index alone.
 *
 * The threshold is tested against the TOTAL horizon BEFORE any structural
 * branch. Branching on multi-period first would narrow a 2-period x
 * 24-snapshot network to 24 rows — a regression on exactly the small models
 * where the point is that nothing changes, and it would alter the golden test
 * fixture's default view.
 *
 * Multi-period returns a PERIOD rather than ISO bounds because `resolveRange`
 * matches the parallel `periods` array natively, whereas ISO bounds on a
 * multi-period network match rows in every period at once — every period
 * replicates the same base operational year. See the comment at the `fromIso`
 * handling below, and CLAUDE.md's note on the Horizon filter's year remap.
 */
export function defaultWindow(
  snapshots: string[],
  periods: Array<number | string> | undefined,
): DefaultWindow {
  if (snapshots.length <= WINDOW_THRESHOLD) return { kind: 'whole' }

  if (periods && periods.length === snapshots.length) {
    const seen = new Set<number | string>(periods)
    const arr = [...seen]
    if (arr.length > 1) {
      const allNumeric = arr.every(p => typeof p === 'number')
      const sorted = allNumeric
        ? (arr as number[]).sort((a, b) => a - b)
        : arr.map(String).sort()
      return { kind: 'period', period: sorted[0] }
    }
  }

  const lastIdx = Math.min(DEFAULT_FLAT_WINDOW, snapshots.length) - 1
  return { kind: 'iso', fromIso: snapshots[0], toIso: snapshots[lastIdx] }
}
```

- [ ] **Step 4: Run to verify it passes**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
npx vitest run src/pages/results/filterContext.test.ts > /tmp/w1.log 2>&1; echo "EXIT=$?"
npx tsc -b > /tmp/w1tsc.log 2>&1; echo "TSCB=$?"
```

Expected: `EXIT=0` with 9 passed, `TSCB=0`.

- [ ] **Step 5: Prove the threshold ordering test discriminates**

Temporarily move the multi-period branch ABOVE the `snapshots.length <= WINDOW_THRESHOLD` check. Confirm `leaves a SHORT multi-period network whole` FAILS. Restore, confirm green, confirm `git diff` on `filterContext.tsx` shows only the intended addition. Report both observations — this is the defect the spec's own table originally had.

- [ ] **Step 6: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git add pypsa-gui/frontend/src/pages/results/filterContext.test.ts
git commit pypsa-gui/frontend/src/pages/results/filterContext.tsx pypsa-gui/frontend/src/pages/results/filterContext.test.ts -m "feat(gui): choose a bounded default view from the snapshot index

Threshold is tested against the TOTAL horizon before any structural branch,
so a 2-period x 24-snapshot network keeps opening whole - an earlier draft
branched on multi-period first and would have narrowed it to 24 rows.

Multi-period returns a PERIOD, not ISO bounds: resolveRange matches the
parallel periods array natively, whereas an ISO range on a multi-period
network matches rows in every period at once."
```

---

### Task 2: Apply the default and make the window visible

**Files:**
- Modify: `pypsa-gui/frontend/src/pages/Results.tsx`

**Interfaces:**
- Consumes: `defaultWindow`, `DefaultWindow` from Task 1.
- Produces, for Tasks 3–4: the `ResultsFilter` context value now carries a non-null `selectedPeriod` or a narrowed `fromIso`/`toIso` on large networks. No new export.

**The gap this task closes.** `Results.tsx:250-252` defines `isFiltered` as "the user moved at least one bound off the full horizon", and its comment says so explicitly: *"so the seeded defaults don't trigger the warn banner."* A default window would therefore be applied **invisibly** — the chip at `:341-345` renders only when `isFiltered`. The spec claimed discoverability was already built; it is not, for defaults. This task adds it.

- [ ] **Step 1: Add the import, then apply the default in the existing seeding effect**

`Results.tsx` already imports `ResultsFilterProvider` from `./results/filterContext` at `:8`. Extend that import rather than adding a second one:

```ts
import { ResultsFilterProvider, defaultWindow } from './results/filterContext'
```


`Results.tsx:193-200` currently seeds both bounds to the full horizon behind a `seededRef` one-shot guard. Replace the body, keeping the guard and the existing comment about suppressing the browser's locale placeholder:

```ts
  const seededRef = useRef(false)
  useEffect(() => {
    if (seededRef.current) return
    if (!firstSnap16 || !lastSnap16) return
    // Seed the inputs to the full span first — this is what suppresses the
    // native datetime-local placeholder and shows the model's real extent.
    setFromIso(firstSnap16)
    setToIso(lastSnap16)
    // Then narrow to the opening window, if this network warrants one.
    const w = defaultWindow(snap?.snapshots ?? [], snap?.periods)
    if (w.kind === 'period') {
      setSelectedPeriod(w.period)
    } else if (w.kind === 'iso') {
      setFromIso(w.fromIso.slice(0, 16))
      setToIso(w.toIso.slice(0, 16))
    }
    seededRef.current = true
  }, [firstSnap16, lastSnap16, snap])
```

`setSelectedPeriod` is declared at `:222`, after this effect in source order — that is fine, `const` declarations are hoisted into scope for a closure that runs after mount. If `tsc` disagrees, move the effect below `:231` rather than restructuring the state.

- [ ] **Step 2: Make the active window visible**

`isFiltered` keeps its exact current meaning and styling — it is what turns the chip warn-coloured once the *user* narrows. Add a second, neutral indicator beside it at `:250`:

```ts
  // True whenever the ACTIVE view is not the whole horizon, whoever caused it.
  // `isFiltered` deliberately excludes the seeded defaults so the warn styling
  // means "you narrowed this"; a default window still has to be visible, just
  // not alarming.
  const isWindowed = isFiltered || selectedPeriod !== 'all'
```

and render the chip on `isWindowed` rather than `isFiltered`, using `text-warn` only when `isFiltered` is true:

```tsx
          {isWindowed && (
            <span className={`ml-2 font-mono text-[10px] ${isFiltered ? 'text-warn' : 'text-muted'}`}>
              {selectedPeriod !== 'all'
                ? `period ${selectedPeriod}`
                : `${toDisplay(fromIso) || '…'} → ${toDisplay(toIso) || '…'}`}
            </span>
          )}
```

Change the adjacent `{isFiltered && (` guard on the reset affordance at `:346` to `{isWindowed && (` so the user can always widen back, and make `resetHorizon` also clear the period:

```ts
  const resetHorizon = () => {
    if (firstSnap16) setFromIso(firstSnap16)
    if (lastSnap16)  setToIso(lastSnap16)
    setSelectedPeriod('all')
  }
```

- [ ] **Step 3: Verify types and the existing suite**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
npx tsc -b > /tmp/w2tsc.log 2>&1; echo "TSCB=$?"
npx vitest run > /tmp/w2.log 2>&1; echo "VITEST=$?"
```

Expected `TSCB=0`, `VITEST=0`. Any Results-related test that breaks here is signal, not noise — report which and why before changing it. The golden fixture is 2 periods × 48 snapshots total, below the threshold, so no existing test should see a narrowed default.

- [ ] **Step 4: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git commit pypsa-gui/frontend/src/pages/Results.tsx -m "feat(gui): open large-model Results tabs on a bounded window

isFiltered deliberately excludes the seeded defaults so its warn styling
means 'you narrowed this'. A default window still has to be visible, so
isWindowed drives the chip in a neutral colour and always offers the widen
action - otherwise the narrowing would be silent."
```

---

### Task 3: Ranged fetches in Dispatch

**Files:**
- Modify: `pypsa-gui/frontend/src/pages/results/Dispatch.tsx`

**Interfaces:**
- Consumes: `resolveRange` (existing, `filterContext.tsx:82`); the nine getters widened in the 2026-08-04 plan, each taking an optional second `range: {from, to}`.
- Produces, for Task 4: the pattern below, to be repeated in the other five tabs.

**No probe is needed.** Unlike the canvas, the snapshot index is already available independently: `Results.tsx` fetches `nk(currentProject, 'snapshots')`. Dispatch reads the same cached query and resolves the filter to positional bounds before any results payload arrives.

- [ ] **Step 1: Write `useResultsWindow` — one hook, not six copies**

Six tabs each need the same three things: the snapshot query, the resolved bounds, and a validity guard. Writing that out per file would be eighteen near-identical blocks. React forbids hooks in a loop but permits a **custom hook** called once per component, so it collapses to one call site each.

Create `pypsa-gui/frontend/src/hooks/useResultsWindow.ts` — `src/hooks/` is the established home for shared query hooks (`useSolveQueue.ts`, `useLocalSettings.ts`):

```ts
/**
 * Positional bounds for the active Horizon filter.
 *
 * Derived from the SNAPSHOT INDEX, not from a results payload, so a tab can
 * window its fetch before any payload exists. That is why no probe is needed
 * here, unlike the canvas overlay: `/api/network/snapshots` is already fetched
 * by `Results.tsx` under this same key, so every tab reads it from cache.
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { nk } from '../utils/queryKeys'
import { useResultsFilter, resolveRange } from '../pages/results/filterContext'

export function useResultsWindow(currentProject: string | null): {
  win: { from: number; to: number }
  winValid: boolean
} {
  const filter = useResultsFilter()
  const { data: snap } = useQuery({
    queryKey: nk(currentProject, 'snapshots'),
    queryFn: networkApi.getSnapshots,
    staleTime: 5_000,
  })
  const win = useMemo(
    () => resolveRange(snap?.snapshots ?? [], filter, snap?.periods),
    [snap, filter],
  )
  // An inverted range means the selected period is absent from this network —
  // "nothing to show", not "fetch everything".
  return { win, winValid: win.from <= win.to }
}
```

Confirm the `@tanstack/react-query` import path and the `nk` path against `src/hooks/useSolveQueue.ts` rather than assuming; copy its import lines if they differ.

- [ ] **Step 2: Use it in Dispatch**

`Dispatch.tsx` already imports `resolveRange` and `useResultsFilter` (`:25`), `networkApi` (`:10`) and `nk` (`:12`). Add the hook import and call it once near the existing `resultSource`:

```ts
import { useResultsWindow } from '../../hooks/useResultsWindow'
```

```ts
  const { win, winValid } = useResultsWindow(currentProject)
```

Leave the existing `resolveRange` call at `:490` alone — that one resolves against the *payload's* index for the client-side re-slice, and is a different thing from this one.

- [ ] **Step 3: Window the nine per-snapshot queries**

Each of the nine gains the bounds in its key and passes them to the getter. `generators` at `:248` in full:

```ts
  const { data: gensTS } = useQuery({
    queryKey: nk(currentProject, 'results', 'generators', resultSource, win.from, win.to),
    queryFn: () => resultsApi.getGeneratorResults(resultSource, win),
    enabled: winValid,
  })
```

Apply the identical shape to `storage` (`:256`), `storage_dispatch` (`:257`), `store_dispatch` (`:258`), `store_energy` (`:259`), `loads` (`:260`) and `links` (`:264`), each with its own getter.

`curtailment` (`:252`) and `lost_load` (`:267`) are per-snapshot too but take **no `source` argument** — their getters are `resultsApi.getCurtailment` and `resultsApi.getLostLoad`. Pass the range as the first argument if their signature allows it; if those two getters were not widened in the previous plan, leave them unranged and report it rather than changing `api/simulation.ts` from inside this task.

**Both bounds go in the key**, not just `from`: unlike the canvas's fixed-size chunks, a tab window can move either end independently when the user edits the filter.

**Leave `cost_breakdown` (`:247`) and `economics_by_carrier` (`:274`) alone** — they are aggregate endpoints with no snapshot axis.

- [ ] **Step 4: Leave every client-side slice untouched**

Do NOT change any `aggregateTS(ts, names, range)` or `weightedSum(...)` call. Once the payload is already the window, `resolveRange` against the payload's own index returns the whole payload and the second slice is an identity operation. Leaving it means no arithmetic changes and the tab still works if a fetch ever returns more than requested.

- [ ] **Step 5: Verify**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
npx tsc -b > /tmp/w3tsc.log 2>&1; echo "TSCB=$?"
npx vitest run > /tmp/w3.log 2>&1; echo "VITEST=$?"
```

Expected `TSCB=0`, `VITEST=0`.

- [ ] **Step 6: Confirm the key separation survived**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
grep -n "results', 'generators'" src/pages/results/Dispatch.tsx src/pages/results/AggregatedOverview.tsx
```

Dispatch's key must now carry `resultSource, win.from, win.to`; AggregatedOverview's must still be the bare three-element form. If they are equal, a windowed payload can reach the summary tab — stop and report.

- [ ] **Step 7: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git commit pypsa-gui/frontend/src/pages/results/Dispatch.tsx -m "feat(gui): fetch only the active window in the Dispatch tab

Bounds come from the snapshot index, not from a results payload, so no probe
is needed - Results.tsx already fetches that query and Dispatch reads it from
cache.

Both bounds go in the query key: unlike the canvas's fixed-size chunks, a tab
window can move either end independently."
```

---

### Task 4: Ranged fetches in the other five tabs

**Files:**
- Modify: `pypsa-gui/frontend/src/pages/results/LoadFlow.tsx`
- Modify: `pypsa-gui/frontend/src/pages/results/Prices.tsx`
- Modify: `pypsa-gui/frontend/src/pages/results/Curtailment.tsx`
- Modify: `pypsa-gui/frontend/src/pages/results/LostLoadTab.tsx`
- Modify: `pypsa-gui/frontend/src/pages/results/StorageCycling.tsx`

**Interfaces:**
- Consumes: the pattern from Task 3.
- Produces: nothing further.

- [ ] **Step 1: Apply the Task 3 pattern to each**

In each file, call the hook Task 3 created — one line, no repeated query or memo — then window every **per-snapshot** query exactly as in Task 3:

```ts
import { useResultsWindow } from '../../hooks/useResultsWindow'
```

```ts
  const { win, winValid } = useResultsWindow(currentProject)
```

```ts
  const { data: xTS } = useQuery({
    queryKey: nk(currentProject, 'results', '<name>', resultSource, win.from, win.to),
    queryFn: () => resultsApi.get<X>Results(resultSource, win),
    enabled: winValid,
  })
```

**Convert only queries whose endpoint returns `{index, columns, data}`.** The sixteen per-snapshot endpoints are: `generators`, `storage_dispatch`, `store_dispatch`, `store_energy`, `storage`, `lines`, `links`, `transformers`, `unit_commitment`, `voltages`, `line_reactive`, `transformer_reactive`, `prices`, `curtailment`, `lost_load`, `loads`. Anything else — `cost_breakdown`, `statistics`, `losses`, `carrier_kpis`, `emissions`, `line_duals`, `lcoh`, `economics_by_carrier`, `price_drivers`, `asset_economics`, `objective_decomposition`, `ac_pf/status` — is an aggregate with no snapshot axis; leave it alone.

If a getter you need was not widened with an optional `range` argument in the 2026-08-04 plan, leave that query unranged and report it. Do not edit `api/simulation.ts` from inside this task — that file's contract was set by the previous plan and changing it here puts every other consumer in this task's blast radius.

- [ ] **Step 2: Verify**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
npx tsc -b > /tmp/w4tsc.log 2>&1; echo "TSCB=$?"
npx vitest run > /tmp/w4.log 2>&1; echo "VITEST=$?"
```

Expected `TSCB=0`, `VITEST=0`.

- [ ] **Step 3: Confirm AggregatedOverview was not touched**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git status --porcelain -- pypsa-gui/frontend/src/pages/results/AggregatedOverview.tsx
```

Expected: no output. If it appears, revert it — it is Task 5's file and must not gain ranged fetches at all.

- [ ] **Step 4: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git commit pypsa-gui/frontend/src/pages/results/LoadFlow.tsx pypsa-gui/frontend/src/pages/results/Prices.tsx pypsa-gui/frontend/src/pages/results/Curtailment.tsx pypsa-gui/frontend/src/pages/results/LostLoadTab.tsx pypsa-gui/frontend/src/pages/results/StorageCycling.tsx -m "feat(gui): fetch only the active window in the five remaining windowing tabs

Same pattern as Dispatch. Only endpoints returning index/columns/data are
converted; the aggregates have no snapshot axis."
```

---

### Task 5: Make `complete` bite in AggregatedOverview

**Files:**
- Modify: `pypsa-gui/frontend/src/pages/results/AggregatedOverview.tsx`
- Create: `pypsa-gui/frontend/src/pages/results/AggregatedOverview.test.tsx`

**Interfaces:**
- Consumes: `TSPayload.range` from `shared.tsx` (added by the 2026-08-04 plan).
- Produces: nothing further.

**Why this exists.** `AggregatedOverview` computes over `fullRange` at 13 sites — the whole payload, whatever it contains. If it is ever handed a windowed payload, its headline energy and cost figures silently become window totals under whole-horizon labels. Today that cannot happen: its getters are called with one argument, so the backend returns no `range` key. This guard is for the day someone converts it by accident, which is the most likely way this design gets broken later. It is also the first consumer of the `complete` field the previous plan added.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/pages/results/AggregatedOverview.test.tsx`. Read an existing component test in `src/components/` first and copy its QueryClient/provider harness rather than inventing one; set `retry: false` on the client so a failed query does not slow the test.

```tsx
import { describe, expect, it, vi } from 'vitest'
import { isPartialPayload } from './AggregatedOverview'

describe('isPartialPayload', () => {
  it('is false for an unranged payload — the normal case', () => {
    expect(isPartialPayload([{ index: [], columns: [], data: [] }])).toBe(false)
  })

  it('is false for a payload that is ranged but complete', () => {
    const ts = {
      index: [], columns: [], data: [],
      range: { from: 0, to: 47, total: 48, complete: true, capped: false },
    }
    expect(isPartialPayload([ts])).toBe(false)
  })

  it('is TRUE for a windowed payload', () => {
    // The regression this guards: converting AggregatedOverview's getters to
    // ranged calls would otherwise turn its horizon KPIs into window totals
    // under horizon labels, silently.
    const ts = {
      index: [], columns: [], data: [],
      range: { from: 0, to: 719, total: 26280, complete: false, capped: false },
    }
    expect(isPartialPayload([ts])).toBe(true)
  })

  it('is TRUE if ANY payload is windowed', () => {
    const whole = { index: [], columns: [], data: [] }
    const part = {
      index: [], columns: [], data: [],
      range: { from: 0, to: 0, total: 100, complete: false, capped: false },
    }
    expect(isPartialPayload([whole, null, part, undefined])).toBe(true)
  })

  it('ignores null and undefined payloads', () => {
    expect(isPartialPayload([null, undefined])).toBe(false)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
npx vitest run src/pages/results/AggregatedOverview.test.tsx > /tmp/w5.log 2>&1; echo "EXIT=$?"
```

Expected: `isPartialPayload` is not exported.

- [ ] **Step 3: Implement the guard**

In `AggregatedOverview.tsx`, above the component:

```tsx
/**
 * True when any payload is a WINDOW rather than the whole series.
 *
 * This tab reports whole-horizon energy and cost: it computes over `fullRange`
 * — the entire payload, whatever it holds — at 13 sites, deliberately ignoring
 * the Horizon filter, because summarising the horizon is its job. Hand it a
 * windowed payload and every KPI silently becomes a window total under a
 * horizon label.
 *
 * Unreachable today: this tab's getters are called with one argument, so the
 * backend emits no `range` key at all. It exists for the day someone converts
 * them, which is the most likely way this design gets broken later.
 */
export function isPartialPayload(
  payloads: Array<{ range?: { complete: boolean } } | null | undefined>,
): boolean {
  return payloads.some(p => p?.range != null && !p.range.complete)
}
```

and inside the component, before the KPI block:

```tsx
  const partial = isPartialPayload([gensTS, loadTS, storPowerTS])
```

Render an explicit state instead of numbers when `partial` is true:

```tsx
  if (partial) {
    return (
      <div className="p-4 text-sm text-warn">
        This tab reports whole-horizon totals, but the results it received cover
        only part of the horizon. Totals are unavailable — reload the Results
        panel, and if this persists it is a bug rather than a setting.
      </div>
    )
  }
```

Place it after the existing loading/empty guards so it cannot pre-empt them.

- [ ] **Step 4: Run to verify it passes**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
npx vitest run src/pages/results/AggregatedOverview.test.tsx > /tmp/w5.log 2>&1; echo "EXIT=$?"
npx tsc -b > /tmp/w5tsc.log 2>&1; echo "TSCB=$?"
```

Expected `EXIT=0` with 5 passed, `TSCB=0`.

- [ ] **Step 5: Prove the guard would catch a real conversion**

Temporarily change one of AggregatedOverview's getter calls to pass a range — e.g. `resultsApi.getGeneratorResults(undefined, { from: 0, to: 0 })` — and confirm by reading the code path that `partial` would become true and the tab would render the message rather than numbers. Restore. Report what you traced; a runtime proof needs a live backend, so a code-read is the honest evidence here — say so rather than claiming a test run.

- [ ] **Step 6: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git add pypsa-gui/frontend/src/pages/results/AggregatedOverview.test.tsx
git commit pypsa-gui/frontend/src/pages/results/AggregatedOverview.tsx pypsa-gui/frontend/src/pages/results/AggregatedOverview.test.tsx -m "feat(gui): refuse to report horizon totals from a windowed payload

AggregatedOverview computes over the whole payload at 13 sites because
summarising the horizon is its job. Handed a window it would report window
totals under horizon labels, silently.

Unreachable today - its getters take one argument - and that is the point:
this is the first consumer of the complete flag, and it exists for the day
someone converts them."
```

---

## Final verification

- [ ] **Both typechecks and the full frontend suite**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
npx tsc --noEmit -p tsconfig.json > /tmp/wf-tsc.log 2>&1; echo "TSC=$?"
npx tsc -b > /tmp/wf-tscb.log 2>&1; echo "TSCB=$?"
npx vitest run > /tmp/wf-fe.log 2>&1; echo "VITEST=$?"
```

Both typechecks must pass. `tsc -b` is the one the build runs and the one that caught a missing `@types/node` after `--noEmit` reported clean.

- [ ] **Backend suite** — this plan is frontend-only, so it is a regression check rather than a gate:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests > /tmp/wf-be.log 2>&1; echo "PYTEST_EXIT=$?"; tail -4 /tmp/wf-be.log
```

Baseline: 2249 passed, 18 skipped.

- [ ] **Rebuild the DMG**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui"
bash build-macos.sh > /tmp/wf-build.log 2>&1; echo "BUILD_EXIT=$?"; tail -12 /tmp/wf-build.log
```

Expected `BUILD_EXIT=0`, a clean secret-scan line, and a DMG timestamp later than the last commit. **A failed build leaves the PREVIOUS bundle in place**, so the presence of a `.dmg` is not evidence of success — check the exit status and the timestamp. Per CLAUDE.md the app serves the built SPA from the gitignored `frontend/dist/`, so until this runs the change exists in source only.

---

## Notes for the executor

- **Do not convert `AggregatedOverview` to ranged fetches.** It is excluded by design, and Task 5 exists to make an accidental conversion fail loudly.
- **Do not "fix" the duplicate fetch** where AggregatedOverview and Dispatch each download `generators`. That duplication is what keeps their cache entries separate; collapsing it would feed a window into the summary tab.
- **Do not change `api/simulation.ts`.** Its contract was set by the 2026-08-04 plan. If a getter you need lacks an optional `range` argument, report it rather than widening it here.
- **`tsc --noEmit -p tsconfig.json` is not enough.** It does not build `tsconfig.node.json`; `tsc -b` does, and that is what the DMG build runs.
- **Two tests the spec asked for are deliberately not in this plan. Do not add them; the reasoning is the deliverable.**
  - *The identity property* — "fetching window `[a,b]` and re-slicing equals fetching whole and slicing to `[a,b]`". Already asserted one layer down by `tests/test_results_range.py::test_a_server_slice_equals_the_same_slice_taken_client_side`, across all sixteen endpoints, against a real solved network. Reproducing it in vitest would require mocking the backend, and a mocked slice tests the mock. Cite the backend test instead.
  - *A query-key inequality test* — the keys are built inline inside components and cannot be extracted without restructuring three files. Task 3 Step 5 checks it once by grep, and the durable guard is Task 5's `isPartialPayload`, which catches the consequence (a windowed payload reaching the summary tab) rather than the cause. Guarding the consequence is the better test here: it stays true however the keys are later refactored.
