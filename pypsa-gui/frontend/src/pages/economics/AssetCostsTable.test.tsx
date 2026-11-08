// TCU-05: the costs table marks a cost priced through a changed scale input "out of date", with the
// reason as visible text under the table (the key to the chip), and says nothing for fresh rows.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AssetCostsTable from './AssetCostsTable'
import { useUIStore } from '../../store/uiStore'
import type { AssetCostRow, ProvenanceEntry } from '../../api/assetSchema'

const costs = vi.hoisted(() => vi.fn())
vi.mock('../../api/assetSchema', async (orig) => {
  const actual = await orig<typeof import('../../api/assetSchema')>()
  return { ...actual, assetSchemaApi: { ...actual.assetSchemaApi, costs } }
})
vi.mock('../../layout/properties/cardKit', async (orig) => ({
  ...(await orig<typeof import('../../layout/properties/cardKit')>()), useGlobalDefaultLifetime: () => 25 }))

const prov = (stale?: ProvenanceEntry['stale']): ProvenanceEntry => ({ status: 'default', source: 'pack', source_year: 2020,
  currency_year: 2020, range: null, illustrative: false, ...(stale ? { stale } : {}) })
const row = (name: string, provenance: AssetCostRow['provenance']): AssetCostRow => ({
  component: 'Generator', name, carrier: 'solar', basis: 'EUR/MW', capital_cost: 50_000, fom_cost: 0,
  parts: [{ name: 'investment', upfront_per_unit: 800_000, lifetime: 25, fom_share: 0, derived_from_capital_cost: false }],
  provenance } as unknown as AssetCostRow)

afterEach(() => { cleanup(); costs.mockReset() })

describe('AssetCostsTable out-of-date costs', () => {
  it('chips the stale row, names it under the table and leaves the fresh row alone', async () => {
    useUIStore.setState({ currentProject: 'p' } as never)
    costs.mockResolvedValue({ discount_rate: 0.07, assets: [
      row('pv_a', { overnight_cost: prov({ field: 'dc_ac_ratio', recorded: 1.3, current_field: 'dc_ac_ratio', current: 1.5,
        reason: 'scale input changed' }) }),
      row('pv_b', { overnight_cost: prov() }),
    ] })
    render(<QueryClientProvider client={new QueryClient()}><AssetCostsTable /></QueryClientProvider>)
    await waitFor(() => screen.getByTestId('asset-cost-prov-Generator-pv_a-stale'))
    expect(screen.queryByTestId('asset-cost-prov-Generator-pv_b-stale')).toBeNull()
    const note = screen.getByTestId('asset-costs-out-of-date').textContent
    expect(note).toContain('Generator · pv_a: Out of date: DC/AC ratio changed. Priced with DC/AC ratio 1.3, now 1.5.')
    expect(note).toContain('Open the asset and compare with references to re-price.')
    expect(note).not.toContain('pv_b')
  })

  it('shows no note line when nothing is out of date', async () => {
    useUIStore.setState({ currentProject: 'p' } as never)
    costs.mockResolvedValue({ discount_rate: 0.07, assets: [row('pv_b', { overnight_cost: prov() })] })
    render(<QueryClientProvider client={new QueryClient()}><AssetCostsTable /></QueryClientProvider>)
    await waitFor(() => screen.getByTestId('asset-cost-Generator-pv_b'))
    expect(screen.queryByTestId('asset-costs-out-of-date')).toBeNull()
  })
})
