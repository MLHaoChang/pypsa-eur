# Asset editing — Scope C: drop-on-a-bus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dragging a palette asset onto a bus — on the schematic canvas *or* the map canvas — opens the creation form with that bus already filled into the asset's terminal field, and a bus dropped on the schematic stops writing canvas pixels into PyPSA's longitude/latitude.

**Architecture:** The pointer-drag currently inlined in `Sidebar.tsx` moves into one `useAssetDrag` hook whose drop resolver returns a single struct describing what was under the cursor. Both canvases publish the same `data-bus-name` DOM attribute so the resolver never has to know React Flow's or Leaflet's internal markup. `CreationForm` gains one terminal-prefill branch and loses one coordinate-seed branch. The dead `AssetPalette.tsx` and its three orphan `FIELD_MAP` entries go in the same change.

**Tech Stack:** React 19 + TypeScript 5.8 (strict), `@xyflow/react` 12 for the schematic canvas, `leaflet` 1.9 / `react-leaflet` 5 for the map canvas, zustand 5 for `uiStore`, vitest 4.1.10 + jsdom 29 + Testing Library 16 for tests.

---

## Plan set

This feature ships as **three plans, executed in this order**. Each one produces
working, testable software on its own: C ships without A or B; A ships without B.

| # | File | Scope | Why this order |
|---|---|---|---|
| 1 | `docs/superpowers/plans/2026-08-09-asset-editing-c-drag-drop.md` (this file) | C — drop-on-a-bus | Smallest. Its only shared file is `Sidebar.tsx`, which nothing else in this feature touches. |
| 2 | `docs/superpowers/plans/2026-08-09-asset-editing-a-grid.md` | A — the editable bottom grid | Largest. Owns `BottomPanel.tsx` and the one backend contract change (`PATCH /_bulk`). **Creates `utils/attributeCatalog.ts`, `hooks/useCatalog.ts` and `GET /api/network/catalog/{component}`.** |
| 3 | `docs/superpowers/plans/2026-08-09-asset-editing-b-parameter-table.md` | B — the parameter surface | Consumes all three artefacts Plan A creates, and inherits them already characterised. |

### Spec decision coverage — all 30 decisions

Source: `docs/superpowers/specs/2026-08-08-asset-editing-design.md`. "Primary
plan" is the single plan that owns the decision; "also touched by" records
where the same decision has a subordinate consequence, so the mapping is
exhaustive without being ambiguous about ownership.

