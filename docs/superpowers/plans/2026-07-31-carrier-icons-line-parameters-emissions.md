# Carrier icons, length-dependent line parameters, and explained emissions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A solar plant shows a sun; a line's impedance follows its length or the user is asked; and `0 tCO2` never again silently means "nobody told me what this fuel emits".

**Architecture:** Three independent parts sharing one shape — a default value indistinguishable from a deliberate one. Each is detected by a pure, separately-tested function, and each surfaces a choice rather than rewriting silently. Part A moves an existing carrier-icon table into a shared module and resolves a group's icon by badge identity. Part B adds a preview/apply pair around impedance rescaling. Part C fills two catalog gaps and makes a zero state its reason.

**Tech Stack:** TypeScript / React 19 / react-leaflet 5 / vitest 4 + @testing-library/react (frontend); FastAPI / PyPSA / pytest (backend); pixi for every interpreter and for node.

**Spec:** `docs/superpowers/specs/2026-07-31-line-parameters-and-carrier-icons-design.md`

## Global Constraints

- **A1/A2 — the carrier table lives in ONE place** (`utils/carrierBadges.tsx`), imported by both canvases. Never copy it. A group's icon is resolved by **badge identity**, not carrier-string equality: `onwind` + `offwind-ac` is uniform (both → `Wind`); `solar` + `onwind` is mixed and falls back to the category icon.
- **B1 — length is geometry, impedance is a modelling choice.** Length keeps being rewritten automatically. Impedance is **never** written without consent.
- **B2 — per-km is the quantity held constant:** `new_r = (old_r / old_length) × new_length`, likewise `x` and `b`.
- **B4 — the threshold is 5% and it is a *length* test.** For a per-km-preserving rescale `new_r/old_r == new_length/old_length` exactly, so `r`, `x` and `b` share one relative change. Do not test them separately.
- **C1 — add only emission factors this repo already justifies:** `gas` → `0.187` (identical to its own `CCGT`/`OCGT`), `diesel` → `0.267` (identical to its own `oil`). Inventing a factor is worse than omitting one.
- **C3 — the warning is ungated:** not conditioned on `co2_price`, not on a global constraint. Those two conditions are exactly why nothing fired.
- **C4 — offer, never rewrite.** `ensure_carrier` returns early for an existing row, so a catalog entry can never repair an existing project.
- **Both carrier catalogs change together** — `backend/services/carrier_catalog.py` and `frontend/src/utils/carrierCatalog.ts`. The backend file's docstring says to keep them in sync.
- **Never write to or delete anything under `pypsa-gui/backend/projects/`.**
- **Never hardcode an interpreter or tool path.** Node comes from pixi.
- **Judge every test run by its exit code.** This pytest config suppresses the `N passed` summary line and `-q` prints warnings last.
- **Before every commit:** `git branch --show-current` must print `feature/local-app-impl`. Commit path-limited; never `git add -A`.
- **Floating map UI is `z-[900]`** (CLAUDE.md:697 — Leaflet controls sit at z-800) **and hides while a slide panel or the command palette is open.**

## Test commands (both verified working)

Frontend, from `pypsa-gui/frontend`:
```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run <file>
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc -b --noEmit
```
Backend, from the repo root:
```bash
pixi run -e test python -m pytest pypsa-gui/backend/tests/<file> -q; echo "exit=$?"
pixi run gui-tests; echo "exit=$?"     # full suite, ~20 min, ONE blocking call
```

## File Structure

| File | Responsibility |
|---|---|
| `frontend/src/utils/carrierBadges.tsx` **(new)** | The carrier→icon table and `uniformBadge()`. Moved out of `TopologyCanvas.tsx`. |
| `frontend/src/utils/carrierBadges.test.tsx` **(new)** | `uniformBadge`'s contract. |
| `frontend/src/pages/TopologyCanvas.tsx` **(modify)** | Delete the local table; import it. |
| `frontend/src/pages/MapCanvas.tsx` **(modify)** | Badge-aware bubbles; the rescale dialog and its batching. |
| `frontend/src/utils/rescale.ts` **(new)** | The ≤5% / >5% partition. Pure. |
| `frontend/src/utils/rescale.test.ts` **(new)** | Threshold boundaries. |
| `frontend/src/components/RescaleDialog.tsx` **(new)** | Presentational old→new table. No Leaflet, no store. |
| `frontend/src/components/RescaleDialog.test.tsx` **(new)** | The copy and the two actions. |
| `backend/routers/network.py` **(modify)** | Previews from the two length-changing paths; the apply endpoint. |
| `backend/tests/test_line_rescale.py` **(new)** | Preview arithmetic, skip reasons, apply scoping. |
| `backend/services/carrier_catalog.py` **(modify)** | `gas`, `diesel`. |
| `frontend/src/utils/carrierCatalog.ts` **(modify)** | The same two, kept in sync. |
| `backend/services/validation_service.py` **(modify)** | The ungated `carrier_zero_co2` warning. |
| `backend/tests/test_carrier_emissions.py` **(new)** | The warning's four cases + `ensure_carrier`'s early return. |
| `frontend/src/pages/results/Emissions.tsx` **(modify)** | A zero that states its reason. |

---

### Task 1: The shared carrier-badge table

**Files:**
- Create: `pypsa-gui/frontend/src/utils/carrierBadges.tsx`
- Test: `pypsa-gui/frontend/src/utils/carrierBadges.test.tsx`
- Modify: `pypsa-gui/frontend/src/pages/TopologyCanvas.tsx:453-487` (delete the local table and helper; import instead)

**Interfaces:**
- Consumes: `H2Icon` from `../components/AssetIcons` (already shared — `TopologyCanvas.tsx:24` imports it from there).
- Produces:
  ```ts
  export type BadgeIcon = React.FC<{ size?: number; style?: React.CSSProperties; strokeWidth?: number }>
  export interface BadgeDef { Icon: BadgeIcon; label: string }
  export const CARRIER_BADGES: Record<string, BadgeDef>
  export function getCarrierBadge(carrier: string): BadgeDef
  export function uniformBadge(carriers: string[]): BadgeDef | null
  ```

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/utils/carrierBadges.test.tsx`:

```tsx
// The map draws ONE bubble per bus × category, so it must decide which icon is
// honest for a group of carriers. Testing badge IDENTITY rather than carrier
// strings is the point: onwind + offwind-ac is uniform (both are wind), while
// solar + onwind is not.
import { describe, expect, it } from 'vitest'
import { getCarrierBadge, uniformBadge } from './carrierBadges'

describe('getCarrierBadge', () => {
  it('resolves solar — the gap that made a solar plant render as wind', () => {
    expect(getCarrierBadge('solar').label).toBe('Solar')
    expect(getCarrierBadge('solar-rooftop').label).toBe('Solar')
  })

  it('resolves the other carriers this project uses', () => {
    expect(getCarrierBadge('onwind').label).toBe('Wind')
    expect(getCarrierBadge('offwind-ac').label).toBe('Wind')
    expect(getCarrierBadge('hydro').label).toBe('Hydro')
    expect(getCarrierBadge('nuclear').label).toBe('Nuclear')
    expect(getCarrierBadge('coal').label).toBe('Coal')
    expect(getCarrierBadge('biomass').label).toBe('Biomass')
    expect(getCarrierBadge('gas').label).toBe('Gas')
  })

  it('falls back to a truncated label for an unknown carrier', () => {
    expect(getCarrierBadge('unobtainium').label).toBe('unobt')
  })
})

