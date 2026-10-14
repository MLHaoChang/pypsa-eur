// The S1c follow-up to U3's provenance chips: the Store, Load, Link, Line and
// Transformer views read their asset's stamps too (`useAssetProvenance`), and
// show each one in the read-out and on its field in the edit form.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import type { ProvenanceEntry } from '../api/assetSchema'

const provenance = vi.hoisted(() => vi.fn())
vi.mock('../api/assetSchema', async (orig) => {
  const actual = await orig<typeof import('../api/assetSchema')>()
  return { ...actual, assetSchemaApi: { ...actual.assetSchemaApi, provenance } }
})
vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  const empty = vi.fn(async () => [])
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: empty, getCarriers: empty, getStores: empty, getLines: empty,
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

import { LinePanel, StoreCard } from './PropertiesPanel'

const STAMP: ProvenanceEntry = { status: 'default', source: 'generic-defaults 1.0 abc', source_year: 2020,
  currency_year: 2020, range: null, illustrative: false }
const STORE = { name: 'tank', bus: 'H2', carrier: 'H2', e_nom: 100, e_nom_extendable: false,
  overnight_cost: 12_000, capital_cost: 1_000, fom_cost: 200, marginal_cost: 0, lifetime: 25 } as never
const LINE = { name: 'L1', bus0: 'B1', bus1: 'B2', carrier: 'AC', s_nom: 500, s_nom_extendable: false,
  length: 100, r: 3, x: 30, b: 0, lifetime: 40, inv_per_km_overnight: 432, inv_per_km_fom_share: 0.02,
  capital_cost: 3240, fom_cost: 864 } as never

function renderWith(node: React.ReactNode, seed: Array<[string, unknown]>) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  })
  for (const [key, value] of seed) qc.setQueryData(nk('Demo', key), value)
  render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => { useUIStore.setState({ currentProject: null }); provenance.mockReset() })

describe('the S1c views show their provenance', () => {
  it('a Store card reads its stamps and shows the chip in the read-out and on the field', async () => {
    provenance.mockResolvedValue({ component: 'Store', name: 'tank', fields: { overnight_cost: STAMP } })
    renderWith(<StoreCard store={STORE} mode="detail" onRename={() => {}} />, [['stores', [STORE]]])
    await waitFor(() => expect(screen.getByTestId('asset-prov-overnight_cost')).toBeTruthy())
    expect(provenance).toHaveBeenCalledWith('Store', 'tank')
    await userEvent.click(screen.getByRole('button', { name: /^Edit Store/ }))
    const chip = await screen.findByTestId('asset-prov-overnight_cost')
    expect(chip.closest('label')?.querySelector('input')).toBeTruthy()
  })

  it('a Line panel reads its stamps and shows the chip on the per-km cost', async () => {
    provenance.mockResolvedValue({ component: 'Line', name: 'L1', fields: { inv_per_km_overnight: STAMP } })
    renderWith(<LinePanel name="L1" />, [['lines', [LINE]]])
    await waitFor(() => expect(screen.getByTestId('asset-prov-inv_per_km_overnight')).toBeTruthy())
    expect(provenance).toHaveBeenCalledWith('Line', 'L1')
  })

  it('an asset without stamps shows no chip', async () => {
    provenance.mockResolvedValue({ component: 'Store', name: 'tank', fields: {} })
    renderWith(<StoreCard store={STORE} mode="detail" onRename={() => {}} />, [['stores', [STORE]]])
    await waitFor(() => expect(provenance).toHaveBeenCalled())
    expect(document.querySelectorAll('[data-testid^="asset-prov-"]')).toHaveLength(0)
  })
})