| Decision | Primary plan | Also touched by |
|---|---|---|
| D1 `AssetTable` extended in place, no virtualisation | A | — |
| D2 three pure modules + two hooks, `coerce.ts` unmodified | A | C (the `hooks/useAssetDrag.ts` row of D2's table is built here, under D25) |
| D3 one catalog endpoint + one service module | A | — |
| D4 typed cell editors, one draft, one commit path | A | — |
| D5 keyboard map + the guarded capture-phase Escape | A | — |
| D6 clipboard I/O via `ClipboardEvent`, TSV wire format | A | — |
| D7 three paste shapes against the paste target | A | — |
| D8 whole-batch rejection naming offending cells | A | — |
| D9 `PATCH /_bulk` gains an additive `rows` body form | A | — |
| D10 optimistic write with rollback, exact cache contract | A | — |
| D11 one paste/fill = one undo step | A | — |
| D12 blank-and-infinity contract | A | — |
| D13 editability = catalog `status` + two overrides | A | — |
| D14 `varying` attribute checked for a real series | A | — |
| D15 grid shows absolute `r`/`x`/`b` with unit headers | A | — |
| D16 `CarriersTable` absorbed and deleted | A | — |
| D17 `availableCols` stays derived from the data | A | — |
| D18 large-paste confirmation is a `confirmToast` | A | — |
| D19 native table semantics + roving tabindex | A | — |
| D20 extras section opens all three layers on all eight forms | B | — |
| D21 backend passthrough catalog-whitelisted at the two CRUD helpers | B | — |
| D22 six reveal rules in one table | B | — |
| D23 "+ Add parameter" persisted per palette type | B | — |
| D24 catalog query key `['catalog', component]`, nine payload fields | A | B (consumes the hook and the payload; the spec files D24 under Scope B, the sequencing brief builds it in A) |
| D25 one `useAssetDrag` hook, both canvases publish `data-bus-name` | **C** (Tasks 2, 3) | — |
| D26 map drops carry no coordinates, no global Leaflet handle | **C** (Task 2) | — |
| D27 terminal prefill, 17 of 18 palette items, honours the carrier filter | **C** (Task 4) | — |
| D28 schematic drops stop writing `x`/`y` | **C** (Task 5) | — |
| D29 five deletions | **C** (Task 6 — rows 1 and 5: `AssetPalette.tsx`, the three orphan `FIELD_MAP` entries) | A (rows 2–4: `SimpleTable`, `CarriersTable`, the bulk-edit toolbar) |
| D30 characterization tests are task zero in each scope | **all three** — the single deliberate exception to one-decision-one-plan, because the spec's own D30 table has one row per scope. C Task 1; A Tasks 1–4; B Tasks 1–2. | — |

### Success-criteria coverage — all 42

| Criteria | Plan | Note |
|---|---|---|
| 1–28 | A | The whole editable-grid surface: editors, clipboard, paste shapes, rollback, undo spacing, blank/`inf`, bus and carrier cells, series shadow, overrides, unit headers, confirm toast, Carriers tab. |
| 29–34 | B | Extras persistence and round-trip, picker contents, reveal/require rules under `lopf` and `pf`, creation-form parity. |
| **35** Drop a Generator on a schematic bus → `bus` prefilled | **C** | Task 4. |
| **36** Same on the map canvas | **C** | Task 4. |
| **37** Electrolyzer on a hydrogen bus leaves `bus0` empty + mismatch line | **C** | Task 4. |
| **38** Bus dropped on the schematic gets `x == 0 and y == 0`, appears in `UnplacedBusesPanel` | **C** | Task 5. |
| 39 the five deletions absent + `npm run build` passes | A | Plan A's deletion task runs the five-name absence check across the whole tree; Plan C Task 6 removes and independently verifies two of the five. |
| 40 reverting the whole-batch 404 / the blank-to-`inf` rule each fails a test | A | — |
| 41 `utils/coerce.ts` unchanged, its ten tests pass unmodified | A | — |
| 42 full suites green against the `c2cc4510` baseline | A | Every plan's last task re-runs both suites; the numeric baseline in criterion 42 is asserted by whichever plan lands last. |

**Covered by no plan:** none. Every decision and every success criterion above
has a plan.

---

## Global Constraints

Every task's requirements implicitly include this section.

**Paths contain a space.** The worktree is
`/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing`.
Quote every path in every shell command.

**Branch and commits.** Work only on `feature/asset-editing` in that worktree.
Multiple agent sessions share this repo (`CLAUDE.md:702-712`). Before every
commit run `git branch --show-current` and `git status --porcelain`. Commit with
a **path-limited** `git add <paths>` naming only the files the task lists —
**never `git add -A`**. Never `git stash`: the stash ref is shared between
worktrees. Never touch `/Users/orange/Desktop/Code Test/pypsa-eur-assistant`.

**Toolchain — Node is NOT on the default PATH.** This worktree has no
`.pixi/envs/`. Borrow the main checkout's **`test`** environment — the only one
carrying node, npm, npx, python, pytest, ruff *and* `pywebview`:

```bash
PIXI_BIN="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/test/bin"
```

Frontend, from `<worktree>/pypsa-gui/frontend`:

```bash
PATH="$PIXI_BIN:$PATH" npm test                                   # == "vitest run"
PATH="$PIXI_BIN:$PATH" npx vitest run src/utils/coerce.test.ts    # one file
PATH="$PIXI_BIN:$PATH" npm run build                              # == "tsc -b && vite build"
```

`npm run build` is the **only** type-check in the project — there is no
`typecheck` script, no ESLint and no Prettier anywhere in the repo.

Backend, from `<worktree>/pypsa-gui/backend`:

```bash
"$PIXI_BIN/python" -m pytest tests/test_bulk_update.py -v     # one file
"$PIXI_BIN/python" -m pytest                                  # whole suite
"$PIXI_BIN/python" -m ruff check <file>                       # backend lint
```

- **The backend suite MUST run in pixi's `test` environment, not `default`.**
  `default` omits `pywebview` **by design** (`pixi.toml:318-325`) and yields
  **7 spurious failures**. `PIXI_BIN` above already points at `test`.
- **Never pass an extra `-q`.** `pypsa-gui/backend/pytest.ini:15` already sets
  `addopts = -q`. A second `-q` stacks to `-qq` and **suppresses the
  `N passed in Xs` summary line**, so the run prints only dots and there is
  nothing to check the expected output against. Use `-v` for one file and no
  flag for the whole suite.

**Baseline at `c2cc4510`** (measured, `ledger.md:99-105`): frontend 82 files /
660 tests / 0 failures; backend 2183 passed / 23 skipped / 0 failures in the
`test` env. Every suite run is judged against those numbers plus the new tests.

**Test-writing house rules.**
- `vite.config.ts:34-35` sets `globals: false`. **Every test file imports
  `describe` / `it` / `expect` / `vi` / `beforeEach` from `'vitest'`.**
- **`@testing-library/jest-dom` is NOT installed.** Use plain vitest matchers
  (`expect(el.textContent).toBe(…)`), never `toBeInTheDocument()`.
- Mock network access with `vi.mock('<rel>/api/<module>', async importOriginal
  => …)`. msw is not installed; there are zero `vi.stubGlobal('fetch', …)`
  calls in the repo.
- A test must never build its expectation by calling the function under test
  (`2026-08-01-trustworthy-numbers-design.md:138-144`).
- Frontend tests are co-located: `Foo.test.tsx` beside `Foo.tsx`. There are no
  `__tests__` directories.
- Backend tests are flat in `pypsa-gui/backend/tests/`, use the `client` and
  `install_network` fixtures and the module-level `build_network` helper from
  `tests/conftest.py`.

**jsdom capability facts, measured in this worktree** — assume otherwise and
each costs an hour:
- `document.elementFromPoint` is **undefined**. `vi.spyOn(document,
  'elementFromPoint')` throws `Cannot spy on undefined`. Install it with
  `Object.defineProperty(document, 'elementFromPoint', { value: fn,
  configurable: true, writable: true })`.
- `ClipboardEvent` and `DataTransfer` are **undefined**. A copy/paste event has
  to be built by hand.
- `PointerEvent` **is** defined. `fireEvent.pointerDown(el, { button: 0,
  clientX, clientY })` works, and `window.dispatchEvent(new
  MouseEvent('pointermove', { clientX, clientY }))` reaches a
  `window.addEventListener('pointermove', …)` handler.
- A component using `<Handle>` from `@xyflow/react` renders standalone when
  wrapped in `<ReactFlowProvider>`; without it, React Flow's store throws.

**TypeScript.** `strict: true`; `noUncheckedIndexedAccess` is **off**;
`types: []`. The `@/*` path alias (`tsconfig.json:19-21`) is TypeScript-only
and **non-functional at runtime** — use relative imports, as every existing
file does.

**House idioms that are not optional.**
- Modifier keys: `const modifier = e.ctrlKey || e.metaKey` (`App.tsx:491`).
- Query keys: `nk(projectId, root)` (`utils/queryKeys.ts:23-25`). In a
  non-React callback read the id via `useUIStore.getState().currentProject` —
  a mismatched id makes `getQueryData` return `undefined` and silently wipes a
  payload (`queryKeys.ts:16-22`).
- FastAPI error arrays are formatted into readable strings before display,
  never `String([{…}])` (`.cursor/rules/pypsa-gui-frontend.mdc:19`).
- `routers/network.py` is a declared change hotspot
  (`.cursor/rules/pypsa-gui-backend.mdc:27-29`) — surgical edits only.
- The desktop app is not current until `npm run build` then
  `bash pypsa-gui/build-macos.sh` (`CLAUDE.md:56-84`). No task in this plan
  claims the `.app` is current.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `pypsa-gui/frontend/src/layout/Sidebar.drag.test.tsx` (new) | Characterization: pins today's palette drag before any of it moves | 1 |
| `pypsa-gui/frontend/src/hooks/useAssetDrag.ts` (new) | The pointer-drag gesture and the drop resolver — the single owner of "what is under the cursor" | 2 |
| `pypsa-gui/frontend/src/hooks/useAssetDrag.test.tsx` (new) | The four drop outcomes, against a synthetic DOM | 2 |
| `pypsa-gui/frontend/src/layout/Sidebar.tsx:270-303` | `AssetPaletteInline` consumes the hook instead of inlining the gesture | 2 |
| `pypsa-gui/frontend/src/pages/TopologyCanvas.tsx:308` | `BusNode` emits `data-bus-name`; the component becomes a named export so it can be rendered in a test | 3 |
| `pypsa-gui/frontend/src/pages/TopologyCanvas.busnode.test.tsx` (new) | `BusNode` carries the bus name as a DOM attribute | 3 |
| `pypsa-gui/frontend/src/pages/MapCanvas.tsx:28-35,1087` | `busDivIcon` takes a bus name and emits it, HTML-attribute-escaped; becomes a named export | 3 |
| `pypsa-gui/frontend/src/pages/MapCanvas.busicon.test.tsx` (new) | The div-icon HTML carries the escaped name | 3 |
| `pypsa-gui/frontend/src/store/uiStore.ts:14-18` | `CreationRequest` gains `dropBusName?: string` | 2 |
| `pypsa-gui/frontend/src/layout/CreationForm.tsx` | `TERMINAL_FIELD`; terminal prefill on open; the `x`/`y` seed removed | 4, 5 |
| `pypsa-gui/frontend/src/layout/CreationForm.prefill.test.tsx` (new) | Prefill honours the carrier filter; bus drops no longer seed coordinates | 4, 5 |
| `pypsa-gui/frontend/src/layout/AssetPalette.tsx` | **deleted** (294 lines, imported nowhere, and stale) | 6 |

---

## Task 1: Characterize the palette drag before touching it

**Files:**
- Test: `pypsa-gui/frontend/src/layout/Sidebar.drag.test.tsx` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing importable. This task's product is a set of tests that must
  stay green through Tasks 2–6. Later tasks are judged partly on not breaking it.

**Context the implementer needs.** `Sidebar.tsx:270-303` implements a manual
pointer-event drag — not HTML5 drag-and-drop; its own comment
(`Sidebar.tsx:245-253`) records that the HTML5 API "was unreliable in the
user's environment". Three behaviours are load-bearing and **none of them has a
test today** (recon §14 risk 1): a 3-pixel movement threshold promotes a click
into a drag; a click below the threshold calls `setCreationItem({id, label})`
with **no** `dropPosition`; and a release that does not land on `.react-flow`
returns silently, cancelling. Pin all three now, because Task 2 moves every line
of that function into a different file.

The palette is nested: `Sidebar` (expanded mode, the default) renders a `DATA`
section which is open by default, whose `DataSectionContent` renders an
"Assets" row that must be clicked to reveal `AssetPaletteInline`
(`Sidebar.tsx:1235-1248`). The test therefore clicks "Assets" first.

- [ ] **Step 1: Write the characterization test**

Create `pypsa-gui/frontend/src/layout/Sidebar.drag.test.tsx`:

```tsx
// Characterization of the palette's pointer-drag, written BEFORE the gesture
// moves into hooks/useAssetDrag.ts. Sidebar.tsx:270-303 has zero coverage
// today (recon §14 risk 1) and every line of it is about to move, so these
// three cases are the only thing that will notice a behaviour change.
//
// jsdom facts this file depends on, measured in this worktree:
//   • PointerEvent exists, so fireEvent.pointerDown works.
//   • document.elementFromPoint does NOT exist — it must be installed with
//     defineProperty, not vi.spyOn.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { useUIStore } from '../store/uiStore'
import Sidebar from './Sidebar'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: { ...actual.networkApi, getMeta: vi.fn(), undoInfo: vi.fn() },
  }
})
vi.mock('../api/projects', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/projects')>()
  return { ...actual, projectsApi: { ...actual.projectsApi, list: vi.fn() } }
})
vi.mock('../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulation')>()
  return {
    ...actual,
    simulationApi: { ...actual.simulationApi, preflight: vi.fn() },
  }
})

import { networkApi } from '../api/network'
import { projectsApi } from '../api/projects'
import { simulationApi } from '../api/simulation'

/** jsdom has no elementFromPoint. Install one that returns `el` (or null). */
function stubElementFromPoint(el: Element | null) {
  Object.defineProperty(document, 'elementFromPoint', {
    value: () => el,
    configurable: true,
    writable: true,
  })
}

function renderSidebar() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/** Open the DATA ▸ Assets disclosure so the palette items are in the tree. */
async function openPalette() {
  renderSidebar()
  await userEvent.click(screen.getByText('Assets'))
  return screen.getByText('Bus').closest('[role="button"]') as HTMLElement
}

beforeEach(() => {
  vi.mocked(networkApi.getMeta).mockReset().mockResolvedValue({} as never)
  vi.mocked(networkApi.undoInfo).mockReset().mockResolvedValue({ depth: 0 })
  vi.mocked(projectsApi.list).mockReset().mockResolvedValue([])
  vi.mocked(simulationApi.preflight).mockReset().mockResolvedValue({} as never)
  useUIStore.setState({ currentProject: 'Demo', creationItem: null })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null, creationItem: null })
})

describe('palette drag — behaviour as of c2cc4510', () => {
  it('a click with no movement opens the form with NO dropPosition', async () => {
    const item = await openPalette()
    fireEvent.pointerDown(item, { button: 0, clientX: 10, clientY: 10 })
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 10, clientY: 10 }))

    const req = useUIStore.getState().creationItem
    expect(req?.id).toBe('bus')
    expect(req?.dropPosition).toBe(undefined)
  })

  it('a drag past the 3px threshold onto .react-flow carries a dropPosition', async () => {
    const item = await openPalette()

    const canvas = document.createElement('div')
    canvas.className = 'react-flow'
    document.body.appendChild(canvas)
    stubElementFromPoint(canvas)
    ;(window as unknown as { rfInstance?: unknown }).rfInstance = {
      screenToFlowPosition: ({ x, y }: { x: number; y: number }) => ({ x: x * 2, y: y * 2 }),
    }

    fireEvent.pointerDown(item, { button: 0, clientX: 10, clientY: 10 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 90, clientY: 70 }))
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 90, clientY: 70 }))

    const req = useUIStore.getState().creationItem
    expect(req?.id).toBe('bus')
    expect(req?.dropPosition).toEqual({ x: 180, y: 140 })

    delete (window as unknown as { rfInstance?: unknown }).rfInstance
  })

  it('a drag released outside .react-flow cancels silently', async () => {
    const item = await openPalette()

    const elsewhere = document.createElement('div')
    document.body.appendChild(elsewhere)
    stubElementFromPoint(elsewhere)

    fireEvent.pointerDown(item, { button: 0, clientX: 10, clientY: 10 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 300, clientY: 300 }))
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 300, clientY: 300 }))

    expect(useUIStore.getState().creationItem).toBe(null)
  })

  it('a movement of 2px stays a click, not a drag', async () => {
    const item = await openPalette()

    const elsewhere = document.createElement('div')
    document.body.appendChild(elsewhere)
    stubElementFromPoint(elsewhere)

    fireEvent.pointerDown(item, { button: 0, clientX: 10, clientY: 10 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 12, clientY: 12 }))
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 12, clientY: 12 }))

    // Below the 3px threshold `moved` stays false, so this is the click path
    // and the form opens even though the release was outside the canvas.
    expect(useUIStore.getState().creationItem?.id).toBe('bus')
    expect(useUIStore.getState().creationItem?.dropPosition).toBe(undefined)
  })
})
```

- [ ] **Step 2: Run it and confirm all four pass against unmodified source**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/Sidebar.drag.test.tsx
```

Expected: `Test Files  1 passed (1)` and `Tests  4 passed (4)`.

These are characterization tests: they pin behaviour that already exists, so
they must pass on the **first** run. A failure here means the test is wrong,
not the source. Do not "fix" `Sidebar.tsx` to make them pass.

- [ ] **Step 3: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current    # must print: feature/asset-editing
git status --porcelain
git add pypsa-gui/frontend/src/layout/Sidebar.drag.test.tsx
git diff --cached --name-only
git commit -m "test(gui): characterize the palette pointer-drag before extracting it"
```

---

## Task 2: Extract `useAssetDrag` and give it the four-outcome drop resolver

**Files:**
- Create: `pypsa-gui/frontend/src/hooks/useAssetDrag.ts`
- Create: `pypsa-gui/frontend/src/hooks/useAssetDrag.test.tsx`
- Modify: `pypsa-gui/frontend/src/layout/Sidebar.tsx:235-364` (replace the inlined `beginDrag` and its ghost state with the hook)
- Modify: `pypsa-gui/frontend/src/store/uiStore.ts:14-18` (`CreationRequest` gains `dropBusName`)

**Interfaces:**
- Consumes: `useUIStore` from `../store/uiStore`; the existing global handle
  `window.rfInstance` pinned by `TopologyCanvas.tsx:2923-2924`, typed as
  `{ screenToFlowPosition(p: {x: number; y: number}): {x: number; y: number} }`.
- Produces, for Tasks 3–5 and for Plan A/B (which do not consume it, but must
  not contradict it):

```ts
/** What the pointer was over when the drag was released. */
export interface DropResult {
  /** 'schematic' = React Flow (.react-flow); 'map' = Leaflet (.leaflet-container);
   *  null = released outside both, i.e. cancelled. */
  canvas: 'schematic' | 'map' | null
  /** Name of the bus under the pointer, from the nearest [data-bus-name]
   *  ancestor. null when the release did not land on a bus. */
  busName: string | null
  /** React Flow flow-space coordinates. Non-null ONLY for a schematic drop
   *  with window.rfInstance present. Always null on the map (D26). */
  position: { x: number; y: number } | null
}

export function resolveDrop(clientX: number, clientY: number): DropResult

export interface AssetDragItem { id: string; label: string }

export function useAssetDrag(): {
  ghost: { label: string; x: number; y: number } | null
  beginDrag: (e: React.PointerEvent, item: AssetDragItem) => void
}
```

  and, in `uiStore.ts`:

```ts
export interface CreationRequest {
  id: string
  label: string
  dropPosition?: { x: number; y: number }
  /** Bus the palette item was dropped on. Consumed by CreationForm's
   *  terminal prefill (D27). Absent when the drop did not land on a bus. */
  dropBusName?: string
}
```

**Context the implementer needs.** D25 fixes the hit-test order and it is not
negotiable: `[data-bus-name]` first, then `.react-flow`, then
`.leaflet-container`, then cancel. Testing `[data-bus-name]` first is what lets
one attribute serve both canvases; the alternative — React Flow's own `data-id`
— would tie the resolver to `@xyflow/react`'s internal markup and would still
need a second check to tell a `bus` node from an `assetGroup` node
(`TopologyCanvas.tsx:1786`).

D26 is why `position` is schematic-only: map drops prefill terminals and
nothing else, so `containerPointToLatLng` is never called and **no global
Leaflet handle is added**. Do not add a `window.leafletMap` — the missing
handle is deliberate, not an oversight.

Neither canvas emits `data-bus-name` yet; Task 3 adds it. This task's tests
build the DOM by hand, so it is complete and reviewable on its own.

- [ ] **Step 1: Write the failing test for the resolver**

Create `pypsa-gui/frontend/src/hooks/useAssetDrag.test.tsx`:

```tsx
// The drop resolver's four outcomes (spec D25), plus the gesture's threshold.
//
// jsdom has no document.elementFromPoint, so every test installs one that
// returns the element it wants the pointer to have been over.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
import { useAssetDrag, resolveDrop } from './useAssetDrag'
import { useUIStore } from '../store/uiStore'

function stubElementFromPoint(el: Element | null) {
  Object.defineProperty(document, 'elementFromPoint', {
    value: () => el,
    configurable: true,
    writable: true,
  })
}

function mount(className: string, busName?: string): HTMLElement {
  const el = document.createElement('div')
  if (className) el.className = className
  if (busName !== undefined) el.setAttribute('data-bus-name', busName)
  document.body.appendChild(el)
  return el
}

beforeEach(() => {
  document.body.innerHTML = ''
  useUIStore.setState({ creationItem: null })
  ;(window as unknown as { rfInstance?: unknown }).rfInstance = {
    screenToFlowPosition: ({ x, y }: { x: number; y: number }) => ({ x: x + 1, y: y + 1 }),
  }
})

afterEach(() => {
  vi.restoreAllMocks()
  delete (window as unknown as { rfInstance?: unknown }).rfInstance
  useUIStore.setState({ creationItem: null })
})

describe('resolveDrop — the four outcomes', () => {
  it('a bus marker inside the schematic canvas is a bus drop with a position', () => {
    const canvas = mount('react-flow')
    const node = document.createElement('div')
    node.setAttribute('data-bus-name', 'Bus A')
    canvas.appendChild(node)
    stubElementFromPoint(node)

    expect(resolveDrop(10, 20)).toEqual({
      canvas: 'schematic',
      busName: 'Bus A',
      position: { x: 11, y: 21 },
    })
  })

  it('a bus marker inside the map canvas is a bus drop with NO position', () => {
    const canvas = mount('leaflet-container')
    const marker = document.createElement('div')
    marker.setAttribute('data-bus-name', 'Bus B')
    canvas.appendChild(marker)
    stubElementFromPoint(marker)

    expect(resolveDrop(10, 20)).toEqual({
      canvas: 'map',
      busName: 'Bus B',
      position: null,
    })
  })

  it('empty schematic canvas is a schematic drop with no bus', () => {
    stubElementFromPoint(mount('react-flow'))
    expect(resolveDrop(10, 20)).toEqual({
      canvas: 'schematic',
      busName: null,
      position: { x: 11, y: 21 },
    })
  })

  it('empty map canvas is a map drop with no bus and no position', () => {
    stubElementFromPoint(mount('leaflet-container'))
    expect(resolveDrop(10, 20)).toEqual({
      canvas: 'map',
      busName: null,
      position: null,
    })
  })

  it('anything else cancels', () => {
    stubElementFromPoint(mount('some-other-panel'))
    expect(resolveDrop(10, 20)).toEqual({ canvas: null, busName: null, position: null })
  })

  it('cancels when the pointer is over nothing at all', () => {
    stubElementFromPoint(null)
    expect(resolveDrop(10, 20)).toEqual({ canvas: null, busName: null, position: null })
  })

  it('a schematic drop with no rfInstance still resolves, with a null position', () => {
    delete (window as unknown as { rfInstance?: unknown }).rfInstance
    stubElementFromPoint(mount('react-flow'))
    expect(resolveDrop(10, 20)).toEqual({
      canvas: 'schematic',
      busName: null,
      position: null,
    })
  })
})

function Harness() {
  const { ghost, beginDrag } = useAssetDrag()
  return (
    <div>
      <div
        data-testid="item"
        role="button"
        onPointerDown={(e) => beginDrag(e, { id: 'thermal', label: 'Thermal' })}
      />
      <span data-testid="ghost">{ghost ? `${ghost.label}@${ghost.x},${ghost.y}` : 'none'}</span>
    </div>
  )
}

describe('useAssetDrag — the gesture', () => {
  it('a click below the 3px threshold opens the form with no drop data', () => {
    const { getByTestId } = render(<Harness />)
    stubElementFromPoint(mount('react-flow'))

    fireEvent.pointerDown(getByTestId('item'), { button: 0, clientX: 5, clientY: 5 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 6, clientY: 6 }))
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 6, clientY: 6 }))

    expect(useUIStore.getState().creationItem).toEqual({ id: 'thermal', label: 'Thermal' })
  })

  it('a drag onto a bus sets dropBusName as well as dropPosition', () => {
    const { getByTestId } = render(<Harness />)
    const canvas = mount('react-flow')
    const node = document.createElement('div')
    node.setAttribute('data-bus-name', 'Bus A')
    canvas.appendChild(node)
    stubElementFromPoint(node)

    fireEvent.pointerDown(getByTestId('item'), { button: 0, clientX: 5, clientY: 5 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 60, clientY: 70 }))
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 60, clientY: 70 }))

    expect(useUIStore.getState().creationItem).toEqual({
      id: 'thermal',
      label: 'Thermal',
      dropPosition: { x: 61, y: 71 },
      dropBusName: 'Bus A',
    })
  })

  it('a drag onto a map bus sets dropBusName and no dropPosition', () => {
    const { getByTestId } = render(<Harness />)
    const canvas = mount('leaflet-container')
    const marker = document.createElement('div')
    marker.setAttribute('data-bus-name', 'Bus B')
    canvas.appendChild(marker)
    stubElementFromPoint(marker)

    fireEvent.pointerDown(getByTestId('item'), { button: 0, clientX: 5, clientY: 5 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 60, clientY: 70 }))
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 60, clientY: 70 }))

    expect(useUIStore.getState().creationItem).toEqual({
      id: 'thermal',
      label: 'Thermal',
      dropBusName: 'Bus B',
    })
  })

  it('a drag released outside both canvases changes nothing', () => {
    const { getByTestId } = render(<Harness />)
    stubElementFromPoint(mount('unrelated'))

    fireEvent.pointerDown(getByTestId('item'), { button: 0, clientX: 5, clientY: 5 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 60, clientY: 70 }))
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 60, clientY: 70 }))

    expect(useUIStore.getState().creationItem).toBe(null)
  })

  it('the ghost follows the pointer while dragging and clears on release', () => {
    const { getByTestId } = render(<Harness />)
    stubElementFromPoint(mount('unrelated'))

    fireEvent.pointerDown(getByTestId('item'), { button: 0, clientX: 5, clientY: 5 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 60, clientY: 70 }))
    expect(getByTestId('ghost').textContent).toBe('Thermal@60,70')

    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 60, clientY: 70 }))
    expect(getByTestId('ghost').textContent).toBe('none')
  })

  it('ignores a non-left button', () => {
    const { getByTestId } = render(<Harness />)
    fireEvent.pointerDown(getByTestId('item'), { button: 2, clientX: 5, clientY: 5 })
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 5, clientY: 5 }))
    expect(useUIStore.getState().creationItem).toBe(null)
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/hooks/useAssetDrag.test.tsx
```

Expected: the file fails to collect with
`Failed to resolve import "./useAssetDrag"`. Not a single test runs yet.

- [ ] **Step 3: Add `dropBusName` to `CreationRequest`**

In `pypsa-gui/frontend/src/store/uiStore.ts`, replace the `CreationRequest`
interface at `:14-18`:

```ts
export interface CreationRequest {
  id: string
  label: string
  dropPosition?: { x: number; y: number }
  // Bus the palette item was released over, from the [data-bus-name]
  // attribute both canvases publish. CreationForm uses it to prefill the
  // asset's terminal field (spec D27). Absent when the drop landed on empty
  // canvas — dropping onto nothing must stay exactly as permissive as it is
  // today, so this is optional and never validated here.
  dropBusName?: string
}
```

- [ ] **Step 4: Write the hook**

Create `pypsa-gui/frontend/src/hooks/useAssetDrag.ts`:

```ts
import { useState } from 'react'
import { useUIStore } from '../store/uiStore'

// ── Palette drag, extracted from Sidebar.tsx ─────────────────────────────────
// Manual pointer-event drag, NOT HTML5 drag-and-drop: the HTML5 API was
// unreliable in the user's environment (drops didn't land), so the gesture is
// tracked by hand — pointerdown → pointermove → pointerup — with our own
// ghost and our own hit-test via document.elementFromPoint.
//
// This file is the ONLY owner of the hit-test. The duplicate that lived in
// layout/AssetPalette.tsx is deleted, not migrated: that file was dead and its
// palette was stale (it labelled `hydrogen` "P2G / Electrolysis" while
// FIELD_MAP.hydrogen is a StorageUnit).

/** What the pointer was over when the drag was released. */
export interface DropResult {
  /** 'schematic' = React Flow (.react-flow); 'map' = Leaflet
   *  (.leaflet-container); null = released outside both, i.e. cancelled. */
  canvas: 'schematic' | 'map' | null
  /** Name of the bus under the pointer, from the nearest [data-bus-name]
   *  ancestor. null when the release did not land on a bus. */
  busName: string | null
  /** React Flow flow-space coordinates. Non-null ONLY for a schematic drop
   *  with window.rfInstance present. Always null on the map: map drops
   *  prefill terminals only, so no coordinate conversion is needed and no
   *  global Leaflet handle exists to do it with (spec D26). */
  position: { x: number; y: number } | null
}

export interface AssetDragItem { id: string; label: string }

const DRAG_THRESHOLD_PX = 3

type RfInstance = { screenToFlowPosition: (p: { x: number; y: number }) => { x: number; y: number } }

function flowPosition(clientX: number, clientY: number): { x: number; y: number } | null {
  // TopologyCanvas pins the instance to window in onInit (TopologyCanvas.tsx
  // :2923-2924). Without it a new node would land at (0,0) regardless of
  // where it was dropped, so a missing handle degrades to "no position"
  // rather than to a wrong one.
  const rf = (window as unknown as { rfInstance?: RfInstance }).rfInstance
  return rf?.screenToFlowPosition({ x: clientX, y: clientY }) ?? null
}

/**
 * Resolve a release point to a drop outcome. Evaluation order is fixed:
 *   1. [data-bus-name]      → a bus drop, carrying that bus's name
 *   2. .react-flow          → the schematic canvas, no bus
 *   3. .leaflet-container   → the map canvas, no bus
 *   4. otherwise            → cancelled
 * Testing the bus attribute FIRST is what lets one attribute serve both
 * canvases. Using React Flow's own `data-id` instead would tie this to
 * @xyflow/react's internal markup and would still need a second check to tell
 * a `bus` node from an `assetGroup` node (TopologyCanvas.tsx:1786).
 */
export function resolveDrop(clientX: number, clientY: number): DropResult {
  const target = document.elementFromPoint(clientX, clientY)
  const busEl = target?.closest('[data-bus-name]') ?? null
  const busName = busEl?.getAttribute('data-bus-name') ?? null

  const schematic = target?.closest('.react-flow') ?? null
  if (schematic) {
    return { canvas: 'schematic', busName, position: flowPosition(clientX, clientY) }
  }
  const map = target?.closest('.leaflet-container') ?? null
  if (map) {
    return { canvas: 'map', busName, position: null }
  }
  return { canvas: null, busName: null, position: null }
}

export function useAssetDrag(): {
  ghost: { label: string; x: number; y: number } | null
  beginDrag: (e: React.PointerEvent, item: AssetDragItem) => void
} {
  // Drag ghost — a fixed-position chip that follows the cursor. The caller
  // renders it with pointer-events:none so pointerup passes through to the
  // real drop target underneath.
  const [ghost, setGhost] = useState<{ label: string; x: number; y: number } | null>(null)
  const setCreationItem = useUIStore(s => s.setCreationItem)

  function beginDrag(e: React.PointerEvent, item: AssetDragItem) {
    if (e.button !== 0) return  // left button only
    e.preventDefault()
    const startX = e.clientX
    const startY = e.clientY
    let moved = false

    const onMove = (ev: PointerEvent) => {
      const dx = Math.abs(ev.clientX - startX)
      const dy = Math.abs(ev.clientY - startY)
      if (!moved && (dx > DRAG_THRESHOLD_PX || dy > DRAG_THRESHOLD_PX)) {
        moved = true
        document.body.style.cursor = 'grabbing'
      }
      if (moved) setGhost({ label: item.label, x: ev.clientX, y: ev.clientY })
    }

    const onUp = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      document.body.style.cursor = ''
      setGhost(null)

      if (!moved) {
        // Click — open the slide-in panel with no drop data at all.
        setCreationItem({ id: item.id, label: item.label })
        return
      }

      const drop = resolveDrop(ev.clientX, ev.clientY)
      if (drop.canvas === null) return  // released outside both canvases — cancel silently

      setCreationItem({
        id: item.id,
        label: item.label,
        ...(drop.position ? { dropPosition: drop.position } : {}),
        ...(drop.busName ? { dropBusName: drop.busName } : {}),
      })
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  return { ghost, beginDrag }
}
```

- [ ] **Step 5: Run the hook's tests**

Run:

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/hooks/useAssetDrag.test.tsx
```

Expected: `Tests  13 passed (13)`.

- [ ] **Step 6: Make `Sidebar.tsx` consume the hook**

In `pypsa-gui/frontend/src/layout/Sidebar.tsx`, inside `AssetPaletteInline`
(`:235`):

1. Delete the `ghost` `useState` declaration (`:241`) and the whole
   `function beginDrag(…) { … }` block (`:254-303`), including its comment
   header at `:245-253`.
2. Replace the removed `setCreationItem` destructure line (`:237`,
   `const { setCreationItem } = useUIStore()`) with the hook call — the
   click-to-open path now lives inside the hook too:

```tsx
  const { ghost, beginDrag } = useAssetDrag()
  const setCreationItem = useUIStore(s => s.setCreationItem)
```

   `setCreationItem` is still needed directly for the keyboard path at
   `Sidebar.tsx:336-339` (Enter/Space on a palette item), which is not a drag.

3. Add the import beside the existing `useUIStore` import (`Sidebar.tsx:16`):

```tsx
import { useAssetDrag } from '../hooks/useAssetDrag'
```

Everything else in `AssetPaletteInline` — the `onPointerDown={(e) => beginDrag(e, …)}`
wiring at `:329`, the ghost JSX at `:353-361`, the collapse state, the
`role="button"` items — stays exactly as it is.

- [ ] **Step 7: Run Task 1's characterization tests plus the hook's**

Run:

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/Sidebar.drag.test.tsx src/hooks/useAssetDrag.test.tsx
```

Expected: `Test Files  2 passed (2)`, `Tests  17 passed (17)`. Task 1's four
cases passing unmodified is the proof that the extraction changed no behaviour.

- [ ] **Step 8: Type-check and commit**

```bash
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: exit 0, `vite build` prints `✓ built in …`. No TypeScript errors.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/hooks/useAssetDrag.ts \
        pypsa-gui/frontend/src/hooks/useAssetDrag.test.tsx \
        pypsa-gui/frontend/src/layout/Sidebar.tsx \
        pypsa-gui/frontend/src/store/uiStore.ts
git diff --cached --name-only
git commit -m "feat(gui): extract the palette drag into useAssetDrag with a bus-aware drop resolver"
```

---

## Task 3: Both canvases publish `data-bus-name`

**Files:**
- Modify: `pypsa-gui/frontend/src/pages/TopologyCanvas.tsx:308` (`BusNode`)
- Create: `pypsa-gui/frontend/src/pages/TopologyCanvas.busnode.test.tsx`
- Modify: `pypsa-gui/frontend/src/pages/MapCanvas.tsx:28-35` (`busDivIcon`) and `:1087` (its call site)
- Create: `pypsa-gui/frontend/src/pages/MapCanvas.busicon.test.tsx`

**Interfaces:**
- Consumes: `resolveDrop`'s contract from Task 2 — specifically that it reads
  `closest('[data-bus-name]')?.getAttribute('data-bus-name')`.
- Produces:
  - `export function BusNode(props: NodeProps): JSX.Element` — newly a **named**
    export of `TopologyCanvas.tsx`. The default export and `nodeTypes`
    (`:1786`) are unchanged.
  - `export function busDivIcon(color: string, name: string): L.DivIcon` —
    newly a **named** export of `MapCanvas.tsx`, and newly two-argument.

**Context the implementer needs — the two canvases are not the same size of
job, and the plan does not pretend they are.**

*Schematic:* everything it needs already exists. Node ids **are** bus names
(`TopologyCanvas.tsx:2232` builds `{ id: b.name, type: 'bus', … }`), React Flow
already renders `data-id` on every node wrapper, and `screenToFlowPosition` is
already pinned globally. The change is **one attribute on one `<div>`**. It is
still worth doing rather than reading React Flow's `data-id`, for the reason in
Task 2's comment.

*Map:* three of the four pieces recon inventoried were missing. Task 2 supplied
the first (a drop now reaches Leaflet at all — before it, `Sidebar.tsx:287-288`
returned early because nothing in the Leaflet tree matches `.react-flow`). This
task supplies the second: `busDivIcon(color)` at `MapCanvas.tsx:28-35` builds
its marker as an **HTML string** and carries no bus name, so
`elementFromPoint(...).closest('.pypsa-bus-marker')` today tells you *a* bus was
hit but not *which*. Because the markup is a string, the name **must be
HTML-attribute-escaped** — a bus called `A"B` would otherwise break out of the
attribute and produce malformed markup. The third missing piece (a global
Leaflet handle) is deliberately not built: D26.

Leaflet's `divIcon` renders the `html` string inside a container element that
carries `className`, so the `data-bus-name` goes on the inner `<div>` and
`closest()` finds it from any descendant.

- [ ] **Step 1: Write the failing test for the schematic node**

Create `pypsa-gui/frontend/src/pages/TopologyCanvas.busnode.test.tsx`:

```tsx
// BusNode must publish the bus name as a DOM attribute so useAssetDrag's
// resolveDrop can recover it with closest('[data-bus-name]').
//
// BusNode renders <Handle> from @xyflow/react, which reads React Flow's
// zustand store — rendering it bare throws. <ReactFlowProvider> supplies the
// store, which is enough to render one node in isolation (measured).
import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { ReactFlowProvider } from '@xyflow/react'
import { BusNode } from './TopologyCanvas'
import type { Bus } from '../api/types'

const BUS: Bus = {
  name: 'DE0 0', v_nom: 380, carrier: 'AC', x: 6.9, y: 50.9,
  country: 'DE', unit: '', control: 'PQ', sub_network: '',
}

function renderNode(bus: Bus) {
  return render(
    <ReactFlowProvider>
      {/* NodeProps is wider than what BusNode reads; it reads id, data and
          selected only. The cast keeps the test to the real call shape. */}
      <BusNode
        {...({ id: bus.name, data: { bus }, selected: false } as never)}
      />
    </ReactFlowProvider>,
  )
}

describe('BusNode DOM identity', () => {
  it('carries the bus name in data-bus-name', () => {
    const { container } = renderNode(BUS)
    const el = container.querySelector('[data-bus-name]')
    expect(el?.getAttribute('data-bus-name')).toBe('DE0 0')
  })

  it('the attribute is findable from a descendant with closest()', () => {
    const { container } = renderNode(BUS)
    const label = Array.from(container.querySelectorAll('span'))
      .find(s => s.textContent === 'DE0 0')
    expect(label?.closest('[data-bus-name]')?.getAttribute('data-bus-name')).toBe('DE0 0')
  })
})
```

- [ ] **Step 2: Write the failing test for the map marker**

Create `pypsa-gui/frontend/src/pages/MapCanvas.busicon.test.tsx`:

```tsx
// busDivIcon builds its marker as an HTML STRING, so the bus name has to be
// attribute-escaped on the way in. A bus called `A"B` would otherwise close
// the attribute early and produce markup whose data-bus-name is 'A'.
import { describe, expect, it } from 'vitest'
import { busDivIcon } from './MapCanvas'

describe('busDivIcon', () => {
  it('emits the bus name as data-bus-name', () => {
    const html = busDivIcon('#ff0000', 'DE0 0').options.html as string
    expect(html).toContain('data-bus-name="DE0 0"')
  })

  it('escapes the four characters that would break out of the attribute', () => {
    const html = busDivIcon('#ff0000', `A"B&C<D>E`).options.html as string
    expect(html).toContain('data-bus-name="A&quot;B&amp;C&lt;D&gt;E"')
  })

  it('keeps the marker class the rest of MapCanvas styles against', () => {
    expect(busDivIcon('#ff0000', 'B1').options.className).toBe('pypsa-bus-marker')
  })

  it('parses back to an element whose attribute is the original name', () => {
    const wrapper = document.createElement('div')
    wrapper.innerHTML = busDivIcon('#ff0000', `A"B`).options.html as string
    expect(
      wrapper.firstElementChild?.getAttribute('data-bus-name'),
    ).toBe(`A"B`)
  })
})
```

- [ ] **Step 3: Run both and watch them fail**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/pages/TopologyCanvas.busnode.test.tsx src/pages/MapCanvas.busicon.test.tsx
```

Expected: both files fail to collect —
`"BusNode" is not exported by "src/pages/TopologyCanvas.tsx"` and
`"busDivIcon" is not exported by "src/pages/MapCanvas.tsx"`.

- [ ] **Step 4: Make `BusNode` an export and emit the attribute**

In `pypsa-gui/frontend/src/pages/TopologyCanvas.tsx`, change the declaration at
`:308` from `function BusNode(` to:

```tsx
// Exported so a test can render one node in isolation inside a
// <ReactFlowProvider>. nodeTypes (:1786) still references it locally.
export function BusNode({ id, data, selected }: NodeProps) {
```

and add `data-bus-name` to the outer `<div>` that begins at `:327`:

```tsx
    <div
      data-bus-name={bus.name}
      className="relative flex items-center gap-1.5 transition-all"
```

Nothing else in `BusNode` changes.

- [ ] **Step 5: Give `busDivIcon` a name parameter**

In `pypsa-gui/frontend/src/pages/MapCanvas.tsx`, replace `busDivIcon` at
`:28-35`:

```tsx
/**
 * HTML-attribute escaping for the divIcon's `html` string. The marker markup
 * is built as a string, so a bus called `A"B` would close the attribute early
 * and the drop hit-test would recover the wrong name. Escaping & first is
 * required — doing it later would double-escape the entities the other
 * replacements introduce.
 */
function escapeAttr(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

// Draggable bus marker. Mimics the previous CircleMarker visually (12 px,
// 2 px coloured border, white fill) but uses a Marker + divIcon so leaflet
// gives us the `draggable` capability and a `dragend` event. The cursor
// changes to "grab" so users discover that the dot is draggable.
//
// `data-bus-name` is the drop hit-test's only handle on which bus was hit —
// the same attribute TopologyCanvas's BusNode publishes, so hooks/
// useAssetDrag.ts needs exactly one branch for both canvases (spec D25).
export function busDivIcon(color: string, name: string): L.DivIcon {
  return L.divIcon({
    className: 'pypsa-bus-marker',
    html: `<div data-bus-name="${escapeAttr(name)}" style="width:12px;height:12px;border:2px solid ${color};background:#fff;border-radius:50%;box-sizing:border-box;cursor:grab;"></div>`,
    iconSize: [12, 12],
    iconAnchor: [6, 6],
  })
}
```

- [ ] **Step 6: Update the call site**

In `pypsa-gui/frontend/src/pages/MapCanvas.tsx:1087`, inside the bus `<Marker>`:

```tsx
              icon={busDivIcon(colour, bus.name)}
```

`bus` is already in scope — it is the `map` callback parameter at `:1075`.

- [ ] **Step 7: Run the tests**

Run:

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/pages/TopologyCanvas.busnode.test.tsx src/pages/MapCanvas.busicon.test.tsx
```

Expected: `Test Files  2 passed (2)`, `Tests  6 passed (6)`.

- [ ] **Step 8: Type-check and commit**

```bash
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: exit 0. A `busDivIcon` call site left at one argument would fail here
with `Expected 2 arguments, but got 1`.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/pages/TopologyCanvas.tsx \
        pypsa-gui/frontend/src/pages/TopologyCanvas.busnode.test.tsx \
        pypsa-gui/frontend/src/pages/MapCanvas.tsx \
        pypsa-gui/frontend/src/pages/MapCanvas.busicon.test.tsx
git diff --cached --name-only
git commit -m "feat(gui): publish data-bus-name from both the schematic and map bus renderers"
```

---

## Task 4: Terminal prefill on the creation form

**Files:**
- Modify: `pypsa-gui/frontend/src/layout/CreationForm.tsx` (add `TERMINAL_FIELD` beside `AUTO_PREFIX` at `:244`; prefill inside the `useState` initialiser at `:383-394`)
- Create: `pypsa-gui/frontend/src/layout/CreationForm.prefill.test.tsx`

**Interfaces:**
- Consumes: `CreationRequest.dropBusName` (Task 2).
- Produces:

```ts
/**
 * Palette id → the field a drop-on-a-bus prefills. 17 of the 18 palette items
 * have one; `bus` is itself the terminal and is deliberately absent.
 */
export const TERMINAL_FIELD: Record<string, 'bus' | 'bus0'>
```

**Context the implementer needs.** D27: the prefilled field is `bus` for the
eleven single-terminal types and `bus0` for the six branch types (`line`,
`transformer`, `electrolyzer`, `fuel_cell`, `power_to_heat`, `chp`). `bus` — the
palette item — has no terminal, so dropping a bus on a bus behaves as a plain
canvas drop.

The prefill is **conditional on the field's carrier filter**. Six of the
`FIELD_MAP` bus fields carry a `busCarrierFilter` (`BusFieldSpec`), and
`filteredBusNames(filter)` at `CreationForm.tsx:375-381` is the existing
predicate. Writing a hydrogen bus into an electricity-only terminal because the
user happened to drop there would create a form the backend cannot satisfy; when
the filter rejects the bus, the form opens **unprefilled** and the existing
mismatch line ("No H₂ bus in network — add one first", `CreationForm.tsx:518-523`)
still renders. That is the whole of success criterion 37.

`filteredBusNames` is declared above the `useState` initialiser and closes over
`allBuses`, so it is legal to call from inside the initialiser.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/layout/CreationForm.prefill.test.tsx`:

```tsx
// Terminal prefill (spec D27) and the coordinate-seed removal (D28).
//
// The bus list CreationForm reads is the React Query cache under
// nk(currentProject, 'buses') — seeded directly with setQueryData rather than
// through a mocked fetch, because the form reads it with getQueryData, not
// useQuery (CreationForm.tsx:366-369).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import CreationForm from './CreationForm'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getCarriers: vi.fn(async () => []),
      createBus: vi.fn(),
      createGenerator: vi.fn(),
      createLink: vi.fn(),
    },
  }
})

