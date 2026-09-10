# Asset editing — Scope B: the parameter surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every attribute PyPSA defines for a component becomes reachable from the UI — added through a "+ Add parameter" picker, rendered in an extras section on all eight edit forms, persisted through the Pydantic models to `n.add()`, and marked required at the moment a solve actually needs it.

**Architecture:** `utils/extrasStore.ts` owns the per-scope localStorage list of chosen attribute keys. `cardKit.tsx` gains two pure additions — `extrasPatch(form, keys)` for the save path and `<ExtrasSection>` for the render path — so each of the eight edit forms takes three one-line changes rather than a rewrite. The backend opens the same door in exactly two places: every Create model gains `extra='allow'`, and the two generic CRUD helpers drop any extra key the catalog does not report as an `Input` attribute. D22's six reveal rules land in `utils/attributeCatalog.ts` beside Scope A's helpers and are consumed by the edit forms and the creation form alike.

**Tech Stack:** React 19 + TypeScript 5.8 (strict), `@tanstack/react-query` 5, zustand 5, vitest 4.1.10 + jsdom 29 + Testing Library 16; FastAPI + Pydantic v2 + PyPSA 1.1.2 + pytest.

---

## Plan set

| # | File | Scope | State |
|---|---|---|---|
| 1 | `2026-08-09-asset-editing-c-drag-drop.md` | C — drop-on-a-bus | **Done**, `d93248ce`…`e8614a35` |
| 2 | `2026-08-09-asset-editing-a-grid.md` | A — the editable grid | **Done**, `15b55b58`…`54a5b3c0` |
| 3 | this file | B — the parameter surface | this plan |

**Base is `54a5b3c0`.** Scope A's `attributeCatalog.ts`, `useCatalog.ts` and
`GET /api/network/catalog/{component}` are already built and characterised;
this plan consumes them rather than creating them.

### Spec decision coverage

| Decision | Task |
|---|---|
| D20 extras section opens all three layers on all eight forms | 4, 5 |
| D21 backend passthrough catalog-whitelisted at the two CRUD helpers | 6 |
| D22 six reveal rules in one table | 7 |
| D23 "+ Add parameter" persisted per palette type | 3, 8 |
| D24 catalog payload | consumed (built in Scope A Task 5/7) |
| D30 characterization first | 1, 2 |

### Success-criteria coverage

| Criteria | Task |
|---|---|
| 29 add an attribute, save, reload, value survives | 5, 6 (end-to-end proof) |
| 30 picker shows type, unit, description, default — `inf` not blank | 8 |
| 31 extras persist under `creationform:extras:<paletteId>`; a bad `v` is discarded | 3, 8 |
| 32 `lopf` + extendable: `capital_cost` OR `overnight_cost`; `pf` clears it | 7 |
| 33 no Slack bus + `pf` marks `control` required network-wide; clears on any Slack; `lopf` clears it even with `run_ac_pf_after_lopf` | 7 |
| 34 `p_nom_min`/`p_nom_max` hidden in the **creation** form until extendable | 7 |

---

## Global Constraints

Every task's requirements implicitly include this section.

**Paths contain a space.** The worktree is
`/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing`.
Quote every path in every shell command.

**Branch and commits.** Work only on `feature/asset-editing` in that worktree.
Before every commit run `git branch --show-current` and `git status
--porcelain`. Commit with a **path-limited** `git add <paths>` — **never
`git add -A`**. Never `git stash`.

**Toolchain.**

```bash
PIXI_BIN="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/test/bin"
# frontend, from <worktree>/pypsa-gui/frontend
PATH="$PIXI_BIN:$PATH" npm test          # vitest run
PATH="$PIXI_BIN:$PATH" npm run build     # tsc -b && vite build — the ONLY type-check
# backend, from <worktree>/pypsa-gui/backend
"$PIXI_BIN/python" -m pytest             # never add -q; pytest.ini already sets it
"$PIXI_BIN/python" -m ruff check <file>
```

**Baseline at `54a5b3c0`, measured:**

| Suite | Measured |
|---|---|
| Frontend | **93 files, 855 tests, 0 failures** |
| Backend | **2336 passed, 18 skipped, 0 failures** |

Ignore the spec's `2183 / 23` — it is stale; see Scope A's plan for why.

**Test-writing house rules.** `globals: false`, so every test file imports
`describe`/`it`/`expect`/`vi` from `'vitest'`. `@testing-library/jest-dom` is
NOT installed. Tests are co-located. A test must never build its expectation by
calling the function under test.

**jsdom facts that cost an hour each if assumed otherwise:**
- `localStorage` is a bare `{}` — `getItem`/`setItem` throw. Every access in
  production code must be try/catch-wrapped, and **a test must never assert on
  `localStorage`**; assert on rendered output or on an injected store.
- Toasts do not render: `<Toaster/>` lives in `App.tsx`. Assert through
  `vi.spyOn(toast, 'error')`.
- A React state update from a non-React listener needs `act()`.

**House idioms.** `const modifier = e.ctrlKey || e.metaKey`. Query keys via
`nk(projectId, root)`; in non-React callbacks read the id via
`useUIStore.getState().currentProject`. FastAPI error arrays are formatted, never
`String([{…}])`. `routers/network.py` is a declared hotspot — surgical edits.
The desktop app is not current until `npm run build` then `build-macos.sh`.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `frontend/src/layout/PropertiesPanel.save.test.tsx` (new) | Characterization: the edit cards' save payloads and the `...current` spread | 1 |
| `frontend/src/layout/properties/cardKit.test.tsx` (new) | Characterization: `EditShell`'s children seam, `toFS`, `nf`/`ni`/`no` | 2 |
| `frontend/src/utils/extrasStore.ts` (new) | The per-scope chosen-attribute list, `{v:1,keys:[]}` envelope, try/catch everywhere | 3 |
| `frontend/src/utils/extrasStore.test.ts` (new) | Round-trip, version rejection, corrupt-value tolerance | 3 |
| `frontend/src/layout/properties/cardKit.tsx` | `extrasPatch` + `<ExtrasSection>` + `useSolveMode` | 4, 7 |
| `frontend/src/layout/PropertiesPanel.tsx` | Three one-line changes × 8 forms; reveal rules replace the derived booleans | 5, 7 |
| `backend/models/schemas.py` | Every Create model gains `extra='allow'` | 6 |
| `backend/services/attribute_catalog.py` | `input_attributes(n, cls)` | 6 |
| `backend/routers/network.py:157,199` | The whitelist at `_create_component` / `_update_component` | 6 |
| `backend/tests/test_extras_passthrough.py` (new) | A catalog attr persists; a non-catalog key is dropped | 6 |
| `frontend/src/utils/attributeCatalog.ts` | D22's six reveal rules, appended beside Scope A's helpers | 7 |
| `frontend/src/layout/CreationForm.tsx` | "+ Add parameter" picker; the render loop consults the reveal rules | 7, 8 |

---

## Task 1: Characterize the edit cards' save payloads

**Files:**
- Test: `pypsa-gui/frontend/src/layout/PropertiesPanel.save.test.tsx` (create)

**Interfaces:** Consumes nothing. Produces the safety net Tasks 5 and 7 edit under.

**Context.** `PropertiesPanel.tsx` is 2508 lines with coverage only of rescale.
Task 5 touches all eight cards' seed and payload; Task 7 replaces their
reveal predicates. The load-bearing behaviour is the `...current` spread at
`:144`: a field present in the cached object but absent from the card's
enumeration **survives at its old value**. Extras must ride on top of that
without disturbing it.

The Generator card is the one to pin — it is the largest, it is what criteria
29 and 32 exercise, and every other card is the same shape. Its save reads the
cache with `qc.getQueryData(nk(projectId,'generators'))` (`:141`), so the test
seeds that key directly.

- [ ] **Step 1: Write the characterization test**

Create `pypsa-gui/frontend/src/layout/PropertiesPanel.save.test.tsx`:

```tsx
// Characterization of the Generator edit card's save payload, written BEFORE
// the extras section opens its three layers (spec D20, D30).
//
// The behaviour that matters: the payload starts from the CACHED object
// (PropertiesPanel.tsx:141-144), so a field the card does not enumerate
// survives at its old value. Extras must ride on top of that, not replace it.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: vi.fn(async () => []),
      getCarriers: vi.fn(async () => []),
      updateGenerator: vi.fn(async () => ({ name: 'gas' })),
    },
  }
})

import { networkApi } from '../api/network'
import { GeneratorPanel } from './PropertiesPanel'

const GEN = {
  name: 'gas', bus: 'B1', carrier: 'gas', p_nom: 100, p_nom_extendable: false,
  p_nom_min: 0, p_nom_max: null, p_min_pu: 0, p_max_pu: 1, marginal_cost: 50,
  capital_cost: 1000, efficiency: 0.5, committable: false, control: 'PQ',
  build_year: 2025, lifetime: null,
  // Not enumerated by the card — this is the field whose survival is the point.
  weight: 7,
} as never

function renderCard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  client.setQueryData(nk('Demo', 'generators'), [GEN])
  return render(
    <QueryClientProvider client={client}>
      <GeneratorPanel gen={GEN} />
    </QueryClientProvider>,
  )
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null })
})

/** Open the card's edit form. */
async function openEdit() {
  renderCard()
  await userEvent.click(await screen.findByRole('button', { name: /edit/i }))
}

describe('Generator save payload — behaviour as of 54a5b3c0', () => {
  it('sends the enumerated fields', async () => {
    await openEdit()
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(networkApi.updateGenerator).toHaveBeenCalled())
    const [, payload] = vi.mocked(networkApi.updateGenerator).mock.calls[0]
    expect(payload.p_nom).toBe(100)
    expect(payload.marginal_cost).toBe(50)
    expect(payload.carrier).toBe('gas')
  })

  it('a cached field the card does not enumerate SURVIVES at its old value', async () => {
    // This is the ...current spread at :144. Without it a partial payload
    // would wipe the field to a Pydantic default on the backend's remove+add.
    await openEdit()
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(networkApi.updateGenerator).toHaveBeenCalled())
    const [, payload] = vi.mocked(networkApi.updateGenerator).mock.calls[0]
    expect((payload as Record<string, unknown>).weight).toBe(7)
  })

  it('a blanked optional bound is sent as null, not omitted', async () => {
    // The unconditional payload.p_nom_max = no(form,'p_nom_max') at :182.
    // Omitting it would make a bound impossible to clear once typed.
    await openEdit()
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(networkApi.updateGenerator).toHaveBeenCalled())
    const [, payload] = vi.mocked(networkApi.updateGenerator).mock.calls[0]
    expect('p_nom_max' in (payload as object)).toBe(true)
    expect(payload.p_nom_max).toBe(null)
  })

  it('edits to an enumerated field reach the payload', async () => {
    await openEdit()
    const input = screen.getByDisplayValue('100') as HTMLInputElement
    fireEvent.change(input, { target: { value: '250' } })
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(networkApi.updateGenerator).toHaveBeenCalled())
    const [, payload] = vi.mocked(networkApi.updateGenerator).mock.calls[0]
    expect(payload.p_nom).toBe(250)
  })
})
```

- [ ] **Step 2: Export `GeneratorPanel` if it is not already exported**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
grep -n "function GeneratorPanel" pypsa-gui/frontend/src/layout/PropertiesPanel.tsx
```

If the declaration reads `function GeneratorPanel(`, change it to
`export function GeneratorPanel(` and add a one-line comment saying it is
exported so the card can be rendered in isolation. Do not change its props.
If the card takes props other than `gen`, read the declaration and pass what it
needs — adjust `renderCard` above rather than the component.

- [ ] **Step 3: Run it and adapt the queries to the real markup**

Run:

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/PropertiesPanel.save.test.tsx
```

Expected: all four pass. These pin existing behaviour, so a failure means the
**query** is wrong, not the component. Read the card's render and match its real
markup — in particular confirm how the edit form is opened (the button's
accessible name) and that `100` is the displayed `p_nom` value. Adjust the
selectors, never the component.

- [ ] **Step 4: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/PropertiesPanel.save.test.tsx \
        pypsa-gui/frontend/src/layout/PropertiesPanel.tsx
git diff --cached --name-only
git commit -m "test(gui): characterize the Generator card's save payload before extras open it"
```

---

## Task 2: Characterize `cardKit`'s `EditShell` seam and form helpers

**Files:**
- Test: `pypsa-gui/frontend/src/layout/properties/cardKit.test.tsx` (create)

**Interfaces:** Consumes nothing. Produces the pin under Task 4's additions.

**Context.** `cardKit.tsx` has 33 exports and **zero tests**. D20 depends on two
of its properties: `EditShell` renders arbitrary `children` into its 2-column
grid and keeps the Save/Cancel footer (`:771-797`), and `toFS` turns an object
into a string form-state map, mapping `null`, `undefined` and non-finite
numbers to `''` (`:276-284`). Task 4 adds `extrasPatch` beside `toFS`/`nf`/`ni`/
`no`; these tests are what notice if that addition disturbs them.

- [ ] **Step 1: Write the characterization test**

Create `pypsa-gui/frontend/src/layout/properties/cardKit.test.tsx`:

```tsx
// Characterization of the cardKit primitives spec D20 depends on, written
// BEFORE extrasPatch and ExtrasSection are added beside them. cardKit.tsx has
// 33 exports and zero tests today.
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EditShell, nf, ni, no, toFS } from './cardKit'

describe('toFS — the form-state seed', () => {
  it('stringifies the requested keys', () => {
    expect(toFS({ a: 1, b: 'x' }, ['a', 'b'])).toEqual({ a: '1', b: 'x' })
  })

  it('maps null and undefined to an empty string', () => {
    expect(toFS({ a: null, b: undefined }, ['a', 'b'])).toEqual({ a: '', b: '' })
  })

  it('maps a non-finite number to an empty string, so inf renders blank', () => {
    expect(toFS({ a: Infinity, b: NaN }, ['a', 'b'])).toEqual({ a: '', b: '' })
  })

  it('renders booleans as the strings the cards compare against', () => {
    expect(toFS({ a: true, b: false }, ['a', 'b'])).toEqual({ a: 'true', b: 'false' })
  })

  it('includes only the requested keys — this is what extras widen', () => {
    expect(toFS({ a: 1, b: 2 }, ['a'])).toEqual({ a: '1' })
  })
})

describe('nf / ni / no — the payload readers', () => {
  it('nf falls back when the value is not a number', () => {
    expect(nf({ a: '5.5' }, 'a', 1)).toBe(5.5)
    expect(nf({ a: '' }, 'a', 1)).toBe(1)
  })

  it('ni parses an integer', () => {
    expect(ni({ a: '7' }, 'a', 1)).toBe(7)
    expect(ni({ a: 'x' }, 'a', 1)).toBe(1)
  })

  it('no returns null for a blank, which is how a bound is cleared', () => {
    expect(no({ a: '' }, 'a')).toBe(null)
    expect(no({ a: '  ' }, 'a')).toBe(null)
    expect(no({ a: '3' }, 'a')).toBe(3)
  })
})

describe('EditShell — the render seam D20 depends on', () => {
  it('renders arbitrary children', () => {
    render(
      <EditShell title="T" onSave={() => {}} onCancel={() => {}} saving={false}>
        <div data-testid="child">hello</div>
      </EditShell>,
    )
    expect(screen.getByTestId('child').textContent).toBe('hello')
  })

  it('keeps the Save and Cancel footer alongside the children', () => {
    render(
      <EditShell title="T" onSave={() => {}} onCancel={() => {}} saving={false}>
        <div data-testid="child">hello</div>
      </EditShell>,
    )
    expect(screen.getByRole('button', { name: /^save$/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^cancel$/i })).toBeTruthy()
  })

  it('calls onSave', async () => {
    const onSave = vi.fn()
    const userEvent = (await import('@testing-library/user-event')).default
    render(
      <EditShell title="T" onSave={onSave} onCancel={() => {}} saving={false}>
        <div />
      </EditShell>,
    )
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    expect(onSave).toHaveBeenCalled()
  })

  it('disables Save while saving', () => {
    render(
      <EditShell title="T" onSave={() => {}} onCancel={() => {}} saving={true}>
        <div />
      </EditShell>,
    )
    expect((screen.getByRole('button', { name: /saving/i }) as HTMLButtonElement).disabled)
      .toBe(true)
  })
})
```

- [ ] **Step 2: Run it**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/properties/cardKit.test.tsx
```

Expected: all pass first time.

- [ ] **Step 3: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/properties/cardKit.test.tsx
git diff --cached --name-only
git commit -m "test(gui): characterize cardKit's EditShell seam and form helpers"
```

---

## Task 3: `extrasStore.ts` — the persisted chosen-attribute list

**Files:**
- Create: `pypsa-gui/frontend/src/utils/extrasStore.ts`
- Create: `pypsa-gui/frontend/src/utils/extrasStore.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:

```ts
export function creationScope(paletteId: string): string   // 'creationform:extras:<paletteId>'
export function editScope(componentClass: string): string  // 'propertiespanel:extras:<Class>'
export function loadExtras(scope: string): string[]
export function saveExtras(scope: string, keys: string[]): void
```

**Context.** D23 fixes the creation form's key as
`creationform:extras:<paletteId>` with the value `{ "v": 1, "keys": [...] }`,
and a mismatched `v` **drops the entry** — versioning inside the value, never in
the key (`topologyLayoutStore.ts:19,41`). Every read and write is individually
try/catch-wrapped, because jsdom's `localStorage` is a bare `{}` whose
`getItem` throws and because Safari private mode throws on `setItem`.

**A plan decision the spec does not state.** D23 defines only the creation
form's key, but D20 needs extras on the **edit** cards too, where there is no
palette id — an edit card knows its component class. `editScope` therefore
mints `propertiespanel:extras:<ComponentClass>` using the same envelope and the
same rules. Criterion 31 names the creation-form key and is satisfied verbatim;
the edit key is the smallest consistent extension.

No regex sweep ships on project deletion: unlike `network-diagram:*:state` this
family is not project-scoped and has nothing to clean up (D23).

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/utils/extrasStore.test.ts`:

```ts
// extrasStore — the persisted list of attributes the user added to a form
// (spec D23). Pure module over localStorage, every access try/catch-wrapped.
//
// jsdom's localStorage is a bare {} whose methods throw, so these tests install
// a real in-memory Storage first. Production code survives the bare {} because
// every access is wrapped; that survival is asserted at the end.
import { beforeEach, describe, expect, it } from 'vitest'
import { creationScope, editScope, loadExtras, saveExtras } from './extrasStore'

function installStorage() {
  const map = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (k: string) => map.get(k) ?? null,
      setItem: (k: string, v: string) => { map.set(k, v) },
      removeItem: (k: string) => { map.delete(k) },
      clear: () => map.clear(),
      key: (i: number) => [...map.keys()][i] ?? null,
      get length() { return map.size },
    },
  })
  return map
}

