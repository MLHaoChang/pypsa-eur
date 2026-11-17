import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useAssetSchema } from './useAssetSchema'
import { assetSchemaApi } from '../api/assetSchema'

vi.unmock('./useAssetSchema')
vi.mock('../api/assetSchema', () => ({ assetSchemaApi: { get: vi.fn(), units: vi.fn() } }))
import { useUIStore } from '../store/uiStore'
import { buildPatch, seedForm } from '../utils/assetForm'

const field = (key: string, unit_display: string, scale: number) =>
  ({ key, unit_display, unit_storage: unit_display, scale, kind: 'number', derived: false }) as never

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('useAssetSchema cost units on a tonne bus (ui-modern 05d)', () => {
  it('shows per-tonne units with scale 1 for a cost field, and leaves an energy bus alone', async () => {
    useUIStore.setState({ currentProject: 'Demo' })
    vi.mocked(assetSchemaApi.get).mockResolvedValue({
      component: 'Generator',
      groups: [{ id: 'investment', label: 'x', fields: [field('overnight_cost', 'EUR/kW', 0.001), field('marginal_cost', 'EUR/MWh', 1)] }],
    } as never)
    const units = vi.mocked(assetSchemaApi.units)
    units.mockResolvedValue({ component: 'Generator', name: 'g', units: {},
      costs: { overnight_cost: { unit: 'EUR/(t/h)', scale: 1 }, marginal_cost: { unit: 'EUR/t', scale: 1 } } })
    const t = renderHook(() => useAssetSchema('Generator', 'g'), { wrapper: wrapper() })
    await waitFor(() => expect(t.result.current.data?.groups[0].fields[0].unit_display).toBe('EUR/(t/h)'))
    const [oc, mc] = t.result.current.data!.groups[0].fields
    expect([oc.scale, oc.unit_storage, mc.unit_display]).toEqual([1, 'EUR/(t/h)', 'EUR/t'])
    // write path: 123.4 EUR/t is stored as exactly 123.4; stored 0.1234 reads back as 0.1234
    expect(buildPatch([oc], { overnight_cost: '123.4' }, { overnight_cost: 0 })).toEqual({ overnight_cost: 123.4 })
    expect(seedForm([oc], { overnight_cost: 0.1234 })).toEqual({ overnight_cost: '0.1234' })
    expect(oc.unit_note).toMatch(/stored as entered/)

    units.mockResolvedValue({ component: 'Generator', name: 'e', units: {}, costs: {} })
    const e = renderHook(() => useAssetSchema('Generator', 'e'), { wrapper: wrapper() })
    await waitFor(() => expect(units).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(e.result.current.data).toBeTruthy())
    expect(e.result.current.data?.groups[0].fields[0]).toMatchObject({ unit_display: 'EUR/kW', scale: 0.001 })
    expect(e.result.current.data?.groups[0].fields[0].unit_note).toBeUndefined()
    // an energy asset still stores x1000 from EUR/kW
    expect(buildPatch([e.result.current.data!.groups[0].fields[0]], { overnight_cost: '123.4' }, { overnight_cost: 0 })).toEqual({ overnight_cost: 123400 })
  })
})
