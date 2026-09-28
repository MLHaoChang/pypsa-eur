// The capital-cost badge names an annuity, so it carries `/yr`.
//
// `capital_cost` is PyPSA's ANNUALISED investment cost (€/MW/yr — its own
// tooltip in propertyDocs.ts says so), while `overnight_cost` is the upfront
// figure (€/MW). Both rows sat side by side with the same `€/MW` badge, so a
// reader typed an overnight number into the annuity field and the LP charged
// it every year. The badge is the only place the difference is visible
// without hovering, so it is pinned here for the read-out rows.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
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
      getGeneratorProfiles: vi.fn(async () => ({})),
      updateGenerator: vi.fn(async () => ({ name: 'gas' })),
      deleteGenerator: vi.fn(),
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

import { GeneratorCard } from './PropertiesPanel'

const GEN = {
  name: 'gas', bus: 'B1', carrier: 'gas', p_nom: 100, p_nom_extendable: false,
  p_nom_min: 0, p_nom_max: null, p_min_pu: 0, p_max_pu: 1, marginal_cost: 50,
  capital_cost: 1000, fom_cost: 20, overnight_cost: 12000, efficiency: 0.5,
  committable: false, control: 'PQ', build_year: 2025, lifetime: 25,
} as never

function rowText(label: string): string {
  const row = screen.getByText(label).closest('div')
  if (!row) throw new Error(`no row labelled ${label}`)
  return row.textContent ?? ''
}

function renderCard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  client.setQueryData(nk('Demo', 'generators'), [GEN])
  return render(
    <QueryClientProvider client={client}>
      <GeneratorCard gen={GEN} onRename={() => {}} />
    </QueryClientProvider>,
  )
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('cost badges on the Generator card', () => {
  it('labels the annuity field per year and the overnight field without', () => {
    renderCard()
    expect(rowText('Capital cost')).toContain('€/MW/yr')
    expect(rowText('FOM cost')).toContain('€/MW/yr')
    const overnight = rowText('Overnight cost')
    expect(overnight).toContain('€/MW')
    expect(overnight).not.toContain('/yr')
  })
})
