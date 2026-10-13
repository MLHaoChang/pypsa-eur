// Click-to-place queue sequencing — a separate responsibility from geo.ts's
// coordinate predicate (coordinates vs. placement-queue order), so it lives
// in its own file rather than being folded in there.
//
// Both functions are pure functions of `unplaced` (the bus names still
// awaiting a location, in network order) and `skipped` (the set of names the
// user has deferred via "Skip" during the CURRENT placement session). Extracted
// out of MapCanvas.tsx so this state machine — which was deliberately
// redesigned away from the original numeric-index plan (see the comment this
// replaces in MapCanvas.tsx) — has direct test coverage instead of only being
// reachable through a component that needs react-query, zustand, a results
// provider and a sized Leaflet container to mount.
//
// See docs/superpowers/specs/2026-07-30-unplaced-buses-map-design.md.

/**
 * The bus the placement strip should target next: the first name in
 * `unplaced` that hasn't been skipped this session, or — once every
 * remaining bus has been skipped over — the head of the queue again. That
 * fallback means "skip" only ever REORDERS the queue, it never drops a bus:
 * once nothing is left unskipped, the picker cycles back to `unplaced[0]`
 * rather than getting stuck.
 *
 * Returns `undefined` when there is nothing left to place.
 */
export function nextBusToPlace(unplaced: string[], skipped: Set<string>): string | undefined {
  return unplaced.find(n => !skipped.has(n)) ?? unplaced[0]
}

/**
 * Whether the Skip action should be enabled. Disabled once every remaining
 * unplaced bus has already been skipped this session — skipping further
 * would just cycle the same bus back to the front with no new information,
 * so the button goes inert instead of looking live and doing nothing.
 *
 * `false` for an empty `unplaced` array (there's nothing left to skip) —
 * never observed in practice, since the placement strip that reads this only
 * renders while a `placingBus` exists, which requires a non-empty `unplaced`.
 */
export function canSkip(unplaced: string[], skipped: Set<string>): boolean {
  return !unplaced.every(n => skipped.has(n))
}

// ── "Place all buses around this point" (plan M4) ────────────────────────────
// A template campus opens with every bus unplaced; one click on the grid
// connection lays the whole campus out: the FIRST bus at the click, the rest
// on a ring around it with neighbours `spacingM` apart (a chord, so the ring
// grows with the count rather than crowding). Deterministic, pure, in the
// map's [lat, lng]; the caller PUTs each bus's x/y.

export const PLACE_ALL_SPACING_M = 200
const EARTH_RADIUS_M = 6378137
const DEG = Math.PI / 180

/** `[lat, lng]` per bus name, first at `center`, the others evenly around it. */
export function layoutAround(center: readonly [number, number], names: readonly string[], spacingM: number = PLACE_ALL_SPACING_M): Record<string, [number, number]> {
  const out: Record<string, [number, number]> = {}
  if (names.length === 0) return out
  out[names[0]] = [center[0], center[1]]
  const ring = names.length - 1
  if (ring === 0) return out
  // Chord between neighbours = spacing: r = s / (2 sin(π/n)); a lone second
  // bus or a pair sits at the spacing itself.
  const radius = ring < 3 ? spacingM : spacingM / (2 * Math.sin(Math.PI / ring))
  const perLat = EARTH_RADIUS_M * DEG
  const perLng = perLat * Math.cos(center[0] * DEG)
  for (let i = 0; i < ring; i++) {
    const angle = (2 * Math.PI * i) / ring
    const east = radius * Math.sin(angle), north = radius * Math.cos(angle)
    out[names[i + 1]] = [center[0] + north / perLat, center[1] + east / perLng]
  }
  return out
}
