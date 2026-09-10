// Regression: the whole Results panel crashed (React #310, "Rendered more
// hooks than during the previous render") the first time dispatch data
// arrived AFTER an empty first render — `useRef` sat below the "No dispatch
// data" early return, so the data-bearing render called one more hook than
// the empty one. Found by driving the built app in a real browser (QA round
// S16); no in-process test rendered the two states in sequence on one
// instance, which is the only way the defect can fire.
//
// Bite (verified): move `const chartRef = useRef(...)` back below the early
// return — the rerender here throws and this test fails.
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import DispatchStack from './DispatchStack'

type Props = Parameters<typeof DispatchStack>[0]

const base: Omit<Props, 'gensTS'> = {
  loadTS: null,
  storPowerTS: null,
  generators: [{ name: 'g1', carrier: 'gas' }] as Props['generators'],
  loads: [] as Props['loads'],
  storageUnits: [] as Props['storageUnits'],
  range: { from: 0, to: 1 },
}

describe('DispatchStack hook order', () => {
  it('survives dispatch data arriving after the empty first render', () => {
    const { rerender } = render(<DispatchStack gensTS={null} {...base} />)
    expect(screen.getByText(/No dispatch data/i)).toBeTruthy()
    const gensTS = {
      index: ['t0', 't1'],
      columns: ['g1'],
      data: [[10], [12]],
    }
    expect(() =>
      rerender(<DispatchStack gensTS={gensTS as Props['gensTS']} {...base} />),
    ).not.toThrow()
  })
})
