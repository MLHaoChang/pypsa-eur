// 05d (GK B2 F2″, F3, T2, T3, U1): each edit card claims the basis its costs were typed under, from the same
// schema that scaled the patch; sends nothing until its fields are loaded; a bus move alone saves without a claim;
// and a server refusal of the claim reads as the backend's sentence, once.
import type { ReactElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
      getBuses: vi.fn(async () => [{ name: 'ore' }, { name: 'grid' }]),
      getCarriers: vi.fn(async () => []),
      getGeneratorProfiles: vi.fn(async () => ({})),
      getCatalog: vi.fn(async (component: string) => ({ component, attributes: [] })),
      listTimeseries: vi.fn(async () => []),
      updateGenerator: vi.fn(async () => ({})),
      updateStorageUnit: vi.fn(async () => ({})),
      updateStore: vi.fn(async () => ({})),
      updateLink: vi.fn(async () => ({})),
      updateLoad: vi.fn(async () => ({})),
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
import { GeneratorCard, LinkCard, LoadCard, StorageUnitCard, StoreCard } from './PropertiesPanel'

type Api = 'updateGenerator' | 'updateStorageUnit' | 'updateStore' | 'updateLink' | 'updateLoad'
interface Case { cls: string; root: string; api: Api; row: Record<string, unknown>; el: (r: never) => ReactElement }

const CASES: Record<string, Case> = {
  Generator: {
    cls: 'Generator', root: 'generators', api: 'updateGenerator',
    row: { name: 'crusher', bus: 'ore', carrier: 'ore', control: 'PQ', p_nom: 120, p_nom_extendable: false,
      p_nom_min: 0, p_nom_max: 80, p_min_pu: 0, p_max_pu: 1, efficiency: 1, marginal_cost: 0, overnight_cost: 50,
      curtailment_cost: 0, lifetime: 40, build_year: 2027, committable: false },
    el: r => <GeneratorCard gen={r} onRename={() => {}} />,
  },
  StorageUnit: {
    cls: 'StorageUnit', root: 'storage_units', api: 'updateStorageUnit',
    row: { name: 'silo', bus: 'ore', carrier: 'ore', p_nom: 10, p_nom_extendable: false, max_hours: 2,
      efficiency_store: 0.9, efficiency_dispatch: 0.9, marginal_cost: 0, capital_cost: 0, lifetime: 20,
      build_year: 2027, cyclic_state_of_charge: false },
    el: r => <StorageUnitCard su={r} onRename={() => {}} />,
  },
  Store: {
    cls: 'Store', root: 'stores', api: 'updateStore',
    row: { name: 'tank', bus: 'ore', carrier: 'ore', e_nom: 336, e_nom_extendable: false, e_nom_min: 0,
      e_nom_max: 1000, e_cyclic: false, standing_loss: 0, marginal_cost: 0, lifetime: 30, build_year: 2027 },
    el: r => <StoreCard store={r} onRename={() => {}} />,
  },
  Link: {
    cls: 'Link', root: 'links', api: 'updateLink',
    row: { name: 'kiln', bus0: 'ore', bus1: 'grid', carrier: 'ore', p_nom: 50, p_nom_extendable: false,
      efficiency: 1, marginal_cost: 0, overnight_cost: 50, lifetime: 30, build_year: 2027 },
    el: r => <LinkCard link={r} onRename={() => {}} />,
  },
  Load: {
    cls: 'Load', root: 'loads', api: 'updateLoad',
    row: { name: 'mill', bus: 'ore', carrier: '', p_set: 5 },
    el: r => <LoadCard load={r} onRename={() => {}} />,
  },
}
const COST_CARDS = ['Generator', 'StorageUnit', 'Store', 'Link']

// The backend's per-tonne map (services/asset_schema/fields.py _TONNE_COST_UNIT), applied to the fixture.
const TONNE_UNIT: Record<string, string> = {
  'EUR/kW': 'EUR/(t/h)', 'EUR/kW/yr': 'EUR/(t/h)/yr', 'EUR/kWh': 'EUR/t', 'EUR/kWh/yr': 'EUR/t/yr', 'EUR/MWh': 'EUR/t',
}
function tonneUnits(cls: string, name: string) {
  const fields = ((schemas as unknown as Record<string, { groups: { fields: { key: string; unit_display: string }[] }[] }>)[cls])
    .groups.flatMap(g => g.fields)
  const costs = Object.fromEntries(fields.filter(f => TONNE_UNIT[f.unit_display])
    .map(f => [f.key, { unit: TONNE_UNIT[f.unit_display], scale: 1 }]))
  return { component: cls, name, units: {}, costs }
}

function mount(c: Case) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  client.setQueryData(nk('Demo', c.root), [c.row])
  render(<QueryClientProvider client={client}>{c.el(c.row as never)}</QueryClientProvider>)
  return client
}
const put = (c: Case) => vi.mocked(networkApi[c.api])
async function openEdit() {
  await userEvent.click(await screen.findByRole('button', { name: /edit/i }))
}
async function saveWhenReady() {
  const save = await screen.findByRole('button', { name: /^save$/i })
  await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false))
  await userEvent.click(save)
}
function inputStarting(prefix: string): HTMLInputElement {
  const span = screen.getAllByText((t) => t.startsWith(prefix))[0]
  const el = span.closest('label')?.querySelector('input')
  if (!el) throw new Error(`no input labelled ${prefix}…`)
  return el as HTMLInputElement
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  units.mockReset()
  schemaGet.mockReset()
  schemaGet.mockImplementation(async (c: string) => (schemas as unknown as Record<string, unknown>)[c])
  for (const c of Object.values(CASES)) put(c).mockClear()
})
afterEach(() => { useUIStore.setState({ currentProject: null }) })