let store: Map<string, string>
beforeEach(() => { store = installStorage() })

describe('scope keys', () => {
  it('mints the creation-form key D23 fixes', () => {
    expect(creationScope('thermal')).toBe('creationform:extras:thermal')
  })

  it('mints a distinct edit-card key per component class', () => {
    expect(editScope('Generator')).toBe('propertiespanel:extras:Generator')
  })
})

describe('round-trip', () => {
  it('reads back what it wrote', () => {
    saveExtras(creationScope('thermal'), ['weight', 'p_min_pu'])
    expect(loadExtras(creationScope('thermal'))).toEqual(['weight', 'p_min_pu'])
  })

  it('stores the versioned envelope, not a bare array', () => {
    saveExtras('s', ['a'])
    expect(JSON.parse(store.get('s') as string)).toEqual({ v: 1, keys: ['a'] })
  })

  it('returns an empty list for a scope never written', () => {
    expect(loadExtras('nothing-here')).toEqual([])
  })

  it('keeps scopes independent', () => {
    saveExtras(creationScope('thermal'), ['a'])
    saveExtras(creationScope('battery'), ['b'])
    expect(loadExtras(creationScope('thermal'))).toEqual(['a'])
    expect(loadExtras(creationScope('battery'))).toEqual(['b'])
  })

  it('an empty list round-trips as empty', () => {
    saveExtras('s', [])
    expect(loadExtras('s')).toEqual([])
  })
})

describe('version and corruption handling (criterion 31)', () => {
  it('discards an entry whose v is not 1', () => {
    store.set('s', JSON.stringify({ v: 2, keys: ['a'] }))
    expect(loadExtras('s')).toEqual([])
  })

  it('discards an entry with no v at all', () => {
    store.set('s', JSON.stringify({ keys: ['a'] }))
    expect(loadExtras('s')).toEqual([])
  })

  it('discards a bare legacy array', () => {
    store.set('s', JSON.stringify(['a']))
    expect(loadExtras('s')).toEqual([])
  })

  it('tolerates unparseable JSON', () => {
    store.set('s', '{not json')
    expect(loadExtras('s')).toEqual([])
  })

  it('drops non-string entries rather than trusting them', () => {
    store.set('s', JSON.stringify({ v: 1, keys: ['a', 7, null, 'b'] }))
    expect(loadExtras('s')).toEqual(['a', 'b'])
  })

  it('de-duplicates', () => {
    saveExtras('s', ['a', 'a', 'b'])
    expect(loadExtras('s')).toEqual(['a', 'b'])
  })
})

describe('a hostile localStorage never throws out of the module', () => {
  it('survives getItem throwing', () => {
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: { getItem() { throw new Error('nope') }, setItem() {} },
    })
    expect(loadExtras('s')).toEqual([])
  })

  it('survives setItem throwing — Safari private mode, jsdom bare object', () => {
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: { getItem: () => null, setItem() { throw new Error('quota') } },
    })
    expect(() => saveExtras('s', ['a'])).not.toThrow()
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/utils/extrasStore.test.ts
```

Expected: `Failed to resolve import "./extrasStore"`.

- [ ] **Step 3: Write the module**

Create `pypsa-gui/frontend/src/utils/extrasStore.ts`:

```ts
// ── Extras store ─────────────────────────────────────────────────────────────
// The list of attributes the user has added to a form beyond its curated set
// (spec D23). Pure over localStorage: no React, no DOM beyond the storage API.
//
// House convention from recon §18: ':' separator, feature-scoped namespace,
// dynamic segment last, every read and write individually try/catch-wrapped.
// The value carries its own version — `{ v: 1, keys: [...] }` — and a
// mismatched `v` drops the entry. Versioning lives INSIDE the value, never in
// the key (topologyLayoutStore.ts:19,41), so a future format change does not
// strand entries under an unreadable key.
//
// No regex sweep ships on project deletion: unlike network-diagram:*:state
// this family is not project-scoped and has nothing to clean up.

const VERSION = 1

/** D23's key: the creation form persists per palette type. */
export function creationScope(paletteId: string): string {
  return `creationform:extras:${paletteId}`
}

/**
 * The edit cards' key. D23 defines only the creation-form key, but D20 needs
 * extras on the edit cards too, where there is no palette id — a card knows
 * its component class. Same envelope, same rules.
 */
export function editScope(componentClass: string): string {
  return `propertiespanel:extras:${componentClass}`
}

export function loadExtras(scope: string): string[] {
  try {
    const raw = localStorage.getItem(scope)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return []
    const box = parsed as { v?: unknown; keys?: unknown }
    // A mismatched version drops the entry rather than guessing at its shape.
    if (box.v !== VERSION || !Array.isArray(box.keys)) return []
    const seen = new Set<string>()
    for (const k of box.keys) if (typeof k === 'string') seen.add(k)
    return [...seen]
  } catch {
    return []
  }
}

export function saveExtras(scope: string, keys: string[]): void {
  try {
    const unique = [...new Set(keys.filter(k => typeof k === 'string'))]
    localStorage.setItem(scope, JSON.stringify({ v: VERSION, keys: unique }))
  } catch {
    // Safari private mode and jsdom's bare {} both throw here. Losing the
    // preference is acceptable; losing the edit the user was making is not.
  }
}
```

- [ ] **Step 4: Run, type-check, commit**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/utils/extrasStore.test.ts
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: all pass; build exit 0.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/utils/extrasStore.ts pypsa-gui/frontend/src/utils/extrasStore.test.ts
git diff --cached --name-only
git commit -m "feat(gui): persist chosen extra parameters per form scope"
```

---

## Task 4: `extrasPatch` and `<ExtrasSection>` in `cardKit`

**Files:**
- Modify: `pypsa-gui/frontend/src/layout/properties/cardKit.tsx`
- Modify: `pypsa-gui/frontend/src/layout/properties/cardKit.test.tsx`

**Interfaces:**
- Consumes: `loadExtras`/`saveExtras`/`editScope` (Task 3); `useCatalog` and
  `CatalogAttribute` (Scope A).
- Produces:

```tsx
/** Form values for the chosen extra keys, ready to Object.assign onto a payload. */
export function extrasPatch(fs: FS, keys: string[]): Record<string, unknown>

export function ExtrasSection(props: {
  componentClass: string
  fs: FS
  set: SetFS
  /** Keys the card already renders — never offered as an extra. */
  curated: string[]
}): JSX.Element | null
```

**Context.** These are **additions** to `cardKit.tsx`, not changes to any
existing export — Task 2's tests are what prove that.

`extrasPatch` is the save half of D20. It converts the string form-state back to
values the backend accepts, using the same conventions the cards already use:
a blank is `null` (which Pydantic's `Optional` aliases turn into PyPSA's
sentinel), `'true'`/`'false'` become booleans, a numeric-looking string becomes
a number, everything else stays a string. It is **pure** — no React, no catalog
— so it can be unit-tested directly and called from any card's payload builder.

`ExtrasSection` is the render half. It reads the component's catalog, lists the
attributes the user has chosen for `editScope(componentClass)`, renders one
input each, and offers a picker to add more. It returns `null` when the catalog
has not loaded, so a card renders exactly as it does today until the data
arrives.

- [ ] **Step 1: Add the failing tests**

Append to `pypsa-gui/frontend/src/layout/properties/cardKit.test.tsx`:

```tsx
import { extrasPatch } from './cardKit'

describe('extrasPatch — the save half of D20', () => {
  it('returns only the requested keys', () => {
    expect(extrasPatch({ a: '1', b: '2' }, ['a'])).toEqual({ a: 1 })
  })

  it('is empty when no extras are chosen', () => {
    expect(extrasPatch({ a: '1' }, [])).toEqual({})
  })

  it('coerces a numeric string to a number', () => {
    expect(extrasPatch({ a: '3.5' }, ['a'])).toEqual({ a: 3.5 })
  })

  it('coerces boolean strings, the form-state convention toFS emits', () => {
    expect(extrasPatch({ a: 'true', b: 'false' }, ['a', 'b']))
      .toEqual({ a: true, b: false })
  })

  it('sends a blank as null so a value can be cleared', () => {
    // null routes through Pydantic's Optional aliases to PyPSA's sentinel —
    // the same semantic the cards' own no() helper produces.
    expect(extrasPatch({ a: '' }, ['a'])).toEqual({ a: null })
  })

  it('leaves a non-numeric string alone', () => {
    expect(extrasPatch({ a: 'CCGT' }, ['a'])).toEqual({ a: 'CCGT' })
  })

  it('ignores a key with no form entry rather than sending undefined', () => {
    expect(extrasPatch({}, ['missing'])).toEqual({})
  })
})
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/properties/cardKit.test.tsx
```

