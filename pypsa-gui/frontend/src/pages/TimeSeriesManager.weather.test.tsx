// The Renewables tab (RP-04b): the weather bar and a one-click Generate only on
// a row that may be written.
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
  await screen.findByTestId('weather-bar')
}

describe('Renewables tab', () => {
  it('shows the weather bar, and Generate only where a series may be written', async () => {
    await openRenewables()
    expect(await screen.findByTestId('generate-pv_ready')).toBeTruthy()
    expect(screen.queryByTestId('generate-pv_noloc')).toBeNull()
    expect(screen.queryByTestId('generate-pv_file')).toBeNull()
  })

  it('one click generates with the project\'s weather choice and applies it', async () => {
    prof.generate.mockResolvedValue({ id: 'j1' })
    prof.pending.mockResolvedValue({ pending: [{ job_id: 'j1' }], jobs: [], keep: 10 })
    prof.applyBatch.mockResolvedValue({ applied: [{}], count: 1, skipped: [] })
    await openRenewables()
    await userEvent.click(await screen.findByTestId('generate-pv_ready'))
    expect((await screen.findByTestId('generate-notice')).textContent).toContain('pv_ready: profile generated and applied')
    expect(prof.generate).toHaveBeenCalledWith('Site A', { points: [], from_generators: ['pv_ready'], technology: 'solar' })
  })
})
