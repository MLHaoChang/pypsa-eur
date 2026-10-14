// S1c: the Store, Link and Load cards render from the asset schema
// (services/asset_schema/fields.py, plan 2026-10-13-s1-expert-asset-cards.md),
// like the StorageUnit and Generator cards since S1b. These pin what each card
// shows, in which unit, and what a save writes back in the stored unit.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  const empty = vi.fn(async () => [])
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: empty, getCarriers: empty,
      getStores: empty, getLinks: empty, getLoads: empty,
      updateStore: vi.fn(async () => ({ name: 'tank' })),
      updateLink: vi.fn(async () => ({ name: 'ely' })),
      updateLoad: vi.fn(async () => ({ name: 'demand' })),
      getLoadProfiles: vi.fn(async () => ({})),
      getLinkProfiles: vi.fn(async () => ({})),
      getCatalog: vi.fn(async (component: string) => ({ component, attributes: [] })),
      listTimeseries: empty,
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
import { LinkCard, LoadCard, StoreCard } from './PropertiesPanel'

const STORE = { name: 'tank', bus: 'H2', carrier: 'H2', e_nom: 100, e_nom_extendable: true,
  e_nom_min: 0, e_nom_max: 500, e_min_pu: 0, e_max_pu: 1, e_cyclic: true, e_initial: 0,
  standing_loss: 0, marginal_cost: 0, overnight_cost: 12_000, capital_cost: 1_000, fom_cost: 200,
  lifetime: 25, build_year: 2030 } as never
const LINK = { name: 'ely', bus0: 'B1', bus1: 'H2', carrier: 'H2 electrolysis', p_nom: 5,
  p_nom_extendable: false, p_nom_min: 0, p_nom_max: null, p_min_pu: 0, p_max_pu: 1, efficiency: 0.7,
  marginal_cost: 0, overnight_cost: 600_000, capital_cost: 50_000, fom_cost: 12_000, lifetime: 20 } as never
const LOAD = { name: 'demand', bus: 'B1', carrier: 'AC', p_set: 40, q_set: 0, sign: -1 } as never

function client(seed: Array<[string, unknown]>) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  })
  for (const [key, value] of seed) qc.setQueryData(nk('Demo', key), value)
  return qc
}

function inputOf(label: string): HTMLInputElement {
  return screen.getByLabelText(label) as HTMLInputElement
}

async function openEdit(node: React.ReactNode, seed: Array<[string, unknown]>) {
  render(<QueryClientProvider client={client(seed)}>{node}</QueryClientProvider>)
  await userEvent.click(await screen.findByRole('button', { name: /^Edit / }))
}

async function sent(fn: (...a: never[]) => unknown): Promise<Record<string, unknown>> {
  await userEvent.click(screen.getByRole('button', { name: /^save/i }))
  await waitFor(() => expect(fn).toHaveBeenCalled())
  return vi.mocked(fn).mock.calls[0][1] as Record<string, unknown>
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(networkApi.updateStore).mockClear()
  vi.mocked(networkApi.updateLink).mockClear()
  vi.mocked(networkApi.updateLoad).mockClear()
})
afterEach(() => { useUIStore.setState({ currentProject: null }) })

describe('the Store card', () => {
  const card = <StoreCard store={STORE} onRename={() => {}} />

  it('types the overnight cost per kWh and shows the annualised cost read-only', async () => {
    await openEdit(card, [['stores', [STORE]]])
    expect(inputOf('Energy capacity (MWh)').value).toBe('100')
    expect(inputOf('Overnight cost (€/kWh)').value).toBe('12')
    expect(inputOf('Fixed O&M (€/kWh/yr)').value).toBe('0.2')
    const cc = screen.getByTestId('asset-derived-capital_cost')
    expect(cc.querySelector('input')).toBeNull()
    expect(cc.textContent).toContain('€/kWh/yr')
  })

  it('saves the overnight cost in €/MWh, and never a typed capital cost', async () => {
    await openEdit(card, [['stores', [STORE]]])
    fireEvent.change(inputOf('Overnight cost (€/kWh)'), { target: { value: '15' } })
    const body = await sent(networkApi.updateStore)
    expect(body.overnight_cost).toBe(15_000)
    expect(body.fom_cost).toBeCloseTo(200, 9)
    // The PUT carries the whole row: the derived column goes back unchanged.
    expect(body.capital_cost).toBe(1_000)
  })

  it('a cleared maximum capacity sends null (no upper bound)', async () => {
    await openEdit(card, [['stores', [STORE]]])
    fireEvent.change(inputOf('Maximum capacity (MWh)'), { target: { value: '' } })
    const body = await sent(networkApi.updateStore)
    expect(body.e_nom_max).toBeNull()
  })

  it('the initial energy shows only when the store is not cyclic', async () => {
    await openEdit(card, [['stores', [STORE]]])
    expect(screen.queryByText(/^Initial energy/)).toBeNull()
    fireEvent.click(screen.getByText('Cyclic state of energy'))
    expect(screen.getByText(/^Initial energy/)).toBeTruthy()
  })

  it('the bounds show only while extendable', async () => {
    await openEdit(card, [['stores', [STORE]]])
    expect(screen.getByText(/^Maximum capacity/)).toBeTruthy()
    fireEvent.click(screen.getByText('Extendable'))
    expect(screen.queryByText(/^Maximum capacity/)).toBeNull()
  })
})

describe('the Link card', () => {
  const card = <LinkCard link={LINK} onRename={() => {}} />

  it.each(['card', 'detail'] as const)('shows both ends in its %s read-out', (mode) => {
    render(<QueryClientProvider client={client([['links', [LINK]]])}>
      <LinkCard link={LINK} mode={mode} onRename={() => {}} />
    </QueryClientProvider>)
    expect(screen.getByText('From bus').closest('div')?.textContent).toContain('B1')
    expect(screen.getByText('To bus').closest('div')?.textContent).toContain('H2')
  })

  it('types the efficiency as a percentage and saves it as a share', async () => {
    await openEdit(card, [['links', [LINK]]])
    expect(inputOf('Efficiency (%)').value).toBe('70')
    expect(inputOf('Overnight cost (€/kW)').value).toBe('600')
    fireEvent.change(inputOf('Efficiency (%)'), { target: { value: '65' } })
    const body = await sent(networkApi.updateLink)
    expect(body.efficiency).toBeCloseTo(0.65, 12)
    expect(body.overnight_cost).toBe(600_000)
    expect(body.bus0).toBe('B1')
    expect(body.bus1).toBe('H2')
  })

  it('the annualised capital cost is derived, with no input', async () => {
    await openEdit(card, [['links', [LINK]]])
    const cc = screen.getByTestId('asset-derived-capital_cost')
    expect(cc.querySelector('input')).toBeNull()
    expect(cc.textContent).toContain('€/kW/yr')
  })
})

describe('the Load card', () => {
  it('saves the demand and keeps the sign', async () => {
    await openEdit(<LoadCard load={LOAD} onRename={() => {}} />, [['loads', [LOAD]]])
    expect(inputOf('Demand (MW)').value).toBe('40')
    fireEvent.change(inputOf('Demand (MW)'), { target: { value: '55' } })
    const body = await sent(networkApi.updateLoad)
    expect(body.p_set).toBe(55)
    expect(body.sign).toBe(-1)
  })
})