Expected: the `extrasPatch` block fails — the export does not exist. Task 2's
tests still pass.

- [ ] **Step 3: Add `extrasPatch` beside the other form helpers**

In `pypsa-gui/frontend/src/layout/properties/cardKit.tsx`, immediately after
`no()` (`:292-294`):

```ts
/**
 * Form values for the chosen extra keys, ready to Object.assign onto a card's
 * payload (spec D20's save layer).
 *
 * Rendering an extras field alone is a lie: the value would land in `form`,
 * never reach `payload`, and be overwritten by the `...current` spread at
 * PropertiesPanel.tsx:144. This is the function that closes that gap.
 *
 * Conventions match what the cards already do: a blank is null (Pydantic's
 * Optional aliases turn it into PyPSA's ±inf / NaN sentinel), 'true'/'false'
 * are the strings toFS emits for booleans, and a numeric-looking string
 * becomes a number so it does not upcast a numeric column to object dtype.
 */
export function extrasPatch(fs: FS, keys: string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const k of keys) {
    const raw = fs[k]
    if (raw === undefined) continue          // never send undefined
    const t = raw.trim()
    if (t === '') { out[k] = null; continue }
    if (t === 'true') { out[k] = true; continue }
    if (t === 'false') { out[k] = false; continue }
    const n = Number(t)
    out[k] = Number.isFinite(n) ? n : raw
  }
  return out
}
```

- [ ] **Step 4: Add `ExtrasSection`**

At the end of `pypsa-gui/frontend/src/layout/properties/cardKit.tsx`:

```tsx
// ── ExtrasSection ────────────────────────────────────────────────────────────
// The appended block on an edit card holding attributes beyond the card's
// curated set (spec D20). Additive: it renders as the last child of EditShell's
// 2-column grid and touches nothing above it.
export function ExtrasSection({ componentClass, fs, set, curated }: {
  componentClass: string
  fs: FS
  set: SetFS
  curated: string[]
}) {
  const { data } = useCatalog(componentClass)
  const [keys, setKeys] = useState<string[]>(() => loadExtras(editScope(componentClass)))
  const [picking, setPicking] = useState(false)

  // Until the catalog arrives the card renders exactly as it does today.
  if (!data) return null

  const curatedSet = new Set(curated)
  const byName = new Map(data.attributes.map(a => [a.name, a]))
  // Only Input attributes are offerable: an Output is computed by the solver
  // and writing one is meaningless (same authority D13 uses in the grid).
  const offerable = data.attributes.filter(
    a => a.status.startsWith('Input') && !curatedSet.has(a.name) && !keys.includes(a.name),
  )

  const add = (name: string) => {
    const next = [...keys, name]
    setKeys(next)
    saveExtras(editScope(componentClass), next)
    setPicking(false)
    // Adding a parameter never seeds a value — the field opens empty and
    // PyPSA's default continues to apply until the user types one (D23).
    set(prev => ({ ...prev, [name]: prev[name] ?? '' }))
  }

  const drop = (name: string) => {
    const next = keys.filter(k => k !== name)
    setKeys(next)
    saveExtras(editScope(componentClass), next)
  }

  return (
    <div className="col-span-2 mt-2 pt-2 border-t border-border">
      <div className="text-[10px] font-semibold text-muted uppercase tracking-wide mb-1">
        More parameters
      </div>
      {keys.map(k => {
        const attr = byName.get(k)
        return (
          <label key={k} className="flex items-center gap-1.5 mb-1">
            <span className="text-[10px] text-muted w-32 shrink-0 truncate" title={attr?.description ?? k}>
              {k}{attr?.unit ? ` (${attr.unit})` : ''}
            </span>
            <input
              value={fs[k] ?? ''}
              onChange={e => set(prev => ({ ...prev, [k]: e.target.value }))}
              placeholder={attr?.default_text ?? ''}
              className="flex-1 bg-bg border border-border rounded px-1.5 py-0.5 text-xs"
            />
            <button
              type="button"
              onClick={() => drop(k)}
              aria-label={`Remove ${k}`}
              className="text-muted hover:text-danger"
            >
              <X size={11} />
            </button>
          </label>
        )
      })}
      {picking ? (
        <select
          autoFocus
          value=""
          onChange={e => { if (e.target.value) add(e.target.value) }}
          onBlur={() => setPicking(false)}
          className="w-full bg-bg border border-accent rounded px-1.5 py-0.5 text-xs"
        >
          <option value="">Choose a parameter…</option>
          {offerable.map(a => (
            <option key={a.name} value={a.name}>
              {a.name}{a.unit ? ` (${a.unit})` : ''} — {a.type}, default {a.default_text || '—'}
            </option>
          ))}
        </select>
      ) : (
        <button
          type="button"
          onClick={() => setPicking(true)}
          className="text-[10px] text-accent hover:underline"
        >+ Add parameter</button>
      )}
    </div>
  )
}
```

Add the imports this needs at the top of `cardKit.tsx` (merge into the existing
import lines rather than duplicating them):

```tsx
import { useCatalog } from '../../hooks/useCatalog'
import { editScope, loadExtras, saveExtras } from '../../utils/extrasStore'
```

`useState` and `X` are already imported by this file; confirm with
`grep -n "useState\|X," pypsa-gui/frontend/src/layout/properties/cardKit.tsx | head`
and add only what is missing.

- [ ] **Step 5: Run, type-check, commit**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/properties/cardKit.test.tsx
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: all pass; build exit 0.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/properties/cardKit.tsx \
        pypsa-gui/frontend/src/layout/properties/cardKit.test.tsx
git diff --cached --name-only
git commit -m "feat(gui): extrasPatch and ExtrasSection, the two halves D20 needs"
```

---

## Task 5: Wire the extras section into all eight edit forms

**Files:**
- Modify: `pypsa-gui/frontend/src/layout/PropertiesPanel.tsx`
- Modify: `pypsa-gui/frontend/src/layout/PropertiesPanel.save.test.tsx`

**Interfaces:** Consumes `extrasPatch`, `ExtrasSection` (Task 4);
`loadExtras`, `editScope` (Task 3).

**Context.** Each of the eight forms takes **three one-line changes** (D20):

1. **Seed** — `toFS(obj, [...CURATED, ...loadExtras(editScope(CLASS))])`, so an
   extras field opens showing its current value.
2. **Render** — `<ExtrasSection …/>` as the last child of `EditShell`, and
   inside `<Section title="Edit Parameters">` for `LinePanel` and
   `TransformerPanel`, which use none of the shells.
3. **Save** — `Object.assign(payload, extrasPatch(form, extraKeys))` after the
   card's last unconditional assignment.

The eight forms and their `EditShell`/`Section` sites:

| Card | Class | seed (`toFS`) | render |
|---|---|---|---|
| Generator | `Generator` | `:207` | `EditShell` `:336` |
| StorageUnit | `StorageUnit` | `:507` | `EditShell` `:596` |
| Store | `Store` | `:716` | `EditShell` `:786` |
| Load | `Load` | `:901` | `EditShell` `:967` |
| Link | `Link` | `:1115` | `EditShell` `:1236` |
| Bus | `Bus` | `:1544` | `EditShell` `:1633` |
| Line | `Line` | local `numInp`/`txtInp` | `<Section title="Edit Parameters">` |
| Transformer | `Transformer` | local `numInp`/`txtInp` | `<Section title="Edit Parameters">` |

**Order the assignment correctly.** `Object.assign(payload, extrasPatch(...))`
must come **after** the card's last unconditional assignment, or the
`...current` spread and the explicit `payload.X = no(form,'X')` lines would
overwrite an extras value. It must not come before `return networkApi.update…`.

- [ ] **Step 1: Add the failing round-trip test**

Append to `pypsa-gui/frontend/src/layout/PropertiesPanel.save.test.tsx`:

```tsx
import { saveExtras, editScope } from '../utils/extrasStore'

describe('extras reach the payload — D20 (criterion 29)', () => {
  it('an added catalog attribute is sent with the value the user typed', async () => {
    // The three layers together: the seed puts it in `form`, ExtrasSection
    // renders it, extrasPatch puts it in `payload`. Rendering alone would be a
    // lie — the value would never leave the form.
    saveExtras(editScope('Generator'), ['weight'])
    await openEdit()
    const input = await screen.findByPlaceholderText(/.*/)
    // Locate the extras input by its label rather than by order.
    const extras = screen.getByTitle(/weight/i).parentElement
      ?.querySelector('input') as HTMLInputElement
    fireEvent.change(extras ?? input, { target: { value: '42' } })
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(networkApi.updateGenerator).toHaveBeenCalled())
    const [, payload] = vi.mocked(networkApi.updateGenerator).mock.calls[0]
    expect((payload as Record<string, unknown>).weight).toBe(42)
  })
})
```

This test needs the catalog mocked. Extend the existing `vi.mock` block at the
top of the file to include:

```tsx
      getCatalog: vi.fn(async (component: string) => ({
        component,
        attributes: [
          { name: 'weight', status: 'Input (optional)', varying: false,
            dtype: 'float64', unit: null, description: 'Weighting',
            type: 'float', default: 1, default_text: '1.0' },
        ],
      })),