describe.each(COST_CARDS)('the %s card claims the basis it shows (T2)', (cls) => {
  const c = CASES[cls]
  it('on a tonne bus: tonne', async () => {
    units.mockResolvedValue(tonneUnits(cls, String(c.row.name)))
    mount(c)
    await openEdit()
    await waitFor(() => expect(screen.getAllByText(/€\/(\(t\/h\)|t)\b/).length).toBeGreaterThan(0))
    await saveWhenReady()
    await waitFor(() => expect(put(c)).toHaveBeenCalled())
    expect(put(c).mock.calls[0][2]).toBe('tonne')
  })
  it('on an energy bus: energy', async () => {
    units.mockResolvedValue({ component: cls, name: c.row.name, units: {} })
    mount(c)
    await openEdit()
    await saveWhenReady()
    await waitFor(() => expect(put(c)).toHaveBeenCalled())
    expect(put(c).mock.calls[0][2]).toBe('energy')
  })
})

describe.each(Object.keys(CASES))('the %s card holds Save until its fields load (F3, T2)', (cls) => {
  it('is disabled while the schema is pending, and a click sends nothing', async () => {
    const c = CASES[cls]
    schemaGet.mockImplementation(() => new Promise(() => {}))
    units.mockResolvedValue({ component: cls, name: c.row.name, units: {} })
    mount(c)
    await openEdit()
    const save = await screen.findByRole('button', { name: /^save$/i })
    expect((save as HTMLButtonElement).disabled).toBe(true)
    await userEvent.click(save)
    expect(put(c)).not.toHaveBeenCalled()
  })
})

describe.each(['Generator', 'Link'])('the %s claim and patch come from one schema (T3)', (cls) => {
  const c = CASES[cls]
  it('123.4 typed per t/h is stored as 123.4 under the tonne claim', async () => {
    units.mockResolvedValue(tonneUnits(cls, String(c.row.name)))
    mount(c)
    await openEdit()
    await waitFor(() => expect(screen.getAllByText(/€\/\(t\/h\)/).length).toBeGreaterThan(0))
    fireEvent.change(inputStarting('Overnight cost'), { target: { value: '123.4' } })
    await saveWhenReady()
    await waitFor(() => expect(put(c)).toHaveBeenCalled())
    const [, body, basis] = put(c).mock.calls[0] as [string, Record<string, unknown>, string]
    expect([body.overnight_cost, basis]).toEqual([123.4, 'tonne'])
  })
  it('123.4 typed per kW is stored x1000 under the energy claim', async () => {
    units.mockResolvedValue({ component: cls, name: c.row.name, units: {} })
    mount(c)
    await openEdit()
    await waitFor(() => expect(screen.getAllByText(/€\/kW/).length).toBeGreaterThan(0))
    fireEvent.change(inputStarting('Overnight cost'), { target: { value: '123.4' } })
    await saveWhenReady()
    await waitFor(() => expect(put(c)).toHaveBeenCalled())
    const [, body, basis] = put(c).mock.calls[0] as [string, Record<string, unknown>, string]
    expect([body.overnight_cost, basis]).toEqual([123400, 'energy'])
  })
})