describe('uniformBadge', () => {
  it('returns the badge when every carrier resolves to the same one', () => {
    expect(uniformBadge(['solar'])?.label).toBe('Solar')
    // Different strings, same badge — a turbine is honest for this group.
    expect(uniformBadge(['onwind', 'offwind-ac', 'offwind-dc'])?.label).toBe('Wind')
  })

  it('returns null when the group is mixed', () => {
    expect(uniformBadge(['solar', 'onwind'])).toBeNull()
    expect(uniformBadge(['gas', 'coal'])).toBeNull()
  })

  it('returns null for an empty group', () => {
    expect(uniformBadge([])).toBeNull()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

From `pypsa-gui/frontend`:
```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/utils/carrierBadges.test.tsx
```
Expected: FAIL — `Failed to resolve import "./carrierBadges"`.

- [ ] **Step 3: Create the shared module**

Create `pypsa-gui/frontend/src/utils/carrierBadges.tsx`. Move `BadgeIcon`, `BadgeDef`, `CARRIER_BADGES` and `getCarrierBadge` verbatim from `TopologyCanvas.tsx:453-487`, then add the missing carriers and `uniformBadge`:

```tsx
import React from 'react'
import {
  Atom, BatteryCharging, Droplets, Factory, Flame, Fuel, Leaf, Mountain,
  PlugZap, Sun, Thermometer, Waves, Wind, Zap,
} from 'lucide-react'
import { H2Icon } from '../components/AssetIcons'

// Carrier → pictogram, shared by the schematic canvas and the map.
//
// It lived inside TopologyCanvas.tsx, which meant the map could only get it by
// copying — the same copy-and-drift that utils/carriers.ts exists to prevent.
// The map was not copying it at all: it hard-coded ONE icon per category, so
// every renewable group rendered as a wind turbine and a real project's solar
// plant was drawn as wind.
//
// `solar` was also simply absent, so even the original table would have fallen
// through to a generic Zap.

export type BadgeIcon = React.FC<{ size?: number; style?: React.CSSProperties; strokeWidth?: number }>
export interface BadgeDef { Icon: BadgeIcon; label: string }

export const CARRIER_BADGES: Record<string, BadgeDef> = {
  H2:              { Icon: H2Icon,          label: 'H₂' },
  hydrogen:        { Icon: H2Icon,          label: 'H₂' },
  electrolysis:    { Icon: Droplets,        label: 'ELY' },
  heat:            { Icon: Thermometer,     label: 'Heat' },
  heat_pump:       { Icon: Thermometer,     label: 'HP' },
  'heat pump':     { Icon: Thermometer,     label: 'HP' },
  // Fossil. `gas` covers the fuel-level carrier; CCGT/OCGT are technologies.
  gas:             { Icon: Flame,           label: 'Gas' },
  CCGT:            { Icon: Flame,           label: 'Gas' },
  OCGT:            { Icon: Flame,           label: 'Gas' },
  coal:            { Icon: Factory,         label: 'Coal' },
  lignite:         { Icon: Factory,         label: 'Coal' },
  oil:             { Icon: Fuel,            label: 'Oil' },
  diesel:          { Icon: Fuel,            label: 'Oil' },
  nuclear:         { Icon: Atom,            label: 'Nuclear' },
  SMR:             { Icon: H2Icon,          label: 'SMR' },
  // Renewables.
  solar:           { Icon: Sun,             label: 'Solar' },
  'solar-rooftop': { Icon: Sun,             label: 'Solar' },
  onwind:          { Icon: Wind,            label: 'Wind' },
  'offwind-ac':    { Icon: Wind,            label: 'Wind' },
  'offwind-dc':    { Icon: Wind,            label: 'Wind' },
  wind:            { Icon: Wind,            label: 'Wind' },
  hydro:           { Icon: Droplets,        label: 'Hydro' },
  ror:             { Icon: Droplets,        label: 'Hydro' },
  PHS:             { Icon: Droplets,        label: 'PHS' },
  biomass:         { Icon: Leaf,            label: 'Biomass' },
  biogas:          { Icon: Leaf,            label: 'Biogas' },
  geothermal:      { Icon: Mountain,        label: 'Geo' },
  wave:            { Icon: Waves,           label: 'Wave' },
  tidal:           { Icon: Waves,           label: 'Tidal' },
  // Storage and transport.
  battery:         { Icon: BatteryCharging, label: 'Batt.' },
  BEV:             { Icon: BatteryCharging, label: 'BEV' },
  DC:              { Icon: PlugZap,         label: 'DC' },
  HVDC:            { Icon: PlugZap,         label: 'HVDC' },
  AC:              { Icon: Zap,             label: 'AC' },
  resistive:       { Icon: Zap,             label: 'Res.' },
}

export function getCarrierBadge(carrier: string): BadgeDef {
  if (CARRIER_BADGES[carrier]) return CARRIER_BADGES[carrier]
  const key = Object.keys(CARRIER_BADGES).find(k =>
    carrier.toLowerCase().includes(k.toLowerCase())
  )
  return key ? CARRIER_BADGES[key] : { Icon: Zap, label: carrier.slice(0, 5) }
}

/**
 * The badge shared by every carrier in a group, or null when the group is
 * mixed or empty.
 *
 * Compared by badge, not by carrier string, so `onwind` + `offwind-ac` still
 * resolves to a turbine — the icon is honest and specific. `solar` + `onwind`
 * has no honest single icon, so the caller falls back to the category icon.
 */
export function uniformBadge(carriers: string[]): BadgeDef | null {
  if (carriers.length === 0) return null
  const first = getCarrierBadge(carriers[0])
  return carriers.every(c => getCarrierBadge(c) === first) ? first : null
}
```

Note `getCarrierBadge` returns the **same object reference** for entries sharing a `BadgeDef`, which is why `uniformBadge` can compare with `===`. Entries that must compare equal therefore point at distinct-but-equal literals today — so define shared ones once and reference them, or compare on `Icon` + `label`. Use the latter to avoid a subtle trap:

```ts
export function uniformBadge(carriers: string[]): BadgeDef | null {
  if (carriers.length === 0) return null
  const first = getCarrierBadge(carriers[0])
  return carriers.every(c => {
    const b = getCarrierBadge(c)
    return b.Icon === first.Icon && b.label === first.label
  }) ? first : null
}
```

Use this second version. The `===`-on-object version above is shown only to explain why it is wrong.

- [ ] **Step 4: Run the test to verify it passes**

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/utils/carrierBadges.test.tsx
```
Expected: PASS, 6 tests.

- [ ] **Step 5: Point TopologyCanvas at the shared module**

Delete `BadgeIcon`, `BadgeDef`, `CARRIER_BADGES` and `getCarrierBadge` from `TopologyCanvas.tsx` (lines 453-487) and add:

```tsx
import { getCarrierBadge, type BadgeDef } from '../utils/carrierBadges'
```

Leave `CarrierBadge` (the component) where it is. Remove any lucide imports in `TopologyCanvas.tsx` that are now unused — `tsc` will not flag unused imports, so check by hand.

- [ ] **Step 6: Type-check and run the whole frontend suite**

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc -b --noEmit; echo "exit=$?"
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run; echo "exit=$?"
```
Expected: `exit=0` from both.

- [ ] **Step 7: Commit**

```bash
git branch --show-current   # expect feature/local-app-impl
git commit pypsa-gui/frontend/src/utils/carrierBadges.tsx \
           pypsa-gui/frontend/src/utils/carrierBadges.test.tsx \
           pypsa-gui/frontend/src/pages/TopologyCanvas.tsx \
  -m "feat(gui): one carrier-icon table, with solar in it

Moved out of TopologyCanvas so the map can import rather than copy, and
filled the gaps — solar was absent entirely, so it fell through to a generic
Zap. uniformBadge() resolves a mixed group honestly.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Carrier-aware bubbles on the map

**Files:**
- Modify: `pypsa-gui/frontend/src/pages/MapCanvas.tsx` — `AssetGroupLayerProps` (`:266`), the bubble (`:288`), `assetGroupDivIcon` (`:153`), the memo (`:755-768`), the context menu (`:1161`, `:1163`, `:1210`)

**Interfaces:**
- Consumes: `uniformBadge`, `BadgeDef` from `../utils/carrierBadges` (Task 1).
- Produces: nothing downstream.

- [ ] **Step 1: Widen the memo's value type**

`categoryCountsByBus` currently maps to `Record<AssetCategory, number>`. It becomes:

```tsx
interface CategoryEntry { count: number; badge: BadgeDef | null }
```

Replace the memo at `MapCanvas.tsx:755-768` with:

```tsx
  // Count AND icon per bus × category. The badge is resolved here, once, so
  // the decision lives in one place and consumers just render it. A parallel
  // map keyed the same way would be the same drift risk one level down.
  const categoryCountsByBus = useMemo(() => {
    const carriers = new Map<string, Record<AssetCategory, string[]>>()
    const out = new Map<string, Record<AssetCategory, CategoryEntry>>()
    for (const b of buses as Bus[]) {
      carriers.set(b.name, { Thermal: [], Renewables: [], Storage: [], Load: [] })
    }
    for (const g of generators as Generator[]) {
      const r = carriers.get(g.bus); if (!r) continue
      if (isRenewableCarrier(g.carrier)) r.Renewables.push(g.carrier)
      else r.Thermal.push(g.carrier)
    }
    for (const l of loads as Load[]) { const r = carriers.get(l.bus); if (r) r.Load.push(l.carrier ?? '') }
    for (const s of sus as StorageUnit[]) { const r = carriers.get(s.bus); if (r) r.Storage.push(s.carrier) }
    for (const s of stores as Store[]) { const r = carriers.get(s.bus); if (r) r.Storage.push(s.carrier) }
    for (const [busName, byCat] of carriers) {
      out.set(busName, {
        Thermal:    { count: byCat.Thermal.length,    badge: uniformBadge(byCat.Thermal) },
        Renewables: { count: byCat.Renewables.length, badge: uniformBadge(byCat.Renewables) },
        Storage:    { count: byCat.Storage.length,    badge: uniformBadge(byCat.Storage) },
        Load:       { count: byCat.Load.length,       badge: uniformBadge(byCat.Load) },
      })
    }
    return out
  }, [buses, generators, loads, sus, stores])
```

If `Load` has no `carrier` field on its type, pass `[]` for Load rather than inventing one — check `api/types.ts` and use whichever is true. A load with no carrier resolves to `null` and keeps the category icon, which is correct.

- [ ] **Step 2: Update the three read sites**

- `MapCanvas.tsx:266` — `categoryCountsByBus: Map<string, Record<AssetCategory, CategoryEntry>>`
- `MapCanvas.tsx:288` — `const entry = categoryCountsByBus.get(busName)?.[cat]; const count = entry?.count ?? 0`
- `MapCanvas.tsx:1163` — `const withAssets = cats.filter(c => (counts?.[c]?.count ?? 0) > 0)`
- `MapCanvas.tsx:1210` — `const count = counts?.[cat]?.count ?? 0`

- [ ] **Step 3: Draw the resolved icon**

`assetGroupDivIcon` (`MapCanvas.tsx:153`) takes `cat` and reads `CATEGORY_STYLE[cat]`. Give it the badge:

```tsx
function assetGroupDivIcon(
  cat: AssetCategory, count: number, dx: number, dy: number,
  dispatchMw?: number, badge?: BadgeDef | null,
): L.DivIcon {
  const { Icon: CategoryIcon, color } = CATEGORY_STYLE[cat]
  // A group whose carriers all share one badge gets that badge's pictogram —
  // a solar-only group is a sun, not the generic renewables turbine. A mixed
  // group keeps the category icon, because no single icon is honest for it.
  const Icon = badge?.Icon ?? CategoryIcon
  const iconSvg = ReactDOMServer.renderToStaticMarkup(
    <Icon size={14} color={color} strokeWidth={2} />
  )
  // …rest unchanged
```

`BadgeIcon` accepts `size`, `style` and `strokeWidth` but **not** `color`. Lucide icons accept `color`; `H2Icon` may not. Pass colour via `style={{ color }}` instead of the `color` prop so both work:

```tsx
    <Icon size={14} style={{ color }} strokeWidth={2} />
```

and verify the rendered SVG still picks up the colour (lucide uses `currentColor`).

At the call site (`MapCanvas.tsx:301`):
```tsx
            icon={assetGroupDivIcon(cat, count, offset.dx, offset.dy, dispatchMw, entry?.badge)}
```

- [ ] **Step 4: Type-check and run the suite**

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc -b --noEmit; echo "exit=$?"
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run; echo "exit=$?"
```
Expected: `exit=0` from both.

- [ ] **Step 5: Verify by hand (spec success criterion 1)**

Start the dev stack. Note `pixi run gui`'s sequential `depends-on` never gets past the non-exiting `gui-backend`, so start `gui-backend` and `gui-frontend` separately.

Open `3_nodes_system` (generator carriers `gas` and `solar`), switch to Satellite, right-click a bus and show its asset groups. Expected: the bus with `PV_B3` shows a **sun**, and the bus with `Gas_B2` shows a **flame**. Before this change both categories drew their generic icon and the solar plant appeared as a wind turbine.

If you cannot observe the browser, say so and mark it unverified — do not describe an outcome you did not see.

- [ ] **Step 6: Commit**

```bash
git branch --show-current   # expect feature/local-app-impl
git commit pypsa-gui/frontend/src/pages/MapCanvas.tsx \
  -m "fix(gui): the map draws the carrier's icon, not the category's

A solar plant rendered as a wind turbine because the map hard-coded one icon
per category. Resolved per bus x category via uniformBadge, so a mixed group
still falls back to the generic icon rather than picking a winner.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The backend preview and apply pair

**Files:**
- Modify: `pypsa-gui/backend/routers/network.py` — `_recompute_lengths_for_bus` (`:330`), `update_bus`'s coord block (`:439-447`), `recalculate_line_lengths` (`:549`)
- Test: `pypsa-gui/backend/tests/test_line_rescale.py` (create)

**Interfaces:**
- Consumes: `_bus_coord`, `_line_haversine_km` (unchanged).
- Produces:
  - `_impedance_preview(line_name, old_length, new_length, old) -> dict | None`
  - `_recompute_lengths_for_bus(n, bus_name) -> list[dict]` (was `int`)
  - `PUT /api/network/buses/{name}` response gains `"rescale": [...]`
  - `POST /api/network/lines/recalculate_lengths` response gains `"rescale": [...]`
  - `POST /api/network/lines/rescale_impedances`, body `{"lines": [{"name","r","x","b"}]}` → `{"updated": int, "skipped": [{"name","reason"}]}`

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_line_rescale.py`:

```python
"""
Changing a line's length must not silently change its per-km impedance.

r/x/b are stored absolute; the properties form presents them per-km (it
divides by length to display and multiplies back to save). Both backend paths
that change length — a bus move and the Ruler button — rewrote ONLY length, so
the physical per-km value silently changed by the length ratio. Click-to-place
makes that easy to hit: LineCreate.length defaults to 1.0 km, so real geography
rescales a hand-built network by orders of magnitude.

Length stays automatic — it follows from coordinates. Impedance is a modelling
choice and is never written without consent, so these endpoints PREVIEW and a
separate one applies.
"""
from __future__ import annotations


def _bus(client, name, x, y):
    r = client.post("/api/network/buses", json={"name": name, "v_nom": 380.0, "x": x, "y": y})
    assert r.status_code == 201, r.text


def _line(client, name, bus0, bus1, length, r_ohm, x_ohm, b_s):
    resp = client.post("/api/network/lines", json={
        "name": name, "bus0": bus0, "bus1": bus1, "length": length,
        "r": r_ohm, "x": x_ohm, "b": b_s, "s_nom": 500.0,
    })
    assert resp.status_code == 201, resp.text


def _lines(client):
    return {ln["name"]: ln for ln in client.get("/api/network/lines").json()}


def test_recalculate_previews_the_rescale_without_writing_it(client):
    _bus(client, "COLOGNE", 6.960, 50.938)
    _bus(client, "BERLIN", 13.405, 52.520)
    _line(client, "L1", "COLOGNE", "BERLIN", 1.0, 3.0, 17.5, 0.00015)

    r = client.post("/api/network/lines/recalculate_lengths")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == 1

    (prev,) = body["rescale"]
    assert prev["name"] == "L1"
    assert prev["old_length"] == 1.0
    assert 460.0 < prev["new_length"] < 490.0
    assert prev["skipped_reason"] is None
    # Per-km preserved: new/old == length ratio, identically for r, x and b.
    ratio = prev["new_length"] / prev["old_length"]
    assert abs(prev["new"]["r"] - 3.0 * ratio) < 1e-6
    assert abs(prev["new"]["x"] - 17.5 * ratio) < 1e-6
    assert abs(prev["new"]["b"] - 0.00015 * ratio) < 1e-9
    assert abs(prev["rel_change"] - (ratio - 1.0)) < 1e-6

    # PREVIEW ONLY. The length is rewritten (geometry); the impedance is not.
    after = _lines(client)["L1"]
    assert after["r"] == 3.0 and after["x"] == 17.5 and after["b"] == 0.00015
    assert 460.0 < after["length"] < 490.0


def test_apply_writes_only_the_named_lines(client):
    _bus(client, "COLOGNE", 6.960, 50.938)
    _bus(client, "BERLIN", 13.405, 52.520)
    _line(client, "L1", "COLOGNE", "BERLIN", 1.0, 3.0, 17.5, 0.00015)
    _line(client, "L2", "COLOGNE", "BERLIN", 1.0, 9.0, 21.0, 0.00030)

    r = client.post("/api/network/lines/rescale_impedances", json={
        "lines": [{"name": "L1", "r": 100.0, "x": 200.0, "b": 0.5}],
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1, "skipped": []}

    after = _lines(client)
    assert (after["L1"]["r"], after["L1"]["x"], after["L1"]["b"]) == (100.0, 200.0, 0.5)
    assert (after["L2"]["r"], after["L2"]["x"], after["L2"]["b"]) == (9.0, 21.0, 0.00030)


def test_apply_reports_an_unknown_line_instead_of_creating_it(client):
    _bus(client, "COLOGNE", 6.960, 50.938)
    r = client.post("/api/network/lines/rescale_impedances", json={
        "lines": [{"name": "GHOST", "r": 1.0, "x": 2.0, "b": 3.0}],
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 0, "skipped": [{"name": "GHOST", "reason": "unknown-line"}]}
    assert "GHOST" not in _lines(client)


def test_a_zero_length_line_is_reported_not_guessed(client):
    # Per-km is undefined when the old length is 0, so there is nothing to
    # preserve. Reporting beats inventing an impedance.
    _bus(client, "COLOGNE", 6.960, 50.938)
    _bus(client, "BERLIN", 13.405, 52.520)
    _line(client, "L1", "COLOGNE", "BERLIN", 0.0, 3.0, 17.5, 0.00015)

    body = client.post("/api/network/lines/recalculate_lengths").json()
    (prev,) = body["rescale"]
    assert prev["skipped_reason"] == "old_length<=0"
    assert prev["new"] == prev["old"]


def test_an_all_zero_impedance_line_produces_no_preview(client):
    # Scaling zero by anything is zero — there is no choice to offer.
    _bus(client, "COLOGNE", 6.960, 50.938)
    _bus(client, "BERLIN", 13.405, 52.520)
    _line(client, "L1", "COLOGNE", "BERLIN", 1.0, 0.0, 0.0, 0.0)

    body = client.post("/api/network/lines/recalculate_lengths").json()
    assert body["rescale"] == []


def test_moving_a_bus_previews_its_connected_lines(client):
    _bus(client, "COLOGNE", 6.960, 50.938)
    _bus(client, "BERLIN", 13.405, 52.520)
    _line(client, "L1", "COLOGNE", "BERLIN", 1.0, 3.0, 17.5, 0.00015)

    r = client.put("/api/network/buses/BERLIN", json={
        "name": "BERLIN", "v_nom": 380.0, "x": 2.35, "y": 48.86,   # -> Paris
    })
    assert r.status_code == 200, r.text
    (prev,) = r.json()["rescale"]
    assert prev["name"] == "L1"
    assert prev["skipped_reason"] is None
    assert prev["new"]["r"] > 3.0
    # Still preview-only.
    assert _lines(client)["L1"]["r"] == 3.0
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pixi run -e test python -m pytest pypsa-gui/backend/tests/test_line_rescale.py -q; echo "exit=$?"
```
Expected: `exit=1` — `KeyError: 'rescale'` and a 404/405 on `rescale_impedances`.

- [ ] **Step 3: Add the preview helper**

In `pypsa-gui/backend/routers/network.py`, beside `_line_haversine_km`:

```python
_IMPEDANCE_FIELDS = ("r", "x", "b")


def _impedance_preview(
    line_name: str, old_length: float, new_length: float, old: dict[str, float]
) -> dict | None:
    """
    What a per-km-preserving rescale WOULD do. Never mutates.

    Returns None when there is no choice to offer — an all-zero impedance
    scales to zero whatever the length does.

    The relative change is identical for r, x and b (each is multiplied by the
    same length ratio), so one number describes all three.
    """
    if all(float(old.get(k, 0.0) or 0.0) == 0.0 for k in _IMPEDANCE_FIELDS):
        return None

    reason: str | None = None
    if not (old_length > 0):
        reason = "old_length<=0"      # per-km undefined — nothing to preserve
    elif not (new_length > 0):
        reason = "new_length<=0"      # would zero the impedance

    if reason is not None:
        new = dict(old)
        rel = 0.0
    else:
        ratio = new_length / old_length
        new = {k: float(old.get(k, 0.0) or 0.0) * ratio for k in _IMPEDANCE_FIELDS}
        rel = abs(ratio - 1.0)

    return {
        "name": line_name,
        "old_length": float(old_length),
        "new_length": float(new_length),
        "old": {k: float(old.get(k, 0.0) or 0.0) for k in _IMPEDANCE_FIELDS},
        "new": new,
        "rel_change": rel,
        "skipped_reason": reason,
    }
```

- [ ] **Step 4: Make the two length paths report**

Replace `_recompute_lengths_for_bus` (`network.py:330`):

```python
def _recompute_lengths_for_bus(n, bus_name: str) -> list[dict]:
    """
    Rewrite line.length for every line touching `bus_name`, and return one
    preview per line whose impedance a per-km-preserving rescale would change.

    Length is rewritten here because it follows from geometry. Impedance is a
    modelling choice and is only PREVIEWED — see _impedance_preview and
    POST /lines/rescale_impedances. The caller must hold PyPSAService.get_lock().
    """
    if n.lines.empty:
        return []
    mask = (n.lines["bus0"] == bus_name) | (n.lines["bus1"] == bus_name)
    previews: list[dict] = []
    for line_name in n.lines.index[mask]:
        b0 = str(n.lines.at[line_name, "bus0"])
        b1 = str(n.lines.at[line_name, "bus1"])
        d = _line_haversine_km(n, b0, b1)
        if d is None:
            continue
        old_length = float(n.lines.at[line_name, "length"])
        old = {k: float(n.lines.at[line_name, k]) for k in _IMPEDANCE_FIELDS}
        n.lines.at[line_name, "length"] = float(d)
        p = _impedance_preview(str(line_name), old_length, float(d), old)
        if p is not None:
            previews.append(p)
    return previews
```

In `update_bus` (`network.py:439-447`), the changelog used the returned count:

```python
    rescale: list[dict] = []
    if coord_changed:
        new_name = result.get("name", name)
        with PyPSAService.get_lock():
            rescale = _recompute_lengths_for_bus(n, new_name)
        if rescale:
            change_log_service.log(
                "update", "Lines", "(auto)",
                f"Auto-rewrote {len(rescale)} line length(s) after bus '{new_name}' moved",
            )
    if isinstance(result, dict):
        result = {**result, "rescale": rescale}
    return result
```

`_recompute_lengths_for_bus` now only returns an entry when there is an impedance to offer, so a line with zero impedance no longer counts toward the changelog message. Keep a separate counter if the old count is wanted; the message above is accurate for what it says.

In `recalculate_line_lengths` (`network.py:549`), capture the old values before overwriting and add `"rescale"` to the response:

```python
    updated = 0
    skipped = 0
    previews: list[dict] = []
    with PyPSAService.get_lock():
        for line_name in n.lines.index:
            b0 = str(n.lines.at[line_name, "bus0"]) if "bus0" in n.lines.columns else ""
            b1 = str(n.lines.at[line_name, "bus1"]) if "bus1" in n.lines.columns else ""
            d_km = _line_haversine_km(n, b0, b1)
            if d_km is None:
                skipped += 1
                continue
            old_length = float(n.lines.at[line_name, "length"])
            old = {k: float(n.lines.at[line_name, k]) for k in _IMPEDANCE_FIELDS}
            n.lines.at[line_name, "length"] = float(d_km)
            updated += 1
            p = _impedance_preview(str(line_name), old_length, float(d_km), old)
            if p is not None:
                previews.append(p)
    ...
    return {"updated": updated, "skipped": skipped, "total": int(len(n.lines)), "rescale": previews}
```

- [ ] **Step 5: Add the apply endpoint**

Add to `models/schemas.py`:

```python
class ImpedanceRescaleEntry(BaseModel):
    name: str
    r: float
    x: float
    b: float


class ImpedanceRescaleRequest(BaseModel):
    lines: list[ImpedanceRescaleEntry]
```

and in `network.py`, **below** `recalculate_line_lengths`:

```python
@router.post("/lines/rescale_impedances")
def rescale_impedances(req: ImpedanceRescaleRequest):
    """
    Write the previewed impedances for an explicit list of lines.

    Deliberately takes the VALUES rather than recomputing them: by the time the
    user consents, the length has already been rewritten, so the old per-km is
    no longer derivable from the network. Recomputing here would silently use
    the new length as the old one and scale by 1.
    """
    n = PyPSAService.get_network()
    updated = 0
    skipped: list[dict] = []
    with PyPSAService.get_lock():
        for entry in req.lines:
            if entry.name not in n.lines.index:
                skipped.append({"name": entry.name, "reason": "unknown-line"})
                continue
            n.lines.at[entry.name, "r"] = float(entry.r)
            n.lines.at[entry.name, "x"] = float(entry.x)
            n.lines.at[entry.name, "b"] = float(entry.b)
            updated += 1
    if updated:
        change_log_service.log(
            "update", "Lines", "(rescale)",
            f"Rescaled impedance on {updated} line(s) to preserve per-km values after a length change",
        )
    return {"updated": updated, "skipped": skipped}
```

Import `ImpedanceRescaleRequest` alongside the other schemas.

- [ ] **Step 6: Run the tests**

```bash
pixi run -e test python -m pytest pypsa-gui/backend/tests/test_line_rescale.py -q; echo "exit=$?"
pixi run -e test python -m pytest pypsa-gui/backend/tests/test_line_lengths.py -q; echo "exit=$?"
```
Expected: `exit=0` from both. The second is the previous change's regression suite and must not have moved.

- [ ] **Step 7: Run the full backend suite**

```bash
pixi run gui-tests; echo "exit=$?"
```
Expected: `exit=0`. `_recompute_lengths_for_bus`'s return type changed from `int` to `list[dict]`; any existing test asserting on the old count is the plausible breakage. Read it before changing it.

- [ ] **Step 8: Commit**

```bash
git branch --show-current   # expect feature/local-app-impl
git commit pypsa-gui/backend/routers/network.py \
           pypsa-gui/backend/models/schemas.py \
           pypsa-gui/backend/tests/test_line_rescale.py \
  -m "feat(gui): preview the impedance rescale a length change implies

r/x/b are stored absolute but presented per-km, so rewriting only length
silently changed the physical per-km value. Length stays automatic (geometry);
impedance is previewed and applied separately (a modelling choice). The apply
endpoint takes VALUES, not names alone — once length is rewritten the old
per-km is no longer derivable.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The threshold partition

**Files:**
- Create: `pypsa-gui/frontend/src/utils/rescale.ts`
- Test: `pypsa-gui/frontend/src/utils/rescale.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export interface RescalePreview {
    name: string
    old_length: number; new_length: number
    old: { r: number; x: number; b: number }
    new: { r: number; x: number; b: number }
    rel_change: number
    skipped_reason: string | null
  }
  export const RESCALE_PROMPT_THRESHOLD = 0.05
  export function partitionRescale(previews: RescalePreview[]):
    { auto: RescalePreview[]; ask: RescalePreview[]; blocked: RescalePreview[] }
  ```

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/utils/rescale.test.ts`:

```ts
// Which impedance changes are applied silently, which need consent, and which
// cannot be applied at all. See D-B4 in
// docs/superpowers/specs/2026-07-31-line-parameters-and-carrier-icons-design.md
import { describe, expect, it } from 'vitest'
import { partitionRescale, RESCALE_PROMPT_THRESHOLD, type RescalePreview } from './rescale'

const preview = (over: Partial<RescalePreview>): RescalePreview => ({
  name: 'L1',
  old_length: 1, new_length: 1,
  old: { r: 3, x: 17.5, b: 0.00015 },
  new: { r: 3, x: 17.5, b: 0.00015 },
  rel_change: 0,
  skipped_reason: null,
  ...over,
})

describe('partitionRescale', () => {
  it('applies an immaterial change without asking', () => {
    const { auto, ask } = partitionRescale([preview({ rel_change: 0.01 })])
    expect(auto.map(p => p.name)).toEqual(['L1'])
    expect(ask).toEqual([])
  })

  it('asks when the change is material', () => {
    const { auto, ask } = partitionRescale([preview({ rel_change: 2.5 })])
    expect(auto).toEqual([])
    expect(ask.map(p => p.name)).toEqual(['L1'])
  })

  it('treats exactly the threshold as immaterial', () => {
    // The boundary is stated once, here, so nobody has to re-read the code to
    // find out whether it is < or <=.
    const { auto, ask } = partitionRescale([preview({ rel_change: RESCALE_PROMPT_THRESHOLD })])
    expect(auto.map(p => p.name)).toEqual(['L1'])
    expect(ask).toEqual([])
  })

  it('never applies or asks about a blocked line', () => {
    const { auto, ask, blocked } = partitionRescale([
      preview({ name: 'ZERO', skipped_reason: 'old_length<=0', rel_change: 0 }),
    ])
    expect(auto).toEqual([])
    expect(ask).toEqual([])
    expect(blocked.map(p => p.name)).toEqual(['ZERO'])
  })

  it('splits a mixed batch', () => {
    const { auto, ask, blocked } = partitionRescale([
      preview({ name: 'A', rel_change: 0.001 }),
      preview({ name: 'B', rel_change: 300 }),
      preview({ name: 'C', skipped_reason: 'new_length<=0' }),
    ])
    expect(auto.map(p => p.name)).toEqual(['A'])
    expect(ask.map(p => p.name)).toEqual(['B'])
    expect(blocked.map(p => p.name)).toEqual(['C'])
  })

  it('handles an empty batch', () => {
    expect(partitionRescale([])).toEqual({ auto: [], ask: [], blocked: [] })
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/utils/rescale.test.ts
```
Expected: FAIL — `Failed to resolve import "./rescale"`.

- [ ] **Step 3: Write the implementation**

Create `pypsa-gui/frontend/src/utils/rescale.ts`:

```ts
// Deciding what to do with a previewed impedance rescale.
//
// r/x/b are stored absolute and shown per-km. When a length changes, holding
// per-km constant means scaling all three by the length ratio — so the
// relative change is IDENTICAL for r, x and b, and equals the change in
// length. One number describes the whole line; there is nothing to compare
// field by field.
//
// Accepting a material rescale changes x, and DC OPF splits flows inversely
// proportional to x — so results move. That is why anything material is a
// question rather than an action.

export interface RescalePreview {
  name: string
  old_length: number
  new_length: number
  old: { r: number; x: number; b: number }
  new: { r: number; x: number; b: number }
  rel_change: number
  skipped_reason: string | null
}

/** Relative change at or below which the rescale is applied without asking. */
export const RESCALE_PROMPT_THRESHOLD = 0.05

export function partitionRescale(previews: RescalePreview[]): {
  auto: RescalePreview[]
  ask: RescalePreview[]
  blocked: RescalePreview[]
} {
  const auto: RescalePreview[] = []
  const ask: RescalePreview[] = []
  const blocked: RescalePreview[] = []
  for (const p of previews) {
    if (p.skipped_reason !== null) blocked.push(p)
    else if (p.rel_change <= RESCALE_PROMPT_THRESHOLD) auto.push(p)
    else ask.push(p)
  }
  return { auto, ask, blocked }
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/utils/rescale.test.ts
```
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git branch --show-current   # expect feature/local-app-impl
git commit pypsa-gui/frontend/src/utils/rescale.ts \
           pypsa-gui/frontend/src/utils/rescale.test.ts \
  -m "feat(gui): partition previewed impedance rescales by materiality

Pure and separately tested, including the exact-threshold boundary. No
consumers yet.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The rescale dialog and its wiring

**Files:**
- Create: `pypsa-gui/frontend/src/components/RescaleDialog.tsx`
- Test: `pypsa-gui/frontend/src/components/RescaleDialog.test.tsx`
- Modify: `pypsa-gui/frontend/src/api/network.ts`, `pypsa-gui/frontend/src/pages/MapCanvas.tsx`

**Interfaces:**
- Consumes: `partitionRescale`, `RescalePreview` (Task 4); `Dialog` from `../components/Dialog`; the `"rescale"` field on both endpoints (Task 3).
- Produces: nothing downstream.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/components/RescaleDialog.test.tsx`:

```tsx
// The dialog must state the consequence, not just ask a yes/no question:
// accepting changes x, and DC OPF splits flows inversely proportional to x.
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RescaleDialog from './RescaleDialog'
import type { RescalePreview } from '../utils/rescale'

const p: RescalePreview = {
  name: 'L1',
  old_length: 1.78, new_length: 476.3,
  old: { r: 3.0, x: 17.5, b: 0.00015 },
  new: { r: 802.7, x: 4682.6, b: 0.04013 },
  rel_change: 266.6,
  skipped_reason: null,
}

describe('RescaleDialog', () => {
  it('renders nothing when there is nothing to ask about', () => {
    const { container } = render(
      <RescaleDialog previews={[]} blocked={[]} onAccept={() => {}} onDecline={() => {}} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('names the line and shows both lengths', () => {
    render(<RescaleDialog previews={[p]} blocked={[]} onAccept={() => {}} onDecline={() => {}} />)
    expect(screen.getByText('L1')).toBeDefined()
    expect(screen.getByText(/1\.78/)).toBeDefined()
    expect(screen.getByText(/476\.3/)).toBeDefined()
  })

  it('states that accepting moves solver results', () => {
    render(<RescaleDialog previews={[p]} blocked={[]} onAccept={() => {}} onDecline={() => {}} />)
    expect(screen.getByText(/results will change/i)).toBeDefined()
  })

  it('reports lines it cannot rescale instead of hiding them', () => {
    const blocked: RescalePreview = { ...p, name: 'ZERO', skipped_reason: 'old_length<=0' }
    render(<RescaleDialog previews={[p]} blocked={[blocked]} onAccept={() => {}} onDecline={() => {}} />)
    expect(screen.getByText('ZERO')).toBeDefined()
    expect(screen.getByText(/had no length/i)).toBeDefined()
  })

  it('reports each choice exactly once', async () => {
    const onAccept = vi.fn(); const onDecline = vi.fn()
    render(<RescaleDialog previews={[p]} blocked={[]} onAccept={onAccept} onDecline={onDecline} />)
    await userEvent.click(screen.getByRole('button', { name: /update/i }))
    expect(onAccept).toHaveBeenCalledTimes(1)
    expect(onDecline).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/components/RescaleDialog.test.tsx
```
Expected: FAIL — `Failed to resolve import "./RescaleDialog"`.

- [ ] **Step 3: Write the component**

Create `pypsa-gui/frontend/src/components/RescaleDialog.tsx`:

```tsx
import { Dialog } from './Dialog'
import type { RescalePreview } from '../utils/rescale'

// Asks whether a length change should carry its impedance with it.
//
// Presentational: no store, no Leaflet, no data fetching, so the copy the user
// reads is under test. The caller owns the decision and the API calls.
//
// It shows numbers rather than a bare yes/no because the choice is not
// obvious: keeping the old absolute values preserves solver results but leaves
// the per-km impedance physically wrong; taking the new ones fixes the physics
// and moves the results.
interface RescaleDialogProps {
  previews: RescalePreview[]
  blocked: RescalePreview[]
  onAccept: () => void
  onDecline: () => void
}

const BLOCKED_REASON: Record<string, string> = {
  'old_length<=0': 'had no length, so its per-km value is unknown',
  'new_length<=0': 'would end up with zero length',
}

export default function RescaleDialog({ previews, blocked, onAccept, onDecline }: RescaleDialogProps) {
  if (previews.length === 0 && blocked.length === 0) return null

  const perKm = (v: number, len: number) => (len > 0 ? (v / len) : NaN)
  const fmt = (v: number) => (Number.isFinite(v) ? v.toPrecision(3) : '—')

  return (
    <Dialog
      open
      onClose={onDecline}
      title="Line lengths changed"
      panelClassName="bg-bg rounded-xl shadow-2xl w-[620px] max-w-[95vw] overflow-hidden"
    >
      <div className="p-4 text-xs text-text">
        {previews.length > 0 && (
          <>
            <p className="text-muted leading-relaxed">
              These lines are now a different length. Their resistance, reactance and
              susceptance are stored as absolute values, so unless they are rescaled the
              per-km figures shown in the properties panel change instead.
            </p>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-[11px] font-mono">
                <thead className="text-muted">
                  <tr>
                    <th className="text-left py-1">Line</th>
                    <th className="text-right">Length (km)</th>
                    <th className="text-right">r (Ω/km)</th>
                    <th className="text-right">x (Ω/km)</th>
                  </tr>
                </thead>
                <tbody>
                  {previews.map(p => (
                    <tr key={p.name} className="border-t border-border">
                      <td className="py-1 font-sans font-medium">{p.name}</td>
                      <td className="text-right">{fmt(p.old_length)} → {fmt(p.new_length)}</td>
                      <td className="text-right">
                        {fmt(perKm(p.old.r, p.old_length))} → {fmt(perKm(p.new.r, p.new_length))}
                      </td>
                      <td className="text-right">
                        {fmt(perKm(p.old.x, p.old_length))} → {fmt(perKm(p.new.x, p.new_length))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-[11px] text-muted">
              Updating keeps the per-km values you see above and changes the stored
              absolute values. It changes <span className="font-mono">x</span>, and power
              flow splits inversely with <span className="font-mono">x</span> —{' '}
              <span className="text-text font-medium">your results will change</span>. Undo
              reverses it.
            </p>
          </>
        )}

        {blocked.length > 0 && (
          <p className="mt-3 text-[11px] text-muted">
            Not rescaled:{' '}
            {blocked.map((b, i) => (
              <span key={b.name}>
                {i > 0 && ', '}
                <span className="font-mono text-text">{b.name}</span>{' '}
                {BLOCKED_REASON[b.skipped_reason ?? ''] ?? 'could not be rescaled'}
              </span>
            ))}
            .
          </p>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onDecline}
            className="px-3 py-1.5 rounded-md border border-border text-xs hover:bg-border/30 transition-colors"
          >Keep current values</button>
          <button
            type="button"
            onClick={onAccept}
            disabled={previews.length === 0}
            className="px-3 py-1.5 rounded-md bg-accent text-white text-xs font-medium hover:opacity-90 transition-opacity disabled:opacity-40"
          >Update {previews.length} line{previews.length === 1 ? '' : 's'}</button>
        </div>
      </div>
    </Dialog>
  )
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/components/RescaleDialog.test.tsx
```
Expected: PASS, 5 tests.

- [ ] **Step 5: Add the API call**

In `pypsa-gui/frontend/src/api/network.ts`, beside `recalculateLineLengths`:

```ts
  rescaleImpedances: (lines: Array<{ name: string; r: number; x: number; b: number }>) =>
    client.post<{ updated: number; skipped: Array<{ name: string; reason: string }> }>(
      '/network/lines/rescale_impedances', { lines },
    ).then(r => r.data),
```

and widen `recalculateLineLengths`'s response type to include `rescale: RescalePreview[]`.

- [ ] **Step 6: Wire it into MapCanvas**

Add state and a handler beside the existing mutations:

```tsx
  // Previewed impedance rescales awaiting a decision. Accumulated rather than
  // handled per-event: during click-to-place, prompting on every click would
  // make the mode unusable, so the batch is drained once at the end.
  const [pendingRescale, setPendingRescale] = useState<RescalePreview[]>([])

  const applyRescale = useCallback(async (previews: RescalePreview[]) => {
    if (previews.length === 0) return
    await networkApi.rescaleImpedances(previews.map(p => ({
      name: p.name, r: p.new.r, x: p.new.x, b: p.new.b,
    })))
    qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'lines') })
  }, [qc])

  // Immaterial changes are applied straight away; material ones queue for the
  // dialog. Blocked ones are surfaced by the dialog, never silently dropped.
  const ingestRescale = useCallback((previews: RescalePreview[] | undefined) => {
    if (!previews || previews.length === 0) return
    const { auto, ask, blocked } = partitionRescale(previews)
    void applyRescale(auto)
    if (ask.length || blocked.length) setPendingRescale(prev => [...prev, ...ask, ...blocked])
  }, [applyRescale])
```

Call `ingestRescale` from both mutation successes — `updateBusPosMut.onSuccess` (from `data.rescale`) and `recalcMut.onSuccess` (from `r.rescale`). **While `placing` is true, hold the batch**: keep collecting into `pendingRescale` but do not open the dialog; open it in the existing completion effect once placement ends.

Render beside `UnplacedBusesPanel`, inside the same `placementUiAllowed` guard is **not** required — a modal dialog is not floating map furniture — but it must not open while placement is still running:

```tsx
      {!placing && (
        <RescaleDialog
          previews={pendingRescale.filter(p => p.skipped_reason === null)}
          blocked={pendingRescale.filter(p => p.skipped_reason !== null)}
          onAccept={async () => {
            await applyRescale(pendingRescale.filter(p => p.skipped_reason === null))
            setPendingRescale([])
          }}
          onDecline={() => setPendingRescale([])}
        />
      )}
```

Also widen `Bus`-move handling: `updateBusPosMut`'s `mutationFn` must return the response body so `onSuccess` can read `rescale`.

- [ ] **Step 7: Type-check and run the suite**

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc -b --noEmit; echo "exit=$?"
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run; echo "exit=$?"
```
Expected: `exit=0` from both.

- [ ] **Step 8: Verify by hand (spec success criteria 2-5)**

In the dev stack, on a **copy** of a project (never the user's real `3_nodes_system` — placement writes coordinates):

1. Drag a placed bus a few metres. Expected: no dialog; the line's per-km r/x in the properties panel are unchanged.
2. Place three unplaced buses at real coordinates. Expected: **one** dialog after the last placement, listing three lines.
3. Press "Keep current values". Expected: lengths are the new ones, r/x/b unchanged.
4. Repeat and press "Update". Expected: per-km r/x unchanged in the properties panel, absolute values scaled.

Report each as verified or unverified with what you observed.

- [ ] **Step 9: Commit**

```bash
git branch --show-current   # expect feature/local-app-impl
git commit pypsa-gui/frontend/src/components/RescaleDialog.tsx \
           pypsa-gui/frontend/src/components/RescaleDialog.test.tsx \
           pypsa-gui/frontend/src/api/network.ts \
           pypsa-gui/frontend/src/pages/MapCanvas.tsx \
  -m "feat(gui): ask before a length change rewrites line impedance

Immaterial changes apply silently; material ones show old vs new per-km and
say plainly that results will move. Batched during placement so the mode stays
usable.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The emissions catalog gap and its warning

**Files:**
- Modify: `pypsa-gui/backend/services/carrier_catalog.py`, `pypsa-gui/frontend/src/utils/carrierCatalog.ts`, `pypsa-gui/backend/services/validation_service.py`
- Test: `pypsa-gui/backend/tests/test_carrier_emissions.py` (create)

**Interfaces:**
- Produces: validation code `carrier_zero_co2`; `_looks_fossil(carrier: str) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_carrier_emissions.py`:

```python
"""
A zero CO2 result must never be silently caused by a missing emission factor.

A real project reported 0 tCO2 for a 300 MW gas plant. The number was correct:
its generator's carrier is `gas`, which was absent from the catalog, so
ensure_carrier created a bare row with co2_emissions=0.0 and the emissions
formula multiplied by it.

Nothing warned because both existing guards are CONDITIONAL — one on
co2_price > 0, the other on a global constraint existing. That network had
neither, which is why this warning is ungated.
"""
from __future__ import annotations

from services.carrier_catalog import CARRIER_CATALOG, ensure_carrier
from services.validation_service import _looks_fossil


def _codes(client) -> list[str]:
    r = client.get("/api/validation")
    assert r.status_code == 200, r.text
    return [i["code"] for i in r.json().get("issues", [])]


def test_gas_is_in_the_catalog_with_the_same_factor_as_CCGT(client):
    # Justified by this repo's own entries rather than invented: CCGT and OCGT
    # burn natural gas and are already 0.187.
    assert CARRIER_CATALOG["gas"]["co2_emissions"] == CARRIER_CATALOG["CCGT"]["co2_emissions"]
    assert CARRIER_CATALOG["diesel"]["co2_emissions"] == CARRIER_CATALOG["oil"]["co2_emissions"]


def test_ensure_carrier_never_repairs_an_existing_row(client):
    # This is why the fix must be OFFERED rather than left to the catalog.
    import pypsa
    n = pypsa.Network()
    n.add("Carrier", "gas", nice_name="gas", color="", co2_emissions=0.0)
    ensure_carrier(n, "gas")
    assert float(n.carriers.at["gas", "co2_emissions"]) == 0.0


def test_looks_fossil_excludes_biogas():
    # `biogas` contains `gas` but its CO2 is biogenic and conventionally zero.
    # A false positive here teaches users to ignore the warning.
    assert _looks_fossil("gas") is True
    assert _looks_fossil("CCGT") is True
    assert _looks_fossil("lignite") is True
    assert _looks_fossil("diesel") is True
    assert _looks_fossil("biogas") is False
    assert _looks_fossil("solar") is False
    assert _looks_fossil("onwind") is False


def test_warns_for_a_fossil_carrier_with_no_intensity(client):
    client.post("/api/network/buses", json={"name": "B1", "v_nom": 380.0, "x": 6.96, "y": 50.9})
    client.post("/api/network/generators", json={
        "name": "G1", "bus": "B1", "carrier": "gas", "p_nom": 300.0, "efficiency": 0.45,
    })
    # Zero the intensity the catalog would now supply, reproducing the state a
    # project created before this fix is in.
    client.put("/api/network/carriers/gas", json={"name": "gas", "co2_emissions": 0.0})
    assert "carrier_zero_co2" in _codes(client)


def test_does_not_warn_for_renewables(client):
    client.post("/api/network/buses", json={"name": "B1", "v_nom": 380.0, "x": 6.96, "y": 50.9})
    client.post("/api/network/generators", json={
        "name": "G1", "bus": "B1", "carrier": "solar", "p_nom": 300.0,
    })
    assert "carrier_zero_co2" not in _codes(client)


def test_does_not_warn_once_an_intensity_is_set(client):
    client.post("/api/network/buses", json={"name": "B1", "v_nom": 380.0, "x": 6.96, "y": 50.9})
    client.post("/api/network/generators", json={
        "name": "G1", "bus": "B1", "carrier": "gas", "p_nom": 300.0, "efficiency": 0.45,
    })
    client.put("/api/network/carriers/gas", json={"name": "gas", "co2_emissions": 0.187})
    assert "carrier_zero_co2" not in _codes(client)
```

Check the validation endpoint's real path and response shape before running — adjust `_codes` to match if `/api/validation` differs.

- [ ] **Step 2: Run the test to verify it fails**

```bash
pixi run -e test python -m pytest pypsa-gui/backend/tests/test_carrier_emissions.py -q; echo "exit=$?"
```
Expected: `exit=1` — `KeyError: 'gas'` and `ImportError` on `_looks_fossil`.

- [ ] **Step 3: Add the two catalog entries, in both files**

In `pypsa-gui/backend/services/carrier_catalog.py`, in the thermal block:

```python
    # Fuel-level carriers. `gas` is an ordinary PyPSA-Eur carrier name, and its
    # absence is what made a real project report 0 tCO2 for a 300 MW gas plant:
    # ensure_carrier fell back to co2_emissions=0.0 and nothing warned. The
    # factors match this file's own CCGT/OCGT (natural gas) and oil, rather
    # than being invented — a wrong factor is worse than a missing one because
    # it looks authoritative.
    "gas":            {"nice_name": "Natural Gas", "color": "#e0986c", "co2_emissions": 0.187},
    "diesel":         {"nice_name": "Diesel",      "color": "#3b3b3b", "co2_emissions": 0.267},
```

Add the matching entries to `CATALOG_THERMAL` in `pypsa-gui/frontend/src/utils/carrierCatalog.ts`:

```ts
  { name: 'gas',    nice_name: 'Natural Gas', color: '#e0986c', co2_emissions: 0.187 },
  { name: 'diesel', nice_name: 'Diesel',      color: '#3b3b3b', co2_emissions: 0.267 },
```

- [ ] **Step 4: Add the ungated warning**

In `pypsa-gui/backend/services/validation_service.py`:

```python
# Carrier names that imply combustion of fossil carbon. Substring, lower-cased,
# because carrier naming is free-form.
#
# `biogas` is excluded deliberately: it contains `gas`, but its CO2 is biogenic
# and conventionally counted as zero, so warning on it would be a false
# positive — and a warning that cries wolf is one users learn to dismiss.
_FOSSIL_KEYWORDS = ("gas", "coal", "lignite", "oil", "diesel", "peat", "waste",
                    "ccgt", "ocgt", "methane")
_FOSSIL_EXCLUDE = ("biogas", "biomethane")


def _looks_fossil(carrier: str) -> bool:
    c = (carrier or "").lower()
    if any(x in c for x in _FOSSIL_EXCLUDE):
        return False
    return any(k in c for k in _FOSSIL_KEYWORDS)
```

and, in the network-level checks, a check that is **not** gated on `co2_price` or on a global constraint:

```python
def _check_carrier_emissions(n) -> list[Issue]:
    """
    A fossil-looking carrier with no CO2 intensity makes every emissions figure
    zero, silently. Ungated on purpose: the two pre-existing guards fire only
    when co2_price > 0 or a global constraint exists, and a real project had
    neither — which is exactly how a 300 MW gas plant reported 0 tCO2.
    """
    out: list[Issue] = []
    if n.generators.empty or n.carriers.empty:
        return out
    if "co2_emissions" not in n.carriers.columns:
        used = sorted({str(c) for c in n.generators["carrier"].unique() if _looks_fossil(str(c))})
        intensities = {c: 0.0 for c in used}
    else:
        used = sorted({str(c) for c in n.generators["carrier"].unique() if _looks_fossil(str(c))})
        intensities = {
            c: float(n.carriers.at[c, "co2_emissions"]) if c in n.carriers.index else 0.0
            for c in used
        }
    for carrier, value in intensities.items():
        if value > 0:
            continue
        suggested = CARRIER_CATALOG.get(carrier, {}).get("co2_emissions", 0.0)
        hint = (f" The catalog value for '{carrier}' is {suggested} tCO2/MWh."
                if suggested else "")
        out.append(_warn("carrier_zero_co2", "Carrier", carrier,
            f"Carrier '{carrier}' looks like a fossil fuel but has co2_emissions = 0, "
            f"so every emissions figure for it is zero.{hint}"))
    return out
```

Call it from the network-level check list, and import `CARRIER_CATALOG`.

- [ ] **Step 5: Run the tests**

```bash
pixi run -e test python -m pytest pypsa-gui/backend/tests/test_carrier_emissions.py -q; echo "exit=$?"
```
Expected: `exit=0`.

- [ ] **Step 6: Run the full backend suite**

```bash
pixi run gui-tests; echo "exit=$?"
```
Expected: `exit=0`. A new warning code appearing in validation output is the plausible breakage — an existing test may assert an exact issue list. Read it before changing it.

- [ ] **Step 7: Commit**

```bash
git branch --show-current   # expect feature/local-app-impl
git commit pypsa-gui/backend/services/carrier_catalog.py \
           pypsa-gui/backend/services/validation_service.py \
           pypsa-gui/frontend/src/utils/carrierCatalog.ts \
           pypsa-gui/backend/tests/test_carrier_emissions.py \
  -m "fix(gui): warn when a fossil carrier has no CO2 intensity

A 300 MW gas plant reported 0 tCO2 because `gas` was missing from the catalog,
so its carrier row was created with co2_emissions=0. Both existing guards are
conditional — on co2_price and on a global constraint — and that project had
neither. This one is ungated. biogas is excluded from fossil detection.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: A zero that states its reason, and a one-click fix

**Files:**
- Modify: `pypsa-gui/frontend/src/pages/results/Emissions.tsx:121`
- Modify: the issues surface that renders validation warnings (`pypsa-gui/frontend/src/pages/IssuesPanel.tsx`)

**Interfaces:**
- Consumes: `networkApi.getCarriers`, `networkApi.updateCarrier` (both already exist in `api/network.ts`), and the `carrier_zero_co2` issue code (Task 6).

- [ ] **Step 1: Explain the zero in the KPI**

In `Emissions.tsx`, the total is rendered at `:121`. When it is zero **and** no carrier in the network has `co2_emissions > 0`, replace the bare figure's hint with the reason. Fetch carriers via the existing query and compute:

```tsx
  // A bare "0 tCO2" cannot distinguish "this system is clean" from "nobody
  // told me what this fuel emits", and those call for opposite actions.
  const anyIntensity = (carriers as Carrier[]).some(c => (c.co2_emissions ?? 0) > 0)
  const zeroUnexplained = view.total_tCO2 === 0 && !anyIntensity
```

and pass a `hint` on the KPI when `zeroUnexplained`:

```
No carrier in this network has a CO₂ intensity, so every emissions figure is
zero. Set one under the Carrier tab, or use the fix offered in Issues.
```

- [ ] **Step 2: Offer the fix from the issue**

In the issues surface, a `carrier_zero_co2` issue gains an action button reading `Set <carrier> to <value> tCO₂/MWh` when the message carries a catalog value, wired to:

```tsx
onClick={async () => {
  const current = (carriers as Carrier[]).find(c => c.name === carrierName)
  if (!current) return
  await networkApi.updateCarrier(carrierName, { ...current, co2_emissions: suggested })
  qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'carriers') })
  toast.success(`${carrierName} set to ${suggested} tCO₂/MWh`)
}}
```

Spread the cached carrier row first — the backend's `_update_component` does remove + add and would otherwise reset every omitted field, the same trap `updateBusPosMut` documents.

Rather than parsing the value out of the message text, have Task 6's issue carry it. If `Issue` has no spare field, add the value to the message in a stable form and parse it in one place with a test — do not scatter the parsing.

- [ ] **Step 3: Type-check and run the suite**

```bash
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc -b --noEmit; echo "exit=$?"
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run; echo "exit=$?"
```
Expected: `exit=0` from both.

- [ ] **Step 4: Verify by hand (spec success criteria 7-8, 12)**

Against a **copy** of `3_nodes_system`: open Issues. Expected: a `carrier_zero_co2` warning naming `gas`. Open Results → Emissions. Expected: the total reads 0 with the explanation, not a bare 0. Press the offered fix, re-run the solve, and confirm the total is now non-zero.

- [ ] **Step 5: Commit**

```bash
git branch --show-current   # expect feature/local-app-impl
git commit pypsa-gui/frontend/src/pages/results/Emissions.tsx \
           pypsa-gui/frontend/src/pages/IssuesPanel.tsx \
  -m "feat(gui): a zero-emissions result says why, and offers the fix

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Rebuild the packaged app

**Files:** none modified.

- [ ] **Step 1: Quit any running copy**

```bash
osascript -e 'quit app "PyPSA Studio"' 2>/dev/null; pgrep -f "PyPSA Studio" || echo "not running"
```
Replacing the bundle under a live process unlinks files it has not yet opened; `pyproj` reads `proj.db` lazily during save, so the app runs fine and then fails exactly when it matters.

- [ ] **Step 2: Build**

```bash
bash pypsa-gui/build-macos.sh
```
The credential gate must print `clean: no secrets or user data found`. If it refuses, stop and report — never delete files from the bundle to get past it.

- [ ] **Step 3: Install and confirm the new code shipped**

```bash
rm -rf "/Applications/PyPSA Studio.app"
cp -R "pypsa-gui/dist-app/PyPSA Studio.app" /Applications/
grep -rlo "results will change" "/Applications/PyPSA Studio.app/Contents/Frameworks/frontend/dist/assets/"
```
The last command must find the rescale dialog's copy — that string exists only in this change, so finding it proves the rebuild took.

- [ ] **Step 4: Confirm the tree is clean**

```bash
git status --porcelain
```
Expected: empty.

- [ ] **Step 5: Refresh the hand-over DMG**

```bash
cp pypsa-gui/dist-app/PyPSA-Studio.dmg ~/Desktop/PyPSA-Studio.dmg
ls -lh ~/Desktop/PyPSA-Studio.dmg
```

---

## Self-review

**Spec coverage.** A1/A2/A3 → Task 1. A4 → Task 2 (all four categories resolved). B1 → Task 3 (length written, impedance previewed). B2 → Task 3 Step 3. B3 → Task 3 Steps 4-5. B4 → Task 4. B5 → Task 5 Step 6 (batching). C1 → Task 6 Step 3. C2 → Task 7 Step 1. C3 → Task 6 Step 4. C4 → Task 7 Step 2. Success criteria 1 → Task 2 Step 5; 2-5 → Task 5 Step 8; 6 → Task 3 Step 1; 7-8, 12 → Task 7 Step 4; 9-11 → Task 6 Step 1.

**Known gap to close during implementation.** Success criteria 9 ("declining does not re-prompt within the session") and 11 (`biogas`) are covered by tests, but 9's session behaviour is not implemented by any step — the issues surface re-renders the warning every time validation runs. Task 7 must add per-session dismissal, or the criterion must be dropped. Flag it to the controller rather than silently skipping it.

**Placeholder scan.** No TBD/TODO. Two steps deliberately say "check the real shape before running" — Task 6 Step 1 (the validation endpoint's path) and Task 2 Step 1 (whether `Load` has a `carrier` field) — because both are facts the implementer must confirm rather than assume; each says exactly what to do with either answer.

**Type consistency.** `RescalePreview` is defined in Task 4 and used with those exact field names in Tasks 3 (JSON), 5 and the dialog. `BadgeDef`/`uniformBadge` from Task 1 match Task 2's use. `_impedance_preview` returns the same keys the frontend reads. `rescaleImpedances` takes `{name, r, x, b}`, matching `ImpedanceRescaleEntry`.
