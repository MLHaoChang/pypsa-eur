// Phase 12h — the Generator card's "availability includes outages" flag.
//
// The flag is a per-asset bool that zeroes a unit's outage rate everywhere
// (both adequacy engines and the reserve margin), so the UI has exactly two
// jobs and both have a bite the reviews found:
//
//  1. the key must be in `toFS(gen, [...])`. The save is a remove+add and
//     the payload is built FROM THE FORM — leave the key out and the flag is
//     silently cleared every time the user saves an unrelated field;
//  2. the payload must assign the flag EXPLICITLY, beside `committable`,
//     never through the `...current` spread. The spread carries the GET's
//     value, so an unchecked box would re-save the old `true` and the flag
//     could not be cleared at all.
//
// Follows the render/mock/userEvent recipe in PropertiesPanel.rescale.test.tsx.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import type { Generator } from '../api/types'
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
      updateGenerator: vi.fn(),
    },
  }
})

function generator(flag: boolean | null): Generator {
  return {
    name: 'nuc', bus: 'Bus1', carrier: 'nuclear',
    outage_rate_value: 0.05, outage_rate_basis: 'EFORd', mttr_hours: 100,
    p_max_pu_includes_outages: flag,
    p_nom: 100, p_nom_extendable: false, p_nom_min: 0, p_nom_max: null,
    p_min_pu: 0, p_max_pu: 0.8, control: 'PQ',
    marginal_cost: 5, capital_cost: 0, fom_cost: 0,
    overnight_cost: null, discount_rate: null, curtailment_cost: 0,
    efficiency: 1, committable: false,
    ramp_limit_up: null, ramp_limit_down: null,
    start_up_cost: 0, shut_down_cost: 0, min_up_time: 0, min_down_time: 0,
    e_sum_min: null, e_sum_max: null,
    build_year: 2025, lifetime: null, unit: 'MW',
  }
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <PropertiesPanel />
    </QueryClientProvider>,
  )
}

function mount(flag: boolean | null) {
  vi.mocked(networkApi.getGenerators).mockReset().mockResolvedValue([generator(flag)])
}

beforeEach(() => {
  vi.mocked(networkApi.getBuses).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getCarriers).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getLoads).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getStorageUnits).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getStores).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getLinks).mockReset().mockResolvedValue([])
  // `updateGenerator` returns the raw axios response; the panel only awaits
  // it, so an empty envelope is enough and keeps the mock type-correct.
  vi.mocked(networkApi.updateGenerator).mockReset()
    .mockResolvedValue({ data: { name: 'nuc' } } as never)
  mount(false)
  useUIStore.setState({
    currentProject: 'Demo',
    selectedComponent: { type: 'Generator', name: 'nuc' },
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null, selectedComponent: null })
})

async function openEditor() {
  await userEvent.click(await screen.findByRole('button', { name: /edit generator/i }))
  return screen.findByRole('checkbox', { name: /availability includes outages/i })
}

describe('Generator card — p_max_pu_includes_outages', () => {
  it('renders the checkbox in the Adequacy section, unchecked when the flag is clear', async () => {
    renderPanel()
    const box = await openEditor()
    expect((box as HTMLInputElement).checked).toBe(false)
  })

  it('sends true when the box is ticked', async () => {
    renderPanel()
    await userEvent.click(await openEditor())
    await userEvent.click(await screen.findByRole('button', { name: /^save$/i }))

    await vi.waitFor(() => expect(networkApi.updateGenerator).toHaveBeenCalledTimes(1))
    const [, payload] = vi.mocked(networkApi.updateGenerator).mock.calls[0]
    expect((payload as Record<string, unknown>).p_max_pu_includes_outages).toBe(true)
  })

  it('reflects a SET flag on open — the key is in the form (bite: drop it from toFS)', async () => {
    mount(true)
    renderPanel()
    const box = await openEditor()
    expect((box as HTMLInputElement).checked).toBe(true)
  })

  it('keeps a set flag through a save that changes nothing else', async () => {
    // The bite this pins: with the key missing from `toFS`, the form has no
    // value for it, the payload sends `false`, and the remove+add save wipes
    // a flag the user never touched.
    mount(true)
    renderPanel()
    await openEditor()
    await userEvent.click(await screen.findByRole('button', { name: /^save$/i }))

    await vi.waitFor(() => expect(networkApi.updateGenerator).toHaveBeenCalledTimes(1))
    const [, payload] = vi.mocked(networkApi.updateGenerator).mock.calls[0]
    expect((payload as Record<string, unknown>).p_max_pu_includes_outages).toBe(true)
  })

  it('CLEARS a set flag when the box is unticked (bite: rely on the ...current spread)', async () => {
    mount(true)
    renderPanel()
    await userEvent.click(await openEditor())
    await userEvent.click(await screen.findByRole('button', { name: /^save$/i }))

    await vi.waitFor(() => expect(networkApi.updateGenerator).toHaveBeenCalledTimes(1))
    const [, payload] = vi.mocked(networkApi.updateGenerator).mock.calls[0]
    expect((payload as Record<string, unknown>).p_max_pu_includes_outages).toBe(false)
  })

  it('treats a null flag from the backend as unchecked', async () => {
    mount(null)
    renderPanel()
    const box = await openEditor()
    expect((box as HTMLInputElement).checked).toBe(false)
  })
})
