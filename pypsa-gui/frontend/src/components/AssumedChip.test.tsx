// WALK-IMPROVE-1 b: an assumed input names its source on the page, not on
// hover; a typed value reads "Stated" with no source; a kept one "Confirmed".
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { AssumedChip, AssumedKey, assumedSourceText, assumedState } from './AssumedChip'

afterEach(cleanup)

describe('AssumedChip', () => {
  it('shows "Assumed" with its source, year and note as visible text', () => {
    render(<AssumedChip testId="a" source="BMF AfA table" sourceYear={2000} note="batteries per 3.1.3" />)
    const a = screen.getByTestId('a')
    expect(a.dataset.state).toBe('assumed')
    expect(a.textContent).toContain('Assumed')
    expect(screen.getByTestId('a-source').textContent).toBe('BMF AfA table, 2000 — batteries per 3.1.3')
    expect(a.querySelector('[title]')).toBeNull()
  })

  it('says the source is not recorded rather than showing nothing', () => {
    expect(assumedSourceText({ source: '  ' })).toBe('source not recorded')
    expect(assumedSourceText({ source: 'WACC', note: 'assumed, no primary source' }))
      .toBe('WACC — assumed, no primary source')
  })

  it('reads "Stated" with no source once typed, even if it was confirmed', () => {
    render(<AssumedChip testId="t" source="BMF" sourceYear={2000} typed confirmed />)
    const t = screen.getByTestId('t')
    expect(t.dataset.state).toBe('typed')
    expect(t.textContent).toBe('Stated')
    expect(screen.queryByTestId('t-source')).toBeNull()
  })

  it('reads "Confirmed" and keeps its source when kept', () => {
    render(<AssumedChip testId="c" source="BMF" sourceYear={2000} confirmed />)
    expect(screen.getByTestId('c').dataset.state).toBe('confirmed')
    expect(screen.getByTestId('c').textContent).toContain('Confirmed')
    expect(screen.getByTestId('c-source').textContent).toBe('BMF, 2000')
  })

  it('orders the states typed > confirmed > assumed', () => {
    expect(assumedState({})).toBe('assumed')
    expect(assumedState({ confirmed: true })).toBe('confirmed')
    expect(assumedState({ typed: true, confirmed: true })).toBe('typed')
  })
})

describe('AssumedKey', () => {
  it('names Assumed and Stated, and Confirmed only where a value can be kept', () => {
    const { rerender } = render(<AssumedKey testId="k" />)
    expect(screen.getByTestId('k').textContent).toMatch(/Assumed.*Stated/)
    expect(screen.getByTestId('k').textContent).not.toContain('Confirmed')
    rerender(<AssumedKey testId="k" confirmable />)
    expect(screen.getByTestId('k').textContent).toMatch(/Assumed.*Confirmed.*Stated/)
  })
})
