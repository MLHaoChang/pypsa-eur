import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { COST_UNAVAILABLE } from './results/shared'

vi.mock('../api/projects', () => ({
  projectsApi: { resultsSummary: vi.fn() },
}))

import { projectsApi } from '../api/projects'
import { EconomicsTab } from './CompareView'

/**
 * The plan's mock shape (`economics.total_cost`) was a placeholder — the real
 * `EconomicsComparison` carries `by_carrier` keyed by carrier, each with
 * CarrierPeriodValue buckets. Corrected against `models/schemas.py` and the
 * `EconomicsTab` body rather than propagated.
 */
const pv = (total: number) => ({ total, by_period: {} })

const summary = (available: boolean) => ({
  project: 'alpha',
  is_multi_period: false,
  periods: [],
  has_solve: true,
  economics: {
    available,
    by_carrier: {
      gas: {
        revenue_meur: pv(available ? 1234.5 : 0),
        opex_meur: pv(available ? 500 : 0),
        gen_cost_meur: pv(0),
        storage_charge_cost_meur: pv(0),
        curtailment_cost_meur: pv(0),
        lost_load_cost_meur: pv(0),
        capex_meur: pv(available ? 700 : 0),
        dispatch_gwh: pv(available ? 10 : 0),
        lcoe_eur_per_mwh: pv(available ? 42 : 0),
      },
    },
    per_asset_lcoh: [],
  },
})

function renderTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <EconomicsTab a="alpha" b="beta" />
    </QueryClientProvider>,
  )
}

describe('Compare tabs distinguish unavailable from zero', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders the unavailable marker, never a zero, when the block did not resolve', async () => {
    vi.mocked(projectsApi.resultsSummary).mockResolvedValue(summary(false) as never)
    renderTab()
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    expect(screen.queryByText(/0\.00/)).toBeNull()
  })

  it('treats a payload with NO flag as unavailable, not as available', async () => {
    // A response from an older backend carries no `available` at all. Coalescing
    // that to available reintroduces the whole defect, so the guard is
    // `!== true` rather than `=== false`. The first implementation of this used
    // `=== false` and let such a payload through.
    const legacy = summary(true) as Record<string, any>
    delete legacy.economics.available
    vi.mocked(projectsApi.resultsSummary).mockResolvedValue(legacy as never)
    renderTab()
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
  })

  it('renders the figures when the block resolved', async () => {
    vi.mocked(projectsApi.resultsSummary).mockResolvedValue(summary(true) as never)
    renderTab()
    expect(await screen.findAllByText(/1,?234/)).not.toHaveLength(0)
    expect(screen.queryByText(COST_UNAVAILABLE)).toBeNull()
  })
})
