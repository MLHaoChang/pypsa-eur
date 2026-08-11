// Task 5: CompareView had no unavailable branch at all, so Task 4's
// `available: boolean` flags (added to every Comparison block on
// ResultsSummary — see backend/models/schemas.py) changed nothing a user
// saw. A solved-but-unresolved figure still rendered as a confident €0.00,
// indistinguishable from a real zero — exactly what ADR-0001 forbids.
//
// EconomicsTab is exported (CompareView.tsx) solely so this test can render
// it in isolation, following the render/mock/QueryClientProvider recipe in
// PropertiesPanel.rescale.test.tsx:55-63.
//
// Mock shape verified against backend/models/schemas.py: EconomicsComparison
// has no `total_cost` field (that was the brief's placeholder) — it carries
// `available` + `by_carrier: dict[str, CarrierEconomics]` +
// `per_asset_lcoh`, nested under ResultsSummary.economics. `by_carrier` is
// left empty in the unavailable case to match the real backend contract
// (every dict on an unresolved block defaults to `{}` — see the "False
// means this block resolved nothing" docstrings), which exercises
// EconomicsTab's own "no economic data" fallback path and proves it now
// reports unavailability rather than reusing that unrelated empty-fleet
// message. `has_solve: true` is required — EconomicsTab bails out to
// UnsolvedBanner otherwise, which would make both cases render identical
// prose and the test pass no matter what the `available` branch did.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { COST_UNAVAILABLE } from './results/shared'

vi.mock('../api/projects', () => ({
  projectsApi: { resultsSummary: vi.fn() },
}))

import { projectsApi } from '../api/projects'
import { EconomicsTab } from './CompareView'

const summary = (available: boolean, project: string) => ({
  project,
  has_solve: true,
  periods: [],
  economics: {
    available,
    by_carrier: available
      ? {
          gas: {
            revenue_meur: { total: 1234.5, by_period: {} },
            opex_meur: { total: 0, by_period: {} },
            gen_cost_meur: { total: 0, by_period: {} },
            storage_charge_cost_meur: { total: 0, by_period: {} },
            curtailment_cost_meur: { total: 0, by_period: {} },
            lost_load_cost_meur: { total: 0, by_period: {} },
            capex_meur: { total: 0, by_period: {} },
            dispatch_gwh: { total: 0, by_period: {} },
            lcoe_eur_per_mwh: { total: 0, by_period: {} },
          },
        }
      : {},
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
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (name: string) => Promise.resolve(summary(false, name) as never),
    )
    renderTab()
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    expect(screen.queryByText(/0\.00/)).toBeNull()
  })

  it('renders the figure when the block resolved', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (name: string) => Promise.resolve(summary(true, name) as never),
    )
    renderTab()
    expect(await screen.findByText(/1,?234/)).toBeTruthy()
    expect(screen.queryByText(COST_UNAVAILABLE)).toBeNull()
  })

  it('renders the resolved side\'s real figure and marks only the unresolved side unavailable', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (name: string) => Promise.resolve(summary(name === 'alpha', name) as never),
    )
    renderTab()
    // alpha resolved -> its revenue cell shows the real figure.
    expect(await screen.findByText(/1,?234/)).toBeTruthy()
    // beta did not resolve -> at least one cell reads the marker, not 0.00.
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    expect(screen.queryByText(/0\.00/)).toBeNull()
  })
})
