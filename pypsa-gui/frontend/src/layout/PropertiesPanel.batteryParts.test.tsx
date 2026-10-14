// S1b: the StorageUnit card types a battery as its two overnight parts and
// shows the derived annualised cost read-only (plan
// 2026-10-13-s1-expert-asset-cards.md). Before S1b the card offered
// `capital_cost` as a typed field on a battery with parts; the backend
// re-derives it from the parts, so a typed value was silently dropped.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: vi.fn(async () => []),
      getCarriers: vi.fn(async () => []),
      updateStorageUnit: vi.fn(async () => ({ name: 'bess' })),
      createStorageUnit: vi.fn(async () => ({ name: 'bess_2037' })),
      deleteStorageUnit: vi.fn(),
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

// The vintage clone's toast form, answered at once: year 2037, cost left as offered.
vi.mock('../utils/toasts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/toasts')>()
  return {
    ...actual,
    vintageCloneToast: vi.fn(async (o: { defaultCost: number | null,
      onSubmit: (y: number, c: number | null) => Promise<void> }) => o.onSubmit(2037, o.defaultCost)),
  }
})

import { networkApi } from '../api/network'
import { vintageCloneToast } from '../utils/toasts'
import { StorageUnitCard } from './PropertiesPanel'

const BESS = {
  name: 'bess', bus: 'B1', carrier: 'battery', p_nom: 80, p_nom_extendable: true, p_nom_min: 0,
  p_nom_max: 80, max_hours: 4, efficiency_store: 0.96, efficiency_dispatch: 0.96, standing_loss: 0,
  cyclic_state_of_charge: true, marginal_cost: 0, build_year: 2027,
  overnight_cost: null, capital_cost: 95_712, fom_cost: 722, lifetime: 25,
  inv_power_overnight: 213_900, inv_power_lifetime: 10, inv_power_fom_share: 0.0034,
  inv_energy_overnight: 189_900, inv_energy_lifetime: 25, inv_energy_fom_share: 0,
} as never

function inputOf(label: string): HTMLInputElement {
  return screen.getByLabelText(label) as HTMLInputElement
}

// A battery priced the old way: one overnight cost, no parts.
const LEGACY = {
  ...(BESS as object), overnight_cost: 500_000,
  inv_power_overnight: null, inv_power_lifetime: null, inv_power_fom_share: null,
  inv_energy_overnight: null, inv_energy_lifetime: null, inv_energy_fom_share: null,
} as never

function renderCard(su: never, mode: 'card' | 'detail' = 'card') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  client.setQueryData(nk('Demo', 'storage_units'), [su])
  render(<QueryClientProvider client={client}><StorageUnitCard su={su} mode={mode} onRename={() => {}} /></QueryClientProvider>)
}

async function openEdit(su: never = BESS) {
  renderCard(su)
  await userEvent.click(await screen.findByRole('button', { name: /edit/i }))
}

async function sent(): Promise<Record<string, unknown>> {
  await userEvent.click(screen.getByRole('button', { name: /^save/i }))
  await waitFor(() => expect(networkApi.updateStorageUnit).toHaveBeenCalled())
  return vi.mocked(networkApi.updateStorageUnit).mock.calls[0][1] as Record<string, unknown>
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(networkApi.updateStorageUnit).mockClear()
  vi.mocked(networkApi.createStorageUnit).mockClear()
})
afterEach(() => { useUIStore.setState({ currentProject: null }) })

