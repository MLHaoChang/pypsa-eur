# Unplaced buses on the satellite / hybrid map — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Satellite and Hybrid show the network — or say plainly why they can't — and stop every code path that measures a distance to Null Island.

**Architecture:** One shared predicate decides whether a bus has a real location (`x == 0 && y == 0` means "never set", because that is PyPSA's default). It lives in a pure frontend module mirrored by a four-line guard in the backend's haversine helper. On top of that sit two small UI units: a presentational panel that names the problem, and a click-to-place mode that reuses the existing bus-move mutation. Nothing is stored — "placed" is derived from `x`/`y` on every read, so there is no schema change and no migration.

**Tech Stack:** TypeScript / React 19 / react-leaflet 5 / Leaflet 1.9 / vitest 4 + @testing-library/react (frontend); FastAPI / PyPSA / pytest (backend); pixi for every interpreter and for node.

**Spec:** `docs/superpowers/specs/2026-07-30-unplaced-buses-map-design.md`

## Global Constraints

- **D1 — the test is `x == 0` AND `y == 0`, both exactly.** A bus at `(0, 51.5)` is Greenwich and must keep working. `-0.0 === 0` in JavaScript, so negative zero needs no special case.
- **D2 — derived, not stored.** No `placed` column, no schema change, no migration.
- **D3 — the backend guard lands BEFORE the frontend wiring** (Task 2 before Task 4). A map that hides a bus while the backend still measures a line to it is worse than today's behaviour. Backend-only is safe on its own; frontend-only is not.
- **D4 — the predicate lives in `frontend/src/utils/geo.ts`**, imported everywhere. Never re-inline it.
- **D5 — persistent UI replaces the transient toast.** Delete the toast; do not keep both.
- **D6 — "Recalculate line lengths" is offered after placement, never automatic.** Rewriting `length` is a model edit.
- **D7 — no CSV import, no geocoding, no extra basemaps, no offline tiles.** Out of scope.
- **Never write to or delete anything under `pypsa-gui/backend/projects/`** — 113 MB of irreplaceable user work.
- **Never hardcode an interpreter path.** Use `pixi run`. Node and npm come from `.pixi/envs/default/bin`, not the system.
- **Before every commit:** run `git branch --show-current` (expect `feature/local-app-impl`) and commit path-limited — `git commit <path> ...`, never `git add -A`.
- **Judge a test run by its exit code, not by grepping the output.** This suite's pytest config suppresses the usual `N passed` summary line; `-q` prints warnings last and a naive grep for "passed" finds nothing on a green run.

## Test commands (both verified working on 2026-07-30)

Frontend, single file — from `pypsa-gui/frontend`:

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/utils/geo.test.ts
```

Backend, single file — from the repo root:

```bash
pixi run -e test python -m pytest pypsa-gui/backend/tests/test_line_lengths.py -q; echo "exit=$?"
```

Full backend suite: `pixi run gui-tests` (the `test` environment is the only one carrying `pywebview`; `-e test` is not optional).

## File Structure

| File | Responsibility |
|---|---|
| `pypsa-gui/frontend/src/utils/geo.ts` **(new)** | The coordinate rule and the unplaced-bus derivation. Pure, no React, no Leaflet. |
| `pypsa-gui/frontend/src/utils/geo.test.ts` **(new)** | The rule's contract, including Greenwich. |
| `pypsa-gui/frontend/src/components/UnplacedBusesPanel.tsx` **(new)** | Presentational empty state + partial chip. No Leaflet import, so it is testable in jsdom. |
| `pypsa-gui/frontend/src/components/UnplacedBusesPanel.test.tsx` **(new)** | The copy the user actually reads. |
| `pypsa-gui/frontend/src/pages/MapCanvas.tsx` **(modify)** | Wiring only: import the rule, delete the local copy and the toast, render the panel, host click-to-place. |
| `pypsa-gui/backend/routers/network.py:288-299` **(modify)** | `_bus_coord` returns `None` for the default pair. |
| `pypsa-gui/backend/tests/test_line_lengths.py` **(new)** | Regression test for the corruption path. |

**Why the empty state is its own file:** mounting `MapCanvas` in jsdom would need react-query providers, the zustand store, `CanvasResultsProvider`, and a Leaflet container with real dimensions — there is no precedent for a Leaflet test in this suite and the result would be brittle. Splitting the panel out gives a real component test of the user-visible behaviour with none of that. The "no markers render" half of the guarantee is covered by `geo.test.ts`: every marker, line and bubble call site already does `if (!c) return null`, so a null coordinate *is* the non-render.

---

### Task 1: The coordinate rule

**Files:**
- Create: `pypsa-gui/frontend/src/utils/geo.ts`
- Test: `pypsa-gui/frontend/src/utils/geo.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `interface BusCoords { x: number | null | undefined; y: number | null | undefined }`
  - `busLatLng(b: BusCoords): [number, number] | null` — Leaflet `[lat, lng]` order, or `null` when unplaced/invalid
  - `isPlaced(b: BusCoords): boolean`
  - `unplacedBusNames<T extends BusCoords & { name: string }>(buses: T[]): string[]` — names in input order

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/utils/geo.test.ts`:

```ts
// The contract for "does this bus have a real location?". Every case here maps
// to a success criterion in
// docs/superpowers/specs/2026-07-30-unplaced-buses-map-design.md.
// The Greenwich cases are the point of the file: the rule is the exact PAIR of
// zeroes, and a naive `x === 0 || y === 0` would hide a legitimate bus.
import { describe, expect, it } from 'vitest'
import { busLatLng, isPlaced, unplacedBusNames } from './geo'

describe('busLatLng', () => {
  it("treats PyPSA's (0, 0) default as not placed", () => {
    expect(busLatLng({ x: 0, y: 0 })).toBeNull()
    expect(busLatLng({ x: -0, y: 0 })).toBeNull()
    expect(busLatLng({ x: 0, y: -0 })).toBeNull()
    // netCDF omits all-default columns, so an untouched network arrives with
    // no x/y at all rather than with zeroes.
    expect(busLatLng({ x: null, y: null })).toBeNull()
    expect(busLatLng({ x: undefined, y: undefined })).toBeNull()
  })

  it('keeps a bus on either zero meridian — only the exact pair means unset', () => {
    expect(busLatLng({ x: 0, y: 51.5 })).toEqual([51.5, 0])     // Greenwich
    expect(busLatLng({ x: 6.96, y: 0 })).toEqual([0, 6.96])     // equator
  })

  it('swaps PyPSA (x=lng, y=lat) into Leaflet [lat, lng]', () => {
    expect(busLatLng({ x: 6.96, y: 50.938 })).toEqual([50.938, 6.96])
  })

  it('rejects non-finite and out-of-range values', () => {
    expect(busLatLng({ x: NaN, y: 50 })).toBeNull()
    expect(busLatLng({ x: 6.96, y: 91 })).toBeNull()
    expect(busLatLng({ x: 6.96, y: -91 })).toBeNull()
    expect(busLatLng({ x: 181, y: 50 })).toBeNull()
    expect(busLatLng({ x: -181, y: 50 })).toBeNull()
  })
})

describe('isPlaced', () => {
  it('is the boolean face of busLatLng', () => {
    expect(isPlaced({ x: 0, y: 0 })).toBe(false)
    expect(isPlaced({ x: 6.96, y: 50.938 })).toBe(true)
  })
})

describe('unplacedBusNames', () => {
  it('names every bus still at the default, in input order', () => {
    expect(unplacedBusNames([
      { name: 'B1', x: 0, y: 0 },
      { name: 'B2', x: 6.96, y: 50.9 },
      { name: 'B3', x: 0, y: 0 },
    ])).toEqual(['B1', 'B3'])
  })

  it('is empty when every bus is placed', () => {
    expect(unplacedBusNames([{ name: 'B1', x: 6.96, y: 50.9 }])).toEqual([])
  })

  it('is empty for an empty network', () => {
    expect(unplacedBusNames([])).toEqual([])
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

From `pypsa-gui/frontend`:

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/utils/geo.test.ts
```

Expected: FAIL — `Failed to resolve import "./geo"`.

- [ ] **Step 3: Write the implementation**

Create `pypsa-gui/frontend/src/utils/geo.ts`:

```ts
// Bus coordinates, one source of truth. PyPSA's convention is bus.x =
// longitude, bus.y = latitude; Leaflet wants [lat, lng] tuples, so every
// consumer needs the same swap and the same validity rule.
//
// WHY (0, 0) MEANS "NOT PLACED". PyPSA's Bus.x and Bus.y default to 0.0, and a
// netCDF export omits all-default columns entirely — a network whose author
// never set coordinates arrives with no x/y variables at all. Treating that as
// a real position put every such bus at 0°N 0°E in the Gulf of Guinea, made
// the map fit to a zero-size bounds and slam to maximum zoom over open water
// where Esri has no imagery, and suppressed the map's own "no coordinates"
// warning because zero buses looked missing. It also let the backend's
// haversine measure line lengths TO Null Island and write them into the model.
// See docs/superpowers/specs/2026-07-30-unplaced-buses-map-design.md.
//
// The rule is BOTH coordinates exactly zero. A bus at (0, 51.5) is Greenwich
// and stays valid; `x === 0 || y === 0` would hide it. `-0.0 === 0` in
// JavaScript, so negative zero needs no separate case.
//
// This module is deliberately pure — no React, no Leaflet — so the rule can be
// tested directly and imported from both the map and any future consumer.
// `utils/carriers.ts` exists because the same kind of predicate was
// copy-pasted into four components and drifted; this is that lesson applied
// before the drift rather than after.

export interface BusCoords {
  x: number | null | undefined
  y: number | null | undefined
}

/** Leaflet-ordered [lat, lng], or null when the bus has no usable location. */
export function busLatLng(b: BusCoords): [number, number] | null {
  const lat = Number(b.y)
  const lng = Number(b.x)
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null
  if (lat === 0 && lng === 0) return null
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null
  return [lat, lng]
}

export function isPlaced(b: BusCoords): boolean {
  return busLatLng(b) !== null
}

/** Names of the buses still awaiting a location, in the order given. */
export function unplacedBusNames<T extends BusCoords & { name: string }>(buses: T[]): string[] {
  return buses.filter(b => !isPlaced(b)).map(b => b.name)
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/utils/geo.test.ts
```

Expected: PASS, 6 tests.

- [ ] **Step 5: Mutation-check the rule**

Temporarily change `if (lat === 0 && lng === 0)` to `if (lat === 0 || lng === 0)` and re-run. Expected: the "keeps a bus on either zero meridian" test FAILS. Revert the mutation and confirm green again. If the mutation survives, the test is not pinning D1 and must be fixed before continuing.

- [ ] **Step 6: Commit**

```bash
git branch --show-current   # expect feature/local-app-impl
git commit pypsa-gui/frontend/src/utils/geo.ts pypsa-gui/frontend/src/utils/geo.test.ts \
  -m "feat(gui): one rule for whether a bus has a real location

PyPSA's Bus.x/Bus.y default to 0.0 and netCDF omits all-default columns, so a
network that never set coordinates has no x/y at all. Nothing knew that pair
meant 'unset'. Pure module, no consumers yet.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The backend stops measuring to Null Island

Lands **before** any frontend wiring (D3).

**Files:**
- Modify: `pypsa-gui/backend/routers/network.py:288-299`
- Test: `pypsa-gui/backend/tests/test_line_lengths.py` (create)

**Interfaces:**
- Consumes: nothing from Task 1 — this is the backend half of the same rule, deliberately duplicated across the language boundary.
- Produces: `_bus_coord(n, bus_name) -> tuple[float, float] | None` now returns `None` for the default pair. Callers `_line_haversine_km`, `_recompute_lengths_for_bus`, `create_line` and `recalculate_line_lengths` already handle `None` by skipping — no caller changes.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_line_lengths.py`:

```python
"""
Line lengths must never be measured to a bus that has no location.

PyPSA's Bus.x / Bus.y default to 0.0. Before this guard, `_bus_coord` returned
(0.0, 0.0) for a bus nobody had placed, so `recalculate_lengths` computed the
great-circle distance from a real substation to 0°N 0°E in the Gulf of Guinea
and wrote several thousand kilometres into n.lines.length as fact.

`routers/network.py:386-394` documents an earlier instance of the same bug
class — a partial PUT whose Pydantic default made every bus look like it had
moved to the origin. That one was fixed at the request layer. This is the
remaining path: coordinates that genuinely are (0, 0).
"""
from __future__ import annotations


def _place(client, name: str, x: float, y: float):
    r = client.post("/api/network/buses", json={"name": name, "v_nom": 380.0, "x": x, "y": y})
    assert r.status_code == 201, r.text


def _unplaced(client, name: str):
    """A bus created the way the GUI creates one: no coordinates supplied."""
    r = client.post("/api/network/buses", json={"name": name, "v_nom": 380.0})
    assert r.status_code == 201, r.text


def _line(client, name: str, bus0: str, bus1: str, length: float):
    # A positive length is the manual-override path (create_line only
    # auto-fills when the caller passes length <= 0), so this pins a value the
    # recalculation must be seen to leave alone.
    r = client.post("/api/network/lines", json={
        "name": name, "bus0": bus0, "bus1": bus1, "length": length, "s_nom": 100.0,
    })
    assert r.status_code == 201, r.text


def _lengths(client) -> dict[str, float]:
    return {ln["name"]: ln["length"] for ln in client.get("/api/network/lines").json()}


def test_recalculate_skips_a_line_touching_an_unplaced_bus(client):
    _place(client, "COLOGNE", 6.960, 50.938)
    _unplaced(client, "NOWHERE")
    _line(client, "L1", "COLOGNE", "NOWHERE", 42.0)

    r = client.post("/api/network/lines/recalculate_lengths")
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 0, "skipped": 1, "total": 1}

    # The pre-existing value survives untouched. Without the guard this became
    # the haversine distance from Cologne to Null Island — about 5,600 km.
    assert _lengths(client)["L1"] == 42.0


def test_recalculate_still_measures_a_line_between_two_placed_buses(client):
    # The guard must not turn into "skip everything": this is the case the
    # feature exists for. Cologne -> Berlin is ~475 km.
    _place(client, "COLOGNE", 6.960, 50.938)
    _place(client, "BERLIN", 13.405, 52.520)
    _line(client, "L1", "COLOGNE", "BERLIN", 1.0)

    r = client.post("/api/network/lines/recalculate_lengths")
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1, "skipped": 0, "total": 1}
    assert 460.0 < _lengths(client)["L1"] < 490.0


def test_a_bus_on_the_prime_meridian_is_still_placed(client):
    # D1: the rule is BOTH coordinates zero. Greenwich has x == 0 and must
    # keep working, or the guard has traded one silent corruption for another.
    _place(client, "GREENWICH", 0.0, 51.478)
    _place(client, "COLOGNE", 6.960, 50.938)
    _line(client, "L1", "GREENWICH", "COLOGNE", 1.0)

    r = client.post("/api/network/lines/recalculate_lengths")
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1, "skipped": 0, "total": 1}
    assert 480.0 < _lengths(client)["L1"] < 520.0
```

- [ ] **Step 2: Run the test to verify it fails**

From the repo root:

```bash
pixi run -e test python -m pytest pypsa-gui/backend/tests/test_line_lengths.py -q; echo "exit=$?"
```

Expected: `exit=1`. `test_recalculate_skips_a_line_touching_an_unplaced_bus` fails on `{"updated": 1, "skipped": 0, ...} != {"updated": 0, "skipped": 1, ...}`. The other two tests should already pass — they pin behaviour that must survive the change.

- [ ] **Step 3: Write the implementation**

In `pypsa-gui/backend/routers/network.py`, in `_bus_coord`, insert the guard between the finite check and the return:

```python
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    # PyPSA's Bus.x / Bus.y default to 0.0, so the exact pair means "never
    # set", not "the Gulf of Guinea". Without this, every line touching an
    # unplaced bus is rewritten to the great-circle distance to Null Island
    # and stored as fact — see tests/test_line_lengths.py and
    # docs/superpowers/specs/2026-07-30-unplaced-buses-map-design.md.
    #
    # BOTH exactly zero: a bus at (0, 51.478) is Greenwich and stays valid.
    if x == 0.0 and y == 0.0:
        return None
    return x, y
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pixi run -e test python -m pytest pypsa-gui/backend/tests/test_line_lengths.py -q; echo "exit=$?"
```

Expected: `exit=0`.

- [ ] **Step 5: Mutation-check the guard (spec success criterion 5)**

Change `if x == 0.0 and y == 0.0:` to `if x == 0.0 or y == 0.0:` and re-run. Expected: `test_a_bus_on_the_prime_meridian_is_still_placed` FAILS. Then delete the guard entirely and re-run. Expected: `test_recalculate_skips_a_line_touching_an_unplaced_bus` FAILS. Restore the guard and confirm `exit=0`. If either mutation survives, the tests do not pin the behaviour.

- [ ] **Step 6: Run the full backend suite for regressions**

```bash
pixi run gui-tests; echo "exit=$?"
```

Expected: `exit=0`. Existing tests that create buses without coordinates and then assert on line lengths are the plausible breakage; if one fails, read it before changing it — it may be pinning the old wrong behaviour, in which case update it and say so in the commit message.

- [ ] **Step 7: Commit**

```bash
git branch --show-current   # expect feature/local-app-impl
git commit pypsa-gui/backend/routers/network.py pypsa-gui/backend/tests/test_line_lengths.py \
  -m "fix(gui): stop measuring line lengths to Null Island

A bus nobody placed sits at PyPSA's (0, 0) default, and _bus_coord returned it
as a real position, so recalculate_lengths wrote the distance from a real
substation to the Gulf of Guinea into n.lines.length. Lands before the
frontend half so the map never hides a bus the backend is still measuring to.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The panel that names the problem

**Files:**
- Create: `pypsa-gui/frontend/src/components/UnplacedBusesPanel.tsx`
- Test: `pypsa-gui/frontend/src/components/UnplacedBusesPanel.test.tsx`

**Interfaces:**
- Consumes: nothing (presentational — the caller does the deriving with `unplacedBusNames` from Task 1).
- Produces:
  ```ts
  interface UnplacedBusesPanelProps {
    unplacedCount: number
    totalCount: number
    placing: boolean
    onStartPlacing: () => void
  }
  export default function UnplacedBusesPanel(props: UnplacedBusesPanelProps): JSX.Element | null
  ```

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/components/UnplacedBusesPanel.test.tsx`:

```tsx
// What the map tells a user whose buses have no coordinates. This replaced a
// transient toast that fired once and vanished — see D5 in
// docs/superpowers/specs/2026-07-30-unplaced-buses-map-design.md. A message
// that disappears is most of why the original bug went unreported for so long.
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import UnplacedBusesPanel from './UnplacedBusesPanel'

const noop = () => {}

describe('UnplacedBusesPanel', () => {
  it('renders nothing when every bus is placed', () => {
    const { container } = render(
      <UnplacedBusesPanel unplacedCount={0} totalCount={12} placing={false} onStartPlacing={noop} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('explains the problem in full when no bus is placed', () => {
    render(
      <UnplacedBusesPanel unplacedCount={3} totalCount={3} placing={false} onStartPlacing={noop} />,
    )
    expect(screen.getByText(/no bus has a location yet/i)).toBeDefined()
    // The count and the cause both have to be on screen: "3 buses" tells the
    // user it is their whole network, "0, 0" tells them why the map is blank.
    expect(screen.getByText(/all 3 buses/i)).toBeDefined()
    expect(screen.getByText(/0, 0/)).toBeDefined()
  })

  it('shrinks to a count when only some buses are unplaced', () => {
    render(
      <UnplacedBusesPanel unplacedCount={3} totalCount={12} placing={false} onStartPlacing={noop} />,
    )
    expect(screen.getByText(/3 of 12 buses unplaced/i)).toBeDefined()
    expect(screen.queryByText(/no bus has a location yet/i)).toBeNull()
  })

  it('starts placement when the action is pressed', async () => {
    const onStartPlacing = vi.fn()
    render(
      <UnplacedBusesPanel unplacedCount={3} totalCount={3} placing={false} onStartPlacing={onStartPlacing} />,
    )
    await userEvent.click(screen.getByRole('button', { name: /place buses on the map/i }))
    expect(onStartPlacing).toHaveBeenCalledTimes(1)
  })

  it('gets out of the way while placement is running', () => {
    // The panel sits over the map. Leaving it up during placement would cover
    // the very thing the user has to click.
    const { container } = render(
      <UnplacedBusesPanel unplacedCount={3} totalCount={3} placing onStartPlacing={noop} />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

From `pypsa-gui/frontend`:

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/components/UnplacedBusesPanel.test.tsx
```

Expected: FAIL — `Failed to resolve import "./UnplacedBusesPanel"`.

- [ ] **Step 3: Write the implementation**

Create `pypsa-gui/frontend/src/components/UnplacedBusesPanel.tsx`:

```tsx
import { MapPin } from 'lucide-react'

// The map's answer to "why is this blank?". Presentational on purpose: it
// imports no Leaflet and no store, so it can be mounted in jsdom and the copy
// the user reads is actually under test. The caller derives the counts with
// `unplacedBusNames` from utils/geo.
//
// It replaces a `toast()` that fired once per mount and vanished after 4.5
// seconds (D5). A network whose buses have no coordinates is a persistent
// condition and deserves persistent UI.
interface UnplacedBusesPanelProps {
  unplacedCount: number
  totalCount: number
  /** True while click-to-place is running; the panel hides so it can't cover the map. */
  placing: boolean
  onStartPlacing: () => void
}

export default function UnplacedBusesPanel({
  unplacedCount, totalCount, placing, onStartPlacing,
}: UnplacedBusesPanelProps) {
  if (unplacedCount === 0 || placing) return null

  // Some buses are placed — the map already shows a network, so a full-size
  // panel over it would be in the way. A count in the toolbar band is enough.
  if (unplacedCount < totalCount) {
    return (
      <button
        type="button"
        onClick={onStartPlacing}
        title="Place the remaining buses by clicking the map"
        className="absolute z-[500] flex items-center gap-1.5 px-2.5 py-1.5 bg-bg border border-border
                   rounded-md shadow text-[11px] font-medium text-text hover:text-accent
                   hover:border-accent transition-colors"
        style={{ top: 128, left: 10 }}
      >
        <MapPin size={12} />
        {unplacedCount} of {totalCount} buses unplaced
      </button>
    )
  }

  // Nothing is placed: the map is showing its default view and no network at
  // all. State the cause plainly — the coordinates, not the basemap.
  return (
    <div
      role="status"
      className="absolute z-[500] left-1/2 -translate-x-1/2 max-w-md w-[min(28rem,calc(100%-2rem))]
                 bg-bg border border-border rounded-lg shadow-lg p-4 text-center"
      style={{ top: 96 }}
    >
      <div className="flex items-center justify-center gap-2 text-sm font-semibold text-text">
        <MapPin size={14} className="text-accent" />
        No bus has a location yet
      </div>
      <p className="mt-2 text-xs text-muted leading-relaxed">
        Satellite and Hybrid plot buses by longitude (x) and latitude (y).
        All {totalCount} buses are still at PyPSA&rsquo;s default 0, 0.
      </p>
      <button
        type="button"
        onClick={onStartPlacing}
        className="mt-3 px-3 py-1.5 rounded-md bg-accent text-white text-xs font-medium
                   hover:opacity-90 transition-opacity"
      >
        Place buses on the map
      </button>
      <p className="mt-2 text-[10px] text-muted">
        or open a bus and paste a <span className="font-mono">lat, lng</span> pair into its properties
      </p>
    </div>
  )
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/components/UnplacedBusesPanel.test.tsx
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git branch --show-current   # expect feature/local-app-impl
git commit pypsa-gui/frontend/src/components/UnplacedBusesPanel.tsx \
           pypsa-gui/frontend/src/components/UnplacedBusesPanel.test.tsx \
  -m "feat(gui): persistent empty state for a map with no placed buses

Replaces a 4.5s toast with UI that stays as long as the condition does.
Presentational and Leaflet-free so the copy is under test. Not wired yet.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Wire the map to the rule

The behavioural change users see: Satellite and Hybrid stop flying to open ocean.

**Files:**
- Modify: `pypsa-gui/frontend/src/pages/MapCanvas.tsx` — lines 207-215 (delete local `busLatLng`), 217-235 (`FitToNetwork`), 579-591 (delete the toast), plus the render block around line 748.

**Interfaces:**
- Consumes: `busLatLng`, `unplacedBusNames` from `../utils/geo` (Task 1); `UnplacedBusesPanel` from `../components/UnplacedBusesPanel` (Task 3).
- Produces: `placing: boolean` state and `setPlacing`, consumed by Task 5.

- [ ] **Step 1: Replace the local predicate with the shared one**

Delete lines 207-215 of `MapCanvas.tsx` — the comment block and the whole `busLatLng` function — and add to the import block at the top of the file:

```tsx
import { busLatLng, unplacedBusNames } from '../utils/geo'
import UnplacedBusesPanel from '../components/UnplacedBusesPanel'
```

Leave all ten existing `busLatLng(...)` calls (lines 225, 270, 585, 776-777, 816-817, 850-851, 905) untouched — they now resolve to the import. Do **not** re-inline the rule anywhere (D4).

- [ ] **Step 2: Verify the file still type-checks**

From `pypsa-gui/frontend`:

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc -b --noEmit
```

Expected: no errors. If `busLatLng` is reported as unused-then-undefined, the deletion took the wrong lines.

- [ ] **Step 3: Suspend the auto-fit during placement**

`FitToNetwork` fires the moment the first bus gains coordinates, which would yank the map out from under someone mid-placement. Replace the component (now around line 208 after the deletion) with:

```tsx
// One-shot helper that fits the map view to the network bounds the first
// time data lands. Subsequent renders don't re-fit so the user's pan/zoom
// is preserved.
//
// `suspended` is true while click-to-place is running. Without it, placing the
// first bus makes `points.length === 1` and this calls setView(..., 11) —
// snapping the map to that bus while the user is lining up the next click.
function FitToNetwork({ buses, suspended }: { buses: Bus[]; suspended: boolean }) {
  const map = useMap()
  const fittedRef = useRef(false)
  useEffect(() => {
    if (fittedRef.current || suspended) return
    const points = buses.map(busLatLng).filter((p): p is [number, number] => p !== null)
    if (points.length === 0) return
    if (points.length === 1) {
      map.setView(points[0], 11)
    } else {
      map.fitBounds(L.latLngBounds(points), { padding: [40, 40] })
    }
    fittedRef.current = true
  }, [buses, map, suspended])
  return null
}
```

Note `fittedRef` is deliberately **not** set while suspended, so the fit still happens once placement ends.

- [ ] **Step 4: Delete the toast and derive the counts**

Remove the whole `missingWarnedRef` effect (lines 579-591, from the `// Warn once if many buses…` comment through the closing `}, [buses])`) and the now-unused `missingWarnedRef` declaration. In its place:

```tsx
  // Buses still at PyPSA's (0, 0) default. Derived on every render (D2) — the
  // previous code toasted this once per mount and then forgot it, which is
  // most of why a network of unplaced buses read as a broken basemap.
  const unplaced = useMemo(() => unplacedBusNames(buses as Bus[]), [buses])

  // Set by UnplacedBusesPanel / the placement strip; consumed by ClickToPlace
  // and by FitToNetwork's `suspended` prop.
  const [placing, setPlacing] = useState(false)
```

If `toast` becomes unused in the file after this, leave the import — `recalcMut` and `updateBusPosMut` both still use it. Confirm with `npx tsc -b --noEmit`.

- [ ] **Step 5: Render the panel and pass `suspended`**

In the render block, change the `FitToNetwork` line to pass the new prop:

```tsx
        <FitToNetwork buses={buses as Bus[]} suspended={placing} />
```

and add the panel as a sibling of the existing map toolbar — **outside** `<MapContainer>`, next to the `{ctxMenu && ...}` block, so it is not a Leaflet layer:

```tsx
      <UnplacedBusesPanel
        unplacedCount={unplaced.length}
        totalCount={(buses as Bus[]).length}
        placing={placing}
        onStartPlacing={() => setPlacing(true)}
      />
```

- [ ] **Step 6: Run the whole frontend suite**

From `pypsa-gui/frontend`:

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run; echo "exit=$?"
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc -b --noEmit; echo "exit=$?"
```

Expected: `exit=0` from both.

- [ ] **Step 7: Verify by hand against the real symptom**

Start the dev stack (`pixi run gui`), open the `3_nodes_system` project, and switch to Satellite.

Expected: the map holds its central-Europe default view (roughly 50°N 10°E, zoom 5) and the panel reads "No bus has a location yet … All 3 buses are still at PyPSA's default 0, 0."

Before this change the same click flew to 0°N 0°E at maximum zoom and showed a featureless panel with no explanation. If you still land on open ocean, `busLatLng` is not the imported one.

- [ ] **Step 8: Commit**

```bash
git branch --show-current   # expect feature/local-app-impl
git commit pypsa-gui/frontend/src/pages/MapCanvas.tsx \
  -m "fix(gui): the satellite map stops flying to Null Island

Imports the shared rule instead of its own copy, so an unplaced bus is hidden
rather than drawn in the Gulf of Guinea and the auto-fit no longer collapses
to a zero-size bounds at maximum zoom over open water. The once-per-mount
toast is replaced by the persistent panel.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Click-to-place

**Files:**
- Modify: `pypsa-gui/frontend/src/pages/MapCanvas.tsx`

**Interfaces:**
- Consumes: `placing` / `setPlacing` and `unplaced` (Task 4); the existing `updateBusPosMut` (line 609).
- Produces: nothing consumed downstream.

- [ ] **Step 1: Add the click handler component**

`useMapEvents` is exported by the installed react-leaflet (verified: `react-leaflet/lib/hooks.d.ts` declares `useMapEvents(handlers: LeafletEventHandlerFnMap): LeafletMap`). Add it to the existing react-leaflet import and place this component beside `FitToNetwork`:

```tsx
// Click-to-place. Mounted inside <MapContainer> only while placement is
// running, so the map has no click handler at all the rest of the time.
//
// Leaflet does not fire `click` at the end of a drag — the same guarantee the
// bus markers already rely on — so panning to find a location cannot drop a
// bus by accident.
function ClickToPlace({ onPick }: { onPick: (lat: number, lng: number) => void }) {
  const map = useMapEvents({
    click: (e) => onPick(e.latlng.lat, e.latlng.lng),
  })
  useEffect(() => {
    const el = map.getContainer()
    const previous = el.style.cursor
    el.style.cursor = 'crosshair'
    return () => { el.style.cursor = previous }
  }, [map])
  return null
}
```

- [ ] **Step 2: Add the placement queue and the picker**

Beside the `placing` state from Task 4:

```tsx
  // The bus currently awaiting a click. Always the first still-unplaced bus,
  // recomputed from `unplaced` so it follows the server's view of the network
  // rather than a stale local queue — placing a bus removes it from `unplaced`
  // when the buses query invalidates, which advances this on its own.
  const placingBus = placing ? (unplaced[skipped] ?? unplaced[0]) : undefined
  const [skipped, setSkipped] = useState(0)

  // Leave placement mode when there is nothing left to place.
  useEffect(() => {
    if (placing && unplaced.length === 0) {
      setPlacing(false)
      setSkipped(0)
      toast.success('Every bus now has a location.')
    }
  }, [placing, unplaced.length])

  // Escape exits placement mode.
  useEffect(() => {
    if (!placing) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setPlacing(false); setSkipped(0) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [placing])
```

Declare `const [skipped, setSkipped] = useState(0)` **above** `placingBus` — the snippet above shows them adjacent for readability, but `skipped` must be initialised first.

- [ ] **Step 3: Mount the handler and the strip**

Inside `<MapContainer>`, beside `<FitToNetwork …>`:

```tsx
        {placing && placingBus && (
          <ClickToPlace onPick={(lat, lng) => updateBusPosMut.mutate({ name: placingBus, lat, lng })} />
        )}
```

Outside `<MapContainer>`, beside `<UnplacedBusesPanel …>`:

```tsx
      {placing && placingBus && (
        <div
          className="absolute z-[500] left-1/2 -translate-x-1/2 flex items-center gap-3 px-3 py-2
                     bg-bg border border-border rounded-lg shadow-lg text-xs"
          style={{ bottom: 24 }}
        >
          <span className="text-text">
            Placing <span className="font-mono font-medium">{placingBus}</span> — click the map
            <span className="text-muted"> ({unplaced.length} left)</span>
          </span>
          <button
            type="button"
            onClick={() => setSkipped(s => s + 1)}
            disabled={skipped + 1 >= unplaced.length}
            className="px-2 py-1 rounded border border-border text-text hover:bg-border/30
                       transition-colors disabled:opacity-35"
          >Skip</button>
          <button
            type="button"
            onClick={() => { setPlacing(false); setSkipped(0) }}
            className="px-2 py-1 rounded bg-accent text-white hover:opacity-90 transition-opacity"
          >Done</button>
        </div>
      )}
```

- [ ] **Step 4: Offer the recalculation, never run it (D6)**

The completion toast from Step 2 becomes an offer. Replace `toast.success('Every bus now has a location.')` with:

```tsx
      toast.success(
        (t) => (
          <span className="flex items-center gap-2">
            Every bus now has a location.
            <button
              type="button"
              onClick={() => { toast.dismiss(t.id); handleRecalc() }}
              className="px-2 py-0.5 rounded border border-border text-[11px] hover:bg-border/30"
            >Recalculate line lengths</button>
          </span>
        ),
        { duration: 8000 },
      )
```

`handleRecalc` (line 642) already puts a confirmation in front of the write, so the model edit stays two deliberate clicks away. Move the completion effect below `handleRecalc`'s declaration, or the reference is a use-before-define.

- [ ] **Step 5: Type-check and run the suite**

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc -b --noEmit; echo "exit=$?"
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run; echo "exit=$?"
```

Expected: `exit=0` from both.

- [ ] **Step 6: Verify by hand (spec success criterion 4)**

With `pixi run gui`, open `3_nodes_system` in Satellite mode and press "Place buses on the map".

Check all of:
1. The cursor becomes a crosshair and the strip reads `Placing B1 — click the map (3 left)`.
2. **Drag the map without releasing on a click** — no bus is placed.
3. Click three times in different places. Each click advances to the next bus; after the third the strip disappears and the completion toast offers the recalculation.
4. The three buses and the lines between them are now drawn.
5. Drag one of the placed buses — it moves and its connected line lengths update.
6. Press "Place buses on the map" again after placing only one bus (reload first): the partial chip reads `2 of 3 buses unplaced`.
7. Press Escape mid-placement — the mode exits and the panel comes back.

- [ ] **Step 7: Commit**

```bash
git branch --show-current   # expect feature/local-app-impl
git commit pypsa-gui/frontend/src/pages/MapCanvas.tsx \
  -m "feat(gui): place buses by clicking the map

Walks the unplaced list through the existing bus-move mutation, so placement
inherits its refusal to send a partial PUT before the buses query has settled.
Offers the line-length recalculation on completion rather than running it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Rebuild the packaged app and accept it

Required by `CLAUDE.md`: the macOS app must be rebuilt after any change that reaches it, and the running app must be quit before reinstalling.

**Files:** none modified. This task produces the artefact.

- [ ] **Step 1: Quit any running copy**

```bash
osascript -e 'quit app "PyPSA Studio"' 2>/dev/null; pgrep -f "PyPSA Studio" || echo "not running"
```

Expected: `not running`. Replacing the bundle under a live process unlinks files it has not yet opened — that is what produced the earlier "save failed … disk I/O error", when `pyproj` went to read `proj.db` lazily and found it gone.

- [ ] **Step 2: Build**

```bash
bash pypsa-gui/build-macos.sh
```

Expected: the credential gate prints `clean: no secrets or user data found`, then the DMG size. A non-zero exit anywhere fails the whole script (`set -euo pipefail`).

- [ ] **Step 3: Install and launch**

```bash
rm -rf "/Applications/PyPSA Studio.app"
cp -R "pypsa-gui/dist-app/PyPSA Studio.app" /Applications/
open -a "PyPSA Studio"
```

- [ ] **Step 4: Accept against the reported symptom**

In the packaged app, open `3_nodes_system` and switch to Satellite.

Expected: the central-Europe default view and the "No bus has a location yet" panel — not open ocean. Place the three buses by clicking; confirm the lines draw between them. Then quit the app cleanly and check that no unflushed work was reported:

```bash
cat ~/Library/Application\ Support/PyPSA\ Studio/last-shutdown-report.json
```

Expected: `"unflushed": []` and `"server_stage": "clean"`.

- [ ] **Step 5: Refresh the hand-over DMG**

```bash
cp pypsa-gui/dist-app/PyPSA-Studio.dmg ~/Desktop/PyPSA-Studio.dmg
ls -lh ~/Desktop/PyPSA-Studio.dmg
```

- [ ] **Step 6: Confirm the tree is clean**

```bash
git status --porcelain
```

Expected: empty. `dist-app/` and `.build-venv/` are gitignored; if either appears, stop and fix the ignore rules rather than committing build output.

---

## Self-review

**Spec coverage.** D1 → Task 1 Step 5 and Task 2 Step 5 (mutation-checked both sides). D2 → Task 4 Step 4 (`useMemo`, no persistence). D3 → task ordering, stated in Global Constraints. D4 → Task 1, enforced by Task 4 Step 1. D5 → Task 3 and Task 4 Step 4. D6 → Task 5 Step 4. D7 → nothing in the plan touches import, geocoding or basemaps. Success criteria 1-5 → Task 4 Step 7, Task 2 Step 5 (criterion 5), Task 2 Step 1 (criterion 3), Task 5 Step 6 (criterion 4), Task 1 Step 1 (criterion 2).

**One deliberate divergence from the spec.** The spec's test list says "MapCanvas component test — all-default buses render no markers and show the empty state". This plan splits that: the empty state gets a real component test (Task 3) by being extracted into a Leaflet-free unit, and the "no markers" half is covered by `geo.test.ts`, because every marker/line/bubble call site already returns `null` on a null coordinate. There is no Leaflet test precedent in this suite and mounting `MapCanvas` in jsdom would need react-query, zustand, `CanvasResultsProvider` and a sized container. The spec is amended to match.

**Placeholder scan.** No TBD/TODO, no "handle edge cases", no "similar to Task N". Every code step carries the code.

**Type consistency.** `busLatLng`, `isPlaced`, `unplacedBusNames`, `BusCoords` are defined in Task 1 and used with those exact names in Tasks 4 and 5. `UnplacedBusesPanelProps` fields (`unplacedCount`, `totalCount`, `placing`, `onStartPlacing`) match between Task 3's definition and Task 4's call site. `updateBusPosMut.mutate({ name, lat, lng })` in Task 5 matches the existing signature at `MapCanvas.tsx:610`.
