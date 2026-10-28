// The Renewables tab (RP-04c): the Profile Studio link is for Expert only.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import TimeSeriesManager from './TimeSeriesManager'

vi.mock('../api/network', () => ({
  networkApi: new Proxy({
    getLoads: async () => [],
    getGenerators: async () => [
      { name: 'pv_ready', bus: 'b1', carrier: 'solar', p_nom: 20 },
      { name: 'pv_noloc', bus: 'b2', carrier: 'solar', p_nom: 20 },
      { name: 'pv_file', bus: 'b1', carrier: 'solar', p_nom: 20 }],
    getLinks: async () => [],
    getLoadProfiles: async () => ({}),
    getGeneratorProfiles: async () => ({
      pv_ready: { category: 'renewable' }, pv_noloc: { category: 'renewable' }, pv_file: { category: 'renewable' } }),
    getLinkProfiles: async () => ({}),
    getSnapshots: async () => ({ count: 0 }),
    getTimeseries: async () => ({ index: [], columns: [], data: [] }),
  } as Record<string, unknown>, { get: (t, k: string) => t[k] ?? (async () => ({})) }),
}))
const prof = vi.hoisted(() => ({ readiness: vi.fn(), weather: vi.fn(), generate: vi.fn(), pending: vi.fn(), applyBatch: vi.fn() }))
vi.mock('../api/profiles', async () => {
  const real = await vi.importActual<typeof import('../api/profiles')>('../api/profiles')
  return { ...real, profilesApi: { ...real.profilesApi, ...prof } }
})
vi.mock('../components/series/DefaultsReview', () => ({ default: () => <div data-testid="defaults-review" /> }))
vi.mock('../pages/economics/SiteConnectionLink', () => ({ openSiteConnection: vi.fn() }))

const ready = (o: object) => ({ generator: 'pv_ready', technology: 'solar', bus: 'b1', lon: 8, lat: 50,
  coord_source: 'bus', profile: 'none', ...o })

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><TimeSeriesManager /></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  prof.readiness.mockResolvedValue([ready({}),
    ready({ generator: 'pv_noloc', bus: 'b2', coord_source: 'missing', lon: null, lat: null }),
    ready({ generator: 'pv_file', profile: 'uploaded' })])
  prof.weather.mockResolvedValue({ source: 'synthetic', year: 2023, year_source: 'snapshots',
    time_basis: { value: 'UTC', assumed: false } })
  useUIStore.setState({ currentProject: 'Site A', timeSeriesFocus: null, uiMode: 'expert' })
})
afterEach(() => cleanup())

async function openRenewables() {
  renderIt()
  await userEvent.click(await screen.findByRole('button', { name: /Renewables/ }))
  await screen.findByTestId('readiness-pv_ready')
}

describe('Renewables tab', () => {
  it('offers Tune only for located Expert rows and opens the existing dialog', async () => {
    await openRenewables()
    expect(screen.queryByTestId('tune-pv_noloc')).toBeNull()
    await userEvent.click(screen.getByTestId('tune-pv_ready'))
    expect(screen.getByRole('dialog', { name: 'Tune the profile of pv_ready' })).toBeTruthy()
  })

  it('offers no Tune entry in Guided', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    await openRenewables()
    expect(screen.queryByTestId('tune-pv_ready')).toBeNull()
  })
  it('links to Profile Studio in Expert', async () => {
    await openRenewables()
    expect(screen.getByTestId('ts-open-profile-studio')).toBeTruthy()
  })

  it('has no Profile Studio link in Guided', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    await openRenewables()
    expect(screen.queryByTestId('ts-open-profile-studio')).toBeNull()
  })
})
