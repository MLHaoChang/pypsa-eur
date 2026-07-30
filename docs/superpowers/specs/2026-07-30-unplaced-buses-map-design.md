# Unplaced buses on the satellite / hybrid map — design

**Date:** 2026-07-30
**Branch:** `feature/local-app-impl`
**Reported as:** "the map view does not support satellite or hybrid"

## Goal

Satellite and Hybrid show your network, or say plainly why they can't — and
nothing anywhere measures a distance to Null Island.

## Why now

The reported symptom was that the tool has no satellite or hybrid map. It has
both, and has since `9ac144ea`:

- [`MapModeSwitcher.tsx:6-10`](../../../pypsa-gui/frontend/src/components/MapModeSwitcher.tsx) — the Blank / Satellite / Hybrid toggle
- [`MapCanvas.tsx:181-191`](../../../pypsa-gui/frontend/src/pages/MapCanvas.tsx) — Esri World Imagery plus the two transparent
  Transportation + Places overlays that make Hybrid

Both ship in the installed `.app` (`arcgisonline` and `Satellite` are present
in `Contents/Frameworks/frontend/dist/assets/spa-*.js`), and the tiles are
reachable — a direct fetch of `.../World_Imagery/MapServer/tile/5/10/16`
returns HTTP 200, 16 KB.

**The actual cause is that nothing knows `(0, 0)` means "unset".** PyPSA's
`Bus.x` and `Bus.y` default to `0.0`, verified directly:

```
>>> n = pypsa.Network(); n.add('Bus','B1'); n.buses.loc['B1',['x','y']].to_dict()
{'x': 0.0, 'y': 0.0}
```

Because every value is the default, netCDF omits the columns entirely — the
reporter's `3_nodes_system/network.nc` has no `buses_x` / `buses_y` variables
at all.

Four consequences follow from that single gap:

| Where | Today | Should be |
|---|---|---|
| [`busLatLng`](../../../pypsa-gui/frontend/src/pages/MapCanvas.tsx) (`MapCanvas.tsx:209-215`) | `(0,0)` is a valid position | unset → bus hidden |
| `FitToNetwork` (`MapCanvas.tsx:220-235`) | zero-extent bounds → zoom 19 over open ocean | no placed buses → keep the central-Europe default |
| the warning toast (`MapCanvas.tsx:585-591`) | counts 0 missing, stays silent | persistent, states the problem |
| [`_bus_coord`](../../../pypsa-gui/backend/routers/network.py) (`network.py:288-299`) | haversine measures **to** Null Island | skip unplaced buses |

So the user switches to Satellite, the map flies to 0°N 0°E at maximum zoom
over the Gulf of Guinea — where Esri has no imagery — and shows a featureless
panel with no warning. That is indistinguishable from an unimplemented feature.

The fourth row is a live data-corruption path, not cosmetics. Place two buses
of three, press "recalculate line lengths", and every line touching the third
gets a length measured to the Gulf of Guinea written into the model as fact.

This bug class has bitten this codebase before. `network.py:386-394` documents
the earlier instance verbatim:

> `coord_changed = (old_x != 0.0)` fires for every non-origin bus, rewriting
> every connected line's length to the haversine distance to (0, 0). Symptom:
> fleet of broken line lengths after editing a single bus attribute.

That was fixed at the request layer with `exclude_unset`. This spec closes the
remaining path — coordinates that genuinely are `(0, 0)`.

## Constraints

**Nothing else reads bus coordinates.** Audited: the only backend consumers of
`n.buses` `x`/`y` are `_bus_coord` (`network.py:288`) and the bus-update
coordinate-change check (`network.py:402-403`). `io.py:159`'s `x` is line
*reactance* in the MATPOWER export, not a coordinate.

**Setting a coordinate already works and is not in scope to rebuild.**
[`CoordPairInput`](../../../pypsa-gui/frontend/src/layout/properties/cardKit.tsx)
(`cardKit.tsx:459`) accepts a pasted `lat, lng` pair per bus, and map drags
write back through `updateBusPosMut`. The gap is detection and discovery.

