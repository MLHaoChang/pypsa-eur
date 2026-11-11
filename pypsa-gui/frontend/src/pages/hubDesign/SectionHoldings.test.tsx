// ui-modern 05 slice 2 (finding 7): a Guided section lists what it holds.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { FlowHeld } from '../../api/flow'
import { useUIStore } from '../../store/uiStore'
import { SectionHoldings } from './SectionHoldings'

const openTimeSeriesFor = vi.fn()
vi.mock('../../layout/flowNavigation', async orig => ({
  ...(await orig<typeof import('../../layout/flowNavigation')>()),
  openTimeSeriesFor: (...a: unknown[]) => openTimeSeriesFor(...a),
}))

const held = (over: Partial<FlowHeld>): FlowHeld => ({
  component: 'Generator', entity: 'pv', carrier: null, field: 'p_nom', value: 5, unit: 'MW',
  extendable: false, series: false, ...over,
})
const gens = (n: number) => Array.from({ length: n }, (_, i) => held({ entity: `g${i + 1}` }))

beforeEach(() => {
  useUIStore.setState({ uiMode: 'guided', activeSection: 'assets', activeSlidePanel: 'hubDesign',
    fieldDrawer: null, flowReturn: null })
  openTimeSeriesFor.mockClear()
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('SectionHoldings', () => {
  it('one row per thing, one link per row', () => {
    render(<SectionHoldings id="assets" pending={false} failed={false}
      holds={[held({}), held({ component: 'Store', entity: 'tank', field: 'e_nom', value: 40, unit: 'MWh' })]} />)
    const rows = screen.getAllByTestId('section-held')
    expect(rows.map(r => r.textContent)).toEqual([
      'pvGeneratorCapacity: 5 MWEdit ›', 'tankStoreEnergy capacity: 40 MWhEdit ›'])
    for (const r of rows) expect(r.querySelectorAll('button')).toHaveLength(1)
    // Two classes: each group is titled with its count.
    expect(screen.getAllByTestId('section-held-group').map(g => g.querySelector('p')?.textContent))
      .toEqual(['Generators (1)', 'Stores (1)'])
  })

  it('a group longer than 8 folds, and "Show all" lists every row', () => {
    render(<SectionHoldings id="assets" pending={false} failed={false} holds={gens(11)} />)
    expect(screen.getAllByTestId('section-held')).toHaveLength(8)
    const more = screen.getByTestId('section-held-show-all')
    expect(more.textContent).toBe('(3 more) Show all')
    fireEvent.click(more)
    expect(screen.getAllByTestId('section-held')).toHaveLength(11)
    expect(screen.queryByTestId('section-held-show-all')).toBeNull()
  })

  it('8 rows do not fold', () => {
    render(<SectionHoldings id="assets" pending={false} failed={false} holds={gens(8)} />)
    expect(screen.getAllByTestId('section-held')).toHaveLength(8)
    expect(screen.queryByTestId('section-held-show-all')).toBeNull()
  })

  it('"Edit" on an asset opens its editor over the card and stays in Guided', () => {
    render(<SectionHoldings id="assets" pending={false} failed={false} holds={[held({})]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit pv' }))
    const ui = useUIStore.getState()
    expect(ui.uiMode).toBe('guided')
    expect(ui.fieldDrawer).toEqual({ section: 'assets', panel: null, component: 'Generator', entity: 'pv', field: null })
  })

  it('"Edit" on a time series opens the Time Series drawer on that row', () => {
    render(<SectionHoldings id="profiles" pending={false} failed={false}
      holds={[held({ field: 'p_max_pu', value: null, unit: null })]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit pv, Availability time series' }))
    expect(openTimeSeriesFor).toHaveBeenCalledWith('Generator', 'pv', { field: 'p_max_pu' })
  })

  it('"Edit" on a carrier goes to Expert and leaves a way back to the section', () => {
    useUIStore.setState({ activeSection: 'carriers' })
    render(<SectionHoldings id="carriers" pending={false} failed={false}
      holds={[held({ component: 'Carrier', entity: 'solar', field: null, value: null, unit: null })]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit solar' }))
    const ui = useUIStore.getState()
    expect(ui.uiMode).toBe('expert')
    expect(ui.flowReturn).toMatchObject({ mode: 'guided', section: 'carriers', label: 'Carriers & processes' })
  })

  it('empty, failed and loading each say so', () => {
    const { rerender } = render(<SectionHoldings id="loads" pending={false} failed={false} holds={[]} />)
    expect(screen.getByTestId('section-held-empty').textContent).toBe('Nothing here yet.')
    rerender(<SectionHoldings id="loads" pending={false} failed holds={undefined} />)
    expect(screen.getByTestId('section-held-failed').textContent).toContain('could not be read')
    vi.useFakeTimers()
    rerender(<SectionHoldings id="loads" pending failed={false} holds={undefined} />)
    const loading = screen.getByTestId('section-held-loading')
    expect(loading.getAttribute('aria-busy')).toBe('true')
    // Nothing flashes for a fast read; the bars come after 200 ms, in the same box.
    expect(loading.children).toHaveLength(0)
    act(() => { vi.advanceTimersByTime(200) })
    expect(loading.children).toHaveLength(3)
  })

  it('a list the server cut short says so under the rows', () => {
    const { rerender } = render(<SectionHoldings id="assets" pending={false} failed={false}
      holds={gens(3)} total={40} />)
    expect(screen.getByTestId('section-held-rest').textContent).toBe('Showing the first 3 of 40. The rest are in Expert.')
    rerender(<SectionHoldings id="assets" pending={false} failed={false} holds={gens(3)} total={3} />)
    expect(screen.queryByTestId('section-held-rest')).toBeNull()
  })

  it('a cut list gives no per-group count: the rows sent are not all of the class', () => {
    const two = [...gens(2), held({ component: 'Store', entity: 'tank', field: 'e_nom', unit: 'MWh' })]
    const titles = () => screen.getAllByTestId('section-held-group').map(g => g.querySelector('p')?.textContent)
    const { rerender } = render(<SectionHoldings id="assets" pending={false} failed={false}
      holds={two} total={100} />)
    expect(titles()).toEqual(['Generators', 'Stores'])
    expect(screen.getByTestId('section-held-rest').textContent).toContain('first 3 of 100')
    rerender(<SectionHoldings id="assets" pending={false} failed={false} holds={two} total={3} />)
    expect(titles()).toEqual(['Generators (2)', 'Stores (1)'])
  })

  it('a deferred flow says the list comes back, and a failed refetch keeps the list', () => {
    const { rerender } = render(<SectionHoldings id="assets" pending={false} failed={false}
      holds={undefined} deferred />)
    expect(screen.getByTestId('section-held-deferred').textContent).toContain('once the project check has run')
    expect(screen.queryByTestId('section-held-loading')).toBeNull()

    rerender(<SectionHoldings id="assets" pending={false} failed holds={[held({})]} />)
    expect(screen.getByTestId('section-held-list')).toBeTruthy()
    expect(screen.queryByTestId('section-held-failed')).toBeNull()
  })
})
