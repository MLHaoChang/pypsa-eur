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
import { COST_WORDS, CURTAILMENT_RATE, HEADLINE_WORDS, comparisonBasis, headlineDelta, headlineFigure, headlineTrend } from './headlineWords'
import { HEADLINES } from './investment/financeModel'

const card = (id: string, over: Partial<HeadlineCard> = {}): HeadlineCard => ({
  id, value: null, unit: null, state: 'ready', reason: null, formula_id: null, detail: null, ...over,
})
const HUB = {
  kind: 'energy_hub',
  cards: [
    card('lcoe', { state: 'not_run', reason: 'The investment case has not run on this solve.' }),
    card('system_cost', { value: 34_700_000, unit: 'currency' }),
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
    // The system cost covers the solved snapshots; only the annualised new build reads per year.
    expect(screen.getByTestId('headline-system_cost').textContent).toContain('in this solve')
    expect(screen.getByTestId('headline-system_cost').textContent).not.toContain('per year')
    expect(screen.getByTestId('headline-new_build_capital').textContent).toContain('per year')
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

  it('names the line at the peak loading', async () => {
    api.getHeadlineKpis.mockResolvedValue({ kind: 'network', cards: [
      card('system_cost', { value: 5e8, unit: 'currency' }),
      card('peak_line_loading', { value: 87.25, unit: '%', detail: 'North tie' }),
    ] })
    renderStrip()
    expect((await screen.findByTestId('headline-peak_line_loading')).textContent).toContain('North tie')
  })

  it('holds the row with placeholders while the figures load, and shows no value', async () => {
    // The placeholder has as many tiles as the last row shown (five here), plus the source line's bar.
    api.getHeadlineKpis.mockResolvedValue(HUB)
    renderStrip()
    await screen.findByTestId('headline-strip')
    cleanup()
    let answer: (v: unknown) => void = () => {}
    api.getHeadlineKpis.mockReturnValue(new Promise(r => { answer = r }))
    renderStrip()
    const loading = screen.getByTestId('headline-strip-loading')
    expect(loading.getAttribute('aria-busy')).toBe('true')
    expect(loading.textContent).toBe('Loading the headline figures…')
    expect(loading.querySelectorAll('[aria-hidden="true"]')).toHaveLength(HUB.cards.length + 1)
    answer(HUB)
    await screen.findByTestId('headline-strip')
    expect(screen.queryByTestId('headline-strip-loading')).toBeNull()
  })

  it('links an LCOE card without a figure to the Investment case, except while the case runs', async () => {
    const stale = { state: 'unavailable' as const, reason: 'The investment case is out of date: run it again.' }
    api.getHeadlineKpis.mockResolvedValue({ kind: 'energy_hub', cards: [card('lcoe', stale), card('curtailed_share', { state: 'unavailable', reason: 'This run cannot give this figure.' })] })
    renderStrip()
    const lcoe = await screen.findByTestId('headline-lcoe')
    expect(lcoe.textContent).toContain('out of date: run it again')
    expect(lcoe.textContent).toContain('Investment case ›')
    // Another card that is unavailable has no tab to send the reader to.
    expect(screen.getByTestId('headline-curtailed_share').querySelector('button')).toBeNull()
    cleanup()
    api.getHeadlineKpis.mockResolvedValue({ kind: 'energy_hub', cards: [card('lcoe', { state: 'running', reason: 'The investment case is still running.' })] })
    renderStrip()
    const running = await screen.findByTestId('headline-lcoe')
    expect(running.textContent).toContain('still running')
    expect(running.querySelector('button')).toBeNull()
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

// ui-modern 03 S3: the change against the base case (K3) and the trend over
// the investment periods (K6). The backend subtracts; this only words it.
describe('HeadlineStrip against the base case', () => {
  const CASE = { role: 'case', base_name: 'Campus', base_project_id: 'p1', comparable: true, reason: null }
  const delta = (value: number, pct: number | null) => ({ value, pct, note: pct == null ? 'zero base: no percentage' : null })

  it('a case says each change with its sign in words, and the source line names the base project', async () => {
    api.getHeadlineKpis.mockResolvedValue({ kind: 'network', comparison: CASE, cards: [
      card('system_cost', { value: 33_240_000, unit: 'currency', delta: delta(-1_460_000, -4.21) }),
      card('new_build_capital', { value: 2_210_000, unit: 'currency/yr', delta: delta(2_210_000, null) }),
      card('curtailed_share', { value: 12.3, unit: '%', delta: delta(1.25, 11.3) }),
      card('peak_line_loading', { value: 80, unit: '%', delta: delta(0, 0) }),
    ] })
    renderStrip()
    const strip = await screen.findByTestId('headline-strip')
    expect(screen.getByTestId('headline-system_cost').textContent).toContain('−4.2 % vs Base case')
    // The tile's own basis words stay beside the change.
    expect(screen.getByTestId('headline-system_cost').textContent).toContain('in this solve')
    // No percentage over a zero base: the change in the tile's unit and rounding.
    expect(screen.getByTestId('headline-new_build_capital').textContent).toContain('+€2.21 M vs Base case')
    // A share changes in percentage points, never a percent of a percent.
    expect(screen.getByTestId('headline-curtailed_share').textContent).toContain('+1.3 percentage points vs Base case')
    expect(screen.getByTestId('headline-peak_line_loading').textContent).toContain('No change vs Base case')
    expect(strip.querySelector('[data-chart-source]')?.textContent).toContain('Base case: Campus')
    expect(screen.queryByTestId('headline-not-compared')).toBeNull()
  })

  it('the base says it is the base case and shows no change', async () => {
    api.getHeadlineKpis.mockResolvedValue({ ...HUB, comparison: { ...CASE, role: 'base' } })
    renderStrip()
    const strip = await screen.findByTestId('headline-strip')
    expect(strip.querySelector('[data-chart-source]')?.textContent).toMatch(/· Base case$/)
    expect(strip.textContent).not.toContain('vs Base case')
  })

  it('a case that is not compared says why in visible text and shows no change', async () => {
    const reason = 'Not compared with the base case: the base\'s inputs or finance settings changed since this case was branched.'
    api.getHeadlineKpis.mockResolvedValue({ ...HUB, comparison: { ...CASE, comparable: false, reason } })
    renderStrip()
    await screen.findByTestId('headline-strip')
    expect(screen.getByTestId('headline-not-compared').textContent).toBe(reason)
    expect(screen.getByTestId('headline-strip').textContent).not.toContain('vs Base case')
    expect(screen.getByTestId('headline-strip').querySelector('[data-chart-source]')?.textContent).toContain('Base case: Campus')
  })

  it('an out-of-date strip shows no change beside its dimmed figures', async () => {
    api.getHeadlineKpis.mockResolvedValue({ kind: 'network', comparison: CASE, cards: [
      card('system_cost', { value: 33_240_000, unit: 'currency', delta: delta(-1_460_000, -4.21) }),
    ] })
    renderStrip({ line: 'Latest solve · solved', stale: true })
    const strip = await screen.findByTestId('headline-strip')
    expect(strip.textContent).not.toContain('vs Base case')
    expect(strip.querySelector('[data-chart-source]')?.textContent).toContain('Base case: Campus')
  })

  it('a project with no base case says nothing about one', async () => {
    renderStrip()
    const strip = await screen.findByTestId('headline-strip')
    expect(strip.textContent).not.toContain('Base case')
    expect(comparisonBasis(null)).toBeUndefined()
    // A base the user cannot open has no name to show.
    expect(comparisonBasis({ ...CASE, base_name: null } as never)).toBeUndefined()
  })

  it('a card without a figure never shows a change', () => {
    expect(headlineDelta(card('lcoe', { delta: delta(1, 1) }))).toBeUndefined()
    expect(headlineDelta(card('lcoe', { value: 1, unit: 'EUR/MWh' }))).toBeUndefined()
    expect(headlineDelta(card('lcoe', { value: 52, unit: 'EUR/MWh', delta: delta(2, 4) }))).toBe('+4.0 % vs Base case')
    expect(headlineDelta(card('unserved_energy', { value: 1500, unit: 'MWh', delta: delta(-500, null) }))).toBe('−500 MWh vs Base case')
  })
})

describe('HeadlineStrip trend over the investment periods', () => {
  const TREND = [{ period: 2030, value: 41_000_000 }, { period: 2040, value: 37_500_000 }, { period: 2050, value: 33_000_000 }]

  it('draws the line with a text alternative and its first and last period in words', async () => {
    api.getHeadlineKpis.mockResolvedValue({ kind: 'network', cards: [
      card('system_cost', { value: 111_500_000, unit: 'currency', trend: TREND }),
      card('new_build_capital', { value: 2_210_000, unit: 'currency' }),
    ] })
    renderStrip()
    const trend = await screen.findByTestId('headline-system_cost-trend')
    expect(trend.getAttribute('role')).toBe('img')
    expect(trend.getAttribute('aria-label')).toBe('2030 to 2050: €41.0 M to €33.0 M')
    expect(trend.textContent).toBe('2030 · €41.0 M2050 · €33.0 M')
    expect(trend.querySelector('polyline')?.getAttribute('stroke')).toBe('var(--color-series-1)')
    expect(screen.queryByTestId('headline-new_build_capital-trend')).toBeNull()
  })

  it('a single-period run has no trend, so its tiles are as they were', async () => {
    renderStrip()
    await screen.findByTestId('headline-strip')
    expect(document.querySelector('[data-testid$="-trend"]')).toBeNull()
    expect(headlineTrend(card('system_cost', { value: 1, unit: 'currency', trend: TREND.slice(0, 1) }))).toBeNull()
    expect(headlineTrend(card('system_cost', { trend: TREND }))).toBeNull()
  })

  it('dims the trend with the figure when the solve is out of date', async () => {
    api.getHeadlineKpis.mockResolvedValue({ kind: 'network', cards: [
      card('system_cost', { value: 111_500_000, unit: 'currency', trend: TREND }),
    ] })
    renderStrip({ line: 'Latest solve · solved', stale: true })
    const trend = await screen.findByTestId('headline-system_cost-trend')
    expect(trend.parentElement?.className).toContain('opacity-60')
  })
})
