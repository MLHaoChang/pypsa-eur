// 05d, GK B2 F1a: a new asset's cost field follows the bus it attaches to. A value typed per tonne must be
// stored as typed; the same typing on an energy bus is per kW and stored x1000.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import schemaFixture from '../test/fixtures/assetSchema.json'
import CreationForm from './CreationForm'

vi.unmock('../hooks/useAssetSchema')
vi.mock('../api/assetSchema', async (orig) => ({
  ...(await orig<typeof import('../api/assetSchema')>()),
  assetSchemaApi: { get: vi.fn(), units: vi.fn(), unitsForBus: vi.fn() },
}))
vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getCarriers: vi.fn(async () => []),
    getCatalog: vi.fn(async () => ({ component: 'Generator', attributes: [] })), createGenerator: vi.fn(async () => ({})) } }
})
import { networkApi } from '../api/network'
import { assetSchemaApi } from '../api/assetSchema'

const BUSES = [{ name: 'NH3', carrier: 'ammonia' }, { name: 'Elec A', carrier: 'AC' }]

function fieldInput(labelRe: RegExp): HTMLInputElement {
  const label = Array.from(document.querySelectorAll('label')).find(l => labelRe.test(l.textContent ?? ''))
  if (!label) throw new Error(`no field labelled ${labelRe}`)
  return ((label.querySelector('input') ? label : label.parentElement) as HTMLElement).querySelector('input') as HTMLInputElement
}

async function createOn(bus: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  client.setQueryData(nk('Demo', 'buses'), BUSES)
  render(<QueryClientProvider client={client}><CreationForm item={{ id: 'thermal', label: 'Conventional' }} /></QueryClientProvider>)
  await screen.findByText('Add to Network')
  fireEvent.change(fieldInput(/^Name/), { target: { value: 'G1' } })
  fireEvent.change(fieldInput(/^Attach to Bus/), { target: { value: bus } })
  if (bus === 'NH3') await waitFor(() => expect(screen.getAllByText(/€\/\(t\/h\)/).length).toBeGreaterThan(0))
  fireEvent.change(fieldInput(/^Overnight cost/), { target: { value: '123.4' } })
  await userEvent.click(screen.getByText('Add to Network'))
  await waitFor(() => expect(vi.mocked(networkApi).createGenerator).toHaveBeenCalled())
  return vi.mocked(networkApi).createGenerator.mock.calls[0][0] as Record<string, unknown>
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', creationItem: null })
  vi.mocked(assetSchemaApi.get).mockResolvedValue((schemaFixture as Record<string, never>).Generator)
  vi.mocked(assetSchemaApi.unitsForBus).mockImplementation(async (_c, bus) => ({
    component: 'Generator', bus, units: {},
    costs: bus === 'NH3' ? { overnight_cost: { unit: 'EUR/(t/h)', scale: 1 } } : {} }))
})
afterEach(() => { vi.clearAllMocks(); useUIStore.setState({ currentProject: null, creationItem: null }) })

describe('CreationForm cost fields follow the bus', () => {
  it('stores 123.4 per tonne exactly as typed on a tonne bus', async () => {
    expect((await createOn('NH3')).overnight_cost).toBe(123.4)
  })
  it('still stores x1000 from EUR/kW on an energy bus', async () => {
    expect((await createOn('Elec A')).overnight_cost).toBe(123400)
  })
})
