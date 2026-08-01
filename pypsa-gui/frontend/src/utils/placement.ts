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
