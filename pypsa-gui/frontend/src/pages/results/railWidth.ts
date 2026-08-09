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
 * The width a drag records: exactly where the user dragged to, floored.
 *
 * No ceiling parameter, deliberately — see the module comment. This is a
 * one-line function that exists to be a NAMED, greppable, table-tested place
 * where the absence of a ceiling is visible, because every previous version of
 * this logic looked obviously correct at the call site too.
 *
 * INTENDED CONSEQUENCE, which will look wrong to the next reader: dragging
 * left on a rail that is already space-constrained records a desired width
 * LARGER than currently fits. That is correct. The user asked for wider, so we
 * store wider and render what fits; when the dock closes or the window grows
 * they get the width they asked for. Clamping it here is precisely the bug
 * this module exists to prevent.
 */
export function desiredFromDrag(releasedAt: number): number {
  return Math.max(RAIL_MIN_W, releasedAt)
}
