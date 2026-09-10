/**
 * Width arithmetic for the docked comparison rail in Results.
 *
 * ONE RULE, and it is the whole design:
 *
 *   The WRITE path never consults available space.
 *   The RENDER path is the only thing that does.
 *
 * `compareRailWidth` (store, persisted) is the width the user asked for. A
 * drag records exactly what the user dragged to, floored at `RAIL_MIN_W`, and
 * nothing else ever writes it. What appears on screen is
 * `renderedRailWidth(desired, wrapW)` — derived per render, never stored, and
 * therefore incapable of corrupting anything.
 *
 * Why the write path is forbidden from knowing the ceiling: four separate
 * defects, each individually fixed and each of which came back somewhere else,
 * all had the same root — the write decision consulting available space.
 *
 *   1. The clamp WROTE the constrained value back through a persisting setter,
 *      so opening the assistant destroyed a dragged 700 on a layout event.
 *   2. Fixed by rendering the constraint instead — but a drag pinned against
 *      the ceiling still recorded the ceiling, so the constraint was written
 *      by a gesture rather than by a layout event.
 *   3. Fixed by suppressing pinned writes — but the guard read the LIVE stored
 *      width, which the same gesture was rewriting, so one pixel of press
 *      drift disarmed it and the preference ratcheted down.
 *   4. Fixed by deciding once per gesture from a mousedown snapshot — but the
 *      ceiling in that snapshot goes stale when the assistant dock opens
 *      mid-drag (`applyUiNavigate` does exactly that, on an agent turn, with
 *      no idea a mouse button is held), so the release computed against a
 *      wrapper that no longer existed.
 *
 * Every one of those fixes was locally correct. The class survived because the
 * question "how much room is there" was being asked at write time at all.
 * It is not asked here any more.
 *
 * The rule is about the CEILING specifically, not about "the write path knows
 * nothing". `desiredFromDrag` does take the stored width as it stood at
 * mousedown, and that is sound: the drag is the only writer, so that snapshot
 * cannot be invalidated by anything else mid-gesture. The ceiling could be,
 * and was.
 */

/**
 * Minimum px kept for BOTH the live Results pane and the comparison rail.
 * The single definition — `uiStore`'s `setCompareRailWidth` floor and its
 * stored-value validation both import this.
 */
export const RAIL_MIN_W = 360

/**
 * The widest the rail may be while the live pane keeps its floor.
 *
 * RENDER-SIDE ONLY. Do not use this to decide what to store. The outer `max`
 * matters: below a `2 × RAIL_MIN_W` wrapper, `wrapW - RAIL_MIN_W` is smaller
 * than the rail's own minimum, and returning it would let a caller compute a
 * rail narrower than the floor.
 */
export function railCeiling(wrapW: number): number {
  return Math.max(RAIL_MIN_W, wrapW - RAIL_MIN_W)
}

/**
 * What the rail actually renders at: the desired width, constrained to what
 * currently fits. Derived state — never stored, so it cannot go stale and
 * cannot lose anything. Recomputed from a live measurement on every render,
 * which is also why a layout event landing mid-gesture needs no special
 * handling: the next render simply constrains against the new width.
 */
export function renderedRailWidth(desired: number, wrapW: number): number {
  return Math.max(RAIL_MIN_W, Math.min(desired, railCeiling(wrapW)))
}

/**
 * The width a drag records.
 *
 * THE FORBIDDEN INPUT IS THE CEILING, NOT THE STORED PREFERENCE. Nothing here
 * may ask how much room there is — that is what went stale in round 4 and what
 * self-referenced in round 3. `storedAtStart` is safe to consult for a reason
 * the ceiling never was: a drag is the ONLY writer of the stored width, so a
 * value snapshotted at mousedown cannot be changed by anyone else before the
 * release. It cannot go stale; the ceiling could, on any layout event.
 *
 * `startW` is the ON-SCREEN width at mousedown (already constrained), so
 * `startW + delta` is what the pointer is asking for and is what the preview
 * follows pixel-for-pixel. But it must not be recorded blindly: whenever the
 * rail is space-constrained, `startW` is BELOW the stored width, so a small
 * leftward nudge computes a value below what the user already had.
 *
 * Concretely, and this is the whole reason for the `delta >= 0` branch:
 * wrapper 820, ceiling 460, stored 700, rendered 460. Pull 1px left — the
 * natural "make it wider" nudge, and well inside trackpad click-drag jitter —
 * and `startW + delta` is 461. Recording that loses 239px of the user's
 * preference, in the OPPOSITE direction from what the gesture asked, with the
 * rail rendering 460 before and after: no visible change at all. The loss is
 * `stored - (startW + delta)`, i.e. LARGEST for the smallest gestures, and it
 * only reaches zero once the drag exceeds the constraint gap.
 *
 * So: a gesture that asks for the rail to be at least as wide as it started
 * may never record less than the user already had. `>= 0` rather than `> 0`
 * because a drag that returns to exactly its origin asks for no change, and
 * must not be a write that silently drops to the constrained width either.
 *
 * A gesture that asks for LESS is recorded as asked — that is a real shrink,
 * it is visible on screen, and there is nothing to protect.
 *
 * On an unconstrained rail `startW === storedAtStart`, so `max` is an
 * identity and this reduces to `startW + delta` in every case.
 */
export function desiredFromDrag(
  storedAtStart: number,
  startW: number,
  delta: number,
): number {
  const askedFor = startW + delta
  const next = delta >= 0 ? Math.max(storedAtStart, askedFor) : askedFor
  return Math.max(RAIL_MIN_W, next)
}