describe('a battery is edited as its two parts', () => {
  it('shows each part per kW / kWh and the annualised cost read-only', async () => {
    await openEdit()
    expect(inputOf('Power part: overnight cost (€/kW)').value).toBe('213.9')
    expect(inputOf('Energy part: overnight cost (€/kWh)').value).toBe('189.9')
    expect(inputOf('Power part: fixed O&M (%)').value).toBe('0.34')
    // No typed annualised cost, overnight cost or lifetime on a battery: the
    // capital cost and lifetime rows are read-only, the single overnight cost is gone.
    const typed = Array.from(document.querySelectorAll('label'))
      .filter(l => l.querySelector('input'))
      .map(l => l.textContent ?? '')
    expect(typed.some(t => /^Capital cost/.test(t))).toBe(false)
    expect(typed.some(t => /^Lifetime/.test(t))).toBe(false)
    expect(screen.queryByText(/^Overnight cost/)).toBeNull()
    expect(screen.getByTestId('asset-derived-capital_cost').textContent).toContain('95.71')
    expect(screen.getByTestId('asset-derived-lifetime').textContent).toContain('25')
  })

  it('saves a changed part in the stored unit (€/MW), and never a typed capital cost', async () => {
    await openEdit()
    fireEvent.change(inputOf('Energy part: overnight cost (€/kWh)'), { target: { value: '150' } })
    const body = await sent()
    expect(body.inv_energy_overnight).toBe(150_000)
    expect(body.inv_power_overnight).toBe(213_900)
    expect(body.inv_power_fom_share).toBeCloseTo(0.0034, 12)
    expect(body.efficiency_store).toBeCloseTo(0.96, 12)
    // No capital cost is typed: the PUT carries the whole row, so the derived
    // value goes back unchanged, and the backend re-derives it from the parts.
    expect(body.capital_cost).toBe(95_712)
  })

  it('clearing a part sends null (the backend then has nothing to derive from)', async () => {
    await openEdit()
    fireEvent.change(inputOf('Power part: overnight cost (€/kW)'), { target: { value: '' } })
    const body = await sent()
    expect(body.inv_power_overnight).toBeNull()
  })

  it('the bounds show only while extendable', async () => {
    await openEdit()
    expect(screen.getByText('Maximum capacity (MW)')).toBeTruthy()
    fireEvent.click(screen.getByText('Extendable'))
    expect(screen.queryByText('Maximum capacity (MW)')).toBeNull()
  })
})

describe('a battery priced the old way, and the vintage clone', () => {
  it('a unit with one overnight cost and no parts says so in its edit form', async () => {
    await openEdit(LEGACY)
    const note = screen.getByTestId('legacy-overnight-note')
    expect(note.textContent).toContain('500')
    expect(note.textContent).toContain('€/kW')
    expect(note.textContent).toContain('Enter the two parts')
  })

  it('a battery with parts shows no such note', async () => {
    await openEdit()
    expect(screen.queryByTestId('legacy-overnight-note')).toBeNull()
  })

  it('the clone of a battery carries both parts, and offers no single overnight cost', async () => {
    renderCard(BESS, 'detail')
    await userEvent.click(screen.getByRole('button', { name: /clone for vintage/i }))
    await waitFor(() => expect(networkApi.createStorageUnit).toHaveBeenCalled())
    expect(vi.mocked(vintageCloneToast).mock.calls[0][0].defaultCost).toBeNull()
    const body = vi.mocked(networkApi.createStorageUnit).mock.calls[0][0] as Record<string, unknown>
    expect(body.name).toBe('bess_2037')
    expect(body.build_year).toBe(2037)
    expect(body.inv_power_overnight).toBe(213_900)
    expect(body.inv_energy_overnight).toBe(189_900)
    expect(body.inv_power_lifetime).toBe(10)
    expect(body.inv_energy_lifetime).toBe(25)
    expect(body.overnight_cost).toBeNull()
  })

  it('the clone of a legacy battery offers, and keeps, its single overnight cost', async () => {
    renderCard(LEGACY, 'detail')
    await userEvent.click(screen.getByRole('button', { name: /clone for vintage/i }))
    await waitFor(() => expect(networkApi.createStorageUnit).toHaveBeenCalled())
    expect(vi.mocked(vintageCloneToast).mock.calls.at(-1)![0].defaultCost).toBe(500_000)
    const body = vi.mocked(networkApi.createStorageUnit).mock.calls[0][0] as Record<string, unknown>
    expect(body.overnight_cost).toBe(500_000)
  })
})
