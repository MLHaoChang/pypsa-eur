// A storage unit's Inflow editor shows only where an inflow belongs, by the
// server's verdict (`current.inflow_applies`): a hydro unit gets it, a
// battery does not (UX review of #586).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import type { StorageUnit } from '../api/types'
import PropertiesPanel from './PropertiesPanel'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: vi.fn(), getCarriers: vi.fn(), getGenerators: vi.fn(),
      getLoads: vi.fn(), getStorageUnits: vi.fn(), getStores: vi.fn(),
      getLinks: vi.fn(), getLinkProfiles: vi.fn(),
    },
  }
})
const series = vi.hoisted(() => ({ current: vi.fn() }))
vi.mock('../api/profiles', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/profiles')>()
  return { ...actual, seriesApi: { ...actual.seriesApi, ...series } }
})

const unit = (name: string, carrier: string) => ({
  name, bus: 'site', carrier, p_nom: 10, max_hours: 4,
}) as unknown as StorageUnit

function renderPanel(name: string) {
  useUIStore.setState({ currentProject: 'Demo', selectedComponent: { type: 'StorageUnit', name } })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><PropertiesPanel /></QueryClientProvider>)
}

beforeEach(() => {
  for (const fn of ['getBuses', 'getCarriers', 'getGenerators', 'getLoads', 'getStores', 'getLinks'] as const) {
    vi.mocked(networkApi[fn]).mockReset().mockResolvedValue([])
  }
  vi.mocked(networkApi.getLinkProfiles).mockReset().mockResolvedValue({})
  vi.mocked(networkApi.getStorageUnits).mockReset()
    .mockResolvedValue([unit('dam', 'hydro'), unit('batt', 'battery')])
  series.current.mockImplementation(async (_p: string, ct: string, attr: string, name: string) =>
    ct === 'storage_units' && attr === 'inflow'
      ? { exists: false, applicable: name === 'dam' } : { exists: false })
})
afterEach(() => {
  cleanup()
  useUIStore.setState({ currentProject: null, selectedComponent: null })
})

describe('the Inflow section', () => {
  it('shows on a hydro unit', async () => {
    renderPanel('dam')
    expect(await screen.findByText('Inflow')).toBeTruthy()
  })

  it('is absent on a battery', async () => {
    renderPanel('batt')
    await vi.waitFor(() => expect(series.current).toHaveBeenCalledWith('Demo', 'storage_units', 'inflow', 'batt'))
    await new Promise(r => setTimeout(r, 50))
    expect(screen.queryByText('Inflow')).toBeNull()
  })
})
