// M5 (plan 2026-10-14-m5-non-asset-forms.md): the Bus panel renders its
// fields from the asset schema, as the asset cards do. The read-out and the
// form take the descriptors' labels and help (the old propertyDocs tips are
// folded into them); the save is the descriptors' patch, with a blank country
// or sub-network sent as "" (null would be refused), and an edited extra
// column saved like every other card's.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { editScope, saveExtras } from '../utils/extrasStore'
import type { Bus } from '../api/types'
import PropertiesPanel from './PropertiesPanel'

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
      updateBus: vi.fn(async () => ({ name: 'Bus1', rescale: [] })),
      getCatalog: vi.fn(async (component: string) => ({ component, attributes: [] })),
    },
  }
})

const BUS1: Bus = {
  name: 'Bus1', v_nom: 380, carrier: 'AC', x: 6.96, y: 50.9,
  country: 'DE', unit: '', control: 'PQ', sub_network: '',
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><PropertiesPanel /></QueryClientProvider>)
}

async function tipOf(label: string): Promise<string> {
  const text = await screen.findByText(label, { selector: 'span' })
  const trigger = text.parentElement?.querySelector('[tabindex="0"]')
  if (!trigger) throw new Error(`row ${label} has no tooltip`)
  fireEvent.focus(trigger)
  const tip = (await screen.findByRole('tooltip')).textContent ?? ''
  fireEvent.blur(trigger)
  return tip
}

async function save(): Promise<Record<string, unknown>> {
  await userEvent.click(await screen.findByRole('button', { name: /^save$/i }))
  await waitFor(() => expect(networkApi.updateBus).toHaveBeenCalled())
  return vi.mocked(networkApi.updateBus).mock.calls[0][1] as unknown as Record<string, unknown>
}

beforeEach(() => {
  vi.mocked(networkApi.getBuses).mockReset().mockResolvedValue([{ ...BUS1, my_tag: 'A' } as Bus])
  for (const f of ['getCarriers', 'getGenerators', 'getLoads', 'getStorageUnits', 'getStores', 'getLinks'] as const) {
    vi.mocked(networkApi[f]).mockReset().mockResolvedValue([] as never)
  }
  vi.mocked(networkApi.updateBus).mockClear()
  localStorage.clear()
  useUIStore.setState({ currentProject: 'Demo', selectedComponent: { type: 'Bus', name: 'Bus1' } })
})
afterEach(() => { useUIStore.setState({ currentProject: null, selectedComponent: null }); localStorage.clear() })

describe('the Bus panel from the asset schema (M5)', () => {
  it('reads out the nominal voltage under its descriptor label, with the descriptor help', async () => {
    renderPanel()
    expect(await screen.findByText('Nominal voltage')).toBeTruthy()
    expect(await tipOf('Control')).toContain('Slack (the reference)')
    expect(await tipOf('Sub-network')).toContain("'region' clustering mode")
  })

  it('edits through the descriptors: kV, the control options, coordinates in °E and °N', async () => {
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /edit bus/i }))
    expect((await screen.findByLabelText('Nominal voltage (kV)') as HTMLInputElement).value).toBe('380')
    expect((screen.getByLabelText('Longitude (°E)') as HTMLInputElement).value).toBe('6.96')
    expect((screen.getByLabelText('Latitude (°N)') as HTMLInputElement).value).toBe('50.9')
    const control = screen.getByLabelText(/^Control/) as HTMLSelectElement
    expect(Array.from(control.options).map(o => o.value).filter(Boolean)).toEqual(['PQ', 'PV', 'Slack'])
  })

  it('saves the descriptors’ patch: a new voltage as typed, a cleared sub-network and country as ""', async () => {
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /edit bus/i }))
    fireEvent.change(await screen.findByLabelText('Nominal voltage (kV)'), { target: { value: '220' } })
    fireEvent.change(screen.getByLabelText('Country'), { target: { value: '' } })
    const body = await save()
    expect(body.v_nom).toBe(220)
    expect(body.country).toBe('')
    expect(body.sub_network).toBe('')
    expect(body.name).toBe('Bus1')
    expect(body.carrier).toBe('AC')
    expect(body.x).toBe(6.96)
    expect(body.y).toBe(50.9)
  })

  it('an edited extra column is saved, as on every asset card', async () => {
    saveExtras(editScope('Bus'), ['my_tag'])
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /edit bus/i }))
    fireEvent.change((await screen.findAllByLabelText(/my_tag/)).find(e => e.tagName === 'INPUT' && (e as HTMLInputElement).value === 'A')!, { target: { value: 'B' } })
    const body = await save()
    expect(body.my_tag).toBe('B')
  })
})
