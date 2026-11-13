// The drawer's header and its size fields say the same unit: on a tonne bus both read t/h, never MW.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

import schemas from '../test/fixtures/assetSchema.json'

// The real hook (the setup file stubs it from the fixture), fed by the fixture and a mocked units read.
vi.unmock('../hooks/useAssetSchema')
const units = vi.hoisted(() => vi.fn())
vi.mock('../api/assetSchema', async (orig) => {
  const actual = await orig<typeof import('../api/assetSchema')>()
  return {
    ...actual,
    assetSchemaApi: {
      ...actual.assetSchemaApi, units,
      get: async (c: string) => (schemas as unknown as Record<string, unknown>)[c],
    },
  }
})
vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: vi.fn(async () => []),
      getCarriers: vi.fn(async () => []),
      getGeneratorProfiles: vi.fn(async () => ({})),
      getCatalog: vi.fn(async (component: string) => ({ component, attributes: [] })),
      listTimeseries: vi.fn(async () => []),
    },
  }
})
vi.mock('../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulation')>()
  return {
    ...actual,
    simulationApi: { ...actual.simulationApi, getSolverConfig: vi.fn(async () => ({ mode: 'lopf' })) },
  }
})

import { GeneratorCard, StoreCard } from './PropertiesPanel'

const GEN = {
  name: 'crusher', bus: 'ore', carrier: 'ore', control: 'PQ', p_nom: 120, p_nom_extendable: false,
  p_nom_min: 0, p_nom_max: 80, p_min_pu: 0, p_max_pu: 1, efficiency: 1, marginal_cost: 0,
  curtailment_cost: 0, lifetime: 40, build_year: 2027, committable: false,
} as never

async function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(nk('Demo', 'generators'), [GEN])
  render(<QueryClientProvider client={client}><GeneratorCard gen={GEN} onRename={() => {}} /></QueryClientProvider>)
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }); units.mockReset() })
afterEach(() => { useUIStore.setState({ currentProject: null }) })

describe('a generator on a tonne bus', () => {
  it('reads t/h in the header and the size field', async () => {
    units.mockResolvedValue({ component: 'Generator', name: 'crusher', units: { p_nom: 't/h', p_nom_max: 't/h' } })
    await mount()
    expect(await screen.findByText(/120 t\/h/)).toBeTruthy()
    await userEvent.click(await screen.findByRole('button', { name: /edit/i }))
    expect(await screen.findByText(/Capacity \(t\/h\)/)).toBeTruthy()
    expect(screen.queryByText(/120 MW/)).toBeNull()
  })

  it('an energy-bus generator keeps MW', async () => {
    units.mockResolvedValue({ component: 'Generator', name: 'crusher', units: {} })
    await mount()
    expect(await screen.findByText(/120 MW/)).toBeTruthy()
    await userEvent.click(await screen.findByRole('button', { name: /edit/i }))
    expect(await screen.findByText(/Capacity \(MW\)/)).toBeTruthy()
  })
})

const STORE = {
  name: 'tank', bus: 'h2', carrier: 'H2', e_nom: 336, e_nom_extendable: false, e_nom_min: 0, e_nom_max: 1000,
  e_cyclic: false, standing_loss: 0, lifetime: 30, build_year: 2027,
} as never

async function mountStore() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(nk('Demo', 'stores'), [STORE])
  render(<QueryClientProvider client={client}><StoreCard store={STORE} onRename={() => {}} /></QueryClientProvider>)
}

describe('a hydrogen store', () => {
  it('on a tonne bus reads t and drops the MWh-based kg H₂ tail', async () => {
    units.mockResolvedValue({ component: 'Store', name: 'tank', units: { e_nom: 't', e_nom_max: 't', e_initial: 't' } })
    await mountStore()
    expect(await screen.findByText(/336 t\b/)).toBeTruthy()
    expect(screen.queryByText(/kg H₂/)).toBeNull()
    await userEvent.click(await screen.findByRole('button', { name: /edit/i }))
    expect(await screen.findByText(/Energy capacity \(t\)/)).toBeTruthy()
    expect(await screen.findByText(/Initial energy \(t\)/)).toBeTruthy()
    expect(screen.queryByText(/29\.8 kg/)).toBeNull()
  })

  it('on an energy bus keeps MWh and the kg H₂ tail', async () => {
    units.mockResolvedValue({ component: 'Store', name: 'tank', units: {} })
    await mountStore()
    expect(await screen.findByText(/336 MWh.*10000 kg H₂/)).toBeTruthy()
  })
})
