import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import Curtailment from './Curtailment'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getCurtailment: vi.fn(),
      getGeneratorResults: vi.fn(),
    },
  }
})

vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getGenerators: vi.fn(),
      getSnapshots: vi.fn(),
      getInvestmentPeriods: vi.fn(),
    },
  }
})

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  // Single renewable generator, single snapshot, weight 1 (via getSnapshots
  // below): Σ curtailed MW × weighting × years over one point equals that
  // point.
  vi.mocked(resultsApi.getCurtailment).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['SolarGen'], data: [[424.24]],
  })
  vi.mocked(resultsApi.getGeneratorResults).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['SolarGen'], data: [[0]],
  })
  // Full Generator shape (api/types.ts:33-55) — 29 fields, not just the 4
  // the brief supplied. Curtailment.tsx reads only `name` and `carrier` off
  // each row: `carrierByName` (:46) and `renewableNames` (:62-64). Every
  // other field below is an inert PyPSA-default / "no constraint" value
  // (mirrors Dispatch.test.tsx's fixture, which traced the same interface)
  // that nothing in this test's render path consumes.
  vi.mocked(networkApi.getGenerators).mockReset().mockResolvedValue([
    {
      name: 'SolarGen', bus: 'Bus 0', carrier: 'solar', p_nom: 500,
      p_nom_extendable: false, p_nom_min: 0, p_nom_max: null,
      p_min_pu: 0, p_max_pu: 1, control: 'PQ',
      marginal_cost: 0, capital_cost: 0, fom_cost: 0,
      overnight_cost: null, discount_rate: null, curtailment_cost: 0,
      efficiency: 1, committable: false,
      ramp_limit_up: null, ramp_limit_down: null,
      start_up_cost: 0, shut_down_cost: 0, min_up_time: 0, min_down_time: 0,
      e_sum_min: null, e_sum_max: null,
      build_year: 0, lifetime: null, unit: 'MW',
    },
  ])
  vi.mocked(networkApi.getSnapshots).mockReset().mockResolvedValue({
    count: 1, snapshots: ['2026-01-01T00:00:00'], weightings: [], ts_start: null, ts_end: null,
    can_sample_weeks: false,
  })
  vi.mocked(networkApi.getInvestmentPeriods).mockReset().mockResolvedValue({ periods: [], weightings: [] })
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Curtailment />
    </QueryClientProvider>,
  )
}

it('renders a distinctive curtailed-energy KPI sourced from a single mocked snapshot', async () => {
  renderPage()
  // Curtailment.tsx's own local fmtEnergy (lines 199-205) rounds the MWh
  // tier to 1 decimal: "424.2 MWh". With a single carrier ('solar') and a
  // single snapshot, the per-carrier table's "By carrier" row (:137) sums to
  // the SAME total as the "Curtailed energy" KPI (:110-111) — both derive
  // from `weightedSum` over the identical one-column, one-row payload, so
  // both render the literal string "424.2 MWh". `findByText` requires
  // exactly one match; use `findAllByText` and assert on the known count
  // (2) instead, same resolution Dispatch.test.tsx used for its own
  // legitimate duplicate.
  const matches = await screen.findAllByText((text) => text.includes('424.2'))
  expect(matches.length).toBe(2)
})
