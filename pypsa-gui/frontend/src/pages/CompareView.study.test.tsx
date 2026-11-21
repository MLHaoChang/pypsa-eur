// Issue 23 slice (a): Compare shows each side's saved Guided study headline,
// base against a what-if child; one side alone gets a muted line naming it.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('../api/projects', () => ({
  projectsApi: { resultsSummary: vi.fn(), compareState: vi.fn() },
}))

import { projectsApi } from '../api/projects'
import { OverviewTab } from './CompareView'
import type { CompareStudy } from '../api/types'

const study = (over: Partial<CompareStudy> = {}): CompareStudy => ({
  state: 'current',
  cost_at_target_eur: 1_234_567,
  period_basis: 'single_period',
  achieved_ens_permyriad: 0.4,
  ens_cap_permyriad: 1,
  mc_lole_h: 2.5,
  certified: true,
  ...over,
})

const state = (name: string, eh_study: CompareStudy | null) => ({
  name,
  bus_count: 1, generator_count: 1, line_count: 0, load_count: 1,
  link_count: 0, storage_unit_count: 0, store_count: 0, snapshot_count: 2,
  snapshot_start: null, snapshot_end: null,
  installed_capacity_by_carrier: {}, optimised_capacity_by_carrier: null,
  storage_capacity_by_carrier: {}, store_capacity_by_carrier: {},
  peak_demand_mw: 5, total_energy_mwh: 10,
  objective: null, solve_time: null, dispatch_status: 'none',
  parent_project: null, scenario_description: null,
  created_at: null, last_saved: null,
  eh_study,
})

const summary = (project: string) => ({
  project, has_solve: false, periods: [],
  capacity: {
    available: false, capacity_mw_by_carrier: {}, capex_meur_by_carrier: {},
    new_capex_meur_by_carrier: {}, new_capacity_mw_by_carrier: {},
    storage_mw_by_carrier: {}, storage_mwh_by_carrier: {},
    new_storage_mw_by_carrier: {}, new_storage_mwh_by_carrier: {},
    link_capacity_mw_by_carrier: {}, new_link_capacity_mw_by_carrier: {},
  },
})

function renderWith(a: CompareStudy | null, b: CompareStudy | null) {
  vi.mocked(projectsApi.compareState).mockImplementation(
    (p: string) => Promise.resolve(state(p, p === 'Base' ? a : b) as never),
  )
  vi.mocked(projectsApi.resultsSummary).mockImplementation(
    (p: string) => Promise.resolve(summary(p) as never),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <OverviewTab a="Base" b="What if" />
    </QueryClientProvider>,
  )
}

describe('Compare: Guided study headline', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows both studies side by side with plain labels, equal numbers and no delta', async () => {
    renderWith(study(), study())
    expect(await screen.findByText('Guided study')).toBeTruthy()
    expect(screen.getByText('Cost at target (€ per year)')).toBeTruthy()
    expect(screen.getAllByText('€1.23M')).toHaveLength(2)
    expect(screen.getAllByText('0.40 ‱')).toHaveLength(2)
    expect(screen.getAllByText('Pass')).toHaveLength(2)
    expect(screen.getAllByText('Current')).toHaveLength(2)
  })

  it('shows a signed delta with its unit when the child differs', async () => {
    renderWith(study(), study({ mc_lole_h: 4 }))
    expect(await screen.findByText('+1.5 h per year')).toBeTruthy()
  })

  it('says out of date, and gives no delta, when a side was edited after its study', async () => {
    renderWith(study(), study({ state: 'out_of_date', mc_lole_h: 4 }))
    expect(await screen.findByText('Out of date')).toBeTruthy()
    expect(screen.queryByText('+1.5 h per year')).toBeNull()
    expect(screen.queryByText(/stale/i)).toBeNull()
  })

  it('gives no cost delta between a per-year cost and an NPV', async () => {
    renderWith(study(), study({ period_basis: 'npv_multi_period', cost_at_target_eur: 9_000_000 }))
    expect(await screen.findByText('Cost at target (€, per year vs NPV)')).toBeTruthy()
    expect(screen.getByText('€9.00M')).toBeTruthy()
    expect(screen.queryByText(/^\+€/)).toBeNull()
  })

  it('names the one side that has results', async () => {
    renderWith(null, study())
    expect(await screen.findByText('Study: only What if has results')).toBeTruthy()
    expect(screen.queryByText('Guided study')).toBeNull()
  })

  it('shows nothing when neither side ran a study', async () => {
    renderWith(null, null)
    expect(await screen.findByText('Solver state')).toBeTruthy()
    expect(screen.queryByText(/Guided study|Study: only/)).toBeNull()
  })
})