describe('the units flip between opening the card and Save (T3)', () => {
  it('the claim follows the schema the patch is scaled with, not the one the card opened with', async () => {
    const c = CASES.Generator
    units.mockResolvedValueOnce({ component: 'Generator', name: 'crusher', units: {} })
    units.mockResolvedValue(tonneUnits('Generator', 'crusher'))
    const client = mount(c)
    await openEdit()
    await waitFor(() => expect(screen.getAllByText(/€\/kW/).length).toBeGreaterThan(0))
    await client.invalidateQueries()
    await waitFor(() => expect(screen.getAllByText(/€\/\(t\/h\)/).length).toBeGreaterThan(0))
    fireEvent.change(inputStarting('Overnight cost'), { target: { value: '123.4' } })
    await saveWhenReady()
    await waitFor(() => expect(put(c)).toHaveBeenCalled())
    const [, body, basis] = put(c).mock.calls[0] as [string, Record<string, unknown>, string]
    expect([body.overnight_cost, basis]).toEqual([123.4, 'tonne'])
  })
})

describe('a bus move on a card (U1)', () => {
  const c = CASES.Generator
  async function moveTo(bus: string) {
    units.mockResolvedValue(tonneUnits('Generator', 'crusher'))
    mount(c)
    await openEdit()
    await waitFor(() => expect(screen.getAllByText(/€\/\(t\/h\)/).length).toBeGreaterThan(0))
    const select = screen.getAllByText('Bus').map(s => s.closest('label')?.querySelector('select')).find(Boolean)
    await waitFor(() => expect(select!.querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(select!, { target: { value: bus } })
  }

  it('alone saves with no claim and the stored costs', async () => {
    await moveTo('grid')
    await saveWhenReady()
    await waitFor(() => expect(put(c)).toHaveBeenCalled())
    const [, body, basis] = put(c).mock.calls[0] as [string, Record<string, unknown>, string | undefined]
    expect([body.bus, body.overnight_cost, basis]).toEqual(['grid', 50, undefined])
  })

  it('with a cost typed keeps the claim, so the server can refuse it', async () => {
    await moveTo('grid')
    fireEvent.change(inputStarting('Overnight cost'), { target: { value: '60' } })
    await saveWhenReady()
    await waitFor(() => expect(put(c)).toHaveBeenCalled())
    expect(put(c).mock.calls[0][2]).toBe('tonne')
  })
})

describe('a refused claim (409 cost_basis_mismatch)', () => {
  it('toasts the backend sentence once, not the axios status line', async () => {
    const toast = (await import('react-hot-toast')).default
    const shown = vi.spyOn(toast, 'error').mockImplementation(() => '')
    const message = "This asset's costs are per t/h or t, but the form assumed per kW or kWh. "
      + 'Nothing was saved; reopen the card and enter the costs again.'
    put(CASES.Generator).mockRejectedValueOnce(Object.assign(
      new Error('Request failed with status code 409'),
      { response: { status: 409, data: { detail: { error_kind: 'cost_basis_mismatch', claimed: 'energy', actual: 'tonne', message } } } },
    ))
    units.mockResolvedValue({ component: 'Generator', name: 'crusher', units: {} })
    mount(CASES.Generator)
    await openEdit()
    await saveWhenReady()
    await waitFor(() => expect(shown).toHaveBeenCalled())
    expect(shown.mock.calls.map(c => String(c[0])).filter(t => t.startsWith('Save failed')))
      .toEqual([`Save failed: ${message}`])
    shown.mockRestore()
  })
})
