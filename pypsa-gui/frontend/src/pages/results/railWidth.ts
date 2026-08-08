/**
 * Width arithmetic for the docked comparison rail in Results.
 *
 * Extracted because the WRITE DECISION — "does this gesture record a new
 * desired width, and what is it" — is where three rounds of the same defect
 * kept landing. It previously existed only as a one-line `if` reading live
 * global state, inside a closure, inside a `useCallback`, inside a 900-line
 * component: unreachable by any test that was not a full DOM drag simulation,
 * which is why every version of it shipped looking obviously correct.
 *
 * The invariant these functions exist to protect:
 *
 *   `compareRailWidth` is the width the USER ASKED FOR. It is persisted. It
 *   must never be overwritten by a value that the available space chose.
 *
 * The rail still shrinks on screen when it does not fit — that is
 * `renderedRailWidth`, computed per render and never stored.
 */

/**
 * Minimum px kept for BOTH the live Results pane and the comparison rail.
 * `uiStore.setCompareRailWidth` enforces the same floor on the stored value.
 */
export const RAIL_MIN_W = 360

/**
 * The widest the rail may be while the live pane keeps its floor.
 *
 * The outer `max` matters: below a `2 × RAIL_MIN_W` wrapper, `wrapW -
 * RAIL_MIN_W` is smaller than the rail's own minimum, and returning it would
 * let callers compute a rail narrower than the floor. Every caller must use
 * THIS, not a bare subtraction — a caller that spelled the bound differently
 * is how the 660px-wrapper bug hid.
 */
export function railCeiling(wrapW: number): number {
  return Math.max(RAIL_MIN_W, wrapW - RAIL_MIN_W)
}

/**
 * What the rail actually renders at: the desired width, constrained to what
 * currently fits. Never stored.
 */
export function renderedRailWidth(desired: number, wrapW: number): number {
  return Math.max(RAIL_MIN_W, Math.min(desired, railCeiling(wrapW)))
}

/**
 * The width a completed drag should RECORD, or `null` for "record nothing".
 *
 * Called once per gesture, on mouseup, with the position the user released at
 * (unclamped) and the desired width as it stood when the gesture STARTED.
 * Both of those are deliberate:
 *
 *  * Once per gesture, because a drag is one decision. Deciding per mousemove
 *    meant an intermediate position could be recorded and then leave the
 *    guard disarmed for the rest of the gesture — one pixel of press drift
 *    wrote a sub-ceiling width, and every later move compared against THAT
 *    instead of the user's real preference. The gesture rendered identically
 *    at both ends while the stored value ratcheted down to the ceiling.
 *
 *  * `desiredAtDragStart`, not the live store value, because the live value is
 *    what this same gesture is in the middle of rewriting. A guard that reads
 *    it is comparing against its own output.
 *
 * Returns `null` when the release lands pinned against the ceiling while a
 * LARGER width is already stored: that is the space constraint deciding, not
 * the user, and recording it would replace their preference with whatever this
 * window happens to allow. Growing INTO the ceiling from a smaller stored
 * width is a real choice and IS recorded.
 */
export function nextDesiredRailWidth(
  desiredAtDragStart: number,
  wrapW: number,
  releasedAt: number,
): number | null {
  const ceiling = railCeiling(wrapW)
  const next = Math.max(RAIL_MIN_W, Math.min(ceiling, releasedAt))
  // Float equality is exact here: `Math.min` returns the ceiling OPERAND
  // bit-identically when it wins, so no arithmetic happens on this path.
  if (next === ceiling && desiredAtDragStart > ceiling) return null
  return next
}
