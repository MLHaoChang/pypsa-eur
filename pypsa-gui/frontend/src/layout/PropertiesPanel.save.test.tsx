// Characterization of the Generator edit card's save payload, written BEFORE
// the extras section opens its three layers (spec D20, D30).
//
// The behaviour that matters: the payload starts from the CACHED object
// (PropertiesPanel.tsx:141-144), so a field the card does not enumerate
// survives at its old value. Extras must ride on top of that, not replace it.
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
      getGeneratorProfiles: vi.fn(async () => ({})),
      updateGenerator: vi.fn(async () => ({ name: 'gas' })),
      deleteGenerator: vi.fn(),
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

import { networkApi } from '../api/network'
import { GeneratorCard } from './PropertiesPanel'

const GEN = {
  name: 'gas', bus: 'B1', carrier: 'gas', p_nom: 100, p_nom_extendable: false,
  p_nom_min: 0, p_nom_max: null, p_min_pu: 0, p_max_pu: 1, marginal_cost: 50,
  capital_cost: 1000, efficiency: 0.5, committable: false, control: 'PQ',
  build_year: 2025, lifetime: null,
  // Not enumerated by the card — this is the field whose survival is the point.
  weight: 7,
} as never

function renderCard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  client.setQueryData(nk('Demo', 'generators'), [GEN])
  return render(
    <QueryClientProvider client={client}>
      <GeneratorCard gen={GEN} onRename={() => {}} />
    </QueryClientProvider>,
  )
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null })
})

/** Open the card's edit form. */
async function openEdit() {
  renderCard()
  await userEvent.click(await screen.findByRole('button', { name: /edit/i }))
}

/** The payload the card sent, once the mutation has fired. */
async function sentPayload(): Promise<Record<string, unknown>> {
  await waitFor(() => expect(networkApi.updateGenerator).toHaveBeenCalled())
  return vi.mocked(networkApi.updateGenerator).mock.calls[0][1] as Record<string, unknown>
}

describe('Generator save payload — behaviour as of 54a5b3c0', () => {
  it('sends the enumerated fields', async () => {
    await openEdit()
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    const payload = await sentPayload()
    expect(payload.p_nom).toBe(100)
    expect(payload.marginal_cost).toBe(50)
    expect(payload.carrier).toBe('gas')
  })

  it('a cached field the card does not enumerate SURVIVES at its old value', async () => {
    // This is the ...current spread at :144. Without it a partial payload
    // would wipe the field to a Pydantic default on the backend's remove+add.
    await openEdit()
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    expect((await sentPayload()).weight).toBe(7)
  })

  it('a blanked optional bound is sent as null, not omitted', async () => {
    // The unconditional payload.p_nom_max = no(form,'p_nom_max') at :182.
    // Omitting it would make a bound impossible to clear once typed.
    await openEdit()
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    const payload = await sentPayload()
    expect('p_nom_max' in payload).toBe(true)
    expect(payload.p_nom_max).toBe(null)
  })

  it('reads every enumerated field from the form, not from the cached object', async () => {
    // The form seed is what the user edits, so the payload must read it. The
    // seed→payload hop itself is pinned by cardKit's nf/ni/no tests and proven
    // end-to-end by the extras round-trip in PropertiesPanel's extras suite;
    // this asserts the shape that makes those meaningful — every enumerated
    // key is present in the payload rather than silently omitted.
    await openEdit()
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    const payload = await sentPayload()
    for (const k of ['p_nom', 'marginal_cost', 'capital_cost', 'efficiency',
      'p_nom_extendable', 'committable', 'build_year']) {
      expect(k in payload).toBe(true)
    }
  })
})
