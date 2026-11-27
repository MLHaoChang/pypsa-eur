// ── Fitting the topology canvas only while it has an area (pure) ─────────────
// The canvas stays mounted but display:none while a full-screen tab (Results)
// fills the main area. React Flow animates fitView through d3's zoom
// interpolation, which divides by the pane's size: on a 0×0 pane every frame's
// x, y and zoom is NaN, and the dotted Background writes "NaN" into its
// <pattern x/y> and <circle cx/cy/r>. The canvas refits when its width changes,
// so a fit skipped while hidden happens once the canvas shows again.

export interface Fittable<O> { fitView: (opts?: O) => unknown }
export interface Sized { clientWidth: number; clientHeight: number }

/** Fit `instance` into `pane` when the pane has a positive area; false when skipped. */
export function fitWhenVisible<O>(instance: Fittable<O> | null, pane: Sized | null, opts: O): boolean {
  if (!instance || !pane || !(pane.clientWidth > 0 && pane.clientHeight > 0)) return false
  instance.fitView(opts)
  return true
}