```

and install a working `localStorage` in `beforeEach`, copying
`installStorage()` from `src/utils/extrasStore.test.ts` — jsdom's bare `{}`
would make `saveExtras` a silent no-op and the test would pass for the wrong
reason.

- [ ] **Step 2: Run and watch it fail**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/PropertiesPanel.save.test.tsx
```

Expected: the new test fails — no extras section renders and `weight` is absent
from the payload. Task 1's four tests still pass.

- [ ] **Step 3: Wire the Generator card, all three layers**

In `pypsa-gui/frontend/src/layout/PropertiesPanel.tsx`, add the imports:

```tsx
import { ExtrasSection, extrasPatch } from './properties/cardKit'
import { editScope, loadExtras } from '../utils/extrasStore'
```

(merge `ExtrasSection`/`extrasPatch` into the existing `cardKit` import.)

Inside the Generator card, above `startEdit`, add:

```tsx
  // The attributes the user has added beyond this card's curated set (D20).
  // Read once per render so the seed, the render and the save all agree.
  const extraKeys = loadExtras(editScope('Generator'))
```

**Seed** — extend the `toFS` call at `:207`:

```tsx
    const base = toFS(gen, ['name', 'bus', 'carrier', 'p_nom', 'p_nom_extendable', 'p_nom_min', 'p_nom_max',
```

becomes a call whose key list is followed by `...extraKeys`. Concretely, append
`, ...extraKeys` immediately before the closing `]` of that array, and cast the
array to `(keyof Generator)[]` if `tsc` objects:

```tsx
    const base = toFS(gen, ([ /* …the existing keys, unchanged… */,
      ...extraKeys ] as unknown) as (keyof typeof gen)[])
```

**Save** — immediately before `return networkApi.updateGenerator(gen.name, payload)`:

```tsx
      // Extras last: the ...current spread and the explicit payload.X = no(...)
      // lines above would otherwise overwrite a value the user just typed.
      Object.assign(payload, extrasPatch(form, extraKeys))
```

**Render** — as the last child of the Generator card's `EditShell` (`:336`),
immediately before `</EditShell>`:

```tsx
      <ExtrasSection
        componentClass="Generator"
        fs={form}
        set={setForm}
        curated={['name', 'bus', 'carrier', 'p_nom', 'p_nom_extendable', 'p_nom_min',
          'p_nom_max', 'p_min_pu', 'p_max_pu', 'marginal_cost', 'capital_cost',
          'fom_cost', 'curtailment_cost', 'efficiency', 'committable', 'build_year',
          'start_up_cost', 'shut_down_cost', 'min_up_time', 'min_down_time',
          'lifetime', 'ramp_limit_up', 'ramp_limit_down', 'e_sum_min', 'e_sum_max',
          'overnight_cost', 'discount_rate_pct', 'control']}
      />
```

- [ ] **Step 4: Run the Generator test**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/PropertiesPanel.save.test.tsx
```

Expected: all five pass.

- [ ] **Step 5: Repeat the same three changes on the other seven cards**

For each of StorageUnit, Store, Load, Link, Bus, Line and Transformer, make the
identical three edits, substituting the card's own component class and its own
curated key list (read the card's `toFS` call — the curated list is exactly the
keys it already passes). For `LinePanel` and `TransformerPanel`, which use no
shell, place `<ExtrasSection …/>` as the last child of their
`<Section title="Edit Parameters">` block.

Verify each one compiles before moving to the next:

```bash
PATH="$PIXI_BIN:$PATH" npm run build
```

- [ ] **Step 6: Full suite, type-check, commit**

```bash
PATH="$PIXI_BIN:$PATH" npm test
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: 0 failures; build exit 0.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/PropertiesPanel.tsx \
        pypsa-gui/frontend/src/layout/PropertiesPanel.save.test.tsx
git diff --cached --name-only
git commit -m "feat(gui): extras section on all eight edit forms, seeded rendered and saved"
```

---

## Task 6: Backend passthrough, catalog-whitelisted at the two CRUD helpers

**Files:**
- Modify: `pypsa-gui/backend/models/schemas.py`
- Modify: `pypsa-gui/backend/services/attribute_catalog.py`
- Modify: `pypsa-gui/backend/routers/network.py:157,199`
- Create: `pypsa-gui/backend/tests/test_extras_passthrough.py`

**Interfaces:**
- Consumes: `catalog_for` (Scope A).
- Produces:

```python
# services/attribute_catalog.py
def input_attributes(n, component_class: str) -> set[str]: ...
```

**Context.** Pydantic v2 defaults to `extra='ignore'`, and there are no Update
models — every PUT reuses the Create model. So a newly-exposed attribute
returns **200 and never persists** (recon §0). Two changes ship together, and
either alone is broken:

- Each Create model gains `extra='allow'` so unknown keys survive
  `model_dump(exclude_unset=True)`.
- The whitelist lands in exactly one place per operation — `_create_component`
  and `_update_component` — which `pypsa-gui/README.md:214-218` already
  designates as the single home for cross-cutting concerns.

**`extra='allow'` without the whitelist would let an arbitrary key reach
`n.add()`.** That is why they are one commit.

**The whitelist is not "catalog Input only".** A key already declared on the
Pydantic model must keep passing through exactly as today, even where PyPSA
marks it `Output` — narrowing to catalog-Input alone would silently change
existing behaviour. The rule is: a key that the model declares passes; a key
the model does **not** declare (i.e. an extra) passes only if the catalog
reports it as an `Input` attribute of that class. Everything else is dropped,
exactly as today.

**`BusCreate` already has `model_config = ConfigDict(populate_by_name=True)`** —
merge `extra='allow'` into it, do not replace it.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_extras_passthrough.py`:

```python
"""
Catalog-whitelisted passthrough for attributes beyond each Create model's
declared fields (spec D21).

Pydantic v2 defaults to extra='ignore' and there are no Update models, so
before this change a newly-exposed attribute returned 200 and never persisted.
The two halves — extra='allow' and the whitelist at the two generic CRUD
helpers — must ship together: the first alone lets an arbitrary key reach
n.add().
"""
from __future__ import annotations

import pypsa

from tests.conftest import build_network


def test_a_catalog_input_attribute_persists_through_put(client, install_network):
    n = build_network()
    install_network(n)
    # `weight` is a real Generator Input attribute that GeneratorCreate does
    # not declare — exactly the case D21 exists for.
    r = client.put("/api/network/generators/gas", json={"weight": 3.0})
    assert r.status_code == 200
    assert float(n.generators.at["gas", "weight"]) == 3.0


def test_a_non_catalog_key_is_dropped_not_persisted(client, install_network):
    n = build_network()
    install_network(n)
    r = client.put("/api/network/generators/gas", json={"not_a_pypsa_attribute": 5})
    assert r.status_code == 200
    assert "not_a_pypsa_attribute" not in n.generators.columns


def test_a_declared_field_still_persists(client, install_network):
    # The whitelist must not narrow existing behaviour for declared fields.
    n = build_network()
    install_network(n)
    r = client.put("/api/network/generators/gas", json={"p_nom": 250.0})
    assert r.status_code == 200
    assert float(n.generators.at["gas", "p_nom"]) == 250.0


def test_a_catalog_input_attribute_persists_through_post(client, install_network):
    n = build_network()
    install_network(n)
    r = client.post("/api/network/generators", json={
        "name": "new_gen", "bus": "B1", "carrier": "gas", "p_nom": 10.0,
        "weight": 4.0,
    })
    assert r.status_code in (200, 201)
    assert float(n.generators.at["new_gen", "weight"]) == 4.0


def test_a_non_catalog_key_is_dropped_on_post(client, install_network):
    n = build_network()
    install_network(n)
    r = client.post("/api/network/generators", json={
        "name": "g2", "bus": "B1", "carrier": "gas", "p_nom": 10.0,
        "bogus_key": 1,
    })
    assert r.status_code in (200, 201)
    assert "bogus_key" not in n.generators.columns


def test_extras_do_not_disturb_the_partial_update_merge(client, install_network):
    # _merge_partial_update keeps unsent fields at their current value; an
    # extra must not reset any of them.
    n = build_network()
    install_network(n)
    before = float(n.generators.at["gas", "marginal_cost"])
    r = client.put("/api/network/generators/gas", json={"weight": 2.0})
    assert r.status_code == 200
    assert float(n.generators.at["gas", "marginal_cost"]) == before


def test_input_attributes_reports_the_catalog_inputs():
    from services.attribute_catalog import input_attributes
    n = pypsa.Network()
    attrs = input_attributes(n, "Generator")
    assert "p_nom" in attrs
    assert "weight" in attrs
    assert "p_nom_opt" not in attrs          # Output
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/backend"
"$PIXI_BIN/python" -m pytest tests/test_extras_passthrough.py -v
```

