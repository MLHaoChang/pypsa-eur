// Economics in Expert, E1 (plan 2026-10-14-economics-in-expert.md): the
// ECONOMICS step opens one panel. Basis holds the rates (the solver config's,
// the query Solver Settings reads) and IC's finance inputs, embedded as they
// are; Asset costs lists what every asset costs and opens its card.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import type { AssetCostsPayload, ProvenanceEntry } from '../../api/assetSchema'

vi.mock('../results/investment/FinanceInputsEditor', () => ({
  default: () => <div data-testid="ic-finance-inputs" />,
}))
// IC's editors, mounted as they are (E0): stubs that show what the panel passes.
vi.mock('../results/investment/SiteConnectionSection', () => ({
  default: ({ config, needed }: { config: { poc_link?: string } | null; needed: boolean }) =>
    <div data-testid="ic-site" data-needed={String(needed)} data-poc={config?.poc_link ?? ''} />,
}))
vi.mock('../results/investment/TariffSection', () => ({ default: () => <div data-testid="ic-tariff" /> }))
vi.mock('../results/investment/ContractsEditor', () => ({ default: () => <div data-testid="ic-contracts" /> }))
vi.mock('../results/investment/ParticipantsDesigner', () => ({ default: () => <div data-testid="ic-participants" /> }))
vi.mock('../results/investment/LibraryBrowser', () => ({ default: () => <div data-testid="ic-library" /> }))
const getCommercial = vi.hoisted(() => vi.fn())
vi.mock('../../api/commercial', async (orig) => {
  const actual = await orig<typeof import('../../api/commercial')>()
  return { ...actual, commercialApi: { ...actual.commercialApi, getCommercial } }
})
const costs = vi.hoisted(() => vi.fn())
vi.mock('../../api/assetSchema', async (orig) => {
  const actual = await orig<typeof import('../../api/assetSchema')>()
  return { ...actual, assetSchemaApi: { ...actual.assetSchemaApi, costs } }
})
const update = vi.hoisted(() => vi.fn(async (c: unknown) => c))
vi.mock('../../api/simulation', async (orig) => {
  const actual = await orig<typeof import('../../api/simulation')>()
  return {
    ...actual,
    simulationApi: { ...actual.simulationApi, getSolverConfig: vi.fn(async () => ({})), updateSolverConfig: update },
  }
})

import EconomicsPanel from './EconomicsPanel'
import { applyUiNavigate } from '../../components/uiNavigate'

const STAMP: ProvenanceEntry = { status: 'customised', source: null, source_year: null, currency_year: null,
  range: null, illustrative: false }
const base = { carrier: '', size_column: 'p_nom', size: 10, extendable: false, fom_cost: null, provenance: {} }
const PAYLOAD: AssetCostsPayload = {
  discount_rate: 0.07,
  assets: [
    { ...base, component: 'StorageUnit', name: 'bat', basis: 'EUR/MW', upfront_per_unit: 950_000,
      upfront_total: 9.5e6, capital_cost: 104_000,
      parts: [
        { name: 'power', upfront_per_unit: 150_000, lifetime: 15, fom_share: 0.01, derived_from_capital_cost: false },
        { name: 'energy', upfront_per_unit: 800_000, lifetime: 15, fom_share: 0.02, derived_from_capital_cost: false },
      ] },
    { ...base, component: 'Generator', name: 'legacy', basis: 'EUR/MW', upfront_per_unit: 993_000,
      upfront_total: 9.93e6, capital_cost: 80_000, provenance: { overnight_cost: STAMP },
      parts: [{ name: 'investment', upfront_per_unit: 993_000, lifetime: 30, fom_share: null,
                derived_from_capital_cost: true }] },
    { ...base, component: 'Line', name: 'ab', basis: 'EUR/MVA', size_column: 's_nom', upfront_per_unit: 20_000,
      upfront_total: 2e6, capital_cost: 1500,
      parts: [{ name: 'per_km', upfront_per_unit: 20_000, lifetime: null, fom_share: 0.02,
                derived_from_capital_cost: false }] },
    { ...base, component: 'Generator', name: 'free', basis: 'EUR/MW', upfront_per_unit: null, upfront_total: null,
      capital_cost: 0, parts: [] },
  ],
}

function renderPanel(cfg: Record<string, unknown> = { discount_rate: 0.07, inflation_rate: 0 }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  qc.setQueryData(nk('Demo', 'solverConfig'), cfg)
  render(<QueryClientProvider client={qc}><EconomicsPanel /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: 'economics', selectedComponent: null })
  costs.mockResolvedValue(PAYLOAD)
  getCommercial.mockResolvedValue({})
})
afterEach(() => { cleanup(); vi.clearAllMocks(); useUIStore.setState({ currentProject: null, activeSlidePanel: null }) })

