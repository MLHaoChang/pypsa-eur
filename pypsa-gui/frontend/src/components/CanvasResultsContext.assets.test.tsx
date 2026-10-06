// Plan 1 A2: the overlay's per-component map (`byAsset`), one entry per
// Generator, Load, StorageUnit, Store and Link, built from the same chunks
// the group maps use — so an individual asset node shows for one component
// what the group bubble shows for the group. One of each class, at one
// snapshot, with stubbed chunks.
import { render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CanvasResultsProvider, useCanvasResults, type AssetOverlay } from './CanvasResultsContext'
import { networkApi } from '../api/network'
import { resultsApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'

vi.mock('../api/network')
vi.mock('../api/simulation')

const chunk = (columns: string[], row: number[]) =>
  ({ index: ['2026-01-01 00:00'], columns, data: [row], range: { from: 0, to: 0, total: 1 } })

let seen: Map<string, AssetOverlay> | null = null
function Probe() { const r = useCanvasResults(); seen = r.enabled ? r.byAsset : null; return null }

function renderOverlay() {
  useUIStore.setState({ resultsOverlayEnabled: true, resultsSnapshotIdx: 0, resultSource: 'lopf', currentProject: 'p' })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><CanvasResultsProvider><Probe /></CanvasResultsProvider></QueryClientProvider>)
}

beforeEach(() => {
  seen = null
  vi.mocked(resultsApi.getGeneratorResults).mockResolvedValue(chunk(['PV'], [7.5]) as never)
  vi.mocked(resultsApi.getLoadResults).mockResolvedValue(chunk(['Hall'], [4]) as never)
  vi.mocked(resultsApi.getStorageDispatchResults).mockResolvedValue(chunk(['BESS'], [-2]) as never)
  vi.mocked(resultsApi.getStorageResults).mockResolvedValue(chunk(['BESS'], [30]) as never)           // 30 MWh of 10 × 4
  vi.mocked(resultsApi.getStoreDispatchResults).mockResolvedValue(chunk(['Tank'], [1]) as never)
  vi.mocked(resultsApi.getStoreEnergyResults).mockResolvedValue(chunk(['Tank'], [100]) as never)      // 100 MWh of 500
  vi.mocked(resultsApi.getLinkResults).mockResolvedValue(chunk(['Ely'], [3]) as never)
  for (const f of ['getLineResults', 'getLineReactive', 'getTransformerResults'] as const) {
    vi.mocked(resultsApi[f]).mockResolvedValue(null as never)
  }
  vi.mocked(networkApi.getGenerators).mockResolvedValue([{ name: 'PV', bus: 'B', carrier: 'solar', p_nom: 12 }] as never)
  vi.mocked(networkApi.getLoads).mockResolvedValue([{ name: 'Hall', bus: 'B', carrier: 'AC', p_set: 8 }] as never)
  vi.mocked(networkApi.getStorageUnits).mockResolvedValue([{ name: 'BESS', bus: 'B', carrier: 'battery', p_nom: 10, max_hours: 4 }] as never)
  vi.mocked(networkApi.getStores).mockResolvedValue([{ name: 'Tank', bus: 'H', carrier: 'H2', e_nom: 500 }] as never)
  vi.mocked(networkApi.getLinks).mockResolvedValue([{ name: 'Ely', bus0: 'B', bus1: 'H', carrier: 'H2', p_nom: 5, p_nom_opt: 6 }] as never)
  for (const f of ['getLines', 'getTransformers'] as const) vi.mocked(networkApi[f]).mockResolvedValue([] as never)
  vi.mocked(networkApi.listVintageResults).mockResolvedValue({ results: {} } as never)
})

describe('CanvasResultsProvider byAsset', () => {
  it('maps one of each class from the same chunks as the group maps', async () => {
    renderOverlay()
    await waitFor(() => expect(seen?.size).toBe(5))
    expect(seen!.get('Generator:PV')).toEqual({ cls: 'Generator', dispatchMW: 7.5, socPct: null, capacity: 12 })
    expect(seen!.get('Load:Hall')).toEqual({ cls: 'Load', dispatchMW: 4, socPct: null, capacity: null })
    expect(seen!.get('StorageUnit:BESS')).toEqual({ cls: 'StorageUnit', dispatchMW: -2, socPct: 75, capacity: 10 })
    expect(seen!.get('Store:Tank')).toEqual({ cls: 'Store', dispatchMW: 1, socPct: 20, capacity: 500 })
    // a Link's capacity is p_nom_opt when the solve set it
    expect(seen!.get('Link:Ely')).toEqual({ cls: 'Link', dispatchMW: 3, socPct: null, capacity: 6 })
  })

  it('a component without a row at the snapshot still has its capacity, with null dispatch', async () => {
    vi.mocked(resultsApi.getGeneratorResults).mockResolvedValue(chunk(['Other'], [1]) as never)
    renderOverlay()
    await waitFor(() => expect(seen?.get('Generator:PV')).toBeTruthy())
    expect(seen!.get('Generator:PV')).toEqual({ cls: 'Generator', dispatchMW: null, socPct: null, capacity: 12 })
  })
})
