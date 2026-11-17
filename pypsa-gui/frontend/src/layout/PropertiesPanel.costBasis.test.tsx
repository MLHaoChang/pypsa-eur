// 05d (GK B2 F2″, F3): an edit card claims the basis its costs were typed under, sends nothing until its
// fields are loaded, and a server refusal of the claim reads as the backend's sentence.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

import schemas from '../test/fixtures/assetSchema.json'

vi.unmock('../hooks/useAssetSchema')
const units = vi.hoisted(() => vi.fn())
const schemaGet = vi.hoisted(() => vi.fn())
vi.mock('../api/assetSchema', async (orig) => {
  const actual = await orig<typeof import('../api/assetSchema')>()
  return { ...actual, assetSchemaApi: { ...actual.assetSchemaApi, units, get: schemaGet } }
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
      updateGenerator: vi.fn(async () => ({ name: 'crusher' })),
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

import { networkApi } from '../api/network'
import { GeneratorCard } from './PropertiesPanel'

const GEN = {
  name: 'crusher', bus: 'ore', carrier: 'ore', control: 'PQ', p_nom: 120, p_nom_extendable: false,
  p_nom_min: 0, p_nom_max: 80, p_min_pu: 0, p_max_pu: 1, efficiency: 1, marginal_cost: 0,
  curtailment_cost: 0, lifetime: 40, build_year: 2027, committable: false,
} as never

const TONNE = {
  component: 'Generator', name: 'crusher', units: { p_nom: 't/h', p_nom_max: 't/h' },
  costs: {
    overnight_cost: { unit: 'EUR/(t/h)', scale: 1 }, capital_cost: { unit: 'EUR/(t/h)/yr', scale: 1 },
    fom_cost: { unit: 'EUR/(t/h)/yr', scale: 1 }, marginal_cost: { unit: 'EUR/t', scale: 1 },
    curtailment_cost: { unit: 'EUR/t', scale: 1 },
  },
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  client.setQueryData(nk('Demo', 'generators'), [GEN])
  render(<QueryClientProvider client={client}><GeneratorCard gen={GEN} onRename={() => {}} /></QueryClientProvider>)
}

async function openEdit() {
  await userEvent.click(await screen.findByRole('button', { name: /edit/i }))
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  units.mockReset()
  schemaGet.mockReset()
  schemaGet.mockImplementation(async (c: string) => (schemas as unknown as Record<string, unknown>)[c])
  vi.mocked(networkApi.updateGenerator).mockClear()
})
afterEach(() => { useUIStore.setState({ currentProject: null }) })

describe('an edit card claims the cost basis it shows', () => {
  it('a generator on a tonne bus saves with the tonne claim', async () => {
    units.mockResolvedValue(TONNE)
    mount()
    await openEdit()
    await screen.findByText(/Capacity \(t\/h\)/)
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(networkApi.updateGenerator).toHaveBeenCalled())
    expect(vi.mocked(networkApi.updateGenerator).mock.calls[0][2]).toBe('tonne')
  })

  it('a generator on an energy bus saves with the energy claim', async () => {
    units.mockResolvedValue({ component: 'Generator', name: 'crusher', units: {} })
    mount()
    await openEdit()
    await screen.findByText(/Capacity \(MW\)/)
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(networkApi.updateGenerator).toHaveBeenCalled())
    expect(vi.mocked(networkApi.updateGenerator).mock.calls[0][2]).toBe('energy')
  })
})

describe('Save waits for the fields (F3)', () => {
  it('is disabled while the schema is not loaded, so nothing typed is dropped', async () => {
    schemaGet.mockImplementation(() => new Promise(() => {}))
    units.mockResolvedValue({ component: 'Generator', name: 'crusher', units: {} })
    mount()
    await openEdit()
    const save = await screen.findByRole('button', { name: /^save$/i })
    expect((save as HTMLButtonElement).disabled).toBe(true)
    await userEvent.click(save)
    expect(networkApi.updateGenerator).not.toHaveBeenCalled()
  })
})

describe('a refused claim (409 cost_basis_mismatch)', () => {
  it('toasts the backend sentence once, not the axios status line', async () => {
    const toast = (await import('react-hot-toast')).default
    const shown = vi.spyOn(toast, 'error').mockImplementation(() => '')
    const message = "This asset's costs are per tonne, but the form assumed per energy. "
      + 'Nothing was saved; reopen the card and enter the costs again.'
    vi.mocked(networkApi.updateGenerator).mockRejectedValueOnce(Object.assign(
      new Error('Request failed with status code 409'),
      { response: { status: 409, data: { detail: { error_kind: 'cost_basis_mismatch', claimed: 'energy', actual: 'tonne', message } } } },
    ))
    units.mockResolvedValue({ component: 'Generator', name: 'crusher', units: {} })
    mount()
    await openEdit()
    await screen.findByText(/Capacity \(MW\)/)
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(shown).toHaveBeenCalled())
    expect(shown.mock.calls.map(c => String(c[0])).filter(t => t.startsWith('Save failed')))
      .toEqual([`Save failed: ${message}`])
    shown.mockRestore()
  })
})
