// S1b: the Generator card renders from the asset schema (plan
// 2026-10-13-s1-expert-asset-cards.md): investment is typed as the overnight
// cost per kW and saved per MW; the annualised capital cost is derived;
// curtailment cost shows only where there is energy to spill.
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
      updateGenerator: vi.fn(async () => ({ name: 'pv' })),
      deleteGenerator: vi.fn(),
      getGeneratorProfiles: vi.fn(async () => ({})),
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

const PV = {
  name: 'pv', bus: 'B1', carrier: 'solar', control: 'PQ', p_nom: 0, p_nom_extendable: true, p_nom_min: 0,
  p_nom_max: 80, p_min_pu: 0, p_max_pu: 1, efficiency: 1, marginal_cost: 0, curtailment_cost: 0,
  overnight_cost: 482_500, lifetime: 40, fom_cost: 11_950, capital_cost: 48_098, build_year: 2027,
  committable: false,
} as never
const GAS = { ...(PV as object), name: 'gas', carrier: 'gas' } as never

function inputOf(label: string): HTMLInputElement {
  const el = screen.getByText(label).closest('label')?.querySelector('input')
  if (!el) throw new Error(`no input labelled ${label}`)
  return el as HTMLInputElement
}

async function openEdit(gen: unknown) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  client.setQueryData(nk('Demo', 'generators'), [gen])
  render(<QueryClientProvider client={client}><GeneratorCard gen={gen as never} onRename={() => {}} /></QueryClientProvider>)
  await userEvent.click(await screen.findByRole('button', { name: /edit/i }))
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(networkApi.updateGenerator).mockClear()
})
afterEach(() => { useUIStore.setState({ currentProject: null }) })

describe('a generator is priced by its overnight cost', () => {
  it('shows the overnight cost and fixed O&M per kW', async () => {
    await openEdit(PV)
    expect(inputOf('Overnight cost (€/kW)').value).toBe('482.5')
    expect(inputOf('Fixed O&M (€/kW/yr)').value).toBe('11.95')
    expect(screen.getByTestId('asset-derived-capital_cost').textContent).toContain('48.1')
  })

  it('a pack-priced unit (capital_cost 0) shows the capital cost PyPSA charges', async () => {
    await openEdit({ ...(GAS as object), overnight_cost: 581_394.9, lifetime: 25, discount_rate: 0.07,
      capital_cost: 0, fom_cost: 10_346 })
    // 581.39 €/kW × annuity(7 %, 25 y) = 49.89 €/kW/yr, not the column's 0.
    expect(screen.getByTestId('asset-derived-capital_cost').textContent).toContain('49.89')
  })

  it('saves the overnight cost per MW', async () => {
    await openEdit(PV)
    fireEvent.change(inputOf('Overnight cost (€/kW)'), { target: { value: '500' } })
    await userEvent.click(screen.getByRole('button', { name: /^save/i }))
    await waitFor(() => expect(networkApi.updateGenerator).toHaveBeenCalled())
    const body = vi.mocked(networkApi.updateGenerator).mock.calls[0][1] as Record<string, unknown>
    expect(body.overnight_cost).toBe(500_000)
    expect(body.fom_cost).toBe(11_950)
    expect(body.lifetime).toBe(40)
  })

  it('curtailment cost shows for solar, not for gas', async () => {
    await openEdit(PV)
    expect(screen.queryByText('Curtailment cost (€/MWh)')).toBeTruthy()
  })

  it('…and not for a thermal unit', async () => {
    await openEdit(GAS)
    expect(screen.queryByText('Curtailment cost (€/MWh)')).toBeNull()
  })
})
