// Phase 2 plan Task 6.3 / spec §6.4: while results show, the overlay lists
// the site's objects with their current value under the snapshot's
// timestamp — not colour-only, not hover-only.
import { act, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import SiteResultsReadout from './SiteResultsReadout'
import { createResultsStore } from '../site3d/resultsStore'

const objects = [
  { type: 'Generator', name: 'PV field', kind: 'pv', bus: 'B' },
  { type: 'Line', name: 'L1', kind: 'feeder', bus: 'B' },
]

describe('SiteResultsReadout', () => {
  it('renders nothing while no results show', () => {
    const store = createResultsStore()
    const { container } = render(<SiteResultsReadout store={store} objects={objects} />)
    expect(container.textContent).toBe('')
  })
  it('lists each object with its value under the timestamp, following the store', () => {
    const store = createResultsStore()
    render(<SiteResultsReadout store={store} objects={objects} />)
    act(() => store.set({ idx: 12, iso: '2030-01-01T12:00:00', states: new Map([
      ['Generator:PV field', { kind: 'output', mw: 12.3, cap: 20 }],
      ['Line:L1', { kind: 'branch', p0: 45, cap: 120, unit: 'MVA', bus0: 'B', bus1: 'Grid' }],
    ]) }))
    expect(screen.getByRole('heading').textContent).toContain('2030-01-01 12:00')
    const items = screen.getAllByRole('listitem').map(li => li.textContent)
    expect(items).toEqual(['PV field · 12.3 MW · 62 % of 20 MW', 'L1 · 45.0 MW → Grid · 38 % of 120 MVA rating'])
    act(() => store.set({ idx: 0, iso: '', states: new Map() }))
    expect(screen.queryByRole('list')).toBeNull()
  })
  it('starts collapsed in a narrow pane, open in a wide one', () => {
    const store = createResultsStore()
    const state = { idx: 1, iso: 't', states: new Map([['Generator:PV field', { kind: 'output' as const, mw: 1, cap: 2 }]]) }
    const at = (w: number) => {
      const r = render(<div data-site-pane ref={el => { if (el) Object.defineProperty(el, 'clientWidth', { value: w, configurable: true }) }}><SiteResultsReadout store={store} objects={objects} /></div>)
      return r
    }
    const narrow = at(440)
    act(() => store.set(state))
    expect((narrow.container.querySelector('details') as HTMLDetailsElement).open).toBe(false)
    narrow.unmount()
    const store2 = createResultsStore()
    const wide = render(<div data-site-pane ref={el => { if (el) Object.defineProperty(el, 'clientWidth', { value: 1200, configurable: true }) }}><SiteResultsReadout store={store2} objects={objects} /></div>)
    act(() => store2.set(state))
    expect((wide.container.querySelector('details') as HTMLDetailsElement).open).toBe(true)
  })
})