describe('the Basis tab', () => {
  it('embeds IC’s finance inputs beside the rates', () => {
    renderPanel()
    expect(screen.getByTestId('ic-finance-inputs')).toBeTruthy()
    expect((screen.getByLabelText('Discount rate (%)') as HTMLInputElement).value).toBe('7')
  })

  it('saves only the two rates, as shares, through the solver config', async () => {
    renderPanel({ discount_rate: 0.07, inflation_rate: 0.02, solver_name: 'highs' })
    fireEvent.change(screen.getByLabelText('Discount rate (%)'), { target: { value: '5.5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save rates' }))
    await waitFor(() => expect(update).toHaveBeenCalled())
    const sent = update.mock.calls[0][0] as Record<string, number>
    expect(Object.keys(sent).sort()).toEqual(['discount_rate', 'inflation_rate'])
    expect(sent.discount_rate).toBeCloseTo(0.055, 12)
    expect(sent.inflation_rate).toBeCloseTo(0.02, 12)
  })

  it('cannot save an unchanged or empty rate', () => {
    renderPanel()
    const save = screen.getByRole('button', { name: 'Save rates' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('Discount rate (%)'), { target: { value: '' } })
    expect(save.disabled).toBe(true)
  })
})

describe('the Asset costs tab', () => {
  async function openTab() {
    renderPanel()
    fireEvent.click(screen.getByRole('tab', { name: 'Asset costs' }))
    await screen.findByTestId('asset-cost-StorageUnit-bat')
  }

  it('shows a battery’s two parts per kW, as on its card', async () => {
    await openTab()
    const row = screen.getByTestId('asset-cost-StorageUnit-bat')
    expect(row.textContent).toContain('power 150 + energy 800 €/kW')
    expect(row.textContent).toContain('104 €/kW/yr')
  })

  it('keeps branch money per MVA and a lifetime that never ends as never', async () => {
    await openTab()
    const row = screen.getByTestId('asset-cost-Line-ab')
    expect(row.textContent).toContain('20,000')
    expect(row.textContent).toContain('€/MVA')
    expect(row.textContent).toContain('never')
  })

  it('flags a cost back-calculated from a typed capital cost, with its provenance chip', async () => {
    await openTab()
    const row = screen.getByTestId('asset-cost-Generator-legacy')
    expect(within(row).getByText('from capital cost')).toBeTruthy()
    expect(screen.getByTestId('asset-cost-prov-Generator-legacy').getAttribute('data-status')).toBe('customised')
    expect(screen.queryByTestId('asset-cost-prov-StorageUnit-bat')).toBeNull()      // nothing recorded, no chip
  })

  it('says which assets are not priced', async () => {
    await openTab()
    expect(screen.getByTestId('asset-cost-Generator-free').textContent).toContain('not priced')
    expect(screen.getByText(/1 not priced/)).toBeTruthy()
  })

  it('a row opens the asset’s card: the panel closes and the asset is selected', async () => {
    await openTab()
    fireEvent.click(screen.getByTestId('asset-cost-StorageUnit-bat'))
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'StorageUnit', name: 'bat' })
  })
})

describe('navigation', () => {
  it.each(['Economics', 'economics'])('the assistant’s ui_open_panel %s opens the panel', (id) => {
    useUIStore.setState({ activeSlidePanel: null })
    applyUiNavigate({ kind: 'navigate', panel_id: id })
    expect(useUIStore.getState().activeSlidePanel).toBe('economics')
  })
})

describe('E2: the site connection and IC’s editors', () => {
  it('a project with no site connection gets the form on the Basis tab, before any solve', async () => {
    getCommercial.mockResolvedValue({})
    renderPanel()
    const site = await screen.findByTestId('ic-site')
    expect(site.getAttribute('data-needed')).toBe('true')
    expect(getCommercial).toHaveBeenCalled()
  })

  it('a project with one gets its summary', async () => {
    getCommercial.mockResolvedValue({ poc_link: 'grid_import' })
    renderPanel()
    const site = await screen.findByTestId('ic-site')
    expect(site.getAttribute('data-needed')).toBe('false')
    expect(site.getAttribute('data-poc')).toBe('grid_import')
  })

  it('the Tariffs & contracts tab mounts the tariff, contracts and participants editors', () => {
    renderPanel()
    fireEvent.click(screen.getByRole('tab', { name: 'Tariffs & contracts' }))
    for (const id of ['ic-tariff', 'ic-contracts', 'ic-participants']) expect(screen.getByTestId(id)).toBeTruthy()
  })

  it('the Library tab mounts the library browser', () => {
    renderPanel()
    fireEvent.click(screen.getByRole('tab', { name: 'Library' }))
    expect(screen.getByTestId('ic-library')).toBeTruthy()
  })
})
