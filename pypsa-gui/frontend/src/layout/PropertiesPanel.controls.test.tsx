// control-strategy 02: a converter-coupled asset's form shows its Controls
// block after the asset card (design B3: General › Sizing › Controls), in
// the panel both Guided's drawer and Expert's Properties render.
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
const island = vi.hoisted(() => ({ island: vi.fn() }))
vi.mock('../api/campusElectrical', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/campusElectrical')>()
  return { ...actual, campusApi: { ...actual.campusApi, ...island } }
})
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
  island.island.mockResolvedValue({ config: null, study: null, controls: ['gfm_droop'],
    control_fields: { gfm_droop: ['droop_pct'] }, presets: {}, unit_controls: {
      batt: { kind: 'bess', control: 'gfm_droop', values: { pf_rated: 0.95, droop_pct: 4 },
        sources: { pf_rated: 'assumed', droop_pct: 'assumed' }, preset_scope: null } } })
  series.current.mockImplementation(async (_p: string, ct: string, attr: string, name: string) =>
    ct === 'storage_units' && attr === 'inflow'
      ? { exists: false, applicable: name === 'dam' } : { exists: false })
})
afterEach(() => {
  cleanup()
  useUIStore.setState({ currentProject: null, selectedComponent: null })
})

describe('the Controls block', () => {
  it('follows the asset card on a unit the island settings hold', async () => {
    renderPanel('batt')
    const block = await screen.findByTestId('controls-block')
    const cardRows = screen.getAllByTestId('row-label')                 // the asset card's rows
    expect(cardRows.length).toBeGreaterThan(0)
    expect(cardRows.at(-1)!.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(block.querySelector('[data-testid="row-label"]')).toBeNull()
  })

  it('is absent on a unit they do not hold', async () => {
    renderPanel('dam')
    await vi.waitFor(() => expect(island.island).toHaveBeenCalledWith('Demo'))
    await new Promise(r => setTimeout(r, 50))
    expect(screen.queryByTestId('controls-block')).toBeNull()
  })
})
