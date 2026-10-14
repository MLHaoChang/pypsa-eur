// The capital-cost badge names an annuity, so it carries `/yr`, on every
// component card and panel.
//
// `capital_cost` is PyPSA's ANNUALISED investment cost (€/MW/yr — its
// descriptor's help says so), while `overnight_cost` is the upfront
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
// A battery priced as its two parts, derived as S0's `derive_composite` does.
const SU_PARTS = { ...(SU as object), overnight_cost: null, capital_cost: 95_712, fom_cost: 722, lifetime: 25,
  inv_power_overnight: 213_900, inv_power_lifetime: 10, inv_power_fom_share: 0.0034,
  inv_energy_overnight: 189_900, inv_energy_lifetime: 25, inv_energy_fom_share: 0 } as never
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

// The read-out of a schema-rendered card (S1b, S1c).
function expectSchemaBadges(unit: 'kW' | 'kWh') {
  expect(rowText('Capital cost (annualised)')).toContain(`€/${unit}/yr`)
  expect(rowText('Fixed O&M')).toContain(`€/${unit}/yr`)
  const overnight = rowText('Overnight cost')
  expect(overnight).toContain(`€/${unit}`)
  expect(overnight).not.toContain('/yr')
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('capital-cost badges name the annuity per year', () => {
  // S1b: the Generator card renders from the asset schema: money per kW, the
  // annualised capital cost derived (overnight × annuity + fixed O&M).
  it('Generator card', () => {
    wrap(<GeneratorCard gen={GEN} onRename={() => {}} />, [['generators', [GEN]]])
    expect(rowText('Capital cost (annualised)')).toContain('€/kW/yr')
    expect(rowText('Fixed O&M')).toContain('€/kW/yr')
    const overnight = rowText('Overnight cost')
    expect(overnight).toContain('€/kW')
    expect(overnight).not.toContain('/yr')
  })
  // S1b: the StorageUnit card renders from the asset schema. Money reads per
  // kW (§3.4 of the asset-parameterisation spec); the annualised capital cost
  // is derived and says so; the battery's investment is its two parts.
  it('StorageUnit card', () => {
    wrap(<StorageUnitCard su={SU_PARTS} onRename={() => {}} />, [['storage_units', [SU_PARTS]]])
    const cc = rowText('Capital cost (annualised)')
    expect(cc).toContain('€/kW/yr')
    expect(cc).toContain('95.71')
    expect(rowText('Power part: overnight cost')).toContain('€/kW')
    expect(rowText('Energy part: overnight cost')).toContain('€/kWh')
  })
  // S1c: the Store and Link cards render from the asset schema too: money per
  // kWh (a Store sizes energy) or per kW (a Link sizes power).
  it('Store card', () => {
    wrap(<StoreCard store={STORE} onRename={() => {}} />, [['stores', [STORE]]])
    expectSchemaBadges('kWh')
  })
  it('Link card', () => {
    wrap(<LinkCard link={LINK} onRename={() => {}} />, [['links', [LINK]]])
    expectSchemaBadges('kW')
  })
  // S1c-2: the Line and Transformer panels render from the asset schema.
  // Money stays per MVA (catalogues publish branch costs that way); a line's
  // investment is typed per km, so its read-out shows the derived totals.
  it('Line panel', async () => {
    wrap(<LinePanel name="L1" />, [['lines', [LINE]]])
    await screen.findByText('Capital cost (annualised)')
    expect(rowText('Capital cost (annualised)')).toContain('€/MVA/yr')
    expect(rowText('Fixed O&M (total)')).toContain('€/MVA/yr')
  })
  it('Transformer panel', async () => {
    wrap(<TransformerPanel name="T1" />, [['transformers', [TR]]])
    await screen.findByText('Capital cost (annualised)')
    expect(rowText('Capital cost (annualised)')).toContain('€/MVA/yr')
    expect(rowText('Fixed O&M')).toContain('€/MVA/yr')
    const overnight = rowText('Overnight cost')
    expect(overnight).toContain('€/MVA')
    expect(overnight).not.toContain('/yr')
  })
})

// Edit mode and the quick-add form label the same fields, so they carry the
// same badges. S0 fixed them without a test (gate S0 [S3]); these pin them.
// A schema-rendered card types the overnight cost and fixed O&M; the
// annualised capital cost is a derived row with no input.
function expectSchemaEditBadges(unit: 'kW' | 'kWh') {
  expect(screen.getByText(`Overnight cost (€/${unit})`)).toBeTruthy()
  expect(screen.getByText(`Fixed O&M (€/${unit}/yr)`)).toBeTruthy()
  const cc = screen.getByTestId('asset-derived-capital_cost')
  expect(cc.textContent).toContain(`€/${unit}/yr`)
  expect(cc.querySelector('input')).toBeNull()
}

describe('edit-mode capital-cost badges name the annuity per year', () => {
  it('Generator edit form', () => {
    wrap(<GeneratorCard gen={GEN} onRename={() => {}} />, [['generators', [GEN]]])
    fireEvent.click(screen.getByRole('button', { name: /^Edit / }))
    expect(screen.getByText('Overnight cost (€/kW)')).toBeTruthy()
    expect(screen.getByText('Fixed O&M (€/kW/yr)')).toBeTruthy()
    // Derived, read-only: no typed annualised capital cost.
    expect(screen.getByTestId('asset-derived-capital_cost').textContent).toContain('€/kW/yr')
    expect(screen.getByTestId('asset-derived-capital_cost').querySelector('input')).toBeNull()
  })
  it('StorageUnit edit form', () => {
    wrap(<StorageUnitCard su={SU_PARTS} onRename={() => {}} />, [['storage_units', [SU_PARTS]]])
    fireEvent.click(screen.getByRole('button', { name: /^Edit / }))
    // The parts are typed per kW / kWh; the annualised cost is derived, read-only.
    expect(screen.getByLabelText('Power part: overnight cost (€/kW)')).toBeTruthy()
    expect(screen.getByLabelText('Energy part: overnight cost (€/kWh)')).toBeTruthy()
    expect(screen.getByTestId('asset-derived-capital_cost').textContent).toContain('€/kW/yr')
    expect(screen.queryByText(/^Capital cost \(€\/MW\/yr\)$/)).toBeNull()
  })
  it('Store edit form', () => {
    wrap(<StoreCard store={STORE} onRename={() => {}} />, [['stores', [STORE]]])
    fireEvent.click(screen.getByRole('button', { name: /^Edit / }))
    expectSchemaEditBadges('kWh')
  })
  it('Link edit form', () => {
    wrap(<LinkCard link={LINK} onRename={() => {}} />, [['links', [LINK]]])
    fireEvent.click(screen.getByRole('button', { name: /^Edit / }))
    expectSchemaEditBadges('kW')
  })
  it('Line edit form', async () => {
    wrap(<LinePanel name="L1" />, [['lines', [LINE]]])
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Parameters' }))
    expect(screen.getByText('Overnight cost per km (€/MVA/km)')).toBeTruthy()
    const cc = screen.getByTestId('asset-derived-capital_cost')
    expect(cc.textContent).toContain('€/MVA/yr')
    expect(cc.querySelector('input')).toBeNull()
  })
  it('Transformer edit form', async () => {
    wrap(<TransformerPanel name="T1" />, [['transformers', [TR]]])
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Parameters' }))
    expect(screen.getByText('Overnight cost (€/MVA)')).toBeTruthy()
    expect(screen.getByText('Fixed O&M (€/MVA/yr)')).toBeTruthy()
    const cc = screen.getByTestId('asset-derived-capital_cost')
    expect(cc.textContent).toContain('€/MVA/yr')
    expect(cc.querySelector('input')).toBeNull()
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