**The schematic canvas is unaffected.** `TopologyCanvas` positions come from
`layout.json` / per-project localStorage, not `x`/`y`, which is why Blank mode
looks correct while the map does not.

**From `CLAUDE.md`.** TDD: RED before GREEN, mutation-check the guard. The
packaged macOS app must be rebuilt after any change that reaches it.

## Decisions

**D1. The test is `x == 0 and y == 0` — both, exactly.** A bus at `(0, 51.5)`
is Greenwich and stays valid; only the exact pair means "never set". In
JavaScript `-0.0 === 0`, so negative zero is covered without a special case.

**D2. Derived, not stored.** No `placed` column, no schema change, no
migration, nothing to keep in sync with `x`/`y`.

**D3. The backend guard lands first; the frontend is never ahead of it.** A map
that hides a bus while the backend still measures a line to it is worse than
today's behaviour. Ordering satisfies that invariant without forcing one large
commit: backend-only is a strict improvement on its own, frontend-only is not.

**D4. The predicate moves to `utils/geo.ts`.** `carriers.ts` exists precisely
because a shared predicate was copy-pasted into four files and drifted — two
copies null-guarded, two crashed. Same shape of risk, same remedy.

**D5. Persistent UI replaces the transient toast.** A message that appears once
and vanishes is a large part of why this went unnoticed.

**D6. "Recalculate line lengths" after placement is offered, never automatic.**
Rewriting `length` is a model edit and must stay an explicit user action.

**D7. Bulk import and geocoding are deferred.** The networks large enough to
need them — PyPSA-Eur exports — already ship coordinates and never hit this.

## What changes

### The rule

New `frontend/src/utils/geo.ts` with a colocated `geo.test.ts`. `busLatLng`
moves out of `MapCanvas.tsx` into it and gains one clause:

```ts
export function busLatLng(b: Pick<Bus, 'x' | 'y'>): [number, number] | null {
  const lat = Number(b.y)
  const lng = Number(b.x)
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null
  if (lat === 0 && lng === 0) return null      // PyPSA's never-set default (D1)
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null
  return [lat, lng]
}
```

Every call site in `MapCanvas.tsx` — ten calls across eight blocks (lines 225,
270, 585, 776-777, 816-817, 850-851, 905) — imports it, so markers, lines,
asset bubbles, `FitToNetwork` and the unplaced count are all corrected by one
change.

### The backend mirror

`_bus_coord` returns `None` for the default pair. Its callers already skip on
`None`, so the fix propagates without further edits:
`recalculate_line_lengths` counts unplaced buses as `skipped` (already in its
`{updated, skipped, total}` response and its success toast), line-creation
auto-fill at `network.py:523-533` leaves `length` untouched, and
`_recompute_lengths_for_bus` skips.

### The empty state

`FitToNetwork` returns early on zero points today, so with the predicate fixed
the map simply holds its `[50, 10]` zoom-5 default. Over that, a persistent
panel when no bus is placed:

> **No bus has a location yet**
> Satellite and Hybrid plot buses by longitude (x) and latitude (y). All 3
> buses are still at PyPSA's default 0, 0.
> **[ Place buses on the map ]** · or paste `lat, lng` into a bus's properties

When some are placed, it shrinks to a chip in the existing top-left map toolbar
(`z-[400]`, alongside the Ruler button): `3 of 12 buses unplaced`, click to
enter placement mode. The toast at `MapCanvas.tsx:585-591` is removed (D5).

### Placement mode

A `<ClickToPlace>` child of `MapContainer` using react-leaflet's
`useMapEvents({ click })`. The cursor becomes a crosshair and a strip reads
`Placing B2 — click the map (2 of 3) · Skip · Done`. Each click fires the
existing `updateBusPosMut`, then advances to the next unplaced bus. Escape
exits.

