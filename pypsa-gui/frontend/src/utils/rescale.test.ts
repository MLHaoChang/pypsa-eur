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

  it('routes a blocked line to blocked regardless of rel_change magnitude', () => {
    // A blocked line with high rel_change must go to blocked, not ask. If it
    // went to ask, Task 5 would offer the user an undefined per-km rescale.
    const { auto, ask, blocked } = partitionRescale([
      preview({ name: 'BROKEN', skipped_reason: 'new_length<=0', rel_change: 300 }),
    ])
    expect(auto).toEqual([])
    expect(ask).toEqual([])
    expect(blocked.map(p => p.name)).toEqual(['BROKEN'])
  })

  it('splits a mixed batch', () => {
    const { auto, ask, blocked } = partitionRescale([
      preview({ name: 'A', rel_change: 0.001 }),
      preview({ name: 'B', rel_change: 300 }),
      preview({ name: 'C', skipped_reason: 'new_length<=0' }),
      preview({ name: 'D', skipped_reason: 'undefined_per_km', rel_change: 150 }),
    ])
    expect(auto.map(p => p.name)).toEqual(['A'])
    expect(ask.map(p => p.name)).toEqual(['B'])
    expect(blocked.map(p => p.name)).toEqual(['C', 'D'])
  })

  it('handles an empty batch', () => {
    expect(partitionRescale([])).toEqual({ auto: [], ask: [], blocked: [] })
  })
})
