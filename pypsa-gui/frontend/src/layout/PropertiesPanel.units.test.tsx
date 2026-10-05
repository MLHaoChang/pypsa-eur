// The capital-cost badge names an annuity, so it carries `/yr`, on every
// component card and panel.
//
// `capital_cost` is PyPSA's ANNUALISED investment cost (€/MW/yr — its own
// tooltip in propertyDocs.ts says so), while `overnight_cost` is the upfront
// figure (€/MW). Both rows sat side by side with the same `€/MW` badge, so a
// reader typed an overnight number into the annuity field and the LP charged
// it every year. The badge is the only place the difference is visible
// without hovering, so it is pinned here for every read-out row.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
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
      getBuses: empty, getCarriers: empty, getGenerators: empty, getLoads: empty,
      getStorageUnits: empty, getStores: empty, getLinks: empty,
      getLines: empty, getTransformers: empty,
      getGeneratorProfiles: vi.fn(async () => ({})),
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

import PropertiesPanel, {
  GeneratorCard, StorageUnitCard, StoreCard, LinkCard, LinePanel, TransformerPanel,
} from './PropertiesPanel'

const COSTS = { capital_cost: 1000, fom_cost: 20, overnight_cost: 12000 }
const GEN = { name: 'gas', bus: 'B1', carrier: 'gas', p_nom: 100, p_nom_extendable: false,
  p_nom_min: 0, p_nom_max: null, p_min_pu: 0, p_max_pu: 1, marginal_cost: 50, efficiency: 0.5,
  committable: false, control: 'PQ', build_year: 2025, lifetime: 25, ...COSTS } as never
const SU = { name: 'bess', bus: 'B1', carrier: 'battery', p_nom: 10, p_nom_extendable: false,
  max_hours: 4, efficiency_store: 0.95, efficiency_dispatch: 0.95, marginal_cost: 1, ...COSTS } as never
const STORE = { name: 'tank', bus: 'H2', carrier: 'H2', e_nom: 100, e_nom_extendable: false,
  marginal_cost: 0, ...COSTS } as never
const LINK = { name: 'ely', bus0: 'B1', bus1: 'H2', carrier: 'H2', p_nom: 5, p_nom_extendable: false,
  efficiency: 0.7, marginal_cost: 0, ...COSTS } as never
const LINE = { name: 'L1', bus0: 'B1', bus1: 'B2', s_nom: 100, s_nom_extendable: false,
  r: 0.1, x: 0.2, b: 0, length: 10, v_nom: 110, ...COSTS } as never
const TR = { name: 'T1', bus0: 'B1', bus1: 'B2', s_nom: 100, s_nom_extendable: false,
  r: 0.01, x: 0.1, tap_ratio: 1, ...COSTS } as never

function rowText(label: string): string {
  const row = screen.getByText(label).closest('div')
  if (!row) throw new Error(`no row labelled ${label}`)
  return row.textContent ?? ''
}

function wrap(node: React.ReactNode, seed: Array<[string, unknown]>) {
  const client = new QueryClient({
    // staleTime: the seeded rows stay authoritative; the mocked getters
    // return [] and a mount refetch would otherwise drop the panel to
    // "Loading…" before an edit click lands.
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  })
  for (const [key, value] of seed) client.setQueryData(nk('Demo', key), value)
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)
}

