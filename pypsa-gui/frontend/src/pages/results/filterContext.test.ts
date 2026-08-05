import { describe, expect, it } from 'vitest'
import { defaultWindow, DEFAULT_FLAT_WINDOW, WINDOW_THRESHOLD } from './filterContext'

/** N hourly ISO stamps starting 2030-01-01T00:00:00. */
function stamps(n: number): string[] {
  const out: string[] = []
  const start = Date.UTC(2030, 0, 1)
  for (let i = 0; i < n; i++) {
    out.push(new Date(start + i * 3_600_000).toISOString().slice(0, 19))
  }
  return out
}

describe('defaultWindow', () => {
  it('leaves a short flat network whole', () => {
    expect(defaultWindow(stamps(168), undefined)).toEqual({ kind: 'whole' })
  })

  it('leaves a network of exactly the threshold whole', () => {
    // 8760 is one hourly year — the largest horizon that renders as a chart
    // without windowing, so it must NOT be narrowed.
    expect(defaultWindow(stamps(WINDOW_THRESHOLD), undefined)).toEqual({ kind: 'whole' })
  })

  it('leaves a SHORT multi-period network whole', () => {
    // The threshold is tested against the TOTAL horizon before any structural
    // branch. Branching on multi-period first would window the golden test
    // fixture (2 periods x 24) down to 24 rows.
    const periods = [...Array(24).fill(2030), ...Array(24).fill(2035)]
    expect(defaultWindow(stamps(48), periods)).toEqual({ kind: 'whole' })
  })

  it('opens a long multi-period network on its first period', () => {
    const periods = [...Array(8760).fill(2030), ...Array(8760).fill(2035)]
    expect(defaultWindow(stamps(17520), periods)).toEqual({ kind: 'period', period: 2030 })
  })

  it('picks the numerically lowest period, not the first encountered', () => {
    const periods = [...Array(8760).fill(2040), ...Array(8760).fill(2030)]
    expect(defaultWindow(stamps(17520), periods)).toEqual({ kind: 'period', period: 2030 })
  })

  it('opens a long flat network on its first month', () => {
    const s = stamps(26280)
    expect(defaultWindow(s, undefined)).toEqual({
      kind: 'iso',
      fromIso: s[0],
      toIso: s[DEFAULT_FLAT_WINDOW - 1],
    })
  })

  it('treats an empty periods array as flat', () => {
    const s = stamps(26280)
    expect(defaultWindow(s, [])).toEqual({
      kind: 'iso', fromIso: s[0], toIso: s[DEFAULT_FLAT_WINDOW - 1],
    })
  })

  it('returns whole for an empty snapshot list', () => {
    expect(defaultWindow([], undefined)).toEqual({ kind: 'whole' })
  })

  it('never runs past the end when the horizon is barely over the threshold', () => {
    const s = stamps(WINDOW_THRESHOLD + 10)
    const w = defaultWindow(s, undefined)
    expect(w).toEqual({ kind: 'iso', fromIso: s[0], toIso: s[DEFAULT_FLAT_WINDOW - 1] })
  })
})
