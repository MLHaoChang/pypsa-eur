// Single-pass min/max/range helpers.
//
// `Math.max(...vals)` and `Math.min(...vals)` use the spread operator, which
// pushes every element onto the JS argument stack. Browsers cap that at
// roughly 65 535 (Chrome) or 524 287 (Firefox), so an 8 760-hour profile is
// safe today but a multi-year horizon (~26 280) or a multi-decade study can
// trip `RangeError: Maximum call stack size exceeded`. Reduce-based
// implementations have no such cap.
//
// Always handles NaN: non-finite entries are skipped (NaN propagates through
// `<` / `>` as `false`, which would otherwise leave the seed value
// unchanged but signal the wrong sentinel on entirely-NaN inputs).

export function safeMax(vals: ArrayLike<number>): number {
  let m = -Infinity
  const n = vals.length
  for (let i = 0; i < n; i += 1) {
    const v = vals[i]
    if (Number.isFinite(v) && v > m) m = v
  }
  return m === -Infinity ? 0 : m
}

export function safeMin(vals: ArrayLike<number>): number {
  let m = Infinity
  const n = vals.length
  for (let i = 0; i < n; i += 1) {
    const v = vals[i]
    if (Number.isFinite(v) && v < m) m = v
  }
  return m === Infinity ? 0 : m
}

export function safeMinMax(vals: ArrayLike<number>): { min: number; max: number } {
  let lo = Infinity, hi = -Infinity
  const n = vals.length
  for (let i = 0; i < n; i += 1) {
    const v = vals[i]
    if (Number.isFinite(v)) {
      if (v < lo) lo = v
      if (v > hi) hi = v
    }
  }
  if (lo === Infinity) return { min: 0, max: 0 }
  return { min: lo, max: hi }
}
