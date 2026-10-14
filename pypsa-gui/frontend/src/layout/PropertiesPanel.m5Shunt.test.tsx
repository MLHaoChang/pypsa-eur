// M5c (plan 2026-10-14-m5-non-asset-forms.md): a shunt impedance has a
// Properties card, rendered from the asset schema like the Carrier card. It
// reads out the bus, conductance and susceptance with the descriptors' help,
// saves the descriptors' patch (a new bus, a rename followed by the
// selection), and is deleted from the panel header.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import type { Bus, ShuntImpedance } from '../api/types'
import PropertiesPanel from './PropertiesPanel'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getShuntImpedances: vi.fn(),
      getBuses: vi.fn(),
      updateShuntImpedance: vi.fn(async () => ({})),
      deleteShuntImpedance: vi.fn(async () => ({})),
      getCatalog: vi.fn(async (component: string) => ({ component, attributes: [] })),
    },
  }
})

const CAP: ShuntImpedance = { name: 'cap1', bus: 'B1', g: 0.001, b: 0.02 }
const bus = (name: string) => ({ name, v_nom: 20, carrier: 'AC', x: 0, y: 0, country: '', unit: '',
  control: 'PQ', sub_network: '' }) as Bus

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><PropertiesPanel /></QueryClientProvider>)
}

async function save(): Promise<[string, Record<string, unknown>]> {
  await userEvent.click(await screen.findByRole('button', { name: /^save$/i }))
  await waitFor(() => expect(networkApi.updateShuntImpedance).toHaveBeenCalled())
  const [name, body] = vi.mocked(networkApi.updateShuntImpedance).mock.calls[0]
  return [name, body as unknown as Record<string, unknown>]
}

beforeEach(() => {
  vi.mocked(networkApi.getShuntImpedances).mockReset().mockResolvedValue([CAP])
  vi.mocked(networkApi.getBuses).mockReset().mockResolvedValue([bus('B1'), bus('B2')])
  vi.mocked(networkApi.updateShuntImpedance).mockClear()
  vi.mocked(networkApi.deleteShuntImpedance).mockClear()
  localStorage.clear()
  useUIStore.setState({ currentProject: 'Demo', rightPanelOpen: true,
    selectedComponent: { type: 'ShuntImpedance', name: 'cap1' } })
})
afterEach(() => { useUIStore.setState({ currentProject: null, selectedComponent: null }); localStorage.clear() })

describe('the shunt impedance card from the asset schema (M5c)', () => {
  it('reads out the bus, conductance and susceptance in siemens', async () => {
    renderPanel()
    expect(await screen.findByText('Shunt impedance')).toBeTruthy()
    expect(await screen.findByText('Susceptance')).toBeTruthy()
    expect(screen.getByText('Conductance')).toBeTruthy()
    expect(screen.getByText('B1')).toBeTruthy()
    expect(screen.getByText(/AC power flow only/)).toBeTruthy()
  })

  it('saves the descriptors’ patch: the susceptance as typed, a new bus', async () => {
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /edit shunt/i }))
    const b = await screen.findByLabelText('Susceptance (S)') as HTMLInputElement
    expect(b.value).toBe('0.02')
    fireEvent.change(b, { target: { value: '-0.05' } })
    const busSelect = screen.getByLabelText(/^Bus/) as HTMLSelectElement
    await waitFor(() => expect(Array.from(busSelect.options).some(o => o.value === 'B2')).toBe(true))
    fireEvent.change(busSelect, { target: { value: 'B2' } })
    const [name, body] = await save()
    expect(name).toBe('cap1')
    expect(body).toMatchObject({ name: 'cap1', bus: 'B2', b: -0.05, g: 0.001 })
  })

  it('a rename moves the selection to the new name', async () => {
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /edit shunt/i }))
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'reactor1' } })
    const [, body] = await save()
    expect(body.name).toBe('reactor1')
    await waitFor(() => expect(useUIStore.getState().selectedComponent)
      .toEqual({ type: 'ShuntImpedance', name: 'reactor1' }))
  })

  it('the header deletes it', async () => {
    renderPanel()
    await screen.findByText('Susceptance')
    await userEvent.click(screen.getByTitle('Delete ShuntImpedance'))
    await waitFor(() => expect(networkApi.deleteShuntImpedance).toHaveBeenCalledWith('cap1'))
  })
})
