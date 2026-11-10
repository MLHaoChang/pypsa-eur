// The Results headline strip (ui-modern 03 S2): the backend's cards in its
// order, each named with its tab's own word, one "Out of date" mark, and a
// state with its sentence where the run has no figure.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { ChartSourceProvider } from '../../components/charts/chartTheme'
import type { HeadlineCard } from '../../api/simulation'

const api = vi.hoisted(() => ({ getHeadlineKpis: vi.fn() }))
vi.mock('../../api/simulation', async (orig) => {
  const actual = await orig<typeof import('../../api/simulation')>()
  return { ...actual, resultsApi: { ...actual.resultsApi, getHeadlineKpis: api.getHeadlineKpis } }
})
const nav = vi.hoisted(() => ({ showResultsTab: vi.fn() }))
vi.mock('../../layout/flowNavigation', async (orig) => ({
  ...(await orig<typeof import('../../layout/flowNavigation')>()), showResultsTab: nav.showResultsTab,
}))

import HeadlineStrip from './HeadlineStrip'
import { COST_WORDS, CURTAILMENT_RATE, HEADLINE_WORDS, headlineFigure } from './headlineWords'
import { HEADLINES } from './investment/financeModel'

const card = (id: string, over: Partial<HeadlineCard> = {}): HeadlineCard => ({
  id, value: null, unit: null, state: 'ready', reason: null, formula_id: null, detail: null, ...over,
})
const HUB = {
  kind: 'energy_hub',
  cards: [
    card('lcoe', { state: 'not_run', reason: 'The investment case has not run on this solve.' }),
    card('system_cost', { value: 34_700_000, unit: 'currency/yr' }),
    card('new_build_capital', { value: 2_210_000, unit: 'currency/yr' }),
    card('curtailed_share', { value: 12.34, unit: '%', formula_id: 'curtailment-share-of-available/1' }),
    card('unserved_energy', { value: 0, unit: 'MWh', state: 'zero' }),
  ],
}

function renderStrip(source = { line: 'Latest solve · solved · 2030-01-01 00:00 → 2030-12-31 23:00 · all periods', stale: false }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <ChartSourceProvider value={source}><HeadlineStrip /></ChartSourceProvider>
    </QueryClientProvider>,
  )
}
const figure = (id: string) => screen.getByTestId(`headline-${id}`).querySelector('[data-kpi-value]')

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  api.getHeadlineKpis.mockResolvedValue(HUB)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('HeadlineStrip', () => {
  it('shows the cards in the backend order, each with its unit and what it covers', async () => {
    renderStrip()
    const strip = await screen.findByTestId('headline-strip')
    expect([...strip.querySelectorAll('[data-headline]')].map(t => t.getAttribute('data-testid'))).toEqual(
      HUB.cards.map(c => `headline-${c.id}`))
    expect(figure('system_cost')?.getAttribute('data-kpi-value')).toBe('€34.7 M')
    expect(screen.getByTestId('headline-system_cost').textContent).toContain('per year')
    expect(figure('curtailed_share')?.getAttribute('data-kpi-value')).toBe('12.3 %')
    // A computed zero stays a 0, never a state.
    expect(figure('unserved_energy')?.getAttribute('data-kpi-value')).toBe('0 MWh')
    // One source line for the row, and no engine identifier anywhere.
    expect(strip.querySelectorAll('[data-chart-source]')).toHaveLength(1)
    expect(strip.textContent).toContain('Latest solve · solved')
    expect(strip.textContent).not.toMatch(/curtailment-share|\/1|_/)
  })

  it('a card without a figure says its state and why, with one link to the tab that produces it', async () => {
    renderStrip()
    const lcoe = await screen.findByTestId('headline-lcoe')
    expect(lcoe.getAttribute('data-state')).toBe('not_run')
    expect(lcoe.querySelector('[data-kpi-value]')).toBeNull()
    expect(lcoe.textContent).toContain('Not run yet')
    expect(lcoe.textContent).toContain('The investment case has not run on this solve.')
    const links = lcoe.querySelectorAll('button')
    expect(links).toHaveLength(1)
    expect(links[0].textContent).toBe('Investment case ›')
    fireEvent.click(links[0])
    expect(nav.showResultsTab).toHaveBeenCalledWith('investment', { from: 'Summary' })
  })

  it('says "Out of date" once, on the source line, and dims the figures', async () => {
    renderStrip({ line: 'Latest solve · solved', stale: true })
    const strip = await screen.findByTestId('headline-strip')
    expect(strip.textContent?.match(/Out of date/g)).toHaveLength(1)
    expect(strip.querySelector('[data-chart-source]')?.textContent).toContain('Out of date')
    expect(figure('system_cost')?.className).toContain('opacity-60')
  })

  it('a card the changed inputs left without a figure keeps the one mark and gives its sentence', async () => {
    api.getHeadlineKpis.mockResolvedValue({ kind: 'network', cards: [
      card('system_cost', { value: 1e6, unit: 'currency/yr' }),
      card('curtailed_share', { state: 'stale', reason: 'The inputs changed since this solve.' }),
    ] })
    renderStrip()
    const strip = await screen.findByTestId('headline-strip')
    expect(strip.textContent?.match(/Out of date/g)).toHaveLength(1)
    expect(screen.getByTestId('headline-curtailed_share').textContent).toContain('The inputs changed since this solve.')
  })

  it('names the line at the peak loading, and the horizon for a multi-period cost', async () => {
    api.getHeadlineKpis.mockResolvedValue({ kind: 'network', cards: [
      card('system_cost', { value: 5e8, unit: 'currency' }),
      card('peak_line_loading', { value: 87.25, unit: '%', detail: 'North tie' }),
    ] })
    renderStrip()
    expect((await screen.findByTestId('headline-peak_line_loading')).textContent).toContain('North tie')
    expect(screen.getByTestId('headline-system_cost').textContent).toContain('whole horizon')
  })

  it('says so when the figures cannot be read, and draws nothing when nothing is solved', async () => {
    api.getHeadlineKpis.mockRejectedValue(new Error('500'))
    renderStrip()
    expect((await screen.findByTestId('headline-strip-failed')).textContent).toContain('could not be read')
    cleanup()
    api.getHeadlineKpis.mockResolvedValue(null)
    renderStrip()
    await Promise.resolve()
    expect(screen.queryByTestId('headline-strip')).toBeNull()
  })
})

describe('headline words', () => {
  // The ids `services/results/headline_kpis.py` ORDER can send.
  const IDS = ['lcoe', 'system_cost', 'new_build_capital', 'curtailed_share', 'unserved_energy', 'peak_line_loading']

  it('names every card the backend can send, in its tab’s own words', () => {
    expect(Object.keys(HEADLINE_WORDS).sort()).toEqual([...IDS].sort())
    expect(HEADLINE_WORDS.lcoe.label).toBe(HEADLINES.find(h => h.id === 'lcoe_real')!.label)
    expect(HEADLINE_WORDS.curtailed_share.label).toBe(CURTAILMENT_RATE)
    expect(HEADLINE_WORDS.system_cost.label).toBe(COST_WORDS.total)
    for (const w of Object.values(HEADLINE_WORDS)) expect(w.label).not.toMatch(/CAPEX|annualised|_/)
  })

  it('formats a price with its currency', () => {
    expect(headlineFigure(card('lcoe', { value: 55.5, unit: 'EUR/MWh' }))).toEqual({ value: '55.50 €/MWh', basis: 'investment case' })
    expect(headlineFigure(card('lcoe', { value: 55.5, unit: 'USD/MWh' })).value).toBe('55.50 USD/MWh')
  })
})
