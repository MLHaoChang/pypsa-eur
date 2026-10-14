// M3: the Summary tab renders four cards from the endpoints the detailed
// tabs use, links each to its tab, and never shows an absent quantity as
// zero (no VoLL: "not modelled"; no cap, margin or target: no "met").
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { ResultsFilterProvider } from './filterContext'

const api = vi.hoisted(() => ({
  getCostBreakdown: vi.fn(), getEmissions: vi.fn(), getLostLoad: vi.fn(), getReserveMargin: vi.fn(),
  getAdequacy: vi.fn(), getStatistics: vi.fn(), getStatus: vi.fn(),
}))
vi.mock('../../api/simulation', async (orig) => {
  const actual = await orig<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: { ...actual.resultsApi, getCostBreakdown: api.getCostBreakdown, getEmissions: api.getEmissions,
      getLostLoad: api.getLostLoad, getReserveMargin: api.getReserveMargin, getAdequacy: api.getAdequacy,
      getStatistics: api.getStatistics },
    simulationApi: { ...actual.simulationApi, getStatus: api.getStatus },
  }
})

import SummaryTab from './SummaryTab'

const COST = { capex: 9e6, capex_expansion: 3e6, opex: 1e6, total: 1e7, by_period: [] }
const EMISSIONS = { total_tCO2: 2500, caps: [{ name: 'co2_cap', investment_period: null, scope: 'horizon',
  cap_tCO2: 2500, used_tCO2: 2500, shadow_price_eur_per_tCO2: 85, slack_tCO2: 0, binding: true }], by_period: [] }
const STATS = [
  { level_0: 'Generator', level_1: 'solar', 'Optimal Capacity': 120, 'Installed Capacity': 20 },
  { level_0: 'Generator', level_1: 'gas', 'Optimal Capacity': 80, 'Installed Capacity': 80 },
]

function renderTab(onOpenTab = vi.fn(), period: number | null = null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <ResultsFilterProvider value={{ fromIso: null, toIso: null, selectedPeriod: period }}>
        <SummaryTab onOpenTab={onOpenTab} />
      </ResultsFilterProvider>
    </QueryClientProvider>,
  )
  return onOpenTab
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  api.getStatus.mockResolvedValue({ running: false, status: 'ok', condition: 'optimal', objective: 1.2e7, solve_time: 3.4 })
  api.getCostBreakdown.mockResolvedValue(COST)
  api.getEmissions.mockResolvedValue(EMISSIONS)
  api.getLostLoad.mockResolvedValue({ total_mwh: 12, total_cost_eur: 36_000, voll_eur_per_mwh: 3000,
    shed_hours: { total: 4, by_period: {} } })
  api.getReserveMargin.mockResolvedValue(null)
  api.getAdequacy.mockResolvedValue(null)
  api.getStatistics.mockResolvedValue(STATS)
})
afterEach(() => { cleanup(); vi.clearAllMocks(); useUIStore.setState({ currentProject: null }) })

describe('the Summary tab (M3)', () => {
  it('shows the cost, its split and the new-build capital', async () => {
    renderTab()
    const card = await screen.findByTestId('summary-cost')
    // UX overhaul S3b: the headline follows the KPI tile standard (R1).
    await within(card).findByText('€9.00 M')
    expect(card.querySelector('[data-kpi-value="€10.0 M"]')).not.toBeNull()
    expect(within(card).getByText('€1.00 M')).toBeTruthy()
    expect(within(card).getByText('€3.00 M')).toBeTruthy()
  })

  it('shows the emissions and a binding cap with its shadow price', async () => {
    renderTab()
    const card = await screen.findByTestId('summary-emissions')
    await within(card).findByText(/binding/)
    expect(card.querySelector('[data-kpi-value="2.50 kt CO₂"]')).not.toBeNull()
    expect(within(card).getByText(/binding · €85\.00\/t/)).toBeTruthy()
  })

  it('shows unserved energy, its cost at VoLL and the shed hours', async () => {
    renderTab()
    const card = await screen.findByTestId('summary-adequacy')
    await within(card).findByText('€36.00 k')
    expect(card.querySelector('[data-kpi-value="12.0 MWh"]')).not.toBeNull()
    expect(within(card).getByText('€36.00 k')).toBeTruthy()
    expect(within(card).getByText('4.0')).toBeTruthy()
  })

  it('no VoLL set reads "not modelled", never 0 MWh; no margin or target, no verdict', async () => {
    api.getLostLoad.mockResolvedValue(null)
    renderTab()
    const card = await screen.findByTestId('summary-adequacy')
    expect(await within(card).findByText(/not modelled/)).toBeTruthy()
    expect(within(card).queryByText(/MWh/)).toBeNull()
    expect(within(card).queryByText('Reserve margin')).toBeNull()
  })

  it('a reserve margin the solve enforced says whether it was met', async () => {
    api.getReserveMargin.mockResolvedValue({ margin: 0.15, by_period: [{ period: 2030, met: false, binding: true }] })
    renderTab()
    const card = await screen.findByTestId('summary-adequacy')
    expect(await within(card).findByText('not met')).toBeTruthy()
  })

  it('lists the key capacities with what this solve built', async () => {
    renderTab()
    const row = await screen.findByTestId('summary-cap-Generator-solar')
    expect(row.textContent).toContain('120 MW')
    expect(row.textContent).toContain('100 MW')
    expect(screen.getByTestId('summary-cap-Generator-gas').textContent).toContain('—')
  })

  it('shows the solve status and the LP objective', async () => {
    renderTab()
    const status = await screen.findByTestId('summary-status')
    expect(within(status).getByText('optimal')).toBeTruthy()
    expect(status.textContent).toContain('€12.00 M')
  })

  it('each card opens its tab', async () => {
    const onOpen = renderTab()
    fireEvent.click(within(await screen.findByTestId('summary-emissions')).getByRole('button', { name: /Emissions/ }))
    fireEvent.click(within(screen.getByTestId('summary-adequacy')).getByRole('button', { name: /Adequacy/ }))
    fireEvent.click(within(screen.getByTestId('summary-cost')).getByRole('button', { name: /Capacity Expansion/ }))
    expect(onOpen.mock.calls.map(c => c[0])).toEqual(['emissions', 'adequacy', 'capex'])
  })

  it('before any solve it says there is nothing yet', async () => {
    for (const f of [api.getCostBreakdown, api.getEmissions, api.getStatistics, api.getLostLoad]) f.mockResolvedValue(null)
    renderTab()
    expect(await screen.findByTestId('summary-empty')).toBeTruthy()
  })
})

describe('the Summary tab follows the tile standard (UX overhaul S3b)', () => {
  it('titles its cards in sentence case, not forced capitals', async () => {
    renderTab()
    const title = await screen.findByText('Cost')
    expect(title.className).not.toMatch(/uppercase/)
  })
})
