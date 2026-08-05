import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import LostLoadTab from './LostLoadTab'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return { ...actual, resultsApi: { ...actual.resultsApi, getLostLoadRanged: vi.fn() } }
})

vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getSnapshots: vi.fn(),
      getInvestmentPeriods: vi.fn(),
    },
  }
})

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  // Full getLostLoad shape (api/simulation.ts:357-367): `total_mwh` /
  // `total_cost_eur` / `voll_eur_per_mwh` are required alongside the
  // index/columns/data TS payload, not optional. LostLoadTab.tsx's `llMeta`
  // cast (LostLoadTab.tsx:54-59) only ever reads `voll_eur_per_mwh` and
  // `bus_carriers` off this object — `total_mwh`/`total_cost_eur` feed
  // nothing in the component, so 0 is a fully inert default for both.
  // `voll_eur_per_mwh: 0` keeps the "Lost-load cost" / "Cost (€)" / "VOLL
  // price" figures at their zero baseline (verified below), so they can't
  // coincidentally collide with the "424.2" text this test asserts on.
  vi.mocked(resultsApi.getLostLoadRanged).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['Bus 0'], data: [[424.24]],
    total_mwh: 0, total_cost_eur: 0, voll_eur_per_mwh: 0,
  })
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
      <LostLoadTab />
    </QueryClientProvider>,
  )
}

it('renders a distinctive lost-load KPI sourced from a single mocked snapshot', async () => {
  renderPage()
  // LostLoadTab.tsx's local fmtEnergy (lines 286-292) mirrors Curtailment's:
  // "424.2 MWh". With a single bus ('Bus 0') and a single snapshot, the same
  // 424.24 MWh total renders THREE times: the "Total lost load" KPI
  // (LostLoadTab.tsx:153), the "By carrier" table's lone "Electrical" row
  // (:179, since an empty `bus_carriers` map falls back to the "electrical"
  // alias), and the "Per-bus lost load" table's lone "Bus 0" row (:265) — all
  // three sum the identical one-column, one-row payload via
  // `weightedSumSplit`. A single-match `findByText` throws
  // "Found multiple elements" here (verified); use `findAllByText` and
  // assert on the known count (3) instead, same resolution
  // Curtailment.test.tsx used for its own two-way duplicate. Recharts' axis
  // ticks are live DOM in this suite too, but the rendered X-axis date tick
  // and Y-axis "Lost load (MW)" label contain no "424.2" substring, so they
  // don't inflate the count (confirmed by dumping the full rendered body
  // text during development — see task-24-report.md).
  const matches = await screen.findAllByText((text) => text.includes('424.2'))
  expect(matches.length).toBe(3)
})