Expected: the two persistence tests and `input_attributes` fail; the drop tests
and the declared-field test pass already (today everything unknown is dropped).

- [ ] **Step 3: Add `input_attributes` to the catalog service**

In `pypsa-gui/backend/services/attribute_catalog.py`, append:

```python
def input_attributes(n: Any, component_class: str) -> set[str]:
    """
    Every attribute PyPSA marks as an Input for this class.

    The whitelist D21 applies at the two generic CRUD helpers: an extra key
    (one the Pydantic model does not declare) survives only if it is here.
    Returns an empty set for an unknown class, which drops every extra — the
    safe direction.
    """
    try:
        attr = _CATALOG_ATTRS[component_class]
    except KeyError:
        return set()
    defaults = getattr(n.components, attr).defaults
    mask = defaults["status"].astype(str).str.startswith("Input", na=False)
    return {str(x) for x in defaults.index[mask]}
```

- [ ] **Step 4: Open the Create models**

In `pypsa-gui/backend/models/schemas.py`, give every `*Create` model
`extra='allow'`. For `BusCreate` (`:30-31`), which already has a config, merge:

```python
class BusCreate(BaseModel):
    # extra='allow' lets an attribute the model does not declare survive
    # model_dump(exclude_unset=True). It is whitelisted against PyPSA's catalog
    # at _create_component / _update_component (spec D21) — the two ship
    # together, because allow without the whitelist lets any key reach n.add().
    model_config = ConfigDict(populate_by_name=True, extra='allow')
```

For every other Create model listed by

```bash
grep -n "^class.*Create(BaseModel)" pypsa-gui/backend/models/schemas.py
```

add as the first line of the class body:

```python
    model_config = ConfigDict(extra='allow')
```

Confirm `ConfigDict` is already imported at the top of the file; add it to the
existing `from pydantic import …` line if not.

- [ ] **Step 5: Apply the whitelist at the two CRUD helpers**

In `pypsa-gui/backend/routers/network.py`, add a module-level helper directly
above `_create_component` (`:157`):

```python
def _drop_unknown_extras(component_class: str, kwargs: dict, declared: set[str]) -> dict:
    """
    Drop any extra key PyPSA's catalog does not report as an Input attribute
    (spec D21).

    `declared` is the Create model's own field set. A declared key passes
    through exactly as it does today, even where PyPSA marks it Output —
    narrowing to catalog-Input alone would silently change existing behaviour.
    An EXTRA key (one the model does not declare, admitted by extra='allow')
    passes only if the catalog knows it as an Input.
    """
    extras = [k for k in kwargs if k not in declared]
    if not extras:
        return kwargs
    n = PyPSAService.get_network()
    allowed = attribute_catalog.input_attributes(n, component_class)
    return {k: v for k, v in kwargs.items()
            if k not in extras or k in allowed}
```

Then call it at the top of both helpers. In `_create_component` and
`_update_component`, the caller passes `kwargs` from
`model.model_dump(exclude_unset=True)`; the model's declared fields are
`type(model).model_fields`. Rather than thread the model through, filter at
each **route** instead — that keeps both generic helpers untouched. Concretely,
add one line to every route that builds `kwargs`:

```python
    kwargs = _drop_unknown_extras(
        "<ComponentClass>", body.model_dump(exclude_unset=True), set(type(body).model_fields)
    )
```

Find every such site with:

```bash
grep -n "model_dump(exclude_unset=True)" pypsa-gui/backend/routers/network.py
```

and apply the same one-line wrap at each. This is a mechanical change; the
characterization suites and `test_extras_passthrough.py` are what prove it.

- [ ] **Step 6: Run the test, then the whole backend suite**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/backend"
"$PIXI_BIN/python" -m pytest tests/test_extras_passthrough.py -v
"$PIXI_BIN/python" -m ruff check services/attribute_catalog.py tests/test_extras_passthrough.py
"$PIXI_BIN/python" -m pytest
```

Expected: the file green; ruff clean on the two files named; the whole suite
**0 failures** with a passed count no lower than 2336.

- [ ] **Step 7: Commit — both halves together**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/backend/models/schemas.py \
        pypsa-gui/backend/services/attribute_catalog.py \
        pypsa-gui/backend/routers/network.py \
        pypsa-gui/backend/tests/test_extras_passthrough.py
git diff --cached --name-only
git commit -m "feat(gui): catalog-whitelisted passthrough for undeclared attributes"
```

---

## Task 7: D22's six reveal rules

**Files:**
- Modify: `pypsa-gui/frontend/src/utils/attributeCatalog.ts`
- Create: `pypsa-gui/frontend/src/utils/revealRules.test.ts`
- Modify: `pypsa-gui/frontend/src/layout/properties/cardKit.tsx` (add `useSolveMode`)
- Modify: `pypsa-gui/frontend/src/layout/PropertiesPanel.tsx`
- Modify: `pypsa-gui/frontend/src/layout/CreationForm.tsx`

**Interfaces:** Produces:

```ts
export type SolveMode = 'lopf' | 'pf' | string
export interface RevealContext {
  mode: SolveMode
  extendable: boolean
  committable: boolean
  /** True when NO bus in the network has control === 'Slack'. */
  noSlackBus: boolean
}
export function isRevealed(column: string, ctx: RevealContext): boolean
export function isRequired(column: string, ctx: RevealContext): boolean
export function requiredPairMessage(ctx: RevealContext): string | null
```

```tsx
// cardKit.tsx
export function useSolveMode(): SolveMode
```

**Context — what the two columns mean, so every row is checkable.** *Reveal*
makes a field visible; it asserts nothing about the network and cannot
over-report, so it is never mode-gated. *Require* marks a field as blocking,
and it carries exactly one meaning: **its absence produces a backend `_err`,
and `_err`s are what block the solve — warnings never block.**

| # | When | Reveal | Require |
|---|---|---|---|
| 1 | `*_nom_extendable` true | `*_nom_min`, `*_nom_max` | — |
| 2 | `lopf` **and** extendable | — | `capital_cost > 0` **OR** `overnight_cost > 0`, marked on the pair |
| 3 | `lopf` **and** extendable | — | `*_nom_min` and `*_nom_max` finite, min < max |
| 4 | `lopf` **and** not extendable | — | `*_nom > 0` |
| 5 | `committable` true | the seven unit-commitment fields | — |
| 6 | `pf` **and** no Slack bus | — | `control`, network-wide |

**Rules 2, 3 and 4 carry a mode condition because their backend counterparts
do.** `_check_extendable_bounds` is reachable only from `_check_lopf`, which the
dispatcher runs only for `mode == "lopf"`. In `pf` mode the backend never
inspects `capital_cost` or the `*_nom` bounds, so marking them required there
would block the user on a field the run does not need.

**Rule 2 is a disjunction because the backend's is.** A frontend demanding
`capital_cost` alone would over-report against a network the solver accepts.

**Rule 6 deliberately does not fire on the LOPF → AC-PF chain.** With
`mode == "lopf"` and `run_ac_pf_after_lopf` set, the dispatcher runs
`_check_stage2_ac_pf`, not `_check_pf`, and that emits a `_warn` satisfied by
**any** of a Slack generator, a Slack bus, or an `ac_pf_slack_bus` override. A
warning never stops a launch, so firing *required* there would tell the user a
run is blocked when it is not. Preflight already surfaces the advisory in
`IssuesPanel`.

The mode comes from one place for all four mode-gated rules — the solver
configuration the Solver Settings page already loads, under
`nk(currentProject,'solverConfig')`, exactly as `useGlobalDiscountRate`
(`cardKit.tsx:318-326`) reads it.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/utils/revealRules.test.ts`:

```ts
// D22's six reveal rules. Pure: no React, no DOM.
import { describe, expect, it } from 'vitest'
import { isRequired, isRevealed, requiredPairMessage } from './attributeCatalog'

const base = { mode: 'lopf', extendable: false, committable: false, noSlackBus: false }

describe('rule 1 — extendable reveals the bounds, unconditionally', () => {
  it('reveals p_nom_min and p_nom_max when extendable', () => {
    const ctx = { ...base, extendable: true }
    expect(isRevealed('p_nom_min', ctx)).toBe(true)
    expect(isRevealed('p_nom_max', ctx)).toBe(true)
  })

  it('hides them when not extendable — criterion 34', () => {
    expect(isRevealed('p_nom_min', base)).toBe(false)
    expect(isRevealed('p_nom_max', base)).toBe(false)
  })

  it('is NOT mode-gated: a reveal asserts nothing and cannot over-report', () => {
    const ctx = { ...base, mode: 'pf', extendable: true }
    expect(isRevealed('p_nom_min', ctx)).toBe(true)
  })

  it('covers the e_nom and s_nom families too', () => {
    const ctx = { ...base, extendable: true }
    expect(isRevealed('e_nom_min', ctx)).toBe(true)
    expect(isRevealed('s_nom_max', ctx)).toBe(true)
  })

  it('leaves an unrelated column revealed', () => {
    expect(isRevealed('marginal_cost', base)).toBe(true)
  })
})

