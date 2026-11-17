// guided-results 03: the summary line and section 5's bars above a Guided list.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { FlowHeld, FlowSection } from '../../api/flow'
import { SectionHoldings } from './SectionHoldings'
import { GenericSectionCard, SectionView } from './SectionView'
import * as summary from './holdSummary'

// Spies over the real functions: who reads the summaries' inputs.
vi.mock('./holdSummary', async orig => {
  const real = await orig<typeof import('./holdSummary')>()
  return { ...real, holdSummary: vi.fn(real.holdSummary), fixedCharts: vi.fn(real.fixedCharts), isHelper: vi.fn(real.isHelper) }
})

const held = (over: Partial<Omit<FlowHeld, 'helper'>> & { helper?: unknown }): FlowHeld => ({
  component: 'Generator', entity: 'pv', carrier: 'solar', field: 'p_nom', value: 5, unit: 'MW',
  extendable: false, series: false, ...over,
} as FlowHeld)
const ASSETS = [held({ value: 40 }), held({ entity: 'wind', carrier: 'wind', value: 12 }),
  held({ entity: 'ely', component: 'Link', carrier: 'hydrogen', value: 4, unit: 't/h' }),
  held({ entity: 'ely2', component: 'Link', carrier: 'ammonia', value: 2, unit: 't/h' }),
  held({ entity: 'bat', component: 'StorageUnit', carrier: 'battery', extendable: true }),
  held({ entity: '__lost_steel', carrier: 'steel', value: 1e6, helper: true })]

const assets = (holds: FlowHeld[], total?: number) =>
  render(<SectionHoldings id="assets" holds={holds} total={total} pending={false} failed={false} />)

afterEach(cleanup)

describe('section 5: line and bars', () => {
  it('the line, one chart per unit, and the same numbers in text', () => {
    assets(ASSETS)
    expect(screen.getByTestId('section-held-line').textContent)
      .toBe('6 assets incl. 1 helper · 52 MW, 6 t/h fixed, 1 sized by the study')
    const charts = screen.getAllByTestId('assets-chart')
    expect(charts.map(c => c.getAttribute('data-unit'))).toEqual(['MW', 't/h'])
    // The unit is on the axis; a tonne bus never shares the MW chart.
    expect(within(charts[0]).getByTestId('assets-chart-axis').textContent).toContain('MW')
    expect(within(charts[0]).queryByText(/hydrogen/)).toBeNull()
    expect(within(charts[0]).getByTestId('assets-chart-text').textContent)
      .toBe('Fixed capacity by carrier: solar 40 MW, wind 12 MW.')
    expect(within(charts[1]).getByTestId('assets-chart-text').textContent)
      .toBe('Fixed capacity by carrier: hydrogen 4 t/h, ammonia 2 t/h.')
  })

  it('extendable assets and helpers are named, not drawn', () => {
    assets(ASSETS)
    expect(screen.getByTestId('assets-sized').textContent).toBe('Sized by the study, not drawn: bat')
    expect(screen.getByTestId('assets-helpers').textContent)
      .toBe('Added by the model, left out of the sums and the chart: lost production (steel)')
    for (const c of screen.getAllByTestId('assets-chart')) expect(c.textContent).not.toContain('steel')
  })

  it('no chart for one carrier or no fixed capacity, and the line says so', () => {
    assets([held({}), held({ entity: 'pv2' })])
    expect(screen.queryByTestId('assets-chart')).toBeNull()
    expect(screen.getByTestId('assets-no-chart').textContent).toBe('No chart: the fixed capacity is all on one carrier.')
    cleanup()
    assets([held({ extendable: true })])
    expect(screen.queryByTestId('assets-chart')).toBeNull()
    expect(screen.getByTestId('assets-no-chart').textContent).toBe('No chart: no asset has a fixed size.')
  })

  it('a capped list: the cap note on the line and on the chart', () => {
    assets(ASSETS, 300)
    expect(screen.getByTestId('section-held-line').textContent).toContain('from the first 6 of 300')
    for (const c of screen.getAllByTestId('assets-chart')) expect(c.textContent).toContain('from the first 6 of 300 assets')
  })

  it('a missing or odd helper flag never crashes and reads as an asset', () => {
    assets([held({ helper: 'true' }), held({ entity: 'w', carrier: 'wind', helper: undefined })])
    expect(screen.getByTestId('section-held-line').textContent).toBe('2 assets · 10 MW fixed')
    expect(screen.queryByTestId('assets-helpers')).toBeNull()
  })

  it('an empty section keeps its plain state: no line, no chart', () => {
    assets([])
    expect(screen.getByTestId('section-held-empty').textContent).toBe('Nothing here yet.')
    expect(screen.queryByTestId('section-held-line')).toBeNull()
    expect(screen.queryByTestId('assets-no-chart')).toBeNull()
  })

  it('sections 2, 4, 6 have a line and no chart', () => {
    render(<SectionHoldings id="loads" pending={false} failed={false}
      holds={[held({ component: 'Load', field: 'p_set', value: 40 })]} />)
    expect(screen.getByTestId('section-held-line').textContent).toBe('1 load · largest peak 40 MW')
    expect(screen.queryByTestId('assets-chart')).toBeNull()
  })
})

// GK (3): the summaries are display only. With their inputs altered (helper
// flags, values, units) and their functions spied, the section's state, counts
// and items read the same, and nothing outside the card calls them.
const flowRows: Record<string, Partial<FlowSection>> = {}
vi.mock('../../hooks/useProjectFlow', () => ({
  useProjectFlow: () => ({ isError: false, isPending: false, flow: { sections: [] },
    section: (id: string) => flowRows[id] ?? null }),
}))
vi.mock('../../components/flowScope/NotInScopeGroup', () => ({ SectionNotInScope: () => null }))
vi.mock('../../hooks/useRunGate', () => ({ useRunGate: () => ({ checking: false, blocked: false, notes: [] }) }))

describe('display only', () => {
  const heading = (holds: FlowHeld[]) => {
    flowRows.assets = { state: 'warning', summary: '6 assets', holds, holds_total: holds.length,
      items: [{ code: 'missing_x', severity: 'warning', source: 'template', blocking: false, acknowledged: false,
        card: null, in_scope: true, message: 'Check x.',
        target: { section: 'assets', panel: null, component: null, entity: null, field: null } }] }
    const view = render(<QueryClientProvider client={new QueryClient()}><SectionView id="assets" /></QueryClientProvider>)
    const out = [screen.getByTestId('section-summary').textContent, screen.getByTestId('section-state-assets').outerHTML,
      screen.getAllByTestId('section-item').length]
    view.unmount()
    return out
  }

  it('the state, summary and items are the same with the summaries\' inputs altered', () => {
    const line = vi.mocked(summary.holdSummary)
    const bars = vi.mocked(summary.fixedCharts)
    const helper = vi.mocked(summary.isHelper)
    for (const f of [line, bars, helper]) f.mockClear()
    const before = heading(ASSETS)
    const after = heading(ASSETS.map(h => ({ ...h, helper: !(h as { helper?: boolean }).helper, value: 7, unit: 't' })))
    expect(after).toEqual(before)
    // SectionView (state, counts, items, assumptions) never reads them.
    expect(line).not.toHaveBeenCalled()
    expect(bars).not.toHaveBeenCalled()
    expect(helper).not.toHaveBeenCalled()
    // The card does, and only for its own line.
    render(<GenericSectionCard id="assets" />)
    expect(line).toHaveBeenCalled()
  })
})
