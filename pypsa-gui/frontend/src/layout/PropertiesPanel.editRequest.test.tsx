// Obstacle 3 (guided-mode spec §2.7): the EH tagging tour's targets
// (`eh-bus-fields`, `eh-link-role`) render only in the Bus / Link card's Edit
// form. `uiStore.propertiesEditRequest` lets the tour's `prepare` (and the
// tour's `reveal` ids) put the card into Edit; the card consumes the request
// and clears it. P30 (B10): the request names the component
// (`{type, name}`); only the card showing that component takes it, and a
// selection change clears it.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import type { Bus, Link } from '../api/types'
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

const BUS = { name: 'hub', v_nom: 1, carrier: 'AC', x: 0, y: 0, country: '',
  unit: '', control: 'PQ', sub_network: '' } as Bus
const LINK = {
  name: 'imp', bus0: 'grid', bus1: 'hub', carrier: 'AC', p_nom: 100,
  p_nom_extendable: false, p_nom_min: 0, p_nom_max: null, p_min_pu: 0,
  p_max_pu: 1, efficiency: 1, marginal_cost: 0, capital_cost: 0,
  build_year: 2025, lifetime: null,
} as Link

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><PropertiesPanel /></QueryClientProvider>)
}

beforeEach(() => {
  for (const fn of ['getCarriers', 'getGenerators', 'getLoads', 'getStorageUnits', 'getStores'] as const) {
    vi.mocked(networkApi[fn]).mockReset().mockResolvedValue([])
  }
  vi.mocked(networkApi.getBuses).mockReset().mockResolvedValue([BUS])
  vi.mocked(networkApi.getLinks).mockReset().mockResolvedValue([LINK])
  vi.mocked(networkApi.getLinkProfiles).mockReset().mockResolvedValue({})
})

afterEach(() => {
  useUIStore.setState({ currentProject: null, selectedComponent: null, propertiesEditRequest: null })
})

describe('propertiesEditRequest', () => {
  it('puts the Bus card into Edit (EH fields rendered) and clears the request', async () => {
    useUIStore.setState({ currentProject: 'Demo', selectedComponent: { type: 'Bus', name: 'hub' },
      propertiesEditRequest: { type: 'Bus', name: 'hub' } })
    renderPanel()
    expect(await screen.findByTestId('eh-bus-fields')).toBeTruthy()
    expect(useUIStore.getState().propertiesEditRequest).toBeNull()
  })

  it('a request that arrives after mount also enters Edit', async () => {
    useUIStore.setState({ currentProject: 'Demo', selectedComponent: { type: 'Bus', name: 'hub' } })
    renderPanel()
    expect(await screen.findByTestId('props-edit-bus')).toBeTruthy()
    expect(screen.queryByTestId('eh-bus-fields')).toBeNull()
    act(() => useUIStore.getState().requestPropertiesEdit({ type: 'Bus', name: 'hub' }))
    expect(await screen.findByTestId('eh-bus-fields')).toBeTruthy()
    expect(useUIStore.getState().propertiesEditRequest).toBeNull()
  })

  // P30 (B10) rewrite of the P22 test: the request now names a component, so
  // "a Bus request" is a request for a named bus, and it is cleared (not left
  // pending) when the selection moves to the Link.
  it('a Bus request leaves a Link card alone, a Link request opens it', async () => {
    useUIStore.setState({ currentProject: 'Demo', selectedComponent: { type: 'Link', name: 'imp' },
      propertiesEditRequest: { type: 'Bus', name: 'hub' } })
    renderPanel()
    expect(await screen.findByTestId('props-edit-link')).toBeTruthy()
    expect(screen.queryByTestId('eh-link-role')).toBeNull()
    expect(useUIStore.getState().propertiesEditRequest).toEqual({ type: 'Bus', name: 'hub' })
    act(() => useUIStore.getState().requestPropertiesEdit({ type: 'Link', name: 'imp' }))
    expect(await screen.findByTestId('eh-link-role')).toBeTruthy()
    expect(useUIStore.getState().propertiesEditRequest).toBeNull()
  })

  it('a request for bus A is not consumed by bus B and is cleared when the selection changes', async () => {
    const B = { ...BUS, name: 'other' } as Bus
    vi.mocked(networkApi.getBuses).mockResolvedValue([BUS, B])
    useUIStore.setState({ currentProject: 'Demo', selectedComponent: { type: 'Bus', name: 'other' },
      propertiesEditRequest: { type: 'Bus', name: 'hub' } })
    renderPanel()
    expect(await screen.findByTestId('props-edit-bus')).toBeTruthy()
    await new Promise(r => setTimeout(r, 50))
    expect(screen.queryByTestId('eh-bus-fields')).toBeNull()          // bus B stays in view mode
    expect(useUIStore.getState().propertiesEditRequest).toEqual({ type: 'Bus', name: 'hub' })
    act(() => useUIStore.getState().setSelectedComponent({ type: 'Link', name: 'imp' }))
    expect(useUIStore.getState().propertiesEditRequest).toBeNull()     // cleared by the move
    act(() => useUIStore.getState().setSelectedComponent({ type: 'Bus', name: 'hub' }))
    expect(await screen.findByTestId('props-edit-bus')).toBeTruthy()
    await new Promise(r => setTimeout(r, 50))
    expect(screen.queryByTestId('eh-bus-fields')).toBeNull()          // nothing left to replay
  })

  it('a request for Link A is not consumed by Link B', async () => {
    useUIStore.setState({ currentProject: 'Demo', selectedComponent: { type: 'Link', name: 'imp' },
      propertiesEditRequest: { type: 'Link', name: 'other' } })
    renderPanel()
    expect(await screen.findByTestId('props-edit-link')).toBeTruthy()
    await new Promise(r => setTimeout(r, 50))
    expect(screen.queryByTestId('eh-link-role')).toBeNull()
    expect(useUIStore.getState().propertiesEditRequest).toEqual({ type: 'Link', name: 'other' })
  })

  it('selecting the requested component keeps the request (prepare selects, then asks)', () => {
    useUIStore.setState({ selectedComponent: null, propertiesEditRequest: null })
    useUIStore.getState().requestPropertiesEdit({ type: 'Bus', name: 'hub' })
    useUIStore.getState().setSelectedComponent({ type: 'Bus', name: 'hub' })
    expect(useUIStore.getState().propertiesEditRequest).toEqual({ type: 'Bus', name: 'hub' })
  })
})