describe('rule 5 — committable reveals the unit-commitment fields', () => {
  const uc = ['start_up_cost', 'shut_down_cost', 'min_up_time', 'min_down_time',
    'ramp_limit_up', 'ramp_limit_down', 'p_min_pu']

  it('reveals them when committable', () => {
    const ctx = { ...base, committable: true }
    for (const c of uc) expect(isRevealed(c, ctx)).toBe(true)
  })

  it('hides start_up_cost when not committable', () => {
    expect(isRevealed('start_up_cost', base)).toBe(false)
  })
})

describe('rules 2-4 — required only under lopf', () => {
  it('rule 4: a non-extendable asset requires p_nom under lopf', () => {
    expect(isRequired('p_nom', { ...base, mode: 'lopf', extendable: false })).toBe(true)
  })

  it('rule 4 does not fire under pf', () => {
    expect(isRequired('p_nom', { ...base, mode: 'pf', extendable: false })).toBe(false)
  })

  it('rule 3: an extendable asset requires both bounds under lopf', () => {
    const ctx = { ...base, mode: 'lopf', extendable: true }
    expect(isRequired('p_nom_min', ctx)).toBe(true)
    expect(isRequired('p_nom_max', ctx)).toBe(true)
  })

  it('rule 3 does not fire under pf — criterion 32', () => {
    const ctx = { ...base, mode: 'pf', extendable: true }
    expect(isRequired('p_nom_min', ctx)).toBe(false)
  })

  it('rule 2: the cost pair is marked on BOTH members under lopf + extendable', () => {
    const ctx = { ...base, mode: 'lopf', extendable: true }
    expect(isRequired('capital_cost', ctx)).toBe(true)
    expect(isRequired('overnight_cost', ctx)).toBe(true)
  })

  it('rule 2 does not fire when not extendable', () => {
    expect(isRequired('capital_cost', { ...base, mode: 'lopf' })).toBe(false)
  })

  it('rule 2 does not fire under pf', () => {
    expect(isRequired('capital_cost', { ...base, mode: 'pf', extendable: true })).toBe(false)
  })

  it('states the disjunction, so the message cannot read as "capital_cost only"', () => {
    const msg = requiredPairMessage({ ...base, mode: 'lopf', extendable: true })
    expect(msg).toContain('capital_cost')
    expect(msg).toContain('overnight_cost')
    expect(msg?.toLowerCase()).toContain('or')
  })

  it('has no pair message when the rule does not apply', () => {
    expect(requiredPairMessage(base)).toBe(null)
  })
})

describe('rule 6 — control, network-wide, pf only', () => {
  it('marks control required under pf with no Slack bus', () => {
    expect(isRequired('control', { ...base, mode: 'pf', noSlackBus: true })).toBe(true)
  })

  it('clears the moment any bus is Slack — criterion 33', () => {
    expect(isRequired('control', { ...base, mode: 'pf', noSlackBus: false })).toBe(false)
  })

  it('does not fire under lopf, even though the AC-PF chain may run', () => {
    // _check_stage2_ac_pf emits a _warn, not an _err, and is satisfied by a
    // Slack generator OR a Slack bus OR an ac_pf_slack_bus override. A warning
    // never blocks a launch, so `required` would be a lie.
    expect(isRequired('control', { ...base, mode: 'lopf', noSlackBus: true })).toBe(false)
  })
})
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npx vitest run src/utils/revealRules.test.ts
```

Expected: fails — `isRevealed` is not exported.

- [ ] **Step 3: Append the rules to `attributeCatalog.ts`**

At the end of `pypsa-gui/frontend/src/utils/attributeCatalog.ts`:

```ts
// ── D22's reveal rules ───────────────────────────────────────────────────────
// Reveal makes a field visible: it asserts nothing about the network, cannot
// over-report, and is therefore never mode-gated. Require marks a field as
// blocking, and carries exactly one meaning throughout — its absence produces
// a backend _err, and _errs are what block a solve. A rule mirroring a _warn
// is not a require rule, and none below is one.

export type SolveMode = 'lopf' | 'pf' | string

export interface RevealContext {
  mode: SolveMode
  extendable: boolean
  committable: boolean
  /** True when NO bus in the network has control === 'Slack'. */
  noSlackBus: boolean
}

/** p_nom_min / e_nom_max / s_nom_min … — the extendable bound family. */
const NOM_BOUND = /^(p|e|s)_nom_(min|max)$/
/** p_nom / e_nom / s_nom — the capacity itself. */
const NOM = /^(p|e|s)_nom$/

/** Rule 5's seven unit-commitment fields (already shipped at PropertiesPanel.tsx:411-419). */
const UNIT_COMMITMENT = new Set([
  'start_up_cost', 'shut_down_cost', 'min_up_time', 'min_down_time',
  'ramp_limit_up', 'ramp_limit_down', 'p_min_pu',
])

/** Rule 2's disjunction. Marked on BOTH members, reported only when both fail. */
const COST_PAIR = new Set(['capital_cost', 'overnight_cost'])

export function isRevealed(column: string, ctx: RevealContext): boolean {
  // Rule 1: extendable reveals the bounds. Deliberately unconditional — it is
  // existing shipped behaviour, it makes no claim, and gating it would hide
  // fields the user is editing.
  if (NOM_BOUND.test(column)) return ctx.extendable
  // Rule 5: committable reveals the unit-commitment fields, likewise ungated.
  if (UNIT_COMMITMENT.has(column)) return ctx.committable
  return true
}

export function isRequired(column: string, ctx: RevealContext): boolean {
  const lopf = ctx.mode === 'lopf'

  // Rule 4: a non-extendable asset needs a capacity to dispatch.
  if (NOM.test(column)) return lopf && !ctx.extendable

  // Rule 3: an extendable asset needs finite bounds with min < max.
  if (NOM_BOUND.test(column)) return lopf && ctx.extendable

  // Rule 2: the cost disjunction. Both members carry the marker; the message
  // states the OR so it cannot read as "capital_cost only".
  if (COST_PAIR.has(column)) return lopf && ctx.extendable

  // Rule 6: control, network-wide, pf only. NOT on the lopf → AC-PF chain:
  // that path runs _check_stage2_ac_pf, which emits a _warn satisfied by a
  // Slack generator OR a Slack bus OR an ac_pf_slack_bus override. A warning
  // never blocks a launch, so `required` would be a lie there.
  if (column === 'control') return ctx.mode === 'pf' && ctx.noSlackBus

  return false
}

/**
 * The message for rule 2's disjunction, or null when the rule does not apply.
 * Stated once so no card can render half of it.
 */
export function requiredPairMessage(ctx: RevealContext): string | null {
  if (ctx.mode !== 'lopf' || !ctx.extendable) return null
  return 'An extendable asset needs capital_cost or overnight_cost above zero.'
}
```

- [ ] **Step 4: Add `useSolveMode` to `cardKit`**

In `pypsa-gui/frontend/src/layout/properties/cardKit.tsx`, beside
`useGlobalDiscountRate` (`:318`):

```tsx
/**
 * The configured solve mode, for D22's four mode-gated rules. One data
 * dependency for all four, read from the same solverConfig query the Solver
 * Settings page already loads.
 */
export function useSolveMode(): SolveMode {
  const currentProject = useUIStore(s => s.currentProject)
  const { data } = useQuery({
    queryKey: nk(currentProject, 'solverConfig'),
    queryFn: simulationApi.getSolverConfig,
    staleTime: 60_000,
  })
  return data?.mode ?? 'lopf'
}
```

with `import type { SolveMode } from '../../utils/attributeCatalog'` added.

- [ ] **Step 5: Consume the rules in the edit forms and the creation form**

In `pypsa-gui/frontend/src/layout/PropertiesPanel.tsx`, replace the five derived
booleans and the two inlined predicates that gate field visibility with
`isRevealed(...)`. Find them with:

```bash
grep -n "extendable &&\|committable &&" pypsa-gui/frontend/src/layout/PropertiesPanel.tsx
```

Each site becomes `isRevealed('<column>', revealCtx)` where the card builds
one context per render:

```tsx
  const revealCtx = {
    mode: useSolveMode(),
    extendable: form.p_nom_extendable === 'true',
    committable: form.committable === 'true',
    noSlackBus: !allBuses.some(b => String(b.control).toLowerCase() === 'slack'),
  }
