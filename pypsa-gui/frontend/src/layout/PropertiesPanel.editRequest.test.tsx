// Obstacle 3 (guided-mode spec §2.7): the EH tagging tour's targets
// (`eh-bus-fields`, `eh-link-role`) render only in the Bus / Link card's Edit
// form. `uiStore.propertiesEditRequest` lets the tour's `prepare` (and the
// tour's `reveal` ids) put the card into Edit; the card consumes the request
// and clears it.
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
      propertiesEditRequest: 'Bus' })
    renderPanel()
    expect(await screen.findByTestId('eh-bus-fields')).toBeTruthy()
    expect(useUIStore.getState().propertiesEditRequest).toBeNull()
  })

  it('a request that arrives after mount also enters Edit', async () => {
    useUIStore.setState({ currentProject: 'Demo', selectedComponent: { type: 'Bus', name: 'hub' } })
    renderPanel()
    expect(await screen.findByTestId('props-edit-bus')).toBeTruthy()
    expect(screen.queryByTestId('eh-bus-fields')).toBeNull()
    act(() => useUIStore.getState().requestPropertiesEdit('Bus'))
    expect(await screen.findByTestId('eh-bus-fields')).toBeTruthy()
    expect(useUIStore.getState().propertiesEditRequest).toBeNull()
  })

  it('a Bus request leaves a Link card alone, a Link request opens it', async () => {
    useUIStore.setState({ currentProject: 'Demo', selectedComponent: { type: 'Link', name: 'imp' },
      propertiesEditRequest: 'Bus' })
    renderPanel()
    expect(await screen.findByTestId('props-edit-link')).toBeTruthy()
    expect(screen.queryByTestId('eh-link-role')).toBeNull()
    expect(useUIStore.getState().propertiesEditRequest).toBe('Bus')
    act(() => useUIStore.getState().requestPropertiesEdit('Link'))
    expect(await screen.findByTestId('eh-link-role')).toBeTruthy()
    expect(useUIStore.getState().propertiesEditRequest).toBeNull()
  })
})
