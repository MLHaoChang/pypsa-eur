// An integer (MIP) solve has no marginal prices: the backend withholds every
// figure priced at them as null with price_limit "mip_duals_not_marginal".
// The tab must say "Not available (integer solve)" — never 0, "—" or NaN —
// and no total may silently sum the withheld part.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import Economics from './Economics'
import { withheldCell, withheldTotalNote } from './sectionWords'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: { ...actual.resultsApi, getAssetEconomics: vi.fn(), getLcoh: vi.fn() },
  }
})

const WITHHELD = withheldCell('mip_duals_not_marginal')

function mipPayload() {
  return {
    currency: 'EUR',
    is_multi_period: false,
    capital_costs_available: true,
    price_limit: 'mip_duals_not_marginal',
    periods: [],
    generators: [{
      name: 'Gas', bus: 'B1', carrier: 'gas', p_nom_opt_mw: 100, energy_mwh: 500,
      capacity_factor: 0.5, revenue_eur: null, vom_cost_eur: 100, fixed_cost_eur: 900,
      fom_cost_eur: 0, net_profit_eur: null, lcoe_eur_per_mwh: 2, avg_price_eur_per_mwh: null,
      by_period: [],
    }],
    storage_units: [{
      name: 'Bat', bus: 'B1', carrier: 'battery', p_nom_opt_mw: 10, max_hours: 4,
      energy_capacity_mwh: 40, round_trip_efficiency: 0.9, discharge_mwh: 50, charge_mwh: 60,
      discharge_revenue_eur: null, charge_cost_eur: null, vom_cost_eur: 0, fixed_cost_eur: 300,
      fom_cost_eur: 0, net_profit_eur: null, lcos_eur_per_mwh: null, spread_eur_per_mwh: null,
      avg_discharge_price_eur_per_mwh: null, avg_charge_price_eur_per_mwh: null, by_period: [],
    }],
    stores: [],
    links: [],
    reading_notes: [],
  }
}

afterEach(() => cleanup())
beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(resultsApi.getLcoh).mockReset().mockResolvedValue({ rows: [], total: null, currency: 'EUR' })
  vi.mocked(resultsApi.getAssetEconomics).mockReset().mockResolvedValue(mipPayload() as never)
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><Economics /></QueryClientProvider>)
}

const kpi = (label: string) => screen.getAllByText(label)
  .map(el => el.closest('[data-state]'))
  .find(Boolean) as HTMLElement

it('shows withheld price-based KPIs as "Not available (integer solve)", never 0 or NaN', async () => {
  renderPage()
  await screen.findAllByText('Total revenue')
  for (const label of ['Total revenue', 'Net profit', 'Charge cost (storage)', 'Avg capture price']) {
    const tile = kpi(label)
    expect(tile.textContent, label).toContain(WITHHELD)
    // The Kpi's own unavailable state (as Dispatch): the reason, and no figure at all.
    expect(tile.getAttribute('data-state'), label).toBe('unavailable')
    expect(tile.querySelector('[data-kpi-value]'), label).toBeNull()
  }
  expect(document.body.textContent).not.toContain('NaN')
  // Not "0 profitable · 0 loss-making": profitability is not known.
  expect(document.body.textContent).not.toMatch(/0 profitable/)
})

it('labels the variable-cost total that leaves out the withheld charge cost', async () => {
  renderPage()
  await screen.findAllByText('Variable cost')
  expect(kpi('Variable cost').textContent).toContain(withheldTotalNote('charge cost', 'mip_duals_not_marginal'))
})

it('withholds the table cells and the group totals', async () => {
  renderPage()
  await screen.findAllByText('Total revenue')
  fireEvent.click(screen.getByText('Storage'))
  const bat = screen.getByText('Bat').closest('tr')!
  // revenue, charge cost, net profit, LCOS, spread: all withheld.
  expect(bat.textContent!.split(WITHHELD).length - 1).toBe(5)
  const storageTotal = screen.getByText('Storage').closest('tr')!
  expect(storageTotal.textContent).toContain(WITHHELD)
  expect(storageTotal.textContent).not.toContain('NaN')
  // A generator LCOE is not price-based: it stays a number.
  fireEvent.click(screen.getByText('Thermal'))
  const gasRow = screen.getAllByText('Gas').map(el => el.closest('tr')).find(tr => tr?.textContent?.includes('B1') || tr?.textContent?.includes('gas'))!
  expect(gasRow.textContent).toContain('2.00 €/MWh')
})

// GK B2 F1 (S1): a group total over rows with a withheld revenue is withheld, never
// the other rows' sum (a partial sum) or 0 — both for a mixed group and for a group
// whose every row is withheld (a MIP's normal case).
it.each([
  ['a mixed group', [null, 812]],
  ['an all-withheld group', [null, null]],
])('withholds the Thermal group revenue for %s', async (_case, revenues) => {
  const base = mipPayload()
  const gen = base.generators[0]
  vi.mocked(resultsApi.getAssetEconomics).mockResolvedValue({
    ...base,
    generators: revenues.map((rev, i) => ({
      ...gen, name: `G${i}`, revenue_eur: rev, net_profit_eur: rev === null ? null : rev - 1000,
    })),
  } as never)
  renderPage()
  await screen.findAllByText('Total revenue')
  const thermal = screen.getByText('Thermal').closest('tr')!
  expect(thermal.textContent).toContain(WITHHELD)
  expect(thermal.textContent).not.toMatch(/812|€0(?![.\d])|NaN/)
})