```

substituting `e_nom_extendable` / `s_nom_extendable` on the cards that use them,
and `false` for `committable` on cards with no such field.

In `pypsa-gui/frontend/src/layout/CreationForm.tsx`, the render loop currently
filters nothing. Gate it so the create and edit forms stop disagreeing about
when `p_nom_min` / `p_nom_max` are shown (criterion 34):

```tsx
              {fields.filter(f => isRevealed(f.key, {
                mode: solveMode,
                extendable: form.p_nom_extendable === 'true'
                  || form.e_nom_extendable === 'true'
                  || form.s_nom_extendable === 'true',
                committable: form.committable === 'true',
                noSlackBus: false,   // the creation form makes no network-wide claim
              })).map(f => {
```

- [ ] **Step 6: Run, type-check, commit**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/utils/revealRules.test.ts
PATH="$PIXI_BIN:$PATH" npm test
PATH="$PIXI_BIN:$PATH" npm run build
```

Expected: all green; build exit 0.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/utils/attributeCatalog.ts \
        pypsa-gui/frontend/src/utils/revealRules.test.ts \
        pypsa-gui/frontend/src/layout/properties/cardKit.tsx \
        pypsa-gui/frontend/src/layout/PropertiesPanel.tsx \
        pypsa-gui/frontend/src/layout/CreationForm.tsx
git diff --cached --name-only
git commit -m "feat(gui): six reveal rules mirroring the backend's own err/warn split"
```

---

## Task 8: "+ Add parameter" on the creation form, and final verification

**Files:**
- Modify: `pypsa-gui/frontend/src/layout/CreationForm.tsx`
- Modify: `pypsa-gui/frontend/src/layout/CreationForm.prefill.test.tsx`

**Interfaces:** Consumes `ExtrasSection`-equivalent behaviour via
`loadExtras`/`saveExtras`/`creationScope` (Task 3) and `useCatalog` (Scope A).

**Context.** D23: the picker lists the component's `Input` attributes the form
does not already show, one row each — `description` as help text, `unit` as the
suffix, `type` plus the attribute's default so the user can see what they are
adding. **The default is displayed as `default_text` whenever `default` is
`null`**, so an unbounded attribute reads `inf` rather than blank (criterion
30). Adding a parameter never seeds a value: the field opens empty and PyPSA's
default continues to apply until the user types one.

The creation form's scope key is `creationScope(item.id)` — the palette id,
which is exactly what D23 fixes and criterion 31 checks.

- [ ] **Step 1: Write the failing test**

Append to `pypsa-gui/frontend/src/layout/CreationForm.prefill.test.tsx`:

```tsx
import { creationScope, loadExtras, saveExtras } from '../utils/extrasStore'

describe('+ Add parameter — D23', () => {
  it('persists the chosen key under the palette-id scope (criterion 31)', () => {
    saveExtras(creationScope('thermal'), ['weight'])
    expect(loadExtras(creationScope('thermal'))).toEqual(['weight'])
    expect(creationScope('thermal')).toBe('creationform:extras:thermal')
  })

  it('renders an input for a persisted extra', async () => {
    saveExtras(creationScope('thermal'), ['weight'])
    renderForm({ id: 'thermal', label: 'Thermal' })
    expect(await screen.findByTitle(/weight/i)).toBeTruthy()
  })

  it('shows inf rather than a blank for an unbounded default (criterion 30)', async () => {
    renderForm({ id: 'thermal', label: 'Thermal' })
    await userEvent.click(await screen.findByText(/add parameter/i))
    const opt = screen.getByRole('option', { name: /p_nom_max/ })
    expect(opt.textContent).toContain('inf')
  })
})
```

The file's existing `vi.mock('../api/network', …)` must gain `getCatalog`
returning at least `weight` (`default: 1`, `default_text: '1.0'`) and
`p_nom_max` (`default: null`, `default_text: 'inf'`), and the file needs the
`installStorage()` helper in `beforeEach`, copied from
`src/utils/extrasStore.test.ts`. It also needs `userEvent` imported.

- [ ] **Step 2: Run and watch it fail**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/CreationForm.prefill.test.tsx
```

Expected: the two rendering tests fail; the persistence one passes (Task 3 built it).

- [ ] **Step 3: Render the extras block on the creation form**

In `pypsa-gui/frontend/src/layout/CreationForm.tsx`, add the imports:

```tsx
import { useCatalog } from '../hooks/useCatalog'
import { creationScope, loadExtras, saveExtras } from '../utils/extrasStore'
```

Inside the component, beside the existing state:

```tsx
  // Attributes the user has added to THIS palette type (spec D23). The scope
  // key is the palette id, not the component class: two palette items can map
  // to one class (thermal and renewable are both Generators) and the user's
  // choice belongs to the item they clicked.
  const [extraKeys, setExtraKeys] = useState<string[]>(() => loadExtras(creationScope(item.id)))
  const [picking, setPicking] = useState(false)
  const { data: catalog } = useCatalog(COMPONENT_TYPE[item.id] ?? null)
```

and render, immediately after the field loop's closing `)}`:

```tsx
        {catalog && (
          <div className="col-span-2 mt-2 pt-2 border-t border-border">
            {extraKeys.map(k => {
              const attr = catalog.attributes.find(a => a.name === k)
              return (
                <label key={k} className="flex items-center gap-1.5 mb-1">
                  <span className="text-[10px] text-muted w-32 shrink-0 truncate"
                        title={attr?.description ?? k}>
                    {k}{attr?.unit ? ` (${attr.unit})` : ''}
                  </span>
                  <input
                    value={form[k] ?? ''}
                    onChange={e => set(k, e.target.value)}
                    placeholder={attr?.default_text ?? ''}
                    className="flex-1 bg-bg border border-border rounded px-1.5 py-0.5 text-xs"
                  />
                </label>
              )
            })}
            {picking ? (
              <select
                autoFocus
                value=""
                onChange={e => {
                  const v = e.target.value
                  if (!v) return
                  const next = [...extraKeys, v]
                  setExtraKeys(next)
                  saveExtras(creationScope(item.id), next)
                  setPicking(false)
                }}
                onBlur={() => setPicking(false)}
                className="w-full bg-bg border border-accent rounded px-1.5 py-0.5 text-xs"
              >
                <option value="">Choose a parameter…</option>
                {catalog.attributes
                  .filter(a => a.status.startsWith('Input')
                    && !fields.some(f => f.key === a.name)
                    && !extraKeys.includes(a.name))
                  .map(a => (
                    <option key={a.name} value={a.name}>
                      {/* default_text stands in whenever `default` is null, so an
                          unbounded attribute reads `inf` rather than blank (D23). */}
                      {a.name}{a.unit ? ` (${a.unit})` : ''} — {a.type}, default {a.default_text || '—'}
                    </option>
                  ))}
              </select>
            ) : (
              <button
                type="button"
                onClick={() => setPicking(true)}
                className="text-[10px] text-accent hover:underline"
              >+ Add parameter</button>
            )}
          </div>
        )}
```

The extras values already reach the payload: `createMut` sends `form`, and the
extras keys live in `form` alongside the curated ones. Confirm by reading the
`createMut` body — if it enumerates keys rather than spreading `form`, add
`Object.assign(payload, extrasPatch(form, extraKeys))` before the create call,
importing `extrasPatch` from `./properties/cardKit`.

- [ ] **Step 4: Run the file**

```bash
PATH="$PIXI_BIN:$PATH" npx vitest run src/layout/CreationForm.prefill.test.tsx
```

Expected: all pass, including Scope C's eight prefill/coordinate tests
unmodified.

- [ ] **Step 5: Full verification of the whole feature**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing/pypsa-gui/frontend"
PATH="$PIXI_BIN:$PATH" npm test
PATH="$PIXI_BIN:$PATH" npm run build
cd "../backend"
"$PIXI_BIN/python" -m pytest
```

Expected: frontend 0 failures with a count no lower than 855; build exit 0;
backend 0 failures with a count no lower than 2336.

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/asset-editing"
git diff --stat e8614a35 -- pypsa-gui/frontend/src/utils/coerce.ts
```

Expected: **no output** — criterion 41 still holds.

- [ ] **Step 6: Commit**

```bash
git branch --show-current
git status --porcelain
git add pypsa-gui/frontend/src/layout/CreationForm.tsx \
        pypsa-gui/frontend/src/layout/CreationForm.prefill.test.tsx
git diff --cached --name-only
git commit -m "feat(gui): + Add parameter on the creation form, persisted per palette type"
```

---

## Scope B is done when

- `npm test` is green with 0 failures and a count no lower than 855.
- `npm run build` exits 0.
- `"$PIXI_BIN/python" -m pytest` reports 0 failures and no fewer than 2336 passed.
- Manually, in the running app (`bash pypsa-gui/start.sh`), criteria 29–34:
  adding a catalog attribute through "+ Add parameter" on a Generator, saving
  and reloading the project shows the saved value; the picker shows each
  attribute's type, unit, description and default, and reads `inf` rather than
  blank for `p_nom_max`; the chosen extras survive a reload; in `lopf` mode
  ticking `p_nom_extendable` with `capital_cost = 0` and `overnight_cost = 5`
  produces no required-field error, setting both to 0 produces one naming the
  pair, and switching to `pf` clears it; on a network with no Slack bus and a
  solve mode of `pf`, `control` is marked required on every Bus form and any
  one Slack clears it everywhere; `p_nom_min`/`p_nom_max` stay hidden in the
  creation form until `p_nom_extendable` is ticked.

**Not claimed by this plan:** the desktop `.app` is stale until `npm run build`
is followed by `bash pypsa-gui/build-macos.sh`.