function expectAnnuityBadges(unit: 'MW' | 'MWh' | 'MVA') {
  expect(rowText('Capital cost')).toContain(`€/${unit}/yr`)
  expect(rowText('FOM cost')).toContain(`€/${unit}/yr`)
  const overnight = rowText('Overnight cost')
  expect(overnight).toContain(`€/${unit}`)
  expect(overnight).not.toContain('/yr')
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('capital-cost badges name the annuity per year', () => {
  it('Generator card', () => {
    wrap(<GeneratorCard gen={GEN} onRename={() => {}} />, [['generators', [GEN]]])
    expectAnnuityBadges('MW')
  })
  it('StorageUnit card', () => {
    wrap(<StorageUnitCard su={SU} onRename={() => {}} />, [['storage_units', [SU]]])
    expectAnnuityBadges('MW')
  })
  it('Store card', () => {
    wrap(<StoreCard store={STORE} onRename={() => {}} />, [['stores', [STORE]]])
    expectAnnuityBadges('MWh')
  })
  it('Link card', () => {
    wrap(<LinkCard link={LINK} onRename={() => {}} />, [['links', [LINK]]])
    expectAnnuityBadges('MW')
  })
  it('Line panel', async () => {
    wrap(<LinePanel name="L1" />, [['lines', [LINE]]])
    await screen.findByText('Capital cost')
    expectAnnuityBadges('MVA')
  })
  it('Transformer panel', async () => {
    wrap(<TransformerPanel name="T1" />, [['transformers', [TR]]])
    await screen.findByText('Capital cost')
    expect(rowText('Capital cost')).toContain('€/MVA/yr')
  })
})

// Edit mode and the quick-add form label the same fields, so they carry the
// same badges. S0 fixed them without a test (gate S0 [S3]); these pin them.
// The badge sits inside the input's label as "Capital cost (€/MW/yr)".
function expectEditBadges(unit: 'MW' | 'MWh' | 'MVA') {
  expect(screen.getByText(`Capital cost (€/${unit}/yr)`)).toBeTruthy()
  expect(screen.getByText(`FOM cost (€/${unit}/yr)`)).toBeTruthy()
  // The upfront figure stays per unit, never per year.
  expect(screen.getByText(`Overnight cost (€/${unit})`)).toBeTruthy()
  expect(screen.queryByText(`Capital cost (€/${unit})`)).toBeNull()
}

describe('edit-mode capital-cost badges name the annuity per year', () => {
  it('Generator edit form', () => {
    wrap(<GeneratorCard gen={GEN} onRename={() => {}} />, [['generators', [GEN]]])
    fireEvent.click(screen.getByRole('button', { name: /^Edit / }))
    expectEditBadges('MW')
  })
  it('StorageUnit edit form', () => {
    wrap(<StorageUnitCard su={SU} onRename={() => {}} />, [['storage_units', [SU]]])
    fireEvent.click(screen.getByRole('button', { name: /^Edit / }))
    expectEditBadges('MW')
  })
  it('Store edit form', () => {
    wrap(<StoreCard store={STORE} onRename={() => {}} />, [['stores', [STORE]]])
    fireEvent.click(screen.getByRole('button', { name: /^Edit / }))
    expectEditBadges('MWh')
  })
  it('Link edit form', () => {
    wrap(<LinkCard link={LINK} onRename={() => {}} />, [['links', [LINK]]])
    fireEvent.click(screen.getByRole('button', { name: /^Edit / }))
    expectEditBadges('MW')
  })
  it('Line edit form', async () => {
    wrap(<LinePanel name="L1" />, [['lines', [LINE]]])
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Parameters' }))
    expectEditBadges('MVA')
  })
  it('Transformer edit form', async () => {
    wrap(<TransformerPanel name="T1" />, [['transformers', [TR]]])
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Parameters' }))
    expectEditBadges('MVA')
  })
})

describe('the quick-add form labels the annuity per year', () => {
  it('a Generator added from a bus asks for Capital cost (€/MW/yr)', async () => {
    useUIStore.setState({ selectedComponent: { type: 'Bus', name: 'B1' }, rightPanelOpen: true })
    wrap(<PropertiesPanel />, [
      ['buses', [{ name: 'B1', v_nom: 110, carrier: 'AC', control: 'PQ', country: '', sub_network: '', x: 0, y: 0 }]],
    ])
    fireEvent.click(await screen.findByRole('button', { name: /^Generator$/ }))
    expect(screen.getByText('Capital cost (€/MW/yr)')).toBeTruthy()
    expect(screen.queryByText('Capital cost (€/MW)')).toBeNull()
  })
})
