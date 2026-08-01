import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import StorageCycling from './StorageCycling'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return { ...actual, resultsApi: { ...actual.resultsApi, getStorageDispatchResults: vi.fn() } }
})

vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getStorageUnits: vi.fn(),
      getSnapshots: vi.fn(),
      listVintageResults: vi.fn(),
    },
  }
})

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  // pNom is rendered as u.pNom.toFixed(1) — a direct passthrough of the
  // mocked network row's own p_nom field, no aggregation involved.
  vi.mocked(resultsApi.getStorageDispatchResults).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['Battery1'], data: [[10]],
  })
  // Full StorageUnit shape (api/types.ts:56-70) — 21 fields, not just the 4
  // the brief supplied. StorageCycling.tsx's `meta` builder (:114-121) only
  // ever reads `name`, `carrier`, `p_nom_opt` (absent here, so it falls back
  // to `p_nom`) and `max_hours` off each row. Every other field below is an
  // inert PyPSA-default / "no constraint" value (mirrors Curtailment.test.tsx
  // and Dispatch.test.tsx's fixtures, which traced sibling interfaces the
  // same way) that nothing in this test's render path consumes.
  vi.mocked(networkApi.getStorageUnits).mockReset().mockResolvedValue([
    {
      name: 'Battery1', bus: 'Bus 0', carrier: 'battery', p_nom: 424.24,
      p_nom_extendable: false, p_nom_min: 0, p_nom_max: null,
      max_hours: 4, efficiency_store: 1, efficiency_dispatch: 1,
      standing_loss: 0, cyclic_state_of_charge: false, state_of_charge_initial: 0,
      inflow: 0,
      capital_cost: 0, marginal_cost: 0, fom_cost: 0,
      overnight_cost: null, discount_rate: null,
      build_year: 0, lifetime: null,
    },
  ])
  vi.mocked(networkApi.getSnapshots).mockReset().mockResolvedValue({
    count: 1, snapshots: ['2026-01-01T00:00:00'], weightings: [], ts_start: null, ts_end: null,
    can_sample_weeks: false,
  })
  // listVintageResults resolves an object keyed by component class
  // (api/network.ts:201-207), not the bare array the brief supplied — `[]`
  // would fail to typecheck against `{ results: Record<...> }`. An empty
  // `results` map is the real "no vintage expansion has run" shape and is
  // inert here regardless: with a single flat snapshot (no `periods` on
  // either the dispatch TS or getSnapshots), StorageCycling.tsx's `p` is
  // always `null` (:140-144), so `capForPeriod` (:165-166) short-circuits on
  // `p == null` before ever touching `vintageResults`.
  vi.mocked(networkApi.listVintageResults).mockReset().mockResolvedValue({ results: {} })
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <StorageCycling />
    </QueryClientProvider>,
  )
}

it('renders a distinctive rated power directly from the mocked storage-unit row', async () => {
  renderPage()
  const match = await screen.findByText('424.2')
  expect(match).toBeTruthy()
})
