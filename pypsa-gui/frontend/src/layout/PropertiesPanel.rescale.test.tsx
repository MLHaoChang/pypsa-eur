// Regression coverage for one of the four write paths Finding 1 (2026-07-31
// review) found silently discarding a backend-computed rescale preview: the
// Properties panel's Bus form. Before the lift, `updateMut`'s onSuccess took
// no parameter at all — `data.rescale` was computed by the backend and never
// even read. Renders PropertiesPanel + the single app-wide RescaleDialogHost
// side by side (as App.tsx does) so a passing test proves the FULL path:
// BusPanel's mutation feeds the shared store, and the dialog picks it up.
// Follows the render/mock/userEvent recipe in IssuesPanel.test.tsx.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { useRescaleStore } from '../store/rescaleStore'
import type { Bus } from '../api/types'
import type { RescalePreview } from '../utils/rescale'
import PropertiesPanel from './PropertiesPanel'
import RescaleDialogHost from '../components/RescaleDialogHost'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: vi.fn(),
      getCarriers: vi.fn(),
      getGenerators: vi.fn(),
      getLoads: vi.fn(),
      getStorageUnits: vi.fn(),
      getStores: vi.fn(),
      getLinks: vi.fn(),
      updateBus: vi.fn(),
      rescaleImpedances: vi.fn(),
    },
  }
})

const BUS1: Bus = {
  name: 'Bus1', v_nom: 380, carrier: 'AC', x: 6.96, y: 50.9,
  country: 'DE', unit: '', control: 'PQ', sub_network: '',
}

const MATERIAL_RESCALE: RescalePreview = {
  name: 'L1',
  old_length: 1.78, new_length: 476.3,
  old: { r: 3.0, x: 17.5, b: 0.00015 },
  new: { r: 802.7, x: 4682.6, b: 0.04013 },
  rel_change: 266.6,
  skipped_reason: null,
}

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <PropertiesPanel />
      <RescaleDialogHost />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(networkApi.getBuses).mockReset().mockResolvedValue([BUS1])
  vi.mocked(networkApi.getCarriers).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getGenerators).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getLoads).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getStorageUnits).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getStores).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getLinks).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.updateBus).mockReset()
  vi.mocked(networkApi.rescaleImpedances).mockReset()
  useUIStore.setState({ currentProject: 'Demo', selectedComponent: { type: 'Bus', name: 'Bus1' } })
  useRescaleStore.setState({ pendingRescale: [], placementActive: false })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null, selectedComponent: null })
  useRescaleStore.setState({ pendingRescale: [], placementActive: false })
})

describe('PropertiesPanel Bus form — rescale preview wiring', () => {
  it('does NOT call rescaleImpedances just from rendering', async () => {
    renderApp()
    await screen.findByText('Bus1')
    expect(networkApi.rescaleImpedances).not.toHaveBeenCalled()
  })

  it('feeds a material rescale preview from updateBus into the shared dialog', async () => {
    vi.mocked(networkApi.updateBus).mockResolvedValue({ name: 'Bus1', rescale: [MATERIAL_RESCALE] })
    renderApp()

    await userEvent.click(await screen.findByRole('button', { name: /edit bus/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^save$/i }))

    // The dialog is rendered by the SEPARATE RescaleDialogHost instance,
    // proving the preview crossed from BusPanel's mutation into the shared
    // store rather than being dropped where it used to be.
    expect(await screen.findByText('L1')).toBeDefined()
    expect(networkApi.rescaleImpedances).not.toHaveBeenCalled()
  })

  it('auto-applies an immaterial rescale preview without opening the dialog', async () => {
    const immaterial: RescalePreview = { ...MATERIAL_RESCALE, rel_change: 0.01 }
    vi.mocked(networkApi.updateBus).mockResolvedValue({ name: 'Bus1', rescale: [immaterial] })
    vi.mocked(networkApi.rescaleImpedances).mockResolvedValue({ updated: 1, skipped: [] })
    renderApp()

    await userEvent.click(await screen.findByRole('button', { name: /edit bus/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^save$/i }))

    await vi.waitFor(() => expect(networkApi.rescaleImpedances).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('L1')).toBeNull()
  })
})