Reusing the existing mutation carries three properties for free:

- It already refuses a partial PUT when the bus row is absent from the query
  cache (`MapCanvas.tsx:609-623`), so placement cannot reset `v_nom`,
  `carrier`, `control`, `country` or `sub_network` to Pydantic defaults.
- Markers are already draggable, so correcting a misplaced bus needs no new
  code.
- Leaflet suppresses `click` after a drag — the guarantee `MapCanvas.tsx`
  already documents for markers — so panning to find a location cannot drop a
  bus by accident.

One new interaction: `FitToNetwork` fires the moment the first bus gains
coordinates, which would yank the map out from under the user mid-placement. It
takes a `suspended` prop, true while placing.

On completion, a "Recalculate line lengths" action is offered (D6).

## Success criteria

1. Opening a project whose buses are all at `(0,0)` in Satellite mode shows the
   central-Europe default view and the empty-state panel — never open ocean at
   zoom 19.
2. A bus at `(0, 51.5)` renders at Greenwich.
3. `recalculate_line_lengths` on a network with one placed and one unplaced bus
   returns `updated=0, skipped=1` and leaves `n.lines.length` unchanged.
4. Placement mode places all three buses of `3_nodes_system` by clicking, and
   the map then draws the lines between them.
5. Reverting the `x == 0 and y == 0` guard in the backend fails a test.

## Tests

Written before the implementation, per `CLAUDE.md`.

- `frontend/src/utils/geo.test.ts` — `(0,0)` unset; Greenwich `(0, 51.5)`
  placed; `-0.0`; `NaN`; out-of-range; ordinary coordinates.
- `frontend/src/components/UnplacedBusesPanel.test.tsx` — the empty state's
  copy and its two shapes (full panel vs. partial chip), mounted in jsdom.
- `backend/tests/test_line_lengths.py` — success criterion 3. This is the
  regression test for the corruption path, and the mutation target for
  criterion 5.

**Why there is no `MapCanvas` component test.** The empty state is extracted
into `UnplacedBusesPanel`, a unit with no Leaflet import, so the copy the user
reads is genuinely under test. The other half of the guarantee — that an
unplaced bus renders nothing — is covered by `geo.test.ts`, because every
marker, line and asset-bubble call site already does `if (!c) return null`; a
null coordinate *is* the non-render. Mounting `MapCanvas` itself would need
react-query, the zustand store, `CanvasResultsProvider` and a Leaflet container
with real dimensions, against no Leaflet-test precedent in this suite.

## Out of scope

- **CSV / bulk coordinate import and geocoding** (D7).
- **Additional basemaps** — Street, Terrain, Light/Dark are available from the
  same keyless Esri family for one URL line each, but they are cosmetic until
  the map can show a network.
- **Offline tiles** — every mode needs the internet and fails silently, which
  matters now that this is a desktop app. Separate piece of work; any caching
  route needs Esri's terms checked first.
- **Changing PyPSA's defaults.**

## Known limitations

**A bus cannot be positioned at exactly 0°N 0°E.** That is open ocean in the
Gulf of Guinea. The alternative is a persisted "is placed" flag on every bus
(D2), which costs a schema migration to serve a case that does not occur in a
power system. Stated here rather than discovered later.

**Each placement click has typically already written line lengths, before the
completion toast offers to.** `update_bus` (`network.py:427-435`, pre-existing
and out of scope here) auto-rewrites the length of every line connected to a
bus whenever that bus's coordinates change — so during click-to-place, each
click that lands next to an already-placed bus writes that connecting line's
length immediately, not just at the end. The completion toast's "Recalculate
line lengths" action (D6) is offered as though nothing has been written yet;
in practice it is a fleet-wide catch-up pass, not the first write. Not a
correctness bug — the auto-rewrite is scoped per-bus and skips unplaced
endpoints (via `_bus_coord`) the same as the fleet-wide recalculation — but
worth knowing before reading the toast copy as "nothing has happened yet."
