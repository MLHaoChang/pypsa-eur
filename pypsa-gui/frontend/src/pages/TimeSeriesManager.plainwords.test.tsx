// Time Series' own text uses plain field words: no PyPSA attribute name on any tab or sub-tab.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import TimeSeriesManager from './TimeSeriesManager'

vi.mock('../api/network', () => ({
  networkApi: new Proxy({
    getLoads: async () => [], getGenerators: async () => [], getLinks: async () => [],
    getLoadProfiles: async () => ({}), getGeneratorProfiles: async () => ({}),
    getLinkProfiles: async () => ({}), getSnapshots: async () => ({ count: 0 }),
    getTimeseries: async () => ({ index: [], columns: [], data: [] }),
  } as Record<string, unknown>, { get: (t, k: string) => t[k] ?? (async () => ({})) }),
}))
vi.mock('../api/profiles', async () => {
  const real = await vi.importActual<typeof import('../api/profiles')>('../api/profiles')
  return { ...real, profilesApi: { ...real.profilesApi, readiness: vi.fn(async () => []),
    weather: vi.fn(async () => ({ source: 'synthetic', year: 2023, year_source: 'snapshots',
      time_basis: { value: 'UTC', assumed: false } })), pending: vi.fn(async () => []) } }
})
vi.mock('../components/series/DefaultsReview', () => ({ default: () => <div /> }))
vi.mock('../pages/economics/SiteConnectionLink', () => ({ openSiteConnection: vi.fn() }))

const RAW = /p_set|p_max_pu|p_min_pu|marginal_cost/

beforeEach(() => useUIStore.setState({ currentProject: 'Site A', timeSeriesFocus: null, uiMode: 'expert' }))
afterEach(() => cleanup())

describe('Time Series chrome', () => {
  it('shows no raw attribute name on any tab or sub-tab', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { container } = render(<QueryClientProvider client={client}><TimeSeriesManager /></QueryClientProvider>)
    const text = () => container.textContent ?? ''
    for (const tab of ['Loads', 'Renewables', 'Conventional', 'Demand Response', 'Links']) {
      await userEvent.click(await screen.findByRole('button', { name: new RegExp(tab) }))
      expect(text(), tab).not.toMatch(RAW)
      const subs = ['Availability', 'Shedding availability', 'Must-run', 'Forced-shed minimum', 'Marginal cost', 'Activation cost']
      for (const sub of subs) {
        const b = screen.queryAllByRole('button').find(x => x.textContent?.startsWith(sub))
        if (!b) continue
        await userEvent.click(b)
        expect(text(), `${tab} › ${sub}`).not.toMatch(RAW)
      }
    }
  })
})
