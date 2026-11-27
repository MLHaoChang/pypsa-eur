// The topology canvas must not fit itself while hidden behind Results: a
// fitView on a 0×0 pane animates to NaN and the Background logs SVG errors
// (AA06 S5 result journey, expert/single).
import { describe, it, expect, vi } from 'vitest'
import { fitWhenVisible } from './topologyFit'

describe('fitWhenVisible', () => {
  const opts = { duration: 400 }

  it('fits a pane with an area', () => {
    const fitView = vi.fn()
    expect(fitWhenVisible({ fitView }, { clientWidth: 800, clientHeight: 600 }, opts)).toBe(true)
    expect(fitView).toHaveBeenCalledWith(opts)
  })

  it.each([
    ['hidden (0×0)', { clientWidth: 0, clientHeight: 0 }],
    ['zero width', { clientWidth: 0, clientHeight: 600 }],
    ['zero height', { clientWidth: 800, clientHeight: 0 }],
    ['NaN size', { clientWidth: Number.NaN, clientHeight: Number.NaN }],
    ['no pane yet', null],
  ])('skips a pane that is %s', (_, pane) => {
    const fitView = vi.fn()
    expect(fitWhenVisible({ fitView }, pane, opts)).toBe(false)
    expect(fitView).not.toHaveBeenCalled()
  })

  it('skips before React Flow is initialised', () => {
    expect(fitWhenVisible(null, { clientWidth: 800, clientHeight: 600 }, opts)).toBe(false)
  })
})
