// S1c-2: the Line and Transformer panels render from the asset schema
// (services/asset_schema/fields.py, plan 2026-10-13-s1-expert-asset-cards.md).
// A line's investment is typed per MVA and km; the backend derives its
// annualised cost from that times the length, so the panel never types it.
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
      getBuses: empty, getCarriers: empty, getLines: empty, getTransformers: empty,
      updateLine: vi.fn(async () => ({ name: 'L1' })),
      updateTransformer: vi.fn(async () => ({ name: 'T1' })),
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
import { LinePanel, TransformerPanel } from './PropertiesPanel'

// 100 km, priced per km: the backend derived capital_cost = 432 × 100 × annuity(7 %, 40 y).
const LINE = { name: 'L1', bus0: 'B1', bus1: 'B2', carrier: 'AC', s_nom: 500, s_nom_extendable: true,
  s_nom_min: 0, s_nom_max: null, length: 100, r: 3, x: 30, b: 0.0001, lifetime: 40, build_year: 2030,
  inv_per_km_overnight: 432, inv_per_km_fom_share: 0.02, overnight_cost: null,
  capital_cost: 3240.4, fom_cost: 864 } as never
// A line priced the old way: one overnight cost per MVA, no per-km cost.
const LEGACY_LINE = { ...(LINE as object), inv_per_km_overnight: null, inv_per_km_fom_share: null,
  overnight_cost: 43_200 } as never
const TR = { name: 'T1', bus0: 'HV', bus1: 'LV', s_nom: 400, s_nom_extendable: false, s_nom_min: 0,
  s_nom_max: null, r: 0.002, x: 0.12, tap_ratio: 1, phase_shift: 0, v_nom_0: 380, v_nom_1: 110,
  overnight_cost: 15_000, capital_cost: 1200, fom_cost: 50, lifetime: 40 } as never

function inputOf(label: string): HTMLInputElement {
  return screen.getByLabelText(label) as HTMLInputElement
}

async function openEdit(node: React.ReactNode, seed: Array<[string, unknown]>) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  })
  for (const [key, value] of seed) qc.setQueryData(nk('Demo', key), value)
  render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
  await userEvent.click(await screen.findByRole('button', { name: 'Edit Parameters' }))
}

async function sent(fn: (...a: never[]) => unknown): Promise<Record<string, unknown>> {
  await userEvent.click(screen.getByRole('button', { name: /^save/i }))
  await waitFor(() => expect(fn).toHaveBeenCalled())
  return vi.mocked(fn).mock.calls[0][1] as Record<string, unknown>
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(networkApi.updateLine).mockClear()
  vi.mocked(networkApi.updateTransformer).mockClear()
})
afterEach(() => { useUIStore.setState({ currentProject: null }) })

describe('the Line panel', () => {
  it('types the investment per km and shows the annualised cost read-only', async () => {
    await openEdit(<LinePanel name="L1" />, [['lines', [LINE]]])
    expect(inputOf('Overnight cost per km (€/MVA/km)').value).toBe('432')
    expect(inputOf('Fixed O&M (%)').value).toBe('2')
    expect(inputOf('Lifetime (years)').value).toBe('40')
    const cc = screen.getByTestId('asset-derived-capital_cost')
    expect(cc.querySelector('input')).toBeNull()
    expect(cc.textContent).toContain('3,240')
  })

  it('saves the per-km cost, and never a typed capital cost', async () => {
    await openEdit(<LinePanel name="L1" />, [['lines', [LINE]]])
    fireEvent.change(inputOf('Overnight cost per km (€/MVA/km)'), { target: { value: '500' } })
    const body = await sent(networkApi.updateLine)
    expect(body.inv_per_km_overnight).toBe(500)
    expect(body.inv_per_km_fom_share).toBeCloseTo(0.02, 12)
    // The PUT carries the whole row: the derived columns go back unchanged and
    // the backend re-derives them from the part and the length.
    expect(body.capital_cost).toBe(3240.4)
  })

  it('scales r, x and b typed per km by the length being saved', async () => {
    await openEdit(<LinePanel name="L1" />, [['lines', [LINE]]])
    expect(inputOf('Resistance r (Ω/km)').value).toBe('0.03')
    fireEvent.change(inputOf('Length (km)'), { target: { value: '50' } })
    fireEvent.change(inputOf('Reactance x (Ω/km)'), { target: { value: '0.4' } })
    const body = await sent(networkApi.updateLine)
    expect(body.length).toBe(50)
    expect(body.r).toBeCloseTo(1.5, 12)
    expect(body.x).toBeCloseTo(20, 12)
  })

  it('keeps the topology read-only', async () => {
    await openEdit(<LinePanel name="L1" />, [['lines', [LINE]]])
    expect(screen.queryByLabelText(/^From bus/)).toBeNull()
    expect(screen.queryByLabelText(/^To bus/)).toBeNull()
    const body = await sent(networkApi.updateLine)
    expect(body.bus0).toBe('B1')
    expect(body.bus1).toBe('B2')
  })

  it('a line priced the old way says so, and one priced per km does not', async () => {
    await openEdit(<LinePanel name="L1" />, [['lines', [LEGACY_LINE]]])
    const note = screen.getByTestId('legacy-overnight-note')
    expect(note.textContent).toContain('43,200')
    expect(note.textContent).toContain('Enter the cost per km')
  })

  it('a line priced per km shows no legacy note', async () => {
    await openEdit(<LinePanel name="L1" />, [['lines', [LINE]]])
    expect(screen.queryByTestId('legacy-overnight-note')).toBeNull()
  })
})

describe('the Transformer panel', () => {
  it('types the overnight cost per MVA and shows the annualised cost read-only', async () => {
    await openEdit(<TransformerPanel name="T1" />, [['transformers', [TR]]])
    expect(inputOf('Overnight cost (€/MVA)').value).toBe('15000')
    expect(inputOf('Reactance (pu)').value).toBe('0.12')
    expect(screen.getByTestId('asset-derived-capital_cost').querySelector('input')).toBeNull()
    expect(screen.queryByLabelText(/^HV bus/)).toBeNull()
  })

  it('saves the typed fields and sends the declared voltages back', async () => {
    await openEdit(<TransformerPanel name="T1" />, [['transformers', [TR]]])
    fireEvent.change(inputOf('Overnight cost (€/MVA)'), { target: { value: '18000' } })
    fireEvent.change(inputOf('Declared LV voltage (kV)'), { target: { value: '' } })
    const body = await sent(networkApi.updateTransformer)
    expect(body.overnight_cost).toBe(18_000)
    expect(body.v_nom_0).toBe(380)
    expect(body.v_nom_1).toBeNull()                  // cleared: no declared voltage to check
    expect(body.tap_ratio).toBe(1)
    expect(body.bus0).toBe('HV')
  })
})
