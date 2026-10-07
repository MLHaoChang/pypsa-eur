// Characterisation (Phase 2 plan Task 5.4): the overlay's period-effective
// capacity — a Solar2@2028 vintage counts only from 2028 — pinned before the
// rule moves to site3d/capacity.ts, which the 3D view shares.
import { render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CanvasResultsProvider, useCanvasResults } from './CanvasResultsContext'
import { networkApi } from '../api/network'
import { resultsApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'

vi.mock('../api/network')
vi.mock('../api/simulation')

const ts = { index: ['2026-01-01 00:00', '2028-01-01 00:00'], periods: [2026, 2028], columns: ['Solar2', 'Gas'], data: [[100, 5], [400, 5]], range: { from: 0, to: 1, total: 2 } }
let seen: Map<string, number> | null = null
function Probe() { const r = useCanvasResults(); seen = r.enabled ? r.byAssetGroupCapacity : null; return null }

function renderAt(idx: number) {
  useUIStore.setState({ resultsOverlayEnabled: true, resultsSnapshotIdx: idx, resultSource: 'lopf', currentProject: 'p' })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><CanvasResultsProvider><Probe /></CanvasResultsProvider></QueryClientProvider>)
}

beforeEach(() => {
  seen = null
  vi.mocked(resultsApi.getGeneratorResults).mockResolvedValue(ts as never)
  for (const f of ['getLoadResults', 'getLineResults', 'getLinkResults', 'getStorageDispatchResults', 'getStoreDispatchResults', 'getStorageResults', 'getStoreEnergyResults', 'getLineReactive'] as const) {
    vi.mocked(resultsApi[f]).mockResolvedValue(null as never)
  }
  vi.mocked(networkApi.getGenerators).mockResolvedValue([
    { name: 'Solar2', bus: 'B', carrier: 'solar', p_nom: 300, p_nom_opt: 593 },
    { name: 'Gas', bus: 'B', carrier: 'gas', p_nom: 50, p_nom_opt: 0 },
  ] as never)
  for (const f of ['getLoads', 'getLines', 'getLinks', 'getStorageUnits', 'getStores'] as const) vi.mocked(networkApi[f]).mockResolvedValue([] as never)
  vi.mocked(networkApi.listVintageResults).mockResolvedValue({ results: { Generator: { Solar2: { capacity_field: 'p_nom', initial_capacity: 300, periods: [{ build_year: 2028, p_nom_opt: 293, p_nom_min: 0, p_nom_max: null }] } } } } as never)
})

describe('CanvasResultsProvider period-effective capacity', () => {
  it('a 2028 vintage is not counted in 2026', async () => {
    renderAt(0)
    await waitFor(() => expect(seen?.get('B|Renewables')).toBe(300))
  })
  it('and is counted from 2028', async () => {
    renderAt(1)
    await waitFor(() => expect(seen?.get('B|Renewables')).toBe(593))
  })
  it('without a vintage entry the capacity is p_nom_opt ?? p_nom (a 0 optimum included)', async () => {
    renderAt(1)
    await waitFor(() => expect(seen?.get('B|Thermal')).toBe(0))
  })
})