const BUSES = [
  { name: 'Elec A', carrier: 'AC' },
  { name: 'H2 A', carrier: 'H2' },
]

function renderForm(item: { id: string; label: string; dropBusName?: string; dropPosition?: { x: number; y: number } }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  client.setQueryData(nk('Demo', 'buses'), BUSES)
  return render(
    <QueryClientProvider client={client}>
      <CreationForm item={item} />
    </QueryClientProvider>,
  )
}

/** The BusAutocomplete input rendered under a given field label. */
function busInputFor(label: string): HTMLInputElement {
  const wrapper = screen.getByText(label).parentElement as HTMLElement
  return wrapper.querySelector('input[type="text"]') as HTMLInputElement
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', creationItem: null })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null, creationItem: null })
})

describe('terminal prefill', () => {
  it('a Generator dropped on a bus opens with `bus` prefilled', () => {
    renderForm({ id: 'thermal', label: 'Thermal', dropBusName: 'Elec A' })
    expect(busInputFor('Attach to Bus *').value).toBe('Elec A')
  })

  it('an Electrolyzer dropped on a hydrogen bus leaves bus0 EMPTY', () => {
    // bus0 is filtered to non-h2 (CreationForm.tsx:132). Prefilling it with an
    // H2 bus would write a terminal the backend cannot use.
    renderForm({ id: 'electrolyzer', label: 'Electrolyzer', dropBusName: 'H2 A' })
    expect(busInputFor('Electricity bus (input) *').value).toBe('')
  })

  it('an Electrolyzer dropped on an electricity bus DOES prefill bus0', () => {
    renderForm({ id: 'electrolyzer', label: 'Electrolyzer', dropBusName: 'Elec A' })
    expect(busInputFor('Electricity bus (input) *').value).toBe('Elec A')
  })

  it('a bus name that is not in the network is not prefilled', () => {
    renderForm({ id: 'thermal', label: 'Thermal', dropBusName: 'Ghost' })
    expect(busInputFor('Attach to Bus *').value).toBe('')
  })

  it('a drop with no bus leaves the terminal empty', () => {
    renderForm({ id: 'thermal', label: 'Thermal' })
    expect(busInputFor('Attach to Bus *').value).toBe('')
  })

  it('a Bus dropped on a bus prefills nothing — it IS the terminal', () => {
    renderForm({ id: 'bus', label: 'Bus', dropBusName: 'Elec A' })
    // The Bus form has no bus field at all; the assertion is that rendering
    // does not throw and the name field holds the auto-generated name.
    expect((screen.getByDisplayValue(/^Bus \d+$/) as HTMLInputElement).value)
      .toMatch(/^Bus \d+$/)
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/CreationForm.prefill.test.tsx
```

Expected: 3 failures — the two prefill cases assert `'Elec A'` and receive
`''`. The three "leaves it empty" cases and the Bus case pass already, which is
correct: they assert behaviour that must not change.

- [ ] **Step 3: Add the terminal map**

In `pypsa-gui/frontend/src/layout/CreationForm.tsx`, immediately above
`AUTO_PREFIX` (`:244`):

```ts
/**
 * Palette id → the field a drop-on-a-bus prefills (spec D27).
 *
 * 17 of the 18 palette items have a terminal. `bus` is deliberately absent:
 * it IS the terminal, so dropping a bus onto a bus behaves as a plain canvas
 * drop. The six branch types take `bus0`; the eleven single-terminal types
 * take `bus`.
 *
 * The orphan FIELD_MAP keys `link` / `generator` / `load` are not listed —
 * they are unreachable from the live UI and are deleted in the same change as
 * AssetPalette.tsx (spec D29).
 */
export const TERMINAL_FIELD: Record<string, 'bus' | 'bus0'> = {
  line: 'bus0', transformer: 'bus0',
  electrolyzer: 'bus0', fuel_cell: 'bus0', power_to_heat: 'bus0', chp: 'bus0',
  thermal: 'bus', renewable: 'bus',
  battery: 'bus', psh: 'bus', caes: 'bus', flywheel: 'bus', hydrogen: 'bus',
  thermal_storage: 'bus',
  load_elec: 'bus', load_h2: 'bus', load_heat: 'bus',
}
```

- [ ] **Step 4: Prefill in the form's initialiser**

In `CreationForm.tsx`, inside the `useState` initialiser, replace the drop-position
block at `:386-392` with the terminal prefill (the coordinate seed is removed
here as well — that is Task 5's subject, and Task 5's test proves it):

```ts
    // Terminal prefill from a drop-on-a-bus (spec D27). Applied only when the
    // target bus passes the field's own carrier filter — otherwise the form
    // opens unprefilled and the existing mismatch line ("No H₂ bus in
    // network…") does the explaining, rather than a hydrogen bus silently
    // landing in an electricity-only terminal.
    const terminal = TERMINAL_FIELD[item.id]
    if (item.dropBusName && terminal) {
      const spec = fields.find(f => f.key === terminal) as BusFieldSpec | undefined
      if (spec && filteredBusNames(spec.busCarrierFilter).includes(item.dropBusName)) {
        init[terminal] = item.dropBusName
      }
    }
    return init
```

- [ ] **Step 5: Run the test**

Run:

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/CreationForm.prefill.test.tsx
```

Expected: `Tests  6 passed (6)`.

- [ ] **Step 6: Type-check and commit**

```bash
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: exit 0.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/CreationForm.tsx \
        pypsa-gui/frontend/src/layout/CreationForm.prefill.test.tsx
git diff --cached --name-only
git commit -m "feat(gui): prefill the dropped bus into the asset's terminal, respecting the carrier filter"
```

---

## Task 5: Schematic drops stop writing `x`/`y`

**Files:**
- Modify: `pypsa-gui/frontend/src/layout/CreationForm.tsx:80-81` (comment) and `:449-451` (the `setPendingNodePosition` handoff, kept)
- Modify: `pypsa-gui/frontend/src/layout/CreationForm.prefill.test.tsx` (add the coordinate cases)

**Interfaces:**
- Consumes: `CreationRequest.dropPosition` (unchanged shape).
- Produces: nothing new. The behaviour change is that `init.x` / `init.y` keep
  `FIELD_MAP.bus`'s `'0'` defaults on every drop.

**Context the implementer needs.** `Sidebar.tsx:296` obtained `dropPosition`
from `rfInstance.screenToFlowPosition(...)` — **React Flow flow-space pixels** —
and `CreationForm.tsx:389-392` wrote them into `init.x` ("Longitude") and
`init.y` ("Latitude"), which `createMut` then sent verbatim. Two outcomes, and
the second is the dangerous one: coordinates outside ±180/±90 are quarantined by
`utils/geo.ts:45` and the bus lands in `UnplacedBusesPanel` — visible and
recoverable — but a flow-space point that happens to fall in range, say
`(45, 30)`, is accepted as a **real geographic position**, and `update_bus` then
rewrites connected line lengths measured to a bus off the coast of Egypt.

D28 removes the seed and keeps `setPendingNodePosition` (`:449-451`), so the new
node still appears where it was dropped — through the canvas's position cache,
which is a UI concern, rather than through PyPSA's lon/lat, which is a data
concern. A bus created by a drop therefore lands at `x == 0 and y == 0`, which
is precisely the `UnplacedBusesPanel` predicate
(`2026-07-30-unplaced-buses-map-design.md` D1, implemented at `utils/geo.ts:44`).

If Task 4 already deleted the seed block in its Step 4, this task's Step 2 is a
no-op verification — run the tests and confirm.

- [ ] **Step 1: Add the failing coordinate cases**

Append to the `describe` block in
`pypsa-gui/frontend/src/layout/CreationForm.prefill.test.tsx`:

```tsx
describe('schematic drops no longer seed coordinates (spec D28)', () => {
  it('a Bus dropped at flow-space (45, 30) opens with x=0 and y=0', () => {
    // (45, 30) is in-range for lon/lat, so before D28 this produced a bus
    // silently placed off the coast of Egypt rather than an unplaced one.
    renderForm({ id: 'bus', label: 'Bus', dropPosition: { x: 45, y: 30 } })
    const lon = screen.getByText('Longitude').parentElement
      ?.querySelector('input') as HTMLInputElement
    const lat = screen.getByText('Latitude').parentElement
      ?.querySelector('input') as HTMLInputElement
    expect(lon.value).toBe('0')
    expect(lat.value).toBe('0')
  })

  it('a Bus dropped at out-of-range flow-space also opens with x=0 and y=0', () => {
    renderForm({ id: 'bus', label: 'Bus', dropPosition: { x: 1420.5, y: 883.25 } })
    const lon = screen.getByText('Longitude').parentElement
      ?.querySelector('input') as HTMLInputElement
    expect(lon.value).toBe('0')
  })
})
```

- [ ] **Step 2: Run and confirm the seed is gone**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/CreationForm.prefill.test.tsx
```

Expected: `Tests  8 passed (8)`.

If the two new cases fail with `expected '45' to be '0'`, the seed block at
`CreationForm.tsx:386-392` is still present — delete it now, keeping the
`return init` line, then re-run.

- [ ] **Step 3: Update the stale field comment**

In `CreationForm.tsx`, replace the `x`/`y` comment inside `FIELD_MAP.bus`
(`:75-79`), which still describes the deleted behaviour:

```ts
    // x/y are the bus's geographic coordinates (longitude / latitude).
    // A canvas drop does NOT seed them: React Flow flow-space pixels are not
    // a geographic position, and an in-range pair (e.g. 45, 30) would be
    // accepted as one — a bus silently placed off the coast of Egypt, with
    // the backend then measuring line lengths to it. A dropped bus keeps
    // these '0' defaults, lands as unplaced (utils/geo.ts:44), and is picked
    // up by UnplacedBusesPanel. The drop point still reaches the canvas via
    // setPendingNodePosition below, which is a layout cache, not PyPSA data.
```

- [ ] **Step 4: Confirm `setPendingNodePosition` is untouched**

Run:

```bash
grep -n "setPendingNodePosition" pypsa-gui/frontend/src/layout/CreationForm.tsx
```

Expected: two hits — the destructure at `:358` and the call at `:450`, inside
the `if (item.dropPosition && item.id === 'bus')` guard in `onSuccess`. Both
must survive. If the call is gone, the node no longer appears where it was
dropped and this task has over-reached.

- [ ] **Step 5: Run the whole frontend suite, type-check, commit**

```bash
PATH="$PIXI_BIN:$PATH" npm test
```

Expected: `Test Files  87 passed (87)`, `Tests  678 passed (678)`, 0 failures —
the 660-test baseline plus this plan's 18 new tests (4 + 13 + 6 + … recount
against the actual reported number; the requirement is **0 failures** and a
count no lower than 660).

```bash
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: exit 0.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/CreationForm.tsx \
        pypsa-gui/frontend/src/layout/CreationForm.prefill.test.tsx
git diff --cached --name-only
git commit -m "fix(gui): stop schematic drops writing canvas pixels into PyPSA lon/lat"
```

---

## Task 6: Delete the dead palette and its three orphan `FIELD_MAP` entries

**Files:**
- Delete: `pypsa-gui/frontend/src/layout/AssetPalette.tsx` (294 lines)
- Modify: `pypsa-gui/frontend/src/layout/CreationForm.tsx` — remove the `link`, `generator` and `load` entries from `FIELD_MAP` (`:122-130`, `:156`, `:236-241`) and their siblings in `AUTO_PREFIX`, `QUERY_KEY`, `COMPONENT_TYPE` and `CREATE_FN`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. This task only removes.

**Context the implementer needs.** `AssetPalette.tsx` is imported nowhere — the
only occurrence of the identifier outside comments is its own
`export default function AssetPalette()` at `:115`. It is not merely dead but
**stale**: its `hydrogen` item is labelled "P2G / Electrolysis" while
`FIELD_MAP.hydrogen` is a StorageUnit, so anyone reviving it would ship a wrong
form.

`FIELD_MAP` has 21 keys; the 18 palette ids account for 18 of them. `generator`
and `load` survive only because the dead file lists them
(`AssetPalette.tsx:92`, `:108`); `link` is in neither palette. Every writer of
`creationItem` was enumerated in recon §6 — `Sidebar.tsx:279, 299, 335` all read
`item.id` straight from `PALETTE_SECTIONS`, and `CreationForm.tsx:452, 474, 580`
all pass `null` — so the three are unreachable from the live UI.

**They must go in the same change as the file.** Deleting the palette alone
leaves them merely unreferenced rather than wrong, which is how they survived
the last cleanup.

Note the shape of the entries: `generator: genFields('')` and `load: [...]` are
single `FIELD_MAP` lines/blocks, and `AUTO_PREFIX` / `QUERY_KEY` /
`COMPONENT_TYPE` / `CREATE_FN` each carry a `link`, `generator` and `load` key
that must go too. `SUBMIT_TRANSFORM` has only a `line` entry and is untouched.

- [ ] **Step 1: Delete the file**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git rm pypsa-gui/frontend/src/layout/AssetPalette.tsx
```

- [ ] **Step 2: Remove the three orphan entries**

In `pypsa-gui/frontend/src/layout/CreationForm.tsx`:

1. Delete the whole `link: [ … ],` block from `FIELD_MAP` (`:122-130`).
2. Delete the `generator: genFields(''),` line (`:156`).
3. Delete the whole `load: [ … ],` block from `FIELD_MAP` (`:236-241`).
4. In `AUTO_PREFIX` remove `link: 'Link'`, `generator: 'Gen'` and
   `load: 'Load'` (the `load_elec` / `load_h2` / `load_heat` keys stay).
5. In `QUERY_KEY` remove `link: 'links'`, `generator: 'generators'`,
   `load: 'loads'`.
6. In `COMPONENT_TYPE` remove `link: 'Link'`, `generator: 'Generator'`,
   `load: 'Load'`.
7. In `CREATE_FN` remove the `link:`, `generator:` and `load:` lines.

Leave every other key in all five maps exactly as it is — in particular
`electrolyzer`, `fuel_cell`, `power_to_heat` and `chp` are Links and keep their
`CREATE_FN` entries pointing at `networkApi.createLink`.

- [ ] **Step 3: Prove nothing referenced them**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
grep -rn "AssetPalette" pypsa-gui/frontend/src || echo "NO REFERENCES"
```

Expected: `NO REFERENCES`, or only hits inside comments that mention the
deletion. Any `import … from './AssetPalette'` here means the file was live and
the deletion must stop.

- [ ] **Step 4: Type-check — this is the real gate**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: exit 0 with `✓ built in …`. `tsc -b` is the only static check in the
project, and it is what would catch a surviving reference to a deleted
`FIELD_MAP` key or to the deleted module.

- [ ] **Step 5: Run the whole frontend suite**

Run:

```bash
PATH="$PIXI_BIN:$PATH" npm test
```

Expected: 0 failures, and a test count no lower than the 660 baseline. The
palette drag tests from Task 1 must still be green — they exercise
`AssetPaletteInline` in `Sidebar.tsx`, which is the live palette and is not
being deleted.

- [ ] **Step 6: Run the backend suite to prove Scope C touched nothing server-side**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/backend"
"$PIXI_BIN/python" -m pytest
```

Expected: `2183 passed, 23 skipped` (the `c2cc4510` baseline, unchanged — this
plan adds no backend tests and modifies no backend file). Remember: no extra
`-q`, or the summary line you are checking will not be printed.

- [ ] **Step 7: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/AssetPalette.tsx \
        pypsa-gui/frontend/src/layout/CreationForm.tsx
git diff --cached --name-only
git commit -m "chore(gui): delete the dead AssetPalette and its three orphan FIELD_MAP entries"
```

---

## Scope C is done when

- `npm test` is green with the four Sidebar drag characterization tests, the
  thirteen `useAssetDrag` tests, the six canvas-attribute tests and the eight
  `CreationForm` prefill/coordinate tests all passing.
- `npm run build` exits 0.
- The backend suite still reports `2183 passed, 23 skipped`.
- Manually, in the running app (`pixi run gui`): dragging a Generator onto a bus
  on the schematic canvas opens the form with `bus` filled (criterion 35);
  the same drag on the map canvas does the same (criterion 36); dropping an
  Electrolyzer on a hydrogen bus leaves `bus0` empty and shows the mismatch line
  (criterion 37); dropping a Bus on the schematic creates it with `x == 0` and
  `y == 0` and it appears in `UnplacedBusesPanel` (criterion 38).
- `pypsa-gui/frontend/src/layout/AssetPalette.tsx` is absent from the tree and
  `FIELD_MAP` has 18 keys.
